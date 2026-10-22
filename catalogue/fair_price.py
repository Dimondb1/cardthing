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

A product gets no verdict without MIN_SHOPS UK shops listing it: one shop's prices measured against its own
past say nothing about the market, so a lone £1,199.99 listing is never called a good price. Nor without
MIN_DAYS days of prices before today, or without a delivered price now (a shop whose delivery is unknown), and
only in the games in GAMES while this is on trial.

at_lowest lists the products whose cheapest delivered price now is the lowest of the last LOW_DAYS, for the
Deals page.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, DecimalField, ExpressionWrapper, F, FloatField, IntegerField, Min, OuterRef, Q, Subquery
from django.utils import timezone

from .models import DailyLowestPrice, Product
from .offers import MAX_REAL_PERCENT

GAMES = {"pokemon"}
WINDOW_DAYS = 365
MIN_DAYS = 14
MIN_SHOPS = 2
GOOD = Decimal("0.05")
HIGH = Decimal("0.15")
LOW_DAYS = 90       # at_lowest: the lowest of this many days back
LOWEST, GOOD_PRICE, FAIR, ABOVE, HIGH_PRICE = 5, 4, 3, 2, 1
KEYS = {LOWEST: "lowest", GOOD_PRICE: "good", FAIR: "fair", ABOVE: "above", HIGH_PRICE: "high"}


@dataclass
class Verdict:
    band: int                 # 1 (high) to 5 (lowest we've seen)
    price: Decimal            # today's cheapest delivered price
    average: Decimal          # the average of the days before today
    low: Decimal
    low_date: date
    days: int                 # days with a price before today
    since: date               # the first of them

    @property
    def key(self):
        return KEYS[self.band]

    @property
    def bands(self):
        """The five bands from best at the top to dearest at the bottom, each marked when it is this one."""
        return [{"band": band, "key": KEYS[band], "on": band == self.band} for band in sorted(KEYS, reverse=True)]


def applies(product):
    return product.game.slug in GAMES


def band_for(price, average, low):
    if price <= average * (1 - GOOD):
        return LOWEST if price <= low else GOOD_PRICE
    if price <= average * (1 + GOOD):
        return FAIR
    if price <= average * (1 + HIGH):
        return ABOVE
    return HIGH_PRICE


def verdict(product, price, today=None, shops=MIN_SHOPS):
    """The Verdict for ``product`` at ``price`` (its cheapest delivered price now), or None. ``shops`` is how
    many UK shops list it, in stock or not. Two queries."""
    if price is None or shops < MIN_SHOPS or not applies(product):
        return None
    today = today or timezone.localdate()
    rows = DailyLowestPrice.objects.filter(product=product, date__lt=today, date__gte=today - timedelta(days=WINDOW_DAYS))
    summary = rows.aggregate(days=Count("pk"), average=Avg("price"), low=Min("price"), since=Min("date"))
    if summary["days"] < MIN_DAYS:
        return None
    pence = Decimal("0.01")
    average = Decimal(summary["average"]).quantize(pence)
    low = Decimal(summary["low"]).quantize(pence)
    # The latest day at the low, so "Lowest £x on <date>" names the most recent time it was that cheap.
    low_date = rows.filter(price=summary["low"]).order_by("-date").values_list("date", flat=True).first()
    return Verdict(band=band_for(price, average, low), price=price, average=average, low=low,
                   low_date=low_date, days=summary["days"], since=summary["since"])


def at_lowest(limit=12, today=None):
    """Products in GAMES, listed by MIN_SHOPS shops or more, whose cheapest confirmed delivered price now is at or
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
        Product.objects.for_lists().priced().filter(game__slug__in=GAMES, listing_count__gte=MIN_SHOPS)
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
