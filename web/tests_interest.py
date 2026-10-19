"""The home page's Drops and Featured deals put popular sets first for everyone (on the server, cached) and the
visitor's own games first in their browser (interest.js), with nothing about the visitor sent or kept."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import popularity
from catalogue.models import DailyPageView, DropAlert, Listing, OutboundClick, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

SCRIPT = Path(settings.BASE_DIR) / "web" / "static" / "js" / "interest.js"


def rows_of(html, marker):
    """[(opening tag, row html)] for each <li> carrying this class, in page order."""
    parts = html.split(f'<li class="{marker}"')[1:]
    return [(part.split(">", 1)[0], part) for part in parts]


def shown(rows):
    return [row for tag, row in rows if not re.search(r"\bhidden\b", tag)]


class PopularityScoreTests(TestCase):
    def test_soonness_halves_every_three_weeks_and_undated_counts_as_four_weeks(self):
        today = timezone.localdate()
        now = popularity.score(0, 0, 0, 1, today, today)
        self.assertEqual(now, 4)
        self.assertAlmostEqual(popularity.score(0, 0, 0, 1, today + timedelta(days=21), today), 2)
        self.assertAlmostEqual(popularity.score(0, 0, 0, 1, None, today),
                               popularity.score(0, 0, 0, 1, today + timedelta(days=28), today))
        # A date already passed is no sooner than today.
        self.assertEqual(popularity.score(0, 0, 0, 1, today - timedelta(days=3), today), now)

    def test_a_far_off_set_needs_strong_interest_to_beat_one_out_this_week(self):
        today = timezone.localdate()
        this_week = popularity.score(0, 0, 0, 1, today + timedelta(days=4), today)
        far = today + timedelta(days=60)
        self.assertLess(popularity.score(10, 1, 0, 1, far, today), this_week)       # some interest is not enough
        self.assertGreater(popularity.score(40, 5, 2, 2, far, today), this_week)    # strong interest is

    def test_weights(self):
        today = timezone.localdate()
        self.assertEqual(popularity.score(1, 0, 0, 0, today, today), 2)
        self.assertEqual(popularity.score(0, 1, 0, 0, today, today), 5)
        self.assertEqual(popularity.score(0, 0, 1, 0, today, today), 9)
        self.assertEqual(popularity.score(0, 0, 0, 1, today, today), 4)


class HomeDropsOrderTests(TestCase):
    """The Coming soon rows on the home page, most popular first, the same for every visitor."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.today = timezone.localdate()
        self.pokemon = make_game()
        self.magic = make_game("Magic: The Gathering", slug="magic-the-gathering", search_aliases="mtg, magic")
        self.harbour = make_retailer("Harbour Games")
        self.north = make_retailer("Northgate Cards")

    def upcoming(self, name, game=None, days=10, shops=1):
        slug = name.lower().replace(" ", "-")
        product_set = make_set(game or self.pokemon, name=name, slug=slug, code=slug[:6].upper(),
                               release_date=self.today + timedelta(days=days))
        product = make_product(product_set, name=f"{name} Elite Trainer Box", slug=f"{slug}-etb")
        for shop in (self.harbour, self.north)[:shops]:
            make_listing(product, shop, price="49.99", availability=Listing.Availability.PREORDER)
        return product_set, product

    def order(self):
        from web.views import home_coming_soon

        cache.clear()
        return [row.name for row in home_coming_soon()]

    def test_without_any_interest_the_soonest_comes_first(self):
        self.upcoming("Later Set", days=20)
        self.upcoming("Sooner Set", days=5)
        self.assertEqual(self.order(), ["Sooner Set", "Later Set"])

    def test_product_page_views_this_week_move_a_set_up(self):
        self.upcoming("Sooner Set", days=5)
        _, product = self.upcoming("Watched Set", days=12)
        DailyPageView.objects.create(date=self.today, kind=DailyPageView.Kind.PRODUCT, key=product.slug, hits=6)
        self.assertEqual(self.order(), ["Watched Set", "Sooner Set"])
        # Views older than the week do not count.
        DailyPageView.objects.update(date=self.today - timedelta(days=8))
        self.assertEqual(self.order(), ["Sooner Set", "Watched Set"])

    def test_clicks_to_shops_this_week_move_a_set_up(self):
        self.upcoming("Sooner Set", days=5)
        _, product = self.upcoming("Clicked Set", days=12)
        for _ in range(2):
            OutboundClick.objects.create(product=product, retailer=self.harbour)
        self.assertEqual(self.order(), ["Clicked Set", "Sooner Set"])
        OutboundClick.objects.update(created_at=timezone.now() - timedelta(days=8))
        self.assertEqual(self.order(), ["Sooner Set", "Clicked Set"])

    def test_only_confirmed_reminders_count(self):
        self.upcoming("Sooner Set", days=5)
        wanted, _ = self.upcoming("Wanted Set", days=12)
        DropAlert.objects.create(product_set=wanted, email="a@example.com")
        self.assertEqual(self.order(), ["Sooner Set", "Wanted Set"])
        DropAlert.objects.create(product_set=wanted, email="b@example.com", confirmed_at=timezone.now())
        self.assertEqual(self.order(), ["Wanted Set", "Sooner Set"])

    def test_more_shops_taking_pre_orders_move_a_set_up(self):
        self.upcoming("One Shop", days=8)
        self.upcoming("Two Shops", days=14, shops=2)
        self.assertEqual(self.order(), ["Two Shops", "One Shop"])

    def test_scores_for_many_sets_take_one_query(self):
        rows = [self.upcoming(f"Set {n}", days=n + 3)[0] for n in range(6)]
        with CaptureQueriesContext(connection) as queries:
            counts = popularity.interest_counts([row.pk for row in rows])
        self.assertEqual(len(queries), 1)
        self.assertEqual(len(counts), 6)
        # The rows become a dict, so the outer query neither joins the games nor sorts.
        outer = queries[0]["sql"].split("FROM", 1)[1].rsplit("WHERE", 1)
        self.assertNotIn("ORDER BY", queries[0]["sql"].rsplit(")", 1)[-1])
        self.assertNotIn("catalogue_game", outer[-1] + outer[0].rsplit(")", 1)[-1])

    def test_the_order_is_cached_under_its_own_key_and_cleared_with_the_lists(self):
        from catalogue import signals

        self.assertIn(signals.HOME_DROPS_CACHE_KEY, signals.LIST_CACHE_KEYS)
        self.assertEqual(signals.HOME_DROPS_CACHE_KEY, "web:home-drops:v1")
        self.assertEqual(signals.HOME_CACHE_KEY, "web:home-lists:v6")   # now holds the featured spares too
        self.upcoming("Sooner Set", days=5)
        self.client.get(reverse("web:home"))
        self.assertIsNotNone(cache.get(signals.HOME_DROPS_CACHE_KEY))
        signals.clear_list_caches_now(force=True)
        self.assertIsNone(cache.get(signals.HOME_DROPS_CACHE_KEY))

    def test_home_queries_do_not_grow_with_the_number_of_sets(self):
        for n in range(2):
            self.upcoming(f"Set {n}", days=n + 3)
        cache.clear()
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("web:home"))
        for n in range(2, 10):
            self.upcoming(f"Set {n}", days=n + 3, game=self.magic if n % 2 else None)
        cache.clear()
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("web:home"))
        self.assertEqual(len(few), len(many))

    def test_rows_carry_their_game_and_set_and_only_the_top_three_show_without_script(self):
        for n in range(5):
            self.upcoming(f"Pokemon Set {n}", days=n + 3)
        for n in range(4):
            self.upcoming(f"Magic Set {n}", game=self.magic, days=n + 20)
        html = self.client.get(reverse("web:home")).content.decode()
        rows = rows_of(html, "coming__row")
        self.assertEqual(len(rows), 8)   # COMING_SOON_RENDERED
        visible = shown(rows)
        self.assertEqual(len(visible), 3)
        for tag, _ in rows:
            self.assertRegex(tag, r'data-game="(pokemon|magic-the-gathering)" data-set="[a-z0-9-]+"')
        # The page without script reads exactly the server's order: its first three, the rest hidden after them.
        from web.views import home_coming_soon

        names = [row.name for row in home_coming_soon()]
        self.assertEqual([re.search(r'class="coming__name"[^>]*>([^<]+)<', row).group(1) for _, row in rows], names)
        self.assertEqual([re.search(r'class="coming__name"[^>]*>([^<]+)<', row).group(1) for row in visible], names[:3])
        self.assertIn('data-interest data-interest-show="3"', html)

    def test_game_and_set_pages_still_show_every_row(self):
        for n in range(5):
            self.upcoming(f"Pokemon Set {n}", days=n + 3)
        html = self.client.get(self.pokemon.get_absolute_url() + "?tab=pre-orders").content.decode()
        rows = rows_of(html, "coming__row")
        self.assertEqual(len(shown(rows)), len(rows))
        self.assertNotIn("data-interest", html)


class FeaturedSparesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.game = make_game()
        self.magic = make_game("Magic: The Gathering", slug="magic-the-gathering", search_aliases="mtg, magic")
        self.set = make_set(self.game)
        self.magic_set = make_set(self.magic, name="Foundations", slug="foundations", code="FDN")
        self.paying = make_retailer("Paying", affiliate_url_template="https://aff.example/?u={url}")
        self.plain = make_retailer("Plain")
        self.dear = make_retailer("Dear")

    def deal(self, n, shop, product_set=None):
        product = make_product(product_set or self.set, name=f"Deal Product {n} Booster Box", slug=f"deal-{n}",
                               product_type="booster_box")
        make_listing(product, shop, price=f"{50 + n}.00")
        make_listing(product, self.dear, price=f"{60 + n}.00")
        return product

    def test_the_featured_deals_are_unchanged_and_spares_are_the_next_qualifying_deals_on_each_side(self):
        from catalogue import offers
        from web.views import earns, featured_deals, featured_pick, home_lists

        for n in range(9):
            self.deal(n, self.paying)
        for n in range(9, 14):
            self.deal(n, self.plain, self.magic_set)
        no_saving = make_product(self.set, name="Lonely Booster Box", slug="lonely", product_type="booster_box")
        make_listing(no_saving, self.paying, price="40.00")
        products = list(Product.objects.for_lists().filter(in_stock_count__gte=1)
                        .prefetch_related(offers.buyable_prefetch()))
        rows, spares = featured_pick(products)
        self.assertEqual([p.pk for p, _ in rows], [p.pk for p, _ in featured_deals(products)])
        self.assertEqual(sum(1 for _, s in rows if earns(s.best)), 6)
        self.assertEqual([p.slug for p, s in spares if earns(s.best)], ["deal-6", "deal-7"])
        self.assertEqual(len([p for p, s in spares if not earns(s.best)]), 2)
        shown_ids = {p.pk for p, _ in rows}
        self.assertFalse(shown_ids & {p.pk for p, _ in spares})
        self.assertNotIn(no_saving.pk, [p.pk for p, _ in spares])
        featured, trending, cached_spares = home_lists()
        self.assertEqual([p.pk for p, _ in featured], [p.pk for p, _ in rows])
        self.assertEqual([p.pk for p, _ in cached_spares], [p.pk for p, _ in spares])

    def test_spares_are_hidden_without_script_and_every_card_carries_its_game_and_side(self):
        for n in range(9):
            self.deal(n, self.paying)
        for n in range(9, 14):
            self.deal(n, self.plain, self.magic_set)
        html = self.client.get(reverse("web:home")).content.decode()
        start = html.index('id="featured-title"')
        block = html[start:html.index("</section>", start)]
        cards = rows_of(block, "trending__item")
        self.assertEqual(len(cards), 12)
        self.assertEqual(len(shown(cards)), 8)
        for tag, _ in cards:
            self.assertRegex(tag, r'data-game="(pokemon|magic-the-gathering)"')
            self.assertRegex(tag, r'data-side="(earning|other)"')
            self.assertEqual("data-spare" in tag, bool(re.search(r"\bhidden\b", tag)))
        # The page without script shows the featured deals in the server's order.
        from web.views import home_lists

        names = [p.name for p, _ in home_lists()[0]]
        self.assertEqual([re.search(r'trending__name">([^<]+)<', row).group(1) for row in shown(cards)], names)

    def test_no_spares_when_every_qualifying_deal_is_featured(self):
        for n in range(3):
            self.deal(n, self.paying)
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertNotIn("data-spare hidden", html)
        self.assertIn('data-interest data-interest-show="3"', html)


class InterestScriptTests(TestCase):
    """interest.js reads only this browser's history and never talks to the network."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_the_script_makes_no_network_calls_and_can_be_inlined(self):
        source = SCRIPT.read_text()
        for call in ("fetch", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "import(", "src ="):
            self.assertNotIn(call, source)
        self.assertNotIn("</", source)   # safe inside an inline <script>
        self.assertNotIn("!", source)    # the copy tests read inline script as page text
        self.assertNotIn("—", source)

    def test_it_runs_inline_right_after_the_rows_with_the_games_it_can_match(self):
        game = make_game(search_aliases="ptcg, pkmn")
        product_set = make_set(game, release_date=timezone.localdate() + timedelta(days=5))
        product = make_product(product_set)
        make_listing(product, make_retailer(), price="49.99", availability=Listing.Availability.PREORDER)
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<script id="interest-games" type="application/json">', html)
        self.assertIn('["pokemon", "pok\\u00e9mon", "ptcg", "pkmn"]', html)
        script = html.index("const read = (key)")
        self.assertLess(html.index('class="coming__list"'), script)
        self.assertLess(script, html.index('id="watch-title"'))

    def test_history_remembers_the_game_of_a_product_and_of_game_and_set_pages(self):
        game = make_game()
        product_set = make_set(game)
        product = make_product(product_set)
        make_listing(product, make_retailer())
        self.assertIn('data-viewed-game="pokemon"', self.client.get(product.get_absolute_url()).content.decode())
        self.assertIn('data-visited-game="pokemon"', self.client.get(game.get_absolute_url()).content.decode())
        self.assertIn('data-visited-game="pokemon"', self.client.get(product_set.get_absolute_url()).content.decode())
        self.assertNotIn("data-visited-game", self.client.get(reverse("web:search"), {"q": "box"}).content.decode())
        history = (Path(settings.BASE_DIR) / "web" / "static" / "js" / "history.js").read_text()
        self.assertIn('"ripraptor.games"', history)
        self.assertNotIn("fetch(`${picks", history.split("Remember this game")[1].split("Remember this search")[0])


@unittest.skipUnless(shutil.which("node"), "needs node")
class BrowserScriptTests(TestCase):
    """history.js and interest.js run in node against a small stand-in for the page and localStorage."""

    HARNESS = """
const store = JSON.parse(process.argv[3]);
const clicks = [];
const el = (extra) => Object.assign({ hidden: true, dataset: {}, children: [], append() {},
  querySelector: () => el(), addEventListener: (type, fn) => clicks.push(fn) }, extra || {});
const section = el();
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; },
  removeItem: (k) => { delete store[k]; } };
global.fetch = () => Promise.resolve({ ok: false, text: () => "" });
global.document = {
  querySelector: (sel) => (sel === "[data-resume]" ? section : null),
  querySelectorAll: () => [],
  getElementById: () => ({ textContent: JSON.stringify([["magic-the-gathering", "magic", "mtg"]]) }),
  createElement: () => el(),
};
require(process.argv[2]);
const shown = section.hidden === false;
clicks.forEach((fn) => fn());
console.log(JSON.stringify({ shown, store }));
"""

    def run_script(self, name, store):
        script = Path(settings.BASE_DIR) / "web" / "static" / "js" / name
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as harness:
            harness.write(self.HARNESS)
        self.addCleanup(os.unlink, harness.name)
        done = subprocess.run(["node", harness.name, str(script), json.dumps(store)],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_games_only_history_still_offers_clear_history(self):
        result = self.run_script("history.js", {"ripraptor.games": json.dumps(["magic-the-gathering"])})
        self.assertTrue(result["shown"])
        self.assertNotIn("ripraptor.games", result["store"])   # Clear history forgot it

    def test_odd_values_in_storage_never_stop_either_script(self):
        odd = {"ripraptor.games": json.dumps("notalist"), "ripraptor.viewed": json.dumps([None, 5, {"slug": "x"}]),
               "ripraptor.saved": json.dumps({"a": 1}), "ripraptor.searches": json.dumps([None, 7, "mtg"])}
        for name in ("interest.js", "history.js"):
            with self.subTest(name=name):
                self.run_script(name, odd)
