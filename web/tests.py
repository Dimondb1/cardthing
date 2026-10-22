import re
import shutil
import tempfile
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue.models import DailyLowestPrice, Listing, OutboundClick, Product, Retailer
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
            reverse("web:contact"),
            reverse("web:list_shop"),
            reverse("web:list_shop_thanks"),
            reverse("web:drops"),
            reverse("web:drops") + "?show=out",
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
        for heading in ("Find your game", "Featured deals", "Save £3.01 vs Northgate Cards", "Browse by product"):
            self.assertContains(response, heading)
        # The deeper lists live on the Deals and Games pages so the home page stays short.
        for moved in ("Trending now", "Biggest savings", "Biggest price drop", "Recently released", "Browse by game", "Back in stock"):
            self.assertNotContains(response, moved)

    def test_deals_page_carries_what_left_the_home_page(self):
        response = self.client.get(reverse("web:deals"))
        self.assertContains(response, "Trending now")
        self.assertContains(response, "Biggest savings right now")
        self.assertContains(response, self.etb.name)

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
        # The time under the headline price is that shop's, not the newest check of any shop.
        self.assertContains(response, "Price checked at Harbour Games 1 hour ago.")
        self.assertNotContains(response, "Last checked")
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

    def test_deals_page_shows_price_drops(self):
        make_listing(self.unpriced, self.harbour, price="30.00")
        DailyLowestPrice.objects.create(
            product=self.unpriced, date=timezone.localdate() - timedelta(days=7), price="36.00"
        )
        response = self.client.get(reverse("web:deals"))
        self.assertContains(response, "Biggest price drops")
        self.assertContains(response, "was £36.00")
        self.assertNotContains(self.client.get(reverse("web:home")), "was £36.00")

    def test_robots_txt(self):
        response = self.client.get("/robots.txt")
        self.assertContains(response, "Disallow: /go/")


class OutboundTests(PageTestCase):
    def test_redirects_and_counts_the_click(self):
        before = OutboundClick.objects.filter(product=self.etb).count()
        response = self.client.get(
            self.cheap.get_outbound_url(), HTTP_USER_AGENT="Mozilla/5.0 Firefox/130.0",
            HTTP_REFERER="http://testserver/",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.cheap.url)
        self.assertEqual(OutboundClick.objects.filter(product=self.etb).count(), before + 1)

    def test_uses_the_affiliate_link(self):
        self.harbour.affiliate_url_template = "https://network.example/c?u={url}"
        self.harbour.save()
        response = self.client.get(self.cheap.get_outbound_url(), HTTP_USER_AGENT="Mozilla/5.0 Firefox/130.0",
                                   HTTP_REFERER="http://testserver/")
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

    def test_no_template_notes_show_on_a_page(self):
        # A {# note #} that runs over two lines is printed on the page, not hidden.
        for url in self.public_urls() + ["/no-such-page/"]:
            html = self.client.get(url).content.decode()
            visible_text = re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html))
            for mark in ("{#", "#}", "{%", "%}", "{{", "}}"):
                with self.subTest(url=url, mark=mark):
                    self.assertNotIn(mark, visible_text)

    def test_every_template_note_closes_on_its_own_line(self):
        from pathlib import Path

        for path in Path(__file__).resolve().parent.joinpath("templates").rglob("*.html"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "{#" in line:
                    with self.subTest(template=str(path), line=number):
                        self.assertIn("#}", line[line.index("{#"):])

    def test_no_exclamation_marks(self):
        for url in self.public_urls():
            html = self.client.get(url).content.decode()
            visible_text = re.sub(r"<[^>]*>", " ", html)
            with self.subTest(url=url):
                self.assertNotIn("!", visible_text)

    def test_the_product_page_alert_form_keeps_the_house_style(self):
        mail_on = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com",
                   "RIPRAPTOR_PREORDER_ALERTS_FROM": timezone.now() - timedelta(days=1)}
        with self.settings(**mail_on):
            for url in (self.sold_out.get_absolute_url(), self.unpriced.get_absolute_url()):
                html = self.client.get(url).content.decode()
                with self.subTest(url=url):
                    self.assertIn("Tell me when a shop has it or opens pre-orders", html)
                    self.assertNotIn("!", re.sub(r"<[^>]*>", " ", html))
                    for phrase in self.BANNED:
                        self.assertNotIn(phrase, html.lower())


class ServerErrorPageTests(TestCase):
    def test_500_page_renders_without_a_database_or_context(self):
        from django.template.loader import get_template

        html = get_template("500.html").render({})
        self.assertIn("Something went wrong", html)
        self.assertNotIn("—", html)


class SortTests(PageTestCase):
    def test_best_match_only_offered_with_a_query(self):
        # A sort chosen keeps the game page's sort and filters in view, however short the list.
        browse = self.client.get(self.game.get_absolute_url() + "?sort=price").content.decode()
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
        self.assertContains(self.client.get(reverse("web:home")), "Latest shop price check 1 hour ago")


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
        self.assertContains(response, "Compare 2 shops")
        # The two cheapest shops as rows: the cheapest flagged, the dearer in red with an arrow.
        self.assertContains(response, '<span class="shops__name">Harbour Games</span><span class="shops__flag">Cheapest</span><span class="shops__price">£54.99</span>')
        self.assertContains(response, '<span class="shops__name">Northgate Cards</span><span class="shops__price shops__price--more">£58.00 <span aria-hidden="true">↑</span></span>')
        self.assertContains(response, '<p class="card__meta">Pokémon<span aria-hidden="true"> · </span><span class="visually-hidden">, </span>Elite Trainer Box</p>')
        # The filters are one select each and two switches, not a row of checkboxes.
        self.assertContains(response, '<select name="lang" id="id_lang">')
        self.assertContains(response, '<option value="" selected>Language</option>')
        self.assertContains(response, '<label class="toggle"><input type="checkbox" name="in_stock" id="id_in_stock">')
        self.assertNotContains(response, 'class="pill pill--check')


class TermsAndRefreshTests(PageTestCase):
    def test_terms_page_holds_the_disclosures(self):
        response = self.client.get(reverse("web:terms"))
        self.assertContains(response, "commission")
        self.assertContains(response, "independent")
        self.assertContains(response, "kept in your browser, not by us")

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
        # Reload prices replaces the line under the headline price with this, so it keeps naming its own shop.
        self.assertEqual(row["checked_line"], "Price checked at Harbour Games 1 hour ago.")

    def test_product_page_has_refresh_control(self):
        response = self.client.get(self.etb.get_absolute_url())
        self.assertContains(response, 'data-refresh="')
        self.assertContains(response, "Reload prices")
        self.assertNotContains(response, "Check prices")
        self.assertContains(response, f'data-listing="{self.cheap.pk}"')
        self.assertContains(response, f'data-checked-for="{self.cheap.pk}"')
        # Reloading reads what we hold; it must not claim the shops were just asked.
        self.assertContains(response, 'data-done="Showing our latest checks. Each shop&#x27;s time is beside its price."')


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
        data, crumbs, page = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual(data["@type"], "Product")
        self.assertEqual(page["@type"], "ItemPage")
        self.assertEqual(crumbs["@type"], "BreadcrumbList")
        self.assertEqual([c["name"] for c in crumbs["itemListElement"]], ["Pokémon", "Prismatic Evolutions", self.etb.name])
        self.assertEqual(data["offers"]["lowPrice"], "54.99")
        self.assertEqual(data["offers"]["offerCount"], 2)

    def test_home_lists_are_cached_and_cleared_by_imports(self):
        from django.core.cache import cache

        from catalogue.importers import run_import
        from catalogue.models import Retailer
        from catalogue.testing import lists_due

        cache.clear()
        self.client.get(reverse("web:home"))
        self.assertFalse(lists_due("web:home-lists:v6"))
        self.harbour.source_type = Retailer.Source.SHOPIFY
        self.harbour.source_url = "https://h.example/"
        self.harbour.save()
        # The import marks the lists stale for the jobs to build again in place: the copy stays meanwhile.
        run_import(self.harbour, fetch=lambda url: b'{"products": []}')
        self.assertIsNotNone(cache.get("web:home-lists:v6"))
        self.assertTrue(lists_due("web:home-lists:v6"))

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
    ROW = "Check price and stock on Amazon"

    def test_no_link_without_a_tracking_tag(self):
        response = self.client.get(self.etb.get_absolute_url())
        self.assertNotContains(response, "Compare on Amazon")
        self.assertNotContains(response, self.ROW)

    def test_tagged_search_link_when_we_have_no_amazon_price(self):
        from django.test import override_settings

        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            response = self.client.get(self.etb.get_absolute_url())
        self.assertContains(response, "Compare on Amazon")
        self.assertContains(response, f'href="/go/search/{self.etb.slug}/amazon/?b=amazon"')
        self.assertNotContains(response, "tag=ripraptor-21")
        self.assertContains(response, 'rel="sponsored nofollow noopener"')

    def test_amazon_is_the_last_row_of_every_price_list(self):
        from django.test import override_settings

        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            html = self.client.get(self.etb.get_absolute_url()).content.decode()
            # Two real shops, then the Amazon row, in the visible list.
            rows = html[html.index('<ul class="offers">'):html.index("</ul>", html.index('<ul class="offers">'))]
            self.assertEqual(rows.count('data-listing="'), 2)
            self.assertEqual(rows.count("offer--amazon"), 1)
            self.assertLess(rows.index("Northgate Cards"), rows.index(self.ROW))
            self.assertIn('<li class="offer offer--amazon">', rows)
            self.assertIn(self.ROW, rows)
            self.assertIn('Check on Amazon<span class="visually-hidden"> (opens in a new tab)</span></a>', rows)
            self.assertIn(f'href="/go/search/{self.etb.slug}/amazon/?b=amazon" data-click="amazon"', rows)
            self.assertIn('target="_blank" rel="sponsored nofollow noopener">Check on Amazon', rows)
            self.assertEqual(html.count(self.ROW), 1)
            # Sold out everywhere: the row follows the out-of-stock shop.
            sold = self.client.get(self.sold_out.get_absolute_url()).content.decode()
            rows = sold[sold.index('<ul class="offers">'):sold.index("</ul>", sold.index('<ul class="offers">'))]
            self.assertLess(rows.index("Harbour Games"), rows.index(self.ROW))
            # No prices at all: the list is the Amazon row alone, with nothing to reload.
            none = self.client.get(self.unpriced.get_absolute_url()).content.decode()
            self.assertIn(self.ROW, none)
            self.assertIn('<h2 id="offers-title"', none)
            self.assertNotIn("Reload prices", none)
        self.assertNotIn('id="offers-title"', self.client.get(self.unpriced.get_absolute_url()).content.decode())

    def test_no_search_link_once_amazon_has_a_real_price(self):
        from django.test import override_settings

        from catalogue.models import Retailer

        amazon = make_retailer("Amazon", slug="amazon", source_type=Retailer.Source.AMAZON)
        make_listing(self.etb, amazon, price="80.00")
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            response = self.client.get(self.etb.get_absolute_url())
        self.assertNotContains(response, "Compare on Amazon")
        self.assertNotContains(response, self.ROW)   # Amazon's real price is in the list instead
        self.assertContains(response, '<span class="offer__name">Amazon</span>')


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
        self.assertContains(response, '<option value="ja" selected>')
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
        # The longest wording that fits in about 60 characters before the site name (web/hubs.py).
        self.assertContains(response, "<title>Prismatic Evolutions Elite Trainer Box UK from £54.99 | RipRaptor</title>")
        self.assertContains(response, 'content="Cheapest Prismatic Evolutions Elite Trainer Box today is £54.99 delivered at Harbour Games. Compare 2 UK shops')
        self.assertContains(response, '<meta property="og:type" content="product">')
        self.assertContains(response, '<meta property="og:url" content="http://testserver' + self.etb.get_absolute_url() + '">')
        self.assertContains(response, '"@type": "BreadcrumbList"')
        self.assertContains(response, '"@type": "Product"')

    def test_a_name_holding_a_script_tag_stays_inside_the_data_block(self):
        import json

        from django.core.cache import cache

        from catalogue.models import ProductSet

        cache.clear()
        name = "Prismatic Evolutions Elite Trainer Box </script><script>alert(1)</script>"
        Product.objects.filter(pk=self.etb.pk).update(name=name)
        ProductSet.objects.filter(pk=self.pre.pk).update(name="Prismatic Evolutions </script><img src=x onerror=alert(1)>")
        crumbs = {}
        for url in (self.etb.get_absolute_url(), self.pre.get_absolute_url()):
            html = self.client.get(url).content.decode()
            self.assertNotIn("</script><script>", html, url)
            self.assertNotIn("<img src=x", html, url)
            start = html.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
            blocks = json.loads(html[start:html.index("</script>", start)])
            # The names read back whole: the escapes are only how they are written.
            crumbs[url] = [crumb["name"] for block in (blocks if isinstance(blocks, list) else [blocks])
                           if block["@type"] == "BreadcrumbList" for crumb in block["itemListElement"]]
        self.assertEqual(crumbs[self.etb.get_absolute_url()][1:],
                         ["Prismatic Evolutions </script><img src=x onerror=alert(1)>", name])
        self.assertEqual(crumbs[self.pre.get_absolute_url()][1:],
                         ["Prismatic Evolutions </script><img src=x onerror=alert(1)>"])

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
        self.assertEqual(unlisted, ["BreadcrumbList", "ItemPage"])
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
        # The heading is the restock's own day: just after midnight, ten minutes ago is yesterday.
        recent = now - timedelta(minutes=10)
        sold = Listing.objects.get(product=self.sold_out)
        Restock.objects.create(product=self.etb, retailer=self.harbour, listing=self.cheap, at=recent, price="54.99")
        Restock.objects.create(product=self.sold_out, retailer=self.harbour, listing=sold, at=now - timedelta(days=1, hours=2), price="140.00")
        body = self.client.get(reverse("web:deals")).content.decode()
        self.assertIn("Every time a shop we check went from sold out to in stock", body)
        self.assertIn(timezone.localtime(recent).strftime("%A %-d %B"), body)
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
        self.assertIn(f'href="{self.cheap.get_outbound_url()}?b=watchlist" data-click="watchlist"', body)
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
        # On a card the button is the bookmark alone; its words stay for screen readers and the script.
        self.assertIn(f'button--save button--icon" href="/watchlist/?p={self.etb.slug}"', results)
        self.assertIn('<span data-watch-label class="visually-hidden">Save</span>', results)
        self.assertIn('data-watch-link="/watchlist/"', page)
        self.assertIn('<span data-watch-count></span>', page)

    def test_clicks_from_a_watchlist_are_marked_by_the_page_they_came_from(self):
        page = {"HTTP_USER_AGENT": "Mozilla/5.0 Firefox/130.0", "HTTP_REFERER": "http://testserver/watchlist/"}
        self.client.get(self.cheap.get_outbound_url() + "?b=watchlist", **page)
        self.client.get(self.cheap.get_outbound_url() + "?b=card", HTTP_USER_AGENT="Mozilla/5.0 Firefox/130.0",
                        HTTP_REFERER="http://testserver/search/")
        self.assertEqual(list(OutboundClick.objects.order_by("-pk").values_list("source", "button")[:2]),
                         [("search", "card"), ("watchlist", "watchlist")])

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
        self.assertIn('<span class="price">£54.99</span>', body)
        self.assertIn('<span class="delivered">delivered</span> · Harbour Games', body)
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
        self.assertContains(self.client.get(reverse("web:terms")), "searches you make are kept in your browser")


class AmazonLeadTests(PageTestCase):
    def test_amazon_leads_the_box_when_nothing_is_in_stock(self):
        with self.settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            sold = self.client.get(self.sold_out.get_absolute_url()).content.decode()
            priced = self.client.get(self.etb.get_absolute_url()).content.decode()
        self.assertIn("buybox__amazon--lead", sold)
        self.assertIn("Sold out at the shops we check. Amazon and eBay sellers often still have it", sold)
        self.assertLess(sold.index("buybox__amazon--lead"), sold.index("buybox__save"))
        self.assertNotIn("buybox__amazon--lead", priced)
        self.assertEqual(priced.count("Compare on Amazon"), 1)


class NetworkVerificationTests(PageTestCase):
    def test_impact_site_verification_tag_is_on_every_page(self):
        for url in (reverse("web:home"), self.etb.get_absolute_url()):
            self.assertContains(self.client.get(url), '<meta name="impact-site-verification" value="fb2ca9a2-0f96-46ce-b287-b35517e23ca5">')


class FeaturedAndMarketplaceTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.game = make_game()
        self.set = make_set(self.game)
        self.paying = make_retailer("Paying", affiliate_url_template="https://aff.example/?u={url}")
        self.plain = make_retailer("Plain")
        self.dear = make_retailer("Dear")

    def deal(self, n, shop):
        product = make_product(self.set, name=f"Deal Product {n} Booster Box", slug=f"deal-{n}", product_type="booster_box")
        make_listing(product, shop, price=f"{50 + n}.00")
        make_listing(product, self.dear, price=f"{60 + n}.00")
        return product

    def test_featured_is_three_in_four_earning_and_only_real_deals(self):
        from web.views import earns, home_lists

        for n in range(8):
            self.deal(n, self.paying)
        for n in range(8, 12):
            self.deal(n, self.plain)
        no_saving = make_product(self.set, name="Lonely Booster Box", slug="lonely", product_type="booster_box")
        make_listing(no_saving, self.paying, price="40.00")
        featured = home_lists()[0]
        self.assertEqual(len(featured), 8)
        self.assertEqual(sum(1 for _, s in featured if earns(s.best)), 6)
        self.assertNotIn(no_saving.pk, [p.pk for p, _ in featured])
        self.assertEqual([earns(s.best) for _, s in featured][:4], [True, True, True, False])
        body = self.client.get(reverse("web:home")).content.decode()
        self.assertIn("Featured deals", body)
        self.assertIn("featured deals favour shops that pay one", body)
        self.assertContains(self.client.get(reverse("web:terms")), "We favour shops that pay us a commission")

    def test_featured_fills_from_the_other_side_when_one_is_short(self):
        from web.views import earns, featured_deals
        from catalogue.models import Product as P
        from catalogue import offers

        for n in range(2):
            self.deal(n, self.paying)
        for n in range(2, 12):
            self.deal(n, self.plain)
        products = list(P.objects.for_lists().filter(in_stock_count__gte=1).prefetch_related(offers.buyable_prefetch()))
        featured = featured_deals(products)
        self.assertEqual((len(featured), sum(1 for _, s in featured if earns(s.best))), (8, 2))

    def test_sold_out_pages_offer_amazon_and_an_ebay_search_with_our_campaign(self):
        product = make_product(self.set, name="30th Celebration Booster Bundle", slug="30th-bundle", product_type="bundle")
        make_listing(product, self.plain, price="29.99", availability="out_of_stock")
        with self.settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21", RIPRAPTOR_EBAY_CAMPAIGN_ID="5339216899"):
            body = self.client.get(product.get_absolute_url()).content.decode()
        self.assertIn("Search eBay", body)
        # The buttons go through /go/search/, so the page itself never carries the campaign or the tag.
        self.assertNotIn("campid=", body)
        self.assertNotIn("tag=ripraptor-21", body)
        self.assertIn("Amazon and eBay sellers often still have it", body)
        found = re.findall(r'<a[^>]+href="/go/search/30th-bundle/(?:ebay|amazon)/\?b=(?:ebay|amazon)"[^>]+>', body)
        self.assertEqual(len(found), 3)   # Amazon and eBay in the price box, Amazon again as the last price row
        for tag in found:
            self.assertIn('rel="sponsored nofollow noopener"', tag)
        with self.settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21", RIPRAPTOR_EBAY_CAMPAIGN_ID="5339216899"):
            ebay = self.client.get("/go/search/30th-bundle/ebay/", HTTP_REFERER="http://testserver/products/30th-bundle/",
                                   HTTP_USER_AGENT="Mozilla/5.0 Safari/604.1")["Location"]
        self.assertTrue(ebay.startswith("https://www.ebay.co.uk/sch/i.html?_nkw=Pok%C3%A9mon+30th+Celebration+Booster+Bundle"))
        self.assertIn("campid=5339216899", ebay)
        self.assertIn("mkrid=710-53481-19255-0", ebay)
        # With an eBay price already in the comparison, no eBay search.
        make_listing(product, make_retailer("eBay", source_type="ebay"), price="40.00")
        with self.settings(RIPRAPTOR_EBAY_CAMPAIGN_ID="5339216899"):
            self.assertNotIn("Search eBay", self.client.get(product.get_absolute_url()).content.decode())


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
        # A drop is dated to the start of its day, so the restock is stamped now to be the newer entry.
        # An hour ago would be yesterday for the first hour after midnight, and rightly sort below the drop.
        today = timezone.localdate()
        Restock.objects.create(product=self.sold_out, retailer=self.harbour, at=timezone.now(), price="140.00")
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

    def test_feeds_announce_preorder_openings_beside_restocks_and_drops(self):
        from datetime import datetime, time

        from catalogue.models import PreorderOpen, Restock

        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        box = make_product(make_set(magic, name="Foundations", slug="foundations"), name="Foundations Play Booster Box", product_type="booster_box")
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        # A price drop is dated at local midnight, so the events here come after it whatever time the test runs.
        midnight = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
        now = max(timezone.now(), midnight + timedelta(minutes=15))
        restock = Restock.objects.create(product=self.sold_out, retailer=self.harbour, at=now - timedelta(minutes=5), price="140.00")
        opening = PreorderOpen.objects.create(product=self.unpriced, retailer=self.north, at=now, price="44.50")
        unknown = PreorderOpen.objects.create(product=box, retailer=self.harbour, at=now - timedelta(minutes=10), price="99.00",
                                              delivery_known=False)
        PreorderOpen.objects.create(product=self.unpriced, retailer=ebay, at=now, price="40.00")          # a marketplace
        PreorderOpen.objects.create(product=self.unpriced, retailer=self.harbour, at=now - timedelta(days=8), price="45.00")  # too old
        items = self.feed(reverse("web:feed_deals")).find("channel").findall("item")
        self.assertEqual(
            [(item.findtext("guid"), item.findtext("title")) for item in items],
            [
                (f"preorder-{opening.pk}", "Pre-orders open: Prismatic Evolutions Booster Bundle, £44.50 at Northgate Cards"),
                (f"restock-{restock.pk}", "Back in stock: Prismatic Evolutions Super-Premium Collection, £140.00 at Harbour Games"),
                (f"preorder-{unknown.pk}", "Pre-orders open: Foundations Play Booster Box, £99.00 at Harbour Games"),
                (f"drop-{self.etb.pk}-{timezone.localdate().isoformat()}",
                 "Price drop: Prismatic Evolutions Elite Trainer Box, now £54.99 delivered, was £57.00"),
            ],
        )
        self.assertIn("Pre-orders opened at Northgate Cards on", items[0].findtext("description"))
        self.assertIn("£44.50 delivered", items[0].findtext("description"))
        self.assertIn("£99.00 plus delivery", items[2].findtext("description"))
        self.assertEqual(items[0].findtext("link"), "http://testserver" + self.unpriced.get_absolute_url())
        self.assertEqual(items[0].find("guid").get("isPermaLink"), "false")
        magic_titles = [i.findtext("title") for i in self.feed(reverse("web:feed_game", args=["magic-the-gathering"])).find("channel").findall("item")]
        self.assertEqual(magic_titles, ["Pre-orders open: Foundations Play Booster Box, £99.00 at Harbour Games"])

    def test_preorder_entries_are_one_query(self):
        from catalogue.models import PreorderOpen
        from content import service as copy
        from web import feeds

        for product in (self.etb, self.sold_out, self.unpriced):
            PreorderOpen.objects.create(product=product, retailer=self.north, price="44.50")
        store = copy.load_all()   # the wording is read once per request, not once per entry
        with self.assertNumQueries(1):
            entries = feeds.preorder_entries(None, store, timezone.now())
        self.assertEqual(len(entries), 3)

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
        # Switched off rather than deleted, so the merge can be undone.
        self.assertFalse(Product.objects.get(pk=twin.pk).is_active)
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
        # A sort chosen keeps the filters in view on a game page with only a few products.
        response = self.client.get(self.magic.get_absolute_url() + "?sort=price")
        self.assertContains(response, '<option value="booster_box">Play booster box</option>')
        self.assertContains(response, '<option value="collector_booster_box">Collector booster box</option>')
        self.assertNotContains(response, "Elite Trainer Box</option>")
        self.assertNotContains(response, '<option value="deck">')
        response = self.client.get(self.game.get_absolute_url() + "?sort=price")
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
    def test_football_is_pinned_among_the_first_tiles(self):
        from django.core.cache import cache

        football = make_game(name="Football cards", slug="football", search_aliases="")
        attax = make_set(football, name="Match Attax 2026/27", slug="match-attax-2026-27", code="MA27")
        tin = make_product(attax, name="Match Attax 2026/27 Mega Tin", slug="ma27-mega-tin", product_type="tin")
        make_listing(tin, self.harbour, price="14.99")
        cache.clear()
        response = self.client.get(reverse("web:home"))
        html = response.content.decode()
        tiles = html[html.index('class="tiles tiles--games"'):html.index("</ul>", html.index('class="tiles tiles--games"'))]
        # Pinned: among the first tiles of the row, so it shows without swiping.
        links = re.findall(r'href="(/games/[^"]+/)"', tiles)
        self.assertIn("/games/football/", links[:4])
        # The football row moved off the home page; its game page lists the range.
        self.assertNotContains(response, "Football and sports cards")
        self.assertContains(self.client.get(football.get_absolute_url()), "Match Attax 2026/27 Mega Tin")


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

    def out_now(self, **params):
        return self.client.get(reverse("web:drops"), {"show": "out", **params})

    def test_home_row_leads_with_the_newest_release_and_skips_old_stock(self):
        response = self.client.get(reverse("web:home"))
        html = response.content.decode()
        self.assertLess(html.index('id="featured-title"'), html.index('id="latest-title"'))
        row = html[html.index('id="latest-title"'):html.index("</section>", html.index('id="latest-title"'))]
        self.assertIn("Drops</h2>", row)
        self.assertIn('href="/drops/?show=out"', row)
        self.assertIn("Out ", row)
        self.assertNotIn("Base Set Booster Box", row)
        # The set to come is a Coming soon row, ahead of the Out now row that holds what is in stock.
        self.assertLess(row.index("Mega Evolution"), row.index('id="out-title"'))
        self.assertLess(row.index('id="out-title"'), row.index("Prismatic Evolutions Elite Trainer Box"))

    def test_out_now_lists_new_products_in_stock_with_filters(self):
        response = self.out_now()
        self.assertContains(response, "Prismatic Evolutions Elite Trainer Box")
        self.assertNotContains(response, "Base Set Booster Box")
        # A pre-order is under Coming soon, with its own button, not here.
        self.assertNotContains(response, "Mega Evolution Elite Trainer Box")
        self.assertContains(self.client.get(reverse("web:drops")), "Mega Evolution")
        self.assertContains(response, 'name="price"')
        self.assertContains(response, '<link rel="canonical" href="http://testserver/drops/?show=out">')
        sitemap = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("http://testserver/drops/</loc>", sitemap)
        self.assertNotIn("http://testserver/new/", sitemap)

    def test_a_product_shops_only_just_listed_counts_as_a_drop_even_without_a_release_date(self):
        fresh = make_product(self.old_set, name="Base Set Blister", slug="base-set-blister", product_type="bundle")
        make_listing(fresh, self.harbour, price="12.00")
        self.assertContains(self.out_now(), "Base Set Blister")

    def test_the_home_out_now_row_holds_only_what_out_now_lists(self):
        """The home row is a preview of Out now: a pre-order is not on it (it is a Coming soon row instead), so
        every product it shows is behind its link."""
        from django.core.cache import cache

        loose = make_product(self.old_set, name="Phantasmal Flames Booster Bundle", slug="pfl-bundle", product_type="bundle")
        Product.objects.filter(pk=loose.pk).update(created_at=timezone.now() - timedelta(days=100))
        make_listing(loose, self.harbour, price="29.99", availability=Listing.Availability.PREORDER)
        cache.clear()
        home = self.client.get(reverse("web:home")).content.decode()
        row = home[home.index('id="out-title"'):]
        row = row[:row.index("</ul>")]
        self.assertIn('href="/drops/?show=out"', row)
        self.assertNotIn("Phantasmal Flames Booster Bundle", row)
        self.assertNotIn("Mega Evolution Elite Trainer Box", row)
        out_now = self.out_now().content.decode()
        names = re.findall(r'class="trending__name">([^<]+)<', row)
        self.assertIn("Prismatic Evolutions Elite Trainer Box", names)
        for name in names:
            self.assertIn(name, out_now)

    def test_home_has_one_drops_section_with_coming_soon_then_out_now(self):
        home = self.client.get(reverse("web:home")).content.decode()
        self.assertEqual(home.count('class="home-section home-drops"'), 1)
        section = home[home.index('id="latest-title"'):home.index("</section>", home.index('id="latest-title"'))]
        self.assertIn('class="section-head__all" href="/drops/"', section)
        self.assertLess(section.index('id="coming-title"'), section.index('id="out-title"'))
        self.assertIn('href="/drops/">Coming soon', section)
        self.assertIn('href="/drops/?show=out">Out now', section)
        # The pre-order is a Coming soon row with its button, not a card in the Out now row.
        self.assertIn("Mega Evolution", section[:section.index('id="out-title"')])
        self.assertIn("Pre-order<span", section)
        # A product that is out is not marked.
        self.assertNotIn("is-upcoming", self.client.get(self.game.get_absolute_url(), {"q": ""}).content.decode().split("Mega Evolution")[0].split('class="card"')[-1])

    def test_the_launch_import_itself_is_not_a_drop(self):
        from django.core.cache import cache

        # Everything created at launch: only products with a recent release date count.
        Product.objects.update(created_at=timezone.now() - timedelta(hours=3))
        Product.objects.filter(pk=self.etb.pk).update(release_date=timezone.localdate() - timedelta(days=3))
        cache.clear()
        response = self.out_now()
        self.assertContains(response, "Prismatic Evolutions Elite Trainer Box")
        self.assertNotContains(response, "Base Set Booster Box")
        self.assertNotContains(response, "Prismatic Evolutions Booster Bundle")


class SharedCacheTests(PageTestCase):
    """The web workers and every command share one cache folder, so a clear anywhere is seen everywhere."""

    def file_cache(self):
        from django.test import override_settings

        folder = tempfile.mkdtemp(prefix="ripraptor-cache-test-")
        self.addCleanup(shutil.rmtree, folder, True)
        overrides = override_settings(CACHES={"default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": folder,
        }})
        overrides.enable()
        self.addCleanup(overrides.disable)
        return folder

    def inside_the_window(self):
        """This process cleared a moment ago, so an unforced clear is skipped for the next 30 seconds."""
        from catalogue.testing import inside_the_cache_window

        inside_the_cache_window(self)

    def wall_clock(self):
        """Patch the clock web.listcache dates its lists by, and the cache's own expiry clock, to ``wall[0]``,
        which starts a little after the real time (the saves that set up the test marked the lists then), on an
        empty cache."""
        import time
        from types import SimpleNamespace
        from unittest import mock

        from django.core.cache import cache

        cache.clear()
        wall = [float(int(time.time()) + 1000)]
        clock = SimpleNamespace(time=lambda: wall[0], monotonic=time.monotonic, sleep=time.sleep)
        for target in ("web.listcache.time", "django.core.cache.backends.base.time",
                       "django.core.cache.backends.locmem.time"):
            patcher = mock.patch(target, clock)
            patcher.start()
            self.addCleanup(patcher.stop)
        return wall

    def test_two_cache_instances_on_one_directory_share_a_clear(self):
        from django.core.cache.backends.filebased import FileBasedCache

        from catalogue.importers import run_import
        from catalogue.models import Retailer
        from catalogue.signals import HOME_CACHE_KEY, clear_list_caches
        from catalogue.testing import lists_due
        from web.feeds import feed_cache_key

        folder = self.file_cache()
        web_worker = FileBasedCache(folder, {})  # another process's view of the same folder
        self.client.get(reverse("web:home"))
        self.assertFalse(lists_due(cache=web_worker))
        web_worker.set(feed_cache_key("deals"), "cached feed", 300)
        clear_list_caches(force=True)
        # The lists stay for visitors while the jobs build them again; the feeds go.
        self.assertIsNotNone(web_worker.get(HOME_CACHE_KEY))
        self.assertTrue(lists_due(cache=web_worker))
        self.assertIsNone(web_worker.get(feed_cache_key("deals")))

        # The end of an import in a cron process marks what the web worker cached, even inside the window.
        self.client.get(reverse("web:home"))
        self.assertFalse(lists_due(cache=web_worker))
        self.inside_the_window()
        self.harbour.source_type = Retailer.Source.SHOPIFY
        self.harbour.source_url = "https://h.example/"
        self.harbour.save()
        self.assertFalse(lists_due(cache=web_worker))
        run_import(self.harbour, fetch=lambda url: b'{"products": []}')
        self.assertTrue(lists_due(cache=web_worker))

    def test_an_ordinary_clear_waits_for_the_window_and_a_forced_one_does_not(self):
        from django.test import override_settings

        from catalogue import signals
        from catalogue.testing import cache_lists, lists_due
        from web import listcache

        wall = self.wall_clock()
        start = wall[0]
        with override_settings(RIPRAPTOR_CACHE_CLEAR_SECONDS=30):
            cache_lists()
            wall[0] = start + 10
            self.cheap.save()
            self.assertTrue(listcache.is_stale(signals.HOME_CACHE_KEY))
            self.assertFalse(lists_due())   # ten seconds old: a run of saves does not rebuild it each time
            wall[0] = start + 30
            self.assertTrue(lists_due())    # thirty seconds old: the jobs build it again
            cache_lists()
            wall[0] = start + 31
            self.assertFalse(lists_due())
            self.assertTrue(signals.clear_list_caches(force=True))
            self.assertTrue(lists_due())    # forced: at once, however young

    def test_feed_clears_are_debounced_to_once_per_thirty_seconds_unless_forced(self):
        from unittest import mock

        from django.core.cache import cache
        from django.test import override_settings

        from catalogue import signals
        from web.feeds import feed_cache_key

        feed = feed_cache_key("deals")
        clock = mock.Mock(return_value=1000.0)
        with override_settings(RIPRAPTOR_CACHE_CLEAR_SECONDS=30), \
                mock.patch.object(signals, "_last_clear", None), mock.patch("catalogue.signals.time.monotonic", clock):
            self.assertTrue(signals.clear_list_caches())
            cache.set(feed, "feed", 300)
            clock.return_value = 1010.0
            self.assertFalse(signals.clear_list_caches())
            self.cheap.save()  # a save inside the window is skipped too
            self.assertEqual(cache.get(feed), "feed")
            self.assertTrue(signals.clear_list_caches(force=True))
            self.assertIsNone(cache.get(feed))
            cache.set(feed, "feed", 300)
            clock.return_value = 1039.9  # the forced clear started a new window
            self.assertFalse(signals.clear_list_caches())
            self.assertEqual(cache.get(feed), "feed")
            clock.return_value = 1040.0
            self.cheap.save()
            self.assertIsNone(cache.get(feed))

    def test_a_save_inside_the_window_shows_when_the_window_closes(self):
        from unittest import mock

        from django.core.cache import cache
        from django.test import override_settings

        from catalogue import signals
        from web.feeds import feed_cache_key

        clock = mock.Mock(return_value=1000.0)  # time.monotonic, for the feeds' debounce
        wall = self.wall_clock()                # time.time, for the lists' age and the cache's own expiry
        start = wall[0]
        with override_settings(RIPRAPTOR_CACHE_CLEAR_SECONDS=30), \
                mock.patch.object(signals, "_last_clear", None), mock.patch("catalogue.signals.time.monotonic", clock):
            self.cheap.save()  # clears and opens the window at 1000
            self.client.get(reverse("web:home"))  # a visitor refills the lists
            self.assertContains(self.client.get(reverse("web:home")), "Harbour Games")
            cache.set(feed_cache_key("deals"), "feed", 300)

            clock.return_value, wall[0] = 1010.0, start + 10
            self.cheap.is_active = False
            self.cheap.save()  # inside the window: the lists still show the hidden price for now
            self.assertContains(self.client.get(reverse("web:home")), "Harbour Games")

            # The window closes 30 seconds after the lists were built: they are built again, and the feed is gone.
            clock.return_value, wall[0] = 1030.0, start + 30
            self.assertIsNone(cache.get(feed_cache_key("deals")))
            self.assertNotContains(self.client.get(reverse("web:home")), "Harbour Games")
            self.assertIsNotNone(cache.get(signals.HOME_CACHE_KEY))

    def test_a_clear_waits_for_the_commit_and_a_rollback_never_clears(self):
        from django.db import transaction
        from django.test import override_settings

        from catalogue import pricing
        from catalogue.testing import cache_lists, lists_due

        cache_lists()
        with override_settings(RIPRAPTOR_CLEAR_AFTER_COMMIT=True):
            # Django Admin saves a retailer inside a transaction: another worker must not refill the
            # lists from the old delivery costs between the clear and the commit.
            with self.captureOnCommitCallbacks() as callbacks:
                with transaction.atomic():
                    pricing.apply_delivery_rules(self.harbour)
                    self.cheap.save()
                self.assertFalse(lists_due())
            self.assertEqual(len(callbacks), 2)
            for callback in callbacks:
                callback()  # the commit
            self.assertTrue(lists_due())

            cache_lists()
            with self.captureOnCommitCallbacks() as callbacks:
                try:
                    with transaction.atomic():
                        pricing.apply_delivery_rules(self.harbour)
                        raise ValueError("the save failed")
                except ValueError:
                    pass
            self.assertEqual(callbacks, [])
            self.assertFalse(lists_due())

    def test_owner_fixes_and_delivery_rules_clear_inside_the_window(self):
        from django.contrib.auth import get_user_model

        from catalogue import pricing
        from catalogue.testing import cache_lists, lists_due

        self.inside_the_window()
        cache_lists()
        self.cheap.save()
        self.assertFalse(lists_due())
        pricing.apply_delivery_rules(self.harbour)
        self.assertTrue(lists_due())

        cache_lists()
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        self.client.post(reverse("checks"), {"action": "hide", "listing": self.cheap.pk})
        self.assertTrue(lists_due())

    def test_unwritable_cache_dir_falls_back_to_locmem_with_a_warning(self):
        import os
        from pathlib import Path
        from unittest import mock

        from ripraptor.caching import shared_caches

        folder = Path(tempfile.mkdtemp(prefix="ripraptor-cache-test-"))
        self.addCleanup(shutil.rmtree, folder, True)
        caches = shared_caches(folder / "cache")
        self.assertEqual(caches["default"]["BACKEND"], "django.core.cache.backends.filebased.FileBasedCache")
        self.assertEqual(caches["default"]["LOCATION"], str(folder / "cache"))
        self.assertGreaterEqual(caches["default"]["OPTIONS"]["MAX_ENTRIES"], 10000)   # the lists are not culled
        self.assertEqual(os.listdir(folder / "cache"), [])  # the write check leaves nothing behind

        blocker = folder / "a-file"
        blocker.write_text("")
        with self.assertLogs("ripraptor", "WARNING") as logged:
            caches = shared_caches(blocker / "cache")  # cannot be created
        self.assertEqual(caches["default"]["BACKEND"], "django.core.cache.backends.locmem.LocMemCache")
        self.assertIn("cannot be used", logged.output[0])

        with mock.patch("ripraptor.caching.tempfile.NamedTemporaryFile", side_effect=PermissionError("read only")), \
                self.assertLogs("ripraptor", "WARNING") as logged:
            caches = shared_caches(folder / "cache")  # exists but cannot be written
        self.assertEqual(caches["default"]["BACKEND"], "django.core.cache.backends.locmem.LocMemCache")
        self.assertIn("read only", logged.output[0])

    def test_cache_folder_comes_from_the_environment_or_the_server_default(self):
        from pathlib import Path

        from ripraptor.caching import cache_dir

        base = Path("/srv/ripraptor")
        self.assertEqual(cache_dir({"RIPRAPTOR_CACHE_DIR": "/tmp/rr-cache"}, False, base), Path("/tmp/rr-cache"))
        self.assertEqual(cache_dir({}, False, base), Path("/var/lib/ripraptor/cache"))
        self.assertEqual(cache_dir({"RIPRAPTOR_CACHE_DIR": " "}, True, base), base / ".cache")


class ComingSoonTests(PageTestCase):
    """Announced sets still to come, as rows on the home page and game pages (the Pre-orders tab), on the drops
    page and on set pages."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)
        self.today = timezone.localdate()
        self.soon_set = make_set(self.game, name="Ascended Heroes", slug="ascended-heroes", code="ASC",
                                 release_date=self.today + timedelta(days=10))

    def rows_url(self):
        return self.game.get_absolute_url() + "?tab=pre-orders"

    def rows(self):
        return self.block(self.client.get(self.rows_url()).content.decode())

    def block(self, html):
        start = html.index('id="coming-title"')
        return html[start:html.index("</section>", start)]

    def add_preorder(self, product_set, retailer=None, price="49.99", **kwargs):
        slug = kwargs.pop("slug", f"{product_set.slug}-etb")
        product = Product.objects.filter(slug=slug).first() or make_product(
            product_set, name=f"{product_set.name} Elite Trainer Box", slug=slug)
        make_listing(product, retailer or self.harbour, price=price,
                     availability=Listing.Availability.PREORDER, **kwargs)
        return product

    def test_a_set_dated_in_ten_days_with_no_products_shows_its_date_and_no_pre_orders(self):
        block = self.rows()
        self.assertIn("Coming soon", block)
        self.assertIn("Ascended Heroes", block)
        self.assertIn(f'href="{self.soon_set.get_absolute_url()}"', block)
        self.assertIn(f"Out {(self.today + timedelta(days=10)):%-d %b %Y}", block)
        self.assertIn("No pre-orders yet", block)
        # Its one button: Remind me, opening that set's reminder on the drops page, once emails are set up.
        # Until then it opens the set's page.
        self.assertIn(f'data-primary href="{self.soon_set.get_absolute_url()}">See the set', block)
        self.assertNotIn("#drop-", block)
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN="tok", RIPRAPTOR_MAIL_FROM="alerts@ripraptor.com"):
            block = self.rows()
        self.assertEqual(block.count("data-primary"), 1)
        self.assertIn(f'data-primary href="/drops/?remind={self.soon_set.pk}#drop-{self.soon_set.pk}">Remind me', block)
        self.assertIn('href="/drops/?game=pokemon"', block)   # See all
        # Without a pre-order price it stays off the home page, which is the money page.
        self.assertNotIn('id="coming-title"', self.client.get(reverse("web:home")).content.decode())

    def test_a_pre_order_shows_the_price_and_shop_count_and_reaches_the_home_page(self):
        product = self.add_preorder(self.soon_set, price="49.99")
        self.add_preorder(self.soon_set, retailer=self.north, price="52.00")
        cheapest = Listing.objects.get(product=product, retailer=self.harbour)
        block = self.rows()
        self.assertIn("Pre-orders from", block)
        self.assertIn("£49.99", block)
        self.assertIn("at 2 shops", block)
        self.assertIn("checked 1 hour ago", block)
        self.assertNotIn("No pre-orders yet", block)
        # The price links to our product page; the one button goes to the cheapest shop through /go/.
        self.assertIn(f'href="{product.get_absolute_url()}"', block)
        self.assertEqual(block.count("data-primary"), 1)
        self.assertIn(f'href="/go/{cheapest.pk}/?b=coming" data-click="coming"', block)
        self.assertIn('target="_blank" rel="sponsored nofollow noopener" data-primary>', block)
        self.assertIn("Pre-order<span", block)
        self.assertNotIn("Remind me", block)
        home = self.client.get(reverse("web:home")).content.decode()
        self.assertLess(home.index('id="latest-title"'), home.index('id="coming-title"'))
        home_block = self.block(home)
        self.assertIn("Ascended Heroes", home_block)
        self.assertIn("£49.99", home_block)
        self.assertIn('href="/drops/"', home_block)
        self.assertIn("/go/", home_block)

    def test_the_home_page_shows_only_the_three_soonest_sets_with_a_pre_order(self):
        self.add_preorder(self.soon_set)
        for n in range(4):
            product_set = make_set(self.game, name=f"Future {n}", slug=f"future-{n}", code=f"F{n}",
                                   release_date=self.today + timedelta(days=n + 1))
            self.add_preorder(product_set)
        home = self.block(self.client.get(reverse("web:home")).content.decode())
        # With no views, clicks or reminders and one shop each, popularity is soonness: the three soonest show.
        # The rest are rendered hidden, for the visitor's own games (interest.js), and show without script.
        rows = home.split('<li class="coming__row"')[1:]
        shown = [row for row in rows if not row.split(">", 1)[0].endswith(" hidden")]
        self.assertEqual(len(rows) - len(shown), 2)
        shown = "".join(shown)
        for name in ("Future 0", "Future 1", "Future 2"):
            self.assertIn(name, shown)
        self.assertNotIn("Future 3", shown)
        self.assertNotIn("Ascended Heroes", shown)
        # The game page and the drops page still list every one.
        self.assertEqual(self.rows().count('class="coming__row"'), 5)
        drops = self.client.get(reverse("web:drops")).content.decode()
        for name in ("Future 3", "Ascended Heroes"):
            self.assertIn(name, drops)

    def test_a_future_set_a_shop_already_has_in_stock_never_reads_no_pre_orders(self):
        product = make_product(self.soon_set, name="Ascended Heroes Booster Box", slug="ascended-heroes-box")
        listing = make_listing(product, self.harbour, price="120.00", availability=Listing.Availability.IN_STOCK)
        block = self.rows()
        self.assertIn("Ascended Heroes", block)
        self.assertIn("In stock from", block)
        self.assertIn("£120.00", block)
        self.assertIn("at Harbour Games", block)
        self.assertIn(f'href="/go/{listing.pk}/?b=coming"', block)
        self.assertIn("Buy now<span", block)
        self.assertNotIn("No pre-orders yet", block)
        # A pre-order price still leads when a shop takes pre-orders too.
        self.add_preorder(self.soon_set, retailer=self.north, price="49.99")
        from django.core.cache import cache

        cache.clear()
        block = self.rows()
        self.assertIn("£49.99", block)
        self.assertNotIn("In stock from", block)
        self.assertIn("Pre-order<span", block)

    def test_an_ebay_or_stale_in_stock_listing_does_not_count_as_a_shop_selling_it(self):
        from catalogue.models import Retailer

        product = make_product(self.soon_set, name="Ascended Heroes Booster Box", slug="ascended-heroes-box")
        make_listing(product, make_retailer("eBay", source_type=Retailer.Source.EBAY), price="150.00",
                     availability=Listing.Availability.IN_STOCK)
        make_listing(product, self.harbour, price="120.00", availability=Listing.Availability.IN_STOCK, hours_ago=100)
        block = self.rows()
        self.assertIn("No pre-orders yet", block)
        self.assertNotIn("In stock from", block)

    def test_an_undated_set_a_shop_already_sells_from_stock_is_not_coming_soon(self):
        undated = make_set(self.game, name="Chaos Rising", slug="chaos-rising", code="CRI")
        self.add_preorder(undated, price="55.00")
        self.assertIn("Chaos Rising", self.rows())
        product = make_product(undated, name="Chaos Rising Booster Box", slug="chaos-rising-box")
        make_listing(product, self.north, price="110.00", availability=Listing.Availability.IN_STOCK)
        from django.core.cache import cache

        cache.clear()
        self.assertNotIn("Chaos Rising", self.rows())
        self.assertNotIn("Date not announced yet", self.client.get(undated.get_absolute_url()).content.decode())

    def test_one_shop_and_an_unknown_delivery_are_worded_as_such(self):
        self.add_preorder(self.soon_set, price="44.00", delivery_known=False)
        block = self.rows()
        self.assertIn("at 1 shop", block)
        self.assertIn("£44.00", block)
        self.assertIn("+ delivery", block)

    def test_a_confirmed_delivered_price_leads_an_unknown_one(self):
        self.add_preorder(self.soon_set, price="40.00", delivery_known=False)
        self.add_preorder(self.soon_set, retailer=self.north, price="45.00", delivery="3.00")
        block = self.rows()
        self.assertIn("£48.00", block)
        self.assertNotIn("£40.00", block)

    def test_an_undated_set_shows_only_while_a_shop_takes_pre_orders(self):
        undated = make_set(self.game, name="Chaos Rising", slug="chaos-rising", code="CRI")
        self.assertNotIn("Chaos Rising", self.rows())
        # An eBay pre-order is a reseller's guess and does not count.
        from catalogue.models import Retailer

        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.add_preorder(undated, retailer=ebay, price="60.00")
        from django.core.cache import cache

        cache.clear()
        self.assertNotIn("Chaos Rising", self.rows())
        self.add_preorder(undated, price="55.00")
        block = self.rows()
        self.assertIn("Chaos Rising", block)
        self.assertIn("£55.00", block)
        self.assertIn("at 1 shop", block)
        # Undated sets follow the dated ones.
        self.assertLess(block.index("Ascended Heroes"), block.index("Chaos Rising"))
        set_page = self.client.get(undated.get_absolute_url()).content.decode()
        # Said once, in the line under the heading.
        self.assertRegex(set_page, r"1 sealed product, on pre-order at \d UK shops?\. Release date not announced yet\.")
        self.assertEqual(set_page.count("not announced yet"), 1)

    def test_a_stale_or_kept_out_pre_order_does_not_count(self):
        undated = make_set(self.game, name="Chaos Rising", slug="chaos-rising", code="CRI")
        self.add_preorder(undated, price="55.00", hours_ago=100)
        self.add_preorder(undated, retailer=self.north, price="5.00", sanity=Listing.Sanity.EXCLUDED)
        self.assertNotIn("Chaos Rising", self.rows())

    def test_a_set_dated_yesterday_leaves_the_block(self):
        self.soon_set.release_date = self.today - timedelta(days=1)
        self.soon_set.save()
        self.add_preorder(self.soon_set)
        for url in (self.rows_url(), reverse("web:home"), self.game.get_absolute_url()):
            with self.subTest(url=url):
                self.assertNotIn("Ascended Heroes</a>\n", self.client.get(url).content.decode())
                self.assertNotIn('id="coming-title"', self.client.get(url).content.decode())
        # Its set page says it is out.
        self.assertContains(self.client.get(self.soon_set.get_absolute_url()),
                            f"Released {(self.today - timedelta(days=1)):%-d %b %Y}")

    def test_a_set_dated_today_still_shows(self):
        self.soon_set.release_date = self.today
        self.soon_set.save()
        self.assertIn("Ascended Heroes", self.rows())

    def test_game_pages_show_their_own_sets_in_the_pre_orders_tab(self):
        other = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        make_set(other, name="Lorwyn Eclipsed", slug="lorwyn-eclipsed", code="ECL",
                 release_date=self.today + timedelta(days=20))
        make_product(make_set(other, name="Old Magic", slug="old-magic", code="OLD"), name="Old Magic Box", slug="old-magic-box")
        # The In stock tab leads with what can be bought now.
        self.assertNotIn('id="coming-title"', self.client.get(self.game.get_absolute_url()).content.decode())
        page = self.client.get(self.game.get_absolute_url() + "?tab=pre-orders").content.decode()
        block = self.block(page)
        self.assertIn("Ascended Heroes", block)
        self.assertNotIn("Lorwyn Eclipsed", block)
        self.assertIn("Ascended Heroes", self.block(self.client.get(
            self.game.get_absolute_url() + "?tab=pre-orders&pane=1").content.decode()))
        magic = self.block(self.client.get(other.get_absolute_url() + "?tab=pre-orders").content.decode())
        self.assertIn("Lorwyn Eclipsed", magic)
        self.assertNotIn("Ascended Heroes", magic)
        self.assertIn('href="/drops/?game=magic-the-gathering"', magic)
        drops = self.client.get(reverse("web:drops")).content.decode()
        self.assertLess(drops.index("Ascended Heroes"), drops.index("Lorwyn Eclipsed"))
        # A set page does not repeat the block.
        self.assertNotIn('id="coming-title"', self.client.get(self.pre.get_absolute_url()).content.decode())

    def test_the_set_page_prints_the_date_and_where_it_came_from(self):
        self.soon_set.release_date_source = "pokemon_uk_news"
        self.soon_set.save()
        html = self.client.get(self.soon_set.get_absolute_url()).content.decode()
        self.assertIn(f"Out {(self.today + timedelta(days=10)):%-d %b %Y}", html)
        self.assertIn("Date from pokemon.com UK news", html)
        self.assertNotIn("Released ", html)
        # The owner's own date carries no source line.
        self.soon_set.release_date_source = "owner"
        self.soon_set.save()
        html = self.client.get(self.soon_set.get_absolute_url()).content.decode()
        self.assertIn("Out ", html)
        self.assertNotIn("Date from", html)
        # A released set with no date says nothing about one.
        self.assertNotIn("Date not announced yet", self.client.get(self.pre.get_absolute_url()).content.decode())

    def test_a_source_name_never_brings_an_exclamation_mark(self):
        vanguard = make_game(name="Cardfight Vanguard", slug="cardfight-vanguard")
        product_set = make_set(vanguard, name="Dragon Rhapsody", slug="dragon-rhapsody", code="VGE-DZ-BT16",
                               release_date=self.today + timedelta(days=5), release_date_source="bushiroad_vanguard")
        html = self.client.get(product_set.get_absolute_url()).content.decode()
        self.assertIn("Date from Cardfight Vanguard site", html)
        self.assertNotIn("!", re.sub(r"<[^>]*>", " ", html))

    def test_coming_soon_sets_is_one_query(self):
        from django.core.cache import cache

        from web.views import coming_soon_queryset, coming_soon_sets

        for n in range(3):
            product_set = make_set(self.game, name=f"Future {n}", slug=f"future-{n}", code=f"F{n}",
                                   release_date=self.today + timedelta(days=n + 1))
            self.add_preorder(product_set)
            self.add_preorder(product_set, retailer=self.north, price="60.00")
        with self.assertNumQueries(1):
            rows = list(coming_soon_queryset())
        self.assertEqual(len(rows), 4)
        self.assertEqual([row.preorder_count for row in rows], [2, 2, 2, 0])
        cache.clear()
        with self.assertNumQueries(1):
            coming_soon_sets(self.game)
        with self.assertNumQueries(0):
            self.assertEqual(len(coming_soon_sets(self.game, limit=2)), 2)

    def page_queries(self, url):
        from django.core.cache import cache
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(queries)

    def test_every_page_keeps_its_query_count_however_many_sets_are_coming(self):
        self.add_preorder(self.soon_set)
        urls = [reverse("web:drops"), reverse("web:drops") + "?show=out", reverse("web:home"), self.rows_url(),
                self.soon_set.get_absolute_url()]
        before = {url: self.page_queries(url) for url in urls}
        for n in range(4):
            product_set = make_set(self.game, name=f"Future {n}", slug=f"future-{n}", code=f"F{n}",
                                   release_date=self.today + timedelta(days=n + 2))
            self.add_preorder(product_set)
        after = {url: self.page_queries(url) for url in urls}
        self.assertEqual(after, before)

    def test_the_block_is_cached_and_cleared_by_clear_list_caches(self):
        from catalogue.signals import clear_list_caches
        from catalogue.testing import inside_the_cache_window, lists_due

        self.client.get(reverse("web:home"))
        self.client.get(self.game.get_absolute_url())
        self.assertFalse(lists_due("web:coming-soon:v2"))
        self.assertFalse(lists_due("web:coming-soon:v2:pokemon"))
        # Inside the clear window a new set waits for the cache, then a forced clear shows it.
        inside_the_cache_window(self)
        make_set(self.game, name="Phantom Tides", slug="phantom-tides", code="PHT",
                 release_date=self.today + timedelta(days=30))
        self.assertNotIn("Phantom Tides", self.client.get(self.rows_url()).content.decode())
        clear_list_caches(force=True)
        self.assertTrue(lists_due("web:coming-soon:v2"))
        self.assertTrue(lists_due("web:coming-soon:v2:pokemon"))
        self.assertIn("Phantom Tides", self.rows())

    def test_a_new_set_shows_at_once_outside_the_clear_window(self):
        self.client.get(self.rows_url())
        make_set(self.game, name="Phantom Tides", slug="phantom-tides", code="PHT",
                 release_date=self.today + timedelta(days=30))
        self.assertIn("Phantom Tides", self.rows())

    def test_house_style_holds_with_the_block_on_every_page(self):
        self.add_preorder(self.soon_set)
        undated = make_set(self.game, name="Chaos Rising", slug="chaos-rising", code="CRI")
        self.add_preorder(undated, price="55.00")
        early = make_set(self.game, name="Early Street", slug="early-street", code="ERS",
                         release_date=self.today + timedelta(days=5))
        make_listing(make_product(early, name="Early Street Box", slug="early-street-box"), self.north,
                     price="99.00", availability=Listing.Availability.IN_STOCK)
        blocks = [reverse("web:home"), self.rows_url()]
        set_pages = [self.soon_set.get_absolute_url(), undated.get_absolute_url()]
        drops = [reverse("web:drops"), reverse("web:drops") + "?show=out"]
        for url in blocks + set_pages + drops:
            html = self.client.get(url).content.decode()
            visible = re.sub(r"<[^>]*>", " ", html)
            with self.subTest(url=url):
                if url in blocks:
                    self.assertIn('id="coming-title"', html)
                elif url in drops:
                    self.assertIn('class="stock-tabs stock-tabs--two drops-tabs"', html)
                else:
                    # The line under the heading gives the date, or says none is announced.
                    self.assertTrue("due out on " in visible or "Release date not announced yet." in visible)
                self.assertNotIn("!", visible)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())


class PicksTests(TestCase):
    """Picked for you: in-stock products like the ones a visitor viewed, saved or searched for, real deals
    first and labelled too good to miss, never the ones they already looked at."""

    def setUp(self):
        from decimal import Decimal

        self.game = make_game()
        self.set = make_set(self.game, name="Surging Sparks", slug="surging-sparks")
        other_set = make_set(self.game, name="Journey Together", slug="journey-together")
        self.shops = [make_retailer(f"Shop {n}", delivery_cost=Decimal("0")) for n in range(3)]
        self.viewed = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        self.same_set = make_product(self.set, name="Surging Sparks Booster Bundle", product_type="booster_bundle")
        self.deal = make_product(other_set, name="Journey Together Elite Trainer Box")
        self.sold_out = make_product(self.set, name="Surging Sparks Booster Box", product_type="booster_box")
        self.unrelated = make_product(other_set, name="Journey Together Booster Box", product_type="booster_box")
        for product, prices in ((self.viewed, ("50.00", "52.00")), (self.same_set, ("30.00", "31.00")),
                                (self.deal, ("40.00", "52.00")), (self.unrelated, ("100.00", "101.00"))):
            for shop, price in zip(self.shops, prices):
                make_listing(product, shop, price=price)
        make_listing(self.sold_out, self.shops[0], price="120.00", availability=Listing.Availability.OUT_OF_STOCK)

    def picks(self, query):
        return self.client.get(reverse("web:picks_api") + query).content.decode()

    def test_products_like_the_viewed_one_come_with_the_real_deals_first(self):
        body = self.picks(f"?p={self.viewed.slug}")
        self.assertNotIn(self.viewed.name, body)
        self.assertNotIn(self.sold_out.name, body)
        self.assertNotIn(self.unrelated.name, body)
        # A 23% saving on the next shop is too good to miss and comes first; the same set's bundle follows.
        self.assertLess(body.index(self.deal.name), body.index(self.same_set.name))
        self.assertEqual(body.count("Too good to miss"), 1)
        self.assertIn("Save £12.00", body)

    def test_searches_count_too_and_nothing_known_gives_nothing(self):
        self.assertIn(self.same_set.name, self.picks("?s=surging+sparks+elite"))
        self.assertEqual(self.picks("").count("trending__item"), 0)
        self.assertEqual(self.picks("?p=nope").count("trending__item"), 0)

    def test_the_row_costs_the_same_queries_however_much_was_viewed(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as one:
            self.picks(f"?p={self.viewed.slug}")
        with CaptureQueriesContext(connection) as many:
            self.picks(f"?p={self.viewed.slug},{self.same_set.slug},{self.deal.slug},{self.unrelated.slug}")
        self.assertLessEqual(len(many), len(one))
        self.assertLessEqual(len(one), 6)

    def test_the_home_page_has_the_section_hidden_until_the_script_fills_it(self):
        page = self.client.get(reverse("web:home")).content.decode()
        self.assertIn(f'data-picks data-endpoint="{reverse("web:picks_api")}" hidden', page)
        self.assertLess(page.index("data-picks"), page.index("data-resume"))


class AnalyticsTests(PageTestCase):
    """Google Analytics loads only after the visitor allows it, and never on a private link."""

    GA = {"RIPRAPTOR_GA_ID": "G-TEST123"}

    def test_public_pages_ask_first_and_load_nothing_of_googles(self):
        with self.settings(**self.GA):
            for url in self.public_urls() + [reverse("web:terms")]:
                html = self.client.get(url).content.decode()
                with self.subTest(url=url):
                    self.assertIn('src="/static/js/analytics.js" data-ga-id="G-TEST123" defer', html)
                    self.assertIn('data-consent hidden', html)
                    self.assertIn("Allow analytics", html)
                    self.assertIn("No analytics", html)
                    self.assertIn("data-consent-open hidden", html)
                    # Google's own script is added by analytics.js once the visitor allows it.
                    self.assertNotIn("googletagmanager", html)
                    self.assertNotIn("!", re.sub(r"<[^>]*>", " ", html))
                    for phrase in CopyStyleTests.BANNED:
                        self.assertNotIn(phrase, html.lower())

    def test_the_terms_page_explains_it_while_it_is_set_up(self):
        with self.settings(**self.GA):
            self.assertContains(self.client.get(reverse("web:terms")), "We use Google Analytics only if you allow it")
        with self.settings(RIPRAPTOR_GA_ID=""):
            html = self.client.get(reverse("web:terms")).content.decode()
            self.assertNotIn("Google Analytics", html)
            self.assertNotIn("analytics.js", html)
            self.assertNotIn("data-consent", self.client.get(reverse("web:home")).content.decode())

    def test_private_links_never_load_it(self):
        from catalogue.models import StockAlert
        from inbox import service as inbox

        alert = StockAlert.objects.create(product=self.sold_out, email="someone@example.com")
        conversation = inbox.start("Is the bundle coming back?")
        with self.settings(RIPRAPTOR_ADSENSE_CLIENT="ca-pub-1", **self.GA):
            # A mistyped thread link is a 404 whose address still holds what may be most of a token.
            for url in (reverse("web:alert_confirm", args=[alert.token]), reverse("web:alert_stop", args=[alert.token]),
                        conversation.get_absolute_url(), "/contact/c/mistyped-token/"):
                html = self.client.get(url).content.decode()
                with self.subTest(url=url):
                    self.assertNotIn("analytics.js", html)
                    self.assertNotIn("data-consent", html)
                    self.assertNotIn("G-TEST123", html)
                    self.assertNotIn("dwin2.com", html)
                    self.assertNotIn("googlesyndication", html)
                    # Leaving the page passes on only the site's address, never the token.
                    self.assertIn('<meta name="referrer" content="strict-origin">', html)
            self.assertNotIn('name="referrer"', self.client.get(reverse("web:home")).content.decode())

    def test_the_script_keeps_private_links_out_and_asks_first(self):
        from django.contrib.staticfiles import finders

        script = open(finders.find("js/analytics.js"), encoding="utf-8").read()
        self.assertIn("config.page_referrer", script)
        self.assertIn("allow_google_signals: false", script)
        self.assertIn("ad_storage: \"denied\"", script)
        # The tag is only ever added by start(), and start() is called from one place, on "granted" alone.
        self.assertEqual(script.count("googletagmanager.com/gtag/js"), 1)
        self.assertEqual(script.count("start()"), 1)
        self.assertIn('    if (answer === "granted") start();\n    if (answer !== "denied") return;\n', script)
        self.assertIn("if (from.origin === location.origin && PRIVATE.test(from.pathname)) config.page_referrer = `${from.origin}/`;", script)
        # Refusing reloads a page whose tag is running, here or in another tab or a page kept for Back.
        self.assertIn("if (started) location.reload();", script)
        self.assertIn('addEventListener("storage"', script)
        self.assertIn('addEventListener("pageshow"', script)
        # The server and the script agree on which addresses are private.
        from web.context_processors import PRIVATE_LINK

        self.assertIn(PRIVATE_LINK.pattern.replace("/", r"\/"), script)
        for route in ("contact_thread", "alert_confirm", "alert_stop"):
            path = reverse(f"web:{route}", args=["abc123"])
            with self.subTest(path=path):
                self.assertRegex(path, r"^/(?:contact/c|alerts/(?:confirm|stop))/")


class RedesignTests(PageTestCase):
    """The home page tiles and cards, and the product card on listing pages."""

    def test_the_home_page_has_the_tiles_the_watchlist_card_and_the_one_line_headline(self):
        from django.core.cache import cache

        hidden = make_game(name="Hidden game", slug="hidden", is_active=False)
        tin = make_product(make_set(hidden, name="Old", slug="old", code="OLD"), name="Old Tin", slug="old-tin", product_type="tin")
        make_listing(tin, self.harbour, price="9.99")
        cache.clear()
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<p class="hero__eyebrow">UK price comparison</p>', html)
        self.assertIn('Bad prices. <span class="hero__accent">Binned.</span></h1>', html)
        # The line under it says every price is delivered.
        self.assertIn("delivery included. Not just the basket price.", html)
        self.assertIn('<span class="search__button-text">Find best price</span>', html)
        self.assertIn('<a class="tile tile--game" href="/games/pokemon/"><span class="tile__cover" aria-hidden="true">', html)
        self.assertIn('<span class="tile__name">Pokémon</span><span class="tile__meta">3 products</span></a>', html)
        # No product has a picture here, so the tile shows the booster box outline instead.
        self.assertIn('<span class="product-image product-image--cover"><svg', html)
        # Browse by product: a tile per type the search page can filter to, with the type's outline drawing.
        types = html[html.index('class="tiles tiles--types"'):html.index("</ul>", html.index('class="tiles tiles--types"'))]
        self.assertIn('href="/search/?type=elite_trainer_box"', types)
        self.assertIn('<span class="tile__name">Elite Trainer Box</span><span class="tile__meta">1 product</span>', types)
        self.assertIn('<span class="tile__name">Collection box</span>', types)
        self.assertIn('class="placeholder"', types)
        self.assertNotIn("type=tin", types)   # a product of a hidden game is not listed, so it is not counted
        self.assertIn('<a class="poster poster--watch" href="/watchlist/">', html)
        self.assertIn("Keep an eye on it.", html)
        # The headline's orange sentence sits on the same line, and a game tile is one short row.
        from django.contrib.staticfiles import finders

        css = open(finders.find("css/site.css"), encoding="utf-8").read()
        self.assertIn(".hero__accent { color: var(--color-brand); white-space: nowrap; }", css)
        self.assertIn('grid-template-columns: var(--tile-game-art) minmax(0, 1fr); grid-template-areas: "art name" "art meta";', css)
        # Every row card ends with the same button.
        featured = html[html.index('id="featured-title"'):]
        self.assertIn('<span class="trending__cta">Compare prices<span aria-hidden="true"> →</span></span>', featured)

    def test_a_game_tile_writes_a_big_count_short_never_rounding_up(self):
        from .templatetags.ripraptor import compact_count

        self.assertEqual([compact_count(n) for n in (7, 999, 1000, 1099, 4179, 9999, 12345)],
                         ["7", "999", "1k", "1k", "4.1k", "9.9k", "12k"])

    def test_every_game_is_in_the_swipe_row_with_its_box_art(self):
        from django.core.cache import cache

        for n in range(8):
            game = make_game(name=f"Game {n}", slug=f"game-{n}", search_aliases="")
            make_product(make_set(game, name=f"Set {n}", slug=f"set-{n}", code=f"S{n}"), name=f"Game {n} Booster Box",
                         slug=f"game-{n}-box", product_type="booster_box", image_url=f"https://img.example/{n}.png")
        cache.clear()
        html = self.client.get(reverse("web:home")).content.decode()
        tiles = html[html.index('class="tiles tiles--games"'):html.index("</ul>", html.index('class="tiles tiles--games"'))]
        # Nine games, not the first six: the row swipes, so none is cut.
        self.assertEqual(tiles.count('class="tile tile--game"'), 9)
        for n in range(8):
            self.assertIn(f'src="https://img.example/{n}.png"', tiles)

    def test_the_home_page_keeps_the_whole_logo_and_the_raptor_panels(self):
        home = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('src="/static/img/logo.png"', home)
        # WebP first (the same artwork, a half of the PNG's bytes), the PNG for browsers without it.
        self.assertIn('<source srcset="/static/img/logo.webp" type="image/webp">', home)
        self.assertNotIn("hero__mark", home)
        # The owner's two panels: big capitals with the last sentence on its own line, the head cropped by the edge.
        deals = home[home.index('class="poster poster--deals"'):home.index("</a>", home.index('class="poster poster--deals"'))]
        self.assertIn('<a class="poster poster--deals" href="/deals/">', home)
        self.assertIn('Prices. <span class="poster__line">Dropped.</span>', deals)
        self.assertIn("Find your next rip for less.", deals)
        self.assertIn("Explore deals", deals)
        self.assertIn('<span class="mark poster__mark" aria-hidden="true"></span>', deals)
        watch = home[home.index('class="poster poster--watch"'):home.index("</a>", home.index('class="poster poster--watch"'))]
        self.assertIn("Keep an eye on it.", watch)
        self.assertIn("Save products. Watch the price.", watch)
        self.assertIn("View watchlist", watch)
        self.assertIn('<span class="mark poster__mark" aria-hidden="true"></span>', watch)

    def test_a_search_with_no_results_says_no_luck_yet(self):
        html = self.client.get(reverse("web:search"), {"q": "charizard"}).content.decode()
        card = html[html.index('class="no-luck"'):]
        self.assertIn('<h1 class="no-luck__title">No luck. Yet.</h1>', card)
        self.assertIn("No products match ‘charizard’", card)
        self.assertIn("Try the set name or a shorter search.", card)
        self.assertIn(f'href="{reverse("web:games")}">Browse games', card)

    def test_each_game_tile_shows_box_art_from_its_own_products(self):
        from django.core.cache import cache

        from web.views import game_covers

        old_box = make_product(self.pre, name="Old Booster Box", slug="old-box", product_type="booster_box",
                               image_url="https://img.example/old-box.png", release_date=timezone.localdate() - timedelta(days=900))
        new_box = make_product(self.pre, name="New Booster Box", slug="new-box", product_type="booster_box",
                               image_url="https://img.example/new-box.png", release_date=timezone.localdate() - timedelta(days=30))
        make_product(self.pre, name="Newest Booster Box", slug="newest-box", product_type="booster_box",
                     release_date=timezone.localdate())   # no picture, so never the cover
        Product.objects.filter(pk=self.etb.pk).update(image_url="https://img.example/etb.png")
        hidden = make_product(self.pre, name="Hidden Booster Box", slug="hidden-box", product_type="booster_box",
                              image_url="https://img.example/hidden.png", release_date=timezone.localdate(), is_active=False)
        other = make_game(name="Disney Lorcana", slug="lorcana", short_name="Lorcana")
        make_product(make_set(other, name="First Chapter", slug="first-chapter", code="TFC"), name="Lorcana Booster Box",
                     slug="lorcana-box", product_type="booster_box", image_url="https://img.example/lorcana.png")
        games = [self.game, other]
        with self.assertNumQueries(2):
            game_covers(games)
        # A booster box beats the elite trainer box, the newest boxed picture wins, hidden products never show.
        self.assertEqual(self.game.cover.pk, new_box.pk)
        self.assertNotIn(self.game.cover.pk, (old_box.pk, hidden.pk))
        self.assertEqual(other.cover.name, "Lorcana Booster Box")
        cache.clear()
        html = self.client.get(reverse("web:home")).content.decode()
        tiles = html[html.index('class="tiles tiles--games"'):html.index("</ul>", html.index('class="tiles tiles--games"'))]
        self.assertIn('src="https://img.example/new-box.png"', tiles)
        self.assertIn('src="https://img.example/lorcana.png"', tiles)

    def test_row_cards_say_see_product_when_no_shop_has_a_price(self):
        html = self.client.get(reverse("web:recent_api"), {"p": f"{self.etb.slug},{self.sold_out.slug}"}).content.decode()
        self.assertEqual(html.count("Compare prices"), 1)
        self.assertEqual(html.count("See product"), 1)
        self.assertLess(html.index("Compare prices"), html.index("See product"))

    def test_a_card_names_the_language_when_it_is_not_english(self):
        japanese = make_product(self.pre, name="Prismatic Evolutions Booster Box (Japanese)", slug="pev-box-ja", product_type="booster_box")
        make_listing(japanese, self.harbour, price="70.00")
        html = self.client.get(reverse("web:search"), {"q": "prismatic"}).content.decode()
        self.assertEqual(html.count('<span class="card__lang">Japanese<button type="button" class="info"'), 1)
        self.assertEqual(html.count('<span class="card__lang">'), 1)
        # One language at a time: the select shows the choice, and an old link with two keeps the last one.
        response = self.client.get(reverse("web:search"), {"lang": "ja"})
        self.assertContains(response, '<option value="ja" selected>')
        self.assertContains(response, japanese.name)
        self.assertNotContains(response, self.etb.name)
        response = self.client.get(reverse("web:search") + "?lang=ja&lang=en")
        self.assertContains(response, '<option value="en" selected>')
        self.assertContains(response, self.etb.name)
        self.assertNotContains(response, japanese.name)

    def test_a_sold_out_card_keeps_its_check_prices_link_and_has_nothing_to_compare(self):
        html = self.client.get(reverse("web:search"), {"q": "super-premium"}).content.decode()
        card = html[html.index('<li class="card'):html.index("</li>", html.index('<li class="card'))]
        self.assertIn("Out of stock", card)
        self.assertIn("Last seen at £140.00", card)
        self.assertIn('class="card__check"', card)
        self.assertNotIn("card__compare", card)
        self.assertNotIn('class="shops"', card)
        self.assertNotIn("Buy now", card)

    def test_only_game_pages_have_the_row_of_sets(self):
        later = make_set(self.game, name="Surging Sparks", slug="surging-sparks", code="SSP")
        make_listing(make_product(later, name="Surging Sparks Booster Box", slug="ssp-box", product_type="booster_box"), self.harbour, price="120.00")
        # A set page links back to its game once, quietly, and lists no other sets.
        set_page = self.client.get(self.pre.get_absolute_url()).content.decode()
        self.assertNotIn("set-nav", set_page)
        self.assertNotIn(f'href="{later.get_absolute_url()}"', set_page)
        self.assertIn('<a href="/games/pokemon/">All Pokémon products</a>', set_page)
        # The game page's row of sets is named for screen readers (no heading on screen), none marked as chosen,
        # and needs no All sets with so few.
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        row = html[html.index('<nav class="set-nav"'):html.index("</nav>", html.index('<nav class="set-nav"'))]
        self.assertIn('<nav class="set-nav" id="sets" aria-label="Sets">', html)
        self.assertNotIn("set-nav__title", row)
        self.assertIn(f'<a href="{self.pre.get_absolute_url()}">Prismatic Evolutions<span class="set-nav__count', row)
        self.assertIn(f'<a href="{later.get_absolute_url()}">Surging Sparks<span class="set-nav__count', row)
        self.assertNotIn("aria-current", row)
        self.assertNotIn("All sets", row)

    def test_a_game_page_shows_its_newest_sets_and_all_sets(self):
        from datetime import date

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from .views import GAME_PAGE_SETS

        def queries():
            with CaptureQueriesContext(connection) as captured:
                html = self.client.get(self.game.get_absolute_url()).content.decode()
            return html, len(captured)

        queries()   # the cached lists are built once
        _, few = queries()
        for n in range(GAME_PAGE_SETS + 3):
            made = make_set(self.game, name=f"Era {n}", slug=f"era-{n}", code="", release_date=date(2030 + n, 1, 1))
            make_product(made, name=f"Era {n} Booster Box", slug=f"era-{n}-box")
        # Nobody has looked at any set this week, so the newest come first (the sets people want lead otherwise).
        from catalogue.models import DailyPageView, OutboundClick

        DailyPageView.objects.all().delete()
        OutboundClick.objects.all().delete()
        queries()   # the saves cleared the cached lists
        html, many = queries()
        row = html[html.index('<nav class="set-nav"'):html.index("</nav>", html.index('<nav class="set-nav"'))]
        names = re.findall(r'<li><a href="[^"]+">([^<]+)<span class="set-nav__count', row)
        self.assertEqual(names, [f"Era {n}" for n in range(GAME_PAGE_SETS + 2, 2, -1)])
        self.assertIn(f'<a class="set-nav__all" href="{reverse("web:games")}#game-pokemon">All sets</a>', row)
        self.assertNotIn("Prismatic Evolutions", row)
        # However many sets, the same queries.
        self.assertEqual(many, few)

    def test_the_headline_puts_its_last_sentence_in_orange_whatever_it_says(self):
        from content.models import SiteContent

        from web.templatetags.ripraptor import accent_last

        self.assertEqual(accent_last("Bad prices. Binned."), 'Bad prices. <span class="hero__accent">Binned.</span>')
        self.assertEqual(accent_last("Prices. Dropped.", "poster__line"), 'Prices. <span class="poster__line">Dropped.</span>')
        self.assertEqual(accent_last("Better prices"), "Better prices")
        self.assertEqual(accent_last("One. Two. Three."), 'One. Two. <span class="hero__accent">Three.</span>')
        self.assertEqual(accent_last("A <b>x</b>. B."), 'A &lt;b&gt;x&lt;/b&gt;. <span class="hero__accent">B.</span>')
        # The owner's own wording in admin gets the same treatment, with nothing repeated.
        from content import service

        service.sync_defaults()
        SiteContent.objects.filter(key="home.hero.title").update(content="Find it cheaper. Open more.")
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<h1 id="hero-title" class="hero__title">Find it cheaper. <span class="hero__accent">Open more.</span></h1>', html)
        self.assertEqual(html.count("Open more."), 1)


class AdminAppTests(TestCase):
    """The admin as its own home screen app."""

    def test_the_manifest_opens_the_admin_and_offers_the_daily_pages(self):
        from django.contrib.staticfiles import finders

        response = self.client.get("/admin/manifest.webmanifest")   # no sign-in: the browser fetches it without one
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        data = response.json()
        self.assertEqual((data["id"], data["start_url"], data["scope"]), ("/admin/", "/admin/", "/admin/"))
        self.assertEqual(data["name"], "RipRaptor admin")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual([s["url"] for s in data["shortcuts"]],
                         ["/admin/checks/", "/admin/inbox/conversation/?unread__exact=1", "/admin/crawl/", "/admin/insights/"])
        sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in data["icons"]}
        self.assertEqual(sizes, {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")})
        for icon in data["icons"] + data["shortcuts"][0]["icons"]:
            self.assertTrue(finders.find(icon["src"].removeprefix("/static/")), icon["src"])
        # The public app is unchanged and separate.
        public = self.client.get("/manifest.webmanifest").json()
        self.assertEqual((public["id"], public["start_url"], public["scope"]), ("/", "/", "/"))

    def test_every_admin_page_links_it_and_the_home_offers_to_add_it(self):
        from django.contrib.auth import get_user_model

        login = self.client.get("/admin/login/").content.decode()
        self.assertIn('<link rel="manifest" href="/admin/manifest.webmanifest">', login)
        self.assertIn('<link rel="apple-touch-icon" href="/static/img/admin-apple-touch-icon.png">', login)
        owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "x")
        self.client.force_login(owner)
        for url in ("/admin/", "/admin/checks/", "/admin/insights/", "/admin/catalogue/product/"):
            html = self.client.get(url).content.decode()
            with self.subTest(url=url):
                self.assertEqual(html.count('rel="manifest"'), 1)
                self.assertIn('href="/admin/manifest.webmanifest"', html)
                self.assertNotIn('href="/manifest.webmanifest"', html)
                self.assertIn('<div id="site-name"><a href="/admin/">RipRaptor admin</a></div>', html)
        home = self.client.get("/admin/").content.decode()
        self.assertIn("data-admin-app", home)
        self.assertIn('<button type="button" class="button" data-admin-app-add hidden>Add to home screen</button>', home)
        # Public pages keep the public app.
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', self.client.get("/").content.decode())

    def test_the_admin_sign_in_renews_while_it_is_used_and_visitors_get_no_session(self):
        from django.conf import settings
        from django.contrib.auth import get_user_model

        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.client.get("/")
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "x"))
        response = self.client.get("/admin/")
        # The session is untouched by this page, yet its cookie is sent again with a fresh two-week expiry.
        self.assertIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[settings.SESSION_COOKIE_NAME]["max-age"], settings.SESSION_COOKIE_AGE)


class RaptorMarkTests(PageTestCase):
    """The raptor head stands where it has something to say, and never on the money page."""

    PUZZLED = '<div class="empty-mark" aria-hidden="true"><span class="mark mark--md"></span><span class="empty-mark__ask">?</span></div>'
    PLAIN = '<div class="empty-mark" aria-hidden="true"><span class="mark mark--md"></span></div>'

    def test_the_raptor_stands_where_it_has_something_to_say(self):
        home = self.client.get(reverse("web:home")).content.decode()
        self.assertEqual(home.count('<span class="mark poster__mark" aria-hidden="true"></span>'), 2)
        # Something went wrong: the ink head with a question mark.
        self.assertContains(self.client.get(reverse("web:search"), {"q": "nothing matches this"}), self.PUZZLED)
        self.assertContains(self.client.get("/no-such-page/"), self.PUZZLED, status_code=404)
        # Nothing is wrong, just nothing here: the head on its own.
        empty = self.client.get(reverse("web:watchlist")).content.decode()
        self.assertIn(self.PLAIN, empty)
        self.assertNotIn("empty-mark__ask", empty)
        # Filtered down to nothing is not a miss the raptor reports; the clear-filters link does the job.
        self.assertNotContains(self.client.get(reverse("web:search"), {"q": "etb", "type": "tin"}), "empty-mark")
        # Not a page-title ornament: the Deals heading stands on its own.
        self.assertNotContains(self.client.get(reverse("web:deals")), 'class="mark ')
        self.assertNotContains(self.client.get(reverse("web:deals")), 'class="mark"')

    def test_the_money_page_has_no_raptor(self):
        # Apart from the head on Buy now, which is the button's own drawing (buy-mark) and hidden from screen readers.
        for product in (self.etb, self.sold_out, self.unpriced):
            html = self.client.get(product.get_absolute_url()).content.decode()
            self.assertNotIn('class="mark ', html)
            self.assertNotIn('class="mark"', html)
            self.assertNotIn("empty-mark", html)

    def test_every_raptor_is_hidden_from_screen_readers(self):
        # A head on its own carries aria-hidden; one inside an empty-mark wrapper inherits the wrapper's.
        head = re.compile(r'(<div class="empty-mark"[^>]*>\s*)?<span class="mark(?: [^"]*)?"([^>]*)>')
        wrapper = re.compile(r'<div class="empty-mark"[^>]*>')
        for url in self.public_urls() + ["/no-such-page/"]:
            html = self.client.get(url).content.decode()
            for found in wrapper.findall(html):
                self.assertIn('aria-hidden="true"', found, url)
            for parent, attrs in head.findall(html):
                self.assertIn('aria-hidden="true"', parent or attrs, url)

    def test_the_end_of_the_swipe_deck_shows_the_raptor(self):
        from django.contrib.staticfiles import finders

        script = open(finders.find("js/deck.js"), encoding="utf-8").read()
        finished = script[script.index("if (finished) {"):script.index("} else {", script.index("if (finished) {"))]
        self.assertIn('el("span", "mark mark--md")', finished)
        self.assertIn('setAttribute("aria-hidden", "true")', finished)
        self.assertIn("words.finished", finished)


class StockTabsTests(TestCase):
    """Game and set pages split their products into In stock, Pre-orders and Out of stock: what can be
    bought now first, counted for every tab in one query, each tab its own page without script and a
    list on its own (pane=1) for the script to swipe to."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.game = make_game()
        self.later = make_set(self.game, name="Later Set", slug="later-set", code="LAT",
                              release_date=timezone.localdate() + timedelta(days=90))
        self.sooner = make_set(self.game, name="Sooner Set", slug="sooner-set", code="SOO",
                               release_date=timezone.localdate() + timedelta(days=10))
        self.old = make_set(self.game, name="Old Set", slug="old-set", code="OLD",
                            release_date=timezone.localdate() - timedelta(days=200))
        self.a, self.b = make_retailer("Shop A"), make_retailer("Shop B")
        self.stocked = make_product(self.old, name="Old Set Booster Box", slug="old-box")
        make_listing(self.stocked, self.a, price="90.00")
        make_listing(self.stocked, self.b, price="95.00")
        self.far = make_product(self.later, name="Later Set Elite Trainer Box", slug="later-etb")
        make_listing(self.far, self.a, price="50.00", availability=Listing.Availability.PREORDER)
        self.near = make_product(self.sooner, name="Sooner Set Elite Trainer Box", slug="sooner-etb")
        make_listing(self.near, self.a, price="60.00", availability=Listing.Availability.PREORDER)
        make_listing(self.near, self.b, price="61.00", availability=Listing.Availability.PREORDER)
        self.gone = make_product(self.old, name="Old Set Bundle", slug="old-bundle", product_type=Product.Type.BUNDLE)
        make_listing(self.gone, self.a, price="30.00", availability=Listing.Availability.OUT_OF_STOCK)
        self.url = self.game.get_absolute_url()

    def names(self, response):
        return re.findall(r'class="card__name"><a href="[^"]+">([^<]+)</a>', response.content.decode())

    def test_in_stock_comes_first_and_each_product_is_in_one_tab(self):
        page = self.client.get(self.url)
        html = page.content.decode()
        self.assertEqual(self.names(page), ["Old Set Booster Box"])
        for label, count in (("In stock", 1), ("Pre-orders", 2), ("Out of stock", 1)):
            self.assertRegex(html, rf'{label}</span> <span class="stock-tabs__count">{count}</span>')
        # The line under the heading counts them, so no second count.
        self.assertContains(page, "4 sealed products compared across 2 UK shops")
        self.assertNotContains(page, "browse-head__count")
        self.assertContains(page, 'data-tab="in-stock" aria-current="page"')
        self.assertEqual(self.names(self.client.get(self.url + "?tab=out-of-stock")), ["Old Set Bundle"])
        # Anything else asked for is the tab the page opens on.
        self.assertEqual(self.names(self.client.get(self.url + "?tab=nonsense")), ["Old Set Booster Box"])

    def test_pre_orders_are_soonest_out_first_unless_sorted(self):
        page = self.client.get(self.url + "?tab=pre-orders")
        self.assertEqual(self.names(page), ["Sooner Set Elite Trainer Box", "Later Set Elite Trainer Box"])
        self.assertContains(page, "Soonest out first.")
        self.assertNotContains(page, "Clear filters")
        by_price = self.client.get(self.url + "?tab=pre-orders&sort=price")
        self.assertEqual(self.names(by_price), ["Later Set Elite Trainer Box", "Sooner Set Elite Trainer Box"])
        self.assertNotContains(by_price, "Soonest out first.")

    def test_two_shops_means_two_shops_for_the_tab_in_view(self):
        html = self.client.get(self.url + "?compared=on").content.decode()
        for label, count in (("In stock", 1), ("Pre-orders", 1)):
            self.assertRegex(html, rf'{label}</span> <span class="stock-tabs__count">{count}</span>')
        # An empty tab is left out.
        self.assertNotIn('data-tab="out-of-stock"', html)
        self.assertIn('class="stock-tabs stock-tabs--two"', html)
        self.assertEqual(self.names(self.client.get(self.url + "?tab=pre-orders&compared=on")),
                         ["Sooner Set Elite Trainer Box"])

    def test_the_tabs_replace_the_in_stock_toggle_only_where_they_show(self):
        self.assertNotContains(self.client.get(self.url), 'name="in_stock"')
        self.assertNotContains(self.client.get(self.old.get_absolute_url()), 'name="in_stock"')
        self.assertContains(self.client.get(reverse("web:search") + "?q=box"), 'name="in_stock"')
        self.assertNotContains(self.client.get(reverse("web:search") + "?q=box"), "stock-tabs")
        # An old link with the toggle on still opens the In stock tab, with nothing to clear.
        old_link = self.client.get(self.url + "?in_stock=on")
        self.assertEqual(self.names(old_link), ["Old Set Booster Box"])
        self.assertNotContains(old_link, "Clear filters")

    def test_tab_links_keep_the_filters_and_start_at_page_one(self):
        html = self.client.get(self.url + "?type=elite_trainer_box&page=1&tab=out-of-stock").content.decode()
        self.assertIn('href="?type=elite_trainer_box&amp;tab=pre-orders" data-tab="pre-orders"', html)
        self.assertIn('href="?type=elite_trainer_box" data-tab="in-stock"', html)
        self.assertIn('name="tab" value="out-of-stock"', html)
        self.assertIn('data-src="?type=elite_trainer_box&amp;tab=pre-orders&amp;pane=1"', html)
        self.assertIn("Nothing matches these filters.", html)

    def test_a_pane_is_just_its_list_never_indexed_and_links_to_whole_pages(self):
        from django.test import override_settings

        with override_settings(RIPRAPTOR_PAGE_SIZE=1):
            pane = self.client.get(self.url + "?tab=pre-orders&pane=1")
        html = pane.content.decode()
        self.assertTrue(html.lstrip().startswith("<section class=\"stock-pane\" data-tab=\"pre-orders\""))
        self.assertNotIn("<html", html)
        self.assertEqual(pane["X-Robots-Tag"], "noindex")
        self.assertIn('href="?tab=pre-orders&amp;page=2"', html)
        self.assertNotIn("pane=1", html)

    def test_empty_tabs_say_so(self):
        Listing.objects.filter(product=self.gone).delete()
        Listing.objects.filter(product__in=[self.far, self.near]).delete()
        self.assertContains(self.client.get(self.url + "?tab=pre-orders"), "No pre-orders here right now.")
        Listing.objects.filter(product=self.stocked).delete()
        # Asked for by name; the page itself opens on a tab with products.
        self.assertContains(self.client.get(self.url + "?tab=in-stock"), "Nothing here is in stock right now.")
        self.assertNotContains(self.client.get(self.url), "Nothing here is in stock right now.")
        Product.objects.exclude(pk=self.stocked.pk).update(is_active=False)
        make_listing(self.stocked, self.a)
        self.assertContains(self.client.get(self.url + "?tab=out-of-stock"), "Everything here is in stock or on pre-order.")

    def test_set_pages_have_the_tabs_too(self):
        page = self.client.get(self.old.get_absolute_url())
        self.assertContains(page, "stock-tabs")
        self.assertEqual(self.names(page), ["Old Set Booster Box"])
        self.assertEqual(self.names(self.client.get(self.old.get_absolute_url() + "?tab=out-of-stock")), ["Old Set Bundle"])

    def test_the_queries_do_not_grow_with_the_products(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.get(self.url)
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url)
        for n in range(6):
            product = make_product(self.old, name=f"Old Set Tin {n}", slug=f"old-tin-{n}", product_type=Product.Type.TIN)
            make_listing(product, self.a, price="20.00")
        self.client.get(self.url)   # the saves cleared the cached coming soon list
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertEqual(len(few), len(many))


class ProductPageRedesignTests(PageTestCase):
    """The product page's first screen: name, picture, cheapest delivered price, Buy now, then Compare prices."""

    def page(self, product):
        return self.client.get(product.get_absolute_url()).content.decode()

    def test_the_first_screen_runs_name_picture_price_buy_then_compare(self):
        html = self.page(self.etb)
        order = [html.index(marker) for marker in (
            'class="breadcrumbs"', '<h1 class="product-title">', 'class="product-facts">Elite Trainer Box<button type="button" class="info"',
            'class="product-media"', 'class="buybox__price"', 'class="buy-now"', 'class="compare-jump"',
            'id="offers-title"',
        )]
        self.assertEqual(order, sorted(order))

    def test_buy_now_goes_to_the_cheapest_available_offer_through_go(self):
        html = self.page(self.etb)
        button = html[html.index('<a class="buy-now"'):html.index("</a>", html.index('<a class="buy-now"'))]
        self.assertIn(f'href="{self.cheap.get_outbound_url()}?b=buy_now"', button)
        self.assertIn('rel="sponsored nofollow noopener"', button)
        self.assertIn('<span class="buy-now__label">Buy now</span>', button)
        self.assertIn('<span class="buy-now__shop">at Harbour Games</span>', button)
        self.assertIn("£54.99 (opens in a new tab)", button)
        # The logo's own raptor head and a bag drawn apart, so the bag can swing from the jaw, and hidden from readers.
        self.assertIn('<span class="buy-mark" aria-hidden="true"><span class="buy-mark__head"></span><svg class="buy-mark__bag"', button)

    def test_a_cheaper_shop_that_is_sold_out_is_not_where_buy_now_goes(self):
        sold = make_listing(self.etb, make_retailer("Cheap But Gone"), price="10.00",
                            availability=Listing.Availability.OUT_OF_STOCK)
        html = self.page(self.etb)
        button = html[html.index('<a class="buy-now"'):html.index("</a>", html.index('<a class="buy-now"'))]
        self.assertIn(f'href="{self.cheap.get_outbound_url()}?b=buy_now"', button)
        self.assertNotIn(sold.get_outbound_url(), button)

    def test_compare_prices_is_a_plain_link_to_a_focusable_heading(self):
        html = self.page(self.etb)
        self.assertIn('<a class="compare-jump" href="#offers-title" data-jump data-compare="buy_box">', html)
        self.assertIn('Compare prices<span class="compare-jump__arrow" aria-hidden="true">↓</span>', html)
        self.assertIn('<span class="compare-jump__note">2 shops, cheapest delivered first</span>', html)
        self.assertIn('<h2 id="offers-title" class="section-title" tabindex="-1">', html)

    def test_sold_out_and_unpriced_products(self):
        sold = self.page(self.sold_out)
        self.assertNotIn('class="buy-now"', sold)
        self.assertNotIn("buybox__sweep", sold)
        self.assertIn('class="compare-jump"', sold)   # the shops' last prices are still worth seeing
        self.assertNotIn("compare-jump__note", sold)
        unpriced = self.page(self.unpriced)
        self.assertNotIn('class="buy-now"', unpriced)
        self.assertNotIn('class="compare-jump"', unpriced)
        self.assertIn('class="product-media"><span class="product-image product-image--large"><svg class="placeholder"', unpriced)

    def test_unknown_delivery_says_so_and_leaves_room_for_the_note(self):
        Listing.objects.filter(product=self.etb).update(delivery_known=False)
        html = self.page(self.etb)
        self.assertIn("product-head--note", html)
        self.assertIn("Lowest item price", html)
        self.assertIn("£52.00</span></span> <span class=\"delivered\">+ delivery</span>", html)
        self.assertIn("Check the total at the shop before you buy.", html)

    def test_long_names_get_the_smaller_title(self):
        long = make_product(self.pre, name="Prismatic Evolutions Super Premium Collection With Extra Promo Cards And Sleeves",
                            slug="long-name")
        make_listing(long, self.harbour, price="99.00")
        self.assertIn("product-head--long", self.page(long))
        self.assertNotIn("product-head--long", self.page(self.etb))

    def test_best_in_7_days_only_with_the_price_history_behind_it(self):
        self.assertIn("Best in 7 days", self.page(self.etb))
        from catalogue.models import DailyLowestPrice
        DailyLowestPrice.objects.filter(product=self.etb).delete()
        self.assertNotIn("Best in 7 days", self.page(self.etb))

    def test_the_hero_picture_loads_first(self):
        Product.objects.filter(pk=self.etb.pk).update(image_url="https://img.example/etb.png")
        html = self.page(self.etb)
        media = html[html.index('class="product-media"'):html.index("</div>", html.index('class="product-media"'))]
        self.assertIn('fetchpriority="high"', media)
        self.assertNotIn('loading="lazy"', media)

    def test_the_queries_do_not_grow_with_the_shops(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.page(self.etb)
        with CaptureQueriesContext(connection) as few:
            self.page(self.etb)
        for n in range(5):
            make_listing(self.etb, make_retailer(f"Shop {n}"), price=f"6{n}.00")
        self.page(self.etb)
        with CaptureQueriesContext(connection) as many:
            self.page(self.etb)
        self.assertEqual(len(few), len(many))

    def test_the_arrival_animation_keeps_to_the_motion_rules(self):
        from django.contrib.staticfiles import finders

        css = open(finders.find("css/site.css"), encoding="utf-8").read()
        start = css.index("/* Arrival, once per page view")
        block = css[start:css.index("@keyframes bag-swing", start)]
        block += css[css.index("@keyframes bag-swing"):css.index("}\n}", css.index("@keyframes bag-swing"))]
        # Only when motion is allowed, from the motion tokens, and only opacity and transforms change.
        self.assertIn("@media (prefers-reduced-motion: no-preference) {", block)
        for rule in re.findall(r"animation: [^;]+;", block):
            self.assertRegex(rule, r"var\(--motion-(fast|normal|slow)\)")
        keyframes = re.findall(r"@keyframes [\w-]+ \{(.*?)\}\s*$", block, re.S | re.M)
        self.assertEqual(len(keyframes), 4)   # media-in, sweep-down, sweep-across, bag-swing
        for frames in keyframes:
            for prop in re.findall(r"([a-z-]+):", frames):
                self.assertIn(prop, ("opacity", "transform"))
        # The bag swings from the top of its handle, placed and sized by tokens.
        self.assertIn(".buy-mark__bag { position: absolute; left: var(--buy-bag-left); top: var(--buy-bag-top); width: var(--buy-bag-size); height: auto; overflow: visible; transform-origin: 50% 0; }", css)

    def test_compare_prices_script_honours_reduced_motion_and_moves_focus(self):
        from django.contrib.staticfiles import finders

        script = open(finders.find("js/product.js"), encoding="utf-8").read()
        self.assertIn('behavior: reduce.matches ? "auto" : "smooth"', script)
        self.assertIn("target.focus({ preventScroll: true })", script)


class GoldenLayoutTests(PageTestCase):
    """One golden-ratio scale for every gap, the logo at the left edge, and the raptor on every card's buy button."""

    def css(self, name):
        from django.contrib.staticfiles import finders

        return open(finders.find(f"css/{name}"), encoding="utf-8").read()

    def test_tokens_define_the_phi_scale_and_one_section_rhythm(self):
        tokens = self.css("tokens.css")
        self.assertIn("--phi: 1.618;", tokens)
        steps = re.findall(r"--space-\d: calc\(([\d.]+)rem \* var\(--density\)\);", tokens)
        self.assertEqual(len(steps), 8)
        steps = [float(step) for step in steps]
        # Each step from 1rem up and down is phi times its neighbour, to within the sixteenth-of-a-rem rounding.
        for small, large in zip(steps[2:], steps[3:]):
            self.assertAlmostEqual(large / small, 1.618, delta=0.1)
        self.assertIn("--rhythm-hero: var(--rhythm-section);", tokens)
        self.assertIn("--golden-major: 61.8%;", tokens)
        self.assertIn("--split-major: 1.618fr;", tokens)

    def test_every_section_gap_uses_the_rhythm_tokens(self):
        css = self.css("site.css")
        # The home page's first section no longer has a gap of its own.
        self.assertNotIn("home-section--games { margin-top", css)
        for rule in (".block { margin-top: var(--rhythm-section); }",
                     ".prose-page section { margin-top: var(--rhythm-section); }",
                     ".saved { margin-top: var(--rhythm-section); }"):
            self.assertIn(rule, css)
        # The home and deals pages' sections sit closer, 20px on a phone and 24px wider, so more shows on a screen.
        self.assertIn(".home-section { margin-top: var(--rhythm-home); }", css)
        self.assertIn(".hero + .home-section { margin-top: var(--rhythm-home); }", css)
        tokens = self.css("tokens.css")
        self.assertIn("--rhythm-home: calc(1.25rem * var(--density));", tokens)
        self.assertIn("--rhythm-home: calc(1.5rem * var(--density));", tokens)
        # Side by side columns use the golden split.
        self.assertEqual(css.count("minmax(0, var(--split-major)) minmax(0, var(--split-minor))"), 3)

    def test_the_logo_sits_at_the_left_with_no_back_arrow(self):
        css = self.css("site.css")
        self.assertIn(".wordmark { grid-area: brand; justify-self: start;", css)
        self.assertNotIn("justify-self: center", css[css.index("/* Header"):css.index("/* Search ---")])
        self.assertIn('grid-template-areas: "brand . nav" "search search search";', css)
        for url in (reverse("web:home"), reverse("web:deals"), self.etb.get_absolute_url()):
            html = self.client.get(url).content.decode()
            self.assertNotIn("masthead__back", html)
            self.assertNotIn("data-back", html)
            # The logo's size matches the file, so nothing shifts when it loads.
            self.assertIn('width="1200" height="304"', html)

    def test_card_buy_buttons_carry_the_raptor_mark(self):
        html = self.client.get(reverse("web:search"), {"q": "etb"}).content.decode()
        start = html.index('<a class="button button--buy button--card"')
        button = html[start:html.index("</a>", start)]
        self.assertIn('<span class="buy-mark" aria-hidden="true"><span class="buy-mark__head"></span>', button)
        self.assertNotIn("icon--bag", button)
        css = self.css("site.css")
        self.assertIn(".button--card .buy-mark { --buy-mark: var(--buy-mark-card); --buy-mark-head: var(--buy-mark-head-card);", css)
        # The bag swings only on the product page's Buy now, never in a list.
        self.assertIn(".buy-now .buy-mark__bag { animation: bag-swing", css)

    def test_card_price_and_button_share_one_row(self):
        css = self.css("site.css")
        self.assertIn('grid-template-areas: "price buy" "at at"; align-items: center;', css)
        self.assertIn(".card__offer > p { margin: 0; }", css)
        # One column on phones that never grows past the screen.
        self.assertIn(".cards { list-style: none; display: grid; grid-template-columns: minmax(0, 1fr);", css)

    def test_gaps_are_measured_glyph_to_glyph(self):
        css = self.css("site.css")
        # Section headings start at their capitals; text that closes a section or a control ends at its descenders.
        self.assertIn(".section-head__title, .section-title, .prose-page h2 { text-box: trim-start cap alphabetic; }", css)
        self.assertIn(".hero__meta, .section-head__note, .page-intro, .browse-head > p:last-child, .small-print:last-child,", css)
        # Trimming needs a block, not a flex box, around the text.
        self.assertNotIn(".section-head__title { display: flex", css)
        self.assertNotIn(".hero__meta { display: flex", css)
        # Room kept for hover lifts and row padding does not add to the section gap.
        self.assertIn(".stock-panes.is-swipe { margin-bottom: calc(-1 * var(--space-2)); }", css)
        self.assertIn(".drops:last-child > .drop:last-child > .drop__link { padding-bottom: 0; }", css)
        # The tab labels' padding is the gap above them, and the tab rule starts with the content from tablet width.
        self.assertIn("margin-top: calc(-1 * var(--space-3));", css)
        self.assertIn("@media (min-width: 48rem) { .stock-tabs { margin-inline: 0; padding-inline: 0; } }", css)
        # A subtitle sits the same distance under its page title on every page.
        self.assertIn(".page-head .page-title, .page-title:has(+ .prose--lead) { margin-bottom: var(--space-2); }", css)
        self.assertIn(".small-print p + p, .small-print + .small-print { margin-top: var(--space-2); }", css)

    def test_swipe_price_and_button_share_one_row_and_cards_share_one_inner_edge(self):
        css = self.css("site.css")
        self.assertIn(".swipe__offer { display: contents; }", css)
        self.assertIn(".swipe .button--buy { grid-column: 2; }", css)
        self.assertIn("--buybox-pad: var(--card-pad);", css)
        self.assertNotIn("--buybox-pad: var(--space-3)", css)


class StockTabsScriptTests(TestCase):
    def test_only_the_visitors_own_swipe_changes_the_tab(self):
        # Placing the list on load fires scrollend at once when motion is reduced, and Chrome can re-snap the
        # track by itself on a page opening on a later tab: neither may move a Pre-orders link onto Out of
        # stock. A move the visitor did not make goes back to the list in view and returns before select().
        from django.contrib.staticfiles import finders

        script = open(finders.find("js/tabs.js"), encoding="utf-8").read()
        settle = script[script.index("function settle() {"):script.index("if (\"onscrollend\" in window)")]
        untouched = settle[settle.index("if (!touched) {"):settle.index("return;")]
        self.assertIn("track.scrollLeft = current * track.clientWidth;", untouched)
        self.assertNotIn("select(", untouched)
        self.assertLess(settle.index("return;"), settle.index("select(index)"))
        self.assertIn('["pointerdown", "touchstart", "wheel", "keydown"]', script)


class SocialLinksTests(TestCase):
    def test_every_page_footer_links_to_the_three_accounts_in_a_new_tab(self):
        html = self.client.get(reverse("web:home")).content.decode()
        footer = html[html.index('<footer class="site-footer">'):]
        self.assertIn("<p class=\"site-footer__follow-title\">Follow us</p>", footer)
        for href, name in (
            ("https://www.instagram.com/ripraptor_uk/", "Instagram"),
            ("https://www.facebook.com/ripraptor", "Facebook"),
            ("https://www.tiktok.com/@ripraptoruk", "TikTok"),
        ):
            with self.subTest(name=name):
                link = footer[footer.index(f'href="{href}"') - 60:footer.index(f"RipRaptor on {name}")]
                self.assertIn('target="_blank" rel="noopener"', link)
                self.assertIn('class="social__link"', link)


class GamesPageTests(PageTestCase):
    """Every game on the Games page ends with a link to all its products; its sets are listed newest first, up to
    views.GAMES_PAGE_SETS, with a link that shows the rest in place; a game with no sets yet shows the link and
    no empty list."""

    def games_page(self):
        return self.client.get(reverse("web:games")).content.decode()

    def section(self, html, slug):
        start = html.index(f'aria-labelledby="game-{slug}"')
        return html[start:html.index("</section>", start)]

    def test_a_game_with_no_sets_shows_only_its_products_link(self):
        rift = make_game(name="Riftbound", slug="riftbound")
        for n in range(3):
            Product.objects.create(game=rift, name=f"Origins Booster Box {n}", product_type=Product.Type.BOOSTER_BOX)
        section = self.section(self.games_page(), "riftbound")
        self.assertNotIn("<ul", section)
        self.assertIn(f'<a href="{rift.get_absolute_url()}">See all 3 products</a>', section)

    def test_sets_newest_first_capped_with_a_link_to_the_rest(self):
        from datetime import date

        from .views import GAMES_PAGE_SETS

        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        for n in range(GAMES_PAGE_SETS + 2):
            made = make_set(magic, name=f"Set Number {n}", slug=f"set-{n}", code="", release_date=date(2020 + n, 1, 1))
            make_product(made, name=f"Set Number {n} Booster Box")
        section = self.section(self.games_page(), "magic-the-gathering")
        # The newest show; the rest wait hidden in the page until the game is named in the address (:target).
        self.assertEqual(section.count("<li>"), GAMES_PAGE_SETS)
        self.assertEqual(section.count('<li class="game-index__later">'), 2)
        self.assertLess(section.index(f"Set Number {GAMES_PAGE_SETS + 1}<"), section.index(f"Set Number {GAMES_PAGE_SETS}<"))
        self.assertLess(section.rindex("<li>"), section.index("Set Number 0<"))
        self.assertIn(f'<a class="game-index__all" href="#game-magic-the-gathering">All {GAMES_PAGE_SETS + 2} sets</a>',
                      section)
        self.assertIn(f"See all {GAMES_PAGE_SETS + 2} products</a>", section)
        self.assertIn('id="game-magic-the-gathering"', section)
        from django.contrib.staticfiles import finders

        css = open(finders.find("css/site.css"), encoding="utf-8").read()
        self.assertIn(".game-index__game:not(:has(:target)) .game-index__later, .game-index__game:has(:target) "
                      ".game-index__all { display: none; }", css)
        # The game page's All sets opens that list.
        game_page = self.client.get(magic.get_absolute_url()).content.decode()
        self.assertIn(f'href="{reverse("web:games")}#game-magic-the-gathering"', game_page)

    def test_a_few_sets_need_no_link_to_the_rest(self):
        section = self.section(self.games_page(), self.game.slug)
        self.assertIn(self.pre.name, section)
        self.assertNotIn("game-index__all", section)
        self.assertNotIn("game-index__later", section)
        self.assertIn("See all 3 products", section)

    def test_the_page_costs_the_same_however_many_games_and_sets(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        self.games_page()
        with CaptureQueriesContext(connection) as few:
            self.games_page()
        for n in range(4):
            game = make_game(name=f"Game {n}", slug=f"game-{n}")
            for m in range(3):
                make_product(make_set(game, name=f"Set {n} {m}", slug=f"set-{n}-{m}", code=""), name=f"Box {n} {m}")
        with CaptureQueriesContext(connection) as many:
            self.games_page()
        self.assertEqual(len(many), len(few))
