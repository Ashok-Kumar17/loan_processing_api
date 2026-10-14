"""Registrar & Transfer Agent (RTA) adapters: CAMS and KFintech.

In India mutual-fund units are serviced by one of two RTAs. A loan against
mutual funds needs both to (1) fetch the investor's holdings by PAN and
(2) mark a lien on pledged units so they cannot be redeemed until released.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string


class RTAError(Exception):
    """Raised when an RTA rejects a request (e.g. insufficient free units for a lien)."""


@dataclass(frozen=True)
class RTAHolding:
    rta: str
    folio: str
    isin: str
    scheme_name: str
    category: str  # EQUITY | DEBT | HYBRID
    units: Decimal
    nav: Decimal
    nav_date: date


class RTAClient:
    name = ""

    def fetch_holdings(self, pan: str) -> list[RTAHolding]:  # pragma: no cover - interface
        raise NotImplementedError

    def mark_lien(self, pan: str, folio: str, isin: str, units: Decimal) -> str:  # pragma: no cover
        raise NotImplementedError

    def release_lien(self, lien_reference: str) -> None:  # pragma: no cover
        raise NotImplementedError


# Fictional schemes, split by the RTA that services them.
_CATALOGUE = {
    "CAMS": [
        ("INF000C01011", "Northstar Bluechip Equity Fund - Direct Growth", "EQUITY", Decimal("84.2150")),
        ("INF000C01029", "Northstar Short Duration Debt Fund - Direct Growth", "DEBT", Decimal("31.6720")),
        ("INF000C01037", "Meridian Nifty 50 Index Fund - Direct Growth", "EQUITY", Decimal("212.9030")),
        ("INF000C01045", "Meridian Balanced Advantage Fund - Direct Growth", "HYBRID", Decimal("47.0815")),
    ],
    "KFINTECH": [
        ("INF000K01018", "Harbor Flexi Cap Fund - Direct Growth", "EQUITY", Decimal("66.4402")),
        ("INF000K01026", "Harbor Corporate Bond Fund - Direct Growth", "DEBT", Decimal("29.1187")),
        ("INF000K01034", "Lighthouse Midcap Opportunities - Direct Growth", "EQUITY", Decimal("118.5590")),
    ],
}


def _seed(pan: str, salt: str) -> int:
    return int(hashlib.sha256(f"{salt}:{pan.upper()}".encode()).hexdigest(), 16)


class _MockRTAClient(RTAClient):
    """Deterministic: the same PAN always returns the same portfolio."""

    def __init__(self):
        self._liens: dict[str, tuple[str, Decimal]] = {}

    def fetch_holdings(self, pan: str) -> list[RTAHolding]:
        seed = _seed(pan, self.name)
        holdings = []
        for i, (isin, scheme, category, nav) in enumerate(_CATALOGUE[self.name]):
            if (seed >> i) & 1 == 0 and i != 0:  # first scheme always present
                continue
            units = Decimal((seed >> (8 + 12 * i)) % 9000 + 1000) + Decimal("0.125")
            holdings.append(
                RTAHolding(
                    rta=self.name,
                    folio=f"{self.name[:2]}{seed % 10**8:08d}",
                    isin=isin,
                    scheme_name=scheme,
                    category=category,
                    units=units,
                    nav=nav,
                    nav_date=date.today(),
                )
            )
        return holdings

    def mark_lien(self, pan: str, folio: str, isin: str, units: Decimal) -> str:
        if units <= 0:
            raise RTAError("Lien units must be positive.")
        reference = f"{self.name[:2]}LN{uuid.uuid4().hex[:12].upper()}"
        self._liens[reference] = (isin, units)
        return reference

    def release_lien(self, lien_reference: str) -> None:
        self._liens.pop(lien_reference, None)


class MockCAMSClient(_MockRTAClient):
    name = "CAMS"


class MockKFintechClient(_MockRTAClient):
    name = "KFINTECH"


@lru_cache(maxsize=1)
def rta_clients() -> dict[str, RTAClient]:
    clients = [import_string(path)() for path in settings.INTEGRATIONS["RTA_PROVIDERS"]]
    return {client.name: client for client in clients}


def rta_for(name: str) -> RTAClient:
    return rta_clients()[name]
