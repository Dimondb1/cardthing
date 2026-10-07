"""
List products whose cheapest offer is far under the next one: usually two
different things matched as one (a pack against a box), not a bargain.

    python manage.py suspect_savings

The site never shows these savings. The same list is in admin under Things
to check, with a button to hide whichever listing is the wrong product.
"""

from django.core.management.base import BaseCommand

from catalogue import offers
from catalogue.checks import wrong_matches


class Command(BaseCommand):
    help = "List products whose price gap is too large to be a real saving."

    def handle(self, *args, **options):
        found = 0
        for product, summary in wrong_matches():
            found += 1
            best, second = summary.best, summary.second
            self.stdout.write(
                f"{product.name}: £{best.delivered_price} at {best.retailer.name} against "
                f"£{second.delivered_price} at {second.retailer.name}\n"
                f"  {best.url}\n  {second.url}"
            )
        self.stdout.write(f"{found} products with a gap over {offers.MAX_REAL_PERCENT}%. Their savings are not shown.")
