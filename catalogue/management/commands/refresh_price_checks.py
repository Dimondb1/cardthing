"""
Store every product's Price check band (catalogue/fair_price.py, refresh_bands), for the lists' Fairest price
sort and the gauge on product cards. tidy_all runs it each hour; install.sh once after an update.

    python manage.py refresh_price_checks
"""

from django.core.management.base import BaseCommand

from catalogue import fair_price


class Command(BaseCommand):
    help = "Store each product's Price check band for lists."

    def handle(self, *args, **options):
        changed = fair_price.refresh_bands()
        if changed:
            from catalogue.signals import clear_list_caches

            clear_list_caches(force=True)
        self.stdout.write(f"{changed} Price checks changed.")
