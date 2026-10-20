"""
Why traffic changed: the explainer at the top of the Insights page.

The By day table alone misleads: it puts part of today beside whole days, and a script that opens thirty
thousand pages, or four thousand addresses that each open one page and never click, sit in it beside people.
``explain`` compares like with like and says what it can and cannot see:

* Part of today against the same hours of the same weekday last week and of yesterday, using the time each
  visitor arrived (``DailyVisitor.first_seen``). Rows from before arrival times were kept are left out of
  these comparisons, and the result says when the history starts.
* Yesterday as a whole day against the average of the previous four of the same weekday, or, with fewer than
  two of those, the seven days before.
* Burst days (pages per visitor or visitors per click far outside the normal range, or a few visitors opening
  most of the pages) are flagged and left out of every average. Visitors at the daily views cap are scripts
  and never counted as people.
* The change in people is broken down by source, landing page, device, country and new or returning, and a
  driver is named only when it is big and out of proportion to the rest.
* Health: hours with no visitors, hours of visitors with no clicks, clicks per visitor falling, shop reads
  overdue.

It runs a fixed handful of aggregate queries however many visitors there are. Every sentence comes from
content/registry.py (section "insights") so the owner can reword it in admin.
"""

from datetime import datetime, timedelta
from math import sqrt
from statistics import median

from django.db.models import Count, Q, Sum
from django.utils import timezone

from content.service import get as copy_get
from content.service import load_all

from .insights import COUNTRY_NAMES, VIEWS_PER_VISITOR_CAP, source_group, stale_after, worker_stopped
from .models import DailyPageView, DailyVisitor, OutboundClick, Product, Retailer

# How far back the explainer looks: four weeks of the same weekday before yesterday, and a little spare.
WINDOW_DAYS = 36
# A day with fewer visitors than this says nothing about the normal range, and counting is taken to start on
# the first day that reaches it (Ben's first days, from 2 Oct 2026, were the owner testing).
MIN_DAY_VISITORS = 20
# One visitor opening this many pages in a day reads the site like a script.
HEAVY_VIEWS = 30

# Bursts. A robust spread (1.4826 x MAD, the standard deviation for normal data) is floored at a quarter of
# the median, so a run of near identical days does not make every small wobble a burst.
BURST_SPREADS = 5         # this many spreads above the median
BURST_TIMES = 3           # and at least this many times the median
BURST_MIN_PAGES = 4       # pages per visitor below this is never a burst: people browse
BURST_MIN_PER_CLICK = 8   # visitors per click below this is never a burst
BURST_CROWD = 1.5         # a visitors-per-click burst also brings at least this many times the usual visitors
FEW_SHARE = 0.5           # a few visitors opening half the pages ...
FEW_MAX = 3               # ... where a few is at most 3, or 5% of the day's visitors
FEW_MIN_PAGES = 200
# More clicks to shops than people in a day is not plausibly human: clicks are saved per /go/ request with only a
# user agent check, so a script that follows Buy now links counts. Such days are left out of the usual clicks
# per person, which the Buy links checks expect, but their people still count.
CLICKS_PER_PERSON_MAX = 1.0
# Page totals by kind leave out the rows that are not pages anyone opened, as the By day table does.
NOT_PAGES = [DailyPageView.Kind.WATCHED, DailyPageView.Kind.INSTALL, DailyPageView.Kind.ALERTS]

# A change is noise below the largest of these: 5 people, 15%, or two Poisson standard deviations.
NOISE_MIN = 5
NOISE_SHARE = 0.15

# A driver must move at least 20 people, carry a quarter of the change, and move out of proportion: its
# change must differ from its share of the total change by at least 30% of its own change.
DRIVER_MIN = 20
DRIVER_SHARE = 0.25
DRIVER_EXCESS = 0.3

# Health.
GAP_HOURS = 2             # hours in a row with no visitors ...
GAP_EXPECTED = 6          # ... when at least this many usually arrive in them
CLICKLESS_HOURS = 3       # hours in a row with visitors and no clicks ...
CLICKLESS_VISITORS = 15   # ... with at least this many people ...
CLICKLESS_EXPECTED = 5    # ... and at least this many clicks usually
CONVERSION_DROP = 0.5     # clicks per person under half the usual ...
CONVERSION_MISSING = 10   # ... and at least this many clicks short
DAYTIME = range(8, 23)    # with no hourly history yet, people are expected 08:00 to 22:59 only

SOCIAL = {"Reddit", "Facebook and Instagram", "Discord", "X and Threads", "YouTube and TikTok", "WhatsApp",
          "AI assistants"}
DEVICES = {"mobile": "Phone", "tablet": "Tablet", "desktop": "Computer"}
DIMENSIONS = [("source", "Source"), ("landing", "Landing page type"), ("page", "Landing page"),
              ("country", "Country"), ("device", "Device"), ("returning", "New or returning")]


def number(n):
    """4,386 for whole numbers, one decimal place for averages that are not whole."""
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.1f}"
    return f"{int(n):,}"


def signed(n):
    """+176 or -254, rounded to whole people, for the change columns."""
    return f"{round(n):+,}"


def is_noise(now, then):
    return abs(now - then) < max(NOISE_MIN, NOISE_SHARE * then, 2 * sqrt(max(then, 0)))


def robust(values):
    """Median and a floored robust spread of ``values``."""
    mid = median(values)
    mad = median(abs(v - mid) for v in values)
    return mid, max(1.4826 * mad, 0.25 * mid, 1e-9)


def burst_reasons(day, stats):
    """Why a day's counts look automated: a list of (registry key, values), empty for a normal day.

    ``views`` is every page the day counted, as By day shows it, so it also covers rows from before each
    visitor's own page count was kept (7 Oct 2026). The few-visitors rule looks only below the views cap: a
    visitor at the cap is a script already left out of people, so it says nothing about the people count."""
    visitors, views, clicks = day["visitors"], day["views"], day["clicks"]
    if visitors < MIN_DAY_VISITORS:
        return []
    reasons = []
    per_visitor = views / visitors
    per_click = visitors / max(clicks, 1)
    if stats:
        (pages_mid, pages_spread), (click_mid, click_spread), visitors_mid = stats
        many_pages = per_visitor >= max(BURST_MIN_PAGES, BURST_TIMES * pages_mid, pages_mid + BURST_SPREADS * pages_spread)
        no_clicks = per_click >= max(
            BURST_MIN_PER_CLICK, BURST_TIMES * click_mid, click_mid + BURST_SPREADS * click_spread
        ) and visitors >= BURST_CROWD * visitors_mid
    else:
        # Too few normal days to know the range: only the plainly impossible counts.
        many_pages = per_visitor >= 10
        no_clicks = per_click >= 30 and visitors >= 100
    if many_pages:
        reasons.append(("insights.why.reason_views", {"views": number(views), "visitors": number(visitors)}))
    if no_clicks:
        reasons.append(("insights.why.reason_clicks", {"visitors": number(visitors), "clicks": number(clicks)}))
    people_pages = day["people_pages"]
    if (people_pages >= FEW_MIN_PAGES and day["heavy"] and day["heavy"] <= max(FEW_MAX, 0.05 * day["people"])
            and day["heavy_views"] >= FEW_SHARE * people_pages):
        reasons.append(("insights.why.reason_few_one" if day["heavy"] == 1 else "insights.why.reason_few", {
            "count": number(day["heavy"]), "share": round(100 * day["heavy_views"] / people_pages),
        }))
    return reasons


def at_same_time(day, local_now):
    """The moment on ``day`` with the same clock time as ``local_now``."""
    return timezone.make_aware(datetime.combine(day, local_now.time().replace(tzinfo=None)))


def start_of(day):
    return timezone.make_aware(datetime.combine(day, datetime.min.time()))


def explain(now=None, store=None):
    """Why traffic is doing what it is doing, as data and plain sentences for the Insights page.

    ``days`` is each day's counts, {date: row}, with the people (``people``, ``returning``), page views,
    clicks from people and whether the day is a burst, for the dashboard's week (web/dashboard.py)."""
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    today = local_now.date()
    yesterday = today - timedelta(days=1)
    since = today - timedelta(days=WINDOW_DAYS)
    cap = VIEWS_PER_VISITOR_CAP
    store = load_all() if store is None else store

    def say(key, **values):
        return copy_get(key, store, **values)

    # Today's rows that arrived after ``now`` are not "so far" (``now`` can be a past moment). Rows with no
    # arrival time cannot be placed in the day, so they stay in.
    so_far_q = Q(date__lt=today) | Q(first_seen__isnull=True) | Q(first_seen__lte=now)
    heavy_q = Q(views__gte=HEAVY_VIEWS, views__lt=cap)

    # 1. Each day's counts in one query, its pages in another, and its clicks in a third.
    days = {}
    for row in (
        DailyVisitor.objects.filter(so_far_q, date__gte=since, date__lte=today).values("date").annotate(
            visitors=Count("id"), scripts=Count("id", filter=Q(views__gte=cap)), own_pages=Sum("views"),
            people_pages=Sum("views", filter=Q(views__lt=cap)),
            heavy=Count("id", filter=heavy_q), heavy_pages=Sum("views", filter=heavy_q),
            timed=Count("first_seen"), landed=Count("id", filter=~Q(landing="")),
            returning=Count("id", filter=Q(returning=True, views__lt=cap)),
        ).order_by("date")
    ):
        row["heavy_views"] = row.pop("heavy_pages") or 0
        row["people_pages"] = row["people_pages"] or 0
        days[row["date"]] = row
    # Whole days take their pages from the page counts, as By day does: each visitor's own count only exists
    # from 7 Oct 2026, so earlier rows hold 0. Today takes its visitors' own counts, which can be cut at ``now``.
    pages_by_day = dict(
        DailyPageView.objects.filter(date__gte=since, date__lt=today).exclude(kind__in=NOT_PAGES).order_by()
        .values_list("date").annotate(n=Sum("hits")).values_list("date", "n")
    )
    clicks_by_day = dict(
        OutboundClick.objects.people().filter(created_at__gte=start_of(since), created_at__lte=now).order_by()
        .values_list("created_at__date").annotate(n=Count("id")).values_list("created_at__date", "n")
    )
    empty = {"visitors": 0, "scripts": 0, "own_pages": 0, "people_pages": 0, "heavy": 0, "heavy_views": 0,
             "timed": 0, "landed": 0, "returning": 0}
    for offset in range(WINDOW_DAYS + 1):
        day = since + timedelta(days=offset)
        row = days.setdefault(day, {"date": day, **empty})
        row["views"] = (row.pop("own_pages") or 0) if day == today else pages_by_day.get(day, 0)
        row["clicks"] = clicks_by_day.get(day, 0)
        row["people"] = row["visitors"] - row["scripts"]
        row["timed_fully"] = row["visitors"] > 0 and row["timed"] == row["visitors"]
        row["clicks_doubtful"] = row["people"] >= MIN_DAY_VISITORS and row["clicks"] > CLICKS_PER_PERSON_MAX * row["people"]
        row["burst"] = False

    counting_since = next((d for d in sorted(days) if days[d]["visitors"] >= MIN_DAY_VISITORS), None)
    timed_since = next((d for d in sorted(days) if days[d]["timed"]), None)
    if counting_since is None:
        return {"enough": False, "headline": say("insights.why.too_little"), "findings": [], "health": [],
                "bursts": [], "burst_dates": set(), "comparisons": [], "today": today, "now": local_now,
                "history": {"counting_since": None, "timed_since": timed_since}, "days": days}

    # 2. Bursts, judged against the median and spread of the whole days counted so far.
    whole = [days[d] for d in sorted(days) if counting_since <= d < today and days[d]["visitors"] >= MIN_DAY_VISITORS]
    stats = None
    if len(whole) >= 4:
        stats = (
            robust([d["views"] / d["visitors"] for d in whole]),
            robust([d["visitors"] / max(d["clicks"], 1) for d in whole]),
            median(d["visitors"] for d in whole),
        )
    bursts = []
    for day in sorted(days):
        reasons = burst_reasons(days[day], stats)
        days[day]["burst"] = bool(reasons)
        if reasons:
            bursts.append({
                "date": day, "today": day == today,
                "visitors": days[day]["visitors"], "views": days[day]["views"], "clicks": days[day]["clicks"],
                "reasons": [say(key, **values) for key, values in reasons],
            })
    bursts.reverse()

    def usable(day):
        return day >= counting_since and not days[day]["burst"]

    def baseline_days(target):
        """The previous four of the same weekday, or the seven days before when fewer than two are usable."""
        same = [target - timedelta(days=7 * k) for k in range(1, 5)]
        same = [d for d in same if d in days and usable(d)]
        if len(same) >= 2:
            return same, "weekday"
        week = [target - timedelta(days=k) for k in range(1, 8)]
        week = [d for d in week if d in days and usable(d)]
        return (week, "week") if len(week) >= 3 else ([], "")

    def conversion_of(base):
        """Clicks per person over ``base``, leaving out days with more clicks than people. None when unknown."""
        sure = [d for d in base if not days[d]["clicks_doubtful"]]
        people = sum(days[d]["people"] for d in sure)
        return sum(days[d]["clicks"] for d in sure) / people if people else None

    comparisons, findings, health = [], [], []

    def add(weight, text, check="", tone="", kind="", period=""):
        findings.append({"weight": weight, "text": text, "check": check, "tone": tone, "kind": kind, "period": period})

    # 3. Yesterday, whole, against its usual.
    base, basis = baseline_days(yesterday)
    yday = days[yesterday]
    yesterday_cmp = None
    if base:
        then = sum(days[d]["people"] for d in base) / len(base)
        then_clicks = sum(days[d]["clicks"] for d in base) / len(base)
        weekday = f"{yesterday:%A}"
        yesterday_cmp = {
            "key": "yesterday", "label": say("insights.why.period_yesterday", date=f"{yesterday:%a %-d %b}"),
            "basis": say("insights.why.basis_weekdays", weekday=weekday, count=len(base)) if basis == "weekday"
            else say("insights.why.basis_week", count=len(base)),
            "now": yday["people"], "then": round(then, 1), "now_clicks": yday["clicks"], "then_clicks": round(then_clicks, 1),
            "base_days": base, "burst": yday["burst"], "conversion": conversion_of(base),
            "period_q": Q(date=yesterday), "period_day": yesterday, "base_q": Q(date__in=base), "base_count": len(base),
        }
        comparisons.append(yesterday_cmp)

    # 4. Today so far, against the same hours last week and yesterday.
    tday = days[today]
    last_week = today - timedelta(days=7)
    cut_week, cut_yesterday = at_same_time(last_week, local_now), at_same_time(yesterday, local_now)
    so_far = DailyVisitor.objects.filter(date__in=[today, yesterday, last_week], views__lt=cap).aggregate(
        today=Count("id", filter=Q(date=today, first_seen__lte=now)),
        week=Count("id", filter=Q(date=last_week, first_seen__lte=cut_week)),
        yesterday=Count("id", filter=Q(date=yesterday, first_seen__lte=cut_yesterday)),
    )
    clicks_so_far = OutboundClick.objects.people().filter(created_at__gte=start_of(last_week), created_at__lte=now).order_by().aggregate(
        today=Count("id", filter=Q(created_at__gte=start_of(today), created_at__lte=now)),
        week=Count("id", filter=Q(created_at__gte=start_of(last_week), created_at__lte=cut_week)),
        yesterday=Count("id", filter=Q(created_at__gte=start_of(yesterday), created_at__lte=cut_yesterday)),
    )
    today_timed = tday["visitors"] == 0 or tday["timed_fully"]
    references = []
    if today_timed:
        for key, day, cut, label in (
            ("week", last_week, cut_week, say("insights.why.basis_last_week", weekday=f"{last_week:%A}")),
            ("yesterday", yesterday, cut_yesterday, say("insights.why.basis_yesterday")),
        ):
            if days.get(day) and days[day]["timed_fully"] and usable(day):
                references.append({"key": key, "date": day, "cut": cut, "basis": label,
                                   "then": so_far[key], "then_clicks": clicks_so_far[key]})
    today_cmp = {
        "key": "today", "label": say("insights.why.period_today", time=f"{local_now:%H:%M}"),
        "now": so_far["today"], "now_clicks": clicks_so_far["today"], "references": references, "burst": tday["burst"],
        "until": f"{local_now:%H:%M}", "people": tday["people"],
    }
    if references:
        ref = references[0]
        today_cmp.update({
            "basis": ref["basis"], "then": ref["then"], "then_clicks": ref["then_clicks"],
            "period_q": Q(date=today, first_seen__lte=now), "period_day": today,
            "base_q": Q(date=ref["date"], first_seen__lte=ref["cut"]), "base_count": 1,
            "conversion": ref["then_clicks"] / ref["then"]
            if ref["then"] and not days[ref["date"]]["clicks_doubtful"] else None,
        })
        comparisons.insert(0, today_cmp)
    elif tday["visitors"]:
        add(65, say("insights.why.part_day", period=today_cmp["label"], now=number(tday["people"])),
            kind="compare", period="today")

    # Same-time comparisons need a whole day of arrival times a week ago. The day the change went live is only
    # partly timed (rows from before it have none), so the first whole timed day is usually the day after.
    start = timed_since or today
    first_full = next((d for d in sorted(days) if d >= start and days[d]["timed_fully"] and d < today), None)
    if first_full is None:
        first_full = start + timedelta(days=1) if (start < today or not tday["timed_fully"]) else start
    same_time_from = first_full + timedelta(days=7)
    if today < same_time_from:
        add(30, say("insights.why.history", date=f"{start:%a %-d %b}", first=f"{first_full:%a %-d %b}",
                    end=f"{same_time_from:%a %-d %b}"), kind="history")

    # 5. What drove each change.
    for weight, cmp in ((70, today_cmp if references else None), (60, yesterday_cmp)):
        if cmp is None:
            continue
        cmp["drivers"] = attribute(cmp)
        now_n, then_n = cmp["now"], cmp["then"]
        if cmp["burst"]:
            reason = next(b["reasons"][0] for b in bursts if b["date"] == (today if cmp is today_cmp else yesterday))
            add(weight + 20, say("insights.why.burst_now", period=cmp["label"], reason=reason), tone="bad", kind="burst",
                period=cmp["key"])
        if is_noise(now_n, then_n):
            add(weight - 30, say("insights.why.same", period=cmp["label"], now=number(now_n), then=number(round(then_n)),
                                 basis=cmp["basis"]), kind="compare", period=cmp["key"])
            cmp["noise"] = True
            continue
        cmp["noise"] = False
        diff = now_n - then_n
        if round(then_n) == 0:
            # Nothing to set it against: no percentage, and no driver, since every group rose from nothing.
            add(weight, say("insights.why.up_from_none", period=cmp["label"], now=number(now_n), basis=cmp["basis"]),
                kind="compare", period=cmp["key"])
            continue
        percent = round(100 * abs(diff) / then_n)
        add(weight, say("insights.why.up" if diff > 0 else "insights.why.down", period=cmp["label"], now=number(now_n),
                        then=number(round(then_n)), basis=cmp["basis"], diff=number(abs(round(diff))), percent=percent),
            tone="" if diff > 0 else "bad", kind="compare", period=cmp["key"])
        named = cmp["drivers"]["named"]
        if not named:
            add(weight - 1, say("insights.why.spread"), kind="driver", period=cmp["key"])
        rise = diff > 0
        for n, row in enumerate(named[:2]):
            values = {"name": row["label"], "then": number(round(row["then"])), "now": number(row["now"]),
                      "diff": number(abs(round(row["change"]))), "share": row["share"]}
            if row["share"] > 100 and row.get("other"):
                # More than the whole change: say so, and which group went the other way.
                key = "insights.why.driver_up_over" if rise else "insights.why.driver_down_over"
                values.update(total=number(abs(round(diff))), other=row["other"]["label"],
                              other_diff=number(abs(round(row["other"]["change"]))))
            else:
                key = "insights.why.driver_up" if rise else "insights.why.driver_down"
            add(weight - 1 - n, say(key, **values), check=check_next(row, say), kind="driver", period=cmp["key"])

    # 6. Health: counting, Buy links, clicks per visitor, shop reads.
    checked = health_checks(now, local_now, today, days, tday, today_timed, baseline_days, conversion_of,
                            yesterday_cmp, today_cmp, health, say)
    for item in health:
        add(item["weight"], item["text"], tone="bad", kind="health")

    doubtful = [d for d in sorted(days, reverse=True)
                if counting_since <= d < today and days[d]["clicks_doubtful"] and not days[d]["burst"]]
    if doubtful:
        names = ", ".join(f"{d:%a %-d %b} ({number(days[d]['clicks'])} clicks, {number(days[d]['people'])} people)"
                          for d in doubtful[:4])
        add(45, say("insights.why.clicks_doubtful", names=names), check=say("insights.why.clicks_doubtful_check"),
            kind="clicks_doubtful")

    left_out = [b for b in bursts if not b["today"] and b["date"] != yesterday]
    if left_out:
        names = ", ".join(f"{b['date']:%a %-d %b} ({b['reasons'][0]})" for b in left_out[:4])
        add(50, say("insights.why.burst_days", names=names), check=say("insights.why.burst_check"), kind="burst")

    ranked = rank(findings)
    if checked == {"gap", "clicks"}:
        healthy = say("insights.why.healthy")
    elif "clicks" in checked:
        healthy = say("insights.why.healthy_no_hours")
    else:
        healthy = say("insights.why.healthy_no_clicks")
    return {
        "enough": True,
        "now": local_now,
        "today": today,
        "headline": ranked[0]["text"] if ranked else say("insights.why.healthy"),
        "findings": ranked,
        "comparisons": comparisons,
        "today_so_far": today_cmp,
        "yesterday": yesterday_cmp,
        "bursts": bursts,
        "burst_dates": {b["date"] for b in bursts},
        "health": health,
        "healthy": healthy if not health else "",
        "checked": checked,
        "history": {
            "counting_since": counting_since, "timed_since": timed_since,
            "days_counted": (today - counting_since).days, "whole_days_judged": len(whole),
            "first_full_day": first_full, "same_time_from": same_time_from,
        },
        "deploy_marker": None,   # the site keeps no record of when it was updated
        "days": days,
    }


def rank(findings, limit=5):
    """The ``limit`` most important findings. A change shown always keeps where it came from: when a driver is
    pushed out, it takes the place of a background note, a second driver, or a health warning after the first
    (every health warning is listed in full under Health anyway)."""
    findings = sorted(findings, key=lambda f: -f["weight"])
    ranked = findings[:limit]
    for compare in [f for f in ranked if f["kind"] == "compare"]:
        own = [f for f in findings if f["kind"] == "driver" and f["period"] == compare["period"]]
        if not own or any(f in ranked for f in own):
            continue
        first_drivers = {next(f for f in ranked if f["kind"] == "driver" and f["period"] == p)["text"]
                         for p in {f["period"] for f in ranked if f["kind"] == "driver"}}
        spare = ([f for f in ranked if f["kind"] not in ("health", "compare", "driver")]
                 or [f for f in ranked if f["kind"] == "driver" and f["text"] not in first_drivers]
                 or [f for f in ranked if f["kind"] == "health"][1:])
        if spare:
            ranked.remove(spare[-1])
            ranked.insert(ranked.index(compare) + 1, own[0])
    return ranked


def attribute(cmp):
    """The change in people from ``base_q`` (averaged over its days) to ``period_q``, by each dimension.

    Two aggregate queries: one grouped by source, device, country and new or returning, one by landing page."""
    period_q, base_q, base_count = cmp["period_q"], cmp["base_q"], cmp["base_count"]
    # The period is one day and the baseline other days, so the date says which side a row is on.
    people = DailyVisitor.objects.filter(period_q | base_q, views__lt=VIEWS_PER_VISITOR_CAP).order_by()
    period_day = cmp["period_day"]
    totals = {dim: {} for dim, _ in DIMENSIONS}
    hosts = {}

    def add_to(cell, day, n):
        if day == period_day:
            cell["now"] += n
        else:
            cell["then"] += n / base_count

    def put(dim, name, day, n):
        add_to(totals[dim].setdefault(name, {"now": 0, "then": 0}), day, n)

    for day, source, device, country, returning, n in (
        people.values_list("date", "source", "device", "country", "returning").annotate(n=Count("id"))
    ):
        group = source_group(source)
        put("source", group, day, n)
        if source:
            add_to(hosts.setdefault(group, {}).setdefault(source, {"now": 0, "then": 0}), day, n)
        put("device", DEVICES.get(device, "Unknown"), day, n)
        put("country", COUNTRY_NAMES.get(country, country or "Unknown"), day, n)
        put("returning", "Returning visitors" if returning else "New visitors", day, n)
    landing_rows = list(people.values_list("date", "landing").annotate(n=Count("id")))
    landed_everywhere = all(landing for _day, landing, _n in landing_rows)
    labels = dict(DailyPageView.Kind.choices)
    if landed_everywhere:
        for day, landing, n in landing_rows:
            kind, _, key = landing.partition(":")
            put("landing", labels.get(kind, kind), day, n)
            if key:
                put("page", landing, day, n)
    names = {}
    page = totals["page"]
    if page:
        top = sorted(page, key=lambda k: -abs(page[k]["now"] - page[k]["then"]))[:5]
        totals["page"] = {k: page[k] for k in top}
        wanted = [k.split(":", 1)[1] for k in top if k.startswith("product:")]
        if wanted:
            names = dict(Product.objects.filter(slug__in=wanted).values_list("slug", "name"))

    now_total = sum(c["now"] for c in totals["source"].values())

    then_total = sum(c["then"] for c in totals["source"].values())
    change = now_total - then_total
    out = {"now": now_total, "then": round(then_total, 1), "change": round(change, 1), "signed": signed(change),
           "dimensions": [], "named": [],
           "landing_recorded": landed_everywhere}
    candidates = []
    for dim, title in DIMENSIONS:
        rows = []
        for name, cell in totals[dim].items():
            delta = cell["now"] - cell["then"]
            label = name
            if dim == "page":
                kind, _, key = name.partition(":")
                label = names.get(key, key) if kind == "product" else f"{labels.get(kind, kind)}: {key}"
            row = {"dimension": dim, "label": label, "now": cell["now"], "then": round(cell["then"], 1),
                   "change": round(delta, 1), "signed": signed(delta),
                   "share": round(100 * delta / change) if change else 0}
            if dim == "source" and name in hosts:
                top_host = max(hosts[name].items(), key=lambda item: abs(item[1]["now"] - item[1]["then"]))
                row["host"] = top_host[0]
            row["group"] = name if dim == "source" else ""
            rows.append(row)
            if not then_total or len(totals[dim]) < 2:
                # From nothing every group rose in proportion to nothing, and one group is the whole: neither
                # says where a change came from.
                continue
            excess = delta - change * cell["then"] / then_total
            if (change and delta * change > 0 and abs(delta) >= DRIVER_MIN and abs(delta) >= DRIVER_SHARE * abs(change)
                    and abs(excess) >= DRIVER_EXCESS * abs(delta)):
                candidates.append(row)
        rows.sort(key=lambda r: -abs(r["change"]))
        for row in rows:
            against = [r for r in rows if r["change"] * row["change"] < 0]
            row["other"] = max(against, key=lambda r: abs(r["change"])) if against else None
        out["dimensions"].append({"key": dim, "title": title, "rows": rows})
    order = {dim: n for n, (dim, _) in enumerate(DIMENSIONS)}
    # Where people came from comes first: it is the one a cause can be looked up for (a post, a search change).
    # Pages, devices and countries follow, biggest first.
    candidates.sort(key=lambda r: (r["dimension"] != "source", -abs(r["change"]), order[r["dimension"]]))
    # One driver per dimension, so "Search engines" and "Product" are not the same people named twice over.
    seen = set()
    for row in candidates:
        if row["dimension"] not in seen:
            seen.add(row["dimension"])
            out["named"].append(row)
    out["sources"] = next(d["rows"] for d in out["dimensions"] if d["key"] == "source")
    return out


def check_next(row, say):
    if row["dimension"] != "source":
        return say("insights.why.check_other_driver")
    group = row["group"]
    if group == "Search engines":
        return say("insights.why.check_search")
    if group in SOCIAL:
        return say("insights.why.check_social", name=row.get("host") or group)
    if group == "Typed or bookmarked":
        return say("insights.why.check_typed")
    return say("insights.why.check_other")


def runs(flags):
    """(start, end) of each run of True in ``flags``, end exclusive."""
    out, start = [], None
    for i, flag in enumerate(list(flags) + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i))
            start = None
    return out


def health_checks(now, local_now, today, days, tday, today_timed, baseline_days, conversion_of, yesterday_cmp,
                  today_cmp, health, say):
    """Append problems to ``health`` and return which checks of today could run: "gap" (visitors hour by
    hour) and "clicks" (Buy now clicks today). A check that could not run is never reported as passed."""
    hour_now = local_now.hour
    checked = set()

    def hhmm(hour):
        return f"{hour:02d}:00"

    # Hours of today with no visitors, against the usual for those hours on the same weekday.
    usual_days, _ = baseline_days(today)
    base = [d for d in usual_days if days[d]["timed_fully"]]
    hourly_rows = (
        DailyVisitor.objects.filter(Q(date__in=base) | Q(date=today, first_seen__lte=now), first_seen__isnull=False,
                                    views__lt=VIEWS_PER_VISITOR_CAP)
        .order_by().values_list("date", "first_seen__hour").annotate(n=Count("id"))
    )
    today_hours, base_hours = [0] * 24, [0.0] * 24
    for day, hour, n in hourly_rows:
        if day == today:
            today_hours[hour] += n
        else:
            base_hours[hour] += n / len(base)
    if not base and yesterday_cmp:
        # No hourly history yet: the usual day spread evenly over the daytime hours.
        per_hour = yesterday_cmp["then"] / len(DAYTIME)
        base_hours = [per_hour if h in DAYTIME else 0.0 for h in range(24)]
    if today_timed and any(base_hours):
        checked.add("gap")
        done = range(hour_now)   # whole hours so far
        for start, end in runs(today_hours[h] == 0 for h in done):
            expected = sum(base_hours[start:end])
            if end - start >= GAP_HOURS and expected >= GAP_EXPECTED:
                health.append({"kind": "gap", "weight": 100, "start": hhmm(start), "end": hhmm(end),
                               "text": say("insights.why.gap", start=hhmm(start), end=hhmm(end),
                                           expected=number(round(expected)))})
                break

    # Today's clicks are expected at the rate of the comparison shown for today (same weekday, same hours),
    # otherwise of the usual days for today. Days with more clicks than people are left out of both.
    conversion = today_cmp.get("conversion")
    if conversion is None:
        conversion = conversion_of(usual_days)
    click_hours = [0] * 24
    for hour, n in (
        OutboundClick.objects.people().filter(created_at__gte=start_of(today), created_at__lte=now)
        .order_by().values_list("created_at__hour").annotate(n=Count("id"))
    ):
        click_hours[hour] = n
    if conversion and not tday["burst"] and today_timed:
        # Hours of visitors with no clicks to shops.
        checked.add("clicks")
        done = range(hour_now)
        for start, end in runs(click_hours[h] == 0 for h in done):
            # From the first hour anyone arrived: a quiet night with no clicks is not a fault.
            while start < end and not today_hours[start]:
                start += 1
            visitors = sum(today_hours[start:end])
            expected = visitors * conversion
            if end - start >= CLICKLESS_HOURS and visitors >= CLICKLESS_VISITORS and expected >= CLICKLESS_EXPECTED:
                health.append({"kind": "clicks", "weight": 95, "start": hhmm(start), "end": hhmm(end),
                               "text": say("insights.why.no_clicks", start=hhmm(start), end=hhmm(end),
                                           visitors=number(visitors), expected=number(round(expected)))})
                break
    elif conversion and not tday["burst"]:
        # Today's rows have no arrival times (the day this went live): the day so far as a whole instead.
        checked.add("clicks")
        people, actual = tday["people"], tday["clicks"]
        expected = people * conversion
        if actual == 0 and people >= CLICKLESS_VISITORS and expected >= CLICKLESS_EXPECTED:
            health.append({"kind": "clicks", "weight": 95,
                           "text": say("insights.why.no_clicks_today", time=f"{local_now:%H:%M}",
                                       visitors=number(people), expected=number(round(expected)))})
        elif actual < CONVERSION_DROP * expected and expected - actual >= CONVERSION_MISSING:
            health.append({"kind": "conversion", "weight": 80,
                           "text": say("insights.why.conversion", period=today_cmp["label"],
                                       now=number(round(100 * actual / people, 1)),
                                       then=number(round(100 * conversion, 1)))})

    def conversion_drop(cmp, rate):
        """Clicks per person far under ``rate``. Only the usual number of people is expected to click at the
        usual rate: extra people from a new source may click less without anything being broken."""
        expected = min(cmp["now"], cmp["then"]) * rate
        actual = cmp["now_clicks"]
        if cmp["now"] and actual < CONVERSION_DROP * expected and expected - actual >= CONVERSION_MISSING:
            health.append({"kind": "conversion", "weight": 80,
                           "text": say("insights.why.conversion", period=cmp["label"],
                                       now=number(round(100 * actual / cmp["now"], 1)),
                                       then=number(round(100 * rate, 1)))})

    rate = (yesterday_cmp or {}).get("conversion")
    if yesterday_cmp and rate and not yesterday_cmp["burst"]:
        expected = yesterday_cmp["now"] * rate
        if yesterday_cmp["now_clicks"] == 0 and yesterday_cmp["now"] >= CLICKLESS_VISITORS and expected >= CLICKLESS_EXPECTED:
            health.append({"kind": "clicks", "weight": 95,
                           "text": say("insights.why.no_clicks_day", day=f"{yesterday_cmp['period_day']:%a %-d %b}",
                                       visitors=number(yesterday_cmp["now"]), expected=number(round(expected)))})
        else:
            conversion_drop(yesterday_cmp, rate)
    if (today_cmp.get("references") and today_cmp.get("conversion") and not today_cmp["burst"]
            and not any(h["kind"] == "clicks" for h in health)):
        conversion_drop(today_cmp, today_cmp["conversion"])

    # Shop reads.
    stopped = worker_stopped(now)
    if stopped:
        health.append({"kind": "worker", "weight": 55,
                       "text": say("insights.why.worker", time=f"{timezone.localtime(stopped):%a %-d %b %H:%M}")})
    overdue = [
        r.name for r in Retailer.objects.filter(is_active=True, last_ok_at__isnull=False)
        .exclude(source_type=Retailer.Source.MANUAL).only("name", "last_ok_at", "read_every_minutes").order_by("name")
        if r.last_ok_at < now - stale_after(r)
    ]
    if overdue:
        health.append({"kind": "stale", "weight": 54,
                       "text": say("insights.why.stale", count=len(overdue), names=", ".join(overdue[:4]))})
    return checked
