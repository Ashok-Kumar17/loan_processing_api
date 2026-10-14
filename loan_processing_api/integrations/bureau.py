"""Credit bureau adapter (CIBIL / Experian / CRIF style soft pull)."""

import hashlib
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class BureauReport:
    score: int
    active_accounts: int
    overdue_accounts: int


class BureauClient:
    def fetch_report(self, pan: str) -> BureauReport:  # pragma: no cover - interface
        raise NotImplementedError


class MockBureauClient(BureauClient):
    """Deterministic scores in 700-899.

    PANs whose digits start with 9 (e.g. ABCDE9123F) simulate a thin/poor file
    (score 640, one overdue account) so the rejection path can be demoed.
    """

    def fetch_report(self, pan: str) -> BureauReport:
        pan = pan.upper()
        if pan[5] == "9":
            return BureauReport(score=640, active_accounts=4, overdue_accounts=1)
        seed = int(hashlib.sha256(f"bureau:{pan}".encode()).hexdigest(), 16)
        return BureauReport(score=700 + seed % 200, active_accounts=seed % 5, overdue_accounts=0)


@lru_cache(maxsize=1)
def bureau_client() -> BureauClient:
    return import_string(settings.INTEGRATIONS["BUREAU_PROVIDER"])()
