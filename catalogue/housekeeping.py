"""
Keeping the database small.

Every shop read saves the shop products it could not match on its ImportRun. Kept on every hourly
read of every shop, that text filled a 23 GB disk with 6.5 GB of import records in two weeks, while
the products and prices themselves took about 30 MB. Only each shop's latest whole read needs the
list (the stockist finder reads it), so the text is cleared from every other run, and runs older
than RUNS_KEPT_DAYS are deleted.

Written in plain SQL against columns every version of the table has, so the update can run it on a
database from before its migrations, ahead of the backup.
"""

from datetime import timedelta

from django.db import connection
from django.utils import timezone

RUNS_KEPT_DAYS = 30
TABLE = "catalogue_importrun"


def prune_runs(now=None, keep_days=RUNS_KEPT_DAYS):
    """Clear the unmatched text from every run but each shop's latest good whole read, and delete runs
    older than ``keep_days`` except each shop's latest run of any kind and its latest good read (the
    eBay and Amazon daily gate looks at that one). Returns (texts cleared, runs deleted)."""
    now = now or timezone.now()
    with connection.cursor() as cursor:
        # Freed pages are only listed as free, never overwritten: with secure delete on (some builds of
        # SQLite default to it) clearing gigabytes of text would write gigabytes to the -wal file.
        cursor.execute("PRAGMA secure_delete = OFF")
        columns = {row.name for row in connection.introspection.get_table_description(cursor, TABLE)}
        whole = "AND note = ''" if "note" in columns else ""
        good = f"SELECT MAX(id) FROM {TABLE} WHERE finished_at IS NOT NULL AND error = '' {whole} GROUP BY retailer_id"
        latest = f"SELECT MAX(id) FROM {TABLE} GROUP BY retailer_id"
        cursor.execute(f"UPDATE {TABLE} SET unmatched = '' WHERE unmatched != '' AND id NOT IN ({good})")
        cleared = cursor.rowcount
        cursor.execute(
            f"DELETE FROM {TABLE} WHERE started_at < %s AND id NOT IN ({good}) AND id NOT IN ({latest})",
            [now - timedelta(days=keep_days)],
        )
        deleted = cursor.rowcount
    return cleared, deleted


def file_sizes():
    """(bytes the database file takes, bytes its pages hold), for a SQLite file database."""
    values = []
    with connection.cursor() as cursor:
        for pragma in ("page_size", "page_count", "freelist_count"):
            cursor.execute(f"PRAGMA {pragma}")
            values.append(cursor.fetchone()[0])
    page_size, pages, free = values
    return pages * page_size, (pages - free) * page_size


def vacuum():
    """Give the free pages back to the disk. SQLite writes a fresh copy of what is in use, so this needs
    free space about the size of the live data, not of the file."""
    with connection.cursor() as cursor:
        cursor.execute("VACUUM")
        # In WAL mode the fresh copy is first written to the -wal file; this moves it into the database
        # and empties the -wal file, so the space really comes back to the disk.
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
