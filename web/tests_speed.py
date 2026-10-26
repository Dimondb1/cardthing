"""Pages ready before the tap: whole game and set pages kept in the cache and warmed, pages rendered ahead of a
tap in Chrome without miscounting visits, and taps that answer at once."""

import json

from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import DailyPageView, DailySearch
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import pagecache, warming

BROWSER = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"


def static_text(path):
    with open(finders.find(path), encoding="utf-8") as handle:
        return handle.read()


@override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
class PageCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.product_set = make_set(cls.game)
        cls.product = make_product(cls.product_set)
        make_listing(cls.product, make_retailer())

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_the_second_visitor_is_served_the_page_the_first_one_built(self):
        url = self.game.get_absolute_url()
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertNotIn(pagecache.HEADER, first)
        with self.assertNumQueries(0):
            second = self.client.get(url)
        self.assertEqual(second[pagecache.HEADER], "cached")
        self.assertEqual(second.content, first.content)
        self.assertEqual(second["Content-Type"], first["Content-Type"])
        # Each tab's list and each set of filters is its own page.
        self.assertNotIn(pagecache.HEADER, self.client.get(url + "?tab=out-of-stock"))
        self.assertNotIn(pagecache.HEADER, self.client.get(url + "?type=booster_box"))
        pane = self.client.get(url + "?tab=out-of-stock&pane=1")
        self.assertEqual(self.client.get(url + "?tab=out-of-stock&pane=1")["X-Robots-Tag"], pane["X-Robots-Tag"])

    def test_set_pages_are_kept_too_and_product_pages_never(self):
        for url, kept in ((self.product_set.get_absolute_url(), True), (self.product.get_absolute_url(), False)):
            with self.subTest(url):
                self.client.get(url)
                self.assertEqual(self.client.get(url).get(pagecache.HEADER) == "cached", kept)

    def test_a_visitor_on_a_cached_page_still_counts(self):
        url = self.game.get_absolute_url()
        for _ in range(2):
            self.client.get(url, HTTP_USER_AGENT=BROWSER)
        self.assertEqual(DailyPageView.objects.get(kind="game", key=self.game.slug).hits, 2)

    @override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=0)
    def test_nothing_is_kept_when_turned_off(self):
        url = self.game.get_absolute_url()
        self.client.get(url)
        self.assertNotIn(pagecache.HEADER, self.client.get(url))
        self.assertEqual(pagecache.game_paths([self.game]), [])
        self.assertNotIn(f"page:{url}", warming.missing())

    def test_the_owners_change_forgets_every_kept_page(self):
        from web import listcache

        url = self.game.get_absolute_url()
        self.client.get(url)
        self.assertEqual(self.client.get(url)[pagecache.HEADER], "cached")
        listcache.mark_stale(force=True, hard=True)
        self.assertNotIn(pagecache.HEADER, self.client.get(url))
        self.assertEqual(self.client.get(url)[pagecache.HEADER], "cached")

    def test_warming_builds_each_games_page_once(self):
        path = self.game.get_absolute_url()
        self.assertIn(f"page:{path}", warming.missing())
        self.assertIn(f"page:{path}", warming.due())
        took = warming.warm_pages()
        self.assertIn(f"page: {path}", took)
        self.assertTrue(pagecache.has(path))
        self.assertNotIn(f"page:{path}", warming.missing())
        self.assertNotIn(f"page:{path}", warming.due())
        self.assertNotIn(f"page: {path}", warming.warm_pages())   # kept: not built again
        self.assertIn(f"page: {path}", warming.warm_pages(fresh=True))
        with self.assertNumQueries(0):
            page = self.client.get(path)
        self.assertEqual(page[pagecache.HEADER], "cached")
        # Built for the site's own address, so its links are the site's, and marked as the game page.
        from django.conf import settings

        html = page.content.decode()
        self.assertIn(f'<link rel="canonical" href="{settings.RIPRAPTOR_SITE_URL}/games/pokemon/">', html)
        self.assertIn('data-page="game"', html)
        self.assertNotIn("testserver:", html)


class AheadOfATapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.product = make_product(make_set(cls.game))
        make_listing(cls.product, make_retailer())

    def test_public_pages_say_which_pages_may_be_rendered_ahead(self):
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<script type="speculationrules">', html)
        rules = json.loads(html.split('<script type="speculationrules">')[1].split("</script>")[0])
        rule = rules["prerender"][0]
        self.assertEqual(rule["eagerness"], "moderate")
        self.assertEqual(rule["where"]["and"][0], {"href_matches": "/*"})
        excluded = rule["where"]["and"][1]["not"]["href_matches"]
        for path in ("/go/*", "/admin/*", "/search/*", "/swipe/*", "/watchlist/*", "/alerts/*", "/contact/*",
                     "/refer/*", "/r/*", "/feeds/*", "/api/*", "/dashboard/*"):
            self.assertIn(path, excluded)

    def test_a_private_page_has_none(self):
        from inbox.models import Conversation

        thread = Conversation.objects.create()
        self.assertNotIn("speculationrules", self.client.get(thread.get_absolute_url()).content.decode())

    def test_a_page_fetched_ahead_is_not_a_view_until_it_is_shown(self):
        url = self.game.get_absolute_url()
        self.client.get(url, HTTP_USER_AGENT=BROWSER, HTTP_SEC_PURPOSE="prefetch;prerender")
        self.client.get(url + "?tab=out-of-stock&pane=1", HTTP_USER_AGENT=BROWSER)
        self.assertFalse(DailyPageView.objects.filter(kind="game").exists())
        note = reverse("web:note_api")
        self.assertEqual(self.client.get(note + f"?what=view&path={url}", HTTP_USER_AGENT=BROWSER).status_code, 204)
        self.assertEqual(DailyPageView.objects.get(kind="game", key=self.game.slug).hits, 1)
        self.client.get(note + f"?what=view&path={self.product.get_absolute_url()}", HTTP_USER_AGENT=BROWSER)
        self.assertEqual(DailyPageView.objects.get(kind="product", key=self.product.slug).hits, 1)
        # Not a robot's, not a path that is no page, and not a note sent ahead of a tap itself.
        self.client.get(note + f"?what=view&path={url}", HTTP_USER_AGENT="Googlebot/2.1")
        self.client.get(note + "?what=view&path=/no/such/page/", HTTP_USER_AGENT=BROWSER)
        self.client.get(note + "?what=view&path=/admin/", HTTP_USER_AGENT=BROWSER)
        self.client.get(note + f"?what=view&path={url}", HTTP_USER_AGENT=BROWSER, HTTP_SEC_PURPOSE="prefetch")
        self.assertEqual(DailyPageView.objects.get(kind="game", key=self.game.slug).hits, 1)
        self.assertEqual(DailyPageView.objects.count(), 2)

    def test_a_search_fetched_ahead_is_not_recorded(self):
        self.client.get(reverse("web:search") + "?q=box", HTTP_USER_AGENT=BROWSER, HTTP_SEC_PURPOSE="prefetch")
        self.assertFalse(DailySearch.objects.exists())
        self.client.get(reverse("web:search") + "?q=box", HTTP_USER_AGENT=BROWSER)
        self.assertTrue(DailySearch.objects.exists())

    def test_the_scripts_wait_until_the_page_is_shown(self):
        site = static_text("js/site.js")
        self.assertIn('document.prerendering || (entry && entry.deliveryType === "navigational-prefetch")', site)
        self.assertIn("what=view&path=", site)
        self.assertIn('document.addEventListener("prerenderingchange", count, { once: true })', site)
        for path, name in (("js/history.js", "remember"), ("js/install.js", "offer")):
            script = static_text(path)
            self.assertIn(f'if (document.prerendering) document.addEventListener("prerenderingchange", {name}, {{ once: true }});', script)
            self.assertIn(f"else {name}();", script)


class TapTests(TestCase):
    def test_lists_do_not_fade_their_cards_in(self):
        site = static_text("js/site.js")
        self.assertIn('document.querySelectorAll(".saving, .drop, .trending__item")', site)
        self.assertNotIn(".card,", site[site.index("One-time reveals"):site.index("One-time reveals") + 400])

    def test_a_tap_answers_at_once(self):
        self.assertIn("a, button, summary, select, input, label { touch-action: manipulation; }", static_text("css/site.css"))
