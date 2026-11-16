"""eBay health (web/ebay_health.py): the owner's page of whether eBay is updating often enough, where it is the
cheapest, people's clicks to it and the seller rating rules. The owner, 15 Nov 2026: "an ebay health check is it
updating often enough, how many products on the site, how many ebay products are cheapest, with a link to see those
products, how many legit clicks. Maybe add a seller. Rating donfig button"."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue import ebay
from catalogue.importers import Offer
from catalogue.models import EbaySettings, OutboundClick, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from catalogue.tests_ebay import item


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False, RIPRAPTOR_EBAY_STALE_AFTER_HOURS=4)
class EbayHealthTests(TestCase):
    def setUp(self):
        cache.clear()
        product_set = make_set(make_game())
        self.shop = make_retailer("Harbour Games")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)

        def product(name):
            return make_product(product_set, name=name, slug=name.lower().replace(" ", "-"))

        self.cheaper = product("Surging Sparks Booster Box")    # eBay £5 under the shop
        make_listing(self.cheaper, self.shop, price="150.00")
        self.cheaper_item = make_listing(self.cheaper, self.ebay, price="145.00", seller_name="cardking",
                                         seller_feedback=Decimal("97.5"), seller_ratings=812)
        self.tie = product("Stellar Crown Booster Box")         # the same price: the shop's
        make_listing(self.tie, self.shop, price="120.00")
        make_listing(self.tie, self.ebay, price="120.00")
        self.only = product("Prismatic Evolutions Booster Bundle")   # only eBay has it, checked three hours ago
        make_listing(self.only, self.ebay, price="38.00", hours_ago=3)
        self.stale = product("Twilight Masquerade Booster Box")      # eBay's price past four hours: does not count
        make_listing(self.stale, self.ebay, price="20.00", hours_ago=6)
        product("Paldea Evolved Elite Trainer Box")                  # no eBay price at all
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        self.url = reverse("ebay_health")

    def page(self, **query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        return response.content.decode()

    def test_whether_it_is_updating_often_enough(self):
        html = self.page()
        # Four eBay items in stock: two checked inside two hours, one inside four, one past it.
        self.assertIn("<b>4</b><span>eBay items in stock</span>", html)
        self.assertIn("<b>2</b><span>checked in the last 2 hours</span>", html)
        self.assertIn("<b>1</b><span>past 4 hours, not shown as buyable</span>", html)
        # One in four past it is far more than the share allowed.
        self.assertIn('<p class="verdict behind"><b>Falling behind</b>Too many eBay items in stock are past', html)
        from catalogue.models import Listing

        from datetime import timedelta

        from django.utils import timezone

        Listing.objects.filter(product=self.stale).delete()
        self.assertIn("The latest eBay check was a while ago.", self.page())   # an hour ago
        Listing.objects.filter(product=self.tie, retailer=self.ebay).update(
            last_checked=timezone.now() - timedelta(minutes=10))
        self.assertIn('<p class="verdict ok"><b>Up to date</b>', self.page())
        Listing.objects.filter(retailer=self.ebay).delete()
        self.assertIn('<p class="verdict stopped"><b>Not updating</b>No eBay item has been checked yet.', self.page())

    def test_products_and_where_ebay_is_the_cheapest(self):
        html = self.page()
        self.assertIn("<b>5</b><span>products on the site</span>", html)
        self.assertIn("<b>3</b><span>with an eBay price that counts now</span>", html)
        self.assertIn("<b>2</b><span>where eBay is the cheapest</span>", html)
        start = html.index('<section id="cheapest">')
        part = html[start:html.index("</section>", start)]
        # The biggest saving first, then a product only eBay has; never a tie or a price that does not count.
        self.assertLess(part.index("Surging Sparks Booster Box"), part.index("Prismatic Evolutions Booster Bundle"))
        self.assertIn(f'<a href="{self.cheaper.get_absolute_url()}">Surging Sparks Booster Box</a>', part)
        self.assertIn('<td data-label="Saving">£5.00</td>', part)
        self.assertIn('<td data-label="Best shop">No UK shop</td>', part)
        self.assertIn("cardking<span class=\"muted\">97.5% positive, 812 ratings</span>", part)
        self.assertNotIn("Stellar Crown", part)
        self.assertNotIn("Twilight Masquerade", part)

    def test_see_all_of_them(self):
        from unittest import mock

        from web import ebay_health

        with mock.patch.object(ebay_health, "SHOWN", 1):
            html = self.page()
            self.assertIn('<a href="?all=1#cheapest">See all 2</a>', html)
            self.assertNotIn("Prismatic Evolutions Booster Bundle</a>", html)
            self.assertIn("Prismatic Evolutions Booster Bundle</a>", self.page(all="1"))

    def test_peoples_clicks_to_ebay(self):
        listing = self.cheaper_item
        for _ in range(3):
            OutboundClick.objects.create(product=self.cheaper, retailer=self.ebay, listing=listing)
        OutboundClick.objects.create(product=self.cheaper, retailer=self.ebay, button=OutboundClick.Button.EBAY)
        OutboundClick.objects.create(product=self.cheaper, retailer=self.ebay, suspect=True)    # a direct hit
        OutboundClick.objects.create(product=self.cheaper, retailer=self.ebay, automated=True)  # a script's
        OutboundClick.objects.create(product=self.cheaper, retailer=self.shop)
        html = self.page()
        self.assertIn("<b>4</b><span>today</span>", html)
        self.assertIn("<b>4</b><span>last 30 days, 3 to an item and 1 to eBay's search</span>", html)
        self.assertIn("<b>5</b><span>people's clicks to every shop, last 30 days</span>", html)
        self.assertIn("1 direct hits to eBay were turned away in the last 30 days.", html)
        self.assertIn(f"?retailer__id__exact={self.ebay.pk}&amp;suspect__exact=0", html)

    def test_the_seller_rules_are_set_here_and_used_for_every_offer(self):
        html = self.page()
        self.assertIn('name="min_feedback" min="50.0" max="100.0" step="0.1" inputmode="decimal" value="95.0"', html)
        self.assertIn('name="min_ratings" min="0" max="100000" step="1" inputmode="numeric" value="50"', html)
        self.assertIn("Every eBay price showing now meets these.", html)
        self.assertIsNotNone(ebay.item_offer(item("Surging Sparks Booster Box", feedback="96.0"), self.cheaper, Offer))
        response = self.client.post(self.url, {"min_feedback": "98", "min_ratings": "1000"}, follow=True)
        self.assertContains(response, "Saved: a seller needs 98.0% positive feedback and 1,000 ratings.")
        self.assertEqual((EbaySettings.objects.get().min_feedback, EbaySettings.objects.get().min_ratings),
                         (Decimal("98.0"), 1000))
        self.assertEqual(ebay.seller_rules(), (Decimal("98.0"), 1000))
        self.assertIsNone(ebay.item_offer(item("Surging Sparks Booster Box", feedback="96.0"), self.cheaper, Offer))
        # The cardking item (97.5%, 812 ratings) is below them now, until its next check.
        self.assertContains(response, "1 eBay price showing now is from sellers below these and drops at its next "
                                      "check.")

    def test_rules_out_of_range_save_nothing(self):
        for bad in ({"min_feedback": "120", "min_ratings": "10"}, {"min_feedback": "lots", "min_ratings": "10"},
                    {"min_feedback": "96", "min_ratings": "-1"}):
            with self.subTest(bad=bad):
                response = self.client.post(self.url, bad, follow=True)
                self.assertContains(response, "Nothing was saved.")
        self.assertFalse(EbaySettings.objects.exists())

    def why(self):
        html = self.page()
        start = html.index('<section id="why">')
        return html[start:html.index("</section>", start)]

    def test_it_says_why_it_is_not_updating(self):
        """The owner, 16 Nov 2026, with no eBay price counting and all 5,000 lookups unused: "something is wrong"."""
        from datetime import timedelta

        from django.utils import timezone

        from catalogue.models import WorkerState

        # No background reader has ever run.
        part = self.why()
        self.assertIn("<b>The background reader has stopped, so nothing checks eBay between the hourly reads.</b> "
                      "It has never started. Running the update command restarts it.", part)
        # Running, but Pause all is on.
        state = WorkerState.objects.create(pk=1, heartbeat_at=timezone.now(), paused=True)
        self.assertIn("<b>Pause all is on, so no shop is read and no stock is checked.</b>", self.why())
        # Running, and eBay waiting after errors, with eBay's own words.
        state.paused = False
        state.save()
        Retailer.objects.filter(pk=self.ebay.pk).update(
            error_streak=3, backoff_until=timezone.now() + timedelta(hours=2),
            last_error="Checking eBay listings: HTTP 403 Forbidden")
        part = self.why()
        self.assertIn("<b>eBay is waiting after 3 failed tries, until", part)
        self.assertIn("eBay&#x27;s last error: Checking eBay listings: HTTP 403 Forbidden", part)
        self.assertIn("Background reader: running", part)
        # Paused on its own.
        Retailer.objects.filter(pk=self.ebay.pk).update(reading_paused=True)
        self.assertIn("<b>eBay is paused, so its items are not checked.</b>", self.why())

    def test_try_ebay_again_now_clears_its_wait(self):
        from datetime import timedelta

        from django.utils import timezone

        from catalogue.models import WorkerState

        WorkerState.objects.create(pk=1, heartbeat_at=timezone.now())
        Retailer.objects.filter(pk=self.ebay.pk).update(error_streak=4, backoff_until=timezone.now() + timedelta(hours=6))
        self.assertIn('<input type="hidden" name="action" value="try_again"><input type="submit" value="Try eBay again '
                      'now">', self.why())
        response = self.client.post(self.url, {"action": "try_again"}, follow=True)
        self.assertContains(response, "Trying eBay again: its items are checked within a few minutes.")
        self.ebay.refresh_from_db()
        self.assertEqual((self.ebay.error_streak, self.ebay.backoff_until), (0, None))
        self.assertIsNotNone(self.ebay.next_read_at)
        self.assertFalse(EbaySettings.objects.exists())   # the seller rules are left alone

    def test_with_no_ebay_price_counting_it_never_says_ebay_is_simply_not_cheapest(self):
        from catalogue.models import Listing, WorkerState

        WorkerState.objects.create(pk=1, heartbeat_at=None)
        Listing.objects.filter(retailer=self.ebay).update(availability=Listing.Availability.OUT_OF_STOCK)
        html = self.page()
        self.assertIn('No eBay price counts now, so eBay cannot be the cheapest anywhere. See <a href="#why">why it is '
                      "not updating</a>.", html)
        self.assertNotIn("eBay is not the cheapest for any product now.", html)
        # Every item checked in the last day and marked out of stock is a reason of its own.
        self.assertIn("eBay items were checked in the last day and marked out of stock, and none is in stock.", html)

    def test_what_ebay_said_is_left_without_a_question_mark(self):
        from catalogue.models import WorkerState

        WorkerState.objects.create(pk=1, ebay_usage={"lookups": 5000, "searches": None,
                                                     "asked_at": "2026-11-16T05:56:00+00:00"})
        html = self.page()
        self.assertIn("eBay said 5,000 item lookups were left today", html)
        self.assertNotIn("?", html[html.index("The daily eBay search"):html.index("More on")])

    def test_the_seller_rules_never_stop_the_checks(self):
        from unittest import mock

        from django.db import DatabaseError

        with mock.patch.object(EbaySettings, "current", side_effect=DatabaseError("no such table")):
            self.assertEqual(ebay.seller_rules(), (Decimal("95.0"), 50))
            self.assertIsNotNone(ebay.item_offer(item("Surging Sparks Booster Box"), self.cheaper, Offer))
        self.assertIsNone(cache.get(ebay.RULES_KEY))   # the defaults were kept nowhere

    def test_a_few_queries_and_staff_only(self):
        with CaptureQueriesContext(connection) as queries:
            self.page()
        self.assertLess(len(queries), 25)
        self.assertContains(self.client.get(reverse("admin:index")), f'<a href="{self.url}">')
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
