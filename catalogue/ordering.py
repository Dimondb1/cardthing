"""
Shared filters and sort orders for product lists: the browse pages and the
swipe deck use the same ones so a choice means the same thing everywhere.
"""

from datetime import timedelta

from django.db.models import (
    Case, DecimalField, ExpressionWrapper, F, FloatField, IntegerField, Min, OuterRef, Q, Subquery, Value, When,
)
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone

from . import languages
from .models import DailyLowestPrice, Listing, fresh

# Language choices, from each product's stored language (catalogue/languages.py). "English" means
# nothing in its name or set says otherwise.
LANGUAGES = languages.FILTER

SORTS = [
    ("newest", "Newest first"),
    ("price", "Lowest price"),
    ("saving", "Biggest saving"),
    ("low", "Lowest in 90 days"),
    ("fair", "Fairest price"),
    ("card", "Cheapest per card"),
]
LOW_DAYS = 90
# A product of these types is one pack, when no shop says otherwise (a three-pack blister is a bundle).
PACK_TYPES = ("booster_pack", "collector_booster_pack")
# Products whose cards can be counted: cards a pack known, and packs known or the product is one pack
# (catalogue/contents.py). Only these have a price per card.
CARDS_KNOWN = ((Q(cards_per_pack__isnull=False) | Q(cards_found__isnull=False))
               & (Q(packs__isnull=False) | Q(packs_found__isnull=False) | Q(product_type__in=PACK_TYPES)))


def with_card_price(products):
    """Annotate ``cards_total`` (packs times cards a pack, the owner's figures over the shops') and ``price_per_card``
    (the cheapest price shared over them), each None where a count is not known. As a float: the database keeps
    whole-pound prices as whole numbers, which would divide to nothing."""
    one_pack = Case(When(product_type__in=PACK_TYPES, then=Value(1)), default=Value(None), output_field=IntegerField())
    packs = Coalesce("packs", "packs_found", one_pack, output_field=IntegerField())
    cards = Coalesce("cards_per_pack", "cards_found", output_field=IntegerField())
    return products.annotate(cards_total=ExpressionWrapper(packs * cards, output_field=IntegerField())).annotate(
        price_per_card=ExpressionWrapper(Cast("lowest_price", FloatField()) / F("cards_total"), output_field=FloatField()))


def apply_languages(products, codes):
    """Keep products in any of the chosen languages. No choice means every language."""
    codes = [c for c in codes if c in dict(LANGUAGES)]
    if not codes:
        return products
    q = Q()
    for code in codes:
        if code == "en":
            q |= Q(language=languages.ENGLISH)
        elif code == "other":
            q |= Q(language__in=languages.OTHER)
        elif code == "zh":
            q |= Q(language__in=languages.CHINESE_FAMILY)
        else:
            q |= Q(language=code)
    return products.filter(q)


def order_products(products, sort, first=None):
    """Order a ``for_lists()`` queryset by one of SORTS. ``first`` goes before everything."""
    has_price = Case(When(lowest_price__isnull=True, then=Value(1)), default=Value(0), output_field=IntegerField())
    products = products.annotate(has_price=has_price)
    if sort == "price":
        ordering = [F("lowest_price").asc(nulls_last=True), "name"]
    elif sort == "saving":
        buyable = Listing.objects.filter(
            fresh(), product=OuterRef("pk"), is_active=True, retailer__is_active=True,
            availability__in=Listing.BUYABLE, sanity__in=Listing.COUNTED,
        ).order_by("delivered_price").values("delivered_price")
        money = DecimalField(max_digits=9, decimal_places=2)
        products = products.annotate(
            second_price=Subquery(buyable[1:2], output_field=money),
            saving=ExpressionWrapper(F("second_price") - F("lowest_price"), output_field=money),
        )
        ordering = ["has_price", F("saving").desc(nulls_last=True), "name"]
    elif sort == "low":
        since = timezone.localdate() - timedelta(days=LOW_DAYS)
        history = (
            DailyLowestPrice.objects.filter(product=OuterRef("pk"), date__gte=since)
            .values("product").annotate(low=Min("price")).values("low")
        )
        products = products.annotate(low_90=Subquery(history, output_field=DecimalField(max_digits=9, decimal_places=2)))
        at_low = Case(
            When(lowest_price__isnull=False, low_90__isnull=False, lowest_price__lte=F("low_90"), then=Value(0)),
            default=Value(1), output_field=IntegerField(),
        )
        products = products.annotate(at_low=at_low)
        ordering = ["has_price", "at_low", F("lowest_price").asc(nulls_last=True), "name"]
    elif sort == "card":
        # Cheapest per card (the owner, 2 Nov 2026: for people who just like ripping packs); products whose cards
        # cannot be counted after those that can.
        products = with_card_price(products)
        ordering = ["has_price", F("price_per_card").asc(nulls_last=True), F("lowest_price").asc(nulls_last=True),
                    "name"]
    elif sort == "fair":
        # The Price check's verdict as stored each hour (fair_price.refresh_bands): lowest yet first, then good,
        # fair, above average and high, then products with no verdict; the cheaper first within each.
        ordering = ["has_price", F("price_band").desc(nulls_last=True), F("lowest_price").asc(nulls_last=True), "name"]
    elif sort == "best":
        # A search's Best match (catalogue/search.py apply_search, which annotates bulk; name_rank goes first): a
        # bulk lot after the everyday products, then the products more shops list, the ones people buy, so a
        # broad search does not lead with a £2,000 display or one of nine tin artworks, then the newest.
        ordering = ["has_price", "bulk", F("listing_count").desc(nulls_last=True), F("release").desc(nulls_last=True),
                    "name"]
    elif sort == "soonest":
        # Not offered in the sort menu: the Pre-orders tab's own order, the next to come out first.
        ordering = ["has_price", F("release").asc(nulls_last=True), "name"]
    else:
        ordering = ["has_price", F("release").desc(nulls_last=True), "name"]
    if first:
        ordering.insert(0, first)
    return products.order_by(*ordering)


def order_products_default(products, sort, default_ordering, first=None):
    """Like order_products, but a sort of "newest" uses the list's own ordering."""
    if sort == "newest":
        return products.annotate(
            has_price=Case(When(lowest_price__isnull=True, then=Value(1)), default=Value(0), output_field=IntegerField())
        ).order_by(*default_ordering)
    return order_products(products, sort, first=first)
