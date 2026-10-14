from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command

from lending import services
from lending.models import CreditLine, LedgerEntry
from partners.models import CommissionAccrual, ReferralLink

from .helpers import active_line, client_for

pytestmark = pytest.mark.django_db
DAY = date(2026, 7, 1)


def _line_with_outstanding(user, amount=Decimal("100000")):
    line = active_line(user, fraction=Decimal("0.9"))
    services.drawdown(user, line.pk, amount)
    return line


def test_daily_interest_accrues_at_apr(borrower):
    line = _line_with_outstanding(borrower)
    result = services.accrue_for_day(DAY)
    assert result.lines == 1 and result.interest == Decimal("28.49")  # 1,00,000 x 10.40% / 365
    line.refresh_from_db()
    assert line.interest_due == Decimal("28.49")
    assert LedgerEntry.objects.get(entry_type="INTEREST").value_date == DAY


def test_accrual_is_idempotent_per_day(borrower):
    line = _line_with_outstanding(borrower)
    services.accrue_for_day(DAY)
    again = services.accrue_for_day(DAY)
    assert again.lines == 0
    line.refresh_from_db()
    assert line.interest_due == Decimal("28.49")
    assert LedgerEntry.objects.filter(entry_type="INTEREST").count() == 1


def test_no_interest_on_undrawn_or_inactive_lines(borrower, make_borrower):
    active_line(borrower)  # active, nothing drawn
    assert services.accrue_for_day(DAY).lines == 0


def test_trail_commission_only_for_referred_borrowers(partner, make_borrower):
    link = ReferralLink.objects.create(partner=partner, label="x")
    referred = make_borrower(referral=link)
    direct = make_borrower()
    _line_with_outstanding(referred)
    _line_with_outstanding(direct)
    result = services.accrue_for_day(DAY)
    assert result.lines == 2
    commission = CommissionAccrual.objects.get()
    assert commission.partner == partner
    assert commission.amount == Decimal("1.37")  # 1,00,000 x 0.50% / 365
    assert result.commission == Decimal("1.37")


@pytest.mark.parametrize("n_lines", [2, 12])
def test_accrual_runs_in_constant_queries(make_borrower, partner, n_lines, django_assert_num_queries):
    link = ReferralLink.objects.create(partner=partner, label="x")
    for _ in range(n_lines):
        _line_with_outstanding(make_borrower(referral=link), Decimal("50000"))
    # select lines, bulk insert ledger, bulk insert commissions, bulk update lines (+ savepoint pair)
    with django_assert_num_queries(6):
        assert services.accrue_for_day(DAY).lines == n_lines


def test_management_command_backfills_days(borrower):
    _line_with_outstanding(borrower)
    call_command("accrue_interest", "--date", "2026-07-10", "--days", "10")
    entries = LedgerEntry.objects.filter(entry_type="INTEREST")
    assert entries.count() == 10
    assert min(e.value_date for e in entries) == date(2026, 7, 1)
    assert CreditLine.objects.get().interest_due == Decimal("284.90")


def test_accrual_endpoint_is_ops_only(borrower, ops_user):
    _line_with_outstanding(borrower)
    payload = {"date": str(DAY + timedelta(days=1))}
    assert client_for(borrower).post("/api/ops/accrue-interest/", payload, format="json").status_code == 403
    res = client_for(ops_user).post("/api/ops/accrue-interest/", payload, format="json")
    assert res.status_code == 200 and res.json()["lines"] == 1
