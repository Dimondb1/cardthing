"""
Server health: how busy the server's processors are now, which program is using them, the last 24 hours, memory and
disk, and whether the background reader is easing off, from the samples it takes each minute
(catalogue/server_health.py). Staff only, never for editors. The owner, 25 Nov 2026: "the server is running at 99%
cpu how do we ensure that we monitor this from the ripraptor admin".

Four queries: the latest sample, the last 24 hours, and two for how long the processors have been busy.
"""

from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import server_health
from catalogue.insights import clock
from catalogue.models import ServerSample
from guides.access import not_for_editors

DAY = timedelta(hours=24)
SLOT = timedelta(minutes=10)
RECENT = timedelta(minutes=15)
STALE = timedelta(minutes=5)       # no sample for this long: the reader is not sampling


def averaged(samples):
    """{program: average percent} over ``samples`` (each with ``programs``)."""
    totals = {}
    for programs in samples:
        for name, share in (programs or {}).items():
            totals[name] = totals.get(name, 0.0) + share
    return {name: total / len(samples) for name, total in totals.items()} if samples else {}


def slots(rows, now):
    """The last 24 hours in SLOT steps, oldest first: (start, average, peak), or (start, None, None) with no
    sample."""
    start = now - DAY
    count = int(DAY / SLOT)
    found = [[] for _ in range(count)]
    for at, cpu in rows:
        index = int((at - start) / SLOT)
        if 0 <= index < count:
            found[index].append(cpu)
    return [(start + SLOT * i, sum(values) / len(values) if values else None, max(values) if values else None)
            for i, values in enumerate(found)]


def chart(day_slots):
    """What the template draws, in a box one unit a slot wide and 100 high (the percent), stretched to the page:
    the line (point strings, broken where there is no sample), the busy line and a hover target a slot. The labels
    are text beside it, so they stay readable at any width."""
    lines, current, targets = [], [], []
    for i, (start, average, peak) in enumerate(day_slots):
        if average is None:
            if current:
                lines.append(" ".join(current))
            current = []
        else:
            current.append(f"{i + 0.5},{round(100 - average, 1)}")
        label = (f"{timezone.localtime(start):%H:%M} to {timezone.localtime(start + SLOT):%H:%M}: "
                 + (f"{average:.0f}% on average, {peak:.0f}% at most" if average is not None else "no sample"))
        targets.append({"x": i, "label": label})
    if current:
        lines.append(" ".join(current))
    return {"width": len(day_slots), "lines": lines, "busy_y": 100 - server_health.BUSY_PERCENT, "targets": targets}


def hourly(day_slots):
    """[(hour start, average, peak)] of the hours with samples, newest first, for the table under the chart."""
    hours = {}
    for start, average, peak in day_slots:
        if average is None:
            continue
        key = timezone.localtime(start).replace(minute=0, second=0, microsecond=0)
        hours.setdefault(key, []).append((average, peak))
    return [(hour, sum(a for a, _p in values) / len(values), max(p for _a, p in values))
            for hour, values in sorted(hours.items(), reverse=True)]


def state_for(latest, busy, now):
    if latest is None:
        return "none"
    if now - latest.at > STALE:
        return "stale"
    if busy is not None:
        return "busy"
    if latest.cpu >= server_health.EASE_AT:
        return "high"
    return "ok"


def log_text(now, state, latest, busy, using, day_slots):
    """Everything on the page as plain text, to paste into a message."""
    lines = [f"Server health at {timezone.localtime(now):%d %b %Y %H:%M} (UK time)", f"State: {state}"]
    if latest is not None:
        lines.append(f"Latest sample {clock(latest.at, now)}: processors {latest.cpu:.0f}% busy, {latest.cpus} "
                     f"processor{'s' if latest.cpus != 1 else ''}, load {latest.load:.2f}, memory "
                     f"{latest.memory_used_mb} of {latest.memory_total_mb} MB, disk {latest.disk_used_percent}% used, "
                     f"reader {'easing off (one shop read at a time)' if latest.easing else 'at its usual pace'}")
    if busy is not None:
        lines.append(f"Busy for {busy.minutes} minutes, since {clock(busy.since, now)}")
    if using:
        lines.append("Last 15 minutes, by program: " + "; ".join(f"{words} {share:.1f}%" for words, share in using))
    lines.append("Last 24 hours, 10 minutes a line (average, peak):")
    for start, average, peak in day_slots:
        if average is not None:
            lines.append(f"  {timezone.localtime(start):%d %b %H:%M}  {average:5.1f}  {peak:5.1f}")
    return "\n".join(lines)


@staff_member_required
@not_for_editors
@require_GET
def server_page(request):
    now = timezone.now()
    latest = ServerSample.objects.order_by("-at").first()
    rows = list(ServerSample.objects.filter(at__gte=now - DAY, at__lte=now).order_by("at")
                .values_list("at", "cpu", "programs", "easing"))
    busy = server_health.busy_now(now) if latest is not None and now - latest.at <= STALE else None
    state = state_for(latest, busy, now)
    recent = [programs for at, _cpu, programs, _easing in rows if at >= now - RECENT]
    using = server_health.top_programs(averaged(recent), least=0.1)
    day_slots = slots([(at, cpu) for at, cpu, _programs, _easing in rows], now)
    easing_since = None
    if latest is not None and latest.easing:
        for at, _cpu, _programs, eased in reversed(rows):
            if not eased:
                break
            easing_since = at
    peak = max((cpu for _at, cpu, _programs, _easing in rows), default=None)
    response = render(request, "admin/server_health.html", {
        **admin.site.each_context(request),
        "title": "Server health",
        "now": now,
        "state": state,
        "latest": latest,
        "latest_ago": clock(latest.at, now) if latest else "",
        "busy": busy,
        "busy_since": clock(busy.since, now) if busy else "",
        "using": using,
        "easing_since": clock(easing_since, now) if easing_since else "",
        "chart": chart(day_slots),
        "hours": hourly(day_slots),
        "peak": peak,
        "samples": len(rows),
        "busy_percent": server_health.BUSY_PERCENT,
        "busy_minutes": int(server_health.BUSY_FOR.total_seconds() // 60),
        "ease_at": server_health.EASE_AT,
        "ease_until": server_health.EASE_UNTIL,
        "keep_days": server_health.KEEP_DAYS,
        "log": log_text(now, state, latest, busy, using, day_slots),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
