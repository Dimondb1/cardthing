"""A game's page of one kind of product (web/type_pages.py): /games/<game>/<kind>/. The owner's Search Console, 25 Nov
2026: most searches Google showed the site for were "<game> booster boxes" or "<game> booster packs", answered by the
game's own page at about position 70, because no page was about the kind searched for."""

import json
import re

from django.core.cache import cache
from django.test import TestCase

from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import type_pages


class TypePageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.game = make_game()
        self.set = make_set(self.game)
        self.harbour, self.north = make_retailer("Harbour Games"), make_retailer("Northgate Cards")
        self.boxes = []
        for n, price in enumerate(("150.00", "139.00", "160.00")):
            box = make_product(self.set, name=f"Prismatic Evolutions Booster Box {n}", slug=f"pe-box-{n}",
                               product_type=Product.Type.BOOSTER_BOX)
            make_listing(box, self.harbour, price=price)
            self.boxes.append(box)
        make_listing(self.boxes[1], self.north, price="141.00")
        self.etb = make_product(self.set, slug="pe-etb")
        make_listing(self.etb, self.harbour, price="55.00")
        self.url = "/games/pokemon/booster-boxes/"

    def page(self, url=None, **params):
        response = self.client.get(url or self.url, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_page_is_about_the_kind_searched_for(self):
        html = self.page()
        self.assertIn("<title>Cheapest Pokémon booster boxes UK: compare prices | RipRaptor</title>", html)
        self.assertIn('<link rel="canonical" href="http://testserver/games/pokemon/booster-boxes/">', html)
        self.assertIn('<h1 class="browse-head__title">Pokémon booster boxes</h1>', html)
        description = re.search(r'<meta name="description" content="([^"]+)"', html).group(1)
        self.assertTrue(description.startswith("3 Pokémon booster boxes in stock at 2 UK shops, from £139.00 delivered."),
                        description)
        # Only booster boxes, cheapest first in the list; the ETB is not on it.
        self.assertIn(self.boxes[1].get_absolute_url(), html)
        self.assertNotIn(self.etb.get_absolute_url(), html.split("<main", 1)[1].split("hub-about", 1)[0])
        # The line of figures links the cheapest in stock, and the About names what it compares.
        self.assertIn(f'<a href="{self.boxes[1].get_absolute_url()}">', html)
        self.assertIn("RipRaptor compares UK shops&#x27; prices for Pokémon booster boxes, with delivery included.", html)
        # Breadcrumbs: Games, the game, then this page.
        self.assertIn(f'<li><a href="{self.game.get_absolute_url()}">Pokémon</a></li>', html)
        blocks = []
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            found = json.loads(block)
            blocks += found if isinstance(found, list) else [found]
        crumbs = next(block for block in blocks if block.get("@type") == "BreadcrumbList")
        self.assertEqual([item["name"] for item in crumbs["itemListElement"]], ["Pokémon", "Pokémon booster boxes"])
        self.assertTrue(any(block.get("@type") == "CollectionPage" for block in blocks))

    def test_the_games_list_of_one_kind_names_the_kinds_page_as_its_canonical_address(self):
        html = self.page(self.game.get_absolute_url(), type="booster_box")
        self.assertIn('<link rel="canonical" href="http://testserver/games/pokemon/booster-boxes/">', html)
        # The game's own page, and a set chosen with the kind, keep theirs.
        self.assertIn('<link rel="canonical" href="http://testserver/games/pokemon/">', self.page(self.game.get_absolute_url()))
        self.assertIn('<link rel="canonical" href="http://testserver/games/pokemon/">',
                      self.page(self.game.get_absolute_url(), type="booster_box", set=self.set.slug))

    def test_addresses_are_the_kind_in_the_games_own_words(self):
        self.assertEqual(type_pages.slug_for("pokemon", Product.Type.ELITE_TRAINER_BOX), "elite-trainer-boxes")
        self.assertEqual(type_pages.slug_for("magic-the-gathering", Product.Type.DECK), "commander-decks")
        self.assertEqual(type_pages.slug_for("cardfight-vanguard", Product.Type.BOOSTER_PACK), "booster-packs")
        self.assertEqual(type_pages.slug_for("pokemon", Product.Type.OTHER), "")
        self.assertEqual(type_pages.code_for("pokemon", "elite-trainer-boxes"), Product.Type.ELITE_TRAINER_BOX)
        self.assertIsNone(type_pages.code_for("pokemon", "nothing-like-it"))
        self.assertIn("<h1 class=\"browse-head__title\">Pokémon ETBs</h1>", self.page("/games/pokemon/elite-trainer-boxes/"))

    def test_a_kind_with_nothing_or_an_unknown_address_is_not_found_and_a_set_of_the_same_name_wins(self):
        self.assertEqual(self.client.get("/games/pokemon/tins/").status_code, 404)
        self.assertEqual(self.client.get("/games/pokemon/nothing-like-it/").status_code, 404)
        self.assertEqual(self.client.get("/games/no-such-game/booster-boxes/").status_code, 404)
        clash = make_set(self.game, name="Booster Boxes", slug="booster-boxes")
        make_product(clash, name="Booster Boxes Collection", slug="clash-collection",
                     product_type=Product.Type.COLLECTION_BOX)
        self.assertIn("<h1 class=\"browse-head__title\">Booster Boxes</h1>", self.page())

    def test_nothing_in_stock_says_no_cheapest(self):
        Listing.objects.filter(product__product_type=Product.Type.BOOSTER_BOX).update(
            availability=Listing.Availability.OUT_OF_STOCK)
        html = self.page()
        self.assertIn("<title>Pokémon booster boxes: UK prices and pre-orders | RipRaptor</title>", html)
        self.assertNotIn("Cheapest Pokémon booster boxes", html)

    def test_a_long_title_takes_the_short_form(self):
        long_game = make_game(name="Cardfight!! Vanguard overDress Special Edition", slug="cardfight-vanguard")
        product_set = make_set(long_game, name="Long Set", slug="long-set")
        box = make_product(product_set, name="Long Set Booster Box", slug="long-box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.harbour, price="80.00")
        html = self.page("/games/cardfight-vanguard/booster-boxes/")
        title = re.search(r"<title>([^<]+)</title>", html).group(1)
        self.assertTrue(title.startswith(f"{long_game.display_short} booster boxes: UK prices"), title)

    def test_the_sitemap_lists_each_kind_with_enough_products(self):
        xml = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("/games/pokemon/booster-boxes/</loc>", xml)
        self.assertNotIn("/games/pokemon/elite-trainer-boxes/", xml)   # one ETB: its page is not offered

    def test_no_em_dashes_or_exclamation_marks(self):
        html = self.page()
        visible = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", html.split("<body", 1)[1], flags=re.S)
        self.assertNotIn("—", visible)
        self.assertNotIn("!", visible)
