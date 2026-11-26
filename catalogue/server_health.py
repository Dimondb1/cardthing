"""
Server health: how busy the server's processors are, which program is using them, and its memory and disk, sampled
once a minute by the background reader (catalogue/worker.py) and kept for KEEP_DAYS. The owner, 25 Nov 2026: "the
server is running at 99% cpu how do we ensure that we monitor this from the ripraptor admin".

Everything is read from Linux's own files, so no package is added:

- /proc/stat: the time all the processors have spent busy and idle since the server started. The share of the time
  between two samples that was busy is how busy the processors are (cpu, in percent of all of them).
- /proc/<pid>/stat and /proc/<pid>/cmdline: each process's own processor time, added up by the program it belongs to
  (PROGRAMS: the website, the background reader, the scheduled jobs, the web server, everything else), as a percent of
  all the processors, the same scale as cpu.
- /proc/loadavg, /proc/meminfo and the database's disk.

Busy: the processors at or over BUSY_PERCENT in every sample of the last BUSY_FOR. The owner is told then
(notify.owner, at most once a day), and Admin home says so under Needs you until it calms down.

Holding back: the reader looks at the processors every few seconds (CpuMeter) and, from the hold figure in Crawler
settings (CrawlerSettings, 75% over 60 seconds unless changed), starts no new job until they are under the start-again
figure (55%), so the website keeps processor time for visitors (Worker.watch_processors). Each sample says whether it
was holding, how many jobs ran, how many requests went to shops, and how quickly the website served its pages
(catalogue/response_times.py), so the Server health page can show the three side by side. The reader also runs with
a low CPUWeight (deploy/ripraptor-worker.service): when both want the processors, the website gets them first.
"""

import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

SAMPLE_EVERY = timedelta(seconds=60)
KEEP_DAYS = 7
PRUNE_EVERY = timedelta(hours=1)
BUSY_PERCENT = 90
BUSY_FOR = timedelta(minutes=15)
SERVER_BUSY = "Server processors busy"
SERVER_PATH = "/admin/server/"

WEBSITE, READER, JOBS, WEB_SERVER, OTHER = "website", "reader", "jobs", "web_server", "other"
# In the order the page lists them, with the words it uses.
PROGRAMS = {
    WEBSITE: "Website (the pages visitors open)",
    READER: "Background reader (reading the shops)",
    JOBS: "Scheduled jobs (hourly and nightly tasks)",
    WEB_SERVER: "Web server (Caddy)",
    OTHER: "Everything else",
}


def program_for(args):
    """Which of PROGRAMS a process belongs to, from its command line."""
    line = " ".join(args)
    if "gunicorn" in line:
        return WEBSITE
    if "run_worker" in line:
        return READER
    if "manage.py" in line:
        return JOBS
    if args and os.path.basename(args[0]) == "caddy":
        return WEB_SERVER
    return OTHER


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def cpu_times(stat_text):
    """(busy, total) processor time since the server started, from /proc/stat's first line. Idle and waiting for the
    disk are not busy; time the host gave to another server (steal) is, as the server could not use it."""
    for line in (stat_text or "").splitlines():
        if line.startswith("cpu "):
            values = [int(value) for value in line.split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values[:8])
            return total - idle, total
    return None


def process_ticks(stat_text):
    """A process's own processor time (user and system), from its /proc/<pid>/stat. The name in brackets may hold
    spaces, so the fields are read after the last bracket."""
    if not stat_text or ")" not in stat_text:
        return None
    fields = stat_text.rsplit(")", 1)[1].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def processes(proc="/proc"):
    """{pid: (program, processor ticks)} of every process that can be read."""
    found = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        ticks = process_ticks(read_text(f"{proc}/{name}/stat"))
        if ticks is None:
            continue
        args = [part for part in (read_text(f"{proc}/{name}/cmdline") or "").split("\0") if part]
        found[int(name)] = (program_for(args), ticks)
    return found


def memory_mb(meminfo_text):
    """(used, total) memory in MB, used being what programs hold (total less what is available)."""
    values = {}
    for line in (meminfo_text or "").splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0])
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    return round((total - available) / 1024), round(total / 1024)


def load_average(loadavg_text):
    try:
        return float((loadavg_text or "").split()[0])
    except (IndexError, ValueError):
        return 0.0


@dataclass
class Reading:
    """One sample, before it is saved."""
    cpu: float
    cpus: int
    load: float
    memory_used_mb: int
    memory_total_mb: int
    disk_used_percent: int
    programs: dict = field(default_factory=dict)


class Sampler:
    """Takes a Reading each time it is asked, from the time since the last one. The first gives none: a share of busy
    time needs two looks. ``read`` and ``procs`` stand in for /proc in tests."""

    def __init__(self, read=None, procs=None, cpus=None, disk=None):
        self.read = read or read_text
        self.procs = procs or processes
        self.cpus = cpus or (lambda: os.cpu_count() or 1)
        self.disk = disk or default_disk
        self.last_cpu = None
        self.last_procs = {}

    def take(self):
        times = cpu_times(self.read("/proc/stat"))
        procs = self.procs()
        if times is None:
            return None
        last_cpu, last_procs = self.last_cpu, self.last_procs
        self.last_cpu, self.last_procs = times, procs
        if last_cpu is None:
            return None
        busy, total = times[0] - last_cpu[0], times[1] - last_cpu[1]
        if total <= 0:
            return None
        programs = dict.fromkeys(PROGRAMS, 0.0)
        for pid, (program, ticks) in procs.items():
            before = last_procs.get(pid)
            # A process started since the last look counts all its time; one that was there, the time since.
            spent = ticks - before[1] if before is not None and before[0] == program else ticks
            if spent > 0:
                programs[program] += spent
        # /proc/stat counts every processor; a process's ticks are on the same clock, so both are shares of all.
        shares = {name: round(min(100.0, ticks * 100 / total), 1) for name, ticks in programs.items()}
        used, memory_total = memory_mb(self.read("/proc/meminfo"))
        return Reading(
            cpu=round(max(0.0, min(100.0, busy * 100 / total)), 1), cpus=self.cpus(),
            load=load_average(self.read("/proc/loadavg")), memory_used_mb=used, memory_total_mb=memory_total,
            disk_used_percent=self.disk(), programs=shares,
        )


class CpuMeter:
    """How busy the processors have been over a moving window, from /proc/stat looked at every few seconds: one small
    file, so it can be read each turn of the reader. ``read`` and ``clock`` stand in for /proc and the time in tests."""

    # Fewer seconds than this of looks (or than the window, when it is shorter) gives no average: a reader that has just
    # started is never held on its first few seconds.
    LEAST = 15

    def __init__(self, read=None, clock=None):
        self.read = read or read_text
        self.clock = clock or time.monotonic
        self.looks = deque()

    def look(self, window):
        """Look now, keeping the looks that span the last ``window`` seconds."""
        times = cpu_times(self.read("/proc/stat"))
        if times is None:
            return
        now = self.clock()
        self.looks.append((now, *times))
        # Keep one look at or before the window's start, so the average spans all of it.
        while len(self.looks) > 2 and self.looks[1][0] <= now - window:
            self.looks.popleft()

    def average(self, window):
        """The busy percent over the last ``window`` seconds, or None until the looks span enough of it."""
        if len(self.looks) < 2:
            return None
        first, last = self.looks[0], self.looks[-1]
        if last[0] - first[0] < min(window, self.LEAST):
            return None
        busy, total = last[1] - first[1], last[2] - first[2]
        if total <= 0:
            return None
        return max(0.0, min(100.0, busy * 100 / total))


def default_disk():
    from django.conf import settings

    from .notify import disk_used

    path = os.path.dirname(str(settings.DATABASES["default"]["NAME"])) or "/"
    try:
        return disk_used(path)[0]
    except OSError:
        return 0


def save(reading, now, held=False, jobs=0, shop_requests=0, pages=None):
    """Save a sample, with what the reader was doing and ``pages`` (response_times.collect)."""
    from .models import ServerSample

    pages = pages or {}
    return ServerSample.objects.create(
        at=now, cpu=reading.cpu, cpus=reading.cpus, load=reading.load, memory_used_mb=reading.memory_used_mb,
        memory_total_mb=reading.memory_total_mb, disk_used_percent=reading.disk_used_percent,
        programs=reading.programs, held=held, jobs=jobs, shop_requests=shop_requests,
        pages=pages.get("pages", 0), page_ms=pages.get("average"), page_ms_slowest=pages.get("slowest"),
        pages_slow=pages.get("slow", 0),
    )


def prune(now):
    from .models import ServerSample

    return ServerSample.objects.filter(at__lt=now - timedelta(days=KEEP_DAYS)).delete()[0]


@dataclass
class Busy:
    """How long the processors have been at or over BUSY_PERCENT without a break, up to now."""
    since: object        # the first sample of the run
    minutes: int
    cpu: float           # the latest sample's
    programs: dict       # the latest sample's


def busy_now(now=None):
    """Busy (see the module's notes) when every sample of the last BUSY_FOR, and at least most of them, are at or
    over BUSY_PERCENT, else None. Three queries."""
    from .models import ServerSample

    now = now or timezone.now()
    recent = list(ServerSample.objects.filter(at__gte=now - BUSY_FOR, at__lte=now).order_by("at")
                  .values_list("at", "cpu", "programs"))
    # A sample a minute: most of the window must be there, so a reader that just started is not taken as busy.
    if len(recent) < BUSY_FOR / SAMPLE_EVERY * 0.6 or any(cpu < BUSY_PERCENT for _at, cpu, _programs in recent):
        return None
    calm = (ServerSample.objects.filter(at__lt=recent[0][0], cpu__lt=BUSY_PERCENT).order_by("-at")
            .values_list("at", flat=True).first())
    first = (ServerSample.objects.filter(at__gt=calm) if calm else ServerSample.objects).order_by("at").values_list(
        "at", flat=True).first()
    first = first or recent[0][0]
    return Busy(first, int((now - first).total_seconds() // 60), recent[-1][1], recent[-1][2])


def top_programs(programs, least=1.0):
    """[(words, percent)] of the programs using the processors, most first, leaving out those under ``least``."""
    found = [(PROGRAMS.get(name, name), share) for name, share in (programs or {}).items() if share >= least]
    return sorted(found, key=lambda item: -item[1])


def notice_busy(now=None):
    """Tell the owner when the processors are busy (busy_now), at most once a day. Returns whether a notice went."""
    from . import notify

    busy = busy_now(now)
    if busy is None:
        return False
    using = ", ".join(f"{words.split(' (')[0].lower()} {share:.0f}%" for words, share in top_programs(busy.programs)[:3])
    detail = (
        f"The server's processors have been at least {BUSY_PERCENT}% busy for {busy.minutes} minutes "
        f"({busy.cpu:.0f}% now). Most of it: {using or 'nothing named'}. While they are over the hold figure in Crawler "
        "settings the background reader starts no new job, so the website keeps time for visitors. The Server health "
        "page in Admin shows what is using them, and the last 24 hours."
    )
    return notify.owner(SERVER_BUSY, SERVER_BUSY, SERVER_PATH, detail, now=now, tags="warning")
