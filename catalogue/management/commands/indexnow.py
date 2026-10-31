"""
Tell Bing and the other IndexNow search engines which product pages changed since the last run (catalogue/indexnow.py).
The hourly cron runs it.

    python manage.py indexnow [--dry-run]
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalogue import indexnow


class Command(BaseCommand):
    help = "Send the product pages changed since the last run to IndexNow (Bing and others)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count the pages and send nothing.")

    def handle(self, *args, dry_run=False, **options):
        if not settings.RIPRAPTOR_INDEXNOW and not dry_run:
            self.stdout.write("IndexNow is off (RIPRAPTOR_INDEXNOW=0).")
            return
        try:
            sent = indexnow.run(dry_run=dry_run)
        except indexnow.IndexNowError as exc:
            raise CommandError(f"{exc}. The same pages go again next hour.") from exc
        self.stdout.write(f"{sent} changed product pages {'would be sent' if dry_run else 'sent'} to IndexNow.")
