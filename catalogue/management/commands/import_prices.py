import re
import time
import traceback
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalogue.importers import close_abandoned_runs, run_import
from catalogue.models import ImportRun, Retailer
from catalogue.signals import clear_list_caches

# The run on the hour also reads a shop whose next read falls within a quarter of an hour of the run's
# start. Next reads are counted from the start of the run before, and a run can start a few minutes late
# (Python starting, or the stock watch holding the lock for up to nine and a half minutes), so without it a
# shop read every 60 minutes would be read every other hour. It never shortens a wait after errors.
DUE_SLACK = timedelta(minutes=15)
# "HTTP Error 429: Too Many Requests" from a shop, "eBay API 429: ..." or "Amazon API 503: ..." from a marketplace.
STATUS_RE = re.compile(r"(?:HTTP Error|API) (\d{3})\b")


def http_status(error):
    """The HTTP status a failed read's error names, or None. Being throttled counts as 429."""
    found = STATUS_RE.search(error or "")
    if found:
        return int(found.group(1))
    return 429 if "kept throttling us" in (error or "") else None


class Command(BaseCommand):
    help = (
        "Fetch current prices from every retailer with a price source, or one "
        "retailer by slug. With --due, only the shops whose next read has come. Run from cron every hour."
    )

    def add_arguments(self, parser):
        parser.add_argument("retailer", nargs="?", help="Retailer slug. Default: all.")
        parser.add_argument("--feed", help="Local CSV file to import instead of fetching.")
        parser.add_argument(
            "--due", action="store_true",
            help="Read only the shops whose next read has come, skipping paused shops and shops waiting after errors.",
        )

    def handle(self, *args, retailer=None, feed=None, due=False, **options):
        retailers = Retailer.objects.filter(is_active=True).exclude(
            source_type=Retailer.Source.MANUAL
        )
        if retailer:
            # One shop by name is read now, whatever its schedule says.
            retailers = Retailer.objects.filter(slug=retailer)
            if not retailers.exists():
                raise CommandError(f"No retailer with slug '{retailer}'.")
            due = False
        if not retailers.exists():
            self.stdout.write("No retailers have a price source. Set one in admin.")
            return
        # Runs a deploy, a timeout or a crash cut short would otherwise show as running for ever.
        closed = close_abandoned_runs()
        if closed:
            self.stdout.write(f"Closed {closed} earlier runs that stopped before they finished.")
        if due:
            self.read_due(feed)
            return
        # Marketplaces first: they have a daily allowance and take minutes, while one big shop
        # can take most of an hour. Then shops by name.
        first = (Retailer.Source.EBAY, Retailer.Source.AMAZON)
        ordered = sorted(retailers, key=lambda r: (r.source_type not in first, r.name.lower()))
        for item in ordered:
            self.read(item, feed)

    def read_due(self, feed):
        """Read each due shop once, asking again after every read so a shop that comes due meanwhile is read too."""
        started = timezone.now()
        read = set()
        while True:
            now = timezone.now()
            item = Retailer.due(now, next_by=max(now, started + DUE_SLACK)).exclude(pk__in=read).first()
            if item is None:
                break
            read.add(item.pk)
            # Stamped before the fetch: a read that crashes or hangs cannot be retried in a loop.
            item.next_read_at = started + timedelta(minutes=item.read_every_minutes)
            item.save(update_fields=["next_read_at"])
            began = timezone.now()
            try:
                self.read(item, feed, since=started)
            except Exception as exc:
                # One shop's read going wrong in a way nobody foresaw must not stop the others, nor the tidy
                # that follows on the cron line. It counts as a failed read, so the shop backs off.
                self.read_crashed(item, began, exc)
        if not read:
            self.stdout.write("No shops are due.")

    def read_crashed(self, item, began, exc):
        error = f"The read stopped on an unexpected error: {type(exc).__name__}: {exc}"[:300]
        self.stderr.write(f"{item}: {error}\n{traceback.format_exc()}")
        now = timezone.now()
        # The run it opened would otherwise show as running until close_abandoned_runs finds it.
        ImportRun.objects.filter(retailer=item, finished_at__isnull=True, started_at__gte=began).update(
            finished_at=now, error=error
        )
        # Whatever it wrote before it stopped should show.
        clear_list_caches(force=True)
        item.refresh_from_db(fields=["read_every_minutes", "error_streak"])
        item.read_failed(now, None, error)

    def read(self, item, feed, since=None):
        began = timezone.now()
        started = time.monotonic()
        run = run_import(item, feed_path=feed)
        seconds = time.monotonic() - started
        now = timezone.now()
        # The owner may have changed the cadence while the shop was being read.
        item.refresh_from_db(fields=["read_every_minutes", "error_streak"])
        if run.error:
            item.read_failed(now, http_status(run.error), run.error)
            self.stderr.write(f"{item}: {run.error}")
            return
        if run.started_at < began:
            # A marketplace already read today: run_import handed back that earlier run.
            item.read_ok(now, 0, ok_at=run.finished_at, since=since)
        else:
            item.read_ok(now, seconds, since=since)
        line = f"{item}: {run.offers_found} offers, {run.listings_updated} listings updated"
        line += f" in {int(seconds)}s"
        if run.unmatched:
            line += f", {run.unmatched.count(chr(10)) + 1} unmatched (see admin)"
        self.stdout.write(line)
