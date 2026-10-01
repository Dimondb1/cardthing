import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import ebay
from .importers import Offer, run_import
from .models import ImportRun, Listing, Product, Retailer
from .testing import make_game, make_listing, make_product, make_set

KEYS = {
    "RIPRAPTOR_EBAY_APP_ID": "RipRapto-RipRapto-PRD-abc",
    "RIPRAPTOR_EBAY_CERT_ID": "PRD-secret",
    "RIPRAPTOR_EBAY_CAMPAIGN_ID": "5339012345",
}


def item(title, price="79.99", postage="0.00", item_id="v1|123|0", feedback="99.5", currency="GBP", postage_known=True):
    row = {
        "itemId": item_id,
        "title": title,
        "price": {"value": price, "currency": currency},
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{item_id.split('|')[1]}",
        "itemAffiliateWebUrl": f"https://www.ebay.co.uk/itm/{item_id.split('|')[1]}?mkevt=1&campid=5339012345",
        "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
        "seller": {"username": "cardshop", "feedbackPercentage": feedback},
        "condition": "New",
    }
    if postage_known:
        row["shippingOptions"] = [{"shippingCost": {"value": postage, "currency": "GBP"}}]
    return row


class FakeApi:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, url, headers, data=None):
        self.calls.append((url, headers, data))
        if url == ebay.TOKEN_URL:
            return {"access_token": "tok", "expires_in": 7200}
        return {"itemSummaries": self.results}


class TokenTests(TestCase):
    def test_token_request_uses_basic_auth_and_client_credentials(self):
        api = FakeApi([])
        self.assertEqual(ebay.access_token("app", "cert", request=api), "tok")
        url, headers, data = api.calls[0]
        self.assertEqual(url, ebay.TOKEN_URL)
        self.assertEqual(headers["Authorization"], "Basic YXBwOmNlcnQ=")
        self.assertIn(b"grant_type=client_credentials", data)

    def test_no_token_is_a_clear_error(self):
        with self.assertRaises(ebay.EbayError):
            ebay.access_token("app", "cert", request=lambda *a, **k: {"error": "invalid_client"})

    def test_missing_keys_are_a_clear_error(self):
        with self.assertRaises(ebay.EbayError):
            ebay.credentials()


class ItemTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), ean="0820650853500")

    def test_result_becomes_an_offer_with_postage_and_affiliate_link(self):
        offer = ebay.item_offer(item("Pokemon Prismatic Evolutions Elite Trainer Box", postage="3.49"), self.product, Offer)
        self.assertEqual(offer.price, Decimal("79.99"))
        self.assertEqual(offer.delivery, Decimal("3.49"))
        self.assertEqual(offer.product_pk, self.product.pk)
        self.assertIn("campid=5339012345", offer.url)

    def test_unknown_postage_low_feedback_and_other_currency_are_skipped(self):
        self.assertIsNone(ebay.item_offer(item("Box", postage_known=False), self.product, Offer))
        self.assertIsNone(ebay.item_offer(item("Box", feedback="80.0"), self.product, Offer))
        self.assertIsNone(ebay.item_offer(item("Box", currency="EUR"), self.product, Offer))

    def test_search_uses_barcode_when_we_have_one_and_name_otherwise(self):
        self.assertIn("gtin=0820650853500", ebay.search_url(self.product))
        self.product.ean = ""
        self.assertIn("q=Prismatic+Evolutions+Elite+Trainer+Box", ebay.search_url(self.product))
        self.assertIn("conditions%3A%7BNEW%7D", ebay.search_url(self.product))


@override_settings(**KEYS)
class LookupTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.bundle = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle")
        self.retailer = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )

    def test_cheapest_matching_result_wins_and_singles_are_skipped(self):
        api = FakeApi([
            item("Prismatic Evolutions Umbreon ex 161/131 single card", price="40.00", item_id="v1|1|0"),
            item("Pokemon TCG Prismatic Evolutions Elite Trainer Box sealed", price="79.99", item_id="v1|2|0"),
            item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="85.00", item_id="v1|3|0"),
        ])
        offers = ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("79.99"))
        self.assertEqual(offers[0].product_pk, self.etb.pk)
        search_url, headers, _ = api.calls[1]
        self.assertIn("gtin=0820650853500", search_url)
        self.assertEqual(headers["X-EBAY-C-MARKETPLACE-ID"], "EBAY_GB")
        self.assertIn("affiliateCampaignId=5339012345", headers["X-EBAY-C-ENDUSERCTX"])
        self.etb.refresh_from_db()
        self.assertIsNotNone(self.etb.ebay_checked_at)

    def test_name_search_needs_a_full_match(self):
        self.etb.delete()
        api = FakeApi([item("Prismatic Evolutions Booster Bundle x2 bundles", price="30.00")])
        self.assertEqual(ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0), [])
        api = FakeApi([item("Pokemon Prismatic Evolutions Booster Bundle", price="30.00")])
        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        self.assertEqual([o.product_pk for o in offers], [self.bundle.pk])

    def test_products_already_on_ebay_are_refreshed_first_and_marked_sold_out_when_gone(self):
        make_listing(self.bundle, self.retailer, price="30.00", url="https://www.ebay.co.uk/itm/9?campid=1")
        api = FakeApi([])
        offers = ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("q=Prismatic+Evolutions+Booster+Bundle", api.calls[1][0])
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].availability, Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(offers[0].url, "https://www.ebay.co.uk/itm/9?campid=1")

    def test_limit_counts_products_not_results(self):
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertEqual(len(api.calls), 2)   # token plus one search


@override_settings(**KEYS)
class ImportTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.retailer = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )

    def test_import_creates_a_listing_with_postage_in_the_delivered_price(self):
        api = FakeApi([item("Pokemon Prismatic Evolutions Elite Trainer Box", price="79.99", postage="3.49")])
        with mock.patch.object(ebay, "http", api), mock.patch.object(ebay, "PAUSE", 0):
            run = run_import(self.retailer)
        self.assertEqual(run.error, "")
        listing = Listing.objects.get(retailer=self.retailer, product=self.etb)
        self.assertEqual(listing.price, Decimal("79.99"))
        self.assertEqual(listing.delivery_cost, Decimal("3.49"))
        self.assertEqual(listing.delivered_price, Decimal("83.48"))
        self.assertIn("campid=5339012345", listing.url)

    def test_a_partial_run_leaves_unchecked_products_alone(self):
        other = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle")
        kept = make_listing(other, self.retailer, price="30.00")
        with mock.patch.object(ebay, "ebay_offers", return_value=[]):
            run_import(self.retailer)
        kept.refresh_from_db()
        self.assertEqual(kept.availability, Listing.Availability.IN_STOCK)

    def test_import_runs_at_most_once_a_day(self):
        ImportRun.objects.create(retailer=self.retailer, finished_at=timezone.now() - datetime.timedelta(hours=2))
        with mock.patch.object(ebay, "http") as called:
            run = run_import(self.retailer)
        called.assert_not_called()
        self.assertEqual(run.error, "")

    def test_missing_keys_are_reported_not_raised(self):
        with override_settings(RIPRAPTOR_EBAY_APP_ID=""):
            run = run_import(self.retailer)
        self.assertIn("RIPRAPTOR_EBAY_APP_ID", run.error)

    def test_setup_shops_adds_ebay_only_with_keys(self):
        self.retailer.delete()
        out = StringIO()
        with override_settings(RIPRAPTOR_EBAY_APP_ID=""):
            call_command("setup_shops", stdout=out)
        self.assertFalse(Retailer.objects.filter(slug="ebay").exists())
        call_command("setup_shops", stdout=out)
        self.assertEqual(Retailer.objects.get(slug="ebay").source_type, Retailer.Source.EBAY)
        self.assertEqual(Product.objects.count(), 1)
