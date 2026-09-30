"""
What a product card needs beyond the cheapest price: the runner-up, the
saving between them, and whether today's price is a low point.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from .models import DailyLowestPrice, Listing, stale_cutoff

BEST_WEEK = "best_week"
LOWEST_TODAY = "lowest_today"


def buyable_prefetch():
    """Attach ``product.offers``: buyable listings, cheapest first."""
    return Prefetch(
        "listings",
        queryset=Listing.objects.filter(
            is_active=True,
            retailer__is_active=True,
            last_checked__gte=stale_cutoff(),
            availability__in=Listing.BUYABLE,
        )
        .select_related("retailer")
        .order_by("delivered_price", "retailer__name"),
        to_attr="offers",
    )


class Summary:
    __slots__ = ("best", "second", "saving", "percent", "badge")

    def __init__(self, best, second, saving, percent, badge):
        self.best, self.second, self.saving, self.percent, self.badge = best, second, saving, percent, badge


def summarise(product, week_lows=None):
    """Summary for a product loaded with ``buyable_prefetch``.

    ``week_lows`` maps product id to the lowest daily price in the trending
    window, from ``week_low_map``; without it no badge is given.
    """
    offers = getattr(product, "offers", [])
    best = offers[0] if offers else None
    second = offers[1] if len(offers) > 1 else None
    saving = percent = None
    if best and second and second.delivered_price > best.delivered_price:
        saving = second.delivered_price - best.delivered_price
        percent = int((saving / second.delivered_price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    badge = None
    if best and week_lows is not None and product.pk in week_lows:
        badge = BEST_WEEK if best.delivered_price <= week_lows[product.pk] else LOWEST_TODAY
    return Summary(best, second, saving, percent, badge)


def week_low_map(product_ids, days=None, today=None):
    """Lowest recorded daily price per product over the last ``days`` days."""
    days = days or settings.RIPRAPTOR_TRENDING_DAYS
    today = today or timezone.localdate()
    lows = {}
    rows = DailyLowestPrice.objects.filter(
        product_id__in=list(product_ids), date__gte=today - timedelta(days=days), date__lt=today
    ).values_list("product_id", "price")
    for pk, price in rows:
        if pk not in lows or price < lows[pk]:
            lows[pk] = price
    return lows


MAX_REAL_PERCENT = 70


def biggest_savings(products, limit=6):
    """Products whose cheapest offer beats the runner-up by the most, as (product, summary)."""
    rows = []
    for product in products:
        summary = summarise(product)
        # A saving above this share is a wrong product link, not a bargain.
        if summary.saving and summary.percent <= MAX_REAL_PERCENT:
            rows.append((product, summary))
    rows.sort(key=lambda row: (-row[1].percent, -row[1].saving, row[0].name))
    return rows[:limit]
