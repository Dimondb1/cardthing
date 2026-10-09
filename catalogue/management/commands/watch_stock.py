"""
Re-check stock on the products most likely to change, far more often than
the hourly import can.

    python manage.py watch_stock            # up to 300 listings
    python manage.py watch_stock --limit 50

The hourly import reads whole shops, which takes an hour or more across all
of them. This command instead asks each shop about single products, which is
cheap: a Shopify shop answers /products/<handle>.js in a few milliseconds,
and a website shop's product page carries the same data. Half the budget
goes to the products people are watching: the most viewed and most saved
to a watchlist over the last two days, in or out of stock. The rest checks,
in order: in-stock listings of the products people click (so the site never
shows a sold-out item as in stock for long), then out-of-stock listings of
those, then everything else by how long ago it was checked. A listing that
comes back is stamped, and the home page shows it under "Back in stock".
Meant for a cron entry every ten minutes.
"""

import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Q, Sum
from django.utils import timezone

from catalogue import crawl
from catalogue.models import DailyPageView, Listing, OutboundClick, Product, Retailer, WorkerState
# check_listing is the probe the background reader uses too; the names stay importable from here.
from catalogue.probe import _session_fetches, probe_listing as check_listing, record_probe, shopify_js_url

__all__ = ["Command", "check_listing", "shopify_js_url", "watched_products", "_session_fetches"]

WATCHED_DAYS = 2        # page views and watchlist rows this recent count as "watching"
WATCHED_PRODUCTS = 200  # the most watched products considered each run


def watched_products(now):
    """Ids of the products most viewed or most saved to a watchlist lately, most watched first."""
    since = timezone.localdate(now) - timedelta(days=WATCHED_DAYS - 1)
    slugs = list(
        DailyPageView.objects.filter(date__gte=since, kind__in=[DailyPageView.Kind.PRODUCT, DailyPageView.Kind.WATCHED])
        .exclude(key="")
        .values("key").annotate(n=Sum("hits")).order_by("-n").values_list("key", flat=True)[:WATCHED_PRODUCTS]
    )
    ids = dict(Product.objects.filter(slug__in=slugs, is_active=True).values_list("slug", "pk"))
    return [ids[slug] for slug in slugs if slug in ids]


class Command(BaseCommand):
    help = "Re-check the stock of the listings most likely to have changed."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--pause", type=float, default=0.3)
        parser.add_argument(
            "--if-worker-dead", type=int, metavar="MINUTES",
            help="Do nothing when the background reader's heartbeat is younger than this: it checks stock itself.",
        )

    def handle(self, *args, limit=300, pause=0.3, if_worker_dead=None, **options):
        if if_worker_dead is not None and WorkerState.beating_within(if_worker_dead):
            self.stdout.write("The background reader is running, so it checks stock instead.")
            return
        if crawl.all_paused():
            # Pause all on the Crawl health page stops every request to the shops, stock checks included.
            self.stdout.write("Reading is paused for every shop. Resume all on the Crawl health page starts it again.")
            return
        now = timezone.now()
        popular = set(
            OutboundClick.objects.filter(created_at__gte=now - timedelta(days=7))
            .values("product").annotate(n=Count("id")).order_by("-n").values_list("product", flat=True)[:200]
        )
        # A shop the owner paused is not asked about single products either.
        base = (
            Listing.objects.filter(is_active=True, retailer__is_active=True, retailer__reading_paused=False)
            .exclude(retailer__source_type__in=[Retailer.Source.MANUAL, Retailer.Source.EBAY, Retailer.Source.AMAZON])
        )
        recently_checked = Q(last_checked__gte=now - timedelta(minutes=8))
        queue = []
        # 0. Up to half the budget: what people are viewing and watching, in or out of stock.
        watched = watched_products(now)
        if watched:
            rank = {pk: i for i, pk in enumerate(watched)}
            rows = base.filter(product_id__in=watched).exclude(recently_checked).exclude(availability=Listing.Availability.PREORDER)
            queue += sorted(rows, key=lambda l: (rank[l.product_id], l.last_checked))[: limit // 2]
        # 1. In stock now: confirm it still is, popular products first.
        queue += list(base.filter(availability=Listing.Availability.IN_STOCK, product_id__in=popular).exclude(recently_checked).order_by("last_checked")[:limit])
        # 2. Out of stock on popular products: the ones people are waiting for.
        queue += list(base.filter(availability=Listing.Availability.OUT_OF_STOCK, product_id__in=popular).exclude(recently_checked).order_by("last_checked")[:limit])
        # 3. Everything else out of stock, least recently checked first.
        queue += list(base.filter(availability=Listing.Availability.OUT_OF_STOCK).exclude(recently_checked).order_by("last_checked")[:limit])
        seen, checked, changed, restocked = set(), 0, 0, 0
        for listing in queue:
            if listing.pk in seen or checked >= limit:
                continue
            seen.add(listing.pk)
            listing = Listing.objects.select_related("retailer", "product__product_set").get(pk=listing.pk)
            result = check_listing(listing)
            checked += 1
            outcome = record_probe(listing, result)
            if outcome in ("changed", "restocked"):
                changed += 1
            if outcome == "restocked":
                restocked += 1
                self.stdout.write(f"back in stock: {listing.product.name} at {listing.retailer.name} £{result[0]}")
            if outcome is not None and pause:
                time.sleep(pause)
        if restocked:
            from catalogue.signals import clear_list_caches

            clear_list_caches(force=True)
        self.stdout.write(f"{checked} checked, {changed} changed, {restocked} back in stock.")
