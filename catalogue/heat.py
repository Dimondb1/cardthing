"""
How much each product matters right now, so the background reader knows what to probe and how often.

Points come from what visitors do and from what makes a price likely to move:

- 4 per product page view today, at most 20
- 6 when it was on a loaded watchlist in the last two days
- 3 per click to a shop in the last seven days, at most 15
- 8 when a confirmed back-in-stock alert is waiting for it
- 5 when one shop or none can sell it now (one more shop changes the comparison)
- 6 when any shop has it on pre-order
- 5 when it was added in the last fourteen days

A product with 10 points or more is HOT: its listings are probed every ten minutes, at most 600 at a
time (the rest wait as WARM). With 3 or more it is WARM: probed every hour. The rest are COLD and are
refreshed only by whole-shop reads.
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import DailyPageView, Listing, OutboundClick, Product, StockAlert, stale_cutoff

VIEW_POINTS, VIEW_CAP = 4, 20
WATCHED_POINTS, WATCHED_DAYS = 6, 2
CLICK_POINTS, CLICK_CAP, CLICK_DAYS = 3, 15, 7
ALERT_POINTS = 8
FEW_SHOPS_POINTS = 5
PREORDER_POINTS = 6
NEW_POINTS, NEW_DAYS = 5, 14

HOT, WARM = 10, 3
HOT_EVERY = timedelta(minutes=10)
WARM_EVERY = timedelta(minutes=60)
HOT_CAP = 600


def heat_scores(now=None):
    """{product id: points} for every active product with any points, in four queries."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    by_slug_views, watched = {}, set()
    # 1. Page views today and watchlist rows in the last two days.
    rows = (
        DailyPageView.objects.filter(
            date__gte=today - timedelta(days=WATCHED_DAYS - 1),
            kind__in=[DailyPageView.Kind.PRODUCT, DailyPageView.Kind.WATCHED],
        )
        .exclude(key="")
        .values_list("kind", "key", "date", "hits")
    )
    for kind, key, date, hits in rows:
        if kind == DailyPageView.Kind.WATCHED and hits:
            watched.add(key)
        elif kind == DailyPageView.Kind.PRODUCT and date == today:
            by_slug_views[key] = by_slug_views.get(key, 0) + hits
    # 2. Clicks to a shop.
    clicks = dict(
        OutboundClick.objects.filter(created_at__gte=now - timedelta(days=CLICK_DAYS))
        .values_list("product").annotate(n=Count("id")).values_list("product", "n")
    )
    # 3. Confirmed alerts still waiting.
    alerts = set(StockAlert.objects.filter(confirmed_at__isnull=False).values_list("product_id", flat=True).distinct())
    # 4. Every active product with what its listings say.
    buyable = (
        Q(listings__is_active=True, listings__retailer__is_active=True, listings__last_checked__gte=stale_cutoff(now),
          listings__availability__in=Listing.BUYABLE)
        & ~Q(listings__sanity=Listing.Sanity.EXCLUDED)
    )
    products = (
        Product.objects.filter(is_active=True)
        .annotate(
            shops=Count("listings__retailer", filter=buyable, distinct=True),
            preorders=Count("listings", filter=Q(listings__availability=Listing.Availability.PREORDER, listings__is_active=True)),
        )
        .values_list("pk", "slug", "created_at", "shops", "preorders")
    )
    new_since = now - timedelta(days=NEW_DAYS)
    scores = {}
    for pk, slug, created_at, shops, preorders in products:
        points = min(VIEW_POINTS * by_slug_views.get(slug, 0), VIEW_CAP)
        if slug in watched:
            points += WATCHED_POINTS
        points += min(CLICK_POINTS * clicks.get(pk, 0), CLICK_CAP)
        if pk in alerts:
            points += ALERT_POINTS
        if shops <= 1:
            points += FEW_SHOPS_POINTS
        if preorders:
            points += PREORDER_POINTS
        if created_at and created_at >= new_since:
            points += NEW_POINTS
        if points:
            scores[pk] = points
    return scores


def tier_of(points):
    if points >= HOT:
        return "hot"
    if points >= WARM:
        return "warm"
    return "cold"


def tier_counts(scores):
    """How many products are HOT and WARM, for the Crawl health page."""
    hot = sum(1 for points in scores.values() if points >= HOT)
    warm = sum(1 for points in scores.values() if WARM <= points < HOT)
    return hot, warm
