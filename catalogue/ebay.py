"""
Prices from eBay.co.uk through the Browse API, with eBay Partner Network links.

eBay lends an application a token in exchange for its keyset, then answers
item searches. For each product we ask once a day, by barcode where we have
one and otherwise by name, for new, buy-it-now items in Britain delivered
to Britain, cheapest first including postage, and keep the cheapest one
whose title matches the product.

    RIPRAPTOR_EBAY_APP_ID, RIPRAPTOR_EBAY_CERT_ID   production keys from developer.ebay.com
    RIPRAPTOR_EBAY_CAMPAIGN_ID                       the EPN campaign id

Links are eBay's own affiliate links for the campaign, so the retailer
needs no affiliate link format.
"""

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Min, Q
from django.utils import timezone

from . import languages
from .classify import LIVE_RIP, SINGLE_CARD, find_type
from .matching import AUTO_LINK, SUGGEST, contradiction, key_words, match_key, score
from .models import Listing, Product

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
# eBay's one-item lookup (getItem): GET /item/<id> (item_address). It counts against the same daily allowance as a
# search, so it is used only where one listing matters (the Claude judge's evidence, Test eBay now). eBay's
# twenty-at-once lookup (getItems) is for partners eBay chooses and has always refused this application (403,
# 16 Nov 2026), so since 24 Nov 2026 the site no longer asks it: every price is kept fresh by searching again.
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"
LIMITS_URL = "https://api.ebay.com/developer/analytics/v1_beta/rate_limit/?api_context=buy&api_name=Browse"
ITEM_ID = re.compile(r"/itm/(\d+)")
KEEP_BACK = 50     # searches left unused so the hourly stock watcher never hits the wall
SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE = "EBAY_GB"
POSTCODE = "SW1A1AA"
FILTER = "conditions:{NEW},buyingOptions:{FIXED_PRICE},itemLocationCountry:GB,deliveryCountry:GB,priceCurrency:GBP"
# The defaults for a seller's least positive feedback and ratings; the owner sets their own on the eBay health page
# (EbaySettings, seller_rules), and every offer is judged on those.
MIN_FEEDBACK = 95.0
# A seller needs this many ratings as well: 100% from three sales says little (the owner, 3 Nov 2026). A seller eBay
# gives no count for is judged on the percentage alone.
MIN_RATINGS = 50
# The owner's seller rules, kept in the shared cache so a run reads the row once, and cleared when they are saved.
RULES_KEY = "ebay:seller-rules"
RULES_SECONDS = 10 * 60
PAUSE = 0.2
GIVE_UP_AFTER = 5  # failed lookups in a row before a run stops and keeps what it found
# A run stops starting lookups after this and finishes normally with what it found, well inside the background
# reader's 60 minute limit for a marketplace read (catalogue/worker.py) and the hourly import's 55. Cut off by
# either, a run counts as failed and is tried again, spending the day's searches. Tomorrow's run starts with the
# products this one did not reach, as they were looked up longest ago.
RUN_SECONDS = 45 * 60


# An application token lasts two hours; one is kept for TOKEN_LIFE so the judge's lookups do not ask for one each time.
TOKEN_LIFE = 90 * 60
_live = {"token": "", "token_until": 0.0}

# eBay's twenty-at-once item lookup is refused to this application (eBay API 403, 16 Nov 2026: getItems is a limited
# release for chosen partners), so a price is only kept up to date by searching for its product again, from the same
# daily allowance as every other search (about 5,000). Every hourly read searches a slice: what is left above KEEP_BACK
# shared evenly over the hours to eBay's reset, and never more than RIPRAPTOR_EBAY_DAILY_LIMIT spread over the day.
# A read that comes late (the reader busy with shops, the import lock held) takes the share of every hour since the
# last good one, so none of the day's searches goes unspent: on 23 Nov 2026 the reads came four hours apart and
# spent about 1,200 of the 4,800, and 1,663 of the 2,076 eBay items in stock were over a day old (the owner: "Is
# ebay updating enough?"). eBay's one-item lookup (getItem) is no way round the refusal: it counts against this same
# allowance (the buy.browse resource), so every lookup would be a search fewer.
# A product already on eBay is due again after KNOWN_EVERY, or POPULAR_EVERY when visitors look at it. Those due
# after KNOWN_EVERY go first, longest since searched first, so none passes the hours its price counts for; then the
# most looked at. Until 24 Nov 2026 the most looked at always went first, every four hours, and the quieter products
# waited behind them past a day (the owner's eBay health page: 282 eBay items in stock hidden, one unchecked for ten
# days: "fix the ebay falling behind thing"). Products not on eBay get NEW_SHARE of each slice. The owner,
# 16 Nov 2026, chose this over one search run a morning: "Spread out, show 24h".
KNOWN_EVERY = timedelta(hours=20)
POPULAR_EVERY = timedelta(hours=4)
NEW_SHARE = Decimal("0.2")


# What the owner's Test eBay now button last found (self_test), for the eBay health page and its Log to copy.
TEST_KEY = "ebay:last-test"


def last_test():
    """{"at": ISO time, "lines": [...]} from the last self_test, or {}."""
    found = cache.get(TEST_KEY)
    return dict(found) if isinstance(found, dict) else {}


def self_test(request=None):
    """Ask eBay each thing the site asks it, once, and say in plain words how each went, with eBay's own words when
    it refuses: a token, the day's allowance, a search for a product on eBay now, and the one-item lookup for its
    listing. The owner, 16 Nov 2026: "Instead of guessing and hoping why aren't you producing an output log". Spends
    a search and a lookup from the day's allowance and changes no listing. Returns the lines, kept for the eBay
    health page (last_test)."""
    def ask(url, headers, data=None):
        if request is not None:
            return request(url, headers, data)
        return http(url, headers, data=data, retries=0)   # one try: the page is waiting

    def left(count):
        return "an unknown number of" if count is None else f"{count:,}"

    lines = []

    def finish():
        try:
            cache.set(TEST_KEY, {"at": timezone.now().isoformat(), "lines": lines}, None)
        except Exception:  # noqa: BLE001 - the lines are still returned
            logger.warning("eBay: could not keep the test's answers")
        return lines

    try:
        app, cert, campaign = credentials()
    except EbayError as exc:
        lines.append(f"Keys: NOT SET. {exc}")
        return finish()
    lines.append("Keys: all three are set.")
    try:
        token = access_token(app, cert, request=ask)
    except EbayError as exc:
        lines.append(f"Token: REFUSED. eBay's words: {exc}")
        return finish()
    lines.append("Token: eBay issued one, so it accepts the keys.")
    headers = headers_for(token, campaign)
    try:
        searches, _lookups, reset = allowances(headers, request=ask)
        lines.append(f"Allowance: {left(searches)} searches left today, reset {reset or 'not said'}.")
    except EbayError as exc:
        lines.append(f"Allowance: eBay did not say. Its words: {exc}")
    listing = (Listing.objects.filter(retailer__source_type="ebay", is_active=True, product__is_active=True,
                                      availability__in=Listing.BUYABLE)
               .select_related("product").order_by("-last_checked").first())
    if listing is None:
        lines.append("Search: not tried. There is no eBay listing in stock to search for.")
        return finish()
    product = listing.product
    try:
        answer = ask(search_order(product)[0][1], headers)
    except EbayError as exc:
        lines.append(f"Search for {product.name[:80]}: REFUSED. eBay's words: {exc}")
    else:
        found = answer.get("itemSummaries") or []
        lines.append(f"Search for {product.name[:80]}: eBay found {answer.get('total', len(found))} listings.")
    item_id = item_id_of(listing.url)
    if not item_id:
        return finish()
    try:
        item = ask(item_address(item_id), headers)
    except EbayError as exc:
        lines.append(f"One-item lookup: REFUSED. eBay's words: {exc}")
    else:
        price = item.get("price") or {}
        lines.append(f"One-item lookup: eBay showed it, {str(item.get('title') or 'no title')[:80]}, "
                     f"{price.get('value', 'no price')} {price.get('currency', '')}".rstrip() + ".")
    return finish()


def forget_live():
    """Drop the kept token and the last test (tests, and a run that got a new keyset)."""
    _live.update(token="", token_until=0.0)
    cache.delete(TEST_KEY)


def item_address(item_id):
    """eBay's one-item lookup for ``item_id``, URL-encoded as eBay asks (v1|123|0 has bars in it)."""
    return ITEM_URL + urllib.parse.quote(item_id, safe="")


def not_found(error):
    """Whether eBay's error says an item asked about does not exist (its error 11001)."""
    return "11001" in str(error)


class EbayError(Exception):
    pass


def credentials():
    app = getattr(settings, "RIPRAPTOR_EBAY_APP_ID", "")
    cert = getattr(settings, "RIPRAPTOR_EBAY_CERT_ID", "")
    campaign = getattr(settings, "RIPRAPTOR_EBAY_CAMPAIGN_ID", "")
    if not (app and cert and campaign):
        raise EbayError("Set RIPRAPTOR_EBAY_APP_ID, RIPRAPTOR_EBAY_CERT_ID and RIPRAPTOR_EBAY_CAMPAIGN_ID first.")
    return app, cert, campaign


def http(url, headers, data=None, retries=3, wait=5):
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(wait * (attempt + 1))
                continue
            raise EbayError(f"eBay API {exc.code}: {detail}") from exc
        except OSError as exc:
            # A dropped connection or a timeout is usually momentary: wait and ask again.
            if attempt < retries:
                logger.warning("eBay: %s, retrying", exc)
                time.sleep(wait * (attempt + 1))
                continue
            raise EbayError(f"eBay API unreachable: {exc}") from exc
    raise EbayError("eBay API kept throttling us.")


def token_for(request=None, clock=None):
    """An application token, kept for TOKEN_LIFE."""
    clock = clock or time.monotonic
    if _live["token"] and clock() < _live["token_until"]:
        return _live["token"]
    app, cert, _campaign = credentials()
    _live["token"] = access_token(app, cert, request=request)
    _live["token_until"] = clock() + TOKEN_LIFE
    return _live["token"]


def access_token(app, cert, request=None):
    request = request or http
    basic = base64.b64encode(f"{app}:{cert}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode()
    answer = request(
        TOKEN_URL,
        {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    token = answer.get("access_token")
    if not token:
        raise EbayError("eBay did not issue a token. Check the App ID and Cert ID are the production keys.")
    return token


def allowances(headers, request=None):
    """(searches left today, bulk lookups left today, reset time) from eBay's own counter, and remembered for the
    Crawl health page (WorkerState.ebay_usage): the owner, 6 Nov 2026, "in the past we've always exhausted it". The
    bulk lookups are eBay's own count of a lookup the site no longer asks, kept only as eBay said it."""
    from .models import WorkerState

    request = request or http
    answer = request(LIMITS_URL, headers)
    left = {"buy.browse": None, "buy.browse.item.bulk": None}
    reset = ""
    for api in answer.get("rateLimits", []) or []:
        for resource in api.get("resources", []) or []:
            for rate in resource.get("rates", []) or []:
                if resource.get("name") in left:
                    left[resource["name"]] = rate.get("remaining")
                    reset = rate.get("reset", reset)
    try:
        WorkerState.objects.update_or_create(pk=1, defaults={"ebay_usage": {
            "searches": left["buy.browse"], "lookups": left["buy.browse.item.bulk"], "reset": reset or "",
            "asked_at": timezone.now().isoformat(),
        }})
    except Exception:  # noqa: BLE001 - the page's figures are a courtesy; a busy database must not stop a run
        logger.warning("eBay: could not note the allowances for the Crawl health page")
    return left["buy.browse"], left["buy.browse.item.bulk"], reset


def seller_rules(fresh=False):
    """(least positive feedback %, least number of ratings) a seller needs for an offer to count (item_offer): the
    owner's (EbaySettings, set on the eBay health page), from the shared cache. ``fresh`` reads the row."""
    from django.db import DatabaseError

    kept = None if fresh else cache.get(RULES_KEY)
    if not isinstance(kept, tuple) or len(kept) != 2:
        from .models import EbaySettings

        try:
            row = EbaySettings.current()
        except DatabaseError:
            # Never the reason eBay's checks stop: a database too busy to answer, or not yet holding the table, gives
            # the defaults for this offer, kept nowhere, and the next one asks again.
            logger.warning("eBay: could not read the seller rules; the defaults apply")
            return Decimal(str(MIN_FEEDBACK)), MIN_RATINGS
        kept = (Decimal(row.min_feedback), int(row.min_ratings))
        cache.set(RULES_KEY, kept, RULES_SECONDS)
    return kept


def item_id_of(url):
    """eBay's API id for a listing link: the number after /itm/, as a plain item."""
    match = ITEM_ID.search(url or "")
    return f"v1|{match.group(1)}|0" if match else ""


# Words shops add that eBay sellers rarely repeat; dropped from the fallback search.
QUERY_NOISE = re.compile(
    r"\((?:[^)]*)\)|\bofficial\b|\btrading cards?\b|\bcard game\b|\btcg\b|\bsealed\b|\benglish\b|[:|,!]", re.I
)
# Games whose name sellers put in the title, and which keeps a search away from other things of the same name.
GAME_IN_QUERY = {"pokemon": "Pokemon", "magic-the-gathering": "MTG", "one-piece": "One Piece", "yu-gi-oh": "Yu-Gi-Oh",
                 "lorcana": "Lorcana", "star-wars-unlimited": "Star Wars Unlimited", "flesh-and-blood": "Flesh and Blood",
                 "digimon": "Digimon", "dragon-ball": "Dragon Ball", "riftbound": "Riftbound",
                 "cardfight-vanguard": "Cardfight Vanguard", "weiss-schwarz": "Weiss Schwarz",
                 "union-arena": "Union Arena"}


def search_queries(product):
    """Keyword searches to try in turn, the next only when the one before found nothing."""
    name = " ".join(product.name.split())
    game = GAME_IN_QUERY.get(product.game.slug, "")
    lead = f"{game} " if game and game.lower().split()[0] not in name.lower() else ""
    first = f"{lead}{name}"
    plain = " ".join(QUERY_NOISE.sub(" ", name).split())
    second = f"{lead}{plain}" if plain and plain != name else ""
    return [q for q in (first, second, plain if lead and plain else "") if q]


def search_url(product, query=None):
    params = {"filter": FILTER, "sort": "price", "limit": "50"}
    if product.ean and query is None:
        params["gtin"] = product.ean
    else:
        params["q"] = (query or search_queries(product)[0])[:100]
    return SEARCH_URL + "?" + urllib.parse.urlencode(params)


# A listing that is several of the thing, part of it, or something sold alongside it.
NOT_THE_THING = re.compile(
    r"\b(?:[2-9]|\d{2,})\s*x\b|\bx\s*(?:[2-9]|\d{2,})\b|\bjob ?lot\b|\blot of\b|\bbundle of\b|\bset of [2-9]\b|\bcase\b|"
    r"\bcodes?\b|\bcode cards?\b|\bp?tcgo\b|\btcg ?live\b|\bonline\b|\bdigital\b|\bfun ?packs?\b|"
    r"\bsampl(?:e|ing)\b|\bsample packs?\b|\bpromo ?packs?\b|\bmini ?packs?\b|\b3[ -]?cards?\b|\bthree[ -]?cards?\b|"
    r"\bsleeves?\b|\bsingles\b|\bsingle cards?\b|\bpromo\b|\bplay ?mats?\b|\bbinder\b|\bdeck ?box\b|\bempty\b|\bproxy\b|\bdamaged\b|"
    r"\bstickers?\b|\bonly\b|\btokens?\b|\bbasic lands?\b|\bfrom\b|\bcontents\b|\bopened\b|\bcustom\b|\breplica\b|\bart cards?\b|\bdice\b|"
    # Toys with a card game's name: figure advent calendars and figure sets from toy makers. "Pokémon 2024 Holiday
    # Advent Calendar Jazwares 24 Gifts 58 Pieces" was the Holiday Calendar 2024 card box at £31.90 (the owner,
    # 16 Nov 2026: "These aren't the same thing").
    r"\bjazwares\b|\bwicked cool toys\b|\bfunko\b|\blego\b|\bmega (?:construx|bloks)\b|\bnanoblock\b|\bplush\b|"
    r"\bfigures?\b(?! box)|\b\d+\s*pieces\b",
    re.I,
)
# Nothing genuine sells for under this share of the cheapest shop's price: below it the listing is
# stickers, a part, a single pack of a box, or another thing with the same words. Shops' last known
# prices count even when out of stock, and marketplaces' own prices never set the bar.
PRICE_FLOOR = Decimal("0.4")
MARKETPLACES = ("ebay", "amazon")
# A name with fewer identifying words than this ("151 Booster Pack") only matches the strict way.
MIN_LOOSE_WORDS = 2

def shop_prices():
    """{product pk: the cheapest delivered price any shop last showed}, in stock or not.

    Only prices the other shops agree with count. A doubtful price is used only for a product with no
    OK one, and an excluded price never: an impossible shop price must not set the eBay floor.
    """
    rows = (
        Listing.objects.filter(is_active=True, product__is_active=True)
        .exclude(retailer__source_type__in=MARKETPLACES)
        .order_by()
        .values("product")
        .annotate(
            ok=Min("delivered_price", filter=Q(sanity=Listing.Sanity.OK)),
            doubtful=Min("delivered_price", filter=Q(sanity=Listing.Sanity.DOUBTFUL)),
        )
        .values_list("product", "ok", "doubtful")
    )
    return {pk: ok if ok is not None else doubtful for pk, ok, doubtful in rows if ok is not None or doubtful is not None}


def too_cheap_listings():
    """eBay listings on sale that the current rules refuse: under the price floor, or a title that is
    a multi-buy, a code, a sampling pack or another language. Matched before the rule existed."""
    shops = shop_prices()
    live = Listing.objects.filter(retailer__source_type="ebay", is_active=True).exclude(
        availability=Listing.Availability.OUT_OF_STOCK
    )
    return [
        listing for listing in live.select_related("product")
        if (listing.product_id in shops and listing.delivered_price < shops[listing.product_id] * PRICE_FLOOR)
        or (listing.title and junk(listing.product, listing.title))
    ]


def wrong_listings():
    """eBay listings, in stock or not, whose title the current rules say is not the product at all (junk): a
    single card, a part, a multi-buy, a code, another language. Such a row helps nobody on the product's page,
    and its price was never the product's."""
    rows = Listing.objects.filter(retailer__source_type="ebay").exclude(title="").select_related("product__game")
    return [listing for listing in rows if junk(listing.product, listing.title)]


PACK_COUNT = re.compile(r"\b(?:[2-9]|\d{2,})\s*(?:booster\s*)?packs?\b", re.I)


class Specifics:
    """For one game, which of our products a title could name more precisely than the one searched for."""

    def __init__(self, rows):
        self.words = {}
        self.index = {}
        for pk, name in rows:
            kw = frozenset(key_words(name))
            self.words[pk] = (name, kw)
            for word in kw:
                self.index.setdefault(word, set()).add(pk)

    def more_specific(self, product, title):
        """True when another product's name holds all of ours and more, and the title holds all of it."""
        mine = self.words.get(product.pk, (product.name, frozenset(key_words(product.name))))[1]
        if not mine:
            return False
        candidates = set.intersection(*(self.index.get(word, set()) for word in mine))
        for pk in candidates - {product.pk}:
            name, theirs = self.words[pk]
            if len(theirs) > len(mine) and score(name, title) == 100:
                return True
        return False


def languages_in(text, loose=True):
    """The language a title is in, as a set of at most one code (catalogue/languages.py). Read loosely, as
    sellers write: an upper-case "JP" counts."""
    code = languages.language_of(text or "", loose=loose)
    return {code} if code else set()


def junk(product, title):
    """Is the listing plainly not one sealed unit of this product, whatever its name says?

    A multi-buy, an online code, a part, a single card (classify.SINGLE_CARD), a live rip (classify.LIVE_RIP), another
    language, or anything
    matching.contradiction finds (another game or set code, a Pokémon Center edition, a damaged or opened box,
    another unit of sale).
    Checked on every result, including those that name the product word for word, and on known listings.
    """
    game = product.game.slug if product.game_id else ""
    ours = getattr(product, "language", None)
    ours = languages.language_of(product.name, game) if ours is None else ours
    theirs = languages.language_of(title or "", game, loose=True)
    if not languages.same(ours, theirs) or languages.mixed(title or "", game):
        return True
    if NOT_THE_THING.search(title) and not NOT_THE_THING.search(product.name):
        return True
    if SINGLE_CARD.search(title) and not SINGLE_CARD.search(product.name):
        return True
    if LIVE_RIP.search(title or ""):   # a live rip or break: packs opened on a stream, not the sealed product
        return True
    if product.product_type == "booster_pack" and PACK_COUNT.search(title):
        return True
    # Another game, set code, edition, condition or unit of sale, read as every shop's title is.
    return bool(contradiction(product.name, title, game, ours))


def is_this_product(product, title, specifics):
    """Does an eBay title name exactly this product?

    eBay sellers pad titles with the game's name and selling words, so the
    rule runs the other way round from shop matching: every word of our
    name must be in the title, the title must be the same kind of product,
    in the same language, one of it, and not a more specific product of ours.
    """
    if product.product_type == "booster_box":
        # A booster display is a booster box.
        title = re.sub(r"\bbooster display(?: box)?\b", "booster box", title, flags=re.I)
        title = re.sub(r"\bdisplay(?: box)?\b", "booster box", title, flags=re.I)
    if len(key_words(product.name)) < MIN_LOOSE_WORDS:
        return False
    if score(product.name, title) < 100:
        return False
    if junk(product, title):
        return False
    kind = find_type(title)
    if kind and kind != product.product_type:
        return False
    return not specifics.more_specific(product, title)


def headers_for(token, campaign):
    return {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE,
        "X-EBAY-C-ENDUSERCTX": f"affiliateCampaignId={campaign},contextualLocation=country%3DGB%2Czip%3D{POSTCODE}",
        "Accept-Language": "en-GB",
    }


def money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def item_offer(item, product, Offer):
    """An Offer for one search result, or None when it is not a clean UK buy-it-now price."""
    title = item.get("title", "")
    price = item.get("price", {}) or {}
    if price.get("currency", "GBP") != "GBP":
        return None
    amount = money(price.get("value"))
    url = item.get("itemAffiliateWebUrl") or item.get("itemWebUrl", "")
    if amount is None or not title or not url:
        return None
    seller = item.get("seller", {}) or {}
    least_feedback, least_ratings = seller_rules()
    feedback = seller.get("feedbackPercentage")
    if feedback is not None and money(feedback) is not None and money(feedback) < least_feedback:
        return None
    ratings = money(seller.get("feedbackScore"))
    if ratings is not None and ratings < least_ratings:
        return None
    options = item.get("shippingOptions") or []
    cost = money((options[0].get("shippingCost", {}) or {}).get("value")) if options else None
    if cost is None:
        return None   # no quote to Britain means we cannot say what it costs delivered
    return Offer(
        title=title,
        url=url,
        price=amount,
        ean=product.ean or "",
        availability=Listing.Availability.IN_STOCK,
        delivery=cost,
        image=(item.get("image", {}) or {}).get("imageUrl", ""),
        product_pk=product.pk,
        description=item_description(item),
        seller_note=seller_note(item),
        seller=seller_standing(seller),
    )


def seller_standing(seller):
    """(name, positive feedback % to one place, number of ratings) of an eBay seller, as eBay's answer gives them; a
    part eBay left out is empty. Shown on the row (offer_row.html), never made up."""
    feedback = money(seller.get("feedbackPercentage"))
    ratings = money(seller.get("feedbackScore"))
    return (
        str(seller.get("username") or "")[:80],
        feedback.quantize(Decimal("0.1")) if feedback is not None and 0 <= feedback <= 100 else None,
        int(ratings) if ratings is not None and ratings >= 0 else None,
    )


def seller_note(item):
    """What eBay says of the item or its seller, the strongest first: its Authenticity Guarantee, a Top Rated Plus
    item (a 30 day return, posted in a day, a top rated seller), a seller registered as a business. Search results and
    item lookups both carry them (qualifiedPrograms, topRatedBuyingExperience, seller.sellerAccountType on eBay UK)."""
    from .models import Listing

    if "AUTHENTICITY_GUARANTEE" in (item.get("qualifiedPrograms") or []):
        return Listing.SellerNote.AUTHENTICITY
    if item.get("topRatedBuyingExperience") is True:
        return Listing.SellerNote.TOP_RATED
    if ((item.get("seller") or {}).get("sellerAccountType") or "").upper() == "BUSINESS":
        return Listing.SellerNote.BUSINESS
    return Listing.SellerNote.NONE


def item_description(item):
    """The seller's description as plain text, from what eBay already sent (the short description, else the
    description the item lookup gives), or None when eBay sent neither, which leaves the listing's."""
    from .importers import clean_description

    if "shortDescription" not in item and "description" not in item:
        return None
    return clean_description(item.get("shortDescription") or item.get("description"))


def verdict(product, item, Offer, catalogue, specifics, shops):
    """(offer, None) when one search result is this product at a believable price, else (offer, why not)."""
    offer = item_offer(item, product, Offer)
    if offer is None:
        return None, "not a clean UK buy-it-now price (currency, seller feedback or ratings, or no UK postage)"
    if product.pk in shops and offer.price + offer.delivery < shops[product.pk] * PRICE_FLOOR:
        return offer, f"too cheap to be the sealed product (under £{shops[product.pk] * PRICE_FLOOR:.2f})"
    if junk(product, offer.title):
        return offer, "a multi-buy, an online code, a part, another language or plainly another product"
    match, value = catalogue.best_match(offer.title, game=product.game.slug)
    needed = SUGGEST if product.ean else AUTO_LINK
    # A duplicate catalogue entry with the same key counts as this product.
    same = catalogue.by_key.get((product.game.slug, match_key(product.name, product.game.slug)), ())
    named = match and (match[0] == product.pk or match[0] in same) and value >= needed
    if named or is_this_product(product, offer.title, specifics):
        return offer, None
    return offer, "a different product, a part or a multi-buy"


def hourly_searches(searches_left, reset, now=None, since=None):
    """Searches one hourly read may spend: what is left above KEEP_BACK shared evenly over the hours to eBay's reset
    (each hour's read gets its share, the last one before the reset what remains), at most RIPRAPTOR_EBAY_DAILY_LIMIT
    over the day. A read that comes more than an hour after the last good one (``since``) takes the share of each
    hour since, so a late read spends what the reads it missed would have. Unknown, it assumes a whole day is left."""
    now = now or timezone.now()
    hours = 24
    try:
        at = datetime.fromisoformat(str(reset).replace("Z", "+00:00")) if reset else None
    except ValueError:
        at = None
    if at is not None and at.tzinfo is not None and at > now:
        hours = max(1, min(24, -(-int((at - now).total_seconds()) // 3600)))
    missed = 1
    if since is not None:
        missed = max(1, min(24, round((now - since).total_seconds() / 3600)))
    daily = getattr(settings, "RIPRAPTOR_EBAY_DAILY_LIMIT", 4800)
    cap = -(-daily // 24) * missed
    if searches_left is None:
        return cap
    spare = searches_left - KEEP_BACK
    return max(0, min(cap, spare * missed // hours, spare))


def due_known(products, scores, now):
    """The products already on eBay that are due another search, in the order to search them. First every one not
    searched for KNOWN_EVERY, longest since first, so no eBay price passes the hours it counts for while visitors'
    favourites are searched again; then those visitors look at (``scores``) after POPULAR_EVERY, most looked at
    first."""
    epoch = datetime(2000, 1, 1, tzinfo=dt_timezone.utc)
    due = []
    for product in products:
        score = scores.get(product.pk, 0)
        at = product.ebay_checked_at
        if at is None or now - at >= KNOWN_EVERY:
            due.append((0, 0, at or epoch, product.pk, product))
        elif score > 0 and now - at >= POPULAR_EVERY:
            due.append((1, -score, at, product.pk, product))
    due.sort(key=lambda row: row[:4])
    return [row[4] for row in due]


def search_order(product):
    """[(tag, address)] of the searches for a product in the order to try them: the one that found it last time
    (Product.ebay_query) first, then the barcode, then the words."""
    tried = ([("gtin", search_url(product))] if product.ean else []) + [(q, search_url(product, q))
                                                                        for q in search_queries(product)]
    last = getattr(product, "ebay_query", "")
    return sorted(tried, key=lambda row: row[0] != last) if last else tried


def ebay_offers(retailer, limit=None, request=None, pause=None, run=None, clock=None):
    """All the offers of one run, as a list. See iter_ebay_offers."""
    return list(iter_ebay_offers(retailer, limit=limit, request=request, pause=pause, run=run, clock=clock))


def iter_ebay_offers(retailer, limit=None, request=None, pause=None, run=None, clock=None):
    """Offers for every product already on eBay, then up to ``limit`` new lookups.

    Credentials and allowances are checked straight away; the offers then
    come one at a time as they are found, so the importer saves each as it
    arrives and an interrupted run keeps everything found before it stopped.

    Known listings are refreshed by searching for their products again (due_known), and NEW_SHARE of each hour's
    searches goes on products not yet on eBay.
    When an allowance runs out, or RUN_SECONDS have passed, the run stops and keeps what it found.
    ``run`` is the ImportRun to keep posted so admin shows progress. ``clock`` gives seconds (for tests).
    """
    from .finder import interest_first, interest_scores
    from .importers import Catalogue, Offer

    request = request or http
    pause = PAUSE if pause is None else pause
    clock = clock or time.monotonic
    began = clock()
    app, cert, campaign = credentials()
    token = access_token(app, cert, request=request)
    headers = headers_for(token, campaign)
    searches_left, _lookups, reset = allowances(headers, request=request)
    hourly = limit is None
    if hourly:
        # An hourly read: its share of what is left today. None left is not a failure: the next hour tries again.
        limit = hourly_searches(searches_left, reset, since=getattr(retailer, "last_ok_at", None))
        logger.info("eBay: %d searches for this hour, %s left today", limit,
                    "an unknown number" if searches_left is None else f"{searches_left:,}")
    else:
        if searches_left is not None and searches_left <= KEEP_BACK:
            raise EbayError(f"eBay's search allowance for today is used up. It resets at {reset or 'midnight Pacific time'}.")
        if searches_left is not None:
            limit = min(limit, searches_left - KEEP_BACK)

    def offers_as_found():
        now = timezone.now()
        scores = interest_scores(now, visitors_only=True)
        catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug"))
        # An eBay listing on a product no longer on the site is never searched for again (only the site's products
        # are), so it would stay in stock, never checked, for ever: it is taken out of stock. On 24 Nov 2026 one had
        # gone ten days and kept the eBay health page "falling behind".
        Listing.objects.filter(retailer=retailer, is_active=True, product__is_active=False).exclude(
            availability=Listing.Availability.OUT_OF_STOCK).update(availability=Listing.Availability.OUT_OF_STOCK)
        existing = {row.product_id: row for row in Listing.objects.filter(retailer=retailer, is_active=True)}
        products = Product.objects.filter(is_active=True).select_related("game")
        offers = []
        checked = []
        found_by = {}   # product id -> the search that found it, when not the one tried first last time

        marked = set()

        def mark_checked():
            new = [pk for pk in checked if pk not in marked]
            if new:
                Product.objects.filter(pk__in=new).update(ebay_checked_at=timezone.now())
                marked.update(new)
            for pk in [pk for pk in found_by if pk in marked]:
                Product.objects.filter(pk=pk).update(ebay_query=found_by.pop(pk))

        def post():
            if run is not None:
                type(run).objects.filter(pk=run.pk).update(offers_found=len(checked), listings_updated=len(offers))
            logger.info("eBay: %d products checked, %d matched so far", len(checked), len(offers))

        def out_of_stock(product):
            old = existing[product.pk]
            return Offer(
                title=product.name, url=old.url, price=old.price, ean=product.ean or "",
                availability=Listing.Availability.OUT_OF_STOCK, delivery=old.delivery_cost, product_pk=product.pk,
            )

        shops = shop_prices()

        def too_cheap(product, offer):
            return product.pk in shops and offer.price + offer.delivery < shops[product.pk] * PRICE_FLOOR

        # Known listings due again (due_known), searched for below: the oldest first, then the most looked at.
        known = due_known(products.filter(pk__in=existing), scores, now)
        # One eBay listing is one product: an item already another product's is never taken for a second
        # (one Future Flash SV4M box was on both our Scarlet ex SV1S and Violet ex SV1V boxes).
        taken = {}
        for pk, row in existing.items():
            item_id = item_id_of(row.url)
            if item_id:
                taken.setdefault(item_id, pk)
        gone = list(known)

        def out_of_time():
            return clock() - began >= RUN_SECONDS

        # New lookups: the products visitors want most first (unless searched in the last three days),
        # then never tried or tried longest ago, then the products most shops stock. An hourly read keeps NEW_SHARE of
        # its searches for them, about three a product, so the products already on eBay never take every search.
        new_searches = int(limit * NEW_SHARE) if hourly else 0
        fresh = interest_first(
            products.exclude(pk__in=existing)
            .annotate(shops=Count("listings"))
            .order_by(F("ebay_checked_at").asc(nulls_first=True), "-shops", "-ean", "-pk"),
            max(limit - len(gone), new_searches // 3),
            "ebay_checked_at",
        )
        known_searches = limit - new_searches if fresh else limit
        failures = 0
        specifics = {}

        def specifics_for(game_slug):
            if game_slug not in specifics:
                specifics[game_slug] = Specifics(
                    Product.objects.filter(is_active=True, game__slug=game_slug).values_list("pk", "name")
                )
            return specifics[game_slug]

        searches = 0
        for product in gone + fresh:
            if searches >= limit:
                break
            if product.pk in existing and searches >= known_searches:
                continue   # the rest of this hour's searches are kept for products not yet on eBay
            if out_of_time():
                logger.warning("eBay: %d minutes used after %d products, stopping and keeping what was found; "
                               "the rest go first next time", RUN_SECONDS // 60, len(checked))
                break
            try:
                answer = {}
                for tag, url in search_order(product):
                    answer = request(url, headers)
                    searches += 1
                    if answer.get("itemSummaries"):
                        if tag != product.ebay_query:
                            found_by[product.pk] = tag[:120]
                        break
                    if searches >= limit:
                        break
            except EbayError as exc:
                if "429" in str(exc):
                    logger.warning("eBay: search allowance used up after %d products, keeping what was found", len(checked))
                    break
                # Skip this product (it stays first in line tomorrow) unless eBay keeps failing.
                failures += 1
                logger.warning("eBay: lookup for %s failed: %s", product, exc)
                if failures >= GIVE_UP_AFTER:
                    logger.warning("eBay: %d failures in a row, stopping after %d products and keeping what was found", failures, len(checked))
                    break
                continue
            failures = 0
            checked.append(product.pk)
            # Of every result that really is this product, keep the cheapest delivered.
            best = None
            for item in answer.get("itemSummaries", []) or []:
                offer, reason = verdict(product, item, Offer, catalogue, specifics_for(product.game.slug), shops)
                if reason is None and taken.get(item_id_of(offer.url) or None, product.pk) != product.pk:
                    continue   # another product's listing already
                if reason is None and (best is None or offer.price + offer.delivery < best.price + best.delivery):
                    best = offer
            if best is not None and best.availability != Listing.Availability.OUT_OF_STOCK and item_id_of(best.url):
                taken[item_id_of(best.url)] = product.pk
            if best is None and product.pk in existing:
                best = out_of_stock(product)
            if best is not None:
                offers.append(best)
                yield offers[-1]
            if len(checked) % 100 == 0:
                mark_checked()
                post()
            time.sleep(pause)
        mark_checked()
        post()

    return offers_as_found()
