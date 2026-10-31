"""People, not scripts: a visitor counts as a person once their browser runs the page (site.js reports in), and the
dashboard shows who is on the site now. The owner, 31 Oct 2026: every day said "not accurate" and they could not
tell who was using the site."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from catalogue import traffic
from catalogue.models import DailyVisitor
from catalogue.testing import make_game, make_product, make_set
from catalogue.tests_traffic import add_visitors

from .tests_dashboard import NOW, TODAY, URL

HUMAN = {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone) Safari/605.1"}


class ReportTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box")

    def report(self, path, what="here", **extra):
        return self.client.get(reverse("web:note_api"), {"what": what, "path": path}, **{**HUMAN, **extra})

    def test_a_page_that_reports_in_marks_its_visitor_a_person_on_that_page(self):
        self.client.get(self.product.get_absolute_url(), **HUMAN)
        visitor = DailyVisitor.objects.get()
        self.assertFalse(visitor.browser)
        self.assertEqual(self.report(self.product.get_absolute_url()).status_code, 204)
        visitor.refresh_from_db()
        self.assertTrue(visitor.browser)
        self.assertEqual(visitor.last_page, f"product:{self.product.slug}")
        self.assertIsNotNone(visitor.last_seen)
        self.assertEqual(visitor.views, 1)   # a report is not a page view

    def test_a_report_never_makes_a_visitor_and_a_bot_never_reports(self):
        self.report("/")
        self.assertFalse(DailyVisitor.objects.exists())
        self.client.get("/", **HUMAN)
        self.report("/", HTTP_USER_AGENT="Googlebot/2.1")
        self.assertFalse(DailyVisitor.objects.get().browser)

    def test_a_page_not_counted_by_name_still_marks_a_browser_and_keeps_the_last_page(self):
        self.client.get(self.product.get_absolute_url(), **HUMAN)
        self.report(self.product.get_absolute_url())
        self.report("/alerts/confirm/abc/")
        visitor = DailyVisitor.objects.get()
        self.assertTrue(visitor.browser)
        self.assertEqual(visitor.last_page, f"product:{self.product.slug}")

    def test_a_page_shown_after_rendering_ahead_counts_and_reports_at_once(self):
        self.report(self.product.get_absolute_url(), what="view")
        visitor = DailyVisitor.objects.get()
        self.assertTrue(visitor.browser)
        self.assertEqual(visitor.views, 1)

    def test_every_page_reports_in_once_when_shown(self):
        from django.contrib.staticfiles import finders

        script = open(finders.find("js/site.js"), encoding="utf-8").read()
        self.assertIn('const what = ahead ? "view" : "here";', script)
        self.assertIn("fetch(`${note}?what=${what}&path=${encodeURIComponent(location.pathname)}`, { keepalive: true })", script)
        self.assertIn('document.addEventListener("prerenderingchange", report, { once: true });', script)


class CountingTests(TestCase):
    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def browsers(self, day, n, **kwargs):
        add_visitors(day, n, **kwargs)
        newest = DailyVisitor.objects.filter(date=day).order_by("-pk")[:n]
        DailyVisitor.objects.filter(pk__in=[v.pk for v in newest]).update(browser=True)

    def test_from_the_first_day_pages_report_in_people_are_browsers_and_no_day_is_a_burst(self):
        for back in range(3, 10):
            add_visitors(TODAY - timedelta(days=back), 40, hours=[8, 9])
        # Two days ago pages started reporting in: 30 browsers among 4,030 visitors, the rest a scanner.
        start = TODAY - timedelta(days=2)
        self.browsers(start, 30, hours=[8, 9])
        add_visitors(start, 4000, hours=[8, 9])
        self.browsers(TODAY - timedelta(days=1), 35, hours=[8, 9])
        why = traffic.explain(NOW)
        day = why["days"][start]
        self.assertEqual((day["people"], day["automated"], day["by_browser"], day["burst"]), (30, 4000, True, False))
        self.assertEqual(why["history"]["browser_since"], start)
        self.assertGreaterEqual(why["history"]["counting_since"], start)
        # Days before keep the old count.
        old = why["days"][TODAY - timedelta(days=3)]
        self.assertEqual((old["people"], old["by_browser"]), (40, False))

    def test_returning_people_are_browsers_too(self):
        self.browsers(TODAY, 3, returning=True, hours=[8])
        add_visitors(TODAY, 5, returning=True, hours=[8])
        self.assertEqual(traffic.explain(NOW)["days"][TODAY]["returning"], 3)


class DashboardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.client.force_login(get_user_model().objects.create_user("owner", password="pw", is_staff=True))
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box")

    def person(self, page, minutes_ago=1, browser=True):
        n = DailyVisitor.objects.count()
        DailyVisitor.objects.create(date=TODAY, token=f"p{n}", views=2, first_seen=NOW - timedelta(hours=1),
                                    browser=browser, last_seen=NOW - timedelta(minutes=minutes_ago), last_page=page)

    def page(self):
        return self.client.get(URL).content.decode()

    def test_on_the_site_now_counts_browsers_from_the_last_five_minutes_by_page(self):
        product = f"product:{self.product.slug}"
        for _ in range(2):
            self.person(product)
        self.person("home")
        self.person(product, minutes_ago=6)        # gone
        self.person(product, browser=False)         # never ran the page
        html = self.page()
        kpi = html[html.index('<a class="kpi" href="#now">'):html.index("</a>", html.index('href="#now"'))]
        self.assertIn("On the site now", kpi)
        self.assertIn('<span class="kpi__value">3</span>', kpi)
        self.assertIn("Opened a page in the last 5 minutes.", kpi)
        card = html[html.index('id="now"'):html.index("</section>", html.index('id="now"'))]
        self.assertIn(f'<a href="{self.product.get_absolute_url()}">Surging Sparks Booster Box</a></span><span class="dash-rows__count">2</span>', card)
        self.assertIn('<span class="dash-rows__name">Home</span><span class="dash-rows__count">1</span>', card)

    def test_nobody_on_says_so(self):
        html = self.page()
        self.assertIn("Nobody in the last 5 minutes.", html)

    def test_today_counted_by_browser_says_what_was_left_out(self):
        add_visitors(TODAY, 4, hours=[9])
        DailyVisitor.objects.filter(pk__in=list(DailyVisitor.objects.values_list("pk", flat=True)[:1])).update(browser=True)
        html = self.page()
        self.assertIn("So far, to 13:13. 3 automated visits left out.", html)
        self.assertNotIn("Today looks automated", html)

    def test_on_the_site_now_is_never_cached(self):
        self.page()
        self.person("home")
        self.assertIn('<span class="kpi__value">1</span>', self.page())
