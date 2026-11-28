"""
Server health: how busy the server's processors are now, which program is using them, memory and disk, and the last 24
hours of three things side by side: the processors (with the times the background reader held new jobs shaded), how
quickly the website served its pages, and how much the reader asked the shops. From the samples the reader takes each
minute (catalogue/server_health.py, catalogue/response_times.py) and its last heartbeat. Staff only, never for
editors. The owner, 25 Nov 2026: "the server is running at 99% cpu how do we ensure that we monitor this from the
ripraptor admin"; 26 Nov 2026: "historical CPU usage, scraping activity and website response times".

It also says what the reader's own processor time went on, by kind of work (reading shops, stock checks, building the
page lists, planning, upkeep: Worker.spent), what the hourly tidy's last run cost step by step (tidy_all keeps its
figures in the cache), and the log gives each ten minutes' share by program, so a busy spell can be put down to the
reader, the website or a scheduled job after the fact. The owner, 28 Nov 2026, of a server
with a second processor: "here we are cpu sitting at 99 percent again"; the log then could not say who.

Six queries: the latest sample, the last 24 hours, two for how long the processors have been busy, the crawler settings
and the reader's heartbeat.
"""

from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import crawl, server_health
from catalogue.insights import clock
from catalogue.models import CrawlerSettings, ServerSample, WorkerState
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


class Slot:
    """Ten minutes of samples, added up."""

    def __init__(self, start):
        self.start, self.samples = start, 0
        self.cpu_total, self.peak = 0.0, 0.0
        self.pages, self.page_ms_total, self.slowest, self.slow = 0, 0, 0, 0
        self.requests, self.held, self.most_jobs = 0, 0, 0
        self.programs = {}   # program -> its shares added up, for share()

    def add(self, cpu, held, jobs, shop_requests, pages, page_ms, slowest, slow, programs=None):
        self.samples += 1
        self.cpu_total += cpu
        for name, share in (programs or {}).items():
            self.programs[name] = self.programs.get(name, 0.0) + share
        self.peak = max(self.peak, cpu)
        self.held += bool(held)
        self.most_jobs = max(self.most_jobs, jobs)
        self.requests += shop_requests
        if pages and page_ms is not None:
            self.pages += pages
            self.page_ms_total += pages * page_ms
            self.slowest = max(self.slowest, slowest or 0)
            self.slow += slow

    @property
    def average(self):
        return self.cpu_total / self.samples if self.samples else None

    @property
    def page_ms(self):
        """The average time a page took, over every page served in the slot."""
        return round(self.page_ms_total / self.pages) if self.pages else None

    @property
    def requests_a_minute(self):
        return self.requests / self.samples if self.samples else None

    def share(self, program):
        """The program's average share of all the processors over the slot, in percent."""
        return self.programs.get(program, 0.0) / self.samples if self.samples else 0.0

    def label(self):
        return f"{timezone.localtime(self.start):%H:%M} to {timezone.localtime(self.start + SLOT):%H:%M}"


def slots(rows, now):
    """The last 24 hours in SLOT steps, oldest first. ``rows`` as server_page reads them."""
    start = now - DAY
    found = [Slot(start + SLOT * i) for i in range(int(DAY / SLOT))]
    for at, cpu, programs, held, jobs, shop_requests, pages, page_ms, slowest, slow, _work in rows:
        index = int((at - start) / SLOT)
        if 0 <= index < len(found):
            found[index].add(cpu, held, jobs, shop_requests, pages, page_ms, slowest, slow, programs)
    return found


def reader_work(samples):
    """[(words, seconds of processor time a minute, percent of the reader's own)] for each kind of work the reader
    did over ``samples`` (each a sample's reader_work), the most first; [] when none of them carries any."""
    from catalogue.worker import WORK

    counted = [work for work in samples if work]
    totals = {}
    for work in counted:
        for kind, seconds in work.items():
            totals[kind] = totals.get(kind, 0.0) + seconds
    whole = sum(totals.values())
    if not whole:
        return []
    return [(WORK.get(kind, kind), seconds / len(counted), seconds * 100 / whole)
            for kind, seconds in sorted(totals.items(), key=lambda item: -item[1])]


def rounded_up(value, step):
    return max(step, -(-int(value) // step) * step)


def chart(day_slots, value, top, label, line_at=None, shade=None):
    """What the template draws for one chart, in a box one unit a slot wide and 100 high, stretched to the page: the
    line (point strings, broken where a slot has no figure), an optional dashed line at ``line_at``, the slots to shade
    (``shade``) and a hover target a slot. ``value`` and ``label`` take a slot. The figures on the axis are text beside
    it, so they stay readable at any width."""
    lines, current, targets, shaded = [], [], [], []
    for i, slot in enumerate(day_slots):
        figure = value(slot) if slot.samples else None
        if figure is None:
            if current:
                lines.append(" ".join(current))
            current = []
        else:
            current.append(f"{i + 0.5},{round(100 - min(figure, top) * 100 / top, 1)}")
        targets.append({"x": i, "label": f"{slot.label()}: {label(slot) if slot.samples else 'no sample'}"})
        if shade is not None and slot.samples and shade(slot):
            shaded.append(i)
    if current:
        lines.append(" ".join(current))
    return {"width": len(day_slots), "lines": lines, "targets": targets, "shaded": shaded,
            "line_y": None if line_at is None else round(100 - line_at * 100 / top, 1)}


def charts(day_slots, controls):
    """The three charts of the last 24 hours, with their scales."""
    ms_top = rounded_up(max((s.page_ms or 0 for s in day_slots), default=0) * 1.1, 250)
    requests_top = rounded_up(max((s.requests_a_minute or 0 for s in day_slots), default=0) * 1.1, 10)
    return {
        "cpu": {**chart(day_slots, lambda s: s.average, 100,
                        lambda s: f"{s.average:.0f}% on average, {s.peak:.0f}% at most"
                                  + (f"; new jobs held {s.held} of {s.samples} minutes" if s.held else ""),
                        line_at=controls.cpu_pause_percent, shade=lambda s: s.held),
                "top": "100%", "middle": "50%"},
        "pages": {**chart(day_slots, lambda s: s.page_ms, ms_top,
                          lambda s: (f"{s.pages} pages, {s.page_ms} ms on average, slowest {s.slowest} ms"
                                     + (f", {s.slow} over a second" if s.slow else "")) if s.pages else "no pages"),
                  "top": f"{ms_top} ms", "middle": f"{ms_top // 2} ms"},
        "reading": {**chart(day_slots, lambda s: s.requests_a_minute, requests_top,
                            lambda s: f"{s.requests_a_minute:.0f} requests to shops a minute, at most {s.most_jobs} "
                                      f"job{'s' if s.most_jobs != 1 else ''} at once"
                                      + (f"; new jobs held {s.held} of {s.samples} minutes" if s.held else ""),
                            shade=lambda s: s.held),
                    "top": requests_top, "middle": requests_top // 2},
    }


def hourly(day_slots):
    """Each hour with samples, newest first, for the table under the charts: its start, processors' average and peak,
    pages' average and slowest time, requests to shops a minute and minutes new jobs were held."""
    hours = {}
    for slot in day_slots:
        if not slot.samples:
            continue
        key = timezone.localtime(slot.start).replace(minute=0, second=0, microsecond=0)
        hours.setdefault(key, []).append(slot)
    found = []
    for hour, parts in sorted(hours.items(), reverse=True):
        samples = sum(s.samples for s in parts)
        pages = sum(s.pages for s in parts)
        found.append({
            "hour": hour,
            "average": sum(s.cpu_total for s in parts) / samples,
            "peak": max(s.peak for s in parts),
            "page_ms": round(sum(s.page_ms_total for s in parts) / pages) if pages else None,
            "slowest": max(s.slowest for s in parts) if pages else None,
            "requests": sum(s.requests for s in parts) / samples,
            "held": sum(s.held for s in parts),
        })
    return found


def minutes_and_seconds(seconds):
    """How long, in words: "45 seconds", "12 minutes 4 seconds"."""
    whole = max(0, int(round(seconds)))
    parts = [(whole // 60, "minute"), (whole % 60, "second")]
    return " ".join(f"{number} {unit}{'' if number == 1 else 's'}" for number, unit in parts if number) or "0 seconds"


def tidy_run(now):
    """The hourly tidy's last run (tidy_all keeps its figures in the cache), for the page: when it ended, how long
    it took, its processor seconds and its steps, the heaviest first, those under half a second left out. None
    when none is kept."""
    from datetime import datetime

    from catalogue.management.commands.tidy_all import last_run

    kept = last_run()
    if kept is None:
        return None
    try:
        at = datetime.fromisoformat(kept["at"])
        steps = sorted(((str(name), float(seconds)) for name, seconds in kept["steps"]), key=lambda step: -step[1])
        return {"at": clock(at, now), "took": minutes_and_seconds(float(kept["seconds"])),
                "cpu": minutes_and_seconds(float(kept["cpu"])), "cpu_seconds": float(kept["cpu"]),
                "steps": [(name, seconds) for name, seconds in steps if seconds >= 0.5]}
    except (KeyError, TypeError, ValueError):
        return None


def state_for(latest, busy, now, controls):
    if latest is None:
        return "none"
    if now - latest.at > STALE:
        return "stale"
    if busy is not None:
        return "busy"
    if latest.cpu >= controls.cpu_pause_percent:
        return "high"
    return "ok"


def log_text(now, state, latest, busy, using, day_slots, controls, reader, work=(), tidy=None):
    """Everything on the page as plain text, to paste into a message."""
    lines = [f"Server health at {timezone.localtime(now):%d %b %Y %H:%M} (UK time)", f"State: {state}",
             f"Crawler settings: {controls.summary()}"]
    if reader:
        lines.append(f"Reader: {reader['words']}")
    if latest is not None:
        lines.append(f"Latest sample {clock(latest.at, now)}: processors {latest.cpu:.0f}% busy, {latest.cpus} "
                     f"processor{'s' if latest.cpus != 1 else ''}, load {latest.load:.2f}, memory "
                     f"{latest.memory_used_mb} of {latest.memory_total_mb} MB, disk {latest.disk_used_percent}% used, "
                     f"{latest.jobs} job{'s' if latest.jobs != 1 else ''} running, {latest.shop_requests} requests to "
                     f"shops in the minute, new jobs {'held' if latest.held else 'not held'}, {latest.pages} pages"
                     + (f" at {latest.page_ms} ms on average" if latest.page_ms is not None else ""))
    if busy is not None:
        lines.append(f"Busy for {busy.minutes} minutes, since {clock(busy.since, now)}")
    if using:
        lines.append("Last 15 minutes, by program: " + "; ".join(f"{words} {share:.1f}%" for words, share in using))
    if work:
        lines.append("Last 15 minutes, the reader's own processor time by kind of work, seconds a minute: "
                     + "; ".join(f"{words} {seconds:.1f} ({share:.0f}%)" for words, seconds, share in work))
    if tidy:
        lines.append(f"Hourly tidy, last run ended {tidy['at']}: took {tidy['took']}, {tidy['cpu_seconds']:.0f} "
                     "seconds of processor time; by step, seconds: "
                     + ("; ".join(f"{name} {seconds:.0f}" for name, seconds in tidy["steps"]) or "none over half a second"))
    lines.append("Last 24 hours, 10 minutes a line: processors average and peak %, pages and their average ms, "
                 "requests to shops a minute, minutes held, then the share of the processors by program: reader %, "
                 "website %, scheduled jobs %")
    for slot in day_slots:
        if slot.samples:
            lines.append(f"  {timezone.localtime(slot.start):%d %b %H:%M}  {slot.average:5.1f}  {slot.peak:5.1f}  "
                         f"{slot.pages:5d}  {slot.page_ms if slot.page_ms is not None else '-':>5}  "
                         f"{slot.requests_a_minute:5.1f}  {slot.held:2d}  {slot.share(server_health.READER):5.1f}  "
                         f"{slot.share(server_health.WEBSITE):5.1f}  {slot.share(server_health.JOBS):5.1f}")
    return "\n".join(lines)


def reader_now(state, controls, now):
    """What the background reader is doing about the processors, from its last heartbeat, in words, or None when it
    is not running."""
    if state is None or not state.alive(now):
        return None
    paused = crawl.all_paused(state)
    busy = f"{state.cpu_now:.0f}%" if state.cpu_now is not None else "not measured yet"
    if paused:
        words = "Pause all is on: no shop is read or checked."
    elif state.held_since:
        words = (f"New jobs held since {clock(state.held_since, now)}: the processors are {busy} busy over "
                 f"{controls.cpu_window_seconds} seconds. Jobs start again under {controls.cpu_resume_percent}%.")
    else:
        words = (f"Jobs start as usual: the processors are {busy} busy over {controls.cpu_window_seconds} seconds, "
                 f"and new jobs are held from {controls.cpu_pause_percent}%.")
    if state.slowed:
        words += f" Running jobs are slowed by {controls.slow_seconds} seconds a request."
    running = len(state.running or [])
    words += f" {running} job{'s' if running != 1 else ''} running, at most {controls.max_jobs} at once."
    return {"words": words, "held": bool(state.held_since), "paused": paused}


@staff_member_required
@not_for_editors
@require_GET
def server_page(request):
    now = timezone.now()
    latest = ServerSample.objects.order_by("-at").first()
    rows = list(ServerSample.objects.filter(at__gte=now - DAY, at__lte=now).order_by("at")
                .values_list("at", "cpu", "programs", "held", "jobs", "shop_requests", "pages", "page_ms",
                             "page_ms_slowest", "pages_slow", "reader_work"))
    controls = CrawlerSettings.current()
    reader = reader_now(WorkerState.current(), controls, now)
    busy = server_health.busy_now(now) if latest is not None and now - latest.at <= STALE else None
    state = state_for(latest, busy, now, controls)
    recent = [row[2] for row in rows if row[0] >= now - RECENT]
    using = server_health.top_programs(averaged(recent), least=0.1)
    work = reader_work([row[10] for row in rows if row[0] >= now - RECENT])
    tidy = tidy_run(now)
    day_slots = slots(rows, now)
    peak = max((row[1] for row in rows), default=None)
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
        "work": work,
        "tidy": tidy,
        "charts": charts(day_slots, controls),
        "hours": hourly(day_slots),
        "peak": peak,
        "samples": len(rows),
        "controls": controls,
        "reader": reader,
        "busy_percent": server_health.BUSY_PERCENT,
        "busy_minutes": int(server_health.BUSY_FOR.total_seconds() // 60),
        "keep_days": server_health.KEEP_DAYS,
        "log": log_text(now, state, latest, busy, using, day_slots, controls, reader, work, tidy),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
