"""The compact layout (web/compact.py): a short list at every step, on while the owner's switch is. The owner, 9 Nov 2026,
after using a simpler site: "Can we retain the bottom product search bar we have, but can we emulate their layout as
much as possible please do this a version I can turn on and off in admin"."""

import re
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from catalogue.models import Listing, Product, SiteLook
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import compact, look

from .tests import CopyStyleTests

BOX, ETB, PACK, TIN = (Product.Type.BOOSTER_BOX, Product.Type.ELITE_TRAINER_BOX, Product.Type.BOOSTER_PACK,
                       Product.Type.TIN)


def switch(feed=False, on=True):
    row = SiteLook.load()
    row.feed_home, row.compact = feed, on
    row.save()


class CompactCase(TestCase):
    def setUp(self):
        cache.clear()
        self.pokemon = make_game()
        self.reign = make_set(self.pokemon, name="Mega Evolution—Delta Reign", slug="delta-reign", code="ME03",
                              release_date=date(2026, 11, 14))
        self.older = make_set(self.pokemon, name="Surging Sparks", slug="surging-sparks", code="SSP",
                              release_date=date(2024, 11, 8))
        self.shops = [make_retailer("Harbour Games"), make_retailer("Kestrel Cards"), make_retailer("Moor Toys")]
        self.box = self.item(self.reign, "Pokémon TCG: Mega Evolution Delta Reign Booster Box", BOX, ["180.00", "190.00"],
                             packs=36)
        self.etb = self.item(self.reign, "Pokémon TCG: Mega Evolution Delta Reign Elite Trainer Box", ETB, ["70.00"],
                             packs=9)
        self.tin = self.item(self.reign, "Mega Evolution Delta Reign Mini Tin", TIN, ["12.00"],
                             availability=Listing.Availability.PREORDER)
        self.gone = self.item(self.reign, "Mega Evolution Delta Reign Booster Pack", PACK, ["5.00"],
                              availability=Listing.Availability.OUT_OF_STOCK)
        self.sparks = self.item(self.older, "Surging Sparks Booster Pack", PACK, ["4.50"])
        switch()

    def item(self, product_set, name, kind, prices, availability=Listing.Availability.IN_STOCK, **fields):
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        product = make_product(product_set, name=name, slug=slug, product_type=kind, **fields)
        for shop, price in zip(self.shops, prices):
            make_listing(product, shop, price=price, availability=availability)
        return product

    def page(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class SwitchTests(CompactCase):
    def test_it_is_off_until_the_owner_ticks_it_and_wins_over_the_new_home_page(self):
        switch(on=False)
        self.assertNotIn("cx-hero", self.page("/"))
        switch(feed=True, on=True)
        html = self.page("/")
        self.assertIn('<body class="feed-look compact-look"', html)
        self.assertNotIn('class="feed"', html)
        self.assertEqual(look.mode(self.client.get("/").wsgi_request), look.COMPACT)

    def test_staff_can_see_it_before_it_is_on_and_visitors_cannot(self):
        switch(on=False)
        self.assertNotIn("cx-hero", self.page("/", look="compact"))
        staff = get_user_model().objects.create_user("owner", password="pw", is_staff=True)
        self.client.force_login(staff)
        self.assertIn("cx-hero", self.page("/", look="compact"))
        admin = get_user_model().objects.create_user("boss", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        change = self.page(reverse("admin:catalogue_sitelook_change", args=[SiteLook.load().pk]))
        self.assertIn('name="compact"', change)
        self.assertIn("?look=compact", change)


class HomeTests(CompactCase):
    def test_the_home_page_leads_with_the_numbers_then_the_sets_people_want(self):
        html = self.page("/")
        self.assertIn('<h1 class="cx-hero__title">4 sealed prices in stock at 2 UK shops.</h1>', html)
        self.assertIn("Checked 1 hour ago", html)   # make_listing checks an hour back
        sets = html[html.index('class="cx-sets"'):]
        # Only sets with something to buy, the newest first when nobody has looked yet; the cheapest a pack of each
        # among its products in stock: the box's 36 packs at £180 is £5.00, Surging Sparks' pack £4.50.
        self.assertLess(sets.index("Mega Evolution—Delta Reign"), sets.index("Surging Sparks"))
        self.assertIn("From £5.00 a pack", sets)
        self.assertIn("From £4.50 a pack", sets)
        self.assertIn(f'href="{self.reign.get_absolute_url()}"', sets)
        # The search box and the games stay at the foot, as on the feed.
        self.assertIn('class="feed-dock"', html)
        self.assertIn('placeholder="Search any product"', html)


class GamePageTests(CompactCase):
    def test_a_game_lists_its_sets_newest_first_and_links_every_product(self):
        html = self.page(self.pokemon.get_absolute_url())
        self.assertIn('<h1 class="cx-hero__title">Pokémon</h1>', html)
        self.assertIn("3 in stock across 2 sets at 2 UK shops.", html)
        self.assertLess(html.index("Mega Evolution—Delta Reign"), html.index("Surging Sparks"))
        self.assertIn('href="/search/?game=pokemon">All Pokémon products</a>', html)
        self.assertIn('placeholder="Search Pokémon"', html)
        self.assertIn('<link rel="canonical" href="http://testserver/games/pokemon/">', html)

    def test_a_filtered_game_page_is_the_usual_list_drawn_as_rows(self):
        html = self.page(self.pokemon.get_absolute_url(), type="booster_box")
        self.assertNotIn("cx-hero", html)
        self.assertIn('<ul class="crows">', html)
        self.assertIn('<li class="crow">', html)
        self.assertNotIn('<li class="card', html)


class SetPageTests(CompactCase):
    def rows(self, html):
        return re.findall(r'<span class="crow__name">([^<]+)</span>', html)

    def test_a_set_lists_its_products_as_rows_in_stock_first(self):
        html = self.page(self.reign.get_absolute_url())
        self.assertIn('<h1 class="cx-sethead__title">Mega Evolution—Delta Reign</h1>', html)
        self.assertIn("4 products", html)
        self.assertIn("2 in stock", html)
        self.assertIn('<span class="cx-sethead__pack">From £5.00 a pack</span>', html)
        self.assertIn(f'href="{self.pokemon.get_absolute_url()}"><span aria-hidden="true">← </span>All Pokémon sets</a>', html)
        # The set's own name comes off each product's: in stock first, the cheapest a pack first (the box at £5.00 a
        # pack before the ETB at £7.78), then the pre-order, then what no shop has.
        self.assertEqual(self.rows(html), ["Booster Box", "Elite Trainer Box", "Mini Tin", "Booster Pack"])
        box = html[html.index("Booster Box</span>"):]
        self.assertIn('<span class="crow__price">£180.00</span>', box)
        self.assertIn("2 shops", box[:box.index("</li>")])
        self.assertIn("£5.00 a pack", box[:box.index("</li>")])
        self.assertIn("Pre-order", html[html.index("Mini Tin</span>"):html.index("Booster Pack</span>")])
        self.assertIn('<li class="crow crow--none">', html)
        self.assertIn('class="feed-dock"', html)

    def test_its_types_are_chips_all_first(self):
        html = self.page(self.reign.get_absolute_url())
        chips = html[html.index('class="cx-types"'):html.index("</nav>", html.index('class="cx-types"'))]
        self.assertEqual(re.findall(r'<a class="chip" href="([^"]+)"( aria-current="true")?>([^<]+)</a>', chips), [
            (self.reign.get_absolute_url(), ' aria-current="true"', "All"),
            ("?type=booster_box", "", "Booster boxes"), ("?type=elite_trainer_box", "", "ETBs"),
            ("?type=booster_pack", "", "Booster packs"), ("?type=tin", "", "Tins"),
        ])
        html = self.page(self.reign.get_absolute_url(), type="elite_trainer_box")
        self.assertEqual(self.rows(html), ["Elite Trainer Box"])
        self.assertIn('aria-current="true">ETBs</a>', html)
        self.assertIn(f'<a class="chip" href="{self.reign.get_absolute_url()}">All</a>', html)


class SearchTests(CompactCase):
    def test_search_results_are_rows_with_the_search_box_at_the_foot(self):
        html = self.page(reverse("web:search"), q="delta reign")
        self.assertIn('<ul class="crows">', html)
        self.assertIn("Pokémon TCG: Mega Evolution Delta Reign Booster Box", html)   # the full name out of its set
        self.assertIn('class="feed-dock"', html)
        self.assertIn('<body class="feed-look compact-look"', html)


class NamesTests(CompactCase):
    def test_a_products_name_in_its_sets_list(self):
        self.assertEqual(compact.short_name(self.box), "Booster Box")
        self.assertEqual(compact.short_name(self.sparks), "Booster Pack")
        odd = make_product(self.older, name="Surging Sparks", slug="odd", product_type=BOX)
        self.assertEqual(compact.short_name(odd), "Surging Sparks")   # nothing left: the whole name


class CompactStyleTests(CompactCase):
    def test_every_compact_page_keeps_the_house_style(self):
        for url in ("/", self.pokemon.get_absolute_url(), self.reign.get_absolute_url(),
                    reverse("web:search") + "?q=delta"):
            html = self.page(url)
            visible = re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html)).lower()
            # Set names are the publisher's own (Mega Evolution—Delta Reign): data, not our words.
            visible = visible.replace("mega evolution—delta reign", "")
            for phrase in CopyStyleTests.BANNED:
                with self.subTest(url=url, phrase=phrase):
                    self.assertNotIn(phrase, visible)
            self.assertNotIn("!", visible)
