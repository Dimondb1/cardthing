"""
Bring real prices in from retailers.

Two sources are supported:

* Shopify stores expose every product at ``/products.json``. Most UK card
  shops run on Shopify. Offers are matched to RipRaptor products by barcode
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
import dataclasses
import html as html_
import io
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from . import pricing
from .classify import GAMES, classify, find_game
from .matching import AUTO_LINK, SUGGEST, best_match, covers, match_key, score, shop_title
from .models import Game, ImportRun, Listing, Product, Retailer, ShopProduct

logger = logging.getLogger(__name__)

USER_AGENT = "RipRaptor price check (+https://ripraptor.example)"
MAX_SHOPIFY_PAGES = 400
TIMEOUT = 30
PREORDER_WORDS = re.compile(r"pre[\s-]?order", re.I)
# A tag counts only when it says pre-order and nothing else. Shop apps add tags
# like "Pre-Order - Inventory Trigger" to products that are in stock today.
PREORDER_TAG = re.compile(r"^\s*pre[\s-]?orders?\s*$", re.I)
# A collection named for leaving pre-orders out ("all-products-excluding-pre-orders").
NOT_PREORDER = re.compile(r"\b(?:exclud\w*|without|except|no|non|not)[\s-]+(?:\w+[\s-]+)?pre[\s-]?orders?", re.I)


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
    product_pk: int | None = None   # set when the source already knows which product this is


class ImportError_(Exception):
    pass


# Marketplaces allow a limited number of calls a day, so they are read this often, not hourly.
DAILY_EVERY_HOURS = 20
# A run still open after this long was cut short (a deploy, a timeout, a crash): the longest real
# read, Amazon at 2000 calls 1.1 s apart, takes about 37 minutes.
ABANDONED_AFTER = timedelta(hours=3)
STOPPED = "Stopped before it finished."


def safe_url(url):
    """Percent-encode the characters urllib refuses ("é", curly quotes, spaces) in a page address."""
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query, safe="=&%:@!$'()*+,;/?-._~")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def fetch(url, retries=2):
    url = safe_url(url)
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

def session_fetch(session_url, timeout=None):
    """A fetch that keeps cookies, having first opened ``session_url``.

    Some shops show each visitor their own currency. Opening the shop's
    "switch to pounds" link sets a cookie, and every page read with this
    fetch afterwards is priced in pounds.
    """
    import http.cookiejar

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    state = {"ready": False}

    def fetch_with_cookies(url, retries=2):
        if not state["ready"]:
            state["ready"] = True
            try:
                fetch_with_cookies(session_url, retries=retries)
            except ImportError_:
                pass
        request = urllib.request.Request(safe_url(url), headers={"User-Agent": USER_AGENT})
        last = None
        for attempt in range(retries + 1):
            try:
                with opener.open(request, timeout=timeout or TIMEOUT) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise ImportError_(f"Could not fetch {url}: HTTP Error {exc.code}") from exc
            except (urllib.error.URLError, OSError) as exc:
                last = exc
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise ImportError_(f"Could not fetch {url}: {exc}") from exc
        raise ImportError_(f"Could not fetch {url}: {last}")

    return fetch_with_cookies


def retailer_fetch(retailer, fetch=None):
    """The fetch to read this retailer with: cookie-keeping when it needs a session."""
    if retailer.session_url and fetch is None:
        return session_fetch(retailer.session_url)
    return fetch or globals()["fetch"]


def shop_currency(base, fetch=fetch):
    """The currency a Shopify shop prices in, from its /meta.json, or "" if unknown."""
    try:
        return str(json.loads(fetch(f"{base}/meta.json")).get("currency") or "").upper()
    except (ImportError_, json.JSONDecodeError, AttributeError):
        return ""


PREORDER_COLLECTIONS = ("pre-order", "pre-orders", "preorder", "preorders")


def preorder_collections(base, fetch=fetch):
    """Handles of the shop's collections whose names say pre-order, plus the usual names."""
    names = list(PREORDER_COLLECTIONS)
    try:
        listed = json.loads(fetch(f"{base}/collections.json?limit=250")).get("collections", [])
    except Exception:   # a shop without the listing, or a test fake that has no answer for it
        listed = []
    for collection in listed:
        handle = collection.get("handle", "")
        if PREORDER_WORDS.search(handle) and not NOT_PREORDER.search(handle) and handle not in names:
            names.append(handle)
    return names


def preorder_handles(base, fetch=fetch, limit=20):
    """Handles of products the shop lists in a pre-order collection.

    Many Shopify shops mark pre-orders only through a collection, so the
    product itself says "available" while the shop page says pre-order.
    Every collection named for pre-orders is read: shops call them
    "pre-order", "all-pre-order-items", "pokemon-pre-orders" and so on.
    """
    handles = set()
    for name in preorder_collections(base, fetch=fetch):
        for page in range(1, limit + 1):
            try:
                products = json.loads(fetch(f"{base}/collections/{name}/products.json?limit=250&page={page}")).get("products", [])
            except (ImportError_, json.JSONDecodeError, AttributeError):
                break
            if not products:
                break
            handles.update(p.get("handle", "") for p in products)
            if len(products) < 250:
                break
    return handles


def shopify_offers(retailer, fetch=fetch):
    base = retailer.source_url.rstrip("/")
    currency = shop_currency(base, fetch=fetch)
    if currency and currency != "GBP":
        raise ImportError_(f"{base} prices in {currency}, not pounds. Prices were not imported.")
    preorders = preorder_handles(base, fetch=fetch)
    # A shop that sells far more than cards (Zatu: board games, puzzles, books) is read from its
    # card game collection, so the thousands of other products never reach the matcher.
    listing = f"{base}/collections/{retailer.collection}/products.json" if retailer.collection else f"{base}/products.json"
    page = 1
    first_handle = None
    while page <= MAX_SHOPIFY_PAGES:
        try:
            raw = fetch(f"{listing}?limit=250&page={page}")
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
                PREORDER_TAG.match(tag) for tag in product.get("tags", [])
            ) or product.get("handle") in preorders
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
                variant_url = url
                if variant.get("title") and variant["title"] != "Default Title":
                    title = f"{title} ({variant['title']})"
                    # Land the buyer on this variant, not the page's variant menu.
                    if variant.get("id"):
                        variant_url = f"{url}?variant={variant['id']}"
                yield Offer(
                    title=title,
                    url=variant_url,
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
MAX_SITEMAPS = 500
INFORMATIVE_SITEMAP = 50   # this many product-looking pages and the rest of the sitemap is skipped
SITEMAP_WORKERS = 4


def sitemap_urls(base, fetch=fetch, limit=MAX_PAGES):
    """Every page address listed in the site's sitemap(s), product-looking ones first."""
    found, seen, queue = [], set(), [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/xmlsitemap.php"]
    try:
        robots = fetch(f"{base}/robots.txt").decode("utf-8", "replace")
        queue = [line.split(":", 1)[1].strip() for line in robots.splitlines() if line.lower().startswith("sitemap:")] + queue
    except ImportError_:
        pass
    # Read every sitemap file (up to MAX_SITEMAPS of them) before ranking, so
    # a shop that lists its accessories first still gets its sealed products
    # fetched. The page limit is applied after ranking. Sitemap files are
    # static and large, so a few are read at once.
    def read(url):
        try:
            return fetch(url).decode("utf-8", "replace")
        except ImportError_:
            return ""

    with ThreadPoolExecutor(max_workers=SITEMAP_WORKERS) as pool:
        while queue and len(seen) < MAX_SITEMAPS:
            batch = []
            while queue and len(batch) < SITEMAP_WORKERS and len(seen) < MAX_SITEMAPS:
                url = queue.pop(0)
                if url not in seen:
                    seen.add(url)
                    batch.append(url)
            for text in pool.map(read, batch):
                for loc in SITEMAP_LOC.findall(text):
                    loc = loc.replace("&amp;", "&")
                    if loc.endswith(".xml") or "sitemap" in loc.lower():
                        queue.append(loc)
                    else:
                        found.append(loc)
    found = [u for u in found if not u.lower().endswith((".jpg", ".png", ".webp", ".pdf"))]
    ranked = [(rank, i, u) for i, u in enumerate(found) if (rank := page_rank(u)) is not None]
    ranked.sort()
    # When the sitemap clearly marks its product pages, the pages that read as
    # neither product nor game (blog posts, guides, policies) are not worth an hour.
    if sum(1 for rank, _i, _u in ranked if rank <= 1) >= INFORMATIVE_SITEMAP:
        ranked = [row for row in ranked if row[0] <= 1]
    return [u for _rank, _i, u in ranked[:limit]]


def slug_words(url):
    """The last path segment of a page address as words: 'pokemon-151-etb' -> 'pokemon 151 etb'."""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    return re.sub(r"[-_+]+", " ", path.rsplit("/", 1)[-1]).strip()


def page_rank(url):
    """Order sitemap pages so the ones worth fetching come first.

    Big shops list every single card and accessory, far more pages than we can
    fetch each hour. The address usually carries the product title, so: pages
    whose address reads as a sealed product go first, then addresses that say
    nothing about a game, and a page whose address names a game but reads as a
    single card or accessory is skipped (None).
    """
    words = slug_words(url)
    if classify(words):
        return 0
    if find_game(words):
        return None
    return 1 if any(w in url.lower() for w in PRODUCT_PATH_WORDS) else 2


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


OUT_OF_STOCK_HINTS = re.compile(
    r"schema\.org/OutOfStock|\"instock\"\s*:\s*false|\"available_to_sell\"\s*:\s*0\b|"
    r"<button[^>]*add[- ]?to[- ]?(?:cart|basket)[^>]*\bdisabled\b|<button[^>]*\bdisabled\b[^>]*add[- ]?to[- ]?(?:cart|basket)",
    re.I,
)


def page_stock_hint(html):
    """"outofstock" when the page carries an explicit sold-out signal, else "" (in stock).

    A visible "Add to Cart" button proves nothing: shops render it disabled
    when the item is sold out.
    """
    if OUT_OF_STOCK_HINTS.search(html):
        return "outofstock"
    if re.search(r"out of stock|sold out", html, re.I) and not re.search(r"add to (?:cart|basket)", html, re.I):
        return "outofstock"
    return ""


def page_offer(url, html):
    """An Offer from a product page's schema.org data or Open Graph tags, or None."""
    for block in JSON_LD.findall(html):
        try:
            data = json.loads(block.strip(), strict=False)  # some shops leave control characters in descriptions
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
                title=html_.unescape(str(node.get("name", ""))).strip(),
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
                availability=_availability(meta.get("product:availability", "") or page_stock_hint(html)),
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
            # Some shops leave the game out of the title ("Kyurem V Collection
            # Box") but put it in the address, so the address words go along
            # as a tag for the classifier to read.
            if not offer.tags:
                offer = dataclasses.replace(offer, tags=(slug_words(url),))
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


GOOGLE_NS = "{http://base.google.com/ns/1.0}"
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def xml_feed_offers(text):
    """Offers from a Google Shopping feed (RSS 2.0 or Atom with the g: namespace)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ImportError_(f"The feed is not valid XML: {exc}") from exc
    items = root.iter("item") if root.find("channel") is not None else root.iter(f"{ATOM_NS}entry")

    def field(item, name):
        node = item.find(f"{GOOGLE_NS}{name}")
        if node is None:
            node = item.find(name)
        if node is None:
            node = item.find(f"{ATOM_NS}{name}")
        return (node.text or "").strip() if node is not None and node.text else ""

    for item in items:
        price = money(field(item, "sale_price") or field(item, "price"))
        url = field(item, "link")
        if not url:
            link = item.find(f"{ATOM_NS}link")
            url = (link.get("href") or "").strip() if link is not None else ""
        if price is None or not url:
            continue
        shipping = item.find(f"{GOOGLE_NS}shipping")
        delivery = money(field(shipping, "price")) if shipping is not None else None
        yield Offer(
            title=field(item, "title"),
            url=url,
            price=price,
            ean=clean_ean(field(item, "gtin")),
            availability=AVAILABILITY_WORDS.get(field(item, "availability").lower(), Listing.Availability.IN_STOCK),
            delivery=delivery,
            image=field(item, "image_link"),
        )


def feed_offers(text):
    """Offers from a CSV feed (Awin, Google Merchant columns) or a Google Shopping XML feed."""
    if text.lstrip().startswith("<"):
        yield from xml_feed_offers(text)
        return
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


# Unchanged listings are stamped this many at a time: one short UPDATE each.
STAMP_CHUNK = 500
# Progress is written to the run, and unchanged listings stamped, after this many offers.
PROGRESS_EVERY = 250


def unchanged(listing, offer, delivery):
    """True when this offer says nothing the existing ``listing`` does not already hold, so the check
    only needs its time stamped. ``delivery`` is the charge worked out for the offer (None = unknown).
    An offer without a price only ever changes the stock state, so only that is compared."""
    if listing.pk is None or listing._state.adding:
        return False
    if offer.availability != listing.availability:
        return False
    if offer.price <= 0:
        return True
    return (
        listing.price == offer.price
        and listing.delivery_known == (delivery is not None)
        and listing.delivery_cost == (delivery if delivery is not None else Decimal("0.00"))
        and link_key(listing.url) == link_key(offer.url)
    )


def stamp_checked(pks, checked_at):
    """Mark listings as checked at ``checked_at`` without rewriting anything else, then empty ``pks``."""
    for start in range(0, len(pks), STAMP_CHUNK):
        Listing.objects.filter(pk__in=pks[start:start + STAMP_CHUNK]).update(last_checked=checked_at)
    pks.clear()


def apply_offers(retailer, offers, checked_at=None, run=None, complete=True):
    """Update listings from ``offers``.

    ``complete`` says the offers cover the shop's whole range, so anything not
    among them is out of stock there. A website crawl stops at MAX_PAGES and
    is not complete: its unseen products keep their last state.

    Offers match a product by barcode, or by the link of a listing that was
    added by hand for this retailer. Returns (found, updated, unmatched titles).

    An offer that changes nothing on its listing is not written: its listing is only stamped as
    checked, in chunks, before each progress post and at the end. An offer without a price never
    creates a listing.
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
    catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug"))
    ignored = set(
        ShopProduct.objects.filter(retailer=retailer, status=ShopProduct.Status.IGNORED).values_list("url", flat=True)
    )

    to_stamp = []

    if True:
        for offer in offers:
            if found and found % PROGRESS_EVERY == 0:
                # Stamp first, so a run cut short after this point leaves these listings fresh.
                stamp_checked(to_stamp, checked_at)
                if run is not None:
                    ImportRun.objects.filter(pk=run.pk).update(offers_found=found, listings_updated=updated)
            found += 1
            product_pk = offer.product_pk
            if product_pk is None:
                product_pk = products_by_ean.get(ean_key(offer.ean)) if offer.ean else None
            if product_pk is None:
                product_pk = products_by_link.get(link_key(offer.url))
            sealed = None
            if product_pk is None and offer.url not in ignored:
                # No barcode and no hand-made link: guess from the name, but
                # only for things that are sealed products of a known game.
                sealed = classify(offer.title, offer.shop_type, offer.vendor, offer.tags, offer.price)
                if sealed is None:
                    unmatched.append(f"{offer.title} [{offer.ean or 'no barcode'}] {offer.url}")
                    continue
                match, value = catalogue.best_match(offer.title, game=sealed.game)
                if (match is None or value < AUTO_LINK) and getattr(settings, "RIPRAPTOR_AUTO_CATALOGUE", True):
                    created_pk = create_from_offer(offer, catalogue, sealed=sealed)
                    if created_pk is not None:
                        product_pk = created_pk
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
            if offer.price <= 0:
                # No price is not a price: it may update the stock of a listing we have, never make one,
                # and never stands in for a sibling variant seen earlier in this run.
                if product_pk in seen_products:
                    continue
                listing = Listing.objects.filter(product_id=product_pk, retailer=retailer).first()
                if listing is None:
                    unmatched.append(f"{offer.title} [{offer.ean or 'no barcode'}] {offer.url} (no price)")
                    continue
                created = False
            else:
                listing, created = Listing.objects.get_or_create(
                    product_id=product_pk, retailer=retailer, defaults={"url": offer.url, "price": offer.price}
                )
            if product_pk in seen_products and listing.availability != Listing.Availability.OUT_OF_STOCK:
                # Several variants of one product: keep the cheapest one that is in stock.
                if offer.availability == Listing.Availability.OUT_OF_STOCK or offer.price >= listing.price:
                    continue
            seen_products.add(product_pk)
            title = (offer.title or "")[:300]
            if created or listing.url != offer.url or listing.title != title:
                listing.url = offer.url
                listing.title = title
                listing.save(update_fields=["url", "title"])
            if offer.image and getattr(settings, "RIPRAPTOR_USE_FEED_IMAGES", True):
                images_by_product.setdefault(product_pk, offer.image)
            delivery = offer.delivery if offer.delivery is not None else retailer.delivery_for(offer.price)
            updated += 1
            if not created and unchanged(listing, offer, delivery):
                to_stamp.append(listing.pk)
                continue
            pricing.record_check(
                listing,
                price=offer.price,
                delivery_cost=delivery,
                availability=offer.availability,
                checked_at=checked_at,
            )

        stamp_checked(to_stamp, checked_at)

        # Fill in images for products that have none.
        if images_by_product:
            for product in Product.objects.filter(pk__in=images_by_product, image="", image_url=""):
                product.image_url = images_by_product[product.pk][:1000]
                product.save(update_fields=["image_url"])

        # Anything the retailer no longer lists is out of stock there.
        if complete:
            Listing.objects.filter(retailer=retailer).exclude(product_id__in=seen_products).exclude(
                availability=Listing.Availability.OUT_OF_STOCK
            ).update(availability=Listing.Availability.OUT_OF_STOCK, last_checked=checked_at)

    return found, updated, unmatched


class Catalogue:
    """Our products, indexed by word so an offer is only scored against likely matches."""

    def __init__(self, rows):
        from .matching import words

        self.names = {}
        self.games = {}
        self.index = {}
        self.by_key = {}
        self._words = words
        for row in rows:
            self.add(*row)

    def add(self, pk, name, game=None):
        self.names[pk] = name
        self.games[pk] = game
        self.by_key.setdefault((game, match_key(name)), []).append(pk)
        for word in set(self._words(name)):
            self.index.setdefault(word, set()).add(pk)

    def append(self, row):
        self.add(*row)

    def __iter__(self):
        return iter(self.names.items())

    def best_match(self, title, game=None):
        """(product, score) for the shop title, only among products of ``game``.

        A full score needs every meaningful word of our name in the title
        and every identifying word of the title in our name, so a short
        product name cannot swallow another set's, edition's or game's product.
        """
        title = shop_title(title)
        title_words = set(self._words(title))
        for pk in self.by_key.get((game, match_key(title)), ()):
            return (pk, self.names[pk]), AUTO_LINK
        counts = {}
        for word in title_words:
            for pk in self.index.get(word, ()):
                if game is None or self.games.get(pk) == game:
                    counts[pk] = counts.get(pk, 0) + 1
        # Only products sharing at least two words (or all of a short name) are worth scoring.
        candidates = [(pk, self.names[pk]) for pk, n in counts.items() if n >= 2 or n >= len(set(self._words(self.names[pk])))]
        match, value = best_match(title, candidates)
        if match and value >= AUTO_LINK and not covers(match[1], title):
            value = SUGGEST
        return match, value


def create_from_offer(offer, catalogue, sealed=None):
    """Make a product for a sealed item no catalogue product matches. Returns its pk or None.

    ``catalogue`` is the live list of (pk, name, game) and is extended in place
    so later offers in the same run match the new product.
    """
    sealed = sealed or classify(offer.title, offer.shop_type, offer.vendor, offer.tags, offer.price)
    if sealed is None:
        return None
    # The clean name may already exist under a slightly different shop title.
    match, value = catalogue.best_match(sealed.name, game=sealed.game)
    if match and value >= AUTO_LINK and score(sealed.name, match[1]) >= AUTO_LINK:
        return match[0]
    game = Game.objects.filter(slug=sealed.game).first()
    if game is None:
        for slug, name, short, words in GAMES:
            if slug == sealed.game:
                game = Game.objects.create(name=name, short_name=short, slug=slug, search_aliases=", ".join(words),
                                           sort_order=Game.objects.count() + 1)
    from django.utils.text import slugify

    slug = slugify(sealed.name)[:220]
    existing = Product.objects.filter(slug=slug).first()
    if existing is not None and existing.game_id == game.pk:
        catalogue.append((existing.pk, existing.name, game.slug))
        return existing.pk
    if existing is not None:
        # Same words, another game ("Origins Booster Pack"): keep the addresses apart.
        slug = f"{slug[:200]}-{game.slug}"
        existing = Product.objects.filter(slug=slug).first()
        if existing is not None:
            catalogue.append((existing.pk, existing.name, game.slug))
            return existing.pk
    product = Product.objects.create(
        game=game, name=sealed.name, slug=slug, product_type=sealed.product_type,
        ean=offer.ean or "", image_url=offer.image[:1000] if offer.image else "",
    )
    catalogue.append((product.pk, product.name, game.slug))
    return product.pk


def _as_import_errors(offers, errors):
    """Pass offers through, turning a source's own errors into ImportError_ for run_import to record."""
    try:
        yield from offers
    except errors as exc:
        raise ImportError_(str(exc)) from exc


def close_abandoned_runs(older_than=ABANDONED_AFTER, now=None):
    """Close every run that started more than ``older_than`` ago and never finished. Returns how many.

    Such a run stays "Running" in admin for ever otherwise. It gets an error, so it never counts
    as today's marketplace fetch and never as a shop's last good read.
    """
    now = now or timezone.now()
    return ImportRun.objects.filter(finished_at__isnull=True, started_at__lt=now - older_than).update(
        finished_at=now, error=STOPPED
    )


def run_import(retailer, feed_path=None, fetch=None):
    run = ImportRun.objects.create(retailer=retailer)
    fetch = retailer_fetch(retailer, fetch)
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
        elif retailer.source_type in (Retailer.Source.AMAZON, Retailer.Source.EBAY):
            from .amazon import AmazonError, amazon_offers
            from .ebay import EbayError, iter_ebay_offers

            # The last run that actually fetched something. Empty runs are left out, so the
            # skip records an older version of this code saved cannot keep postponing the fetch.
            last = (
                ImportRun.objects.filter(retailer=retailer, error="", finished_at__isnull=False, offers_found__gt=0)
                .exclude(pk=run.pk)
                .order_by("-finished_at")
                .first()
            )
            if last and last.finished_at > timezone.now() - timedelta(hours=DAILY_EVERY_HOURS):
                # Already fetched today. Leave no trace of this skip: an empty, error-free
                # run here would itself count as "fetched today" next hour, and the
                # retailer would never be read again.
                run.delete()
                return last
            try:
                if retailer.source_type == Retailer.Source.AMAZON:
                    offers = amazon_offers(retailer)
                else:
                    # Saved one by one as eBay finds them, so an interrupted run keeps its work.
                    offers = _as_import_errors(iter_ebay_offers(retailer, run=run), (EbayError,))
            except (AmazonError, EbayError) as exc:
                raise ImportError_(str(exc)) from exc
        else:
            raise ImportError_("This retailer's prices are entered by hand.")
        complete = retailer.source_type not in (Retailer.Source.WEBSITE, Retailer.Source.EBAY)
        found, updated, unmatched = apply_offers(retailer, offers, run=run, complete=complete)
        run.offers_found = found
        run.listings_updated = updated
        run.unmatched = "\n".join(unmatched)
    except (ImportError_, OSError) as exc:
        run.error = str(exc)
        logger.error("Import for %s failed: %s", retailer, exc)
    run.finished_at = timezone.now()
    run.save()
    from .signals import clear_list_caches

    clear_list_caches()
    return run
