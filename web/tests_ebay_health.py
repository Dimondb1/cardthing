"""eBay health (web/ebay_health.py): the owner's page of whether eBay is updating often enough, where it is the
cheapest, people's clicks to it and the seller rating rules. The owner, 15 Nov 2026: "an ebay health check is it
updating often enough, how many products on the site, how many ebay products are cheapest, with a link to see those
products, how many legit clicks. Maybe add a seller. Rating donfig button". Simplified 24 Nov 2026: "simplify the ebay
health menu for me and keep the log for you"."""

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

    def top(self, html=None):
        """The plain part at the top: the verdict, its reasons, the three numbers and Try eBay again now."""
        html = html or self.page()
        start = html.index('<div class="eh">')
        return html[start:html.index('<section id="test">', start)]

    def test_one_plain_verdict_and_three_numbers(self):
        from datetime import timedelta

        from django.utils import timezone

        from catalogue.models import Listing

        # An item on a product taken off the site shows nowhere, so it is never counted as hidden.
        gone = make_product(self.cheaper.product_set, name="Old Box", slug="old-box", is_active=False)
        make_listing(gone, self.ebay, price="10.00", hours_ago=30)
        top = self.top()
        # Four eBay items in stock on the site's products: three inside the four hours a price counts for here, one
        # past them, which is far more than the share allowed.
        self.assertIn('<p class="verdict behind"><b>Behind</b>1 eBay price is hidden until it is checked again.</p>',
                      top)
        self.assertIn("<li><b>3</b><span>eBay prices showing</span></li>", top)
        self.assertIn("<li><b>1</b><span>hidden until checked again</span></li>", top)
        self.assertIn("<span>last check</span>", top)
        self.assertIn("A price not checked for 4 hours is hidden until it is checked again.", top)
        Listing.objects.filter(product=self.stale).delete()
        self.assertIn('<p class="verdict ok"><b>Up to date</b>eBay prices are being checked often enough.</p>',
                      self.page())
        # A missed hourly search is behind; nothing inside the hours a price counts for is not updating.
        Listing.objects.filter(retailer=self.ebay).update(last_checked=timezone.now() - timedelta(hours=2, minutes=1))
        self.assertIn("<b>Behind</b>eBay was last checked 2\xa0hours ago; it is searched every hour.", self.page())
        Listing.objects.filter(retailer=self.ebay).update(last_checked=timezone.now() - timedelta(hours=5))
        self.assertIn('<p class="verdict stopped"><b>Not updating</b>Nothing from eBay has been checked for over 4 hours.',
                      self.page())
        Listing.objects.filter(retailer=self.ebay).delete()
        self.assertIn('<p class="verdict stopped"><b>Not updating</b>No eBay price has been checked yet.', self.page())

    def test_everything_else_is_folded_away_with_the_log_last(self):
        html = self.page()
        folds = [html.index(mark) for mark in ('<details id="cheapest">', "<summary>Clicks to eBay", '<details id="rules">',
                                               '<details id="log">')]
        self.assertEqual(folds, sorted(folds))
        self.assertLess(html.index('<section id="test">'), folds[0])
        self.assertIn("<summary>Log to copy <span>for Claude, when something looks wrong</span></summary>", html)
        # Nothing about the item lookup is left above the log.
        self.assertNotIn("lookup", html[:folds[-1]].lower())

    def test_products_and_where_ebay_is_the_cheapest(self):
        html = self.page()
        self.assertIn("<summary>Where eBay is the cheapest <span>2 products</span></summary>", html)
        self.assertIn("3 of the 5 products on the site have an eBay price that counts now.", html)
        start = html.index('<details id="cheapest">')
        part = html[start:html.index("</details>", start)]
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
            every = self.page(all="1")
            self.assertIn("Prismatic Evolutions Booster Bundle</a>", every)
            self.assertIn('<details id="cheapest" open>', every)

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

    def test_it_says_why_it_is_not_updating(self):
        """The owner, 16 Nov 2026, with no eBay price counting: "something is wrong"."""
        from datetime import timedelta

        from django.utils import timezone

        from catalogue.models import WorkerState

        # No background reader has ever run.
        self.assertIn("<li><b>The background reader has stopped, so eBay is not searched.</b> It has never started. "
                      "Running the update command restarts it.</li>", self.top())
        # Running, but Pause all is on.
        state = WorkerState.objects.create(pk=1, heartbeat_at=timezone.now(), paused=True)
        self.assertIn("<b>Pause all is on, so no shop is read and no stock is checked.</b>", self.top())
        # Running, and eBay waiting after errors, with eBay's own words.
        state.paused = False
        state.save()
        Retailer.objects.filter(pk=self.ebay.pk).update(
            error_streak=3, backoff_until=timezone.now() + timedelta(hours=2),
            last_error="Searching eBay: HTTP 403 Forbidden")
        top = self.top()
        self.assertIn("<b>eBay is waiting after 3 failed tries, until", top)
        self.assertIn("eBay&#x27;s last error: Searching eBay: HTTP 403 Forbidden", top)
        self.assertNotIn("stopped", top)
        # Paused on its own.
        Retailer.objects.filter(pk=self.ebay.pk).update(reading_paused=True)
        self.assertIn("<b>eBay is paused, so its prices are not checked.</b>", self.top())

    def test_it_says_when_ebays_hourly_search_is_kept_waiting(self):
        """The owner, 16 Nov 2026, with the reader running, eBay not waiting and nothing checked for hours: "something
        is wrong"."""
        from datetime import timedelta

        from django.utils import timezone

        from catalogue.models import WorkerState

        now = timezone.now()
        WorkerState.objects.create(pk=1, heartbeat_at=now)
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=now - timedelta(hours=2, minutes=5))
        top = self.top()
        self.assertIn("<b>eBay&#x27;s read has been due for 2\xa0hours", top)
        self.assertIn("without starting.</b> Another job is holding the lock that reads share", top)
        # Due a few minutes ago is ordinary: the reader starts it within a minute or two.
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=now - timedelta(minutes=3))
        self.assertNotIn("without starting", self.top())
        # When eBay is up to date, nothing but the verdict is said.
        from catalogue.models import Listing

        Listing.objects.filter(product=self.stale).delete()
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=now - timedelta(hours=2, minutes=5))
        top = self.top()
        self.assertIn('<p class="verdict ok">', top)
        self.assertNotIn('<ul class="reasons">', top)
        self.assertNotIn("Try eBay again now", top)

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
        self.assertIn("Verdict: Behind. 1 eBay price is hidden until it is checked again.", text)
        self.assertIn("BACKGROUND READER\nnever started\nlast plan: none noted", text)
        self.assertIn("EBAY ALLOWANCE (what eBay last said)\neBay has not been asked yet", text)
        self.assertNotIn("ITEM CHECK", text)
        self.assertIn("EBAY TEST\nnot run yet: tap Test eBay now on this page", text)
        self.assertIn("none on record", text)
        self.assertIn("The reader keeps this log from the first time it runs this version.", text)
        self.assertIn('<details id="log">', page)
        self.assertIn('<button type="button" class="button" data-copy-from="ebay-log" data-label="Copy the log" '
                      'data-done="Copied" data-failed="Selected: now copy it" hidden>Copy the log</button>', page)
        self.assertRegex(page, r'<script nonce="[^"]+" src="[^"]*js/copy-log[^"]*\.js" defer></script>')

        now = timezone.now()
        WorkerState.objects.create(
            pk=1, heartbeat_at=now, started_at=now - timedelta(hours=5), jobs_done=321, last_job="Read eBay",
            ebay_usage={"lookups": 5000, "searches": 3010, "asked_at": (now - timedelta(hours=1)).isoformat()})
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=now - timedelta(hours=2),
                                                        last_ok_at=now - timedelta(hours=20))
        ImportRun.objects.create(retailer=self.ebay, started_at=now - timedelta(hours=21),
                                 finished_at=now - timedelta(hours=20), offers_found=412, listings_updated=380)
        ImportRun.objects.create(retailer=self.ebay, started_at=now - timedelta(hours=45),
                                 finished_at=now - timedelta(hours=44), error="eBay API 500: Internal error",
                                 log="12:00:01 ERROR Import for eBay failed: eBay API 500: Internal error")
        worklog.note_plan({
            "at": (now - timedelta(seconds=40)).isoformat(), "threads": 3,
            "running": [{"label": "Read Harbour Games", "minutes": 12}],
            "queue": {"reads": 4, "reads_with_room": 1, "checks": 5, "pulses": 1, "other": 0},
            "ebay": {"running": "", "read": "due, with room to start"},
            "checks_waiting_after_a_failed_try": 2,
            "lock": {"held_since": "", "refused": "another command holds it",
                     "refused_since": (now - timedelta(minutes=50)).isoformat()},
        })
        keeper = worklog.Keeper()   # as the background reader keeps its lines
        for level, line in ((logging.INFO, "eBay: this hour's search finished in 3 minutes: 200 products searched, "
                                           "180 listings updated"),
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
        self.assertIn("jobs done since it started: 321, last job: Read eBay", text)
        self.assertIn("(0 min ago), 3 threads\n  running then: Read Harbour Games (12 min)\n  due: 4 reads (1 with room "
                      "to start), 5 shops to check, 1 pulses, 0 other\n  eBay running: no; eBay read: due, with room to "
                      "start\n  listings waiting after a failed try: 2\n  import lock: not held by the reader; "
                      "refused since ", text)
        self.assertIn("(50 min ago): another command holds it", text)
        self.assertIn(f"EBAY SHOP ROW\neBay (id {self.ebay.pk}): paused no, reads every 60 min", text)
        self.assertIn("(2 h ago), last good read: ", text)
        self.assertIn("failed tries in a row: 0, waiting until: none\nlast error: none", text)
        self.assertIn("(60 min ago): searches left 3010, resets not said", text)
        self.assertNotIn("5000", text)   # what eBay says of its item lookups is not used, so not shown
        self.assertIn("EBAY ITEMS (on the site's products)\nlive: 4, in stock or pre-order: 4, out of stock: 0\nin stock, "
                      "last checked under 1 h: 0, 1 to 2 h: 2, 2 to 4 h: 1, 4 to 8 h: 1, 8 to 24 h: 0, over 24 h: 0", text)
        self.assertIn("a price counts for 4 h; a product on eBay is searched again after 20 h (the longest unchecked "
                      "first), one visitors look at after 4 h\nprices are kept fresh by searching only: eBay's item "
                      "lookups are not used", text)
        self.assertIn(": 412 offers found, 380 listings updated\n", text)
        self.assertIn(": 0 offers found, 0 listings updated, ERROR: eBay API 500: Internal error\n    12:00:01 ERROR "
                      "Import for eBay failed", text)
        problems = text[text.index("READER LOG: WARNINGS AND ERRORS"):text.index("READER LOG: EBAY AND THE IMPORT LOCK")]
        self.assertIn("WARNING Reads have waited 50 minutes for the import lock", problems)
        self.assertNotIn("INFO eBay: this hour", problems)
        self.assertIn("INFO eBay: this hour's search finished in 3 minutes: 200 products searched, 180 listings updated",
                      text[text.index("READER LOG: EBAY AND THE IMPORT LOCK"):])

    @override_settings(RIPRAPTOR_EBAY_APP_ID="app", RIPRAPTOR_EBAY_CERT_ID="cert", RIPRAPTOR_EBAY_CAMPAIGN_ID="camp")
    def test_test_ebay_now_asks_ebay_and_shows_its_answers(self):
        """The owner, 16 Nov 2026: "Instead of guessing and hoping". One tap asks eBay each thing the site asks it and
        shows what it said, on the page and in the log to copy."""
        from unittest import mock

        from catalogue.models import Listing
        from django.utils import timezone

        from catalogue.tests_ebay_search_only import FakeEbay

        Listing.objects.filter(pk=self.cheaper_item.pk).update(url="https://www.ebay.co.uk/itm/111",
                                                               last_checked=timezone.now())
        html = self.page()
        self.assertIn('<input type="hidden" name="action" value="test"><input type="submit" value="Test eBay now">', html)
        self.assertNotIn("Asked ", html[html.index('<section id="test">'):html.index('<details id="cheapest">')])
        refused = FakeEbay([], fail='eBay API 403: {"errors":[{"errorId":1100,"message":"Access denied"}]}')
        with mock.patch.object(ebay, "http", lambda url, headers, data=None, retries=3, wait=5: refused(url, headers, data)):
            response = self.client.post(self.url, {"action": "test"})
            self.assertEqual(response["Location"], self.url + "#test")
        html = self.page()
        self.assertIn("Asked eBay. Its answers are under Test eBay now.", html)
        part = html[html.index('<section id="test">'):html.index('<details id="cheapest">')]
        self.assertIn("<li>Token: eBay issued one, so it accepts the keys.</li>", part)
        self.assertIn("<li>Search for Surging Sparks Booster Box: REFUSED. eBay&#x27;s words: eBay API 403:", part)
        self.assertIn("<li>One-item lookup: REFUSED. eBay&#x27;s words: eBay API 403:", part)
        text, _page = self.log_box()
        self.assertIn("EBAY TEST (Test eBay now, ", text)
        self.assertIn("One-item lookup: REFUSED. eBay's words: eBay API 403:", text)
        self.assertFalse(EbaySettings.objects.exists())   # the seller rules are left alone
        self.assertEqual(Listing.objects.get(pk=self.cheaper_item.pk).availability, Listing.Availability.IN_STOCK)

    def test_try_ebay_again_now_clears_its_wait(self):
        from datetime import timedelta

        from django.utils import timezone

        from catalogue.models import WorkerState

        WorkerState.objects.create(pk=1, heartbeat_at=timezone.now())
        Retailer.objects.filter(pk=self.ebay.pk).update(error_streak=4, backoff_until=timezone.now() + timedelta(hours=6))
        self.assertIn('<input type="hidden" name="action" value="try_again"><input type="submit" value="Try eBay again '
                      'now">', self.top())
        response = self.client.post(self.url, {"action": "try_again"}, follow=True)
        self.assertContains(response, "Trying eBay again: it is searched within a few minutes.")
        self.ebay.refresh_from_db()
        self.assertEqual((self.ebay.error_streak, self.ebay.backoff_until), (0, None))
        self.assertIsNotNone(self.ebay.next_read_at)
        self.assertFalse(EbaySettings.objects.exists())   # the seller rules are left alone

    def test_with_no_ebay_price_counting_it_never_says_ebay_is_simply_not_cheapest(self):
        from catalogue.models import Listing, WorkerState

        WorkerState.objects.create(pk=1, heartbeat_at=None)
        Listing.objects.filter(retailer=self.ebay).update(availability=Listing.Availability.OUT_OF_STOCK)
        html = self.page()
        self.assertIn("No eBay price counts now, so eBay cannot be the cheapest anywhere.", html)
        self.assertNotIn("eBay is not the cheapest for any product now.", html)
        # Every item checked in the last day and marked out of stock is a reason of its own.
        self.assertIn("<b>4 eBay items were checked in the last day and marked out of stock, and none is in stock.</b>",
                      self.top(html))

    def test_what_ebay_said_is_left_without_a_question_mark(self):
        from catalogue.models import WorkerState

        WorkerState.objects.create(pk=1, ebay_usage={"searches": None, "asked_at": "2026-11-16T05:56:00+00:00"})
        text, _page = self.log_box()
        allowance = text[text.index("EBAY ALLOWANCE"):text.index("EBAY ITEMS")]
        self.assertIn("searches left not said, resets not said", allowance)
        self.assertNotIn("?", allowance)

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
