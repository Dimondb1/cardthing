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
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from . import pricing
from .classify import GAMES, classify
from .matching import AUTO_LINK, SUGGEST, best_match, score
from .models import Game, ImportRun, Listing, Product, Retailer, ShopProduct

logger = logging.getLogger(__name__)

USER_AGENT = "CardScout price check (+https://cardscout.example)"
MAX_SHOPIFY_PAGES = 400
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
    shop_type: str = ""   # the shop's own category, for example "Booster Box"
    vendor: str = ""
    tags: tuple = ()


class ImportError_(Exception):
    pass


def fetch(url, retries=2):
    import http.client

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                last = exc
                continue
            raise ImportError_(f"Could not fetch {url}: {exc}") from exc
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
    raise ImportError_(f"Could not fetch {url}: {last}")


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
    first_handle = None
    while page <= MAX_SHOPIFY_PAGES:
        try:
            raw = fetch(f"{base}/products.json?limit=250&page={page}")
            products = json.loads(raw).get("products", [])
        except ImportError_:
            if page == 1:
                raise
            return  # past the last page
        except json.JSONDecodeError as exc:
            if page == 1:
                raise ImportError_(f"{base} did not return Shopify JSON") from exc
            return
        if not products:
            return
        handle = products[0].get("handle")
        if page == 1:
            first_handle = handle
        elif handle == first_handle:
            return  # the shop repeats its first page instead of ending
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
                    shop_type=product.get("product_type", "") or "",
                    vendor=product.get("vendor", "") or "",
                    tags=tuple(product.get("tags", []) or []),
                )
        page += 1


# Any website: sitemap + schema.org product data -----------------------------

SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
JSON_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
META = re.compile(r"<meta[^>]+(?:property|name)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']*)[\"']", re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
PRODUCT_PATH_WORDS = ("/product", "/products/", "/p/", "/item", "/shop/", "-p-")
MAX_PAGES = 3000


def sitemap_urls(base, fetch=fetch, limit=MAX_PAGES):
    """Every page address listed in the site's sitemap(s), product-looking ones first."""
    found, seen, queue = [], set(), [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/xmlsitemap.php"]
    try:
        robots = fetch(f"{base}/robots.txt").decode("utf-8", "replace")
        queue = [line.split(":", 1)[1].strip() for line in robots.splitlines() if line.lower().startswith("sitemap:")] + queue
    except ImportError_:
        pass
    while queue and len(found) < limit:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            text = fetch(url).decode("utf-8", "replace")
        except ImportError_:
            continue
        for loc in SITEMAP_LOC.findall(text):
            loc = loc.replace("&amp;", "&")
            if loc.endswith(".xml") or "sitemap" in loc.lower():
                queue.append(loc)
            else:
                found.append(loc)
    found = [u for u in found if not u.lower().endswith((".jpg", ".png", ".webp", ".pdf"))]
    found.sort(key=lambda u: 0 if any(w in u.lower() for w in PRODUCT_PATH_WORDS) else 1)
    return found[:limit]


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from _walk(node[key])


def _availability(text):
    text = (text or "").lower()
    if "preorder" in text or "pre-order" in text:
        return Listing.Availability.PREORDER
    if "outofstock" in text or "out_of_stock" in text or "soldout" in text or "discontinued" in text:
        return Listing.Availability.OUT_OF_STOCK
    return Listing.Availability.IN_STOCK


def page_offer(url, html):
    """An Offer from a product page's schema.org data or Open Graph tags, or None."""
    for block in JSON_LD.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            kind = node.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" not in kinds:
                continue
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = money(offers.get("price") or offers.get("lowPrice"))
            currency = (offers.get("priceCurrency") or "GBP").upper()
            if price is None or currency != "GBP":
                continue
            image = node.get("image") or ""
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url", "")
            ean = clean_ean(node.get("gtin13") or node.get("gtin") or node.get("gtin14") or node.get("gtin12") or "")
            if not ean:
                upc = re.search(r'"(?:upc|gtin|ean|barcode)"\s*:\s*"(\d{8,14})"', html)
                ean = clean_ean(upc.group(1)) if upc else ""
            return Offer(
                title=str(node.get("name", "")).strip(),
                url=offers.get("url") or url,
                price=price,
                ean=ean,
                availability=_availability(str(offers.get("availability", ""))),
                image=image,
            )
    meta = {key.lower(): value for key, value in META.findall(html)}
    upc = re.search(r'"(?:upc|gtin|ean|barcode)"\s*:\s*"(\d{8,14})"', html)
    if "product:price:amount" in meta and meta.get("product:price:currency", "GBP").upper() == "GBP":
        price = money(meta["product:price:amount"])
        if price is not None:
            title = meta.get("og:title") or (TITLE.search(html).group(1).strip() if TITLE.search(html) else "")
            return Offer(
                title=title, url=url, price=price,
                ean=clean_ean(meta.get("product:gtin13") or meta.get("product:ean") or meta.get("product:gtin") or (upc.group(1) if upc else "")),
                availability=_availability(meta.get("product:availability", "") or ("outofstock" if re.search(r"out of stock|sold out", html, re.I) and not re.search(r"add to (?:cart|basket)", html, re.I) else "")),
                image=meta.get("og:image", ""),
            )
    return None


def website_offers(retailer, fetch=fetch, pause=0.5, limit=MAX_PAGES):
    """Offers from every product page the site's sitemap lists."""
    base = retailer.source_url.rstrip("/")
    for url in sitemap_urls(base, fetch=fetch, limit=limit):
        try:
            html = fetch(url).decode("utf-8", "replace")
        except ImportError_:
            continue
        offer = page_offer(url, html)
        if offer and offer.title:
            yield offer
        if pause:
            time.sleep(pause)


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


def apply_offers(retailer, offers, checked_at=None, run=None):
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
    catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name"))
    ignored = set(
        ShopProduct.objects.filter(retailer=retailer, status=ShopProduct.Status.IGNORED).values_list("url", flat=True)
    )

    if True:
        for offer in offers:
            found += 1
            if run is not None and found % 250 == 0:
                ImportRun.objects.filter(pk=run.pk).update(offers_found=found, listings_updated=updated)
            product_pk = products_by_ean.get(ean_key(offer.ean)) if offer.ean else None
            if product_pk is None:
                product_pk = products_by_link.get(link_key(offer.url))
            if product_pk is None and offer.url not in ignored:
                # No barcode and no hand-made link: guess from the name.
                match, value = catalogue.best_match(offer.title)
                if (match is None or value < AUTO_LINK) and getattr(settings, "CARDSCOUT_AUTO_CATALOGUE", True):
                    created_pk = create_from_offer(offer, catalogue)
                    if created_pk is not None:
                        product_pk = created_pk
                        products_by_ean.update({})
                if product_pk is not None:
                    pass
                elif match and value >= AUTO_LINK and match[0] not in seen_products:
                    product_pk = match[0]
                    ShopProduct.objects.update_or_create(
                        retailer=retailer, url=offer.url,
                        defaults={"title": offer.title, "price": offer.price, "image_url": offer.image,
                                  "suggested_id": product_pk, "confidence": value,
                                  "status": ShopProduct.Status.LINKED, "last_seen": checked_at},
                    )
                elif match and value >= SUGGEST and classify(offer.title, offer.shop_type, offer.vendor, offer.tags, offer.price):
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


class Catalogue:
    """Our products, indexed by word so an offer is only scored against likely matches."""

    def __init__(self, rows):
        from .matching import words

        self.names = {}
        self.index = {}
        self._words = words
        for pk, name in rows:
            self.add(pk, name)

    def add(self, pk, name):
        self.names[pk] = name
        for word in set(self._words(name)):
            self.index.setdefault(word, set()).add(pk)

    def append(self, row):
        self.add(*row)

    def __iter__(self):
        return iter(self.names.items())

    def best_match(self, title):
        title_words = set(self._words(title))
        counts = {}
        for word in title_words:
            for pk in self.index.get(word, ()):
                counts[pk] = counts.get(pk, 0) + 1
        # Only products sharing at least two words (or all of a short name) are worth scoring.
        candidates = [(pk, self.names[pk]) for pk, n in counts.items() if n >= 2 or n >= len(set(self._words(self.names[pk])))]
        return best_match(title, candidates)


def create_from_offer(offer, catalogue):
    """Make a product for a sealed item no catalogue product matches. Returns its pk or None.

    ``catalogue`` is the live list of (pk, name) and is extended in place so
    later offers in the same run match the new product.
    """
    sealed = classify(offer.title, offer.shop_type, offer.vendor, offer.tags, offer.price)
    if sealed is None:
        return None
    # The clean name may already exist under a slightly different shop title.
    for pk, name in catalogue:
        if score(name, sealed.name) >= AUTO_LINK and score(sealed.name, name) >= AUTO_LINK:
            return pk
    game = Game.objects.filter(slug=sealed.game).first()
    if game is None:
        for slug, name, short, words in GAMES:
            if slug == sealed.game:
                game = Game.objects.create(name=name, short_name=short, slug=slug, search_aliases=", ".join(words),
                                           sort_order=Game.objects.count() + 1)
    from django.utils.text import slugify

    slug = slugify(sealed.name)[:220]
    existing = Product.objects.filter(slug=slug).first()
    if existing is not None:
        catalogue.append((existing.pk, existing.name))
        return existing.pk
    product = Product.objects.create(
        game=game, name=sealed.name, slug=slug, product_type=sealed.product_type,
        ean=offer.ean or "", image_url=offer.image[:1000] if offer.image else "",
    )
    catalogue.append((product.pk, product.name))
    return product.pk


def run_import(retailer, feed_path=None, fetch=fetch):
    run = ImportRun.objects.create(retailer=retailer)
    try:
        if retailer.source_type == Retailer.Source.SHOPIFY:
            if not retailer.source_url:
                raise ImportError_("Set the shop address on the retailer first.")
            offers = shopify_offers(retailer, fetch=fetch)
        elif retailer.source_type == Retailer.Source.WEBSITE:
            if not retailer.source_url:
                raise ImportError_("Set the shop address on the retailer first.")
            offers = website_offers(retailer, fetch=fetch)
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
        found, updated, unmatched = apply_offers(retailer, offers, run=run)
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
