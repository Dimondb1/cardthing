"""eBay's listings checked between the daily runs (ebay.check_listings, worker.Worker.check_ebay): the products that
matter every ten minutes or every hour, like any shop's, through eBay's bulk item lookup. The owner, 6 Nov 2026, of
a price "checked at eBay 20 hours ago": "I want users to know they are getting the latest prices. 20 hours ago
isn't good enough. If the eBay API times out then we need another way."."""

from datetime import timedelta
from decimal import Decimal

from django.test import override_settings

from . import ebay, worker
from .models import Listing, Product, Retailer
from .tests_worker import WorkerTestCase

KEYS = override_settings(RIPRAPTOR_EBAY_APP_ID="app", RIPRAPTOR_EBAY_CERT_ID="cert", RIPRAPTOR_EBAY_CAMPAIGN_ID="camp")


def item(number, price="85.89", postage="3.89", feedback="99.6", ratings=1200, title="Surging Sparks Booster Box"):
    return {
        "itemId": f"v1|{number}|0", "title": title, "price": {"value": price, "currency": "GBP"},
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{number}", "seller": {"feedbackPercentage": feedback, "feedbackScore": ratings},
        "shippingOptions": [{"shippingCost": {"value": postage}}], "image": {},
    }


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
        asked = url.split("item_ids=", 1)[1].split(",")
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
        self.assertEqual(api.lookups(), [ebay.ITEMS_URL + "?item_ids=v1|123|0"])
        # The token and the allowance are kept: a second check asks for neither again.
        self.check(api)
        self.assertEqual(sum(1 for url in api.calls if url == ebay.TOKEN_URL), 1)
        self.assertEqual(sum(1 for url in api.calls if url.startswith(ebay.LIMITS_URL)), 1)

    def test_an_ended_listing_a_poor_seller_or_junk_is_out_of_stock_at_its_last_price(self):
        gone = FakeEbay([])
        self.assertEqual(self.check(gone), {self.row.pk: (Decimal("82.00"), Listing.Availability.OUT_OF_STOCK, Decimal("3.89"))})
        poor = FakeEbay([item(123, feedback="90.0")])
        self.assertEqual(self.check(poor)[self.row.pk][1], Listing.Availability.OUT_OF_STOCK)
        junk = FakeEbay([item(123, title="Surging Sparks Booster Box x 3 Multibuy")])
        self.assertEqual(self.check(junk)[self.row.pk][1], Listing.Availability.OUT_OF_STOCK)
        # One ended listing in a batch of two: eBay refuses the batch, so each is asked for on its own.
        other = self.other_listing()
        api = FakeEbay([item(456, price="51.00", postage="0.00", title="Other Box")])
        found = self.check(api, self.row, other)
        self.assertEqual(found[self.row.pk][1], Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(found[other.pk], (Decimal("51.00"), Listing.Availability.IN_STOCK, Decimal("0.00")))
        self.assertEqual(len(api.lookups()), 3)

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

    def test_an_ebay_listing_nobody_is_looking_at_is_still_checked_every_six_hours(self):
        box = self.product("cold-box")
        self.listing(box, self.shop("Other Shop"), 5, price="21.00")   # two shops sell it and nobody looks: cold
        cold = self.listing(box, self.ebay, 5 * 60, price="20.00", url="https://www.ebay.co.uk/itm/789")
        reader = self.make_worker(ebay_request=FakeEbay([item(123), item(789, title="Cold Box", price="19.00", postage="0.00")]))
        [task] = reader.plan(self.clock())
        self.assertEqual(task.listings, [self.row.pk])   # the hot one; the cold one is not due for another hour
        Listing.objects.filter(pk=cold.pk).update(last_checked=self.clock() - timedelta(hours=7))
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
