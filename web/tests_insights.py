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
        data = insights.report(30)
        self.assertEqual((data["total_views"], data["total_clicks"]), (2, 1))
        self.assertEqual(data["click_rate"], 100.0)
        self.assertEqual(data["top_products"][0]["product"], self.etb)
        self.assertEqual(data["shops"], [{"name": "Harbour Games", "clicks": 1}])
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
        with self.assertNumQueries(14):
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
        DailyVisitor.objects.create(date=today, token="a" * 32, country="GB")
        DailyVisitor.objects.create(date=today, token="b" * 32, country="GB")
        DailyVisitor.objects.create(date=today - timedelta(days=1), token="c" * 32, country="US")
        DailyVisitor.objects.create(date=today - timedelta(days=60), token="d" * 32, country="DE")
        data = insights.report(30)
        self.assertEqual(data["total_visitors"], 3)
        self.assertEqual(data["countries"][0], {"code": "GB", "name": "United Kingdom", "visitors": 2, "share": 66.7})
        staff = User.objects.create_user("ben2", password="pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("insights"))
        self.assertContains(response, "United Kingdom")
        self.assertContains(response, "<b>3</b> visitors")
