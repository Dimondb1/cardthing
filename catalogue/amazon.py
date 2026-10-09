"""
Prices from Amazon.co.uk through the Product Advertising API.

Amazon publishes no feed. Its API answers signed requests (AWS signature
version 4) at about one a second, so a run reads every product we have
already found on Amazon in batches of ten, then looks up new products:
by barcode where we have one, otherwise by name, keeping a result only
when it matches the product we asked about.

    RIPRAPTOR_AMAZON_ACCESS_KEY, RIPRAPTOR_AMAZON_SECRET_KEY
    RIPRAPTOR_AMAZON_PARTNER_TAG   the Associates tag, for example ripraptor-21

Links keep Amazon's own tracking address, so no affiliate link format is
needed on the retailer.
"""

import datetime
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .matching import AUTO_LINK
from .models import Listing, Product

logger = logging.getLogger(__name__)

HOST = "webservices.amazon.co.uk"
REGION = "eu-west-1"
SERVICE = "ProductAdvertisingAPI"
MARKETPLACE = "www.amazon.co.uk"
BATCH = 10
PAUSE = 1.1  # seconds between calls: new accounts get one a second
RESOURCES = [
    "ItemInfo.Title",
    "ItemInfo.ExternalIds",
    "Images.Primary.Large",
    "Offers.Listings.Price",
    "Offers.Listings.Availability.Type",
    "Offers.Listings.Condition",
    "Offers.Listings.DeliveryInfo.IsFreeShippingEligible",
]


class AmazonError(Exception):
    pass


def credentials():
    key = getattr(settings, "RIPRAPTOR_AMAZON_ACCESS_KEY", "")
    secret = getattr(settings, "RIPRAPTOR_AMAZON_SECRET_KEY", "")
    tag = getattr(settings, "RIPRAPTOR_AMAZON_PARTNER_TAG", "")
    if not (key and secret and tag):
        raise AmazonError(
            "Set RIPRAPTOR_AMAZON_ACCESS_KEY, RIPRAPTOR_AMAZON_SECRET_KEY and "
            "RIPRAPTOR_AMAZON_PARTNER_TAG first."
        )
    return key, secret, tag


# Signing ---------------------------------------------------------------------

def _sign(key, message):
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def signed_headers(operation, body, key, secret, now=None):
    """Headers for one API call, signed with AWS signature version 4."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    path = f"/paapi5/{operation.lower()}"
    target = f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{operation}"
    headers = {
        "content-encoding": "amz-1.0",
        "content-type": "application/json; charset=utf-8",
        "host": HOST,
        "x-amz-date": stamp,
        "x-amz-target": target,
    }
    signed = ";".join(headers)
    canonical = "\n".join(
        ["POST", path, "", *[f"{k}:{v}" for k, v in headers.items()], "", signed, hashlib.sha256(body).hexdigest()]
    )
    scope = f"{day}/{REGION}/{SERVICE}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signing_key = _sign(_sign(_sign(_sign(("AWS4" + secret).encode(), day), REGION), SERVICE), "aws4_request")
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    )
    return path, headers


def call(operation, payload, key, secret, retries=2):
    """One API call, returning the decoded JSON. Retries when throttled."""
    body = json.dumps(payload).encode()
    path, headers = signed_headers(operation, body, key, secret)
    request = urllib.request.Request(f"https://{HOST}{path}", data=body, headers=headers, method="POST")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            if exc.code == 429 and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            if exc.code == 404 and "NoResults" in detail:
                return {}
            raise AmazonError(f"Amazon API {exc.code}: {detail}") from exc
        except OSError as exc:
            raise AmazonError(f"Amazon API unreachable: {exc}") from exc
    raise AmazonError("Amazon API kept throttling us.")


# Reading answers -------------------------------------------------------------

def item_offer(item, Offer):
    """An Offer for one API item, or None when it has no new-condition price."""
    asin = item.get("ASIN", "")
    title = (item.get("ItemInfo", {}).get("Title", {}) or {}).get("DisplayValue", "")
    url = item.get("DetailPageURL", "")
    listings = (item.get("Offers", {}) or {}).get("Listings", []) or []
    listing = next((row for row in listings if (row.get("Condition", {}) or {}).get("Value", "New") == "New"), None)
    if not (asin and title and url and listing):
        return None
    price = listing.get("Price", {}) or {}
    if price.get("Currency", "GBP") != "GBP":
        return None
    try:
        amount = Decimal(str(price.get("Amount", "")))
    except InvalidOperation:
        return None
    in_stock = (listing.get("Availability", {}) or {}).get("Type", "Now") == "Now"
    free = (listing.get("DeliveryInfo", {}) or {}).get("IsFreeShippingEligible", False)
    eans = ((item.get("ItemInfo", {}).get("ExternalIds", {}) or {}).get("EANs", {}) or {}).get("DisplayValues", [])
    image = ((item.get("Images", {}).get("Primary", {}) or {}).get("Large", {}) or {}).get("URL", "")
    return Offer(
        title=title,
        url=url,
        price=amount,
        ean=eans[0] if eans else "",
        availability=Listing.Availability.IN_STOCK if in_stock else Listing.Availability.OUT_OF_STOCK,
        delivery=Decimal("0.00") if free else None,
        image=image,
        tags=(asin,),
    )


def search_payload(keywords, tag):
    return {
        "Keywords": keywords,
        "SearchIndex": "All",
        "Condition": "New",
        "ItemCount": 10,
        "PartnerTag": tag,
        "PartnerType": "Associates",
        "Marketplace": MARKETPLACE,
        "Resources": RESOURCES,
    }


def items_payload(asins, tag):
    return {
        "ItemIds": list(asins),
        "Condition": "New",
        "PartnerTag": tag,
        "PartnerType": "Associates",
        "Marketplace": MARKETPLACE,
        "Resources": RESOURCES,
    }


# The import ------------------------------------------------------------------

def amazon_offers(retailer, limit=None, call_api=None, pause=PAUSE):
    """Offers for every product known on Amazon plus up to ``limit`` new lookups.

    ``call_api(operation, payload)`` can replace the signed call in tests.
    """
    from .finder import interest_first
    from .importers import Catalogue, Offer, ean_key

    key, secret, tag = credentials()
    limit = limit if limit is not None else getattr(settings, "RIPRAPTOR_AMAZON_DAILY_LIMIT", 2000)
    api = call_api or (lambda operation, payload: call(operation, payload, key, secret))
    catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug"))
    products = Product.objects.filter(is_active=True).select_related("game")
    offers = []
    calls = 0

    known = list(products.exclude(amazon_asin=""))
    for start in range(0, len(known), BATCH):
        batch = known[start:start + BATCH]
        answer = api("GetItems", items_payload([p.amazon_asin for p in batch], tag))
        calls += 1
        by_asin = {p.amazon_asin: p for p in batch}
        for item in (answer.get("ItemsResult", {}) or {}).get("Items", []) or []:
            product = by_asin.get(item.get("ASIN"))
            offer = item_offer(item, Offer)
            if product and offer:
                offer.ean = product.ean or ""
                offers.append(offer)
        time.sleep(pause)

    budget = max(0, limit - calls)
    # The products visitors want most first (unless searched in the last three days), then never tried,
    # then the longest ago; products with a barcode ahead of the rest.
    fresh = products.filter(amazon_asin="").order_by(F("amazon_checked_at").asc(nulls_first=True), "-ean", "-pk")
    checked = []
    found = {}
    for product in interest_first(fresh, budget, "amazon_checked_at"):
        keywords = product.ean or product.name
        answer = api("SearchItems", search_payload(keywords, tag))
        checked.append(product.pk)
        for item in (answer.get("SearchResult", {}) or {}).get("Items", []) or []:
            offer = item_offer(item, Offer)
            if offer is None:
                continue
            if product.ean and ean_key(offer.ean) == ean_key(product.ean):
                matched = True
            else:
                match, value = catalogue.best_match(offer.title, game=product.game.slug)
                matched = bool(match) and match[0] == product.pk and value >= AUTO_LINK
            if matched:
                found[product.pk] = offer.tags[0]
                offer.ean = product.ean or ""
                offers.append(offer)
                break
        time.sleep(pause)
    now = timezone.now()
    Product.objects.filter(pk__in=checked).update(amazon_checked_at=now)
    for pk, asin in found.items():
        Product.objects.filter(pk=pk).update(amazon_asin=asin)
    logger.info("Amazon: %d known products refreshed, %d looked up, %d offers", len(known), len(checked), len(offers))
    return offers
