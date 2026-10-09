"""
Pause all: one switch that stops every scheduled shop read.

The switch is a file in the shared cache folder (RIPRAPTOR_CACHE_DIR), so the
web workers that flip it and the hourly import that obeys it see the same
thing. It is a file rather than a cache entry because a deploy empties the
cache, and a pause the owner set must survive a deploy. A shop's own pause is
Retailer.reading_paused; this switch leaves those untouched, so Resume all
brings back exactly the shops that were reading before.
"""

from pathlib import Path

from django.conf import settings
from django.db.models import BooleanField, ExpressionWrapper, Max, OuterRef, Q, Subquery

from .insights import clock
from .models import ImportRun, Retailer

FLAG_NAME = "crawl-paused"


def flag_path():
    return Path(settings.RIPRAPTOR_CACHE_DIR) / FLAG_NAME


def all_paused():
    """True while Pause all is on."""
    return flag_path().exists()


def pause_all():
    """Turn Pause all on. Raises OSError when the cache folder cannot be written."""
    path = flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def resume_all():
    """Turn Pause all off. Raises OSError when the cache folder cannot be written."""
    flag_path().unlink(missing_ok=True)


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


def shops(now):
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
    everything_paused = all_paused()
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
