"""Drop review rows that are not sealed products (singles, accessories, events)."""

from django.core.management.base import BaseCommand

from catalogue.classify import classify
from catalogue.models import ShopProduct


class Command(BaseCommand):
    help = "Remove shop products from the review queue that are not sealed TCG products."

    def handle(self, *args, **options):
        removed = 0
        for row in ShopProduct.objects.filter(status=ShopProduct.Status.REVIEW).iterator():
            if classify(row.title, price=row.price) is None:
                row.delete()
                removed += 1
        self.stdout.write(f"Removed {removed}. {ShopProduct.objects.filter(status='review').count()} left to review.")
