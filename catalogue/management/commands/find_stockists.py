"""
Look for other shops selling products that one shop sells, or none, the way the background reader does
every 5 minutes.

    python manage.py find_stockists                          # one batch of 30 products
    python manage.py find_stockists --requests 20            # ask the shops at most 20 times
    python manage.py find_stockists --budget-seconds 60      # start nothing new after a minute

Only Shopify shops already added are asked, under the same hourly caps as the background reader (20
requests a shop, 200 in all). For hand use and tests: the background reader runs the same search on its own.
"""

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalogue import crawl, finder


class Command(BaseCommand):
    help = "Look for products that one shop sells, or none, at the other Shopify shops already added."

    def add_arguments(self, parser):
        parser.add_argument("--requests", type=int, default=None, help="Ask the shops at most this many times.")
        parser.add_argument("--budget-seconds", type=int, default=120, help="Start no new search after this long.")
        parser.add_argument("--products", type=int, default=finder.BATCH, help="Look for at most this many products.")

    def handle(self, *args, requests=None, budget_seconds=120, products=finder.BATCH, **options):
        if crawl.all_paused():
            self.stdout.write("Pause all is on, so no shop was asked.")
            return
        began = time.monotonic()
        result = finder.find(
            products, requests=requests, clock=timezone.now,
            stop=lambda: time.monotonic() - began >= budget_seconds,
        )
        self.stdout.write(f"{result}.")
