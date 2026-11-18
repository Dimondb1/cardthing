"""
Shop clicks timeline: the owner's page of every click through to a shop, drawn as one lane a shop with a dot for each
click along the time, and under it every click in time order with the product, the shop and the page it came from. The
owner, 18 Nov 2026: "I would like to be able to see the click throughs to shops in a visual that illustrates the shop
the product and the time on a timeline". Real people's clicks only (OutboundClick.objects.people()): never the direct
hits and scripts the dashboard leaves out too.

The lanes are plain HTML, a dot placed along its lane by its time, and each dot is a link to its click in the list, so
a tap on a phone shows the product with no script at all. Today runs from midnight to now (UK time); 7 and 28 days end
now. One query for the page, however many clicks. Staff only, never for editors, never cached or indexed.
"""
from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue.models import OutboundClick
from guides.access import not_for_editors

PERIODS = (("today", "Today", 0), ("7", "7 days", 7), ("28", "28 days", 28))
DEFAULT = "today"
MOST = 5000       # clicks drawn at most, the newest: a dot each
LISTED = 300      # clicks listed under the lanes at most, the newest
# The page a click came from (catalogue.clicks.PAGES values), in words.
PAGE_LABELS = {
    "home": "Home page", "product": "Product page", "game": "Game page", "set": "Set page", "search": "Search",
    "deals": "Deals", "new": "New in", "drops": "Drops", "watchlist": "Watchlist", "swipe": "Swipe",
    "games": "Every game", "guides": "Guides", "guide": "A guide", "checker": "Fair Price Checker",
    "per_card": "Cheapest per card", "list": "Shopping list",
}
BUTTONS = dict(OutboundClick.Button.choices)


def window(period, now=None):
    """(start, end) of the period: today from UK midnight, else the last 7 or 28 days."""
    now = now or timezone.now()
    days = dict((key, length) for key, _label, length in PERIODS)[period]
    if not days:
        local = timezone.localtime(now)
        return local.replace(hour=0, minute=0, second=0, microsecond=0), now
    return now - timedelta(days=days), now


NOW_ROOM = 90     # a tick past this far along would run into the axis's "Now" at its end


def ticks(period, start, end):
    """[(label, left %)] for the time axis: every three hours today, every day over 7 days, every week over 28, none
    so near the end that it runs into "Now"."""
    span = (end - start).total_seconds() or 1
    found = []
    if period == "today":
        for hour in range(0, 24, 3):
            at = start.replace(hour=hour)
            if at <= end:
                found.append((f"{hour:02d}", 100 * (at - start).total_seconds() / span))
    else:
        step = 1 if period == "7" else 7
        day = timezone.localtime(start).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        while day < end:
            label = f"{day:%a}" if period == "7" else f"{day.day} {day:%b}"
            found.append((label, 100 * (day - start).total_seconds() / span))
            day += timedelta(days=step)
    return [(label, left) for label, left in found if left <= NOW_ROOM]


def clicks_between(start, end, shop=None):
    """The people's clicks from ``start`` to ``end``, newest first, at most MOST, with their product, game and shop.
    One query."""
    rows = (OutboundClick.objects.people().filter(created_at__gte=start, created_at__lte=end)
            .select_related("product", "product__game", "retailer").order_by("-created_at"))
    if shop:
        rows = rows.filter(retailer_id=shop)
    return list(rows[:MOST])


def lanes(clicks, start, end):
    """[{"shop", "count", "dots": [(click, left %)]}], the busiest shop first: one lane a shop."""
    span = (end - start).total_seconds() or 1
    found = {}
    for click in clicks:
        lane = found.setdefault(click.retailer_id, {"shop": click.retailer, "count": 0, "dots": []})
        lane["count"] += 1
        left = 100 * (click.created_at - start).total_seconds() / span
        lane["dots"].append((click, min(100.0, max(0.0, left))))
    return sorted(found.values(), key=lambda lane: (-lane["count"], lane["shop"].name.casefold()))


def where(click):
    """Where the click came from, in words: the button when it is known, else the page."""
    return BUTTONS.get(click.button) or PAGE_LABELS.get(click.source, click.source or "")


def by_day(clicks):
    """[(day, [click])] for the list, newest day first, each click with its words for where it came from."""
    days = []
    for click in clicks:
        click.where = where(click)
        local = timezone.localtime(click.created_at).date()
        if not days or days[-1][0] != local:
            days.append((local, []))
        days[-1][1].append(click)
    return days


@staff_member_required
@not_for_editors
@require_GET
def click_timeline_page(request):
    period = request.GET.get("period", DEFAULT)
    period = period if period in {key for key, _label, _days in PERIODS} else DEFAULT
    try:
        shop = int(request.GET.get("shop", "")) or None
    except ValueError:
        shop = None
    start, end = window(period)
    clicks = clicks_between(start, end, shop)
    listed = clicks[:LISTED]
    response = render(request, "admin/click_timeline.html", {
        **admin.site.each_context(request),
        "title": "Shop clicks timeline",
        "period": period, "periods": PERIODS, "shop": shop,
        "shop_name": clicks[0].retailer.name if shop and clicks else "",
        "count": len(clicks), "capped": len(clicks) >= MOST,
        "shops": len({click.retailer_id for click in clicks}),
        "products": len({click.product_id for click in clicks}),
        "via_rip": sum(1 for click in clicks if click.via_rip),
        "lanes": lanes(clicks, start, end), "ticks": ticks(period, start, end),
        "days": by_day(listed), "more": max(0, len(clicks) - LISTED),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
