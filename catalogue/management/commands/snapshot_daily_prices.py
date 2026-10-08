from django.core.management.base import BaseCommand
from django.db import connection

from catalogue.pricing import snapshot_all


def checkpoint():
    """Fold the write-ahead log back into the database file and empty it.

    SQLite does this a little at a time on its own, but a reader that never lets go can keep the
    -wal file growing; once a night, after the snapshot, it is truncated to nothing.
    """
    if connection.vendor != "sqlite":
        return None
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return cursor.fetchone()


class Command(BaseCommand):
    help = (
        "Record today's cheapest delivered price for every product, then checkpoint the "
        "write-ahead log. Run once a day, for example from cron, so price history has no gaps."
    )

    def handle(self, *args, **options):
        count = snapshot_all()
        self.stdout.write(f"Recorded prices for {count} products.")
        result = checkpoint()
        if result and result[0]:
            # The first column is 1 when another connection kept the checkpoint from finishing.
            self.stderr.write("The write-ahead log could not be emptied: another process was busy. It is tried again tomorrow.")
