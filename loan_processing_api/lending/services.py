"""Lending business logic. Views stay thin; every rule lives here.

Concurrency model
-----------------
Every operation that changes money or units first takes a row lock
(SELECT ... FOR UPDATE) on the credit line and/or holdings, then re-reads state
under that lock. On PostgreSQL this serialises competing drawdowns on the same
line, so N parallel requests can never push the outstanding amount past the
sanctioned limit. The CHECK constraint on CreditLine is a second, database-level
guard behind the application check.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from core.exceptions import BusinessRuleError
from core.models import audit
from core.money import ZERO, daily_interest, to_money
from integrations import payout
from integrations.bureau import bureau_client
from integrations.rta import RTAError, rta_clients, rta_for
from partners.models import CommissionAccrual

from .models import CreditLine, Holding, LedgerEntry, Pledge

log = logging.getLogger("lending")
Status = CreditLine.Status

# Allowed state transitions. Anything else is a 409.
TRANSITIONS = {
    Status.APPROVED: {Status.ACTIVE, Status.CANCELLED},
    Status.ACTIVE: {Status.CLOSED},
    Status.REJECTED: set(),
    Status.CANCELLED: set(),
    Status.CLOSED: set(),
}


def _transition(line: CreditLine, new_status: str, actor) -> None:
    if new_status not in TRANSITIONS[line.status]:
        raise BusinessRuleError(
            f"Cannot move credit line from {line.status} to {new_status}.", code="invalid_transition"
        )
    old = line.status
    line.status = new_status
    audit(actor, "credit_line", line.pk, "status_change", from_status=old, to_status=new_status)


def _lock_line(line_id: int, borrower=None) -> CreditLine:
    qs = CreditLine.objects.select_for_update()
    if borrower is not None:
        qs = qs.filter(borrower=borrower)
    try:
        return qs.get(pk=line_id)
    except CreditLine.DoesNotExist:
        raise BusinessRuleError("Credit line not found.", code="not_found", http_status=404) from None


# --------------------------------------------------------------------------- portfolio
@transaction.atomic
def sync_portfolio(borrower) -> list[Holding]:
    """Pull holdings from every RTA and upsert them. Pledged units are never reduced by a sync."""
    pan = borrower.get_pan()
    existing = {(h.folio, h.isin): h for h in Holding.objects.select_for_update().filter(borrower=borrower)}
    to_create, to_update = [], []
    for client in rta_clients().values():
        for item in client.fetch_holdings(pan):
            holding = existing.get((item.folio, item.isin))
            if holding is None:
                to_create.append(
                    Holding(
                        borrower=borrower,
                        rta=item.rta,
                        folio=item.folio,
                        isin=item.isin,
                        scheme_name=item.scheme_name,
                        category=item.category,
                        units=item.units,
                        nav=item.nav,
                        nav_date=item.nav_date,
                    )
                )
            else:
                holding.units = max(item.units, holding.pledged_units)
                holding.nav, holding.nav_date = item.nav, item.nav_date
                to_update.append(holding)
    Holding.objects.bulk_create(to_create)
    if to_update:
        Holding.objects.bulk_update(to_update, ["units", "nav", "nav_date"])
    audit(borrower, "portfolio", borrower.pk, "synced", created=len(to_create), updated=len(to_update))
    return list(Holding.objects.filter(borrower=borrower))


# --------------------------------------------------------------------------- application
@dataclass(frozen=True)
class PledgeRequest:
    holding_id: int
    units: Decimal


@transaction.atomic
def apply_for_credit_line(borrower, requested_limit: Decimal, pledges: list[PledgeRequest]) -> CreditLine:
    policy = settings.LENDING
    requested_limit = to_money(requested_limit)
    if not policy["MIN_LIMIT"] <= requested_limit <= policy["MAX_LIMIT"]:
        raise BusinessRuleError(
            f"Requested limit must be between {policy['MIN_LIMIT']} and {policy['MAX_LIMIT']}.",
            code="limit_out_of_range",
            http_status=400,
        )
    if CreditLine.objects.filter(borrower=borrower, status__in=CreditLine.OPEN_STATUSES).exists():
        raise BusinessRuleError("You already have an open credit line.", code="open_line_exists")

    ids = [p.holding_id for p in pledges]
    if len(set(ids)) != len(ids):
        raise BusinessRuleError("Each holding can appear only once.", code="duplicate_holding", http_status=400)
    # Lock in primary-key order so two concurrent applications can't deadlock.
    holdings = {
        h.pk: h for h in Holding.objects.select_for_update().filter(borrower=borrower, pk__in=ids).order_by("pk")
    }
    missing = set(ids) - holdings.keys()
    if missing:
        raise BusinessRuleError(f"Unknown holding(s): {sorted(missing)}.", code="unknown_holding", http_status=400)

    eligible = ZERO
    for p in pledges:
        h = holdings[p.holding_id]
        if p.units <= 0 or p.units > h.free_units:
            raise BusinessRuleError(
                f"Holding {h.pk}: can pledge between 0 and {h.free_units} free units.",
                code="insufficient_units",
                http_status=400,
            )
        eligible += h.eligible_value_of(p.units)

    if eligible < policy["MIN_LIMIT"]:
        raise BusinessRuleError(
            f"Pledged units support only Rs {eligible}; minimum limit is Rs {policy['MIN_LIMIT']}.",
            code="insufficient_collateral",
            http_status=400,
        )

    report = bureau_client().fetch_report(borrower.get_pan())
    partner = borrower.referred_via.partner if borrower.referred_via_id else None
    line = CreditLine(
        borrower=borrower,
        partner=partner,
        requested_limit=requested_limit,
        apr=policy["DEFAULT_APR"],
        bureau_score=report.score,
    )
    if report.score < policy["MIN_BUREAU_SCORE"] or report.overdue_accounts > 0:
        line.status = Status.REJECTED
        line.rejection_reason = f"Bureau score {report.score} / overdue accounts {report.overdue_accounts}."
        line.save()
        audit(borrower, "credit_line", line.pk, "rejected", score=report.score)
        return line

    line.status = Status.APPROVED
    line.sanctioned_limit = min(requested_limit, to_money(eligible))
    try:
        with transaction.atomic():
            line.save()
    except IntegrityError:  # a concurrent application won the one-open-line race
        raise BusinessRuleError("You already have an open credit line.", code="open_line_exists") from None

    pledge_rows = []
    for p in pledges:
        h = holdings[p.holding_id]
        h.pledged_units += p.units  # reserve now; lien is marked on /pledge
        pledge_rows.append(
            Pledge(
                credit_line=line,
                holding=h,
                units=p.units,
                nav_at_pledge=h.nav,
                ltv=h.ltv,
                eligible_value=h.eligible_value_of(p.units),
            )
        )
    Holding.objects.bulk_update(holdings.values(), ["pledged_units"])
    Pledge.objects.bulk_create(pledge_rows)
    audit(borrower, "credit_line", line.pk, "approved", sanctioned=line.sanctioned_limit, score=report.score)
    return line


def _release_reservations(line: CreditLine) -> None:
    pledges = list(
        line.pledges.select_related("holding").filter(status__in=[Pledge.Status.REQUESTED, Pledge.Status.PLEDGED])
    )
    holdings = {p.holding_id: p.holding for p in pledges}
    locked = {h.pk: h for h in Holding.objects.select_for_update().filter(pk__in=holdings).order_by("pk")}
    now = timezone.now()
    for p in pledges:
        if p.status == Pledge.Status.PLEDGED and p.lien_reference:
            rta_for(p.holding.rta).release_lien(p.lien_reference)
        locked[p.holding_id].pledged_units -= p.units
        p.status, p.released_at = Pledge.Status.RELEASED, now
    Holding.objects.bulk_update(locked.values(), ["pledged_units"])
    Pledge.objects.bulk_update(pledges, ["status", "released_at"])


@transaction.atomic
def mark_liens(borrower, line_id: int) -> CreditLine:
    """Mark a lien at the RTA for every pledge. If any RTA call fails, liens already
    marked in this attempt are released again (compensation) and nothing is committed."""
    line = _lock_line(line_id, borrower)
    if line.status != Status.APPROVED:
        raise BusinessRuleError(
            f"Liens can only be marked on an APPROVED line (is {line.status}).", code="invalid_transition"
        )

    pan = borrower.get_pan()
    pledges = list(line.pledges.select_related("holding").filter(status=Pledge.Status.REQUESTED))
    marked: list[tuple[str, str]] = []
    try:
        for p in pledges:
            ref = rta_for(p.holding.rta).mark_lien(pan, p.holding.folio, p.holding.isin, p.units)
            marked.append((p.holding.rta, ref))
            p.lien_reference, p.status = ref, Pledge.Status.PLEDGED
    except RTAError as exc:
        for rta_name, ref in marked:
            rta_for(rta_name).release_lien(ref)
        log.warning("Lien marking failed for line %s: %s (rolled back %d liens)", line.pk, exc, len(marked))
        raise BusinessRuleError(f"RTA rejected lien request: {exc}", code="lien_failed", http_status=502) from exc

    Pledge.objects.bulk_update(pledges, ["lien_reference", "status"])
    _transition(line, Status.ACTIVE, borrower)
    line.activated_at = timezone.now()
    line.save(update_fields=["status", "activated_at"])
    return line


@transaction.atomic
def cancel_line(borrower, line_id: int) -> CreditLine:
    line = _lock_line(line_id, borrower)
    _transition(line, Status.CANCELLED, borrower)
    _release_reservations(line)
    line.closed_at = timezone.now()
    line.save(update_fields=["status", "closed_at"])
    return line


@transaction.atomic
def close_line(borrower, line_id: int) -> CreditLine:
    line = _lock_line(line_id, borrower)
    if line.status == Status.ACTIVE and line.total_due > 0:
        raise BusinessRuleError(f"Repay Rs {line.total_due} before closing.", code="dues_pending")
    _transition(line, Status.CLOSED, borrower)
    _release_reservations(line)
    line.closed_at = timezone.now()
    line.save(update_fields=["status", "closed_at"])
    return line


# --------------------------------------------------------------------------- money movement
def drawdown(borrower, line_id: int, amount: Decimal) -> LedgerEntry:
    """Must be called inside a transaction (run_idempotent provides one)."""
    amount = to_money(amount)
    if amount <= 0:
        raise BusinessRuleError("Amount must be positive.", code="invalid_amount", http_status=400)
    line = _lock_line(line_id, borrower)
    if line.status != Status.ACTIVE:
        raise BusinessRuleError(f"Credit line is {line.status}, not ACTIVE.", code="line_not_active")
    if amount > line.available_limit:
        raise BusinessRuleError(f"Amount exceeds available limit of Rs {line.available_limit}.", code="limit_exceeded")
    line.outstanding_principal += amount
    line.save(update_fields=["outstanding_principal"])
    entry = LedgerEntry.objects.create(
        credit_line=line,
        entry_type=LedgerEntry.Type.DRAWDOWN,
        amount=amount,
        principal_component=amount,
        balance_after=line.outstanding_principal,
        reference=payout.transfer(borrower.username, amount),
        value_date=timezone.localdate(),
    )
    audit(borrower, "credit_line", line.pk, "drawdown", amount=amount, entry=entry.pk)
    return entry


def repay(borrower, line_id: int, amount: Decimal) -> LedgerEntry:
    """Interest is settled first, then principal. Must be called inside a transaction."""
    amount = to_money(amount)
    if amount <= 0:
        raise BusinessRuleError("Amount must be positive.", code="invalid_amount", http_status=400)
    line = _lock_line(line_id, borrower)
    if line.status != Status.ACTIVE:
        raise BusinessRuleError(f"Credit line is {line.status}, not ACTIVE.", code="line_not_active")
    if amount > line.total_due:
        raise BusinessRuleError(f"Amount exceeds total due of Rs {line.total_due}.", code="overpayment")
    interest_part = min(amount, line.interest_due)
    principal_part = amount - interest_part
    line.interest_due -= interest_part
    line.outstanding_principal -= principal_part
    line.save(update_fields=["interest_due", "outstanding_principal"])
    entry = LedgerEntry.objects.create(
        credit_line=line,
        entry_type=LedgerEntry.Type.REPAYMENT,
        amount=amount,
        principal_component=principal_part,
        interest_component=interest_part,
        balance_after=line.outstanding_principal,
        reference="RPY" + uuid.uuid4().hex[:16].upper(),
        value_date=timezone.localdate(),
    )
    audit(borrower, "credit_line", line.pk, "repayment", amount=amount, entry=entry.pk)
    return entry


# --------------------------------------------------------------------------- daily accrual
@dataclass(frozen=True)
class AccrualResult:
    day: date
    lines: int
    interest: Decimal
    commission: Decimal


@transaction.atomic
def accrue_for_day(day: date) -> AccrualResult:
    """Accrue one day of interest on every active line, plus partner trail commission.

    Runs in a constant number of queries regardless of how many lines are active,
    and is idempotent: lines that already have an accrual for `day` are skipped,
    and a unique constraint backs that up if two runs overlap.
    """
    already = LedgerEntry.objects.filter(
        credit_line=OuterRef("pk"), entry_type=LedgerEntry.Type.INTEREST, value_date=day
    )
    lines = list(
        CreditLine.objects.select_for_update()
        .filter(status=Status.ACTIVE, outstanding_principal__gt=0)
        .filter(~Exists(already))
        .order_by("pk")
    )
    trail_rate = settings.LENDING["PARTNER_TRAIL_RATE"]
    entries, commissions = [], []
    total_interest = total_commission = ZERO
    for line in lines:
        interest = daily_interest(line.outstanding_principal, line.apr)
        if interest <= 0:
            continue
        line.interest_due += interest
        total_interest += interest
        entries.append(
            LedgerEntry(
                credit_line=line,
                entry_type=LedgerEntry.Type.INTEREST,
                amount=interest,
                interest_component=interest,
                balance_after=line.outstanding_principal,
                value_date=day,
            )
        )
        if line.partner_id:
            commission = daily_interest(line.outstanding_principal, trail_rate)
            if commission > 0:
                total_commission += commission
                commissions.append(
                    CommissionAccrual(
                        partner_id=line.partner_id,
                        credit_line=line,
                        accrual_date=day,
                        principal_base=line.outstanding_principal,
                        amount=commission,
                    )
                )
    LedgerEntry.objects.bulk_create(entries)
    CommissionAccrual.objects.bulk_create(commissions)
    if entries:
        CreditLine.objects.bulk_update([e.credit_line for e in entries], ["interest_due"])
    log.info("Accrued %s interest on %d lines for %s", total_interest, len(entries), day)
    return AccrualResult(day=day, lines=len(entries), interest=total_interest, commission=total_commission)
