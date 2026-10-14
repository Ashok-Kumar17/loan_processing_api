"""Populate the database with a realistic demo: an ops admin, one MFD partner with
two referral links, referred borrowers with active credit lines, and 45 days of
accrued interest + trail commission so the partner dashboard has data to show."""

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from lending import services
from lending.models import Holding, LedgerEntry
from partners.models import Partner, ReferralLink

PASSWORDS = {"admin": "Admin@12345", "partner_demo": "Partner@12345", "borrower_demo": "Borrower@12345"}
BACKDATE_DAYS = 45


class Command(BaseCommand):
    help = "Create demo users, a partner with referral links, credit lines and 45 days of accruals."

    def handle(self, *args, **opts):
        if User.objects.filter(username="partner_demo").exists():
            self.stdout.write(self.style.WARNING("Demo data already present - skipping."))
            return
        logging.getLogger("lending").setLevel(logging.WARNING)  # keep the 45-day backfill quiet
        with transaction.atomic():
            self._seed()
        self.stdout.write(self.style.SUCCESS("Demo data created. Logins:"))
        for user, pwd in PASSWORDS.items():
            self.stdout.write(f"  {user:<14} {pwd}")

    def _seed(self):
        User.objects.create_superuser("admin", "admin@example.com", PASSWORDS["admin"], role=User.Role.OPS)

        puser = User.objects.create_user("partner_demo", password=PASSWORDS["partner_demo"], role=User.Role.PARTNER)
        partner = Partner.objects.create(
            user=puser, kind=Partner.Kind.MFD, registration_code="ARN-104532", firm_name="Sharma Wealth Partners"
        )
        whatsapp = ReferralLink.objects.create(partner=partner, label="WhatsApp broadcast - Oct", code="SHARMA01")
        seminar = ReferralLink.objects.create(partner=partner, label="Investor seminar, Pune", code="SHARMA02")

        borrowers = [
            ("borrower_demo", "ABCDE1234F", whatsapp, Decimal("0.60")),
            ("priya_k", "DEMOA1001B", whatsapp, Decimal("0.35")),
            ("rahul_m", "DEMOB1002C", whatsapp, Decimal("0.80")),
            ("ananya_s", "DEMOC1003D", seminar, Decimal("0.20")),
            ("vikram_r", "DEMOD1004E", seminar, Decimal("0.55")),
            ("meera_j", "DEMOE1005F", seminar, None),  # signed up, no line yet
            ("arjun_t", "DEMOF9006G", whatsapp, Decimal("0.50")),  # bureau rejection (digits start with 9)
        ]
        start = timezone.localdate() - timedelta(days=BACKDATE_DAYS)
        for username, pan, link, utilisation in borrowers:
            user = User(username=username, role=User.Role.BORROWER, referred_via=link)
            user.set_password(PASSWORDS.get(username, "Borrower@12345"))
            user.set_pan(pan)
            user.save()
            holdings = services.sync_portfolio(user)
            if utilisation is None:
                continue
            pledges = [services.PledgeRequest(h.pk, (h.units / 2).quantize(Decimal("0.001"))) for h in holdings[:3]]
            eligible = sum(Holding.objects.get(pk=p.holding_id).eligible_value_of(p.units) for p in pledges)
            requested = (min(eligible, Decimal("25000000")) // 1000) * 1000  # round down to Rs 1,000
            line = services.apply_for_credit_line(user, requested, pledges)
            if line.status != line.Status.APPROVED:
                continue
            services.mark_liens(user, line.pk)
            entry = services.drawdown(user, line.pk, (line.sanctioned_limit * utilisation).quantize(Decimal("1")))
            LedgerEntry.objects.filter(pk=entry.pk).update(value_date=start)

        for offset in range(BACKDATE_DAYS):
            services.accrue_for_day(start + timedelta(days=offset))
