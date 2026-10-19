"""
The cached lists the busiest pages read (home deals, Deals, Out now, Coming soon), kept so a visitor never
waits for one to be built while another copy is there.

Each list is stored as an Entry: the rows and when their build started. A price change no longer deletes the
lists; it notes the time of the clear (``mark_stale``) under one small key, and any list built before that
time is stale. What happens next:

- a visitor who finds a list younger than RIPRAPTOR_LIST_MAX_STALE_SECONDS is served it at once, stale or not;
- the background jobs (web.warming, run by the reader every few seconds, at the end of each import and from
  cron every five minutes) build the stale and ageing lists again and replace them in place, so the cache is
  never empty after the first fill;
- a visitor builds a list only when there is none, or it is older than the maximum staleness, and then
  under a short lock in the shared cache, so concurrent visitors wait for that one build rather than all
  running their own.

Every price on these lists shows when it was checked, and no list is ever served more than
RIPRAPTOR_LIST_MAX_STALE_SECONDS after its build started, so a price that crosses the stale rule leaves at
the next build, at most that long later.

Visitors' requests never start a build in the background. The site runs gunicorn sync workers, two on a
small server: a build is several seconds of Python holding the worker's interpreter lock, so a thread in a
web worker would slow every page that worker serves meanwhile, and is killed mid-build whenever the worker
restarts. The reader is a separate process that already builds the lists, so it does the refreshing.
"""

import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache as default_cache

# When the lists were last cleared: {"at": seconds, "forced_at": seconds} (time.time()).
CLEARED_KEY = "web:lists:cleared"
LOCK_SUFFIX = ":building"
# A build holds its lock no longer than this; a process killed mid-build leaves a lock that lapses.
LOCK_SECONDS = 120
# A visitor who finds another process building a missing list waits this long for it, then builds its own.
WAIT_SECONDS = 20
WAIT_STEP = 0.1
# The cache keeps an entry this long. Far longer than it may be served (the maximum staleness), so the cache
# is not emptied by expiry while the reader is down: an old entry is built again by the visitor who finds it.
KEEP_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Entry:
    value: object
    built_at: float   # time.time() when the build started, before it read anything


def max_stale():
    return settings.RIPRAPTOR_LIST_MAX_STALE_SECONDS


def lock_key(key):
    return key + LOCK_SUFFIX


def entry(key, cache=None):
    """The Entry under ``key``, or None when there is none (or something else is stored there)."""
    found = (cache or default_cache).get(key)
    return found if isinstance(found, Entry) else None


def peek(key, cache=None):
    """The rows under ``key`` whatever their age, or None. For checks and tests; pages call get_or_build."""
    found = entry(key, cache)
    return None if found is None else found.value


def cleared(cache=None):
    return (cache or default_cache).get(CLEARED_KEY) or {}


def mark_stale(force=False, cache=None, now=None):
    """Every list built before now is stale. A forced clear (the end of an import, an owner's fix) asks the
    jobs to build them again on their next turn; an ordinary one lets a list stay until it is at least
    RIPRAPTOR_CACHE_CLEAR_SECONDS old, so a run of saves does not keep the reader building."""
    cache = cache or default_cache
    now = time.time() if now is None else now
    marks = dict(cleared(cache))
    marks["at"] = now
    if force:
        marks["forced_at"] = now
    cache.set(CLEARED_KEY, marks, None)


def is_stale(key, cache=None, marks=None):
    """True when the list under ``key`` was built before the last clear. A missing list is not stale."""
    found = entry(key, cache)
    marks = cleared(cache) if marks is None else marks
    return found is not None and found.built_at < marks.get("at", 0)


def is_due(key, now=None, cache=None, marks=None):
    """Whether the jobs should build ``key`` again: missing, cleared by a forced clear, cleared by an ordinary
    one and at least RIPRAPTOR_CACHE_CLEAR_SECONDS old, or within a minute of RIPRAPTOR_HOME_CACHE_SECONDS old."""
    now = time.time() if now is None else now
    found = entry(key, cache)
    return found is None or entry_due(found, now, cleared(cache) if marks is None else marks)


def entry_due(found, now, marks):
    age = now - found.built_at
    if found.built_at < marks.get("forced_at", 0):
        return True
    if found.built_at < marks.get("at", 0) and age >= settings.RIPRAPTOR_CACHE_CLEAR_SECONDS:
        return True
    return age >= refresh_age()


def refresh_age():
    """How old a list may grow before the jobs build it again although nothing cleared it."""
    seconds = settings.RIPRAPTOR_HOME_CACHE_SECONDS
    return max(seconds - 60, seconds // 2)


def usable(found, now, cache=None):
    """Whether a visitor may be served ``found``: younger than the maximum staleness. Where stale copies are
    not served (RIPRAPTOR_SERVE_STALE off, as in the tests, which run no background jobs), the request does
    the jobs' work instead: a copy they would build again now (entry_due) is built by the request."""
    if found is None or now - found.built_at >= max_stale():
        return False
    return settings.RIPRAPTOR_SERVE_STALE or not entry_due(found, now, cleared(cache))


def store(key, build, cache=None):
    cache = cache or default_cache
    started = time.time()
    value = build()
    cache.set(key, Entry(value, started), KEEP_SECONDS)
    return value


def get_or_build(key, build, fresh=False, cache=None):
    """The rows under ``key``: served from the cache when younger than the maximum staleness, else built.

    ``fresh`` (the jobs) builds them again and replaces them in place, unless another process holds the
    build lock, when the copy that process is replacing is returned instead of building twice.
    """
    cache = cache or default_cache
    found = entry(key, cache)
    if not fresh and usable(found, time.time(), cache):
        return found.value
    lock = lock_key(key)
    if cache.add(lock, 1, LOCK_SECONDS):
        try:
            return store(key, build, cache)
        finally:
            cache.delete(lock)
    if fresh and found is not None:
        return found.value
    return wait_then_build(key, build, cache)


def wait_then_build(key, build, cache):
    """Another process is building ``key``: wait for its copy, up to WAIT_SECONDS, then build our own. When
    the lock goes without a copy (the build failed), take it over at once."""
    give_up = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < give_up:
        time.sleep(WAIT_STEP)
        found = entry(key, cache)
        if usable(found, time.time(), cache):
            return found.value
        if cache.add(lock_key(key), 1, LOCK_SECONDS):
            try:
                return store(key, build, cache)
            finally:
                cache.delete(lock_key(key))
    return store(key, build, cache)
