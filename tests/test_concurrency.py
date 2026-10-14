"""Race-condition tests. These need real row locks, so they run on PostgreSQL only
(CI runs them against a postgres:16 service container)."""

import threading
from decimal import Decimal

import pytest
from django.db import connection, connections
from rest_framework.test import APIClient

from lending.models import CreditLine, LedgerEntry

from .helpers import active_line

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs PostgreSQL row-level locks"),
]


def _fire(n, make_request):
    """Start n threads behind a barrier so their requests hit the database together."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            barrier.wait()
            results[i] = make_request(i)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_parallel_drawdowns_never_exceed_the_limit(borrower):
    line = active_line(borrower)
    amount = (line.sanctioned_limit * Decimal("0.3")).quantize(Decimal("0.01"))  # only 3 can fit

    def draw(i):
        res = _client(borrower).post(
            f"/api/credit-lines/{line.pk}/drawdowns/",
            {"amount": str(amount)},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"k-{i}",
        )
        return res.status_code

    codes = _fire(20, draw)
    assert codes.count(201) == 3
    assert codes.count(409) == 17
    line.refresh_from_db()
    assert line.outstanding_principal == amount * 3 <= line.sanctioned_limit
    assert LedgerEntry.objects.filter(entry_type="DRAWDOWN").count() == 3


def test_parallel_retries_with_one_key_execute_exactly_once(borrower):
    line = active_line(borrower)

    def draw(i):
        res = _client(borrower).post(
            f"/api/credit-lines/{line.pk}/drawdowns/", {"amount": "5000"}, format="json", HTTP_IDEMPOTENCY_KEY="one-key"
        )
        return res.status_code, res.get("Idempotent-Replayed"), res.json()["id"]

    results = _fire(15, draw)
    assert {code for code, _, _ in results} == {201}
    assert sum(1 for _, replayed, _ in results if replayed) == 14
    assert len({entry_id for _, _, entry_id in results}) == 1
    assert LedgerEntry.objects.filter(entry_type="DRAWDOWN").count() == 1
    line.refresh_from_db()
    assert line.outstanding_principal == Decimal("5000")


def test_parallel_drawdowns_and_repayments_keep_ledger_consistent(borrower):
    line = active_line(borrower)
    _client(borrower).post(
        f"/api/credit-lines/{line.pk}/drawdowns/", {"amount": "50000"}, format="json", HTTP_IDEMPOTENCY_KEY="seed"
    )

    def op(i):
        kind = "drawdowns" if i % 2 == 0 else "repayments"
        res = _client(borrower).post(
            f"/api/credit-lines/{line.pk}/{kind}/", {"amount": "1000"}, format="json", HTTP_IDEMPOTENCY_KEY=f"op-{i}"
        )
        return res.status_code

    assert set(_fire(20, op)) == {201}
    line = CreditLine.objects.get(pk=line.pk)
    assert line.outstanding_principal == Decimal("50000")  # 10 draws and 10 repayments of 1,000 cancel out
    last = LedgerEntry.objects.filter(credit_line=line).order_by("-id").first()
    assert last.balance_after == line.outstanding_principal
