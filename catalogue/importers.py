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
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import pricing
from .classify import GAMES, classify, find_game
from .matching import AUTO_LINK, SUGGEST, best_match, covers, match_key, score, shop_title
from .models import (
    Game, ImportRun, Listing, Product, Retailer, RetailerCollection, ShopPage, ShopProduct, stale_cutoff,
)
from .sanity import judge_product, trust_expiry

logger = logging.getLogger(__name__)

USER_AGENT = "RipRaptor price check (+https://ripraptor.example)"
MAX_SHOPIFY_PAGES = 400
TIMEOUT = 30
PREORDER_WORDS = re.compile(r"pre[\s-]?order", re.I)
# A tag counts only when it says pre-order (or pre-orders live, or coming soon) and nothing else. Shop apps
# add tags like "Pre-Order - Inventory Trigger" to products that are in stock today.
PREORDER_TAG = re.compile(r"^\s*(?:pre[\s-]?orders?(?:[\s-]?live)?|coming[\s-]?soon)\s*$", re.I)
# Collections a shop fills before release day: their available products are pre-orders.
COMING_SOON = re.compile(r"coming[\s-]?soon", re.I)
# Collections where a new pre-order often shows first. The pre-order pulse watches them, but being in one
# says nothing about stock, so a product found there is a pre-order only by its own title or tags.
NEW_IN = re.compile(r"new[\s-]?(?:releases?|arrivals?)", re.I)
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
    page_pk: int | None = None   # the ShopPage a website read took this offer from
    published_at: datetime | None = None   # when the shop published the product (Shopify's published_at)


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


def time_left(deadline):
    """Seconds before ``deadline`` (a time.monotonic() value), or None when there is no deadline."""
    return None if deadline is None else deadline - time.monotonic()


def socket_timeout(deadline):
    """The wait allowed for each connect or read: TIMEOUT, or less when a deadline is closer."""
    left = time_left(deadline)
    if left is None:
        return TIMEOUT
    if left <= 0:
        raise TimeoutError("ran out of time")
    return min(TIMEOUT, max(left, 0.1))


def read_body(response, deadline=None):
    """The whole answer. With a deadline the body is read a piece at a time and given up once it passes.

    The socket timeout covers each read on its own, so a shop that sends a byte every few seconds could
    otherwise keep one request open for as long as it liked.
    """
    if deadline is None:
        return response.read()
    import http.client

    pieces = []
    while True:
        if time_left(deadline) <= 0:
            raise TimeoutError("ran out of time")
        piece = response.read1(65536)
        if not piece:
            break
        pieces.append(piece)
    body = b"".join(pieces)
    if getattr(response, "length", None):
        raise http.client.IncompleteRead(body, response.length)
    return body


def fetch(url, retries=2, deadline=None):
    """The page at ``url``. ``deadline`` (a time.monotonic() value) caps the whole request, every try included."""
    url = safe_url(url)
    import http.client

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=socket_timeout(deadline)) as response:
                return read_body(response, deadline)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries and not out_of_time(deadline):
                time.sleep(2 * (attempt + 1))
                last = exc
                continue
            raise ImportError_(f"Could not fetch {url}: {exc}") from exc
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last = exc
            if attempt < retries and not out_of_time(deadline):
                time.sleep(2 * (attempt + 1))
                continue
            break
    raise ImportError_(f"Could not fetch {url}: {last}")


def out_of_time(deadline):
    left = time_left(deadline)
    return left is not None and left <= 0


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

    def fetch_with_cookies(url, retries=2, deadline=None):
        import http.client

        if not state["ready"]:
            state["ready"] = True
            try:
                fetch_with_cookies(session_url, retries=retries, deadline=deadline)
            except ImportError_:
                pass
        request = urllib.request.Request(safe_url(url), headers={"User-Agent": USER_AGENT})
        last = None
        for attempt in range(retries + 1):
            try:
                wait = socket_timeout(deadline) if deadline is not None else (timeout or TIMEOUT)
                with opener.open(request, timeout=wait) as response:
                    return read_body(response, deadline)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries and not out_of_time(deadline):
                    time.sleep(2 * (attempt + 1))
                    continue
                raise ImportError_(f"Could not fetch {url}: HTTP Error {exc.code}") from exc
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                last = exc
                if attempt < retries and not out_of_time(deadline):
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
        if collection_kind(handle) == PREORDER_KIND and handle not in names:
            names.append(handle)
    return names


PREORDER_KIND, NEW_KIND = "preorder", "new"


def collection_kind(handle):
    """PREORDER_KIND for a collection named for pre-orders or coming soon, NEW_KIND for new releases or
    arrivals, None for any other (and for one named for leaving pre-orders out)."""
    handle = str(handle or "")
    if not handle or NOT_PREORDER.search(handle):
        return None
    if PREORDER_WORDS.search(handle) or COMING_SOON.search(handle):
        return PREORDER_KIND
    if NEW_IN.search(handle):
        return NEW_KIND
    return None


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


# The background reader waits this long between products.json pages, on top of the shop's own rate.
PAGE_PAUSE = 0.5


def says_preorder(product):
    """True when a Shopify product's own title or tags say pre-order."""
    return bool(PREORDER_WORDS.search(product.get("title", "") or "")) or any(
        PREORDER_TAG.match(str(tag)) for tag in product.get("tags", []) or []
    )


def product_offers(base, product, preorder=False):
    """One Offer per priced variant of a Shopify product (a products.json entry).

    ``preorder`` says the shop lists it in a pre-order collection; its own title or tags can say so too.
    An available variant of a pre-order is PREORDER, an unavailable one OUT_OF_STOCK.
    """
    url = f"{base}/products/{product.get('handle', '')}"
    preorder = preorder or says_preorder(product)
    images = product.get("images") or []
    product_image = images[0].get("src", "") if images else ""
    published_at = parse_published(product.get("published_at"))
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
            published_at=published_at,
        )


def parse_published(value):
    """A Shopify time ("2026-10-09T09:00:00+01:00") as an aware datetime, or None when missing or odd."""
    if not value or not isinstance(value, str):
        return None
    try:
        moment = parse_datetime(value)
    except ValueError:
        return None
    if moment is None:
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, dt_timezone.utc)
    return moment


def shopify_offers(retailer, fetch=fetch, pause=0.0):
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
        if page > 1 and pause:
            time.sleep(pause)
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
            yield from product_offers(base, product, preorder=product.get("handle") in preorders)
        page += 1


# The pre-order pulse ----------------------------------------------------------

# Pages of one collection read in a pulse, as for the pre-order collections of a whole-shop read.
COLLECTION_PAGES = 20
PULSE_NOTE = "Pre-order pulse"
HTTP_STATUS = re.compile(r"HTTP Error (\d{3})\b")


@dataclass
class Pulse:
    """What one look at a shop's collections did."""

    read: list = dataclasses.field(default_factory=list)   # handles whose products were read and applied
    run: ImportRun | None = None
    error: str = ""
    status: int | None = None   # the HTTP status of a failed request, when it gave one
    updated: int = 0
    no_list: bool = False   # the shop has no collection list, so it is looked at weekly


def status_of(error):
    found = HTTP_STATUS.search(str(error))
    return int(found.group(1)) if found else None


def tracked_collections(listed):
    """{handle: (kind, products_count, updated_at)} for the collections in a collections.json answer the pulse watches."""
    tracked = {}
    for collection in listed:
        if not isinstance(collection, dict):
            continue
        handle = str(collection.get("handle") or "")[:200]
        kind = collection_kind(handle)
        if kind is None:
            continue
        try:
            count = int(collection.get("products_count") or 0)
        except (TypeError, ValueError):
            count = 0
        tracked[handle] = (kind, count, parse_published(collection.get("updated_at")))
    return tracked


def collection_products(base, handle, fetch, pause=0.0, stop=None):
    """The products of one collection, page by page. Returns (products, finished): ``finished`` is False
    when ``stop()`` said time was up before the last page, so the collection is read again next time."""
    products, first = [], None
    for page in range(1, COLLECTION_PAGES + 1):
        if page > 1:
            if stop is not None and stop():
                return products, False
            if pause:
                time.sleep(pause)
        try:
            items = json.loads(fetch(f"{base}/collections/{handle}/products.json?limit=250&page={page}")).get("products", [])
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            raise ImportError_(f"{base} did not return Shopify JSON for {handle}") from exc
        if not items or not isinstance(items, list):
            break
        if page == 1:
            first = items[0].get("handle")
        elif items[0].get("handle") == first:
            break   # the shop repeats its first page instead of ending
        products.extend(item for item in items if isinstance(item, dict))
        if len(items) < 250:
            break
    return products, True


def poll_collections(retailer, fetch=fetch, now=None, pause=0.0, stop=None):
    """One look at a Shopify shop's collection list, reading only the watched collections that changed.

    One request for /collections.json. A watched collection (named for pre-orders, coming soon, new
    releases or new arrivals) whose product count or change time moved since the last look, or that is
    new, has its products read (up to COLLECTION_PAGES pages) and applied like a shop read that covers
    part of the shop, on an ImportRun noted as the pulse. A product from a pre-order or coming soon
    collection is a pre-order while available; one from a new releases or arrivals collection is applied
    only when its own title or tags say pre-order, because being new says nothing about stock.

    A shop that answers 404 or not JSON has no collection list: it is marked so and looked at weekly.
    The look is stamped before the request, so a pulse that fails is not repeated in a loop. ``stop`` is
    asked between requests; a pre-order collection left part read is read again at the next look. Pre-order
    and coming soon collections are read before new arrivals, and one collection that cannot be read is
    noted on the run without stopping the others (a 429 still ends the look).
    """
    now = now or timezone.now()
    base = retailer.source_url.rstrip("/")
    Retailer.objects.filter(pk=retailer.pk).update(collections_polled_at=now)
    retailer.collections_polled_at = now
    pulse = Pulse()

    def has_list(ok):
        pulse.no_list = not ok
        if retailer.collections_ok is not ok:
            Retailer.objects.filter(pk=retailer.pk).update(collections_ok=ok)
            retailer.collections_ok = ok

    try:
        listed = json.loads(fetch(f"{base}/collections.json?limit=250")).get("collections")
        if not isinstance(listed, list):
            raise ValueError("no collections")
    except ImportError_ as exc:
        pulse.error, pulse.status = str(exc)[:300], status_of(exc)
        if pulse.status in (404, 410):
            has_list(False)
        return pulse
    except (ValueError, UnicodeDecodeError, AttributeError):
        has_list(False)
        pulse.error = f"{base} has no collection list"
        return pulse
    has_list(True)

    tracked = tracked_collections(listed)
    known = {row.handle: row for row in RetailerCollection.objects.filter(retailer=retailer)}
    gone = [handle for handle in known if handle not in tracked]
    if gone:
        RetailerCollection.objects.filter(retailer=retailer, handle__in=gone).delete()
    # Pre-order and coming soon collections first: a large new arrivals collection listed before them
    # must not spend the pulse's time on products that are only new.
    changed = sorted(
        (
            handle for handle, (_kind, count, updated) in tracked.items()
            if handle not in known or known[handle].products_count != count or known[handle].updated_at != updated
        ),
        key=lambda handle: tracked[handle][0] != PREORDER_KIND,
    )
    if not changed:
        return pulse

    pulse.run = run = ImportRun.objects.create(retailer=retailer, note=f"{PULSE_NOTE}: {', '.join(changed)}"[:200])
    found = updated = 0
    unmatched, failed = [], []
    try:
        # As a whole-shop read does: prices in another currency are never applied.
        currency = shop_currency(base, fetch=fetch)
        if currency and currency != "GBP":
            raise ImportError_(f"{base} prices in {currency}, not pounds. Prices were not imported.")
        for attempt, handle in enumerate(changed):
            if attempt and stop is not None and stop():
                break
            kind, count, changed_at = tracked[handle]
            try:
                products, finished = collection_products(base, handle, fetch, pause=pause, stop=stop)
            except ImportError_ as exc:
                # A shop throttling the pulse ends it; one collection that cannot be read is noted and the
                # rest are still read, so it never blocks the collections listed after it.
                if status_of(exc) == 429:
                    raise
                failed.append(str(exc))
                pulse.status = status_of(exc)
                continue
            offers = []
            for product in products:
                for offer in product_offers(base, product, preorder=kind == PREORDER_KIND):
                    if kind == PREORDER_KIND or offer.availability == Listing.Availability.PREORDER:
                        offers.append(offer)
            if offers:
                n, changed_count, missed = apply_offers(retailer, offers, run=run, complete=False)
                found, updated, unmatched = found + n, updated + changed_count, unmatched + missed
            # A new arrivals collection read part way counts as read, so a large one is not read again from
            # its first page at every look; shops usually list it newest first and the whole read sees the rest.
            if finished or kind == NEW_KIND:
                RetailerCollection.objects.update_or_create(
                    retailer=retailer, handle=handle,
                    defaults={"products_count": count, "updated_at": changed_at, "last_read_at": now},
                )
                pulse.read.append(handle)
        if failed:
            run.error = pulse.error = " ".join(failed)[:300]
    except ImportError_ as exc:
        run.error = pulse.error = str(exc)[:300]
        pulse.status = status_of(exc)
    except Exception as exc:
        run.error = f"The pulse stopped on an unexpected error: {type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        run.offers_found, run.listings_updated = found, updated
        run.unmatched = "\n".join(unmatched)
        run.finished_at = timezone.now()
        run.save()
        pulse.updated = updated
        if updated:
            from .signals import clear_list_caches

            clear_list_caches(force=True)
    return pulse


# Any website: sitemap + schema.org product data -----------------------------

SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
SITEMAP_LASTMOD = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", re.I)
SITEMAP_ENTRY_END = re.compile(r"</(?:url|sitemap)\s*>", re.I)
JSON_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
META = re.compile(r"<meta[^>]+(?:property|name)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']*)[\"']", re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
PRODUCT_PATH_WORDS = ("/product", "/products/", "/p/", "/item", "/shop/", "-p-")
MAX_PAGES = 3000   # pages of one shop kept in its index, the ones worth fetching first
PAGES_PER_READ = 600   # pages fetched in one read, so a 3,000 page shop is read through in five
# A page unread this long is read before any page the sitemap says has changed, and a page the
# sitemap dates as unchanged is read again once it is this old. Its listing then never nears the
# 72 hour stale cutoff, even when a shop dates hundreds of pages as changed on every read, and a
# stock change the shop does not date is caught within a day.
RECHECK_UNCHANGED = timedelta(hours=24)
# A page missing from the sitemap this long has gone from the shop.
PAGE_FORGOTTEN_AFTER = timedelta(days=30)
MAX_SITEMAPS = 500
INFORMATIVE_SITEMAP = 50   # this many product-looking pages and the rest of the sitemap is skipped
SITEMAP_WORKERS = 4


def sitemap_lastmod(text):
    """A sitemap <lastmod> as an aware datetime, or None when missing or unreadable."""
    from datetime import datetime, time as time_, timezone as tz

    from django.utils.dateparse import parse_date, parse_datetime

    if not text:
        return None
    try:
        when = parse_datetime(text)
        if when is None:
            day = parse_date(text)
            when = datetime.combine(day, time_.min) if day else None
        if when is None:
            return None
        if timezone.is_naive(when):
            when = when.replace(tzinfo=tz.utc)
        # Stored in UTC, so a date at the edge of the calendar ("0001-01-01T00:00:00+01:00") fails
        # here, where it is only unreadable, and not when the index is saved, where it would stop the read.
        return when.astimezone(tz.utc)
    except (ValueError, OverflowError):
        return None


def sitemap_pages(base, fetch=fetch, limit=MAX_PAGES, must_answer=False):
    """(address, lastmod) for every page the site's sitemap(s) list, product-looking ones first.

    lastmod is None when the sitemap does not date the page. With ``must_answer``, a site that
    answered neither robots.txt nor any sitemap raises ImportError_, so a shop that cannot be reached
    counts as a failed read instead of a read that found nothing.
    """
    found, seen, queue = {}, set(), [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/xmlsitemap.php"]
    answered, failed = [], []
    try:
        robots = fetch(f"{base}/robots.txt").decode("utf-8", "replace")
        answered.append(True)
        queue = [line.split(":", 1)[1].strip() for line in robots.splitlines() if line.lower().startswith("sitemap:")] + queue
    except ImportError_ as exc:
        failed.append(exc)
    # Read every sitemap file (up to MAX_SITEMAPS of them) before ranking, so
    # a shop that lists its accessories first still gets its sealed products
    # fetched. The page limit is applied after ranking. Sitemap files are
    # static and large, so a few are read at once.
    def read(url):
        try:
            text = fetch(url).decode("utf-8", "replace")
        except ImportError_ as exc:
            failed.append(exc)
            return ""
        answered.append(True)
        return text

    with ThreadPoolExecutor(max_workers=SITEMAP_WORKERS) as pool:
        while queue and len(seen) < MAX_SITEMAPS:
            batch = []
            while queue and len(batch) < SITEMAP_WORKERS and len(seen) < MAX_SITEMAPS:
                url = queue.pop(0)
                if url not in seen:
                    seen.add(url)
                    batch.append(url)
            for text in pool.map(read, batch):
                for entry in SITEMAP_ENTRY_END.split(text):
                    locs = SITEMAP_LOC.findall(entry)
                    # A date belongs to a page only when its entry names that one page.
                    dated = SITEMAP_LASTMOD.search(entry) if len(locs) == 1 else None
                    lastmod = sitemap_lastmod(dated.group(1)) if dated else None
                    for loc in locs:
                        loc = loc.replace("&amp;", "&")
                        if loc.endswith(".xml") or "sitemap" in loc.lower():
                            queue.append(loc)
                        elif loc not in found or (lastmod and (found[loc] is None or lastmod > found[loc])):
                            found[loc] = lastmod
    if must_answer and not answered and failed:
        raise ImportError_(str(failed[-1]))
    found = [(u, lastmod) for u, lastmod in found.items() if not u.lower().endswith((".jpg", ".png", ".webp", ".pdf"))]
    ranked = [(rank, i, page) for i, page in enumerate(found) if (rank := page_rank(page[0])) is not None]
    ranked.sort(key=lambda row: row[:2])
    # When the sitemap clearly marks its product pages, the pages that read as
    # neither product nor game (blog posts, guides, policies) are not worth an hour.
    if sum(1 for rank, _i, _p in ranked if rank <= 1) >= INFORMATIVE_SITEMAP:
        ranked = [row for row in ranked if row[0] <= 1]
    return [page for _rank, _i, page in ranked[:limit]]


def sitemap_urls(base, fetch=fetch, limit=MAX_PAGES):
    """Every page address listed in the site's sitemap(s), product-looking ones first."""
    return [url for url, _lastmod in sitemap_pages(base, fetch=fetch, limit=limit)]


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


def index_pages(retailer, pages, now=None):
    """Record the sitemap's pages for ``retailer``. Returns {address: (page pk, last fetched)}."""
    now = now or timezone.now()
    rows = [
        ShopPage(retailer=retailer, url=url, slug_words=slug_words(url)[:300], lastmod=lastmod, last_seen_at=now)
        for url, lastmod in pages
        if len(url) <= 1000
    ]
    ShopPage.objects.bulk_create(
        rows, batch_size=500, update_conflicts=True,
        unique_fields=["retailer", "url"], update_fields=["lastmod", "last_seen_at"],
    )
    wanted = {row.url for row in rows}
    return {
        url: (pk, fetched)
        for pk, url, fetched in ShopPage.objects.filter(retailer=retailer).values_list("pk", "url", "last_fetched_at")
        if url in wanted
    }


def pages_to_read(retailer, pages, limit=PAGES_PER_READ, now=None):
    """The (page pk, address) pairs one read fetches, in order, after indexing ``pages``.

    Pages never fetched come first, in sitemap rank order; then pages unread for RECHECK_UNCHANGED,
    so no listing nears the stale cutoff however many pages the sitemap calls changed; then pages
    the sitemap dates after their last fetch; then undated pages. Each group is longest unread
    first. A page the sitemap dates before its last fetch waits until it is overdue. A date in the
    future cannot be true, so it counts as no date: otherwise the page would count as changed on
    every read for ever.
    """
    now = now or timezone.now()
    pages = [(url, lastmod if lastmod is None or lastmod <= now else None) for url, lastmod in pages]
    index = index_pages(retailer, pages, now=now)
    never, overdue, changed, undated = [], [], [], []
    for position, (url, lastmod) in enumerate(pages):
        if url not in index:
            continue
        pk, fetched = index[url]
        if fetched is None:
            never.append((position, pk, url))
        elif fetched <= now - RECHECK_UNCHANGED:
            overdue.append((fetched, position, pk, url))
        elif lastmod is not None and lastmod > fetched:
            changed.append((fetched, position, pk, url))
        elif lastmod is None:
            undated.append((fetched, position, pk, url))
    order = never + sorted(overdue) + sorted(changed) + sorted(undated)
    return [tuple(row[-2:]) for row in order[:limit]]


def website_offers(retailer, fetch=fetch, pause=0.5, limit=PAGES_PER_READ):
    """Offers from up to ``limit`` of the product pages the site's sitemap lists.

    Every page is indexed as a ShopPage, and each fetch is stamped on its page, so the next read
    carries on where this one stopped.
    """
    base = retailer.source_url.rstrip("/")
    for page_pk, url in pages_to_read(retailer, sitemap_pages(base, fetch=fetch, must_answer=True), limit=limit):
        try:
            html = fetch(url).decode("utf-8", "replace")
        except ImportError_:
            html = None
        # Stamped even when the page fails, so a broken page cannot hold the front of every read.
        ShopPage.objects.filter(pk=page_pk).update(last_fetched_at=timezone.now())
        offer = page_offer(url, html) if html is not None else None
        if offer and offer.title:
            # Some shops leave the game out of the title ("Kyurem V Collection
            # Box") but put it in the address, so the address words go along
            # as a tag for the classifier to read.
            if not offer.tags:
                offer = dataclasses.replace(offer, tags=(slug_words(url),))
            yield dataclasses.replace(offer, page_pk=page_pk)
        if pause:
            time.sleep(pause)


def forget_pages(now=None, dry_run=False):
    """Remove pages no sitemap has listed for PAGE_FORGOTTEN_AFTER. Returns how many."""
    gone = ShopPage.objects.filter(last_seen_at__lt=(now or timezone.now()) - PAGE_FORGOTTEN_AFTER)
    if dry_run:
        return gone.count()
    return gone.delete()[0]


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
    An offer without a price is never unchanged: it confirms no price, so it never stamps one."""
    if listing.pk is None or listing._state.adding or offer.price <= 0:
        return False
    return (
        offer.availability == listing.availability
        and listing.price == offer.price
        and listing.delivery_known == (delivery is not None)
        and listing.delivery_cost == (delivery if delivery is not None else Decimal("0.00"))
        and link_key(listing.url) == link_key(offer.url)
    )


def stamp_checked(pks, checked_at):
    """Mark listings as checked at ``checked_at`` without rewriting anything else, then empty ``pks``.

    The product of a listing is judged again although its price is the same when the listing's last
    verdict was not OK (the other shops may have corrected theirs since), when the owner's trust in it
    has run out, or when it was out of date until now (the other shops were judged without it). One
    query per chunk, run before the stamp, finds them.
    """
    again = (
        ~Q(sanity=Listing.Sanity.OK)
        | Q(trusted_at__lt=trust_expiry(checked_at))
        | Q(last_checked__lt=stale_cutoff(checked_at), availability__in=Listing.BUYABLE)
    )
    for start in range(0, len(pks), STAMP_CHUNK):
        chunk = pks[start:start + STAMP_CHUNK]
        products = list(
            Listing.objects.filter(again, pk__in=chunk)
            .order_by().values_list("product_id", flat=True).distinct()
        )
        Listing.objects.filter(pk__in=chunk).update(last_checked=checked_at)
        for product_id in products:
            judge_product(product_id, now=checked_at)
    pks.clear()


def apply_offers(retailer, offers, checked_at=None, run=None, complete=True):
    """Update listings from ``offers``.

    ``complete`` says the offers cover the shop's whole range, so anything not
    among them is out of stock there. A website read fetches PAGES_PER_READ pages
    and is not complete: its unseen products keep their last state.

    Offers match a product by barcode, or by the link of a listing that was
    added by hand for this retailer. Returns (found, updated, unmatched titles).

    An offer that changes nothing on its listing is not written: its listing is only stamped as
    checked, in chunks, before each progress post and at the end, and when the read fails part way.

    An offer without a price is held until every offer has been read, because variants of one product
    come in any order. It is used only for a product no priced offer in this run touched, never changes
    a listing's link or title, never creates a listing, and can only take a listing out of stock.
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
    # A page the owner said is not one of ours is never matched by name. A No to the stockist finder is
    # about one product only: the page can still be matched by name to any other.
    ignored, refused = set(), {}
    for url, source, suggested_pk, looked_for_pk in ShopProduct.objects.filter(
        retailer=retailer, status=ShopProduct.Status.IGNORED
    ).values_list("url", "source", "suggested_id", "product_id"):
        if source == ShopProduct.Source.FINDER:
            refused[url] = {suggested_pk, looked_for_pk} - {None}
        else:
            ignored.add(url)

    to_stamp = []
    # Product pk -> (first offer without a price, whether every such offer said out of stock).
    unpriced = {}

    try:
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
                if match is not None and match[0] in refused.get(offer.url, ()):
                    # The owner said this page is not that product.
                    unmatched.append(f"{offer.title} [{offer.ean or 'no barcode'}] {offer.url}")
                    continue
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
                        defaults={"title": offer.title, "price": offer.price, "availability": offer.availability,
                                  "image_url": offer.image, "suggested_id": product_pk, "confidence": value,
                                  "status": ShopProduct.Status.LINKED, "last_seen": checked_at},
                    )
                elif match and value >= SUGGEST:
                    ShopProduct.objects.update_or_create(
                        retailer=retailer, url=offer.url,
                        defaults={"title": offer.title, "price": offer.price, "availability": offer.availability,
                                  "image_url": offer.image, "suggested_id": match[0], "confidence": value,
                                  "last_seen": checked_at},
                    )
                    unmatched.append(f"{offer.title} -> maybe {match[1]} ({value}%)")
                    continue
            if product_pk is None:
                unmatched.append(f"{offer.title} [{offer.ean or 'no barcode'}] {offer.url}")
                continue
            if not offer.url.lower().startswith(("http://", "https://")):
                unmatched.append(f"{offer.title} [link is not a web address]")
                continue
            if offer.page_pk is not None:
                # The shop's page now names its product, so a search can find it without a fetch.
                ShopPage.objects.filter(pk=offer.page_pk).exclude(product_id=product_pk).update(product_id=product_pk)
            if offer.price <= 0:
                # No price is not a price. A priced variant of the same product may still come later in
                # the run, so this offer waits until the end and never stands in for one.
                first, all_out = unpriced.get(product_pk, (offer, True))
                unpriced[product_pk] = (first, all_out and offer.availability == Listing.Availability.OUT_OF_STOCK)
                continue
            listing, created = Listing.objects.get_or_create(
                product_id=product_pk, retailer=retailer, defaults={"url": offer.url, "price": offer.price}
            )
            if product_pk in seen_products and listing.availability != Listing.Availability.OUT_OF_STOCK:
                # Several variants of one product: keep the cheapest one that is in stock.
                if offer.availability == Listing.Availability.OUT_OF_STOCK or offer.price >= listing.price:
                    continue
            seen_products.add(product_pk)
            title = (offer.title or "")[:300]
            # The shop's own publishing time is kept from the first read that gives it, so a later
            # republish cannot make a pre-order look as if it reached the site sooner than it did.
            published = offer.published_at if listing.shop_published_at is None else None
            if created or listing.url != offer.url or listing.title != title or published:
                listing.url = offer.url
                listing.title = title
                fields = ["url", "title"]
                if published:
                    listing.shop_published_at = published
                    fields.append("shop_published_at")
                listing.save(update_fields=fields)
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
                new=created,
            )
    except BaseException:
        # The listings seen so far were checked: keep that, unless the database cannot take it either.
        try:
            stamp_checked(to_stamp, checked_at)
        except Exception:
            logger.warning("Checks of %s not stamped after the read failed", retailer, exc_info=True)
        raise

    stamp_checked(to_stamp, checked_at)

    # Products this run saw only without a price: the stock of a listing we have, nothing more.
    unpriced = {pk: held for pk, held in unpriced.items() if pk not in seen_products}
    pks = list(unpriced)
    listings = {}
    for start in range(0, len(pks), STAMP_CHUNK):
        for listing in Listing.objects.filter(retailer=retailer, product_id__in=pks[start:start + STAMP_CHUNK]):
            listings.setdefault(listing.product_id, listing)
    for pk, (offer, all_out) in unpriced.items():
        listing = listings.get(pk)
        if listing is None:
            unmatched.append(f"{offer.title} [{offer.ean or 'no barcode'}] {offer.url} (no price)")
            continue
        # Still listed by the shop, so not swept out of stock below.
        seen_products.add(pk)
        if all_out and listing.availability != Listing.Availability.OUT_OF_STOCK:
            pricing.record_check(listing, price=Decimal("0"), delivery_cost=None,
                                 availability=Listing.Availability.OUT_OF_STOCK, checked_at=checked_at)
            updated += 1

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


def run_import(retailer, feed_path=None, fetch=None, page_pause=0.0):
    """Read one retailer's prices and save them. ``page_pause`` is the wait between Shopify product pages."""
    run = ImportRun.objects.create(retailer=retailer)
    fetch = retailer_fetch(retailer, fetch)
    try:
        if retailer.source_type == Retailer.Source.SHOPIFY:
            if not retailer.source_url:
                raise ImportError_("Set the shop address on the retailer first.")
            offers = shopify_offers(retailer, fetch=fetch, pause=page_pause)
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

    clear_list_caches(force=True)
    return run
