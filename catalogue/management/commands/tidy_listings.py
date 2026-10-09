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
from contextlib import nullcontext

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.classify import NOT_SEALED, box_contents, find_game
from catalogue.ebay import EbayError, fill_titles, too_cheap_listings
from catalogue.importers import slug_words
from catalogue.matching import DIFFERENT, expand, match_key
from catalogue.models import Game, Listing, Product
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
    # "...-booster-box-36x-packs" says what is inside the box, the same rule classify uses.
    if NOT_SEALED.search(box_contents(title, bracketed=False)) and not NOT_SEALED.search(product.name):
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


def misfiled():
    """{product: game} for products whose shop addresses all name one other game.

    A shop can tag a Magic box as Pokemon; the product is then made under the
    wrong game and its right listings look wrong. Only products whose own
    name names no game are moved, and only when every address that names a
    game agrees.
    """
    named = {}
    rows = Listing.objects.filter(product__is_active=True).values_list("product_id", "url", "product__game__slug", "product__name")
    for product_id, url, current, name in rows.iterator():
        game = find_game(slug_words(url))
        if game is None or find_game(name) is not None:
            continue
        named.setdefault(product_id, (current, set()))[1].add(game)
    moves = {pk: games.pop() for pk, (current, games) in named.items() if len(games) == 1 and current not in games}
    games = {game.slug: game for game in Game.objects.filter(slug__in=set(moves.values()))}
    return {product: games[moves[product.pk]] for product in Product.objects.filter(pk__in=moves).select_related("game", "product_set")
            if moves[product.pk] in games}


class Command(BaseCommand):
    help = "Delete listings whose shop address does not read as their product."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        removed = 0
        # eBay listings saved before titles were kept cannot be judged by their title, so ask eBay for it.
        # Not in a dry run: fetching saves each title, and a dry run changes nothing.
        if dry_run:
            self.stdout.write("eBay titles not fetched in a dry run.")
        else:
            try:
                filled = fill_titles()
            except EbayError as exc:
                filled = 0
                self.stdout.write(f"eBay titles not fetched: {exc}")
            if filled:
                self.stdout.write(f"{filled} eBay titles fetched.")
        # Each change is its own short transaction, so a web request or another job never waits
        # for a whole table walk. A dry run wraps them all in one transaction and rolls it back.
        with transaction.atomic() if dry_run else nullcontext():
            for product, game in misfiled().items():
                self.stdout.write(f"{'would move' if dry_run else 'moved'}: {product.name} from {product.game.name} to {game.name}")
                # Saved in a dry run too: the transaction is rolled back, and the listings below are
                # then judged against the right game, as they will be for real.
                with transaction.atomic():
                    product.game = game
                    if product.product_set_id and product.product_set.game_id != game.pk:
                        product.product_set = None
                    product.save(update_fields=["game", "product_set"])
            # Judge every listing first, then delete. A write while the chunked read is still open would
            # find its snapshot out of date as soon as another process commits, and SQLite then reports
            # the database locked at once rather than waiting.
            misfits = [
                listing for listing in Listing.objects.select_related("product__game", "retailer").iterator()
                if not fits(listing)
            ]
            for listing in misfits:
                removed += 1
                self.stdout.write(f"{'would remove' if dry_run else 'removed'}: {listing.product.name} <- {listing.retailer.name} {listing.url}")
                if not dry_run:
                    with transaction.atomic():
                        listing.delete()
            # An eBay listing the current rules refuse (far under every shop's price, or a title that is a
            # multi-buy, a code or a sampling pack) is hidden until eBay is searched again.
            cheap = too_cheap_listings()
            for listing in cheap:
                self.stdout.write(f"{'would hide' if dry_run else 'hidden'}: {listing.product.name} <- eBay £{listing.delivered_price}")
            if not dry_run:
                with transaction.atomic():
                    Listing.objects.filter(pk__in=[listing.pk for listing in cheap]).update(
                        availability=Listing.Availability.OUT_OF_STOCK
                    )
                if cheap:
                    clear_list_caches(force=True)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{removed} listings. {Listing.objects.count()} remain. {len(cheap)} eBay prices too low to trust.")
