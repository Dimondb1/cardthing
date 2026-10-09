"""
Shrink the database: clear old import records, then give the space back to the disk.

    python manage.py compact_db

The update runs it before the backup, so the backup copies only what is in use. Safe while the
site runs: the clear-out is a few statements, and the vacuum holds the database for a few seconds
when the live data is small. The hourly tidy-up clears the records without the vacuum, and SQLite
reuses the freed pages.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from catalogue import housekeeping

# A file with more than this much unused space is vacuumed.
VACUUM_OVER = 200 * 1024 * 1024


def mb(size):
    return f"{size / 1024 / 1024:,.0f} MB"


class Command(BaseCommand):
    help = "Clear old import records and give their space back to the disk."

    def handle(self, *args, **options):
        cleared, deleted = housekeeping.prune_runs()
        self.stdout.write(f"Import records: {cleared} lists of unmatched products cleared, {deleted} old runs deleted.")
        if connection.vendor != "sqlite" or connection.is_in_memory_db():
            return
        before, used = housekeeping.file_sizes()
        if before - used <= VACUUM_OVER:
            self.stdout.write(f"Database is {mb(before)}, nothing worth giving back.")
            return
        self.stdout.write(f"Database is {mb(before)} with {mb(used)} in use: shrinking it.")
        housekeeping.vacuum()
        after, _ = housekeeping.file_sizes()
        self.stdout.write(f"Database is now {mb(after)}.")
