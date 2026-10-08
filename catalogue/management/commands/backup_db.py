import os
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
        target = folder / backup_name(database)
        # Written under a temporary name and renamed when complete, so a copy cut short by a
        # timeout is never mistaken for a good one or counted among the newest.
        partial = target.with_name(target.name + ".part")
        with closing(sqlite3.connect(partial)) as copy:
            connection.connection.backup(copy)
        os.replace(partial, target)
        removed = 0
        for old in backups(folder, database)[:-keep]:
            old.unlink()
            removed += 1
        self.stdout.write(f"Backed up to {target}, removed {removed} older copies.")
