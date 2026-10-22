"""In stock instead: the closest products shops have in stock, on a product page or released set page nobody can
sell now (catalogue/picks.instead). The owner: "People will be looking for that if we don't have it. Do we have
the next best thing ready to show them?"."""

from datetime import date

from django.core.cache import cache
from django.test import TestCase

from catalogue import picks
from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

OUT = Listing.Availability.OUT_OF_STOCK
BOX, ETB = Product.Type.BOOSTER_BOX, Product.Type.ELITE_TRAINER_BOX


class InsteadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.shop = make_retailer("Harbour Games")
        self.celebration = make_set(self.game, name="30th Celebration", slug="30th-celebration", code="CEL",
                                    release_date=date(2026, 2, 27))
        self.older = make_set(self.game, name="Surging Sparks", slug="surging-sparks", code="SSP",
                              release_date=date(2024, 11, 8))
        self.newer = make_set(self.game, name="Ascended Heroes", slug="ascended-heroes", code="ASC",
                              release_date=date(2026, 5, 22))
        self.wanted = self.item(self.celebration, "30th Celebration Booster Box", BOX, OUT)

    def item(self, product_set, name, kind, availability=Listing.Availability.IN_STOCK, language=""):
        product = make_product(product_set, name=name, slug=name.lower().replace(" ", "-"), product_type=kind)
        if language:
            Product.objects.filter(pk=product.pk).update(language=language)
            product.language = language
        make_listing(product, self.shop, price="100.00", availability=availability)
        return product

    def test_the_same_set_first_then_the_newest_of_the_kind_in_the_same_language(self):
        etb = self.item(self.celebration, "30th Celebration Elite Trainer Box", ETB)
        old_box = self.item(self.older, "Surging Sparks Booster Box", BOX)
        new_box = self.item(self.newer, "Ascended Heroes Booster Box", BOX)
        japanese = self.item(self.newer, "Ascended Heroes Japanese Booster Box", BOX, language="ja")
        self.item(self.newer, "Ascended Heroes Tin", Product.Type.TIN)   # neither the set nor the kind
        rows = picks.instead([self.wanted])
        self.assertEqual([product for product, _, _ in rows], [etb, new_box, old_box, japanese])

    def test_never_more_than_four_and_never_a_sold_out_one(self):
        for n in range(6):
            self.item(self.older, f"Surging Sparks Booster Box {n}", BOX)
        self.item(self.newer, "Ascended Heroes Booster Box", BOX, OUT)
        rows = picks.instead([self.wanted])
        self.assertEqual(len(rows), picks.INSTEAD_MAX)
        self.assertTrue(all(product.in_stock_count for product, _, _ in rows))

    def test_a_sold_out_product_page_shows_the_row_right_under_the_buy_box(self):
        self.item(self.newer, "Ascended Heroes Booster Box", BOX)
        html = self.client.get(self.wanted.get_absolute_url()).content.decode()
        self.assertIn('<h2 id="instead-title" class="section-head__title">In stock instead</h2>', html)
        self.assertIn("Nobody has this in stock. These are the closest products that are.", html)
        self.assertIn('href="/products/ascended-heroes-booster-box/" data-compare="instead"', html)
        self.assertLess(html.index('class="buybox'), html.index("instead-title"))
        # A product a shop can sell has no row.
        html = self.client.get("/products/ascended-heroes-booster-box/").content.decode()
        self.assertNotIn("instead-title", html)

    def test_a_sold_out_set_page_shows_the_row_and_one_with_stock_or_pre_orders_does_not(self):
        self.item(self.newer, "Ascended Heroes Booster Box", BOX)
        html = self.client.get(self.celebration.get_absolute_url()).content.decode()
        self.assertIn("Nothing from this set is in stock. These are the closest products that are.", html)
        self.assertIn('href="/products/ascended-heroes-booster-box/" data-compare="instead"', html)
        self.assertNotIn("instead-title", self.client.get(self.newer.get_absolute_url()).content.decode())
        cache.clear()
        self.item(self.celebration, "30th Celebration Tin", Product.Type.TIN,
                  Listing.Availability.PREORDER)
        self.assertNotIn("instead-title", self.client.get(self.celebration.get_absolute_url()).content.decode())

    def test_a_tap_in_the_row_is_counted_as_its_own_place(self):
        from catalogue.clicks import COMPARE_PLACES

        self.assertIn("instead", COMPARE_PLACES)
