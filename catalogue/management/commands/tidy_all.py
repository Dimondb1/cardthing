"""
Run every cleanup in the right order after an import or a code update.

    python manage.py tidy_all

tidy_listings (a shop item linked to the wrong product), merge_duplicates
(one product under two names), tidy_catalogue (products that no longer pass
the rules). Run by start.bat, update_prices and the server's hourly import,
so a rule added in code is applied to data already in the database.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run tidy_listings, merge_duplicates and tidy_catalogue in order."

    def handle(self, *args, **options):
        for name in ("tidy_listings", "merge_duplicates", "tidy_catalogue"):
            self.stdout.write(f"== {name}")
            call_command(name, stdout=self.stdout)
