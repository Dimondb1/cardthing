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
from django.utils.text import slugify

from catalogue import fair_price
from catalogue.models import Listing, Product, SiteLook
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from content import service as copy
from web import feed, listcache, look, pagecache, warming

from .tests import CopyStyleTests

HOME = reverse("web:home")
CARD_START = (r'<li class="feed-card" data-game="([^"]+)" data-side="[a-z]+"( data-spare hidden)?>'
              r'\n  <a class="feed-card__link" href="([^"]+)"')
PICTURE = "https://cdn.example/{}.png"


def picture(product):
    """The stand-in picture a test product was given: its own, by name."""
    return PICTURE.format(slugify(product.name))


def switch(on):
    row = SiteLook.load()
    row.feed_home = on
    row.save()


def rail_title(html):
    """The words of the rail's heading, after the hunting raptor mark it wears."""
    found = re.search(r'<h2 id="feed-rail-title" class="feed__h2 feed__h2--mark"><svg class="hunt-mark".*?</svg>(.*?)</h2>',
                      html, re.S)
    return found.group(1) if found else None


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
        product = make_product(product_set, name=name, product_type=kind, image_url=PICTURE.format(slugify(name)),
                               rrp=Decimal(rrp) if rrp else None)
        make_listing(product, self.harbour, price=price, availability=stock)
        if second:
            make_listing(product, self.northgate, price=second)
        return product

    def page(self, url=HOME, **params):
        return self.client.get(url, params).content.decode()

    def card(self, html, product):
        found = re.search(rf'<li class="feed-card" data-game="{product.game.slug}" data-side="[a-z]+"( data-spare hidden)?>'
                          rf'\n  <a class="feed-card__link" href="{re.escape(product.get_absolute_url())}"', html)
        self.assertIsNotNone(found, product.name)
        return html[found.start():html.index("</li>", found.start())]

    def cards(self, html, start="feed-rail-title", end="feed-deals-title"):
        """[(game slug, product URL, spare?)] of the cards on the rail headed ``start``, up to ``end``."""
        part = html[html.index(start):html.index(end) if end and end in html else len(html)]
        return [(game, url, bool(spare)) for game, spare, url in re.findall(
            CARD_START, part)]


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
        tiles = re.findall(r'<li class="feed-tile"><a class="feed-tile__link" href="([^"]+)" data-browse-link>.*?'
                           r'<span class="feed-tile__label">([^<]+)</span>', html, re.S)
        self.assertEqual(tiles, [
            (f"{reverse('web:search')}?type=booster_box", "Booster boxes"),
            (f"{reverse('web:search')}?type=bundle", "Bundles"),
            (f"{reverse('web:search')}?type=collector_booster_box", "Collector boxes"),
            (f"{reverse('web:search')}?type=elite_trainer_box", "ETBs"),
            (f"{reverse('web:search')}?type=tin", "Tins"),
        ])
        self.assertIn(picture(self.bundle), html)
        self.assertNotIn(picture(self.gone), html)
        # The rail: four products under their RRP, so Furthest under RRP, furthest first.
        self.assertEqual(rail_title(html), "Furthest under RRP")
        self.assertIn(f'<a class="feed__all" href="{reverse("web:deals")}">See all</a>', html)
        self.assertEqual([url for _game, url, _spare in self.cards(html)],
                         [p.get_absolute_url() for p in (self.collector, self.box, self.bundle, self.tin)])
        self.assertIn('<ul class="feed__row feed__cards" data-interest data-interest-show="4">', html)
        card = self.card(html, self.collector)
        self.assertIn('<span class="feed-card__game">Magic</span>', card)
        self.assertIn('<span class="feed-card__name">Final Fantasy Collector Booster Box</span>', card)
        self.assertIn('<span class="feed-card__price">£300.00</span>', card)
        self.assertIn("delivered", card)
        self.assertIn("Harbour Games</span>", card)
        self.assertIn('<span class="feed-card__save">£100.00 under RRP</span>', card)
        self.assertIn('data-compare="feed"', card)
        self.assertNotIn(self.etb.get_absolute_url(), html[html.index("feed-rail-title"):html.index("feed-deals-title")])
        # Rip's Deals under it, as on the usual home page: the deals with evidence that are not on the rail above,
        # the line that says the row favours shops that pay, and the saving each deal is measured by.
        deals = html[html.index("feed-deals-title"):html.index("feed-drop-title")]
        self.assertIn('class="feed__h2 feed__h2--mark"><span class="mark section-head__mascot" aria-hidden="true"></span>'
                      'Rip&#x27;s Deals</h2>', deals)
        self.assertEqual([url for _game, url, _spare in self.cards(html, "feed-deals-title", "feed-drop-title")],
                         [self.etb.get_absolute_url()])
        self.assertIn('data-compare="featured"', deals)
        self.assertIn("Save £5.00 vs Northgate Cards", deals)
        self.assertIn('<p class="feed__note">Rip&#x27;s Deals favour shops that pay us a commission.', deals)
        self.assertIn(f'<a href="{reverse("web:about")}">', deals)
        self.assertIn(f'<a class="feed__all" href="{reverse("web:deals")}">See all</a>', deals)
        # The visitor's own games first, in the browser: the games to match on and the script, after the rails.
        self.assertIn('<script id="interest-games" type="application/json">', html)
        self.assertIn('"ptcg"', html[html.index('id="interest-games"'):html.index("feed-drop-title")])
        self.assertLess(html.index("feed-deals-title"), html.index('id="interest-games"'))
        self.assertLess(html.index('id="interest-games"'), html.index("feed-drop-title"))
        self.assertIn("[data-interest]:not([data-interest-done])", html)
        # An arrow waits at the end of each row for feed.js to show while there is more to the right.
        self.assertEqual(html.count('<button class="feed__more" type="button" hidden aria-label="Show more">'), 3)
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
        self.assertIn(f'<form class="feed-search" action="{reverse("web:search")}" method="get" role="search" ', dock)
        self.assertIn('placeholder="Search sets, boxes and packs"', dock)
        self.assertIn('<button class="feed-search__mic" type="button" hidden aria-label="Search by voice" '
                      'data-listening="Listening">', dock)
        # Results as you type: the same script and wording as the header's search, the panel above the box.
        self.assertIn(f'data-live-search="{reverse("web:search_api")}" data-checking="', dock)
        self.assertIn('data-recent="', dock)
        self.assertIn('<div class="live" hidden data-live-panel="feed-q"><p class="live__status" role="status" '
                      'aria-live="polite"></p><ul class="live__results" role="listbox" id="feed-q-results"></ul></div>', dock)
        self.assertIn('role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="feed-q-results"', dock)
        # The headers wear the site's marks: the hunting raptor on the deals, the raptor on Rip's Deals, the drops mark.
        self.assertIn('<h2 id="feed-rail-title" class="feed__h2 feed__h2--mark"><svg class="hunt-mark"', html)
        self.assertIn('<h2 id="feed-deals-title" class="feed__h2 feed__h2--mark"><span class="mark section-head__mascot" '
                      'aria-hidden="true"></span>Rip&#x27;s Deals</h2>', html)
        self.assertIn('<h2 id="feed-drop-title" class="feed__h2"><svg class="section-head__icon" aria-hidden="true" '
                      'focusable="false"><use href="#rr-drops"/></svg>Dropping soon</h2>', html)
        # Switching games: Chrome cross-fades feed pages and fetches a game's page on touch; only feed pages say so.
        self.assertIn("<style>@view-transition { navigation: auto; }</style>", html)
        self.assertRegex(html, r'<script nonce="[^"]+" type="speculationrules">\{"prefetch": \[\{"where": '
                             r'\{"selector_matches": "\.feed-game"\}, "eagerness": "moderate"\}\]\}</script>')
        self.assertIn("@view-transition", self.page(self.pokemon.get_absolute_url()))
        switch(False)
        self.assertNotIn("@view-transition", self.page())
        self.assertNotIn("@view-transition", self.page(self.pokemon.get_absolute_url()))
        self.assertNotIn('name="game"', dock)
        self.assertIn(f'<a class="feed-game" href="{HOME}" aria-current="page">', dock)
        self.assertIn('<use href="#rr-game-all"/>', dock)
        games = re.findall(r'<a class="feed-game" href="([^"]+)"[^>]*>.*?<use href="#rr-game-([a-z-]+)"/>.*?'
                           r'<span class="feed-game__label">([^<]+)</span>', dock)
        self.assertEqual(games, [(HOME, "all", "All"), (self.pokemon.get_absolute_url(), "pokemon", "Pokémon"),
                                 (self.magic.get_absolute_url(), "magic", "Magic")])
        self.assertRegex(html, r'<script nonce="[^"]+" src="/static/js/feed.js" defer></script>')
        self.assertIn('<body class="feed-look', html)

    def test_a_tile_is_pictured_by_the_product_most_shops_sell_not_the_cheapest(self):
        # A mis-typed accessory is cheap and at one shop; a real booster box is at two. The owner, 4 Nov 2026:
        # "We have a picture frame as a booster pack".
        frame = self.product(self.surging, "Pikachu Card Display Frame", Product.Type.BOOSTER_BOX, "17.98")
        cache.clear()
        look.remember()
        html = self.page()
        tile = html[html.index('<li class="feed-tile">'):html.index("</li>", html.index('<li class="feed-tile">'))]
        self.assertIn("Booster boxes", tile)
        self.assertIn(picture(self.box), tile)
        self.assertNotIn(picture(frame), html)

    def test_with_fewer_products_under_rrp_the_rail_is_best_prices_today_with_the_saving_on_the_next_shop(self):
        Product.objects.filter(pk__in=[self.collector.pk, self.bundle.pk]).update(rrp=None)
        cache.clear()
        look.remember()
        html = self.page()
        self.assertEqual(rail_title(html), "Best prices today")
        self.assertEqual([url for _game, url, _spare in self.cards(html, end="feed-drop-title")],
                         [self.box.get_absolute_url(), self.etb.get_absolute_url()])
        self.assertIn('<span class="feed-card__save">Save £8.00 vs Northgate Cards</span>', self.card(html, self.box))
        self.assertIn('<span class="feed-card__save">Save £5.00 vs Northgate Cards</span>', self.card(html, self.etb))
        self.assertNotIn("under RRP", html)
        self.assertNotIn("feed-deals-title", html)   # both deals are on the rail already

    def test_the_home_rail_keeps_spares_behind_it_for_the_visitors_own_games_and_a_games_page_does_not(self):
        for n in range(12):
            self.product(self.fantasy, f"Final Fantasy Thing {n}", Product.Type.BOOSTER_PACK, "4.00", rrp="5.00")
        cache.clear()
        look.remember()
        html = self.page()
        cards = self.cards(html)
        self.assertEqual(len(cards), feed.RAIL_SIZE + 4)
        self.assertEqual([spare for _game, _url, spare in cards], [False] * feed.RAIL_SIZE + [True] * 4)
        self.assertIn(f'data-interest data-interest-show="{feed.RAIL_SIZE}"', html)
        self.assertEqual(cards[0][1], self.collector.get_absolute_url())   # furthest under RRP first, still
        self.assertEqual([spare for _game, _url, spare in self.cards(self.page(self.magic.get_absolute_url()), end=None)],
                         [False] * feed.RAIL_SIZE)

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
        tiles = re.findall(r'<li class="feed-tile"><a class="feed-tile__link" href="([^"]+)" data-browse-link>', html)
        self.assertEqual(tiles, [f"{url}?type=booster_box", f"{url}?type=bundle", f"{url}?type=elite_trainer_box",
                                 f"{url}?type=tin"])   # one of each in stock (the drop is on pre-order), so by type
        # Three Pokémon products under RRP: Best prices today, the saving on the next shop, no game label.
        self.assertEqual(rail_title(html), "Best prices today")
        self.assertIn(f'<a class="feed__all" href="{reverse("web:deals")}?game=pokemon">See all</a>', html)
        self.assertEqual([(game, url) for game, url, _spare in self.cards(html, end="feed-drop-title")],
                         [("pokemon", self.box.get_absolute_url()), ("pokemon", self.etb.get_absolute_url())])
        self.assertNotIn("feed-card__game", html)
        self.assertNotIn("feed-deals-title", html)   # its deals are on the rail already
        self.assertIn('id="interest-games"', html)
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
        for n in range(12):   # in stock, so picked over, but no RRP or second shop: the rows stay as they are
            self.product(self.surging, f"Surging Sparks Thing {n}", Product.Type.BOOSTER_PACK, "4.00")
        after = {url: count(url) for url in before}
        self.assertEqual(after, before)
        self.assertLessEqual(before[HOME], 6)   # the wording, the tiles' products and the bar's games


class TypeTileTests(FeedCase):
    """A type tile filters in place: the list lands under the tiles, the row sticks, Back works (browse.js). The
    owner, 4 Nov 2026: tapping a tile went to /search/?type=, "where the results are below the fold, so users
    think nothing happened"."""

    def setUp(self):
        super().setUp()
        switch(True)

    def test_the_search_page_names_the_type_and_folds_its_filters(self):
        switch(False)
        for kind, title in (("booster_box", "Booster boxes"), ("elite_trainer_box", "ETBs"), ("tin", "Tins")):
            html = self.page(reverse("web:search"), type=kind)
            with self.subTest(kind=kind):
                self.assertIn(f'<h1 class="browse-head__title">{title}</h1>', html)
                self.assertIn(f"<title>{title} | RipRaptor</title>", html)
                self.assertNotIn("All products", html)
        self.assertIn('<h1 class="browse-head__title">All products</h1>', self.page(reverse("web:search")))
        self.assertIn("Results for ‘etb’", self.page(reverse("web:search"), q="etb", type="booster_box"))
        # The sort and filters fold under one button, as on a game's page, on every search.
        for params in ({"type": "booster_box"}, {"q": "box"}):
            with self.subTest(params=params):
                html = self.page(reverse("web:search"), **params)
                self.assertIn('<details class="filter-panel" data-filter-panel>', html)
                self.assertIn('<summary class="filter-panel__toggle"><span>Sort and filter</span>', html)

    def test_a_tile_is_a_link_that_loads_its_list_in_place(self):
        html = self.page()
        self.assertIn('<div data-browse>\n', html)
        self.assertLess(html.index("<div data-browse>"), html.index('<div class="feed">'))
        self.assertRegex(html, r'<script nonce="[^"]+" src="/static/js/browse.js" defer></script>')
        self.assertIn(f'<a class="feed-tile__link" href="{reverse("web:search")}?type=booster_box" data-browse-link>', html)
        self.assertNotIn('aria-current="page"><span class="feed-tile__well"', html)
        self.assertIn('data-loading="Loading"', html)
        self.assertIn('class="feed__scroller feed__scroller--tiles"', html)

    def test_the_search_page_filtered_to_a_type_leads_with_the_tiles_and_the_list_right_under(self):
        html = self.page(reverse("web:search"), type="booster_box")
        self.assertIn('<div class="feed feed--filtered">', html)
        self.assertNotIn("feed__title", html)
        self.assertNotIn("feed-rail-title", html)
        self.assertNotIn("feed-deals-title", html)
        self.assertNotIn("feed-drop-title", html)
        self.assertNotIn('id="interest-games"', html)
        # The chosen tile wears the mark and leads back to the feed; the others open their own lists in place.
        self.assertIn(f'<a class="feed-tile__link" href="{HOME}" data-browse-link aria-current="page">', html)
        self.assertIn(f'<a class="feed-tile__link" href="{reverse("web:search")}?type=bundle" data-browse-link>', html)
        # The list's head: the type, its count, the sort, and Clear back to the feed. No folded filters here.
        head = html[html.index('<div class="browse-head feed-list-head">'):html.index("</div>\n", html.index("feed-list-head__clear"))]
        self.assertIn('<h1 class="browse-head__title">Booster boxes</h1>', head)
        self.assertIn('<p class="browse-head__count" role="status">2 products</p>', head)   # the box and the drop's
        self.assertIn('<form class="feed-sort" method="get" action="" data-autosubmit>', head)
        self.assertIn('<input type="hidden" name="type" value="booster_box">', head)
        self.assertIn('name="sort"', head)
        self.assertIn(f'<a class="feed-list-head__clear" href="{HOME}" data-browse-link><span aria-hidden="true">× </span>Clear</a>', head)
        self.assertNotIn('class="filter-panel"', html)   # the folded Sort and filter: the Filters pill serves instead
        self.assertIn(self.box.get_absolute_url(), html[html.index("feed-list-head"):])
        self.assertEqual(html.count("<h1"), 1)
        # The dock stays, outside the region a tile swaps.
        self.assertLess(html.index('<div class="feed-dock">'), html.index("<div data-browse>"))
        # A search with words is the usual results page, feed or no feed.
        self.assertNotIn('class="feed', self.page(reverse("web:search"), q="box", type="booster_box"))
        self.assertNotIn('class="feed', self.page(reverse("web:search"), q="box"))

    def test_a_games_page_filtered_to_a_type_does_the_same_and_clears_to_the_games_page(self):
        url = self.pokemon.get_absolute_url()
        html = self.page(url, type="elite_trainer_box")
        self.assertIn('<div class="feed feed--filtered">', html)
        self.assertNotIn("feed__title", html)
        self.assertNotIn("feed-rail-title", html)
        self.assertIn(f'<a class="feed-tile__link" href="{url}" data-browse-link aria-current="page">', html)
        self.assertIn(f'<a class="feed-tile__link" href="{url}?type=booster_box" data-browse-link>', html)
        self.assertIn('<h1 class="browse-head__title">ETBs</h1>', html)
        self.assertIn(f'<a class="feed-list-head__clear" href="{url}" data-browse-link>', html)
        self.assertNotIn('class="filter-panel"', html)   # the folded Sort and filter: the Filters pill serves instead
        self.assertNotIn('class="sets', html)
        self.assertIn(self.etb.get_absolute_url(), html)
        self.assertNotIn(self.box.get_absolute_url(), html[html.index("feed-list-head"):])
        self.assertLess(html.index('<div class="feed-dock">'), html.index("<div data-browse>"))
        # The usual game page, unfiltered, keeps its feed, sets and folded filters.
        plain = self.page(url)
        self.assertIn('<div class="feed">', plain)
        self.assertIn("feed-rail-title", plain)
        self.assertNotIn("feed-list-head", plain)

    def test_the_languages_a_list_holds_are_chips_ready_to_tap_and_the_rest_folds_under_filters(self):
        # The owner, 4 Nov 2026: "a filter button but smart filter like. Languages ready to tap could work. Less is
        # more and maintain a beautiful layout."
        url = reverse("web:search")
        html = self.page(url, type="booster_box")
        self.assertNotIn('class="chip"', html)   # one language: nothing to choose between
        japan = self.product(self.surging, "Surging Sparks Japanese Booster Box", Product.Type.BOOSTER_BOX, "60.00")
        Product.objects.filter(pk=japan.pk).update(language="ja")
        cache.clear()
        look.remember()
        html = self.page(url, type="booster_box")
        row = html[html.index('<div class="feed-filters">'):html.index("</details>", html.index('<div class="feed-filters">'))]
        self.assertIn('<a class="chip" href="?type=booster_box&amp;lang=en">English</a>', row)
        self.assertIn('<a class="chip" href="?type=booster_box&amp;lang=ja">Japanese</a>', row)
        self.assertNotIn("Chinese", row)
        # The Filters pill, last in the row, folds the price band and 2+ shops; the type and sort ride along hidden.
        self.assertIn('<details class="feed-filters__more" data-filter-panel>', row)
        self.assertIn('<summary class="chip chip--more"><svg class="feed-ic" aria-hidden="true" focusable="false">'
                      '<use href="#rr-filter"/></svg>Filters</summary>', row)
        self.assertIn('<form class="filters feed-filters__form" method="get" action="" data-autosubmit>', row)
        self.assertIn('<input type="hidden" name="type" value="booster_box">', row)
        self.assertIn('name="price"', row)
        self.assertIn('name="compared"', row)
        self.assertIn('name="in_stock"', row)
        self.assertNotIn('name="lang"', row)
        self.assertNotIn("Sort and filter", html)
        # A tap on Japanese narrows the list and marks the chip; a tap on it again clears the language.
        html = self.page(url, type="booster_box", lang="ja")
        row = html[html.index('<div class="feed-filters">'):html.index("</details>", html.index('<div class="feed-filters">'))]
        self.assertIn('<a class="chip" href="?type=booster_box" aria-current="true">Japanese<span class="chip__off" '
                      'aria-hidden="true">×</span>', row)
        self.assertIn('<a class="chip" href="?type=booster_box&amp;lang=en">English</a>', row)
        self.assertIn('<p class="browse-head__count" role="status">1 product</p>', html)
        self.assertIn(japan.get_absolute_url(), html)
        self.assertNotIn(self.box.get_absolute_url(), html[html.index("feed-list-head"):])
        self.assertIn('<input type="hidden" name="lang" value="ja">', html)   # the sort keeps the language
        # A price band chosen is named on the pill and kept by the sort.
        html = self.page(url, type="booster_box", price="50-100", compared="on")
        self.assertIn('Filters<span class="chip__count">£50 to £100 · 2+ shops</span></summary>', html)
        self.assertIn('<input type="hidden" name="price" value="50-100">', html)
        self.assertIn('<input type="hidden" name="compared" value="on">', html)
        # A game's page: the same row, without the In stock toggle the stock tabs replace.
        game = self.pokemon.get_absolute_url()
        html = self.page(game, type="booster_box")
        row = html[html.index('<div class="feed-filters">'):html.index("</details>", html.index('<div class="feed-filters">'))]
        self.assertIn('<a class="chip" href="?type=booster_box&amp;lang=ja">Japanese</a>', row)
        self.assertNotIn('name="in_stock"', row)
        self.assertIn('name="compared"', row)

    def test_the_filtered_pages_keep_the_house_style(self):
        for url, params in ((reverse("web:search"), {"type": "booster_box"}), (self.pokemon.get_absolute_url(), {"type": "tin"})):
            html = self.page(url, **params)
            text = text_of(html)
            with self.subTest(url=url):
                self.assertNotIn("!", text)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())
                for mark in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(mark, text)


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
