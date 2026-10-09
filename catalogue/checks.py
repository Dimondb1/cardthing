"""
What the owner should look at, for the Things to check page in admin: prices
the other shops make doubtful or impossible, wrong matches behind impossible
savings, products that look like duplicates, and shops whose delivery charge
is not known. Each comes with its fix.
"""

from datetime import timedelta

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import offers
from .models import Listing, OutboundClick, Product, Retailer
from .pricing import MARKETPLACES

# How many doubtful or excluded prices the page lists at once.
SANITY_ROWS = 50
# Doubtful prices of the products people clicked through for in this many days come first.
CLICK_DAYS = 7


def judged():
    """Listings that are judged and shown: a hidden listing, a switched-off shop or product waits until it is back."""
    return Listing.objects.live().filter(product__is_active=True)


def sanity_counts():
    """{'doubtful': n, 'excluded': n} over every judged listing, in one query, for the section headings."""
    return judged().aggregate(
        doubtful=Count("pk", filter=Q(sanity=Listing.Sanity.DOUBTFUL)),
        excluded=Count("pk", filter=Q(sanity=Listing.Sanity.EXCLUDED)),
    )


def doubtful_prices(now=None):
    """Listings whose price the other shops make doubtful, most clicked products first, then oldest verdict."""
    since = (now or timezone.now()) - timedelta(days=CLICK_DAYS)
    clicks = (
        OutboundClick.objects.filter(product=OuterRef("product_id"), created_at__gte=since)
        .order_by().values("product").annotate(n=Count("id")).values("n")
    )
    return list(
        judged().filter(sanity=Listing.Sanity.DOUBTFUL)
        .annotate(clicks=Coalesce(Subquery(clicks, output_field=IntegerField()), Value(0)))
        .select_related("product", "retailer")
        .order_by("-clicks", "sanity_at", "pk")[:SANITY_ROWS]
    )


def excluded_prices():
    """Listings kept out of the comparison because they are far from every other shop, newest verdict first."""
    return list(
        judged().filter(sanity=Listing.Sanity.EXCLUDED)
        .select_related("product", "retailer")
        .order_by("-sanity_at", "pk")[:SANITY_ROWS]
    )


def wrong_matches():
    """[(product, summary)] where the cheapest price is doubtful or too far under the next to be the same thing.

    Products with a doubtful price come from doubtful_prices; the saving cap still catches a gap between
    prices that have not been judged yet.
    """
    doubtful = {listing.product_id for listing in doubtful_prices()}
    products = Product.objects.for_lists().filter(lowest_price__isnull=False).prefetch_related(offers.buyable_prefetch())
    rows = []
    for product in products.order_by("name"):
        summary = offers.summarise(product)
        if (summary.suspect or product.pk in doubtful) and summary.best and summary.second:
            rows.append((product, summary))
    return rows


def duplicates():
    """[(keep, [others])] that the loose rule would merge. Strict duplicates are merged every hour already."""
    from .management.commands.merge_duplicates import duplicate_groups

    return duplicate_groups(loose=True)


def unknown_delivery_shops():
    """Shops, not marketplaces, with prices whose delivery charge is not known, most affected first."""
    return list(
        Retailer.objects.filter(is_active=True)
        .exclude(source_type__in=MARKETPLACES)
        .annotate(unknown=Count("listings", filter=Q(listings__delivery_known=False, listings__is_active=True)))
        .filter(unknown__gt=0)
        .order_by("-unknown", "name")
    )
