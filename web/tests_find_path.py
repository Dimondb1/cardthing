"""Game, set and type: search understands them and a set page offers its types in a row (catalogue/search.py
understand and suggest_sets, web/views.py search_path and set_types). The owner, 9 Nov 2026: "We should search game
(pokemon) - sets (delta reign) - the type. I like the way the site is set currently set out, so if you can do it
without too much visual change that would be good. But we could work on visibly decluttering if you can without
compromising on function." Also the product card's decluttering: one shop's row is not repeated under its price, and
one link leads to the comparison."""

import re
from datetime import date

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue import search
from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

ETB, BOX, BUNDLE, TIN = (Product.Type.ELITE_TRAINER_BOX, Product.Type.BOOSTER_BOX, Product.Type.BUNDLE,
                         Product.Type.TIN)


class Catalogue:
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.magic = make_game("Magic: The Gathering", slug="magic", short_name="Magic", search_aliases="mtg")
        self.reign = make_set(self.game, name="Mega Evolution—Delta Reign", slug="delta-reign", code="ME03",
                              release_date=date(2026, 11, 14))
        self.starlit = make_set(self.game, name="Mega Evolution—Starlit Harbour", slug="starlit-harbour", code="ME02",
                                release_date=date(2026, 9, 26))
        self.three = make_set(self.game, name="Set 3", slug="set-3", code="S3", release_date=date(2025, 1, 1))
        self.thirty = make_set(self.game, name="Set 30", slug="set-30", code="S30", release_date=date(2026, 1, 1))
        self.shops = [make_retailer("Harbour Games"), make_retailer("Kestrel Cards"), make_retailer("Moor Toys")]
        self.etb = self.item(self.reign, "Pokémon TCG: Mega Evolution Delta Reign Elite Trainer Box", ETB, shops=2)
        self.box = self.item(self.reign, "Pokémon TCG: Mega Evolution Delta Reign Booster Box", BOX)
        self.bundle = self.item(self.reign, "Pokémon TCG: Mega Evolution Delta Reign Booster Bundle", BUNDLE,
                                availability=Listing.Availability.OUT_OF_STOCK)
        self.starlit_etb = self.item(self.starlit, "Pokémon TCG: Mega Evolution Starlit Harbour Elite Trainer Box", ETB)
        self.three_etb = self.item(self.three, "Set 3 Elite Trainer Box", ETB)
        self.thirty_etb = self.item(self.thirty, "Set 30 Elite Trainer Box", ETB, shops=3)

    def item(self, product_set, name, kind, shops=1, **listing):
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        product = make_product(product_set, name=name, slug=slug, product_type=kind)
        for n, shop in enumerate(self.shops[:shops]):
            make_listing(product, shop, price=f"{60 + n * 5}.00", **listing)
        return product


class UnderstandTests(Catalogue, TestCase):
    def named(self, query):
        found = search.understand(query)
        return ((found["game"] or {}).get("name"), (found["set"] or {}).get("name"), found["type"])

    def test_a_search_names_its_game_set_and_type(self):
        self.assertEqual(self.named("pokemon delta reign etb"), ("Pokémon", "Mega Evolution—Delta Reign", ETB))
        self.assertEqual(self.named("Delta Reign booster boxes"), ("Pokémon", "Mega Evolution—Delta Reign", BOX))
        self.assertEqual(self.named("ptcg delta reign"), ("Pokémon", "Mega Evolution—Delta Reign", ""))
        self.assertEqual(self.named("pokemon tcg etbs"), ("Pokémon", None, ETB))
        self.assertEqual(self.named("mtg collector boxes"), ("Magic: The Gathering", None,
                                                             Product.Type.COLLECTOR_BOOSTER_BOX))

    def test_a_whole_word_names_a_set_and_two_sets_named_alike_name_none(self):
        self.assertEqual(self.named("pokemon set 3 etb")[1], "Set 3")
        self.assertEqual(self.named("pokemon mega evolution etb"), ("Pokémon", None, ETB))
        self.assertEqual(self.named("booster"), (None, None, ""))
        self.assertEqual(self.named("magic delta reign"), ("Magic: The Gathering", None, ""))

    def test_a_renamed_set_is_understood_at_once(self):
        self.assertEqual(self.named("delta reign")[1], "Mega Evolution—Delta Reign")
        self.reign.name = "Mega Evolution—Phantom Reign"
        self.reign.save()
        self.assertEqual(self.named("phantom reign")[1], "Mega Evolution—Phantom Reign")

    def test_the_box_suggests_a_set_as_it_is_typed(self):
        sets, kind = search.suggest_sets("pokemon delta re")
        self.assertEqual(([entry["name"] for entry in sets], kind), (["Mega Evolution—Delta Reign"], ""))
        sets, kind = search.suggest_sets("delta reign etb")
        self.assertEqual(([entry["name"] for entry in sets], kind), (["Mega Evolution—Delta Reign"], ETB))
        # The whole word first, then the newest: Set 3 before Set 30.
        self.assertEqual([entry["name"] for entry in search.suggest_sets("pokemon set 3")[0]], ["Set 3", "Set 30"])
        # A game alone, or a word too short to say which set, suggests none.
        self.assertEqual(search.suggest_sets("pokemon")[0], [])
        self.assertEqual(search.suggest_sets("pokemon d")[0], [])


class SearchTests(Catalogue, TestCase):
    def test_whole_words_lead_best_match(self):
        html = self.client.get(reverse("web:search"), {"q": "set 3 etb"}).content.decode()
        self.assertLess(html.index(self.three_etb.name), html.index(self.thirty_etb.name))

    def test_results_lead_with_links_to_the_game_set_and_type_they_name(self):
        html = self.client.get(reverse("web:search"), {"q": "pokemon delta reign etb"}).content.decode()
        path = html[html.index('aria-label="Where these live"'):]
        path = path[:path.index("</nav>")]
        self.assertEqual(re.findall(r'<a href="([^"]+)">([^<]+)</a>', path), [
            ("/games/pokemon/", "Pokémon"),
            ("/games/pokemon/delta-reign/", "Mega Evolution—Delta Reign"),
            ("/games/pokemon/delta-reign/?type=elite_trainer_box", "ETBs"),
        ])
        html = self.client.get(reverse("web:search"), {"q": "pokemon etb"}).content.decode()
        self.assertIn('<a href="/games/pokemon/?type=elite_trainer_box">ETBs</a>', html)
        # A game alone has nothing more to offer, and words naming nothing show no links.
        for query in ("pokemon", "harbour trainer"):
            with self.subTest(query=query):
                self.assertNotContains(self.client.get(reverse("web:search"), {"q": query}), "Where these live")

    def test_the_box_offers_the_set_first(self):
        results = self.client.get(reverse("web:search_api"), {"q": "delta reign etb"}).json()["results"]
        self.assertEqual(results[0], {
            "name": "Mega Evolution—Delta Reign", "url": "/games/pokemon/delta-reign/?type=elite_trainer_box",
            "meta": "Pokémon set", "price": "", "stock": "3 products", "state": "set", "type": "",
        })
        self.assertEqual(results[1]["name"], self.etb.name)
        results = self.client.get(reverse("web:search_api"), {"q": "pokemon"}).json()["results"]
        self.assertNotIn("set", {result["state"] for result in results})

    def test_the_set_index_costs_one_query_then_none(self):
        url = reverse("web:search_api")
        with CaptureQueriesContext(connection) as first:
            self.client.get(url, {"q": "delta reign"})
        with CaptureQueriesContext(connection) as again:
            self.client.get(url, {"q": "delta reign"})
        self.assertEqual(len(first) - len(again), 2)   # the games and the sets, once


class SetTypesTests(Catalogue, TestCase):
    def row(self, html):
        start = html.index('class="set-nav set-nav--types"')
        return html[start:html.index("</nav>", start)]

    def test_a_set_page_offers_its_types_in_a_row(self):
        html = self.client.get(self.reign.get_absolute_url()).content.decode()
        row = self.row(html)
        links = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)<span class="set-nav__count( set-nav__count--none)?">'
                           r'<span aria-hidden="true">(\d+)</span>', row)
        # The game's order of types, each with how many are in stock: the sold-out bundle in grey.
        self.assertEqual(links, [
            ("?type=booster_box", "Booster boxes", "", "1"),
            ("?type=elite_trainer_box", "ETBs", "", "1"),
            ("?type=bundle", "Bundles", " set-nav__count--none", "0"),
        ])
        self.assertNotIn("aria-current", row)

    def test_a_type_chosen_is_marked_and_a_tap_shows_the_whole_set_again(self):
        url = self.reign.get_absolute_url()
        html = self.client.get(url, {"type": ETB, "lang": "en", "tab": "pre-orders", "page": "2"}).content.decode()
        row = self.row(html)
        self.assertIn('<a href="?lang=en" aria-current="true">ETBs', row)
        self.assertIn('href="?lang=en&amp;type=booster_box"', row)
        self.assertNotIn(self.box.name, html[html.index("</nav>", html.index("set-nav--types")):])
        # The row chooses the type, so the filters leave it out and a type alone needs no Clear filters.
        self.assertNotIn('name="type" id=', html)
        self.assertIn("filter-bar__clear", html)   # for the language
        html = self.client.get(url, {"type": ETB}).content.decode()
        self.assertNotIn("filter-bar__clear", html)
        self.assertEqual(self.row(html).count(f'href="{url}"'), 1)

    def test_a_set_of_one_type_has_no_row_and_the_row_costs_one_query(self):
        self.assertNotContains(self.client.get(self.three.get_absolute_url()), "set-nav--types")
        url = self.reign.get_absolute_url()

        def count():
            cache.clear()
            with CaptureQueriesContext(connection) as queries:
                self.client.get(url)
            return len(queries)

        before = count()
        self.item(self.reign, "Pokémon TCG: Mega Evolution Delta Reign Mini Tin", TIN)
        self.item(self.reign, "Pokémon TCG: Mega Evolution Delta Reign Sleeved Booster", Product.Type.BOOSTER_PACK)
        self.assertEqual(count(), before)


class CardTests(Catalogue, TestCase):
    def card(self, html, name):
        start = html.rindex('<li class="card', 0, html.index(name))
        return html[start:html.index("</li>\n", html.index('class="card__foot"', start))]

    def test_one_shop_is_not_repeated_under_its_price_and_one_link_leads_to_the_comparison(self):
        html = self.client.get(reverse("web:search"), {"q": "elite trainer box"}).content.decode()
        alone = self.card(html, self.three_etb.name)
        self.assertNotIn('class="shops"', alone)
        self.assertIn('class="card__check"', alone)
        self.assertNotIn("card__compare", alone)
        compared = self.card(html, self.thirty_etb.name)
        self.assertIn('class="shops"', compared)
        self.assertIn("Compare 3 shops", compared)
        self.assertNotIn('class="card__check"', compared)

    def test_the_shops_page_table_keeps_its_own_class(self):
        from django.contrib.staticfiles import finders

        css = open(finders.find("css/site.css"), encoding="utf-8").read()
        self.assertEqual(len(re.findall(r"^\.shops \{", css, re.M)), 1)   # the card's list only
        self.assertContains(self.client.get(reverse("web:shops")), '<table class="shop-table">')
