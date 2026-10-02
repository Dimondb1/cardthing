"""
Merge products that are the same thing under slightly different names.

    python manage.py merge_duplicates --dry-run
    python manage.py merge_duplicates

Shops write the same product differently ("Commander Legends: Battle for
Baldur's Gate Bundle" and "Commander Legends Battle For Baldurs Gate
Bundle"), so one product can end up on two pages, each with one shop. This
groups products of one game and type whose names share the same matching
key, keeps the one with the most listings (or an image), moves the other
listings across and deletes the rest.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.matching import match_key
from catalogue.models import Listing, Product, ProductAlias


def merge(keep, others):
    """Move listings from ``others`` onto ``keep`` and delete them. Returns listings moved."""
    moved = 0
    for other in others:
        for listing in other.listings.all():
            if Listing.objects.filter(product=keep, retailer=listing.retailer).exists():
                listing.delete()
            else:
                listing.product = keep
                listing.save(update_fields=["product"])
                moved += 1
        if not keep.image_url and other.image_url:
            keep.image_url = other.image_url
            keep.save(update_fields=["image_url"])
        if not keep.ean and other.ean:
            keep.ean = other.ean
            keep.save(update_fields=["ean"])
        # The old address keeps working: it sends visitors and search engines to the kept page.
        ProductAlias.objects.filter(product=other).update(product=keep)
        ProductAlias.objects.filter(slug=other.slug).delete()
        other.delete()
        ProductAlias.objects.create(slug=other.slug, product=keep)
    return moved


class Command(BaseCommand):
    help = "Merge products whose names identify the same thing."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        groups = defaultdict(list)
        for product in Product.objects.filter(is_active=True).order_by("pk"):
            groups[(product.game_id, product.product_type, match_key(product.name))].append(product)
        merged = moved = 0
        with transaction.atomic():
            for group in groups.values():
                if len(group) < 2:
                    continue
                group.sort(key=lambda p: (-p.listings.count(), not p.image, not p.image_url, len(p.name), p.pk))
                keep, others = group[0], group[1:]
                self.stdout.write(f"{'would keep' if dry_run else 'kept'}: {keep.name}")
                for other in others:
                    self.stdout.write(f"    merged: {other.name}")
                merged += len(others)
                if not dry_run:
                    moved += merge(keep, others)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{merged} products merged, {moved} listings moved. {Product.objects.count()} products remain.")
