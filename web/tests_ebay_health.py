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
        # Four eBay items in stock, all checked inside the twenty hours after which a product is searched again: two
        # inside two hours, one inside four, one past the four a price counts for here.
        self.assertIn("<b>4</b><span>eBay items in stock</span>", html)
        self.assertIn("<b>4</b><span>checked in the last 20 hours</span>", html)
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

    def test_it_says_when_ebays_read_waits_how_the_last_check_went_and_what_is_on_the_ended_list(self):
        """The owner, 16 Nov 2026, with the reader running, eBay not waiting and nothing checked for hours: "something
        is wrong". The page now shows what was hidden: eBay's read due for hours without starting, the last check and
        its problem, and the items in stock that could not be checked."""
        from datetime import timedelta

        from django.utils import timezone

        from catalogue.models import Listing, WorkerState

        now = timezone.now()
        WorkerState.objects.create(pk=1, heartbeat_at=now)
        Listing.objects.filter(retailer=self.ebay).update(last_checked=now - timedelta(hours=6))
        Listing.objects.filter(pk=self.cheaper_item.pk).update(url="https://www.ebay.co.uk/itm/111")
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=now - timedelta(hours=2, minutes=5))
        ebay.note_check(asked=20, in_stock=18, gone=2, now=now - timedelta(hours=5))
        ebay.note_check(problem="eBay did not answer: eBay API 503: Service Unavailable", now=now - timedelta(hours=4))
        ebay.note_ended(["v1|111|0", "v1|222|0"])
        part = self.why()
        self.assertIn("<b>eBay&#x27;s read has been due for 2\xa0hours", part)
        self.assertIn("without starting.</b> Another job is holding the lock that reads share", part)
        self.assertIn("<b>The last eBay item check did not get through, 4\xa0hours ago.</b> eBay did not answer: eBay "
                      "API 503: Service Unavailable", part)
        self.assertIn("<b>3 eBay items in stock have no item number in the link, so they cannot be checked.</b>", part)
        self.assertIn("<b>1 eBay item in stock is on the ended list.</b> eBay once said it had ended, yet a search found "
                      "it again.", part)
        self.assertIn("eBay: not waiting, next read due for 2\xa0hours", part)
        self.assertIn("Last item check: 5\xa0hours ago, 20 asked about: 18 in stock, 2 gone.", part)
        self.assertIn("Last problem, 4\xa0hours ago: eBay did not answer", part)
        self.assertIn("Ended list: 2 items eBay said had ended, not asked about again for 30 days, 1 of them in stock.",
                      part)
        # A check that works after the problem: the problem is no longer a reason, and is still shown as a fact.
        ebay.note_check(asked=1, in_stock=1, now=now)
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=now + timedelta(minutes=40))
        part = self.why()
        self.assertNotIn("did not get through", part)
        self.assertNotIn("without starting", part)
        self.assertIn("Last item check: 0\xa0minutes ago, 1 asked about: 1 in stock, 0 gone.", part)
        self.assertIn(", next read at ", part)

    def log_box(self):
        import html

        page = self.page()
        start = page.index('<textarea id="ebay-log"')
        return html.unescape(page[page.index(">", start) + 1:page.index("</textarea>", start)]), page

    def test_the_log_to_copy_holds_everything_about_ebay_as_text(self):
        """The owner, 16 Nov 2026, asked to run a command on the server and paste what it printed: "Instead of guessing
        and hoping why aren't you producing an output log I could screenshot to you or a log that would show in the
        admin area that I could copy and paste to you"."""
        import logging
        from datetime import timedelta

        from django.utils import timezone

        from catalogue import worklog
        from catalogue.models import ImportRun, WorkerState

        self.addCleanup(lambda: worklog.path().unlink(missing_ok=True))
        # Nothing has run yet: the box says so in each part rather than leaving it out.
        text, page = self.log_box()
        self.assertTrue(text.startswith("RipRaptor eBay log, "))
        self.assertIn("Verdict: Falling behind. Too many eBay items in stock are past", text)
        self.assertIn("BACKGROUND READER\nnever started\nlast plan: none noted", text)
        self.assertIn("EBAY ALLOWANCES (what eBay last said)\neBay has not been asked yet", text)
        self.assertIn("LAST ITEM CHECK\nnone noted yet\nlast problem: none noted", text)
        self.assertIn("EBAY TEST\nnot run yet: tap Test eBay now on this page", text)
        self.assertIn("none on record", text)
        self.assertIn("The reader keeps this log from the first time it runs this version.", text)
        self.assertIn('<p class="note"><a href="#log">Log to copy</a>', page)
        self.assertIn('<button type="button" class="button" data-copy-from="ebay-log" data-label="Copy the log" '
                      'data-done="Copied" data-failed="Selected: now copy it" hidden>Copy the log</button>', page)
        self.assertRegex(page, r'<script nonce="[^"]+" src="[^"]*js/copy-log[^"]*\.js" defer></script>')

        now = timezone.now()
        WorkerState.objects.create(
            pk=1, heartbeat_at=now, started_at=now - timedelta(hours=5), jobs_done=321, last_job="Check 20 at eBay",
            ebay_usage={"lookups": 5000, "searches": None, "asked_at": (now - timedelta(hours=1)).isoformat()})
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=now - timedelta(hours=2),
                                                        last_ok_at=now - timedelta(hours=20))
        ImportRun.objects.create(retailer=self.ebay, started_at=now - timedelta(hours=21),
                                 finished_at=now - timedelta(hours=20), offers_found=412, listings_updated=380)
        ImportRun.objects.create(retailer=self.ebay, started_at=now - timedelta(hours=45),
                                 finished_at=now - timedelta(hours=44), error="eBay API 500: Internal error",
                                 log="12:00:01 ERROR Import for eBay failed: eBay API 500: Internal error")
        ebay.note_check(asked=20, in_stock=18, gone=2, now=now - timedelta(minutes=30))
        worklog.note_plan({
            "at": (now - timedelta(seconds=40)).isoformat(), "threads": 3,
            "running": [{"label": "Read Harbour Games", "minutes": 12}],
            "queue": {"reads": 4, "reads_with_room": 1, "checks": 5, "pulses": 1, "other": 0},
            "ebay": {"running": "", "read": "due, with room to start", "check": "3 items (hot)",
                     "lookup_waiting": "eBay will not serve its item lookup"},
            "checks_waiting_after_a_failed_try": 2,
            "lock": {"held_since": "", "refused": "another command holds it",
                     "refused_since": (now - timedelta(minutes=50)).isoformat()},
        })
        keeper = worklog.Keeper()   # as the background reader keeps its lines
        for level, line in ((logging.INFO, "eBay: checked 20 items: 18 in stock, 2 gone, 0 not reached"),
                            (logging.WARNING, "Reads have waited 50 minutes for the import lock: another command holds "
                                              "it. Next in line: Read eBay")):
            keeper.emit(logging.makeLogRecord({"name": "catalogue.worker", "levelno": level,
                                               "levelname": logging.getLevelName(level), "msg": line}))
        with override_settings(RIPRAPTOR_EBAY_APP_ID="app-secret-1", RIPRAPTOR_EBAY_CERT_ID="cert-secret-2",
                               RIPRAPTOR_EBAY_CAMPAIGN_ID="5339000001"):
            text, page = self.log_box()
        for secret in ("app-secret-1", "cert-secret-2", "5339000001"):
            self.assertNotIn(secret, page)
        self.assertIn("Reason: eBay's read has been due for 2\xa0hours without starting.", text)
        self.assertIn("BACKGROUND READER\nrunning: yes, last sign of life ", text)
        self.assertIn("jobs done since it started: 321, last job: Check 20 at eBay", text)
        self.assertIn("(0 min ago), 3 threads\n  running then: Read Harbour Games (12 min)\n  due: 4 reads (1 with room "
                      "to start), 5 shops to check, 1 pulses, 0 other\n  eBay running: no; eBay read: due, with room to "
                      "start; eBay check planned: 3 items (hot)\n  eBay item lookups: WAITING. eBay will not serve its "
                      "item lookup\n  listings waiting after a failed try: 2\n  import lock: not held by the reader; "
                      "refused since ", text)
        self.assertIn("(50 min ago): another command holds it", text)
        self.assertIn(f"EBAY SHOP ROW\neBay (id {self.ebay.pk}): paused no, reads every 60 min", text)
        self.assertIn("(2 h ago), last good read: ", text)
        self.assertIn("failed tries in a row: 0, waiting until: none\nlast error: none", text)
        self.assertIn("(60 min ago): searches left not said, item lookups left 5000, resets not said", text)
        self.assertIn("EBAY ITEMS\nlive: 4, in stock or pre-order: 4, out of stock: 0\nin stock, last checked under 1 h: 0, "
                      "1 to 2 h: 2, 2 to 4 h: 1, 4 to 8 h: 1, 8 to 24 h: 0, over 24 h: 0", text)
        self.assertIn("in stock with no item number: 4, in stock on the ended list: 0, ended list: 0 items", text)
        self.assertIn("a price counts for 4 h; a product on eBay is searched again after 20 h, one visitors look at after "
                      "4 h", text)
        self.assertIn("(30 min ago): 20 asked about, 18 in stock, 2 gone, 0 not reached", text)
        self.assertIn(": 412 offers found, 380 listings updated\n", text)
        self.assertIn(": 0 offers found, 0 listings updated, ERROR: eBay API 500: Internal error\n    12:00:01 ERROR "
                      "Import for eBay failed", text)
        problems = text[text.index("READER LOG: WARNINGS AND ERRORS"):text.index("READER LOG: EBAY AND THE IMPORT LOCK")]
        self.assertIn("WARNING Reads have waited 50 minutes for the import lock", problems)
        self.assertNotIn("INFO eBay: checked", problems)
        self.assertIn("INFO eBay: checked 20 items: 18 in stock, 2 gone, 0 not reached",
                      text[text.index("READER LOG: EBAY AND THE IMPORT LOCK"):])

    @override_settings(RIPRAPTOR_EBAY_APP_ID="app", RIPRAPTOR_EBAY_CERT_ID="cert", RIPRAPTOR_EBAY_CAMPAIGN_ID="camp")
    def test_test_ebay_now_asks_ebay_and_shows_its_answers(self):
        """The owner, 16 Nov 2026: "Instead of guessing and hoping". One tap asks eBay each thing the site asks it and
        shows what it said, on the page and in the log to copy."""
        from unittest import mock

        from catalogue.models import Listing
        from catalogue.tests_ebay_checks import FakeEbay

        Listing.objects.filter(pk=self.cheaper_item.pk).update(url="https://www.ebay.co.uk/itm/111")
        html = self.page()
        self.assertIn('<input type="hidden" name="action" value="test"><input type="submit" value="Test eBay now">', html)
        self.assertNotIn("Asked ", html[html.index('<section id="test">'):html.index('<section id="why">')])
        refused = FakeEbay([], fail='eBay API 403: {"errors":[{"errorId":1100,"message":"Access denied"}]}')
        with mock.patch.object(ebay, "http", lambda url, headers, data=None, retries=3, wait=5: refused(url, headers, data)):
            response = self.client.post(self.url, {"action": "test"})
            self.assertEqual(response["Location"], self.url + "#test")
        html = self.page()
        self.assertIn("Asked eBay. Its answers are under Test eBay now, and in the log to copy.", html)
        part = html[html.index('<section id="test">'):html.index('<section id="why">')]
        self.assertIn("<li>Token: eBay issued one, so it accepts the keys.</li>", part)
        self.assertIn("<li>Item lookup, 1 listing at once (what the checks use): REFUSED. eBay&#x27;s words: eBay API "
                      "403:", part)
        text, _page = self.log_box()
        self.assertIn("EBAY TEST (Test eBay now, ", text)
        self.assertIn("Item lookup, 1 listing at once (what the checks use): REFUSED. eBay's words: eBay API 403:", text)
        self.assertFalse(EbaySettings.objects.exists())   # the seller rules are left alone
        self.assertEqual(Listing.objects.get(pk=self.cheaper_item.pk).availability, Listing.Availability.IN_STOCK)

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
        self.assertNotIn("?", html[html.index("eBay's hourly searches"):html.index("More on")])

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
        self.assertContains(self.client.get(reverse("admin:index")), f'<a href="{self.url}" class="section">eBay health</a>')
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
