"""
Picked for you: in-stock products like the ones a visitor has looked at, saved or searched for, with the
best deals first.

What a visitor looked at, saved and searched for lives in their browser (catalogue history and
watchlist scripts). The home page sends it with one request; the server ranks and prices, and keeps
nothing about the visitor.

A product counts as like one they looked at when it is in the same set, or the same game and kind, and
more so in the same language. A deal goes first only with evidence: a confirmed saving of at least
GOOD_SAVING percent on a next shop whose price is in line with the market, or a verified 30-day low clearly
under the month's average (offers.Summary.low_deal).

Each pick carries one label saying why it is worth a look (Tag, chosen by tag()). The owner, 30 Oct 2026:
"Too good to miss" on every deal said nothing, so the label names the evidence or the fact instead.
"""

from collections import namedtuple
from datetime import date, timedelta

from django.utils import timezone

from django.db.models import Case, IntegerField, Q, Value, When

from . import offers
from .models import Product
from .search import apply_search

PICKS_MAX = 8
# How many related products are priced to choose from: enough for a good row, few enough to be quick.
CANDIDATES = 120
# A confirmed saving this big on the next shop puts a pick among the deals first, labelled with it.
GOOD_SAVING = 15
# A product out within this many days is labelled a new release.
NEW_DAYS = 30
# In stock instead: how many to show where what a visitor wanted is sold out.
INSTEAD_MAX = 4
# Search terms sent, and results read for each, to learn what a visitor is after.
SEARCHES_READ = 3
RESULTS_PER_SEARCH = 3


# A pick's label: its kind, and the number it shows (a percentage or a count of shops) where it has one. The kinds,
# strongest first: low_30 and best_week (our own price record, as offers.summarise sets them), under (a saving of
# GOOD_SAVING percent or more on the next shop), new (out in the last NEW_DAYS days) and shops (how many have it in
# stock, which every pick has). Price evidence is green on the page; the facts are plain.
class Tag(namedtuple("Tag", "kind number")):
    @property
    def good(self):
        """Price evidence, shown green, rather than a plain fact."""
        return self.kind in ("low_30", "best_week", "under")


def tag(product, summary, today=None):
    """The one label a pick shows: the strongest true thing that can be said about it."""
    if summary.badge in ("low_30", "best_week"):
        return Tag(summary.badge, None)
    if (summary.percent or 0) >= GOOD_SAVING:
        return Tag("under", summary.percent)
    today = today or timezone.localdate()
    release = getattr(product, "release", None)
    if release and today - timedelta(days=NEW_DAYS) <= release <= today:
        return Tag("new", None)
    return Tag("shops", product.in_stock_count)


def from_searches(terms):
    """Products a visitor's recent searches point at: the top few results of each."""
    found = []
    for term in [t for t in terms if t.strip()][:SEARCHES_READ]:
        queryset, words = apply_search(Product.objects.active(), term)
        if words:
            found += list(queryset.order_by("name_rank", "pk")[:RESULTS_PER_SEARCH])
    return found


def picks(seen, limit=PICKS_MAX, closest_first=False):
    """[(product, summary, Tag)] for up to ``limit`` in-stock products like ``seen``, never one of them:
    the best deals first, then the closest to what was seen, then the most widely stocked.

    ``closest_first`` (instead) puts the closest first, then the deals, then the newest."""
    seen = [product for product in seen if product is not None]
    if not seen:
        return []
    sets = {p.product_set_id for p in seen if p.product_set_id}
    kinds = {(p.game_id, p.product_type) for p in seen}
    tongues = {p.language for p in seen}
    related = Q(product_set_id__in=sets)
    for game_id, kind in kinds:
        related |= Q(game_id=game_id, product_type=kind)
    order = ["-in_stock_count", "pk"]
    if closest_first:
        # The set's own products are priced first, however many of the kind are in stock across the game.
        order.insert(0, Case(When(product_set_id__in=sets, then=Value(1)), default=Value(0),
                             output_field=IntegerField()).desc())
    candidates = list(
        Product.objects.for_lists().in_stock().filter(related)
        .exclude(pk__in=[p.pk for p in seen])
        .prefetch_related(offers.buyable_prefetch())
        .order_by(*order)[:CANDIDATES]
    )
    week_lows = offers.week_low_map([p.pk for p in candidates])
    today = timezone.localdate()
    ranked = []
    for product in candidates:
        summary = offers.summarise(product, week_lows)
        if summary.best is None:
            continue
        hot = (summary.percent or 0) >= GOOD_SAVING or summary.low_deal
        closeness = (2 if product.product_set_id in sets else 1) + (product.language in tongues)
        if closest_first:
            key = (closeness, hot, product.release or date.min, summary.percent or 0, product.in_stock_count)
        else:
            key = (hot, closeness, summary.percent or 0, product.in_stock_count)
        ranked.append((key, product, summary))
    ranked.sort(key=lambda row: row[0], reverse=True)
    return [(product, summary, tag(product, summary, today)) for _, product, summary in ranked[:limit]]


def instead(wanted, limit=INSTEAD_MAX):
    """The next best thing to products nobody has in stock: up to ``limit`` in-stock products closest to
    ``wanted``, as picks gives them. The same set and language first, then the same set, then the same game and
    kind in the same language; among those the deals with evidence, then the newest."""
    return picks(wanted, limit=limit, closest_first=True)
