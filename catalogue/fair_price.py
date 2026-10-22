"""
Is today's price a fair one? A verdict from RipRaptor's own record of each product's cheapest delivered price,
one row a day (DailyLowestPrice), which the site keeps whatever happens to any source it reads.

Today's cheapest delivered price is set against the days before today, back up to WINDOW_DAYS:

    5  lowest   Lowest we've seen   at or below the lowest of those days
    4  good     Good price          at least GOOD under their average
    3  fair     Fair price          within GOOD of it
    2  above    Above average       up to HIGH over it
    1  high     High price          more than HIGH over it

A product gets no verdict without MIN_DAYS days of prices before today, or without a delivered price now (a
shop whose delivery is unknown), and only in the games in GAMES while this is on trial.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Min
from django.utils import timezone

from .models import DailyLowestPrice

GAMES = {"pokemon"}
WINDOW_DAYS = 365
MIN_DAYS = 14
GOOD = Decimal("0.05")
HIGH = Decimal("0.15")
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
    if price <= low:
        return LOWEST
    if price <= average * (1 - GOOD):
        return GOOD_PRICE
    if price <= average * (1 + GOOD):
        return FAIR
    if price <= average * (1 + HIGH):
        return ABOVE
    return HIGH_PRICE


def verdict(product, price, today=None):
    """The Verdict for ``product`` at ``price`` (its cheapest delivered price now), or None. Two queries."""
    if price is None or not applies(product):
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
