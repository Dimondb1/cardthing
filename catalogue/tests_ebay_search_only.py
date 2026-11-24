"""eBay's prices are kept fresh by its hourly searches alone. eBay's twenty-at-once item lookup (getItems) is for
partners eBay chooses and always refused this application (403), so since 24 Nov 2026 the site never asks it: the
background reader no longer checks eBay listings between searches, and Test eBay now asks for a token, the day's
searches, one search and eBay's one-item lookup. The owner, 24 Nov 2026: "Do number 1 but also fix the ebay falling
behind thing"."""

import urllib.parse
from decimal import Decimal

from django.test import override_settings

from . import ebay, worker
from .models import Product, Retailer
from .tests_worker import WorkerTestCase

KEYS = override_settings(RIPRAPTOR_EBAY_APP_ID="app", RIPRAPTOR_EBAY_CERT_ID="cert", RIPRAPTOR_EBAY_CAMPAIGN_ID="camp")


def item(number, price="85.89", postage="3.89", title="Surging Sparks Booster Box"):
    return {
        "itemId": f"v1|{number}|0", "title": title, "price": {"value": price, "currency": "GBP"},
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{number}", "seller": {"feedbackPercentage": "99.6", "feedbackScore": 1200},
        "shippingOptions": [{"shippingCost": {"value": postage}}], "image": {},
    }


class FakeEbay:
    """eBay's API as the site uses it now: a token, the allowances, searches and the one-item lookup. The
    twenty-at-once lookup is never asked."""

    def __init__(self, items=(), searches_left=4000, fail=None):
        self.items = {row["itemId"]: row for row in items}
        self.searches_left = searches_left
        self.fail = fail
        self.calls = []

    def __call__(self, url, headers, data=None):
        self.calls.append(url)
        if url == ebay.TOKEN_URL:
            return {"access_token": "tok", "expires_in": 7200}
        if url.startswith(ebay.LIMITS_URL):
            return {"rateLimits": [{"resources": [
                {"name": "buy.browse", "rates": [{"remaining": self.searches_left, "limit": 5000}]},
            ]}]}
        assert "item_ids=" not in url, "the twenty-at-once lookup is never asked"
        if self.fail is not None:
            raise ebay.EbayError(self.fail)
        if url.startswith(ebay.SEARCH_URL):
            return {"total": len(self.items), "itemSummaries": list(self.items.values())}
        item_id = urllib.parse.unquote(url[len(ebay.ITEM_URL):])
        if item_id not in self.items:
            raise ebay.EbayError('eBay API 404: {"errors":[{"errorId":11001,"message":"The item does not exist"}]}')
        return self.items[item_id]


@KEYS
class SearchOnlyTests(WorkerTestCase):
    def setUp(self):
        super().setUp()
        ebay.forget_live()
        self.addCleanup(ebay.forget_live)
        self.ebay = self.shop("eBay", source_type=Retailer.Source.EBAY, source_url="https://www.ebay.co.uk/")
        self.box = self.product("surging-box", views=3)
        Product.objects.filter(pk=self.box.pk).update(name="Surging Sparks Booster Box")
        self.box.refresh_from_db()
        self.row = self.listing(self.box, self.ebay, 600, price="82.00", url="https://www.ebay.co.uk/itm/123")

    def test_the_reader_never_checks_an_ebay_listing_between_searches(self):
        # Looked at by visitors and ten hours old: once checked every half hour through the refused lookup.
        self.listing(self.product("cold-box"), self.ebay, 600, price="20.00", url="https://www.ebay.co.uk/itm/789")
        tasks = self.make_worker().plan(self.clock())
        self.assertFalse([task for task in tasks if task.kind == worker.PROBE and task.retailer_id == self.ebay.pk])
        self.assertIn(Retailer.Source.EBAY, worker.probe.NEVER_CHECKED)

    def test_test_ebay_now_says_how_each_thing_went_in_ebays_own_words(self):
        api = FakeEbay([item(123, price="79.99", postage="2.99")])
        lines = ebay.self_test(request=api)
        self.assertEqual(lines[:4], [
            "Keys: all three are set.", "Token: eBay issued one, so it accepts the keys.",
            "Allowance: 4,000 searches left today, reset not said.",
            "Search for Surging Sparks Booster Box: eBay found 1 listings.",
        ])
        self.assertTrue(lines[4].startswith("One-item lookup: eBay showed it, Surging Sparks Booster Box, 79.99 GBP"))
        self.assertEqual(ebay.last_test()["lines"], lines)
        self.assertFalse([url for url in api.calls if "item_ids=" in url])
        self.row.refresh_from_db()
        self.assertEqual(self.row.price, Decimal("82.00"))   # a test changes no listing
        refused = FakeEbay([item(123)], fail='eBay API 403: {"errors":[{"errorId":1100,"message":"Access denied"}]}')
        lines = ebay.self_test(request=refused)
        self.assertTrue(lines[3].startswith("Search for Surging Sparks Booster Box: REFUSED. eBay's words: eBay API 403"))
        self.assertTrue(lines[4].startswith("One-item lookup: REFUSED. eBay's words: eBay API 403"))
        with override_settings(RIPRAPTOR_EBAY_APP_ID=""):
            self.assertEqual(ebay.self_test(request=api)[0][:14], "Keys: NOT SET.")

    def test_asking_ebay_about_the_allowances_notes_them_for_the_crawl_health_page(self):
        from .models import WorkerState

        headers = ebay.headers_for("tok", "camp")
        self.assertEqual(ebay.allowances(headers, request=FakeEbay(searches_left=3010))[0], 3010)
        usage = WorkerState.current().ebay_usage
        self.assertEqual(usage["searches"], 3010)
        self.assertTrue(usage["asked_at"])
