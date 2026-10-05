"""
How much of the catalogue eBay covers.

    python manage.py ebay_report

Prints how many products have been looked up on eBay, how many matched, how
many are in stock there, how many are still waiting for a first look, and
the most widely stocked products eBay has no match for.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalogue.insights import ebay_coverage


class Command(BaseCommand):
    help = "Report eBay coverage of the catalogue."

    def handle(self, *args, **options):
        c = ebay_coverage()
        if c is None:
            self.stdout.write("eBay is not set up as a shop.")
            return
        when = timezone.localtime(c["last_run"]).strftime("%d %b %H:%M") if c["last_run"] else "never"
        self.stdout.write(f"Last eBay run that found offers: {when}")
        self.stdout.write(f"Products in the catalogue:   {c['products']}")
        self.stdout.write(f"Looked up on eBay:           {c['checked']} ({c['checked_share']}%)")
        self.stdout.write(f"Matched on eBay:             {c['matched']} ({c['match_rate']}% of those looked up)")
        self.stdout.write(f"In stock on eBay now:        {c['in_stock']}")
        if c["stale"]:
            self.stdout.write(f"Out of date, not shown:      {c['stale']} (older than the site's freshness limit)")
        self.stdout.write(f"Cheapest on eBay:            {c['cheapest']}")
        self.stdout.write(f"Not looked up yet:           {c['waiting']} (about {c['days_left']} more days)")
        if c["missed"]:
            self.stdout.write("Widely stocked products eBay has no match for:")
            for row in c["missed"]:
                self.stdout.write(f"  {row['shops']} shops  {row['name']}")
