"""Game and set pages as hubs: titles, descriptions and the line under the heading built from the catalogue,
the product pages' titles and descriptions, and the structured data on all three."""

import json
import re
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import hubs
from .tests import CopyStyleTests, PageTestCase

LD_START = '<script type="application/ld+json">'


def title_of(html):
    return re.search(r"<title>(.*?)</title>", html, re.S).group(1)


def description_of(html):
    return re.search(r'<meta name="description" content="(.*?)">', html, re.S).group(1)


def blocks_of(html):
    start = html.index(LD_START) + len(LD_START)
    data = json.loads(html[start:html.index("</script>", start)])
    return data if isinstance(data, list) else [data]


def by_type(html):
    return {block["@type"]: block for block in blocks_of(html)}


def visible(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html))


class GameHubTests(PageTestCase):
    """PageTestCase's Pokémon: an ETB in stock at two shops (£54.99 delivered cheapest at Harbour Games), a
    collection sold out at Harbour Games and a bundle no shop lists."""

    def test_title_names_the_types_in_stock(self):
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertEqual(title_of(html), "Cheapest Pokémon ETBs in stock: UK price comparison | RipRaptor")

    def test_description_quotes_real_numbers(self):
        description = description_of(self.client.get(self.game.get_absolute_url()).content.decode())
        self.assertEqual(
            description,
            "1 sealed Pokémon product in stock at 2 UK shops, from £54.99 delivered. Best price first, with most "
            "shops checked every hour.",
        )
        self.assertGreaterEqual(len(description), 110)
        self.assertLessEqual(len(description), 160)

    def test_description_counts_in_the_plural(self):
        second = make_product(self.pre, name="Prismatic Evolutions Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(second, make_retailer("Third Shop"), price="200.00")
        description = description_of(self.client.get(self.game.get_absolute_url()).content.decode())
        self.assertTrue(description.startswith("2 sealed Pokémon products in stock at 3 UK shops, from £54.99"))

    def test_intro_says_how_many_and_links_the_cheapest(self):
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn(
            '3 sealed products compared across 2 UK shops, 1 in stock now. Cheapest in stock: '
            f'<a href="{self.etb.get_absolute_url()}">{self.etb.name}</a>, £54.99 delivered at Harbour Games.',
            html,
        )

    def test_the_cheapest_is_in_stock_not_a_pre_order(self):
        cheap_preorder = make_product(self.pre, name="Prismatic Evolutions Tin", product_type=Product.Type.TIN)
        make_listing(cheap_preorder, self.harbour, price="10.00", availability=Listing.Availability.PREORDER)
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn("from £54.99 delivered", description_of(html))
        self.assertIn("£54.99 delivered at Harbour Games.", html)
        self.assertNotIn("£10.00 delivered at", html)

    def test_stale_and_kept_out_prices_do_not_count(self):
        stale = make_product(self.pre, name="Prismatic Evolutions Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(stale, self.harbour, price="5.00", hours_ago=24 * 30)
        excluded = make_product(self.pre, name="Prismatic Evolutions Mini Tin", product_type=Product.Type.TIN)
        make_listing(excluded, self.harbour, price="1.00", sanity=Listing.Sanity.EXCLUDED)
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn("1 in stock now", html)
        self.assertIn("from £54.99 delivered", description_of(html))
        self.assertTrue(title_of(html).startswith("Cheapest Pokémon ETBs in stock"))

    def test_nothing_in_stock_makes_no_stock_or_cheapest_claim(self):
        Listing.objects.filter(product=self.etb).update(availability=Listing.Availability.OUT_OF_STOCK)
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertEqual(title_of(html), "Pokémon sealed products: UK price comparison | RipRaptor")
        description = description_of(html)
        self.assertNotIn("in stock at", description)
        self.assertNotIn("Cheapest", description)
        self.assertIn("3 sealed products compared across 2 UK shops. None is in stock right now", html)
        self.assertNotIn("Cheapest in stock", html)

    def test_a_game_no_shop_lists(self):
        game = make_game(name="Riftbound", slug="riftbound")
        make_product(make_set(game, name="Origins", slug="origins"), name="Origins Booster Box",
                     product_type=Product.Type.BOOSTER_BOX)
        html = self.client.get(game.get_absolute_url()).content.decode()
        self.assertIn("1 sealed product, not yet listed by a UK shop we check.", html)
        self.assertEqual(title_of(html), "Riftbound sealed products: UK price comparison | RipRaptor")

    def test_an_empty_game_has_no_intro(self):
        game = make_game(name="Riftbound", slug="riftbound")
        html = self.client.get(game.get_absolute_url()).content.decode()
        self.assertNotIn("compared across", html)
        self.assertNotIn("sealed product,", html)

    def test_title_names_as_many_types_as_fit(self):
        for name, kind in (("Prismatic Evolutions Booster Box", Product.Type.BOOSTER_BOX),
                           ("Prismatic Evolutions Booster Bundle Two", Product.Type.BUNDLE),
                           ("Prismatic Evolutions Tin", Product.Type.TIN)):
            make_listing(make_product(self.pre, name=name, product_type=kind), self.harbour, price="30.00")
        title = title_of(self.client.get(self.game.get_absolute_url()).content.decode())
        # In the game's own order (booster box before ETB), cut to what fits before the site name.
        self.assertEqual(title, "Cheapest Pokémon booster boxes in stock: UK price comparison | RipRaptor")
        self.assertLessEqual(len(title) - len(" | RipRaptor"), hubs.TITLE_LIMIT)

    def test_the_game_short_name_keeps_titles_free_of_exclamation_marks(self):
        game = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh", short_name="Yu-Gi-Oh")
        product = make_product(make_set(game, name="Alliance Insight", slug="alliance-insight"),
                               name="Alliance Insight Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(product, self.harbour, price="60.00")
        html = self.client.get(game.get_absolute_url()).content.decode()
        self.assertEqual(title_of(html), "Cheapest Yu-Gi-Oh booster boxes in stock UK | RipRaptor")
        self.assertNotIn("!", description_of(html))

    def test_a_shop_whose_only_price_is_stale_is_not_counted(self):
        stale_shop = make_retailer("Old Shop")
        make_listing(self.sold_out, stale_shop, price="130.00", hours_ago=24 * 30)
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn("3 sealed products compared across 2 UK shops", html)

    def test_listed_only_at_stale_prices(self):
        game = make_game(name="Riftbound", slug="riftbound")
        product = make_product(make_set(game, name="Origins", slug="origins"), name="Origins Booster Box",
                               product_type=Product.Type.BOOSTER_BOX)
        make_listing(product, self.harbour, price="90.00", hours_ago=24 * 30)
        html = self.client.get(game.get_absolute_url()).content.decode()
        self.assertIn("1 sealed product, with no current price from a UK shop we check.", html)
        self.assertNotIn("not yet listed", html)
        self.assertNotIn("compared across", html)

    def test_a_shop_name_ending_in_a_full_stop_gets_one_full_stop(self):
        marlow = make_retailer("Marlow Card Co.")
        make_listing(self.etb, marlow, price="40.00")
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn("£40.00 delivered at Marlow Card Co. ", visible(html) + " ")
        self.assertNotIn("Co..", html)
        product_html = self.client.get(self.etb.get_absolute_url()).content.decode()
        self.assertIn("£40.00 delivered at Marlow Card Co. Compare", description_of(product_html))

    def test_a_long_game_name_falls_back_to_the_short_title(self):
        game = make_game(name="Flesh and Blood", slug="flesh-and-blood")
        product = make_product(make_set(game, name="Hunted", slug="hunted"), name="Hunted Booster Box",
                               product_type=Product.Type.BOOSTER_BOX)
        make_listing(product, self.harbour, price="90.00")
        title = title_of(self.client.get(game.get_absolute_url()).content.decode())
        self.assertEqual(title, "Cheapest Flesh and Blood booster boxes in stock UK | RipRaptor")
        self.assertLessEqual(len(title) - len(" | RipRaptor"), hubs.TITLE_LIMIT)

    def test_game_page_queries_do_not_grow_with_the_catalogue(self):
        from django.core.cache import cache

        def count():
            cache.clear()   # the cached lists (coming soon sets) are built afresh both times
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(self.game.get_absolute_url()).status_code, 200)
            return len(queries.captured_queries)

        before = count()
        for number in range(6):
            product = make_product(self.pre, name=f"Prismatic Evolutions Pack {number}",
                                   product_type=Product.Type.BOOSTER_PACK)
            make_listing(product, make_retailer(f"Shop {number}"), price=f"{5 + number}.00")
        self.assertEqual(count(), before)

    def test_hub_numbers_take_three_queries(self):
        with self.assertNumQueries(3):
            stats = hubs.hub_stats(game=self.game)
        self.assertEqual((stats.products, stats.in_stock, stats.shops, stats.stock_shops), (3, 1, 2, 2))
        self.assertEqual(stats.cheapest.product, self.etb)
        self.assertEqual(stats.stocked_types, [Product.Type.ELITE_TRAINER_BOX])

    def test_a_tab_fetched_by_script_skips_the_hub_numbers(self):
        with CaptureQueriesContext(connection) as full:
            self.client.get(self.game.get_absolute_url())
        with CaptureQueriesContext(connection) as pane:
            self.client.get(self.game.get_absolute_url() + "?tab=pre-orders&pane=1")
        self.assertLess(len(pane.captured_queries), len(full.captured_queries))


class SetHubTests(PageTestCase):
    def test_title_description_and_intro(self):
        html = self.client.get(self.pre.get_absolute_url()).content.decode()
        self.assertEqual(title_of(html), "Prismatic Evolutions ETBs: cheapest UK prices | RipRaptor")
        description = description_of(html)
        self.assertTrue(description.startswith(
            "Prismatic Evolutions: 1 sealed Pokémon product in stock at 2 UK shops, from £54.99 delivered."
        ))
        self.assertLessEqual(len(description), 160)
        self.assertIn("3 sealed products compared across 2 UK shops, 1 in stock now.", html)

    def test_links_back_to_the_game_besides_the_breadcrumb(self):
        html = self.client.get(self.pre.get_absolute_url()).content.decode()
        head = html[html.index('<div class="browse-head">'):html.index('class="browse-head__count"')]
        breadcrumb_end = head.index("</nav>")
        self.assertIn(f'<a href="{self.game.get_absolute_url()}">All Pokémon products</a>', head[breadcrumb_end:])

    def test_a_set_still_to_come_gives_its_release_date(self):
        out = timezone.localdate() + timedelta(days=40)
        upcoming = make_set(self.game, name="Ascended Heroes", slug="ascended-heroes", code="ASC", release_date=out)
        product = make_product(upcoming, name="Ascended Heroes Elite Trainer Box")
        make_listing(product, self.harbour, price="49.99", availability=Listing.Availability.PREORDER)
        html = self.client.get(upcoming.get_absolute_url()).content.decode()
        self.assertEqual(title_of(html), "Ascended Heroes release date and UK pre-orders | RipRaptor")
        description = description_of(html)
        self.assertTrue(description.startswith(f"Pokémon Ascended Heroes is due out on {out.day} "))
        self.assertNotIn("in stock", description)
        self.assertIn("1 sealed product compared across 1 UK shop. None is in stock right now", html)

    def test_a_set_still_to_come_that_no_shop_lists_promises_no_prices(self):
        out = timezone.localdate() + timedelta(days=40)
        upcoming = make_set(self.game, name="Ascended Heroes", slug="ascended-heroes", code="ASC", release_date=out)
        make_product(upcoming, name="Ascended Heroes Elite Trainer Box")
        html = self.client.get(upcoming.get_absolute_url()).content.decode()
        self.assertEqual(title_of(html), "Ascended Heroes release date | RipRaptor")
        description = description_of(html)
        self.assertTrue(description.startswith(f"Pokémon Ascended Heroes is due out on {out.day} "))
        self.assertNotIn("pre-order", description.lower())
        self.assertIn("1 sealed product, not yet listed by a UK shop we check.", html)

    def test_a_long_set_name_falls_back_to_the_short_title(self):
        long_set = make_set(self.game, name="Scarlet and Violet Paldean Fates Special", slug="paldean-fates",
                            code="PAF")
        make_listing(make_product(long_set, name="Paldean Fates Elite Trainer Box"), self.harbour, price="60.00")
        title = title_of(self.client.get(long_set.get_absolute_url()).content.decode())
        self.assertEqual(title, "Scarlet and Violet Paldean Fates Special ETBs: UK prices | RipRaptor")
        self.assertLessEqual(len(title) - len(" | RipRaptor"), hubs.TITLE_LIMIT)

    def test_a_released_set_with_nothing_in_stock(self):
        Listing.objects.filter(product=self.etb).update(availability=Listing.Availability.OUT_OF_STOCK)
        html = self.client.get(self.pre.get_absolute_url()).content.decode()
        self.assertEqual(title_of(html), "Prismatic Evolutions sealed products: UK price comparison | RipRaptor")
        self.assertNotIn("cheapest", description_of(html).lower())


class ProductMetaTests(PageTestCase):
    def test_one_shop_is_singular(self):
        Listing.objects.filter(product=self.etb, retailer=self.north).delete()
        description = description_of(self.client.get(self.etb.get_absolute_url()).content.decode())
        self.assertIn("Compare 1 UK shop with", description)
        self.assertNotIn("1 UK shops", description)

    def test_long_names_stay_within_160_characters_on_a_whole_word(self):
        name = "Prismatic Evolutions Elite Trainer Box Pokémon Center Exclusive Edition With Extra Promo Cards"
        Product.objects.filter(pk=self.etb.pk).update(name=name)
        description = description_of(self.client.get(self.etb.get_absolute_url()).content.decode())
        self.assertLessEqual(len(description), 160)
        self.assertTrue(description.startswith(f"Cheapest {name} today is £54.99 delivered at Harbour Games."))
        words = set(f"Cheapest {name} today is £54.99 delivered at Harbour Games. Compare 2 UK shops with stock, "
                    "delivery and when each price was checked.".split())
        for word in description.rstrip("…").split():
            self.assertIn(word, words)

    def test_pre_order_only_makes_no_cheapest_today_claim(self):
        Listing.objects.filter(product=self.etb).update(availability=Listing.Availability.PREORDER)
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        description = description_of(html)
        self.assertTrue(description.startswith(f"{self.etb.name} is on pre-order from £54.99 delivered at Harbour Games."))
        self.assertNotIn("today", description)
        self.assertNotIn("Cheapest", description)


    def test_a_cheaper_pre_order_is_not_called_cheapest_today(self):
        # The cheapest price is a pre-order; another shop has it in stock for more.
        preorder_shop = make_retailer("Total Cards")
        make_listing(self.etb, preorder_shop, price="40.00", availability=Listing.Availability.PREORDER)
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        description = description_of(html)
        self.assertTrue(description.startswith(
            f"{self.etb.name} is on pre-order from £40.00 delivered at Total Cards, or in stock from £54.99 at "
            "Harbour Games."
        ), description)
        self.assertNotIn("Cheapest", description)
        self.assertNotIn("today", description)
        self.assertLessEqual(len(description), 160)
        # The title still quotes the lowest price, which is true of any offer.
        self.assertTrue(title_of(html).startswith(f"{self.etb.name} from £40.00 delivered"))


class FitDescriptionTests(TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(hubs.fit_description("One sentence."), "One sentence.")

    def test_a_trailing_sentence_goes_when_enough_is_left(self):
        first = "A" * 60 + " " + "b" * 60 + "."
        text = f"{first} Then a second sentence that takes the description over the limit by some way."
        self.assertEqual(hubs.fit_description(text), first)

    def test_otherwise_it_is_cut_after_a_whole_word(self):
        text = ("Cheapest thing today is £5.00. " + "word " * 40).strip()
        fitted = hubs.fit_description(text)
        self.assertLessEqual(len(fitted), 160)
        self.assertTrue(fitted.endswith("word…"))

    def test_titles_drop_types_until_they_fit(self):
        build = lambda chosen: "Cheapest " + " and ".join(chosen)   # noqa: E731
        self.assertEqual(hubs.fit_title(build, ["x" * 30, "y" * 30], limit=60), "Cheapest " + "x" * 30)
        self.assertEqual(hubs.fit_title(build, ["x" * 80], limit=60), "Cheapest " + "x" * 80)

    def test_titles_fall_back_to_the_short_form_only_when_one_type_does_not_fit(self):
        build = lambda chosen: "Cheapest " + " and ".join(chosen) + " in stock: long ending"   # noqa: E731
        short = lambda chosen: "Cheapest " + " and ".join(chosen) + " UK"   # noqa: E731
        self.assertEqual(hubs.fit_title(build, ["x" * 10, "y" * 10], limit=60, short=short),
                         "Cheapest " + "x" * 10 + " and " + "y" * 10 + " in stock: long ending")
        self.assertEqual(hubs.fit_title(build, ["x" * 30, "y" * 10], limit=60, short=short),
                         "Cheapest " + "x" * 30 + " and " + "y" * 10 + " UK")
        self.assertEqual(hubs.fit_title(build, ["x" * 80], limit=60, short=short), "Cheapest " + "x" * 80 + " UK")

    def test_tidy_stops(self):
        self.assertEqual(hubs.tidy_stops("at Marlow Card Co.. Compare"), "at Marlow Card Co. Compare")
        self.assertEqual(hubs.tidy_stops("Wait... no"), "Wait... no")

    def test_join_words(self):
        self.assertEqual(hubs.join_words(["a"], "and"), "a")
        self.assertEqual(hubs.join_words(["a", "b", "c"], "and"), "a, b and c")


class HubStructuredDataTests(PageTestCase):
    def check_collection(self, url, crumb_names):
        html = self.client.get(url).content.decode()
        blocks = by_type(html)
        self.assertEqual(set(blocks), {"BreadcrumbList", "CollectionPage"})
        page_url = "http://testserver" + url
        crumbs, page = blocks["BreadcrumbList"], blocks["CollectionPage"]
        for block in (crumbs, page):
            self.assertEqual(block["@context"], "https://schema.org")
        self.assertEqual(crumbs["@id"], page_url + "#breadcrumb")
        self.assertEqual([item["name"] for item in crumbs["itemListElement"]], crumb_names)
        self.assertEqual([item["position"] for item in crumbs["itemListElement"]],
                         list(range(1, len(crumb_names) + 1)))
        self.assertEqual(page["@id"], page_url + "#webpage")
        self.assertEqual(page["url"], page_url)
        self.assertEqual(page["name"] + " | RipRaptor", title_of(html))
        self.assertEqual(page["description"], description_of(html))
        self.assertEqual(page["breadcrumb"], {"@id": crumbs["@id"]})
        items = page["mainEntity"]
        self.assertEqual(items["@type"], "ItemList")
        self.assertEqual(items["numberOfItems"], len(items["itemListElement"]))
        # The In stock tab is what the page shows first: the ETB.
        self.assertEqual(items["itemListElement"], [
            {"@type": "ListItem", "position": 1, "url": "http://testserver" + self.etb.get_absolute_url(),
             "name": self.etb.name},
        ])
        return items

    def test_game_page(self):
        self.check_collection(self.game.get_absolute_url(), ["Pokémon"])

    def test_set_page(self):
        self.check_collection(self.pre.get_absolute_url(), ["Pokémon", "Prismatic Evolutions"])

    def test_only_the_canonical_view_describes_the_page(self):
        # Other tabs, pages and filters share the canonical address and @id but list other products, so they
        # carry only the breadcrumbs.
        url = self.game.get_absolute_url()
        for query in ("?tab=out-of-stock", "?tab=pre-orders", "?type=elite_trainer_box",
                      "?sort=price-low"):
            with self.subTest(query=query):
                html = self.client.get(url + query).content.decode()
                self.assertEqual(set(by_type(html)), {"BreadcrumbList"})
        for query in ("", "?tab=in-stock", "?page=1", "?utm_source=newsletter"):
            with self.subTest(query=query):
                html = self.client.get(url + query).content.decode()
                self.assertEqual(set(by_type(html)), {"BreadcrumbList", "CollectionPage"})

    def test_a_second_page_has_no_item_list(self):
        from django.test import override_settings

        for number in range(3):
            make_listing(make_product(self.pre, name=f"Prismatic Evolutions Pack {number}",
                                      product_type=Product.Type.BOOSTER_PACK), self.harbour, price="5.00")
        with override_settings(RIPRAPTOR_PAGE_SIZE=2):
            first = by_type(self.client.get(self.game.get_absolute_url()).content.decode())
            second = by_type(self.client.get(self.game.get_absolute_url() + "?page=2").content.decode())
        self.assertEqual(first["CollectionPage"]["mainEntity"]["numberOfItems"], 2)
        self.assertEqual(set(second), {"BreadcrumbList"})

    def test_names_cannot_end_the_script_block(self):
        name = "Prismatic Evolutions Elite Trainer Box </script><script>alert(1)</script>"
        Product.objects.filter(pk=self.etb.pk).update(name=name)
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertNotIn("</script><script>alert", html)
        items = by_type(html)["CollectionPage"]["mainEntity"]["itemListElement"]
        self.assertEqual(items[0]["name"], name)


class ProductStructuredDataTests(PageTestCase):
    def test_item_page_points_at_the_product_and_breadcrumbs(self):
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        blocks = by_type(html)
        self.assertEqual(set(blocks), {"Product", "BreadcrumbList", "ItemPage"})
        page_url = "http://testserver" + self.etb.get_absolute_url()
        product, crumbs, page = blocks["Product"], blocks["BreadcrumbList"], blocks["ItemPage"]
        self.assertEqual(product["@id"], page_url + "#product")
        self.assertEqual(product["offers"]["@type"], "AggregateOffer")
        for key in ("lowPrice", "highPrice", "offerCount", "priceCurrency", "availability"):
            self.assertIn(key, product["offers"])
        self.assertEqual(page["@id"], page_url + "#webpage")
        self.assertEqual(page["url"], page_url)
        self.assertEqual(page["name"] + " | RipRaptor", title_of(html))
        self.assertEqual(page["mainEntity"], {"@id": product["@id"]})
        self.assertEqual(page["breadcrumb"], {"@id": crumbs["@id"]})
        self.assertEqual(crumbs["@id"], page_url + "#breadcrumb")

    def test_a_product_without_offers_has_no_main_entity(self):
        blocks = by_type(self.client.get(self.unpriced.get_absolute_url()).content.decode())
        self.assertEqual(set(blocks), {"BreadcrumbList", "ItemPage"})
        self.assertNotIn("mainEntity", blocks["ItemPage"])


class CrawlableListsTests(PageTestCase):
    def test_sitemap_lists_out_now_every_set_and_game(self):
        other = make_set(self.game, name="Surging Sparks", slug="surging-sparks", code="SSP")
        make_product(other, name="Surging Sparks Booster Box", product_type=Product.Type.BOOSTER_BOX)
        body = self.client.get(reverse("web:sitemap")).content.decode()
        for path in ("/deals/", "/drops/", "/drops/?show=out", self.game.get_absolute_url(),
                     self.pre.get_absolute_url(), other.get_absolute_url()):
            self.assertIn(f"<loc>http://testserver{path}</loc>", body)

    def test_out_now_lists_products_in_the_html(self):
        response = self.client.get(reverse("web:drops") + "?show=out")
        self.assertContains(response, '<link rel="canonical" href="http://testserver/drops/?show=out">')


class HubCopyStyleTests(PageTestCase):
    def test_hub_pages_keep_the_house_style_in_every_state(self):
        upcoming = make_set(self.game, name="Ascended Heroes", slug="ascended-heroes",
                            release_date=timezone.localdate() + timedelta(days=40))
        make_product(upcoming, name="Ascended Heroes Elite Trainer Box")
        empty = make_game(name="Riftbound", slug="riftbound")
        urls = [self.game.get_absolute_url(), self.pre.get_absolute_url(), upcoming.get_absolute_url(),
                empty.get_absolute_url(), self.etb.get_absolute_url(), self.unpriced.get_absolute_url()]

        def check():
            for url in urls:
                html = self.client.get(url).content.decode()
                with self.subTest(url=url):
                    self.assertNotIn("!", visible(html))
                    self.assertNotIn("!", title_of(html) + description_of(html))
                    for phrase in CopyStyleTests.BANNED:
                        self.assertNotIn(phrase, html.lower())

        check()
        Listing.objects.update(availability=Listing.Availability.OUT_OF_STOCK)
        check()
