"""
Keeping the database small.

Every shop read saves the shop products it could not match on its ImportRun. Kept on every hourly
read of every shop, that text filled a 23 GB disk with 6.5 GB of import records in two weeks, while
the products and prices themselves took about 30 MB. Only each shop's latest whole read needs the
list (the stockist finder reads it), so the text is cleared from every other run, and runs older
than RUNS_KEPT_DAYS are deleted. What a failed read logged (catalogue/runlog.py) is cleared after a week.

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
        if "log" in columns:
            from .runlog import KEPT_DAYS

            cursor.execute(f"UPDATE {TABLE} SET log = '' WHERE log != '' AND started_at < %s", [now - timedelta(days=KEPT_DAYS)])
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


# A removed product's address answers 410 Gone for a year (web/views.py product_detail). Search engines drop a
# page that answers 410 within weeks, so after a year the address can go back to a plain 404.
REMOVED_KEPT_DAYS = 365


def prune_removed(now=None, keep_days=REMOVED_KEPT_DAYS):
    """Forget removed product addresses older than ``keep_days``, and any a product or an old address has taken
    again, which answer for themselves. Returns how many were forgotten."""
    from django.db.models import Q

    from .models import Product, ProductAlias, RemovedProduct

    now = now or timezone.now()
    gone, _ = RemovedProduct.objects.filter(
        Q(removed_at__lt=now - timedelta(days=keep_days))
        | Q(slug__in=Product.objects.values("slug"))
        | Q(slug__in=ProductAlias.objects.values("slug"))
    ).delete()
    return gone


# The owner's spreadsheets (catalogue/data_jobs.py). A download is a few megabytes and is kept for a fortnight
# to download again; the values it was made with are kept for the 90 days an upload of it is accepted. An
# upload is needed only until it is checked into its list of changes; the list stays for half a year, so a
# file can be undone and what it did is on record.
WORKBOOKS_KEPT_DAYS = 14
EXPORTS_KEPT_DAYS = 90
UPLOADS_KEPT_DAYS = 7
IMPORTS_KEPT_DAYS = 180


def prune_data_files(now=None):
    """Clear old spreadsheet files and delete old downloads and uploads with their changes. Returns
    (workbooks cleared, downloads deleted, uploads cleared, uploads deleted)."""
    from django.db.models import Q

    from .models import DataExport, DataImport

    now = now or timezone.now()
    Status = DataImport.Status
    # Rows first, so a file is never counted as cleared and then deleted. Deferred, so the delete never reads
    # the files it throws away.
    _, exports = DataExport.objects.filter(
        created_at__lt=now - timedelta(days=EXPORTS_KEPT_DAYS)).defer("workbook", "base").delete()
    _, imports = DataImport.objects.filter(
        created_at__lt=now - timedelta(days=IMPORTS_KEPT_DAYS)).defer("upload").delete()
    workbooks = DataExport.objects.filter(
        created_at__lt=now - timedelta(days=WORKBOOKS_KEPT_DAYS), workbook__isnull=False).update(workbook=None)
    # Only a check reads the upload; once it is made, discarded or refused nothing reads it again.
    uploads = DataImport.objects.filter(upload__isnull=False).filter(
        ~Q(status__in=[Status.QUEUED, Status.CHECKING, Status.CHECKED])
        | Q(created_at__lt=now - timedelta(days=UPLOADS_KEPT_DAYS))).update(upload=None)
    return workbooks, exports.get(DataExport._meta.label, 0), uploads, imports.get(DataImport._meta.label, 0)
