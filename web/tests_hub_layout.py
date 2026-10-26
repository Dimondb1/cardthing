"""Game and set pages kept simple: they open on a list with products, show only the tabs with something in
them (none for one list), fold sort and filters away (none for a short list), say what they hold in one line
under the heading, and keep the row of sets to game pages."""

import re
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .tests import CopyStyleTests
from .tests_hubs import by_type, visible
from .views import FILTERS_FROM

MAIL_ON = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com"}
PRE, OUT, STOCK = Listing.Availability.PREORDER, Listing.Availability.OUT_OF_STOCK, Listing.Availability.IN_STOCK


def main_text(html):
    """The words of the page's main content: no title, no structured data, no tags."""
    main = html[html.index("<main"):html.index("</main>")]
    return visible(re.sub(r"(?s)<script\b.*?</script>", " ", main))


def names(html):
    return re.findall(r'class="card__name"><a href="[^"]+">([^<]+)</a>', html)


def tab_keys(html):
    """The tabs in the tab row, in order."""
    if 'class="stock-tabs' not in html:
        return []
    row = html[html.index('class="stock-tabs'):html.index("</nav>", html.index('class="stock-tabs'))]
    return re.findall(r'data-tab="([a-z-]+)"', row)


class HubLayoutTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh", short_name="Yu-Gi-Oh")
        self.a, self.b, self.c = make_retailer("Shop A"), make_retailer("Shop B"), make_retailer("Shop C")

    def product(self, product_set, name, *offers, kind=Product.Type.BOOSTER_BOX):
        """A product with one listing per (retailer, availability) in ``offers``."""
        product = make_product(product_set, name=name, slug=re.sub(r"[^a-z0-9]+", "-", name.lower()),
                               product_type=kind)
        for retailer, availability in offers:
            make_listing(product, retailer, price="60.00", availability=availability)
        return product

    def get(self, url):
        return self.client.get(url).content.decode()


class OwnersCaseTests(HubLayoutTestCase):
    """The page the owner sent: a set still to come with no date, one product on pre-order at two shops."""

    def setUp(self):
        super().setUp()
        self.maestros = make_set(self.game, name="Magnificent Maestros", slug="magnificent-maestros", code="")
        self.box = self.product(self.maestros, "Magnificent Maestros Booster Box", (self.a, PRE), (self.b, PRE))
        for n in range(3):
            older = make_set(self.game, name=f"Older Set {n}", slug=f"older-set-{n}", code="",
                             release_date=timezone.localdate() - timedelta(days=100 + n))
            self.product(older, f"Older Set {n} Booster Box", (self.a, STOCK))
        self.url = self.maestros.get_absolute_url()

    def test_it_opens_on_the_one_list_with_no_tabs_filters_or_set_row(self):
        html = self.get(self.url)
        self.assertEqual(names(html), ["Magnificent Maestros Booster Box"])
        self.assertEqual(tab_keys(html), [])
        self.assertNotIn('class="stock-tabs', html)
        self.assertIn("tabs.js", html)   # loaded all the same, for the tab row a filter change can bring in
        self.assertNotIn('<form class="filters"', html)
        self.assertNotIn("Sort and filter", html)
        self.assertNotIn("set-nav", html)
        self.assertNotIn("Older Set", visible(html))
        self.assertNotIn("Nothing here is in stock right now.", html)
        # The list says what it is, since no tab does.
        self.assertIn('<h2 class="stock-pane__title" id="pane-pre-orders">On pre-order</h2>', html)
        # One product needs no word about its order.
        self.assertNotIn("Soonest out first.", html)

    def test_one_line_says_it_all_with_the_date_merged_in(self):
        html = self.get(self.url)
        self.assertIn('<p class="browse-head__intro">1 sealed product, on pre-order at 2 UK shops. '
                      'Release date not announced yet.</p>', html)
        text = main_text(html)
        self.assertEqual(text.count("not announced yet"), 1)
        self.assertNotIn("None is in stock", text)
        self.assertNotIn("tabs below", text)
        # The line counts the products, so no second count.
        self.assertNotIn("browse-head__count", html)
        self.assertNotIn("1 product ", text)
        # A quiet way back to the game, once besides the breadcrumb.
        head = html[html.index('<div class="browse-head">'):html.index("</div>", html.index('<div class="browse-head">'))]
        self.assertIn('<p class="browse-head__meta"><a href="/games/yu-gi-oh/">All Yu-Gi-Oh products</a></p>', head)

    @override_settings(**MAIL_ON)
    def test_the_alert_link_comes_after_the_list(self):
        html = self.get(self.url)
        self.assertIn('<p class="browse-alert">', html)
        self.assertGreater(html.index('class="browse-alert"'), html.index('class="cards"'))
        self.assertGreater(html.index('class="browse-alert"'), html.index('class="stock-panes"'))

    def test_the_page_carries_its_item_list_on_the_list_it_opens_on(self):
        for query in ("", "?tab=pre-orders"):
            with self.subTest(query=query):
                self.assertEqual(set(by_type(self.get(self.url + query))), {"BreadcrumbList", "CollectionPage"})
        self.assertEqual(set(by_type(self.get(self.url + "?tab=in-stock"))), {"BreadcrumbList"})

    def test_a_tab_named_in_the_address_still_wins(self):
        html = self.get(self.url + "?tab=in-stock")
        self.assertIn("Nothing here is in stock right now.", html)
        self.assertEqual(names(html), [])
        # The tab in view stays in the row, beside the one with products, whose link needs no tab.
        self.assertEqual(tab_keys(html), ["in-stock", "pre-orders"])
        self.assertIn('href="?" data-tab="pre-orders"', html)
        self.assertIn('data-src="?tab=pre-orders&amp;pane=1"', html)


class DefaultTabTests(HubLayoutTestCase):
    def setUp(self):
        super().setUp()
        self.mixed = make_set(self.game, name="Duelist Archive", slug="duelist-archive", code="DA01",
                              release_date=timezone.localdate() - timedelta(days=20))
        self.url = self.mixed.get_absolute_url()

    def test_with_nothing_in_stock_it_opens_on_pre_orders_and_hides_the_empty_tab(self):
        self.product(self.mixed, "Duelist Archive Tin", (self.a, PRE), kind=Product.Type.TIN)
        self.product(self.mixed, "Duelist Archive Box", (self.a, OUT))
        html = self.get(self.url)
        self.assertEqual(names(html), ["Duelist Archive Tin"])
        self.assertEqual(tab_keys(html), ["pre-orders", "out-of-stock"])
        self.assertIn('class="stock-tabs stock-tabs--two"', html)
        self.assertIn('data-tab="pre-orders" aria-current="page"', html)
        self.assertIn("tabs.js", html)
        # The tab it opens on needs no tab in its address; the other names its own.
        self.assertIn('href="?" data-tab="pre-orders"', html)
        self.assertIn('href="?tab=out-of-stock" data-tab="out-of-stock"', html)
        self.assertIn('data-opening="pre-orders"', html)
        self.assertIn("2 sealed products, 1 on pre-order at 1 UK shop.", html)

    def test_with_only_sold_out_products_it_opens_on_out_of_stock(self):
        self.product(self.mixed, "Duelist Archive Box", (self.a, OUT))
        html = self.get(self.url)
        self.assertEqual(names(html), ["Duelist Archive Box"])
        self.assertEqual(tab_keys(html), [])
        self.assertIn('<h2 class="stock-pane__title" id="pane-out-of-stock">Out of stock</h2>', html)
        self.assertIn("1 sealed product compared across 1 UK shop, none in stock right now.", html)
        # A released set gives its code and date in the small print.
        self.assertIn("DA01<span", html)
        self.assertIn(f"Released {self.mixed.release_date:%-d %b %Y}", html)

    def test_an_in_stock_list_alone_has_no_tabs_and_no_heading_to_show(self):
        self.product(self.mixed, "Duelist Archive Box", (self.a, STOCK))
        html = self.get(self.url)
        self.assertEqual(tab_keys(html), [])
        self.assertNotIn("stock-pane__title", html)
        self.assertIn('<h2 class="visually-hidden" id="pane-in-stock">In stock</h2>', html)

    def test_every_tab_shows_while_each_has_products(self):
        self.product(self.mixed, "Duelist Archive Box", (self.a, STOCK))
        self.product(self.mixed, "Duelist Archive Tin", (self.a, PRE), kind=Product.Type.TIN)
        self.product(self.mixed, "Duelist Archive Pack", (self.a, OUT), kind=Product.Type.BOOSTER_PACK)
        html = self.get(self.url)
        self.assertEqual(tab_keys(html), ["in-stock", "pre-orders", "out-of-stock"])
        self.assertNotIn("stock-tabs--two", html)
        self.assertEqual(names(html), ["Duelist Archive Box"])

    def test_an_empty_set_says_so_without_tabs(self):
        html = self.get(self.url)
        self.assertEqual(tab_keys(html), [])
        self.assertIn("Nothing here is in stock right now.", html)
        # With no line under the heading, the count stays.
        self.assertIn('<p class="browse-head__count" role="status">0 products</p>', html)

    @override_settings(**MAIL_ON)
    def test_no_alert_link_under_an_empty_list(self):
        self.assertNotIn("browse-alert", self.get(self.url))
        self.product(self.mixed, "Duelist Archive Box", (self.a, STOCK))
        self.assertIn("browse-alert", self.get(self.url))
        self.assertNotIn("browse-alert", self.get(self.url + "?tab=pre-orders"))

    @override_settings(**MAIL_ON)
    def test_a_set_still_to_come_keeps_its_alert_link_before_anything_is_listed(self):
        coming = make_set(self.game, name="Phantom Tides", slug="phantom-tides", code="PHT",
                          release_date=timezone.localdate() + timedelta(days=30))
        make_product(coming, name="Phantom Tides Booster Box", slug="phantom-tides-box")
        html = self.get(coming.get_absolute_url())
        self.assertIn("browse-alert", html)
        # The date is said once, in the line under the heading, with where it came from in the small print.
        self.assertEqual(main_text(html).count(f"{coming.release_date:%-d %b %Y}"), 1)
        self.assertIn("due out on", html)


class FilterTests(HubLayoutTestCase):
    def setUp(self):
        super().setUp()
        self.big = make_set(self.game, name="Duelist Archive", slug="duelist-archive", code="DA01")
        self.url = self.big.get_absolute_url()

    def fill(self, count, availability=STOCK):
        for n in range(count):
            self.product(self.big, f"Duelist Archive Box {availability} {n}", (self.a, availability))

    def test_a_short_list_has_no_sort_or_filters(self):
        self.fill(FILTERS_FROM - 1)
        html = self.get(self.url)
        self.assertNotIn('<form class="filters"', html)
        self.assertNotIn("filter-panel", html)

    def test_a_long_list_folds_them_under_one_button(self):
        self.fill(FILTERS_FROM)
        html = self.get(self.url)
        self.assertIn('<details class="filter-panel" data-filter-panel>', html)
        self.assertIn('<summary class="filter-panel__toggle"><span>Sort and filter</span></summary>', html)
        panel = html[html.index('<details class="filter-panel"'):html.index("</details>")]
        self.assertIn('<form class="filters"', panel)
        self.assertIn('name="sort"', panel)
        # Without script the form still works: its button is in the page.
        self.assertIn("Show results", panel)

    def test_the_list_in_view_decides(self):
        self.fill(3)
        self.fill(FILTERS_FROM, availability=OUT)
        self.assertNotIn("filter-panel", self.get(self.url))
        self.assertIn('<details class="filter-panel" data-filter-panel>', self.get(self.url + "?tab=out-of-stock"))

    def test_a_chosen_filter_or_sort_shows_the_panel_closed_naming_it_whatever_the_length(self):
        self.fill(2)
        for query, named in (("?type=booster_box", "Booster box"), ("?sort=price", "Lowest price"),
                             ("?compared=on", "2+ shops"), ("?lang=en", "English")):
            with self.subTest(query=query):
                html = self.get(self.url + query)
                # Closed, so a page reached with filters chosen opens on its list; the button says what is chosen.
                self.assertIn('<details class="filter-panel" data-filter-panel>', html)
                self.assertIn(f'<span class="filter-panel__chosen">{named}</span>', html)
                self.assertIn("Clear filters", html)
                # The list it narrowed is counted, beside the line about the whole set.
                self.assertIn('class="browse-head__count" role="status"', html)

    def test_clear_filters_keeps_a_tab_only_when_the_address_named_it(self):
        self.fill(2)
        self.assertIn('<a class="filters__clear filter-bar__clear" href="?" data-tab-link>', self.get(self.url + "?sort=price"))
        self.assertIn('<a class="filters__clear filter-bar__clear" href="?tab=out-of-stock" data-tab-link>',
                      self.get(self.url + "?sort=price&tab=out-of-stock"))

    def test_the_filters_are_an_even_grid_across_the_whole_row(self):
        # The owner: "a cluster" of pills of every width. Two equal ones to a row on a phone, as many as fit wider,
        # and the open panel takes the row's whole width, Clear filters sitting over its corner.
        from django.contrib.staticfiles import finders

        with open(finders.find("css/site.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertIn(".filters:not(.filters--deck) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }", css)
        self.assertIn("grid-template-columns: repeat(auto-fill, minmax(var(--filter-cell), 1fr));", css)
        self.assertIn(".filter-bar .filter-panel { grid-area: 1 / 1 / 2 / 3;", css)
        self.assertIn(".filter-bar__clear { grid-area: 1 / 2;", css)

    def test_search_keeps_its_filters_in_view(self):
        self.fill(2)
        html = self.get(reverse("web:search") + "?q=duelist")
        self.assertIn('<form class="filters"', html)
        self.assertNotIn("filter-panel", html)


class GameSetRowTests(HubLayoutTestCase):
    """The game page's row of sets follows the language and type in view, and each set keeps them."""

    def setUp(self):
        super().setUp()
        self.english = make_set(self.game, name="Duelist Archive", slug="duelist-archive", code="DA01")
        self.product(self.english, "Duelist Archive Box", (self.a, STOCK))
        self.korean = make_set(self.game, name="Korean Pack Set", slug="korean-pack-set", code="")
        box = self.product(self.korean, "Korean Pack Set Box", (self.a, STOCK))
        Product.objects.filter(pk=box.pk).update(language="ko")
        self.url = self.game.get_absolute_url()

    def sets(self, query=""):
        html = self.get(self.url + query)
        row = html[html.index('<nav class="set-nav"'):html.index("</nav>", html.index('<nav class="set-nav"'))]
        return re.findall(r'<a href="([^"]+)">([^<]+)<span class="set-nav__count', row)

    def counts(self, query=""):
        html = self.get(self.url + query)
        row = html[html.index('<nav class="set-nav"'):html.index("</nav>", html.index('<nav class="set-nav"'))]
        return dict(re.findall(r'>([^<]+)<span class="set-nav__count[^"]*"><span aria-hidden="true">(\d+)</span>', row))

    def test_each_set_counts_its_products_in_stock_in_the_language_and_type_chosen(self):
        # The owner: "can it show the number in stock in each button". Only in stock: not pre-orders, not sold out.
        self.product(self.english, "Duelist Archive Tin", (self.a, STOCK), kind=Product.Type.TIN)
        self.product(self.english, "Duelist Archive Deck", (self.a, PRE))
        self.product(self.english, "Duelist Archive Pack", (self.a, OUT))
        korean = self.product(self.english, "Duelist Archive Korean Box", (self.a, STOCK))
        Product.objects.filter(pk=korean.pk).update(language="ko")
        self.assertEqual(self.counts(), {"Duelist Archive": "3", "Korean Pack Set": "1"})
        self.assertEqual(self.counts("?lang=ko"), {"Duelist Archive": "1", "Korean Pack Set": "1"})
        self.assertEqual(self.counts("?type=tin"), {"Duelist Archive": "1"})
        html = self.get(self.url)
        self.assertIn('<span class="visually-hidden">, 3 in stock</span>', html)
        # A set with none in stock still shows, at 0 with a grey dot: people looking for it can see it is out.
        Listing.objects.filter(product__product_set=self.korean).update(availability=OUT)
        self.assertEqual(self.counts()["Korean Pack Set"], "0")
        self.assertIn('<span class="set-nav__count set-nav__count--none"><span aria-hidden="true">0</span>', self.get(self.url))

    def test_the_sets_people_want_this_week_come_first(self):
        from datetime import date

        from catalogue.models import DailyPageView, OutboundClick

        self.english.release_date, self.korean.release_date = date(2025, 1, 1), date(2024, 1, 1)
        self.english.save()
        self.korean.save()
        # Newest first while nobody has looked.
        self.assertEqual([name for _, name in self.sets()], ["Duelist Archive", "Korean Pack Set"])
        # A click counts four views: three views of the newer set lose to one click on the older one.
        today = timezone.localdate()
        DailyPageView.objects.create(kind=DailyPageView.Kind.PRODUCT, key="duelist-archive-box", date=today, hits=3)
        OutboundClick.objects.create(product=Product.objects.get(slug="korean-pack-set-box"),
                                     retailer=self.a, source="game")
        cache.clear()
        self.assertEqual([name for _, name in self.sets()], ["Korean Pack Set", "Duelist Archive"])
        # Views older than the week do not count.
        DailyPageView.objects.create(kind=DailyPageView.Kind.PRODUCT, key="duelist-archive-box",
                                     date=today - timedelta(days=8), hits=50)
        cache.clear()
        self.assertEqual([name for _, name in self.sets()], ["Korean Pack Set", "Duelist Archive"])

    def test_the_demand_is_two_queries_however_many_sets(self):
        from catalogue import popularity

        more = [make_set(self.game, name=f"Other {n}", slug=f"other-{n}", code="") for n in range(5)]
        with self.assertNumQueries(2):
            popularity.set_demand([self.english.pk, self.korean.pk] + [item.pk for item in more])

    def test_the_sets_follow_the_language_and_type_chosen(self):
        self.assertEqual({name for _, name in self.sets()}, {"Duelist Archive", "Korean Pack Set"})
        self.assertEqual([name for _, name in self.sets("?lang=ko")], ["Korean Pack Set"])
        self.assertEqual([name for _, name in self.sets("?lang=en")], ["Duelist Archive"])
        tin = self.product(self.english, "Duelist Archive Tin", (self.a, STOCK), kind=Product.Type.TIN)
        self.assertEqual([name for _, name in self.sets("?type=tin")], ["Duelist Archive"])
        tin.delete()
        self.assertNotIn("set-nav", self.get(self.url + "?type=tin"))

    def test_a_set_keeps_the_language_type_and_sort_not_the_price_or_shops(self):
        # Nothing chosen: plain addresses.
        self.assertEqual({href for href, _ in self.sets()},
                         {self.english.get_absolute_url(), self.korean.get_absolute_url()})
        [(href, name)] = self.sets("?lang=ko&type=booster_box&sort=price&price=50-100&compared=on&page=2")
        self.assertEqual(href, self.korean.get_absolute_url() + "?lang=ko&amp;type=booster_box&amp;sort=price")
        # A value the form does not take is not passed on.
        [(href, _)] = self.sets("?lang=ko&sort=nonsense")
        self.assertEqual(href, self.korean.get_absolute_url() + "?lang=ko")
        # The set page it opens holds what was looked for, its panel closed and naming it.
        html = self.get(self.korean.get_absolute_url() + "?lang=ko&type=booster_box")
        self.assertIn("Korean Pack Set Box", html)
        self.assertIn('<span class="filter-panel__chosen">Booster box · Korean</span>', html)
        self.assertNotIn("data-filter-panel open", html)

    def test_a_filter_change_carries_the_tab_only_when_the_address_named_it(self):
        # The form's tab stays empty on the tab the page opened on, so a filter that empties it opens the next.
        self.product(self.english, "Duelist Archive Tin", (self.a, PRE), kind=Product.Type.TIN)
        self.assertIn('name="tab" value="" data-tab-input', self.get(self.url + "?sort=price"))
        self.assertIn('name="tab" value="pre-orders" data-tab-input', self.get(self.url + "?sort=price&tab=pre-orders"))

    def test_no_separate_count_line_beside_the_stock_tabs(self):
        self.product(self.english, "Duelist Archive Tin", (self.a, PRE), kind=Product.Type.TIN)
        html = self.get(self.url + "?lang=en")
        self.assertIn('class="stock-tabs', html)
        self.assertNotIn("browse-head__count", html)


class IntroWordingTests(HubLayoutTestCase):
    def setUp(self):
        super().setUp()
        self.archive = make_set(self.game, name="Duelist Archive", slug="duelist-archive", code="",
                                release_date=timezone.localdate() - timedelta(days=20))
        self.url = self.archive.get_absolute_url()

    def intro(self):
        html = self.get(self.url)
        return re.search(r'<p class="browse-head__intro">(.*?)</p>', html, re.S).group(1)

    def test_everything_on_pre_order_counts_the_shops_taking_them(self):
        self.product(self.archive, "Duelist Archive Box", (self.a, PRE), (self.b, PRE), (self.c, OUT))
        self.assertEqual(self.intro(), "1 sealed product, on pre-order at 2 UK shops.")

    def test_some_on_pre_order_says_how_many(self):
        self.product(self.archive, "Duelist Archive Box", (self.a, PRE))
        self.product(self.archive, "Duelist Archive Tin", (self.b, OUT), kind=Product.Type.TIN)
        self.product(self.archive, "Duelist Archive Pack", kind=Product.Type.BOOSTER_PACK)
        self.assertEqual(self.intro(), "3 sealed products, 1 on pre-order at 1 UK shop.")

    def test_a_stale_pre_order_is_not_one(self):
        product = self.product(self.archive, "Duelist Archive Box", (self.b, OUT))
        make_listing(product, self.a, availability=PRE, hours_ago=24 * 30)
        self.assertEqual(self.intro(), "1 sealed product compared across 1 UK shop, none in stock right now.")

    def test_in_stock_keeps_the_cheapest_line(self):
        self.product(self.archive, "Duelist Archive Box", (self.a, STOCK))
        # One short line now: the cheapest in stock is a card of its own under it (web/tests_hubs.py).
        self.assertEqual(self.intro(), "1 sealed product compared across 1 UK shop.")
        self.assertIn('class="cheapest-card"', self.get(self.url))

    def test_hub_numbers_still_take_three_queries(self):
        from . import hubs

        self.product(self.archive, "Duelist Archive Box", (self.a, PRE))
        self.product(self.archive, "Duelist Archive Tin", (self.b, STOCK), kind=Product.Type.TIN)
        with self.assertNumQueries(3):
            stats = hubs.hub_stats(product_set=self.archive)
        self.assertEqual((stats.products, stats.in_stock, stats.preorder), (2, 1, 1))


class SetPageQueryTests(HubLayoutTestCase):
    def test_a_set_page_reads_no_other_sets(self):
        archive = make_set(self.game, name="Duelist Archive", slug="duelist-archive", code="DA01")
        self.product(archive, "Duelist Archive Box", (self.a, STOCK))

        def queries():
            self.client.get(archive.get_absolute_url())   # the cached lists are built once
            with CaptureQueriesContext(connection) as captured:
                self.client.get(archive.get_absolute_url())
            return [query["sql"] for query in captured.captured_queries]

        few = queries()
        for n in range(6):
            other = make_set(self.game, name=f"Other {n}", slug=f"other-{n}", code="")
            self.product(other, f"Other {n} Box", (self.a, STOCK))
        many = queries()
        self.assertEqual(len(many), len(few))
        # The row of the game's sets went with the strip: no query lists them. The game page's row still does.
        row = '"catalogue_productset"."id" IN (SELECT'
        self.assertFalse([sql for sql in many if row in sql])
        with CaptureQueriesContext(connection) as game_page:
            self.client.get(self.game.get_absolute_url())
        self.assertEqual(len([query for query in game_page.captured_queries if row in query["sql"]]), 1)


class HubLayoutCopyStyleTests(HubLayoutTestCase):
    def test_every_state_keeps_the_house_style(self):
        coming = make_set(self.game, name="Magnificent Maestros", slug="magnificent-maestros", code="")
        self.product(coming, "Magnificent Maestros Booster Box", (self.a, PRE), (self.b, PRE))
        mixed = make_set(self.game, name="Duelist Archive", slug="duelist-archive", code="DA01")
        for n in range(FILTERS_FROM):
            self.product(mixed, f"Duelist Archive Box {n}", (self.a, STOCK))
        self.product(mixed, "Duelist Archive Tin", (self.a, PRE), kind=Product.Type.TIN)
        sold = make_set(self.game, name="Old Archive", slug="old-archive", code="")
        self.product(sold, "Old Archive Box", (self.a, OUT))
        urls = [coming.get_absolute_url(), mixed.get_absolute_url(), mixed.get_absolute_url() + "?sort=price",
                mixed.get_absolute_url() + "?tab=pre-orders", sold.get_absolute_url(), self.game.get_absolute_url(),
                reverse("web:games") + "#game-yu-gi-oh"]
        with override_settings(**MAIL_ON):
            for url in urls:
                html = self.get(url)
                with self.subTest(url=url):
                    self.assertNotIn("!", visible(html).replace("Yu-Gi-Oh!", ""))
                    for phrase in CopyStyleTests.BANNED:
                        self.assertNotIn(phrase, html.lower())
