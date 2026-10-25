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
from urllib.parse import urlencode, urljoin

from . import importers
from .classify import classify
from .importers import ImportError_
from .models import Product

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
    # "free on orders over £150", "£3.99 (free on orders over £50)", "free for UK orders above £30"
    re.compile(r"\bfree\b(?: uk)?(?: on| for)? (?:all )?(?:uk )?orders? (?:over|above|of|from)\s*" + MONEY, re.I),
]

# "Royal Mail Tracked 48 £3.99", "Standard delivery £2.95", "2nd Class: £1.50".
STANDARD = [
    re.compile(r"(?:royal mail )?track(?:ed)? ?48[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"(?:2nd|second) class[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"standard (?:uk )?(?:delivery|shipping|postage|rate)[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"(?:uk|mainland uk) (?:delivery|shipping|postage)[^£.!?]{0,40}?" + MONEY, re.I),
    re.compile(r"(?:delivery|shipping|postage)(?: is| costs?| charge(?:s|d)? (?:at|is))?\s*(?:just |only |from )?" + MONEY, re.I),
]


# A page may give one threshold for single cards and another for sealed products ("FREE Royal Mail 48h Tracked
# delivery on single card orders over £20 and on sealed product orders over £30", Unicorn Cards, 24 Oct 2026),
# in words FREE_OVER does not foresee. RipRaptor compares sealed products, so a sealed threshold wins and one
# only for single cards never counts.
FREE_SENTENCE = re.compile(r"[^.!?]*\bfree\b[^.!?]*?(?:deliver|shipping|postage|p&p)[^.!?]*", re.I)
FREE_FIGURE = re.compile(r"(?:over|above|from|exceeding|more than)\s*" + MONEY, re.I)
SINGLES = re.compile(r"\bsingles?\b|\bsingle cards?\b", re.I)
SEALED = re.compile(r"\bsealed\b", re.I)
# A sentence about sending abroad ("Free international shipping on orders over £150") is not the UK threshold.
ABROAD = re.compile(r"\binternational|\boverseas\b|\bworldwide\b|\beurope|\beu\b|\busa?\b|rest of the world|"
                    r"outside (?:of )?(?:the )?uk", re.I)


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
    return _quote_at(text, match.start(), match.end())


def _quote_at(text, start, end):
    """The words around text[start:end], without a word cut in two at either side."""
    start, end = max(0, start - 60), min(len(text), end + 40)
    quote = text[start:end]
    if start > 0:
        quote = re.sub(r"^\S*\s", "", quote)
    if end < len(text):
        quote = re.sub(r"\s\S*$", "", quote)
    return quote.strip()


def free_thresholds(text):
    """[(figure, what, quote)] for every threshold in a sentence that offers free delivery, in page order. ``what``
    is "sealed" or "singles" when the words since the sentence's previous figure say which orders it is for."""
    found = []
    for sentence in FREE_SENTENCE.finditer(text):
        words = sentence.group(0)
        if ABROAD.search(words):
            continue
        since = 0
        for figure in FREE_FIGURE.finditer(words):
            before = words[since:figure.start()]
            what = "sealed" if SEALED.search(before) else "singles" if SINGLES.search(before) else ""
            found.append((Decimal(figure.group(1)), what,
                          _quote_at(text, sentence.start() + figure.start(), sentence.start() + figure.end())))
            since = figure.end()
    return found


def parse_delivery(text):
    """(cost, free_over, cost_quote, free_quote) read from delivery page text."""
    free_over = free_quote = None
    thresholds = free_thresholds(text)
    for figure, what, quote in thresholds:
        if what == "sealed":
            free_over, free_quote = figure, quote
            break
    for pattern in FREE_OVER if free_over is None else ():
        match = pattern.search(text)
        if match and not (SINGLES.search(match.group(0)) and not SEALED.search(match.group(0))):
            free_over, free_quote = Decimal(match.group(1)), _quote(text, match)
            break
    if free_over is None:
        for figure, what, quote in thresholds:
            if what == "":
                free_over, free_quote = figure, quote
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

    def post_form(self, url, fields):
        """Send a form, as a shop's own page does; the answer is a page, not read."""
        request = urllib.request.Request(url, data=urlencode(fields).encode(), headers={
            "User-Agent": "RipRaptor price checker (+https://ripraptor.com)",
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ImportError_(f"Could not fetch {url}: {exc}") from exc


def localize(session, base):
    """Ask the shop to price the basket in pounds for the UK. A Shopify shop that sells abroad prices a basket in
    the visitor's currency, and the server may not be in the UK: Kongs Cards answered in dollars, which no
    charge can be read from. Harmless where a shop sells in pounds only."""
    try:
        session.post_form(f"{base}/localization", {"form_type": "localization", "_method": "put", "country_code": "GB",
                                                   "return_to": "/cart"})
    except ImportError_:
        pass


PACKS = (Product.Type.BOOSTER_PACK, Product.Type.COLLECTOR_BOOSTER_PACK)


def sample_variants(products, low=Decimal("3"), high=Decimal("60")):
    """[(variant id, price)] of cheap, in-stock products that have to be posted, in the shop's own order.

    Boxed sealed products come first, then sealed packs: a lone booster pack goes as a letter, which is not what
    a box pays (Hammerhead TCG on 24 Oct 2026: £2.00 for a booster pack, £3.75 for a booster box). Nothing else
    is tried: a single card gets the "singles post" rate and an accessory its own, so a shop with no sealed
    product in stock keeps its charge unknown (Tier Zero Games on 25 Oct 2026: £2.50 for a single card).
    """
    boxed, packs = [], []
    for product in products:
        sealed = classify(product.get("title", ""), product.get("product_type", ""), product.get("vendor", ""),
                          tuple(product.get("tags") or []))
        if sealed is None:
            continue
        group = packs if sealed.product_type in PACKS else boxed
        for variant in product.get("variants", []):
            try:
                price = Decimal(str(variant.get("price")))
            except (ArithmeticError, TypeError, ValueError):
                continue
            if variant.get("available") and variant.get("requires_shipping", True) and low <= price <= high:
                group.append((variant["id"], price))
    return boxed + packs


def sample_variant(products, low=Decimal("3"), high=Decimal("60")):
    """The first of sample_variants, or None."""
    found = sample_variants(products, low, high)
    return found[0] if found else None


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


# Products tried for one basket check: one the basket refuses (Card Empire answered 422) or that gets no UK
# rate gives way to the next.
MAX_TRIES = 5


def shopify_delivery(base, session=None, fetch=None, free_over=None):
    """(standard charge, free_over confirmed?) from a basket check on a Shopify shop.

    ``free_over`` is the threshold read from the shop's page; when given, the basket is kept under it, and a
    basket just over it is priced too, to confirm it delivers free. A basket that comes back free may be over a
    threshold no page gave (Kongs Cards: free over £20), so a cheaper product is tried before free is believed.
    """
    base = base.rstrip("/")
    fetch = fetch or importers.fetch
    session = session or CartSession()
    products = json.loads(fetch(f"{base}/products.json?limit=250")).get("products", [])
    candidates = sample_variants(products)
    if free_over is not None:
        candidates = [candidate for candidate in candidates if candidate[1] < free_over] or candidates
    if not candidates:
        return None, False
    localize(session, base)
    cost = chosen = None
    tries = 0
    for variant_id, price in candidates:
        if chosen is not None and price >= chosen[1]:
            continue
        if tries >= MAX_TRIES:
            break
        tries += 1
        try:
            rate = uk_rate(session, base, variant_id, 1)
        except ImportError_:
            continue
        if rate is None:
            continue
        cost, chosen = rate, (variant_id, price)
        if rate > 0:
            break
    if cost is None:
        return None, False
    variant_id, price = chosen
    if cost == 0 and free_over is not None and price < free_over:
        # Free under the threshold the shop's own page gives is not believed: an option such as collection slipped
        # through, and a £0 charge would make every item there look delivered free (Pixel and Block, free over
        # £200, answered £0.00 for a £57.50 box on 25 Oct 2026).
        return None, False
    confirmed = False
    if cost is not None and free_over is not None and price < free_over:
        quantity = math.ceil(free_over / price) + 1
        if quantity <= MAX_QUANTITY:
            try:
                confirmed = uk_rate(session, base, variant_id, quantity) == 0
            except ImportError_:
                confirmed = False
    return cost, confirmed
