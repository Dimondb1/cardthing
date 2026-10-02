"""
Re-check stock on the products most likely to change, far more often than
the hourly import can.

    python manage.py watch_stock            # up to 300 listings
    python manage.py watch_stock --limit 50

The hourly import reads whole shops, which takes an hour or more across all
of them. This command instead asks each shop about single products, which is
cheap: a Shopify shop answers /products/<handle>.js in a few milliseconds,
and a website shop's product page carries the same data. It checks, in
order: listings that were in stock but are not now (so the site never shows
a sold-out item as in stock for long), then out-of-stock listings of the
products people click most, then the rest by how long ago they were checked.
A listing that comes back is stamped, and the home page shows it under
"Back in stock". Meant for a cron entry every ten minutes.
"""

import json
import time
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from catalogue import pricing
from catalogue import importers
from catalogue.importers import ImportError_, money, page_offer
from catalogue.models import Listing, OutboundClick, Retailer


def shopify_js_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + ".js", "", ""))


_session_fetches = {}


def check_listing(listing, fetch=None):
    """(price, availability) from the shop right now, or None if it could not be read."""
    fetch = fetch or importers.fetch
    if listing.retailer.source_type == Retailer.Source.SHOPIFY and "/products/" in listing.url:
        try:
            data = json.loads(fetch(shopify_js_url(listing.url)))
        except (ImportError_, json.JSONDecodeError):
            return None
        variants = data.get("variants") or []
        available = [v for v in variants if v.get("available")]
        chosen = min(available or variants, key=lambda v: v.get("price", 0), default=None)
        if chosen is None:
            return None
        price = money(chosen.get("price", 0) / 100 if isinstance(chosen.get("price"), int) else chosen.get("price"))
        if price is None:
            return None
        if not available:
            return price, Listing.Availability.OUT_OF_STOCK
        # A pre-order stays a pre-order; the hourly import decides otherwise.
        if listing.availability == Listing.Availability.PREORDER:
            return price, Listing.Availability.PREORDER
        return price, Listing.Availability.IN_STOCK
    if listing.retailer.session_url and fetch is importers.fetch:
        fetch = _session_fetches.setdefault(listing.retailer_id, importers.session_fetch(listing.retailer.session_url))
    try:
        offer = page_offer(listing.url, fetch(listing.url).decode("utf-8", "replace"))
    except ImportError_:
        return None
    if offer is None:
        return None
    return offer.price, offer.availability


class Command(BaseCommand):
    help = "Re-check the stock of the listings most likely to have changed."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--pause", type=float, default=0.3)

    def handle(self, *args, limit=300, pause=0.3, **options):
        now = timezone.now()
        popular = set(
            OutboundClick.objects.filter(created_at__gte=now - timedelta(days=7))
            .values("product").annotate(n=Count("id")).order_by("-n").values_list("product", flat=True)[:200]
        )
        base = Listing.objects.filter(is_active=True, retailer__is_active=True).exclude(retailer__source_type=Retailer.Source.MANUAL)
        recently_checked = Q(last_checked__gte=now - timedelta(minutes=8))
        queue = []
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
            listing = Listing.objects.select_related("retailer", "product").get(pk=listing.pk)
            result = check_listing(listing)
            checked += 1
            if result is None:
                continue
            price, availability = result
            if availability != listing.availability or price != listing.price:
                changed += 1
                if availability == Listing.Availability.IN_STOCK and listing.availability != Listing.Availability.IN_STOCK:
                    restocked += 1
                    self.stdout.write(f"back in stock: {listing.product.name} at {listing.retailer.name} £{price}")
            delivery = listing.retailer.delivery_for(price)
            pricing.record_check(listing, price=price, delivery_cost=delivery, availability=availability)
            if pause:
                time.sleep(pause)
        if restocked:
            from catalogue.signals import clear_list_caches

            clear_list_caches()
        self.stdout.write(f"{checked} checked, {changed} changed, {restocked} back in stock.")
