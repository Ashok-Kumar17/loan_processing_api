"""Money helpers. All amounts are Decimal rupees with 2 decimal places -- never floats."""

from decimal import ROUND_HALF_UP, Decimal

PAISA = Decimal("0.01")
ZERO = Decimal("0.00")


def to_money(value) -> Decimal:
    """Round any numeric value to whole paise, half-up (banker-style rounding is not used for INR)."""
    return Decimal(value).quantize(PAISA, rounding=ROUND_HALF_UP)


def daily_interest(principal: Decimal, apr_percent: Decimal, days_in_year: int = 365) -> Decimal:
    """Simple interest for one day on `principal` at `apr_percent` % per annum."""
    return to_money(principal * apr_percent / Decimal(100) / Decimal(days_in_year))
