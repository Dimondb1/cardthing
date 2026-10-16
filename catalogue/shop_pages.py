"""
The shop's own page as evidence for the Claude judge (catalogue/judge.py).

Before Claude is asked about a Things to check row, the shop page(s) involved are read once and a few
plain facts are taken from them: the page's title and heading, the product and the selected variant
(?variant= is respected), the price, the stock, the barcode, the pack counts and languages the
description names, a short excerpt of it, and whether the page shows one product at all. A page that
answers 404, lists many products, or now opens another product's page says so too.

Only the shops the owner added are asked, and only the way their reads already ask them:

- a Shopify shop through /products/<handle>.js, as the stock probe does (catalogue/probe.py);
- any other shop's page for its schema.org Product data, Open Graph tags, title and h1, as a website
  read does (importers.page_offer), with the cookie-keeping fetch when the shop needs a session;
- an eBay listing through eBay's own item lookup (catalogue/ebay.py), never by reading the page.

Amazon, feed shops (their links are affiliate clicks a server never follows) and prices entered by hand
are not read. Neither is a shop that is paused, switched off, waiting after errors, being read right
now (checked before every request), or asked by the background reader in the last WORKER_QUIET (its
probes, pulses and reads note each shop they ask in the shared cache, worker.note_asked), nor a page the
shop's robots.txt keeps robots away from (a 401 or 403 for robots.txt keeps them from every page; one
that could not be read stops that shop's pages for an hour). A redirect is followed only within the
shop's own site (another site's page is never read and never reported as the shop's), never into a
private network, and a redirect to another page of the shop is reported as the address having moved.
Each shop is asked no faster than its reads (worker.RATES), with one try, a short timeout and no
retries; a 429 or three failures in a row stop the shop being asked for the rest of the run, and
nothing here touches the shop's read back-off. A run reads at most PAGES_PER_RUN pages in PAGE_SECONDS.

What a page said is kept for a day per address (Django's cache), so asking again, the high-effort
second look and the next run never read the same page twice; a page that could not be read is tried
again after an hour. A page that was not read is said to be "not read", with why, and nothing else.
"""

import hashlib
import html as html_
import ipaddress
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.core.cache import cache
from django.utils import timezone

from . import autopilot, crawl, importers, languages
from .classify import PACK_COUNT
from .importers import JSON_LD, META, TITLE, ImportError_, clean_ean, listing_page, money, page_offer
from .models import ImportRun, Retailer
from .probe import shopify_js_url, variant_id

logger = logging.getLogger(__name__)

CACHE_FOR = timedelta(hours=24)
FAILED_CACHE_FOR = timedelta(hours=1)
ROBOTS_CACHE_FOR = timedelta(hours=24)
# A run reads at most this many pages (robots.txt included), in at most this long, so it stays well
# inside the judge's RUN_SECONDS; each request gets one try of at most FETCH_SECONDS.
PAGES_PER_RUN = 60
PAGE_SECONDS = 180
FETCH_SECONDS = 10
ERRORS_IN_A_ROW = 3
# A shop the background reader asked this recently is left alone: its pace is the worker's.
WORKER_QUIET = timedelta(seconds=30)
# How much of a page is sent: a short excerpt of the description, a few variant names. The facts of one
# page come to well under MAX_FACT_CHARS characters of evidence (about 300 tokens).
EXCERPT_CHARS = 300
SCAN_CHARS = 3000
VARIANTS_SHOWN = 8
TEXT_CHARS = 160
MAX_FACT_CHARS = 1200
# The excerpt a block keeps before lists and other words are cut to fit MAX_FACT_CHARS.
EXCERPT_KEPT = 120
ASPECTS_SHOWN = 8
# How the site reads each kind of shop's pages. Any other is never read.
READ = (Retailer.Source.SHOPIFY, Retailer.Source.WEBSITE, Retailer.Source.EBAY)
NOT_READ = {
    Retailer.Source.AMAZON: "Amazon pages are not read",
    Retailer.Source.FEED: "this shop's links are affiliate links, which the site never follows",
    Retailer.Source.MANUAL: "this price was entered by hand",
}
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
CANONICAL = re.compile(r"<link[^>]+rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)[\"']", re.I)
TAGS = re.compile(r"<[^>]+>")
SCRIPTS = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
STOCK_WORDS = {"in_stock": "in stock", "preorder": "pre-order", "out_of_stock": "out of stock"}


def text_of(markup, limit):
    """Plain text from a piece of HTML, whitespace collapsed, cut at ``limit`` characters."""
    text = html_.unescape(TAGS.sub(" ", SCRIPTS.sub(" ", str(markup or ""))))
    return " ".join(text.split())[:limit]


def cache_key(url):
    return "judge-page:" + hashlib.sha256((url or "").encode()).hexdigest()


def host(url):
    name = (urlsplit(url or "").hostname or "").lower()
    return name[4:] if name.startswith("www.") else name


def shop_hosts(retailer):
    return {host(address) for address in (retailer.website, retailer.source_url) if address}


def handle_of(url):
    path = urlsplit(url or "").path
    return path.split("/products/", 1)[1].strip("/").split("/")[0] if "/products/" in path else ""


def stock_word(availability):
    return STOCK_WORDS.get(str(availability), str(availability or "not given"))


class Refused(ImportError_):
    """A request the judge will not make or follow: another site, an address inside a private network,
    or a scheme other than http(s). It says nothing about the shop, so it never counts as its failure."""

    def __init__(self, url, why):
        super().__init__(f"Could not fetch {url}: {why}")
        self.why = why


def private_address(name):
    """Whether ``name`` resolves to an address that is not on the public internet (loopback, a private
    network, link-local, reserved). A name that does not resolve is left for the fetch to fail on."""
    try:
        found = socket.getaddrinfo(name, None)
    except (OSError, UnicodeError):
        return False
    for *_, address in found:
        try:
            if not ipaddress.ip_address(address[0].split("%")[0]).is_global:
                return True
        except ValueError:
            return True
    return False


def refused(url, hosts, allow_private=False):
    """Why the judge must not ask for ``url`` for a shop whose sites are ``hosts``, or ""."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        return "the address is not a web page"
    if host(url) not in hosts:
        return "the shop sent the page to another site"
    if not allow_private and private_address(parts.hostname or ""):
        return "the address is inside a private network"
    return ""


class Page(bytes):
    """A page's body, with the address it was finally read from after the shop's own redirects."""

    def __new__(cls, body, url):
        page = super().__new__(cls, body)
        page.url = url
        return page


class ShopRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to the same shop's site; any other is refused, never fetched."""

    def __init__(self, hosts, allow_private=False):
        self.hosts, self.allow_private = hosts, allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        why = refused(newurl, self.hosts, self.allow_private)
        if why:
            fp.close()
            raise Refused(req.full_url, why)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def page_fetch(retailer, allow_private=False):
    """The judge's fetch for one shop: one try with a deadline, redirects followed only within the shop's
    own site, never into a private network, and with the shop's cookies when it needs a session (as
    importers.session_fetch). Returns a Page; a failure is an ImportError_ worded as importers.fetch's."""
    import http.client
    import http.cookiejar

    hosts = shop_hosts(retailer)
    handlers = [ShopRedirects(hosts, allow_private)]
    if retailer.session_url:
        handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener = urllib.request.build_opener(*handlers)
    state = {"session": not retailer.session_url or bool(refused(retailer.session_url, hosts, allow_private))}

    def one(url, deadline):
        why = refused(url, hosts, allow_private)
        if why:
            raise Refused(url, why)
        request = urllib.request.Request(importers.safe_url(url), headers={"User-Agent": importers.USER_AGENT})
        try:
            with opener.open(request, timeout=importers.socket_timeout(deadline)) as response:
                return Page(importers.read_body(response, deadline), response.geturl())
        except urllib.error.HTTPError as exc:
            raise ImportError_(f"Could not fetch {url}: HTTP Error {exc.code}") from exc
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            raise ImportError_(f"Could not fetch {url}: {exc}") from exc

    def fetch(url, retries=0, deadline=None):
        if not state["session"]:
            state["session"] = True
            try:
                one(retailer.session_url, deadline)
            except ImportError_:
                pass
        return one(url, deadline)

    return fetch


def default_fetch_for(retailer):
    return page_fetch(retailer)


def moved_to(body, url):
    """The path a same-shop redirect took ``url`` to, or "" when the page answered at its own address."""
    final = getattr(body, "url", "") or url
    if urlsplit(final).path.rstrip("/") == urlsplit(url).path.rstrip("/"):
        return ""
    return urlsplit(final).path[:TEXT_CHARS]


class Reader:
    """Reads the shop pages of one judge run. ``fetch_for(retailer)`` gives the fetch for a shop (called
    as fetch(url, retries=0, deadline=...)), ``ebay_request`` stands in for ebay.http, and ``cache_only``
    reads nothing new (a dry run)."""

    def __init__(self, fetch_for=None, ebay_request=None, clock=None, sleep=None, now=None,
                 pages=PAGES_PER_RUN, seconds=PAGE_SECONDS, cache_only=False, wall=None):
        self.fetch_for = fetch_for or default_fetch_for
        self.ebay_request = ebay_request
        self.clock = clock or time.monotonic
        self.wall = wall or time.time
        self.sleep = sleep or time.sleep
        self.now = now
        self.pages_left = pages
        self.seconds = seconds
        self.cache_only = cache_only
        self.started = None
        self.fetches = {}
        self.last = {}
        self.errors = {}
        self.blocked = {}
        self.ebay_headers = None
        self.requests = 0

    # What a page says ---------------------------------------------------------------------------

    def facts(self, url, retailer):
        """The facts of the page at ``url`` (a dict that JSON can hold), from the cache when it has them.
        Never raises: a page that could not be read is {"status": "not read", "why": ...}."""
        if not url:
            return {"status": "not read", "why": "the row has no address"}
        found = cache.get(cache_key(url))
        if found is not None:
            return found
        why = self.why_not(url, retailer)
        if why:
            return {"status": "not read", "why": why}
        try:
            if retailer.source_type == Retailer.Source.EBAY:
                found = self.read_ebay(url)
            elif retailer.source_type == Retailer.Source.SHOPIFY and "/products/" in urlsplit(url).path:
                found = self.read_shopify(url, retailer)
            else:
                found = self.read_page(url, retailer)
        except NotNow as stopped:
            return {"status": "not read", "why": str(stopped)}
        except Exception:   # a page must never stop a run
            logger.warning("Claude judge: reading %s failed", url, exc_info=True)
            found = {"status": "not read", "why": "the page could not be read"}
        found["fetched_at"] = (self.now or timezone.now()).isoformat()
        failed = found["status"] == "not read"
        cache.set(cache_key(url), found, int((FAILED_CACHE_FOR if failed else CACHE_FOR).total_seconds()))
        return found

    def why_not(self, url, retailer):
        """Why the page is not read now, or ""."""
        if self.cache_only:
            return "pages are not read in a dry run"
        if retailer.source_type not in READ:
            return NOT_READ.get(retailer.source_type, "this kind of shop is not read")
        if crawl.all_paused():
            return "Pause all is on"
        if not retailer.is_active:
            return "the shop is switched off"
        if retailer.reading_paused:
            return "reading this shop is paused"
        if retailer.backoff_until and retailer.backoff_until > timezone.now():
            return "the shop is waiting after errors"
        if retailer.pk in self.blocked:
            return self.blocked[retailer.pk]
        if retailer.source_type != Retailer.Source.EBAY:
            if urlsplit(url).scheme not in ("http", "https"):
                return "the address is not a web page"
            if host(url) not in shop_hosts(retailer):
                return "the address is not on this shop's site"
            if self.being_read(retailer):
                return "the shop is being read now"
            if self.worker_asking(retailer):
                return "the stock watcher is asking this shop now"
        return self.out_of_budget()

    def out_of_budget(self):
        if self.pages_left <= 0:
            return "this run has read as many pages as it may"
        if self.started is not None and self.clock() - self.started > self.seconds:
            return "this run has spent its time for reading pages"
        return ""

    def being_read(self, retailer):
        """Whether a read of the shop is running now: the page waits, so the judge never competes with it.
        Asked again before every request (one small query), so a read that starts mid-run is seen."""
        since = timezone.now() - importers.ABANDONED_AFTER
        return ImportRun.objects.filter(
            retailer_id=retailer.pk, finished_at__isnull=True, started_at__gte=since).exists()

    def worker_asking(self, retailer):
        """Whether the background reader (its probes, pulses and reads) asked the shop in the last
        WORKER_QUIET: the judge then leaves the shop alone, so the two never add up to more than the
        shop's pace and the judge never slows the worker."""
        from .worker import last_asked

        asked = last_asked(retailer.pk)
        return asked is not None and self.wall() - asked < WORKER_QUIET.total_seconds()

    # Asking the shop ----------------------------------------------------------------------------

    def get(self, url, retailer):
        """One polite request: the shop's own pace, one try, a short deadline. Returns the body."""
        why = self.out_of_budget()
        if why:
            raise OutOfTime(why)
        from .worker import RATES

        if self.started is None:
            self.started = self.clock()
        rate = RATES.get(retailer.source_type, (0.5, 1))[0]
        last = self.last.get(retailer.pk)
        if last is not None:
            wait = last + 1 / rate - self.clock()
            if wait > 0:
                self.sleep(wait)
        # Checked right before the request, after any wait: a shop read or the stock watcher that
        # started since the run began has the shop to itself.
        if self.being_read(retailer):
            raise NotNow("the shop is being read now")
        if self.worker_asking(retailer):
            raise NotNow("the stock watcher is asking this shop now")
        self.pages_left -= 1
        self.requests += 1
        if retailer.pk not in self.fetches:
            self.fetches[retailer.pk] = self.fetch_for(retailer)
        try:
            body = self.fetches[retailer.pk](url, retries=0, deadline=time.monotonic() + FETCH_SECONDS)
        except ImportError_ as exc:
            status = crawl.http_status(str(exc))
            if not importers.gone(exc) and not isinstance(exc, Refused):
                self.errors[retailer.pk] = self.errors.get(retailer.pk, 0) + 1
                if status == 429:
                    self.blocked[retailer.pk] = "the shop asked the site to slow down"
                elif self.errors[retailer.pk] >= ERRORS_IN_A_ROW:
                    self.blocked[retailer.pk] = "the shop did not answer"
            raise
        finally:
            self.last[retailer.pk] = self.clock()
        self.errors[retailer.pk] = 0
        return body

    def robots_why(self, url, retailer):
        """Why the shop's robots.txt keeps the site's robot from ``url``, or "" when it may read it.

        As the robots convention (and urllib.robotparser) has it: no robots.txt (404 or 410) allows
        everything and a 401 or 403 forbids everything, each kept a day per shop. A robots.txt that could
        not be read (5xx, 429, a timeout, a refused redirect) means no page of that shop is read: that is
        kept only FAILED_CACHE_FOR, so robots.txt is asked again in the next run after it."""
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        key = "judge-robots:" + hashlib.sha256(base.encode()).hexdigest()
        kept = cache.get(key)
        if isinstance(kept, str):
            kept = {"rules": kept}
        if kept is None:
            keep_for = ROBOTS_CACHE_FOR
            try:
                kept = {"rules": self.get(f"{base}/robots.txt", retailer).decode("utf-8", "replace")[:100_000]}
            except ImportError_ as exc:
                status = crawl.http_status(str(exc))
                if importers.gone(exc):
                    kept = {"rules": ""}
                elif status in (401, 403) and not isinstance(exc, Refused):
                    kept = {"rules": "User-agent: *\nDisallow: /\n"}
                else:
                    kept, keep_for = {"unreadable": True}, FAILED_CACHE_FOR
            cache.set(key, kept, int(keep_for.total_seconds()))
        if kept.get("unreadable"):
            return "the shop's robots.txt could not be read"
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(kept["rules"].splitlines())
        if parser.can_fetch(importers.USER_AGENT, url):
            return ""
        return "the shop's robots.txt asks robots not to read this page"

    def fetch_or_fact(self, url, retailer):
        """(body, None), or (None, facts) when robots.txt says no or the shop answered with an error."""
        why = self.robots_why(url, retailer)
        if why:
            return None, {"status": "not read", "why": why}
        try:
            return self.get(url, retailer), None
        except Refused as exc:
            return None, {"status": "not read", "why": exc.why}
        except ImportError_ as exc:
            if importers.gone(exc):
                return None, {"status": "gone", "http": crawl.http_status(str(exc))}
            return None, {"status": "not read", "why": "the shop did not answer"}

    def read_shopify(self, url, retailer):
        address = shopify_js_url(url)
        body, said = self.fetch_or_fact(address, retailer)
        if said:
            return said
        try:
            data = json.loads(body)
        except ValueError:
            return self.read_page(url, retailer)   # not a Shopify answer after all: read it as a page
        variants = [v for v in data.get("variants") or [] if isinstance(v, dict)]
        found = {"status": "product", "source": "shopify", "name": text_of(data.get("title"), TEXT_CHARS)}
        handle = data.get("handle") or ""
        if handle and handle_of(url) and handle != handle_of(url):
            found.update(status="moved", now_at=f"/products/{handle}"[:TEXT_CHARS])
        elif moved_to(body, address):
            found.update(status="moved", now_at=moved_to(body, address).removesuffix(".js"))
        names = [text_of(v.get("title"), 60) for v in variants if v.get("title") not in (None, "", "Default Title")]
        if len(variants) > 1:
            found["variants"] = names[:VARIANTS_SHOWN]
        wanted = variant_id(url)
        chosen = None
        if wanted is not None:
            chosen = next((v for v in variants if str(v.get("id")) == wanted), None)
            if chosen is None:
                found["variant_missing"] = True
        elif len(variants) == 1:
            chosen = variants[0]
        if chosen is not None:
            if chosen.get("title") not in (None, "", "Default Title"):
                found["variant"] = text_of(chosen["title"], 80)
            found["price"] = str(pence(chosen.get("price")) or "")
            found["availability"] = "in stock" if chosen.get("available") else "out of stock"
            found["barcode"] = clean_ean(chosen.get("barcode"))
        elif variants:
            prices = sorted(p for p in (pence(v.get("price")) for v in variants) if p is not None)
            if prices:
                found["price"] = str(prices[0]) if prices[0] == prices[-1] else f"{prices[0]} to {prices[-1]}"
            found["availability"] = "in stock" if any(v.get("available") for v in variants) else "out of stock"
            found["barcodes"] = [code for code in (clean_ean(v.get("barcode")) for v in variants) if code][:VARIANTS_SHOWN]
        if found.get("availability") == "in stock" and importers.says_preorder(
                {"title": data.get("title"), "tags": data.get("tags")}):
            found["availability"] = "pre-order"
        description = data.get("description") or ""
        found["excerpt"] = text_of(description, EXCERPT_CHARS)
        found["scan"] = text_of(description, SCAN_CHARS)
        return found

    def read_page(self, url, retailer):
        body, said = self.fetch_or_fact(url, retailer)
        if said:
            return said
        page = body.decode("utf-8", "replace")
        found = {"source": "page"}
        title = TITLE.search(page)
        heading = H1.search(page)
        found["title"] = text_of(title.group(1), TEXT_CHARS) if title else ""
        found["h1"] = text_of(heading.group(1), TEXT_CHARS) if heading else ""
        products, description = [], ""
        for block in JSON_LD.findall(page):
            try:
                data = json.loads(block.strip(), strict=False)
            except ValueError:
                continue
            for node in importers._walk(data):
                kind = node.get("@type", "")
                if "Product" in (kind if isinstance(kind, list) else [kind]) and node.get("name"):
                    products.append(node)
                    description = description or str(node.get("description") or "")
        names = {" ".join(str(node["name"]).lower().split()) for node in products}
        meta = {key.lower(): value for key, value in META.findall(page)}
        description = description or meta.get("og:description") or meta.get("description") or ""
        found["excerpt"] = text_of(description, EXCERPT_CHARS)
        found["scan"] = text_of(description, SCAN_CHARS)
        if moved_to(body, url):
            # The shop redirected the address to another page of its own site.
            found["now_at"] = moved_to(body, url)
        if listing_page(url) or len(names) > 1:
            found.update(status="category", listed=len(names))
            return found
        offer = page_offer(url, page)
        if offer is None:
            found["status"] = "no product data"
            found["name"] = text_of(meta.get("og:title", ""), TEXT_CHARS)
            return found
        found.update(status="product", name=text_of(offer.title, TEXT_CHARS), price=str(offer.price),
                     availability=stock_word(offer.availability), barcode=offer.ean)
        canonical = CANONICAL.search(page)
        named = (canonical.group(1) if canonical else "") or meta.get("og:url", "")
        if "now_at" not in found and named and host(named) == host(url) and urlsplit(named).path.rstrip("/") != urlsplit(url).path.rstrip("/"):
            found["now_at"] = urlsplit(named).path[:TEXT_CHARS]
        return found

    def read_ebay(self, url):
        from . import ebay

        item_id = ebay.item_id_of(url)
        if not item_id:
            return {"status": "not read", "why": "the address names no eBay item"}
        why = self.out_of_budget()
        if why:
            raise OutOfTime(why)
        if self.started is None:
            self.started = self.clock()
        request = self.ebay_request or (lambda address, headers, data=None: ebay.http(address, headers, data=data, retries=0))
        try:
            if self.ebay_headers is None:
                app, cert, campaign = ebay.credentials()
                self.ebay_headers = ebay.headers_for(ebay.access_token(app, cert, request=request), campaign)
            self.pages_left -= 1
            self.requests += 1
            answer = request(ebay.ITEMS_URL + "?item_ids=" + item_id, self.ebay_headers)
        except ebay.EbayError as exc:
            if "11001" in str(exc) or "404" in str(exc):
                return {"status": "gone", "http": 404, "source": "ebay"}
            if "Set RIPRAPTOR_EBAY" in str(exc):
                return {"status": "not read", "why": "eBay's keys are not set"}
            return {"status": "not read", "why": "eBay did not answer"}
        items = [item for item in answer.get("items") or [] if isinstance(item, dict)]
        if not items:
            return {"status": "gone", "http": 404, "source": "ebay"}
        item = items[0]
        price = item.get("price") or {}
        found = {"status": "product", "source": "ebay", "name": text_of(item.get("title"), TEXT_CHARS),
                 "price": str(money(price.get("value")) or "") if price.get("currency", "GBP") == "GBP" else "",
                 "barcode": clean_ean(item.get("gtin")), "condition": text_of(item.get("condition"), 60)}
        stock = ((item.get("estimatedAvailabilities") or [{}])[0] or {}).get("estimatedAvailabilityStatus", "")
        found["availability"] = "out of stock" if stock == "OUT_OF_STOCK" else "in stock" if stock else ""
        aspects = [f"{a.get('name')}: {a.get('value')}" for a in item.get("localizedAspects") or []
                   if isinstance(a, dict) and a.get("name") and a.get("value")]
        found["aspects"] = [text_of(a, 80) for a in aspects[:ASPECTS_SHOWN]]
        description = item.get("shortDescription") or item.get("description") or ""
        found["excerpt"] = text_of(description, EXCERPT_CHARS)
        found["scan"] = text_of(f"{description} {' '.join(aspects)}", SCAN_CHARS)
        return found


class NotNow(Exception):
    """The page is not read in this run (nothing is kept, so a later run reads it)."""


class OutOfTime(NotNow):
    pass


def pence(value):
    """A /products/<handle>.js price (pence) in pounds."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return money(value / 100)
    return money(value)


# What Claude is sent -----------------------------------------------------------------------------

PAGE_SAYS = {
    "product": "one product's page",
    "category": "a category page that lists several products, not one product's page",
    "gone": "gone: the shop answers that the page does not exist",
    "no product data": "no product data: the page names no product with a price",
}


def evidence(facts, product, shop=""):
    """What Claude is sent about one page (page_evidence), with ``shop`` naming the shop when the row does
    not (a pair), at most MAX_FACT_CHARS characters as JSON (capped)."""
    said = page_evidence(facts, product)
    if shop:
        said["shop"] = text_of(shop, 80)
    return capped(said)


def page_evidence(facts, product):
    """The facts of one page as the judge sends them, labelled as read from the shop. Every field that
    holds the shop's own words starts with "shop_" (the prompt's rule 2). ``product`` is the product the
    page is compared with, for the barcode and the languages."""
    if facts.get("status") == "not read":
        return {"read_from_shop": False, "why_not": facts.get("why", "")}
    when = facts.get("fetched_at", "")
    try:
        when = timezone.localtime(datetime.fromisoformat(when)).strftime("%-d %b %Y %H:%M %Z")
    except (TypeError, ValueError):
        pass
    said = {"read_from_shop": True, "read_at": when,
            "read_through": {"shopify": "the shop's product data", "ebay": "eBay's item lookup"}.get(
                facts.get("source"), "the page's product data and tags")}
    status = facts.get("status")
    if status == "gone":
        said["page"] = PAGE_SAYS["gone"] + (f" (HTTP {facts['http']})" if facts.get("http") else "")
        return said
    said["page"] = PAGE_SAYS.get(status, "")
    if status in ("product", "moved") and (status == "moved" or facts.get("now_at")):
        said["page"] = "the address now opens another product page"
        said["shop_now_at"] = facts.get("now_at", "")
    elif facts.get("now_at"):
        said["page"] = "the address now opens another page of the shop: " + said["page"]
        said["shop_now_at"] = facts["now_at"]
    if status == "category" and facts.get("listed"):
        said["page"] += f" ({facts['listed']} listed)"
    for key, label in (("title", "shop_page_title"), ("h1", "shop_heading"), ("name", "shop_product_name"),
                       ("variant", "shop_selected_variant"), ("condition", "shop_condition")):
        if facts.get(key):
            said[label] = facts[key]
    if facts.get("variants"):
        said["shop_variants_on_page"] = facts["variants"]
    if facts.get("variant_missing"):
        said["selected_variant"] = "the variant in the address is no longer on the page"
    if facts.get("aspects"):
        said["shop_item_specifics"] = facts["aspects"]
    if facts.get("price"):
        said["page_price"] = f"£{facts['price']}"
    if facts.get("availability"):
        said["page_stock"] = facts["availability"]
    if status in ("product", "moved"):
        said["page_barcode"] = autopilot.barcode_check(product, facts.get("barcode") or "")
        ours = importers.ean_key(product.ean)
        if not facts.get("barcode") and facts.get("barcodes"):
            # No variant selected: say whether any of the page's variants carries our barcode.
            said["page_barcode"] = ("one of the page's variants carries ours" if ours and ours in map(importers.ean_key, facts["barcodes"])
                                    else autopilot.barcode_check(product, facts["barcodes"][0]))
    scan = facts.get("scan", "")
    if scan:
        found = words_in(scan, product)
        said["pack_counts_in_description"] = found["pack_counts"]
        said["languages_in_description"] = found["languages"]
        for key, label in (("editions", "editions_in_description"), ("conditions", "conditions_in_description")):
            if found[key]:
                said[label] = found[key]
    if facts.get("excerpt"):
        said["shop_description_excerpt"] = facts["excerpt"]
    return said


# Edition words a description can name: each is another product from the shops' plain edition.
EDITIONS = (
    ("Pokemon Center", re.compile(r"\bpok[eé]mon cent(?:er|re)\b|\bpc (?:etb|elite trainer)\b", re.I)),
    ("first edition", re.compile(r"\b(?:1st|first) edition\b", re.I)),
    ("exclusive", re.compile(r"\bexclusive\b", re.I)),
    ("limited edition", re.compile(r"\blimited edition\b", re.I)),
    ("premium", re.compile(r"\bpremium\b", re.I)),
)


def words_in(text, product):
    """What a shop's description names that can tell products apart: pack counts, languages, edition words
    and condition words (damaged, opened, no shrink wrap), each a short list. Read the same way for a page's
    description and for the description a read stored."""
    from .matching import conditions

    game = product.game.slug if product.game_id else ""
    counts = sorted({int(m.group(1)) for m in PACK_COUNT.finditer(text) if 0 < int(m.group(1)) < 1000})
    named = [languages.name(code) or "English" for code in languages.stated_all(text, game)][:4]
    if re.search(r"\benglish\b", text, re.I) and "English" not in named:
        named.append("English")
    return {
        "pack_counts": counts[:6], "languages": named,
        "editions": [name for name, pattern in EDITIONS if pattern.search(text)],
        "conditions": sorted({"no shrink wrap" if state == "noshrink" else state for state in conditions(text)}),
    }


def size(said):
    return len(json.dumps(said, ensure_ascii=False))


def capped(said):
    """``said`` cut to at most MAX_FACT_CHARS characters as JSON, whatever the shop wrote. The excerpt
    gives way first, down to EXCERPT_KEPT; then the longer of the item specifics and the variants lose
    their last entries, down to one; then the longest of the shop's words is shortened."""
    excerpt = "shop_description_excerpt"
    while size(said) > MAX_FACT_CHARS and len(said.get(excerpt, "")) > EXCERPT_KEPT:
        said[excerpt] = said[excerpt][:max(EXCERPT_KEPT, len(said[excerpt]) - 50)]
    while size(said) > MAX_FACT_CHARS:
        lists = [key for key in ("shop_item_specifics", "shop_variants_on_page") if len(said.get(key) or []) > 1]
        if not lists:
            break
        longest = max(lists, key=lambda key: size(said[key]))
        said[longest] = said[longest][:-1]
    while size(said) > MAX_FACT_CHARS:
        words = [key for key, value in said.items() if key.startswith("shop_") and isinstance(value, str) and value]
        if not words:
            break
        longest = max(words, key=lambda key: len(said[key]))
        said[longest] = said[longest][:-20]
    return said


def representative(product):
    """The shop page that best stands for a product in a pair: a live listing at a shop whose pages are
    read, Shopify first, then other shops, then eBay, most recently checked first. None when there is none."""
    from .models import Listing

    order = {Retailer.Source.SHOPIFY: 0, Retailer.Source.WEBSITE: 1, Retailer.Source.EBAY: 2}
    rows = (Listing.objects.live().filter(product=product, retailer__source_type__in=READ)
            .select_related("retailer").order_by("-last_checked")[:20])
    rows = sorted(rows, key=lambda listing: order.get(listing.retailer.source_type, 9))
    return rows[0] if rows else None


def attach(row, reader):
    """Add the shop page facts to a judge row's evidence: "shop_page" for a found page or a listing, and
    "first_shop_page" and "second_shop_page" for a pair. The row's fingerprint does not include them."""
    if row.kind == "found":
        shop_row = row.shop_product
        row.evidence["shop_page"] = evidence(reader.facts(shop_row.url, shop_row.retailer), row.product)
    elif row.kind == "offer":
        listing = row.listing
        row.evidence["shop_page"] = evidence(reader.facts(listing.url, listing.retailer), row.product)
    elif row.kind == "pair":
        first, second = sorted((row.product, row.other), key=lambda product: product.pk)
        for label, product in (("first_shop_page", first), ("second_shop_page", second)):
            listing = representative(product)
            if listing is None:
                row.evidence[label] = {"read_from_shop": False, "why_not": "no shop page of ours to read"}
                continue
            row.evidence[label] = evidence(reader.facts(listing.url, listing.retailer), product,
                                           shop=listing.retailer.name)
    return row
