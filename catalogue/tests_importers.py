import json
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .importers import apply_offers, feed_offers, run_import, shopify_offers
from .models import ImportRun, Listing, Product, Retailer, ShopProduct
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


class ImportProductsTests(TestCase):
    def test_import_products_from_csv(self):
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        make_game()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("name,game,type,set,set_code,ean,release_date\n"
                    "Prismatic Evolutions Elite Trainer Box,Pokémon,ETB,Prismatic Evolutions,PRE,0820650851230,2025-01-17\n"
                    "Nothing,No Such Game,bundle,,,,\n"
                    "Bad Date,Pokémon,bundle,,,,soon\n")
        out = StringIO()
        call_command("import_products", f.name, stdout=out)
        product = Product.objects.get(slug="prismatic-evolutions-elite-trainer-box")
        self.assertEqual(product.product_type, Product.Type.ELITE_TRAINER_BOX)
        self.assertEqual(product.product_set.code, "PRE")
        self.assertEqual(product.ean, "0820650851230")
        self.assertIn("1 products added, 0 updated.", out.getvalue())
        self.assertIn("no game called", out.getvalue())
        self.assertIn("release_date must be", out.getvalue())
        call_command("import_products", f.name, stdout=out)
        self.assertIn("0 products added, 1 updated.", out.getvalue())

    def test_non_web_links_are_not_stored(self):
        from .importers import Offer

        retailer = make_retailer("Northgate Cards")
        make_product(make_set(make_game()), ean="0820650851230")
        _found, updated, unmatched = apply_offers(retailer, [Offer(title="ETB", url="javascript:alert(1)", price=Decimal("5"), ean="0820650851230")])
        self.assertEqual(updated, 0)
        self.assertEqual(Listing.objects.count(), 0)
        self.assertIn("not a web address", unmatched[0])


class LinkMatchingTests(TestCase):
    def test_offer_matches_a_hand_added_listing_by_link(self):
        from .importers import Offer, link_key

        retailer = make_retailer("Poke Collect", source_type=Retailer.Source.SHOPIFY, source_url="https://poke-collect.example/en-gb")
        product = make_product(make_set(make_game()))  # no barcode
        make_listing(product, retailer, price="1.00", url="https://www.poke-collect.example/en-gb/products/prismatic-etb?variant=1")
        self.assertEqual(link_key("https://poke-collect.example/products/prismatic-etb/"), link_key("http://www.poke-collect.example/en-gb/products/prismatic-etb?x=1"))
        offers = [Offer(title="Prismatic ETB", url="https://poke-collect.example/products/prismatic-etb", price=Decimal("79.95"), ean="")]
        found, updated, unmatched = apply_offers(retailer, offers)
        self.assertEqual((found, updated, unmatched), (1, 1, []))
        listing = Listing.objects.get(product=product, retailer=retailer)
        self.assertEqual(listing.price, Decimal("79.95"))
        self.assertEqual(listing.url, "https://poke-collect.example/products/prismatic-etb")

    def test_unmatched_report_includes_the_link(self):
        from .importers import Offer

        retailer = make_retailer("Poke Collect")
        _f, _u, unmatched = apply_offers(retailer, [Offer(title="Mystery", url="https://x.example/products/mystery", price=Decimal("5"))])
        self.assertEqual(unmatched, ["Mystery [no barcode] https://x.example/products/mystery"])


class NameMatchingTests(TestCase):
    def setUp(self):
        game = make_game()
        pre = make_set(game)
        self.etb = make_product(pre, name="Prismatic Evolutions Elite Trainer Box")
        self.bundle = make_product(pre, name="Prismatic Evolutions Booster Bundle", product_type="bundle")
        self.box = make_product(make_set(game, name="Surging Sparks", slug="surging-sparks", code="SSP"),
                                name="Surging Sparks Booster Box", product_type="booster_box")
        self.retailer = make_retailer("Poke Collect", source_type=Retailer.Source.SHOPIFY, source_url="https://pc.example/")

    def test_scores(self):
        from .matching import score

        self.assertEqual(score(self.etb.name, "Pokemon TCG: Prismatic Evolutions Elite Trainer Box (EN)"), 100)
        self.assertEqual(score(self.etb.name, "Pokémon Prismatic Evolutions ETB"), 100)
        self.assertLess(score(self.box.name, "Surging Sparks Booster Box CASE (6 boxes)"), 60)
        self.assertLess(score(self.box.name, "Surging Sparks Booster Pack"), 100)
        self.assertLess(score(self.etb.name, "Prismatic Evolutions Booster Bundle"), 100)

    def test_import_links_confident_matches_and_queues_the_rest(self):
        page = shopify_page([
            {"handle": "pe-etb", "title": "Pokemon TCG Prismatic Evolutions Elite Trainer Box", "tags": [], "images": [{"src": "https://cdn.example/etb.jpg"}],
             "variants": [{"price": "79.95", "available": True, "barcode": ""}]},
            {"handle": "ss-case", "title": "Surging Sparks Booster Box Case", "tags": [],
             "variants": [{"price": "800", "available": True, "barcode": ""}]},
            {"handle": "ss-pack", "title": "Surging Sparks Booster Pack", "tags": [],
             "variants": [{"price": "4.50", "available": True, "barcode": ""}]},
            {"handle": "mat", "title": "Pikachu Playmat", "tags": [], "variants": [{"price": "20", "available": True, "barcode": ""}]},
        ])
        run = run_import(self.retailer, fetch=lambda url: page if "page=1" in url else shopify_page([]))
        self.assertEqual(run.listings_updated, 1)
        listing = Listing.objects.get(product=self.etb, retailer=self.retailer)
        self.assertEqual(listing.price, Decimal("79.95"))
        self.etb.refresh_from_db()
        self.assertEqual(self.etb.image_url, "https://cdn.example/etb.jpg")
        rows = {r.url: r for r in ShopProduct.objects.all()}
        self.assertEqual(rows["https://pc.example/products/pe-etb"].status, ShopProduct.Status.LINKED)
        self.assertEqual(rows["https://pc.example/products/ss-pack"].status, ShopProduct.Status.REVIEW)
        self.assertEqual(rows["https://pc.example/products/ss-pack"].suggested, self.box)
        self.assertNotIn("https://pc.example/products/mat", rows)
        self.assertIn("maybe Surging Sparks Booster Box", run.unmatched)

    def test_link_action_creates_listing_and_next_import_prices_it(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        row = ShopProduct.objects.create(retailer=self.retailer, title="SS Box", url="https://pc.example/products/ss-box",
                                         price=Decimal("120"), suggested=self.box, confidence=70)
        self.client.force_login(get_user_model().objects.create_superuser("admin", "a@example.com", "pw"))
        self.client.post(reverse("admin:catalogue_shopproduct_changelist"),
                         {"action": "link_to_suggested", "_selected_action": [row.pk]})
        self.assertTrue(Listing.objects.filter(product=self.box, retailer=self.retailer, url=row.url).exists())
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.LINKED)
        page = shopify_page([{"handle": "ss-box", "title": "SS Box", "tags": [], "variants": [{"price": "129.99", "available": True, "barcode": ""}]}])
        run_import(self.retailer, fetch=lambda url: page if "page=1" in url else shopify_page([]))
        self.assertEqual(Listing.objects.get(product=self.box, retailer=self.retailer).price, Decimal("129.99"))
