"""Shop clicks timeline (web/click_timeline.py): every click through to a shop as a dot in its shop's lane along the
time, and listed with the product and where it came from. The owner, 18 Nov 2026: "I would like to be able to see the
click throughs to shops in a visual that illustrates the shop the product and the time on a timeline"."""

from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import OutboundClick
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import click_timeline

URL = "/admin/clicks/"
# A fixed now, mid afternoon UK time, so today's window and its ticks are known.
NOW = timezone.make_aware(datetime(2026, 11, 18, 15, 0))


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        game = make_game()
        cls.box = make_product(make_set(game), name="Surging Sparks Booster Box", slug="box", product_type="booster_box")
        cls.etb = make_product(make_set(game, name="Destined Rivals", slug="rivals", code="DRI"),
                               name="Destined Rivals Elite Trainer Box", slug="etb")
        cls.harbour, cls.kestrel = make_retailer("Harbour Games"), make_retailer("Kestrel Cards")
        make_listing(cls.box, cls.harbour, price="87.00")
        make_listing(cls.etb, cls.kestrel, price="49.99")

    def setUp(self):
        user = get_user_model().objects.create_user("owner", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(user)
        patcher = mock.patch("django.utils.timezone.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def click(self, product, shop, hours_ago, **fields):
        fields.setdefault("source", "product")
        fields.setdefault("button", OutboundClick.Button.BUY_NOW)
        return OutboundClick.objects.create(product=product, retailer=shop, created_at=NOW - timedelta(hours=hours_ago),
                                            **fields)

    def page(self, **params):
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200)
        return response


class AccessTests(Base):
    def test_staff_only_never_cached_or_indexed_and_linked_from_the_admin_home(self):
        self.assertEqual(reverse("click_timeline"), URL)
        response = self.page()
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(f'href="{URL}"', self.client.get(reverse("admin:index")).content.decode())
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)


class TimelineTests(Base):
    def test_one_lane_a_shop_the_busiest_first_with_a_dot_a_click_placed_by_its_time(self):
        early = self.click(self.box, self.harbour, hours_ago=12)      # 03:00, a fifth of the way through today
        self.click(self.box, self.harbour, hours_ago=1)
        self.click(self.etb, self.kestrel, hours_ago=2)
        found = self.page().context
        self.assertEqual([lane["shop"].name for lane in found["lanes"]], ["Harbour Games", "Kestrel Cards"])
        self.assertEqual([lane["count"] for lane in found["lanes"]], [2, 1])
        dots = {click.pk: left for click, left in found["lanes"][0]["dots"]}
        self.assertAlmostEqual(dots[early.pk], 100 * 3 / 15, places=3)
        html = found and self.page().content.decode()
        # Each dot is a link to its click in the list, so a tap shows the product with no script.
        self.assertIn(f'href="#click-{early.pk}" title="Wed 18 Nov, 03:00: Surging Sparks Booster Box"', html)
        self.assertIn(f'<li class="click" id="click-{early.pk}">', html)
        self.assertIn("3 clicks to 2 shops, on 2 products.", html)

    def test_only_peoples_clicks_never_direct_hits_or_scripts(self):
        self.click(self.box, self.harbour, hours_ago=1)
        self.click(self.box, self.harbour, hours_ago=1, suspect=True)
        self.click(self.box, self.harbour, hours_ago=1, automated=True)
        self.assertEqual(self.page().context["count"], 1)

    def test_today_starts_at_midnight_and_the_longer_periods_reach_back(self):
        self.click(self.box, self.harbour, hours_ago=1)
        self.click(self.box, self.harbour, hours_ago=20)        # yesterday
        self.click(self.box, self.harbour, hours_ago=24 * 10)   # ten days ago
        self.assertEqual(self.page().context["count"], 1)
        self.assertEqual(self.page(period="7").context["count"], 2)
        self.assertEqual(self.page(period="28").context["count"], 3)
        self.assertEqual(self.page(period="nonsense").context["period"], "today")
        # Today's axis every three hours up to now, none so near the end it runs into "Now"; seven days by day.
        self.assertEqual([label for label, _left in self.page().context["ticks"]], ["00", "03", "06", "09", "12"])
        self.assertEqual(len(self.page(period="7").context["ticks"]), 6)

    def test_the_list_says_the_time_product_shop_and_where_it_came_from_newest_first(self):
        self.click(self.etb, self.kestrel, hours_ago=3, source="deals", button=OutboundClick.Button.DEAL)
        self.click(self.box, self.harbour, hours_ago=1, via_rip=True, source="product", button="")
        html = self.page().content.decode()
        first, second = html.index("Surging Sparks Booster Box</a>"), html.index("Destined Rivals Elite Trainer Box</a>")
        self.assertLess(first, second)
        self.assertIn("Wednesday 18 November", html)
        self.assertIn("Product page &middot; after Rip helped", html)
        self.assertIn("Buy on a Deals page saving", html)
        self.assertIn(">14:00</time>", html)
        self.assertIn(f'href="?period=today&amp;shop={self.kestrel.pk}">Kestrel Cards</a>', html)

    def test_a_shops_name_shows_only_its_clicks(self):
        self.click(self.box, self.harbour, hours_ago=1)
        self.click(self.etb, self.kestrel, hours_ago=2)
        found = self.page(shop=self.kestrel.pk).context
        self.assertEqual((found["count"], found["shop_name"]), (1, "Kestrel Cards"))
        self.assertIn('<a href="?period=today">Show every shop</a>', self.page(shop=self.kestrel.pk).content.decode())

    def test_an_empty_day_says_so(self):
        self.assertIn("No clicks to shops yet today.", self.page().content.decode())

    def test_one_query_for_the_clicks_however_many(self):
        for hours in range(10):
            self.click(self.box if hours % 2 else self.etb, self.harbour if hours % 3 else self.kestrel, hours_ago=hours)
        with CaptureQueriesContext(connection) as queries:
            click_timeline.clicks_between(NOW - timedelta(days=1), NOW)
        self.assertEqual(len(queries), 1)
        with CaptureQueriesContext(connection) as queries:
            self.page()
        clicks = [query for query in queries if "catalogue_outboundclick" in query["sql"]]
        self.assertEqual(len(clicks), 1)
