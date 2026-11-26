"""
The crawler controls (CrawlerSettings) for the commands that read shops outside the background reader: the weekly and
daily delivery check (check_delivery) and Claude's page reads for Things to check (shop_pages).

- may_read(): False while Pause all is on, or while the reader is holding new jobs because the processors are busy
  (WorkerState.held_since, written with its heartbeat; a reader that has stopped beating holds nothing).
- reading(name): while a command reads shops, a note beside the import lock says so, with its process id. The reader
  counts each live note as a job running, so "Most jobs at once" covers it too (Worker.dispatch). A note left by a
  command that died is ignored, as its process is gone, and removed.
"""

import os
import time
from contextlib import contextmanager

from . import crawl
from .models import WorkerState

NOTE = "ripraptor-reading."
PAUSED, HELD = "paused", "held"


def folder():
    from .worker import IMPORT_LOCK

    return os.path.dirname(IMPORT_LOCK) or "."


def why_not(state=crawl._UNREAD):
    """PAUSED or HELD when a command should start no new read now, else "". One query."""
    if state is crawl._UNREAD:
        state = WorkerState.current()
    if crawl.all_paused(state):
        return PAUSED
    if state is not None and state.held_since is not None and state.alive():
        return HELD
    return ""


def may_read(state=crawl._UNREAD):
    return not why_not(state)


def wait_for_turn(deadline, sleep=time.sleep, clock=time.monotonic, every=30):
    """Wait while new jobs are held, until ``deadline`` (a ``clock`` time). Returns "" once a read may start, PAUSED at
    once while Pause all is on, or HELD when the deadline came first."""
    while True:
        why = why_not()
        if why != HELD or clock() >= deadline:
            return why
        sleep(min(every, max(0.0, deadline - clock())))


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # someone else's process, but there
    return True


def running_elsewhere(where=None):
    """The names of the commands reading shops now, from their notes. Notes of processes that have gone are removed."""
    where = where or folder()
    found = []
    try:
        names = sorted(name for name in os.listdir(where) if name.startswith(NOTE))
    except OSError:
        return found
    for name in names:
        path = os.path.join(where, name)
        try:
            with open(path, encoding="utf-8") as handle:
                pid = int(handle.read().strip() or 0)
        except (OSError, ValueError):
            continue
        if pid and alive(pid):
            found.append(name[len(NOTE):])
        else:
            try:
                os.unlink(path)
            except OSError:
                pass
    return found


@contextmanager
def reading(name, where=None):
    """Leave the note that ``name`` is reading shops for as long as the block runs."""
    path = os.path.join(where or folder(), NOTE + name)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        path = None
    try:
        yield
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
