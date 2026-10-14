"""Bank payout adapter (IMPS/NEFT). The mock returns a UTR-like reference."""

import uuid
from decimal import Decimal


def transfer(beneficiary: str, amount: Decimal) -> str:
    return "UTR" + uuid.uuid4().hex[:16].upper()
