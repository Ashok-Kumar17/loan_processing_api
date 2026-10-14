from decimal import Decimal

import pytest
from django.db import connection

from core import crypto
from core.money import daily_interest, to_money


def test_to_money_rounds_half_up_to_paise():
    assert to_money("10.005") == Decimal("10.01")
    assert to_money("10.004") == Decimal("10.00")
    assert to_money(3) == Decimal("3.00")


def test_daily_interest_is_simple_interest_over_365_days():
    # 1,00,000 at 10.40% p.a. for one day = 28.4931... -> 28.49
    assert daily_interest(Decimal("100000"), Decimal("10.40")) == Decimal("28.49")
    assert daily_interest(Decimal("0"), Decimal("10.40")) == Decimal("0.00")


def test_encrypt_round_trip_and_ciphertext_is_randomised():
    a, b = crypto.encrypt("ABCDE1234F"), crypto.encrypt("ABCDE1234F")
    assert a != b  # Fernet uses a random IV
    assert crypto.decrypt(a) == crypto.decrypt(b) == "ABCDE1234F"


def test_blind_index_is_deterministic_and_case_insensitive():
    assert crypto.blind_index("abcde1234f") == crypto.blind_index("ABCDE1234F")
    assert crypto.blind_index("ABCDE1234F") != crypto.blind_index("ABCDE1234G")


def test_mask_pan():
    assert crypto.mask_pan("ABCDE1234F") == "XXXXXX234F"


@pytest.mark.django_db
def test_health_endpoint(api):
    res = api.get("/health/")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": connection.vendor}


@pytest.mark.django_db
def test_openapi_schema_and_docs_render(api):
    assert api.get("/api/schema/").status_code == 200
    assert api.get("/api/docs/").status_code == 200


@pytest.mark.django_db
def test_partner_dashboard_page_renders(api):
    res = api.get("/partner/")
    assert res.status_code == 200
    assert b"Partner Desk" in res.content


@pytest.mark.django_db
def test_admin_is_view_only_for_money_and_state(admin_client):
    """Credit lines and the ledger can be inspected in the admin but never edited there."""
    from lending.models import CreditLine

    assert admin_client.get("/admin/lending/creditline/").status_code == 200
    assert admin_client.get("/admin/lending/creditline/add/").status_code == 403
    assert not CreditLine.objects.exists()
