"""
Store every product's Price check band (catalogue/fair_price.py, refresh_bands), for the lists' Fairest price
sort and the gauge on product cards, after removing wrong prices from the daily record the bands rest on
(catalogue/pricing.py, prune_wrong_lows). tidy_all runs it each hour; install.sh once after an update.

    python manage.py refresh_price_checks
"""

from django.core.management.base import BaseCommand

from catalogue import fair_price, pricing


class Command(BaseCommand):
    help = "Remove wrong prices from the daily record, then store each product's Price check band for lists."

    def handle(self, *args, **options):
        gone = pricing.prune_wrong_lows()
        if gone:
            self.stdout.write(f"{gone} wrong prices removed from the daily record.")
        changed = fair_price.refresh_bands()
        if changed or gone:
            from catalogue.signals import clear_list_caches

            clear_list_caches(force=True)
        self.stdout.write(f"{changed} Price checks changed.")
