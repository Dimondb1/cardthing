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
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Count, F, Min
from django.utils import timezone

from .classify import LANGUAGE, find_type
from .matching import AUTO_LINK, SUGGEST, key_words, match_key, score
from .models import Listing, Product

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEMS_URL = "https://api.ebay.com/buy/browse/v1/item/get_items"
LIMITS_URL = "https://api.ebay.com/developer/analytics/v1_beta/rate_limit/?api_context=buy&api_name=Browse"
ITEM_ID = re.compile(r"/itm/(\d+)")
BULK = 20          # item ids per bulk lookup
KEEP_BACK = 50     # searches left unused so the hourly stock watcher never hits the wall
SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE = "EBAY_GB"
POSTCODE = "SW1A1AA"
FILTER = "conditions:{NEW},buyingOptions:{FIXED_PRICE},itemLocationCountry:GB,deliveryCountry:GB,priceCurrency:GBP"
MIN_FEEDBACK = 95.0
PAUSE = 0.2
GIVE_UP_AFTER = 5  # failed lookups in a row before a run stops and keeps what it found


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
    """(searches left today, bulk lookups left today, reset time) from eBay's own counter."""
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
    return left["buy.browse"], left["buy.browse.item.bulk"], reset


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
                 "digimon": "Digimon", "dragon-ball": "Dragon Ball", "riftbound": "Riftbound"}


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
    r"\b[2-9]\s*x\b|\bx\s*[2-9]\b|\bjob ?lot\b|\blot of\b|\bbundle of\b|\bset of [2-9]\b|\bcase\b|\bcode cards?\b|"
    r"\bsleeves?\b|\bsingles?\b|\bpromo\b|\bplay ?mats?\b|\bbinder\b|\bdeck ?box\b|\bempty\b|\bproxy\b|\bdamaged\b|"
    r"\bstickers?\b|\bonly\b|\btokens?\b|\bbasic lands?\b|\bfrom\b|\bcontents\b|\bopened\b|\bcustom\b|\breplica\b|\bart cards?\b|\bdice\b",
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
    """{product pk: the cheapest delivered price any shop last showed}, in stock or not."""
    return dict(
        Listing.objects.filter(is_active=True, product__is_active=True)
        .exclude(retailer__source_type__in=MARKETPLACES)
        .order_by()
        .values("product")
        .annotate(cheapest=Min("delivered_price"))
        .values_list("product", "cheapest")
    )


def too_cheap_listings():
    """eBay listings on sale for less than the floor: matched before the floor applied, or since undercut."""
    shops = shop_prices()
    live = Listing.objects.filter(retailer__source_type="ebay", is_active=True).exclude(
        availability=Listing.Availability.OUT_OF_STOCK
    )
    return [
        listing for listing in live.select_related("product")
        if listing.product_id in shops and listing.delivered_price < shops[listing.product_id] * PRICE_FLOOR
    ]


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
    if {m.lower() for m in LANGUAGE.findall(title)} != {m.lower() for m in LANGUAGE.findall(product.name)}:
        return False
    if NOT_THE_THING.search(title) and not NOT_THE_THING.search(product.name):
        return False
    if product.product_type == "booster_pack" and PACK_COUNT.search(title):
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
    feedback = (item.get("seller", {}) or {}).get("feedbackPercentage")
    if feedback is not None and money(feedback) is not None and money(feedback) < Decimal(MIN_FEEDBACK):
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
    )


def ebay_offers(retailer, limit=None, request=None, pause=None, run=None):
    """All the offers of one run, as a list. See iter_ebay_offers."""
    return list(iter_ebay_offers(retailer, limit=limit, request=request, pause=pause, run=run))


def iter_ebay_offers(retailer, limit=None, request=None, pause=None, run=None):
    """Offers for every product already on eBay, then up to ``limit`` new lookups.

    Credentials and allowances are checked straight away; the offers then
    come one at a time as they are found, so the importer saves each as it
    arrives and an interrupted run keeps everything found before it stopped.

    Known listings are refreshed through eBay's bulk item lookup, which has
    its own daily allowance, so the search allowance goes on new products.
    When an allowance runs out the run stops and keeps what it found.
    ``run`` is the ImportRun to keep posted so admin shows progress.
    """
    from .importers import Catalogue, Offer

    request = request or http
    pause = PAUSE if pause is None else pause
    app, cert, campaign = credentials()
    limit = limit if limit is not None else getattr(settings, "RIPRAPTOR_EBAY_DAILY_LIMIT", 4000)
    token = access_token(app, cert, request=request)
    headers = headers_for(token, campaign)
    searches_left, bulk_left, reset = allowances(headers, request=request)
    if searches_left is not None and searches_left <= KEEP_BACK:
        raise EbayError(f"eBay's search allowance for today is used up. It resets at {reset or 'midnight Pacific time'}.")
    if searches_left is not None:
        limit = min(limit, searches_left - KEEP_BACK)

    def offers_as_found():
        nonlocal bulk_left
        catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug"))
        existing = {row.product_id: row for row in Listing.objects.filter(retailer=retailer, is_active=True)}
        products = Product.objects.filter(is_active=True).select_related("game")
        offers = []
        checked = []
        gone = []

        marked = set()

        def mark_checked():
            new = [pk for pk in checked if pk not in marked]
            if new:
                Product.objects.filter(pk__in=new).update(ebay_checked_at=timezone.now())
                marked.update(new)

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

        # Known listings, twenty at a time through the bulk lookup.
        known = list(products.filter(pk__in=existing).order_by("ebay_checked_at"))
        by_item = {}
        for product in known:
            item_id = item_id_of(existing[product.pk].url)
            if item_id:
                by_item[item_id] = product
            else:
                gone.append(product)
        ids = list(by_item)
        batches = [ids[start:start + BULK] for start in range(0, len(ids), BULK)]
        done = 0
        if ids:
            logger.info("eBay: refreshing %d known listings", len(ids))
        while done < len(batches):
            batch = batches[done]
            if bulk_left is not None and bulk_left <= 0:
                break
            try:
                answer = request(ITEMS_URL + "?item_ids=" + ",".join(batch), headers)
            except EbayError as exc:
                if bulk_left is not None:
                    bulk_left -= 1
                if "11001" in str(exc) or "404" in str(exc):
                    # eBay refuses the whole batch when one listing has ended. Ask for each on its own,
                    # so one ended listing does not stop the rest being refreshed.
                    if len(batch) > 1:
                        batches[done:done + 1] = [[item_id] for item_id in batch]
                    else:
                        gone.append(by_item[batch[0]])
                        done += 1
                    continue
                if "429" in str(exc):
                    logger.warning("eBay: bulk allowance used up, %s", exc)
                else:
                    logger.warning("eBay: bulk lookup failed, keeping what was found: %s", exc)
                break
            done += 1
            if bulk_left is not None:
                bulk_left -= 1
            found = set()
            for item in answer.get("items", []) or []:
                product = by_item.get(item.get("itemId"))
                if product is None:
                    continue
                offer = item_offer(item, product, Offer)
                if offer is not None and too_cheap(product, offer):
                    continue   # left unfound, so it is searched again and a genuine listing can replace it
                found.add(item["itemId"])
                offers.append(offer if offer is not None else out_of_stock(product))
                yield offers[-1]
                checked.append(product.pk)
            for item_id in batch:
                if item_id not in found:
                    gone.append(by_item[item_id])
            time.sleep(pause)
        # Batches never reached (allowance or an error) are searched again instead, so they are not left stale.
        for batch in batches[done:]:
            gone.extend(by_item[item_id] for item_id in batch)
        # Listings the bulk lookup no longer knows (ended) are searched again below, first.
        if ids:
            mark_checked()
            logger.info("eBay: %d known listings still live, %d ended or not reached, now searching", len(checked), len(gone))

        # New lookups, the products most shops stock first.
        fresh = list(
            products.exclude(pk__in=existing)
            .annotate(shops=Count("listings"))
            .order_by(F("ebay_checked_at").asc(nulls_first=True), "-shops", "-ean", "-pk")[: max(0, limit - len(gone))]
        )
        failures = 0
        specifics = {}

        def specifics_for(game_slug):
            if game_slug not in specifics:
                specifics[game_slug] = Specifics(
                    Product.objects.filter(is_active=True, game__slug=game_slug).values_list("pk", "name")
                )
            return specifics[game_slug]

        searches = 0
        for product in (gone + fresh)[:limit]:
            if searches >= limit:
                break
            try:
                urls = ([search_url(product)] if product.ean else []) + [search_url(product, q) for q in search_queries(product)]
                answer = {}
                for url in urls:
                    answer = request(url, headers)
                    searches += 1
                    if answer.get("itemSummaries") or searches >= limit:
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
                offer = item_offer(item, product, Offer)
                if offer is None:
                    continue
                if too_cheap(product, offer):
                    continue
                match, value = catalogue.best_match(offer.title, game=product.game.slug)
                needed = SUGGEST if product.ean else AUTO_LINK
                # A duplicate catalogue entry with the same key counts as this product.
                same = catalogue.by_key.get((product.game.slug, match_key(product.name)), ())
                named = match and (match[0] == product.pk or match[0] in same) and value >= needed
                if named or is_this_product(product, offer.title, specifics_for(product.game.slug)):
                    if best is None or offer.price + offer.delivery < best.price + best.delivery:
                        best = offer
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
