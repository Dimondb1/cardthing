"""
Pause all and what the Crawl health page shows.

Pause all is WorkerState.paused, a database row, so the web workers that flip
it, the background reader and the hourly cron all see the same thing, and a
deploy that empties the cache leaves it alone. Before the background reader
the switch was a file called crawl-paused in the cache folder; a file left
from then still counts as paused until Resume all removes it. A shop's own
pause is Retailer.reading_paused; this switch leaves those untouched, so
Resume all brings back exactly the shops that were reading before.
"""

import re
import traceback
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import BooleanField, ExpressionWrapper, Max, OuterRef, Q, Subquery
from django.utils import timezone

from .insights import clock
from .models import ImportRun, Retailer, WorkerState

FLAG_NAME = "crawl-paused"
# "HTTP Error 429: Too Many Requests" from a shop, "eBay API 429: ..." or "Amazon API 503: ..." from a marketplace.
STATUS_RE = re.compile(r"(?:HTTP Error|API) (\d{3})\b")


def flag_path():
    """The file an earlier version used for Pause all. Still honoured, never written."""
    return Path(settings.RIPRAPTOR_CACHE_DIR) / FLAG_NAME


_UNREAD = object()


def all_paused(state=_UNREAD):
    """True while Pause all is on. ``state`` (the WorkerState row, or None) saves a query when the caller has it."""
    if state is _UNREAD:
        state = WorkerState.current()
    return bool(state and state.paused) or flag_path().exists()


def pause_all():
    """Turn Pause all on."""
    WorkerState.objects.update_or_create(pk=1, defaults={"paused": True})


def resume_all():
    """Turn Pause all off, and remove the old switch file if one is left."""
    WorkerState.objects.update_or_create(pk=1, defaults={"paused": False})
    try:
        flag_path().unlink(missing_ok=True)
    except OSError:
        pass


# A drop day: the choices the Crawl health page offers for how often every shop is read, and for how long. Never
# under ten minutes: a shop's own rate limit and the three-times-the-read-time floor (Retailer.cadence_for) hold.
FAST_MINUTES = (10, 15, 30)
FAST_HOURS = (6, 12, 24)


def ebay_usage(state=_UNREAD, now=None):
    """What eBay last said was left of the day's allowances, for the Crawl health page: {"searches", "lookups",
    "asked", "reset"} with the words for when, or None before eBay has been asked. ``state`` as for all_paused."""
    from datetime import datetime

    if state is _UNREAD:
        state = WorkerState.current()
    usage = (state.ebay_usage if state is not None else None) or {}
    if not usage.get("asked_at"):
        return None
    now = now or timezone.now()
    try:
        asked = clock(datetime.fromisoformat(usage["asked_at"]), now)
    except ValueError:
        asked = ""
    reset = ""
    try:
        reset = clock(datetime.fromisoformat(usage["reset"].replace("Z", "+00:00")), now) if usage.get("reset") else ""
    except ValueError:
        reset = ""
    return {"searches": usage.get("searches"), "lookups": usage.get("lookups"), "asked": asked, "reset": reset}


def fast_read(state=_UNREAD, now=None):
    """{"minutes", "until"} while a drop day is on, else None. ``state`` as for all_paused."""
    if state is _UNREAD:
        state = WorkerState.current()
    now = now or timezone.now()
    if state is None or state.fast_until is None or state.fast_until <= now:
        return None
    return {"minutes": state.fast_minutes, "until": clock(state.fast_until, now)}


def start_fast(minutes, hours, now=None):
    """Turn a drop day on: every shop read at least every ``minutes`` for ``hours``, and every shop due now, so the
    first round starts at once. The owner, 6 Nov 2026: "during a drop day we can refresh more frequently"."""
    now = now or timezone.now()
    WorkerState.objects.update_or_create(pk=1, defaults={"fast_minutes": minutes, "fast_until": now + timedelta(hours=hours)})
    return make_all_due(now)


def stop_fast():
    """Turn the drop day off: each shop is read on its own schedule from its next read."""
    WorkerState.objects.filter(pk=1).update(fast_until=None)


# Read now puts a shop first in line: its next read is set this far back, so it is the most overdue shop and the
# reader takes it next (after eBay's hourly search). Made due "now", it went behind every shop already overdue, and
# with many waiting it could be read hours later (the owner, 24 Nov 2026: "nothing feels like it works").
READ_NOW_AHEAD = timedelta(days=1)


def read_now(shop, now=None):
    """Read now: the shop first in line, its errors and wait forgotten, as the Crawl health page's button does."""
    now = now or timezone.now()
    shop.next_read_at = now - READ_NOW_AHEAD
    shop.backoff_until = None
    shop.error_streak = 0
    shop.save(update_fields=["next_read_at", "backoff_until", "error_streak"])


def make_all_due(now=None):
    """Read all: every shop read on a schedule and not paused is due now, its errors forgotten, as Read now does for
    one. Returns (shops made due, shops left paused)."""
    now = now or timezone.now()
    scheduled = Retailer.objects.filter(is_active=True).exclude(source_type=Retailer.Source.MANUAL)
    made_due = scheduled.filter(reading_paused=False).update(next_read_at=now, backoff_until=None, error_streak=0)
    return made_due, scheduled.filter(reading_paused=True).count()


def http_status(error):
    """The HTTP status a failed read's error names, or None. Being throttled counts as 429."""
    found = STATUS_RE.search(error or "")
    if found:
        return int(found.group(1))
    return 429 if "kept throttling us" in (error or "") else None


def finish_read(item, run, began, seconds, since=None, now=None):
    """Record how a scheduled read of ``item`` went, on the shop's own schedule. Returns whether it worked.

    ``began`` is when the read started: a marketplace already read today hands back an older run, which
    counts as a read that worked and took no time.
    """
    now = now or timezone.now()
    # The owner may have changed the cadence while the shop was being read.
    item.refresh_from_db(fields=["read_every_minutes", "error_streak", "failing_since"])
    if run.error:
        from .importers import SHOP_CLOSED

        if run.error.startswith(SHOP_CLOSED):
            item.closed_for_now(now, run.error)
        elif Retailer.NOT_ELIGIBLE in run.error and item.source_type == Retailer.Source.AMAZON:
            item.waiting_for_access(now, run.error)
        else:
            item.read_failed(now, http_status(run.error), run.error)
        return False
    # Amazon's wait for enough sales is over (Retailer.awaiting_access): the owner is emailed. Only for a read that
    # asked Amazon just now, never for an earlier run handed back.
    opened = item.awaiting_access and run.started_at >= began
    if run.started_at < began:
        item.read_ok(now, 0, ok_at=run.finished_at, since=since)
    else:
        item.read_ok(now, seconds, since=since)
    if opened:
        from . import notify

        notify.amazon_open(now)
    return True


def crash_read(item, began, exc):
    """A read that stopped on an error nobody foresaw: close its run and count it as a failed read.

    Returns the error text, with the traceback for the log.
    """
    from .signals import clear_list_caches

    from . import runlog

    error = f"The read stopped on an unexpected error: {type(exc).__name__}: {exc}"[:300]
    logged = f"{item}: {error}\n{traceback.format_exc()}"
    now = timezone.now()
    # The run it opened would otherwise show as running until close_abandoned_runs finds it.
    open_runs = ImportRun.objects.filter(retailer=item, finished_at__isnull=True, started_at__gte=began)
    runlog.add(open_runs, f"{timezone.localtime(now):%H:%M:%S} ERROR {logged}")
    open_runs.update(finished_at=now, error=error)
    # Whatever it wrote before it stopped should show.
    clear_list_caches(force=True)
    item.refresh_from_db(fields=["read_every_minutes", "error_streak", "failing_since"])
    item.read_failed(now, None, error)
    return logged


def last_finished():
    """When the latest shop read finished, or None."""
    return ImportRun.objects.filter(note="").aggregate(t=Max("finished_at"))["t"]


def next_read_for(retailer, now, everything_paused=False):
    """When a shop's next read comes, judged as Retailer.due judges it.

    A paused shop has none. A shop waiting after errors is not read before its wait ends, whatever
    its next read says.
    """
    if everything_paused or retailer.reading_paused:
        return "paused"
    moments = [m for m in (retailer.next_read_at, retailer.backoff_until) if m is not None]
    if not moments or max(moments) <= now:
        return "due now"
    return clock(max(moments), now)


# The Crawl health page's lights (the owner, 24 Nov 2026: "all stores must update at least every hour ... visually show
# the ones that aren't going"): Up to date when the last read that worked is this recent (an hour, with room for the
# read itself and its turn), Late until LATE_FOR, and Not going after that, or when the shop is paused, failing or
# has never been read.
UP_TO_DATE_FOR = timedelta(minutes=75)
LATE_FOR = timedelta(hours=3)
# Amazon waiting to open its price data to the account (Retailer.awaiting_access) is none of these: it is not counted
# as behind and is never red.
OK, LATE, STOPPED, WAITING = "ok", "late", "stopped", "waiting"
WORDS = {OK: "Up to date", LATE: "Late", STOPPED: "Not going", WAITING: "Waiting for Amazon"}


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def light_for(retailer, now, everything_paused):
    """(light, why) for one shop: OK, LATE or STOPPED, and the few words that say why."""
    from django.utils.timesince import timesince

    ok_at = retailer.last_ok_at
    if everything_paused:
        return STOPPED, "Pause all is on"
    if retailer.reading_paused:
        return STOPPED, "Paused"
    if retailer.awaiting_access:
        return WAITING, "Amazon will not share its prices with this account until it has more sales"
    if retailer.backoff_until is not None and retailer.backoff_until > now:
        tries = retailer.error_streak
        return STOPPED, (f"Failing: waiting after {tries} failed tr{'y' if tries == 1 else 'ies'}" if tries
                         else "Waiting")
    if retailer.error_streak:
        return STOPPED, "Its last read failed"
    if ok_at is None:
        return STOPPED, "Never read"
    ago = timesince(ok_at, now, depth=1)
    if now - ok_at <= UP_TO_DATE_FOR:
        return OK, f"Read {ago} ago"
    if now - ok_at <= LATE_FOR:
        return LATE, f"Read {ago} ago"
    return STOPPED, f"Not read for {ago}"


def shops(now, state=_UNREAD):
    """One row per shop that is read on a schedule, for the Crawl health page, in one query.

    A shop is Reading while its latest run has not finished, then Paused, Backing off or Idle. Each has a light
    (light_for) and what it is doing now: reading, its place in line when due, or when it is next read. The shops that
    are not going come first, failing then paused, then the late ones, so a problem is at the top of a phone screen,
    not somewhere in a list of fifty.
    """
    latest_open = (
        # A pulse reads part of a shop (ImportRun.note); only whole reads say a shop is being read.
        ImportRun.objects.filter(retailer=OuterRef("pk"), note="")
        .order_by("-started_at", "-pk")
        .annotate(open=ExpressionWrapper(Q(finished_at__isnull=True), output_field=BooleanField()))
        .values("open")[:1]
    )
    latest_failed = ImportRun.objects.filter(retailer=OuterRef("pk")).exclude(error="").order_by("-started_at", "-pk")
    retailers = (
        Retailer.objects.filter(is_active=True)
        .exclude(source_type=Retailer.Source.MANUAL)
        .annotate(
            reading=Subquery(latest_open, output_field=BooleanField()),
            failed_run=Subquery(latest_failed.values("pk")[:1]),
        )
        .order_by("name")
    )
    everything_paused = all_paused(state)
    rows = []
    in_line = []
    for retailer in retailers:
        backing_off = retailer.backoff_until is not None and retailer.backoff_until > now
        if retailer.reading:
            state = "Reading"
        elif retailer.reading_paused:
            state = "Paused"
        elif backing_off:
            state = f"Backing off until {clock(retailer.backoff_until, now)}"
        else:
            state = "Idle"
        next_read = next_read_for(retailer, now, everything_paused)
        light, why = light_for(retailer, now, everything_paused)
        row = {
            "retailer": retailer,
            "state": state,
            "problem": backing_off and light != WAITING,
            "light": light, "word": WORDS[light], "why": why,
            "doing": "Reading now" if retailer.reading else "",
            "last_ok": clock(retailer.last_ok_at, now) if retailer.last_ok_at else "never",
            "next_read": next_read,
            "last_error": retailer.last_error[:160],
            "failed_run": retailer.failed_run,
            "order": ({STOPPED: 0, LATE: 1, WAITING: 2, OK: 3}[light],
                      0 if backing_off or retailer.error_streak else 1 if retailer.reading_paused else 2,
                      retailer.name.lower()),
        }
        if not row["doing"]:
            if next_read == "due now":
                in_line.append(row)
            elif next_read != "paused":
                row["doing"] = f"{'Asked again' if light == WAITING else 'Next read'} {next_read}"
        rows.append(row)
    # Its place in line, as the reader takes them (Worker.plan): eBay's hourly search first, then the shop due longest.
    epoch = now - timedelta(days=3650)
    in_line.sort(key=lambda row: (row["retailer"].source_type != Retailer.Source.EBAY,
                                  row["retailer"].next_read_at or epoch, row["retailer"].name.lower()))
    for place, row in enumerate(in_line, start=1):
        row["doing"] = f"{ordinal(place)} in line"
    rows.sort(key=lambda row: row["order"])
    return rows


def lights(rows):
    """How many shops are OK, LATE and STOPPED."""
    counts = {OK: 0, LATE: 0, STOPPED: 0, WAITING: 0}
    for row in rows:
        counts[row["light"]] += 1
    return counts


def verdict(rows, worker, everything_paused):
    """("ok" | "behind" | "stopped", why) for the top of the Crawl health page."""
    if everything_paused:
        return "stopped", "Pause all is on, so no shop is read. Tap Resume all to start again."
    if not worker.get("alive"):
        return "stopped", ("The background reader has stopped. The hourly schedule reads the shops until it is "
                           "back, and the server starts it again within five minutes.")
    counts = lights(rows)
    behind = counts[LATE] + counts[STOPPED]
    if not behind:
        return "ok", "Every shop was read in the last hour."
    return "behind", (f"{behind} shop{'s were' if behind != 1 else ' was'} not read in the last hour. They are at the "
                      "top of the list below.")


def worker_status(state, now):
    """What the Crawl health page and the admin home page say about the background reader."""
    from .worker import MEMORY_SHARE   # here: the reader imports this module

    if state is None or state.heartbeat_at is None:
        return {"known": False, "alive": False}
    return {
        "known": True,
        "alive": state.alive(now),
        "heartbeat": clock(state.heartbeat_at, now),
        "started": clock(state.started_at, now) if state.started_at else None,
        "jobs_done": state.jobs_done,
        "last_job": state.last_job,
        "requests": state.requests_last_hour,
        "errors": state.errors_last_hour,
        "note": state.note,
        "memory": state.memory_mb, "memory_limit": state.memory_limit_mb,
        # Where it restarts itself to free memory (worker.MEMORY_SHARE).
        "memory_restart": round(state.memory_limit_mb * MEMORY_SHARE) if state.memory_limit_mb else None,
    }


# The Log to copy on Crawl health (the owner, 24 Nov 2026, of eBay health first: "keep the log for you"): everything
# about the reading as plain text, for the owner to paste to Claude.
LOG_PROBLEMS = 40


def log_text(now, state, rows, worker, everything_paused):
    """The Crawl health page's Log to copy: the verdict, the background reader and what it last planned, the import
    lock and who waits for it, how long each kind of shop takes to read against the time the reader has, the reads of
    the last hour, every shop worst first, and the reader's own warnings (catalogue/worklog.py). No key, token or
    address of a person is in it. Two queries."""
    from collections import defaultdict

    from django.db.models import Count, Q

    from . import worker as reader, worklog

    def at(moment):
        if moment is None:
            return "none"
        if isinstance(moment, str):
            from datetime import datetime

            try:
                moment = datetime.fromisoformat(moment)
            except ValueError:
                return moment
        minutes = int((now - moment).total_seconds() // 60)
        ago = f"{minutes} min ago" if 0 <= minutes < 120 else f"{minutes // 60} h ago" if minutes >= 0 else \
            f"in {-minutes} min" if minutes > -120 else f"in {-minutes // 60} h"
        return f"{timezone.localtime(moment):%d %b %H:%M} ({ago})"

    light, why = verdict(rows, worker, everything_paused)
    counts = lights(rows)
    out = [f"RipRaptor crawl log, {timezone.localtime(now):%a %d %b %Y %H:%M} UK time",
           f"Verdict: {light}. {why}",
           f"Shops read on a schedule: {len(rows)}; up to date {counts[OK]}, late {counts[LATE]}, "
           f"not going {counts[STOPPED]}, waiting for Amazon {counts[WAITING]}"]

    out += ["", "BACKGROUND READER"]
    if state is None:
        out.append("never started")
    else:
        out += [f"running: {'yes' if worker.get('alive') else 'NO'}, last sign of life {at(state.heartbeat_at)}, "
                f"started {at(state.started_at)}",
                f"pause all: {'ON' if everything_paused else 'off'}; drop day: "
                + (f"every {state.fast_minutes} min until {at(state.fast_until)}"
                   if state.fast_until and state.fast_until > now else "off"),
                f"jobs done since it started: {state.jobs_done}, last job: {state.last_job or 'none'}",
                f"requests to shops in the last hour: {state.requests_last_hour}, errors: {state.errors_last_hour}",
                f"memory: {state.memory_mb or '?'} MB of a {state.memory_limit_mb or '?'} MB limit"]
        if state.note:
            out.append(f"note: {state.note}")
    out.append(f"limits: {reader.MAX_READS} whole-shop reads at once, {reader.MAX_WEBSITE_READS} of website shops, "
               f"{reader.GLOBAL_RATE[0]:g} requests a second in all")
    plan = worklog.last_plan()
    if not plan:
        out.append("last plan: none noted")
    elif plan.get("paused"):
        out.append(f"last plan {at(plan.get('at'))}: nothing planned, Pause all is on")
    else:
        queue, lock = plan.get("queue", {}), plan.get("lock", {})
        jobs = ", ".join(f"{job['label']} ({job['minutes']} min)" for job in plan.get("running", [])) or "nothing"
        out += [f"last plan {at(plan.get('at'))}, {plan.get('threads')} threads",
                f"  running then: {jobs}",
                f"  due: {queue.get('reads', 0)} reads ({queue.get('reads_with_room', 0)} with room to start), "
                f"{queue.get('checks', 0)} shops to check, {queue.get('pulses', 0)} pulses, {queue.get('other', 0)} other",
                "  import lock: " + (f"held by the reader since {at(lock.get('held_since'))}"
                                     if lock.get("held_since") else "not held by the reader")
                + (f"; refused since {at(lock.get('refused_since'))}: {lock.get('refused')}"
                   if lock.get("refused_since") else "")]
    waiting = reader.lock_waiters()
    out.append(f"waiting for the import lock now: {', '.join(waiting) if waiting else 'nobody'}")

    out += ["", "READ TIME BY KIND OF SHOP (each shop's last read)"]
    kinds = defaultdict(list)
    for row in rows:
        kinds[row["retailer"].source_type].append(row["retailer"])
    need = website_need = 0
    for kind, shops_of_kind in sorted(kinds.items()):
        timed = [shop for shop in shops_of_kind if shop.last_read_seconds is not None]
        total = sum(shop.last_read_seconds for shop in timed)
        slowest = max(timed, key=lambda shop: shop.last_read_seconds, default=None)
        out.append(f"{kind}: {len(shops_of_kind)} shops, {len(timed)} timed, {total:,} s in all"
                   + (f", slowest {slowest.name} {slowest.last_read_seconds:,} s" if slowest else ""))
        if kind not in (Retailer.Source.AMAZON,):
            need += total
            if kind == Retailer.Source.WEBSITE:
                website_need += total
    out.append(f"reading every shop once takes about {need // 60:,} minutes of reading; the reader has "
               f"{reader.MAX_READS * 60} an hour ({website_need // 60:,} of {reader.MAX_WEBSITE_READS * 60} for "
               "website shops)")

    runs = ImportRun.objects.filter(note="", started_at__gte=now - timedelta(hours=1)).aggregate(
        started=Count("pk"), finished=Count("pk", filter=Q(finished_at__isnull=False)),
        failed=Count("pk", filter=~Q(error="")))
    out += ["", "READS IN THE LAST HOUR",
            f"started {runs['started']}, finished {runs['finished']}, failed {runs['failed']}"]

    out += ["", "SHOPS (worst first): name | light | kind | why | doing | last good read | took | every | errors | last error"]
    for row in rows:
        shop = row["retailer"]
        out.append(" | ".join([
            shop.name, row["word"], shop.source_type, row["why"], row["doing"] or "-",
            at(shop.last_ok_at), f"{shop.last_read_seconds} s" if shop.last_read_seconds is not None else "-",
            f"{shop.every_minutes} min", str(shop.error_streak), (shop.last_error or "-")[:120],
        ]))

    kept = worklog.entries()
    problems = [lines for bad, lines in kept if bad][-LOG_PROBLEMS:]
    out += ["", f"READER LOG: WARNINGS AND ERRORS (the newest {LOG_PROBLEMS}, oldest first)"]
    out += [line for lines in problems for line in lines] or ["none kept"]
    return worklog.scrub("\n".join(out))
