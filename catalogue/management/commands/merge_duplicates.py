"""
Merge products that are the same thing under slightly different names.

    python manage.py merge_duplicates --dry-run
    python manage.py merge_duplicates
    python manage.py merge_duplicates --loose --dry-run

--loose also ignores words shops add that do not change the product
("Exclusive", "English", "TCG", and for Pokémon the series name, such as
"Mega Evolution" or "Scarlet & Violet"). It never runs on its own: read the
dry run, then run it without --dry-run if every group is right.

Shops write the same product differently ("Commander Legends: Battle for
Baldur's Gate Bundle" and "Commander Legends Battle For Baldurs Gate
Bundle"), so one product can end up on two pages, each with one shop. This
groups products of one game and type whose names share the same matching
key, keeps the one with the most listings (or an image), moves the other
listings, price history, restocks, clicks and stock alerts across, and
deletes the rest. The old addresses redirect to the kept product, and
watchlists saved in browsers follow them. Run with --dry-run first.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.matching import match_key
from catalogue.models import DailyLowestPrice, Listing, OutboundClick, Product, ProductAlias, Restock, StockAlert


# Words that never change which product it is.
FILLER = {"exclusive", "english", "tcg", "edition"}
# Pokémon series names, which some shops put before the set name and others leave out.
POKEMON_SERIES = {"pokemon", "mega", "evolution", "base", "set", "scarlet", "violet", "sv", "sword", "shield", "swsh"}
# Words that say what kind of thing it is, not which one.
KIND_WORDS = {"elite", "trainer", "box", "center", "booster", "bundle", "display", "pack", "packs", "case", "tin", "collection"}


def identifying(words):
    return [w for w in words if w not in KIND_WORDS and not w.startswith("#")]


def merge_key(name):
    """The matching key, with a plural dropped when its singular is already there: "Booster Bundle
    Display (10 Bundles)" and "Booster Bundle Display (10)" name the same thing.

    "" when nothing but the kind of product is left: the matching key drops series names, so
    "Scarlet & Violet Elite Trainer Box" and "Sword & Shield Elite Trainer Box" would otherwise meet.
    """
    words = match_key(name).split()
    kept = [w for w in words if not (w.endswith("s") and w[:-1] in words)]
    return " ".join(kept) if identifying(kept) else ""


def loose_key(name, game_slug):
    """``merge_key`` without filler and series words, or "" when nothing identifying is left
    (a bare "Scarlet & Violet Elite Trainer Box" must never meet "Sword & Shield Elite Trainer Box")."""
    noise = FILLER | (POKEMON_SERIES if game_slug == "pokemon" else set())
    words = [w for w in merge_key(name).split() if w not in noise]
    if not identifying(words):
        return ""
    return " ".join(sorted(set(words)))


def carry_history(keep, other):
    """Move what belongs to ``other`` onto ``keep``, so a merge loses no history."""
    lows = dict(DailyLowestPrice.objects.filter(product=keep).values_list("date", "price"))
    for row in DailyLowestPrice.objects.filter(product=other):
        if row.date not in lows:
            row.product = keep
            row.save(update_fields=["product"])
        elif row.price < lows[row.date]:
            DailyLowestPrice.objects.filter(product=keep, date=row.date).update(price=row.price)
    Restock.objects.filter(product=other).update(product=keep)
    OutboundClick.objects.filter(product=other).update(product=keep)
    asked = set(StockAlert.objects.filter(product=keep).values_list("email", flat=True))
    StockAlert.objects.filter(product=other).exclude(email__in=asked).update(product=keep)


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
        carry_history(keep, other)
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


def duplicate_groups(loose=False):
    """[(keep, [others])] for every group of products that name the same thing, the one to keep first:
    most listings, then one with an image, then the shortest name."""
    groups = defaultdict(list)
    for product in Product.objects.filter(is_active=True).select_related("game").order_by("pk"):
        key = loose_key(product.name, product.game.slug) if loose else merge_key(product.name)
        if key:
            groups[(product.game_id, product.product_type, key)].append(product)
    found = []
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda p: (-p.listings.count(), not p.image, not p.image_url, len(p.name), p.pk))
        found.append((group[0], group[1:]))
    return sorted(found, key=lambda row: row[0].name)


class Command(BaseCommand):
    help = "Merge products whose names identify the same thing."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--loose", action="store_true", help="Also ignore filler and series words. Review a dry run first.")

    def handle(self, *args, dry_run=False, loose=False, **options):
        merged = moved = 0
        with transaction.atomic():
            for keep, others in duplicate_groups(loose):
                self.stdout.write(f"{'would keep' if dry_run else 'kept'}: {keep.name}")
                for other in others:
                    self.stdout.write(f"    merged: {other.name}")
                merged += len(others)
                if not dry_run:
                    moved += merge(keep, others)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{merged} products merged, {moved} listings moved. {Product.objects.count()} products remain.")
