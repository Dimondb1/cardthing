"""
Claude checks each new page a shop read made before it goes live (catalogue/new_pages.py), then the pages already
on the site that may not be products (catalogue/not_products.py).

    python manage.py check_new_pages      # what cron runs every 10 minutes, within Claude's limits

Pages that cannot wait go up first: all of them while Claude cannot act, else those waiting longer than
new_pages.HOLD_LIMIT. Then Claude is asked about the rest, one at a time. The suspects among the live pages have
the time that is left, inside cron's 540 seconds, and their own monthly limit.
"""

import time

from django.core.management.base import BaseCommand

from catalogue import new_pages, not_products


class Command(BaseCommand):
    help = "Ask Claude whether each new page is a sealed product, before it goes live."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=new_pages.ROWS_PER_RUN)

    def handle(self, *args, limit=None, **options):
        started = time.monotonic()
        result = new_pages.run(limit=limit)
        self.stdout.write(f"New pages: {result.summary()}")
        left = new_pages.RUN_SECONDS - (time.monotonic() - started)
        if left < 60:
            self.stdout.write("Not a product: no time left this run.")
            return
        result = not_products.run(seconds=min(left, not_products.RUN_SECONDS))
        self.stdout.write(f"Not a product: {result.summary()}")
