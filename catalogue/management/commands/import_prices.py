import re
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalogue.importers import close_abandoned_runs, run_import
from catalogue.models import Retailer

# The hourly run reads a shop whose time comes before half past: that is nearer this run than the next
# one, so a shop read every 45 or 60 minutes is still read every hour rather than every other hour.
HOURLY_SLACK = timedelta(minutes=30)
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
        read = set()
        while True:
            now = timezone.now()
            item = Retailer.due(now + HOURLY_SLACK).exclude(pk__in=read).first()
            if item is None:
                break
            read.add(item.pk)
            # Stamped before the fetch: a read that crashes or hangs cannot be retried in a loop.
            item.next_read_at = now + timedelta(minutes=item.read_every_minutes)
            item.save(update_fields=["next_read_at"])
            self.read(item, feed)
        if not read:
            self.stdout.write("No shops are due.")

    def read(self, item, feed):
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
            item.read_ok(now, 0, ok_at=run.finished_at)
        else:
            item.read_ok(now, seconds)
        line = f"{item}: {run.offers_found} offers, {run.listings_updated} listings updated"
        line += f" in {int(seconds)}s"
        if run.unmatched:
            line += f", {run.unmatched.count(chr(10)) + 1} unmatched (see admin)"
        self.stdout.write(line)
