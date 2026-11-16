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

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Count, Max, Min, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_http_methods

from catalogue import crawl, ebay, worklog
from catalogue.models import (
    AMAZON, EBAY, EbaySettings, ImportRun, Listing, OutboundClick, Product, Retailer, WorkerState, ebay_stale_cutoff,
)
from catalogue.worker import EBAY_EVERY
from guides.access import not_for_editors

SHOWN = 25          # products where eBay is cheapest, before See all
ALL_MOST = 500      # and at most this many with See all
# Healthy while at most this share of the eBay items in stock are past the hours their price counts for, and the
# latest check is this recent; not updating once nothing has been checked for as long as a price counts.
STALE_SHARE_OK = Decimal("0.05")
LATEST_OK = timedelta(minutes=45)
# eBay's read due this long without starting is worth saying: it starts within a minute or two when nothing holds it.
READ_STUCK = timedelta(minutes=30)
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


def when(text):
    """An ISO time note_check kept, or None."""
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def item_numbers(ended):
    """Of eBay's items in stock: how many have no item number in their link, so cannot be checked, and how many are
    on the ended list though the daily search found them since (checked again, ebay.check_listings). One query."""
    no_number = on_list = 0
    for url in Listing.objects.live().filter(retailer__source_type=EBAY, availability__in=Listing.BUYABLE
                                             ).values_list("url", flat=True):
        item_id = ebay.item_id_of(url)
        no_number += not item_id
        on_list += bool(item_id) and item_id in ended
    return no_number, on_list


def reasons(fresh, now):
    """Why eBay's prices are not being kept up to date, the most likely first (the owner, 16 Nov 2026, with no eBay
    price counting: "something is wrong"): the background reader stopped or paused, eBay paused or waiting after
    errors with eBay's last error, the last item checks stopped by a problem, eBay's read due a long time without
    starting, items that cannot be checked, or its items checked and marked out of stock. Each is [{"line",
    "detail"}], with the facts behind them for the page. Four queries."""
    worker = WorkerState.current()
    shops = list(Retailer.objects.filter(source_type=EBAY, is_active=True))
    out = Listing.objects.live().filter(retailer__source_type=EBAY, availability=Listing.Availability.OUT_OF_STOCK)
    marked = out.filter(last_checked__gte=now - timedelta(days=1)).count()
    alive = worker is not None and worker.alive(now)
    check = ebay.last_check()
    check["at"], check["problem_at"] = when(check.get("at")), when(check.get("problem_at"))
    ended = ebay.ended_items()
    no_number, on_list = item_numbers(ended)
    found = []
    if not shops:
        found.append({"line": "There is no eBay shop switched on.", "detail": "Add eBay under Shops, or switch it on."})
    if worker is None or not worker.alive(now):
        seen = f"Its last sign of life was {timesince(worker.heartbeat_at, now)} ago." if worker and worker.heartbeat_at \
            else "It has never started."
        found.append({"line": "The background reader has stopped, so nothing checks eBay between the hourly reads.",
                      "detail": f"{seen} Running the update command restarts it."})
    elif worker.paused:
        found.append({"line": "Pause all is on, so no shop is read and no stock is checked.",
                      "detail": "Tap Resume all on Crawl health."})
    for shop in shops:
        if shop.reading_paused:
            found.append({"line": f"{shop.name} is paused, so its items are not checked.",
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
                                    "Its item checks go on meanwhile; only the daily search waits."})
    if check["problem_at"] and (check["at"] is None or check["problem_at"] > check["at"]):
        found.append({"line": f"The last eBay item check did not get through, {timesince(check['problem_at'], now)} ago.",
                      "detail": check.get("problem", "")})
    if no_number:
        found.append({"line": f"{no_number:,} eBay item{'s' if no_number != 1 else ''} in stock "
                              f"{'have' if no_number != 1 else 'has'} no item number in the link, so "
                              f"{'they cannot' if no_number != 1 else 'it cannot'} be checked.",
                      "detail": f"Only the daily search updates {'them' if no_number != 1 else 'it'}."})
    if on_list:
        found.append({"line": f"{on_list:,} eBay item{'s' if on_list != 1 else ''} in stock "
                              f"{'are' if on_list != 1 else 'is'} on the ended list.",
                      "detail": "eBay once said they had ended, yet the daily search found them again. They are "
                                "checked like any other item and leave the list once eBay shows them." if on_list != 1
                      else "eBay once said it had ended, yet the daily search found it again. It is checked like any "
                           "other item and leaves the list once eBay shows it."})
    if marked and fresh["total"] == 0:
        found.append({"line": f"{marked:,} eBay item{'s were' if marked != 1 else ' was'} checked in the last day and "
                              "marked out of stock, and none is in stock.",
                      "detail": "eBay no longer has them, or they now fail the seller rules or the other checks."})
    facts = {"worker": worker, "alive": alive, "shops": shops, "marked": marked, "check": check,
             "ended": len(ended), "ended_days": ebay.ENDED_DAYS, "on_list": on_list, "no_number": no_number}
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
    planned, eBay's own row, its allowances, its items by how long ago they were checked, the last check, its last
    searches and the reader's own log (catalogue/worklog.py). No key, token or address of a person is in it.
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
                f"requests in the last hour: {worker.requests_last_hour}, errors: {worker.errors_last_hour}"]
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
                f"  eBay running: {market.get('running') or 'no'}; eBay read: {market.get('read') or 'not due'}; "
                f"eBay check planned: {market.get('check') or 'none'}",
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

    out += ["", "EBAY ALLOWANCES (what eBay last said)"]
    said = (worker.ebay_usage if worker else None) or {}
    out.append(f"asked {clock_time(said.get('asked_at'), now)}: searches left {said_left(said, 'searches')}, item "
               f"lookups left {said_left(said, 'lookups')}, resets {said.get('reset') or 'not said'}"
               if said else "eBay has not been asked yet")

    items = Listing.objects.live().filter(retailer__source_type=EBAY)
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
    check = facts["check"]
    out += ["", "EBAY ITEMS",
            f"live: {counts['live']}, in stock or pre-order: {counts['buyable']}, out of stock: {counts['out']}",
            "in stock, last checked " + ", ".join(spans),
            f"latest check of one in stock: {clock_time(fresh['latest'], now)}, oldest: {clock_time(fresh['oldest'], now)}",
            f"latest check of one out of stock: {clock_time(counts['out_latest'], now)}, marked out of stock in the "
            f"last day: {facts['marked']}",
            f"in stock with no item number: {facts['no_number']}, in stock on the ended list: {facts['on_list']}, ended "
            f"list: {facts['ended']} items",
            f"a price counts for {fresh['hours']} h, each item is due a check every {fresh['every_hours']} h",
            "", "LAST ITEM CHECK",
            f"{clock_time(check.get('at'), now)}: {check.get('asked', 0)} asked about, {check.get('in_stock', 0)} in stock, "
            f"{check.get('gone', 0)} gone, {check.get('left', 0)} not reached" if check.get("at") else "none noted yet",
            f"last problem {clock_time(check.get('problem_at'), now)}: {check.get('problem')}"
            if check.get("problem_at") else "last problem: none noted"]

    out += ["", f"EBAY SEARCHES (the last {LOG_RUNS}; the hourly reads between them leave no row)"]
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
        messages.success(request, "Trying eBay again: its items are checked within a few minutes. Come back to this "
                                  "page in ten.")
    else:
        messages.warning(request, "The wait is cleared for eBay, but the background reader is not running, so its "
                                  "items are checked at the next hourly read. The update command restarts the reader.")


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
        if request.POST.get("action") == "try_again":
            try_again(request)
        else:
            save_rules(request)
        return redirect("ebay_health")
    now = timezone.now()
    fresh = freshness(now)
    state, why = verdict(fresh, now)
    found, facts = reasons(fresh, now)
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
        "state": state, "why": why, "fresh": fresh, "searched": searched, "reasons": found, "facts": facts,
        "usage": crawl.ebay_usage(now=now),
        "log": log_text(now, {"ok": "Up to date", "behind": "Falling behind"}.get(state, "Not updating"), why, fresh,
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
