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


class CheckShopTests(TestCase):
    def test_reports_a_shopify_shop(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        page = shopify_page([{"handle": "a", "title": "Prismatic Evolutions Booster Bundle", "tags": [],
                              "variants": [{"price": "44.99", "available": True, "barcode": "0820650851230"}]}])
        out = StringIO()
        with mock.patch("catalogue.management.commands.check_shop.fetch", return_value=page):
            call_command("check_shop", "https://shop.example/", stdout=out)
        self.assertIn("Shopify shop", out.getvalue())
        self.assertIn("Variants with a barcode: 1 of 1", out.getvalue())
        self.assertIn("sealed TCG: 1", out.getvalue())

    def test_reports_a_non_shopify_shop(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        out = StringIO()
        with mock.patch("catalogue.management.commands.check_shop.fetch", return_value=b"<html>"):
            call_command("check_shop", "shop.example", stdout=out)
        self.assertIn("not a Shopify shop", out.getvalue())


class ImageAndBarcodeTests(TestCase):
    def setUp(self):
        self.retailer = make_retailer("Harbour Games", source_type=Retailer.Source.SHOPIFY, source_url="https://harbour.example/")
        self.product = make_product(make_set(make_game()), ean="0820650851230")

    def test_feed_image_is_kept_when_product_has_none(self):
        page = shopify_page([{"handle": "etb", "title": "ETB", "tags": [], "images": [{"src": "https://cdn.example/etb.jpg"}],
                              "variants": [{"price": "44.99", "available": True, "barcode": "820650851230"}]}])
        run_import(self.retailer, fetch=lambda url: page if "page=1" in url else shopify_page([]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, "https://cdn.example/etb.jpg")
        self.assertEqual(self.product.image_src, "https://cdn.example/etb.jpg")

    def test_feed_image_does_not_replace_an_existing_one(self):
        self.product.image_url = "https://cdn.example/mine.jpg"
        self.product.save()
        page = shopify_page([{"handle": "etb", "title": "ETB", "tags": [], "images": [{"src": "https://cdn.example/other.jpg"}],
                              "variants": [{"price": "44.99", "available": True, "barcode": "820650851230"}]}])
        run_import(self.retailer, fetch=lambda url: page if "page=1" in url else shopify_page([]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, "https://cdn.example/mine.jpg")

    def test_import_barcodes_from_csv(self):
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        other = make_product(self.product.product_set, name="Prismatic Evolutions Booster Bundle")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("name,ean\nprismatic evolutions booster bundle,0820650851247\nMissing Product,0820650851254\nBad,12\n")
        out = StringIO()
        call_command("import_barcodes", f.name, stdout=out)
        other.refresh_from_db()
        self.assertEqual(other.ean, "0820650851247")
        self.assertIn("Barcodes set on 1 products.", out.getvalue())
        self.assertIn("Missing Product: product not found", out.getvalue())
        self.assertIn("Bad: no valid barcode", out.getvalue())


class FeedLayoutTests(TestCase):
    def test_awin_layout(self):
        text = ("aw_deep_link,product_name,search_price,store_price,in_stock,ean,aw_image_url,delivery_cost\n"
                "https://x.example/etb,ETB,44.99,49.99,1,0820650851230,https://img.example/etb.jpg,3.99\n"
                "https://x.example/box,Box,120.00,120.00,0,0820650851247,,\n")
        offers = list(feed_offers(text))
        self.assertEqual(offers[0].price, Decimal("44.99"))
        self.assertEqual(offers[0].availability, Listing.Availability.IN_STOCK)
        self.assertEqual(offers[0].image, "https://img.example/etb.jpg")
        self.assertEqual(offers[0].delivery, Decimal("3.99"))
        self.assertEqual(offers[1].availability, Listing.Availability.OUT_OF_STOCK)

    def test_google_merchant_layout(self):
        text = "id,title,link,price,sale_price,availability,gtin,image_link\n1,ETB,https://x.example/etb,49.99 GBP,44.99 GBP,in_stock,0820650851230,https://img.example/etb.jpg\n"
        offer = list(feed_offers(text))[0]
        self.assertEqual(offer.price, Decimal("44.99"))
        self.assertEqual(offer.ean, "0820650851230")

    def test_other_currencies_are_ignored(self):
        from .importers import money

        self.assertIsNone(money("44.99 EUR"))
        self.assertEqual(money("£1,249.00"), Decimal("1249.00"))
        self.assertEqual(money("44.99 GBP"), Decimal("44.99"))
