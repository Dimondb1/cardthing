"""
The stockist finder: products that one shop sells, or none, are looked for at every other Shopify shop
the owner has already added. It never asks any other site.

For each product (most wanted first) and each such shop that has no listing for it:

1. Free: the shop's last whole read is looked at again. A line it could not match that names the same
   game and kind of product, with a name that agrees with ours word for word both ways, is the shop's
   page for it.
2. Otherwise the shop's own search is asked (/search/suggest.json), first with the game and our name,
   then, when that finds nothing, with the set code and kind. Its titles are judged as a shop read
   judges them.

The best page found is read once (/products/<handle>.js) for its barcode, price and stock. Then:

- the same barcode as ours, or a name that agrees both ways when neither side has a barcode: the
  listing is added through apply_offers, so the price is judged against the other shops and its
  history starts like any other;
- a different barcode never links, whatever the name says;
- a likely match (60 to 99, or a sure name with a barcode on one side only) waits on the Things to
  check page for the owner's Yes or No;
- anything else is noted as not found.

A shop is not asked about the same product again for 14 days, 7 when people want it, and never after
the owner said No. Requests are capped at 20 per shop and 200 in all each hour, one second apart at one
shop, and inside the background reader at a fifth of its requests, so whole-shop reads always come first.
"""

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote_plus, urlsplit

from django.core.cache import cache
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils import timezone

from . import crawl, importers
from .classify import classify
from .importers import Catalogue, ImportError_, apply_offers, ean_key, link_key, money, product_offers
from .classify import TYPES
from .matching import AUTO_LINK, SUGGEST, TYPE_WORDS, covers, key_words, shop_title
from .models import (
    DailyPageView, ImportRun, Listing, OutboundClick, Product, Retailer, ShopProduct, StockAlert, StockistSearch,
    stale_cutoff,
)

# Interest: clicks are the strongest sign a comparison matters, and an alert is an explicit ask.
CLICK_POINTS, CLICK_DAYS = 5, 7
VIEW_POINTS, WATCHED_POINTS, RECENT_DAYS = 3, 3, 2
ALERT_POINTS = 10
PREORDER_POINTS = 2
NEW_POINTS, NEW_DAYS = 2, 14
# The owner's "Search other shops now" tap, held for an hour.
BOOST_POINTS = 20
BOOST_FOR = timedelta(hours=1)
BOOST_KEY = "finder:boost"

# A product absent from a shop rarely appears there within a fortnight.
RETRY_AFTER = timedelta(days=14)
HOT_RETRY_AFTER = timedelta(days=7)
HOT_INTEREST = 10
# A shop that could not be asked (a timeout, an error page) is asked again the next day.
ERROR_RETRY_AFTER = timedelta(days=1)
# A shop whose search answers 404 or not with JSON is not searched for a week.
SUGGEST_OFF_FOR = timedelta(days=7)

# Whole-shop reads must never be starved.
SHOP_HOURLY = 20
OVERALL_HOURLY = 200
WORKER_SHARE = 0.2
SHOP_GAP = 1.0
LOG_KEY = "finder:requests"
ERRORS_IN_A_ROW = 3

BATCH = 30
# Products whose shops are looked up in one query while candidates are chosen.
CHUNK = 200
MARKETPLACES = (Retailer.Source.EBAY, Retailer.Source.AMAZON)
UNMATCHED_LINE = re.compile(r"^(?P<title>.+) \[[^\]]*\] (?P<url>https?://\S+)$")


# Interest ---------------------------------------------------------------------------------------

def boosts(now=None):
    """{product id: when the owner tapped Search other shops now} for taps in the last hour."""
    now = now or timezone.now()
    held = cache.get(BOOST_KEY) or {}
    return {pk: at for pk, at in held.items() if now - at < BOOST_FOR}


def boost(product_id, now=None):
    """Put a product first in the finder's queue for the next hour and let it ask shops asked before."""
    now = now or timezone.now()
    held = boosts(now)
    held[product_id] = now
    cache.set(BOOST_KEY, held, int(BOOST_FOR.total_seconds()))
    Product.objects.filter(pk=product_id).update(finder_checked_at=None)


def interest_scores(now=None):
    """{product id: points} for every active product with any, in four queries.

    5 per click to a shop in 7 days, 3 per product page view and 3 per loaded watchlist row in 2 days,
    10 per confirmed stock alert, 2 when any shop has it on pre-order, 2 when added in the last 14 days,
    and 20 for an hour after the owner asks for a search.
    """
    now = now or timezone.now()
    today = timezone.localdate(now)
    views, watched = {}, {}
    for kind, key, hits in (
        DailyPageView.objects.filter(
            date__gte=today - timedelta(days=RECENT_DAYS - 1),
            kind__in=[DailyPageView.Kind.PRODUCT, DailyPageView.Kind.WATCHED],
        )
        .exclude(key="")
        .values_list("kind", "key", "hits")
    ):
        into = watched if kind == DailyPageView.Kind.WATCHED else views
        into[key] = into.get(key, 0) + hits
    clicks = dict(
        OutboundClick.objects.filter(created_at__gte=now - timedelta(days=CLICK_DAYS))
        .values_list("product").annotate(n=Count("id")).values_list("product", "n")
    )
    alerts = dict(
        StockAlert.objects.filter(confirmed_at__isnull=False)
        .values_list("product").annotate(n=Count("id")).values_list("product", "n")
    )
    boosted = boosts(now)
    new_since = now - timedelta(days=NEW_DAYS)
    preorder = Q(
        listings__availability=Listing.Availability.PREORDER, listings__is_active=True, listings__retailer__is_active=True
    )
    scores = {}
    for pk, slug, created, preorders in (
        Product.objects.filter(is_active=True)
        .annotate(preorders=Count("listings", filter=preorder))
        .values_list("pk", "slug", "created_at", "preorders")
    ):
        points = (
            CLICK_POINTS * clicks.get(pk, 0) + VIEW_POINTS * views.get(slug, 0)
            + WATCHED_POINTS * watched.get(slug, 0) + ALERT_POINTS * alerts.get(pk, 0)
        )
        if preorders:
            points += PREORDER_POINTS
        if created and created >= new_since:
            points += NEW_POINTS
        if pk in boosted:
            points += BOOST_POINTS
        if points:
            scores[pk] = points
    return scores


# Candidates -------------------------------------------------------------------------------------

@dataclass
class Candidate:
    product: Product
    interest: int
    shops: list = field(default_factory=list)   # Retailers to ask, paying shops first


def searchable_shops(now=None, busy=()):
    """Active Shopify shops that are not paused or waiting after errors: paying shops first, then by name."""
    now = now or timezone.now()
    return list(
        Retailer.objects.filter(is_active=True, reading_paused=False, source_type=Retailer.Source.SHOPIFY)
        .exclude(source_url="")
        .exclude(pk__in=list(busy))
        .filter(Q(backoff_until__isnull=True) | Q(backoff_until__lte=now))
        .alias(unpaid=Case(When(affiliate_url_template="", then=Value(1)), default=Value(0), output_field=IntegerField()))
        .order_by("unpaid", "name")
    )


def waits(search, interest, boosted_at, now):
    """True while a pair searched before must not be asked again."""
    if search.outcome == StockistSearch.Outcome.IGNORED:
        return True
    if boosted_at is not None and search.searched_at < boosted_at and search.outcome in (
        StockistSearch.Outcome.NONE, StockistSearch.Outcome.ERROR,
    ):
        return False
    if search.outcome == StockistSearch.Outcome.ERROR:
        wait = ERROR_RETRY_AFTER
    elif interest >= HOT_INTEREST:
        wait = HOT_RETRY_AFTER
    else:
        wait = RETRY_AFTER
    return now - search.searched_at < wait


def candidates(limit=BATCH, now=None, shops=None, scores=None):
    """Products with at most one shop able to sell them now, each with the shops to ask about it.

    Most interest first, then pre-orders and new products, then the one looked for longest ago (never
    first). A shop is left out for a product it already lists, and for one asked about it lately or told
    No. A product with no shop left to ask is not a candidate.
    """
    now = now or timezone.now()
    shops = searchable_shops(now) if shops is None else shops
    if not shops or limit <= 0:
        return []
    scores = interest_scores(now) if scores is None else scores
    boosted = boosts(now)
    buyable = Q(
        listings__is_active=True, listings__retailer__is_active=True, listings__last_checked__gte=stale_cutoff(now),
        listings__availability__in=Listing.BUYABLE, listings__sanity__in=Listing.COUNTED,
    ) & ~Q(listings__retailer__source_type__in=MARKETPLACES)
    preorder = Q(listings__availability=Listing.Availability.PREORDER, listings__is_active=True)
    rows = list(
        Product.objects.active()
        .annotate(shops=Count("listings__retailer", filter=buyable, distinct=True),
                  preorders=Count("listings", filter=preorder, distinct=True))
        .filter(shops__lte=1)
        .values_list("pk", "created_at", "finder_checked_at", "preorders")
    )
    new_since = now - timedelta(days=NEW_DAYS)

    def order(row):
        pk, created, checked, preorders = row
        fresh = bool(preorders) or (created is not None and created >= new_since)
        return (-scores.get(pk, 0), not fresh, checked is not None, checked.timestamp() if checked else 0, pk)

    rows.sort(key=order)
    shop_ids = [shop.pk for shop in shops]
    chosen = []
    for start in range(0, len(rows), CHUNK):
        pks = [row[0] for row in rows[start:start + CHUNK]]
        listed = set(
            Listing.objects.filter(product_id__in=pks, retailer_id__in=shop_ids).values_list("product_id", "retailer_id")
        )
        searched = {
            (s.product_id, s.retailer_id): s
            for s in StockistSearch.objects.filter(product_id__in=pks, retailer_id__in=shop_ids)
            .only("product_id", "retailer_id", "searched_at", "outcome")
        }
        for pk in pks:
            interest = scores.get(pk, 0)
            ask = []
            for shop in shops:
                if (pk, shop.pk) in listed:
                    continue
                search = searched.get((pk, shop.pk))
                if search is not None and waits(search, interest, boosted.get(pk), now):
                    continue
                ask.append(shop)
            if ask:
                chosen.append((pk, interest, ask))
            if len(chosen) >= limit:
                break
        if len(chosen) >= limit:
            break
    products = Product.objects.select_related("game", "product_set").in_bulk([pk for pk, _i, _s in chosen])
    return [Candidate(products[pk], interest, ask) for pk, interest, ask in chosen if pk in products]


# Judging ----------------------------------------------------------------------------------------

# Words that say what kind of product something is, not which one.
KIND_WORDS = {word for phrase in TYPE_WORDS for word in phrase.split()} | {
    word for _kind, phrases in TYPES for phrase in phrases for word in phrase.replace("'", " ").split()
}
# A likely match must carry at least this share of the words that name our product, beyond its kind:
# "Delta Reign Elite Trainer Box" shares three of five words with "Prismatic Evolutions Elite Trainer
# Box" and is a different product.
NAMING_SHARE = 0.5


def naming_words(text):
    return key_words(shop_title(text)) - KIND_WORDS


def judge(product, title, sealed=None):
    """0 to 100 for a shop title against this one product. 100 needs the names to agree both ways and,
    when the shop's kind of product is known, the same kind. A title missing most of the words that name
    our product is not a likely match, however many kind words it shares."""
    game = product.game.slug
    match, value = Catalogue([(product.pk, product.name, game)]).best_match(title, game=game)
    if match is None:
        return 0
    if value >= AUTO_LINK and not covers(shop_title(title), product.name):
        value = AUTO_LINK - 1
    ours = naming_words(product.name)
    if ours and len(ours & naming_words(title)) < NAMING_SHARE * len(ours):
        value = min(value, SUGGEST - 1)
    if sealed is not None and sealed.product_type != product.product_type:
        value = min(value, AUTO_LINK - 1)
    return value


def fold(text):
    """Accents dropped, so "Pokémon" finds a shop that writes "Pokemon"."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def queries(product):
    """What to type into the shop's search: the game and our name, then the set code and kind."""
    found = [fold(f"{product.game.display_short} {shop_title(product.name)}".strip())]
    code = product.product_set.code.strip() if product.product_set_id else ""
    if code:
        found.append(fold(f"{code} {product.get_product_type_display()}"))
    return found


def suggest_url(base, query):
    return f"{base}/search/suggest.json?q={quote_plus(query)}&resources%5Btype%5D=product&resources%5Blimit%5D=10"


def handle_of(url):
    """The Shopify handle in a product address, or ""."""
    path = urlsplit(url).path
    if "/products/" not in path:
        return ""
    return path.split("/products/", 1)[1].split("/", 1)[0]


def pounds(value):
    """A /products/<handle>.js price (pence as a whole number) in the products.json form ("149.99")."""
    if isinstance(value, int):
        return str((Decimal(value) / 100).quantize(Decimal("0.01")))
    return value


def as_listing(data):
    """A /products/<handle>.js answer in the shape of a products.json entry, for importers.product_offers."""
    images = []
    for image in data.get("images") or []:
        src = image if isinstance(image, str) else (image or {}).get("src", "")
        if src:
            images.append({"src": f"https:{src}" if src.startswith("//") else src})
    return {
        "handle": data.get("handle") or "",
        "title": data.get("title") or "",
        "tags": data.get("tags") or [],
        "product_type": data.get("type") or "",
        "vendor": data.get("vendor") or "",
        "published_at": data.get("published_at"),
        "images": images,
        "variants": [
            {**variant, "price": pounds(variant.get("price")), "featured_image": None}
            for variant in data.get("variants") or [] if isinstance(variant, dict)
        ],
    }


# Budget -----------------------------------------------------------------------------------------

class NoBudget(Exception):
    def __init__(self, whole_batch):
        super().__init__("no budget")
        self.whole_batch = whole_batch


class AskFailed(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Budget:
    """The finder's requests in the last hour, kept in the cache every process shares."""

    def __init__(self, now, requests=None, worker_requests=None):
        self.limit = requests
        self.used = 0
        self.worker_requests = worker_requests
        self.log = self.recent(cache.get(LOG_KEY) or [], now)

    @staticmethod
    def recent(log, now):
        since = now.timestamp() - 3600
        return [(at, shop) for at, shop in log if at > since]

    def check(self, shop_id, now):
        """Raise NoBudget when a request to this shop now would go over a cap."""
        self.log = self.recent(self.log, now)
        if self.limit is not None and self.used >= self.limit:
            raise NoBudget(True)
        if len(self.log) >= OVERALL_HOURLY:
            raise NoBudget(True)
        if self.worker_requests is not None and len(self.log) + 1 > WORKER_SHARE * (self.worker_requests() + 1):
            raise NoBudget(True)
        if sum(1 for _at, shop in self.log if shop == shop_id) >= SHOP_HOURLY:
            raise NoBudget(False)

    def spend(self, shop_id, now):
        self.used += 1
        self.log.append((now.timestamp(), shop_id))
        cache.set(LOG_KEY, self.log, 3600)


# The search -------------------------------------------------------------------------------------

@dataclass
class Result:
    products: int = 0
    linked: int = 0
    review: int = 0
    requests: int = 0

    def __str__(self):
        return (f"{self.products} product{'' if self.products == 1 else 's'} searched, "
                f"{self.linked} listing{'' if self.linked == 1 else 's'} added, {self.review} to check")


@dataclass
class Hit:
    handle: str
    value: int


def plain_fetch(shop):
    if shop.session_url:
        return importers.session_fetch(shop.session_url)

    def fetch(url):
        return importers.fetch(url, retries=0)

    return fetch


class Finder:
    """One batch. ``fetch_for(shop)`` gives the fetch for a shop, ``busy()`` the shops another job is
    asking now, and ``stop()`` says when to start nothing new."""

    def __init__(self, fetch_for=None, clock=None, sleep=None, budget=None, busy=None, stop=None,
                 requests=None, worker_requests=None):
        self.fetch_for = fetch_for or plain_fetch
        self.clock = clock or timezone.now
        self.sleep = sleep or time.sleep
        self.budget = budget or Budget(self.clock(), requests=requests, worker_requests=worker_requests)
        self.busy = busy or (lambda: ())
        self.stop = stop or (lambda: False)
        self.fetches = {}
        self.unmatched = {}
        self.taken = {}
        self.ignored = {}
        self.blocked = set()
        self.errors = {}
        self.last_asked = {}
        self.out = False

    # Batch

    def run(self, limit=BATCH):
        result = Result()
        if crawl.all_paused():
            return result
        now = self.clock()
        for candidate in candidates(limit, now=now, shops=searchable_shops(now, busy=self.busy())):
            if self.out or self.stop():
                break
            searched = False
            for shop in candidate.shops:
                if self.out or self.stop():
                    break
                outcome = self.search(candidate.product, shop)
                searched = searched or outcome is not None
                if outcome == StockistSearch.Outcome.LINKED:
                    result.linked += 1
                elif outcome == StockistSearch.Outcome.REVIEW:
                    result.review += 1
            if searched or not (self.out or self.stop()):
                # Stamped whether or not anything was found; a product the batch had no budget left for is not.
                result.products += 1
                Product.objects.filter(pk=candidate.product.pk).update(finder_checked_at=self.clock())
        result.requests = self.budget.used
        if result.linked:
            from .signals import clear_list_caches

            clear_list_caches(force=True)
        return result

    def still_askable(self, shop):
        """The shop is still there to ask: not paused, switched off or waiting since the batch began."""
        if shop.pk in self.blocked or shop.pk in set(self.busy()) or crawl.all_paused():
            return False
        now = self.clock()
        return Retailer.objects.filter(pk=shop.pk, is_active=True, reading_paused=False).filter(
            Q(backoff_until__isnull=True) | Q(backoff_until__lte=now)
        ).exists()

    def search(self, product, shop):
        """Look for one product at one shop. Returns the outcome written, or None when the shop was not asked."""
        if not self.still_askable(shop):
            return None
        try:
            hit = self.from_last_read(product, shop)
            if hit is None and self.can_suggest(shop):
                hit = self.from_suggest(product, shop)
            if hit is None:
                return self.note(product, shop, StockistSearch.Outcome.NONE)
            return self.decide(product, shop, hit)
        except NoBudget as exc:
            if exc.whole_batch:
                self.out = True
            else:
                self.blocked.add(shop.pk)
            return None
        except AskFailed:
            return self.note(product, shop, StockistSearch.Outcome.ERROR)

    # Requests

    def ask(self, shop, url):
        self.budget.check(shop.pk, self.clock())
        last = self.last_asked.get(shop.pk)
        if last is not None:
            wait = SHOP_GAP - (self.clock() - last).total_seconds()
            if wait > 0:
                self.sleep(wait)
        now = self.clock()
        self.last_asked[shop.pk] = now
        self.budget.spend(shop.pk, now)
        if shop.pk not in self.fetches:
            self.fetches[shop.pk] = self.fetch_for(shop)
        try:
            body = self.fetches[shop.pk](url)
        except ImportError_ as exc:
            status = crawl.http_status(str(exc))
            self.errors[shop.pk] = self.errors.get(shop.pk, 0) + 1
            if status == 429:
                # The shop says it is being asked too often: it waits like a failed read.
                shop.read_failed(self.clock(), 429, f"Looking for stockists: {exc}")
                self.blocked.add(shop.pk)
            elif self.errors[shop.pk] >= ERRORS_IN_A_ROW:
                self.blocked.add(shop.pk)
            raise AskFailed(status, str(exc)) from exc
        self.errors[shop.pk] = 0
        return body

    # Step one: the shop's last whole read

    def unmatched_lines(self, shop):
        """[(title, address, Sealed)] for the sealed products the shop's last whole read could not match,
        worked out once per batch."""
        if shop.pk not in self.unmatched:
            run = (
                ImportRun.objects.filter(retailer=shop, finished_at__isnull=False, note="", error="")
                .order_by("-started_at").only("unmatched").first()
            )
            lines = []
            for line in (run.unmatched.splitlines() if run else []):
                found = UNMATCHED_LINE.match(line.strip())
                if not found or not handle_of(found["url"]):
                    continue
                sealed = classify(found["title"])
                if sealed is not None:
                    lines.append((found["title"], found["url"], sealed))
            self.unmatched[shop.pk] = lines
        return self.unmatched[shop.pk]

    def from_last_read(self, product, shop):
        for title, url, sealed in self.unmatched_lines(shop):
            if sealed.game != product.game.slug or sealed.product_type != product.product_type:
                continue
            if self.is_taken(shop, url, product):
                continue
            if judge(product, title, sealed) >= AUTO_LINK:
                return Hit(handle_of(url), AUTO_LINK)
        return None

    # Step two: the shop's own search

    def can_suggest(self, shop):
        return shop.suggest_ok or shop.suggest_failed_at is None or self.clock() - shop.suggest_failed_at >= SUGGEST_OFF_FOR

    def suggest_failed(self, shop):
        shop.suggest_ok = False
        shop.suggest_failed_at = self.clock()
        shop.save(update_fields=["suggest_ok", "suggest_failed_at"])

    def from_suggest(self, product, shop):
        base = shop.source_url.rstrip("/")
        for query in queries(product):
            try:
                raw = self.ask(shop, suggest_url(base, query))
            except AskFailed as exc:
                if exc.status == 404:
                    self.suggest_failed(shop)
                raise
            try:
                found = json.loads(raw)["resources"]["results"]["products"]
                if not isinstance(found, list):
                    raise TypeError
            except (ValueError, KeyError, TypeError) as exc:
                self.suggest_failed(shop)
                raise AskFailed(None, f"{base} search did not answer with JSON") from exc
            if not shop.suggest_ok:
                shop.suggest_ok = True
                shop.save(update_fields=["suggest_ok"])
            if found:
                return self.best_result(product, shop, base, found)
        return None

    def best_result(self, product, shop, base, found):
        best = None
        for item in found:
            if not isinstance(item, dict):
                continue
            handle, title = item.get("handle") or "", item.get("title") or ""
            if not handle or not title or self.is_taken(shop, f"{base}/products/{handle}", product):
                continue
            sealed = classify(title, item.get("type") or "", item.get("vendor") or "", tuple(item.get("tags") or ()),
                              money(item.get("price")))
            if sealed is None or sealed.game != product.game.slug:
                continue
            value = judge(product, title, sealed)
            if value >= SUGGEST and (best is None or value > best.value):
                best = Hit(handle, value)
        return best

    # The page and the decision

    def decide(self, product, shop, hit):
        base = shop.source_url.rstrip("/")
        raw = self.ask(shop, f"{base}/products/{hit.handle}.js")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError
        except (ValueError, TypeError) as exc:
            raise AskFailed(None, f"{base}/products/{hit.handle}.js did not answer with JSON") from exc
        offers = [offer for offer in product_offers(base, as_listing(data)) if offer.price > 0]
        if not offers or self.is_taken(shop, offers[0].url, product):
            return self.note(product, shop, StockistSearch.Outcome.NONE)
        ours = ean_key(product.ean)
        out = Listing.Availability.OUT_OF_STOCK
        same = [offer for offer in offers if ours and ean_key(offer.ean) == ours]
        if same:
            return self.link(product, shop, min(same, key=lambda o: (o.availability == out, o.price)), AUTO_LINK)
        judged = []
        for offer in offers:
            sealed = classify(offer.title, offer.shop_type, offer.vendor, offer.tags, offer.price)
            if sealed is not None and sealed.game == product.game.slug:
                judged.append((judge(product, offer.title, sealed), offer))
        if not judged:
            return self.note(product, shop, StockistSearch.Outcome.NONE)
        value, offer = max(judged, key=lambda item: (item[0], item[1].availability != out, -item[1].price))
        theirs = ean_key(offer.ean)
        if value >= AUTO_LINK and not ours and not theirs:
            return self.link(product, shop, offer, value)
        if value >= SUGGEST:
            # A barcode on one side only, a different barcode or a likely name: the owner decides.
            return self.review(product, shop, offer, value)
        return self.note(product, shop, StockistSearch.Outcome.NONE, offer.url, value)

    def is_taken(self, shop, url, product):
        """The page is another product's listing at this shop, or one the owner said is not ours."""
        if shop.pk not in self.taken:
            self.taken[shop.pk] = {
                link_key(u): pk for u, pk in Listing.objects.filter(retailer=shop).values_list("url", "product_id")
            }
            self.ignored[shop.pk] = {
                link_key(u) for u in ShopProduct.objects.filter(retailer=shop, status=ShopProduct.Status.IGNORED)
                .values_list("url", flat=True)
            }
        key = link_key(url)
        owner = self.taken[shop.pk].get(key)
        return key in self.ignored[shop.pk] or (owner is not None and owner != product.pk)

    def link(self, product, shop, offer, value):
        apply_offers(shop, [replace(offer, product_pk=product.pk)], checked_at=self.clock(), complete=False)
        if not Listing.objects.filter(product=product, retailer=shop).exists():
            return self.note(product, shop, StockistSearch.Outcome.NONE, offer.url, value)
        self.taken.setdefault(shop.pk, {})[link_key(offer.url)] = product.pk
        return self.note(product, shop, StockistSearch.Outcome.LINKED, offer.url, value)

    def review(self, product, shop, offer, value):
        ShopProduct.objects.update_or_create(
            retailer=shop, url=offer.url,
            defaults={"title": offer.title[:300], "price": offer.price, "image_url": (offer.image or "")[:1000],
                      "suggested": product, "product": product, "confidence": value,
                      "status": ShopProduct.Status.REVIEW, "source": ShopProduct.Source.FINDER,
                      "last_seen": self.clock()},
        )
        return self.note(product, shop, StockistSearch.Outcome.REVIEW, offer.url, value)

    def note(self, product, shop, outcome, url="", confidence=0):
        StockistSearch.objects.update_or_create(
            product=product, retailer=shop,
            defaults={"searched_at": self.clock(), "outcome": outcome, "url": url[:1000], "confidence": confidence},
        )
        return outcome


def find(limit=BATCH, **kwargs):
    """Run one batch and return its Result."""
    return Finder(**kwargs).run(limit)


# The owner's answers ----------------------------------------------------------------------------

def link(row):
    """Yes: add the listing for the row's product at its shop. The next read or check of the shop prices
    it. Returns the listing, or None when the row names no product."""
    product = row.suggested
    if product is None:
        return None
    listing, created = Listing.objects.get_or_create(
        product=product, retailer=row.retailer,
        defaults={"url": row.url, "price": row.price or 0, "availability": Listing.Availability.IN_STOCK,
                  # The price was seen then, not now.
                  "last_checked": min(row.last_seen, timezone.now())},
    )
    if listing.url != row.url:
        listing.url = row.url
        listing.save(update_fields=["url"])
    if row.image_url and not product.image_src:
        product.image_url = row.image_url
        product.save(update_fields=["image_url"])
    row.status = ShopProduct.Status.LINKED
    row.save(update_fields=["status"])
    if row.source == ShopProduct.Source.FINDER and row.product_id:
        StockistSearch.objects.update_or_create(
            product_id=row.product_id, retailer=row.retailer,
            defaults={"searched_at": timezone.now(), "outcome": StockistSearch.Outcome.LINKED, "url": row.url[:1000],
                      "confidence": row.confidence},
        )
    if created:
        from .sanity import judge_product

        # Judged against the other shops at once, as a read would.
        judge_product(product.pk)
    return listing


def ignore(row):
    """No: the row is not this product. A row the finder found is never asked about at that shop again."""
    row.status = ShopProduct.Status.IGNORED
    row.save(update_fields=["status"])
    if row.source == ShopProduct.Source.FINDER and row.product_id:
        StockistSearch.objects.update_or_create(
            product_id=row.product_id, retailer=row.retailer,
            defaults={"searched_at": timezone.now(), "outcome": StockistSearch.Outcome.IGNORED, "url": row.url[:1000],
                      "confidence": row.confidence},
        )


def search_counts(product_ids):
    """{product id: (shops searched, last search)} in one query, for the Insights page."""
    from django.db.models import Max

    return {
        pk: (n, last)
        for pk, n, last in StockistSearch.objects.filter(product_id__in=list(product_ids))
        .exclude(outcome=StockistSearch.Outcome.ERROR)
        .values_list("product").annotate(n=Count("id"), last=Max("searched_at")).values_list("product", "n", "last")
    }
