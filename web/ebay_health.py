"""
eBay health: the owner's page of how eBay's prices are doing. The owner, 24 Nov 2026: "simplify the ebay health menu
for me and keep the log for you". At the top, one plain verdict and three numbers (eBay prices showing, hidden until
checked again, the latest check) with Test eBay now; the rest is folded away, the Log to copy last.

The first version (the owner, 15 Nov 2026: "an ebay health check is it
updating often enough, how many products on the site, how many ebay products are cheapest, with a link to see those
products, how many legit clicks. Maybe add a seller rating config button").

- Whether eBay is keeping up: of the eBay items in stock on the site's products, how many were checked inside the
  twenty hours after which a product on eBay is searched again (ebay.KNOWN_EVERY) and how many are past the hours after
  which a price stops counting (ebay_stale_cutoff), the latest check, the hourly searches and what eBay last said was
  left of the day's searches.
- Products on the site, and how many have an eBay price that counts now.
- The products where eBay is the cheapest now, cheaper than every UK shop or the only price, each with the best shop
  price beside it, a few at first and every one a tap away (?all=1).
- People's clicks to eBay (OutboundClick.people: never a robot's or a direct hit), to an item and to eBay's search.
- The seller rules: the least positive feedback and ratings a seller needs (EbaySettings, ebay.seller_rules), with
  how many prices showing now are from sellers below them, which drop at their next check.

Staff only, never for editors, never indexed or cached.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Count, Max, Min, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_http_methods

from catalogue import ebay, worklog
from catalogue.models import (
    AMAZON, EBAY, EbaySettings, ImportRun, Listing, OutboundClick, Product, Retailer, WorkerState, ebay_stale_cutoff,
)
from guides.access import not_for_editors

SHOWN = 25          # products where eBay is cheapest, before See all
ALL_MOST = 500      # and at most this many with See all
# Healthy while at most this share of the eBay items in stock are past the hours their price counts for, and the
# latest check is this recent; not updating once nothing has been checked for as long as a price counts.
STALE_SHARE_OK = Decimal("0.05")
# eBay is searched hourly, so the latest check is up to an hour old between runs: behind only once one run is missed.
LATEST_OK = timedelta(minutes=90)
# eBay's read due this long without starting is worth saying: it starts within a minute or two when nothing holds it.
READ_STUCK = timedelta(minutes=30)
FEEDBACK_LEAST, FEEDBACK_MOST = Decimal("50.0"), Decimal("100.0")
RATINGS_MOST = 100_000


def freshness(now):
    """How eBay's in-stock items are being kept up to date: their count, how many were checked inside KNOWN_EVERY and
    inside the hours a price counts for, the rest (not shown as buyable until checked again), and the latest and
    oldest check. Only the site's products count: an item on a product taken off the site shows nowhere. One query."""
    cutoff, due = ebay_stale_cutoff(now), now - ebay.KNOWN_EVERY
    items = Listing.objects.live().filter(retailer__source_type=EBAY, availability=Listing.Availability.IN_STOCK,
                                          product__is_active=True)
    counts = items.aggregate(total=Count("pk"), recent=Count("pk", filter=Q(last_checked__gte=due)),
                             counting=Count("pk", filter=Q(last_checked__gte=cutoff)), latest=Max("last_checked"),
                             oldest=Min("last_checked"))
    counts["stale"] = counts["total"] - counts["counting"]
    counts["showing"] = counts["counting"]
    counts["hours"] = round((now - cutoff).total_seconds() / 3600)
    counts["every_hours"] = round(ebay.KNOWN_EVERY.total_seconds() / 3600)
    counts["popular_hours"] = round(ebay.POPULAR_EVERY.total_seconds() / 3600)
    return counts


def verdict(fresh, now):
    """("ok" | "behind" | "stopped", why): stopped when nothing has been checked inside the hours a price counts for,
    behind when more than STALE_SHARE_OK of the items in stock are past them or the latest check is older than
    LATEST_OK, else ok."""
    latest = fresh["latest"]
    if latest is None:
        return "stopped", "No eBay price has been checked yet."
    if latest < ebay_stale_cutoff(now):
        return "stopped", f"Nothing from eBay has been checked for over {fresh['hours']} hours."
    if fresh["total"] and Decimal(fresh["stale"]) / fresh["total"] > STALE_SHARE_OK:
        return "behind", (f"{fresh['stale']:,} eBay price{'s are' if fresh['stale'] != 1 else ' is'} hidden until "
                          f"{'they are' if fresh['stale'] != 1 else 'it is'} checked again.")
    if now - latest > LATEST_OK:
        return "behind", f"eBay was last checked {timesince(latest, now, depth=1)} ago; it is searched every hour."
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


def when(text):
    """An ISO time kept in the cache (the last test), or None."""
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def reasons(fresh, now):
    """Why eBay's prices are not being kept up to date, the most likely first (the owner, 16 Nov 2026, with no eBay
    price counting: "something is wrong"): the background reader stopped or paused, eBay paused or waiting after
    errors with eBay's last error, eBay's read due a long time without starting, or its items checked and marked out
    of stock. Each is [{"line", "detail"}], with the facts behind them for the page. Three queries."""
    worker = WorkerState.current()
    shops = list(Retailer.objects.filter(source_type=EBAY, is_active=True))
    out = Listing.objects.live().filter(retailer__source_type=EBAY, availability=Listing.Availability.OUT_OF_STOCK)
    marked = out.filter(last_checked__gte=now - timedelta(days=1)).count()
    alive = worker is not None and worker.alive(now)
    found = []
    if not shops:
        found.append({"line": "There is no eBay shop switched on.", "detail": "Add eBay under Shops, or switch it on."})
    if worker is None or not worker.alive(now):
        seen = f"Its last sign of life was {timesince(worker.heartbeat_at, now)} ago." if worker and worker.heartbeat_at \
            else "It has never started."
        found.append({"line": "The background reader has stopped, so eBay is not searched.",
                      "detail": f"{seen} Running the update command restarts it."})
    elif worker.paused:
        found.append({"line": "Pause all is on, so no shop is read and no stock is checked.",
                      "detail": "Tap Resume all on Crawl health."})
    for shop in shops:
        if shop.reading_paused:
            found.append({"line": f"{shop.name} is paused, so its prices are not checked.",
                          "detail": "Tap Resume beside it on Crawl health."})
        if shop.backoff_until and shop.backoff_until > now:
            found.append({"line": f"{shop.name} is waiting after {shop.error_streak} failed tr"
                                  f"{'y' if shop.error_streak == 1 else 'ies'}, until "
                                  f"{timezone.localtime(shop.backoff_until):%H:%M}.",
                          "detail": f"eBay's last error: {shop.last_error or 'not recorded'}"})
        elif shop.error_streak and shop.last_error:
            found.append({"line": f"{shop.name}'s last {shop.error_streak} tr{'y' if shop.error_streak == 1 else 'ies'} "
                                  "failed.", "detail": f"eBay's last error: {shop.last_error}"})
        shop.waiting = now - shop.next_read_at if shop.next_read_at and shop.next_read_at <= now else None
        if alive and not worker.paused and not shop.reading_paused and shop.waiting and shop.waiting > READ_STUCK \
                and not (shop.backoff_until and shop.backoff_until > now):
            found.append({"line": f"{shop.name}'s read has been due for {timesince(shop.next_read_at, now)} without "
                                  "starting.",
                          "detail": "Another job is holding the lock that reads share, or every reading slot is busy. "
                                    "Until it starts, no eBay product is searched."})
    if marked and fresh["total"] == 0:
        found.append({"line": f"{marked:,} eBay item{'s were' if marked != 1 else ' was'} checked in the last day and "
                              "marked out of stock, and none is in stock.",
                      "detail": "eBay no longer has them, or they now fail the seller rules or the other checks."})
    facts = {"worker": worker, "alive": alive, "shops": shops, "marked": marked}
    return found, facts


# The Log to copy box: the newest warnings and errors and the newest lines about eBay from the reader's log, eBay's
# last few searches, and how long ago its items in stock were last checked.
LOG_PROBLEMS, LOG_LINES, LOG_RUNS, LOG_RUN_LINES = 40, 80, 6, 6
AGES = (("under 1 h", 1), ("1 to 2 h", 2), ("2 to 4 h", 4), ("4 to 8 h", 8), ("8 to 24 h", 24))


def clock_time(moment, now):
    """A time for the log: "16 Nov 14:32 (5 min ago)", or "none"."""
    if isinstance(moment, str):
        moment = when(moment)
    if moment is None:
        return "none"
    minutes = int((now - moment).total_seconds() // 60)
    ago = f"{minutes} min ago" if 0 <= minutes < 120 else f"{minutes // 60} h ago" if minutes >= 0 else \
        f"in {-minutes} min" if minutes > -120 else f"in {-minutes // 60} h"
    return f"{timezone.localtime(moment):%d %b %H:%M} ({ago})"


def said_left(said, name):
    return "not said" if said.get(name) is None else said[name]


def log_text(now, state, why, fresh, found, facts):
    """Everything about eBay's updating in one piece of plain text, for the owner to copy and paste (the owner, 16 Nov
    2026: "why aren't you producing an output log I could screenshot to you or a log that would show in the admin
    area that I could copy and paste to you"): the verdict and reasons, the background reader and what it last
    planned, eBay's own row, its search allowance, its items by how long ago they were checked, its last searches and
    the reader's own log (catalogue/worklog.py). No key, token or address of a person is in it.
    Three queries."""
    out = [f"RipRaptor eBay log, {timezone.localtime(now):%a %d %b %Y %H:%M} UK time",
           f"Verdict: {state}. {why}"]
    out += [f"Reason: {reason['line']} {reason['detail']}" for reason in found] or ["Reason: none found"]

    worker, plan = facts["worker"], worklog.last_plan()
    out += ["", "BACKGROUND READER"]
    if worker is None:
        out.append("never started")
    else:
        out += [f"running: {'yes' if facts['alive'] else 'NO'}, last sign of life {clock_time(worker.heartbeat_at, now)}, "
                f"started {clock_time(worker.started_at, now)}",
                f"pause all: {'ON' if worker.paused else 'off'}",
                f"jobs done since it started: {worker.jobs_done}, last job: {worker.last_job or 'none'}",
                f"requests in the last hour: {worker.requests_last_hour}, errors: {worker.errors_last_hour}",
                f"memory: {worker.memory_mb or '?'} MB of a {worker.memory_limit_mb or '?'} MB limit"]
        if worker.note:
            out.append(f"note: {worker.note}")
    if not plan:
        out.append("last plan: none noted (the reader notes one a minute once it runs this version)")
    elif plan.get("paused"):
        out.append(f"last plan {clock_time(plan.get('at'), now)}: nothing planned, Pause all is on")
    else:
        queue, market, lock = plan.get("queue", {}), plan.get("ebay", {}), plan.get("lock", {})
        jobs = ", ".join(f"{job['label']} ({job['minutes']} min)" for job in plan.get("running", [])) or "nothing"
        out += [f"last plan {clock_time(plan.get('at'), now)}, {plan.get('threads')} threads",
                f"  running then: {jobs}",
                f"  due: {queue.get('reads', 0)} reads ({queue.get('reads_with_room', 0)} with room to start), "
                f"{queue.get('checks', 0)} shops to check, {queue.get('pulses', 0)} pulses, {queue.get('other', 0)} other",
                f"  eBay running: {market.get('running') or 'no'}; eBay read: {market.get('read') or 'not due'}",
                f"  listings waiting after a failed try: {plan.get('checks_waiting_after_a_failed_try', 0)}",
                "  import lock: " + (f"held by the reader since {clock_time(lock.get('held_since'), now)}"
                                     if lock.get("held_since") else "not held by the reader")
                + (f"; refused since {clock_time(lock.get('refused_since'), now)}: {lock.get('refused')}"
                   if lock.get("refused_since") else "")]

    out += ["", "EBAY SHOP ROW"]
    for shop in facts["shops"]:
        out += [f"{shop.name} (id {shop.pk}): paused {'YES' if shop.reading_paused else 'no'}, reads every "
                f"{shop.read_every_minutes} min, last read took {shop.last_read_seconds or 0} s",
                f"next read: {clock_time(shop.next_read_at, now)}, last good read: {clock_time(shop.last_ok_at, now)}",
                f"failed tries in a row: {shop.error_streak}, waiting until: {clock_time(shop.backoff_until, now)}",
                f"last error: {shop.last_error or 'none'}"]
    if not facts["shops"]:
        out.append("no eBay shop is switched on")

    out += ["", "EBAY ALLOWANCE (what eBay last said)"]
    said = (worker.ebay_usage if worker else None) or {}
    out.append(f"asked {clock_time(said.get('asked_at'), now)}: searches left {said_left(said, 'searches')}, resets "
               f"{said.get('reset') or 'not said'}" if said else "eBay has not been asked yet")

    items = Listing.objects.live().filter(retailer__source_type=EBAY, product__is_active=True)
    ages = {f"within_{hours}": Count("pk", filter=Q(availability__in=Listing.BUYABLE,
                                                     last_checked__gte=now - timedelta(hours=hours)))
            for _label, hours in AGES}
    counts = items.aggregate(
        live=Count("pk"), buyable=Count("pk", filter=Q(availability__in=Listing.BUYABLE)),
        out=Count("pk", filter=Q(availability=Listing.Availability.OUT_OF_STOCK)),
        out_latest=Max("last_checked", filter=Q(availability=Listing.Availability.OUT_OF_STOCK)), **ages)
    spans, under = [], 0
    for label, hours in AGES:
        spans.append(f"{label}: {counts[f'within_{hours}'] - under}")
        under = counts[f"within_{hours}"]
    spans.append(f"over 24 h: {counts['buyable'] - under}")
    out += ["", "EBAY ITEMS (on the site's products)",
            f"live: {counts['live']}, in stock or pre-order: {counts['buyable']}, out of stock: {counts['out']}",
            "in stock, last checked " + ", ".join(spans),
            f"latest check of one in stock: {clock_time(fresh['latest'], now)}, oldest: {clock_time(fresh['oldest'], now)}",
            f"latest check of one out of stock: {clock_time(counts['out_latest'], now)}, marked out of stock in the "
            f"last day: {facts['marked']}",
            f"a price counts for {fresh['hours']} h; a product on eBay is searched again after {fresh['every_hours']} h "
            f"(the longest unchecked first), one visitors look at after {fresh['popular_hours']} h",
            "prices are kept fresh by searching only: eBay's item lookups are not used"]

    test = ebay.last_test()
    out += ["", f"EBAY TEST (Test eBay now, {clock_time(test.get('at'), now)})" if test else "EBAY TEST"]
    out += list(test.get("lines") or []) or ["not run yet: tap Test eBay now on this page"]

    out += ["", f"EBAY SEARCHES (the last {LOG_RUNS} hourly runs)"]
    runs = list(ImportRun.objects.filter(retailer__source_type=EBAY).order_by("-started_at")[:LOG_RUNS])
    for run in runs:
        ended = f"{timezone.localtime(run.finished_at):%H:%M}" if run.finished_at else "STILL RUNNING"
        out.append(f"{timezone.localtime(run.started_at):%d %b %H:%M} to {ended}: {run.offers_found} offers found, "
                   f"{run.listings_updated} listings updated{', ' + run.note if run.note else ''}"
                   f"{', ERROR: ' + run.error[:300] if run.error else ''}")
        out += ["    " + line[:300] for line in (run.log or "").splitlines()[-LOG_RUN_LINES:]]
    if not runs:
        out.append("none on record")

    kept = worklog.entries()
    problems = [lines for bad, lines in kept if bad][-LOG_PROBLEMS:]
    others = [lines for bad, lines in kept if not bad][-LOG_LINES:]
    out += ["", f"READER LOG: WARNINGS AND ERRORS (the newest {LOG_PROBLEMS}, oldest first)"]
    out += [line for lines in problems for line in lines] or ["none kept"]
    out += ["", f"READER LOG: EBAY AND THE IMPORT LOCK (the newest {LOG_LINES}, oldest first)"]
    out += [line for lines in others for line in lines] or ["none kept"]
    if not kept:
        out.append("The reader keeps this log from the first time it runs this version.")
    # A shop's last error and a search's own log come from outside: nothing secret leaves in them either.
    return worklog.scrub("\n".join(out))


def test_ebay(request):
    """Test eBay now: ask eBay each thing the site asks it, once, and keep its answers for the page and the log
    (ebay.self_test). Changes no listing."""
    try:
        ebay.self_test()
    except Exception as exc:  # noqa: BLE001 - whatever goes wrong is what the owner needs to read
        messages.warning(request, f"The test stopped on an unexpected error: {type(exc).__name__}: {exc}")
        return
    messages.success(request, "Asked eBay. Its answers are under Test eBay now.")


def try_again(request):
    """Try eBay again now, as Crawl health's Read now does for a shop: its wait after errors cleared and its next read
    now, so the background reader checks its items within minutes."""
    shops = Retailer.objects.filter(source_type=EBAY, is_active=True)
    count = shops.update(next_read_at=timezone.now(), backoff_until=None, error_streak=0)
    worker = WorkerState.current()
    if not count:
        messages.warning(request, "There is no eBay shop switched on.")
    elif worker is not None and worker.alive():
        # Not opening with "eBay": the admin's messages capitalise their first letter.
        messages.success(request, "Trying eBay again: it is searched within a few minutes. Come back to this page in "
                                  "ten.")
    else:
        messages.warning(request, "The wait is cleared for eBay, but the background reader is not running, so it is "
                                  "searched at the next hourly read. The update command restarts the reader.")


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
                              "price is judged on these when its product is next searched.")


@staff_member_required
@not_for_editors
@require_http_methods(["GET", "POST"])
def ebay_health_page(request):
    if request.method == "POST":
        if request.POST.get("action") == "try_again":
            try_again(request)
        elif request.POST.get("action") == "test":
            test_ebay(request)
            return redirect(reverse("ebay_health") + "#test")
        else:
            save_rules(request)
        return redirect("ebay_health")
    now = timezone.now()
    fresh = freshness(now)
    state, why = verdict(fresh, now)
    found, facts = reasons(fresh, now)
    cheapest = cheapest_at_ebay()
    show_all = request.GET.get("all") == "1"
    shown = with_items(cheapest[:ALL_MOST if show_all else SHOWN])
    least_feedback, least_ratings = ebay.seller_rules(fresh=True)
    ebay_shop = Retailer.objects.filter(source_type=EBAY).values_list("pk", flat=True).first()
    response = render(request, "admin/ebay_health.html", {
        **admin.site.each_context(request),
        "title": "eBay health",
        "state": state, "why": why, "fresh": fresh, "reasons": found, "facts": facts,
        "test": {"at": when(ebay.last_test().get("at")),
                 "lines": [worklog.scrub(line) for line in ebay.last_test().get("lines") or []]},
        "log": log_text(now, {"ok": "Up to date", "behind": "Behind"}.get(state, "Not updating"), why, fresh,
                        found, facts),
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
