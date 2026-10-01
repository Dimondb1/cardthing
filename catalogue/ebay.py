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
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Count, F
from django.utils import timezone

from .matching import AUTO_LINK, SUGGEST, match_key
from .models import Listing, Product

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE = "EBAY_GB"
POSTCODE = "SW1A1AA"
FILTER = "conditions:{NEW},buyingOptions:{FIXED_PRICE},itemLocationCountry:GB,deliveryCountry:GB,priceCurrency:GBP"
MIN_FEEDBACK = 95.0
PAUSE = 0.2


class EbayError(Exception):
    pass


def credentials():
    app = getattr(settings, "RIPRAPTOR_EBAY_APP_ID", "")
    cert = getattr(settings, "RIPRAPTOR_EBAY_CERT_ID", "")
    campaign = getattr(settings, "RIPRAPTOR_EBAY_CAMPAIGN_ID", "")
    if not (app and cert and campaign):
        raise EbayError("Set RIPRAPTOR_EBAY_APP_ID, RIPRAPTOR_EBAY_CERT_ID and RIPRAPTOR_EBAY_CAMPAIGN_ID first.")
    return app, cert, campaign


def http(url, headers, data=None, retries=2):
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            if exc.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            raise EbayError(f"eBay API {exc.code}: {detail}") from exc
        except OSError as exc:
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


def search_url(product):
    params = {"filter": FILTER, "sort": "price", "limit": "10"}
    if product.ean:
        params["gtin"] = product.ean
    else:
        # The game name keeps "Marvel Super Heroes Bundle" away from sticker bundles.
        params["q"] = f"{product.game.name} {product.name}"
    return SEARCH_URL + "?" + urllib.parse.urlencode(params)


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
    """Offers for up to ``limit`` products: those already on eBay first, then the rest.

    ``run`` is the ImportRun to keep posted: its offers found counts products checked
    so far, so admin shows progress while the run is going.
    """
    from .importers import Catalogue, Offer

    request = request or http
    pause = PAUSE if pause is None else pause
    app, cert, campaign = credentials()
    limit = limit if limit is not None else getattr(settings, "RIPRAPTOR_EBAY_DAILY_LIMIT", 4000)
    token = access_token(app, cert, request=request)
    headers = headers_for(token, campaign)
    catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug"))
    existing = {
        row.product_id: row
        for row in Listing.objects.filter(retailer=retailer, is_active=True)
    }
    products = Product.objects.filter(is_active=True).select_related("game")
    known = list(products.filter(pk__in=existing).order_by("ebay_checked_at"))
    # Never tried first, then the longest ago; within that, the products most shops stock.
    fresh = list(
        products.exclude(pk__in=existing)
        .annotate(shops=Count("listings"))
        .order_by(F("ebay_checked_at").asc(nulls_first=True), "-shops", "-ean", "-pk")[: max(0, limit - len(known))]
    )
    offers = []
    checked = []
    for product in (known + fresh)[:limit]:
        answer = request(search_url(product), headers)
        checked.append(product.pk)
        best = None
        for item in answer.get("itemSummaries", []) or []:
            offer = item_offer(item, product, Offer)
            if offer is None:
                continue
            match, value = catalogue.best_match(offer.title, game=product.game.slug)
            needed = SUGGEST if product.ean else AUTO_LINK
            # A duplicate catalogue entry with the same key counts as this product.
            same = catalogue.by_key.get((product.game.slug, match_key(product.name)), ())
            if match and (match[0] == product.pk or match[0] in same) and value >= needed:
                best = offer
                break   # results are cheapest first including postage
        if best is None and product.pk in existing:
            old = existing[product.pk]
            best = Offer(
                title=product.name, url=old.url, price=old.price, ean=product.ean or "",
                availability=Listing.Availability.OUT_OF_STOCK, delivery=old.delivery_cost, product_pk=product.pk,
            )
        if best is not None:
            offers.append(best)
        if run is not None and len(checked) % 100 == 0:
            type(run).objects.filter(pk=run.pk).update(offers_found=len(checked), listings_updated=len(offers))
            logger.info("eBay: %d products checked, %d matched so far", len(checked), len(offers))
        time.sleep(pause)
    Product.objects.filter(pk__in=checked).update(ebay_checked_at=timezone.now())
    logger.info("eBay: %d products checked, %d offers", len(checked), len(offers))
    return offers
