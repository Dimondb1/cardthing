import time

from django.core.management.base import BaseCommand, CommandError

from catalogue.importers import run_import
from catalogue.models import Retailer


class Command(BaseCommand):
    help = (
        "Fetch current prices from every retailer with a price source, or one "
        "retailer by slug. Run from cron, for example every hour."
    )

    def add_arguments(self, parser):
        parser.add_argument("retailer", nargs="?", help="Retailer slug. Default: all.")
        parser.add_argument("--feed", help="Local CSV file to import instead of fetching.")

    def handle(self, *args, retailer=None, feed=None, **options):
        retailers = Retailer.objects.filter(is_active=True).exclude(
            source_type=Retailer.Source.MANUAL
        )
        if retailer:
            retailers = Retailer.objects.filter(slug=retailer)
            if not retailers.exists():
                raise CommandError(f"No retailer with slug '{retailer}'.")
        if not retailers.exists():
            self.stdout.write("No retailers have a price source. Set one in admin.")
            return
        # Marketplaces first: they have a daily allowance and take minutes, while one big shop
        # can take most of an hour. Then shops by name.
        first = (Retailer.Source.EBAY, Retailer.Source.AMAZON)
        ordered = sorted(retailers, key=lambda r: (r.source_type not in first, r.name.lower()))
        for item in ordered:
            started = time.monotonic()
            run = run_import(item, feed_path=feed)
            if run.error:
                self.stderr.write(f"{item}: {run.error}")
                continue
            line = f"{item}: {run.offers_found} offers, {run.listings_updated} listings updated"
            line += f" in {int(time.monotonic() - started)}s"
            if run.unmatched:
                line += f", {run.unmatched.count(chr(10)) + 1} unmatched (see admin)"
            self.stdout.write(line)
