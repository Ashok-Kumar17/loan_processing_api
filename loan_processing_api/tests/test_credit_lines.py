from decimal import Decimal

import pytest

from integrations.rta import RTAError, rta_for
from lending import services
from lending.models import CreditLine, Holding, Pledge

from .helpers import active_line, approved_line, client_for, next_pan

pytestmark = pytest.mark.django_db


def _apply(user, requested, pledges):
    return client_for(user).post(
        "/api/credit-lines/", {"requested_limit": str(requested), "pledges": pledges}, format="json"
    )


def test_apply_happy_path_reserves_units_and_sanctions_min_of_requested_and_eligible(borrower):
    h = services.sync_portfolio(borrower)[0]
    units = (h.units / 4).quantize(Decimal("0.001"))
    eligible = h.eligible_value_of(units)
    res = _apply(borrower, eligible * 10, [{"holding": h.pk, "units": str(units)}])  # ask for far more
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["status"] == "APPROVED"
    assert Decimal(body["sanctioned_limit"]) == eligible
    assert Decimal(body["available_limit"]) == 0  # nothing drawable until liens are marked
    h.refresh_from_db()
    assert h.pledged_units == units


def test_bureau_rejection(make_borrower):
    user = make_borrower(pan=next_pan(first_digit="9"))
    line = approved_line(user)
    assert line.status == "REJECTED"
    assert line.bureau_score == 640
    assert not line.pledges.exists()
    assert all(h.pledged_units == 0 for h in Holding.objects.filter(borrower=user))


@pytest.mark.parametrize("requested", ["9999.99", "250000000.01"])
def test_limit_must_be_within_policy_range(borrower, requested):
    h = services.sync_portfolio(borrower)[0]
    res = _apply(borrower, requested, [{"holding": h.pk, "units": "1"}])
    assert res.status_code == 400
    assert res.json()["code"] == "limit_out_of_range"


def test_cannot_pledge_more_than_free_units(borrower):
    h = services.sync_portfolio(borrower)[0]
    res = _apply(borrower, "50000", [{"holding": h.pk, "units": str(h.units + 1)}])
    assert res.status_code == 400 and res.json()["code"] == "insufficient_units"


def test_duplicate_holding_in_request(borrower):
    h = services.sync_portfolio(borrower)[0]
    res = _apply(borrower, "50000", [{"holding": h.pk, "units": "1"}, {"holding": h.pk, "units": "1"}])
    assert res.status_code == 400 and res.json()["code"] == "duplicate_holding"


def test_cannot_pledge_someone_elses_holding(borrower, make_borrower):
    other = make_borrower()
    theirs = services.sync_portfolio(other)[0]
    services.sync_portfolio(borrower)
    res = _apply(borrower, "50000", [{"holding": theirs.pk, "units": "1"}])
    assert res.status_code == 400 and res.json()["code"] == "unknown_holding"


def test_insufficient_collateral(borrower):
    h = services.sync_portfolio(borrower)[0]
    res = _apply(borrower, "10000", [{"holding": h.pk, "units": "1"}])
    assert res.status_code == 400 and res.json()["code"] == "insufficient_collateral"


def test_only_one_open_line_per_borrower(borrower):
    approved_line(borrower, fraction=Decimal("0.2"))
    h = Holding.objects.filter(borrower=borrower).last()
    res = _apply(borrower, "10000", [{"holding": h.pk, "units": str(h.free_units)}])
    assert res.status_code == 409 and res.json()["code"] == "open_line_exists"


def test_pledge_marks_liens_and_activates(borrower):
    line = approved_line(borrower)
    res = client_for(borrower).post(f"/api/credit-lines/{line.pk}/pledge/")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ACTIVE"
    assert Decimal(body["available_limit"]) == line.sanctioned_limit
    assert all(p["status"] == "PLEDGED" and p["lien_reference"] for p in body["pledges"])


def test_pledging_twice_is_an_invalid_transition(borrower):
    line = active_line(borrower)
    res = client_for(borrower).post(f"/api/credit-lines/{line.pk}/pledge/")
    assert res.status_code == 409 and res.json()["code"] == "invalid_transition"


def test_lien_failure_releases_already_marked_liens(make_borrower, monkeypatch):
    """Two pledges on different RTAs: the second lien fails -> the first is released, line stays APPROVED."""
    user = make_borrower()
    holdings = services.sync_portfolio(user)
    cams = next(h for h in holdings if h.rta == "CAMS")
    kfin = next(h for h in holdings if h.rta == "KFINTECH")
    pledges = [services.PledgeRequest(h.pk, (h.units / 2).quantize(Decimal("0.001"))) for h in (cams, kfin)]
    line = services.apply_for_credit_line(user, Decimal("10000"), pledges)

    released = []
    monkeypatch.setattr(rta_for("KFINTECH"), "mark_lien", lambda *a: (_ for _ in ()).throw(RTAError("folio frozen")))
    monkeypatch.setattr(rta_for("CAMS"), "release_lien", lambda ref: released.append(ref))

    res = client_for(user).post(f"/api/credit-lines/{line.pk}/pledge/")
    assert res.status_code == 502 and res.json()["code"] == "lien_failed"
    assert len(released) == 1  # the CAMS lien taken in this attempt was given back
    line.refresh_from_db()
    assert line.status == "APPROVED"
    assert set(line.pledges.values_list("status", flat=True)) == {"REQUESTED"}


def test_cancel_releases_reserved_units(borrower):
    line = approved_line(borrower)
    res = client_for(borrower).post(f"/api/credit-lines/{line.pk}/cancel/")
    assert res.status_code == 200 and res.json()["status"] == "CANCELLED"
    assert all(h.pledged_units == 0 for h in Holding.objects.filter(borrower=borrower))


def test_cannot_cancel_an_active_line(borrower):
    line = active_line(borrower)
    assert client_for(borrower).post(f"/api/credit-lines/{line.pk}/cancel/").status_code == 409


def test_close_requires_zero_dues_then_releases_liens(borrower):
    line = active_line(borrower)
    c = client_for(borrower)
    c.post(f"/api/credit-lines/{line.pk}/drawdowns/", {"amount": "5000"}, format="json", HTTP_IDEMPOTENCY_KEY="d1")
    res = c.post(f"/api/credit-lines/{line.pk}/close/")
    assert res.status_code == 409 and res.json()["code"] == "dues_pending"

    c.post(f"/api/credit-lines/{line.pk}/repayments/", {"amount": "5000"}, format="json", HTTP_IDEMPOTENCY_KEY="r1")
    res = c.post(f"/api/credit-lines/{line.pk}/close/")
    assert res.status_code == 200 and res.json()["status"] == "CLOSED"
    assert set(Pledge.objects.filter(credit_line=line).values_list("status", flat=True)) == {"RELEASED"}
    assert all(h.pledged_units == 0 for h in Holding.objects.filter(borrower=borrower))


def test_after_closing_borrower_can_open_a_new_line(borrower):
    line = approved_line(borrower)
    services.cancel_line(borrower, line.pk)
    assert approved_line(borrower).status == "APPROVED"


def test_borrowers_cannot_see_each_others_lines(borrower, make_borrower):
    line = approved_line(borrower)
    intruder = client_for(make_borrower())
    assert intruder.get(f"/api/credit-lines/{line.pk}/").status_code == 404
    assert intruder.post(f"/api/credit-lines/{line.pk}/pledge/").status_code == 404
    assert intruder.get("/api/credit-lines/").json()["count"] == 0


def test_ops_can_list_all_lines_but_not_borrow(borrower, make_borrower, ops_user):
    approved_line(borrower)
    approved_line(make_borrower())
    ops = client_for(ops_user)
    assert ops.get("/api/credit-lines/").json()["count"] == 2
    assert ops.post("/api/credit-lines/", {}, format="json").status_code == 403


def test_partner_cannot_use_borrower_endpoints(partner):
    assert client_for(partner.user).get("/api/credit-lines/").status_code == 403


def test_line_list_query_count_is_constant(borrower, make_borrower, ops_user, django_assert_max_num_queries):
    for _ in range(5):
        approved_line(make_borrower())
    with django_assert_max_num_queries(4):
        client_for(ops_user).get("/api/credit-lines/")


def test_status_values_are_documented():
    assert set(CreditLine.Status.values) == {"APPROVED", "ACTIVE", "CLOSED", "REJECTED", "CANCELLED"}
