"""
Claude finds the RRP of products that have none, from cited pages of the publisher or of UK shops the owner added
(catalogue/rrp_finder.py).

    python manage.py find_rrps              # what cron runs once a day, within Claude's limits
    python manage.py find_rrps --dry-run    # the products it would ask about, and each one's worst case, sending nothing

It runs only while Claude may act (Things to check: on, with a key, Let Claude act).
"""

from django.core.management.base import BaseCommand

from catalogue import rrp_finder


class Command(BaseCommand):
    help = "Ask Claude for the RRP of products that have none, from the publisher's or UK shops' pages."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=rrp_finder.PER_RUN)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, limit=None, dry_run=False, **options):
        result = rrp_finder.run(limit=limit, dry_run=dry_run)
        for line in result.lines:
            self.stdout.write(line)
        self.stdout.write(f"RRPs: {result.summary()}")
