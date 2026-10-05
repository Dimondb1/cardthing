"""
Remove shop listings that were linked to the wrong product.

    python manage.py tidy_listings --dry-run
    python manage.py tidy_listings

Before matching required the same game and agreement both ways, a short
product name such as "Invasion Booster Box" could take on a Yu-Gi-Oh box, a
Cardfight Vanguard box and a single card. This re-checks every listing
against the words in its shop address and drops those that plainly
contradict the product: another game, a language or edition word, an
accessory or single, or a box where the product is a pack. An address that
merely lacks words is left alone, because slugs drop and garble words.
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.classify import NOT_SEALED, find_game
from catalogue.ebay import too_cheap_listings
from catalogue.importers import slug_words
from catalogue.matching import DIFFERENT, expand, match_key
from catalogue.models import Listing
from catalogue.signals import clear_list_caches


PACK_WORDS = re.compile(r"\bpacks?\b|\bblister\b|\bchecklane\b|\bsleeved\b")


def fits(listing):
    """Could the shop address be this product? Only a clear contradiction says no.

    The address is a slug, not the title the importer matched on, so it often
    lacks or garbles words. It is judged only on what it plainly says: a
    different game, a word that marks a different product (a language, an
    edition, an accessory), a different kind of thing (box against pack), or
    a single card.
    """
    title = slug_words(listing.url)
    if not title:
        return True  # nothing to check against
    product = listing.product
    named = find_game(title)
    if named is not None and named != product.game.slug:
        return False
    if NOT_SEALED.search(title) and not NOT_SEALED.search(product.name):
        return False
    title_text = " " + expand(title) + " "
    name_text = " " + expand(product.name) + " "
    for word in DIFFERENT:
        if f" {word} " in title_text and f" {word} " not in name_text:
            return False
    title_marks = {m for m in match_key(title).split() if m.startswith("#")}
    name_marks = {m for m in match_key(product.name).split() if m.startswith("#")}
    if not PACK_WORDS.search(title):
        # A slug ending "...-booster" is as often the box as the pack ("blazing-dominion-booster"),
        # and "151-booster-etb" is an ETB, so a bare "booster" says nothing about the kind.
        title_marks.discard("#pack")
    # The address names a kind of thing the product is not. A name that lists its contents
    # ("Booster Box (36 Booster Packs)") carries both kinds, so the address need only name one.
    if title_marks and name_marks and title_marks - name_marks:
        return False
    return True


class Command(BaseCommand):
    help = "Delete listings whose shop address does not read as their product."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        removed = 0
        with transaction.atomic():
            for listing in Listing.objects.select_related("product__game", "retailer").iterator():
                if fits(listing):
                    continue
                removed += 1
                self.stdout.write(f"{'would remove' if dry_run else 'removed'}: {listing.product.name} <- {listing.retailer.name} {listing.url}")
                if not dry_run:
                    listing.delete()
            # An eBay price far under every shop's is not the sealed product: hide it until eBay is
            # searched again, when a genuine listing can take its place.
            cheap = too_cheap_listings()
            for listing in cheap:
                self.stdout.write(f"{'would hide' if dry_run else 'hidden'}: {listing.product.name} <- eBay £{listing.delivered_price}")
            if not dry_run:
                Listing.objects.filter(pk__in=[listing.pk for listing in cheap]).update(
                    availability=Listing.Availability.OUT_OF_STOCK
                )
                if cheap:
                    clear_list_caches()
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{removed} listings. {Listing.objects.count()} remain. {len(cheap)} eBay prices too low to trust.")
