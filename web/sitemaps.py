"""
The sitemap: the pages worth a search engine's visit, each with the time its content last really changed.

A product is listed while it is worth finding: a shop has it in stock or on pre-order now, it comes out later,
or a shop priced it in the last RECENT_DAYS days. The long tail that no shop has priced for longer stays on
the site and reachable through its set and game pages, which link every product; it is only left out of the
list, so a crawler spends its visits on pages with a price.

lastmod is the latest of the product's own edits (Product.updated_at, which moves only when a field the page
shows is saved), its set's (ProductSet.updated_at: the set's name, code and date are on the product page too)
and the last change of any shop's price, delivery or stock (Listing.changed_at, which a check that finds
nothing new leaves alone). A hidden listing, or one of a shop switched off, counts by when it was hidden or
switched off: that took a shop off the page, and nothing it does while hidden is stamped. Before changed_at
existed the only such record is the last time a shown shop came back into stock. changefreq and priority are
left out: Google ignores them.

Each section costs two queries per sitemap page, however many products there are: a count and the page.
"""

from datetime import timedelta

from django.contrib.sitemaps import Sitemap
from django.db.models import Case, Exists, F, Max, OuterRef, Q, Subquery, When
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
    """A subquery: the latest change among ``listings`` (filtered on an OuterRef and all sharing one ``group``
    value), or None when none has a recorded change. Hidden listings count, by when they were hidden; the
    last restock stands in only for a shown listing with no recorded change."""
    restock = Case(When(is_active=True, retailer__is_active=True, then="back_in_stock_at"))
    changed = Max(Coalesce("changed_at", restock))
    return Subquery(listings.order_by().values(group).annotate(at=changed).values("at")[:1])


def indexable_products(now=None):
    """Active products worth listing, by pk, with ``listing_changed`` (the last shown listing change, or None)
    and ``set_changed`` (when their set was last edited, or None)."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    buyable = Listing.objects.buyable().filter(product=OuterRef("pk"))
    priced = DailyLowestPrice.objects.filter(product=OuterRef("pk"), date__gte=today - timedelta(days=RECENT_DAYS))
    coming = Q(release_date__gt=today) | Q(release_date__isnull=True, product_set__release_date__gt=today)
    return (
        Product.objects.active()
        .filter(Exists(buyable) | coming | Exists(priced))
        .annotate(
            listing_changed=last_listing_change(Listing.objects.filter(product=OuterRef("pk")), "product"),
            set_changed=F("product_set__updated_at"),
        )
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
        return latest(product.updated_at, product.set_changed, product.listing_changed)


class GameSitemap(Sitemap):
    def items(self):
        return Game.objects.filter(is_active=True)


class SetSitemap(Sitemap):
    """A set page shows its name, code and date and lists its products with their prices and stock, so it
    changed when it was edited or when one of them changed (a product filed under it included)."""

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
        return latest(product_set.updated_at, product_set.edited, product_set.listing_changed)


class PageSitemap(Sitemap):
    def items(self):
        return ["web:home", "web:deals", "web:drops", "web:games", "web:about", "web:list_shop", "web:terms"]

    def location(self, name):
        return reverse(name)


SITEMAPS = {
    "products": ProductSitemap,
    "games": GameSitemap,
    "sets": SetSitemap,
    "pages": PageSitemap,
}
