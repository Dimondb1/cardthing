"""
The background reader: one long-running process (manage.py run_worker) that keeps prices fresh all day.

It reads each shop when its turn comes (Retailer.due, the same rules as the hourly cron) and, between
reads, asks shops about single listings of the products people care about (catalogue/heat.py): HOT
ones every ten minutes, WARM ones every hour. Every 15 minutes it also looks at each Shopify shop's
collection list for new pre-orders (importers.poll_collections), and every 5 minutes it looks for other
shops selling products that one shop sells, or none (catalogue/finder.py). There is no job table. Every minute plan() works out
what is due from the shops and listings themselves, so a crash or a deploy loses only the jobs that
were running.

Politeness, so a small shop never notices it:
- one job at a time per shop;
- each shop has a token bucket (Shopify 1 request a second with a burst of 3, website shops one every
  2 seconds, feeds one every 10 seconds) and every request also takes a token from a bucket of 2 a
  second for the whole process;
- at most 2 whole-shop reads run at once, and at most 1 of a website shop, so one thread is always
  free for probes;
- Shopify product pages are read 0.5 seconds apart on top of that.

Safety:
- the process holds /tmp/ripraptor-worker.lock for its whole life, so only one runs;
- it holds /tmp/ripraptor-import.lock only while a shop read is running, so a hand-run import_prices
  or the nightly snapshot under flock waits only for the reads in flight. After 20 minutes of holding
  it without a break it starts no new reads until the lock has been let go, so a command waiting for
  it always gets a turn;
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
from django.db import connections
from django.db.models import Q
from django.utils import timezone

from . import checks, crawl, finder, heat, importers, notify, probe
from .models import ImportRun, Listing, Retailer, WorkerState

logger = logging.getLogger(__name__)

WORKER_LOCK = "/tmp/ripraptor-worker.lock"
IMPORT_LOCK = "/tmp/ripraptor-import.lock"

THREADS = 3
TICK_SECONDS = 5
PLAN_EVERY = timedelta(seconds=60)
BEAT_EVERY = timedelta(seconds=30)

SHOP_READ = "shop_read"
PROBE = "probe"
PULSE = "pulse"
FINDER = "finder"
# The finder asks many shops, so its job is held under this key instead of a shop's.
FINDER_KEY = 0
# Priority is weight times minutes overdue, so nothing waits for ever behind something heavier.
WEIGHTS = {SHOP_READ: 500, "hot": 1000, "warm": 100, PULSE: 300, FINDER: 50}
# A shop never read before has no next read; it counts as an hour overdue.
NEVER_READ_MINUTES = 60

MAX_READS = 2
MAX_WEBSITE_READS = 1
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
# Each probe request is given up after this, however slowly the shop sends it: the socket timeout alone
# allows each read 30 seconds, so a shop sending a byte at a time could hold a request open for ever.
PROBE_FETCH_SECONDS = 10
# Probe requests failing in a row (not counting a listing the shop no longer has) before the shop backs off.
PROBE_ERRORS_IN_A_ROW = 3
GONE = (404, 410)
PROBE_BATCH = 30
# A listing checked this recently is not probed: a shop read or a probe has just seen it.
RECENTLY_CHECKED = timedelta(minutes=8)
IMPORT_LOCK_HOLD_LIMIT = timedelta(minutes=20)

MARKETPLACES = (Retailer.Source.EBAY, Retailer.Source.AMAZON)
# (requests a second, burst) per shop, by how the shop is read. Marketplaces have their own allowance code.
RATES = {
    Retailer.Source.SHOPIFY: (1.0, 3),
    Retailer.Source.WEBSITE: (0.5, 1),
    Retailer.Source.FEED: (0.1, 1),
}
GLOBAL_RATE = (2.0, 2)
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
            return (1 - self.tokens) / self.rate


class Politeness:
    """The buckets, and the fetch wrapper every request of the worker goes through."""

    def __init__(self, clock, sleep):
        self.clock, self.sleep = clock, sleep
        self.everyone = TokenBucket(*GLOBAL_RATE, clock)
        self.shops = {}
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
        self.lock = threading.Lock()

    def acquire(self, now):
        """Take the lock (or another hold on it). False when someone else has it, or when it has been held
        so long without a break that a waiting command should go first."""
        with self.lock:
            if self.count == 0:
                fd = os.open(self.path, os.O_RDONLY | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    return False
                self.fd, self.held_since = fd, now
            elif now - self.held_since >= IMPORT_LOCK_HOLD_LIMIT:
                return False
            self.count += 1
            return True

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


def deadline_for(task):
    if task.kind == PROBE:
        return PROBE_DEADLINE
    if task.kind == FINDER:
        return FINDER_DEADLINE
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


class Worker:
    def __init__(self, clock=None, fetch=None, sleep=None, threads=THREADS, executor=None, import_lock=None,
                 page_pause=None):
        self.clock = clock or timezone.now
        self.sleep = sleep or time.sleep
        self.fetch = fetch
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

    # Planning ---------------------------------------------------------------------------------

    def max_reads(self):
        return max(1, min(MAX_READS, self.threads - 1))

    def plan(self, now):
        """Every job due now, highest priority first, at most one per shop. Reads nothing from any shop."""
        if crawl.all_paused():
            return []
        with self.lock:
            busy = {pk: job.task for pk, job in self.running.items()}
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
        reads.sort(key=lambda t: -t.priority)
        chosen = []
        for task in reads:
            if len(chosen) >= reads_free:
                break
            if task.source_type == Retailer.Source.WEBSITE:
                if website_free <= 0:
                    continue
                website_free -= 1
            chosen.append(task)
        # A shop being read, or about to be, is neither probed nor pulsed: the read sees every listing.
        skip = set(busy) | {t.retailer_id for t in chosen}
        # One job per shop. A shop with listings to check this minute is pulsed at the next plan, unless its
        # pulse is late (PULSE_LATE): then the pulse goes first and the probe waits a minute, so a shop with
        # so many listings due that it has a probe every minute is still looked at for pre-orders.
        pulses = self.plan_pulses(now, skip)
        late = [t for t in pulses if t.late]
        probes = self.plan_probes(now, skip | {t.retailer_id for t in late})
        probed = {t.retailer_id for t in probes}
        pulses = late + [t for t in pulses if not t.late and t.retailer_id not in probed]
        return sorted(chosen + probes + pulses + self.plan_finder(now, busy), key=lambda t: -t.priority)

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
            .exclude(retailer__source_type__in=probe.NOT_PROBED)
            .exclude(retailer_id__in=skip)
            .filter(Q(retailer__backoff_until__isnull=True) | Q(retailer__backoff_until__lte=now))
            .values_list("pk", "product_id", "retailer_id", "retailer__source_type", "retailer__name", "last_checked")
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
            for _points, (pk, _product, retailer_id, source_type, name, checked) in items:
                overdue = now - checked - every
                if overdue < timedelta(0):
                    continue
                failed = failed_at.get(pk)
                if failed is not None and now - failed < every:
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
        if not self.queue:
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
            if task.kind == FINDER and self.threads > 1 and len(self.running) >= self.threads - 1:
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
                    continue
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

    def stamp_read(self, task, now):
        """Move the shop's next read on before the fetch, so a read that crashes or hangs is not retried in a
        loop, and note the read so a restart can close the run it leaves open. False when it is no longer due."""
        shop = Retailer.due(now).filter(pk=task.retailer_id).first()
        if shop is None:
            return False
        shop.next_read_at = now + timedelta(minutes=shop.read_every_minutes)
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
        shop = Retailer.objects.get(pk=job.task.retailer_id)
        # The run's own start is stamped by the database's clock, so it is compared with that clock: an
        # earlier run handed back means a marketplace already read today.
        began = job.read_began = timezone.now()
        run = importers.run_import(shop, fetch=self.fetch_for(shop), page_pause=self.page_pause)
        now = self.clock()
        seconds = max(0.0, (now - job.started).total_seconds())
        ok = crawl.finish_read(shop, run, began, seconds, since=job.started, now=now)
        if not ok:
            logger.error("%s: %s", shop, run.error)
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
            if task.kind == FINDER:
                # The finder holds no shop and leaves no run open: only the other reads need handing back.
                with self.lock:
                    others = [j for j in self.running.values() if j is not job and j.task.kind == SHOP_READ]
                self.hand_back(others, now)
                os._exit(1)
            shop = Retailer.objects.get(pk=task.retailer_id)
            if task.kind in (SHOP_READ, PULSE):
                ImportRun.objects.filter(
                    retailer=shop, finished_at__isnull=True, started_at__gte=job.runs_from
                ).update(finished_at=now, error=importers.STOPPED)
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
                closed += ImportRun.objects.filter(
                    retailer_id=entry["retailer"], finished_at__isnull=True, started_at__gte=since
                ).update(finished_at=now, error=importers.STOPPED)
            except (KeyError, TypeError, ValueError):
                continue
        closed += importers.close_abandoned_runs(now=now)
        WorkerState.objects.filter(pk=1).update(in_flight=[], started_at=now, heartbeat_at=now, jobs_done=0)
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
        except Exception:  # noqa: BLE001
            logger.exception("Could not close the runs in flight")
        # The pool's threads may be in the middle of a long read; they are not waited for.
        os._exit(0)
