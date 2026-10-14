from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from core.exceptions import BusinessRuleError
from lending import services
from lending.models import CreditLine, LedgerEntry

from .helpers import active_line, approved_line, client_for

pytestmark = pytest.mark.django_db


def _draw(user, line, amount, key="k1"):
    return client_for(user).post(
        f"/api/credit-lines/{line.pk}/drawdowns/", {"amount": str(amount)}, format="json", HTTP_IDEMPOTENCY_KEY=key
    )


def _repay(user, line, amount, key="r1"):
    return client_for(user).post(
        f"/api/credit-lines/{line.pk}/repayments/", {"amount": str(amount)}, format="json", HTTP_IDEMPOTENCY_KEY=key
    )


def test_drawdown_requires_idempotency_key(borrower):
    line = active_line(borrower)
    res = client_for(borrower).post(f"/api/credit-lines/{line.pk}/drawdowns/", {"amount": "1000"}, format="json")
    assert res.status_code == 400 and res.json()["code"] == "idempotency_key_required"


def test_drawdown_moves_money_and_writes_ledger(borrower):
    line = active_line(borrower)
    res = _draw(borrower, line, "12500.50")
    assert res.status_code == 201
    entry = res.json()
    assert entry["entry_type"] == "DRAWDOWN" and entry["reference"].startswith("UTR")
    line.refresh_from_db()
    assert line.outstanding_principal == Decimal("12500.50")
    assert Decimal(entry["balance_after"]) == Decimal("12500.50")


def test_drawdown_beyond_available_limit_is_refused(borrower):
    line = active_line(borrower)
    res = _draw(borrower, line, line.sanctioned_limit + 1)
    assert res.status_code == 409 and res.json()["code"] == "limit_exceeded"
    assert not LedgerEntry.objects.exists()


def test_retry_with_same_key_replays_without_moving_money_twice(borrower):
    line = active_line(borrower)
    first = _draw(borrower, line, "1000", key="same")
    second = _draw(borrower, line, "1000", key="same")
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second["Idempotent-Replayed"] == "true"
    assert LedgerEntry.objects.filter(entry_type="DRAWDOWN").count() == 1
    line.refresh_from_db()
    assert line.outstanding_principal == Decimal("1000")


def test_same_key_with_different_payload_is_rejected(borrower):
    line = active_line(borrower)
    _draw(borrower, line, "1000", key="same")
    res = _draw(borrower, line, "2000", key="same")
    assert res.status_code == 422 and res.json()["code"] == "idempotency_key_reused"


def test_business_error_is_replayed_for_same_key(borrower):
    line = active_line(borrower)
    assert _draw(borrower, line, line.sanctioned_limit + 1, key="big").status_code == 409
    replay = _draw(borrower, line, line.sanctioned_limit + 1, key="big")
    assert replay.status_code == 409 and replay["Idempotent-Replayed"] == "true"


def test_keys_are_scoped_per_user(borrower, make_borrower):
    a, b = borrower, make_borrower()
    la, lb = active_line(a), active_line(b)
    assert _draw(a, la, "1000", key="shared").status_code == 201
    assert _draw(b, lb, "1000", key="shared").status_code == 201


def test_cannot_draw_on_line_that_is_not_active(borrower):
    line = approved_line(borrower)
    res = _draw(borrower, line, "1000")
    assert res.status_code == 409 and res.json()["code"] == "line_not_active"


def test_cannot_draw_on_someone_elses_line(borrower, make_borrower):
    line = active_line(borrower)
    assert _draw(make_borrower(), line, "1000").status_code == 404


@pytest.mark.parametrize("amount", ["0", "-5", "abc"])
def test_invalid_amounts(borrower, amount):
    line = active_line(borrower)
    assert _draw(borrower, line, amount).status_code == 400


def test_repayment_settles_interest_before_principal(borrower):
    line = active_line(borrower)
    _draw(borrower, line, "10000")
    CreditLine.objects.filter(pk=line.pk).update(interest_due=Decimal("150.00"))
    res = _repay(borrower, line, "1000")
    assert res.status_code == 201
    body = res.json()
    assert Decimal(body["interest_component"]) == Decimal("150.00")
    assert Decimal(body["principal_component"]) == Decimal("850.00")
    line.refresh_from_db()
    assert line.interest_due == 0 and line.outstanding_principal == Decimal("9150.00")


def test_overpayment_is_refused(borrower):
    line = active_line(borrower)
    _draw(borrower, line, "1000")
    res = _repay(borrower, line, "1000.01")
    assert res.status_code == 409 and res.json()["code"] == "overpayment"


def test_statement_lists_ledger_in_order(borrower):
    line = active_line(borrower)
    _draw(borrower, line, "3000", key="a")
    _repay(borrower, line, "1000", key="b")
    rows = client_for(borrower).get(f"/api/credit-lines/{line.pk}/statement/").json()["results"]
    assert [r["entry_type"] for r in rows] == ["DRAWDOWN", "REPAYMENT"]
    assert [r["balance_after"] for r in rows] == ["3000.00", "2000.00"]


def test_database_check_constraint_blocks_overdraw_even_without_app_checks(borrower):
    line = active_line(borrower)
    with pytest.raises(IntegrityError), transaction.atomic():
        CreditLine.objects.filter(pk=line.pk).update(outstanding_principal=line.sanctioned_limit + 1)


def test_services_drawdown_rolls_back_on_error(borrower):
    """Called directly (no idempotency wrapper) a rule violation leaves no partial writes."""
    line = active_line(borrower)
    with pytest.raises(BusinessRuleError), transaction.atomic():
        services.drawdown(borrower, line.pk, line.sanctioned_limit * 2)
    line.refresh_from_db()
    assert line.outstanding_principal == 0
