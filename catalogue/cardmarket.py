"""
Cardmarket's public product catalogue and price guide, downloaded once a day and kept as our own copy.

Cardmarket publishes, for every game, a catalogue of its sealed products ("non-singles": boxes, packs, elite
trainer boxes, tins, decks) and a daily price guide, as files anyone may download
(https://www.cardmarket.com/Data/Download). We keep only sealed products, for the games in GAMES, and one row
a day of each one's prices (CardmarketPrice), so the history is ours whatever happens to the download. The
European Central Bank's daily reference rate (ExchangeRate) sets Cardmarket's euros beside our pounds.

Prices are Cardmarket's for the European market, in euros, before post to the UK and any import costs: a
reference for whether a UK price is in line with the market, never a price a visitor can buy at. Which of our
products each one is, is catalogue/cardmarket_match.py's.

Run daily from cron by import_cardmarket, after Cardmarket's own early morning update.
"""

import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .models import CardmarketPrice, CardmarketProduct, ExchangeRate, Game

logger = logging.getLogger(__name__)

# Our game's slug and Cardmarket's number for it (its download files are named by it).
GAMES = {
    "pokemon": 6, "magic-the-gathering": 1, "yu-gi-oh": 3, "dragon-ball": 13, "flesh-and-blood": 16,
    "digimon": 17, "one-piece": 18, "lorcana": 19, "star-wars-unlimited": 21, "riftbound": 22,
}
CATALOGUE_URL = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_nonsingles_{id}.json"
PRICES_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_{id}.json"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_NS = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
TIMEOUT = 60
MAX_BYTES = 120 * 1024 * 1024
USER_AGENT = "RipRaptor (https://ripraptor.com) market reference"
# One price guide entry: a flat object, so entries for products we keep are picked out without loading the
# whole guide (singles included, tens of megabytes for Magic) as Python objects.
ENTRY = re.compile(rb'\{"idProduct":(\d+),[^{}]*\}')


class CardmarketError(Exception):
    pass


@dataclass
class Imported:
    game: str
    products: int = 0
    new: int = 0
    prices: int = 0
    day: date | None = None


def fetch(url):
    """The file at ``url``, as bytes. Raises CardmarketError."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise CardmarketError(f"{url} could not be read: {exc.__class__.__name__}") from exc
    if len(data) > MAX_BYTES:
        raise CardmarketError(f"{url} is larger than expected")
    return data


def euros(value):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def day_of(stamp, fallback):
    """The UK date of a file's createdAt ("2026-10-22T02:45:11+0200")."""
    try:
        return timezone.localdate(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z"))
    except (TypeError, ValueError):
        return fallback


def import_game(game, read=fetch, now=None):
    """Read one game's sealed catalogue and today's price guide. Returns Imported."""
    now = now or timezone.now()
    number = GAMES[game.slug]
    try:
        catalogue = json.loads(read(CATALOGUE_URL.format(id=number)))
        products = catalogue["products"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CardmarketError(f"The {game.name} catalogue could not be read") from exc
    result = Imported(game.slug)
    known = set(CardmarketProduct.objects.filter(pk__in=[p.get("idProduct") for p in products]).values_list("pk", flat=True))
    rows = []
    for item in products:
        try:
            pk = int(item["idProduct"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            added = datetime.strptime(item.get("dateAdded") or "", "%Y-%m-%d %H:%M:%S").date()
        except (TypeError, ValueError):
            added = None
        rows.append(CardmarketProduct(
            pk=pk, game=game, name=(item.get("name") or "")[:255], category_id=int(item.get("idCategory") or 0),
            category=(item.get("categoryName") or "")[:120], expansion_id=int(item.get("idExpansion") or 0),
            added_on=added, seen_at=now,
        ))
    CardmarketProduct.objects.bulk_create(
        rows, batch_size=500, update_conflicts=True, unique_fields=["id"],
        update_fields=["game", "name", "category_id", "category", "expansion_id", "added_on", "seen_at"],
    )
    result.products, result.new = len(rows), len({row.pk for row in rows} - known)
    sealed = {row.pk for row in rows}

    guide = read(PRICES_URL.format(id=number))
    header = re.search(rb'"createdAt":"([^"]+)"', guide[:400])
    result.day = day_of(header.group(1).decode() if header else None, timezone.localdate(now))
    prices = []
    for match in ENTRY.finditer(guide):
        pk = int(match.group(1))
        if pk not in sealed:
            continue
        try:
            entry = json.loads(match.group(0))
        except ValueError:
            continue
        trend, avg, low = euros(entry.get("trend")), euros(entry.get("avg")), euros(entry.get("low"))
        if trend or avg:
            prices.append(CardmarketPrice(product_id=pk, date=result.day, trend=trend, avg=avg, low=low))
    CardmarketPrice.objects.bulk_create(
        prices, batch_size=500, update_conflicts=True, unique_fields=["product", "date"],
        update_fields=["trend", "avg", "low"],
    )
    result.prices = len(prices)
    return result


def import_rate(read=fetch):
    """Today's ECB reference rate for pounds. Returns the ExchangeRate, or None if the file had none."""
    try:
        root = ET.fromstring(read(ECB_URL))
    except ET.ParseError as exc:
        raise CardmarketError("The ECB rates could not be read") from exc
    for day in root.iterfind(".//e:Cube[@time]", ECB_NS):
        for rate in day.iterfind("e:Cube[@currency='GBP']", ECB_NS):
            try:
                when = date.fromisoformat(day.get("time"))
                value = Decimal(rate.get("rate"))
            except (TypeError, ValueError, InvalidOperation):
                return None
            found, _ = ExchangeRate.objects.update_or_create(date=when, defaults={"gbp_per_eur": value})
            return found
    return None


def run(slugs=None, read=fetch, now=None):
    """Import the rate and each game's files. Returns (rate, [Imported], [errors]). One game failing never
    stops the others."""
    errors, done = [], []
    rate = None
    try:
        rate = import_rate(read)
    except CardmarketError as exc:
        errors.append(str(exc))
    for game in Game.objects.filter(slug__in=list(slugs or GAMES), is_active=True).order_by("pk"):
        if game.slug not in GAMES:
            continue
        try:
            done.append(import_game(game, read, now))
        except CardmarketError as exc:
            logger.warning("Cardmarket: %s", exc)
            errors.append(str(exc))
    return rate, done, errors


def latest_prices(cardmarket_ids):
    """{Cardmarket product id: its newest CardmarketPrice}. One query."""
    found = {}
    for price in CardmarketPrice.objects.filter(product_id__in=list(cardmarket_ids)).order_by("product_id", "-date"):
        found.setdefault(price.product_id, price)
    return found


def in_pounds(euros_amount, rate):
    """Euros at a rate (ExchangeRate), in pounds to the penny, or None."""
    if euros_amount is None or rate is None:
        return None
    return (euros_amount * rate.gbp_per_eur).quantize(Decimal("0.01"))
