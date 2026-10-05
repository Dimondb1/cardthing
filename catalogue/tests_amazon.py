import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import amazon
from .importers import Offer, run_import
from .models import ImportRun, Listing, Product, Retailer
from .testing import make_game, make_product, make_set

KEYS = {
    "RIPRAPTOR_AMAZON_ACCESS_KEY": "AKIAEXAMPLE",
    "RIPRAPTOR_AMAZON_SECRET_KEY": "secret",
    "RIPRAPTOR_AMAZON_PARTNER_TAG": "ripraptor-21",
}


def api_item(asin, title, price="89.99", ean="0820650853500", stock="Now", free=True, condition="New"):
    return {
        "ASIN": asin,
        "DetailPageURL": f"https://www.amazon.co.uk/dp/{asin}?tag=ripraptor-21",
        "ItemInfo": {
            "Title": {"DisplayValue": title},
            "ExternalIds": {"EANs": {"DisplayValues": [ean] if ean else []}},
        },
        "Images": {"Primary": {"Large": {"URL": f"https://m.media-amazon.com/{asin}.jpg"}}},
        "Offers": {
            "Listings": [
                {
                    "Price": {"Amount": float(price), "Currency": "GBP"},
                    "Availability": {"Type": stock},
                    "Condition": {"Value": condition},
                    "DeliveryInfo": {"IsFreeShippingEligible": free},
                }
            ]
        },
    }


class SigningTests(TestCase):
    def test_headers_carry_a_version_4_signature_for_the_operation(self):
        when = datetime.datetime(2026, 10, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        path, headers = amazon.signed_headers("SearchItems", b"{}", "AKIAEXAMPLE", "secret", now=when)
        self.assertEqual(path, "/paapi5/searchitems")
        self.assertEqual(headers["x-amz-date"], "20261001T120000Z")
        self.assertEqual(headers["x-amz-target"], "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems")
        auth = headers["authorization"]
        self.assertTrue(auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/20261001/eu-west-1/ProductAdvertisingAPI/aws4_request"))
        self.assertIn("SignedHeaders=content-encoding;content-type;host;x-amz-date;x-amz-target", auth)
        self.assertRegex(auth, r"Signature=[0-9a-f]{64}$")

    def test_same_input_signs_the_same_way(self):
        when = datetime.datetime(2026, 10, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        first = amazon.signed_headers("GetItems", b'{"a":1}', "k", "s", now=when)[1]["authorization"]
        second = amazon.signed_headers("GetItems", b'{"a":1}', "k", "s", now=when)[1]["authorization"]
        self.assertEqual(first, second)
        other = amazon.signed_headers("GetItems", b'{"a":2}', "k", "s", now=when)[1]["authorization"]
        self.assertNotEqual(first, other)

    def test_missing_keys_are_a_clear_error(self):
        with self.assertRaises(amazon.AmazonError):
            amazon.credentials()


class ItemTests(TestCase):
    def test_item_becomes_an_offer_with_free_delivery(self):
        offer = amazon.item_offer(api_item("B0TEST", "Pokemon TCG Prismatic Evolutions Elite Trainer Box"), Offer)
        self.assertEqual(offer.price, Decimal("89.99"))
        self.assertEqual(offer.delivery, Decimal("0.00"))
        self.assertEqual(offer.ean, "0820650853500")
        self.assertEqual(offer.availability, Listing.Availability.IN_STOCK)
        self.assertEqual(offer.tags, ("B0TEST",))
        self.assertIn("tag=ripraptor-21", offer.url)

    def test_delivery_unknown_when_not_free(self):
        offer = amazon.item_offer(api_item("B0TEST", "Box", free=False), Offer)
        self.assertIsNone(offer.delivery)

    def test_not_available_now_is_out_of_stock(self):
        offer = amazon.item_offer(api_item("B0TEST", "Box", stock="Unavailable"), Offer)
        self.assertEqual(offer.availability, Listing.Availability.OUT_OF_STOCK)

    def test_used_only_and_priceless_items_are_skipped(self):
        self.assertIsNone(amazon.item_offer(api_item("B0TEST", "Box", condition="Used"), Offer))
        item = api_item("B0TEST", "Box")
        item["Offers"] = {}
        self.assertIsNone(amazon.item_offer(item, Offer))

    def test_other_currency_is_skipped(self):
        item = api_item("B0TEST", "Box")
        item["Offers"]["Listings"][0]["Price"]["Currency"] = "USD"
        self.assertIsNone(amazon.item_offer(item, Offer))


@override_settings(**KEYS)
class LookupTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.box = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle")
        self.calls = []

    def fake_api(self, answers):
        def api(operation, payload):
            self.calls.append((operation, payload))
            return answers.get(operation, {})
        return api

    def test_barcode_search_stores_the_asin_and_returns_the_offer(self):
        api = self.fake_api({"SearchItems": {"SearchResult": {"Items": [
            api_item("B0OTHER", "Some other tin", ean="5012345678900"),
            api_item("B0ETB", "Pokemon Scarlet and Violet Prismatic Evolutions Elite Trainer Box"),
        ]}}})
        offers = amazon.amazon_offers(None, limit=5, call_api=api, pause=0)
        self.etb.refresh_from_db()
        self.assertEqual(self.etb.amazon_asin, "B0ETB")
        self.assertIsNotNone(self.etb.amazon_checked_at)
        self.assertEqual([o.tags[0] for o in offers if o.ean == "0820650853500"], ["B0ETB"])
        search = [p for op, p in self.calls if op == "SearchItems"]
        self.assertEqual(search[0]["Keywords"], "0820650853500")
        self.assertEqual(search[0]["PartnerTag"], "ripraptor-21")
        self.assertEqual(search[0]["Marketplace"], "www.amazon.co.uk")

    def test_name_search_keeps_only_a_result_that_matches_the_product(self):
        self.etb.delete()
        api = self.fake_api({"SearchItems": {"SearchResult": {"Items": [
            api_item("B0SINGLE", "Pokemon Prismatic Evolutions Umbreon ex single card", ean=""),
            api_item("B0BUNDLE", "Pokemon TCG Scarlet and Violet Prismatic Evolutions Booster Bundle", ean=""),
        ]}}})
        offers = amazon.amazon_offers(None, limit=5, call_api=api, pause=0)
        self.box.refresh_from_db()
        self.assertEqual(self.box.amazon_asin, "B0BUNDLE")
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].title, "Pokemon TCG Scarlet and Violet Prismatic Evolutions Booster Bundle")

    def test_nothing_found_is_remembered_and_tried_last_next_time(self):
        api = self.fake_api({"SearchItems": {}})
        amazon.amazon_offers(None, limit=1, call_api=api, pause=0)
        self.assertEqual(len(self.calls), 1)
        first = self.calls[0][1]["Keywords"]
        amazon.amazon_offers(None, limit=1, call_api=api, pause=0)
        self.assertNotEqual(self.calls[1][1]["Keywords"], first)

    def test_known_products_are_refreshed_in_batches_before_new_lookups(self):
        Product.objects.filter(pk=self.etb.pk).update(amazon_asin="B0ETB")
        api = self.fake_api({
            "GetItems": {"ItemsResult": {"Items": [api_item("B0ETB", "Elite Trainer Box", price="79.00")]}},
            "SearchItems": {},
        })
        offers = amazon.amazon_offers(None, limit=2, call_api=api, pause=0)
        self.assertEqual(self.calls[0][0], "GetItems")
        self.assertEqual(self.calls[0][1]["ItemIds"], ["B0ETB"])
        self.assertEqual(offers[0].price, Decimal("79.00"))
        # One of the two calls went on the refresh, so one lookup was left.
        self.assertEqual(sum(1 for op, _ in self.calls if op == "SearchItems"), 1)


@override_settings(**KEYS)
class ImportTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.retailer = Retailer.objects.create(
            name="Amazon", slug="amazon", website="https://www.amazon.co.uk/",
            source_type=Retailer.Source.AMAZON, delivery_cost=Decimal("4.99"), free_delivery_over=Decimal("35"),
        )

    def test_import_creates_a_listing_with_amazons_tracked_link(self):
        answer = {"SearchResult": {"Items": [api_item("B0ETB", "Prismatic Evolutions Elite Trainer Box")]}}
        with mock.patch.object(amazon, "call", return_value=answer), mock.patch.object(amazon, "PAUSE", 0):
            run = run_import(self.retailer)
        self.assertEqual(run.error, "")
        listing = Listing.objects.get(retailer=self.retailer, product=self.etb)
        self.assertEqual(listing.url, "https://www.amazon.co.uk/dp/B0ETB?tag=ripraptor-21")
        self.assertEqual(listing.price, Decimal("89.99"))
        self.assertEqual(listing.delivered_price, Decimal("89.99"))

    def test_import_runs_at_most_once_a_day(self):
        ImportRun.objects.create(retailer=self.retailer, offers_found=10, finished_at=timezone.now() - datetime.timedelta(hours=2))
        with mock.patch.object(amazon, "call") as called:
            run = run_import(self.retailer)
        called.assert_not_called()
        self.assertEqual(run.error, "")

    def test_missing_keys_are_reported_not_raised(self):
        with override_settings(RIPRAPTOR_AMAZON_ACCESS_KEY=""):
            run = run_import(self.retailer)
        self.assertIn("RIPRAPTOR_AMAZON_ACCESS_KEY", run.error)

    def test_terms_page_carries_the_amazon_line_only_while_amazon_is_a_shop(self):
        html = self.client.get(reverse("web:terms")).content.decode()
        self.assertIn("As an Amazon Associate", html)
        self.retailer.is_active = False
        self.retailer.save()
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG=""):
            html = self.client.get(reverse("web:terms")).content.decode()
        self.assertNotIn("As an Amazon Associate", html)

    def test_terms_page_carries_the_amazon_line_while_the_search_link_is_tagged(self):
        self.retailer.delete()
        html = self.client.get(reverse("web:terms")).content.decode()
        self.assertIn("As an Amazon Associate", html)
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG=""):
            html = self.client.get(reverse("web:terms")).content.decode()
        self.assertNotIn("As an Amazon Associate", html)

    def test_setup_shops_adds_amazon_only_with_keys(self):
        self.retailer.delete()
        out = StringIO()
        with override_settings(RIPRAPTOR_AMAZON_ACCESS_KEY=""):
            call_command("setup_shops", stdout=out)
        self.assertFalse(Retailer.objects.filter(slug="amazon").exists())
        call_command("setup_shops", stdout=out)
        shop = Retailer.objects.get(slug="amazon")
        self.assertEqual(shop.source_type, Retailer.Source.AMAZON)
        self.assertEqual(shop.delivery_cost, Decimal("4.99"))
