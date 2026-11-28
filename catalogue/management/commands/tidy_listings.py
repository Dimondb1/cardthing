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

A listing judged fine is not judged again until something it was judged on changes (its address, title or
barcode, its product's name, game, language or barcode, or the rules themselves): see Judged. Until 28 Nov 2026
every listing was read whole and judged afresh every hour, two thirds of the hourly tidy's processor time for
an answer that could not differ. The owner then, of the server: "here we are cpu sitting at 99 percent again".
"""

import hashlib
import re
from contextlib import nullcontext
from pathlib import Path

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalogue import languages, sanity
from catalogue.classify import NOT_SEALED, box_contents, find_game
from catalogue.ebay import too_cheap_listings, wrong_listings
from catalogue.importers import (
    barcode_owners, calls_itself_a_card, ean_key, link_key, listing_page, remove_not_sealed, slug_words, valid_gtin,
)
from catalogue.matching import (
    AUTO_LINK, DIFFERENT, LANGUAGE_DIFFERENT, contradiction, expand, key_words, match_key, score, shop_title,
)
from catalogue.models import DailyLowestPrice, Game, Listing, Product, shown_change
from catalogue.pricing import correct_daily_lowest
from catalogue.signals import clear_list_caches


PACK_WORDS = re.compile(r"\bpacks?\b|\bblister\b|\bchecklane\b|\bsleeved\b")


def same_language(title, product):
    """Whether an address that names a language names the product's own (catalogue/languages.py): plain Chinese
    is the same as Simplified or Traditional, as everywhere else."""
    game = product.game.slug
    theirs = languages.language_of(title, game)
    ours = getattr(product, "language", "") or languages.language_of(product.name, game)
    return bool(theirs) and not languages.mixed(title, game) and languages.same(theirs, ours)


MARKETPLACES = ("ebay", "amazon")
CHUNK = 500   # listings read whole at a time
_OWNED = {}


def owned_barcodes():
    """The barcodes that name one product, read once per tidy run (clear_owned_barcodes starts a run)."""
    if "keys" not in _OWNED:
        _OWNED["keys"] = set(barcode_owners())
    return _OWNED["keys"]


def clear_owned_barcodes():
    _OWNED.clear()


def title_misfit(title, name, game, language="", shop_ean="", ean="", marketplace=False):
    """Why the shop's own title plainly names another product than ours (matching.contradiction, the rule
    every read applies), or "". A shop barcode that is ours and a valid GTIN settles it the other way; a
    marketplace's barcode is our own, copied in, so it never does."""
    if not title:
        return ""
    if not marketplace and shop_ean and ean_key(shop_ean) == ean_key(ean) and valid_gtin(ean):
        return ""
    return contradiction(name, title, game, language)


def fits(listing):
    """Could the shop page be this product? Only a clear contradiction says no.

    A shop's list of products (a category page) is never one product's page. The shop's own title, when
    the listing kept it, is judged as every read judges it (title_misfit). The address is a slug, not the
    title the importer matched on, so it often lacks or garbles words. It is judged only on what it
    plainly says: a different game, a word that marks a different product (a language, an edition, an
    accessory), a different kind of thing (box against pack), or a single card.
    """
    if listing_page(listing.url):
        return False
    product = listing.product
    marketplace = bool(listing.retailer_id) and listing.retailer.source_type in MARKETPLACES
    # A barcode two of our products carry proves nothing (importers.barcode_owners).
    ean = product.ean if product.ean and ean_key(product.ean) in owned_barcodes() else ""
    if title_misfit(listing.title, product.name, product.game.slug, getattr(product, "language", ""),
                    listing.shop_ean or "", ean, marketplace):
        return False
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
            # A language word is judged by the language, not the word: "...-simplified-chinese-gem-4-booster-box"
            # is the product "Chinese Gem Pack Vol 4 Booster Box", whose name says Chinese less fully.
            if word in LANGUAGE_DIFFERENT and same_language(title, product):
                continue
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


class Judged:
    """The listings fits() last found fine, each with a fingerprint of everything it was judged on, kept in the
    shared cache between runs. fits() reads nothing but those values and the rules in the code, so a listing
    whose fingerprint has not moved gets the same answer, and is not read or judged again.

    The rules are part of it: the fingerprint of the code that decides (RULES) is kept with the listings, so the
    first tidy after an update judges every listing afresh, as "a rule added in code is applied to data already
    in the database" (tidy_all) asks. A cache emptied or culled only means everything is judged once more."""

    KEY = "tidy:listings:fine:v1"
    # The files whose code decides fits(): this command's own, and the rules it calls.
    RULES = ("management/commands/tidy_listings.py", "matching.py", "classify.py", "languages.py", "importers.py")
    # What a row carries after its id, in the order row_of gives them.
    FIELDS = ("url", "title", "shop_ean", "retailer__source_type", "product__name", "product__game__slug",
              "product__language", "product__ean")

    def __init__(self):
        self.rules = self.rules_now()
        kept = cache.get(self.KEY)
        self.fine = kept[1] if isinstance(kept, tuple) and len(kept) == 2 and kept[0] == self.rules else {}
        self.now = {}

    @classmethod
    def rules_now(cls):
        folder = Path(__file__).resolve().parent.parent.parent
        digest = hashlib.blake2b(digest_size=16)
        for name in cls.RULES:
            digest.update((folder / name).read_bytes())
        return digest.hexdigest()

    @staticmethod
    def fingerprint(row):
        """One listing's values, as fits() reads them: (url, title, shop barcode, how the shop is read, product
        name, game, language, the product's barcode when it names one product alone)."""
        url, title, shop_ean, source, name, game, language, ean = row
        owned = ean if ean and ean_key(ean) in owned_barcodes() else ""
        text = "\x1f".join((url or "", title or "", shop_ean or "", "m" if source in MARKETPLACES else "",
                             name or "", game or "", language or "", owned))
        return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=8).digest()

    def known(self, pk, mark):
        """Whether the listing was found fine on these very values. A listing that was is kept for the next run."""
        if self.fine.get(pk) != mark:
            return False
        self.now[pk] = mark
        return True

    def found_fine(self, pk, mark):
        self.now[pk] = mark

    def keep(self):
        """Remember this run's answers (only listings that still exist and were fine)."""
        cache.set(self.KEY, (self.rules, self.now), None)


def names_in_full(name, title, game):
    """Whether a shop title names every identifying word of our product's name, in the words matching uses
    (plurals and set codes spelt one way), and says nothing against it."""
    named = score(name, title, game) >= AUTO_LINK or key_words(name, game) <= key_words(shop_title(title), game)
    return named and not contradiction(name, title, game)


def shared_link_misfits(rows):
    """The listings to take off where one shop's link (its variant included) is on several of our products.

    ``rows`` are (listing or its id, retailer id, link, shop title, product name, game slug), and the first of
    each row to take off is returned. A link is one product.
    When the shop's titles on that link name exactly one of the products in full (every word of its name,
    and nothing matching.contradiction finds), the others go: Travelling Man's Avengers Assemble variant was
    on both our Avengers Assemble and Fantastic Four decks. When they name none or several, all stay for
    a person: nothing says which is right.
    """
    groups = {}
    for row in rows:
        groups.setdefault((row[1], link_key(row[2])), []).append(row)
    misfits = []
    for group in groups.values():
        if len({row[4] for row in group}) < 2:
            continue
        titles = {row[3] for row in group if row[3]}
        named = [row for row in group if any(names_in_full(row[4], title, row[5]) for title in titles)]
        if len(named) == 1:
            misfits += [row[0] for row in group if row is not named[0]]
    return misfits


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
    # A product whose own name names another game ("Star wars Booster Pack", filed as Pokemon from the shop's
    # tag) goes to the game its name says.
    for pk, name, current in Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug").iterator():
        game = find_game(name)
        if game is not None and game != current:
            moves[pk] = game
    games = {game.slug: game for game in Game.objects.filter(slug__in=set(moves.values()))}
    return {product: games[moves[product.pk]] for product in Product.objects.filter(pk__in=moves).select_related("game", "product_set")
            if moves[product.pk] in games}


class Command(BaseCommand):
    help = "Delete listings whose shop address does not read as their product."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        removed = 0
        clear_owned_barcodes()
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
            # Every listing as a row of the values it is judged on, not as a whole listing: only those not yet
            # found fine on these values (Judged) are read whole and judged.
            judged = Judged()
            rows = list(Listing.objects.values_list("pk", "retailer_id", *Judged.FIELDS).iterator())
            marks = {row[0]: Judged.fingerprint(row[2:]) for row in rows}
            to_judge = [row[0] for row in rows if not judged.known(row[0], marks[row[0]])]
            whole = {}
            for start in range(0, len(to_judge), CHUNK):
                whole.update(Listing.objects.select_related("product__game", "retailer")
                             .in_bulk(to_judge[start:start + CHUNK]))
            gone = set()
            for pk in to_judge:
                listing = whole.get(pk)
                if listing is None:
                    continue   # deleted since the rows were read
                if fits(listing):
                    judged.found_fine(pk, marks[pk])
                else:
                    gone.add(pk)
            misfit_pks = [pk for pk in to_judge if pk in gone]
            shared = shared_link_misfits([
                (pk, retailer_id, url, title, name, game)
                for pk, retailer_id, url, title, _ean, _source, name, game, _language, _product_ean in rows
                if pk not in gone
            ])
            misfit_pks += shared
            # A listing whose shop description calls it a card from a product ("... is a Basic Pokemon Card from
            # the My First Battle Deck", Magic Madhouse) is a single card, whatever its title: the page reader
            # now refuses such pages (importers.not_sealed_reason), and these were linked before it did.
            gone |= set(shared)
            described = dict(Listing.objects.filter(description__icontains="card from").values_list("pk", "description"))
            single_pks = [pk for pk, _retailer, _url, title, _ean, _source, name, *_rest in rows
                          if pk in described and pk not in gone and calls_itself_a_card(title or name, described[pk])]
            missing = [pk for pk in misfit_pks + single_pks if pk not in whole]
            whole.update(Listing.objects.select_related("product__game", "retailer").in_bulk(missing))
            misfits = [whole[pk] for pk in misfit_pks if pk in whole]
            singles = [whole[pk] for pk in single_pks if pk in whole]
            if not dry_run:
                # What was fine and stays: the listings taken off below are gone, and are in none of it.
                for pk in list(shared) + single_pks:
                    judged.now.pop(pk, None)
                judged.keep()
            for listing in singles:
                self.stdout.write(f"{'would remove' if dry_run else 'removed'} (a single card): {listing.product.name} <- "
                                  f"{listing.retailer.name} {listing.url}")
            if singles:
                removed += len(singles)
                if not dry_run:
                    with transaction.atomic():
                        remove_not_sealed(listing.pk for listing in singles)
            for listing in misfits:
                removed += 1
                self.stdout.write(f"{'would remove' if dry_run else 'removed'}: {listing.product.name} <- {listing.retailer.name} {listing.url}")
                if not dry_run:
                    with transaction.atomic():
                        listing.delete()
            # An eBay listing whose title is plainly another thing (a single card, a part, a multi-buy, a code)
            # is removed, in stock or not: the next eBay search can find the product itself. One far under
            # every shop's price is hidden until eBay is searched again. Either way its price was never the
            # product's, so the days it set as the product's lowest go from the record (the Price check never
            # calls a real price dear against it) and today is worked out again from what still counts.
            wrong = wrong_listings()
            for listing in wrong:
                self.stdout.write(f"{'would remove' if dry_run else 'removed'}: {listing.product.name} <- eBay "
                                  f"{listing.title}")
            removed += len(wrong)
            cheap = [listing for listing in too_cheap_listings() if listing not in wrong]
            for listing in cheap:
                self.stdout.write(f"{'would hide' if dry_run else 'hidden'}: {listing.product.name} <- eBay £{listing.delivered_price}")
            if not dry_run:
                with transaction.atomic():
                    Listing.objects.filter(pk__in=[listing.pk for listing in wrong]).delete()
                    Listing.objects.filter(pk__in=[listing.pk for listing in cheap]).exclude(
                        availability=Listing.Availability.OUT_OF_STOCK
                    ).update(
                        availability=Listing.Availability.OUT_OF_STOCK, changed_at=shown_change(timezone.now())
                    )
                    for listing in wrong + cheap:
                        DailyLowestPrice.objects.filter(product_id=listing.product_id,
                                                        price=listing.delivered_price).delete()
                        correct_daily_lowest(listing.product_id)
                if wrong or cheap:
                    clear_list_caches(force=True)
            # A search result for a product no UK shop lists is doubtful (sanity.ONLY_MARKETPLACE), put to Claude
            # and never claimed as a low. A price check gives the verdict, but one left alone when its last shop
            # listing went may not be checked for a day, so such products are judged again here.
            shop_listed = Listing.objects.exclude(retailer__source_type__in=sanity.MARKETPLACES).values("product_id")
            alone = set(
                Listing.objects.filter(retailer__source_type__in=sanity.MARKETPLACES, is_active=True,
                                       sanity=Listing.Sanity.OK, product__is_active=True)
                .exclude(product_id__in=shop_listed).values_list("product_id", flat=True)
            )
            if not dry_run:
                for product_id in sorted(alone):
                    sanity.judge_product(product_id)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{removed} listings. {Listing.objects.count()} remain. {len(cheap)} eBay prices too low to trust.")
