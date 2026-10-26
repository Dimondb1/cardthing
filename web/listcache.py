"""
The cached lists the busiest pages read (home deals, Deals, Out now, Coming soon), kept so a visitor never
waits for one to be built while another copy is there.

Each list is stored as an Entry: the rows and when their build started. A price change no longer deletes the
lists; it notes the time of the clear (``mark_stale``), and any list built before that time is stale. What
happens next:

- a visitor who finds a list younger than RIPRAPTOR_LIST_MAX_STALE_SECONDS is served it at once, stale or not;
- the background jobs (web.warming, run by the reader every few seconds, at the end of each import and from
  cron every five minutes) build the stale and ageing lists again and replace them in place, so the cache is
  never empty after the first fill;
- a visitor builds a list only when there is none, it is older than the maximum staleness, or the owner
  changed something since it was built (a hard clear), and then under a lock every process shares, so
  concurrent visitors wait for that one build rather than all running their own.

Every price on these lists shows when it was checked, and no list is ever served more than
RIPRAPTOR_LIST_MAX_STALE_SECONDS after its build started, so a price that crosses the stale rule leaves at
the next build, at most that long later.

Visitors' requests never start a build in the background. The site runs gunicorn sync workers, two on a
small server: a build is several seconds of Python holding the worker's interpreter lock, so a thread in a
web worker would slow every page that worker serves meanwhile, and is killed mid-build whenever the worker
restarts. The reader is a separate process that already builds the lists, so it does the refreshing.
"""

import contextvars
import fcntl
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.filebased import FileBasedCache

# When the lists were last cleared, in a memory cache: {"at": seconds, "forced_at": seconds, "hard_at": seconds}
# (time.time()). A file cache keeps them in a file of its own instead (FileState).
CLEARED_KEY = "web:lists:cleared"
LOCK_SUFFIX = ":building"
# A memory cache's build lock lapses after this; a file lock goes when the process holding it ends.
LOCK_SECONDS = 120
# A visitor who finds another process building a missing list waits this long for it, then builds its own.
WAIT_SECONDS = 20
WAIT_STEP = 0.1
# The cache keeps an entry this long. Far longer than it may be served (the maximum staleness), so the cache
# is not emptied by expiry while the reader is down: an old entry is built again by the visitor who finds it.
KEEP_SECONDS = 24 * 3600
MARKS = ("at", "forced_at", "hard_at")
# The folder, inside a file cache's own, for the marks and the build locks. Django's cache only lists, culls
# and clears the *.djcache files at the top of its folder, so nothing here is culled when the cache is full.
STATE_FOLDER = "listcache"

# Set while the owner's own request runs (web.middleware.OwnerEditsMiddleware): a clear then is a hard one.
owner_editing = contextvars.ContextVar("owner_editing", default=False)


def raise_each(marks, names, now):
    """Raise each mark in ``names`` to ``now``, never lowering one, unless it is ahead of the clock (it was
    made before the clock was set back)."""
    for name in names:
        old = marks.get(name, 0)
        marks[name] = now if old > now + CLOCK_SLACK else max(old, now)


class MemoryState:
    """Marks and build locks in a memory cache, which belongs to this process alone: a lock here makes its
    read-modify-write atomic, and the cache's own add is atomic."""

    lock = threading.Lock()

    def __init__(self, cache):
        self.cache = cache

    def marks(self):
        return self.cache.get(CLEARED_KEY) or {}

    def raise_marks(self, names, now):
        with self.lock:
            marks = dict(self.marks())
            raise_each(marks, names, now)
            self.cache.set(CLEARED_KEY, marks, None)

    def take(self, key):
        return key if self.cache.add(lock_key(key), 1, LOCK_SECONDS) else None

    def release(self, held):
        self.cache.delete(lock_key(held))


class FileState:
    """Marks and build locks as files beside a file cache, shared by every process on the server.

    Django's file cache has no atomic add (it checks for the file, then writes it), so two workers could both
    take a lock made with it, and a read-modify-write of one cache key loses one of two clears made at once.
    Here the marks file is rewritten under an exclusive flock, each mark only ever raised to the writer's own
    time, and replaced whole (os.replace) so a reader never sees half of it. A build lock is a flock on a file
    per list, which the kernel releases when the process holding it ends, so a killed build never leaves one.
    """

    def __init__(self, directory):
        self.folder = os.path.join(directory, STATE_FOLDER)
        self.path = os.path.join(self.folder, "cleared.json")

    def marks(self):
        try:
            with open(self.path) as handle:
                found = json.load(handle)
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def raise_marks(self, names, now):
        os.makedirs(self.folder, exist_ok=True)
        with open(self.path + ".lock", "a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            marks = self.marks()
            now = max(now, time.time())   # taken inside the lock, so a later writer's time is never lower
            raise_each(marks, names, now)
            temporary = f"{self.path}.{os.getpid()}.{threading.get_ident()}"
            with open(temporary, "w") as handle:
                json.dump(marks, handle)
            os.replace(temporary, self.path)

    def take(self, key):
        os.makedirs(self.folder, exist_ok=True)
        name = hashlib.sha1(key.encode()).hexdigest()[:16]
        handle = open(os.path.join(self.folder, f"{name}.building"), "a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle

    def release(self, held):
        held.close()   # closing the file lets go of the flock


def resolve(cache):
    return cache if cache is not None else caches["default"]


def state(cache=None):
    cache = resolve(cache)
    if isinstance(cache, FileBasedCache):
        return FileState(cache._dir)
    return MemoryState(cache)


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
    found = resolve(cache).get(key)
    return found if isinstance(found, Entry) else None


def peek(key, cache=None):
    """The rows under ``key`` whatever their age, or None. For checks and tests; pages call get_or_build."""
    found = entry(key, cache)
    return None if found is None else found.value


# A mark further ahead of the clock than this was made before the clock was set back: it is ignored, and the
# next clear replaces it, so a clock change never leaves every list stale.
CLOCK_SLACK = 60


def cleared(cache=None):
    """The time of the last clear of each kind: {"at", "forced_at", "hard_at"}, each missing until one ran."""
    marks = state(cache).marks()
    ahead = time.time() + CLOCK_SLACK
    return {name: value for name, value in marks.items() if name in MARKS and value <= ahead}


def mark_stale(force=False, hard=False, cache=None, now=None):
    """Every list built before now is stale. A forced clear (the end of an import, a restock) asks the jobs to
    build them again on their next turn; an ordinary one lets a list stay until it is at least
    RIPRAPTOR_CACHE_CLEAR_SECONDS old, so a run of saves does not keep the reader building. A hard clear (the
    owner's own edits: an admin save, a hide or a merge on the site) is not served at all: the next visitor
    or job builds the list first, as when there was none.

    Each kind of clear keeps its own time, and a time is only ever raised, so two processes clearing at once
    never undo each other's marks."""
    now = time.time() if now is None else now
    names = ["at"] + (["forced_at"] if force or hard else []) + (["hard_at"] if hard else [])
    state(cache).raise_marks(names, now)
    if hard:
        # The whole pages kept (web.pagecache) go the same way: nobody sees a page built before the edit.
        from web import pagecache

        pagecache.forget()


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
    if found.built_at < max(marks.get("forced_at", 0), marks.get("hard_at", 0)):
        return True
    if found.built_at < marks.get("at", 0) and age >= settings.RIPRAPTOR_CACHE_CLEAR_SECONDS:
        return True
    return age >= refresh_age()


def refresh_age():
    """How old a list may grow before the jobs build it again although nothing cleared it."""
    seconds = settings.RIPRAPTOR_HOME_CACHE_SECONDS
    return max(seconds - 60, seconds // 2)


def usable(found, now, cache=None):
    """Whether a visitor may be served ``found``: younger than the maximum staleness and built after the last
    hard clear (the owner's edits show at once). Where stale copies are not served (RIPRAPTOR_SERVE_STALE
    off, as in the tests, which run no background jobs), the request does the jobs' work instead: a copy
    they would build again now (entry_due) is built by the request."""
    if found is None or now - found.built_at >= max_stale():
        return False
    marks = cleared(cache)
    if found.built_at < marks.get("hard_at", 0):
        return False
    return settings.RIPRAPTOR_SERVE_STALE or not entry_due(found, now, marks)


def store(key, build, cache=None, since=None):
    """Build ``key`` and keep it with the time its build started. ``since``, when given, is called after the
    build and returns an earlier time the rows were read from, or None: rows read before this build began
    (offers shared with another list) date the list from when they were read, so a clear made in between
    leaves it stale."""
    cache = resolve(cache)
    started = time.time()
    value = build()
    read_at = since() if since is not None else None
    built_at = started if read_at is None else min(started, read_at)
    cache.set(key, Entry(value, built_at), KEEP_SECONDS)
    return value


def build_held(key, build, cache, since, held, recheck=True):
    """Build ``key`` holding the lock ``held``, then let go of it. With ``recheck``, a copy another process
    stored between our look and our taking the lock is served instead of building it again."""
    try:
        found = entry(key, cache) if recheck else None
        if found is not None and usable(found, time.time(), cache):
            return found.value
        return store(key, build, cache, since)
    finally:
        state(cache).release(held)


def get_or_build(key, build, fresh=False, cache=None, since=None):
    """The rows under ``key``: served from the cache when younger than the maximum staleness, else built.

    ``fresh`` (the jobs) builds them again and replaces them in place, unless another process holds the
    build lock, when the copy that process is replacing is returned instead of building twice. ``since`` as
    for store.
    """
    cache = resolve(cache)
    found = entry(key, cache)
    if not fresh and usable(found, time.time(), cache):
        return found.value
    held = state(cache).take(key)
    if held is not None:
        return build_held(key, build, cache, since, held, recheck=not fresh)
    if fresh and found is not None:
        return found.value
    return wait_then_build(key, build, cache, since)


def wait_then_build(key, build, cache, since=None):
    """Another process is building ``key``: wait for its copy, up to WAIT_SECONDS, then build our own. When
    the lock goes without a copy (the build failed), take it over at once."""
    give_up = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < give_up:
        time.sleep(WAIT_STEP)
        found = entry(key, cache)
        if usable(found, time.time(), cache):
            return found.value
        held = state(cache).take(key)
        if held is not None:
            return build_held(key, build, cache, since, held)
    return store(key, build, cache, since)
