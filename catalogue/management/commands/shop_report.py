"""
Numbers to show a shop when asking it to start an affiliate programme.

    python manage.py shop_report jet-cards
    python manage.py shop_report            # every shop, one line each

For one shop it prints how many products we list, how many are in stock,
on how many it is the cheapest shop, which big shops it beats and by how
much, and the clicks we sent it in the last 30 days.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from catalogue.models import Listing, OutboundClick, Product, Retailer
from catalogue.offers import buyable_prefetch

CLICK_DAYS = 30
EXAMPLES = 8


def shop_figures(retailer, days=CLICK_DAYS):
    """Counts for one shop plus the products where it is cheapest."""
    listings = Listing.objects.filter(retailer=retailer, is_active=True)
    products = (
        Product.objects.filter(listings__in=listings.buyable())
        .distinct()
        .prefetch_related(buyable_prefetch())
    )
    cheapest = []
    beaten = {}
    for product in products:
        offers = product.offers
        if not offers or offers[0].retailer_id != retailer.pk:
            continue
        best = offers[0]
        if len(offers) > 1:
            second = offers[1]
            cheapest.append((product, best, second))
            beaten[second.retailer.name] = beaten.get(second.retailer.name, 0) + 1
        else:
            cheapest.append((product, best, None))
    cheapest.sort(
        key=lambda row: (row[2].delivered_price - row[1].delivered_price) if row[2] else 0,
        reverse=True,
    )
    since = timezone.now() - timedelta(days=days)
    return {
        "listed": listings.count(),
        "in_stock": listings.buyable().count(),
        "cheapest": cheapest,
        "beaten": sorted(beaten.items(), key=lambda item: -item[1]),
        "clicks": OutboundClick.objects.filter(retailer=retailer, created_at__gte=since).count(),
    }


class Command(BaseCommand):
    help = "Show what RipRaptor does for a shop: listings, where it is cheapest, clicks sent."

    def add_arguments(self, parser):
        parser.add_argument("slug", nargs="?", help="Retailer slug. Leave out for every shop.")

    def handle(self, *args, slug=None, **options):
        if slug:
            try:
                retailer = Retailer.objects.get(slug=slug)
            except Retailer.DoesNotExist:
                raise CommandError(f"No retailer with slug {slug!r}.")
            self.report(retailer)
            return
        since = timezone.now() - timedelta(days=CLICK_DAYS)
        clicks = dict(
            OutboundClick.objects.filter(created_at__gte=since)
            .values_list("retailer_id")
            .annotate(n=Count("id"))
            .values_list("retailer_id", "n")
        )
        self.stdout.write(f"{'shop':28} {'listed':>7} {'in stock':>9} {'cheapest':>9} {'clicks':>7}")
        for retailer in Retailer.objects.filter(is_active=True).order_by("name"):
            figures = shop_figures(retailer)
            self.stdout.write(
                f"{retailer.name[:28]:28} {figures['listed']:7} {figures['in_stock']:9} "
                f"{len(figures['cheapest']):9} {clicks.get(retailer.pk, 0):7}"
            )

    def report(self, retailer):
        figures = shop_figures(retailer)
        self.stdout.write(f"{retailer.name} ({retailer.website})")
        self.stdout.write(f"  Products listed: {figures['listed']}")
        self.stdout.write(f"  In stock now: {figures['in_stock']}")
        self.stdout.write(f"  Cheapest UK shop on: {len(figures['cheapest'])} products")
        self.stdout.write(f"  Clicks sent in the last {CLICK_DAYS} days: {figures['clicks']}")
        if figures["beaten"]:
            self.stdout.write("  Beats:")
            for name, count in figures["beaten"][:EXAMPLES]:
                self.stdout.write(f"    {name}: {count} products")
        if figures["cheapest"]:
            self.stdout.write("  Biggest wins:")
            for product, best, second in figures["cheapest"][:EXAMPLES]:
                if second:
                    saving = second.delivered_price - best.delivered_price
                    self.stdout.write(
                        f"    {product.name}: £{best.delivered_price} delivered, "
                        f"£{saving} under {second.retailer.name}"
                    )
                else:
                    self.stdout.write(f"    {product.name}: £{best.delivered_price} delivered, only shop in stock")
