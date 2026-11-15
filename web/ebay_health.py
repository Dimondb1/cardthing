"""
eBay health: the owner's page of how eBay's prices are doing (the owner, 15 Nov 2026: "an ebay health check is it
updating often enough, how many products on the site, how many ebay products are cheapest, with a link to see those
products, how many legit clicks. Maybe add a seller rating config button").

- Whether eBay is keeping up: of the eBay items in stock, how many were checked inside the background reader's two
  hours (catalogue/worker.py EBAY_EVERY) and how many are past the hours after which a price stops counting
  (ebay_stale_cutoff), the latest check, the daily search and what eBay last said was left of the day's allowances.
- Products on the site, and how many have an eBay price that counts now.
- The products where eBay is the cheapest now, cheaper than every UK shop or the only price, each with the best shop
  price beside it, a few at first and every one a tap away (?all=1).
- People's clicks to eBay (OutboundClick.people: never a robot's or a direct hit), to an item and to eBay's search.
- The seller rules: the least positive feedback and ratings a seller needs (EbaySettings, ebay.seller_rules), with
  how many prices showing now are from sellers below them, which drop at their next check.

Staff only, never for editors, never indexed or cached.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Count, Max, Min, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from catalogue import crawl, ebay
from catalogue.models import AMAZON, EBAY, EbaySettings, Listing, OutboundClick, Product, Retailer, ebay_stale_cutoff
from catalogue.worker import EBAY_EVERY
from guides.access import not_for_editors

SHOWN = 25          # products where eBay is cheapest, before See all
ALL_MOST = 500      # and at most this many with See all
# Healthy while at most this share of the eBay items in stock are past the hours their price counts for, and the
# latest check is this recent; not updating once nothing has been checked for as long as a price counts.
STALE_SHARE_OK = Decimal("0.05")
LATEST_OK = timedelta(minutes=45)
FEEDBACK_LEAST, FEEDBACK_MOST = Decimal("50.0"), Decimal("100.0")
RATINGS_MOST = 100_000


def freshness(now):
    """How eBay's in-stock items are being kept up to date: their count, how many were checked inside EBAY_EVERY and
    inside the hours a price counts for, the rest (not shown as buyable until checked again), and the latest and
    oldest check. One query."""
    cutoff, due = ebay_stale_cutoff(now), now - EBAY_EVERY
    items = Listing.objects.live().filter(retailer__source_type=EBAY, availability=Listing.Availability.IN_STOCK)
    counts = items.aggregate(total=Count("pk"), recent=Count("pk", filter=Q(last_checked__gte=due)),
                             counting=Count("pk", filter=Q(last_checked__gte=cutoff)), latest=Max("last_checked"),
                             oldest=Min("last_checked"))
    counts["stale"] = counts["total"] - counts["counting"]
    counts["hours"] = round((now - cutoff).total_seconds() / 3600)
    counts["every_hours"] = round(EBAY_EVERY.total_seconds() / 3600)
    return counts


def verdict(fresh, now):
    """("ok" | "behind" | "stopped", why): stopped when nothing has been checked inside the hours a price counts for,
    behind when more than STALE_SHARE_OK of the items in stock are past them or the latest check is older than
    LATEST_OK, else ok."""
    latest = fresh["latest"]
    if latest is None:
        return "stopped", "No eBay item has been checked yet."
    if latest < ebay_stale_cutoff(now):
        return "stopped", "Nothing from eBay has been checked for longer than a price counts for."
    if fresh["total"] and Decimal(fresh["stale"]) / fresh["total"] > STALE_SHARE_OK:
        return "behind", "Too many eBay items in stock are past the hours their price counts for."
    if now - latest > LATEST_OK:
        return "behind", "The latest eBay check was a while ago."
    return "ok", "eBay prices are being checked often enough."


def cheapest_at_ebay():
    """[{product, ebay, shop, saving}]: the products whose cheapest price that counts now is eBay's, cheaper than every
    UK shop (a tie goes to the shop) or the only price, the biggest saving first. Amazon's never counts. One query for
    the prices, one for the products."""
    rows = (Listing.objects.buyable().exclude(retailer__source_type=AMAZON).filter(product__is_active=True)
            .values("product").order_by()
            .annotate(ebay=Min("delivered_price", filter=Q(retailer__source_type=EBAY)),
                      shop=Min("delivered_price", filter=~Q(retailer__source_type=EBAY))))
    found = [row for row in rows if row["ebay"] is not None and (row["shop"] is None or row["ebay"] < row["shop"])]
    products = Product.objects.in_bulk([row["product"] for row in found])
    picked = [{"product": products[row["product"]], "ebay": row["ebay"], "shop": row["shop"],
               "saving": row["shop"] - row["ebay"] if row["shop"] is not None else None}
              for row in found if row["product"] in products]
    picked.sort(key=lambda row: (row["saving"] is None, -(row["saving"] or 0), row["product"].name))
    return picked


def with_items(picked):
    """The eBay item each product's price is from, for its seller and when it was checked. One query."""
    if not picked:
        return picked
    items = {}
    for listing in (Listing.objects.buyable().filter(retailer__source_type=EBAY,
                                                     product_id__in=[row["product"].pk for row in picked])
                    .order_by("product_id", "delivered_price")):
        items.setdefault(listing.product_id, listing)
    for row in picked:
        row["item"] = items.get(row["product"].pk)
    return picked


def products_counts(cheapest):
    """Products on the site, with an eBay price that counts now, and where eBay is cheapest. Two queries."""
    active = Product.objects.active().count()
    with_ebay = (Listing.objects.buyable().filter(retailer__source_type=EBAY, product__is_active=True)
                 .values("product").distinct().count())
    return {"active": active, "with_ebay": with_ebay, "cheapest": len(cheapest)}


def clicks(now):
    """People's clicks to eBay (OutboundClick.people: never a robot's or a direct hit) today, in the last 7 and 30
    days, to an item and to eBay's search, beside every person's click in the 30 days, and the direct hits to eBay
    turned away (suspects). Two queries."""
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    to_ebay = Q(retailer__source_type=EBAY)
    search = Q(button=OutboundClick.Button.EBAY)
    counts = OutboundClick.objects.people().filter(created_at__gte=month).aggregate(
        today=Count("pk", filter=to_ebay & Q(created_at__gte=today)),
        week=Count("pk", filter=to_ebay & Q(created_at__gte=week)),
        month=Count("pk", filter=to_ebay),
        items=Count("pk", filter=to_ebay & ~search),
        searches=Count("pk", filter=to_ebay & search),
        everyone=Count("pk"),
    )
    counts["turned_away"] = OutboundClick.objects.suspects().filter(to_ebay, created_at__gte=month).count()
    return counts


def below_rules(least_feedback, least_ratings):
    """eBay prices that count now from sellers below the rules, as each item's last check recorded them: they drop at
    their next check. One query."""
    return (Listing.objects.buyable().filter(retailer__source_type=EBAY)
            .filter(Q(seller_feedback__lt=least_feedback) | Q(seller_ratings__lt=least_ratings)).count())


def save_rules(request):
    """Save the seller rules from the form, both or neither."""
    try:
        feedback = Decimal(request.POST.get("min_feedback", "").strip()).quantize(Decimal("0.1"))
        ratings = int(request.POST.get("min_ratings", "").strip())
    except (InvalidOperation, ValueError):
        feedback, ratings = None, None
    if (feedback is None or not feedback.is_finite() or not FEEDBACK_LEAST <= feedback <= FEEDBACK_MOST
            or ratings is None or not 0 <= ratings <= RATINGS_MOST):
        messages.warning(request, f"Give a positive feedback from {FEEDBACK_LEAST} to {FEEDBACK_MOST}% and 0 to "
                                  f"{RATINGS_MOST:,} ratings. Nothing was saved.")
        return
    EbaySettings.objects.update_or_create(pk=1, defaults={"min_feedback": feedback, "min_ratings": ratings})
    cache.delete(ebay.RULES_KEY)
    messages.success(request, f"Saved: a seller needs {feedback}% positive feedback and {ratings:,} ratings. Each eBay "
                              "price is judged on these at its next check.")


@staff_member_required
@not_for_editors
@require_http_methods(["GET", "POST"])
def ebay_health_page(request):
    if request.method == "POST":
        save_rules(request)
        return redirect("ebay_health")
    now = timezone.now()
    fresh = freshness(now)
    state, why = verdict(fresh, now)
    searched = Product.objects.active().aggregate(
        day=Count("pk", filter=Q(ebay_checked_at__gte=now - timedelta(days=1))), latest=Max("ebay_checked_at"))
    cheapest = cheapest_at_ebay()
    show_all = request.GET.get("all") == "1"
    shown = with_items(cheapest[:ALL_MOST if show_all else SHOWN])
    least_feedback, least_ratings = ebay.seller_rules(fresh=True)
    ebay_shop = Retailer.objects.filter(source_type=EBAY).values_list("pk", flat=True).first()
    response = render(request, "admin/ebay_health.html", {
        **admin.site.each_context(request),
        "title": "eBay health",
        "state": state, "why": why, "fresh": fresh, "searched": searched,
        "usage": crawl.ebay_usage(now=now),
        "products": products_counts(cheapest),
        "cheapest": shown, "cheapest_more": max(0, len(cheapest) - len(shown)), "show_all": show_all,
        "clicks": clicks(now), "ebay_shop": ebay_shop,
        "rules": {"feedback": least_feedback, "ratings": least_ratings,
                  "feedback_least": FEEDBACK_LEAST, "feedback_most": FEEDBACK_MOST, "ratings_most": RATINGS_MOST,
                  "below": below_rules(least_feedback, least_ratings)},
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
