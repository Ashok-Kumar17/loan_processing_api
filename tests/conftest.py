import uuid

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from accounts.models import User
from partners.models import Partner, ReferralLink

from .helpers import next_pan


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def make_borrower():
    def _make(username=None, pan=None, referral: ReferralLink | None = None) -> User:
        user = User(username=username or f"b_{uuid.uuid4().hex[:8]}", role=User.Role.BORROWER, referred_via=referral)
        user.set_password("Str0ng!Passw0rd")
        user.set_pan(pan or next_pan())
        user.save()
        return user

    return _make


@pytest.fixture
def borrower(make_borrower):
    return make_borrower(username="asha")


@pytest.fixture
def make_partner():
    def _make(username=None, firm="Test Wealth") -> Partner:
        user = User.objects.create_user(
            username or f"p_{uuid.uuid4().hex[:8]}", password="Str0ng!Passw0rd", role=User.Role.PARTNER
        )
        return Partner.objects.create(
            user=user, kind=Partner.Kind.MFD, registration_code=f"ARN-{uuid.uuid4().hex[:6]}", firm_name=firm
        )

    return _make


@pytest.fixture
def partner(make_partner):
    return make_partner(username="sharma", firm="Sharma Wealth")


@pytest.fixture
def ops_user():
    return User.objects.create_user("ops", password="Str0ng!Passw0rd", role=User.Role.OPS)
