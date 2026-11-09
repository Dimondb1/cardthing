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
        # Pictures, so the type tiles above a list have products to show (feed.tile_picks skips the unpictured).
        for product in (self.box, self.etb, self.sparks):
            product.image_url = f"https://cdn.example/{product.slug}.jpg"
            product.save()
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
        self.assertIn('<h1 class="cx-hero__title"><span class="mark section-head__mascot cx-hero__mark" aria-hidden="true">'
                      '</span>4 sealed prices in stock at 2 UK shops.</h1>', html)
        self.assertIn('class="hunt-mark"', html)   # the hunting raptor on Today's best deals
        self.assertIn("Checked 1 hour ago", html)   # make_listing checks an hour back
        self.assertNotIn("cx-status", html)   # the headline says it; the other pages carry the small line
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
        self.assertIn('<p class="cx-status">4 prices in stock at 2 UK shops<span aria-hidden="true"> · </span>'
                      '<span class="visually-hidden">, </span>Checked 1 hour ago</p>', html)
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
        # The stock tabs do the In stock switch's job here, so there is none; the tiles sit above the rows.
        self.assertNotIn("cx-tools", html)
        self.assertIn('class="feed feed--filtered cx-tiles"', html)


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

    def test_its_types_are_small_tiles_pictured_by_its_own_products_all_first(self):
        # The owner, 9 Nov 2026: "product types as icons above".
        html = self.page(self.reign.get_absolute_url())
        tiles = html[html.index('class="feed feed--filtered cx-tiles"'):html.index("</ul>", html.index("cx-tiles"))]
        found = re.findall(r'<a class="feed-tile__link" href="([^"]+)" data-browse-link( aria-current="page")?>'
                           r'<span class="feed-tile__well" aria-hidden="true">(.*?)</span><span class="feed-tile__label">'
                           r'([^<]+)</span>', tiles, re.S)
        self.assertEqual([(url, bool(current), label) for url, current, _well, label in found], [
            (self.reign.get_absolute_url(), True, "All"), ("?type=booster_box", False, "Booster boxes"),
            ("?type=elite_trainer_box", False, "ETBs"), ("?type=booster_pack", False, "Booster packs"),
            ("?type=tin", False, "Tins"),
        ])   # the game's own order of types, whatever is in stock
        self.assertIn("#rr-game-all", found[0][2])            # All: the four tiles icon
        self.assertIn(self.box.image_url, found[1][2])        # each type pictured by one of the set's own
        html = self.page(self.reign.get_absolute_url(), type="elite_trainer_box")
        self.assertEqual(self.rows(html), ["Elite Trainer Box"])
        # The chosen tile, marked, leads back to All; the All tile is no longer marked.
        self.assertIn(f'<a class="feed-tile__link" href="{self.reign.get_absolute_url()}" data-browse-link aria-current="page">'
                      '<span class="feed-tile__well" aria-hidden="true"><span class="product-image product-image--tile">', html)
        self.assertIn(f'<a class="feed-tile__link" href="{self.reign.get_absolute_url()}" data-browse-link><span class="feed-tile__well" '
                      'aria-hidden="true"><svg class="feed-ic feed-tile__icon"', html)

    def test_the_in_stock_switch_keeps_only_what_a_shop_has_now(self):
        # The owner, 9 Nov 2026: "I also want a in stock slider".
        html = self.page(self.reign.get_absolute_url())
        self.assertIn('<form class="cx-tools" method="get" action="" data-autosubmit>', html)
        self.assertIn('<label class="toggle"><input type="checkbox" name="in_stock" value="on"><span class="toggle__track" '
                      'aria-hidden="true"></span><span>In stock</span></label>', html)
        html = self.page(self.reign.get_absolute_url(), in_stock="on")
        self.assertEqual(self.rows(html), ["Booster Box", "Elite Trainer Box"])
        self.assertIn('<input type="checkbox" name="in_stock" value="on" checked>', html)
        self.assertIn("4 products", html)   # the set's numbers stay the set's
        # With a type as well, the switch's form carries the type and the tiles carry the switch.
        html = self.page(self.reign.get_absolute_url(), in_stock="on", type="tin")
        self.assertEqual(self.rows(html), [])
        self.assertIn('<input type="hidden" name="type" value="tin">', html)
        self.assertIn('href="?in_stock=on&amp;type=booster_box" data-browse-link>', html)
        self.assertIn('href="?in_stock=on" data-browse-link aria-current="page">', html)
        self.assertIn("Clear filters", html)


class SearchTests(CompactCase):
    def test_search_results_are_rows_with_the_search_box_at_the_foot(self):
        html = self.page(reverse("web:search"), q="delta reign")
        self.assertIn('<ul class="crows">', html)
        self.assertIn("Pokémon TCG: Mega Evolution Delta Reign Booster Box", html)   # the full name out of its set
        self.assertIn('class="feed-dock"', html)
        self.assertIn('<body class="feed-look compact-look"', html)
        self.assertIn('<p class="cx-status">', html)
        self.assertNotIn('class="chips quick"', html)   # the tiles and the switch do the quick filters' job

    def test_a_list_has_the_type_tiles_and_the_in_stock_switch_keeping_its_choices(self):
        html = self.page(reverse("web:search"), game="pokemon", sort="price")
        tiles = html[html.index("cx-tiles"):html.index("</ul>", html.index("cx-tiles"))]
        self.assertIn('href="?game=pokemon&amp;sort=price" data-browse-link aria-current="page">', tiles)
        self.assertIn('href="?game=pokemon&amp;sort=price&amp;type=booster_box" data-browse-link>', tiles)
        self.assertIn('href="?game=pokemon&amp;sort=price&amp;type=elite_trainer_box" data-browse-link>', tiles)
        form = html[html.index('<form class="cx-tools"'):html.index("</form>", html.index('<form class="cx-tools"'))]
        self.assertIn('<input type="hidden" name="game" value="pokemon">', form)
        self.assertIn('<input type="hidden" name="sort" value="price">', form)
        self.assertNotIn('name="q"', form)
        self.assertIn('<input type="checkbox" name="in_stock" value="on">', form)
        # The folded filters keep the switch's choice as a hidden input, not a second switch.
        self.assertEqual(html.count('name="in_stock"'), 1)
        html = self.page(reverse("web:search"), game="pokemon", in_stock="on")
        self.assertIn('<input type="checkbox" name="in_stock" value="on" checked>', html)
        self.assertEqual(html.count('name="in_stock"'), 2)
        self.assertNotIn(self.tin.get_absolute_url(), html)   # a pre-order
        self.assertIn(self.box.get_absolute_url(), html)


class NamesTests(CompactCase):
    def test_a_products_name_in_its_sets_list(self):
        self.assertEqual(compact.short_name(self.box), "Booster Box")
        self.assertEqual(compact.short_name(self.sparks), "Booster Pack")
        odd = make_product(self.older, name="Surging Sparks", slug="odd", product_type=BOX)
        self.assertEqual(compact.short_name(odd), "Surging Sparks")   # nothing left: the whole name
        # However the set's name is punctuated (the live site, 9 Nov 2026: "Mega Evolution - Delta Reign" against
        # "Mega Evolution Delta Reign Booster Box"), and by the set's last words alone.
        self.reign.name = "Mega Evolution - Delta Reign"
        self.reign.save()
        self.box.refresh_from_db()
        self.assertEqual(compact.short_name(self.box), "Booster Box")
        tin = make_product(self.reign, name="Delta Reign Mini Tin: Charizard", slug="tin-2", product_type=TIN)
        self.assertEqual(compact.short_name(tin), "Mini Tin: Charizard")
        other = make_product(self.reign, name="Pokemon Delta Set 3", slug="other-2", product_type=TIN)
        self.assertEqual(compact.short_name(other), "Pokemon Delta Set 3")   # not the set's words in a row


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
