"""
Work out every product's language from its name and set (catalogue/languages.py).

    python manage.py set_languages            # after an update; tidy_all does it every hour too
    python manage.py set_languages --dry-run  # say how many would change
"""

from django.core.management.base import BaseCommand

from catalogue.languages import refresh_languages


class Command(BaseCommand):
    help = "Set each product's language from its name and set, for the language filter."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Change nothing; say how many would change.")

    def handle(self, *args, dry_run=False, **options):
        changed = refresh_languages(dry_run=dry_run)
        self.stdout.write(f"{changed} product languages {'would change' if dry_run else 'set'}.")
