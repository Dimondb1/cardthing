"""
What the owner should look at, for the Things to check page in admin: wrong
matches behind impossible savings, products that look like duplicates, and
shops whose delivery charge is not known. Each comes with its fix.
"""

from django.db.models import Count, Q

from . import offers
from .models import Product, Retailer
from .pricing import MARKETPLACES


def wrong_matches():
    """[(product, summary)] where the cheapest price is too far under the next to be the same thing."""
    products = Product.objects.for_lists().filter(lowest_price__isnull=False).prefetch_related(offers.buyable_prefetch())
    rows = []
    for product in products.order_by("name"):
        summary = offers.summarise(product)
        if summary.suspect:
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
