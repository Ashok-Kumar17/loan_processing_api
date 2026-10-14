import pytest
from django.db import connection

from accounts.models import User
from partners.models import ReferralLink

pytestmark = pytest.mark.django_db

PAYLOAD = {"username": "neha", "password": "Str0ng!Passw0rd", "pan": "abcde1234f", "phone": "9876543210"}


def test_register_stores_pan_encrypted_and_returns_it_masked(api):
    res = api.post("/api/auth/register/", PAYLOAD, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["pan_masked"] == "XXXXXX234F"
    assert "pan" not in res.json()

    user = User.objects.get(username="neha")
    assert user.get_pan() == "ABCDE1234F"
    with connection.cursor() as cur:  # the raw row must not contain the PAN anywhere
        cur.execute("SELECT pan_ciphertext, pan_index, pan_last4 FROM accounts_user WHERE id = %s", [user.id])
        row = " ".join(cur.fetchone())
    assert "ABCDE1234F" not in row.upper()


def test_duplicate_pan_is_rejected_case_insensitively(api):
    api.post("/api/auth/register/", PAYLOAD, format="json")
    res = api.post("/api/auth/register/", {**PAYLOAD, "username": "other", "pan": "ABCDE1234F"}, format="json")
    assert res.status_code == 400
    assert "pan" in res.json()


@pytest.mark.parametrize("pan", ["ABCD1234F", "12345ABCDE", "ABCDE12345", ""])
def test_invalid_pan_formats(api, pan):
    res = api.post("/api/auth/register/", {**PAYLOAD, "pan": pan}, format="json")
    assert res.status_code == 400


def test_weak_password_rejected(api):
    res = api.post("/api/auth/register/", {**PAYLOAD, "password": "password"}, format="json")
    assert res.status_code == 400


def test_referral_code_attributes_borrower_to_partner(api, partner):
    link = ReferralLink.objects.create(partner=partner, label="seminar")
    res = api.post("/api/auth/register/", {**PAYLOAD, "referral_code": link.code}, format="json")
    assert res.status_code == 201
    assert User.objects.get(username="neha").referred_via == link


def test_unknown_or_inactive_referral_code_rejected(api, partner):
    link = ReferralLink.objects.create(partner=partner, label="old", is_active=False)
    assert api.post("/api/auth/register/", {**PAYLOAD, "referral_code": "NOPE1234"}, format="json").status_code == 400
    assert api.post("/api/auth/register/", {**PAYLOAD, "referral_code": link.code}, format="json").status_code == 400


def test_jwt_login_and_me(api):
    api.post("/api/auth/register/", PAYLOAD, format="json")
    tokens = api.post("/api/auth/token/", {"username": "neha", "password": PAYLOAD["password"]}, format="json").json()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    me = api.get("/api/auth/me/").json()
    assert me["username"] == "neha" and me["role"] == "BORROWER"


def test_endpoints_require_authentication(api):
    assert api.get("/api/credit-lines/").status_code == 401
    assert api.get("/api/partner/dashboard/").status_code == 401


def test_login_is_rate_limited(api):
    codes = [
        api.post("/api/auth/token/", {"username": "x", "password": "y"}, format="json").status_code for _ in range(21)
    ]
    assert codes[:20] == [401] * 20
    assert codes[20] == 429
