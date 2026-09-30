"""
Remove shop listings that were linked to the wrong product.

    python manage.py tidy_listings --dry-run
    python manage.py tidy_listings

Before matching required the same game and most of the shop title's words,
a short product name such as "Invasion Booster Box" could take on a Yu-Gi-Oh
box, a Cardfight Vanguard box and a single card. This re-checks every
listing against the words in its shop address and drops those that fail;
the next import puts them back on the right product or in the review queue.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.classify import classify, find_game
from catalogue.importers import slug_words
from catalogue.matching import AUTO_LINK, REVERSE_LINK, match_key, score
from catalogue.models import Listing


def fits(listing):
    """Does the shop address read as the listing's product, of the same game?"""
    title = slug_words(listing.url)
    if not title:
        return True  # nothing to check against
    product = listing.product
    # An address that names a game must name this product's game. One that
    # names none ("ixalan-booster-pack") is judged on its words alone.
    named = find_game(title)
    if named is not None and named != product.game.slug:
        return False
    if classify(title, vendor=product.game.name, tags=(title,)) is None:
        return False
    if match_key(title) == match_key(product.name):
        return True
    return score(product.name, title) >= AUTO_LINK and score(title, product.name) >= REVERSE_LINK


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
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{removed} listings. {Listing.objects.count()} remain.")
