"""How popular a set still to come is, for the order of the home page's Coming soon rows.

Built only from counts the site already keeps, none of them about a person: product page views
(DailyPageView, robots never counted), clicks through to a shop (OutboundClick, robots never counted),
confirmed drop reminders waiting for the set (DropAlert) and the number of UK shops taking pre-orders.

    interest = views + 4 x clicks + 8 x reminders + 3 x shops
    soonness = 0.5 ^ (days until the drop / 21)          (an undated set counts as 28 days away)
    score    = (1 + interest) x soonness

Views and clicks are the last seven days. A click is worth four views because it is someone ready to buy;
a reminder eight, because someone gave an email address for it; a shop taking pre-orders three, because
it is a set a visitor can act on now. Soonness halves every three weeks, so a set six weeks away needs
about four times the interest of one out this week to rank above it, and one three months away about
twenty times. Ties go to the sooner date, then the name.
"""

from datetime import timedelta

from django.db.models import Count, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import DailyPageView, DropAlert, OutboundClick, Product, ProductSet

DAYS = 7
WEIGHTS = {"views": 1, "clicks": 4, "reminders": 8, "shops": 3}
HALF_LIFE_DAYS = 21
UNDATED_DAYS = 28


def interest_counts(set_ids, today=None):
    """{set id: (views, clicks, reminders)} for these sets, in one query."""
    if not set_ids:
        return {}
    today = today or timezone.localdate()
    since_day = today - timedelta(days=DAYS - 1)
    since = timezone.now() - timedelta(days=DAYS)
    slugs = Product.objects.filter(product_set=OuterRef(OuterRef("pk"))).values("slug")
    views = (
        DailyPageView.objects.filter(kind=DailyPageView.Kind.PRODUCT, date__gte=since_day, key__in=slugs)
        .order_by().values("kind").annotate(n=Sum("hits")).values("n")
    )
    clicks = (
        OutboundClick.objects.filter(product__product_set=OuterRef("pk"), created_at__gte=since)
        .order_by().values("product__product_set").annotate(n=Count("id")).values("n")
    )
    reminders = (
        DropAlert.objects.filter(product_set=OuterRef("pk"), confirmed_at__isnull=False)
        .order_by().values("product_set").annotate(n=Count("id")).values("n")
    )
    zero = Value(0, output_field=IntegerField())
    rows = (
        ProductSet.objects.filter(pk__in=set_ids)
        .order_by()   # no join or sort: the rows become a dict
        .annotate(
            views=Coalesce(Subquery(views[:1], output_field=IntegerField()), zero),
            clicks=Coalesce(Subquery(clicks[:1], output_field=IntegerField()), zero),
            reminders=Coalesce(Subquery(reminders[:1], output_field=IntegerField()), zero),
        )
        .values_list("pk", "views", "clicks", "reminders")
    )
    return {pk: (views, clicks, reminders) for pk, views, clicks, reminders in rows}


def score(views, clicks, reminders, shops, release_date, today):
    """The formula in this module's docstring."""
    interest = (WEIGHTS["views"] * views + WEIGHTS["clicks"] * clicks
                + WEIGHTS["reminders"] * reminders + WEIGHTS["shops"] * shops)
    days = UNDATED_DAYS if release_date is None else max(0, (release_date - today).days)
    return (1 + interest) * 0.5 ** (days / HALF_LIFE_DAYS)


def rank_sets(rows, today=None):
    """These coming soon rows (each with ``preorder_count``), most popular first, each given ``popularity``."""
    today = today or timezone.localdate()
    counts = interest_counts([row.pk for row in rows], today)
    for row in rows:
        views, clicks, reminders = counts.get(row.pk, (0, 0, 0))
        row.popularity = round(score(views, clicks, reminders, row.preorder_count or 0, row.release_date, today), 3)
    far = today + timedelta(days=36500)
    return sorted(rows, key=lambda row: (-row.popularity, row.release_date or far, row.name))
