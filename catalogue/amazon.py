"""
Prices from Amazon.co.uk through Amazon's Creators API (it replaced the Product Advertising API).

Amazon publishes no feed. Its Creators API lends an application a token for its credential (an hour, kept and used
again) and then answers item lookups and searches at about one a second. A run reads every product we have already
found on Amazon, ten a request (getItems), then looks up new products by barcode where we have one and otherwise by
name (searchItems), keeping a result only when it matches the product we asked about.

    RIPRAPTOR_AMAZON_CREDENTIAL_ID, RIPRAPTOR_AMAZON_CREDENTIAL_SECRET   from Associates Central, Tools, Creators API
    RIPRAPTOR_AMAZON_PARTNER_TAG                                         the Associates tag, for example ripraptor-21

or saved from Admin, Amazon (web/amazon_admin.py) into a file only the site can read (RIPRAPTOR_AMAZON_KEY_FILE);
what is set in the server's settings wins. The owner, 13 Nov 2026, approved for the Creators API: "We can start
listing products ... I now need you to build it in".

Only Amazon's own offer is kept, when Amazon itself sells it (OffersV2 gives the featured offer and who sells it, but
no delivery): Amazon's published delivery rates then apply (the shop's rules, setup_shops), where another seller's
charge would be a guess. An Amazon price never joins the comparison, the price history or any alert (models.fresh):
Amazon's rules forbid price tracking and alerting on an Associates site unless Amazon agrees, so it is shown only
as its own row on the product page, with when it was checked and Amazon's disclaimer beside it, for
RIPRAPTOR_AMAZON_SHOW_HOURS (web.views.amazon_offer).

Links keep Amazon's own tracked address (detailPageURL), so no affiliate link format is needed on the retailer.
"""

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .matching import AUTO_LINK
from .models import Listing, Product

logger = logging.getLogger(__name__)

# Credential version 3.2 is Europe's: its tokens come from Amazon's UK address. The catalogue is one address for
# every marketplace, which each request names.
TOKEN_URL = "https://api.amazon.co.uk/auth/o2/token"
API_URL = "https://creatorsapi.amazon/catalog/v1/"
SCOPE = "creatorsapi::default"
MARKETPLACE = "www.amazon.co.uk"
BATCH = 10         # items a getItems request, and results a search
PAUSE = 1.1        # seconds between calls: new credentials get one a second
TOKEN_MARGIN = 300 # a token is asked for again this many seconds before Amazon says it ends
RESOURCES = [
    "itemInfo.title",
    "itemInfo.externalIds",
    "offersV2.listings.price",
    "offersV2.listings.availability",
    "offersV2.listings.condition",
    "offersV2.listings.merchantInfo",
]
# Amazon's availability types: in stock, or on pre-order; anything else (out of stock, unavailable, a lead time,
# unknown) is out of stock here.
IN_STOCK = {"IN_STOCK", "IN_STOCK_SCARCE"}
PREORDER = {"PREORDER", "AVAILABLE_DATE"}
_token = {"value": "", "until": 0.0, "for": ""}


class AmazonError(Exception):
    pass


# Credentials -------------------------------------------------------------------------------------------------------

def key_path():
    return Path(settings.RIPRAPTOR_AMAZON_KEY_FILE)


def saved_keys():
    """{"credential_id", "secret", "tag"} saved from Admin, Amazon, or {}."""
    try:
        found = json.loads(key_path().read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def save_keys(credential_id, secret, tag):
    """Write the keys where only the site's user can read them, whole or not at all. A blank secret keeps the one
    saved before, so the page never has to show it."""
    kept = saved_keys()
    keys = {"credential_id": credential_id.strip(), "secret": secret.strip() or kept.get("secret", ""),
            "tag": tag.strip()}
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".amazon-keys-")
    try:
        os.chmod(temp, 0o600)
        os.write(handle, json.dumps(keys).encode())
        os.fsync(handle)
        os.close(handle)
        os.replace(temp, path)
    except BaseException:
        try:
            os.close(handle)
        except OSError:
            pass
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    forget_token()


def keys():
    """(credential id, secret, tag): the server's settings where set, else the ones saved from Admin."""
    saved = saved_keys()
    return (settings.RIPRAPTOR_AMAZON_CREDENTIAL_ID or saved.get("credential_id", ""),
            settings.RIPRAPTOR_AMAZON_CREDENTIAL_SECRET or saved.get("secret", ""),
            settings.RIPRAPTOR_AMAZON_PARTNER_TAG or saved.get("tag", ""))


def partner_tag():
    return keys()[2]


def configured():
    return all(keys())


def credentials():
    credential_id, secret, tag = keys()
    if not (credential_id and secret and tag):
        raise AmazonError("Save the Creators API credential and the Associates tag in Admin, Amazon first.")
    return credential_id, secret, tag


# Calls ---------------------------------------------------------------------------------------------------------------

def forget_token():
    _token.update(value="", until=0.0, **{"for": ""})


def post(url, body, headers, timeout=30):
    """POST JSON, returning (status, decoded JSON or {}, Retry-After seconds or None)."""
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}"), None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "ignore")
        try:
            answer = json.loads(text) if text else {}
        except ValueError:
            answer = {"message": text[:300]}
        retry = exc.headers.get("Retry-After") if exc.headers else None
        return exc.code, answer if isinstance(answer, dict) else {"message": str(answer)[:300]}, retry
    except OSError as exc:
        raise AmazonError(f"Amazon API unreachable: {exc}") from exc


def token(credential_id, secret, send=None, clock=None):
    """An access token for the credential, kept until TOKEN_MARGIN before it ends."""
    send = send or post
    clock = clock or time.monotonic
    if _token["value"] and _token["for"] == credential_id and clock() < _token["until"]:
        return _token["value"]
    status, answer, _retry = send(TOKEN_URL, {"grant_type": "client_credentials", "client_id": credential_id,
                                              "client_secret": secret, "scope": SCOPE}, {})
    if status != 200 or not answer.get("access_token"):
        why = answer.get("error_description") or answer.get("error") or answer.get("message") or f"status {status}"
        raise AmazonError(f"Amazon did not lend a token for the credential: {why}")
    _token.update(value=answer["access_token"], until=clock() + int(answer.get("expires_in") or 3600) - TOKEN_MARGIN,
                  **{"for": credential_id})
    return _token["value"]


def call(operation, payload, credential_id, secret, send=None, retries=2, sleep=None):
    """One catalogue call (getItems, searchItems), returning the decoded JSON. Waits and asks again when throttled,
    and asks for a new token once when Amazon says the one used has ended. Amazon finding none of the items is {}."""
    send = send or post
    sleep = sleep or time.sleep
    renewed = False
    for attempt in range(retries + 2):
        headers = {"Authorization": f"Bearer {token(credential_id, secret, send)}", "x-marketplace": MARKETPLACE}
        status, answer, retry = send(API_URL + operation, payload, headers)
        if status == 200:
            return answer
        kind = answer.get("type", "") or answer.get("error", "")
        reason = answer.get("reason", "")
        if status == 401 and not renewed:
            forget_token()
            renewed = True
            continue
        if status == 429 and attempt < retries:
            wait = answer.get("retryAfterSeconds") or retry or 5 * (attempt + 1)
            try:
                sleep(min(float(wait), 60))
            except (TypeError, ValueError):
                sleep(5)
            continue
        if status == 404 and "ResourceNotFound" in kind:
            return {}
        message = answer.get("message") or answer.get("error_description") or ""
        raise AmazonError(f"Amazon API {status} {kind} {reason}: {message}".replace("  ", " ").strip()[:400])
    raise AmazonError("Amazon API kept throttling us.")


# Reading answers -----------------------------------------------------------------------------------------------------

def items_of(answer, result):
    """The items of a getItems (itemsResult; Amazon's own reference also says itemResults) or searchItems
    (searchResult) answer."""
    holders = [result] if result != "itemsResult" else ["itemsResult", "itemResults"]
    for holder in holders:
        found = (answer.get(holder) or {}).get("items")
        if found:
            return found
    return []


def sold_by_amazon(listing):
    name = ((listing.get("merchantInfo") or {}).get("name") or "").strip().lower()
    return name.startswith("amazon")


def item_offer(item, Offer):
    """An Offer for one item, or None: without a title, link or new-condition price in pounds sold by Amazon itself."""
    asin = item.get("asin", "")
    title = ((item.get("itemInfo") or {}).get("title") or {}).get("displayValue", "")
    url = item.get("detailPageURL", "")
    listings = (item.get("offersV2") or {}).get("listings") or []
    listing = next((row for row in listings if ((row.get("condition") or {}).get("value") or "New") == "New"
                    and sold_by_amazon(row)), None)
    if not (asin and title and url and listing):
        return None
    money = (listing.get("price") or {}).get("money") or {}
    if money.get("currency", "GBP") != "GBP":
        return None
    try:
        amount = Decimal(str(money.get("amount", ""))).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    kind = ((listing.get("availability") or {}).get("type") or "").upper()
    availability = (Listing.Availability.IN_STOCK if kind in IN_STOCK
                    else Listing.Availability.PREORDER if kind in PREORDER else Listing.Availability.OUT_OF_STOCK)
    eans = (((item.get("itemInfo") or {}).get("externalIds") or {}).get("eans") or {}).get("displayValues") or []
    # No picture: Amazon's policies forbid storing its images, and the site keeps a product's picture for good.
    # Delivery is left to the shop's rules: Amazon's published rates for what it sells itself.
    return Offer(title=title, url=url, price=amount, ean=eans[0] if eans else "", availability=availability,
                 delivery=None, image="", tags=(asin,))


def search_payload(keywords, tag):
    return {"keywords": keywords[:200], "searchIndex": "All", "itemCount": BATCH, "condition": "New",
            "availability": "IncludeOutOfStock", "partnerTag": tag, "marketplace": MARKETPLACE,
            "resources": RESOURCES}


def items_payload(asins, tag):
    return {"itemIds": list(asins), "itemIdType": "ASIN", "condition": "New", "partnerTag": tag,
            "marketplace": MARKETPLACE, "resources": RESOURCES}


def check(send=None):
    """Ask Amazon for a token and one search, to show the owner on Admin, Amazon whether the keys work: (True, how
    many items the search found) or (False, what Amazon said)."""
    try:
        credential_id, secret, tag = credentials()
        forget_token()
        answer = call("searchItems", search_payload("Pokemon booster box", tag), credential_id, secret, send=send,
                      retries=0)
    except AmazonError as exc:
        return False, str(exc)
    return True, len(items_of(answer, "searchResult"))


# The import ----------------------------------------------------------------------------------------------------------

def amazon_offers(retailer, limit=None, call_api=None, pause=PAUSE):
    """Offers for every product known on Amazon plus up to ``limit`` new lookups (RIPRAPTOR_AMAZON_DAILY_LIMIT
    shared over the day's runs, importers.AMAZON_EVERY_HOURS apart).

    ``call_api(operation, payload)`` can replace the real call in tests.
    """
    from .finder import interest_first
    from .importers import AMAZON_EVERY_HOURS, Catalogue, Offer, ean_key

    credential_id, secret, tag = credentials()
    if limit is None:
        limit = max(1, settings.RIPRAPTOR_AMAZON_DAILY_LIMIT * AMAZON_EVERY_HOURS // 24)
    api = call_api or (lambda operation, payload: call(operation, payload, credential_id, secret))
    catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug"))
    products = Product.objects.filter(is_active=True).select_related("game")
    offers = []
    calls = 0

    known = list(products.exclude(amazon_asin=""))
    for start in range(0, len(known), BATCH):
        batch = known[start:start + BATCH]
        answer = api("getItems", items_payload([p.amazon_asin for p in batch], tag))
        calls += 1
        by_asin = {p.amazon_asin: p for p in batch}
        for item in items_of(answer, "itemsResult"):
            product = by_asin.get(item.get("asin"))
            offer = item_offer(item, Offer)
            if product and offer:
                offer.ean = product.ean or ""
                offer.product_pk = product.pk
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
        answer = api("searchItems", search_payload(keywords, tag))
        checked.append(product.pk)
        for item in items_of(answer, "searchResult"):
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
                offer.product_pk = product.pk
                offers.append(offer)
                break
        time.sleep(pause)
    now = timezone.now()
    Product.objects.filter(pk__in=checked).update(amazon_checked_at=now)
    for pk, asin in found.items():
        Product.objects.filter(pk=pk).update(amazon_asin=asin)
    logger.info("Amazon: %d known products refreshed, %d looked up, %d offers", len(known), len(checked), len(offers))
    return offers
