"""
Adding shops from a pasted list of addresses (the owner, 8 Nov 2026: "find how they are connecting 208 shops, we need
to best this"). The Add shops page (web/add_shops.py) saves each new address as a ShopToAdd; the background reader
checks them one at a time in a thread of its own (catalogue/worker.py), politely and as RipRaptor's own reader:

* a shop already on RipRaptor (the same host, with or without www.) is left as it is, and one the owner deleted
  (RemovedShop) is not added back,
* a shop whose robots.txt asks readers like ours to stay out, or that turns the check away (a 403, a bot challenge),
  is never added: nothing is worked round,
* a Shopify shop must price in pounds and list sealed trading card products,
* any other shop must list product pages in its sitemap whose data carries a price in pounds (the Website source).

A shop that passes is added switched on, read within the hour like any new shop, its delivery left unknown (shown as
"plus delivery") until the daily check_delivery --unknown reads it from the shop's own delivery page or a basket.

A shop that asked on the List your shop page (inbox/shops.py) is a held row: anyone can type its address, so it is
read with public_get, which reaches only addresses on the public internet (never this server, its network or a
private address, checked again on every redirect), and one that passes waits for the owner's approval (PASSED) and is
added by add_passed. The owner, 14 Nov 2026: "they need to provide the info it then goes to the shop checker function
we already have and is checked, need to make sure this a cannot be abused, I will then approve it".
"""

import ipaddress
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from . import importers
from .models import RemovedShop, Retailer, ShopToAdd

logger = logging.getLogger(__name__)

CHECK_SECONDS = 25        # one shop's check gives up after this long
SHOPIFY_PAGES = 4         # pages of 250 products looked through for a sealed card product
PRODUCT_PAGES = 4         # a website's product pages tried for a price
BATCH = 10                # shops checked by one turn of the reader's thread

SEALED = re.compile(r"booster|elite\s*trainer|\betb\b|collection\s*box|premium\s*collection|starter\s*(?:deck|set)|"
                    r"structure\s*deck|build\s*(?:&|and)\s*battle|\btins?\b|blister|commander\s*deck|illumineer|trove|"
                    r"display|bundle", re.I)
CARDS = re.compile(r"pok[eé]mon|one\s*piece|lorcana|magic|\bmtg\b|yu-?gi-?oh|digimon|dragon\s*ball|star\s*wars|"
                   r"flesh\s*(?:and|&)\s*blood|riftbound|gundam|union\s*arena|weiss|sorcery|tcg|trading\s*card|card\s*game",
                   re.I)
SINGLE = re.compile(r"\b(?:single|psa|bgs|cgc|graded|slab|\d+/\d+)\b", re.I)


def host_of(address):
    """The host of a pasted address, without www. ("" when it has none): shop.co.uk, https://www.shop.co.uk/x."""
    address = (address or "").strip()
    if not address:
        return ""
    if "://" not in address:
        address = "https://" + address
    try:
        return (urlsplit(address).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def base_of(address):
    """The shop's front page address, https and no path: https://www.shop.co.uk."""
    address = address.strip()
    if "://" not in address:
        address = "https://" + address
    parts = urlsplit(address)
    return f"https://{parts.hostname.lower()}"


def take(text):
    """Save the addresses in ``text`` (one or more per line, separated by spaces or commas) that are new as shops to
    add. Returns (saved, already listed) counts."""
    saved = known = 0
    for word in re.split(r"[\s,]+", text or ""):
        host = host_of(word)
        if not host or "." not in host:
            continue
        _row, created = ShopToAdd.objects.get_or_create(host=host, defaults={"address": base_of(word)})
        saved += created
        known += not created
    return saved, known


class NotPublic(importers.ImportError_):
    """An address that is not on the public internet, refused before anything is sent to it."""


def public_host(host):
    """Raise NotPublic unless every address ``host`` resolves to is a public one: not loopback, private, link local,
    reserved, multicast or this server's own."""
    try:
        found = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise NotPublic(f"Could not find {host}") from exc
    if not found:
        raise NotPublic(f"Could not find {host}")
    for *_rest, sockaddr in found:
        address = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if not address.is_global or address.is_multicast:
            raise NotPublic(f"{host} is not on the public internet")


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to http or https on the public internet."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise NotPublic(f"Redirected to {newurl[:80]}")
        public_host(parts.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_get(deadline):
    """A reader for an address anyone could have typed: like importers.fetch, once, but only to the public internet."""
    opener = urllib.request.build_opener(PublicRedirects)

    def get(url):
        url = importers.safe_url(url)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.port not in (None, 80, 443):
            raise NotPublic(f"Not a web address: {url[:80]}")
        public_host(parts.hostname)
        request = urllib.request.Request(url, headers={"User-Agent": importers.USER_AGENT})
        try:
            with opener.open(request, timeout=importers.socket_timeout(deadline)) as response:
                return importers.read_body(response, deadline)
        except NotPublic:
            raise
        except urllib.error.HTTPError as exc:
            raise importers.ImportError_(f"Could not fetch {url}: {exc}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise importers.ImportError_(f"Could not fetch {url}: {exc}") from exc

    return get


def retailer_for(host):
    """The shop on RipRaptor at ``host`` (its website or price source), or None."""
    for shop in Retailer.objects.only("pk", "name", "website", "source_url"):
        if host in (host_of(shop.website), host_of(shop.source_url)):
            return shop
    return None


def robots_for(base, get):
    """The shop's robots.txt rules, or None when it turns us away. A missing file allows everything."""
    rules = urllib.robotparser.RobotFileParser()
    try:
        rules.parse(get(f"{base}/robots.txt").decode("utf-8", "replace").splitlines())
    except importers.ImportError_ as exc:
        if re.search(r"\b(401|403|429)\b", str(exc)):
            return None
        rules.parse([])
    return rules


def sealed(title, kind=""):
    text = f"{title} {kind}"
    return bool(SEALED.search(text) and CARDS.search(text) and not SINGLE.search(text))


def shopify_check(base, get, rules):
    """(found, why, name) for a Shopify shop: found None when the shop is not on Shopify."""
    try:
        meta = json.loads(get(f"{base}/meta.json"))
    except (importers.ImportError_, json.JSONDecodeError):
        return None, "", ""
    if not isinstance(meta, dict) or "currency" not in meta:
        return None, "", ""
    name = str(meta.get("name") or "")[:120]
    currency = str(meta.get("currency") or "").upper()
    if currency and currency != "GBP":
        return False, f"Prices in {currency}, not pounds.", name
    if not rules.can_fetch(importers.USER_AGENT, f"{base}/products.json"):
        return False, "Its robots.txt asks readers like ours not to read its products.", name
    count = 0
    for page in range(1, SHOPIFY_PAGES + 1):
        try:
            products = json.loads(get(f"{base}/products.json?limit=250&page={page}")).get("products", [])
        except (importers.ImportError_, json.JSONDecodeError, AttributeError):
            break
        count += sum(1 for p in products if sealed(p.get("title", ""), p.get("product_type", "")))
        if count or len(products) < 250:
            break
    if not count:
        return False, "No sealed trading card products found among its products.", name
    return True, "Shopify shop in pounds with sealed card products.", name


def website_check(base, get, rules):
    """(found, why, name) for any other shop: its sitemap's product pages must carry a price in pounds."""
    if not rules.can_fetch(importers.USER_AGENT, f"{base}/"):
        return False, "Its robots.txt asks readers like ours to stay out.", ""
    try:
        pages = importers.sitemap_pages(base, fetch=get, limit=40)
    except importers.ImportError_:
        pages = []
    tried = 0
    for url, _lastmod in pages:
        if tried >= PRODUCT_PAGES:
            break
        if not rules.can_fetch(importers.USER_AGENT, url) or importers.listing_page(url):
            continue
        tried += 1
        try:
            html = get(url).decode("utf-8", "replace")
        except importers.ImportError_:
            continue
        offer = importers.page_offer(url, html)
        if offer is not None and offer.price:
            return True, "Product pages with prices, read from its sitemap.", ""
    if not pages:
        return False, "No product feed or sitemap found. Ask the shop for a feed.", ""
    return False, "Its product pages carry no price we can read.", ""


def unique_slug(name, host):
    base = slugify(name)[:100] or slugify(host)[:100] or "shop"
    slug, n = base, 2
    while Retailer.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def unique_name(name):
    candidate, n = name, 2
    while Retailer.objects.filter(name=candidate).exists():
        candidate, n = f"{name} ({n})", n + 1
    return candidate


def shop_name(found, host):
    """The shop's own name from its data, else one made from its address (shop-name.co.uk: Shop Name)."""
    if found.strip():
        return found.strip()
    return host.split(".")[0].replace("-", " ").title()


def check(row, get=None, now=None):
    """Check one shop to add and add it when it passes, or for a held row (a shop's own request) mark it PASSED for
    the owner to approve. Never raises for the shop's own faults: the row says why."""
    now = now or timezone.now()
    deadline = time.monotonic() + CHECK_SECONDS
    if get is None:
        get = public_get(deadline) if row.hold else (lambda url: importers.fetch(url, retries=0, deadline=deadline))
    row.checked_at = now
    existing = retailer_for(row.host)
    if existing is not None:
        row.status, row.retailer, row.note = ShopToAdd.Status.ALREADY, existing, f"Already on RipRaptor as {existing.name}."
        row.save()
        return row
    base = base_of(row.address)
    if row.hold:
        try:
            public_host(urlsplit(base).hostname)
        except NotPublic:
            row.status, row.note = ShopToAdd.Status.NOT_ADDED, "Its address is not a website on the public internet."
            row.save()
            return row
    if RemovedShop.covers("", base):
        row.status, row.note = ShopToAdd.Status.NOT_ADDED, "You deleted this shop before, so it is not added back."
        row.save()
        return row
    rules = robots_for(base, get)
    if rules is None:
        row.status, row.note = ShopToAdd.Status.NOT_ADDED, "The shop turned our reader away. Nothing is worked round."
        row.save()
        return row
    found, why, name = shopify_check(base, get, rules)
    source = Retailer.Source.SHOPIFY
    if found is None:
        found, why, name = website_check(base, get, rules)
        source = Retailer.Source.WEBSITE
    if not found:
        row.status, row.note = ShopToAdd.Status.NOT_ADDED, why
        row.save()
        return row
    if row.hold:
        row.status, row.note = ShopToAdd.Status.PASSED, why
        row.found_name, row.found_source = shop_name(name, row.host)[:120], source
        row.save()
        return row
    return create(row, name, source, why)


def add_passed(row):
    """Add a held shop that passed its check, now the owner approves it. Returns the row, ADDED with its shop, or
    ALREADY when the shop was added another way meanwhile."""
    existing = retailer_for(row.host)
    if existing is not None:
        row.status, row.retailer, row.note = ShopToAdd.Status.ALREADY, existing, f"Already on RipRaptor as {existing.name}."
        row.save()
        return row
    return create(row, row.found_name, row.found_source or Retailer.Source.WEBSITE, row.note)


def create(row, name, source, why):
    """Add the shop the row checked, switched on, as ``name`` read the ``source`` way."""
    base = base_of(row.address)
    name = unique_name(shop_name(name, row.host))
    try:
        with transaction.atomic():
            shop = Retailer.objects.create(
                name=name, slug=unique_slug(name, row.host), website=base + "/", source_type=source,
                source_url=base + "/", delivery_note="Delivery charge not yet confirmed",
            )
    except IntegrityError:
        logger.exception("Could not add %s", row.host)
        row.status, row.note = ShopToAdd.Status.NOT_ADDED, "Could not be saved: add it by hand under Retailers."
        row.save()
        return row
    row.status, row.retailer, row.note = ShopToAdd.Status.ADDED, shop, why
    row.save()
    return row


def check_waiting(limit=BATCH, get=None):
    """Check up to ``limit`` shops waiting, oldest first. Returns how many were checked."""
    done = 0
    for row in ShopToAdd.objects.filter(status=ShopToAdd.Status.WAITING).order_by("created_at", "pk")[:limit]:
        try:
            check(row, get=get)
        except Exception:  # noqa: BLE001 - one shop's fault never stops the rest
            logger.exception("Checking %s failed", row.host)
            row.status, row.note, row.checked_at = ShopToAdd.Status.NOT_ADDED, "The check failed: try again.", timezone.now()
            row.save()
        done += 1
    return done
