"""
The background reader: one long-running process (manage.py run_worker) that keeps prices fresh all day.

It reads each shop when its turn comes (Retailer.due, the same rules as the hourly cron) and, between
reads, asks shops about single listings of the products people care about (catalogue/heat.py): HOT
ones every ten minutes, WARM ones every hour. A due read always starts before any other job when there
is room for it, since one read sees every listing a shop has. Every 15 minutes it also looks at each Shopify shop's
collection list for new pre-orders (importers.poll_collections), every 5 minutes it looks for other
shops selling products that one shop sells, or none (catalogue/finder.py), and every 6 hours (Scryfall
every 24) it reads each release source for announced sets and dates (catalogue/releases.py), one source
at a time. There is no job table. Every minute plan() works out
what is due from the shops and listings themselves, so a crash or a deploy loses only the jobs that
were running.

Politeness, so a small shop never notices it:
- one job at a time per shop;
- each shop has a token bucket (Shopify 1 request a second with a burst of 3, website shops one every
  2 seconds, feeds one every 10 seconds) and every request also takes a token from a bucket of 2 a
  second for the whole process;
- at most 3 whole-shop reads run at once, and at most 2 of website shops, so one thread is always
  free for probes (until 24 Nov 2026 2 and 1, which could not read every shop each hour: the owner, "all
  stores must update at least every hour");
- Shopify product pages are read 0.5 seconds apart on top of that.

Safety:
- the process holds /tmp/ripraptor-worker.lock for its whole life, so only one runs;
- it holds /tmp/ripraptor-import.lock only while a shop read is running, so a hand-run import_prices
  or the nightly snapshot under flock waits only for the reads in flight. After 20 minutes of holding
  it without a break it starts no new reads until the lock has been let go, so a command waiting for
  it always gets a turn, but only while one is waiting: a cron line waiting in flock shows in the
  kernel's list of locks (/proc/locks), and a command that waits by asking again (data_jobs) leaves a
  note beside the lock (ripraptor-import.wanted.<name>) for as long as it waits (lock_waiters). Until
  24 Nov 2026 the reader let it go every 20 minutes whatever, and a slot stood empty until the longest
  read in flight ended;
- every job has a deadline (shop read 20 minutes, website shop 45, marketplace 60, or twice the shop's
  last healthy read when that is longer; probe 60 seconds, with each probe request cut off after 10).
  A job past it marks its shop "Stuck, restarting" and backs it off like a failed read, closes its run,
  hands the other reads in flight back to be read again at once, and ends the process with code 1;
  systemd starts a fresh one;
- a shop that answers probes with 429, or fails three in a row, backs off like a failed read, so
  neither probes nor reads ask it again until its wait is over;
- a heartbeat goes to WorkerState and to systemd's watchdog every 30 seconds. When it stops, the
  hourly cron (import_prices --if-worker-dead 30, watch_stock --if-worker-dead 30) reads the shops.

The owner is told on their phone (catalogue/notify.py, at most once a day each) when a shop has failed
every read for more than a day or more than 10 doubtful prices are waiting; check_worker tells them when
the heartbeat stops.

Everything takes an injected clock, sleep and fetch, and run_once() does one planning pass in the
calling thread, so the tests need no threads and no waiting.
"""

import fcntl
import logging
import os
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, connections
from django.db.models import Q
from django.utils import timezone

from . import checks, crawl, finder, heat, importers, notify, probe, releases, worklog
from .models import ImportRun, Listing, Retailer, WorkerState

logger = logging.getLogger(__name__)

WORKER_LOCK = "/tmp/ripraptor-worker.lock"
IMPORT_LOCK = "/tmp/ripraptor-import.lock"

THREADS = 4
TICK_SECONDS = 5
PLAN_EVERY = timedelta(seconds=60)
BEAT_EVERY = timedelta(seconds=30)

SHOP_READ = "shop_read"
PROBE = "probe"
PULSE = "pulse"
FINDER = "finder"
RELEASE_SCAN = "release_scan"
# The finder asks many shops, so its job is held under this key instead of a shop's.
FINDER_KEY = 0
# Release sources are not shops: one is read at a time, held under this key.
RELEASE_KEY = -1
# Reads go before every other job (plan). Among reads, and among the rest, priority is weight times minutes
# overdue, so nothing waits for ever behind something heavier.
WEIGHTS = {SHOP_READ: 500, "hot": 1000, "warm": 100, "cold": 10, PULSE: 300, FINDER: 50, RELEASE_SCAN: 20}
# A shop never read before has no next read; it counts as an hour overdue.
NEVER_READ_MINUTES = 60

MAX_READS = 3
MAX_WEBSITE_READS = 2
# Longer than any healthy job, short enough that a wedged thread cannot hide behind a green heartbeat.
# A website read fetches up to 600 pages at one every 2 seconds, 20 minutes on its own, so it gets 45.
READ_DEADLINE = timedelta(minutes=20)
WEBSITE_READ_DEADLINE = timedelta(minutes=45)
MARKETPLACE_READ_DEADLINE = timedelta(minutes=60)
# A shop whose healthy read takes longer than its limit gets twice its last read time, up to this, which
# stays under the three hours after which a run counts as abandoned.
LONGEST_READ_DEADLINE = timedelta(hours=2)
PROBE_DEADLINE = timedelta(seconds=60)
# A pulse has the same minute, and starts no new request after PULSE_BUDGET so it ends inside it; a
# collection it had no time for is read at the next pulse.
PULSE_DEADLINE = timedelta(seconds=60)
PULSE_BUDGET = timedelta(seconds=40)
# A pulse this far past due goes before a probe of the same shop instead of after it.
PULSE_LATE = timedelta(minutes=15)
# Pulses of different shops start at least this far apart.
PULSE_GAP = 0.3
# A probe job starts no new request after this, so a slow shop cannot carry it past its deadline.
PROBE_BUDGET = timedelta(seconds=20)
# A batch of the stockist finder every 5 minutes, of this many products. It starts no new search after
# FINDER_BUDGET so it ends inside its deadline.
FINDER_EVERY = timedelta(minutes=5)
FINDER_BATCH = 30
FINDER_DEADLINE = timedelta(seconds=120)
FINDER_BUDGET = timedelta(seconds=90)
# A release source read, which starts no new request after RELEASE_BUDGET so it ends inside its deadline.
RELEASE_DEADLINE = timedelta(seconds=120)
RELEASE_BUDGET = timedelta(seconds=90)
# Each probe request is given up after this, however slowly the shop sends it: the socket timeout alone
# allows each read 30 seconds, so a shop sending a byte at a time could hold a request open for ever.
PROBE_FETCH_SECONDS = 10
# Probe requests failing in a row (not counting a listing the shop no longer has) before the shop backs off.
PROBE_ERRORS_IN_A_ROW = 3
GONE = (404, 410)
PROBE_BATCH = 30
# A listing checked this recently is not probed: a shop read or a probe has just seen it.
RECENTLY_CHECKED = timedelta(minutes=8)
# The reader looks for cached page lists that are due a build (web/warming.py) at least this often, and at
# once after a forced clear or when one is missing. Looking reads every list, so not on every five-second turn.
WARM_CHECK = timedelta(seconds=30)
# Every this often the reader marks today's automated visitors and clicks (catalogue/automated.py), and forgets the
# addresses of those who have gone (catalogue/online.py); who is online is counted once every five minutes of it.
VISITS_EVERY = timedelta(minutes=1)
# How often shops pasted on the Add shops page are looked for (catalogue/shop_adding.py).
SHOPS_EVERY = timedelta(seconds=30)
IMPORT_LOCK_HOLD_LIMIT = timedelta(minutes=20)
# A command that waits for the import lock by asking again and again (data_jobs) leaves a file named this, plus its
# own name, beside the lock while it waits; a cron line waiting in flock shows in /proc/locks instead. A note older
# than LOCK_WANTED_FOR is left from a command that has given up.
LOCK_WANTED = "ripraptor-import.wanted."
LOCK_WANTED_FOR = timedelta(minutes=35)
PROC_LOCKS = "/proc/locks"
# Memory. On 18 Nov 2026 the reader passed its memory limit (MemoryMax in ripraptor-worker.service) four times and was
# killed each time, part way through its reads; the owner, 19 Nov: "look at the memory too". Each shop read builds an
# index of every product (about 30 MB for 10,000), up to three at once in separate threads, and a threaded Python
# process keeps much of what it frees. So after jobs it hands freed memory back (at most every FREE_EVERY), and past
# MEMORY_SHARE of its limit it starts no new job, lets those running finish (for MEMORY_DRAIN at most) and ends
# itself, for systemd to start a fresh one: a clean restart between reads instead of a kill in the middle of one.
MEMORY_SHARE = 0.75
MEMORY_ONE_READ = 0.5   # past this share of its limit, one whole-shop read at a time, so two big reads never peak together
MEMORY_DRAIN = timedelta(minutes=15)
MEMORY_CHECK = timedelta(seconds=30)
FREE_EVERY = timedelta(seconds=60)
# Reads kept waiting for the import lock this long are said in the log (worklog, the eBay health page's Log to copy),
# then again this often. A shorter wait is the lock's ordinary turn-taking and is not worth a line.
LOCK_WAIT_SAID_AFTER = timedelta(minutes=5)
LOCK_WAIT_SAID_EVERY = timedelta(minutes=10)

MARKETPLACES = (Retailer.Source.EBAY, Retailer.Source.AMAZON)
# (requests a second, burst) per shop, by how the shop is read. Marketplaces have their own allowance code.
RATES = {
    Retailer.Source.SHOPIFY: (1.0, 3),
    Retailer.Source.WEBSITE: (0.5, 1),
    Retailer.Source.FEED: (0.1, 1),
}
GLOBAL_RATE = (3.0, 3)
STUCK = "Stuck, restarting"
# The owner hears about a shop that has failed every read for this long, and about doubtful prices once
# there are more than this many: enough to act on, never a stream.
FAILING_FOR = timedelta(hours=24)
DOUBTFUL_PILE = 10


def minutes(delta):
    return delta.total_seconds() / 60


def probe_deadline():
    """When a probe request starting now must be over, on the clock importers.fetch reads."""
    return time.monotonic() + PROBE_FETCH_SECONDS


def sd_notify(message):
    """Tell systemd ``message`` (READY=1, WATCHDOG=1, STOPPING=1) when it started us. Returns whether it was sent."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
        return True
    except OSError:
        return False


class TokenBucket:
    """``rate`` tokens a second, holding at most ``burst``. Thread safe; ``clock`` returns seconds."""

    def __init__(self, rate, burst, clock):
        self.rate, self.burst, self.clock = rate, burst, clock
        self.tokens = float(burst)
        self.at = None
        self.lock = threading.Lock()

    def take(self):
        """Take a token and return 0, or return how many seconds until one is free."""
        with self.lock:
            now = self.clock()
            if self.at is not None:
                self.tokens = min(float(self.burst), self.tokens + (now - self.at) * self.rate)
            self.at = now
            if self.tokens >= 1:
                self.tokens -= 1
                return 0.0
            # At least a millisecond: a wait shorter than a clock can count (three a second leaves a token a hair short
            # of whole) would never let the clock move on, and the caller would ask again for ever.
            return max((1 - self.tokens) / self.rate, 0.001)


# When the worker last asked each shop (wall-clock seconds), in the shared cache, so the Claude judge's
# page reads (catalogue/shop_pages.py) in another process leave a shop alone while the worker asks it
# and never add to the worker's pace. Written at most once a NOTE_EVERY_SECONDS per shop.
ASKED_KEY = "worker-asked:{}"
ASKED_KEPT_SECONDS = 600
NOTE_EVERY_SECONDS = 1.0


def note_asked(retailer_id, at=None):
    """Record that the worker is asking the shop now."""
    cache.set(ASKED_KEY.format(retailer_id), time.time() if at is None else at, ASKED_KEPT_SECONDS)


def last_asked(retailer_id):
    """When the worker last asked the shop (time.time() seconds), or None when not in the last while."""
    return cache.get(ASKED_KEY.format(retailer_id))


class Politeness:
    """The buckets, and the fetch wrapper every request of the worker goes through."""

    def __init__(self, clock, sleep):
        self.clock, self.sleep = clock, sleep
        self.everyone = TokenBucket(*GLOBAL_RATE, clock)
        self.shops = {}
        self.noted = {}
        self.lock = threading.Lock()
        self.requests = deque()

    def bucket_for(self, retailer):
        rate = RATES.get(retailer.source_type)
        if rate is None:
            return None
        with self.lock:
            if retailer.pk not in self.shops:
                self.shops[retailer.pk] = TokenBucket(*rate, self.clock)
            return self.shops[retailer.pk]

    def wait_turn(self, retailer):
        for bucket in (self.bucket_for(retailer), self.everyone):
            if bucket is None:
                continue
            while True:
                wait = bucket.take()
                if not wait:
                    break
                self.sleep(wait)
        with self.lock:
            self.requests.append(self.clock())
            now = time.monotonic()
            note = retailer.pk is not None and now - self.noted.get(retailer.pk, -NOTE_EVERY_SECONDS) >= NOTE_EVERY_SECONDS
            if note:
                self.noted[retailer.pk] = now
        if note:
            note_asked(retailer.pk)

    def wrap(self, retailer, base):
        def polite_fetch(url, *args, **kwargs):
            self.wait_turn(retailer)
            return base(url, *args, **kwargs)

        return polite_fetch

    def requests_last_hour(self):
        with self.lock:
            since = self.clock() - 3600
            while self.requests and self.requests[0] < since:
                self.requests.popleft()
            return len(self.requests)


class ImportLock:
    """The import lock the cron lines take with flock, held while any shop read is running."""

    def __init__(self, path):
        self.path = path
        self.count = 0
        self.fd = None
        self.held_since = None
        self.refused = ""   # why the last acquire was refused, in words for the log; empty once one works
        self.lock = threading.Lock()

    def acquire(self, now):
        """Take the lock (or another hold on it). False when someone else has it, when it has been held
        so long without a break that a waiting command should go first, or when its file cannot be opened
        (``refused`` says which)."""
        with self.lock:
            if self.count == 0:
                try:
                    fd = os.open(self.path, os.O_RDONLY | os.O_CREAT, 0o644)
                except OSError as exc:
                    # Raised from here, this stopped every job being started, checks too, with only a traceback in
                    # the server's log to say so. Reads wait; the reader's log says why.
                    self.refused = f"its file cannot be opened: {exc}"
                    return False
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    self.refused = "another command holds it"
                    return False
                self.fd, self.held_since = fd, now
            elif now - self.held_since >= IMPORT_LOCK_HOLD_LIMIT and self.wanted():
                self.refused = (f"this reader has held it since {timezone.localtime(self.held_since):%H:%M} and lets a "
                                f"waiting command go first ({', '.join(self.wanted())})")
                return False
            self.count += 1
            self.refused = ""
            return True

    def wanted(self):
        """Who is waiting for the lock now (lock_waiters)."""
        return lock_waiters(self.path)

    def release(self):
        with self.lock:
            self.count = max(0, self.count - 1)
            if self.count == 0 and self.fd is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
                self.fd, self.held_since = None, None

    @property
    def held(self):
        return self.count > 0


def lock_waiters(path=None, now=None, proc_locks=PROC_LOCKS):
    """Who is waiting for the import lock at ``path``, in words: "a command in flock" for each process the kernel
    lists as blocked on it (/proc/locks: a line with "->" naming the lock file's inode), and the name on each note a
    command leaves beside it while it asks again and again (want_lock), left out once older than LOCK_WANTED_FOR."""
    path = path or IMPORT_LOCK
    found = []
    try:
        inode = os.stat(path).st_ino
        with open(proc_locks) as table:
            for line in table:
                parts = line.split()
                # "12: -> FLOCK  ADVISORY  WRITE 4321 fd:01:262145 0 EOF": the process waits; its file is the last
                # number of the major:minor:inode field.
                if len(parts) > 6 and parts[1] == "->" and parts[2] == "FLOCK" and \
                        parts[6].rsplit(":", 1)[-1] == str(inode):
                    found.append("a command in flock")
    except (OSError, ValueError):
        pass
    folder = os.path.dirname(path) or "."
    cutoff = (now or time.time()) - LOCK_WANTED_FOR.total_seconds()
    try:
        names = sorted(name for name in os.listdir(folder) if name.startswith(LOCK_WANTED))
    except OSError:
        names = []
    for name in names:
        try:
            if os.stat(os.path.join(folder, name)).st_mtime >= cutoff:
                found.append(name[len(LOCK_WANTED):])
        except OSError:
            continue
    return found


def want_lock(name, path=None, waiting=True):
    """Leave (or, with ``waiting`` False, take away) the note that ``name`` waits for the import lock at ``path``."""
    note = os.path.join(os.path.dirname(path or IMPORT_LOCK) or ".", LOCK_WANTED + name)
    try:
        if waiting:
            with open(note, "a"):
                os.utime(note)
        else:
            os.unlink(note)
    except OSError:
        pass


def hold_worker_lock(path=None):
    """Take the lock that keeps a second worker from starting. Returns the open descriptor, or None."""
    fd = os.open(path or WORKER_LOCK, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


class InlineExecutor:
    """Runs each job at once in the calling thread: run_once and the tests."""

    threaded = False

    def submit(self, fn, *args):
        fn(*args)




def check_shops():
    """The Add shops checks, in their own thread, which closes its database connection when done."""
    from . import shop_adding

    try:
        shop_adding.check_waiting()
    except Exception:  # noqa: BLE001 - the shops wait for the next turn
        logger.exception("Could not check the shops to add")
    finally:
        connections.close_all()

@dataclass
class Task:
    kind: str
    retailer_id: int
    source_type: str
    priority: float
    label: str
    tier: str = ""
    listings: list = field(default_factory=list)
    last_read_seconds: int | None = None   # how long the shop's last healthy read took
    late: bool = False   # a pulse past PULSE_LATE, which goes before a probe of the same shop
    source: str = ""   # the release source a release scan reads


@dataclass
class Job:
    task: Task
    started: datetime
    deadline: datetime
    read_began: datetime | None = None   # the database clock when a shop read opened its run

    @property
    def runs_from(self):
        """Runs this job opened started at or after this."""
        return self.read_began or self.started


# A start this long after the last heartbeat follows an outage, which Crawl health then says. A restart of the
# reader's own (a stuck read, memory) is back within seconds and keeps the note that says why.
DOWN_NOTE = timedelta(minutes=10)


def down_for(delta):
    """How long, to the minute: "45 minutes", "13 hours 50 minutes", "2 days 3 hours"."""
    total = max(1, int(delta.total_seconds() // 60))
    days, hours, mins = total // 1440, (total % 1440) // 60, total % 60
    parts = [(days, "day"), (hours, "hour")] if days else [(hours, "hour"), (mins, "minute")]
    return " ".join(f"{number} {unit}{'' if number == 1 else 's'}" for number, unit in parts if number)


def deadline_for(task):
    if task.kind == PROBE:
        return PROBE_DEADLINE
    if task.kind == FINDER:
        return FINDER_DEADLINE
    if task.kind == RELEASE_SCAN:
        return RELEASE_DEADLINE
    if task.kind == PULSE:
        return PULSE_DEADLINE
    if task.source_type in MARKETPLACES:
        limit = MARKETPLACE_READ_DEADLINE
    elif task.source_type == Retailer.Source.WEBSITE:
        limit = WEBSITE_READ_DEADLINE
    else:
        limit = READ_DEADLINE
    # Longer than any healthy read: a big shop whose reads take 15 minutes is not killed at 20.
    usual = timedelta(seconds=2 * (task.last_read_seconds or 0))
    return max(limit, min(usual, LONGEST_READ_DEADLINE))


def memory_used(statm="/proc/self/statm"):
    """This process's resident memory in bytes, or None where the system does not say (anything but Linux)."""
    try:
        with open(statm) as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def memory_limit(cgroup="/proc/self/cgroup", root="/sys/fs/cgroup"):
    """The memory limit systemd holds this process to (the service's MemoryMax, cgroup v2), in bytes, or None
    without one."""
    try:
        with open(cgroup) as handle:
            path = next(line.split("::", 1)[1].strip() for line in handle if line.startswith("0::"))
        with open(os.path.join(root, path.lstrip("/"), "memory.max")) as handle:
            value = handle.read().strip()
    except (OSError, StopIteration, IndexError):
        return None
    return int(value) if value.isdigit() else None


_LIBC = []


def free_memory():
    """Hand back the memory finished jobs let go of: Python's reference cycles collected, then the free space in
    every thread's heap returned to the system (glibc's malloc_trim). Without glibc it only collects."""
    import ctypes
    import gc

    gc.collect()
    if not _LIBC:
        try:
            _LIBC.append(ctypes.CDLL("libc.so.6").malloc_trim)
        except (OSError, AttributeError):
            _LIBC.append(None)
    if _LIBC[0] is not None:
        _LIBC[0](0)


def megabytes(size):
    return round(size / 1048576) if size else None


class Worker:
    def __init__(self, clock=None, fetch=None, sleep=None, threads=THREADS, executor=None, import_lock=None,
                 page_pause=None, release_fetch=None, warm=False, memory=None, limit=None,
                 free=None, exit=None):
        self.clock = clock or timezone.now
        self.sleep = sleep or time.sleep
        self.fetch = fetch
        self.release_fetch = release_fetch
        self.threads = max(1, threads)
        self.executor = executor
        self.page_pause = importers.PAGE_PAUSE if page_pause is None else page_pause
        self.politeness = Politeness(lambda: self.clock().timestamp(), self.sleep)
        self.import_lock = ImportLock(import_lock or IMPORT_LOCK)
        self.lock = threading.Lock()
        self.running = {}   # retailer id -> Job
        self.queue = []     # tasks planned and not yet started
        self.errors = deque()
        self.probe_failed = {}  # listing id -> when a probe of it last failed
        self.probe_errors = {}  # retailer id -> probe requests failed in a row, across jobs
        self.jobs_done = 0
        self.last_job = ""
        self.last_pulse = None  # clock seconds when the last pulse began
        self.last_finder = None  # when the last finder batch began
        self.last_plan = None
        self.last_beat = None
        self.lock_refused_since = None   # when reads began waiting for the import lock, when one was last refused,
        self.lock_refused_last = None    # and when the log last said so
        self.lock_refused_said = None
        # Whether this reader keeps the cached page lists built (run_worker turns it on), and when it last
        # built them all.
        self.warm = warm
        self.last_dashboard = None
        self.last_visits = None
        self.last_shops = None
        self.shop_thread = None
        self.last_warm = None
        self.warm_forced = None
        # Memory (MEMORY_SHARE): what this process uses and is held to (run_worker passes memory_limit(); without
        # one it never restarts for memory), how it hands memory back and ends itself.
        self.memory = memory or memory_used
        self.limit = limit or None
        self.free = free or free_memory
        self.exit = exit or os._exit
        self.memory_now = None
        self.last_memory = None
        self.last_free = None
        self.freed_after = 0       # jobs done at the last hand back
        self.draining_since = None  # when it stopped starting jobs to restart

    # Planning ---------------------------------------------------------------------------------

    def max_reads(self):
        if self.limit and self.memory_now and self.memory_now > self.limit * MEMORY_ONE_READ:
            return 1
        return max(1, min(MAX_READS, self.threads - 1))

    def plan(self, now):
        """Every job due now: the due reads, then the rest highest priority first. A shop whose read has room
        to start is neither probed nor pulsed. Reads nothing from any shop."""
        if crawl.all_paused():
            worklog.note_plan({"at": now.isoformat(), "paused": True})
            return []
        with self.lock:
            busy = {pk: job.task for pk, job in self.running.items()}
            running = [(job.task, job.started) for job in self.running.values()]
        reads_free = self.max_reads() - sum(1 for t in busy.values() if t.kind == SHOP_READ)
        website_free = MAX_WEBSITE_READS - sum(
            1 for t in busy.values() if t.kind == SHOP_READ and t.source_type == Retailer.Source.WEBSITE
        )
        reads = []
        for shop in Retailer.due(now):
            if shop.pk in busy:
                continue
            overdue = minutes(now - shop.next_read_at) if shop.next_read_at else NEVER_READ_MINUTES
            reads.append(Task(SHOP_READ, shop.pk, shop.source_type, WEIGHTS[SHOP_READ] * max(1.0, overdue),
                              f"Read {shop.name}"[:120], last_read_seconds=shop.last_read_seconds))
        # eBay's hourly search goes first: one read of a few minutes an hour, and with eBay refusing its item lookup,
        # the only way an eBay price is kept fresh. Behind shops long overdue it waited about four hours at a time (the
        # owner's eBay log, 23 Nov 2026: 1,663 of 2,076 eBay items in stock older than a day).
        reads.sort(key=lambda t: (t.source_type != Retailer.Source.EBAY, -t.priority))
        # The reads there is room for now.
        chosen = []
        for task in reads:
            if len(chosen) >= reads_free:
                break
            if task.source_type == Retailer.Source.WEBSITE:
                if website_free <= 0:
                    continue
                website_free -= 1
            chosen.append(task)
        # A shop being read, or about to be, is neither probed nor pulsed: the read sees every listing. Not eBay about
        # to be read: its read is the daily search, handed back the rest of the day, and never sees every listing, and
        # its checks have their own allowance. A read of it that could not start (another command holding the import
        # lock) kept every eBay check waiting with it, so no eBay price counted (the owner, 16 Nov 2026: "something
        # is wrong"). While eBay is being read its checks wait, as any shop's do: one job per shop.
        skip = set(busy) | {t.retailer_id for t in chosen if t.source_type != Retailer.Source.EBAY}
        # One job per shop. A shop with listings to check this minute is pulsed at the next plan, unless its
        # pulse is late (PULSE_LATE): then the pulse goes first and the probe waits a minute, so a shop with
        # so many listings due that it has a probe every minute is still looked at for pre-orders.
        pulses = self.plan_pulses(now, skip)
        late = [t for t in pulses if t.late]
        probes = self.plan_probes(now, skip | {t.retailer_id for t in late})
        probed = {t.retailer_id for t in probes}
        pulses = late + [t for t in pulses if not t.late and t.retailer_id not in probed]
        extra = self.plan_finder(now, busy) + self.plan_releases(now, busy)
        # Every due read goes before every other job. One read sees all of a shop's listings, where a probe
        # sees one a request, and probes of listings an hour or more old outweigh any read by priority, so
        # reads queued among them waited for hours. The reads with no room yet are queued too: dispatch keeps
        # to the read limits and fills a slot at the next turn after a read ends, not at the next plan.
        self.note_plan(now, running, reads, chosen, probes, pulses, extra)
        return reads + sorted(probes + pulses + extra, key=lambda t: -t.priority)

    def note_plan(self, now, running, reads, chosen, probes, pulses, extra):
        """What was planned just now, for the eBay health page's Log to copy (worklog.note_plan): the jobs running,
        the queue, what eBay is due and whether the import lock is free. The reader's own view, which no page can
        work out from the database."""
        market = Retailer.Source.EBAY
        ebay_running = next((task for task, _started in running if task.source_type == market), None)
        ebay_read = next((task for task in reads if task.source_type == market), None)
        with self.lock:
            waiting = len(self.probe_failed)
        worklog.note_plan({
            "at": now.isoformat(),
            "threads": self.threads,
            "running": [{"label": task.label, "minutes": int(minutes(now - started))} for task, started in running],
            "queue": {"reads": len(reads), "reads_with_room": len(chosen), "checks": len(probes), "pulses": len(pulses),
                      "other": len(extra)},
            "ebay": {
                "running": ebay_running.label if ebay_running else "",
                "read": "" if ebay_read is None else ("due, with room to start" if ebay_read in chosen
                                                      else "due, no reading slot free"),
            },
            "checks_waiting_after_a_failed_try": waiting,
            "lock": {
                "held_since": self.import_lock.held_since.isoformat() if self.import_lock.held_since else "",
                "refused": self.import_lock.refused,
                "refused_since": self.lock_refused_since.isoformat() if self.lock_refused_since else "",
            },
        })

    def plan_releases(self, now, busy):
        """The most overdue release source, one at a time (releases.due_sources, one query)."""
        if not settings.RIPRAPTOR_RELEASES or RELEASE_KEY in busy:
            return []
        due = releases.due_sources(now)
        if not due:
            return []
        name, overdue = due[0]
        label = releases.BY_NAME[name].label
        return [Task(RELEASE_SCAN, RELEASE_KEY, "", WEIGHTS[RELEASE_SCAN] * max(1.0, overdue),
                     f"Release source {label}"[:120], source=name)]

    def plan_finder(self, now, busy):
        """A finder batch every FINDER_EVERY, one at a time. It asks no shop another job is asking."""
        if not settings.RIPRAPTOR_FINDER or FINDER_KEY in busy:
            return []
        with self.lock:
            last = self.last_finder
        if last is not None and now - last < FINDER_EVERY:
            return []
        overdue = minutes(now - last - FINDER_EVERY) if last is not None else 1.0
        return [Task(FINDER, FINDER_KEY, "", WEIGHTS[FINDER] * max(1.0, overdue), "Look for other shops")]

    def plan_pulses(self, now, skip):
        """A look at the collection list of each Shopify shop due one (Retailer.pulse_due), in one query."""
        tasks = []
        for pk, name, polled, has_list in Retailer.pulse_due(now).values_list(
            "pk", "name", "collections_polled_at", "collections_ok",
        ):
            if pk in skip:
                continue
            overdue = minutes(now - polled - Retailer.PULSE_EVERY) if polled else NEVER_READ_MINUTES
            # A shop without a collection list is due weekly, so it is never late in this sense.
            late = polled is None or (has_list is not False and now - polled - Retailer.PULSE_EVERY >= PULSE_LATE)
            tasks.append(Task(PULSE, pk, Retailer.Source.SHOPIFY, WEIGHTS[PULSE] * max(1.0, overdue),
                              f"Pre-order pulse {name}"[:120], late=late))
        return tasks

    def plan_probes(self, now, skip):
        scores = heat.heat_scores(now)
        with self.lock:
            self.probe_failed = {pk: at for pk, at in self.probe_failed.items() if now - at < heat.WARM_EVERY}
            failed_at = dict(self.probe_failed)
        rows = (
            Listing.objects.filter(
                is_active=True, retailer__is_active=True, retailer__reading_paused=False,
                last_checked__lt=now - RECENTLY_CHECKED,
            )
            .exclude(retailer__source_type__in=probe.NEVER_CHECKED)
            .exclude(retailer_id__in=skip)
            .filter(Q(retailer__backoff_until__isnull=True) | Q(retailer__backoff_until__lte=now))
            .values_list("pk", "product_id", "retailer_id", "retailer__source_type", "retailer__name", "last_checked", "url",
                         "availability")
        )
        hot, warm = [], []
        for row in rows:
            points = scores.get(row[1], 0)
            if points >= heat.HOT:
                hot.append((points, row))
            elif points >= heat.WARM:
                warm.append((points, row))
        hot.sort(key=lambda item: (-item[0], item[1][5]))
        warm += hot[heat.HOT_CAP:]
        hot = hot[:heat.HOT_CAP]
        by_shop = {}
        for tier, every, items in (("hot", heat.HOT_EVERY, hot), ("warm", heat.WARM_EVERY, warm)):
            for _points, (pk, _product, retailer_id, source_type, name, checked, _url, _availability) in items:
                wait = every
                overdue = now - checked - wait
                if overdue < timedelta(0):
                    continue
                failed = failed_at.get(pk)
                if failed is not None and now - failed < wait:
                    continue
                by_shop.setdefault(retailer_id, (source_type, name, []))[2].append((tier, checked, overdue, pk))
        tasks = []
        for retailer_id, (source_type, name, items) in by_shop.items():
            items.sort(key=lambda item: (item[0] != "hot", item[1]))
            items = items[:PROBE_BATCH]
            tier = items[0][0]
            worst = max(overdue for t, _c, overdue, _pk in items if t == tier)
            tasks.append(Task(
                PROBE, retailer_id, source_type, WEIGHTS[tier] * max(1.0, minutes(worst)),
                f"Check {len(items)} at {name}"[:120], tier=tier, listings=[pk for *_rest, pk in items],
            ))
        return tasks

    def notice_problems(self, now):
        """Tell the owner about a shop failing for a day or doubtful prices piling up. Never while Pause all is on."""
        if crawl.all_paused():
            return
        failing = list(
            Retailer.objects.filter(
                is_active=True, reading_paused=False, error_streak__gt=0, failing_since__lte=now - FAILING_FOR,
            ).exclude(source_type=Retailer.Source.MANUAL).order_by("name").values_list("name", "last_error")
        )
        if failing:
            notify.shops_failing(failing, now=now)
        doubtful = checks.doubtful_count()
        if doubtful > DOUBTFUL_PILE:
            notify.prices_to_check(doubtful, now=now)

    # Dispatch ---------------------------------------------------------------------------------

    def dispatch(self, now):
        """Start every queued job there is room for. Returns how many started."""
        if not self.queue or self.draining_since is not None:
            return 0
        if crawl.all_paused():
            self.queue = []
            return 0
        started = 0
        for task in list(self.queue):
            with self.lock:
                if len(self.running) >= self.threads:
                    break
                if task.retailer_id in self.running:
                    continue
                reads = [j.task for j in self.running.values() if j.task.kind == SHOP_READ]
            if task.kind in (FINDER, RELEASE_SCAN) and self.threads > 1 and len(self.running) >= self.threads - 1:
                # One thread is always left for probes and pulses.
                continue
            if task.kind == SHOP_READ:
                if len(reads) >= self.max_reads():
                    continue
                if task.source_type == Retailer.Source.WEBSITE and sum(
                    1 for t in reads if t.source_type == Retailer.Source.WEBSITE
                ) >= MAX_WEBSITE_READS:
                    continue
                if not self.import_lock.acquire(now):
                    self.lock_refused(now, task)
                    continue
                self.lock_taken(now)
                try:
                    due = self.stamp_read(task, now)
                except Exception:
                    self.import_lock.release()
                    raise
                if not due:
                    self.import_lock.release()
                    self.queue.remove(task)
                    continue
            self.queue.remove(task)
            self.start(task, now)
            started += 1
        return started

    def lock_refused(self, now, task):
        """A read could not start for want of the import lock. Said in the log once the wait is past the lock's
        ordinary turn-taking, then every LOCK_WAIT_SAID_EVERY, with why and which read is next in line."""
        # A wait is one run of refusals: a refusal long after the last one starts a new wait.
        if self.lock_refused_since is None or now - self.lock_refused_last > LOCK_WAIT_SAID_AFTER:
            self.lock_refused_since, self.lock_refused_said = now, None
        self.lock_refused_last = now
        waited = now - self.lock_refused_since
        if waited < LOCK_WAIT_SAID_AFTER:
            return
        if self.lock_refused_said is None or now - self.lock_refused_said >= LOCK_WAIT_SAID_EVERY:
            self.lock_refused_said = now
            logger.warning("Reads have waited %d minutes for the import lock: %s. Next in line: %s",
                           int(minutes(waited)), self.import_lock.refused, task.label)

    def lock_taken(self, now):
        if self.lock_refused_said is not None:
            logger.info("The import lock is free again after %d minutes: reads go on",
                        int(minutes(now - self.lock_refused_since)))
        self.lock_refused_since = self.lock_refused_said = None

    def stamp_read(self, task, now):
        """Move the shop's next read on before the fetch, so a read that crashes or hangs is not retried in a
        loop, and note the read so a restart can close the run it leaves open. False when it is no longer due."""
        shop = Retailer.due(now).filter(pk=task.retailer_id).first()
        if shop is None:
            return False
        shop.next_read_at = now + timedelta(minutes=shop.every_minutes)
        shop.save(update_fields=["next_read_at"])
        reads = [{"retailer": task.retailer_id, "since": now.isoformat()}]
        with self.lock:
            reads += [
                {"retailer": pk, "since": job.started.isoformat()}
                for pk, job in self.running.items() if job.task.kind == SHOP_READ
            ]
        WorkerState.objects.update_or_create(pk=1, defaults={"in_flight": reads})
        return True

    def start(self, task, now):
        job = Job(task, now, now + deadline_for(task))
        with self.lock:
            self.running[task.retailer_id] = job
            if task.kind == FINDER:
                self.last_finder = now
        self.executor.submit(self.run_job, job)

    # Jobs (in a pool thread, or inline) --------------------------------------------------------

    def run_job(self, job):
        task = job.task
        try:
            if task.kind == SHOP_READ:
                ok = self.read_shop(job)
            elif task.kind == PULSE:
                ok = self.pulse_shop(job)
            elif task.kind == FINDER:
                ok = self.find_stockists(job)
            elif task.kind == RELEASE_SCAN:
                ok = self.scan_release(job)
            else:
                ok = self.probe_shop(job)
            if not ok:
                self.note_error()
        except Exception as exc:  # noqa: BLE001 - one job going wrong must not stop the worker
            logger.exception("%s failed", task.label)
            self.note_error()
            if task.kind == SHOP_READ:
                try:
                    logger.error(crawl.crash_read(Retailer.objects.get(pk=task.retailer_id), job.runs_from, exc))
                except Exception:  # noqa: BLE001
                    logger.exception("Could not record the failed read of %s", task.label)
        finally:
            with self.lock:
                self.running.pop(task.retailer_id, None)
                self.jobs_done += 1
                self.last_job = task.label[:120]
            if task.kind == SHOP_READ:
                self.import_lock.release()
                if self.limit:
                    # In the reader's own log (worklog keeps lines about memory): which shops' reads are the heavy ones.
                    logger.info("Memory after %s: %s MB of its %s MB limit", task.label,
                                megabytes(self.memory()), megabytes(self.limit))
            if getattr(self.executor, "threaded", True):
                # Each pool thread has its own database connection; it is closed with the job.
                connections.close_all()

    def fetch_for(self, retailer, probing=False):
        """The fetch a job uses for this shop: the injected one, or the real one, behind the shop's buckets."""
        if self.fetch is not None:
            base = self.fetch
        elif retailer.session_url:
            session = importers.session_fetch(retailer.session_url)
            base = (lambda url: session(url, retries=0, deadline=probe_deadline())) if probing else session
        elif probing:
            # One try, cut off after PROBE_FETCH_SECONDS: a probe must finish inside its minute.
            def base(url):
                return importers.fetch(url, retries=0, deadline=probe_deadline())
        else:
            def base(url, *args, **kwargs):
                return importers.fetch(url, *args, **kwargs)
        return self.politeness.wrap(retailer, base)

    def read_shop(self, job):
        shop = Retailer.objects.filter(pk=job.task.retailer_id).first()
        if shop is None:
            # Deleted in Admin since the read was planned: nothing to read.
            return True
        # The run's own start is stamped by the database's clock, so it is compared with that clock: an
        # earlier run handed back means a marketplace already read today.
        began = job.read_began = timezone.now()
        run = importers.run_import(shop, fetch=self.fetch_for(shop), page_pause=self.page_pause)
        now = self.clock()
        seconds = max(0.0, (now - job.started).total_seconds())
        ok = crawl.finish_read(shop, run, began, seconds, since=job.started, now=now)
        if not ok and run.error.startswith(importers.SHOP_CLOSED):
            logger.warning("%s: %s; looked at again tomorrow", shop, importers.SHOP_CLOSED)
        elif not ok and shop.awaiting_access:
            logger.info("Amazon: not sharing its prices with this account yet (%s); asked again in %d hours",
                        Retailer.NOT_ELIGIBLE, Retailer.ACCESS_WAIT.total_seconds() // 3600)
        elif not ok:
            logger.error("%s: %s", shop, run.error)
        elif shop.source_type == Retailer.Source.EBAY:
            logger.info("eBay: this hour's search finished in %d minutes: %d products searched, %d listings updated",
                        int(seconds // 60), run.offers_found, run.listings_updated)
        return ok

    def probe_shop(self, job):
        from .signals import clear_list_caches

        listings = Listing.objects.select_related("retailer", "product__product_set").in_bulk(job.task.listings)
        if not listings:
            return True
        fetch = self.fetch_for(next(iter(listings.values())).retailer, probing=True)
        retailer_id = job.task.retailer_id
        with self.lock:
            in_a_row = self.probe_errors.get(retailer_id, 0)
        failures, restocked = 0, False
        for pk in job.task.listings:
            now = self.clock()
            if now - job.started >= PROBE_BUDGET:
                break
            listing = listings.get(pk)
            if listing is None or listing.last_checked >= now - RECENTLY_CHECKED:
                continue
            status = None
            try:
                result = probe.ask(listing, fetch=fetch)
            except importers.ImportError_ as exc:
                result, status = None, crawl.http_status(str(exc))
                in_a_row = 0 if status in GONE else in_a_row + 1
                if status == 429 or in_a_row >= PROBE_ERRORS_IN_A_ROW:
                    # The shop is throttling us or not answering: it waits like a failed read, and the
                    # plan leaves a shop that is waiting out of the probes too.
                    self.back_off(retailer_id, self.clock(), status, f"Checking single listings: {exc}")
                    in_a_row, failures = 0, failures + 1
                    break
            else:
                in_a_row = 0
            outcome = probe.record_probe(listing, result)
            if outcome is None:
                # Not saved, so its check time did not move: wait before asking about it again.
                with self.lock:
                    self.probe_failed[pk] = now
                failures += result is None
            restocked = restocked or outcome == "restocked"
        with self.lock:
            self.probe_errors[retailer_id] = in_a_row
        if restocked:
            clear_list_caches(force=True)
        return failures == 0

    def pulse_shop(self, job):
        """Look at one Shopify shop's collection list and read the pre-order collections that changed.

        Pulses of different shops start at least PULSE_GAP apart. A shop that throttles the pulse (429)
        waits like a failed read, so nothing asks it again until its wait is over; any other failure is
        tried again at the next pulse.
        """
        with self.lock:
            now = self.clock().timestamp()
            wait = 0.0 if self.last_pulse is None else self.last_pulse + PULSE_GAP - now
            self.last_pulse = max(now, now + wait)
        if wait > 0:
            self.sleep(wait)
        shop = Retailer.objects.filter(pk=job.task.retailer_id).first()
        if shop is None:
            return True
        result = importers.poll_collections(
            shop, fetch=self.fetch_for(shop, probing=True), now=self.clock(), pause=self.page_pause,
            stop=lambda: self.clock() - job.started >= PULSE_BUDGET,
        )
        # A shop without a collection list is not a failure: it is looked at weekly from now on.
        if not result.error or result.no_list:
            return True
        if result.status == 429:
            self.back_off(shop.pk, self.clock(), 429, f"Looking for pre-orders: {result.error}")
        else:
            logger.warning("%s: %s", shop, result.error)
        return False

    def find_stockists(self, job):
        """One batch of the stockist finder, through this process's buckets, at a fifth of its requests."""
        def busy():
            with self.lock:
                return {pk for pk in self.running if pk != FINDER_KEY}

        result = finder.find(
            FINDER_BATCH, fetch_for=lambda shop: self.fetch_for(shop, probing=True), clock=self.clock,
            sleep=self.sleep, busy=busy, stop=lambda: self.clock() - job.started >= FINDER_BUDGET,
            worker_requests=self.politeness.requests_last_hour,
        )
        if result.products:
            logger.info("Stockist finder: %s", result)
        return True

    def scan_release(self, job):
        """Read one release source. A source that fails keeps its error on the Insights page and is read
        again at its next turn; it never backs off a shop."""
        result = releases.scan(
            job.task.source, fetch=self.release_fetch, now=self.clock(), sleep=self.sleep,
            stop=lambda: self.clock() - job.started >= RELEASE_BUDGET,
        )
        logger.info("%s", result)
        return result.ok

    def back_off(self, retailer_id, now, status, error):
        shop = Retailer.objects.get(pk=retailer_id)
        shop.read_failed(now, status, error)
        logger.warning("%s: %s", shop, error)

    def note_error(self):
        with self.lock:
            self.errors.append(self.clock())

    def errors_last_hour(self, now):
        with self.lock:
            while self.errors and now - self.errors[0] > timedelta(hours=1):
                self.errors.popleft()
            return len(self.errors)

    # Health -----------------------------------------------------------------------------------

    def beat(self, now):
        """Heartbeat: the row the Crawl health page reads, then systemd's watchdog."""
        with self.lock:
            reads = [
                {"retailer": pk, "since": job.started.isoformat()}
                for pk, job in self.running.items() if job.task.kind == SHOP_READ
            ]
            done, last = self.jobs_done, self.last_job
        try:
            WorkerState.objects.update_or_create(pk=1, defaults={
                "heartbeat_at": now, "jobs_done": done, "last_job": last, "in_flight": reads,
                "requests_last_hour": self.politeness.requests_last_hour(),
                "errors_last_hour": self.errors_last_hour(now),
                "memory_mb": megabytes(self.memory_now), "memory_limit_mb": megabytes(self.limit),
            })
        except Exception:  # noqa: BLE001 - a busy database must not stop the watchdog
            logger.exception("Could not write the heartbeat")
        self.last_beat = now
        sd_notify("WATCHDOG=1")

    def check_deadlines(self, now):
        with self.lock:
            late = [job for job in self.running.values() if now > job.deadline]
        if late:
            self.stuck(late[0], now)

    def stuck(self, job, now):
        """A job ran past its deadline: say so, close what it left open and end the process for systemd."""
        task = job.task
        note = f"{task.label} ran past its {int(minutes(deadline_for(task)))} minute limit at {crawl.clock(now, now)}, so the reader restarted."
        logger.error(note)
        try:
            WorkerState.objects.update_or_create(pk=1, defaults={"note": note[:300]})
            if task.kind in (FINDER, RELEASE_SCAN):
                # The finder and a release scan hold no shop and leave no run open: only the other reads need
                # handing back.
                with self.lock:
                    others = [j for j in self.running.values() if j is not job and j.task.kind == SHOP_READ]
                self.hand_back(others, now)
                os._exit(1)
            shop = Retailer.objects.get(pk=task.retailer_id)
            if task.kind in (SHOP_READ, PULSE):
                from . import runlog

                cut = ImportRun.objects.filter(retailer=shop, finished_at__isnull=True, started_at__gte=job.runs_from)
                runlog.add(cut, f"{timezone.localtime(now):%H:%M:%S} ERROR {note}")
                cut.update(finished_at=now, error=importers.STOPPED)
            # A stuck probe backs its shop off too, so the fresh process does not pick the same shop
            # straight away and restart again every few seconds with a heartbeat that looks healthy.
            shop.read_failed(now, None, STUCK)
            with self.lock:
                others = [j for j in self.running.values() if j is not job and j.task.kind == SHOP_READ]
            # The other reads did nothing wrong: they are closed and read again as soon as the process is back.
            self.hand_back(others, now)
        except Exception:  # noqa: BLE001 - the restart matters more than the bookkeeping
            logger.exception("Could not record the stuck job")
        os._exit(1)

    def hand_back(self, reads, now):
        """Close the runs of reads cut short by a restart and make their shops due again at once."""
        for job in reads:
            ImportRun.objects.filter(
                retailer_id=job.task.retailer_id, finished_at__isnull=True, started_at__gte=job.runs_from
            ).update(finished_at=now, error=importers.STOPPED)
            Retailer.objects.filter(pk=job.task.retailer_id).update(next_read_at=now)
        WorkerState.objects.filter(pk=1).update(in_flight=[])

    # Life -------------------------------------------------------------------------------------

    def begin(self):
        """Close the runs the last process left open, then say the worker is up."""
        now = self.clock()
        state = WorkerState.load()
        closed = 0
        for entry in state.in_flight or []:
            try:
                since = datetime.fromisoformat(entry["since"])
                cut = ImportRun.objects.filter(
                    retailer_id=entry["retailer"], finished_at__isnull=True, started_at__gte=since
                ).update(finished_at=now, error=importers.STOPPED)
            except (KeyError, TypeError, ValueError):
                continue
            if cut:
                # A read the last process could not hand back as it stopped (stop): its shop is read first.
                Retailer.objects.filter(pk=entry["retailer"]).update(next_read_at=now)
            closed += cut
        closed += importers.close_abandoned_runs(now=now)
        fields = {"in_flight": [], "started_at": now, "heartbeat_at": now, "jobs_done": 0}
        last = state.heartbeat_at
        if last is not None and now - last >= DOWN_NOTE:
            # Stopped and left stopped (an update cut short), or the server was off: say for how long, on Crawl health
            # and in the log, so an outage that mended itself is still seen.
            note = (f"Started at {crawl.clock(now, now)} after {down_for(now - last)} not running, "
                    f"since {crawl.clock(last, now)}.")
            fields["note"] = note[:300]
            logger.warning("Background reader: %s", note)
        WorkerState.objects.filter(pk=1).update(**fields)
        self.last_beat = now
        sd_notify("READY=1")
        return closed

    def tick(self, now):
        """One turn of the main loop: heartbeat, deadlines, then planning and starting jobs.

        The heartbeat and the deadlines come first, so a planning pass that fails (a busy database, a bad
        row) neither silences the watchdog nor lets a stuck job hide.
        """
        if self.last_beat is None or now - self.last_beat >= BEAT_EVERY:
            self.beat(now)
        self.check_deadlines(now)
        self.keep_memory_down(now)
        try:
            if self.last_plan is None or now - self.last_plan >= PLAN_EVERY:
                self.last_plan = now
                self.queue = self.plan(now)
                planned = True
            else:
                planned = False
            self.dispatch(now)
        except Exception:  # noqa: BLE001 - tried again on the next turn
            logger.exception("The planning pass failed")
            return
        if planned:
            self.notify_safely(now)
        self.keep_pages_warm(now)
        self.keep_dashboard_warm(now)
        self.keep_visits_tidy(now)
        self.keep_shops_checked(now)

    def keep_memory_down(self, now):
        """After jobs, hand freed memory back (FREE_EVERY); look at what the process uses (MEMORY_CHECK); past
        MEMORY_SHARE of its limit, start no new job and, once those running finish or MEMORY_DRAIN passes, end
        itself for systemd to start afresh (restart_for_memory)."""
        with self.lock:
            done, busy = self.jobs_done, bool(self.running)
        if done != self.freed_after and (self.last_free is None or now - self.last_free >= FREE_EVERY):
            self.freed_after, self.last_free = done, now
            try:
                self.free()
            except Exception:  # noqa: BLE001 - only a saving
                logger.exception("Could not hand memory back")
            self.last_memory = None   # look again now it is freed
        if self.last_memory is None or now - self.last_memory >= MEMORY_CHECK:
            self.last_memory = now
            self.memory_now = self.memory()
        if self.draining_since is None:
            if self.limit and self.memory_now and self.memory_now > self.limit * MEMORY_SHARE:
                self.draining_since = now
                logger.warning("Using %s MB of its %s MB limit: no new jobs, and a restart once those running finish",
                               megabytes(self.memory_now), megabytes(self.limit))
            return
        if not busy or now - self.draining_since >= MEMORY_DRAIN:
            self.restart_for_memory(now)

    def restart_for_memory(self, now):
        """End the process for systemd to start a fresh one (Restart=always), saying why on Crawl health. Reads still
        running after MEMORY_DRAIN are closed and read again as soon as it is back."""
        note = (f"Restarted at {crawl.clock(now, now)} to free memory: it was using {megabytes(self.memory_now)} MB "
                f"of its {megabytes(self.limit)} MB limit.")
        logger.warning(note)
        try:
            WorkerState.objects.update_or_create(pk=1, defaults={"note": note[:300]})
            with self.lock:
                reads = [job for job in self.running.values() if job.task.kind == SHOP_READ]
            self.hand_back(reads, now)
        except Exception:  # noqa: BLE001 - the restart matters more than the bookkeeping
            logger.exception("Could not record the restart")
        sd_notify("STOPPING=1")
        self.exit(0)

    def keep_dashboard_warm(self, now):
        """Build again the dashboard's parts over a minute old while it is in use (web.dashboard.warm), so the owner's
        page serves them at once. Looks every WARM_CHECK; a failure waits for the next turn."""
        if not self.warm or (self.last_dashboard is not None and now - self.last_dashboard < WARM_CHECK):
            return
        self.last_dashboard = now
        from web import dashboard

        try:
            dashboard.warm()
        except Exception:  # noqa: BLE001 - the owner's page builds what it lacks
            logger.exception("Could not build the dashboard")

    def keep_visits_tidy(self, now):
        """Every VISITS_EVERY: today's automated visitors and clicks marked, who is online counted and the addresses
        of those gone forgotten. A failure waits for the next turn; tidy_all does the same hourly."""
        if not self.warm or (self.last_visits is not None and now - self.last_visits < VISITS_EVERY):
            return
        self.last_visits = now
        from . import automated, online

        try:
            automated.classify(days=1)
            online.sample()
        except Exception:  # noqa: BLE001
            logger.exception("Could not tidy the visit counts")

    def keep_shops_checked(self, now):
        """Every SHOPS_EVERY: shops pasted on the Add shops page are checked and the good ones added
        (catalogue/shop_adding.py), in a thread of their own so a slow shop never holds up the main loop."""
        if not self.warm or (self.last_shops is not None and now - self.last_shops < SHOPS_EVERY):
            return
        self.last_shops = now
        if self.shop_thread is not None and self.shop_thread.is_alive():
            return
        from .models import ShopToAdd

        try:
            waiting = ShopToAdd.objects.filter(status=ShopToAdd.Status.WAITING).exists()
        except Exception:  # noqa: BLE001 - looked at again on the next turn
            logger.exception("Could not look for shops to add")
            return
        if waiting:
            self.shop_thread = threading.Thread(target=check_shops, name="shop-checks", daemon=True)
            self.shop_thread.start()

    def keep_pages_warm(self, now):
        """Build the cached page lists that are due (cleared by a job's price changes, missing, or nearly
        RIPRAPTOR_HOME_CACHE_SECONDS old) and replace them in place, so visitors are served a copy while
        the new one is built and find it fresh soon after. Runs on the main loop, never in a reading thread,
        and a failure only waits for the next turn: visitors are served the copy they have meanwhile, and
        build one themselves only when there is none or it passed RIPRAPTOR_LIST_MAX_STALE_SECONDS."""
        if not self.warm:
            return
        from web import listcache, warming

        try:
            forced = listcache.cleared().get("forced_at")   # a hard clear raises it too
            looked_lately = self.last_warm is not None and now - self.last_warm < WARM_CHECK
            if looked_lately and forced == self.warm_forced and not warming.missing():
                return
            warming.warm_pages()
            self.last_warm, self.warm_forced = now, forced
        except Exception:  # noqa: BLE001 - tried again on the next turn
            logger.exception("Could not build the cached page lists")

    def notify_safely(self, now):
        """A notice that cannot be sent or noted waits for the next planning pass; it never stops the reader."""
        try:
            self.notice_problems(now)
        except Exception:  # noqa: BLE001
            logger.exception("Could not tell the owner about a problem")

    def run_once(self):
        """One planning pass, every job run to the end in this thread, then a heartbeat. For tests and checks."""
        if self.executor is None:
            self.executor = InlineExecutor()
        now = self.clock()
        self.last_plan = now
        self.queue = self.plan(now)
        while self.dispatch(self.clock()):
            pass
        self.notify_safely(self.clock())
        self.beat(self.clock())

    def run_forever(self):
        from concurrent.futures import ThreadPoolExecutor

        if self.executor is None:
            self.executor = ThreadPoolExecutor(self.threads, thread_name_prefix="reader")
        try:
            while True:
                self.tick(self.clock())
                self.sleep(TICK_SECONDS)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stopped on purpose (a deploy, systemctl stop): close the runs in flight and read those shops again soon."""
        sd_notify("STOPPING=1")
        now = self.clock()
        with self.lock:
            reads = [job for job in self.running.values() if job.task.kind == SHOP_READ]
        try:
            self.hand_back(reads, now)
        except OperationalError as exc:
            # A read still writing holds the database (an update stopped the reader mid-read): the next start closes
            # these runs and reads their shops first (begin), so nothing is lost. The owner's log, 23 Nov 2026,
            # showed this as a red "database is locked" after each update.
            logger.warning("The runs in flight are left for the next start to close (%s)", exc)
        except Exception:  # noqa: BLE001
            logger.exception("Could not close the runs in flight")
        # The pool's threads may be in the middle of a long read; they are not waited for.
        os._exit(0)
