"""
Read how many packs, and cards a pack, each product holds from the shops' own descriptions (catalogue/contents.py).
tidy_all runs it each hour; install.sh once after an update, so the product pages show it straight away.

    python manage.py refresh_contents
"""

from django.core.management.base import BaseCommand

from catalogue.contents import refresh_contents


class Command(BaseCommand):
    help = "Keep each product's packs and cards a pack from the shops' descriptions."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count what would change and change nothing.")

    def handle(self, *args, dry_run=False, **options):
        changed = refresh_contents(dry_run=dry_run)
        self.stdout.write(f"{changed} products' packs and cards {'would change' if dry_run else 'changed'}.")
