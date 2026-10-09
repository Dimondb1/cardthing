"""
Price records and the queries behind price drops, popular products and
price history.

Price importers should call ``record_check`` each time they check a
retailer. It updates the listing and today's lowest price for the product.
"""

import logging
import time
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import OperationalError
from django.db.models import Count, DecimalField, ExpressionWrapper, F, FloatField, Min, OuterRef, Q, Subquery
from django.utils import timezone

from .models import DailyLowestPrice, Listing, OutboundClick, Product, Restock, Retailer, stale_cutoff
from .offers import MAX_REAL_PERCENT

# A listing that flips out and back within this long is one restock, not two.
RESTOCK_COLLAPSE = timedelta(hours=2)
# Marketplaces are many sellers, so their stock coming and going is not a shop restocking.
MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)
# The busiest restock hours are only worth printing once there are this many to count.
RESTOCK_PATTERN_MIN = 6
# How a visitor's small write tries again after a lock: three tries, pausing between them.
LOCK_ATTEMPTS = 3
LOCK_WAITS = (0.5, 1, 2)
# A lock reported sooner than this came from a stale snapshot, which SQLite reports at once instead
# of waiting its busy timeout; only that kind is tried again. A lock reported after the full busy
# wait (20 s) is raised, so a request never waits the busy timeout twice.
LOCK_QUICK_SECONDS = 1.0

logger = logging.getLogger(__name__)


def retry_locked(fn, attempts=LOCK_ATTEMPTS, waits=LOCK_WAITS):
    """Call ``fn`` and return its result, trying again when SQLite reports the database locked at once.

    SQLite already waits ``busy_timeout`` for another writer to finish. A write can still fail
    straight away when its snapshot went stale under it; that one is worth another try after a short
    pause. A lock that outlasted the busy wait, any other OperationalError and a lock that outlasts
    every attempt are raised.
    """
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            return fn()
        except OperationalError as exc:
            quick = time.monotonic() - started < LOCK_QUICK_SECONDS
            if not is_locked(exc) or not quick or attempt == attempts - 1:
                raise
            time.sleep(waits[min(attempt, len(waits) - 1)])


def is_locked(exc):
    return "database is locked" in str(exc)


@contextmanager
def drop_if_locked(what):
    """Count on a best effort basis: a count that cannot be saved because the database is locked is
    logged and dropped, so the visitor still gets the page or the shop. Other errors are raised."""
    try:
        yield
    except OperationalError as exc:
        if not is_locked(exc):
            raise
        logger.warning("%s not counted: %s", what, exc)


def record_check(listing, *, price, delivery_cost, availability, checked_at=None, new=False):
    """Save one check of a listing. ``delivery_cost`` None means the charge is not known: it is stored as
    unknown, never as free.

    A price of nothing or less is not a price (a shop opening a pre-order before pricing it, a deposit
    variant, or a page that lost its price); a shop never sells for nothing, so this holds whatever the
    stock says. Such a check can only take an existing listing out of stock: its stock and check time are
    saved, its price stays, with no restock and no history. It never makes a listing buyable or marks its
    old price as freshly checked, so that price ages out as it would without the check. A new listing is
    not created. Returns None in every case.

    The price is judged against the other shops (sanity.judge_product) when it, its delivery or the
    stock changed, when the listing is new (``new``, for one just created by the caller), when its last
    verdict was not OK, when it comes back from being out of date or when the owner's trust in it has
    run out, so an unchanged price costs no extra queries. It is judged before any restock is kept, so a
    price kept out of the comparison is never announced as back in stock.
    """
    from .sanity import trust_expiry

    checked_at = checked_at or timezone.now()
    new = new or not listing.pk or listing._state.adding
    if price is None or price <= 0:
        if listing.pk and not listing._state.adding and availability == Listing.Availability.OUT_OF_STOCK:
            moved = listing.availability != availability
            listing.availability = availability
            listing.last_checked = checked_at
            listing.save(update_fields=["availability", "last_checked"])
            if moved:
                # No longer a price the other shops are judged against.
                judge(listing, checked_at)
        return None
    cost = delivery_cost if delivery_cost is not None else Decimal("0.00")
    changed = new or (
        listing.price != price or listing.availability != availability
        or listing.delivery_known != (delivery_cost is not None) or listing.delivery_cost != cost
        or listing.sanity != Listing.Sanity.OK
        or (listing.last_checked is not None and listing.last_checked < stale_cutoff(checked_at))
        or (listing.trusted_at is not None and listing.trusted_at < trust_expiry(checked_at))
    )
    fields = ["price", "delivery_cost", "delivery_known", "availability", "last_checked"]
    was_in_stock = listing.availability == Listing.Availability.IN_STOCK
    restocked = availability == Listing.Availability.IN_STOCK and not was_in_stock and listing.pk and not listing._state.adding
    if restocked:
        listing.back_in_stock_at = checked_at
        fields.append("back_in_stock_at")
    listing.price = price
    listing.delivery_known = delivery_cost is not None
    listing.delivery_cost = cost
    listing.availability = availability
    listing.last_checked = checked_at
    listing.save(update_fields=fields)
    if changed:
        judge(listing, checked_at)
    if restocked and listing.sanity != Listing.Sanity.EXCLUDED:
        record_restock(listing, checked_at)
    return update_daily_lowest(listing.product, date=timezone.localdate(checked_at))


def judge(listing, now):
    """Judge the listing's product and copy the listing's own new verdict onto the instance."""
    from .sanity import judge_product

    verdict = judge_product(listing.product_id, now=now).get(listing.pk)
    if verdict is not None:
        listing.sanity, listing.sanity_reason, listing.sanity_ratio = verdict


def apply_delivery_rules(retailer):
    """Re-work every listing's delivery from the retailer's rules, after those rules change.

    Marketplaces are left alone: their delivery comes with each listing. Returns how many listings now
    have an unknown delivery charge.
    """
    if retailer.source_type in MARKETPLACES:
        return 0
    unknown = 0
    for listing in Listing.objects.filter(retailer=retailer):
        delivery = retailer.delivery_for(listing.price)
        known = delivery is not None
        cost = delivery if known else Decimal("0.00")
        unknown += not known
        if listing.delivery_known != known or listing.delivery_cost != cost:
            Listing.objects.filter(pk=listing.pk).update(delivery_cost=cost, delivery_known=known)
    from .signals import clear_list_caches

    clear_list_caches(force=True)
    return unknown


def record_restock(listing, at):
    """Keep a Restock for this listing coming back, unless it only flickered or the shop is a marketplace."""
    if listing.retailer.source_type in MARKETPLACES:
        return None
    if Restock.objects.filter(listing=listing, at__gte=at - RESTOCK_COLLAPSE).exists():
        return None
    return Restock.objects.create(
        product_id=listing.product_id, retailer_id=listing.retailer_id, listing=listing, at=at, price=listing.total,
        delivery_known=listing.delivery_known,
    )


def hour_label(hour):
    if hour == 0:
        return "midnight"
    if hour == 12:
        return "midday"
    return f"{hour % 12}{'am' if hour < 12 else 'pm'}"


def restock_summary(product, days=30, pattern_days=90, now=None):
    """What the product page says about restocks, or None when none has been seen.

    ``count`` and ``latest`` cover the last ``days``; ``latest`` falls back to
    the last restock ever. ``band`` is the two-hour window most restocks of the
    last ``pattern_days`` landed in, as (start label, end label), only once
    there are enough to mean something.
    """
    now = now or timezone.now()
    recent = list(
        Restock.objects.filter(product=product, at__gte=now - timedelta(days=pattern_days))
        .select_related("retailer").order_by("-at")
    )
    in_window = [r for r in recent if r.at >= now - timedelta(days=days)]
    latest = in_window[0] if in_window else (recent[0] if recent else None)
    if latest is None:
        latest = Restock.objects.filter(product=product).select_related("retailer").order_by("-at").first()
        if latest is None:
            return None
    band = None
    if len(recent) >= RESTOCK_PATTERN_MIN:
        hours = [0] * 24
        for restock in recent:
            hours[timezone.localtime(restock.at).hour] += 1
        # The fullest two-hour window; on a tie, the one that starts in the busier hour.
        start = max(range(24), key=lambda h: (hours[h] + hours[(h + 1) % 24], hours[h], -h))
        band = (hour_label(start), hour_label((start + 2) % 24))
    return {
        "count": len(in_window), "days": days, "latest": latest,
        "latest_date": timezone.localdate(latest.at), "band": band,
    }


def restock_log(days=7, per_day=30, now=None):
    """Restocks of the last ``days`` grouped by day, newest first: [(date, [Restock, ...]), ...].

    Each Restock carries ``still_in_stock`` so the page can say when a shop has sold out again.
    """
    now = now or timezone.now()
    rows = (
        Restock.objects.filter(at__gte=now - timedelta(days=days), product__is_active=True, retailer__is_active=True)
        # A price since kept out of the comparison is not repeated here.
        .exclude(listing__sanity=Listing.Sanity.EXCLUDED)
        .select_related("product__game", "retailer", "listing")
        .order_by("-at")
    )
    grouped = {}
    for restock in rows:
        listing = restock.listing
        restock.still_in_stock = bool(listing and listing.availability == Listing.Availability.IN_STOCK and listing.is_active)
        day = timezone.localtime(restock.at).date()
        bucket = grouped.setdefault(day, [])
        if len(bucket) < per_day:
            bucket.append(restock)
    return sorted(grouped.items(), key=lambda item: item[0], reverse=True)


def back_in_stock(limit=8, hours=None):
    """Products a shop has just restocked, newest first, as (product, listing)."""
    hours = hours or settings.RIPRAPTOR_RESTOCK_HOURS
    since = timezone.now() - timedelta(hours=hours)
    rows = (
        Listing.objects.live()
        .filter(availability=Listing.Availability.IN_STOCK, back_in_stock_at__gte=since)
        .exclude(sanity=Listing.Sanity.EXCLUDED)
        .select_related("retailer")
        .order_by("-back_in_stock_at")
    )
    seen, picked = set(), []
    for listing in rows:
        if listing.product_id in seen:
            continue
        seen.add(listing.product_id)
        picked.append(listing)
        if len(picked) >= limit:
            break
    products = {p.pk: p for p in Product.objects.for_lists().filter(pk__in=[l.product_id for l in picked])}
    return [(products[l.product_id], l) for l in picked if l.product_id in products]


def update_daily_lowest(product, date=None):
    """Store the product's current cheapest delivered price against ``date`` if it is lower.

    Only confirmed delivered prices count: an item price without its delivery would make a false low.
    """
    date = date or timezone.localdate()
    lowest = Listing.objects.filter(product=product, delivery_known=True).buyable().aggregate(
        lowest=Min("delivered_price")
    )["lowest"]
    if lowest is None:
        return None
    record, created = DailyLowestPrice.objects.get_or_create(
        product=product, date=date, defaults={"price": lowest}
    )
    if not created and lowest < record.price:
        record.price = lowest
        record.save(update_fields=["price"])
    return record


def snapshot_all(date=None):
    """Record today's lowest price for every product. Run once a day."""
    date = date or timezone.localdate()
    count = 0
    for product in Product.objects.active().with_prices().filter(lowest_known__isnull=False):
        record, created = DailyLowestPrice.objects.get_or_create(
            product=product, date=date, defaults={"price": product.lowest_known}
        )
        if not created and product.lowest_known < record.price:
            record.price = product.lowest_known
            record.save(update_fields=["price"])
        count += 1
    return count


def price_drops(limit=6, days=None, today=None, game=None):
    """Products whose cheapest delivered price is lower than ``days`` ago, optionally within one game."""
    days = days or settings.RIPRAPTOR_TRENDING_DAYS
    today = today or timezone.localdate()
    target = today - timedelta(days=days)
    previous = (
        DailyLowestPrice.objects.filter(
            product=OuterRef("pk"),
            date__lte=target,
            date__gt=target - timedelta(days=days),
        )
        .order_by("-date")
        .values("price")[:1]
    )
    money = DecimalField(max_digits=9, decimal_places=2)
    products = Product.objects.for_lists()
    if game is not None:
        products = products.filter(game=game)
    doubtful = Q(
        listings__is_active=True, listings__retailer__is_active=True, listings__last_checked__gte=stale_cutoff(),
        listings__availability__in=Listing.BUYABLE, listings__delivery_known=True,
        listings__sanity=Listing.Sanity.DOUBTFUL,
    )
    return (
        products
        .annotate(
            previous_price=Subquery(previous, output_field=money),
            doubtful_low=Min("listings__delivered_price", filter=doubtful),
        )
        # Drops are measured on confirmed delivered prices only, like the history they come from.
        .filter(lowest_known__isnull=False, lowest_known=F("lowest_price"), lowest_price__lt=F("previous_price"))
        # A doubtful cheapest price is not news until it is confirmed.
        .filter(Q(doubtful_low__isnull=True) | Q(doubtful_low__gt=F("lowest_known")))
        .annotate(
            drop=ExpressionWrapper(F("previous_price") - F("lowest_price"), output_field=money),
            drop_ratio=ExpressionWrapper(
                (F("previous_price") - F("lowest_price")) * 1.0 / F("previous_price"),
                output_field=FloatField(),
            ),
        )
        # A fall this large is a wrong product link appearing, not a price drop, as with savings.
        .filter(drop_ratio__lte=MAX_REAL_PERCENT / 100)
        .order_by("-drop_ratio", "name")[:limit]
    )


def previous_price_map(product_ids, days=None, today=None):
    """{product id: cheapest price recorded about ``days`` ago}, for movement labels."""
    days = days or settings.RIPRAPTOR_TRENDING_DAYS
    today = today or timezone.localdate()
    target = today - timedelta(days=days)
    previous = {}
    rows = (
        DailyLowestPrice.objects.filter(
            product_id__in=list(product_ids), date__lte=target, date__gt=target - timedelta(days=days)
        )
        .order_by("product_id", "-date")
        .values_list("product_id", "price")
    )
    for pk, price in rows:
        previous.setdefault(pk, price)
    return previous


def popular(limit=8, days=None):
    """Products with the most clicks through to a retailer recently."""
    days = days or settings.RIPRAPTOR_TRENDING_DAYS
    since = timezone.now() - timedelta(days=days)
    top = (
        OutboundClick.objects.filter(created_at__gte=since)
        .values("product")
        .annotate(clicks=Count("id"))
        .order_by("-clicks", "product")[: limit * 2]
    )
    ids = [row["product"] for row in top]
    products = {product.pk: product for product in Product.objects.for_lists().filter(pk__in=ids)}
    ordered = [products[pk] for pk in ids if pk in products]
    return ordered[:limit]


def history(product, days=None, today=None):
    """[(date, price), ...] for the last ``days`` days, oldest first."""
    days = days or settings.RIPRAPTOR_HISTORY_DAYS
    today = today or timezone.localdate()
    start = today - timedelta(days=days - 1)
    return list(
        product.daily_prices.filter(date__gte=start, date__lte=today)
        .order_by("date")
        .values_list("date", "price")
    )


def last_known_price(product):
    return product.daily_prices.order_by("-date").first()
