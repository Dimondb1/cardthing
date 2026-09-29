"""Remove products the classifier no longer accepts (after its rules improve)."""

from django.core.management.base import BaseCommand

from catalogue.classify import NOT_SEALED
from catalogue.models import Product


class Command(BaseCommand):
    help = "Delete automatically created products whose names now fail the sealed-product rules."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        doomed = [p for p in Product.objects.filter(image="") if NOT_SEALED.search(p.name)]
        for p in doomed:
            self.stdout.write(f"{'would remove' if dry_run else 'removed'}: {p.name}")
        if not dry_run:
            Product.objects.filter(pk__in=[p.pk for p in doomed]).delete()
        self.stdout.write(f"{len(doomed)} products. {Product.objects.count()} remain.")
