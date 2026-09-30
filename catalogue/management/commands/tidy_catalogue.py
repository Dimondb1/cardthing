"""Re-check automatically created products after the classifier's rules improve."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Min

from catalogue.classify import MIN_PRICE, NOT_SEALED, is_generic, tidy_name
from catalogue.models import Listing, Product


class Command(BaseCommand):
    help = (
        "Delete automatically created products that now fail the sealed-product rules "
        "(single cards, accessories, generic names, prices below the minimum) and tidy names."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        removed = renamed = merged = 0
        rows = (
            Product.objects.filter(image="")
            .annotate(cheapest=Min("listings__price"), listing_count=Count("listings"))
            .order_by("pk")
        )
        with transaction.atomic():
            for product in rows:
                name = tidy_name(product.name)
                reason = None
                if product.listing_count == 0:
                    reason = "no shop lists it"
                elif NOT_SEALED.search(product.name):
                    reason = "not sealed"
                elif is_generic(name):
                    reason = "generic name"
                elif product.cheapest is not None and product.cheapest < MIN_PRICE:
                    reason = f"cheapest listing {product.cheapest}"
                if reason:
                    self.stdout.write(f"{'would remove' if dry_run else 'removed'} ({reason}): {product.name}")
                    removed += 1
                    if not dry_run:
                        product.delete()
                    continue
                if name != product.name:
                    other = Product.objects.filter(game=product.game, name=name).exclude(pk=product.pk).first()
                    if other is not None:
                        self.stdout.write(f"{'would merge' if dry_run else 'merged'}: {product.name} -> {other.name}")
                        merged += 1
                        if not dry_run:
                            for listing in product.listings.all():
                                if Listing.objects.filter(product=other, retailer=listing.retailer).exists():
                                    listing.delete()
                                else:
                                    listing.product = other
                                    listing.save(update_fields=["product"])
                            product.delete()
                        continue
                    self.stdout.write(f"{'would rename' if dry_run else 'renamed'}: {product.name} -> {name}")
                    renamed += 1
                    if not dry_run:
                        product.name = name
                        product.save(update_fields=["name"])
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(
            f"{removed} removed, {renamed} renamed, {merged} merged. {Product.objects.count()} products remain."
        )
