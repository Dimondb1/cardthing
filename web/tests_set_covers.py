"""The product that pictures a set (web.compact.cover_rank, set_rows). The owner, 28 Nov 2026, of a shop's photo of a
shipping carton standing for 30th Celebration under Sets people want now: "I would like images to be better and
bigger and not crap"."""

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from catalogue import pictures
from catalogue.models import Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import compact

T = Product.Type
PICTURE = "https://cdn.shopify.com/s/files/1/0001/files/{}.png"


class CartonNameTests(SimpleTestCase):
    def test_a_case_a_carton_or_a_counter_display_is_named(self):
        for name in ("30th Celebration Booster Box Case", "Prismatic Evolutions ETB Case (10 Boxes)",
                     "Sealed Case of 6 Elite Trainer Boxes", "Journey Together Master Carton",
                     "30th Celebration 2-Pack Blister Display", "Checklane Blister Display (16)",
                     "Sleeved Booster Display", "Booster Pack Display of 12", "Surging Sparks Pallet",
                     "Gravity Feed Display"):
            self.assertTrue(pictures.carton_named(name), name)

    def test_a_booster_display_is_a_booster_box_and_other_names_are_left(self):
        for name in ("Booster Display", "One Piece OP-10 Booster Display Box", "Prismatic Evolutions Elite Trainer Box",
                     "Showcase Premium Collection", "Final Fantasy Collector Booster Box", "Staircase Deck"):
            self.assertFalse(pictures.carton_named(name), name)


class CoverTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.shops = [make_retailer(f"Shop {n}") for n in range(4)]
        self.n = 0

    def product(self, name, kind, shops=1, picture=True, upload=False):
        self.n += 1
        product = make_product(self.set, name=name, slug=f"p{self.n}", product_type=kind)
        Product.objects.filter(pk=product.pk).update(
            image_url=PICTURE.format(self.n) if picture and not upload else "",
            image=f"products/p{self.n}.png" if upload else "")
        for shop in self.shops[:shops]:
            make_listing(product, shop)
        return product

    def cover(self):
        rows, = [compact.set_rows()]
        return Product.objects.get(pk=rows[0]["cover"])

    def test_a_case_never_pictures_a_set_that_has_another_picture(self):
        self.product("Prismatic Evolutions Booster Box Case", T.BOOSTER_BOX, shops=4)
        box = self.product("Prismatic Evolutions Elite Trainer Box", T.ELITE_TRAINER_BOX)
        self.assertEqual(self.cover(), box)

    def test_a_case_pictures_it_when_nothing_else_has_a_picture(self):
        case = self.product("Prismatic Evolutions Booster Box Case", T.BOOSTER_BOX)
        self.product("Prismatic Evolutions Elite Trainer Box", T.ELITE_TRAINER_BOX, picture=False)
        self.assertEqual(self.cover(), case)

    def test_the_owners_own_upload_comes_first(self):
        self.product("Prismatic Evolutions Booster Box", T.BOOSTER_BOX, shops=4)
        tin = self.product("Prismatic Evolutions Tin", T.TIN, upload=True)
        self.assertEqual(self.cover(), tin)

    def test_a_product_many_shops_list_goes_before_one_shops_own(self):
        self.product("Prismatic Evolutions Booster Box", T.BOOSTER_BOX, shops=1)
        etb = self.product("Prismatic Evolutions Elite Trainer Box", T.ELITE_TRAINER_BOX, shops=compact.COVER_SHOPS)
        self.assertEqual(self.cover(), etb)

    def test_among_well_listed_products_a_booster_box_still_comes_first(self):
        self.product("Prismatic Evolutions Elite Trainer Box", T.ELITE_TRAINER_BOX, shops=4)
        box = self.product("Prismatic Evolutions Booster Box", T.BOOSTER_BOX, shops=3)
        self.assertEqual(self.cover(), box)
        # And among the less listed, as before: the kind first, then the most shops.
        self.assertLess(compact.cover_rank(9, "A Booster Box", T.BOOSTER_BOX, 1, "", "x"),
                        compact.cover_rank(1, "A Tin", T.TIN, 2, "", "x"))

    def test_a_product_without_a_picture_is_the_last_resort(self):
        self.product("Prismatic Evolutions Booster Box", T.BOOSTER_BOX, shops=4, picture=False)
        deck = self.product("Prismatic Evolutions Deck", T.DECK)
        self.assertEqual(self.cover(), deck)

    def test_a_types_tile_on_the_sets_page_passes_over_a_case_too(self):
        from web import feed

        case = self.product("Prismatic Evolutions Booster Box Case", T.BOOSTER_BOX)
        self.assertEqual(feed.set_tile_rows(self.set), [(T.BOOSTER_BOX, case.pk, 1)])   # alone: it pictures the tile
        bare = self.product("Prismatic Evolutions Booster Box (No Picture)", T.BOOSTER_BOX, picture=False)
        self.assertEqual(feed.set_tile_rows(self.set), [(T.BOOSTER_BOX, case.pk, 2)])
        box = self.product("Prismatic Evolutions Booster Box", T.BOOSTER_BOX)
        self.assertEqual(feed.set_tile_rows(self.set), [(T.BOOSTER_BOX, box.pk, 3)])
        self.assertNotEqual(bare.pk, box.pk)
        with CaptureQueriesContext(connection) as asked:
            feed.set_tile_rows(self.set)
        self.assertEqual(len(asked), 1)

    def test_still_two_queries_however_many_products(self):
        for n in range(6):
            self.product(f"Prismatic Evolutions Box {n}", T.BOOSTER_BOX, shops=2)
        with CaptureQueriesContext(connection) as asked:
            compact.set_rows()
        self.assertEqual(len(asked), 2)
