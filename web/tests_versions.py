"""Links between the language versions of one product, and relatives for a product filed under no set
(web/versions.py). The audit of 17 Nov 2026 found the Japanese EB-05 booster box and pack with shops and prices
and almost no links pointing at them: no set, so no "More from this set", and no link to or from the English
pages. The owner approved both ("Approve both")."""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalogue.models import Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import versions

BOX, PACK = Product.Type.BOOSTER_BOX, Product.Type.BOOSTER_PACK


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game(name="One Piece Card Game", slug="one-piece", short_name="One Piece")
        cls.eb05 = make_set(cls.game, name="Heroines Edition Vol.2", slug="heroines-edition-vol-2", code="EB-05")
        cls.eb03 = make_set(cls.game, name="Heroines Edition", slug="heroines-edition", code="EB-03")
        cls.shop = make_retailer("Harbour Games")
        cls.english_box = make_product(cls.eb05, name="EB05 Heroines Edition Vol.2 Booster Box", product_type=BOX)
        cls.english_pack = make_product(cls.eb05, name="Heroines Edition Vol.2 Booster Pack", product_type=PACK)
        # The Japanese pages, filed under no set: the site has the set in English only.
        cls.japanese_box = cls.loose("EB 05 Heroines Edition Vol.2 Japanese Booster Box", BOX)
        cls.japanese_pack = cls.loose("EB-05 Heroines Edition Vol.2 Japanese Booster Pack", PACK)
        for product in (cls.english_box, cls.english_pack, cls.japanese_box, cls.japanese_pack):
            make_listing(product, cls.shop, price="50.00", url=f"https://harbour.example/{product.slug}")

    @classmethod
    def loose(cls, name, kind):
        return Product.objects.create(game=cls.game, name=name, product_type=kind)

    def fresh(self, product):
        return Product.objects.select_related("game", "product_set").get(pk=product.pk)

    def versions_of(self, product):
        return [(version["language"], version["url"]) for version in versions.language_versions(self.fresh(product))]


class LanguageVersionTests(Base):
    def test_the_english_and_japanese_pages_of_one_product_point_at_each_other(self):
        self.assertEqual(self.japanese_box.language, "ja")
        self.assertEqual(self.versions_of(self.english_box), [("Japanese", self.japanese_box.get_absolute_url())])
        self.assertEqual(self.versions_of(self.japanese_box), [("English", self.english_box.get_absolute_url())])
        # The pack's name gives no code: its set's does.
        self.assertEqual(self.versions_of(self.english_pack), [("Japanese", self.japanese_pack.get_absolute_url())])
        self.assertEqual(self.versions_of(self.japanese_pack), [("English", self.english_pack.get_absolute_url())])

    def test_only_the_same_kind_of_product_from_the_same_set_is_a_version(self):
        # A box is never a pack's version, nor a half box or a case the box's, nor another set's box.
        self.loose("EB-05 Heroines Edition Vol.2 Japanese Half Booster Box", BOX)
        self.loose("EB-05 Heroines Edition Vol.2 Japanese Booster Box Case", BOX)
        self.loose("EB-03 Heroines Edition Japanese Booster Box", BOX)
        self.loose("EB-05 Heroines Edition Vol.2 Japanese Double Pack Set", PACK)
        self.assertEqual(self.versions_of(self.english_box), [("Japanese", self.japanese_box.get_absolute_url())])
        # A second English page is a duplicate for the merge, not a version.
        make_product(self.eb05, name="One Piece Card Game Extra Booster Box EB-05", product_type=BOX)
        self.assertEqual(self.versions_of(self.japanese_box), [("English", self.english_box.get_absolute_url())])

    def test_each_language_is_linked_once_and_a_hidden_page_never(self):
        korean = self.loose("EB-05 Heroines Edition Vol.2 Korean Booster Box", BOX)
        self.loose("Heroines Edition Vol.2 EB05 Booster Box Japanese", BOX)
        self.assertEqual(self.versions_of(self.english_box), [("Japanese", self.japanese_box.get_absolute_url()),
                                                               ("Korean", korean.get_absolute_url())])
        Product.objects.filter(pk=korean.pk).update(is_active=False)
        self.assertEqual(self.versions_of(self.english_box), [("Japanese", self.japanese_box.get_absolute_url())])

    def test_a_product_with_no_set_code_has_no_versions_and_costs_no_query(self):
        pokemon = make_game(name="Pokémon", slug="pokemon")
        rivals = make_set(pokemon, name="Destined Rivals", slug="destined-rivals", code="DRI")
        box = make_product(rivals, name="Destined Rivals Booster Box", product_type=BOX)
        Product.objects.create(game=pokemon, name="Destined Rivals Japanese Booster Box", product_type=BOX)
        box, english_box = self.fresh(box), self.fresh(self.english_box)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(versions.language_versions(box), [])
        self.assertEqual(len(queries), 0)
        with CaptureQueriesContext(connection) as queries:
            versions.language_versions(english_box)
        self.assertEqual(len(queries), 1)

    def test_the_page_says_also_in_under_the_name(self):
        html = self.client.get(self.english_box.get_absolute_url()).content.decode()
        self.assertIn(f'<p class="product-versions">Also in <a href="{self.japanese_box.get_absolute_url()}">Japanese'
                      '<span class="visually-hidden">: EB 05 Heroines Edition Vol.2 Japanese Booster Box</span></a></p>', html)
        html = self.client.get(self.japanese_box.get_absolute_url()).content.decode()
        self.assertIn(f'Also in <a href="{self.english_box.get_absolute_url()}">English', html)
        # A page with no version says nothing.
        lone = make_product(self.eb03, name="Heroines Edition Booster Box", product_type=BOX)
        self.assertNotIn("product-versions", self.client.get(lone.get_absolute_url()).content.decode())


class RelativeTests(Base):
    def test_a_product_filed_under_no_set_shows_its_nearest_relatives(self):
        other_set = self.loose("OP-13 Carrying On His Will Japanese Booster Box", BOX)
        make_listing(other_set, self.shop, price="70.00", url="https://harbour.example/op13")
        found = versions.relatives(self.fresh(self.japanese_box))
        # Its own set first, its own language and kind ahead; another set's box shares nothing that names it.
        self.assertEqual([product.pk for product in found],
                         [self.japanese_pack.pk, self.english_box.pk, self.english_pack.pk])
        html = self.client.get(self.japanese_box.get_absolute_url()).content.decode()
        self.assertIn("Related One Piece products", html)
        self.assertIn(self.japanese_pack.get_absolute_url(), html)
        self.assertNotIn(other_set.get_absolute_url(), html)

    def test_a_product_in_a_set_keeps_more_from_its_set(self):
        html = self.client.get(self.english_box.get_absolute_url()).content.decode()
        self.assertIn("More from Heroines Edition Vol.2", html)
        self.assertNotIn("Related One Piece products", html)

    def test_relatives_cost_two_queries_and_none_when_the_name_has_nothing_to_go_by(self):
        japanese_box, bare = self.fresh(self.japanese_box), self.fresh(self.loose("Booster Box", BOX))
        with CaptureQueriesContext(connection) as queries:
            versions.relatives(japanese_box)
        self.assertEqual(len(queries), 2)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(versions.relatives(bare), [])
        self.assertEqual(len(queries), 0)

    def test_a_word_inside_another_word_is_no_relative(self):
        self.loose("Heroic Legends Japanese Booster Box", BOX)
        found = versions.relatives(self.fresh(self.japanese_box))
        self.assertNotIn("Heroic Legends Japanese Booster Box", [product.name for product in found])
