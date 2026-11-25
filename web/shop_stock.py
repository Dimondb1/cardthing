"""
Shop stock: one shop's products, prices and stock on one Admin page, and a list of every shop to pick from. Staff
only, never for editors. The owner, 25 Nov 2026: "Is there a place in admin to view a shops inventory and current
stock levels? If not can you create it?"

Shops publish whether a product can be bought, not how many they hold, so a stock level here is a state: in stock,
pre-order, out of stock or sold by a draw. A listing not checked within its window (stale_cutoff, eBay's shorter one
for eBay) keeps the state it was last read with, and is shown apart as not checked lately: the site does not count it.
A hidden listing (switched off, or its product hidden or merged away) is shown only under its own filter.

Queries are fixed whatever the shop's size: the shop, the counts, the page's count and rows, and the cheapest shop of
each product on the page (shop_report.lead_rows).
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue.models import EBAY, Listing, Retailer, ebay_stale_cutoff, stale_cutoff
from guides.access import not_for_editors

from .shop_report import lead_rows

PER_PAGE = 100
SEARCH_MOST = 80

A = Listing.Availability
# The filters, in the order the page shows them: (code, label). "listed" is everything the site can show.
FILTERS = [
    ("listed", "All listed"),
    ("in_stock", "In stock"),
    ("preorder", "Pre-order"),
    ("out_of_stock", "Out of stock"),
    ("lottery", "Sold by draw"),
    ("stale", "Not checked lately"),
    ("hidden", "Hidden"),
]
CODES = {code for code, _label in FILTERS}
# Shown even at nought: the stock states the owner asked about. The rest appear only when there are some.
ALWAYS = {"listed", "in_stock", "preorder", "out_of_stock"}
MINE = "This shop"
STOCK_WORDS = {A.IN_STOCK: "In stock", A.PREORDER: "Pre-order", A.OUT_OF_STOCK: "Out of stock",
               A.LOTTERY: "Sold by draw"}


def checked_since(now, shop_field="retailer__source_type", checked_field="last_checked"):
    """A Q for a listing checked within its window: eBay's for an eBay one, the shops' for the rest. Amazon's is the
    shops' window too: its price never counts on the site, but whether it was read lately is still worth seeing."""
    return (Q(**{shop_field: EBAY, f"{checked_field}__gte": ebay_stale_cutoff(now)})
            | (~Q(**{shop_field: EBAY}) & Q(**{f"{checked_field}__gte": stale_cutoff(now)})))


def shown_q(prefix=""):
    """A Q for a listing the site can show: switched on, of a product that is."""
    return Q(**{f"{prefix}is_active": True, f"{prefix}product__is_active": True})


def filter_q(code, now):
    """A Q for the listings under the filter ``code``."""
    if code == "hidden":
        return ~shown_q()
    current = checked_since(now)
    if code == "stale":
        return shown_q() & ~current
    if code == "listed":
        return shown_q()
    return shown_q() & current & Q(availability=code)


def counts_for(shop, now):
    """{filter code: listings} for one shop, in one query."""
    return Listing.objects.filter(retailer=shop).aggregate(
        **{code: Count("pk", filter=filter_q(code, now)) for code in CODES})


def shop_rows(search, now):
    """Every shop, its listed and in-stock counts, A to Z, in one query. ``search`` narrows by name or address."""
    shown, current = shown_q("listings__"), checked_since(now, "source_type", "listings__last_checked")
    shops = Retailer.objects.annotate(
        listed=Count("listings", filter=shown),
        in_stock=Count("listings", filter=shown & current & Q(listings__availability=A.IN_STOCK)),
        preorder=Count("listings", filter=shown & current & Q(listings__availability=A.PREORDER)),
    ).order_by("-is_active", Lower("name"))
    if search:
        shops = shops.filter(Q(name__icontains=search) | Q(website__icontains=search))
    return shops


def listings_for(shop, code, search, now):
    """The shop's listings under the filter ``code``: in stock first, then pre-order, out of stock, by draw and not
    checked lately, each A to Z by product. The product comes in the same query."""
    current = checked_since(now)
    rows = (Listing.objects.filter(retailer=shop).filter(filter_q(code, now))
            .select_related("product")
            .annotate(place=Case(
                When(~shown_q(), then=Value(6)),
                When(~current, then=Value(5)),
                When(availability=A.IN_STOCK, then=Value(0)),
                When(availability=A.PREORDER, then=Value(1)),
                When(availability=A.OUT_OF_STOCK, then=Value(2)),
                default=Value(3), output_field=IntegerField()))
            .order_by("place", Lower("product__name"), "pk"))
    if search:
        rows = rows.filter(Q(product__name__icontains=search) | Q(title__icontains=search) | Q(url__icontains=search))
    return rows


def describe(listing, place, lead, shop):
    """What one row of the page says about ``listing``. ``lead`` is its product's cheapest shop now (lead_rows), or
    None when no shop sells it now."""
    if place == 6:
        state, tone = ("Hidden: its product is hidden or merged" if listing.is_active else "Hidden from the site"), "quiet"
    elif place == 5:
        state, tone = f"{STOCK_WORDS.get(listing.availability, listing.get_availability_display())} when last read", "quiet"
    else:
        state = listing.get_closed_why_display() if listing.closed_why else STOCK_WORDS.get(listing.availability, "")
        tone = {A.IN_STOCK: "good", A.PREORDER: "ahead"}.get(listing.availability, "off")
    if not listing.delivery_known:
        delivery = "delivery unknown"
    elif listing.delivery_cost == 0:
        delivery = "free delivery"
    else:
        delivery = f"£{listing.delivery_cost} delivery"
    if not listing.product.is_active:
        cheapest = ""
    elif lead is None:
        cheapest = "Not for sale at any shop now"
    elif lead[0] == shop.pk:
        cheapest = MINE
    else:
        _shop, lead_name, lead_price, lead_known = lead
        cheapest = f"£{lead_price}{'' if lead_known else ' plus delivery'} at {lead_name}"
    kept = ""
    if listing.sanity == Listing.Sanity.EXCLUDED:
        kept = "Kept out of the comparison: the price looks wrong for this product"
    elif listing.sanity == Listing.Sanity.DOUBTFUL:
        kept = "Doubted: it may not be this product"
    return {"listing": listing, "state": state, "tone": tone, "delivery": delivery, "cheapest": cheapest,
            "mine": cheapest == MINE, "kept": kept}


def window_hours(shop):
    """How many hours a price of ``shop`` counts for after its last check."""
    hours = settings.RIPRAPTOR_STALE_AFTER_HOURS
    return min(settings.RIPRAPTOR_EBAY_STALE_AFTER_HOURS, hours) if shop.source_type == EBAY else hours


def no_store(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@staff_member_required
@not_for_editors
@require_GET
def shops_page(request):
    search = (request.GET.get("q") or "").strip()[:SEARCH_MOST]
    shops = list(shop_rows(search, timezone.now()))
    return no_store(render(request, "admin/shop_stock_shops.html", {
        **admin.site.each_context(request),
        "title": "Shop stock",
        "shops": shops,
        "search": search,
    }))


@staff_member_required
@not_for_editors
@require_GET
def shop_page(request, slug):
    shop = get_object_or_404(Retailer, slug=slug)
    now = timezone.now()
    code = request.GET.get("show")
    code = code if code in CODES else "listed"
    search = (request.GET.get("q") or "").strip()[:SEARCH_MOST]
    counts = counts_for(shop, now)
    page = Paginator(listings_for(shop, code, search, now), PER_PAGE).get_page(request.GET.get("page"))
    listings = list(page.object_list)
    leads = lead_rows({listing.product_id for listing in listings}) if listings else {}
    rows = [describe(listing, listing.place, leads.get(listing.product_id), shop) for listing in listings]
    filters = [{"code": key, "label": label, "count": counts[key], "on": key == code}
               for key, label in FILTERS if key in ALWAYS or counts[key] or key == code]
    return no_store(render(request, "admin/shop_stock.html", {
        **admin.site.each_context(request),
        "title": f"{shop.name} stock",
        "shop": shop,
        "counts": counts,
        "filters": filters,
        "code": code,
        "label": dict(FILTERS)[code],
        "search": search,
        "page": page,
        "rows": rows,
        "stale_hours": window_hours(shop),
    }))
