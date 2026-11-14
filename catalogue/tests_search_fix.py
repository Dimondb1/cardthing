"""A search that finds nothing tries the words it most likely meant (catalogue.search.corrected). The owner, 14 Nov
2026: said to the phone, "delta reign" came back as "delta rain" and found nothing, "people will give up after that".
The page says what it searched for instead, with a link to exactly what was typed."""

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from catalogue.models import Product
from catalogue.search import corrected, sounds
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

SEARCH = reverse("web:search")


class FixCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        game = make_game()
        reign = make_set(game, name="Mega Evolution—Delta Reign", slug="delta-reign", code="ME03")
        flames = make_set(game, name="Obsidian Flames", slug="obsidian-flames", code="OBF")
        shop = make_retailer("Harbour Games")
        cls.box = make_product(reign, name="Mega Evolution Delta Reign Booster Box", slug="dr-box",
                               product_type="booster_box")
        cls.etb = make_product(flames, name="Obsidian Flames Elite Trainer Box", slug="obf-etb")
        make_listing(cls.box, shop, price="120.00")
        make_listing(cls.etb, shop, price="45.00")

    def setUp(self):
        cache.clear()   # the words of the products on sale are kept for a few minutes
        self.addCleanup(cache.clear)


class SoundsTests(FixCase):
    def test_words_that_sound_alike_share_a_key(self):
        for heard, meant in (("rain", "reign"), ("lorkana", "lorcana"), ("prismatik", "prismatic"),
                             ("obsidan", "obsidian"), ("paldeon", "paldean")):
            with self.subTest(heard=heard):
                self.assertEqual(sounds(heard)[0], sounds(meant)[0])
        self.assertEqual(sounds("rain")[1], sounds("reign")[1])   # the same vowel sound too
        self.assertEqual(sounds("sv08"), ("", ""))   # a code is never guessed at

    def test_what_was_meant(self):
        everything = Product.objects.for_lists()
        self.assertEqual(corrected("delta rain", everything), "delta reign")
        self.assertEqual(corrected("Delta Rain booster box", everything), "delta reign booster box")
        self.assertEqual(corrected("obsidan flames etb", everything), "obsidian flames etb")
        self.assertEqual(corrected("delta reign boosters", everything), "delta reign booster")
        # Nothing to swap: every word is a product's, a code, or too short to tell while it is typed.
        self.assertEqual(corrected("delta reign", everything), "")
        self.assertEqual(corrected("sv99", everything), "")
        self.assertEqual(corrected("delta rai", everything), "")
        # A swap that finds nothing with the other words is not offered.
        self.assertEqual(corrected("obsidian rain", everything), "")


class PageTests(FixCase):
    def test_delta_rain_shows_delta_reign_and_says_so(self):
        response = self.client.get(SEARCH, {"q": "delta rain"})
        html = response.content.decode()
        self.assertContains(response, "Mega Evolution Delta Reign Booster Box")
        self.assertIn('<h1 class="browse-head__title">Results for ‘delta reign’</h1>', html)
        self.assertIn('<p class="browse-head__fix" role="status">Showing results for ‘delta reign’. '
                      '<a href="?q=delta+rain&amp;exact=1">Search for ‘delta rain’ instead</a></p>', html)

    def test_the_link_searches_exactly_what_was_typed(self):
        response = self.client.get(SEARCH, {"q": "delta rain", "exact": "1"})
        self.assertNotContains(response, "browse-head__fix")
        self.assertNotContains(response, "Mega Evolution Delta Reign Booster Box")

    def test_a_search_that_finds_something_is_left_alone(self):
        response = self.client.get(SEARCH, {"q": "delta reign"})
        self.assertContains(response, "Mega Evolution Delta Reign Booster Box")
        self.assertNotContains(response, "browse-head__fix")

    def test_results_as_you_type_find_it_too(self):
        names = [row["name"] for row in self.client.get(reverse("web:search_api"), {"q": "delta rain"}).json()["results"]]
        self.assertIn("Mega Evolution Delta Reign Booster Box", names)
