"""
Is today's price a fair one? A verdict from RipRaptor's own record of each product's cheapest delivered price,
one row a day (DailyLowestPrice), which the site keeps whatever happens to any source it reads.

Today's cheapest delivered price is set against the days before today, back up to WINDOW_DAYS:

    5  lowest   Lowest yet          at least GOOD under their average, and at or below the lowest of those days
    4  good     Good price          at least GOOD under their average
    3  fair     Fair price          within GOOD of it
    2  above    Above average       up to HIGH over it
    1  high     High price          more than HIGH over it

A price that has never moved is its own lowest every day: that is a fair price, never "Lowest yet".

Our own record counts only with MIN_SHOPS UK shops listing the product: one shop's prices measured against its
own past say nothing about the market, so a lone £1,199.99 listing is never called a good price by it. It also
needs MIN_DAYS days of prices before today.

The market: when Claude matched the product to Cardmarket's surely, or the owner confirmed it
(catalogue/cardmarket_match.py), Cardmarket's trend price for it in the last MARKET_FRESH_DAYS, in pounds at
the day's ECB rate, is a second reference, and the price is banded against it too (market_band): at most
MARKET_GOOD of it is good, up to MARKET_FAIR fair, up to MARKET_ABOVE above, more is high. Cardmarket's prices
are before post to the UK and import costs, which is why a fair UK price may sit a little over them. The market
never makes a price "Lowest yet" (that is our own record's word), and with both references the verdict is the
lower band of the two, so neither can make a dear price look good.

A product gets no verdict without either reference, or without a delivered price now (a shop whose delivery
is unknown). Every game has it: Pokémon first, on trial, then all of them at the owner's request.

at_lowest lists the products whose cheapest delivered price now is the lowest of the last LOW_DAYS, for the
Deals page.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, DecimalField, ExpressionWrapper, F, FloatField, IntegerField, Min, OuterRef, Q, Subquery
from django.utils import timezone

from .models import CardmarketMatch, CardmarketPrice, DailyLowestPrice, ExchangeRate, Listing, Product
from .offers import MAX_REAL_PERCENT

WINDOW_DAYS = 365
MIN_DAYS = 14
MIN_SHOPS = 2
GOOD = Decimal("0.05")
HIGH = Decimal("0.15")
LOW_DAYS = 90       # at_lowest: the lowest of this many days back
MARKET_GOOD, MARKET_FAIR, MARKET_ABOVE = Decimal("0.95"), Decimal("1.15"), Decimal("1.35")
MARKET_FRESH_DAYS = 7
LOWEST, GOOD_PRICE, FAIR, ABOVE, HIGH_PRICE = 5, 4, 3, 2, 1
KEYS = {LOWEST: "lowest", GOOD_PRICE: "good", FAIR: "fair", ABOVE: "above", HIGH_PRICE: "high"}
SIGNAL_BARS = 4   # the Price check's signal bars: lit for the lowest yet, one fewer for each band dearer


@dataclass
class Market:
    gbp: Decimal              # Cardmarket's price for the same product, in pounds
    eur: Decimal
    date: date


@dataclass
class Verdict:
    band: int                 # 1 (high) to 5 (lowest we've seen)
    price: Decimal            # today's cheapest delivered price
    average: Decimal | None = None   # the average of the days before today (None without our own record)
    low: Decimal | None = None
    low_date: date | None = None
    days: int = 0             # days with a price before today
    since: date | None = None  # the first of them
    market: Market | None = None

    @property
    def key(self):
        return KEYS[self.band]

    @property
    def signal(self):
        """The Price check's four signal bars, shortest first, each lit or not: all four for the lowest yet, one
        fewer for each band dearer, none for a high price."""
        lit = self.band - HIGH_PRICE
        return [bar < lit for bar in range(SIGNAL_BARS)]


def band_for(price, average, low):
    if price <= average * (1 - GOOD):
        return LOWEST if price <= low else GOOD_PRICE
    if price <= average * (1 + GOOD):
        return FAIR
    if price <= average * (1 + HIGH):
        return ABOVE
    return HIGH_PRICE


def market_reference(product, today=None):
    """Cardmarket's price for ``product`` in pounds (Market), when its match is used and Cardmarket priced it in
    the last MARKET_FRESH_DAYS, or None. Three queries at most."""
    from .cardmarket import in_pounds

    today = today or timezone.localdate()
    found = (CardmarketMatch.objects.filter(product=product, status__in=CardmarketMatch.USED, cardmarket__isnull=False)
             .values_list("cardmarket_id", flat=True).first())
    if found is None:
        return None
    row = (CardmarketPrice.objects.filter(product_id=found, date__gte=today - timedelta(days=MARKET_FRESH_DAYS))
           .order_by("-date").first())
    euros = (row.trend or row.avg) if row else None
    rate = ExchangeRate.latest() if euros else None
    if rate is None:
        return None
    return Market(gbp=in_pounds(euros, rate), eur=euros, date=row.date)


def market_band(price, market_gbp):
    """The band for ``price`` against Cardmarket's price in pounds. Never LOWEST: that is our own record's."""
    if price <= market_gbp * MARKET_GOOD:
        return GOOD_PRICE
    if price <= market_gbp * MARKET_FAIR:
        return FAIR
    if price <= market_gbp * MARKET_ABOVE:
        return ABOVE
    return HIGH_PRICE


def verdict(product, price, today=None, shops=MIN_SHOPS):
    """The Verdict for ``product`` at ``price`` (its cheapest delivered price now), or None. ``shops`` is how
    many UK shops list it, in stock or not. Up to five queries."""
    if price is None:
        return None
    today = today or timezone.localdate()
    found = Verdict(band=HIGH_PRICE, price=price, market=market_reference(product, today))
    bands = []
    if shops >= MIN_SHOPS:
        rows = DailyLowestPrice.objects.filter(product=product, date__lt=today,
                                               date__gte=today - timedelta(days=WINDOW_DAYS))
        summary = rows.aggregate(days=Count("pk"), average=Avg("price"), low=Min("price"), since=Min("date"))
        if summary["days"] >= MIN_DAYS:
            pence = Decimal("0.01")
            found.average = Decimal(summary["average"]).quantize(pence)
            found.low = Decimal(summary["low"]).quantize(pence)
            # The latest day at the low, so "Lowest £x on <date>" names the most recent time it was that cheap.
            found.low_date = rows.filter(price=summary["low"]).order_by("-date").values_list("date", flat=True).first()
            found.days, found.since = summary["days"], summary["since"]
            bands.append(band_for(price, found.average, found.low))
    if found.market is not None:
        bands.append(market_band(price, found.market.gbp))
    if not bands:
        return None
    found.band = min(bands)
    return found


RECORD, MARKET, BOTH = "record", "market", "both"


def refresh_bands(today=None):
    """Store every active product's Price check band and what it rests on (Product.price_band, price_basis), as
    verdict() works it out, so lists can sort by it and show it with no query of their own. A product whose
    verdict is gone has both emptied. Six queries and one write for those that changed, however many products.
    Returns how many changed."""
    from .cardmarket import in_pounds

    today = today or timezone.localdate()
    prices = dict(
        Listing.objects.buyable().filter(delivery_known=True, product__is_active=True, product__game__is_active=True)
        .order_by().values("product_id").annotate(price=Min("delivered_price")).values_list("product_id", "price")
    )
    shops = dict(
        Listing.objects.live().exclude(sanity=Listing.Sanity.EXCLUDED).filter(product_id__in=list(prices))
        .order_by().values("product_id").annotate(n=Count("retailer_id", distinct=True)).values_list("product_id", "n")
    )
    records = {
        row["product_id"]: row for row in
        DailyLowestPrice.objects.filter(product_id__in=list(prices), date__lt=today,
                                        date__gte=today - timedelta(days=WINDOW_DAYS))
        .order_by().values("product_id").annotate(days=Count("pk"), average=Avg("price"), low=Min("price"))
    }
    matched = dict(CardmarketMatch.objects.filter(product_id__in=list(prices), status__in=CardmarketMatch.USED,
                                                  cardmarket__isnull=False).values_list("product_id", "cardmarket_id"))
    latest = {}
    for row in (CardmarketPrice.objects.filter(product_id__in=set(matched.values()),
                                               date__gte=today - timedelta(days=MARKET_FRESH_DAYS))
                .order_by("product_id", "-date")):
        latest.setdefault(row.product_id, row)
    rate = ExchangeRate.latest() if latest else None
    pence = Decimal("0.01")
    wanted = {}
    for product_id, price in prices.items():
        bands, basis = [], []
        record = records.get(product_id)
        if shops.get(product_id, 0) >= MIN_SHOPS and record and record["days"] >= MIN_DAYS:
            average = Decimal(record["average"]).quantize(pence)
            bands.append(band_for(price, average, Decimal(record["low"]).quantize(pence)))
            basis.append(RECORD)
        row = latest.get(matched.get(product_id))
        euros = (row.trend or row.avg) if row else None
        if euros and rate is not None:
            bands.append(market_band(price, in_pounds(euros, rate)))
            basis.append(MARKET)
        if bands:
            wanted[product_id] = (min(bands), BOTH if len(basis) == 2 else basis[0])
    changed = []
    for product in Product.objects.active().only("pk", "price_band", "price_basis"):
        band, basis = wanted.get(product.pk, (None, ""))
        if (product.price_band, product.price_basis) != (band, basis):
            product.price_band, product.price_basis = band, basis
            changed.append(product)
    Product.objects.bulk_update(changed, ["price_band", "price_basis"], batch_size=500)
    return len(changed)


def at_lowest(limit=12, today=None):
    """Products listed by MIN_SHOPS shops or more, whose cheapest confirmed delivered price now is at or
    below the lowest of the LOW_DAYS before today, with at least MIN_DAYS of them, and at least GOOD under their
    average: a price that has never moved is not news. Biggest saving on the average first, each with ``low_average`` (that average) and
    ``low_days``. One query.
    """
    from .pricing import doubtful_low

    today = today or timezone.localdate()
    window = DailyLowestPrice.objects.order_by().filter(
        product=OuterRef("pk"), date__lt=today, date__gte=today - timedelta(days=LOW_DAYS),
    ).values("product")
    money = DecimalField(max_digits=9, decimal_places=2)

    def summary(aggregate, field):
        return Subquery(window.annotate(value=aggregate).values("value"), output_field=field)

    return list(
        Product.objects.for_lists().priced().filter(listing_count__gte=MIN_SHOPS)
        .annotate(
            low_days=summary(Count("pk"), IntegerField()),
            low_before=summary(Min("price"), money),
            low_average=summary(Avg("price"), money),
            doubtful_low=doubtful_low(),
        )
        .filter(lowest_known__isnull=False, lowest_known=F("lowest_price"), low_days__gte=MIN_DAYS,
                lowest_known__lte=F("low_before"))
        .filter(lowest_known__lte=F("low_average") * (1 - GOOD))
        # A doubtful cheapest price is not news until it is confirmed.
        .filter(Q(doubtful_low__isnull=True) | Q(doubtful_low__gt=F("lowest_known")))
        .annotate(under=ExpressionWrapper((F("low_average") - F("lowest_known")) * 1.0 / F("low_average"),
                                          output_field=FloatField()))
        # A fall this large is a wrong product link appearing, not a low, as with savings and drops.
        .filter(under__lte=MAX_REAL_PERCENT / 100)
        .order_by("-under", "name")[:limit]
    )
