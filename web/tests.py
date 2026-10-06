import re
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue.models import DailyLowestPrice, Listing, OutboundClick, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class PageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.pre = make_set(cls.game)
        cls.etb = make_product(cls.pre)
        cls.harbour = make_retailer("Harbour Games")
        cls.north = make_retailer("Northgate Cards")
        cls.cheap = make_listing(cls.etb, cls.harbour, price="52.00", delivery="2.99")
        make_listing(cls.etb, cls.north, price="58.00")
        cls.sold_out = make_product(
            cls.pre, name="Prismatic Evolutions Super-Premium Collection",
            product_type=Product.Type.COLLECTION_BOX,
        )
        make_listing(cls.sold_out, cls.harbour, price="140.00",
                     availability=Listing.Availability.OUT_OF_STOCK)
        DailyLowestPrice.objects.create(
            product=cls.sold_out, date=timezone.localdate() - timedelta(days=10), price="139.00"
        )
        cls.unpriced = make_product(
            cls.pre, name="Prismatic Evolutions Booster Bundle", product_type=Product.Type.BUNDLE
        )
        for days_ago, price in ((9, "60.00"), (5, "57.00"), (0, "54.99")):
            DailyLowestPrice.objects.create(
                product=cls.etb, date=timezone.localdate() - timedelta(days=days_ago), price=price
            )
        # A click so the home page shows every section.
        OutboundClick.objects.create(listing=cls.cheap, product=cls.etb, retailer=cls.harbour)

    def public_urls(self):
        return [
            reverse("web:home"),
            reverse("web:search"),
            reverse("web:search") + "?q=etb",
            reverse("web:search") + "?q=nothing+matches+this",
            reverse("web:search") + "?q=etb&type=tin",
            reverse("web:games"),
            self.game.get_absolute_url(),
            self.pre.get_absolute_url(),
            self.etb.get_absolute_url(),
            self.sold_out.get_absolute_url(),
            self.unpriced.get_absolute_url(),
            reverse("web:about"),
            reverse("web:watchlist"),
            reverse("web:watchlist") + f"?p={self.etb.slug},{self.sold_out.slug}",
        ]


class LastSeenTests(TestCase):
    def test_out_of_stock_card_shows_the_last_price(self):
        from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

        product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", product_type="booster_box")
        make_listing(product, make_retailer("Shop"), price="120.00", delivery="3.00", availability="out_of_stock")
        response = self.client.get(reverse("web:search"))
        self.assertContains(response, "Last seen at £123.00")


class PageTests(PageTestCase):
    def test_public_pages_load(self):
        for url in self.public_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_home_shows_every_section(self):
        response = self.client.get(reverse("web:home"))
        for heading in ("Trending now", "Biggest savings", "Save £3.01", "Biggest price drop today", "Recently released", "Browse by game"):
            self.assertContains(response, heading)

    def test_product_page_leads_with_the_cheapest_delivered_price(self):
        response = self.client.get(self.etb.get_absolute_url())
        self.assertContains(response, "Cheapest price")
        self.assertContains(response, "£54.99 delivered")
        self.assertContains(response, "at Harbour Games")
        self.assertContains(response, "£52.00 plus £2.99 delivery")
        self.assertContains(response, "Buy now")
        self.assertContains(response, "Best in 7 days")
        self.assertContains(response, 'rank__pos--2">#2</span><span class="rank__name">Northgate Cards')
        self.assertContains(response, "2 retailers have this in stock.")
        self.assertContains(response, "Last checked 1 hour ago.")
        self.assertContains(response, self.cheap.get_outbound_url())

    def test_out_of_stock_product(self):
        response = self.client.get(self.sold_out.get_absolute_url())
        self.assertContains(response, "Out of stock at every retailer we check")
        self.assertContains(response, "Cheapest price when last available: £139.00")
        self.assertNotContains(response, "Buy for")

    def test_product_without_prices(self):
        response = self.client.get(self.unpriced.get_absolute_url())
        self.assertContains(response, "We don&#x27;t have prices for this product yet.")
        self.assertNotContains(response, "Price history")

    def test_hidden_product_is_not_found(self):
        Product.objects.filter(pk=self.etb.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.etb.get_absolute_url()).status_code, 404)

    def test_custom_not_found_page(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)

    def test_search_filters(self):
        url = reverse("web:search")
        self.assertContains(self.client.get(url, {"q": "etb"}), self.etb.name)
        in_stock = self.client.get(url, {"in_stock": "on"})
        self.assertContains(in_stock, self.etb.name)
        self.assertNotContains(in_stock, self.sold_out.name)
        by_type = self.client.get(url, {"type": "bundle"})
        self.assertContains(by_type, self.unpriced.name)
        self.assertNotContains(by_type, self.etb.name)

    def test_search_with_no_matches_offers_games(self):
        response = self.client.get(reverse("web:search"), {"q": "charizard"})
        self.assertContains(response, "No products match ‘charizard’")
        self.assertContains(response, self.game.get_absolute_url())

    def test_home_shows_price_drops(self):
        make_listing(self.unpriced, self.harbour, price="30.00")
        DailyLowestPrice.objects.create(
            product=self.unpriced, date=timezone.localdate() - timedelta(days=7), price="36.00"
        )
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "Price drops this week")
        self.assertContains(response, "was £36.00")

    def test_robots_txt(self):
        response = self.client.get("/robots.txt")
        self.assertContains(response, "Disallow: /go/")


class OutboundTests(PageTestCase):
    def test_redirects_and_counts_the_click(self):
        before = OutboundClick.objects.filter(product=self.etb).count()
        response = self.client.get(
            self.cheap.get_outbound_url(), HTTP_USER_AGENT="Mozilla/5.0 Firefox/130.0"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.cheap.url)
        self.assertEqual(OutboundClick.objects.filter(product=self.etb).count(), before + 1)

    def test_uses_the_affiliate_link(self):
        self.harbour.affiliate_url_template = "https://network.example/c?u={url}"
        self.harbour.save()
        response = self.client.get(self.cheap.get_outbound_url())
        self.assertTrue(response["Location"].startswith("https://network.example/c?u=https%3A"))

    def test_bots_are_not_counted(self):
        before = OutboundClick.objects.count()
        self.client.get(self.cheap.get_outbound_url(), HTTP_USER_AGENT="Googlebot/2.1")
        self.assertEqual(OutboundClick.objects.count(), before)

    def test_hidden_listing_is_not_found(self):
        Listing.objects.filter(pk=self.cheap.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.cheap.get_outbound_url()).status_code, 404)

    def test_outbound_links_are_marked_sponsored(self):
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        for tag in re.findall(r'<a[^>]+href="/go/[^>]+>', html):
            self.assertIn('rel="sponsored nofollow noopener"', tag)


class CopyStyleTests(PageTestCase):
    """Guards for the house style on every public page."""

    BANNED = [
        "—", "&mdash;", "&#8212;",
        "lorem ipsum", "welcome to ripraptor", "learn more", "get started",
        "unlock", "supercharge", "seamless", "next-generation", "game-changing",
        "your ultimate", "all in one place", "elevate", "empowering", "revolutionise",
        "effortless", "powered by ai",
    ]

    def test_no_banned_words_or_em_dashes(self):
        urls = self.public_urls() + ["/no-such-page/"]
        for url in urls:
            html = self.client.get(url).content.decode().lower()
            for phrase in self.BANNED:
                with self.subTest(url=url, phrase=phrase):
                    self.assertNotIn(phrase, html)

    def test_no_exclamation_marks(self):
        for url in self.public_urls():
            html = self.client.get(url).content.decode()
            visible_text = re.sub(r"<[^>]*>", " ", html)
            with self.subTest(url=url):
                self.assertNotIn("!", visible_text)


class ServerErrorPageTests(TestCase):
    def test_500_page_renders_without_a_database_or_context(self):
        from django.template.loader import get_template

        html = get_template("500.html").render({})
        self.assertIn("Something went wrong", html)
        self.assertNotIn("—", html)


class SortTests(PageTestCase):
    def test_best_match_only_offered_with_a_query(self):
        browse = self.client.get(self.game.get_absolute_url()).content.decode()
        search = self.client.get(reverse("web:search"), {"q": "etb"}).content.decode()
        self.assertNotIn("Best match", browse)
        self.assertIn("Newest first", browse)
        self.assertIn("Best match", search)

    def test_sort_by_price(self):
        response = self.client.get(reverse("web:search"), {"sort": "price"})
        names = [p.name for p in response.context["page"].object_list]
        self.assertLess(names.index(self.etb.name), names.index(self.sold_out.name))


class SearchApiTests(PageTestCase):
    def test_returns_matching_products_with_price_and_stock(self):
        response = self.client.get(reverse("web:search_api"), {"q": "prism etb"})
        data = response.json()
        self.assertEqual(data["query"], "prism etb")
        self.assertEqual(data["results"][0]["name"], self.etb.name)
        self.assertEqual(data["results"][0]["price"], "£54.99")
        self.assertEqual(data["results"][0]["state"], "in")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.client.get(reverse("web:search_api")).json()["results"], [])

    def test_home_shows_when_prices_were_last_checked(self):
        self.assertContains(self.client.get(reverse("web:home")), "Prices checked 1 hour ago")


class DeckTests(PageTestCase):
    def test_deck_page_and_api(self):
        self.assertContains(self.client.get(reverse("web:deck")), "Swipe through")
        data = self.client.get(reverse("web:deck_api")).json()
        card = data["cards"][0]
        self.assertEqual(card["name"], self.etb.name)
        self.assertEqual(card["price"], "£54.99")
        self.assertEqual(card["retailer"], "Harbour Games")
        self.assertEqual(card["second"], {"retailer": "Northgate Cards", "price": "£58.00"})
        self.assertEqual(card["saving"], "£3.01")
        self.assertEqual(card["badge"], "best_week")
        self.assertIsNone(data["next"])

    def test_deck_only_includes_priced_products(self):
        names = [c["name"] for c in self.client.get(reverse("web:deck_api")).json()["cards"]]
        self.assertNotIn(self.unpriced.name, names)
        self.assertNotIn(self.sold_out.name, names)

    def test_search_cards_show_rank_and_badge(self):
        response = self.client.get(reverse("web:search"), {"q": "etb"})
        self.assertContains(response, "Best in 7 days")
        self.assertContains(response, "Check prices")
        self.assertContains(response, "#2")


class TermsAndRefreshTests(PageTestCase):
    def test_terms_page_holds_the_disclosures(self):
        response = self.client.get(reverse("web:terms"))
        self.assertContains(response, "commission")
        self.assertContains(response, "independent")
        self.assertContains(response, "stored in your browser")

    def test_footer_is_one_line_with_a_terms_link(self):
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertIn("Prices include UK delivery.", html)
        self.assertIn(reverse("web:terms"), html)
        self.assertNotIn("Product names and trademarks", html)

    def test_prices_api(self):
        data = self.client.get(reverse("web:product_prices_api", args=[self.etb.slug])).json()
        row = next(r for r in data["listings"] if r["id"] == self.cheap.pk)
        self.assertEqual(row["total"], "£54.99")
        self.assertTrue(row["buyable"])

    def test_product_page_has_refresh_control(self):
        response = self.client.get(self.etb.get_absolute_url())
        self.assertContains(response, 'data-refresh="')
        self.assertContains(response, "Reload prices")
        self.assertNotContains(response, "Check prices")
        self.assertContains(response, f'data-listing="{self.cheap.pk}"')


class SeoAndEdgeCaseTests(PageTestCase):
    def test_sitemap_lists_products_games_and_pages(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(self.etb.get_absolute_url(), body)
        self.assertIn(self.game.get_absolute_url(), body)
        self.assertIn(reverse("web:terms"), body)
        self.assertNotIn("/swipe/", body)

    def test_product_images_describe_the_product(self):
        Product.objects.filter(pk=self.etb.pk).update(image_url="https://img.example/etb.jpg")
        for url in (self.etb.get_absolute_url(), self.game.get_absolute_url()):
            body = self.client.get(url).content.decode()
            self.assertIn(f'alt="{self.etb.name}"', body, url)
            self.assertNotIn('alt=""', body, url)

    def test_robots_points_at_sitemap(self):
        self.assertContains(self.client.get("/robots.txt"), "Sitemap: http://testserver/sitemap.xml")

    def test_product_structured_data(self):
        import json

        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        start = html.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
        data, crumbs = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual(data["@type"], "Product")
        self.assertEqual(crumbs["@type"], "BreadcrumbList")
        self.assertEqual([c["name"] for c in crumbs["itemListElement"]], ["Pokémon", "Prismatic Evolutions", self.etb.name])
        self.assertEqual(data["offers"]["lowPrice"], "54.99")
        self.assertEqual(data["offers"]["offerCount"], 2)

    def test_home_lists_are_cached_and_cleared_by_imports(self):
        from django.core.cache import cache

        cache.clear()
        self.client.get(reverse("web:home"))
        self.assertIsNotNone(cache.get("web:home-lists:v2"))
        from catalogue.importers import run_import
        from catalogue.models import Retailer

        self.harbour.source_type = Retailer.Source.SHOPIFY
        self.harbour.source_url = "https://h.example/"
        self.harbour.save()
        run_import(self.harbour, fetch=lambda url: b'{"products": []}')
        self.assertIsNone(cache.get("web:home-lists:v2"))

    def test_long_names_unicode_search_and_bad_pages(self):
        long_name = "Pokémon TCG: Scarlet & Violet " + "Ultra Premium Collection " * 5
        product = make_product(self.pre, name=long_name.strip(), product_type=Product.Type.COLLECTION_BOX)
        self.assertEqual(self.client.get(product.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("web:search"), {"q": "pokémon ultra"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("web:search"), {"q": "'; DROP TABLE--"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("web:search"), {"page": "999"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("web:search"), {"page": "x", "game": "nope", "sort": "bad"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("web:deck_api"), {"offset": "-5"}).status_code, 200)

    def test_only_stale_listings_means_no_cheapest_price(self):
        from datetime import timedelta

        Listing.objects.filter(product=self.etb).update(last_checked=timezone.now() - timedelta(days=10))
        response = self.client.get(self.etb.get_absolute_url())
        self.assertContains(response, "Out of stock at every retailer we check")
        self.assertContains(response, "Not checked since")


class AdminAddProductTests(TestCase):
    def test_product_can_be_added_through_admin_with_a_listing(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(user)
        game = make_game()
        retailer = make_retailer("Test Shop")
        data = {
            "name": "Browser Test Booster Box", "slug": "browser-test-booster-box", "game": game.pk, "product_set": "",
            "product_type": "booster_box", "is_active": "on", "ean": "0820650859999", "image_url": "", "release_date": "",
            "listings-TOTAL_FORMS": "1", "listings-INITIAL_FORMS": "0", "listings-MIN_NUM_FORMS": "0", "listings-MAX_NUM_FORMS": "1000",
            "listings-0-retailer": retailer.pk, "listings-0-url": "https://testshop.example/products/x",
            "listings-0-price": "99.99", "listings-0-delivery_cost": "0", "listings-0-availability": "in_stock",
            "listings-0-last_checked_0": "2026-09-28", "listings-0-last_checked_1": "12:00:00", "listings-0-is_active": "on",
            "_save": "Save",
        }
        response = self.client.post(reverse("admin:catalogue_product_add"), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", {}).get("errors") or response.content[:2000])
        product = Product.objects.get(slug="browser-test-booster-box")
        self.assertEqual(product.listings.count(), 1)
        self.assertContains(self.client.get(product.get_absolute_url()), "£99.99")


class AwinTagTests(PageTestCase):
    def test_every_public_page_loads_the_awin_mastertag(self):
        from django.test import override_settings

        for url in self.public_urls():
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('<script src="https://www.dwin2.com/pub.3111686.min.js"></script>', html)
                self.assertLess(html.index("dwin2.com"), html.index("</body>"))
                self.assertGreater(html.index("dwin2.com"), html.index("site-footer"))
        with override_settings(RIPRAPTOR_AWIN_PUBLISHER_ID=""):
            self.assertNotContains(self.client.get(reverse("web:home")), "dwin2.com")


class AmazonLinkTests(PageTestCase):
    def test_no_link_without_a_tracking_tag(self):
        response = self.client.get(self.etb.get_absolute_url())
        self.assertNotContains(response, "Compare on Amazon")

    def test_tagged_search_link_when_we_have_no_amazon_price(self):
        from django.test import override_settings

        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            response = self.client.get(self.etb.get_absolute_url())
        self.assertContains(response, "Compare on Amazon")
        self.assertContains(response, 'href="https://www.amazon.co.uk/s?k=Prismatic+Evolutions+Elite+Trainer+Box&amp;tag=ripraptor-21"')
        self.assertContains(response, 'rel="sponsored nofollow noopener"')

    def test_no_search_link_once_amazon_has_a_real_price(self):
        from django.test import override_settings

        from catalogue.models import Retailer

        amazon = make_retailer("Amazon", slug="amazon", source_type=Retailer.Source.AMAZON)
        make_listing(self.etb, amazon, price="80.00")
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            response = self.client.get(self.etb.get_absolute_url())
        self.assertNotContains(response, "Compare on Amazon")
        self.assertContains(response, "Amazon")


class LanguageAndSortTests(PageTestCase):
    def setUp(self):
        super().setUp()
        self.japanese = make_product(self.pre, name="Prismatic Evolutions Booster Box (Japanese)", slug="pev-box-ja")
        make_listing(self.japanese, make_retailer("Japan Shop"), price="70.00")
        self.chinese = make_product(self.pre, name="Prismatic Evolutions Booster Box (Simplified Chinese)", slug="pev-box-zh")
        make_listing(self.chinese, make_retailer("China Shop"), price="60.00")
        # The ETB (54.99 against 58.00) saves £3.01; the Japanese box has one shop so no saving.
        # The ETB's recorded prices (60, 57, 54.99) put it at its 90-day low; the Japanese box at £70 is above its £65.
        DailyLowestPrice.objects.create(product=self.japanese, date=timezone.localdate() - timedelta(days=3), price="65.00")

    def names(self, url, **params):
        return [c["name"] for c in self.client.get(url, params).json()["cards"]]

    def test_deck_language_filter_keeps_only_chosen_languages(self):
        url = reverse("web:deck_api")
        english = self.names(url, lang="en")
        self.assertIn(self.etb.name, english)
        self.assertNotIn(self.japanese.name, english)
        self.assertNotIn(self.chinese.name, english)
        both = self.client.get(url + "?lang=en&lang=ja").json()["cards"]
        both_names = [c["name"] for c in both]
        self.assertIn(self.japanese.name, both_names)
        self.assertNotIn(self.chinese.name, both_names)
        self.assertEqual(self.names(url, lang="zh"), [self.chinese.name])

    def test_deck_sort_by_biggest_saving_puts_the_two_shop_product_first(self):
        names = self.names(reverse("web:deck_api"), sort="saving")
        self.assertEqual(names[0], self.etb.name)

    def test_deck_sort_by_ninety_day_low_puts_products_at_their_low_first(self):
        # The ETB is at £54.99 against a recorded £59.99: at its low. The Japanese box is £70 against £65: not.
        names = self.names(reverse("web:deck_api"), sort="low")
        self.assertEqual(names[0], self.etb.name)
        self.assertEqual(names[-1], self.japanese.name)

    def test_unknown_deck_sort_falls_back_to_newest(self):
        self.assertEqual(self.client.get(reverse("web:deck_api"), {"sort": "nonsense"}).status_code, 200)

    def test_deck_page_offers_sort_and_language_choices(self):
        response = self.client.get(reverse("web:deck"), {"lang": "ja", "sort": "saving"})
        self.assertContains(response, 'value="ja" checked')
        self.assertContains(response, '<option value="saving" selected>')
        self.assertContains(response, "Lowest in 90 days")

    def test_browse_pages_take_the_same_filters(self):
        response = self.client.get(reverse("web:search"), {"lang": ["ja"], "sort": "saving"})
        self.assertContains(response, self.japanese.name)
        self.assertNotContains(response, self.chinese.name)
        self.assertContains(response, "Biggest saving")
        response = self.client.get(reverse("web:search"), {"q": "prismatic", "sort": "low"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clear filters")


class DiscoverabilityTests(PageTestCase):
    def test_product_page_has_price_led_title_description_and_sharing_tags(self):
        response = self.client.get(self.etb.get_absolute_url())
        self.assertContains(response, "<title>Prismatic Evolutions Elite Trainer Box from £54.99 delivered: UK price comparison | RipRaptor</title>")
        self.assertContains(response, 'content="Cheapest Prismatic Evolutions Elite Trainer Box today is £54.99 delivered at Harbour Games. Compare 2 UK shops')
        self.assertContains(response, '<meta property="og:type" content="product">')
        self.assertContains(response, '<meta property="og:url" content="http://testserver' + self.etb.get_absolute_url() + '">')
        self.assertContains(response, '"@type": "BreadcrumbList"')
        self.assertContains(response, '"@type": "Product"')

    def test_every_product_data_block_carries_offers(self):
        import json

        def blocks(url):
            html = self.client.get(url).content.decode()
            start = html.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
            return json.loads(html[start:html.index("</script>", start)])

        sold_out = {b["@type"]: b for b in blocks(self.sold_out.get_absolute_url())}
        self.assertEqual(sold_out["Product"]["offers"]["availability"], "https://schema.org/OutOfStock")
        self.assertEqual(sold_out["Product"]["offers"]["lowPrice"], "140.00")
        unlisted = [b["@type"] for b in blocks(self.unpriced.get_absolute_url())]
        self.assertEqual(unlisted, ["BreadcrumbList"])
        for url in (self.etb.get_absolute_url(), self.sold_out.get_absolute_url()):
            for block in blocks(url):
                if block["@type"] == "Product":
                    self.assertIn("offers", block)

    def test_unpriced_product_keeps_the_plain_title(self):
        response = self.client.get(self.unpriced.get_absolute_url())
        self.assertContains(response, "<title>Prismatic Evolutions Booster Bundle: UK prices | RipRaptor</title>")

    def test_home_declares_the_site_and_its_search(self):
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, '"@type": "WebSite"')
        self.assertContains(response, 'search/?q={query}')
        self.assertContains(response, '<meta property="og:image" content="http://testserver/static/img/share.png">')

    def test_game_and_set_pages_carry_breadcrumbs(self):
        for url in (self.game.get_absolute_url(), self.pre.get_absolute_url()):
            self.assertContains(self.client.get(url), '"@type": "BreadcrumbList"')

    def test_llms_txt_describes_the_site_for_ai_crawlers(self):
        response = self.client.get("/llms.txt")
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        body = response.content.decode()
        self.assertIn("# RipRaptor", body)
        self.assertIn("[Pokémon](http://testserver" + self.game.get_absolute_url() + ")", body)
        self.assertIn("/sitemap.xml", body)
        self.assertNotIn("<p>", body)

    def test_robots_names_ai_crawlers_and_keeps_private_paths_out(self):
        body = self.client.get("/robots.txt").content.decode()
        self.assertIn("User-agent: GPTBot\nAllow: /\nDisallow: /admin/", body)
        self.assertIn("User-agent: ClaudeBot", body)
        self.assertTrue(body.strip().endswith("Sitemap: http://testserver/sitemap.xml"))


class RestockRecordPageTests(PageTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_product_page_says_how_often_it_comes_back(self):
        from catalogue.models import Restock

        now = timezone.now()
        Restock.objects.create(product=self.etb, retailer=self.north, listing=self.cheap, at=now - timedelta(days=3), price="55.00")
        Restock.objects.create(product=self.etb, retailer=self.harbour, listing=self.cheap, at=now - timedelta(days=1), price="54.99")
        response = self.client.get(self.etb.get_absolute_url())
        when = timezone.localdate(now - timedelta(days=1)).strftime("%-d %b %Y")
        self.assertContains(response, f"Back in stock 2 times in the last 30 days, most recently at Harbour Games on {when}.")
        self.assertNotContains(response, "Most restocks landed")
        self.assertNotContains(self.client.get(self.sold_out.get_absolute_url()), "Back in stock")

    def test_deals_page_logs_restocks_by_day(self):
        from catalogue.models import Restock

        now = timezone.now()
        sold = Listing.objects.get(product=self.sold_out)
        Restock.objects.create(product=self.etb, retailer=self.harbour, listing=self.cheap, at=now - timedelta(minutes=10), price="54.99")
        Restock.objects.create(product=self.sold_out, retailer=self.harbour, listing=sold, at=now - timedelta(days=1, hours=2), price="140.00")
        body = self.client.get(reverse("web:deals")).content.decode()
        self.assertIn("Every time a shop we check went from sold out to in stock", body)
        self.assertIn(timezone.localtime(now).strftime("%A %-d %B"), body)
        self.assertIn("sold out again", body)
        self.assertLess(body.index(self.etb.name + "</span>"), body.index(self.sold_out.name + "</span>"))


class WatchlistTests(PageTestCase):
    HUMAN = {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone) Safari/605.1"}

    def test_lists_the_named_products_in_order_with_live_prices(self):
        url = reverse("web:watchlist") + f"?p={self.sold_out.slug},{self.etb.slug},no-such-product"
        response = self.client.get(url)
        body = response.content.decode()
        self.assertLess(body.index(f'data-watch-row="{self.sold_out.slug}"'), body.index(f'data-watch-row="{self.etb.slug}"'))
        self.assertNotIn("no-such-product", body)
        self.assertIn('data-now="54.99"', body)
        self.assertIn("delivered at Harbour Games", body)
        self.assertIn(f'href="{self.cheap.get_outbound_url()}?from=watchlist" target="_blank" rel="sponsored nofollow noopener"', body)
        self.assertIn("Last seen at £140.00", body)
        self.assertIn("Saved on this device only.", body)
        self.assertIn('<meta name="robots" content="noindex, follow">', body)
        self.assertIn("<title>Your watchlist | RipRaptor</title>", body)

    def test_an_old_address_still_finds_its_product(self):
        from catalogue.models import ProductAlias

        ProductAlias.objects.create(slug="old-etb-address", product=self.etb)
        body = self.client.get(reverse("web:watchlist") + "?p=old-etb-address").content.decode()
        self.assertIn(f'data-watch-row="{self.etb.slug}"', body)

    def test_an_empty_list_explains_itself(self):
        response = self.client.get(reverse("web:watchlist"))
        self.assertContains(response, "Nothing saved yet")
        self.assertContains(response, "See today&#x27;s deals")

    def test_save_links_work_without_the_script_and_carry_what_it_needs(self):
        page = self.client.get(self.etb.get_absolute_url()).content.decode()
        self.assertIn(f'href="/watchlist/?p={self.etb.slug}" data-watch="{self.etb.slug}" data-watch-id="{self.etb.pk}"', page)
        self.assertIn('data-watch-price="54.99"', page)
        sold = self.client.get(self.sold_out.get_absolute_url()).content.decode()
        self.assertIn(f'data-watch="{self.sold_out.slug}"', sold)
        self.assertIn('data-watch-price=""', sold)
        results = self.client.get(reverse("web:search") + "?q=etb").content.decode()
        self.assertIn(f'button--save button--small" href="/watchlist/?p={self.etb.slug}"', results)
        self.assertIn('data-watch-link="/watchlist/"', page)
        self.assertIn('<span data-watch-count></span>', page)

    def test_clicks_from_a_watchlist_are_marked_and_other_sources_are_not(self):
        self.client.get(self.cheap.get_outbound_url() + "?from=watchlist", HTTP_USER_AGENT="Mozilla/5.0 Firefox/130.0")
        self.client.get(self.cheap.get_outbound_url() + "?from=elsewhere", HTTP_USER_AGENT="Mozilla/5.0 Firefox/130.0")
        self.assertEqual(list(OutboundClick.objects.order_by("-pk").values_list("source", flat=True)[:2]), ["", "watchlist"])

    def test_each_product_on_a_loaded_list_is_counted_and_the_page_is_too(self):
        from catalogue.models import DailyPageView

        url = reverse("web:watchlist") + f"?p={self.etb.slug},{self.sold_out.slug}"
        self.client.get(url, **self.HUMAN)
        self.client.get(url, **self.HUMAN)
        self.client.get(url, HTTP_USER_AGENT="Googlebot/2.1")
        rows = {(r.kind, r.key): r.hits for r in DailyPageView.objects.all()}
        self.assertEqual(rows, {("watched", self.etb.slug): 2, ("watched", self.sold_out.slug): 2, ("watchlist", ""): 2})

    def test_robots_keeps_watchlists_out_of_search_engines(self):
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /watchlist/")


class PickUpTests(PageTestCase):
    def test_recent_row_prices_the_named_products_in_order(self):
        body = self.client.get(reverse("web:recent_api") + f"?p={self.sold_out.slug},{self.etb.slug},nope").content.decode()
        self.assertLess(body.index(self.sold_out.name), body.index(self.etb.name))
        self.assertIn("£54.99 delivered", body)
        self.assertIn("Last checked", body)
        self.assertNotIn("<html", body)
        self.assertEqual(self.client.get(reverse("web:recent_api")).content.decode().count("trending__item"), 0)

    def test_pages_carry_what_the_script_remembers_and_the_home_page_has_the_section(self):
        product = self.client.get(self.etb.get_absolute_url()).content.decode()
        self.assertIn(f'data-viewed-slug="{self.etb.slug}" data-viewed-name="{self.etb.name}"', product)
        results = self.client.get(reverse("web:search") + "?q=etb").content.decode()
        self.assertIn('<span data-search-query="etb" hidden></span>', results)
        home = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('data-resume hidden', home)
        self.assertIn("Pick up where you left off", home)
        self.assertIn("Clear history", home)
        self.assertIn('data-endpoint="/api/recent/"', home)
        self.assertIn('data-recent="Recent searches"', home)
        self.assertContains(self.client.get(reverse("web:terms")), "searches you make are stored in your browser")


class AmazonLeadTests(PageTestCase):
    def test_amazon_leads_the_box_when_nothing_is_in_stock(self):
        with self.settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            sold = self.client.get(self.sold_out.get_absolute_url()).content.decode()
            priced = self.client.get(self.etb.get_absolute_url()).content.decode()
        self.assertIn("buybox__amazon--lead", sold)
        self.assertIn("Sold out at the shops we check. Amazon often has it", sold)
        self.assertLess(sold.index("buybox__amazon--lead"), sold.index("buybox__save"))
        self.assertNotIn("buybox__amazon--lead", priced)
        self.assertEqual(priced.count("Compare on Amazon"), 1)


class HomeScreenTests(PageTestCase):
    def test_the_offer_card_is_on_every_page_hidden_and_events_are_counted(self):
        from catalogue.models import DailyPageView

        home = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('data-install data-note="/api/note/" hidden', home)
        self.assertIn("Add to home screen", home)
        self.assertIn("Not now", home)
        human = {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone) Safari/605.1"}
        for what in ("shown", "added", "dismissed", "opened", "opened", "nonsense"):
            self.assertEqual(self.client.get(reverse("web:note_api") + f"?what={what}", **human).status_code, 204)
        self.client.get(reverse("web:note_api") + "?what=shown", HTTP_USER_AGENT="Googlebot/2.1")
        rows = {r.key: r.hits for r in DailyPageView.objects.filter(kind="install")}
        self.assertEqual(rows, {"shown": 1, "added": 1, "dismissed": 1, "opened": 2})
        staff = self.client
        from django.contrib.auth.models import User

        staff.force_login(User.objects.create_user("ben3", password="pw", is_staff=True))
        page = staff.get(reverse("insights")).content.decode()
        self.assertIn("<td>Added it</td><td class=\"n\">1</td>", page)
        self.assertNotIn("<td>Home screen</td>", page)

    def test_manifest_names_the_site_and_its_icons(self):
        import json as json_module

        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        data = json_module.loads(response.content)
        self.assertEqual((data["name"], data["start_url"], data["display"]), ("RipRaptor", "/", "standalone"))
        self.assertEqual([i["sizes"] for i in data["icons"]], ["192x192", "512x512", "512x512"])
        self.assertEqual(data["icons"][2]["purpose"], "maskable")
        from django.contrib.staticfiles import finders

        for icon in data["icons"]:
            self.assertTrue(finders.find(icon["src"].replace("/static/", "", 1)), icon["src"])

    def test_pages_link_the_manifest_and_the_apple_icon(self):
        page = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', page)
        self.assertIn('<link rel="apple-touch-icon" href="/static/img/apple-touch-icon.png">', page)


class FeedTests(PageTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def feed(self, url):
        import xml.etree.ElementTree as ET

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/rss+xml", response["Content-Type"])
        return ET.fromstring(response.content)

    def test_deals_feed_carries_restocks_and_price_drops_newest_first(self):
        from catalogue.models import Restock

        # The fixture holds £60 nine days ago, £57 five days ago and £54.99 today: the drop is dated today.
        today = timezone.localdate()
        Restock.objects.create(product=self.sold_out, retailer=self.harbour, at=timezone.now() - timedelta(hours=1), price="140.00")
        channel = self.feed(reverse("web:feed_deals")).find("channel")
        self.assertEqual(channel.findtext("title"), "RipRaptor: restocks and price drops")
        self.assertEqual(channel.findtext("link"), "http://testserver/deals/")
        items = channel.findall("item")
        self.assertEqual(
            [item.findtext("title") for item in items],
            [
                "Back in stock: Prismatic Evolutions Super-Premium Collection, £140.00 at Harbour Games",
                "Price drop: Prismatic Evolutions Elite Trainer Box, now £54.99 delivered, was £57.00",
            ],
        )
        self.assertEqual(items[0].findtext("link"), "http://testserver" + self.sold_out.get_absolute_url())
        self.assertIn("£140.00 delivered", items[0].findtext("description"))
        self.assertTrue(items[0].findtext("pubDate"))
        self.assertEqual(items[1].find("guid").get("isPermaLink"), "false")
        self.assertIn(f"fell from £57.00 to £54.99 on {today.strftime('%-d %b')}", items[1].findtext("description"))

    def test_game_feed_keeps_to_its_game_and_unknown_games_are_404(self):
        from catalogue.models import Restock

        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        box = make_product(make_set(magic, name="Foundations", slug="foundations"), name="Foundations Play Booster Box", product_type="booster_box")
        Restock.objects.create(product=box, retailer=self.harbour, at=timezone.now(), price="110.00")
        Restock.objects.create(product=self.etb, retailer=self.harbour, at=timezone.now(), price="54.99")
        titles = [i.findtext("title") for i in self.feed(reverse("web:feed_game", args=["pokemon"])).find("channel").findall("item")]
        self.assertEqual([t for t in titles if t.startswith("Back in stock")], ["Back in stock: Prismatic Evolutions Elite Trainer Box, £54.99 at Harbour Games"])
        magic_titles = [i.findtext("title") for i in self.feed(reverse("web:feed_game", args=["magic-the-gathering"])).find("channel").findall("item")]
        self.assertEqual(magic_titles, ["Back in stock: Foundations Play Booster Box, £110.00 at Harbour Games"])
        self.assertEqual(self.feed(reverse("web:feed_game", args=["pokemon"])).find("channel").findtext("title"), "RipRaptor: Pokémon restocks and price drops")
        self.assertEqual(self.client.get("/feeds/no-such-game.xml").status_code, 404)

    def test_pages_announce_the_feeds_and_the_deals_page_shows_the_address(self):
        home = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<link rel="alternate" type="application/rss+xml" title="RipRaptor: restocks and price drops" href="/feeds/deals.xml">', home)
        self.assertNotIn("/feeds/pokemon.xml", home)
        game = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn('title="RipRaptor: Pokémon restocks and price drops" href="/feeds/pokemon.xml"', game)
        deals = self.client.get(reverse("web:deals")).content.decode()
        self.assertIn("Follow restocks and price drops in a feed reader", deals)
        self.assertIn('<a href="/feeds/deals.xml">http://testserver/feeds/deals.xml</a>', deals)

    def test_a_restock_recorded_by_a_check_reaches_the_feed_without_waiting_for_the_cache(self):
        from decimal import Decimal

        from catalogue import pricing

        before = [i.findtext("title") for i in self.feed(reverse("web:feed_deals")).find("channel").findall("item")]
        self.assertEqual([t for t in before if t.startswith("Back in stock")], [])
        sold = Listing.objects.get(product=self.sold_out)
        pricing.record_check(sold, price=Decimal("139.00"), delivery_cost=Decimal("0"), availability="in_stock")
        titles = [i.findtext("title") for i in self.feed(reverse("web:feed_deals")).find("channel").findall("item")]
        self.assertIn("Back in stock: Prismatic Evolutions Super-Premium Collection, £139.00 at Harbour Games", titles)


class DealsAndAliasTests(PageTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_deals_page_lists_savings_with_a_buy_button_and_is_linked_everywhere(self):
        response = self.client.get(reverse("web:deals"))
        self.assertContains(response, "best sealed TCG deals")
        self.assertContains(response, self.etb.name)
        self.assertContains(response, "Save £3.01")
        self.assertContains(response, 'rel="sponsored nofollow noopener"')
        self.assertContains(response, '<link rel="canonical" href="http://testserver/deals/">')
        self.assertContains(response, '"@type": "BreadcrumbList"')
        self.assertContains(self.client.get(reverse("web:home")), 'href="/deals/"')
        self.assertContains(self.client.get("/sitemap.xml"), "http://testserver/deals/")

    def test_deals_page_copes_with_nothing_to_show(self):
        Listing.objects.all().delete()
        from django.core.cache import cache

        cache.clear()
        self.assertContains(self.client.get(reverse("web:deals")), "No deals to show yet")

    def test_old_product_address_redirects_for_good(self):
        from catalogue.models import ProductAlias

        ProductAlias.objects.create(slug="old-etb-address", product=self.etb)
        response = self.client.get("/products/old-etb-address/")
        self.assertEqual((response.status_code, response["Location"]), (301, self.etb.get_absolute_url()))
        self.assertEqual(self.client.get("/products/never-existed/").status_code, 404)

    def test_merging_duplicates_records_the_old_address(self):
        from io import StringIO

        from django.core.management import call_command

        from catalogue.models import ProductAlias

        twin = make_product(self.pre, name="Scarlet & Violet 8.5 Prismatic Evolutions Elite Trainer Box", slug="sv85-pev-etb")
        make_listing(twin, make_retailer("Third Shop"), price="56.00")
        call_command("merge_duplicates", stdout=StringIO())
        self.assertFalse(Product.objects.filter(pk=twin.pk).exists())
        self.assertEqual(ProductAlias.objects.get(slug="sv85-pev-etb").product, self.etb)
        self.assertEqual(self.client.get("/products/sv85-pev-etb/").status_code, 301)
        self.assertEqual(self.etb.listings.count(), 3)


class GameTypesAndFilterTests(PageTestCase):
    def setUp(self):
        self.magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        self.bloom = make_set(self.magic, name="Bloomburrow", slug="bloomburrow")
        self.play_box = make_product(self.bloom, name="Bloomburrow Play Booster Box", slug="blb-play-box", product_type="booster_box")
        self.collector_box = make_product(
            self.bloom, name="Bloomburrow Collector Booster Box", slug="blb-collector-box", product_type="collector_booster_box"
        )
        make_listing(self.play_box, self.harbour, price="110.00")
        make_listing(self.collector_box, self.harbour, price="230.00")

    def test_type_filter_speaks_the_games_language_and_lists_only_types_it_has(self):
        response = self.client.get(self.magic.get_absolute_url())
        self.assertContains(response, '<option value="booster_box">Play booster box</option>')
        self.assertContains(response, '<option value="collector_booster_box">Collector booster box</option>')
        self.assertNotContains(response, "Elite Trainer Box</option>")
        self.assertNotContains(response, '<option value="deck">')
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, '<option value="elite_trainer_box">Elite Trainer Box</option>')
        self.assertNotContains(response, "Play booster box")

    def test_search_type_filter_follows_the_chosen_game(self):
        response = self.client.get(reverse("web:search"), {"game": "magic-the-gathering"})
        self.assertContains(response, "Play booster box")
        response = self.client.get(reverse("web:search"))
        self.assertContains(response, '<option value="booster_box">Booster box</option>')

    def test_product_pages_use_the_games_words_for_the_type(self):
        self.assertContains(self.client.get(self.play_box.get_absolute_url()), "Play booster box")
        self.assertContains(self.client.get(self.etb.get_absolute_url()), "Elite Trainer Box")

    def test_price_band_and_two_shops_filters(self):
        names = lambda **params: [c[0].name for c in self.client.get(reverse("web:search"), params).context["cards"]]
        self.assertEqual(sorted(names(price="100-")), ["Bloomburrow Collector Booster Box", "Bloomburrow Play Booster Box"])
        self.assertEqual(names(price="50-100"), [self.etb.name])
        self.assertEqual(names(compared="on"), [self.etb.name])
        self.assertContains(self.client.get(reverse("web:search"), {"price": "0-10"}), "Clear filters")

    def test_collector_boosters_classify_as_their_own_type(self):
        from decimal import Decimal

        from catalogue.classify import classify

        sealed = classify("Magic: The Gathering Bloomburrow Collector Booster Box", "", "", (), Decimal("230"))
        self.assertEqual(sealed.product_type, "collector_booster_box")
        sealed = classify("MTG Bloomburrow Collector Booster Pack", "", "", (), Decimal("25"))
        self.assertEqual(sealed.product_type, "collector_booster_pack")
        sealed = classify("MTG Bloomburrow Play Booster Box", "", "", (), Decimal("110"))
        self.assertEqual(sealed.product_type, "booster_box")

    def test_tidy_retypes_old_collector_boxes(self):
        from io import StringIO

        from django.core.management import call_command

        old = make_product(self.bloom, name="Duskmourn Collector Booster Box", slug="dsk-cb", product_type="booster_box")
        make_listing(old, self.harbour, price="200.00")
        call_command("tidy_catalogue", stdout=StringIO())
        old.refresh_from_db()
        self.assertEqual(old.product_type, "collector_booster_box")

    def test_deck_filters_by_type_with_the_games_words(self):
        response = self.client.get(reverse("web:deck"), {"game": "magic-the-gathering"})
        self.assertContains(response, "Collector booster box</option>")
        names = [c["name"] for c in self.client.get(reverse("web:deck_api"), {"game": "magic-the-gathering", "type": "collector_booster_box"}).json()["cards"]]
        self.assertEqual(names, ["Bloomburrow Collector Booster Box"])


class HomeFootballTests(PageTestCase):
    def test_football_is_pinned_among_the_first_chips_and_has_its_own_row(self):
        from django.core.cache import cache

        football = make_game(name="Football cards", slug="football", search_aliases="")
        attax = make_set(football, name="Match Attax 2026/27", slug="match-attax-2026-27", code="MA27")
        tin = make_product(attax, name="Match Attax 2026/27 Mega Tin", slug="ma27-mega-tin", product_type="tin")
        make_listing(tin, self.harbour, price="14.99")
        cache.clear()
        response = self.client.get(reverse("web:home"))
        html = response.content.decode()
        chips = html[html.index('class="chips"'):html.index("</nav>", html.index('class="chips"'))]
        self.assertIn("/games/football/", chips.split("chips__extra")[0])
        self.assertContains(response, "Football and sports cards")
        self.assertContains(response, "Match Attax 2026/27 Mega Tin")


class LatestDropsTests(PageTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.old_set = make_set(self.game, name="Base Set", slug="base-set", code="BS", release_date=timezone.localdate() - timedelta(days=400))
        self.old = make_product(self.old_set, name="Base Set Booster Box", slug="base-set-box", product_type="booster_box")
        make_listing(self.old, self.harbour, price="400.00")
        Product.objects.filter(pk=self.old.pk).update(created_at=timezone.now() - timedelta(days=100))
        self.soon_set = make_set(self.game, name="Mega Evolution", slug="mega-evolution", code="ME1", release_date=timezone.localdate() + timedelta(days=10))
        self.soon = make_product(self.soon_set, name="Mega Evolution Elite Trainer Box", slug="me1-etb")
        make_listing(self.soon, self.harbour, price="49.99", availability=Listing.Availability.PREORDER)

    def test_home_row_leads_with_the_newest_release_and_skips_old_stock(self):
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "Latest drops")
        html = response.content.decode()
        self.assertLess(html.index('id="savings-title"'), html.index('id="latest-title"'))
        row = html[html.index('id="latest-title"'):html.index("</section>", html.index('id="latest-title"'))]
        self.assertIn("Mega Evolution Elite Trainer Box", row)
        self.assertIn("Out ", row)
        self.assertNotIn("Base Set Booster Box", row)
        self.assertLess(row.index("Mega Evolution"), row.index("Prismatic Evolutions Elite Trainer Box"))

    def test_latest_drops_page_lists_new_products_with_filters_and_is_in_the_sitemap(self):
        response = self.client.get(reverse("web:new"))
        self.assertContains(response, "Latest drops")
        self.assertContains(response, "Mega Evolution Elite Trainer Box")
        self.assertNotContains(response, "Base Set Booster Box")
        self.assertContains(response, 'name="price"')
        self.assertContains(response, '<link rel="canonical" href="http://testserver/new/">')
        self.assertContains(self.client.get("/sitemap.xml"), "http://testserver/new/")
        self.assertContains(self.client.get(reverse("web:home")), 'href="/new/"')

    def test_a_product_shops_only_just_listed_counts_as_a_drop_even_without_a_release_date(self):
        fresh = make_product(self.old_set, name="Base Set Blister", slug="base-set-blister", product_type="bundle")
        make_listing(fresh, self.harbour, price="12.00")
        self.assertContains(self.client.get(reverse("web:new")), "Base Set Blister")

    def test_a_pre_order_counts_as_a_drop_without_any_release_date_and_leads_the_list(self):
        from django.core.cache import cache

        Product.objects.update(created_at=timezone.now() - timedelta(days=100))
        loose = make_product(self.old_set, name="Phantasmal Flames Booster Bundle", slug="pfl-bundle", product_type="bundle")
        Product.objects.filter(pk=loose.pk).update(created_at=timezone.now() - timedelta(days=100))
        make_listing(loose, self.harbour, price="29.99", availability=Listing.Availability.PREORDER)
        cache.clear()
        response = self.client.get(reverse("web:new"))
        names = [c[0].name for c in response.context["cards"]]
        self.assertEqual(names[:2], ["Mega Evolution Elite Trainer Box", "Phantasmal Flames Booster Bundle"])
        self.assertIn("Phantasmal Flames Booster Bundle", self.client.get(reverse("web:home")).content.decode())

    def test_pre_orders_are_marked_dropping_soon_and_muted(self):
        home = self.client.get(reverse("web:home")).content.decode()
        row = home[home.index('id="latest-title"'):home.index("</section>", home.index('id="latest-title"'))]
        self.assertIn("Dropping soon", row)
        self.assertIn("is-upcoming", row)
        page = self.client.get(reverse("web:new")).content.decode()
        self.assertIn('class="card is-upcoming"', page)
        self.assertIn("Dropping soon", page)
        # A product that is out is not marked.
        self.assertNotIn("is-upcoming", self.client.get(self.game.get_absolute_url(), {"q": ""}).content.decode().split("Mega Evolution")[0].split('class="card"')[-1])

    def test_the_launch_import_itself_is_not_a_drop(self):
        from django.core.cache import cache

        # Everything created at launch: only products with a recent release date count.
        Product.objects.update(created_at=timezone.now() - timedelta(hours=3))
        cache.clear()
        response = self.client.get(reverse("web:new"))
        self.assertContains(response, "Mega Evolution Elite Trainer Box")
        self.assertNotContains(response, "Base Set Booster Box")
        self.assertNotContains(response, "Prismatic Evolutions Booster Bundle")
