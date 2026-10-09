"""Re-check automatically created products after the classifier's rules improve."""

from contextlib import nullcontext

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Min

from catalogue.classify import find_type, MIN_PRICE, NOT_SEALED, is_generic, tidy_name
from catalogue.models import Listing, Product, ProductAlias


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
            # A product an undoable merge switched off has no listings; deleting it would leave nothing
            # for Undo to put back. Its own address is then an old address of the kept product.
            .exclude(is_active=False, slug__in=ProductAlias.objects.values("slug"))
            .annotate(cheapest=Min("listings__price"), listing_count=Count("listings"))
            .order_by("pk")
        )
        # Each product is its own short transaction, so nothing else waits for the whole walk.
        # A dry run wraps them all in one transaction and rolls it back.
        with transaction.atomic() if dry_run else nullcontext():
            for product in rows:
                with transaction.atomic():
                    done = self.tidy(product, dry_run)
                removed += done == "removed"
                renamed += done == "renamed"
                merged += done == "merged"
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(
            f"{removed} removed, {renamed} renamed, {merged} merged. {Product.objects.count()} products remain."
        )

    def tidy(self, product, dry_run):
        """Remove, retype, merge or rename one product. Returns what was done, or "" for nothing."""
        name = tidy_name(product.name)
        reason = None
        # The count was taken before the walk; a product merged into this one earlier in the run now
        # has its listings, so ask again before removing it and taking a live price with it.
        if product.listing_count == 0 and not product.listings.exists():
            reason = "no shop lists it"
        elif NOT_SEALED.search(product.name):
            reason = "not sealed"
        elif is_generic(name):
            reason = "generic name"
        elif product.cheapest is not None and product.cheapest < MIN_PRICE:
            reason = f"cheapest listing {product.cheapest}"
        if reason:
            self.stdout.write(f"{'would remove' if dry_run else 'removed'} ({reason}): {product.name}")
            if not dry_run:
                product.delete()
            return "removed"
        kind = find_type(name)
        if kind and kind.startswith("collector_") and product.product_type != kind:
            self.stdout.write(f"{'would retype' if dry_run else 'retyped'}: {product.name} -> {kind}")
            if not dry_run:
                product.product_type = kind
                product.save(update_fields=["product_type"])
        if name != product.name:
            other = Product.objects.filter(game=product.game, name=name).exclude(pk=product.pk).first()
            if other is not None:
                self.stdout.write(f"{'would merge' if dry_run else 'merged'}: {product.name} -> {other.name}")
                if not dry_run:
                    for listing in product.listings.all():
                        if Listing.objects.filter(product=other, retailer=listing.retailer).exists():
                            listing.delete()
                        else:
                            listing.product = other
                            listing.save(update_fields=["product"])
                    product.delete()
                return "merged"
            self.stdout.write(f"{'would rename' if dry_run else 'renamed'}: {product.name} -> {name}")
            if not dry_run:
                product.name = name
                product.save(update_fields=["name"])
            return "renamed"
        return ""
