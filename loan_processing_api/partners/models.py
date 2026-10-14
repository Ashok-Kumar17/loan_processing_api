import secrets
import string

from django.conf import settings
from django.db import models

_ALPHABET = string.ascii_uppercase + string.digits


def new_referral_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


class Partner(models.Model):
    """A Mutual Fund Distributor (ARN holder) or SEBI Registered Investment Adviser."""

    class Kind(models.TextChoices):
        MFD = "MFD", "Mutual Fund Distributor"
        RIA = "RIA", "Registered Investment Adviser"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="partner")
    kind = models.CharField(max_length=3, choices=Kind.choices)
    registration_code = models.CharField(max_length=20, unique=True, help_text="ARN for MFDs, SEBI reg. no. for RIAs")
    firm_name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.firm_name} ({self.registration_code})"


class ReferralLink(models.Model):
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="referral_links")
    code = models.CharField(max_length=12, unique=True, default=new_referral_code)
    label = models.CharField(max_length=80, help_text="Campaign name, e.g. 'Diwali WhatsApp blast'")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} - {self.label}"


class CommissionAccrual(models.Model):
    """Daily trail commission earned by a partner on a referred credit line's outstanding principal."""

    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="commissions")
    credit_line = models.ForeignKey("lending.CreditLine", on_delete=models.CASCADE, related_name="commissions")
    accrual_date = models.DateField()
    principal_base = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["credit_line", "accrual_date"], name="uniq_commission_per_line_per_day")
        ]
        indexes = [models.Index(fields=["partner", "accrual_date"])]
