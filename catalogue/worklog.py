"""
The background reader's own log, kept where the owner can read it: every warning and error with the end of its
traceback, and every line about eBay or the import lock, in a file the eBay health page shows in its Log to copy box.
The owner, 16 Nov 2026, asked to run journalctl on the server and paste what it printed: "Instead of guessing and
hoping why aren't you producing an output log I could screenshot to you or a log that would show in the admin area
that I could copy and paste to you".

A file beside the cache, not in it: the update command clears the cache and restarts the reader, and the lines from
before an update are the ones that say what went wrong. Only the background reader writes it (run_worker calls
install), so nothing else can trim it under its feet. The page only reads it.

What the reader last planned (note_plan) is a snapshot, not a log: the jobs running, the queue, what eBay is due and
whether the import lock is free. It is overwritten every minute, in the shared cache.
"""

import logging
import os
import re
import traceback
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

FILE_NAME = "reader.log"
KEEP = 600            # lines kept when the file is trimmed
TRIM_AT = 1200        # and it is trimmed once it holds this many
LINE_CHARS = 600
TRACE_LINES = 6       # the end of a traceback: the failing lines and the error itself
# Lines below a warning are kept when they are about these: eBay's checks and reads, the lock reads share, the
# reader starting, and its memory after each shop read (worker.MEMORY_SHARE).
ABOUT = ("ebay", "import lock", "background reader", "memory")
PLAN_KEY = "worker:last-plan"
PLAN_SECONDS = 15 * 60
# Never in a kept line or in the Log to copy box, which is pasted into messages: the value of any setting that reads
# as a secret, and anyone's email address.
SECRET_SETTINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CERT", "WEBHOOK")
SECRET_LEAST = 8
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HIDDEN, HIDDEN_EMAIL = "[hidden]", "[email]"


def secrets():
    """The settings' secret values, longest first so one inside another is hidden whole."""
    found = {
        value for name in dir(settings)
        if name.isupper() and any(part in name for part in SECRET_SETTINGS)
        and isinstance(value := getattr(settings, name, None), str) and len(value) >= SECRET_LEAST
    }
    return sorted(found, key=len, reverse=True)


def scrub(text):
    """``text`` with every secret setting's value and every email address taken out."""
    for value in secrets():
        text = text.replace(value, HIDDEN)
    return EMAIL.sub(HIDDEN_EMAIL, text)


def path():
    return Path(settings.RIPRAPTOR_CACHE_DIR) / FILE_NAME


class Keeper(logging.Handler):
    """Appends the lines worth reading to the file and trims it now and then. Handlers emit one record at a time
    (logging.Handler.handle holds a lock), so the count needs none of its own."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.count = None   # lines in the file, counted at the first write

    def emit(self, record):
        try:
            message = record.getMessage()
            about = f"{record.name} {message}".lower()
            if record.levelno < logging.WARNING and not any(word in about for word in ABOUT):
                return
            at = timezone.localtime(datetime.fromtimestamp(record.created, tz=dt_timezone.utc))
            lines = [f"{at:%d %b %H:%M:%S} {record.levelname} {message}"[:LINE_CHARS]]
            if record.exc_info and record.exc_info[0] is not None:
                trace = "".join(traceback.format_exception(*record.exc_info)).strip().splitlines()
                lines += ["    " + line.strip()[:LINE_CHARS] for line in trace[-TRACE_LINES:]]
            self.write([scrub(line.replace("\n", " ")) for line in lines])
        except Exception:  # noqa: BLE001 - a log that cannot be kept must never stop the reader
            self.handleError(record)

    def write(self, lines):
        target = path()
        if self.count is None:
            os.makedirs(target.parent, exist_ok=True)
            self.count = len(read_lines())
        with open(target, "a", encoding="utf-8") as file:
            file.write("".join(line + "\n" for line in lines))
        self.count += len(lines)
        if self.count >= TRIM_AT:
            kept = read_lines()[-KEEP:]
            spare = target.with_suffix(".tmp")
            spare.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
            os.replace(spare, target)
            self.count = len(kept)


def install():
    """Start keeping the log: on the catalogue loggers (which do not pass their lines up) and on every other."""
    keeper = Keeper()
    for name in ("catalogue", ""):
        logging.getLogger(name).addHandler(keeper)
    return keeper


def remove(keeper):
    for name in ("catalogue", ""):
        logging.getLogger(name).removeHandler(keeper)


def read_lines():
    try:
        return path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def entries():
    """[(is a warning or an error, its lines)] for every entry kept, oldest first: a line and the end of its
    traceback, which is indented under it."""
    found = []
    for line in read_lines():
        if line.startswith("    ") and found:
            found[-1][1].append(line)
        else:
            found.append((" WARNING " in line[:40] or " ERROR " in line[:40] or " CRITICAL " in line[:40], [line]))
    return found


def note_plan(plan):
    """Keep what the reader planned just now ({"at": ISO time, ...}) for the Log to copy box. A cache that cannot
    take it is never the reason a plan fails."""
    try:
        cache.set(PLAN_KEY, plan, PLAN_SECONDS)
    except Exception:  # noqa: BLE001
        pass


def last_plan():
    found = cache.get(PLAN_KEY)
    return found if isinstance(found, dict) else {}
