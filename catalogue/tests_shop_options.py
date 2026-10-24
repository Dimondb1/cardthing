"""Shop wording that kept one product on several pages (catalogue/matching.py chosen_option, key_text; catalogue/
management/commands/merge_duplicates.py pair_keys). The owner, of five pages for the 30th Celebration Greninja ex
tin: "Same item, different listing, what is Claude doing". Claude was never asked: the names never paired."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from . import checks
from .classify import tidy_name
from .management.commands.merge_duplicates import merge_key
from .matching import chosen_option
from .models import Product
from .testing import make_game, make_listing, make_product, make_retailer, make_set

PAGES = ("ex Tin Greninja 30th Celebration", "30th Celebration Tin (Greninja ex) (Default)",
         "30th Anniversary Greninja Ex Tin", "30th Celebration Sylveon / Greninja Ex Tin (Greninja)")


class WordingTests(TestCase):
    def test_a_shops_chosen_option_and_its_default_variant(self):
        self.assertEqual(chosen_option("30th Celebration Sylveon / Greninja Ex Tin (Greninja)"),
                         "30th Celebration Greninja Ex Tin")
        self.assertEqual(chosen_option("30th Celebration Sylveon / Greninja Ex Tin (Sylveon)"),
                         "30th Celebration Sylveon Ex Tin")
        self.assertEqual(chosen_option("Battle Deck Charizard ex / Pikachu ex (Pikachu ex)"), "Battle Deck Pikachu ex")
        self.assertEqual(chosen_option("Paldea Collection Sprigatito / Fuecoco / Quaxly (Sprigatito)"),
                         "Paldea Collection Sprigatito")
        self.assertEqual(chosen_option("Paldea Collection Sprigatito / Fuecoco / Quaxly (Fuecoco)"), "Paldea Collection Fuecoco")
        self.assertEqual(chosen_option("30th Celebration Tin (Greninja ex) (Default)"), "30th Celebration Tin (Greninja ex)")
        self.assertEqual(chosen_option("Surging Sparks Booster Box - Default Title"), "Surging Sparks Booster Box")
        # A bracket that is not one of the options stays, and so does a name without options.
        self.assertEqual(chosen_option("Sun & Moon / Sword & Shield Pack (Japanese)"),
                         "Sun & Moon / Sword & Shield Pack (Japanese)")
        self.assertEqual(chosen_option("Default Deck Box"), "Default Deck Box")
        self.assertEqual(tidy_name("30th Celebration Sylveon / Greninja Ex Box (Greninja)"), "30th Celebration Greninja Ex Box")

    def test_the_four_names_are_one_key_and_the_mini_tin_and_the_25th_are_their_own(self):
        keys = {merge_key(name, "pokemon") for name in PAGES}
        self.assertEqual(keys, {"30th celebration ex greninja tin #tin"})
        self.assertNotIn(merge_key("30th Celebration Mini Tin Greninja", "pokemon"), keys)
        self.assertNotIn(merge_key("30th Celebration Greninja ex Box", "pokemon"), keys)
        self.assertNotIn(merge_key("30th Celebration Sylveon / Greninja Ex Tin (Sylveon)", "pokemon"), keys)
        self.assertIn("25th anniversary", merge_key("Celebrations 25th Anniversary Elite Trainer Box", "pokemon"))


class MergeTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.set = make_set(self.game, name="30th Celebration", slug="30th-celebration", code="CEL")
        self.pages = [self.page(name, n) for n, name in enumerate(PAGES)]
        self.no_ex = self.page("30th Anniversary Celebration Greninja Tin", 10)
        self.mini = self.page("30th Celebration Mini Tin Greninja", 11)
        self.sylveon = self.page("30th Celebration Sylveon / Greninja Ex Tin (Sylveon)", 12)

    def page(self, name, n):
        product = make_product(self.set, name=name, slug=f"tin-{n}", product_type=Product.Type.TIN)
        make_listing(product, make_retailer(f"Shop {n}"), price="49.99")
        return product

    def test_the_hourly_merge_joins_the_four_and_claude_is_asked_about_the_one_without_ex(self):
        call_command("merge_duplicates", stdout=StringIO())
        shown = set(Product.objects.filter(is_active=True))
        self.assertEqual(len([p for p in self.pages if p in shown]), 1)
        for product in (self.no_ex, self.mini, self.sylveon):
            self.assertIn(product, shown)
        # The one without "ex" is a possible duplicate, for the owner or Claude; the mini tin and the Sylveon tin
        # are never offered.
        pairs = {(keep.pk, other.pk) for keep, others in checks.duplicates() for other in others}
        flat = {pk for pair in pairs for pk in pair}
        self.assertIn(self.no_ex.pk, flat)
        self.assertNotIn(self.mini.pk, flat)
        self.assertNotIn(self.sylveon.pk, flat)


class RippedSealTests(TestCase):
    def test_a_product_sold_with_ripped_seals_is_not_a_sealed_product(self):
        from decimal import Decimal

        from .classify import classify

        for title in ("30th Anniversary Products (RIPPED SEALS) (Greninja Bundle (Tin & EX Box))",
                      "WAVE 2 Pokemon TCG: 30th Anniversary Products (RIPPED SEALS) (Greninja Bundle (Tin & EX Box))",
                      "Surging Sparks Booster Box - Seal Broken", "Prismatic Evolutions Elite Trainer Box (cut seal)"):
            self.assertIsNone(classify(title, "", "", (), Decimal("40.00")), title)
        self.assertIsNotNone(classify("Pokemon 30th Celebration Greninja ex Tin", "", "", (), Decimal("24.99")))



class ShopNoteTests(TestCase):
    """Names read off the live site on 24 Oct 2026, when the owner asked for a check that the naming rules keep
    one product on one page: shops' notes on limits and dates, "(Normal)", Lorcana's set numbers, "Mega Evolution 2",
    Centre and Center, and the blister names each kept a second page of a product."""

    SAME = [
        ("lorcana", "Fabled Booster Box", "Fabled Booster Box (Set 9)", "Fabled (Chapter 9) Booster Box"),
        ("lorcana", "Disney Lorcana TCG Fabled Booster Pack", "Set 9 Fabled Booster Pack", "Fabled (Chapter 9) Booster Pack",
         "Card Game Fabled Booster Packs (12 Cards)"),
        ("lorcana", "Fabled Ariel & Mulan Starter Deck", "Fabled Set 9 Starter Deck Ariel & Mulan"),
        ("pokemon", "Perfect Order Elite Trainer Box", "Perfect Order Elite Trainer Box (Normal)",
         "Mega Evolution 3 Perfect Order Elite Trainer Box",
         "Elite Trainer Box Mega Evolutions Perfect Order *1per person limit*"),
        ("pokemon", "Perfect Order Booster Pack", "Perfect Order Booster Pack (Releases 27.03.26)"),
        ("pokemon", "Scarlet & Violet Surging Sparks Booster Box", "Surging Sparks Full Booster Box",
         "Scarlet & Violet 8 Surging Sparks Booster Box (1 Box)", "Surging Sparks Booster Box February Restock"),
        ("pokemon", "Mega Evolution Ascended Heroes Elite Trainer Box", "Mega Evolution 2.5 Ascended Heroes Elite Trainer Box",
         "Mega Evolution Ascended Heroes Elite Trainer Box Final Drop"),
        ("pokemon", "Ascended Heroes Elite Trainer Box (Pokémon Centre Edition)",
         "Ascended Heroes Pokémon Center Elite Trainer Box English"),
        ("pokemon", "Destined Rivals Elite Trainer Box",
         "WEBSTORE 1 PER CUSTOMER Pokémon TCG: Scarlet & Violet 10 Destined Rivals Elite Trainer Box"),
        ("pokemon", "151 Booster Bundle", "Scarlet & Violet 151 Booster Bundle *2pp limit*"),
        ("pokemon", "Perfect Order Booster Bundle Pokémon TCG 6 Booster Packs", "Perfect Order Booster Pack Bundle"),
        ("pokemon", "Phantasmal Flames Checklane Whimsicott", "Phantasmal Flames Checklane Blister (Whimsicott)"),
        ("pokemon", "Phantasmal Flames Sneasel 3 Pack Blister Pack", "Phantasmal Flames Triple Blister (Sneasel)"),
        ("pokemon", "Prismatic Evolutions Elite Trainer Box",
         "WAVE 2: Pokémon TCG: Scarlet & Violet 8.5 Prismatic Evolutions Elite Trainer Box (ETB)"),
    ]
    APART = [
        ("pokemon", "Mega Evolution 2 Pack Blister", "Phantasmal Flames 2 Pack Blister"),
        ("pokemon", "Base Set 2 Booster Box", "Base Set Booster Box"),
        ("pokemon", "Phantasmal Flames Premium Checklane Blaziken", "Phantasmal Flames Checklane Blaziken"),
        ("pokemon", "Perfect Order 3 Pack Blister", "Perfect Order 1 Pack Blister"),
        ("pokemon", "Surging Sparks Half Booster Box", "Surging Sparks Booster Box"),
        ("pokemon", "Ascended Heroes Elite Trainer Box", "Ascended Heroes Pokémon Center Elite Trainer Box"),
        ("pokemon", "30th Celebration Mini Tin", "30th Celebration Mini Tin Pikachu"),
        ("lorcana", "The First Chapter Booster Box", "Fabled Booster Box"),
    ]

    def test_one_product_in_shops_words_is_one_key_and_the_numbers_agree(self):
        from .management.commands.merge_duplicates import why_not_merge
        from types import SimpleNamespace

        for game, *names in self.SAME:
            keys = {merge_key(tidy_name(name), game) for name in names}
            self.assertEqual(len(keys), 1, (names, keys))
            first = SimpleNamespace(pk=1, name=tidy_name(names[0]), game_slug=game)
            for name in names[1:]:
                self.assertEqual(why_not_merge(first, SimpleNamespace(pk=2, name=tidy_name(name), game_slug=game)), "", name)

    def test_what_tells_two_products_apart_still_does(self):
        from .management.commands.merge_duplicates import why_not_merge
        from types import SimpleNamespace

        for game, first, second in self.APART:
            apart = merge_key(first, game) != merge_key(second, game) or why_not_merge(
                SimpleNamespace(pk=1, name=first, game_slug=game), SimpleNamespace(pk=2, name=second, game_slug=game))
            self.assertTrue(apart, (first, second))

    def test_the_page_shows_the_name_without_the_shops_note(self):
        for shown, name in [
            ("Elite Trainer Box Mega Evolutions Perfect Order", "Elite Trainer Box Mega Evolutions Perfect Order *1per person limit*"),
            ("Perfect Order Booster Pack", "Perfect Order Booster Pack (Releases 27.03.26)"),
            ("Surging Sparks Booster Box", "Surging Sparks Booster Box February Restock"),
            ("Pokémon TCG: Scarlet & Violet 10 Destined Rivals Elite Trainer Box",
             "WEBSTORE 1 PER CUSTOMER Pokémon TCG: Scarlet & Violet 10 Destined Rivals Elite Trainer Box"),
            ("Perfect Order Booster Box", "Perfect Order Booster Box (Normal)"),
            ("OP13 Carrying On His Will Booster Box", "OP13 Carrying On His Will Booster Box *1pp Limit*"),
            # What says which product it is stays.
            ("Fabled Booster Box (Set 9)", "Fabled Booster Box (Set 9)"),
            ("Limited Edition Booster Box", "Limited Edition Booster Box"),
            ("Kanto Friends Mini Tin Pikachu 2025", "Kanto Friends Mini Tin Pikachu 2025"),
        ]:
            self.assertEqual(tidy_name(name), shown)

    def test_a_sets_number_is_not_a_box_set(self):
        from .classify import find_type, plain_type

        self.assertEqual(find_type("Fabled Booster Box (Set 9)"), "booster_box")
        self.assertEqual(plain_type("Fabled Booster Box (Set 9)", "collection_box"), "booster_box")
        self.assertEqual(find_type("Pokemon TCG Box Set"), "collection_box")
        self.assertIsNone(plain_type("Collection Starter Fabled Set 9", "collection_box"))


class ShopNoteMergeTests(TestCase):
    def test_the_hourly_tidy_retypes_and_joins_the_pages(self):
        from .types import refresh_types

        lorcana = make_game("Disney Lorcana", "lorcana")
        fabled = make_set(lorcana, name="Fabled", slug="fabled", code="9")
        box = make_product(fabled, name="Fabled Booster Box", slug="fabled-box", product_type=Product.Type.BOOSTER_BOX)
        filed_wrong = make_product(fabled, name="Fabled Booster Box (Set 9)", slug="fabled-box-set-9",
                                   product_type=Product.Type.COLLECTION_BOX)
        perfect = make_set(make_game(), name="Perfect Order", slug="perfect-order", code="ME3")
        etb = make_product(perfect, name="Perfect Order Elite Trainer Box", slug="po-etb", product_type=Product.Type.ELITE_TRAINER_BOX)
        limited = make_product(perfect, name="Elite Trainer Box Mega Evolutions Perfect Order *1per person limit*",
                               slug="po-etb-limit", product_type=Product.Type.ELITE_TRAINER_BOX)
        numbered = make_product(perfect, name="Mega Evolution 3 Perfect Order Elite Trainer Box", slug="po-etb-3",
                                product_type=Product.Type.ELITE_TRAINER_BOX)
        for n, product in enumerate((box, filed_wrong, etb, limited, numbered)):
            for shop in range(2 if product in (box, etb) else 1):
                make_listing(product, make_retailer(f"Shop {n}-{shop}"), price="40.00")
        self.assertEqual(refresh_types(), 1)
        call_command("merge_duplicates", stdout=StringIO())
        shown = set(Product.objects.filter(is_active=True))
        self.assertEqual(shown, {box, etb})
        self.assertEqual(box.listings.count(), 3)
        self.assertEqual(etb.listings.count(), 4)
