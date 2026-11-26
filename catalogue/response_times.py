"""
How quickly the website serves its pages, for the Server health page beside the processors and the reading (the owner,
26 Nov 2026: "historical CPU usage, scraping activity and website response times").

Each website process times every page it serves, Admin, static files and pictures left out (web.middleware.
ResponseTimeMiddleware), and adds them up a minute at a time in its own memory. When a minute is over, at its next page,
it appends one line to a small file in the cache folder: the number of pages, their total time, the slowest and how
many took over a second. Appending one short line is a single write that no other process can split, and nothing waits
on the database while a visitor waits for a page.

Once a minute the background reader collects the lines (collect) for its server sample. It reads the file it set aside
a minute before and sets the current one aside, so a line written as the file was set aside is still read, a minute
late. The time measured is the website's own work on a page, from the first of its steps to the last: the time a page
waited for a free website process is not in it.
"""

import json
import os
import threading
import time
from pathlib import Path

from django.conf import settings

FILE = "response-times.log"
SLOW_MS = 1000
SKIPPED = ("/admin/", "/static/", "/media/")


def path():
    return Path(settings.RIPRAPTOR_CACHE_DIR) / FILE


class Minute:
    """This process's pages in the current minute."""

    def __init__(self):
        self.lock = threading.Lock()
        self.minute = None
        self.pages = self.total = self.slowest = self.slow = 0

    def add(self, ms, now=None):
        """Count a page that took ``ms``; when a new minute has begun, write the last one's line first."""
        minute = int((now if now is not None else time.time()) // 60)
        line = None
        with self.lock:
            if self.minute != minute:
                if self.pages:
                    line = {"minute": self.minute, "pages": self.pages, "ms": self.total, "slowest": self.slowest,
                            "slow": self.slow}
                self.minute, self.pages, self.total, self.slowest, self.slow = minute, 0, 0, 0, 0
            self.pages += 1
            self.total += ms
            self.slowest = max(self.slowest, ms)
            self.slow += ms >= SLOW_MS
        if line is not None:
            write(line)


def write(line):
    """Append one minute's line. A cache folder that cannot be written loses the minute, never the page."""
    try:
        os.makedirs(path().parent, exist_ok=True)
        with open(path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, separators=(",", ":")) + "\n")
    except OSError:
        pass


CURRENT = Minute()


def counted(request_path):
    return not request_path.startswith(SKIPPED)


def record(request_path, ms, now=None):
    if counted(request_path):
        CURRENT.add(int(ms), now)


def collect():
    """{"pages", "average", "slowest", "slow"} from the lines set aside a minute ago, then sets the current file aside
    for the next collection. Pages 0 and the times None when there were none."""
    current, aside = path(), path().with_suffix(".collecting")
    pages = total = slowest = slow = 0
    try:
        with open(aside, encoding="utf-8") as handle:
            for raw in handle:
                try:
                    line = json.loads(raw)
                    pages += int(line["pages"])
                    total += int(line["ms"])
                    slowest = max(slowest, int(line["slowest"]))
                    slow += int(line["slow"])
                except (ValueError, KeyError, TypeError):
                    continue
        os.unlink(aside)
    except OSError:
        pass
    try:
        os.replace(current, aside)
    except OSError:
        pass
    if not pages:
        return {"pages": 0, "average": None, "slowest": None, "slow": 0}
    return {"pages": pages, "average": round(total / pages), "slowest": slowest, "slow": slow}
