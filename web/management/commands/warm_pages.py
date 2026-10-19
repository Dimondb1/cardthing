"""
Build the cached lists the home, Deals, drops and game pages read, so the next visitor does not wait for them.

    python manage.py warm_pages            # build every list again
    python manage.py warm_pages --due      # build only the lists cleared, missing or nearly expired (cron)
    python manage.py warm_pages --missing  # build only the lists not in the cache

Each list is replaced in place, so a visitor is served the old copy while the new one is built. The background
reader, tidy_all, import_prices and watch_stock already do this after they change prices (web/warming.py); cron
runs --due every five minutes for when the reader is not running. The installer runs it after emptying the cache.
"""

from django.core.management.base import BaseCommand

from web.warming import missing as missing_keys
from web.warming import warm_pages


class Command(BaseCommand):
    help = "Build the cached page lists (home deals, Deals, Out now, Coming soon) now."

    def add_arguments(self, parser):
        which = parser.add_mutually_exclusive_group()
        which.add_argument("--due", action="store_true",
                           help="Build only the lists cleared, missing or nearly expired.")
        which.add_argument("--missing", action="store_true", help="Build only the lists missing from the cache.")

    def handle(self, *args, due=False, missing=False, **options):
        if missing:
            took = warm_pages(only=missing_keys())
        else:
            took = warm_pages(fresh=not due)
        for name, seconds in took.items():
            self.stdout.write(f"{name}: {seconds:.2f} s")
        self.stdout.write(f"{len(took)} page lists ready in {sum(took.values()):.1f} s.")
