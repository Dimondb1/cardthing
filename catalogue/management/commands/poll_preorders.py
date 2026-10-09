"""
Look for new pre-orders at the Shopify shops, the way the background reader does every 15 minutes.

    python manage.py poll_preorders                 # every shop whose look is due
    python manage.py poll_preorders --shop harbour  # one shop now, whatever its schedule says

One request per shop for its collection list; only a pre-order, coming soon, new releases or new
arrivals collection that changed since the last look has its products read. For hand use and tests: the
background reader runs the same look on its own.
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalogue import crawl
from catalogue.importers import poll_collections, retailer_fetch
from catalogue.models import Retailer

# Looks at different shops are this far apart.
GAP_SECONDS = 0.3


class Command(BaseCommand):
    help = "Look at each Shopify shop's collection list and read the pre-order collections that changed."

    def add_arguments(self, parser):
        parser.add_argument("--shop", help="Retailer slug: look at this shop now.")

    def handle(self, *args, shop=None, **options):
        if shop:
            shops = list(Retailer.objects.filter(slug=shop))
            if not shops:
                raise CommandError(f"No retailer with slug '{shop}'.")
            if shops[0].source_type != Retailer.Source.SHOPIFY or not shops[0].source_url:
                raise CommandError(f"{shops[0]} is not a Shopify shop with an address.")
        else:
            if crawl.all_paused():
                self.stdout.write("Pause all is on, so no shop was looked at.")
                return
            shops = list(Retailer.pulse_due(timezone.now()))
        if not shops:
            self.stdout.write("No shop is due a look.")
            return
        for n, retailer in enumerate(shops):
            if n:
                time.sleep(GAP_SECONDS)
            pulse = poll_collections(retailer, fetch=retailer_fetch(retailer))
            if pulse.no_list:
                self.stdout.write(f"{retailer}: no collection list, looked at weekly.")
            elif pulse.error:
                self.stdout.write(f"{retailer}: {pulse.error}")
            elif pulse.read:
                self.stdout.write(f"{retailer}: read {', '.join(pulse.read)}, {pulse.updated} listings checked.")
            else:
                self.stdout.write(f"{retailer}: nothing changed.")
