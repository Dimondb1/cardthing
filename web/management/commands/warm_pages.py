"""
Build the cached lists the home, Deals, drops and game pages read, so the next visitor does not wait for them.

    python manage.py warm_pages            # build every list again
    python manage.py warm_pages --missing  # build only the lists not in the cache

The background reader, tidy_all, import_prices and watch_stock already do this after they change prices
(web/warming.py). The installer runs it after emptying the cache on a deploy.
"""

from django.core.management.base import BaseCommand

from web.warming import warm_pages


class Command(BaseCommand):
    help = "Build the cached page lists (home deals, Deals, Out now, Coming soon) now."

    def add_arguments(self, parser):
        parser.add_argument("--missing", action="store_true", help="Build only the lists missing from the cache.")

    def handle(self, *args, missing=False, **options):
        took = warm_pages(fresh=not missing)
        for name, seconds in took.items():
            self.stdout.write(f"{name}: {seconds:.2f} s")
        self.stdout.write(f"{len(took)} page lists ready in {sum(took.values()):.1f} s.")
