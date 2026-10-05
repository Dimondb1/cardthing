"""
Price records and the queries behind price drops, popular products and
price history.

Price importers should call ``record_check`` each time they check a
retailer. It updates the listing and today's lowest price for the product.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, DecimalField, ExpressionWrapper, F, FloatField, Min, OuterRef, Subquery
from django.utils import timezone

from .models import DailyLowestPrice, Listing, OutboundClick, Product, Restock, Retailer

# A listing that flips out and back within this long is one restock, not two.
RESTOCK_COLLAPSE = timedelta(hours=2)
# Marketplaces are many sellers, so their stock coming and going is not a shop restocking.
MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)
# The busiest restock hours are only worth printing once there are this many to count.
RESTOCK_PATTERN_MIN = 6


def record_check(listing, *, price, delivery_cost, availability, checked_at=None):
    checked_at = checked_at or timezone.now()
    fields = ["price", "delivery_cost", "availability", "last_checked"]
    was_in_stock = listing.availability == Listing.Availability.IN_STOCK
    restocked = availability == Listing.Availability.IN_STOCK and not was_in_stock and listing.pk and not listing._state.adding
    if restocked:
        listing.back_in_stock_at = checked_at
        fields.append("back_in_stock_at")
    listing.price = price
    listing.delivery_cost = delivery_cost
    listing.availability = availability
    listing.last_checked = checked_at
    listing.save(update_fields=fields)
    if restocked:
        record_restock(listing, checked_at)
    return update_daily_lowest(listing.product, date=timezone.localdate(checked_at))


def record_restock(listing, at):
    """Keep a Restock for this listing coming back, unless it only flickered or the shop is a marketplace."""
    if listing.retailer.source_type in MARKETPLACES:
        return None
    if Restock.objects.filter(listing=listing, at__gte=at - RESTOCK_COLLAPSE).exists():
        return None
    return Restock.objects.create(
        product_id=listing.product_id, retailer_id=listing.retailer_id, listing=listing, at=at, price=listing.total
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
    """Store the product's current cheapest price against ``date`` if it is lower."""
    date = date or timezone.localdate()
    lowest = Listing.objects.filter(product=product).buyable().aggregate(
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
    for product in Product.objects.active().with_prices().filter(lowest_price__isnull=False):
        record, created = DailyLowestPrice.objects.get_or_create(
            product=product, date=date, defaults={"price": product.lowest_price}
        )
        if not created and product.lowest_price < record.price:
            record.price = product.lowest_price
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
    return (
        products
        .annotate(previous_price=Subquery(previous, output_field=money))
        .filter(lowest_price__lt=F("previous_price"))
        .annotate(
            drop=ExpressionWrapper(F("previous_price") - F("lowest_price"), output_field=money),
            drop_ratio=ExpressionWrapper(
                (F("previous_price") - F("lowest_price")) * 1.0 / F("previous_price"),
                output_field=FloatField(),
            ),
        )
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
