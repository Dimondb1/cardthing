import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


def backup_name(database, now=None):
    """``db.sqlite3`` becomes ``db.sqlite3.20261008-0040.bak``: the stamp sorts by time."""
    stamp = timezone.localtime(now).strftime("%Y%m%d-%H%M")
    return f"{Path(database).name}.{stamp}.bak"


def backups(folder, database):
    """This database's backups in ``folder``, oldest first."""
    return sorted(Path(folder).glob(f"{Path(database).name}.*.bak"))


def unfinished(folder, database):
    """Copies cut short: ``.part`` files, or a bare stamp from a copy that ran out of space. Never good."""
    found = set(Path(folder).glob(f"{Path(database).name}.*.part"))
    found |= {path for path in Path(folder).glob(f"{Path(database).name}.2*") if not path.name.endswith(".bak")}
    return sorted(found)


def older_style(folder, database):
    """The ``db-<stamp>.sqlite3`` copies earlier updates made, which the newest-five rule never counted."""
    return sorted(Path(folder).glob(f"{Path(database).stem}-2*.sqlite3*"))


# Room left on the disk beyond the copy itself, so the site can keep writing while it is made.
SPARE = 500 * 1024 * 1024


def mb(size):
    return f"{size / 1024 / 1024:,.0f} MB"


class Command(BaseCommand):
    help = (
        "Copy the database with SQLite's own backup, which reads a consistent snapshot while the "
        "site and the cron jobs keep running, then keep only the newest copies. Never use cp: "
        "it misses whatever is still in the -wal file."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dir", help="Folder for the copies. Default: backups/ beside the database.")
        parser.add_argument("--keep", type=int, default=5, help="How many copies to keep. Default: 5.")

    def handle(self, *args, dir=None, keep=5, **options):
        if connection.vendor != "sqlite":
            raise CommandError("backup_db copies a SQLite database only.")
        if keep < 1:
            raise CommandError("--keep must be at least 1.")
        connection.ensure_connection()
        if connection.is_in_memory_db():
            raise CommandError("The database is in memory, so there is no file to back up.")
        database = Path(connection.settings_dict["NAME"])
        folder = Path(dir) if dir else database.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        for old in unfinished(folder, database):
            old.unlink()
            self.stdout.write(f"Removed an unfinished copy: {old.name}")
        # The copy is as big as the database file. Make room by dropping the oldest copies (always keeping
        # one), and stop before writing anything if there is still not enough: a full disk stops the site.
        needed = database.stat().st_size + SPARE
        kept = backups(folder, database)
        while shutil.disk_usage(folder).free < needed and len(kept) > 1:
            kept.pop(0).unlink()
        free = shutil.disk_usage(folder).free
        if free < needed:
            raise CommandError(
                f"Not enough disk space to back up the database: it needs {mb(needed)} and {mb(free)} is free. "
                "Nothing was changed. Run manage.py compact_db to shrink the database first."
            )
        target = folder / backup_name(database)
        # Written under a temporary name and renamed when complete, so a copy cut short by a
        # timeout is never mistaken for a good one or counted among the newest.
        partial = target.with_name(target.name + ".part")
        with closing(sqlite3.connect(partial)) as copy:
            connection.connection.backup(copy)
        os.replace(partial, target)
        # Only once the new copy is safely made: copies in the older naming are never counted otherwise.
        for old in older_style(folder, database):
            old.unlink()
            self.stdout.write(f"Removed an older copy: {old.name}")
        removed = 0
        for old in backups(folder, database)[:-keep]:
            old.unlink()
            removed += 1
        self.stdout.write(f"Backed up to {target}, removed {removed} older copies.")
