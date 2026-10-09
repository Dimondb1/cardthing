"""
Run every cleanup in the right order after an import or a code update.

    python manage.py tidy_all

tidy_listings (a shop item linked to the wrong product), merge_duplicates
(one product under two names), tidy_catalogue (products that no longer pass
the rules), then forgets website shop pages no sitemap has listed for 30
days. Run by start.bat, update_prices and the server's hourly import,
so a rule added in code is applied to data already in the database.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run tidy_listings, merge_duplicates and tidy_catalogue in order, then forget pages gone from shop sitemaps."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what each would do and change nothing.")

    def handle(self, *args, dry_run=False, **options):
        for name in ("tidy_listings", "merge_duplicates", "tidy_catalogue"):
            self.stdout.write(f"== {name}")
            call_command(name, dry_run=dry_run, stdout=self.stdout)
        from catalogue.importers import forget_pages

        self.stdout.write("== shop pages")
        gone = forget_pages(dry_run=dry_run)
        self.stdout.write(f"{gone} shop pages {'would be forgotten' if dry_run else 'forgotten'}.")
