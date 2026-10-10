"""
Retype every product whose name plainly says another type (catalogue/types.py refresh_types).

    python manage.py set_types            # after an update; tidy_all does it every hour too
    python manage.py set_types --dry-run  # list what would change
"""

from django.core.management.base import BaseCommand

from catalogue.types import refresh_types


class Command(BaseCommand):
    help = "Retype products whose name plainly says another type, so the type filter is exact."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Change nothing; list what would change.")

    def handle(self, *args, dry_run=False, **options):
        changed = refresh_types(dry_run=dry_run, log=self.stdout.write if dry_run or options["verbosity"] > 1 else None)
        self.stdout.write(f"{changed} product types {'would change' if dry_run else 'set'}.")
