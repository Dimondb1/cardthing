import json
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .importers import apply_offers, feed_offers, run_import, shopify_offers
from .models import ImportRun, Listing, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


def shopify_page(products):
    return json.dumps({"products": products}).encode()


class ShopifyTests(TestCase):
    def setUp(self):
        self.retailer = make_retailer(
            "Harbour Games", source_type=Retailer.Source.SHOPIFY,
            source_url="https://harbour.example/", delivery_cost=Decimal("3.49"),
            free_delivery_over=Decimal("50"),
        )
        product_set = make_set(make_game())
        self.etb = make_product(product_set, ean="0820650851230")
        self.bundle = make_product(product_set, name="Prismatic Evolutions Booster Bundle", ean="0820650851247")

    def fake_fetch(self, pages):
        calls = []

        def fetch(url):
            calls.append(url)
            page = int(url.rsplit("page=", 1)[1])
            return shopify_page(pages[page - 1] if page <= len(pages) else [])

        fetch.calls = calls
        return fetch

    def test_offers_are_matched_by_barcode_and_delivery_applied(self):
        fetch = self.fake_fetch([[
            {"handle": "pe-etb", "title": "Prismatic Evolutions ETB", "tags": [],
             "variants": [{"price": "44.99", "available": True, "barcode": "820650851230"}]},
            {"handle": "pe-bundle", "title": "Prismatic Evolutions Bundle", "tags": ["Pre-Order"],
             "variants": [{"price": "54.99", "available": True, "barcode": "820650851247"}]},
            {"handle": "mystery", "title": "Mystery Box", "tags": [],
             "variants": [{"price": "10.00", "available": True, "barcode": ""}]},
        ]])
        run = run_import(self.retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)
        self.assertEqual((run.offers_found, run.listings_updated), (3, 2))
        self.assertIn("Mystery Box [no barcode]", run.unmatched)
        etb = Listing.objects.get(product=self.etb, retailer=self.retailer)
        self.assertEqual(etb.price, Decimal("44.99"))
        self.assertEqual(etb.delivery_cost, Decimal("3.49"))
        self.assertEqual(etb.url, "https://harbour.example/products/pe-etb")
        bundle = Listing.objects.get(product=self.bundle, retailer=self.retailer)
        self.assertEqual(bundle.delivery_cost, Decimal("0.00"))
        self.assertEqual(bundle.availability, Listing.Availability.PREORDER)
        self.assertEqual(self.etb.daily_prices.get(date=timezone.localdate()).price, Decimal("48.48"))

    def test_products_missing_from_the_shop_go_out_of_stock(self):
        make_listing(self.etb, self.retailer, price="40.00")
        fetch = self.fake_fetch([[]])
        run_import(self.retailer, fetch=fetch)
        listing = Listing.objects.get(product=self.etb, retailer=self.retailer)
        self.assertEqual(listing.availability, Listing.Availability.OUT_OF_STOCK)

    def test_fetch_failure_is_recorded_not_raised(self):
        def fetch(url):
            from .importers import ImportError_
            raise ImportError_("Could not fetch")

        run = run_import(self.retailer, fetch=fetch)
        self.assertFalse(run.ok)
        self.assertIn("Could not fetch", run.error)
        self.assertEqual(ImportRun.objects.count(), 1)

    def test_pages_are_followed(self):
        fetch = self.fake_fetch([[{"handle": "a", "title": "A", "tags": [], "variants": [{"price": "1", "available": True, "barcode": ""}]}], []])
        list(shopify_offers(self.retailer, fetch=fetch))
        self.assertEqual(len(fetch.calls), 2)


class FeedTests(TestCase):
    def test_feed_columns_are_flexible(self):
        text = "product_name,gtin,aw_deep_link,price,stock_status,delivery\nETB,0820650851230,https://x.example/etb,£44.99,In Stock,2.99\n"
        offers = list(feed_offers(text))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].ean, "0820650851230")
        self.assertEqual(offers[0].price, Decimal("44.99"))
        self.assertEqual(offers[0].delivery, Decimal("2.99"))
        self.assertEqual(offers[0].availability, Listing.Availability.IN_STOCK)

    def test_apply_keeps_cheapest_variant(self):
        retailer = make_retailer("Northgate Cards")
        product = make_product(make_set(make_game()), ean="0820650851230")
        from .importers import Offer
        offers = [
            Offer(title="ETB", url="https://n.example/a", price=Decimal("50"), ean="0820650851230"),
            Offer(title="ETB (2 pack)", url="https://n.example/b", price=Decimal("45"), ean="0820650851230"),
        ]
        apply_offers(retailer, offers)
        self.assertEqual(Listing.objects.get(product=product, retailer=retailer).price, Decimal("45"))
