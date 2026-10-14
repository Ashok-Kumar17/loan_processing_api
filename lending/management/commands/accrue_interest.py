from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from lending.services import accrue_for_day


class Command(BaseCommand):
    help = "Accrue daily interest and partner trail commission (idempotent; safe to re-run)."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=date.fromisoformat, help="Day to accrue (default: today)")
        parser.add_argument("--days", type=int, default=1, help="Backfill this many days ending at --date")

    def handle(self, *args, **opts):
        end = opts["date"] or timezone.localdate()
        for offset in range(opts["days"] - 1, -1, -1):
            r = accrue_for_day(end - timedelta(days=offset))
            self.stdout.write(f"{r.day}: {r.lines} lines, interest Rs {r.interest}, commission Rs {r.commission}")
