from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.money import ZERO, to_money


class Holding(models.Model):
    class RTA(models.TextChoices):
        CAMS = "CAMS", "CAMS"
        KFINTECH = "KFINTECH", "KFintech"

    class Category(models.TextChoices):
        EQUITY = "EQUITY", "Equity"
        HYBRID = "HYBRID", "Hybrid"
        DEBT = "DEBT", "Debt"

    borrower = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="holdings")
    rta = models.CharField(max_length=10, choices=RTA.choices)
    folio = models.CharField(max_length=20)
    isin = models.CharField(max_length=12)
    scheme_name = models.CharField(max_length=120)
    category = models.CharField(max_length=6, choices=Category.choices)
    units = models.DecimalField(max_digits=18, decimal_places=3)
    nav = models.DecimalField(max_digits=12, decimal_places=4)
    nav_date = models.DateField()
    pledged_units = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["borrower", "folio", "isin"], name="uniq_holding_per_folio"),
            models.CheckConstraint(
                condition=Q(pledged_units__gte=0) & Q(pledged_units__lte=F("units")),
                name="pledged_units_within_holding",
            ),
        ]

    @property
    def free_units(self) -> Decimal:
        return self.units - self.pledged_units

    @property
    def ltv(self) -> Decimal:
        return settings.LENDING["LTV"][self.category]

    @property
    def market_value(self) -> Decimal:
        return self.value_of(self.units)

    @property
    def eligible_value(self) -> Decimal:
        """Additional credit this holding can still support (free units only)."""
        return self.eligible_value_of(self.free_units)

    def value_of(self, units: Decimal) -> Decimal:
        return to_money(units * self.nav)

    def eligible_value_of(self, units: Decimal) -> Decimal:
        return to_money(units * self.nav * self.ltv)

    def __str__(self):
        return f"{self.scheme_name} [{self.folio}]"


class CreditLine(models.Model):
    class Status(models.TextChoices):
        APPROVED = "APPROVED", "Approved - awaiting lien"
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    OPEN_STATUSES = (Status.APPROVED, Status.ACTIVE)

    borrower = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="credit_lines")
    partner = models.ForeignKey(
        "partners.Partner", null=True, blank=True, on_delete=models.SET_NULL, related_name="credit_lines"
    )
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    requested_limit = models.DecimalField(max_digits=14, decimal_places=2)
    sanctioned_limit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    apr = models.DecimalField(max_digits=5, decimal_places=2)
    bureau_score = models.PositiveSmallIntegerField(null=True)
    outstanding_principal = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    interest_due = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    rejection_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            # The database itself refuses any write that would over-draw the line.
            models.CheckConstraint(
                condition=Q(outstanding_principal__gte=0) & Q(outstanding_principal__lte=F("sanctioned_limit")),
                name="outstanding_within_limit",
            ),
            models.CheckConstraint(condition=Q(interest_due__gte=0), name="interest_due_non_negative"),
            # At most one open (approved or active) credit line per borrower.
            models.UniqueConstraint(
                fields=["borrower"], condition=Q(status__in=["APPROVED", "ACTIVE"]), name="one_open_line_per_borrower"
            ),
        ]
        indexes = [models.Index(fields=["partner", "status"])]

    @property
    def available_limit(self) -> Decimal:
        if self.status != self.Status.ACTIVE:
            return ZERO
        return self.sanctioned_limit - self.outstanding_principal

    @property
    def total_due(self) -> Decimal:
        return self.outstanding_principal + self.interest_due

    def __str__(self):
        return f"CreditLine#{self.pk} {self.status}"


class Pledge(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        PLEDGED = "PLEDGED", "Lien marked"
        RELEASED = "RELEASED", "Released"

    credit_line = models.ForeignKey(CreditLine, on_delete=models.CASCADE, related_name="pledges")
    holding = models.ForeignKey(Holding, on_delete=models.PROTECT, related_name="pledges")
    units = models.DecimalField(max_digits=18, decimal_places=3)
    nav_at_pledge = models.DecimalField(max_digits=12, decimal_places=4)
    ltv = models.DecimalField(max_digits=4, decimal_places=2)
    eligible_value = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    lien_reference = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(units__gt=0), name="pledge_units_positive")]

    def __str__(self):
        return f"Pledge #{self.pk} ({self.status})"


class LedgerEntry(models.Model):
    """Append-only ledger. `balance_after` is the outstanding principal after this entry."""

    class Type(models.TextChoices):
        DRAWDOWN = "DRAWDOWN", "Drawdown"
        REPAYMENT = "REPAYMENT", "Repayment"
        INTEREST = "INTEREST", "Interest accrual"

    credit_line = models.ForeignKey(CreditLine, on_delete=models.PROTECT, related_name="ledger")
    entry_type = models.CharField(max_length=10, choices=Type.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    principal_component = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    interest_component = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=32, blank=True)
    value_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "ledger entries"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="ledger_amount_positive"),
            # Interest is accrued at most once per line per day, even if the job is re-run.
            models.UniqueConstraint(
                fields=["credit_line", "value_date"],
                condition=Q(entry_type="INTEREST"),
                name="one_interest_accrual_per_day",
            ),
        ]
        indexes = [models.Index(fields=["credit_line", "entry_type", "value_date"])]

    def __str__(self):
        return f"#{self.pk} {self.entry_type} {self.amount}"
