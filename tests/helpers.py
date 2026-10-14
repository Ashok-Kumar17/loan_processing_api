"""Shared test helpers (plain functions; fixtures live in conftest.py)."""

import itertools
import uuid
from decimal import Decimal

from rest_framework.test import APIClient

from lending import services
from lending.models import Holding

_pan_counter = itertools.count(1000)


def next_pan(first_digit: str = "1") -> str:
    """Unique valid PANs. first_digit='9' makes the mock bureau reject the borrower."""
    n = next(_pan_counter)
    return f"TESTA{first_digit}{n % 1000:03d}Z"


def client_for(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


BIG_UNITS = Decimal("100000.000")  # first CAMS scheme NAV 84.215 -> Rs 84.2L market value


def approved_line(user, fraction=Decimal("0.5"), requested=None):
    """Sync the portfolio, give the first holding a fixed 1,00,000 units (so limits are
    deterministic, ~Rs 21L at the default fraction) and pledge `fraction` of it."""
    holdings = services.sync_portfolio(user)
    Holding.objects.filter(pk=holdings[0].pk).update(units=BIG_UNITS)
    h = Holding.objects.get(pk=holdings[0].pk)
    units = (h.free_units * fraction).quantize(Decimal("0.001"))
    eligible = h.eligible_value_of(units)
    requested = requested if requested is not None else (eligible // 1000) * 1000
    return services.apply_for_credit_line(user, requested, [services.PledgeRequest(h.pk, units)])


def active_line(user, **kwargs):
    line = approved_line(user, **kwargs)
    return services.mark_liens(user, line.pk)


def idem_headers(key=None):
    return {"HTTP_IDEMPOTENCY_KEY": key or str(uuid.uuid4())}
