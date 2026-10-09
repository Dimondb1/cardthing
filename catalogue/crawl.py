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
    item.refresh_from_db(fields=["read_every_minutes", "error_streak"])
    if run.error:
        item.read_failed(now, http_status(run.error), run.error)
        return False
    if run.started_at < began:
        item.read_ok(now, 0, ok_at=run.finished_at, since=since)
    else:
        item.read_ok(now, seconds, since=since)
    return True


def crash_read(item, began, exc):
    """A read that stopped on an error nobody foresaw: close its run and count it as a failed read.

    Returns the error text, with the traceback for the log.
    """
    from .signals import clear_list_caches

    error = f"The read stopped on an unexpected error: {type(exc).__name__}: {exc}"[:300]
    logged = f"{item}: {error}\n{traceback.format_exc()}"
    now = timezone.now()
    # The run it opened would otherwise show as running until close_abandoned_runs finds it.
    ImportRun.objects.filter(retailer=item, finished_at__isnull=True, started_at__gte=began).update(
        finished_at=now, error=error
    )
    # Whatever it wrote before it stopped should show.
    clear_list_caches(force=True)
    item.refresh_from_db(fields=["read_every_minutes", "error_streak"])
    item.read_failed(now, None, error)
    return logged


def last_finished():
    """When the latest shop read finished, or None."""
    return ImportRun.objects.aggregate(t=Max("finished_at"))["t"]


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


def shops(now, state=_UNREAD):
    """One row per shop that is read on a schedule, for the Crawl health page, in one query.

    A shop is Reading while its latest run has not finished, then Paused, Backing off or Idle.
    """
    latest_open = (
        ImportRun.objects.filter(retailer=OuterRef("pk"))
        .order_by("-started_at", "-pk")
        .annotate(open=ExpressionWrapper(Q(finished_at__isnull=True), output_field=BooleanField()))
        .values("open")[:1]
    )
    retailers = (
        Retailer.objects.filter(is_active=True)
        .exclude(source_type=Retailer.Source.MANUAL)
        .annotate(reading=Subquery(latest_open, output_field=BooleanField()))
        .order_by("name")
    )
    everything_paused = all_paused(state)
    rows = []
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
        rows.append({
            "retailer": retailer,
            "state": state,
            "problem": backing_off,
            "last_ok": clock(retailer.last_ok_at, now) if retailer.last_ok_at else "never",
            "next_read": next_read,
            "last_error": retailer.last_error[:160],
        })
    return rows


def worker_status(state, now):
    """What the Crawl health page and the admin home page say about the background reader."""
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
    }
