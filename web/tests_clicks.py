"""Clicks from people carry our tracking and count; direct hits reach the plain shop address and do not."""

import re
from pathlib import Path

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import clicks, heat, insights, popularity, pricing
from catalogue.models import OutboundClick, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

BROWSER = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
EBAY_AFFILIATE = (
    "https://www.ebay.co.uk/itm/306123456789?_skw=etb&hash=item4746&mkevt=1&mkcid=1&mkrid=710-53481-19255-0"
    "&campid=5339999999&customid=&toolid=10001&siteid=3"
)


class ClickTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.product_set = make_set(cls.game)
        cls.product = make_product(cls.product_set)
        cls.shop = make_retailer("Harbour Games", affiliate_url_template="https://www.awin1.example/cread.php?p={url}")
        cls.listing = make_listing(cls.product, cls.shop)
        cls.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY, website="https://www.ebay.co.uk/")
        cls.ebay_listing = make_listing(cls.product, cls.ebay, url=EBAY_AFFILIATE)

    def setUp(self):
        cache.clear()

    def go(self, listing, method="get", referer="http://testserver/products/x/", agent=BROWSER, **extra):
        headers = {"HTTP_USER_AGENT": agent, **extra}
        if referer is not None:
            headers["HTTP_REFERER"] = referer
        return getattr(self.client, method)(listing.get_outbound_url(), **headers)


class GoTests(ClickTestCase):
    def test_a_click_from_our_page_keeps_tracking_and_counts(self):
        response = self.go(self.listing)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://www.awin1.example/cread.php?p=https%3A"))
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(OutboundClick.objects.people().count(), 1)
        self.assertEqual(OutboundClick.objects.suspects().count(), 0)

    def test_ebay_click_from_our_page_keeps_the_campaign(self):
        response = self.go(self.ebay_listing, referer="https://www.ripraptor.com/")
        self.assertEqual(response["Location"], EBAY_AFFILIATE)
        self.assertEqual(OutboundClick.objects.people().count(), 1)

    def test_our_own_hosts_count(self):
        for referer in ("https://ripraptor.com/drops/", "https://www.ripraptor.com/", "http://testserver/"):
            self.assertFalse(clicks.suspect_reason(self.request(referer)), referer)

    def request(self, referer, **extra):
        from django.test import RequestFactory

        return RequestFactory().get("/go/1/", HTTP_REFERER=referer, HTTP_USER_AGENT=BROWSER, **extra)

    def test_localhost_counts_only_in_debug(self):
        from django.test import RequestFactory

        def local():
            return RequestFactory().get("/go/1/", HTTP_HOST="ripraptor.com", HTTP_REFERER="http://localhost:8000/",
                                        HTTP_USER_AGENT=BROWSER)
        with override_settings(DEBUG=False, ALLOWED_HOSTS=["ripraptor.com"]):
            self.assertEqual(clicks.suspect_reason(local()), "referer")
        with override_settings(DEBUG=True, ALLOWED_HOSTS=["ripraptor.com"]):
            self.assertEqual(clicks.suspect_reason(local()), "")

    def assert_direct(self, response, listing, plain, saved):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], plain)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(OutboundClick.objects.people().count(), 0)
        self.assertEqual(OutboundClick.objects.suspects().count(), saved)

    def test_no_referer_goes_to_the_plain_address(self):
        self.assert_direct(self.go(self.listing, referer=None), self.listing, self.listing.url, 1)

    def test_a_foreign_referer_goes_to_the_plain_address(self):
        for referer in ("https://evil.example/", "https://ripraptor.com.evil.example/", "not a url", "ftp://ripraptor.com/"):
            response = self.go(self.listing, referer=referer)
            self.assertEqual(response["Location"], self.listing.url, referer)
        self.assertEqual(OutboundClick.objects.people().count(), 0)

    def test_head_goes_to_the_plain_address_and_is_not_saved(self):
        self.assert_direct(self.go(self.listing, method="head"), self.listing, self.listing.url, 0)

    def test_post_is_not_allowed(self):
        self.assertEqual(self.go(self.listing, method="post").status_code, 405)

    def test_prefetch_goes_to_the_plain_address(self):
        for header in ({"HTTP_PURPOSE": "prefetch"}, {"HTTP_SEC_PURPOSE": "prefetch;prerender"}, {"HTTP_X_MOZ": "prefetch"}):
            response = self.go(self.ebay_listing, **header)
            self.assertEqual(response["Location"], "https://www.ebay.co.uk/itm/306123456789?_skw=etb&hash=item4746", header)
        self.assertEqual(OutboundClick.objects.people().count(), 0)
        self.assertEqual(OutboundClick.objects.suspects().count(), 3)

    def test_robots_go_to_the_plain_address_and_are_not_saved(self):
        for agent in ("Googlebot/2.1", "", "python-requests/2.32", "HeadlessChrome/120"):
            response = self.go(self.ebay_listing, agent=agent)
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("campid", response["Location"])
        self.assertFalse(OutboundClick.objects.exists())

    def test_a_connection_over_the_hourly_cap_goes_to_the_plain_address(self):
        for _ in range(clicks.CLICKS_PER_HOUR):
            self.assertEqual(self.go(self.ebay_listing)["Location"], EBAY_AFFILIATE)
        response = self.go(self.ebay_listing)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("campid", response["Location"])
        self.assertEqual(OutboundClick.objects.people().count(), clicks.CLICKS_PER_HOUR)
        self.assertEqual(OutboundClick.objects.suspects().count(), 1)
        # Another connection is not held back.
        self.assertEqual(self.go(self.ebay_listing, REMOTE_ADDR="10.0.0.9")["Location"], EBAY_AFFILIATE)

    def test_the_cap_never_stores_the_address(self):
        self.go(self.listing, REMOTE_ADDR="203.0.113.7")
        keys = list(getattr(cache, "_cache", {}).keys())
        self.assertTrue(keys)
        self.assertFalse(any("203.0.113.7" in str(key) for key in keys))

    def test_ebay_tracking_is_stripped_and_the_item_kept(self):
        plain = clicks.plain_url(EBAY_AFFILIATE)
        self.assertEqual(plain, "https://www.ebay.co.uk/itm/306123456789?_skw=etb&hash=item4746")
        for name in ("mkevt", "mkcid", "mkrid", "campid", "customid", "toolid", "siteid"):
            self.assertNotIn(name + "=", plain)
        self.assertEqual(clicks.plain_url("https://www.ebay.co.uk/itm/1?campid=5&mkevt=1"), "https://www.ebay.co.uk/itm/1")
        self.assertEqual(clicks.plain_url("https://www.amazon.co.uk/dp/B0X?tag=ripraptor-21&th=1"),
                         "https://www.amazon.co.uk/dp/B0X?th=1")
        # Other shops keep their address exactly, query and all.
        self.assertEqual(clicks.plain_url("https://shop.example/p/etb?variant=12"), "https://shop.example/p/etb?variant=12")
        self.assertEqual(clicks.plain_url("https://notebay.example/p?campid=1"), "https://notebay.example/p?campid=1")

    def test_the_affiliate_format_is_not_applied_to_direct_hits(self):
        response = self.go(self.listing, referer=None)
        self.assertNotIn("awin1", response["Location"])
        self.assertEqual(response["Location"], self.listing.url)

    def test_watchlist_source_is_kept(self):
        self.client.get(self.listing.get_outbound_url() + "?from=watchlist", HTTP_USER_AGENT=BROWSER,
                        HTTP_REFERER="http://testserver/watchlist/")
        self.assertEqual(OutboundClick.objects.get().source, "watchlist")

    def test_query_count_stays_small(self):
        with self.assertNumQueries(2):   # the listing, the click
            self.go(self.listing)
        with self.assertNumQueries(2):
            self.go(self.listing, referer=None)
        with self.assertNumQueries(1):
            self.go(self.listing, agent="Googlebot/2.1")


class NetworkLinkTests(ClickTestCase):
    """A feed can give a listing an affiliate network's click address. A direct hit must not go through it."""

    AWIN_PCLICK = "https://www.awin1.com/pclick.php?p=123&a=3111686&m=999"

    def test_an_awin_feed_link_never_reaches_awin_on_a_direct_hit(self):
        from catalogue.importers import feed_offers

        feed = ("aw_deep_link,merchant_deep_link,product_name,search_price,in_stock\n"
                f"{self.AWIN_PCLICK},https://shop.example/p/x,ETB,44.99,1\n")
        offer = next(feed_offers(feed))
        shop = make_retailer("Feed Shop", source_type=Retailer.Source.FEED, website="https://shop.example/")
        listing = make_listing(self.product, shop, url=offer.url)
        self.assertEqual(listing.url, self.AWIN_PCLICK)
        # No shop address inside Awin's product click, so the shop's front page.
        self.assertEqual(self.go(listing, referer=None)["Location"], "https://shop.example/")
        self.assertEqual(self.go(listing, agent="Googlebot/2.1")["Location"], "https://shop.example/")
        # A person on our pages still goes through Awin.
        self.assertEqual(self.go(listing)["Location"], self.AWIN_PCLICK)

    def test_network_links_with_the_shop_address_inside_go_to_that_address(self):
        cases = {
            "https://www.awin1.com/cread.php?awinmid=999&awinaffid=3111686&ued=https%3A%2F%2Fshop.example%2Fp%2Fx%3Fv%3D1":
                "https://shop.example/p/x?v=1",
            "https://www.anrdoezrs.net/click-100-200?url=https%3A%2F%2Fshop.example%2Fp%2Fy": "https://shop.example/p/y",
            "https://www.kqzyfj.com/click-100-200?url=https%3A%2F%2Fwww.amazon.co.uk%2Fdp%2FB0X%3Ftag%3Dx-21":
                "https://www.amazon.co.uk/dp/B0X",
            "https://click.linksynergy.com/deeplink?id=abc&mid=1&murl=https%3A%2F%2Fzatu.example%2Fp": "https://zatu.example/p",
            self.AWIN_PCLICK: "",
            "https://www.awin1.com/cread.php?ued=https%3A%2F%2Fwww.awin1.com%2Fpclick.php%3Fp%3D1": "",
            "https://www.awin1.com/cread.php?ued=javascript%3Aalert(1)": "",
        }
        for url, plain in cases.items():
            with self.subTest(url=url):
                self.assertEqual(clicks.plain_url(url), plain)


class MarketplaceSearchTests(ClickTestCase):
    """The Amazon and eBay search buttons on a product page go through /go/search/, like a shop's Buy button."""

    def search(self, market, referer="http://testserver/products/x/", agent=BROWSER, method="get"):
        headers = {"HTTP_USER_AGENT": agent}
        if referer is not None:
            headers["HTTP_REFERER"] = referer
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21", RIPRAPTOR_EBAY_CAMPAIGN_ID="5339999999"):
            return getattr(self.client, method)(reverse("web:go_search", args=[self.product.slug, market]), **headers)

    def test_a_person_on_our_pages_gets_the_tracked_search(self):
        ebay = self.search("ebay")
        self.assertEqual(ebay.status_code, 302)
        self.assertEqual(ebay["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("campid=5339999999", ebay["Location"])
        self.assertIn("tag=ripraptor-21", self.search("amazon")["Location"])

    def test_anything_else_gets_the_same_search_without_tracking(self):
        for response in (self.search("ebay", referer=None), self.search("ebay", agent="Googlebot/2.1"),
                         self.search("ebay", referer="https://elsewhere.example/"), self.search("ebay", method="head")):
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith("https://www.ebay.co.uk/sch/i.html?_nkw="))
            for name in ("campid", "mkevt", "mkcid", "mkrid", "toolid"):
                self.assertNotIn(name + "=", response["Location"])
        amazon = self.search("amazon", referer=None)["Location"]
        self.assertTrue(amazon.startswith("https://www.amazon.co.uk/s?k="))
        self.assertNotIn("tag=", amazon)
        self.assertEqual(OutboundClick.objects.count(), 0)

    def test_unknown_market_or_no_tracking_id_is_not_found(self):
        self.assertEqual(self.search("etsy").status_code, 404)
        with override_settings(RIPRAPTOR_EBAY_CAMPAIGN_ID=""):
            url = reverse("web:go_search", args=[self.product.slug, "ebay"])
            self.assertEqual(self.client.get(url, HTTP_REFERER="http://testserver/").status_code, 404)

    def test_robots_txt_keeps_crawlers_out(self):
        self.assertIn("Disallow: /go/", self.client.get("/robots.txt").content.decode())
        self.assertTrue(reverse("web:go_search", args=["x", "ebay"]).startswith("/go/"))

    def test_no_public_page_carries_our_campaign_or_tag(self):
        sold_out = make_product(self.product_set, name="Sold Out Box", slug="sold-out-box")
        make_listing(sold_out, self.shop, availability="out_of_stock")
        pages = [self.product.get_absolute_url(), sold_out.get_absolute_url(), reverse("web:home"),
                 self.game.get_absolute_url(), self.product_set.get_absolute_url()]
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21", RIPRAPTOR_EBAY_CAMPAIGN_ID="5339999999"):
            for url in pages:
                with self.subTest(url=url):
                    body = self.client.get(url).content.decode()
                    self.assertNotIn("5339999999", body)
                    self.assertNotIn("ripraptor-21", body)
            body = self.client.get(sold_out.get_absolute_url()).content.decode()
        self.assertIn(f'href="/go/search/{sold_out.slug}/ebay/"', body)
        self.assertIn(f'href="/go/search/{sold_out.slug}/amazon/"', body)


class SuspectsLeftOutTests(ClickTestCase):
    def setUp(self):
        super().setUp()
        self.other = make_product(self.product_set, name="Prismatic Evolutions Booster Bundle")
        other_listing = make_listing(self.other, self.shop)
        OutboundClick.objects.create(listing=self.listing, product=self.product, retailer=self.shop)
        for _ in range(5):
            OutboundClick.objects.create(listing=other_listing, product=self.other, retailer=self.shop, suspect=True)

    def test_insights_counts_people_only_and_shows_direct_hits(self):
        data = insights.report(7)
        self.assertEqual(data["total_clicks"], 1)
        self.assertEqual([s["clicks"] for s in data["shops"]], [1])
        today = data["click_week"][-1]
        self.assertEqual((today["people"], today["direct"]), (1, 5))
        self.assertEqual(len(data["click_week"]), 7)

    def test_insights_page_shows_both_lines(self):
        staff = User.objects.create_user("ben", password="pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("insights"), {"days": "30"})
        self.assertContains(response, "Clicks from people")
        self.assertContains(response, "Direct hits sent on without tracking")
        self.assertContains(response, f"past {clicks.CLICKS_PER_HOUR} clicks in an hour")
        page = response.content.decode()
        row = re.search(r"<tr><td>" + timezone.localdate().strftime("%a %-d %b") + r'</td><td class="n">(\d+)</td><td class="n">(\d+)</td></tr>', page)
        self.assertEqual(row.groups(), ("1", "5"))

    def test_popular_heat_and_set_interest_ignore_suspects(self):
        self.assertEqual([p.pk for p in pricing.popular()], [self.product.pk])
        self.assertEqual(popularity.interest_counts([self.product_set.pk])[self.product_set.pk][1], 1)
        with_suspects = heat.heat_scores()
        OutboundClick.objects.suspects().delete()
        self.assertEqual(heat.heat_scores(), with_suspects)
        OutboundClick.objects.people().delete()
        self.assertNotEqual(heat.heat_scores(), with_suspects)   # the one real click does count

    def test_every_click_count_in_the_code_leaves_suspects_out(self):
        """Any OutboundClick query that is not a write goes through people() or suspects()."""
        root = Path(__file__).resolve().parent.parent
        allowed = re.compile(r"OutboundClick\.objects\.(people|suspects|create|bulk_create|all\(\)\.delete)\b"
                             r"|OutboundClick\.objects\.filter\([^)]*\)\.(update|delete)\(")
        offenders = []
        for folder in ("catalogue", "web", "content", "inbox"):
            for path in (root / folder).rglob("*.py"):
                if path.name.startswith("tests") or "migrations" in path.parts:
                    continue
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    if "OutboundClick.objects." in line and not allowed.search(line):
                        offenders.append(f"{path.relative_to(root)}:{number}")
        # The Insights report filters its window first and splits it into people and direct hits.
        offenders = [o for o in offenders if not o.startswith("catalogue/insights.py")]
        self.assertEqual(offenders, [])
        source = (root / "catalogue" / "insights.py").read_text()
        self.assertIn("clicks = all_clicks.people()", source)
