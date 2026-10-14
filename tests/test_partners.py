from datetime import date
from decimal import Decimal

import pytest

from lending import services
from partners.models import CommissionAccrual, ReferralLink

from .helpers import active_line, approved_line, client_for

pytestmark = pytest.mark.django_db


def test_partner_creates_and_lists_referral_links(partner, make_borrower):
    c = client_for(partner.user)
    res = c.post("/api/partner/referral-links/", {"label": "Diwali WhatsApp"}, format="json")
    assert res.status_code == 201
    link = res.json()
    assert len(link["code"]) == 8 and link["share_url"].endswith(f"ref={link['code']}")

    obj = ReferralLink.objects.get(code=link["code"])
    make_borrower(referral=obj)
    active_line(make_borrower(referral=obj))
    rows = c.get("/api/partner/referral-links/").json()["results"]
    assert rows[0]["signups"] == 2 and rows[0]["active_lines"] == 1


def test_deactivated_link_stops_new_signups(partner, api):
    link = ReferralLink.objects.create(partner=partner, label="old")
    res = client_for(partner.user).patch(f"/api/partner/referral-links/{link.pk}/", {"is_active": False}, format="json")
    assert res.status_code == 200
    signup = api.post(
        "/api/auth/register/",
        {"username": "z", "password": "Str0ng!Passw0rd", "pan": "ZZZZZ1111Z", "referral_code": link.code},
        format="json",
    )
    assert signup.status_code == 400


def test_partner_cannot_touch_another_partners_links(partner, make_partner):
    theirs = ReferralLink.objects.create(partner=make_partner(), label="theirs")
    c = client_for(partner.user)
    assert c.patch(f"/api/partner/referral-links/{theirs.pk}/", {"is_active": False}, format="json").status_code == 404
    assert c.get("/api/partner/referral-links/").json()["count"] == 0


def test_dashboard_totals(partner, make_borrower):
    link = ReferralLink.objects.create(partner=partner, label="x")
    a, b = make_borrower(referral=link), make_borrower(referral=link)
    make_borrower(referral=link)  # signed up, no line
    la, lb = active_line(a), active_line(b)
    services.drawdown(a, la.pk, Decimal("40000"))
    services.drawdown(b, lb.pk, Decimal("60000"))
    services.accrue_for_day(date.today())
    services.accrue_for_day(date(2020, 1, 1))  # an old accrual counts for lifetime but not this month

    body = client_for(partner.user).get("/api/partner/dashboard/").json()
    assert body["clients"] == 3
    assert body["active_lines"] == 2
    assert Decimal(body["sanctioned_total"]) == la.sanctioned_limit + lb.sanctioned_limit
    assert Decimal(body["outstanding_total"]) == Decimal("100000")
    lifetime = sum(CommissionAccrual.objects.values_list("amount", flat=True))
    assert Decimal(body["commission_lifetime"]) == lifetime
    assert Decimal(body["commission_this_month"]) < lifetime


def test_client_list_shows_only_own_clients_and_never_pan(partner, make_partner, make_borrower):
    mine = make_borrower(username="mine", referral=ReferralLink.objects.create(partner=partner, label="a"))
    make_borrower(username="theirs", referral=ReferralLink.objects.create(partner=make_partner(), label="b"))
    approved_line(mine)
    res = client_for(partner.user).get("/api/partner/clients/")
    rows = res.json()["results"]
    assert [r["username"] for r in rows] == ["mine"]
    assert rows[0]["line_status"] == "APPROVED"
    assert "pan" not in res.content.decode().lower()


@pytest.mark.parametrize("n_clients", [2, 15])
def test_dashboard_and_client_list_query_counts_do_not_grow_with_clients(
    partner, make_borrower, n_clients, django_assert_num_queries
):
    link = ReferralLink.objects.create(partner=partner, label="x")
    for _ in range(n_clients):
        line = active_line(make_borrower(referral=link))
        services.drawdown(line.borrower, line.pk, Decimal("20000"))
    services.accrue_for_day(date.today())
    c = client_for(partner.user)  # force-authenticated: no user/partner lookup queries
    with django_assert_num_queries(3):  # client count + line aggregate + commission aggregate
        assert c.get("/api/partner/dashboard/").status_code == 200
    with django_assert_num_queries(2):  # page count + one annotated page query
        assert c.get("/api/partner/clients/").status_code == 200
    with django_assert_num_queries(2):  # page count + one annotated page query
        assert c.get("/api/partner/referral-links/").status_code == 200


def test_commission_history_groups_by_month(partner, make_borrower):
    link = ReferralLink.objects.create(partner=partner, label="x")
    user = make_borrower(referral=link)
    line = active_line(user)
    services.drawdown(user, line.pk, Decimal("100000"))
    for day in (date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 1)):
        services.accrue_for_day(day)
    rows = client_for(partner.user).get("/api/partner/commissions/").json()
    assert [(r["month"], r["amount"]) for r in rows] == [("2026-08-01", "1.37"), ("2026-07-01", "2.74")]


def test_borrower_cannot_access_partner_endpoints(borrower):
    c = client_for(borrower)
    for path in ("dashboard/", "clients/", "commissions/", "referral-links/"):
        assert c.get(f"/api/partner/{path}").status_code == 403
