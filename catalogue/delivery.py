"""
Read a shop's delivery charges from its own delivery page.

    from catalogue.delivery import check_delivery
    finding = check_delivery("https://shop.example")

Shops put their charges on a page called delivery, shipping or similar. The
page is found by trying the usual addresses, its text is read, and two
figures are looked for: the standard UK charge and the order value above
which delivery is free. Every figure comes with the sentence it was read
from, so a person can check it.
"""

import html
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from http.cookiejar import CookieJar
from urllib.parse import urljoin

from . import importers
from .importers import ImportError_

PATHS = [
    "/pages/delivery", "/pages/delivery-information", "/pages/delivery-info", "/pages/delivery-returns",
    "/pages/shipping", "/pages/shipping-information", "/pages/shipping-info", "/pages/shipping-returns",
    "/pages/shipping-delivery", "/pages/shipping-policy", "/pages/postage", "/policies/shipping-policy",
    "/delivery", "/delivery-information", "/delivery-info", "/delivery-returns", "/shipping", "/shipping-information",
    "/shipping-returns", "/shipping-policy", "/postage", "/pages/faq", "/pages/faqs", "/faq", "/faqs",
]

DELIVERY_WORDS = re.compile(r"deliver|shipping|postage", re.I)
MONEY = r"£\s?(\d{1,3}(?:\.\d{2})?)"

# "Free UK delivery on orders over £40", "Free delivery available from £40",
# "orders over £50 qualify for free shipping".
FREE_OVER = [
    re.compile(
        r"free (?:uk |standard |royal mail |tracked |mainland uk )*(?:delivery|shipping|postage|p&p)"
        r"[^£.!?]{0,60}?(?:over|above|from|exceeding|of|more than)\s*" + MONEY, re.I),
    re.compile(r"orders? (?:over|above|of|exceeding|more than)\s*" + MONEY + r"[^£.!?]{0,60}?free (?:uk )?(?:delivery|shipping|postage)", re.I),
    re.compile(r"spend\s*" + MONEY + r"[^£.!?]{0,60}?free (?:uk )?(?:delivery|shipping|postage)", re.I),
]

# "Royal Mail Tracked 48 £3.99", "Standard delivery £2.95", "2nd Class: £1.50".
STANDARD = [
    re.compile(r"(?:royal mail )?track(?:ed)? ?48[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"(?:2nd|second) class[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"standard (?:uk )?(?:delivery|shipping|postage|rate)[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"(?:uk|mainland uk) (?:delivery|shipping|postage)[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"(?:delivery|shipping|postage)(?: is| costs?| charge(?:s|d)? (?:at|is))?\s*(?:just |only |from )?" + MONEY, re.I),
]


@dataclass
class Finding:
    url: str
    cost: Decimal | None
    free_over: Decimal | None
    cost_quote: str = ""
    free_quote: str = ""

    @property
    def complete(self):
        return self.cost is not None and self.free_over is not None


def page_text(raw):
    """Readable text from a page: no scripts, menus, headers or footers."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = re.sub(r"<(script|style|noscript|nav|header|footer|svg)\b[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</(?:p|div|li|tr|h\d)>", ". ", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _quote(text, match):
    start = max(0, match.start() - 60)
    end = min(len(text), match.end() + 40)
    return re.sub(r"^\S*\s|\s\S*$", "", text[start:end]).strip()


def parse_delivery(text):
    """(cost, free_over, cost_quote, free_quote) read from delivery page text."""
    free_over = free_quote = None
    for pattern in FREE_OVER:
        match = pattern.search(text)
        if match:
            free_over, free_quote = Decimal(match.group(1)), _quote(text, match)
            break
    cost = cost_quote = None
    for pattern in STANDARD:
        for match in pattern.finditer(text):
            value = Decimal(match.group(1))
            # A standard UK charge is a few pounds. Anything larger is a
            # courier or overseas rate, anything that is zero is not a charge.
            if 0 < value <= 15 and (cost is None or value < cost):
                cost, cost_quote = value, _quote(text, match)
        if cost is not None:
            break
    return cost, free_over, cost_quote or "", free_quote or ""


LINK = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
LINK_WORDS = re.compile(r"deliver|shipping|postage|p&amp;p|p&p", re.I)


def linked_pages(base, fetch=None):
    """Addresses the shop's own home page links to under a delivery-like name."""
    fetch = fetch or importers.fetch
    try:
        raw = fetch(base + "/")
    except ImportError_:
        return []
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    found = []
    for href, label in LINK.findall(text):
        label = re.sub(r"<[^>]+>", " ", label)
        if not (LINK_WORDS.search(label) or LINK_WORDS.search(href)):
            continue
        if re.search(r"returns? only|track(?:ing)? (?:my|your|an) order|mailto:|tel:", href + " " + label, re.I):
            continue
        url = urljoin(base + "/", html.unescape(href.strip()))
        if url.startswith(base) and url not in found:
            found.append(url)
    return found


def check_delivery(base, fetch=None, paths=PATHS):
    """The delivery Finding for a shop, or None when no delivery page was found."""
    fetch = fetch or importers.fetch
    base = base.rstrip("/")
    candidates = linked_pages(base, fetch=fetch) + [base + path for path in paths]
    for url in dict.fromkeys(candidates):
        try:
            raw = fetch(url)
        except ImportError_:
            continue
        text = page_text(raw)
        if not DELIVERY_WORDS.search(text) or "£" not in text:
            continue
        cost, free_over, cost_quote, free_quote = parse_delivery(text)
        if cost is None and free_over is None:
            continue
        return Finding(url=url, cost=cost, free_over=free_over, cost_quote=cost_quote, free_quote=free_quote)
    return None


# Shopify basket check --------------------------------------------------------
#
# Most shops only show delivery charges at the checkout. Shopify shops answer
# a basket's delivery options through /cart/shipping_rates.json, so one cheap
# product goes in a basket, the UK options are read, and the basket is emptied.

UK_ADDRESS = "shipping_address%5Bzip%5D=SW1A%201AA&shipping_address%5Bcountry%5D=United%20Kingdom&shipping_address%5Bprovince%5D="
NOT_DELIVERY = re.compile(r"collect|pick ?up|in[- ]store|local delivery", re.I)
MAX_QUANTITY = 30


class CartSession:
    """A cookie-keeping client, so the basket survives between requests."""

    def __init__(self, timeout=30):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.timeout = timeout

    def _open(self, url, data=None):
        request = urllib.request.Request(
            url, data=data, headers={"User-Agent": "RipRaptor price checker (+https://ripraptor.com)", "Accept": "application/json"},
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ImportError_(f"Could not fetch {url}: {exc}") from exc

    def get(self, url):
        return self._open(url)

    def post(self, url, payload):
        return self._open(url, json.dumps(payload).encode())


def sample_variant(products, low=Decimal("3"), high=Decimal("40")):
    """(variant id, price) of a cheap, in-stock product that has to be posted."""
    for product in products:
        for variant in product.get("variants", []):
            try:
                price = Decimal(str(variant.get("price")))
            except (ArithmeticError, TypeError, ValueError):
                continue
            if variant.get("available") and variant.get("requires_shipping", True) and low <= price <= high:
                return variant["id"], price
    return None


def uk_rate(session, base, variant_id, quantity):
    """The cheapest UK delivery charge for ``quantity`` of one variant, or None."""
    session.post(f"{base}/cart/clear.js", {})
    session.post(f"{base}/cart/add.js", {"items": [{"id": variant_id, "quantity": quantity}]})
    try:
        data = session.get(f"{base}/cart/shipping_rates.json?{UK_ADDRESS}")
    finally:
        try:
            session.post(f"{base}/cart/clear.js", {})
        except ImportError_:
            pass
    prices = []
    for rate in data.get("shipping_rates", []):
        if NOT_DELIVERY.search(rate.get("name", "")) or rate.get("currency", "GBP") != "GBP":
            continue
        try:
            prices.append(Decimal(str(rate["price"])))
        except (ArithmeticError, KeyError, TypeError, ValueError):
            continue
    return min(prices) if prices else None


def shopify_delivery(base, session=None, fetch=None, free_over=None):
    """(standard charge, free_over confirmed?) from a basket check on a Shopify shop.

    ``free_over`` is the threshold read from the shop's page; when given, a
    basket just over it is priced too, to confirm it delivers free.
    """
    base = base.rstrip("/")
    fetch = fetch or importers.fetch
    session = session or CartSession()
    products = json.loads(fetch(f"{base}/products.json?limit=250")).get("products", [])
    sample = sample_variant(products)
    if sample is None:
        return None, False
    variant_id, price = sample
    cost = uk_rate(session, base, variant_id, 1)
    confirmed = False
    if cost is not None and free_over is not None and price < free_over:
        quantity = math.ceil(free_over / price) + 1
        if quantity <= MAX_QUANTITY:
            try:
                confirmed = uk_rate(session, base, variant_id, quantity) == 0
            except ImportError_:
                confirmed = False
    return cost, confirmed
