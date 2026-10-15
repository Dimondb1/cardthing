"""
The sitemap: the pages worth a search engine's visit, each with the time its content last really changed.

A product is listed while it is worth finding: a shop has it in stock or on pre-order now, it comes out later,
or a shop priced it in the last RECENT_DAYS days. The long tail that no shop has priced for longer stays on
the site and reachable through its set and game pages, which link every product; it is only left out of the
list, so a crawler spends its visits on pages with a price.

lastmod is the latest of the product's own edits (Product.updated_at, which moves only when a field the page
shows is saved) and the last change of any shown shop's price, delivery or stock (Listing.changed_at, which a
check that finds nothing new leaves alone). Before changed_at existed the only such record is the last time a
shop came back into stock. changefreq and priority are left out: Google ignores them.

Each section costs two queries per sitemap page, however many products there are: a count and the page.
"""

from datetime import timedelta

from django.contrib.sitemaps import Sitemap
from django.db.models import Exists, Max, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from catalogue.models import DailyLowestPrice, Game, Listing, Product, ProductSet

# A product priced in this window keeps its place. In the 3 October snapshot a quarter of the products a shop
# priced in the four days before had sold out everywhere by the last day; most UK restocks of sealed product
# come within weeks, and the product page compares with a month ago, so a month keeps a page listed through
# a sell-out without listing what no shop has priced for longer.
RECENT_DAYS = 30


def last_listing_change(listings, group):
    """A subquery: the latest change of a shown listing among ``listings`` (filtered on an OuterRef and all
    sharing one ``group`` value), or None when none has a recorded change."""
    shown = listings.filter(is_active=True, retailer__is_active=True).order_by()
    changed = Max(Coalesce("changed_at", "back_in_stock_at"))
    return Subquery(shown.values(group).annotate(at=changed).values("at")[:1])


def indexable_products(now=None):
    """Active products worth listing, with ``listing_changed`` (the last shown listing change, or None), by pk."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    buyable = Listing.objects.buyable().filter(product=OuterRef("pk"))
    priced = DailyLowestPrice.objects.filter(product=OuterRef("pk"), date__gte=today - timedelta(days=RECENT_DAYS))
    coming = Q(release_date__gt=today) | Q(release_date__isnull=True, product_set__release_date__gt=today)
    return (
        Product.objects.active()
        .filter(Exists(buyable) | coming | Exists(priced))
        .annotate(listing_changed=last_listing_change(Listing.objects.filter(product=OuterRef("pk")), "product"))
        .only("slug", "updated_at")
        .order_by("pk")
    )


def latest(*moments):
    """The latest of the moments given, ignoring None; None when there are none."""
    return max((moment for moment in moments if moment is not None), default=None)


class ProductSitemap(Sitemap):
    def items(self):
        return indexable_products()

    def lastmod(self, product):
        return latest(product.updated_at, product.listing_changed)


class GameSitemap(Sitemap):
    def items(self):
        return Game.objects.filter(is_active=True)


class SetSitemap(Sitemap):
    """A set page lists its products with their prices and stock, so it changed when one of them did."""

    def items(self):
        products = Product.objects.active().filter(product_set=OuterRef("pk")).order_by()
        listings = Listing.objects.filter(product__product_set=OuterRef("pk"), product__is_active=True)
        return (
            ProductSet.objects.filter(game__is_active=True)
            .select_related("game")
            .annotate(
                edited=Subquery(products.values("product_set").annotate(at=Max("updated_at")).values("at")[:1]),
                listing_changed=last_listing_change(listings, "product__product_set"),
            )
            .order_by("pk")
        )

    def lastmod(self, product_set):
        return latest(product_set.edited, product_set.listing_changed)


class PageSitemap(Sitemap):
    def items(self):
        return ["web:home", "web:deals", "web:new", "web:games", "web:about", "web:terms"]

    def location(self, name):
        return reverse(name)


SITEMAPS = {
    "products": ProductSitemap,
    "games": GameSitemap,
    "sets": SetSitemap,
    "pages": PageSitemap,
}
