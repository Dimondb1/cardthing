"""eBay's listings checked between the daily runs (ebay.check_listings, worker.Worker.check_ebay): the products that
matter every ten minutes or every hour, like any shop's, through eBay's bulk item lookup. The owner, 6 Nov 2026, of
a price "checked at eBay 20 hours ago": "I want users to know they are getting the latest prices. 20 hours ago
isn't good enough. If the eBay API times out then we need another way."."""

import time
import urllib.parse
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone

from . import ebay, worker
from .models import DailyPageView, Listing, Product, Retailer
from .tests_worker import WorkerTestCase

KEYS = override_settings(RIPRAPTOR_EBAY_APP_ID="app", RIPRAPTOR_EBAY_CERT_ID="cert", RIPRAPTOR_EBAY_CAMPAIGN_ID="camp")


def item(number, price="85.89", postage="3.89", feedback="99.6", ratings=1200, title="Surging Sparks Booster Box"):
    return {
        "itemId": f"v1|{number}|0", "title": title, "price": {"value": price, "currency": "GBP"},
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{number}", "seller": {"feedbackPercentage": feedback, "feedbackScore": ratings},
        "shippingOptions": [{"shippingCost": {"value": postage}}], "image": {},
    }


def believed():
    """The lookup has shown an item and priced one in this process, as after any check that worked."""
    ebay.lookup_showed()
    ebay.lookup_read()


class FakeEbay:
    """eBay's API as the checks use it: a token, the allowances, and the bulk lookup answered from ``items``."""

    def __init__(self, items=(), bulk_left=5000, fail=None):
        self.items = {row["itemId"]: row for row in items}
        self.bulk_left = bulk_left
        self.fail = fail
        self.calls = []

    def __call__(self, url, headers, data=None):
        self.calls.append(url)
        if url == ebay.TOKEN_URL:
            return {"access_token": "tok", "expires_in": 7200}
        if url.startswith(ebay.LIMITS_URL):
            return {"rateLimits": [{"resources": [
                {"name": "buy.browse", "rates": [{"remaining": 4000, "limit": 5000}]},
                {"name": "buy.browse.item.bulk", "rates": [{"remaining": self.bulk_left, "limit": 5000}]},
            ]}]}
        if self.fail is not None:
            raise ebay.EbayError(self.fail)
        if "item_ids=" not in url:
            # One listing on its own: eBay's getItem, the id in the address.
            item_id = urllib.parse.unquote(url[len(ebay.ITEMS_URL):])
            if item_id not in self.items:
                raise ebay.EbayError("eBay API 404: {\"errors\":[{\"errorId\":11001,\"message\":\"The item does not exist\"}]}")
            return self.items[item_id]
        asked = [urllib.parse.unquote(item_id) for item_id in url.split("item_ids=", 1)[1].split(",")]
        if any(item_id not in self.items for item_id in asked):
            raise ebay.EbayError("eBay API 404: {\"errors\":[{\"errorId\":11001,\"message\":\"The item does not exist\"}]}")
        return {"items": [self.items[item_id] for item_id in asked]}

    def lookups(self):
        return [url for url in self.calls if url.startswith(ebay.ITEMS_URL)]


@KEYS
class CheckTests(WorkerTestCase):
    def setUp(self):
        super().setUp()
        ebay.forget_live()
        self.addCleanup(ebay.forget_live)
        believed()   # the lookup has shown and priced an item before, so it is believed about one it does not
        self.ebay = self.shop("eBay", source_type=Retailer.Source.EBAY, source_url="https://www.ebay.co.uk/")
        self.box = self.product("surging-box", views=3)
        Product.objects.filter(pk=self.box.pk).update(name="Surging Sparks Booster Box")
        self.box.refresh_from_db()
        self.row = self.listing(self.box, self.ebay, 120, price="82.00", url="https://www.ebay.co.uk/itm/123")
        Listing.objects.filter(pk=self.row.pk).update(delivery_cost=Decimal("3.89"), delivery_known=True)
        self.row.refresh_from_db()

    def other_listing(self):
        other = self.product("other-box", views=3)
        return self.listing(other, self.ebay, 120, price="50.00", url="https://www.ebay.co.uk/itm/456")

    def check(self, api, *listings):
        return ebay.check_listings(listings or [self.row], request=api, pause=0, sleep=lambda s: None)

    def test_a_listing_is_read_through_the_bulk_lookup_with_its_price_and_postage(self):
        api = FakeEbay([item(123, price="79.99", postage="2.99")])
        found = self.check(api)
        self.assertEqual(found, {self.row.pk: (Decimal("79.99"), Listing.Availability.IN_STOCK, Decimal("2.99"))})
        self.assertEqual(api.lookups(), ["https://api.ebay.com/buy/browse/v1/item/?item_ids=v1%7C123%7C0"])
        # The token and the allowance are kept: a second check asks for neither again.
        self.check(api)
        self.assertEqual(sum(1 for url in api.calls if url == ebay.TOKEN_URL), 1)
        self.assertEqual(sum(1 for url in api.calls if url.startswith(ebay.LIMITS_URL)), 1)

    def test_an_ended_listing_a_poor_seller_or_junk_is_out_of_stock_at_its_last_price(self):
        gone = FakeEbay([])
        self.assertEqual(self.check(gone), {self.row.pk: (Decimal("82.00"), Listing.Availability.OUT_OF_STOCK, Decimal("3.89"))})
        self.assertIn("v1|123|0", ebay.ended_items())
        ebay.forget_live()
        believed()
        poor = FakeEbay([item(123, feedback="90.0")])
        self.assertEqual(self.check(poor)[self.row.pk][1], Listing.Availability.OUT_OF_STOCK)
        junk = FakeEbay([item(123, title="Surging Sparks Booster Box x 3 Multibuy")])
        self.assertEqual(self.check(junk)[self.row.pk][1], Listing.Availability.OUT_OF_STOCK)
        # One ended listing in a batch of two: eBay refuses the batch, so each half is asked for.
        ebay.forget_live()
        believed()
        other = self.other_listing()
        api = FakeEbay([item(456, price="51.00", postage="0.00", title="Other Box")])
        found = self.check(api, self.row, other)
        self.assertEqual(found[self.row.pk][1], Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(found[other.pk], (Decimal("51.00"), Listing.Availability.IN_STOCK, Decimal("0.00")))
        self.assertEqual(len(api.lookups()), 3)

    def test_an_item_ebay_says_has_ended_is_never_asked_about_again(self):
        """The owner, 12 Nov 2026: "ebay hasn't updated in 8 hours". Every check of a batch holding an ended item asked
        about each of its twenty alone, again and again, and the day's allowance ran out."""
        self.check(FakeEbay([]))
        # Out of stock, as the reader records it, and due its six-hourly look.
        Listing.objects.filter(pk=self.row.pk).update(availability=Listing.Availability.OUT_OF_STOCK,
                                                      last_checked=self.clock() - timedelta(hours=7))
        self.row.refresh_from_db()
        api = FakeEbay([item(123)])
        found = self.check(api)
        self.assertEqual(found[self.row.pk][1], Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(api.lookups(), [])
        # Nor is it planned: the reader leaves it to the daily run, which searches for a new listing.
        reader = self.make_worker(ebay_request=api)
        self.assertEqual(reader.plan(self.clock()), [])

    def test_the_lookup_is_asked_at_ebays_own_address_with_the_ids_encoded(self):
        """Until 16 Nov 2026 it asked /item/get_items, an address eBay does not have. eBay said "not found" of every
        lookup, which was read as every listing having ended (the owner's log: "refreshing 1966 known listings", "0
        known listings still live", then 262 of 262 found live by the search two minutes later)."""
        self.assertEqual(ebay.items_address(["v1|123|0", "v1|456|0"]),
                         "https://api.ebay.com/buy/browse/v1/item/?item_ids=v1%7C123%7C0,v1%7C456%7C0")
        self.assertNotIn("get_items", ebay.ITEMS_URL)

    def many_listings(self, count=12):
        rows = []
        for n in range(count):
            product = self.product(f"many-{n}", views=3)
            Product.objects.filter(pk=product.pk).update(name=f"Many Box {n}")
            rows.append(self.listing(product, self.ebay, 120, price="50.00", url=f"https://www.ebay.co.uk/itm/{2000 + n}"))
        return rows

    def test_a_lookup_that_shows_no_item_is_not_believed_and_marks_nothing(self):
        """A lookup that is failing says "not found" of every item. Believed, it marked every live eBay listing out
        of stock between the daily runs and put each on the ended list, never to be asked about again."""
        ebay.forget_live()   # a reader just started: the lookup has shown nothing yet
        rows = self.many_listings(12)
        api = FakeEbay([])
        with self.assertRaises(ebay.LookupUnusable) as raised:
            self.check(api, *rows)
        self.assertIn("said none of the 10 items asked about exists. That is not believed", str(raised.exception))
        self.assertIn("eBay's words: eBay API 404", str(raised.exception))
        self.assertEqual(ebay.ended_items(), {})
        self.assertLess(len(api.lookups()), 25)   # it stops finding out after ten, where it once spent hundreds
        # The reader: nothing marked, the problem noted with eBay's words, eBay not backed off, no check planned
        # while the lookups wait, and none made.
        ebay.forget_live()
        api = FakeEbay([])
        reader = self.make_worker(ebay_request=api)
        [task] = reader.plan(self.clock())
        now = self.clock()
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        for row in rows:
            row.refresh_from_db()
            self.assertEqual((row.availability, row.last_checked), (Listing.Availability.IN_STOCK, now - timedelta(minutes=120)))
        self.assertIn("That is not believed, so nothing was marked out of stock", ebay.last_check()["problem"])
        self.ebay.refresh_from_db()
        self.assertIsNone(self.ebay.backoff_until)
        asked = len(api.lookups())
        self.clock.advance(minutes=5)
        reader.probe_failed.clear()
        self.assertEqual(reader.plan(self.clock()), [])
        with self.assertRaises(ebay.LookupUnusable):
            self.check(api, *rows)
        self.assertEqual(len(api.lookups()), asked)
        # Once it shows an item it is believed again: the one missing from its answer has ended.
        ebay.forget_live()
        live = FakeEbay([item(2000 + n, title=f"Many Box {n}") for n in range(12) if n != 3])
        found = self.check(live, *rows)
        self.assertEqual(found[rows[3].pk][1], Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(sum(1 for _p, stock, _d in found.values() if stock == Listing.Availability.IN_STOCK), 11)
        self.assertEqual(set(ebay.ended_items()), {"v1|2003|0"})

    def test_items_the_lookup_shows_but_that_cannot_be_priced_are_not_marked_out_of_stock(self):
        """eBay's documents say the bulk lookup returns fewer details than the search. If none of what it sends reads
        as an offer (here, no postage), that hides nothing: the listings wait and the page says what eBay sent."""
        ebay.forget_live()
        rows = self.many_listings(12)
        bare = []
        for n in range(12):
            row = item(2000 + n, title=f"Many Box {n}")
            del row["shippingOptions"]
            bare.append(row)
        with self.assertRaises(ebay.LookupUnusable) as raised:
            self.check(FakeEbay(bare), *rows)
        self.assertIn("eBay showed 12 items and none could be read as an offer", str(raised.exception))
        self.assertIn("eBay sent: image, itemId, itemWebUrl, price, seller, title", str(raised.exception))
        # One that reads as an offer, and the lookup is believed about the rest.
        ebay.forget_live()
        mixed = bare[:11] + [item(2011, title="Many Box 11", price="49.00", postage="1.00")]
        found = self.check(FakeEbay(mixed), *rows)
        self.assertEqual(found[rows[11].pk], (Decimal("49.00"), Listing.Availability.IN_STOCK, Decimal("1.00")))
        self.assertEqual(sum(1 for _p, stock, _d in found.values() if stock == Listing.Availability.OUT_OF_STOCK), 11)

    def test_a_lookup_ebay_will_not_serve_marks_nothing_and_does_not_back_ebay_off(self):
        """eBay's documents call the lookup a limited release for chosen partners: refused (403), or asked at an
        address eBay does not have (a 404 that names no item), it is not a listing that ended."""
        for words in ('eBay API 403: {"errors":[{"errorId":1100,"message":"Access denied","longMessage":"Insufficient '
                      'permissions to fulfill the request."}]}', "eBay API 404: Not Found"):
            ebay.forget_live()
            believed()
            api = FakeEbay([item(123)], fail=words)
            reader = self.make_worker(ebay_request=api)
            [task] = reader.plan(self.clock())
            now = self.clock()
            reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
            self.row.refresh_from_db()
            self.assertEqual(self.row.availability, Listing.Availability.IN_STOCK)
            self.assertEqual(ebay.ended_items(), {})
            problem = ebay.last_check()["problem"]
            self.assertIn("eBay will not serve its item lookup to this application, so nothing was checked.", problem)
            self.assertIn(words[:40], problem)
            self.ebay.refresh_from_db()
            self.assertIsNone(self.ebay.backoff_until)
            reader.probe_failed.clear()
            self.assertEqual(reader.plan(self.clock()), [])   # the lookups wait an hour
            self.assertEqual(len(api.lookups()), 1)

    def test_test_ebay_now_says_how_each_thing_went_in_ebays_own_words(self):
        api = FakeEbay([item(123, price="79.99", postage="2.99")])
        lines = ebay.self_test(request=api)
        self.assertEqual(lines[:3], ["Keys: all three are set.", "Token: eBay issued one, so it accepts the keys.",
                                     "Allowances: 4,000 searches and 5,000 item lookups left today, reset not said."])
        self.assertEqual(lines[3], "Item lookup, 1 listing at once (what the checks use): eBay showed 1 of 1.")
        self.assertTrue(lines[4].startswith("  The first reads as £79.99 with £2.99 postage. eBay sent: "))
        self.assertTrue(lines[5].startswith("Item lookup, one listing on its own: eBay showed it"))
        self.assertEqual(ebay.last_test()["lines"], lines)
        self.row.refresh_from_db()
        self.assertEqual(self.row.price, Decimal("82.00"))   # a test changes no listing
        # Refused: eBay's own words, for both ways of asking.
        refused = FakeEbay([item(123)], fail='eBay API 403: {"errors":[{"errorId":1100,"message":"Access denied"}]}')
        lines = ebay.self_test(request=refused)
        self.assertEqual(lines[3], 'Item lookup, 1 listing at once (what the checks use): REFUSED. eBay\'s words: eBay '
                                   'API 403: {"errors":[{"errorId":1100,"message":"Access denied"}]}')
        self.assertTrue(lines[4].startswith("Item lookup, one listing on its own: REFUSED. eBay's words: eBay API 403"))
        with override_settings(RIPRAPTOR_EBAY_APP_ID=""):
            self.assertEqual(ebay.self_test(request=api)[0][:14], "Keys: NOT SET.")

    def test_an_ended_item_the_daily_search_found_in_stock_again_is_checked_again(self):
        """In stock and on the ended list, it was never checked between daily searches, so its price stopped counting
        four hours after each (the owner, 16 Nov 2026, with no eBay price counting: "something is wrong")."""
        ebay.note_ended(["v1|123|0"])
        api = FakeEbay([item(123, price="79.99")])
        reader = self.make_worker(ebay_request=api)
        [task] = reader.plan(self.clock())
        self.assertEqual(task.listings, [self.row.pk])
        now = self.clock()
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        self.row.refresh_from_db()
        self.assertEqual((self.row.price, self.row.availability), (Decimal("79.99"), Listing.Availability.IN_STOCK))
        self.assertNotIn("v1|123|0", ebay.ended_items())   # eBay showed it: off the list

    def test_ebay_due_a_read_that_cannot_start_still_has_its_items_checked(self):
        """The owner, 16 Nov 2026: the reader running, eBay neither paused nor waiting, 5,000 lookups unused and no eBay
        price counting. eBay's hourly read was due and could not start while another command held the import lock, and
        a shop about to be read is not probed, so every eBay check waited with it."""
        import fcntl
        import os

        from .tests_worker import HeldExecutor

        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=self.clock() - timedelta(hours=3))
        held = os.open(self.import_lock, os.O_RDONLY | os.O_CREAT)
        self.addCleanup(os.close, held)
        fcntl.flock(held, fcntl.LOCK_EX)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor, ebay_request=FakeEbay([item(123)]))
        reader.queue = reader.plan(self.clock())
        self.assertEqual([(t.kind, t.retailer_id) for t in reader.queue],
                         [(worker.SHOP_READ, self.ebay.pk), (worker.PROBE, self.ebay.pk)])
        reader.dispatch(self.clock())
        self.assertEqual([(t.kind, t.retailer_id) for t in executor.jobs], [(worker.PROBE, self.ebay.pk)])
        # The lock let go: the read goes first, and its checks wait for it, one job for eBay at a time.
        fcntl.flock(held, fcntl.LOCK_UN)
        executor.jobs.clear()
        reader.running.clear()
        reader.queue = reader.plan(self.clock())
        reader.dispatch(self.clock())
        self.assertEqual([(t.kind, t.retailer_id) for t in executor.jobs], [(worker.SHOP_READ, self.ebay.pk)])
        self.assertEqual([t.kind for t in reader.queue], [worker.PROBE])
        # While eBay is being read, its checks are not planned.
        self.assertEqual([t.kind for t in reader.plan(self.clock())], [])
        # Any other shop about to be read is still not probed: its read sees every listing.
        shop = self.shop("Due Shop", due=True)
        self.listing(self.product("shop-box", views=3), shop, 30)
        reader.running.clear()
        reader.import_lock.release()
        self.assertNotIn((worker.PROBE, shop.pk), [(t.kind, t.retailer_id) for t in reader.plan(self.clock())])

    def test_each_check_is_noted_for_the_ebay_health_page(self):
        api = FakeEbay([item(123)])
        reader = self.make_worker(ebay_request=api)

        def run():
            [task] = reader.plan(self.clock())
            now = self.clock()
            reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))

        def check():
            run()
            Listing.objects.filter(pk=self.row.pk).update(last_checked=self.clock() - timedelta(hours=3))
            reader.probe_failed.clear()
            return ebay.last_check()

        noted = check()
        self.assertEqual((noted["asked"], noted["in_stock"], noted["gone"], noted["left"]), (1, 1, 0, 0))
        self.assertNotIn("problem", noted)
        api.fail = "eBay API 503: Service Unavailable"
        noted = check()
        self.assertEqual(noted["problem"], "eBay did not answer: eBay API 503: Service Unavailable")
        self.assertEqual(noted["asked"], 1)   # the last check that got an answer is kept
        Retailer.objects.filter(pk=self.ebay.pk).update(backoff_until=None, error_streak=0)

        def broken(url, headers, data=None):
            raise RuntimeError("boom")

        reader.ebay_request = broken
        ebay.forget_live()
        with self.assertLogs("catalogue.worker", "ERROR"):
            noted = check()
        self.assertEqual(noted["problem"], "The check stopped on an unexpected error: RuntimeError: boom")
        # The note is a courtesy: a cache that cannot answer never stops a check saving what eBay said.
        from unittest import mock

        reader.ebay_request = FakeEbay([item(123, price="77.00")])
        with mock.patch.object(ebay, "last_check", side_effect=OSError("disk full")), \
                self.assertLogs("catalogue.ebay", "WARNING"):
            run()
        self.row.refresh_from_db()
        self.assertEqual(self.row.price, Decimal("77.00"))

    def test_one_ended_item_in_twenty_costs_about_ten_lookups_not_twenty_one(self):
        rows, items = [], []
        for n in range(20):
            product = self.product(f"box-{n}", views=3)
            Product.objects.filter(pk=product.pk).update(name=f"Box {n}")
            rows.append(self.listing(product, self.ebay, 120, price="50.00", url=f"https://www.ebay.co.uk/itm/{1000 + n}"))
            if n != 7:
                items.append(item(1000 + n, title=f"Box {n}"))
        api = FakeEbay(items)
        found = self.check(api, *rows)
        self.assertEqual(found[rows[7].pk][1], Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(sum(1 for pk, (_p, stock, _d) in found.items() if stock == Listing.Availability.IN_STOCK), 19)
        self.assertLessEqual(len(api.lookups()), 10)
        self.assertEqual(set(ebay.ended_items()), {"v1|1007|0"})

    def test_the_days_allowance_is_left_to_the_daily_run(self):
        with self.assertRaises(ebay.AllowanceUsed):
            self.check(FakeEbay([item(123)], bulk_left=ebay.BULK_KEEP_BACK))
        # Counted down between asks: the last lookup before the floor is made, and the rest wait.
        ebay.forget_live()
        api = FakeEbay([item(123), item(456, title="Other Box")], bulk_left=ebay.BULK_KEEP_BACK + 1)
        other = self.other_listing()
        found = self.check(api, self.row, other)
        self.assertEqual(set(found), {self.row.pk, other.pk})   # one request covers both
        self.assertEqual(ebay._live["bulk_left"], ebay.BULK_KEEP_BACK)

    def test_asking_ebay_about_the_allowances_notes_them_for_the_crawl_health_page(self):
        from .models import WorkerState

        api = FakeEbay([item(123)], bulk_left=4610)
        self.check(api)
        usage = WorkerState.current().ebay_usage
        self.assertEqual((usage["searches"], usage["lookups"]), (4000, 4610))
        self.assertTrue(usage["asked_at"])

    def test_an_ebay_listing_nobody_is_looking_at_is_still_checked_every_two_hours(self):
        """Twice inside the four hours an eBay price counts for (the owner, 12 Nov 2026: "This sort of time to update
        is not good enough")."""
        box = self.product("cold-box")
        self.listing(box, self.shop("Other Shop"), 5, price="21.00")   # two shops sell it and nobody looks: cold
        cold = self.listing(box, self.ebay, 90, price="20.00", url="https://www.ebay.co.uk/itm/789")
        reader = self.make_worker(ebay_request=FakeEbay([item(123), item(789, title="Cold Box", price="19.00", postage="0.00")]))
        [task] = reader.plan(self.clock())
        self.assertEqual(task.listings, [self.row.pk])   # the hot one; the cold one is not due for half an hour
        # Out of stock, it is not shown as buyable: asked about every six hours, not two.
        Listing.objects.filter(pk=cold.pk).update(last_checked=self.clock() - timedelta(hours=3),
                                                  availability=Listing.Availability.OUT_OF_STOCK)
        [task] = reader.plan(self.clock())
        self.assertEqual(task.listings, [self.row.pk])
        Listing.objects.filter(pk=cold.pk).update(availability=Listing.Availability.IN_STOCK)
        [task] = reader.plan(self.clock())
        self.assertEqual(task.listings, [self.row.pk, cold.pk])   # one job for eBay, the hot listing first
        now = self.clock()
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        cold.refresh_from_db()
        self.assertEqual((cold.price, cold.availability), (Decimal("19.00"), Listing.Availability.IN_STOCK))

    def test_the_background_reader_checks_a_wanted_ebay_listing_and_records_what_it_found(self):
        api = FakeEbay([item(123, price="79.99", postage="2.99")])
        reader = self.make_worker(ebay_request=api)
        [task] = reader.plan(self.clock())
        self.assertEqual((task.kind, task.retailer_id, task.listings), (worker.PROBE, self.ebay.pk, [self.row.pk]))
        now = self.clock()
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        self.row.refresh_from_db()
        self.assertEqual((self.row.price, self.row.delivery_cost, self.row.availability),
                         (Decimal("79.99"), Decimal("2.99"), Listing.Availability.IN_STOCK))
        self.assertGreater(self.row.last_checked, now - timedelta(minutes=1))   # checked now, as a probe records it
        self.ebay.refresh_from_db()
        self.assertIsNone(self.ebay.backoff_until)

    def test_a_429_from_the_lookup_is_the_allowance_used_not_ebay_failing(self):
        # The owner, 6 Nov 2026, with eBay "backing off until 13:49" on Crawl health after one: "That's the eBay failure".
        refused = ('eBay API 429: {"errors": [{"errorId": 2001, "domain": "ACCESS", "category": "REQUEST", '
                   '"message": "Too many requests."}]}')
        api = FakeEbay([item(123)], fail=refused)
        reader = self.make_worker(ebay_request=api)
        [task] = reader.plan(self.clock())
        now = self.clock()
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        self.ebay.refresh_from_db()
        self.assertIsNone(self.ebay.backoff_until)      # not a failed read: the daily search run is not delayed
        self.assertEqual(reader.plan(self.clock()), [])  # the listing waits
        # The checks wait BULK_PAUSE without asking eBay, then ask its counter again and go on if there is room.
        api.fail = None
        base = time.monotonic()
        ticks = [0.0]
        counter_asks = lambda: len([url for url in api.calls if url.startswith(ebay.LIMITS_URL)])   # noqa: E731
        asked = counter_asks()
        with self.assertRaises(ebay.AllowanceUsed):
            ebay.check_listings([self.row], request=api, clock=lambda: base + ticks[0], pause=0, sleep=lambda s: None)
        self.assertEqual(counter_asks(), asked)
        ticks[0] = ebay.BULK_PAUSE + 1
        found = ebay.check_listings([self.row], request=api, clock=lambda: base + ticks[0], pause=0, sleep=lambda s: None)
        self.assertEqual(set(found), {self.row.pk})
        self.assertEqual(counter_asks(), asked + 1)

    def test_ebays_counter_is_asked_again_after_a_hundred_lookups_not_only_hourly(self):
        api = FakeEbay([item(123)])
        self.check(api)
        asked = len([url for url in api.calls if url.startswith(ebay.LIMITS_URL)])
        for _n in range(ebay.ALLOWANCE_ASK_SPEND - 1):
            ebay.spend_bulk()     # what the daily run's refresh and the title filler count as they go
        self.check(api)
        self.assertEqual(len([url for url in api.calls if url.startswith(ebay.LIMITS_URL)]), asked + 1)

    def test_an_ebay_listing_people_look_at_is_checked_every_half_hour_not_every_ten_minutes(self):
        # Its views are today's. Run just before midnight, the half hour below ends in the next day, where they no
        # longer count and it is not hot: so that day has them too (it failed when run at 23:46 on 14 Nov 2026).
        DailyPageView.objects.get_or_create(date=timezone.localdate(self.clock() + timedelta(minutes=31)), kind="product",
                                            key=self.box.slug, defaults={"hits": 3})
        api = FakeEbay([item(123)])
        reader = self.make_worker(ebay_request=api)
        [task] = reader.plan(self.clock())
        now = self.clock()
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        self.clock.advance(minutes=15)
        self.assertEqual(reader.plan(self.clock()), [])   # a shop's hot listing would be due again by now
        self.clock.advance(minutes=16)
        [task] = reader.plan(self.clock())
        self.assertEqual(task.listings, [self.row.pk])

    def test_ebay_not_answering_backs_it_off_and_a_used_allowance_only_makes_the_listings_wait(self):
        api = FakeEbay([item(123)], fail="eBay API 503: Service Unavailable")
        reader = self.make_worker(ebay_request=api)
        [task] = reader.plan(self.clock())
        now = self.clock()
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        self.ebay.refresh_from_db()
        self.assertGreater(self.ebay.backoff_until, now)
        self.assertIn("Checking eBay listings: eBay API 503", self.ebay.last_error)
        self.row.refresh_from_db()
        self.assertEqual(self.row.last_checked, now - timedelta(minutes=120))   # as it was
        # The allowance down to the floor: no back-off, the listing waits an hour, and the daily read is not delayed.
        ebay.forget_live()
        Retailer.objects.filter(pk=self.ebay.pk).update(backoff_until=None, error_streak=0, last_error="")
        api = FakeEbay([item(123)], bulk_left=10)
        reader = self.make_worker(ebay_request=api)
        [task] = reader.plan(self.clock())
        reader.run_job(worker.Job(task, now, now + worker.PROBE_DEADLINE))
        self.ebay.refresh_from_db()
        self.assertIsNone(self.ebay.backoff_until)
        self.assertEqual(reader.plan(self.clock()), [])   # waiting, not asked again at once
