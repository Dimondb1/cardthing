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

from .models import DailyLowestPrice, Listing, OutboundClick, Product


def record_check(listing, *, price, delivery_cost, availability, checked_at=None):
    checked_at = checked_at or timezone.now()
    fields = ["price", "delivery_cost", "availability", "last_checked"]
    was_in_stock = listing.availability == Listing.Availability.IN_STOCK
    if availability == Listing.Availability.IN_STOCK and not was_in_stock and listing.pk and not listing._state.adding:
        listing.back_in_stock_at = checked_at
        fields.append("back_in_stock_at")
    listing.price = price
    listing.delivery_cost = delivery_cost
    listing.availability = availability
    listing.last_checked = checked_at
    listing.save(update_fields=fields)
    return update_daily_lowest(listing.product, date=timezone.localdate(checked_at))


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


def price_drops(limit=6, days=None, today=None):
    """Products whose cheapest delivered price is lower than ``days`` ago."""
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
    return (
        Product.objects.for_lists()
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
