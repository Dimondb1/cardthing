"""
What happened during one shop read, kept on its ImportRun so the owner can see it in admin.

Everything a read logs (catalogue.* loggers, INFO and up) in the thread running it is collected. A read that
fails or warns keeps the lines on ``ImportRun.log``, the last LINES of them; a clean read keeps nothing, so
the database stays small. While a read runs its lines are saved every SAVE_EVERY seconds, and at once for an
error, so a read the background reader cuts off still shows how far it got; run_import clears them again
when the read ends clean. Secrets in addresses (API keys, tokens) are blanked before anything is kept.
"""

import logging
import re
import threading
import time
from collections import deque
from contextlib import contextmanager

LINES = 200          # the last this many lines of a read
LINE_CHARS = 600     # each line cut to this
SAVE_EVERY = 30      # seconds between saves while a read runs
KEPT_DAYS = 7        # housekeeping clears logs older than this

# Query parameters and path parts that carry a key: ?apikey=..., &token=..., Awin's /apikey/<key>/.
SECRET_PARAM = re.compile(r"(?i)\b((?:api_?key|access_?token|token|key|secret|password|sig|signature|auth)=)[^&\s'\"]+")
SECRET_PATH = re.compile(r"(?i)(/(?:apikey|api_key|key|token)/)[^/\s'\"?]+")


def redact(text):
    return SECRET_PATH.sub(r"\1…", SECRET_PARAM.sub(r"\1…", text))


class _Collector(logging.Handler):
    def __init__(self, run_pk, thread, clock):
        super().__init__(level=logging.INFO)
        self.run_pk = run_pk
        self.thread = thread
        self.clock = clock
        self.lines = deque(maxlen=LINES)
        self.warned = False
        self.saved = False
        self.saved_at = clock()   # the first save comes SAVE_EVERY seconds in, or with the first error
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        # Other reads run at the same time in other threads; each keeps only its own lines.
        if record.thread != self.thread:
            return
        try:
            line = redact(self.format(record))
            self.lines.append(line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + "…")
            if record.levelno >= logging.WARNING:
                self.warned = True
            now = self.clock()
            if record.levelno >= logging.ERROR or now - self.saved_at >= SAVE_EVERY:
                self.save(now)
        except Exception:  # noqa: BLE001 - keeping a log must never stop a read
            pass

    def text(self):
        return "\n".join(self.lines)

    def save(self, now=None):
        from .models import ImportRun

        self.saved_at = self.clock() if now is None else now
        self.saved = True
        ImportRun.objects.filter(pk=self.run_pk).update(log=self.text())


@contextmanager
def capture(run, clock=time.monotonic):
    """Collect what this thread logs while the block runs. Yields the collector: ``text()`` is the log and
    ``warned`` says whether anything went wrong. Lines already saved stay on the run if the block raises."""
    collector = _Collector(run.pk, threading.get_ident(), clock)
    root = logging.getLogger("catalogue")
    root.addHandler(collector)
    try:
        yield collector
    except BaseException:
        # A crash: keep the lines so far, so the traceback added after them has its context.
        try:
            collector.save()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        root.removeHandler(collector)


def add(run_filter, text):
    """Add ``text`` to the end of the log of every run the queryset ``run_filter`` selects, keeping it to
    LINES lines. For what happens after a read's own lines: a crash's traceback, a restart cutting it off."""
    for run in run_filter.only("pk", "log"):
        lines = [line for line in (run.log + "\n" + redact(text)).split("\n") if line]
        type(run).objects.filter(pk=run.pk).update(log="\n".join(lines[-LINES:]))
