"""Find with Rip (web/find.py): the guided search. The owner, 5 Nov 2026: "a little bit of a hand-holding for people
who don't really know how to use the site"."""

import re
from datetime import date

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Listing, Product, SiteLook
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import look

from .tests import CopyStyleTests

FIND = reverse("web:find")


class FindCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.harbour = make_retailer("Harbour Games")
        self.pokemon = make_game()
        self.magic = make_game("Magic: The Gathering", "magic", short_name="Magic")
        self.surging = make_set(self.pokemon, "Surging Sparks", "surging-sparks", code="SSP", release_date=date(2024, 11, 8))
        self.prismatic = make_set(self.pokemon, release_date=date(2025, 1, 17))
        self.older = make_set(self.pokemon, "Paldea Evolved", "paldea-evolved", code="PAL", release_date=date(2023, 6, 9))
        self.box = self.product(self.surging, "Surging Sparks Booster Box", Product.Type.BOOSTER_BOX, "87.00")
        self.etb = self.product(self.prismatic, "Prismatic Evolutions Elite Trainer Box", Product.Type.ELITE_TRAINER_BOX, "45.00")
        self.old_box = self.product(self.older, "Paldea Evolved Booster Box", Product.Type.BOOSTER_BOX, "95.00")
        self.gone = self.product(self.prismatic, "Prismatic Evolutions Booster Bundle", Product.Type.BUNDLE, "30.00",
                                 stock=Listing.Availability.OUT_OF_STOCK)
        self.product(make_set(self.magic, "Final Fantasy", "final-fantasy", code="FIN"), "Final Fantasy Play Booster Box",
                     Product.Type.BOOSTER_BOX, "120.00")

    def product(self, product_set, name, kind, price, stock=Listing.Availability.IN_STOCK):
        product = make_product(product_set, name=name, product_type=kind, image_url=f"https://cdn.example/{len(name)}.png")
        make_listing(product, self.harbour, price=price, availability=stock)
        return product

    def page(self, **params):
        return self.client.get(FIND, params)

    def options(self, html):
        return re.findall(r'<a class="find__option[^"]*" href="([^"]+)">.*?<span>([^<]+)</span>', html, re.S)


class StepTests(FindCase):
    def test_the_first_question_is_the_game(self):
        response = self.page()
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Find with Rip | RipRaptor</title>", html)
        self.assertIn('<h2 class="find__question">What are you looking for?</h2>', html)
        self.assertIn('<p class="find__sub">Pick a game and I will do the rest.</p>', html)
        self.assertEqual(self.options(html), [(f"{FIND}?game=pokemon", "Pokémon"), (f"{FIND}?game=magic", "Magic")])
        self.assertIn('<use href="#rr-game-pokemon"/>', html)
        self.assertIn('<li class="find__step find__step--now">', html)
        self.assertEqual(html.count("find__step--done"), 0)
        self.assertNotIn("find__chosen", html)
        self.assertIn('<meta name="robots" content="noindex', html)
        self.assertIn('class="mark find__rip"', html)

    def test_the_second_question_is_the_type_in_stock_for_the_game_pictured_by_a_product(self):
        html = self.page(game="pokemon").content.decode()
        self.assertIn('<h2 class="find__question">What kind of Pokémon product?</h2>', html)
        self.assertEqual(self.options(html), [
            (f"{FIND}?game=pokemon&amp;type=booster_box", "Booster boxes"),
            (f"{FIND}?game=pokemon&amp;type=elite_trainer_box", "ETBs"),
            (f"{FIND}?game=pokemon&amp;type=any", "Anything, show me the lot"),
        ])
        self.assertIn('<span class="find__note">2 in stock</span>', html)   # two booster boxes
        self.assertNotIn("Bundles", html)   # the bundle is sold out
        self.assertIn("https://cdn.example/26.png", html)   # Surging Sparks Booster Box pictures its type
        self.assertIn(f'<a class="chip" href="{FIND}">Pokémon<span class="chip__off"', html)
        self.assertEqual(html.count("find__step--done"), 1)
        self.assertIn(f'<a href="{FIND}">Start again</a>', html)

    def test_the_third_question_is_the_set_newest_first_with_that_type_in_stock(self):
        html = self.page(game="pokemon", type="booster_box").content.decode()
        self.assertIn('<h2 class="find__question">Which set?</h2>', html)
        self.assertEqual(self.options(html), [
            (f"{FIND}?game=pokemon&amp;type=booster_box&amp;set=surging-sparks", "Surging Sparks"),
            (f"{FIND}?game=pokemon&amp;type=booster_box&amp;set=paldea-evolved", "Paldea Evolved"),
            (f"{FIND}?game=pokemon&amp;type=booster_box&amp;set=any", "Any set"),
        ])
        self.assertIn('<span class="find__note">Nov 2024</span>', html)
        self.assertNotIn("Prismatic Evolutions", html[html.index("find__options"):])   # no booster box in stock there
        # Anything: every set with something in stock.
        html = self.page(game="pokemon", type="any").content.decode()
        self.assertEqual([label for _url, label in self.options(html)],
                         ["Prismatic Evolutions", "Surging Sparks", "Paldea Evolved", "Any set"])
        self.assertIn(f'<a class="chip" href="{FIND}?game=pokemon">Anything, show me the lot<span', html)

    def test_the_last_question_is_what_matters_and_then_the_list(self):
        html = self.page(game="pokemon", type="booster_box", set="surging-sparks").content.decode()
        self.assertIn('<h2 class="find__question">What matters most?</h2>', html)
        self.assertEqual([label for _url, label in self.options(html)], [
            "The cheapest delivered price", "A fair price by the Price check", "The biggest saving on the next shop",
            "Just the newest in stock"])
        self.assertIn('<use href="#rr-deals"/>', html)
        self.assertEqual(html.count("find__step--done"), 3)
        self.assertIn(f'<a class="chip" href="{FIND}?game=pokemon&amp;type=booster_box">Surging Sparks<span', html)
        cases = [
            ({"set": "surging-sparks", "want": "price"}, f"{self.surging.get_absolute_url()}?type=booster_box&sort=price"),
            ({"set": "surging-sparks", "want": "newest"}, f"{self.surging.get_absolute_url()}?type=booster_box"),
            ({"set": "any", "want": "fair"}, f"{self.pokemon.get_absolute_url()}?type=booster_box&sort=fair"),
        ]
        for params, target in cases:
            with self.subTest(params=params):
                response = self.page(game="pokemon", type="booster_box", **params)
                self.assertRedirects(response, target, fetch_redirect_response=False)
        response = self.page(game="pokemon", type="any", set="any", want="saving")
        self.assertRedirects(response, f"{self.pokemon.get_absolute_url()}?sort=saving", fetch_redirect_response=False)
        # The list it lands on is the usual one, open on what is in stock, with the sort in force.
        landed = self.client.get(f"{self.surging.get_absolute_url()}?type=booster_box&sort=price")
        self.assertEqual(landed.status_code, 200)
        self.assertIn(self.box.get_absolute_url(), landed.content.decode())

    def test_an_answer_the_site_does_not_know_is_asked_again(self):
        self.assertIn("What are you looking for?", self.page(game="yugioh").content.decode())
        self.assertIn("What kind of Pokémon product?", self.page(game="pokemon", type="spoon").content.decode())
        self.assertIn("Which set?", self.page(game="pokemon", type="booster_box", set="nope").content.decode())
        self.assertIn("What matters most?", self.page(game="pokemon", type="booster_box", set="any", want="moon").content.decode())

    def test_every_answer_is_a_plain_link_and_the_page_keeps_the_house_style(self):
        for params in ({}, {"game": "pokemon"}, {"game": "pokemon", "type": "booster_box"},
                       {"game": "pokemon", "type": "booster_box", "set": "any"}):
            html = self.page(**params).content.decode()
            with self.subTest(params=params):
                self.assertNotIn("<button", html[html.index('<div class="find">'):html.index("</main>")])
                self.assertNotIn("<form", html[html.index('<div class="find">'):html.index("</main>")])
                text = re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style|svg)\b.*?</\1>", " ", html))
                self.assertNotIn("!", text)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())
                self.assertIn("<style>@view-transition { navigation: auto; }</style>", html)
                self.assertRegex(html, r'<script nonce="[^"]+" type="speculationrules">\{"prefetch": \[\{"where": \{"selector_matches": "\.find__option"\}')

    def test_a_step_costs_a_few_queries_however_many_products(self):
        def count(**params):
            with CaptureQueriesContext(connection) as queries:
                self.client.get(FIND, params)
            return len(queries)

        before = [count(), count(game="pokemon"), count(game="pokemon", type="booster_box"),
                  count(game="pokemon", type="booster_box", set="any")]
        for n in range(10):
            self.product(self.surging, f"Surging Sparks Thing {n}", Product.Type.BOOSTER_PACK, "4.00")
        cache.clear()
        after = [count(), count(game="pokemon"), count(game="pokemon", type="booster_box"),
                 count(game="pokemon", type="booster_box", set="any")]
        self.assertEqual(after, before)
        self.assertLessEqual(max(before), 8)


class LinkTests(FindCase):
    def test_rip_sits_in_the_header_while_the_new_home_page_is_on(self):
        # The owner, 5 Nov 2026: "move Find with Rip up, into a special icon in the top, as it's pushed products down".
        self.assertNotIn("masthead__find", self.client.get(reverse("web:home")).content.decode())
        row = SiteLook.load()
        row.feed_home = True
        row.save()
        look.remember()
        link = (f'<a class="masthead__find" href="{FIND}"><span class="mark masthead__find-icon" aria-hidden="true"></span>'
                '<span class="masthead__find-text"><span class="masthead__find-full">Find with Rip</span>'
                '<span class="masthead__find-brief">Find</span></span></a>')
        for url in (reverse("web:home"), self.pokemon.get_absolute_url(), reverse("web:deals")):
            html = self.client.get(url).content.decode()
            with self.subTest(url=url):
                self.assertIn(link, html)
                self.assertLess(html.index("masthead__find"), html.index("masthead__swipe"))
        self.assertIn(f'<a class="masthead__find" href="{FIND}" aria-current="page">', self.client.get(FIND).content.decode())
        # Nothing sits between the headline and the tiles any more.
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertNotIn("find-link", html)
        self.assertNotIn("<a ", html[html.index('class="feed__sub"'):html.index("feed__tiles")])
        css = open("web/static/css/site.css", encoding="utf-8").read()
        self.assertIn(".masthead__find-brief { display: inline; line-height: 1; }", css)   # a phone: the short word
        self.assertIn(".masthead__find-icon { --mark-size: var(--mark-nav); color: var(--color-brand); }", css)
