"""The new home page (web/feed.py) behind the owner's Site look switch (web/look.py). The owner, 4 Nov 2026, with the
marketing team's redesign: "let's redesign can we easily roll this back if i don't like it"."""

import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import fair_price
from catalogue.models import Listing, Product, SiteLook
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from content import service as copy
from web import feed, listcache, look, pagecache, warming

from .tests import CopyStyleTests

HOME = reverse("web:home")
PICTURE = "https://cdn.example/{}.png"


def switch(on):
    row = SiteLook.load()
    row.feed_home = on
    row.save()


def text_of(html):
    return re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style|svg)\b.*?</\1>", " ", html))


class FeedCase(TestCase):
    """Two games, shops' prices with RRPs and savings, and a set on pre-order."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.harbour, self.northgate = make_retailer("Harbour Games"), make_retailer("Northgate Cards")
        self.pokemon = make_game()
        self.magic = make_game("Magic: The Gathering", "magic", short_name="Magic")
        self.surging = make_set(self.pokemon, "Surging Sparks", "surging-sparks", code="SSP")
        self.prismatic = make_set(self.pokemon)
        self.fantasy = make_set(self.magic, "Final Fantasy", "final-fantasy", code="FIN")
        # Under RRP: Final Fantasy £100, Surging Sparks £33, the bundle £10, the tin £5. The ETB has no RRP.
        self.box = self.product(self.surging, "Surging Sparks Booster Box", Product.Type.BOOSTER_BOX, "87.00",
                                second="95.00", rrp="120.00")
        self.etb = self.product(self.prismatic, "Prismatic Evolutions Elite Trainer Box",
                                Product.Type.ELITE_TRAINER_BOX, "45.00", second="50.00")
        self.bundle = self.product(self.prismatic, "Prismatic Evolutions Booster Bundle", Product.Type.BUNDLE,
                                   "30.00", rrp="40.00")
        self.tin = self.product(self.prismatic, "Prismatic Evolutions Mini Tin", Product.Type.TIN, "15.00",
                                rrp="20.00")
        self.collector = self.product(self.fantasy, "Final Fantasy Collector Booster Box",
                                      Product.Type.COLLECTOR_BOOSTER_BOX, "300.00", rrp="400.00")
        self.gone = self.product(self.fantasy, "Final Fantasy Bundle", Product.Type.BUNDLE, "60.00",
                                 stock=Listing.Availability.OUT_OF_STOCK, rrp="80.00")
        # The next drop: on pre-order at one shop, out in ten days.
        self.drop_day = timezone.localdate() + timedelta(days=10)
        self.mega = make_set(self.pokemon, "Mega Evolution", "mega-evolution", code="MEG", release_date=self.drop_day)
        self.drop = make_product(self.mega, name="Mega Evolution Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(self.drop, self.harbour, price="95.49", availability=Listing.Availability.PREORDER)

    def product(self, product_set, name, kind, price, second=None, rrp=None, stock=Listing.Availability.IN_STOCK):
        product = make_product(product_set, name=name, product_type=kind, image_url=PICTURE.format(len(name)),
                               rrp=Decimal(rrp) if rrp else None)
        make_listing(product, self.harbour, price=price, availability=stock)
        if second:
            make_listing(product, self.northgate, price=second)
        return product

    def page(self, url=HOME, **params):
        return self.client.get(url, params).content.decode()

    def card(self, html, product):
        start = html.index(f'<li class="feed-card" data-game="{product.game.slug}">\n  <a class="feed-card__link" '
                           f'href="{product.get_absolute_url()}"')
        return html[start:html.index("</li>", start)]


class SwitchTests(FeedCase):
    def test_off_by_default_the_home_page_is_the_usual_one(self):
        self.assertFalse(SiteLook.load().feed_home)
        html = self.page()
        self.assertNotIn("feed-dock", html)
        self.assertNotIn('class="feed__title"', html)
        self.assertNotIn("feed.js", html)
        self.assertNotIn('class="feed"', self.page(self.pokemon.get_absolute_url()))

    @override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
    def test_the_owners_switch_turns_the_feed_on_and_back_off_without_a_deploy(self):
        look.remember()
        game = self.pokemon.get_absolute_url()
        self.client.get(game)
        self.assertEqual(self.client.get(game)[pagecache.HEADER], "cached")
        switch(True)
        html = self.page()
        self.assertIn('<h1 class="feed__title">Today&#x27;s best prices</h1>', html)
        self.assertIn('class="feed-dock"', html)
        self.assertIn("feed-dock", self.page(game))   # the kept copy of the usual page went with the switch
        switch(False)
        self.assertNotIn("feed-dock", self.page())
        self.assertNotIn("feed-dock", self.page(game))

    def test_an_emptied_cache_leaves_the_usual_look_until_warm_pages_puts_the_switch_back(self):
        switch(True)
        self.assertIn("feed-dock", self.page())
        cache.clear()
        self.assertNotIn("feed-dock", self.page())
        with mock.patch("web.management.commands.warm_pages.warm_pages", return_value={}):
            call_command("warm_pages", "--due", stdout=open("/dev/null", "w"))
        self.assertIn("feed-dock", self.page())

    @override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
    def test_staff_can_see_either_look_now_and_nobody_else_can(self):
        look.remember()
        self.assertNotIn("feed-dock", self.page(look="feed"))
        staff = get_user_model().objects.create_user("owner", password="pw", is_staff=True)
        self.client.force_login(staff)
        self.assertIn("feed-dock", self.page(look="feed"))
        self.assertIn("feed-dock", self.page(self.pokemon.get_absolute_url(), look="feed"))
        self.assertNotIn("feed-dock", self.page(look="classic"))
        self.client.logout()
        switch(True)
        self.assertIn("feed-dock", self.page(look="classic"))
        # A preview is never kept for the next visitor.
        self.client.force_login(staff)
        self.client.get(HOME, {"look": "classic"})
        self.client.logout()
        page = self.client.get(HOME, {"look": "classic"})
        self.assertNotIn(pagecache.HEADER, page)
        self.assertIn("feed-dock", page.content.decode())


class HomeFeedTests(FeedCase):
    def setUp(self):
        super().setUp()
        switch(True)

    def test_it_shows_the_type_tiles_the_rail_and_the_next_drop_from_the_sites_own_prices(self):
        html = self.page()
        self.assertIn('<h1 class="feed__title">Today&#x27;s best prices</h1>', html)
        self.assertIn('<p class="feed__sub">Cheapest delivered prices from UK shops</p>', html)
        # The tiles: each type with something in stock, the most stocked first, pictured by its cheapest product
        # and opening the search narrowed to that type. The sold-out bundle neither counts nor pictures.
        tiles = re.findall(r'<li class="feed-tile"><a class="feed-tile__link" href="([^"]+)">.*?'
                           r'<span class="feed-tile__label">([^<]+)</span>', html, re.S)
        self.assertEqual(tiles, [
            (f"{reverse('web:search')}?type=booster_box", "Booster boxes"),
            (f"{reverse('web:search')}?type=bundle", "Bundles"),
            (f"{reverse('web:search')}?type=collector_booster_box", "Collector boxes"),
            (f"{reverse('web:search')}?type=elite_trainer_box", "ETBs"),
            (f"{reverse('web:search')}?type=tin", "Tins"),
        ])
        self.assertIn(PICTURE.format(len(self.bundle.name)), html)
        self.assertNotIn(PICTURE.format(len(self.gone.name)), html)
        # The rail: four products under their RRP, so Furthest under RRP, furthest first.
        self.assertIn('<h2 id="feed-rail-title" class="feed__h2">Furthest under RRP</h2>', html)
        self.assertIn(f'<a class="feed__all" href="{reverse("web:deals")}">See all</a>', html)
        cards = re.findall(r'<li class="feed-card" data-game="[^"]+">\n  <a class="feed-card__link" href="([^"]+)"', html)
        self.assertEqual(cards, [p.get_absolute_url() for p in (self.collector, self.box, self.bundle, self.tin)])
        card = self.card(html, self.collector)
        self.assertIn('<span class="feed-card__game">Magic</span>', card)
        self.assertIn('<span class="feed-card__name">Final Fantasy Collector Booster Box</span>', card)
        self.assertIn('<span class="feed-card__price">£300.00</span>', card)
        self.assertIn("delivered", card)
        self.assertIn("Harbour Games</span>", card)
        self.assertIn('<span class="feed-card__save">£100.00 under RRP</span>', card)
        self.assertIn('data-compare="feed"', card)
        self.assertNotIn(self.etb.get_absolute_url(), html[html.index("feed-rail-title"):html.index("feed-drop-title")])
        # The next drop: its date, its set, its pre-order price, and All drops.
        drop = html[html.index('<div class="feed-drop">'):html.index("</section>", html.index('<div class="feed-drop">'))]
        self.assertIn(f'<small>{self.drop_day:%b}</small><strong>{self.drop_day.day}</strong>', drop)
        self.assertIn("Pokémon", drop)
        self.assertIn(f'<a class="feed-drop__name" href="{self.mega.get_absolute_url()}">Mega Evolution</a>', drop)
        self.assertIn("£95.49", drop)
        self.assertNotIn("feed-drop__bell", drop)   # reminder emails are not set up
        self.assertIn(f'<a class="feed__all" href="{reverse("web:drops")}">All drops</a>', html)
        # The dock: the search box, then every game with products, All chosen, each by its own icon.
        dock = html[html.index('<div class="feed-dock">'):html.index('<div class="feed">')]
        self.assertIn(f'<form class="feed-search" action="{reverse("web:search")}" method="get" role="search">', dock)
        self.assertIn('placeholder="Search sets, boxes and packs"', dock)
        self.assertNotIn('name="game"', dock)
        self.assertIn(f'<a class="feed-game" href="{HOME}" aria-current="page">', dock)
        self.assertIn('<use href="#rr-game-all"/>', dock)
        games = re.findall(r'<a class="feed-game" href="([^"]+)"[^>]*>.*?<use href="#rr-game-([a-z-]+)"/>.*?'
                           r'<span class="feed-game__label">([^<]+)</span>', dock)
        self.assertEqual(games, [(HOME, "all", "All"), (self.pokemon.get_absolute_url(), "pokemon", "Pokémon"),
                                 (self.magic.get_absolute_url(), "magic", "Magic")])
        self.assertIn('<script src="/static/js/feed.js" defer></script>', html)
        self.assertIn('<body class="feed-look', html)

    def test_with_fewer_products_under_rrp_the_rail_is_best_prices_today_with_the_saving_on_the_next_shop(self):
        Product.objects.filter(pk__in=[self.collector.pk, self.bundle.pk]).update(rrp=None)
        cache.clear()
        look.remember()
        html = self.page()
        self.assertIn('<h2 id="feed-rail-title" class="feed__h2">Best prices today</h2>', html)
        cards = re.findall(r'<li class="feed-card" data-game="[^"]+">\n  <a class="feed-card__link" href="([^"]+)"', html)
        self.assertEqual(cards, [self.box.get_absolute_url(), self.etb.get_absolute_url()])
        self.assertIn('<span class="feed-card__save">Save £8.00 vs Northgate Cards</span>', self.card(html, self.box))
        self.assertIn('<span class="feed-card__save">Save £5.00 vs Northgate Cards</span>', self.card(html, self.etb))
        self.assertNotIn("under RRP", html)

    def test_the_chip_is_the_price_checks_own_verdict(self):
        Product.objects.filter(pk=self.collector.pk).update(price_band=fair_price.LOWEST)
        Product.objects.filter(pk=self.box.pk).update(price_band=fair_price.GOOD_PRICE)
        Product.objects.filter(pk=self.bundle.pk).update(price_band=fair_price.FAIR)
        cache.clear()
        look.remember()
        html = self.page()
        lowest, good = copy.get("product.fair.verdict_lowest"), copy.get("product.fair.verdict_good")
        self.assertIn(f'<span class="feed-chip feed-chip--lowest">{lowest}</span>', self.card(html, self.collector))
        self.assertIn(f'<span class="feed-chip">{good}</span>', self.card(html, self.box))
        self.assertNotIn("feed-chip", self.card(html, self.bundle))
        self.assertNotIn("feed-chip", self.card(html, self.tin))

    def test_the_bell_opens_the_reminder_when_reminder_emails_are_set_up(self):
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN="tok", RIPRAPTOR_MAIL_FROM="alerts@ripraptor.com"):
            html = self.page()
        self.assertIn(f'<a class="feed-drop__bell" href="{reverse("web:drops")}?remind={self.mega.pk}#drop-{self.mega.pk}" '
                      f'aria-label="Remind me Mega Evolution">', html)

    def test_the_partners_row_stays(self):
        from catalogue.models import Partner

        from .tests_partners import logo_file

        Partner.objects.create(name="Zatu Games", website="https://www.zatu.example/", logo=logo_file())
        html = self.page()
        self.assertIn('id="partners-title"', html)
        self.assertLess(html.index('<div class="feed">'), html.index('id="partners-title"'))

    def test_the_games_page_leads_with_the_games_own_feed(self):
        url = self.pokemon.get_absolute_url()
        html = self.page(url)
        self.assertIn('<h1 class="feed__title">Pokémon</h1>', html)
        self.assertIn('<h2 class="browse-head__title">Pokémon</h2>', html)   # the usual list below: one h1 a page
        self.assertEqual(html.count("<h1"), 1)
        tiles = re.findall(r'<li class="feed-tile"><a class="feed-tile__link" href="([^"]+)">', html)
        self.assertEqual(tiles, [f"{url}?type=booster_box", f"{url}?type=bundle", f"{url}?type=elite_trainer_box",
                                 f"{url}?type=tin"])   # one of each in stock (the drop is on pre-order), so by type
        # Three Pokémon products under RRP: Best prices today, the saving on the next shop, no game label.
        self.assertIn('<h2 id="feed-rail-title" class="feed__h2">Best prices today</h2>', html)
        self.assertIn(f'<a class="feed__all" href="{reverse("web:deals")}?game=pokemon">See all</a>', html)
        cards = re.findall(r'<li class="feed-card" data-game="([^"]+)">\n  <a class="feed-card__link" href="([^"]+)"', html)
        self.assertEqual(cards, [("pokemon", self.box.get_absolute_url()), ("pokemon", self.etb.get_absolute_url())])
        self.assertNotIn("feed-card__game", html)
        self.assertIn("Save £8.00 vs Northgate Cards", self.card(html, self.box))
        self.assertIn(f'<a class="feed__all" href="{reverse("web:drops")}?game=pokemon">All drops</a>', html)
        self.assertIn('<a class="feed-drop__name" href="{}">Mega Evolution</a>'.format(self.mega.get_absolute_url()),
                      html)
        dock = html[html.index('<div class="feed-dock">'):html.index('<div class="feed">')]
        self.assertIn('<input type="hidden" name="game" value="pokemon">', dock)
        self.assertIn('placeholder="Search Pokémon boxes and packs"', dock)
        self.assertIn(f'<a class="feed-game" href="{url}" aria-current="page">', dock)
        self.assertNotIn(f'<a class="feed-game" href="{HOME}" aria-current="page">', dock)
        # Magic has nothing on pre-order and one product under RRP.
        magic = self.page(self.magic.get_absolute_url())
        self.assertIn('<h1 class="feed__title">Magic: The Gathering</h1>', magic)
        self.assertNotIn("feed-drop-title", magic)
        self.assertNotIn("feed-rail-title", magic)   # and nothing with a saving on another shop: no rail
        self.assertNotIn("feed-card", magic)
        self.assertIn('<li class="feed-tile">', magic)

    def test_a_games_list_fetched_on_its_own_has_no_feed(self):
        html = self.page(self.pokemon.get_absolute_url(), pane=1)
        self.assertNotIn("feed", html)

    def test_every_page_keeps_the_house_style(self):
        for url in (HOME, self.pokemon.get_absolute_url(), self.magic.get_absolute_url()):
            html = self.page(url)
            text = text_of(html)
            with self.subTest(url=url):
                self.assertNotIn("!", text)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())
                for mark in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(mark, text)

    def test_a_page_costs_a_bounded_number_of_queries_however_many_products(self):
        def count(url):
            warming.warm_pages()
            feed.tile_picks(fresh=True)
            self.client.get(url)   # the first visit after a warm fills what the page shares with every page
            with CaptureQueriesContext(connection) as queries:
                self.client.get(url)
            return len(queries)

        before = {url: count(url) for url in (HOME, self.pokemon.get_absolute_url())}
        for n in range(12):
            self.product(self.surging, f"Surging Sparks Thing {n}", Product.Type.BOOSTER_PACK, "4.00", second="5.00",
                         rrp="6.00")
        after = {url: count(url) for url in before}
        self.assertEqual(after, before)
        self.assertLessEqual(before[HOME], 6)   # the wording, the tiles' products and the bar's games


class CacheTests(FeedCase):
    def test_warm_pages_fills_the_rails_and_the_tiles_with_the_other_lists(self):
        self.assertIn(feed.RAILS_KEY, warming.cached_keys(warming.active_games()))
        self.assertIsNone(listcache.peek(feed.RAILS_KEY))
        took = warming.warm_pages()
        self.assertIn("feed rails", took)
        rails = listcache.peek(feed.RAILS_KEY)
        self.assertEqual(set(rails), {None, self.pokemon.pk, self.magic.pk})
        self.assertEqual(rails[None][0], "rrp")
        self.assertEqual([card.product.pk for card in rails[self.magic.pk][1]], [])
        self.assertEqual(rails[self.pokemon.pk][0], "save")
        self.assertIsNotNone(cache.get(feed.TILES_KEY))

    def test_a_price_change_rebuilds_the_rail(self):
        switch(True)
        self.assertIn("Furthest under RRP", self.page())
        Product.objects.filter(pk__in=[self.collector.pk, self.bundle.pk]).update(rrp=None)
        Listing.objects.filter(product=self.collector).first().save()   # a save clears the lists (catalogue.signals)
        self.assertIn("Best prices today", self.page())


class BarTests(TestCase):
    def test_each_game_gets_its_own_icon_matched_on_its_slug(self):
        cases = {"pokemon": "pokemon", "magic-the-gathering": "magic", "one-piece": "one-piece", "lorcana": "lorcana",
                 "yu-gi-oh": "yugioh", "sports-cards": "sports", "star-wars-unlimited": "star-wars",
                 "riftbound": "riftbound", "digimon": "digimon", "dragon-ball-super": "dragon-ball",
                 "flesh-and-blood": "flesh-and-blood", "union-arena": "union-arena", "cardfight-vanguard": "vanguard",
                 "weiss-schwarz": "weiss-schwarz", "battle-spirits-saga": "all"}
        for slug, icon in cases.items():
            with self.subTest(slug=slug):
                self.assertEqual(feed.icon_for(slug), icon)
        sprite = open("web/templates/web/includes/feed_sprite.svg", encoding="utf-8").read()
        for icon in set(cases.values()):
            self.assertIn(f'id="rr-game-{icon}"', sprite)
        for icon in ("search", "arrow-right", "bell"):
            self.assertIn(f'id="rr-{icon}"', sprite)
        self.assertNotIn("<script", sprite)

    def test_the_bar_has_every_game_with_products_in_its_order(self):
        lorcana = make_game("Disney Lorcana", "lorcana", short_name="Lorcana")
        magic = make_game("Magic: The Gathering", "magic", short_name="Magic")
        make_game("Battle Spirits Saga", "battle-spirits-saga")
        pokemon = make_game()
        riftbound = make_game("Riftbound", "riftbound")
        make_game("Digimon", "digimon")   # no products: not in the bar
        for game in (lorcana, magic, pokemon, riftbound):
            make_product(make_set(game, slug=f"{game.slug}-set", code=game.slug[:3].upper()), name=f"{game.name} Box")
        with self.assertNumQueries(1):
            bar = feed.games_bar()
        self.assertEqual([(g["slug"], g["label"], g["icon"]) for g in bar], [
            ("pokemon", "Pokémon", "pokemon"), ("magic", "Magic", "magic"), ("lorcana", "Lorcana", "lorcana"),
            ("riftbound", "Riftbound", "riftbound")])
        self.assertEqual(bar[0]["url"], pokemon.get_absolute_url())


class AdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))

    def test_admins_home_page_leads_to_the_switch(self):
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn(f'<a href="{reverse("admin:catalogue_sitelook_changelist")}">Site look: switch the new home '
                      'page on, or back off</a>', html)

    def test_the_one_row_has_the_switch_and_a_way_to_see_each_look(self):
        response = self.client.get(reverse("admin:catalogue_sitelook_changelist"))
        self.assertRedirects(response, reverse("admin:catalogue_sitelook_change", args=[1]))
        html = self.client.get(reverse("admin:catalogue_sitelook_change", args=[1])).content.decode()
        self.assertIn('name="feed_home"', html)
        self.assertIn('href="/?look=feed"', html)
        self.assertIn('href="/?look=classic"', html)
        self.assertNotIn('name="_addanother"', html)

    def test_saving_the_row_switches_the_site(self):
        make_listing(make_product(make_set(make_game())), make_retailer())
        self.client.get(reverse("admin:catalogue_sitelook_changelist"))   # makes the row
        self.client.post(reverse("admin:catalogue_sitelook_change", args=[1]), {"feed_home": "on"})
        self.assertTrue(SiteLook.load().feed_home)
        self.client.logout()
        self.assertIn("feed-dock", self.client.get(HOME).content.decode())
