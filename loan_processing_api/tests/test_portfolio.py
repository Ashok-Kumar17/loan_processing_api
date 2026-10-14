from dataclasses import replace
from decimal import Decimal

import pytest

from integrations.rta import rta_for
from lending import services
from lending.models import Holding

from .helpers import client_for

pytestmark = pytest.mark.django_db


def test_sync_pulls_from_both_rtas(borrower):
    res = client_for(borrower).post("/api/portfolio/sync/")
    assert res.status_code == 200
    rtas = {h["rta"] for h in res.json()["holdings"]}
    assert rtas == {"CAMS", "KFINTECH"}


def test_resync_updates_in_place_without_duplicates(borrower):
    first = services.sync_portfolio(borrower)
    second = services.sync_portfolio(borrower)
    assert len(first) == len(second) == Holding.objects.filter(borrower=borrower).count()


def test_eligible_value_applies_ltv_by_category(borrower):
    body = client_for(borrower).post("/api/portfolio/sync/").json()
    ltv = {"EQUITY": Decimal("0.50"), "HYBRID": Decimal("0.60"), "DEBT": Decimal("0.80")}
    total = Decimal("0")
    for h in body["holdings"]:
        expected = (Decimal(h["units"]) * Decimal(h["nav"]) * ltv[h["category"]]).quantize(Decimal("0.01"))
        assert Decimal(h["eligible_value"]) == expected
        total += expected
    assert Decimal(body["total_eligible_value"]) == total


def test_resync_never_reduces_units_below_pledged(borrower, monkeypatch):
    """If the RTA reports fewer units (e.g. a redemption elsewhere), pledged units stay covered."""
    h = services.sync_portfolio(borrower)[0]
    Holding.objects.filter(pk=h.pk).update(pledged_units=Decimal("500"))
    client = rta_for(h.rta)
    original = client.fetch_holdings

    def shrunk(pan):
        return [replace(x, units=Decimal("100")) if x.isin == h.isin else x for x in original(pan)]

    monkeypatch.setattr(client, "fetch_holdings", shrunk)
    services.sync_portfolio(borrower)
    h.refresh_from_db()
    assert h.units == Decimal("500") == h.pledged_units


def test_portfolio_is_borrower_only(partner):
    assert client_for(partner.user).get("/api/portfolio/").status_code == 403
