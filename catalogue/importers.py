"""
Bring real prices in from retailers.

Two sources are supported:

* Shopify stores expose every product at ``/products.json``. Most UK card
  shops run on Shopify. Offers are matched to CardScout products by barcode
  (EAN), so fill in the barcode on each product first.
* A CSV product feed (from an affiliate network, Google Merchant Center or
  the retailer) with columns ``ean``, ``url``, ``price`` and optionally
  ``availability`` (in_stock, preorder, out_of_stock), ``delivery`` and
  ``title``.

Run ``python manage.py import_prices`` from cron. Each run is recorded as an
ImportRun, with the retailer products that could not be matched, so the
missing barcodes can be added in admin.
"""

import csv
import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import pricing
from .matching import AUTO_LINK, SUGGEST, best_match
from .models import ImportRun, Listing, Product, Retailer, ShopProduct

logger = logging.getLogger(__name__)

USER_AGENT = "CardScout price check (+https://cardscout.example)"
TIMEOUT = 30
PREORDER_WORDS = re.compile(r"pre[\s-]?order", re.I)


@dataclass
class Offer:
    title: str
    url: str
    price: Decimal
    ean: str = ""
    availability: str = Listing.Availability.IN_STOCK
    delivery: Decimal | None = None
    image: str = ""


class ImportError_(Exception):
    pass


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise ImportError_(f"Could not fetch {url}: {exc}") from exc


def clean_ean(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 8 <= len(digits) <= 14 else ""


def ean_key(value):
    """Barcodes compare without leading zeros: a UPC is an EAN-13 with one dropped."""
    return clean_ean(value).lstrip("0")


MONEY_RE = re.compile(r"-?\d+(?:\.\d+)?")


def money(value):
    """'44.99', '£44.99', '44.99 GBP' and '1,249.00' all become Decimal('44.99') style values."""
    text = str(value or "").replace(",", "")
    if "GBP" not in text and "£" not in text and re.search(r"[A-Z]{3}", text):
        return None  # another currency
    match = MONEY_RE.search(text)
    if not match:
        return None
    try:
        return Decimal(match.group()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


# Shopify --------------------------------------------------------------------

def shopify_offers(retailer, fetch=fetch):
    base = retailer.source_url.rstrip("/")
    page = 1
    while True:
        raw = fetch(f"{base}/products.json?limit=250&page={page}")
        try:
            products = json.loads(raw).get("products", [])
        except json.JSONDecodeError as exc:
            raise ImportError_(f"{base} did not return Shopify JSON") from exc
        if not products:
            return
        for product in products:
            url = f"{base}/products/{product.get('handle', '')}"
            preorder = bool(PREORDER_WORDS.search(product.get("title", ""))) or any(
                PREORDER_WORDS.search(tag) for tag in product.get("tags", [])
            )
            images = product.get("images") or []
            product_image = images[0].get("src", "") if images else ""
            for variant in product.get("variants", []):
                price = money(variant.get("price"))
                if price is None:
                    continue
                variant_image = (variant.get("featured_image") or {}).get("src", "")
                if not variant.get("available", False):
                    availability = Listing.Availability.OUT_OF_STOCK
                elif preorder:
                    availability = Listing.Availability.PREORDER
                else:
                    availability = Listing.Availability.IN_STOCK
                title = product.get("title", "")
                if variant.get("title") and variant["title"] != "Default Title":
                    title = f"{title} ({variant['title']})"
                yield Offer(
                    title=title,
                    url=url,
                    price=price,
                    ean=clean_ean(variant.get("barcode")),
                    availability=availability,
                    image=variant_image or product_image,
                )
        page += 1


# CSV feed -------------------------------------------------------------------

AVAILABILITY_WORDS = {
    "in_stock": Listing.Availability.IN_STOCK,
    "in stock": Listing.Availability.IN_STOCK,
    "instock": Listing.Availability.IN_STOCK,
    "preorder": Listing.Availability.PREORDER,
    "pre-order": Listing.Availability.PREORDER,
    "pre_order": Listing.Availability.PREORDER,
    "out_of_stock": Listing.Availability.OUT_OF_STOCK,
    "out of stock": Listing.Availability.OUT_OF_STOCK,
    "outofstock": Listing.Availability.OUT_OF_STOCK,
}


def feed_offers(text):
    reader = csv.DictReader(io.StringIO(text))
    fields = {name.strip().lower(): name for name in reader.fieldnames or []}

    def col(row, *names):
        for name in names:
            if name in fields:
                return (row.get(fields[name]) or "").strip()
        return ""

    for row in reader:
        # Awin: search_price / store_price; Google Merchant: price "44.99 GBP".
        price = money(col(row, "sale_price", "price", "search_price", "store_price", "display_price"))
        url = col(row, "url", "link", "aw_deep_link", "merchant_deep_link", "product_url")
        if price is None or not url:
            continue
        stock_word = col(row, "availability", "stock_status", "in_stock").lower()
        if stock_word in ("1", "true", "yes", "y"):
            stock_word = "in_stock"
        elif stock_word in ("0", "false", "no", "n"):
            stock_word = "out_of_stock"
        availability = AVAILABILITY_WORDS.get(stock_word, Listing.Availability.IN_STOCK)
        yield Offer(
            title=col(row, "title", "product_name", "name"),
            url=url,
            price=price,
            ean=clean_ean(col(row, "ean", "gtin", "barcode", "upc")),
            availability=availability,
            delivery=money(col(row, "delivery", "shipping", "delivery_cost", "delivery_cost_gbp")),
            image=col(row, "image_link", "image_url", "aw_image_url", "merchant_image_url", "image"),
        )


# Applying offers ------------------------------------------------------------

def link_key(url):
    """Product links compare without scheme, www, query string or trailing slash."""
    parsed = urllib.parse.urlsplit(url.strip().lower())
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    # Shopify serves the same product at /products/x and /en-gb/products/x.
    if "/products/" in path:
        path = "/products/" + path.split("/products/", 1)[1]
    return host + path


def apply_offers(retailer, offers, checked_at=None):
    """Update listings from ``offers``.

    Offers match a product by barcode, or by the link of a listing that was
    added by hand for this retailer. Returns (found, updated, unmatched titles).
    """
    checked_at = checked_at or timezone.now()
    products_by_link = {
        link_key(url): pk
        for pk, url in Listing.objects.filter(retailer=retailer).values_list("product_id", "url")
    }
    products_by_ean = {}
    for pk, ean in Product.objects.exclude(ean="").values_list("pk", "ean"):
        if ean_key(ean):
            products_by_ean[ean_key(ean)] = pk
    found = updated = 0
    unmatched = []
    seen_products = set()
    images_by_product = {}
    catalogue = list(Product.objects.filter(is_active=True).values_list("pk", "name"))
    ignored = set(
        ShopProduct.objects.filter(retailer=retailer, status=ShopProduct.Status.IGNORED).values_list("url", flat=True)
    )

    with transaction.atomic():
        for offer in offers:
            found += 1
            product_pk = products_by_ean.get(ean_key(offer.ean)) if offer.ean else None
            if product_pk is None:
                product_pk = products_by_link.get(link_key(offer.url))
            if product_pk is None and offer.url not in ignored:
                # No barcode and no hand-made link: guess from the name.
                match, value = best_match(offer.title, catalogue)
                if match and value >= AUTO_LINK and match[0] not in seen_products:
                    product_pk = match[0]
                    ShopProduct.objects.update_or_create(
                        retailer=retailer, url=offer.url,
                        defaults={"title": offer.title, "price": offer.price, "image_url": offer.image,
                                  "suggested_id": product_pk, "confidence": value,
                                  "status": ShopProduct.Status.LINKED, "last_seen": checked_at},
                    )
                elif match and value >= SUGGEST:
                    ShopProduct.objects.update_or_create(
                        retailer=retailer, url=offer.url,
                        defaults={"title": offer.title, "price": offer.price, "image_url": offer.image,
                                  "suggested_id": match[0], "confidence": value, "last_seen": checked_at},
                    )
                    unmatched.append(f"{offer.title} -> maybe {match[1]} ({value}%)")
                    continue
            if product_pk is None:
                unmatched.append(f"{offer.title} [{offer.ean or 'no barcode'}] {offer.url}")
                continue
            if not offer.url.lower().startswith(("http://", "https://")):
                unmatched.append(f"{offer.title} [link is not a web address]")
                continue
            listing, _created = Listing.objects.get_or_create(
                product_id=product_pk, retailer=retailer, defaults={"url": offer.url, "price": offer.price}
            )
            if product_pk in seen_products and listing.availability != Listing.Availability.OUT_OF_STOCK:
                # Several variants of one product: keep the cheapest one that is in stock.
                if offer.availability == Listing.Availability.OUT_OF_STOCK or offer.price >= listing.price:
                    continue
            seen_products.add(product_pk)
            listing.url = offer.url
            listing.save(update_fields=["url"])
            if offer.image and getattr(settings, "CARDSCOUT_USE_FEED_IMAGES", True):
                images_by_product.setdefault(product_pk, offer.image)
            delivery = offer.delivery if offer.delivery is not None else retailer.delivery_for(offer.price)
            pricing.record_check(
                listing,
                price=offer.price,
                delivery_cost=delivery,
                availability=offer.availability,
                checked_at=checked_at,
            )
            updated += 1

        # Fill in images for products that have none.
        if images_by_product:
            for product in Product.objects.filter(pk__in=images_by_product, image="", image_url=""):
                product.image_url = images_by_product[product.pk][:1000]
                product.save(update_fields=["image_url"])

        # Anything the retailer no longer lists is out of stock there.
        Listing.objects.filter(retailer=retailer).exclude(product_id__in=seen_products).exclude(
            availability=Listing.Availability.OUT_OF_STOCK
        ).update(availability=Listing.Availability.OUT_OF_STOCK, last_checked=checked_at)

    return found, updated, unmatched


def run_import(retailer, feed_path=None, fetch=fetch):
    run = ImportRun.objects.create(retailer=retailer)
    try:
        if retailer.source_type == Retailer.Source.SHOPIFY:
            if not retailer.source_url:
                raise ImportError_("Set the shop address on the retailer first.")
            offers = shopify_offers(retailer, fetch=fetch)
        elif retailer.source_type == Retailer.Source.FEED:
            if feed_path:
                text = open(feed_path, encoding="utf-8-sig").read()
            elif retailer.source_url:
                text = fetch(retailer.source_url).decode("utf-8-sig")
            else:
                raise ImportError_("Set the feed address on the retailer or pass a file.")
            offers = feed_offers(text)
        else:
            raise ImportError_("This retailer's prices are entered by hand.")
        found, updated, unmatched = apply_offers(retailer, offers)
        run.offers_found = found
        run.listings_updated = updated
        run.unmatched = "\n".join(unmatched)
    except (ImportError_, OSError) as exc:
        run.error = str(exc)
        logger.error("Import for %s failed: %s", retailer, exc)
    run.finished_at = timezone.now()
    run.save()
    from django.core.cache import cache

    from .signals import HOME_CACHE_KEY

    cache.delete(HOME_CACHE_KEY)
    return run
