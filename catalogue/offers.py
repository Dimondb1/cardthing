"""
What a product card needs beyond the cheapest price: the runner-up, the
saving between them, and whether today's price is a low point.

Deal claims rest on evidence, not on one dear listing:

* A saving on the next shop is claimed only when that shop's price is in line
  with the market: no more than OUTLIER_RATIO times the cheapest, or matched
  within MATCHED of it by a third shop. A lone £699.95 listing against a
  £250 cheapest says that shop is dear, not that £250 is a bargain.
* "Lowest in 30 days" (LOW_30) is verified from our own daily record: today's
  cheapest delivered price is at or below every day's cheapest over the last
  LOW_DAYS, with at least LOW_MIN_DAYS of them recorded.
* A product is a deal (Summary.is_deal: featured deals, Too good to miss)
  only with a 30-day low that is also LOW_UNDER below that month's average
  (a price that never moved is always its own low), or a credible saving of
  at least DEAL_PERCENT.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from .models import DailyLowestPrice, Listing, stale_cutoff

LOW_30 = "low_30"
BEST_WEEK = "best_week"
LOWEST_TODAY = "lowest_today"

LOW_DAYS = 30
LOW_MIN_DAYS = 14
LOW_UNDER = Decimal("0.03")       # a 30-day low counts as a deal this far under the month's average
OUTLIER_RATIO = Decimal("1.5")    # a runner-up dearer than this many times the cheapest is out of line
MATCHED = Decimal("0.10")         # unless a third shop is within this share of it
DEAL_PERCENT = 5                  # a credible saving this big or bigger is a deal


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
        .exclude(sanity=Listing.Sanity.EXCLUDED)
        .select_related("retailer")
        # Confirmed delivered prices first, cheapest first; shops whose delivery is unknown after them.
        .order_by("-delivery_known", "delivered_price", "retailer__name"),
        to_attr="offers",
    )


def offer_order(listing):
    """Sort key for offers: confirmed delivered prices first, cheapest first, then unknown delivery."""
    return (not listing.delivery_known, listing.delivered_price)


# A saving above this share of the runner-up's price is a wrong product link (a pack against a box,
# a part against the whole), not a bargain. It is never shown, and suspect_savings lists it for checking.
# The price verdicts (catalogue/sanity.py) catch these first; this cap stays as a second guard.
MAX_REAL_PERCENT = 70


class Summary:
    __slots__ = ("best", "second", "saving", "percent", "badge", "suspect", "deal", "under")

    def __init__(self, best, second, saving, percent, badge, suspect=False, deal=None, under=None):
        self.best, self.second, self.saving, self.percent, self.badge = best, second, saving, percent, badge
        self.suspect = suspect
        self.deal = deal
        self.under = under      # with LOW_30: how far today's price is under the 30-day average, in percent

    @property
    def low_deal(self):
        """A verified 30-day low that is also clearly under the month's average."""
        return self.badge == LOW_30 and (self.under or 0) >= LOW_UNDER * 100

    @property
    def is_deal(self):
        """Evidence that today's price is a good one: a 30-day low under the average, or a credible saving."""
        return self.low_deal or (self.percent or 0) >= DEAL_PERCENT

    @property
    def strength(self):
        """How strong the evidence is, for ranking deals: the bigger of the two, in percent."""
        return max((self.under or 0) if self.low_deal else 0, self.percent or 0)


class Lows:
    """A product's recent daily cheapest prices (week_low_map): the lowest of the trending window, and the lowest,
    average and number of days over LOW_DAYS."""

    __slots__ = ("week", "month", "average", "days")

    def __init__(self):
        self.week = self.month = self.average = None
        self.days = 0


def in_line(offers, second, best):
    """True when the runner-up's price is in line with the market, so a saving on it means something: no more
    than OUTLIER_RATIO times the cheapest, or matched within MATCHED by a third shop's confirmed price."""
    if second.delivered_price <= best.delivered_price * OUTLIER_RATIO:
        return True
    return any(
        offer.delivery_known and offer.sanity != Listing.Sanity.DOUBTFUL
        and offer.delivered_price <= second.delivered_price * (1 + MATCHED)
        for offer in offers[2:]
    )


def best_deal(offers, best):
    """The offer whose multi-buy ("2 for £50") costs least per item, when that beats buying one at ``best``.

    Only delivered prices are compared, so a deal is never called cheaper against a price without delivery.
    The ranking itself stays on buying one: a multi-buy asks the buyer to spend more."""
    if best is None or not best.delivery_known:
        return None
    found = None
    for offer in offers:
        if not offer.has_deal or offer.deal_delivery is None or offer.sanity == Listing.Sanity.DOUBTFUL:
            continue
        if offer.deal_each < best.delivered_price and (found is None or offer.deal_each < found.deal_each):
            found = offer
    return found


def summarise(product, week_lows=None):
    """Summary for a product loaded with ``buyable_prefetch``.

    ``week_lows`` maps product id to the lowest daily price in the trending
    window, from ``week_low_map``; without it no badge is given.
    """
    offers = getattr(product, "offers", [])
    best = offers[0] if offers else None
    second = offers[1] if len(offers) > 1 else None
    saving = percent = None
    # A doubtful price is shown but never claimed as a saving or a low: it may not be this product.
    suspect = any(offer is not None and offer.sanity == Listing.Sanity.DOUBTFUL for offer in (best, second))
    # A saving is only claimed between two confirmed delivered prices.
    comparable = not suspect and best and second and best.delivery_known and second.delivery_known
    if comparable and second.delivered_price > best.delivered_price:
        saving = second.delivered_price - best.delivered_price
        percent = int((saving / second.delivered_price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if percent > MAX_REAL_PERCENT:
            saving = percent = None
            suspect = True
        elif not in_line(offers, second, best):
            # One dear listing: the gap says that shop is dear, not that the cheapest is a bargain.
            saving = percent = None
    badge = under = None
    lows = week_lows.get(product.pk) if week_lows is not None else None
    if best and best.delivery_known and not suspect and lows is not None:
        price = best.delivered_price
        if lows.days >= LOW_MIN_DAYS and price <= lows.month:
            badge = LOW_30
            under = int(((lows.average - price) / lows.average * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        elif lows.week is not None:
            badge = BEST_WEEK if price <= lows.week else LOWEST_TODAY
    return Summary(best, second, saving, percent, badge, suspect, best_deal(offers, best), under)


def week_low_map(product_ids, days=None, today=None):
    """{product id: Lows} from the daily cheapest prices before today: the lowest over the last ``days`` (the
    trending window) and the lowest, average and count over LOW_DAYS. One query."""
    days = days or settings.RIPRAPTOR_TRENDING_DAYS
    today = today or timezone.localdate()
    week_from, month_from = today - timedelta(days=days), today - timedelta(days=LOW_DAYS)
    lows, totals = {}, {}
    rows = DailyLowestPrice.objects.filter(
        product_id__in=list(product_ids), date__gte=min(week_from, month_from), date__lt=today
    ).values_list("product_id", "date", "price")
    for pk, day, price in rows:
        row = lows.setdefault(pk, Lows())
        if day >= week_from and (row.week is None or price < row.week):
            row.week = price
        if day >= month_from:
            row.days += 1
            totals[pk] = totals.get(pk, 0) + price
            if row.month is None or price < row.month:
                row.month = price
    for pk, row in lows.items():
        if row.days:
            row.average = Decimal(totals[pk]) / row.days
    return lows


def biggest_savings(products, limit=6):
    """Products whose cheapest offer beats the runner-up by the most, as (product, summary)."""
    rows = []
    for product in products:
        summary = summarise(product)
        if summary.saving:
            rows.append((product, summary))
    rows.sort(key=lambda row: (-row[1].percent, -row[1].saving, row[0].name))
    return rows[:limit]
