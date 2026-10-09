from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue import insights
from catalogue.models import DailyPageView, DailySearch, OutboundClick
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

HUMAN = {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone) Safari/605.1"}
BOT = {"HTTP_USER_AGENT": "Mozilla/5.0 (compatible; Googlebot/2.1)"}


class CountingTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.set = make_set(self.game)
        self.etb = make_product(self.set)
        self.shop = make_retailer("Harbour Games")
        self.listing = make_listing(self.etb, self.shop)

    def test_product_views_are_counted_by_slug_and_day(self):
        self.client.get(self.etb.get_absolute_url(), **HUMAN)
        self.client.get(self.etb.get_absolute_url(), **HUMAN)
        row = DailyPageView.objects.get(kind="product", key=self.etb.slug)
        self.assertEqual((row.hits, row.date), (2, timezone.localdate()))

    def test_home_game_set_and_swipe_views_are_counted(self):
        for url in (reverse("web:home"), self.game.get_absolute_url(), self.set.get_absolute_url(), reverse("web:deck")):
            self.client.get(url, **HUMAN)
        self.assertEqual(set(DailyPageView.objects.values_list("kind", flat=True)), {"home", "game", "set", "swipe"})
        self.assertEqual(DailyPageView.objects.get(kind="game").key, self.game.slug)

    def test_bots_admin_and_api_are_not_counted(self):
        self.client.get(self.etb.get_absolute_url(), **BOT)
        self.client.get(self.etb.get_absolute_url())   # no user agent at all
        self.client.get(reverse("web:search_api"), {"q": "etb"}, **HUMAN)
        self.client.get("/admin/login/", **HUMAN)
        self.assertEqual(DailyPageView.objects.count(), 0)

    def test_searches_are_recorded_with_their_result_count(self):
        self.client.get(reverse("web:search"), {"q": "  Prismatic   ETB "}, **HUMAN)
        self.client.get(reverse("web:search"), {"q": "prismatic etb"}, **HUMAN)
        self.client.get(reverse("web:search"), {"q": "nothing here at all"}, **HUMAN)
        self.assertEqual(DailySearch.objects.get(query="prismatic etb").hits, 2)
        self.assertEqual(DailySearch.objects.get(query="prismatic etb").results, 1)
        self.assertEqual(DailySearch.objects.get(query="nothing here at all").results, 0)
        # Paging through results is not a new search.
        self.client.get(reverse("web:search"), {"q": "prismatic etb", "page": 2}, **HUMAN)
        self.assertEqual(DailySearch.objects.get(query="prismatic etb").hits, 2)

    def test_report_totals_and_tables(self):
        self.client.get(self.etb.get_absolute_url(), **HUMAN)
        self.client.get(reverse("web:home"), **HUMAN)
        OutboundClick.objects.create(listing=self.listing, product=self.etb, retailer=self.shop)
        OutboundClick.objects.create(
            listing=self.listing, product=self.etb, retailer=self.shop,
            created_at=timezone.now() - timedelta(days=40),   # outside the window
        )
        DailySearch.objects.create(date=timezone.localdate(), query="nothing", results=0, hits=3)
        OutboundClick.objects.create(listing=self.listing, product=self.etb, retailer=self.shop, source="watchlist")
        data = insights.report(30)
        self.assertEqual((data["total_views"], data["total_clicks"], data["watchlist_clicks"]), (2, 2, 1))
        self.assertEqual(data["click_rate"], 200.0)
        self.assertEqual(data["top_products"][0]["product"], self.etb)
        self.assertEqual(data["shops"], [{"name": "Harbour Games", "clicks": 2}])
        self.assertEqual(data["games"][0]["name"], "Pokémon")
        self.assertEqual(data["empty_searches"][0]["query"], "nothing")
        self.assertEqual(len(data["by_day"]), 30)


class InsightsPageTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ben", password="pw", is_staff=True)
        self.product = make_product(make_set(make_game()))

    def test_page_is_staff_only(self):
        response = self.client.get(reverse("insights"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_page_renders_for_staff_with_a_bounded_window(self):
        self.client.force_login(self.staff)
        self.client.get(self.product.get_absolute_url(), **HUMAN)
        response = self.client.get(reverse("insights"), {"days": "7"})
        self.assertContains(response, "page views")
        self.assertContains(response, self.product.name)
        self.assertContains(response, '<option value="7" selected>')
        self.assertEqual(self.client.get(reverse("insights"), {"days": "nonsense"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("insights"), {"days": "99999"}).status_code, 200)

    def test_admin_index_links_to_insights(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/admin/"), reverse("insights"))

    def test_report_runs_in_a_fixed_number_of_queries(self):
        # 33 since the pre-order line (listings first seen on pre-order this week) joined the report.
        with self.assertNumQueries(33):
            insights.report(30)


class VisitorTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()))
        self.url = self.product.get_absolute_url()

    def test_the_same_visitor_counts_once_a_day_and_different_browsers_count_apart(self):
        from catalogue.models import DailyVisitor

        self.client.get(self.url, **HUMAN)
        self.client.get(self.url, **HUMAN)
        self.client.get(reverse("web:home"), **HUMAN)
        self.assertEqual(DailyVisitor.objects.count(), 1)
        self.client.get(self.url, HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/124")
        self.assertEqual(DailyVisitor.objects.count(), 2)
        self.client.get(self.url, **BOT)
        self.assertEqual(DailyVisitor.objects.count(), 2)
        token = DailyVisitor.objects.first().token
        self.assertEqual(len(token), 32)
        self.assertNotIn("127.0.0.1", token)

    def test_a_browser_that_comes_back_another_day_counts_as_returning(self):
        from unittest import mock

        from catalogue.models import DailyVisitor

        response = self.client.get(self.url, **HUMAN)
        cookie = response.cookies[insights.RETURN_COOKIE]
        self.assertEqual((cookie.value, cookie["httponly"], cookie["samesite"]), ("1", True, "Lax"))
        self.assertEqual(int(cookie["max-age"]), 365 * 24 * 3600)
        self.assertFalse(DailyVisitor.objects.get().returning)
        # The same day: no second row, and the cookie is not set again.
        again = self.client.get(self.url, **HUMAN)
        self.assertNotIn(insights.RETURN_COOKIE, again.cookies)
        tomorrow = timezone.localdate() + timedelta(days=1)
        with mock.patch.object(insights.timezone, "localdate", return_value=tomorrow):
            self.client.get(self.url, **HUMAN)
        rows = DailyVisitor.objects.order_by("date")
        self.assertEqual([(r.date, r.returning) for r in rows], [(tomorrow - timedelta(days=1), False), (tomorrow, True)])

    def test_a_visitor_past_the_daily_cap_stops_counting_and_scripts_are_bots(self):
        from unittest import mock

        from catalogue.models import DailyVisitor

        with mock.patch.object(insights, "VIEWS_PER_VISITOR_CAP", 3):
            for _ in range(5):
                self.client.get(self.url, **HUMAN)
            self.assertEqual(DailyPageView.objects.get(kind="product").hits, 3)
            self.assertEqual(DailyVisitor.objects.get().views, 3)
            self.assertEqual(insights.report(7)["capped_visitors"], 1)
        for agent in ("Python-urllib/3.12", "Mozilla/5.0 HeadlessChrome/120", "Go-http-client/1.1", "Scrapy/2.11"):
            self.client.get(self.url, HTTP_USER_AGENT=agent)
        self.assertEqual(DailyPageView.objects.get(kind="product").hits, 3)

    def test_watchlist_rows_are_not_page_views(self):
        DailyPageView.objects.create(date=timezone.localdate(), kind="watched", key="x", hits=40)
        DailyPageView.objects.create(date=timezone.localdate(), kind="home", key="", hits=2)
        self.assertEqual(insights.report(7)["total_views"], 2)

    def test_bots_get_no_cookie(self):
        response = self.client.get(self.url, **BOT)
        self.assertNotIn(insights.RETURN_COOKIE, response.cookies)

    def test_deals_and_latest_drops_pages_are_counted(self):
        self.client.get(reverse("web:deals"), **HUMAN)
        self.client.get(reverse("web:new"), **HUMAN)
        self.assertEqual(set(DailyPageView.objects.values_list("kind", flat=True)), {"deals", "new"})

    def test_the_address_behind_the_proxy_is_the_one_used(self):
        from catalogue.models import DailyVisitor

        self.client.get(self.url, HTTP_X_FORWARDED_FOR="203.0.113.9", **HUMAN)
        self.client.get(self.url, HTTP_X_FORWARDED_FOR="198.51.100.4", **HUMAN)
        self.assertEqual(DailyVisitor.objects.count(), 2)

    def test_country_is_recorded_when_the_database_is_there(self):
        from unittest import mock

        from catalogue import geo
        from catalogue.models import DailyVisitor

        class FakeReader:
            def get(self, ip):
                return {"country": {"iso_code": "gb"}} if ip.startswith("203.") else None

        with mock.patch.object(geo, "reader", return_value=FakeReader()):
            self.client.get(self.url, HTTP_X_FORWARDED_FOR="203.0.113.9", **HUMAN)
            self.client.get(self.url, HTTP_X_FORWARDED_FOR="198.51.100.4", **HUMAN)
        self.assertEqual(sorted(DailyVisitor.objects.values_list("country", flat=True)), ["", "GB"])

    def test_no_database_means_no_country_and_no_error(self):
        from catalogue import geo

        with self.settings(RIPRAPTOR_GEOIP_DB="/nowhere/dbip.mmdb"):
            geo.reset()
            self.assertEqual(geo.country_of("203.0.113.9"), "")
            self.client.get(self.url, **HUMAN)

    def test_fetch_downloads_and_unpacks_this_months_database(self):
        import gzip
        import io
        import tempfile
        from datetime import date

        from catalogue import geo

        asked = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(url, timeout=0):
            asked.append(url)
            if "2026-10" in url:
                raise OSError("not published yet")
            return Response(gzip.compress(b"MMDB-BYTES"))

        with tempfile.TemporaryDirectory() as folder:
            path = f"{folder}/dbip-country.mmdb"
            geo.fetch(path=path, when=date(2026, 10, 2), opener=opener)
            self.assertEqual(open(path, "rb").read(), b"MMDB-BYTES")
        self.assertTrue(asked[0].endswith("dbip-country-lite-2026-10.mmdb.gz"))
        self.assertTrue(asked[1].endswith("dbip-country-lite-2026-09.mmdb.gz"))

    def test_insights_shows_visitors_and_countries(self):
        from datetime import timedelta

        from catalogue.models import DailyVisitor

        today = timezone.localdate()
        DailyVisitor.objects.create(date=today, token="a" * 32, country="GB", returning=True)
        DailyVisitor.objects.create(date=today, token="b" * 32, country="GB")
        DailyVisitor.objects.create(date=today - timedelta(days=1), token="c" * 32, country="US")
        DailyVisitor.objects.create(date=today - timedelta(days=60), token="d" * 32, country="DE", returning=True)
        data = insights.report(30)
        self.assertEqual(data["total_visitors"], 3)
        self.assertEqual((data["returning_visitors"], data["returning_share"]), (1, 33.3))
        self.assertEqual(data["by_day"][-1]["returning"], 1)
        self.assertEqual(data["countries"][0], {"code": "GB", "name": "United Kingdom", "visitors": 2, "share": 66.7})
        staff = User.objects.create_user("ben2", password="pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("insights"))
        self.assertContains(response, "United Kingdom")
        self.assertContains(response, "<b>3</b> visitors")
        self.assertContains(response, "<b>33.3%</b> returning visitors (1 had been before)")


class ImprovementTests(TestCase):
    def setUp(self):
        from catalogue.models import Retailer

        self.product = make_product(make_set(make_game()))
        self.paid = make_retailer("Paid Shop", affiliate_url_template="https://aff.example/?u={url}",
                                  source_type=Retailer.Source.SHOPIFY, source_url="https://paid.example/")
        self.free = make_retailer("Free Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://free.example/")
        self.paid_listing = make_listing(self.product, self.paid, price="50.00")
        self.free_listing = make_listing(self.product, self.free, price="49.00")

    def test_device_and_source_are_recorded_by_domain_only(self):
        from catalogue.models import DailyVisitor

        self.client.get(self.product.get_absolute_url(), HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile Safari",
                        HTTP_REFERER="https://www.google.co.uk/search?q=prismatic+etb")
        self.client.get(self.product.get_absolute_url(), HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/124",
                        HTTP_REFERER="https://old.reddit.com/r/PokemonTCG/comments/abc")
        self.client.get(self.product.get_absolute_url(), HTTP_USER_AGENT="Mozilla/5.0 (iPad; CPU OS 17_0) Safari")
        rows = {(v.device, v.source) for v in DailyVisitor.objects.all()}
        self.assertEqual(rows, {("mobile", "google.co.uk"), ("desktop", "old.reddit.com"), ("tablet", "")})
        data = insights.report(30)
        groups = {s["name"]: s["visitors"] for s in data["sources"]}
        self.assertEqual(groups, {"Search engines": 1, "Reddit": 1, "Typed or bookmarked": 1})
        self.assertEqual({d["name"]: d["visitors"] for d in data["devices"]}, {"Phone": 1, "Tablet": 1, "Computer": 1})

    def test_unpaid_clicks_and_broken_shops_lead_the_improvements(self):
        from catalogue.models import ImportRun

        for _ in range(3):
            OutboundClick.objects.create(listing=self.free_listing, product=self.product, retailer=self.free)
        OutboundClick.objects.create(listing=self.paid_listing, product=self.product, retailer=self.paid)
        ImportRun.objects.create(retailer=self.paid, finished_at=timezone.now())
        ImportRun.objects.create(retailer=self.free, finished_at=timezone.now(), error="HTTP 503")
        data = insights.report(30)
        self.assertEqual(data["earning_clicks"], 1)
        self.assertEqual(data["unpaid"][0]["name"], "Free Shop")
        titles = [i["title"] for i in data["improvements"]]
        self.assertEqual(titles[0], "1 shop not updating")
        self.assertEqual(titles[1], "75% of clicks earn nothing")
        health = {s["name"]: s for s in data["shops_health"]}
        self.assertEqual(health["Free Shop"]["problem"], "HTTP 503")
        self.assertTrue(health["Paid Shop"]["earns"])

    def test_viewed_never_clicked_and_one_shop_products_are_found(self):
        lonely = make_product(self.product.product_set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle", product_type="bundle")
        make_listing(lonely, self.free, price="30.00")
        for _ in range(3):
            self.client.get(lonely.get_absolute_url(), **HUMAN)
        data = insights.report(30)
        self.assertEqual([r["product"] for r in data["viewed_no_click"]], [lonely])
        self.assertEqual(data["viewed_no_click"][0]["shops"], 1)
        self.assertEqual([r["product"] for r in data["one_shop"]], [lonely])

    def test_page_shows_improvements_first(self):
        staff = User.objects.create_user("ben3", password="pw", is_staff=True)
        self.client.force_login(staff)
        DailySearch.objects.create(date=timezone.localdate(), query="obscure box", results=0, hits=2)
        html = self.client.get(reverse("insights")).content.decode()
        self.assertLess(html.index("Areas to improve"), html.index("page views"))
        self.assertIn("1 search found nothing", html)
        self.assertIn("Shop health", html)
        self.assertIn("Where visitors come from", html)


class ShopReadingTests(TestCase):
    def test_shop_health_explains_cadence_pause_and_backoff(self):
        from catalogue.models import ImportRun, Retailer

        now = timezone.now()
        shopify = {"source_type": Retailer.Source.SHOPIFY, "source_url": "https://shop.example/"}
        regular = make_retailer("Regular Shop", read_every_minutes=45,
                                next_read_at=now + timedelta(minutes=30), **shopify)
        make_retailer("Paused Shop", reading_paused=True, **shopify)
        make_retailer("Failing Shop", error_streak=3, backoff_until=now + timedelta(minutes=40),
                      last_error="Could not fetch https://failing.example/: HTTP Error 503", **shopify)
        slow = make_retailer("Slow Shop", read_every_minutes=360, **shopify)
        # The same error on its latest run is shown under Problem, so Reading does not repeat it.
        url_error = "Could not fetch https://repeat.example/collections/all/products.json?page=3&limit=250: HTTP Error 503"
        repeat = make_retailer("Repeat Shop", error_streak=2, backoff_until=now + timedelta(minutes=10),
                               last_error=url_error, **shopify)
        ImportRun.objects.create(retailer=repeat, finished_at=now - timedelta(minutes=1), error=url_error)
        for shop in (regular, slow):
            ImportRun.objects.create(retailer=shop, started_at=now - timedelta(hours=8),
                                     finished_at=now - timedelta(hours=8))
        health = {s["name"]: s for s in insights.report(30)["shops_health"]}
        local = timezone.localtime
        self.assertIn(health["Regular Shop"]["reading"], {
            f"Reads every 45 min, next {local(regular.next_read_at):%H:%M}",
            f"Reads every 45 min, next {local(regular.next_read_at):%d %b %H:%M}",
        })
        self.assertEqual(health["Paused Shop"]["reading"], "Paused")
        self.assertRegex(health["Failing Shop"]["reading"],
                         r"^Backing off until [\d\w :]+ after 3 errors: Could not fetch https://failing\.example/: HTTP Error 503$")
        self.assertEqual(health["Slow Shop"]["reading"], "Reads every 360 min")
        self.assertRegex(health["Repeat Shop"]["reading"], r"^Backing off until [\d\w :]+ after 2 errors$")
        self.assertEqual(health["Repeat Shop"]["problem"], url_error)
        # Not read for 8 hours: late for a shop read every 45 minutes, on time for one read every 6 hours.
        self.assertTrue(health["Regular Shop"]["problem"].startswith("Last read"))
        self.assertEqual(health["Slow Shop"]["problem"], "")

        staff = User.objects.create_user("ben", password="pw", is_staff=True)
        self.client.force_login(staff)
        html = self.client.get(reverse("insights")).content.decode()
        self.assertIn("<th>Reading</th>", html)
        self.assertIn("after 3 errors", html)
        self.assertEqual(html.count("repeat.example"), 1)
        # A long address in a cell wraps instead of making the page scroll sideways.
        self.assertIn(".ins td.wrap { overflow-wrap: anywhere; }", html)
        self.assertIn('<td class="wrap">Backing off until', html)


class PreorderPulseInsightTests(TestCase):
    def setUp(self):
        from catalogue.models import Retailer

        self.now = timezone.now()
        self.shop = make_retailer("Pulse Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://pulse.example/",
                                  collections_polled_at=self.now - timedelta(minutes=3), collections_ok=True)
        self.set = make_set(make_game())

    def preorder(self, n, published_minutes_ago, waited_minutes, retailer=None):
        from catalogue.models import Listing

        published = self.now - timedelta(minutes=published_minutes_ago)
        listing = make_listing(make_product(self.set, name=f"Box {n}", slug=f"box-{n}"), retailer or self.shop,
                               availability=Listing.Availability.PREORDER)
        Listing.objects.filter(pk=listing.pk).update(
            shop_published_at=published, first_preorder_at=published + timedelta(minutes=waited_minutes)
        )

    def test_the_median_needs_five_timed_preorders(self):
        from catalogue.models import Retailer

        for n, wait in enumerate((4, 9, 12, 30)):
            self.preorder(n, 600, wait)
        # Published long ago and only opened now, or seen before it was published: never timed.
        self.preorder(10, 60 * 24 * 30, 60 * 24 * 30 - 5)
        self.preorder(11, 60, -5)
        # Marketplaces are not shops opening pre-orders.
        self.preorder(12, 60, 1, retailer=make_retailer("eBay", source_type=Retailer.Source.EBAY))
        line = insights.preorder_pulse(self.now)
        self.assertEqual((line["listed"], line["timed"], line["median"]), (6, 4, None))
        self.preorder(4, 600, 14)
        line = insights.preorder_pulse(self.now)
        self.assertEqual((line["listed"], line["timed"], line["median"]), (7, 5, 12))
        self.preorder(5, 600, 16)
        self.assertEqual(insights.preorder_pulse(self.now)["median"], 13)

    def test_preorders_listed_before_the_stamp_existed_are_never_timed(self):
        from decimal import Decimal

        from catalogue.importers import Offer, apply_offers
        from catalogue.models import Listing

        offers = []
        for n in range(5):
            product = make_product(self.set, name=f"Old Box {n}", slug=f"old-box-{n}", ean=f"019621411200{n}")
            listing = make_listing(product, self.shop, availability=Listing.Availability.PREORDER,
                                   url=f"https://pulse.example/products/old-box-{n}")
            offers.append(Offer(title=product.name, url=listing.url, price=listing.price + Decimal("1"),
                                ean=product.ean, availability=Listing.Availability.PREORDER,
                                published_at=self.now - timedelta(days=5)))
        apply_offers(self.shop, offers, complete=False)
        self.assertEqual(Listing.objects.filter(shop_published_at__isnull=False).count(), 5)
        line = insights.preorder_pulse(self.now)
        self.assertEqual((line["listed"], line["timed"], line["median"]), (0, 0, None))

    def test_the_page_shows_the_line_and_each_shops_preorders_and_last_pulse(self):
        staff = User.objects.create_user("ben", password="pw", is_staff=True)
        self.client.force_login(staff)
        self.preorder(1, 30, 6)
        html = self.client.get(reverse("insights")).content.decode()
        self.assertIn("Pre-orders: 1 listed, not enough data yet on how fast they appear here (1 of 5 needed) (last 7 days).", html)
        self.assertIn("<th>Pre-orders</th>", html)
        self.assertIn("<th>Last pulse</th>", html)
        health = {s["name"]: s for s in insights.report(7)["shops_health"]}
        self.assertEqual(health["Pulse Shop"]["preorders"], 1)
        self.assertEqual(health["Pulse Shop"]["pulse"], insights.clock(self.now - timedelta(minutes=3), self.now))
        for n, wait in enumerate((5, 7, 9, 11), start=2):
            self.preorder(n, 30, wait)
        html = self.client.get(reverse("insights")).content.decode()
        self.assertIn("Pre-orders: 5 listed, median 7 minutes from a shop publishing a pre-order to it appearing here (last 7 days).", html)
