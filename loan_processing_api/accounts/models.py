import re

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from core import crypto

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def validate_pan(value: str) -> str:
    value = (value or "").strip().upper()
    if not PAN_RE.match(value):
        raise ValidationError("PAN must look like ABCDE1234F.")
    return value


class User(AbstractUser):
    class Role(models.TextChoices):
        BORROWER = "BORROWER", "Borrower"
        PARTNER = "PARTNER", "Partner (MFD/RIA)"
        OPS = "OPS", "Operations"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.BORROWER, db_index=True)
    phone = models.CharField(max_length=15, blank=True)

    # PAN is never stored in plaintext: ciphertext for retrieval, keyed hash for
    # uniqueness/lookup, last four characters for display.
    pan_ciphertext = models.TextField(blank=True, editable=False)
    pan_index = models.CharField(max_length=64, blank=True, editable=False)
    pan_last4 = models.CharField(max_length=4, blank=True, editable=False)

    referred_via = models.ForeignKey(
        "partners.ReferralLink", null=True, blank=True, on_delete=models.SET_NULL, related_name="signups"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pan_index"], condition=~models.Q(pan_index=""), name="uniq_pan_per_user")
        ]

    def set_pan(self, pan: str) -> None:
        pan = validate_pan(pan)
        self.pan_ciphertext = crypto.encrypt(pan)
        self.pan_index = crypto.blind_index(pan)
        self.pan_last4 = pan[-4:]

    def get_pan(self) -> str:
        return crypto.decrypt(self.pan_ciphertext) if self.pan_ciphertext else ""

    @property
    def pan_masked(self) -> str:
        return ("X" * 6 + self.pan_last4) if self.pan_last4 else ""

    @property
    def is_borrower(self) -> bool:
        return self.role == self.Role.BORROWER

    @property
    def is_partner(self) -> bool:
        return self.role == self.Role.PARTNER

    @property
    def is_ops(self) -> bool:
        return self.role == self.Role.OPS or self.is_superuser
