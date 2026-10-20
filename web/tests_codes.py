"""Set codes in page titles and descriptions: what people type into a search engine ("eb05 booster box uk", "op18
booster box uk", "fb11"). Set pages give the code beside the set's name, product pages put it into the product's
name when the name lacks it, and pre-orders and sets still to come say so with their release date."""

import re
from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import hubs, views
from .templatetags.ripraptor import short_date
from .tests import CopyStyleTests
from .tests_hubs import description_of, title_of

SITE = " | RipRaptor"


def words():
    return hubs.Words(views.text, SimpleNamespace())


def fake_product(name, code, set_name="", type_name="Booster box"):
    product_set = SimpleNamespace(code=code, name=set_name)
    return SimpleNamespace(name=name, product_set=product_set, product_set_id=1 if code is not None else None,
                           type_name=type_name)


class ShownCodeTests(TestCase):
    def test_codes_with_letters_and_digits_are_shown_as_written(self):
        for code, shown in (("OP-18", "OP-18"), ("EB05", "EB05"), ("fb11", "FB11"), ("SV8.5", "SV8.5"),
                            ("  BT28 ", "BT28"), ("OPC-14", "OPC-14")):
            with self.subTest(code=code):
                self.assertEqual(hubs.shown_code(SimpleNamespace(code=code)), shown)

    def test_letter_only_and_number_only_codes_are_not_shown(self):
        for code in ("", "PRE", "FIN", "EOE", "14", None):
            with self.subTest(code=code):
                self.assertEqual(hubs.shown_code(SimpleNamespace(code=code)), "")


class HasCodeTests(TestCase):
    def test_spacing_hyphens_case_and_zero_padding_compare_equal(self):
        for name, code in (("One Piece OP18 Booster Box", "OP-18"), ("One Piece OP 18 Booster Box", "OP-18"),
                           ("one piece op-18 booster box", "OP-18"), ("Extra Booster EB-5 Booster Box", "EB05"),
                           ("Extra Booster EB5 Booster Box", "EB05"), ("Fusion World FB 11 Booster Pack", "FB11"),
                           ("Pokemon csv9.5C Booster Box", "CSV9.5C"), ("Booster Box [OP-18]", "op18"),
                           ("BT-028 Booster Box", "BT28"), ("Special Booster BT19-20 Box", "BT19-20"),
                           ("Theme Booster (BT19 20)", "BT19-20"), ("Booster BT1920", "BT19-20")):
            with self.subTest(name=name, code=code):
                self.assertTrue(hubs.has_code(name, code))

    def test_other_numbers_are_not_the_code(self):
        for name, code in (("One Piece OP-1 Booster Box", "OP-18"), ("Dragon Ball BT2 Booster Box", "BT28"),
                           ("Dragon Ball BT280 Booster Box", "BT28"), ("Extra Booster EB05X", "EB05"),
                           ("Brightness of Hope Booster Box", "FB11"), ("COP-18 Box", "OP-18"),
                           ("Dragon Ball BT19 Booster Box", "BT19-20"), ("Dragon Ball BT20 Box", "BT19-20")):
            with self.subTest(name=name, code=code):
                self.assertFalse(hubs.has_code(name, code))


class NameWithCodeTests(TestCase):
    def test_after_the_set_words(self):
        product = fake_product("Fusion World Brightness of Hope Booster Box", "FB11", "Brightness of Hope")
        self.assertEqual(hubs.name_with_code(product), "Fusion World Brightness of Hope FB11 Booster Box")

    def test_before_the_product_type_when_the_set_is_not_named(self):
        product = fake_product("One Piece Heroines Edition Vol.2 Booster Box", "EB05",
                               "Extra Booster: Heroines Edition Volume 2")
        self.assertEqual(hubs.name_with_code(product), "One Piece Heroines Edition Vol.2 EB05 Booster Box")

    def test_in_front_when_neither_is_named(self):
        product = fake_product("Heroines Edition Vol.2 Sleeve Set", "EB05", "Something Else", type_name="Other")
        self.assertEqual(hubs.name_with_code(product), "EB05 Heroines Edition Vol.2 Sleeve Set")

    def test_a_name_with_the_code_in_any_spelling_is_left_alone(self):
        for name in ("One Piece OP18 Booster Box", "One Piece OP 18 Booster Box", "One Piece OP-18 Booster Box"):
            with self.subTest(name=name):
                self.assertEqual(hubs.name_with_code(fake_product(name, "OP-18", "The Time Traveler")), name)

    def test_a_name_giving_another_code_is_left_alone(self):
        name = "One Piece OP-17 Booster Box"
        self.assertEqual(hubs.name_with_code(fake_product(name, "OP-18", "The Time Traveler")), name)

    def test_the_set_name_counts_only_as_whole_words(self):
        product = fake_product("Originals Collection Booster Box", "OGN1", "Origin")
        self.assertEqual(hubs.name_with_code(product), "Originals Collection OGN1 Booster Box")

    def test_no_code_or_no_set_leaves_the_name(self):
        self.assertEqual(hubs.name_with_code(fake_product("Origins Booster Box", "", "Origins")), "Origins Booster Box")
        self.assertEqual(hubs.name_with_code(fake_product("Final Fantasy Play Booster Box", "FIN", "Final Fantasy")),
                         "Final Fantasy Play Booster Box")
        self.assertEqual(hubs.name_with_code(fake_product("Loose Box", None)), "Loose Box")


class SearchNameTests(TestCase):
    def test_em_dashes_in_names_become_spaces_in_search_titles(self):
        dash = "\u2014"
        self.assertEqual(hubs.search_name(f"Ditto Premium Collection {dash} 30th Celebration"),
                         "Ditto Premium Collection 30th Celebration")
        self.assertEqual(hubs.search_name(f"Mega Evolution{dash}Phantasmal Flames"), "Mega Evolution Phantasmal Flames")
        self.assertEqual(hubs.search_name("Plain Booster Box"), "Plain Booster Box")
        product = fake_product(f"Mega Evolution {dash} Phantasmal Flames Booster Box", "", set_name="Phantasmal Flames")
        self.assertNotIn(dash, hubs.name_with_code(product))


class ProductTitleTests(TestCase):
    def test_the_full_wording_when_it_fits(self):
        self.assertEqual(hubs.product_title(words(), "OP-18 Booster Box", "£80.00", False),
                         "OP-18 Booster Box from £80.00 delivered: UK price comparison")

    def test_uk_then_delivered_then_the_bare_price_as_names_grow(self):
        cases = (
            ("EB05 Heroines Edition Booster Box", "{} UK from £95.00 delivered"),
            ("Heroines Edition Vol.2 EB05 Booster Box", "{} UK from £95.00"),
            ("Heroines Edition Vol.2 EB05 Booster Box Display", "{} from £95.00"),
        )
        for name, wording in cases:
            with self.subTest(name=name):
                title = hubs.product_title(words(), name, "£95.00", False)
                self.assertEqual(title, wording.format(name))
                self.assertLessEqual(len(title), hubs.TITLE_LIMIT)

    def test_a_name_too_long_for_any_wording_is_kept_whole(self):
        name = "One Piece Card Game Premium Booster The Best Vol.3 PRB03 Booster Box Case of Twelve"
        title = hubs.product_title(words(), name, "£300.00", False)
        self.assertEqual(title, f"{name} from £300.00")

    def test_pre_order_wording_only_for_a_pre_order(self):
        self.assertEqual(hubs.product_title(words(), "OP-18 Booster Box", "£80.00", True),
                         "OP-18 Booster Box UK pre-order from £80.00 delivered")
        self.assertEqual(hubs.product_title(words(), "One Piece The Time Traveler OP-18 Booster Box", "£80.00", True),
                         "One Piece The Time Traveler OP-18 Booster Box pre-order from £80.00")
        self.assertNotIn("pre-order", hubs.product_title(words(), "OP-18 Booster Box", "£80.00", False))


class CodePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game(name="One Piece Card Game", slug="one-piece", short_name="One Piece")
        cls.eb05 = make_set(cls.game, name="Heroines Edition Vol.2", slug="heroines-edition-vol-2", code="EB05",
                            release_date=timezone.localdate() - timedelta(days=30))
        cls.box = make_product(cls.eb05, name="One Piece Heroines Edition Vol.2 Booster Box",
                               product_type=Product.Type.BOOSTER_BOX)
        cls.shop = make_retailer("Harbour Games")
        cls.other = make_retailer("Northgate Cards")
        make_listing(cls.box, cls.shop, price="95.00")

    def page(self, obj):
        html = self.client.get(obj.get_absolute_url()).content.decode()
        return html, title_of(html), description_of(html)

    def test_product_title_and_description_carry_the_code(self):
        html, title, description = self.page(self.box)
        # 49 characters of name: only the bare price is left room, and the name is never cut.
        self.assertEqual(title, "One Piece Heroines Edition Vol.2 EB05 Booster Box from £95.00" + SITE)
        self.assertIn("EB05", description)
        # The heading and breadcrumbs keep the product's own name.
        self.assertIn("<h1", html)
        self.assertNotIn("Vol.2 EB05 Booster Box</h1>", html)

    def test_a_name_that_gives_the_code_is_not_changed(self):
        box = make_product(self.eb05, name="One Piece EB-05 Heroines Edition Vol.2 Booster Box",
                           product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.shop, price="96.00")
        title = self.page(box)[1]
        self.assertEqual(title.count("EB"), 1)
        self.assertTrue(title.startswith("One Piece EB-05 Heroines Edition Vol.2 Booster Box "))

    def test_pre_order_title_and_dated_description(self):
        out = timezone.localdate() + timedelta(days=30)
        op18 = make_set(self.game, name="The Time Traveler", slug="the-time-traveler", code="op-18", release_date=out)
        box = make_product(op18, name="One Piece The Time Traveler Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.shop, price="80.00", availability=Listing.Availability.PREORDER)
        _html, title, description = self.page(box)
        self.assertEqual(title, "One Piece The Time Traveler OP-18 Booster Box pre-order from £80.00" + SITE)
        self.assertTrue(description.startswith(
            f"One Piece The Time Traveler OP-18 Booster Box is due out on {short_date(out)}, on pre-order from "
            "£80.00 delivered at Harbour Games."
        ), description)
        self.assertNotIn("in stock", description)

    def test_the_title_says_pre_order_only_when_the_price_quoted_is_one(self):
        # A dearer pre-order beside a cheaper in-stock price: the quoted price is in stock.
        make_listing(self.box, self.other, price="120.00", availability=Listing.Availability.PREORDER)
        self.assertNotIn("pre-order", self.page(self.box)[1])
        # A cheaper pre-order while another shop has it in stock: the product is out, so the title quotes the
        # price without "pre-order" and the description says which is which.
        Listing.objects.filter(retailer=self.other).update(price="60.00")
        _html, title, description = self.page(self.box)
        self.assertNotIn("pre-order", title)
        self.assertIn("from £60.00", title)
        self.assertIn("on pre-order from £60.00", description)
        self.assertIn("in stock from £95.00", description)

    def test_a_stale_pre_order_flag_after_release_is_not_a_pre_order_title(self):
        # The set came out 30 days ago; one shop still marks its only listing as a pre-order.
        box = make_product(self.eb05, name="One Piece Heroines Edition Vol.2 Booster Pack",
                           product_type=Product.Type.BOOSTER_PACK)
        make_listing(box, self.shop, price="5.00", availability=Listing.Availability.PREORDER)
        title = self.page(box)[1]
        self.assertNotIn("pre-order", title)
        self.assertIn("from £5.00", title)

    def test_a_pre_order_with_no_release_date_keeps_the_pre_order_title(self):
        undated = make_set(self.game, name="Undated Tides", slug="undated-tides", code="OP30")
        box = make_product(undated, name="One Piece Undated Tides Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.shop, price="90.00", availability=Listing.Availability.PREORDER)
        self.assertIn("pre-order from £90.00", self.page(box)[1])

    def test_an_unpriced_product_still_to_come_gives_its_date(self):
        out = timezone.localdate() + timedelta(days=50)
        later = make_set(self.game, name="Paradise Found", slug="paradise-found", code="OP19", release_date=out)
        box = make_product(later, name="One Piece Paradise Found Booster Box", product_type=Product.Type.BOOSTER_BOX)
        _html, title, description = self.page(box)
        self.assertEqual(title, "One Piece Paradise Found OP19 Booster Box: UK prices" + SITE)
        self.assertTrue(description.startswith(
            f"One Piece Paradise Found OP19 Booster Box is due out on {short_date(out)}."))

    def test_a_sold_out_product_still_to_come_is_not_called_unlisted(self):
        out = timezone.localdate() + timedelta(days=40)
        later = make_set(self.game, name="Sunken Paths", slug="sunken-paths", code="OP20", release_date=out)
        box = make_product(later, name="One Piece Sunken Paths Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.shop, price="455.99", availability=Listing.Availability.OUT_OF_STOCK)
        _html, _title, description = self.page(box)
        self.assertTrue(description.startswith(f"One Piece Sunken Paths OP20 Booster Box is due out on {short_date(out)}."))
        self.assertNotIn("once shops list it", description)
        self.assertIn("Compare UK shop prices", description)

    def test_unknown_delivery_on_a_product_still_to_come_is_not_called_unlisted(self):
        out = timezone.localdate() + timedelta(days=40)
        later = make_set(self.game, name="Quiet Harbour", slug="quiet-harbour", code="OP21", release_date=out)
        box = make_product(later, name="One Piece Quiet Harbour Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.shop, price="90.00", availability=Listing.Availability.PREORDER, delivery_known=False)
        description = self.page(box)[2]
        self.assertNotIn("once shops list it", description)
        self.assertIn(short_date(out), description)

    def test_an_unpriced_title_that_runs_long_drops_to_the_short_form(self):
        box = make_product(self.eb05, name="One Piece Card Game Heroines Edition Vol.2 Booster Box Display",
                           product_type=Product.Type.BOOSTER_BOX)
        title = self.page(box)[1]
        self.assertEqual(title, "One Piece Card Game Heroines Edition Vol.2 EB05 Booster Box Display UK" + SITE)

    def test_set_page_in_stock(self):
        _html, title, description = self.page(self.eb05)
        self.assertEqual(title, "Heroines Edition Vol.2 (EB05) booster boxes: UK prices" + SITE)
        self.assertLessEqual(len(title) - len(SITE), hubs.TITLE_LIMIT)
        self.assertTrue(description.startswith("Heroines Edition Vol.2 (EB05): 1 sealed One Piece product"))

    def test_set_page_taking_pre_orders(self):
        out = timezone.localdate() + timedelta(days=30)
        op18 = make_set(self.game, name="The Time Traveler", slug="the-time-traveler", code="OP-18", release_date=out)
        box = make_product(op18, name="One Piece The Time Traveler Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.shop, price="80.00", availability=Listing.Availability.PREORDER)
        make_listing(box, self.other, price="82.00", availability=Listing.Availability.PREORDER)
        html, title, description = self.page(op18)
        self.assertEqual(title, "The Time Traveler (OP-18) pre-orders UK and release date" + SITE)
        self.assertIn(short_date(out), description)
        self.assertIn("on pre-order at 2 UK shops", description)
        self.assertIn(f"1 sealed product due out on {short_date(out)}, with 2 UK shops taking pre-orders now.", html)

    def test_set_page_no_shop_lists(self):
        out = timezone.localdate() + timedelta(days=60)
        fb11 = make_set(make_game(name="Dragon Ball Super Card Game Fusion World", slug="dragon-ball-super-fusion-world",
                                  short_name="Fusion World"),
                        name="Brightness of Hope", slug="brightness-of-hope", code="FB11", release_date=out)
        make_product(fb11, name="Fusion World Brightness of Hope Booster Box", product_type=Product.Type.BOOSTER_BOX)
        html, title, description = self.page(fb11)
        self.assertEqual(title, "Brightness of Hope (FB11) release date" + SITE)
        self.assertNotIn("pre-order", description.lower())
        self.assertIn("not yet on pre-order at a UK shop we check", html)
        self.assertIn("once shops list them", description)

    def test_set_page_listed_but_not_on_pre_order(self):
        out = timezone.localdate() + timedelta(days=60)
        op18 = make_set(self.game, name="The Long Watch", slug="the-long-watch", code="OP-18", release_date=out)
        pack = make_product(op18, name="One Piece The Long Watch Booster Pack", product_type=Product.Type.BOOSTER_PACK)
        make_listing(pack, self.shop, price="4.95", availability=Listing.Availability.OUT_OF_STOCK)
        html, title, description = self.page(op18)
        self.assertEqual(title, "The Long Watch (OP-18) release date" + SITE)
        self.assertIn(f"1 sealed product due out on {short_date(out)}, listed at 1 UK shop but not on pre-order yet.",
                      html)
        self.assertNotIn("not yet on pre-order at a UK shop we check", html)
        self.assertNotIn("once shops list them", description)
        self.assertIn("Compare UK shop prices", description)


class SetTitleFitTests(TestCase):
    """set_title on its own, with words from the registry."""

    def set(self, name, code):
        return SimpleNamespace(name=name, code=code)

    def title(self, product_set, keys=("meta.set.title", "meta.set.title_short"), types=("booster boxes",)):
        w = words()
        return hubs.set_title(w, hubs.set_labels(w, product_set), keys, types, game="One Piece")

    def test_the_code_goes_before_extra_types(self):
        title = hubs.set_title(words(), hubs.set_labels(words(), self.set("Royal Blood", "OP-10")),
                               ("meta.set.title", "meta.set.title_short"),
                               ("booster boxes", "booster packs", "starter decks"), game="One Piece")
        self.assertEqual(title, "Royal Blood (OP-10) booster boxes UK: cheapest prices")
        self.assertLessEqual(len(title), hubs.TITLE_LIMIT)

    def test_the_code_goes_before_the_set_name_does(self):
        # 35 characters of name: with the code even the short form runs over, without it the short form fits.
        name = "A Considerably Lengthy Set Name Abc"
        title = self.title(self.set(name, "OP-18"))
        self.assertEqual(title, f"{name} booster boxes: UK prices")
        self.assertLessEqual(len(title), hubs.TITLE_LIMIT)

    def test_a_long_name_keeps_its_short_code_when_nothing_fits(self):
        name = "Scarlet and Violet Paldean Fates Special Expansion Collection"
        title = self.title(self.set(name, "SV4.5"))
        self.assertEqual(title, f"{name} (SV4.5) booster boxes: UK prices")

    def test_a_set_name_with_the_code_in_it_is_not_given_it_twice(self):
        self.assertEqual(self.title(self.set("OP-18 The Time Traveler", "OP18")),
                         "OP-18 The Time Traveler booster boxes UK: cheapest prices")

    def test_titles_fit_for_real_world_shaped_names(self):
        for name, code in (("Nauctis: The Sunken Realm", "NAC"), ("Heroines Edition Vol.2", "EB05"),
                           ("Brightness of Hope", "FB11"), ("The Time Traveler", "OP-18"),
                           ("Ultimate Squad", "BT28")):
            with self.subTest(name=name):
                for keys in (("meta.set.title", "meta.set.title_short"),
                             ("meta.set.title_upcoming", "meta.set.title_upcoming_short",
                              "meta.set.title_upcoming_shortest"),
                             ("meta.set.title_dated",), ("meta.set.title_plain", "meta.set.title_plain_short")):
                    title = self.title(self.set(name, code), keys)
                    self.assertLessEqual(len(title), hubs.TITLE_LIMIT, title)
                    if hubs.shown_code(self.set(name, code)):
                        self.assertIn(f"({code})", title)


class TitleUniquenessTests(TestCase):
    """Similar products in one set keep different titles, and the codes and wording keep the house style."""

    @classmethod
    def setUpTestData(cls):
        game = make_game(name="One Piece Card Game", slug="one-piece", short_name="One Piece")
        cls.op18 = make_set(game, name="The Time Traveler", slug="the-time-traveler", code="OP-18",
                            release_date=timezone.localdate() + timedelta(days=20))
        shop = make_retailer("Harbour Games")
        cls.products = []
        for name, kind, price, availability in (
            ("One Piece The Time Traveler Booster Box", Product.Type.BOOSTER_BOX, "80.00", "preorder"),
            ("One Piece The Time Traveler Booster Pack", Product.Type.BOOSTER_PACK, "4.00", "preorder"),
            ("One Piece The Time Traveler Booster Box Case", Product.Type.BOOSTER_BOX, "900.00", "preorder"),
            ("One Piece OP18 The Time Traveler Booster Box (Japanese)", Product.Type.BOOSTER_BOX, "70.00", "in_stock"),
            ("One Piece OP-18 The Time Traveler Booster Box", Product.Type.BOOSTER_BOX, "81.00", "in_stock"),
            ("One Piece The Time Traveler Sleeves", Product.Type.OTHER, "8.00", "in_stock"),
            ("One Piece The Time Traveler Booster Box (Chinese)", Product.Type.BOOSTER_BOX, "60.00", ""),
        ):
            product = make_product(cls.op18, name=name, product_type=kind)
            if availability:
                make_listing(product, shop, price=price, availability=availability)
            cls.products.append(product)

    def test_titles_are_unique_and_keep_the_code_once(self):
        titles = []
        for product in self.products:
            html = self.client.get(product.get_absolute_url()).content.decode()
            title = title_of(html)[: -len(SITE)]
            titles.append(title)
            with self.subTest(product=product.name):
                codes = re.findall(r"OP-?18", title)
                self.assertEqual(len(codes), 1, title)
                self.assertNotIn("!", title + description_of(html))
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, (title + description_of(html)).lower())
        self.assertEqual(len(titles), len(set(titles)), titles)

    def test_registry_wording_keeps_the_house_style(self):
        from content.registry import REGISTRY

        for key, entry in REGISTRY.items():
            if key.startswith(("meta.product.title", "meta.product.description", "meta.set.", "browse.hub.")):
                with self.subTest(key=key):
                    self.assertNotIn("—", entry.default)
                    self.assertNotIn("!", entry.default)
                    for phrase in CopyStyleTests.BANNED:
                        self.assertNotIn(phrase, entry.default.lower())
