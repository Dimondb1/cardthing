"""
Shared filters and sort orders for product lists: the browse pages and the
swipe deck use the same ones so a choice means the same thing everywhere.
"""

from datetime import timedelta

from django.db.models import Case, DecimalField, ExpressionWrapper, F, IntegerField, Min, OuterRef, Q, Subquery, Value, When
from django.utils import timezone

from .models import DailyLowestPrice, Listing, stale_cutoff

# Language choices. "English" means the name says no other language.
LANGUAGES = [
    ("en", "English"),
    ("ja", "Japanese"),
    ("zh", "Chinese"),
    ("ko", "Korean"),
    ("other", "Other languages"),
]
LANGUAGE_WORDS = {
    "ja": ["japanese"],
    "zh": ["chinese"],
    "ko": ["korean"],
    "other": ["german", "french", "italian", "spanish", "portuguese", "thai", "russian", "indonesian"],
}

SORTS = [
    ("newest", "Newest first"),
    ("price", "Lowest price"),
    ("saving", "Biggest saving"),
    ("low", "Lowest in 90 days"),
]
LOW_DAYS = 90


def language_q(code):
    words = LANGUAGE_WORDS.get(code, [])
    q = Q()
    for word in words:
        q |= Q(name__icontains=word)
    return q


def apply_languages(products, codes):
    """Keep products in any of the chosen languages. No choice means every language."""
    codes = [c for c in codes if c in dict(LANGUAGES)]
    if not codes:
        return products
    q = Q()
    for code in codes:
        if code == "en":
            foreign = Q()
            for other in LANGUAGE_WORDS.values():
                for word in other:
                    foreign |= Q(name__icontains=word)
            q |= ~foreign
        else:
            q |= language_q(code)
    return products.filter(q)


def order_products(products, sort, first=None):
    """Order a ``for_lists()`` queryset by one of SORTS. ``first`` goes before everything."""
    has_price = Case(When(lowest_price__isnull=True, then=Value(1)), default=Value(0), output_field=IntegerField())
    products = products.annotate(has_price=has_price)
    if sort == "price":
        ordering = [F("lowest_price").asc(nulls_last=True), "name"]
    elif sort == "saving":
        buyable = Listing.objects.filter(
            product=OuterRef("pk"), is_active=True, retailer__is_active=True,
            last_checked__gte=stale_cutoff(), availability__in=Listing.BUYABLE,
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
