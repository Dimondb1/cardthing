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

