"""
The work behind the owner's spreadsheet (catalogue/workbook.py), run by `manage.py data_files` in its own
process, never in a web request: making a workbook takes seconds here and up to a minute on the server, and
so does checking one he uploads.

The page queues a job and starts the runner at once (start_now). The runner takes the oldest job of each
kind in turn: downloads to make, uploads to check, files to apply, files to undo. A job writes a heartbeat as
it goes; one whose heartbeat stops (the process was killed) is taken up again, and after three stops it is
given up with a reason. Applying a file resumes at the first change not yet made, so a change is never made
twice. The page starts the runner again when it finds a job stopped, and so does the hourly tidy-up, which
leaves the catalogue alone while a file is part made (part_made).

Each change is made in its own short transaction through catalogue/owner_edits.py, the code his buttons
use. One the site has changed since he checked the file is left as it is and listed; one that fails is
listed too, and the rest go on.
"""

import gzip
import json
import logging
import secrets
from collections import Counter
from datetime import timedelta
from io import StringIO
from pathlib import Path
from time import sleep

from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import OperationalError, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from .models import DataChange, DataExport, DataImport
from .pricing import drop_if_locked, is_locked, retry_locked
from .signals import clear_list_caches

logger = logging.getLogger(__name__)

Status = DataImport.Status
Outcome = DataChange.Outcome
State = DataChange.State
Kind = DataChange.Kind

# A running job whose heartbeat is older than this was stopped (a deploy, the memory killer, a crash).
STALE_AFTER = timedelta(minutes=5)
MAX_ATTEMPTS = 3
# Why a job was given up after three stops, by what it was doing: what to tap next differs.
STOPPED = {
    "building": "Making it stopped three times. Tap Make a spreadsheet to try again.",
    "checking": "Checking it failed three times; the site's log has the details. Discard it, or upload it again.",
    "applying": "Making its changes stopped three times. Those made are listed; upload this file again to make the "
                "rest, or undo it.",
    "undoing": "Undoing it stopped three times. Tap Undo this file to carry on.",
}
# One run of the runner stops taking new jobs after this; the next start carries on.
RUN_FOR = timedelta(minutes=25)
# A file is made only while no shop read is saving prices, which takes the import lock. Shop reads hold it
# for minutes at a time, so the wait is checked every few seconds and may last the whole run.
LOCK_POLL_SECONDS = 10
WAITING_FOR_READ = "Waiting for a shop read to finish"
HEARTBEAT_EVERY = 25
# A copy of the database is made first for a file that merges, takes pages off or makes many changes,
# unless the newest copy is younger than this.
BACKUP_FRESH = timedelta(hours=6)
BACKUP_CHANGES = 200
# A database another process holds past SQLite's busy wait (a deploy's VACUUM or migrate) is tried again
# after a pause; still held after every try, the change is left to be made at the runner's next start.
LOCKED_TRIES = 6
LOCKED_PAUSE_SECONDS = 5
CHANGED_SINCE = "Changed on the site since you checked, so it was left as it was."
UNDO_CHANGED = "Changed on the site since, so it was left as it is."
NO_UPLOAD = "This file is no longer kept. Upload it again."

# (model, waiting, running) in the order the runner takes them.
QUEUES = (
    (DataExport, DataExport.Status.QUEUED, DataExport.Status.BUILDING),
    (DataImport, Status.QUEUED, Status.CHECKING),
    (DataImport, Status.APPLY_QUEUED, Status.APPLYING),
    (DataImport, Status.UNDO_QUEUED, Status.UNDOING),
)


WAITING = {waiting for _model, waiting, _running in QUEUES}
RUNNING = {running for _model, _waiting, running in QUEUES}
# The page starts the runner at once; a job still waiting after this was never taken.
TAKEN_WITHIN = timedelta(seconds=30)


def needs_runner(status, created_at, heartbeat_at, now):
    """True when a job's runner is not there: it was never started (no process could be made, or the server
    restarted) or it was stopped. The page then starts one; a second is harmless, as it finds the lock taken.
    A job queued again by a tap, or by a runner that waited its whole run for a shop read, has its heartbeat
    set then, so its 30 seconds count from that moment."""
    if status in WAITING:
        return (heartbeat_at or created_at) < now - TAKEN_WITHIN
    if status in RUNNING:
        return heartbeat_at is None or heartbeat_at < now - STALE_AFTER
    return False


def lock_path():
    return Path(settings.RIPRAPTOR_CACHE_DIR) / "data-files.lock"


def runner_running():
    """True while a runner holds its lock: a second one started now would only find it taken and stop."""
    import fcntl

    try:
        with open(lock_path(), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
    except BlockingIOError:
        return True
    except OSError:
        return False
    return False


def wake_runner(jobs, now=None):
    """Start the runner when one of ``jobs`` ((status, created_at, heartbeat_at) rows) needs it and none is
    going. The page asks on every look, every few seconds while it refreshes, so a runner busy with a long job
    is never sent a stream of copies. Returns True when one was started."""
    now = now or timezone.now()
    if any(needs_runner(*job, now) for job in jobs) and not runner_running():
        return start_now()
    return False


def part_made():
    """(pk, status, created_at, heartbeat_at) of uploads whose making or undoing was cut short part way (a
    deploy or the memory killer stopped the runner). Until they finish, the hourly tidy-up leaves the
    catalogue alone: it could remove a product the rest of the file gives a page, or join what it keeps apart."""
    changes = DataChange.objects.filter(data_import=OuterRef("pk"))
    return list(
        DataImport.objects.annotate(
            made=Exists(changes.exclude(outcome=Outcome.NONE)),
            unmade=Exists(changes.filter(outcome__in=[Outcome.UNDONE, Outcome.UNDO_SKIPPED])),
        ).filter(Q(status__in=[Status.APPLY_QUEUED, Status.APPLYING], made=True)
                 | Q(status__in=[Status.UNDO_QUEUED, Status.UNDOING], unmade=True))
        .values_list("pk", "status", "created_at", "heartbeat_at")
    )


def pack_base(base):
    return gzip.compress(json.dumps(base, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def unpack_base(blob):
    return json.loads(gzip.decompress(bytes(blob)).decode("utf-8"))


def start_now():
    """Start the runner at once in its own process, so the page returns straight away. data_files takes a
    lock, so a second runner never starts. Never in tests.

    Its log (every failure's traceback) goes where the starting process's goes: the web app's journal
    (journalctl -u ripraptor) on the server, the import log when the hourly tidy-up starts it, the console
    when run by hand."""
    import subprocess
    import sys

    if getattr(settings, "TESTING", False):
        return False
    try:
        subprocess.Popen(
            [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "data_files"], cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=None, start_new_session=True,
        )
    except OSError:
        logger.warning("Spreadsheets: could not start the runner; the next look at the page starts it.", exc_info=True)
        return False
    return True


# The runner ------------------------------------------------------------------------------------------------

class StillLocked(Exception):
    """Another process held the database through every try: the change is not made yet."""


def patiently(attempt):
    """``attempt()`` through retry_locked, tried again after a pause while another process holds the database
    past the busy wait. Raises StillLocked when it never lets go."""
    for _try in range(LOCKED_TRIES):
        try:
            return retry_locked(attempt)
        except OperationalError as exc:
            if not is_locked(exc):
                raise
        sleep(LOCKED_PAUSE_SECONDS)
    raise StillLocked


def run(deadline):
    """Run queued jobs, oldest first by kind, until none is left or ``deadline`` passes. Returns how many ran."""
    ran = 0
    while timezone.now() < deadline:
        reclaim()
        job = claim()
        if job is None:
            break
        ran += 1
        try:
            work(job, deadline)
        except StillLocked:
            # Queued again with no stop counted; the next start of the runner carries on.
            logger.warning("Spreadsheet job %s %s waits: the database stayed locked", type(job).__name__, job.pk)
            break
        except Exception:
            # Left to be taken up again at once, which counts as one of its three tries.
            logger.exception("Spreadsheet job %s %s stopped", type(job).__name__, job.pk)
            with drop_if_locked("Spreadsheet job stop"):
                type(job).objects.filter(pk=job.pk).update(heartbeat_at=None)
    return ran


def work(job, deadline):
    if isinstance(job, DataExport):
        build_export(job)
    elif job.status == Status.CHECKING:
        check_import(job)
    elif job.status == Status.APPLYING:
        apply_import(job, deadline)
    elif job.status == Status.UNDOING:
        undo_import(job, deadline)


def claim(queues=QUEUES):
    """The next job, marked running: only one runner can mark it."""
    for model, waiting, running in queues:
        for pk in model.objects.filter(status=waiting).order_by("created_at", "pk").values_list("pk", flat=True)[:5]:
            now = timezone.now()
            if model.objects.filter(pk=pk, status=waiting).update(status=running, started_at=now, heartbeat_at=now):
                blobs = ("workbook", "base") if model is DataExport else ("upload",)
                return model.objects.defer(*blobs).get(pk=pk)
    return None


def reclaim(now=None):
    """Jobs whose heartbeat stopped go back in the queue, and one stopped three times is given up."""
    now = now or timezone.now()
    stale = Q(heartbeat_at__lt=now - STALE_AFTER) | Q(heartbeat_at__isnull=True)
    for model, waiting, running in QUEUES:
        for pk, attempts in model.objects.filter(stale, status=running).values_list("pk", "attempts"):
            if attempts + 1 >= MAX_ATTEMPTS:
                model.objects.filter(pk=pk, status=running).update(
                    status=model.Status.FAILED, attempts=attempts + 1, error=STOPPED[running], finished_at=now)
            else:
                model.objects.filter(pk=pk, status=running).update(status=waiting, attempts=attempts + 1)


def beat(job, **fields):
    # Skipped while another process holds the database: a missed heartbeat is harmless, a runner stopped by
    # one is not.
    with drop_if_locked("Spreadsheet heartbeat"):
        type(job).objects.filter(pk=job.pk).update(heartbeat_at=timezone.now(), **fields)


# Downloads ----------------------------------------------------------------------------------------------

def build_export(export):
    """Make one export's workbook and keep it with the values it was made with. Returns the workbook bytes, or
    None when it failed, with the reason on the row. Each write is its own short autocommit, so the reads
    that make the file hold no lock."""
    from .workbook import build_export as make

    now = timezone.now()
    DataExport.objects.filter(pk=export.pk).update(
        status=DataExport.Status.BUILDING, started_at=now, heartbeat_at=now, error="")
    try:
        data, base, counts = make(export)
    except Exception as exc:
        logger.exception("Spreadsheet RR-%s could not be made", export.pk)
        DataExport.objects.filter(pk=export.pk).update(
            status=DataExport.Status.FAILED, finished_at=timezone.now(),
            error=f"The file could not be made ({type(exc).__name__}).")
        return None
    DataExport.objects.filter(pk=export.pk).update(
        status=DataExport.Status.READY, finished_at=timezone.now(), heartbeat_at=timezone.now(), counts=counts,
        size_bytes=len(data), workbook=data, base=pack_base(base),
    )
    return data


# Checking an upload ---------------------------------------------------------------------------------------

def import_counts(changes, unread):
    """What the preview's summary and buttons need without reading every change: how many there are in each
    state, the ready ones by kind, and what was not read."""
    from .workbook_plan import counted_as, summary

    return {
        "state": dict(Counter(change.state for change in changes)),
        "kind": dict(Counter(counted_as(change) for change in changes if change.state == State.READY)),
        "unread": list(unread),
        "summary": summary(changes),
    }


def check_import(job):
    """Read an uploaded file and keep the list of what it would change, for the owner's preview. It changes
    nothing on the site. A check again replaces the earlier list and its token, so an apply sent from the
    older preview is refused."""
    from . import workbook, workbook_plan

    upload = DataImport.objects.filter(pk=job.pk).values_list("upload", flat=True).first()
    now = timezone.now()
    if upload is None:
        DataImport.objects.filter(pk=job.pk).update(status=Status.REFUSED, error=NO_UPLOAD, finished_at=now)
        return
    try:
        parsed = workbook.read_upload(bytes(upload))
        export = workbook_plan.export_for(parsed.reference)
        changes = workbook_plan.plan(export, parsed)
    except workbook.FileRefused as exc:
        DataImport.objects.filter(pk=job.pk).update(status=Status.REFUSED, error=str(exc)[:300],
                                                    finished_at=timezone.now(), heartbeat_at=timezone.now())
        return
    for change in changes:
        change.data_import_id = job.pk
    with transaction.atomic():
        DataChange.objects.filter(data_import_id=job.pk).delete()
        DataChange.objects.bulk_create(changes, batch_size=500)
        now = timezone.now()
        DataImport.objects.filter(pk=job.pk).update(
            status=Status.CHECKED, export=export, check_token=secrets.token_hex(6), checked_at=now, finished_at=now,
            heartbeat_at=now, counts=import_counts(changes, parsed.unread), done=0, note="", error="",
            total=sum(change.state == State.READY for change in changes),
        )


# Applying a file -------------------------------------------------------------------------------------------

def wait_for_import_lock(job, until):
    """The import lock, once no shop read holds it, or None when it is still held at ``until``. While it
    waits the page says so, and the heartbeat goes on."""
    from . import worker

    # Read when called, not when imported, so the tests can point it at a file of their own.
    lock = worker.ImportLock(worker.IMPORT_LOCK)
    noted = False
    while True:
        now = timezone.now()
        if lock.acquire(now):
            # Also a note left from a wait in an earlier run.
            beat(job, note="")
            return lock
        if not noted:
            noted = True
            beat(job, note=WAITING_FOR_READ)
        else:
            beat(job)
        if now >= until:
            return None
        if not run_unlocked(until):
            sleep(LOCK_POLL_SECONDS)


def run_unlocked(deadline):
    """Make one queued download, or check one upload, while a file waits for a shop read: neither needs the
    import lock, so neither waits behind it. Returns True when one ran."""
    job = claim(QUEUES[:2])
    if job is None:
        return False
    try:
        work(job, deadline)
    except Exception:
        logger.exception("Spreadsheet job %s %s stopped", type(job).__name__, job.pk)
        with drop_if_locked("Spreadsheet job stop"):
            type(job).objects.filter(pk=job.pk).update(heartbeat_at=None)
    return True


def newest_backup():
    """The newest copy backup_db made of this database, or None."""
    from django.db import connection

    from .management.commands.backup_db import backups

    if connection.vendor != "sqlite" or connection.is_in_memory_db():
        return None
    database = Path(connection.settings_dict["NAME"])
    found = backups(database.parent / "backups", database)
    return found[-1] if found else None


def safety_backup(job, todo):
    """A copy of the database before a file that merges, takes pages off or makes many changes, unless the
    nightly copy (or an earlier one of these) is under six hours old. Every change can be undone, so a copy
    that cannot be made (a full disk) is logged and the file is still made. Returns the copy's name, or ""."""
    if not (todo.filter(kind__in=[Kind.MERGE, Kind.TAKE_OFF]).exists() or todo.count() > BACKUP_CHANGES):
        return ""
    newest = newest_backup()
    if newest is not None and timezone.now().timestamp() - newest.stat().st_mtime < BACKUP_FRESH.total_seconds():
        return ""
    try:
        call_command("backup_db", stdout=StringIO())
    except CommandError:
        logger.warning("Spreadsheet upload %s: no copy of the database was made first", job.pk, exc_info=True)
        return ""
    newest = newest_backup()
    name = newest.name if newest is not None else ""
    DataImport.objects.filter(pk=job.pk).update(backup_name=name[:200])
    return name


def applying_for(job):
    """Who uploaded the file, its number, and the products and sets its new rows became in a run that was
    cut short, so the rest of the file can still name them."""
    from . import owner_edits

    reference = ""
    if job.export_id:
        export = DataExport.objects.filter(pk=job.export_id).only("pk", "token").first()
        reference = export.reference if export else ""
    applying = owner_edits.Applying(user_id=job.created_by_id, reference=reference, export_id=job.export_id,
                                    import_id=job.pk)
    for kind, ref, pk in job.changes.filter(kind__in=[Kind.NEW_PRODUCT, Kind.NEW_SET], outcome=Outcome.APPLIED,
                                            object_id__isnull=False).values_list("kind", "ref", "object_id"):
        (applying.sets if kind == Kind.NEW_SET else applying.products)[ref] = pk
    return applying


def each_change(changes, order):
    """The changes, a few hundred at a time: never one query left open while the table is written."""
    pks = list(changes.order_by(*order).values_list("pk", flat=True))
    for start in range(0, len(pks), 200):
        chunk = pks[start:start + 200]
        found = DataChange.objects.in_bulk(chunk)
        for pk in chunk:
            yield found[pk]


def make(job, change, applying):
    """Make one change in its own transaction, with its outcome. Returns the outcome."""
    from . import owner_edits

    def attempt():
        with transaction.atomic():
            made = owner_edits.apply_change(change, applying)
            DataChange.objects.filter(pk=change.pk).update(
                outcome=Outcome.APPLIED, outcome_note="", applied_at=timezone.now(), undo=made.undo,
                answer=made.answer, object_id=made.object_id if made.object_id is not None else change.object_id)
            DataImport.objects.filter(pk=job.pk).update(done=F("done") + 1)
        return made

    try:
        made = patiently(attempt)
    except StillLocked:
        raise
    except owner_edits.Stale:
        return finish_change(job, change, Outcome.SKIPPED, CHANGED_SINCE)
    except owner_edits.Refused as exc:
        return finish_change(job, change, Outcome.SKIPPED, str(exc))
    except Exception as exc:
        logger.exception("Spreadsheet upload %s: change %s (%s) failed", job.pk, change.seq, change.kind)
        return finish_change(job, change, Outcome.FAILED, f"This could not be made ({type(exc).__name__}). The "
                                                          "site's log has the details.")
    if change.kind == Kind.NEW_PRODUCT:
        applying.products[change.ref] = made.object_id
    elif change.kind == Kind.NEW_SET:
        applying.sets[change.ref] = made.object_id
    return Outcome.APPLIED


def finish_change(job, change, outcome, note):
    DataChange.objects.filter(pk=change.pk).update(outcome=outcome, outcome_note=note[:300])
    DataImport.objects.filter(pk=job.pk).update(done=F("done") + 1)
    return outcome


def requeue(job, status):
    """Back in the queue where it stopped, for the next start of the runner, with no stop counted."""
    with drop_if_locked("Spreadsheet job"):
        DataImport.objects.filter(pk=job.pk).update(status=status, heartbeat_at=timezone.now())


def apply_import(job, deadline):
    """Make the file's ready changes (and the held ones, when he said so) in order: sets, new products,
    product fields, shop pages, merges, pairs kept apart, then shop items."""
    until = min(deadline, timezone.now() + RUN_FOR)
    lock = wait_for_import_lock(job, until)
    if lock is None:
        # Still waiting: the next start of the runner waits again.
        DataImport.objects.filter(pk=job.pk).update(status=Status.APPLY_QUEUED, heartbeat_at=timezone.now())
        return
    try:
        states = [State.READY, State.HELD] if job.include_held else [State.READY]
        todo = job.changes.filter(state__in=states)
        safety_backup(job, todo.filter(outcome=Outcome.NONE))
        beat(job, total=todo.count(), done=todo.exclude(outcome=Outcome.NONE).count())
        applying = applying_for(job)
        for n, change in enumerate(each_change(todo.filter(outcome=Outcome.NONE), ("step", "seq")), start=1):
            make(job, change, applying)
            if n % HEARTBEAT_EVERY == 0:
                beat(job)
    except StillLocked:
        requeue(job, Status.APPLY_QUEUED)
        raise
    finally:
        lock.release()
    # Once, after every change: each change judged its own products as it went.
    clear_list_caches(force=True)
    counts = DataImport.objects.filter(pk=job.pk).values_list("counts", flat=True).first() or {}
    counts["outcome"] = dict(Counter(job.changes.exclude(outcome=Outcome.NONE).values_list("outcome", flat=True)))
    now = timezone.now()
    DataImport.objects.filter(pk=job.pk).update(status=Status.APPLIED, counts=counts, finished_at=now,
                                                heartbeat_at=now, note="")


# Undoing a file -------------------------------------------------------------------------------------------

def unmake(job, change, applying):
    from . import owner_edits

    def attempt():
        with transaction.atomic():
            owner_edits.undo(change, applying)
            DataChange.objects.filter(pk=change.pk).update(outcome=Outcome.UNDONE, outcome_note="",
                                                           undone_at=timezone.now())
            DataImport.objects.filter(pk=job.pk).update(done=F("done") + 1)

    try:
        patiently(attempt)
    except StillLocked:
        raise
    except owner_edits.Stale:
        return finish_change(job, change, Outcome.UNDO_SKIPPED, UNDO_CHANGED)
    except owner_edits.Refused as exc:
        return finish_change(job, change, Outcome.UNDO_SKIPPED, str(exc))
    except Exception as exc:
        logger.exception("Spreadsheet upload %s: undoing change %s (%s) failed", job.pk, change.seq, change.kind)
        return finish_change(job, change, Outcome.UNDO_SKIPPED, f"This could not be undone ({type(exc).__name__}). "
                                                                "The site's log has the details.")
    return Outcome.UNDONE


def undo_import(job, deadline):
    """Take back every change the file made, newest first, each only while the site still holds what the
    file left: a merge is undone before the merges it came after, and a change made by hand since stays. A
    change left as it was by an earlier undo is tried again."""
    until = min(deadline, timezone.now() + RUN_FOR)
    lock = wait_for_import_lock(job, until)
    if lock is None:
        DataImport.objects.filter(pk=job.pk).update(status=Status.UNDO_QUEUED, heartbeat_at=timezone.now())
        return
    try:
        undoing = job.changes.filter(outcome__in=[Outcome.APPLIED, Outcome.UNDONE, Outcome.UNDO_SKIPPED])
        # One left as it was is tried again: Undo this file is tapped again once what stopped it is put right.
        todo = undoing.filter(outcome__in=[Outcome.APPLIED, Outcome.UNDO_SKIPPED])
        beat(job, total=undoing.count(), done=undoing.filter(outcome=Outcome.UNDONE).count())
        applying = applying_for(job)
        for n, change in enumerate(each_change(todo, ("-step", "-seq")), start=1):
            unmake(job, change, applying)
            if n % HEARTBEAT_EVERY == 0:
                beat(job)
    except StillLocked:
        requeue(job, Status.UNDO_QUEUED)
        raise
    finally:
        lock.release()
    clear_list_caches(force=True)
    counts = DataImport.objects.filter(pk=job.pk).values_list("counts", flat=True).first() or {}
    counts["undo"] = dict(Counter(job.changes.filter(outcome__in=[Outcome.UNDONE, Outcome.UNDO_SKIPPED])
                                  .values_list("outcome", flat=True)))
    now = timezone.now()
    DataImport.objects.filter(pk=job.pk).update(status=Status.UNDONE, counts=counts, finished_at=now,
                                                heartbeat_at=now, note="")
