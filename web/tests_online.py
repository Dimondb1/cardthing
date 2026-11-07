"""Who is online (web/online.py, catalogue/online.py), the click check that a browser ran one of our pages
(catalogue/clicks.py), and the dashboard built by the background reader (web/dashboard.py warm). The owner, 7 Nov
2026: "look at why it takes so long to open the dashboard. Also, in admin... current users, so I can see the IP
address and the location and how many people are online at any given point"."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue import clicks, online, worker
from catalogue.models import DailyVisitor, LiveVisitor, OnlineCount, OutboundClick
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import dashboard

PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class SiteCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.product = make_product(make_set(make_game()), name="Delta Reign Elite Trainer Box", slug="delta-etb")
        self.shop = make_retailer("Harbour Games")
        self.listing = make_listing(self.product, self.shop)

    def open_page(self, agent=PHONE, address="203.0.113.9", report=True):
        """A browser opening the product page, and its script reporting in unless ``report`` is False."""
        self.client.get(self.product.get_absolute_url(), HTTP_USER_AGENT=agent, REMOTE_ADDR=address)
        if report:
            return self.client.get(reverse("web:note_api"), {"what": "here", "path": self.product.get_absolute_url()},
                                   HTTP_USER_AGENT=agent, REMOTE_ADDR=address)
        return None


class ClickCheckTests(SiteCase):
    def go(self, agent=PHONE, address="203.0.113.9"):
        return self.client.get(self.listing.get_outbound_url(), HTTP_USER_AGENT=agent, REMOTE_ADDR=address,
                               HTTP_REFERER="http://testserver/products/delta-etb/")

    def test_a_click_from_a_browser_that_never_ran_our_page_is_a_direct_hit(self):
        # A script fetches our page and follows its link with our page as the Referer, never running the page.
        self.open_page(report=False)
        self.go()
        click = OutboundClick.objects.get()
        self.assertEqual((click.suspect, click.suspect_why, click.ran_page), (True, "page", None))
        self.assertEqual(OutboundClick.objects.people().count(), 0)

    def test_the_pages_report_sets_a_cookie_for_a_day_and_its_clicks_count(self):
        response = self.open_page()
        cookie = response.cookies[clicks.PAGE_COOKIE]
        self.assertEqual((cookie.value, cookie["max-age"], cookie["httponly"]), ("1", 86400, True))
        self.go(address="198.51.100.4")   # the same browser on another connection: its cookie still says so
        click = OutboundClick.objects.get()
        self.assertEqual((click.suspect, click.suspect_why, click.ran_page), (False, "", True))

    def test_a_browser_without_cookies_is_known_by_its_visitor_row(self):
        self.open_page()
        self.client.cookies.clear()
        self.go()
        self.assertEqual(OutboundClick.objects.people().count(), 1)


class LiveTests(SiteCase):
    def test_a_page_view_notes_the_visitor_with_their_address_and_the_report_makes_them_a_browser(self):
        self.open_page(report=False)
        row = LiveVisitor.objects.get()
        self.assertEqual((row.ip, row.page, row.path, row.browser, row.device),
                         ("203.0.113.9", "product:delta-etb", "/products/delta-etb/", False, "mobile"))
        self.client.get(reverse("web:note_api"), {"what": "here", "path": "/products/delta-etb/?utm_source=x"},
                        HTTP_USER_AGENT=PHONE, REMOTE_ADDR="203.0.113.9")
        row.refresh_from_db()
        self.assertTrue(row.browser)
        self.assertEqual(row.path, "/products/delta-etb/")   # never the query
        # The next page keeps them a browser and moves them on.
        self.client.get(reverse("web:home"), HTTP_USER_AGENT=PHONE, REMOTE_ADDR="203.0.113.9")
        row.refresh_from_db()
        self.assertEqual((row.page, row.browser), ("home", True))
        # The day's counts never hold the address.
        self.assertNotIn("ip", [field.name for field in DailyVisitor._meta.get_fields()])

    def test_the_address_is_forgotten_half_an_hour_after_the_last_page(self):
        self.open_page()
        self.open_page(agent=DESKTOP, address="192.0.2.7")
        LiveVisitor.objects.filter(ip="192.0.2.7").update(last_seen=timezone.now() - timedelta(minutes=online.LIVE_KEEP + 1))
        self.assertEqual(online.forget(), 1)
        self.assertEqual(list(LiveVisitor.objects.values_list("ip", flat=True)), ["203.0.113.9"])

    def test_counts_are_taken_once_every_five_minutes(self):
        self.open_page()
        self.open_page(agent=DESKTOP, address="192.0.2.7", report=False)
        now = timezone.now()
        first = online.sample(now)
        self.assertEqual((first.people, first.automated), (1, 1))
        self.assertIsNone(online.sample(now + timedelta(seconds=10)))
        self.assertEqual(OnlineCount.objects.count(), 1 if (now + timedelta(seconds=10)).minute // 5 == now.minute // 5 else 2)


class PageTests(SiteCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user("owner", password="pw", is_staff=True)

    def test_owner_sees_each_visitor_with_address_country_and_whether_a_person(self):
        self.open_page()
        self.open_page(agent=DESKTOP, address="192.0.2.7", report=False)
        flood = self.open_page(agent=PHONE.replace("18_0", "17_0"), address="198.51.100.20")
        self.assertIsNotNone(flood)
        token = LiveVisitor.objects.get(ip="198.51.100.20").token
        DailyVisitor.objects.filter(token=token).update(automated=True, automated_why="flood")
        OnlineCount.objects.create(at=timezone.now() - timedelta(hours=2), people=48, automated=3)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("online"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<p class="big" id="online-count">1 person</p>', html)
        self.assertIn("And 2 automated visits", html)
        for address in ("203.0.113.9", "192.0.2.7", "198.51.100.20"):
            self.assertIn(f'<td class="first">{address}</td>', html)
        self.assertIn("<title>Who is online | RipRaptor", html)
        self.assertIn('<span class="ok">Person</span>', html)
        self.assertIn("Fetched the page without running it", html)
        self.assertIn("One of a flood from abroad", html)
        self.assertIn("Chrome on Windows", html)
        self.assertIn("Safari on iPhone", html)
        self.assertIn('<a href="/products/delta-etb/">/products/delta-etb/</a>', html)
        self.assertIn("Most at once: <strong>48</strong>", html)
        self.assertIn('<meta http-equiv="refresh" content="30">', html)
        self.assertIn("for 30 minutes after their last page, then deleted", html)
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_only_the_owners_staff_see_it(self):
        url = reverse("online")
        self.assertEqual(self.client.get(url).status_code, 302)
        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        from guides.apps import EDITORS

        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_and_the_dashboard_link_to_it_and_the_terms_say_how_long_an_address_is_kept(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("admin:index")), f'<a href="{reverse("online")}" class="section">Who is online</a>', html=False)
        self.assertContains(self.client.get(reverse("web:dashboard")), f'<a href="{reverse("online")}">See who is online</a>')
        terms = self.client.get(reverse("web:terms")).content.decode()
        self.assertIn("for 30 minutes after the last page you open, we also keep your internet address", terms)


class DashboardWarmTests(SiteCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user("owner", password="pw", is_staff=True)
        self.client.force_login(self.owner)

    def test_the_page_serves_its_copy_and_the_reader_builds_it_again_while_it_is_in_use(self):
        now = timezone.now()
        self.client.get(reverse("web:dashboard"))
        built = cache.get(dashboard.SECTION_KEY.format(name="traffic"))["built_at"]
        later = now + timedelta(minutes=3)
        with mock.patch("django.utils.timezone.now", return_value=later):
            # Three minutes on: the page still serves the copy at once rather than building it while the owner waits.
            self.client.get(reverse("web:dashboard"))
            self.assertEqual(cache.get(dashboard.SECTION_KEY.format(name="traffic"))["built_at"], built)
            # The reader builds every part over a minute old, as the dashboard was opened lately.
            self.assertEqual(dashboard.warm(later), [name for name, _build in dashboard.SECTIONS])
            self.assertEqual(dashboard.warm(later), [])
        # Nobody looking for half an hour: the reader still keeps every part under five minutes old, so the next
        # open never waits either, and leaves a fresher one alone.
        cache.delete(dashboard.WATCHED_KEY)
        self.assertEqual(dashboard.warm(later + timedelta(minutes=2)), [])
        self.assertEqual(dashboard.warm(later + timedelta(seconds=dashboard.SECTION_IDLE_SECONDS)),
                         [name for name, _build in dashboard.SECTIONS])
        later += timedelta(seconds=dashboard.SECTION_IDLE_SECONDS)
        # The reader stopped: a copy over SECTION_STALE old is built by the page itself.
        stale = later + timedelta(seconds=dashboard.SECTION_STALE + 1)
        with mock.patch("django.utils.timezone.now", return_value=stale):
            self.client.get(reverse("web:dashboard"))
        self.assertEqual(cache.get(dashboard.SECTION_KEY.format(name="traffic"))["built_at"], stale)

    def test_the_reader_runs_it_and_the_visit_tidy_on_its_turn(self):
        reader = worker.Worker(clock=timezone.now, sleep=lambda seconds: None, warm=True)
        now = timezone.now()
        with mock.patch.object(dashboard, "warm") as warm, \
                mock.patch("catalogue.automated.classify") as classify, mock.patch("catalogue.online.sample") as sample:
            reader.keep_dashboard_warm(now)
            reader.keep_dashboard_warm(now + timedelta(seconds=5))
            reader.keep_visits_tidy(now)
            reader.keep_visits_tidy(now + timedelta(minutes=1))
            reader.keep_visits_tidy(now + worker.VISITS_EVERY)
        self.assertEqual(warm.call_count, 1)
        self.assertEqual((classify.call_count, sample.call_count), (2, 2))
        classify.assert_called_with(days=1)
        # A reader without warm (the tests' own) leaves both alone.
        with mock.patch.object(dashboard, "warm") as warm:
            worker.Worker(clock=timezone.now, sleep=lambda seconds: None).keep_dashboard_warm(now)
        warm.assert_not_called()
