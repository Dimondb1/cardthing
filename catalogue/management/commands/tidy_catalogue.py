"""Re-check automatically created products after the classifier's rules improve.

A product removed here leaves its address behind as a RemovedProduct, so a visit answers 410 Gone with links
to its set and game. A product renamed onto the name of another of the same game, type and language is merged
into it as merge_duplicates merges, so its old address redirects there and its history and alerts move too,
and the merge is listed under Sorted for you on Things to check with Undo (since 17 Nov 2026, when the owner
chose that the pairs the rules are sure of merge by themselves, each with Undo; before, this merge deleted the
product). A pair the owner said are not the same, or whose merge anyone undid, is never merged here: the
product is renamed and the two stand side by side. A rename never changes an address.
"""

import re
from contextlib import nullcontext

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Min

from catalogue.classify import find_type, MIN_PRICE, NOT_SEALED, is_generic, tidy_name
from catalogue.languages import product_language
from catalogue.management.commands.merge_duplicates import merge_with_undo, undone_merges
from catalogue.matching import own_name_off
from catalogue.models import Game, Listing, Product, ProductAlias, RemovedProduct
from catalogue.pricing import MARKETPLACES
from catalogue.types import owner_typed


# "Center Chaos Rising Elite Trainer Box": what is left of "Pokémon Center ..." once the game's name went.
CENTER_HEAD = re.compile(r"Cent(?:er|re)\s+\S")

# Why the tidy joined two products, as Sorted for you on Things to check says it.
TIDY_WHY = "The hourly tidy merged them: with the shop's wording taken off, the two names are the same"


class Command(BaseCommand):
    help = (
        "Delete automatically created products that now fail the sealed-product rules "
        "(single cards, accessories, generic names, prices below the minimum) and tidy names."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        removed = renamed = merged = 0
        # A type the owner set (in Django Admin or his spreadsheet) is his, as refresh_types leaves it.
        typed = owner_typed()
        # His Not the same stands, and so does an Undo of a merge, as for the hourly merge.
        from catalogue.checks import kept_apart

        self.apart = kept_apart() | undone_merges()
        self.pokemon = Game.objects.filter(slug="pokemon").values_list("pk", flat=True).first()
        # The shops listing each product, by name: a name carrying one of theirs at its end or start loses it
        # ("Girls Band Cry Premium Booster Box Topaz Cards", from before a read took it off a shop's titles).
        self.shops = {}
        for product_id, shop in (Listing.objects.exclude(retailer__source_type__in=MARKETPLACES)
                                 .values_list("product_id", "retailer__name").order_by()):
            self.shops.setdefault(product_id, set()).add(shop)
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
                    done = self.tidy(product, dry_run, typed)
                removed += done == "removed"
                renamed += done == "renamed"
                merged += done == "merged"
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(
            f"{removed} removed, {renamed} renamed, {merged} merged. {Product.objects.count()} products remain."
        )

    def tidy(self, product, dry_run, typed=frozenset()):
        """Remove, retype, merge or rename one product. Returns what was done, or "" for nothing."""
        name = tidy_name(product.name)
        for shop in sorted(getattr(self, "shops", {}).get(product.pk, ())):
            name = tidy_name(own_name_off(name, shop)) or name
        if product.game_id == getattr(self, "pokemon", None) and CENTER_HEAD.match(name):
            # A Pokémon Center edition whose "Pokémon" a read took for the game's name (classify.PREFIXES).
            name = f"Pokémon {name}"
        reason = None
        # The count was taken before the walk; a product merged into this one earlier in the run now
        # has its listings, so ask again before removing it and taking a live price with it.
        # A product given its own release date was added by hand (admin or import_products), not by a shop
        # read: it waits for a shop, eBay or Amazon to list it rather than being removed.
        if product.listing_count == 0 and product.release_date is None and not product.listings.exists():
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
                # Its address, and any old address that led to it, answer 410 Gone from now on.
                RemovedProduct.record(product)
                product.delete()
            return "removed"
        kind = find_type(name)
        if kind and kind.startswith("collector_") and product.product_type != kind and product.pk not in typed:
            self.stdout.write(f"{'would retype' if dry_run else 'retyped'}: {product.name} -> {kind}")
            if not dry_run:
                product.product_type = kind
                product.save(update_fields=["product_type"])
        if name != product.name:
            # Only a product of the same type is the same thing (a collector booster box and a booster box can
            # share a name until one is retyped), and only one that is shown: merging into a product the owner
            # switched off would take this page off the site.
            other = (
                Product.objects.filter(game=product.game, name=name, product_type=product.product_type, is_active=True)
                .exclude(pk=product.pk).first()
            )
            if other is not None and product_language(other) != product_language(product):
                # One name in two languages (one filed under a Japanese set): two products, never merged.
                other = None
            if other is not None and frozenset((other.pk, product.pk)) in getattr(self, "apart", ()):
                # The owner said they are not the same, or undid their merge: renamed, and left side by side.
                other = None
            if other is not None:
                self.stdout.write(f"{'would merge' if dry_run else 'merged'}: {product.name} -> {other.name}")
                if not dry_run:
                    # Listings, price history, restocks, clicks and alerts move across, the old address redirects
                    # to the kept product for good, and Things to check lists it with Undo.
                    merge_with_undo(other, product, TIDY_WHY)
                return "merged"
            self.stdout.write(f"{'would rename' if dry_run else 'renamed'}: {product.name} -> {name}")
            if not dry_run:
                product.name = name
                product.save(update_fields=["name"])
            return "renamed"
        return ""
