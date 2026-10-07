"""
List products whose cheapest offer is far under the next one: usually two
different things matched as one (a pack against a box), not a bargain.

    python manage.py suspect_savings

The site never shows these savings. Open each product and untick "show on
site" on whichever listing is the wrong product.
"""

from django.core.management.base import BaseCommand

from catalogue import offers
from catalogue.models import Product


class Command(BaseCommand):
    help = "List products whose price gap is too large to be a real saving."

    def handle(self, *args, **options):
        products = Product.objects.for_lists().filter(lowest_price__isnull=False).prefetch_related(offers.buyable_prefetch())
        found = 0
        for product in products.order_by("name"):
            summary = offers.summarise(product)
            if not summary.suspect:
                continue
            found += 1
            best, second = summary.best, summary.second
            self.stdout.write(
                f"{product.name}: £{best.delivered_price} at {best.retailer.name} against "
                f"£{second.delivered_price} at {second.retailer.name}\n"
                f"  {best.url}\n  {second.url}"
            )
        self.stdout.write(f"{found} products with a gap over {offers.MAX_REAL_PERCENT}%. Their savings are not shown.")
