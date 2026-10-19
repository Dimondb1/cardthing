"""
The drop tracker: sets still to come with a date the site trusts, soonest first, each with the cheapest
shop offers for its products, and the sets that came out in the last fortnight.

A drop is a set with a release date (always a full day: catalogue/releases.py writes a set's date only
from the publisher or two sources that agree, or the owner types it), or a set with no date whose accepted
announcement gives a month only. A month-only drop is shown as its month, never as a day. A Release row
that is not accepted is never read here, and nothing is shown that no source or the owner gave.

A set with neither, that a UK shop already takes pre-orders for and none sells from stock, is a drop too,
listed last as "Date not announced yet" (the rule the Coming soon rows on the home and game pages follow),
so the page every "See all" leads to holds every pre-order those rows show. It takes no reminder: with no
day and no month there is nothing to remind about and no point at which the reminder would end.

A set the owner or Claude marked as not sold in UK shops (ProductSet.not_in_shops: only at events or direct
from the publisher) is never a drop: no shop will list it, so it takes no reminder either.

A product with its own later release date (a collection that follows the set by months) is not part of
the set's drop: it is left off the card and out of the emails, the same date every list uses
(Product.effective_release_date). A month-only drop that a shop already sells from stock is out, the rule
the Coming soon list follows, so it is not shown as still to come and takes no reminder.

Offers are shops only: eBay and Amazon are left out, as on the Coming soon list, because their pre-orders
are resellers' guesses. Each product's offer is its cheapest current one, in stock or on pre-order, with a
confirmed delivered price before an item price whose delivery is not known, the order every list uses.

The page costs three queries however many drops there are: the sets, their products and those products'
offers.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db.models import Exists, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone

from .models import Listing, Product, ProductSet, Release, Retailer

MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)
RECENT_DAYS = 14          # a drop stays under Just dropped this long after its day
PRODUCTS_SHOWN = 4        # products listed on one drop card; the set page lists the rest
WEEK, MONTH, UNDATED = "week", "month", "undated"


def month_start(day):
    return day.replace(day=1)


def next_month(day):
    first = month_start(day)
    return (first + timedelta(days=32)).replace(day=1)


def drop_starts(day):
    """The moment a day-dated drop starts: midnight UK time at the start of its day."""
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_default_timezone())


def month_rows(outer="pk"):
    """The month an accepted, month-only announcement gives the set named by ``outer``, the publisher's first."""
    return (
        Release.objects.filter(
            product_set=OuterRef(outer), status=Release.Status.ACCEPTED, precision=Release.Precision.MONTH,
            release_date__isnull=False,
        )
        .order_by("-official", "-last_seen_at")
        .values("release_date")[:1]
    )


def with_month(sets):
    return sets.annotate(drop_month=Subquery(month_rows()))


def in_drop(product, day, month):
    """True when the product comes out with its set's drop: it has no date of its own, or its own date is not
    after the drop's day (or, for a month-only drop, not after its month)."""
    own = product.release_date
    if own is None:
        return True
    if day is not None:
        return own <= day
    return month is None or own < next_month(month)


def shop_offers():
    """Attach ``shop_offers`` to each product: its current shop listings, in stock or on pre-order, cheapest
    first. One query for every product."""
    return Prefetch(
        "listings",
        queryset=Listing.objects.buyable().exclude(retailer__source_type__in=MARKETPLACES)
        .select_related("retailer")
        .order_by("-delivery_known", "delivered_price", "retailer__name"),
        to_attr="shop_offers",
    )


def shop_preorders():
    """True when a UK shop (not eBay or Amazon) takes a current pre-order for one of the set's products."""
    return Exists(
        Listing.objects.buyable().filter(
            product__product_set=OuterRef("pk"), product__is_active=True,
            availability=Listing.Availability.PREORDER,
        ).exclude(retailer__source_type__in=MARKETPLACES)
    )


def drop_queryset(today):
    """Sets that drop from RECENT_DAYS ago onwards, whose announced month has not ended, or that have no date
    at all while a shop takes pre-orders, with their products and offers prefetched."""
    products = Product.objects.active().prefetch_related(shop_offers()).order_by("name")
    return (
        with_month(ProductSet.objects.filter(game__is_active=True, not_in_shops=False).select_related("game"))
        .annotate(shop_preorder=shop_preorders())
        .filter(
            Q(release_date__gte=today - timedelta(days=RECENT_DAYS))
            | Q(release_date__isnull=True, drop_month__gte=month_start(today))
            | (Q(release_date__isnull=True, shop_preorder=True)
               & (Q(drop_month__isnull=True) | Q(drop_month__lt=month_start(today))))
        )
        .prefetch_related(Prefetch("products", queryset=products, to_attr="drop_products"))
    )


@dataclass
class Offer:
    product: Product
    listing: Listing                  # the product's cheapest current offer, in stock or on pre-order
    preorder: Listing | None = None   # its cheapest pre-order, which may be dearer than an in-stock listing

    @property
    def other_preorder(self):
        """The cheapest pre-order when it is not already the listing shown, so the product list shows both."""
        return self.preorder if self.preorder is not None and self.preorder.pk != self.listing.pk else None


def offer_order(listing):
    """The order every list ranks shop offers in, the same as the Coming soon rows (web.views
    coming_soon_queryset): a confirmed delivered price first, then the shown price, then the listing."""
    return (not listing.delivery_known, listing.shown_price, listing.pk)


@dataclass
class Drop:
    """One set on the tracker, with what its card shows."""

    product_set: ProductSet
    day: date | None              # a full day, from the set's own date
    month: date | None            # the first of the month, for a month-only drop; neither for an undated one
    offers: list = field(default_factory=list)       # Offer per product a shop lists, cheapest first
    unlisted: int = 0             # the set's products no shop lists
    preorder_shops: int = 0
    in_stock: bool = False
    checked_at: datetime | None = None

    @property
    def pk(self):
        return self.product_set.pk

    @property
    def sort_day(self):
        return self.day or self.month

    @property
    def undated(self):
        """No day and no month: listed only while a shop takes pre-orders, and never reminded about."""
        return self.day is None and self.month is None

    def is_upcoming(self, today):
        """Still to come: a dated drop from today on, or a month-only or undated one no shop sells from stock
        yet."""
        if self.day is not None:
            return self.day >= today
        return not self.in_stock

    @property
    def best(self):
        """(product, listing) the card leads with: the cheapest pre-order across the set's products when a shop
        takes them (a product's pre-order counts even when another shop sells it from stock for less), else the
        cheapest offer of any kind, else None. The Coming soon rows pick the same listing."""
        preorders = [Offer(o.product, o.preorder) for o in self.offers if o.preorder is not None]
        if preorders:
            return min(preorders, key=lambda o: offer_order(o.listing))
        return min(self.offers, key=lambda o: offer_order(o.listing)) if self.offers else None

    def days_until(self, today):
        return (self.day - today).days if self.day else None

    @property
    def starts_at(self):
        return drop_starts(self.day) if self.day else None

    @property
    def shown_offers(self):
        return self.offers[:PRODUCTS_SHOWN]

    @property
    def product_count(self):
        return len(self.offers) + self.unlisted

    @property
    def more(self):
        """Products the card leaves to the set page."""
        return max(0, self.product_count - len(self.shown_offers))


def build(product_set, today):
    """A Drop from a set loaded by drop_queryset. An announced month that has ended without a day counts as no
    date at all."""
    month = product_set.drop_month
    if month is not None and month < month_start(today):
        month = None
    drop = Drop(product_set, product_set.release_date,
                None if product_set.release_date or month is None else month_start(month))
    preorder_shops = set()
    for product in product_set.drop_products:
        if not in_drop(product, drop.day, drop.month):
            continue
        if not product.shop_offers:
            drop.unlisted += 1
            continue
        preorder = min((listing for listing in product.shop_offers
                        if listing.availability == Listing.Availability.PREORDER), key=offer_order, default=None)
        drop.offers.append(Offer(product, product.shop_offers[0], preorder))
        for listing in product.shop_offers:
            if listing.availability == Listing.Availability.PREORDER:
                preorder_shops.add(listing.retailer_id)
            else:
                drop.in_stock = True
            if drop.checked_at is None or listing.last_checked > drop.checked_at:
                drop.checked_at = listing.last_checked
    drop.offers.sort(key=lambda o: (not o.listing.delivery_known, o.listing.shown_price, o.product.name))
    drop.preorder_shops = len(preorder_shops)
    return drop


def tracker(today=None, game=None):
    """(upcoming, recent): drops still to come, soonest first, month-only drops after the dated ones of their
    month and undated ones last; and drops of the last RECENT_DAYS days, newest first. A month-only or undated
    drop already in stock at a shop is in neither: it is out, but has no day to list it under. ``game`` keeps
    one game's."""
    today = today or timezone.localdate()
    sets = drop_queryset(today)
    if game is not None:
        sets = sets.filter(game=game)
    drops = [build(product_set, today) for product_set in sets]
    upcoming = sorted(
        (d for d in drops if d.is_upcoming(today)),
        key=lambda d: (d.undated, month_start(d.sort_day or today), d.day is None, d.sort_day or today,
                       d.product_set.name),
    )
    recent = sorted((d for d in drops if d.day is not None and d.day < today),
                    key=lambda d: (-d.day.toordinal(), d.product_set.name))
    return upcoming, recent


def group(upcoming, today):
    """[(key, first of month or None, drops)]: This week (dated within six days, even when that runs into
    next month), This month (the rest of this calendar month, month-only ones included), one group per
    later month, then the undated ones. Each keeps the order it is given."""
    week_end = today + timedelta(days=6)
    groups = {}
    for drop in upcoming:
        if drop.undated:
            key, month = (3, UNDATED), None
        elif drop.day is not None and drop.day <= week_end:
            key, month = (0, WEEK), None
        elif month_start(drop.sort_day) == month_start(today):
            key, month = (1, MONTH), None
        else:
            month = month_start(drop.sort_day)
            key = (2, month.isoformat())
        groups.setdefault(key, (month, []))[1].append(drop)
    return [(key[1], month, drops) for key, (month, drops) in sorted(groups.items())]


def upcoming_drop(product_set, today=None):
    """The set's drop when it is still to come and has a day or a month (a reminder can be asked for), else
    None. Two queries."""
    today = today or timezone.localdate()
    loaded = drop_queryset(today).filter(pk=product_set.pk).first()
    if loaded is None:
        return None
    drop = build(loaded, today)
    return drop if drop.is_upcoming(today) and not drop.undated else None
