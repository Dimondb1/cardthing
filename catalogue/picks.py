"""
Picked for you: in-stock products like the ones a visitor has looked at, saved or searched for, with the
deals too good to miss first.

What a visitor looked at, saved and searched for lives in their browser (catalogue history and
watchlist scripts). The home page sends it with one request; the server ranks and prices, and keeps
nothing about the visitor.

A product counts as like one they looked at when it is in the same set, or the same game and kind, and
more so in the same language. "Too good to miss" is only ever a true claim: a confirmed saving of at
least GOOD_SAVING percent on the next shop, or the lowest price of the trending window.
"""

from django.db.models import Q

from . import offers
from .models import Product
from .search import apply_search

PICKS_MAX = 8
# How many related products are priced to choose from: enough for a good row, few enough to be quick.
CANDIDATES = 120
# A confirmed saving this big on the next shop is too good to miss.
GOOD_SAVING = 15
# Search terms sent, and results read for each, to learn what a visitor is after.
SEARCHES_READ = 3
RESULTS_PER_SEARCH = 3


def from_searches(terms):
    """Products a visitor's recent searches point at: the top few results of each."""
    found = []
    for term in [t for t in terms if t.strip()][:SEARCHES_READ]:
        queryset, words = apply_search(Product.objects.active(), term)
        if words:
            found += list(queryset.order_by("name_rank", "pk")[:RESULTS_PER_SEARCH])
    return found


def picks(seen, limit=PICKS_MAX):
    """[(product, summary, hot)] for up to ``limit`` in-stock products like ``seen``, never one of them:
    the deals too good to miss first, then the closest to what was seen, then the most widely stocked."""
    seen = [product for product in seen if product is not None]
    if not seen:
        return []
    sets = {p.product_set_id for p in seen if p.product_set_id}
    kinds = {(p.game_id, p.product_type) for p in seen}
    tongues = {p.language for p in seen}
    related = Q(product_set_id__in=sets)
    for game_id, kind in kinds:
        related |= Q(game_id=game_id, product_type=kind)
    candidates = list(
        Product.objects.for_lists().filter(related, in_stock_count__gte=1)
        .exclude(pk__in=[p.pk for p in seen])
        .prefetch_related(offers.buyable_prefetch())
        .order_by("-in_stock_count", "pk")[:CANDIDATES]
    )
    week_lows = offers.week_low_map([p.pk for p in candidates])
    ranked = []
    for product in candidates:
        summary = offers.summarise(product, week_lows)
        if summary.best is None:
            continue
        hot = (summary.percent or 0) >= GOOD_SAVING or summary.badge == offers.BEST_WEEK
        closeness = (2 if product.product_set_id in sets else 1) + (product.language in tongues)
        ranked.append(((hot, closeness, summary.percent or 0, product.in_stock_count), product, summary, hot))
    ranked.sort(key=lambda row: row[0], reverse=True)
    return [(product, summary, hot) for _, product, summary, hot in ranked[:limit]]
