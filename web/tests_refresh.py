"""The refresh dots under the logo (web/refresh.py, includes/refresh_dots.html). The owner, 6 Nov 2026: "5 dots to
accumulate under the RipRaptor logo to show when the prices are next going to be refreshed"."""

from datetime import datetime, timedelta, timezone as tz

from django.test import TestCase
from django.utils import timezone

from catalogue.models import Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import refresh

NOW = datetime(2026, 11, 6, 13, 17, 30, tzinfo=tz.utc)


class ListsTests(TestCase):
    def test_the_next_five_minute_rebuild_from_the_clock(self):
        found = refresh.for_lists(NOW)
        self.assertEqual((found.kind, found.next), ("lists", datetime(2026, 11, 6, 13, 21, tzinfo=tz.utc)))
        self.assertEqual((found.remaining, found.minutes_part, found.seconds_part, found.due), (210, 3, 30, False))
        self.assertEqual(found.dots, [True, True, False, False, False])
        # On the tick itself the next one is five minutes off; just before it, under a minute, four dots lit.
        self.assertEqual(refresh.for_lists(datetime(2026, 11, 6, 13, 21, tzinfo=tz.utc)).next,
                         datetime(2026, 11, 6, 13, 26, tzinfo=tz.utc))
        before = refresh.for_lists(datetime(2026, 11, 6, 13, 20, 50, tzinfo=tz.utc))
        self.assertEqual((before.dots, before.remaining, before.minutes_part, before.seconds_part), ([True, True, True, True, False], 10, 0, 10))


class ProductTests(TestCase):
    def setUp(self):
        self.box = make_product(make_set(make_game()), name="Delta Reign Booster Box", slug="delta-box")
        self.harbour = make_retailer("Harbour Games")
        self.north = make_retailer("Northgate Cards")
        self.listings = [make_listing(self.box, self.harbour, price="90.00"), make_listing(self.box, self.north, price="95.00")]

    def shop(self, retailer, **fields):
        Retailer.objects.filter(pk=retailer.pk).update(**fields)
        for listing in self.listings:
            listing.retailer.refresh_from_db()

    def test_the_soonest_shop_read_leads_and_the_dots_fill_over_the_hour(self):
        self.shop(self.harbour, next_read_at=NOW + timedelta(minutes=40))
        self.shop(self.north, next_read_at=NOW + timedelta(minutes=12))
        found = refresh.for_product(self.listings, NOW)
        self.assertEqual((found.kind, found.next), ("product", NOW + timedelta(minutes=12)))
        self.assertEqual((found.dots, found.minutes_part, found.seconds_part), ([True, True, True, True, False], 12, 0))
        self.assertEqual(refresh.for_product(self.listings, NOW - timedelta(minutes=40)).dots, [True, False, False, False, False])

    def test_a_shop_waiting_after_errors_a_paused_one_and_one_never_read(self):
        self.shop(self.harbour, next_read_at=NOW + timedelta(minutes=1), backoff_until=NOW + timedelta(minutes=50))
        self.shop(self.north, next_read_at=NOW + timedelta(minutes=20), reading_paused=True)
        found = refresh.for_product(self.listings, NOW)
        self.assertEqual(found.next, NOW + timedelta(minutes=50))     # the wait, not the read it holds back
        self.shop(self.harbour, next_read_at=None, backoff_until=None)
        self.assertEqual(refresh.for_product(self.listings, NOW).kind, "lists")   # nothing to count down to

    def test_a_read_that_is_due_lights_every_dot_and_says_so(self):
        self.shop(self.harbour, next_read_at=NOW - timedelta(minutes=3))
        found = refresh.for_product(self.listings, NOW)
        self.assertTrue(found.due)
        self.assertEqual(found.dots, [True] * 5)


class PageTests(TestCase):
    def test_every_page_carries_the_dots_and_a_product_page_counts_its_own_shops(self):
        box = make_product(make_set(make_game()), name="Delta Reign Booster Box", slug="delta-box")
        shop = make_retailer("Harbour Games")
        make_listing(box, shop, price="90.00")
        Retailer.objects.filter(pk=shop.pk).update(next_read_at=timezone.now() + timedelta(minutes=12))
        html = self.client.get(box.get_absolute_url()).content.decode()
        self.assertIn('<details class="pulse" data-refresh data-next="', html)
        self.assertIn('data-cycle="3600" data-note="Rechecking stock in {time}." data-now="Rechecking stock now." '
                      'data-time="{m} min {s} s" data-seconds="{s} s">', html)
        self.assertIn('<summary class="pulse__dots" aria-label="When stock is next rechecked">', html)
        self.assertEqual(html.count('<span class="pulse__dot'), 5)
        self.assertRegex(html, r'<p class="pulse__note">Rechecking stock in 1[12] min \d+ s\.</p>')
        # The time a shop's read is due: every dot lit and the line says so. Under a minute it is seconds alone.
        Retailer.objects.filter(pk=shop.pk).update(next_read_at=timezone.now() - timedelta(minutes=1))
        html = self.client.get(box.get_absolute_url()).content.decode()
        self.assertIn('<p class="pulse__note">Rechecking stock now.</p>', html)
        self.assertEqual(html.count('class="pulse__dot is-on"'), 5)
        Retailer.objects.filter(pk=shop.pk).update(next_read_at=timezone.now() + timedelta(seconds=30))
        self.assertRegex(self.client.get(box.get_absolute_url()).content.decode(), r"Rechecking stock in (29|30) s\.")
        # The home page counts to the lists' rebuild.
        home = self.client.get("/").content.decode()
        self.assertIn('data-cycle="300" data-note="Rechecking stock in {time}."', home)
        self.assertEqual(home.count('<span class="pulse__dot'), 5)
        # Under the logo, inside the brand's own box.
        self.assertIn('<div class="brand"><a class="wordmark"', home)
