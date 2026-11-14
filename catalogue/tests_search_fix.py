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
        # A plural is no longer a word to fix: it matches as it is (apply_search tries its singular too).
        self.assertEqual(corrected("delta reign boosters", everything), "")
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


class SpokenTests(TestCase):
    """Searches as people say them (the owner, 14 Nov 2026): plurals match, "cheap" is the order and "under £5" a
    limit, "what is the" is nothing, a name such as "pikachu" is kept, and a search that still finds nothing tries
    fewer words before it ends on No luck."""

    @classmethod
    def setUpTestData(cls):
        game = make_game()
        reign = make_set(game, name="Mega Evolution—Delta Reign", slug="delta-reign", code="ME03")
        bolt = make_set(game, name="Scarlet & Violet—Black Bolt", slug="black-bolt", code="BLK")
        other = make_set(game, name="Surging Sparks", slug="surging-sparks", code="SSP")
        cheap, dear = make_retailer("Harbour Games"), make_retailer("Kestrel Cards")
        cls.pack = make_product(reign, name="Mega Evolution Delta Reign Booster Pack", slug="dr-pack",
                                product_type="booster_pack")
        cls.box = make_product(reign, name="Mega Evolution Delta Reign Booster Box", slug="dr-box",
                               product_type="booster_box")
        cls.etb = make_product(bolt, name="Black Bolt Elite Trainer Box", slug="bb-etb")
        cls.sparks = make_product(other, name="Surging Sparks Booster Pack", slug="ss-pack", product_type="booster_pack")
        make_listing(cls.pack, cheap, price="5.50")
        make_listing(cls.box, dear, price="120.00")
        make_listing(cls.etb, cheap, price="45.00")
        make_listing(cls.sparks, cheap, price="4.25")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def page(self, words, **extra):
        return self.client.get(SEARCH, {"q": words, **extra}).content.decode()

    def test_cheap_delta_booster_packs_is_delta_reigns_pack_cheapest_first(self):
        html = self.page("cheap delta booster packs")
        self.assertIn("Mega Evolution Delta Reign Booster Pack", html)
        self.assertNotIn("Mega Evolution Delta Reign Booster Box", html)   # a box is not a pack
        self.assertIn('<h1 class="browse-head__title">Results for ‘delta booster packs’</h1>', html)
        self.assertIn('<p class="browse-head__fix">Cheapest first.</p>', html)
        self.assertIn('<option value="price" selected>', html)

    def test_plurals_match_the_singular_product(self):
        for words in ("delta reign booster packs", "black bolt etbs", "delta reign boxes"):
            with self.subTest(words=words):
                self.assertNotIn("No luck", self.page(words))
        self.assertIn("Black Bolt Elite Trainer Box", self.page("black bolt etb"))

    def test_under_a_price_is_a_limit(self):
        html = self.page("booster packs under £5")
        self.assertIn("Surging Sparks Booster Pack", html)
        self.assertNotIn("Mega Evolution Delta Reign Booster Pack", html)   # £5.50
        self.assertIn("Under £5.", html)

    def test_the_words_that_only_make_the_sentence_are_ignored(self):
        html = self.page("what is the price of delta reign booster pack for me please")
        self.assertIn("Mega Evolution Delta Reign Booster Pack", html)

    def test_a_name_is_kept_never_dropped_for_something_else(self):
        html = self.page("what is the cheapest pikachu box")
        self.assertNotIn("Booster Box</a>", html)   # no Pikachu product: nothing, not some other box
        pikachu = make_product(self.box.product_set, name="Pikachu ex Premium Collection Box", slug="pika-box",
                               product_type="collection_box")
        make_listing(pikachu, make_retailer("Moor Toys"), price="30.00")
        cache.clear()
        self.assertIn("Pikachu ex Premium Collection Box", self.page("what is the cheapest pikachu box"))

    def test_nothing_found_tries_fewer_words_before_no_luck(self):
        html = self.page("delta reign booster pack sealed english")
        self.assertIn("Mega Evolution Delta Reign Booster Pack", html)
        self.assertIn("Showing results for ‘delta reign booster pack’.", html)

    def test_results_as_you_type_read_it_the_same_way(self):
        rows = self.client.get(reverse("web:search_api"), {"q": "cheapest booster packs"}).json()["results"]
        names = [row["name"] for row in rows if row["state"] != "set"]
        self.assertEqual(names[:2], ["Surging Sparks Booster Pack", "Mega Evolution Delta Reign Booster Pack"])

    def test_a_misheard_word_and_extra_words_together_still_find_it(self):
        html = self.page("delta rain booster pack english sealed")
        self.assertIn("Mega Evolution Delta Reign Booster Pack", html)
        self.assertIn("Showing results for ‘delta reign booster pack’.", html)

    def test_nothing_within_the_limit_shows_the_cheapest_and_says_so(self):
        html = self.page("delta reign booster pack under £5")
        self.assertIn("Mega Evolution Delta Reign Booster Pack", html)   # £5.50
        self.assertIn("Nothing under £5 right now, so these are the cheapest.", html)
        self.assertIn('<option value="price" selected>', html)
