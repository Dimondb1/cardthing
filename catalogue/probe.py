"""
Asking a shop about one listing, rather than reading the whole shop.

A Shopify shop answers /products/<handle>.js in a few milliseconds; any
other shop's product page carries the same facts as schema.org data. The
background reader probes the listings people care about every ten minutes
this way, and watch_stock does the same from cron when the reader is not
running.
"""

import json
from urllib.parse import urlsplit, urlunsplit

from django.utils import timezone

from . import importers, pricing
from .importers import ImportError_, money, page_offer
from .models import Listing, Retailer

# Listings read through their own shop's API or product page. A marketplace price comes from its
# daily search, and a price entered by hand is never fetched.
NOT_PROBED = (Retailer.Source.EBAY, Retailer.Source.AMAZON, Retailer.Source.MANUAL)

# One cookie-keeping fetch per shop that needs a session, kept for the life of the process.
_session_fetches = {}


def shopify_js_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + ".js", "", ""))


def released(product, today=None):
    """True when the product's release date is known and has come."""
    when = product.effective_release_date
    return when is not None and when <= (today or timezone.localdate())


def settle_preorder(listing, availability, today=None):
    """What a probe may say about a listing that is on pre-order.

    A shop often marks a pre-order as available, so "available" alone never turns a pre-order into
    stock. It does once the product's release date is known and has come; otherwise it stays a
    pre-order and the next whole-shop read decides.
    """
    if (
        listing.availability == Listing.Availability.PREORDER
        and availability == Listing.Availability.IN_STOCK
        and not released(listing.product, today)
    ):
        return Listing.Availability.PREORDER
    return availability


def probe_listing(listing, fetch=None, today=None):
    """(price, availability) from the shop right now, or None if it could not be read or is not probed."""
    if listing.retailer.source_type in NOT_PROBED:
        return None
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
        return price, settle_preorder(listing, Listing.Availability.IN_STOCK, today)
    if listing.retailer.session_url and fetch is importers.fetch:
        fetch = _session_fetches.setdefault(listing.retailer_id, importers.session_fetch(listing.retailer.session_url))
    try:
        offer = page_offer(listing.url, fetch(listing.url).decode("utf-8", "replace"))
    except ImportError_:
        return None
    if offer is None:
        return None
    return offer.price, settle_preorder(listing, offer.availability, today)


def record_probe(listing, result, checked_at=None):
    """Save what a probe found. Returns "restocked", "changed", "same", or None when nothing was saved.

    No price is not a price: a listing the shop says is available at nothing is left as it was.
    """
    if result is None:
        return None
    price, availability = result
    if (price is None or price <= 0) and availability != Listing.Availability.OUT_OF_STOCK:
        return None
    outcome = "same"
    if availability != listing.availability or price != listing.price:
        outcome = "changed"
        if availability == Listing.Availability.IN_STOCK and listing.availability != Listing.Availability.IN_STOCK:
            outcome = "restocked"
    delivery = listing.retailer.delivery_for(price)
    pricing.record_check(listing, price=price, delivery_cost=delivery, availability=availability, checked_at=checked_at)
    return outcome
