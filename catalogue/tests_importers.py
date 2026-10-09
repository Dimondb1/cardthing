import json
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .classify import classify
from .importers import ImportError_, apply_offers, feed_offers, run_import, shopify_offers
from .models import DailyLowestPrice, ImportRun, Listing, Product, Retailer, ShopProduct
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
            if url.endswith("/meta.json"):
                return b'{"currency": "GBP"}'
            page = int(url.rsplit("page=", 1)[1])
            return shopify_page(pages[page - 1] if page <= len(pages) else [])

        fetch.calls = calls
        return fetch

    def test_a_collection_is_read_instead_of_the_whole_shop(self):
        from io import StringIO

        from django.core.management import call_command

        self.retailer.collection = "trading-card-games"
        self.retailer.save()
        asked = []

        def fetch(url):
            asked.append(url)
            if "/collections/trading-card-games/products.json?limit=250&page=1" in url:
                return shopify_page([{"handle": "etb", "title": "Pokemon TCG: Prismatic Evolutions - Elite Trainer Box", "tags": [],
                                      "variants": [{"price": "54.99", "available": True, "barcode": "0820650851230"}]}])
            if "products.json" in url:
                return shopify_page([])
            return b"{}"

        offers = list(shopify_offers(self.retailer, fetch=fetch))
        self.assertEqual([o.ean for o in offers], ["0820650851230"])
        self.assertTrue(any("/collections/trading-card-games/products.json" in u for u in asked))
        self.assertFalse(any(u.startswith("https://harbour.example/products.json") for u in asked))
        # setup_shops gives Zatu its collection without touching a shop that has one.
        zatu = make_retailer("Zatu Games", slug="zatu-games", website="https://zatu.com/", source_type=Retailer.Source.SHOPIFY, source_url="https://zatu.com/")
        call_command("setup_shops", stdout=StringIO())
        zatu.refresh_from_db()
        self.assertEqual(zatu.collection, "trading-card-games")

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
        self.assertEqual(len([c for c in fetch.calls if "page=" in c and "/collections/" not in c]), 2)


class FeedTests(TestCase):
    def test_feed_columns_are_flexible(self):
        text = "product_name,gtin,aw_deep_link,price,stock_status,delivery\nETB,0820650851230,https://x.example/etb,£44.99,In Stock,2.99\n"
        offers = list(feed_offers(text))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].ean, "0820650851230")
        self.assertEqual(offers[0].price, Decimal("44.99"))
        self.assertEqual(offers[0].delivery, Decimal("2.99"))
        self.assertEqual(offers[0].availability, Listing.Availability.IN_STOCK)

    def test_google_shopping_xml_feed_is_read_with_gtin_first(self):
        text = """<?xml version="1.0"?>
<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel><title>Shop</title>
<item><title>Pokemon TCG: Prismatic Evolutions - Elite Trainer Box</title><link>https://x.example/pev-etb</link>
<g:price>59.99 GBP</g:price><g:sale_price>54.99 GBP</g:sale_price><g:gtin>0820650851230</g:gtin>
<g:availability>in stock</g:availability><g:image_link>https://x.example/etb.jpg</g:image_link>
<g:shipping><g:country>GB</g:country><g:price>2.49 GBP</g:price></g:shipping></item>
<item><title>No price</title><link>https://x.example/none</link></item>
</channel></rss>"""
        offers = list(feed_offers(text))
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual((offer.price, offer.ean, offer.delivery, offer.availability), (Decimal("54.99"), "0820650851230", Decimal("2.49"), Listing.Availability.IN_STOCK))
        self.assertEqual((offer.url, offer.image), ("https://x.example/pev-etb", "https://x.example/etb.jpg"))

    def test_a_broken_xml_feed_is_an_import_error(self):
        with self.assertRaises(ImportError_):
            list(feed_offers("<rss><channel><item>"))

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

    def test_a_box_listed_with_its_pack_count_links_to_our_box(self):
        from .importers import Offer, apply_offers

        offer = Offer(title="Pokemon TCG: Surging Sparks Booster Box (36x Packs)", url="https://pc.example/products/ssp-box",
                      price=Decimal("109.99"), availability=Listing.Availability.IN_STOCK)
        found, updated, unmatched = apply_offers(self.retailer, [offer], complete=False)
        self.assertEqual((updated, unmatched), (1, []))
        self.assertEqual(Listing.objects.get(retailer=self.retailer).product, self.box)

    def test_a_one_pack_variant_on_a_box_page_never_prices_the_box(self):
        from .importers import apply_offers, product_offers

        page = {"handle": "ssp-box", "title": "Pokemon TCG: Surging Sparks Booster Box (36x Packs)", "tags": [], "images": [],
                "variants": [{"id": 1, "title": "1 Pack", "price": "5.50", "available": True, "barcode": ""},
                             {"id": 2, "title": "Booster Box", "price": "154.25", "available": True, "barcode": ""}]}
        found, updated, unmatched = apply_offers(self.retailer, product_offers("https://pc.example", page), complete=False)
        listing = Listing.objects.get(retailer=self.retailer)
        self.assertEqual((listing.product, listing.price, listing.url), (self.box, Decimal("154.25"), "https://pc.example/products/ssp-box?variant=2"))
        self.assertTrue(any("a variant of another kind" in line for line in unmatched))

    def box_page(self, *variants):
        return {"handle": "dri-box", "title": "Pokemon TCG - Scarlet & Violet - Destined Rivals - Booster Box (36x Packs)",
                "tags": [], "images": [],
                "variants": [{"id": n, "title": title, "price": price, "available": available, "barcode": ""}
                             for n, (title, price, available) in enumerate(variants, start=1)]}

    def read_twice(self, first, second):
        from .importers import apply_offers, product_offers

        rivals = make_product(self.box.product_set, name="Destined Rivals Booster Box", slug="dri-box", product_type="booster_box")
        for page in (first, second):
            apply_offers(self.retailer, product_offers("https://pc.example", page), complete=False)
        return Listing.objects.get(retailer=self.retailer, product=rivals)

    def test_a_half_box_count_variant_never_prices_the_box(self):
        page = self.box_page(("18 Packs", "79.99", True), ("36 Packs", "154.25", True))
        listing = self.read_twice(page, page)
        self.assertEqual((listing.price, listing.url), (Decimal("154.25"), "https://pc.example/products/dri-box?variant=2"))

    def test_a_half_box_or_a_case_never_takes_over_the_box_on_a_later_read(self):
        half = self.box_page(("Booster Box", "154.25", True), ("Half Box", "80.00", True))
        self.assertEqual(self.read_twice(half, half).price, Decimal("154.25"))
        Listing.objects.all().delete()
        Product.objects.filter(slug="dri-box").delete()
        case_in = self.box_page(("Booster Box", "154.25", True), ("Case (6 Boxes)", "899.00", True))
        case_box_sold_out = self.box_page(("Booster Box", "154.25", False), ("Case (6 Boxes)", "899.00", True))
        listing = self.read_twice(case_in, case_box_sold_out)
        self.assertEqual((listing.price, listing.availability), (Decimal("154.25"), Listing.Availability.OUT_OF_STOCK))

    def test_a_skipped_variant_leaves_no_linked_row_and_languages_still_count(self):
        from .importers import apply_offers, product_offers

        page = self.box_page(("1 Pack", "5.50", True), ("English", "154.25", True))
        apply_offers(self.retailer, product_offers("https://pc.example", page), complete=False)
        self.assertFalse(ShopProduct.objects.filter(retailer=self.retailer, price=Decimal("5.50")).exists())
        self.assertEqual(Listing.objects.get(retailer=self.retailer).price, Decimal("154.25"))

    def test_a_page_with_one_variant_is_that_product_whatever_its_count(self):
        from .importers import apply_offers, product_offers

        bundle = make_product(self.box.product_set, name="Surging Sparks Booster Bundle", slug="ssp-bundle", product_type="bundle")
        page = {"handle": "ssp-bundle", "title": "Pokemon Surging Sparks Booster Bundle", "tags": [], "images": [],
                "variants": [{"id": 7, "title": "6 Packs", "price": "29.99", "available": True, "barcode": ""}]}
        apply_offers(self.retailer, product_offers("https://pc.example", page), complete=False)
        self.assertEqual(Listing.objects.get(retailer=self.retailer).product, bundle)

    def pe_box_page(self, *variants):
        return {"handle": "pe-box", "title": "Pokemon Prismatic Evolutions Booster Box", "tags": [], "images": [],
                "variants": [{"id": n, "title": title, "price": price, "available": True, "barcode": ""}
                             for n, (title, price) in enumerate(variants, start=1)]}

    def pe_box(self):
        return make_product(self.etb.product_set, name="Prismatic Evolutions Booster Box", slug="pe-box", product_type="booster_box")

    def test_a_one_pack_variant_beside_the_box_never_prices_the_box(self):
        from .importers import apply_offers, product_offers

        box = self.pe_box()
        offers = list(product_offers("https://pc.example", self.pe_box_page(("1 Pack", "4.99"), ("Booster Box", "150.00"))))
        self.assertEqual([(o.variant, o.page_title, o.variants) for o in offers],
                         [("1 Pack", "Pokemon Prismatic Evolutions Booster Box", 2),
                          ("Booster Box", "Pokemon Prismatic Evolutions Booster Box", 2)])
        apply_offers(self.retailer, offers, complete=False)
        listing = Listing.objects.get(retailer=self.retailer)
        self.assertEqual((listing.product, listing.price, listing.url), (box, Decimal("150.00"), "https://pc.example/products/pe-box?variant=2"))
        self.assertFalse(ShopProduct.objects.filter(retailer=self.retailer, price=Decimal("4.99")).exists())

    def test_a_page_with_only_a_one_pack_variant_never_prices_the_box(self):
        from .importers import apply_offers, product_offers

        self.pe_box()
        products = Product.objects.count()
        _found, updated, unmatched = apply_offers(self.retailer, product_offers("https://pc.example", self.pe_box_page(("1 Pack", "4.99"))),
                                                  complete=False)
        self.assertEqual(updated, 0)
        self.assertFalse(Listing.objects.filter(retailer=self.retailer).exists())
        # Not even a likely match, so nothing waits for the owner, and no second box is made from it.
        self.assertFalse(ShopProduct.objects.filter(retailer=self.retailer).exists())
        self.assertEqual(Product.objects.count(), products)
        self.assertEqual(unmatched, ["Pokemon Prismatic Evolutions Booster Box (1 Pack) [no barcode] https://pc.example/products/pe-box?variant=1"])

    def test_a_page_with_only_a_one_pack_variant_makes_no_box(self):
        from .importers import apply_offers, product_offers

        products = Product.objects.count()
        apply_offers(self.retailer, product_offers("https://pc.example", self.pe_box_page(("1 Pack", "4.99"))), complete=False)
        self.assertEqual(Product.objects.count(), products)
        self.assertFalse(Listing.objects.filter(retailer=self.retailer).exists())
        self.assertFalse(ShopProduct.objects.filter(retailer=self.retailer).exists())

    def test_a_box_page_left_with_only_its_pack_variant_stops_pricing_the_box(self):
        from .importers import apply_offers, product_offers

        box = self.pe_box()
        apply_offers(self.retailer, product_offers("https://pc.example", self.pe_box_page(("1 Pack", "4.99"), ("Booster Box", "150.00"))))
        # The shop deletes the box variant: the pack shares the page's address, but never takes over the box's listing.
        _found, _updated, unmatched = apply_offers(self.retailer, product_offers("https://pc.example", self.pe_box_page(("1 Pack", "4.99"))))
        listing = Listing.objects.get(retailer=self.retailer)
        self.assertEqual((listing.product, listing.price, listing.url, listing.availability),
                         (box, Decimal("150.00"), "https://pc.example/products/pe-box?variant=2", Listing.Availability.OUT_OF_STOCK))
        self.assertEqual(unmatched, ["Pokemon Prismatic Evolutions Booster Box (1 Pack) [no barcode] https://pc.example/products/pe-box?variant=1"])

    def test_variants_of_two_kinds_on_one_page_each_price_their_own_product(self):
        from .importers import apply_offers, product_offers

        box = self.pe_box()
        page = {**self.pe_box_page(("Booster Box", "150.00"), ("Elite Trainer Box", "60.00")), "title": "Pokemon Prismatic Evolutions"}
        for _read in range(2):
            # The second read finds both by the address they share, which names only one of the two products.
            apply_offers(self.retailer, product_offers("https://pc.example", page))
        self.assertEqual(set(Listing.objects.filter(retailer=self.retailer).values_list("product", "price", "url")),
                         {(box.pk, Decimal("150.00"), "https://pc.example/products/pe-box?variant=1"),
                          (self.etb.pk, Decimal("60.00"), "https://pc.example/products/pe-box?variant=2")})

    def test_a_box_sold_with_extra_packs_is_not_the_box(self):
        from .importers import Offer, apply_offers

        offer = Offer(title="Pokemon TCG: Surging Sparks Booster Box (36x Packs) + 6x Booster Packs", url="https://pc.example/products/ssp-bundle",
                      price=Decimal("189.99"), availability=Listing.Availability.IN_STOCK)
        apply_offers(self.retailer, [offer], complete=False)
        self.assertFalse(Listing.objects.filter(retailer=self.retailer, product=self.box).exists())

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
        # "Surging Sparks Booster Pack" names no game, so it is neither created nor queued.
        self.assertFalse(Product.objects.filter(name__icontains="Booster Pack").exists())
        self.assertNotIn("https://pc.example/products/ss-pack", rows)
        # A case is not something we list, and a playmat is not a TCG product.
        self.assertFalse(Product.objects.filter(name__icontains="case").exists())
        self.assertNotIn("https://pc.example/products/mat", rows)

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


class WebsiteScraperTests(TestCase):
    PAGE = """<html><head><title>Shop | ETB</title>
    <script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Shop"},
    {"@type":"Product","name":"Prismatic Evolutions Elite Trainer Box","gtin13":"0820650851230","image":["https://img.example/etb.jpg"],
     "offers":{"@type":"Offer","price":"84.99","priceCurrency":"GBP","availability":"https://schema.org/PreOrder","url":"https://shop.example/p/etb"}}]}</script>
    </head><body></body></html>"""
    OG = """<html><head><meta property="og:title" content="Surging Sparks Booster Box"><meta property="product:price:amount" content="139.99">
    <meta property="product:price:currency" content="GBP"><meta property="og:image" content="https://img.example/ss.jpg"></head></html>"""

    def fake_fetch(self, url):
        if url.endswith("/sitemap.xml"):
            return b"<sitemapindex><sitemap><loc>https://shop.example/sitemap-products.xml</loc></sitemap></sitemapindex>"
        if url.endswith("sitemap-products.xml"):
            return b"<urlset><url><loc>https://shop.example/about</loc></url><url><loc>https://shop.example/p/etb</loc></url><url><loc>https://shop.example/product/ss-box</loc></url></urlset>"
        if url.endswith("/p/etb"):
            return self.PAGE.encode()
        if url.endswith("/product/ss-box"):
            return self.OG.encode()
        if url.endswith("/about"):
            return b"<html><body>About us</body></html>"
        from .importers import ImportError_
        raise ImportError_("no " + url)

    def test_sitemap_and_page_data_become_offers(self):
        from .importers import website_offers

        retailer = make_retailer("Shop", source_type=Retailer.Source.WEBSITE, source_url="https://shop.example/")
        offers = list(website_offers(retailer, fetch=self.fake_fetch, pause=0))
        titles = {o.title: o for o in offers}
        self.assertEqual(set(titles), {"Prismatic Evolutions Elite Trainer Box", "Surging Sparks Booster Box"})
        etb = titles["Prismatic Evolutions Elite Trainer Box"]
        self.assertEqual((etb.price, etb.ean, etb.availability, etb.image), (Decimal("84.99"), "0820650851230", Listing.Availability.PREORDER, "https://img.example/etb.jpg"))
        self.assertEqual(titles["Surging Sparks Booster Box"].price, Decimal("139.99"))

    def test_run_import_for_a_website_retailer(self):
        retailer = make_retailer("Shop", source_type=Retailer.Source.WEBSITE, source_url="https://shop.example/")
        product = make_product(make_set(make_game()), ean="0820650851230")
        from unittest import mock

        with mock.patch("catalogue.importers.time.sleep", lambda s: None):
            run = run_import(retailer, fetch=self.fake_fetch)
        self.assertTrue(run.ok, run.error)
        self.assertEqual(Listing.objects.get(product=product, retailer=retailer).price, Decimal("84.99"))


class AutoCatalogueTests(TestCase):
    def test_new_sealed_products_are_created_and_shared_across_shops(self):
        shop_a = make_retailer("Shop A", source_type=Retailer.Source.SHOPIFY, source_url="https://a.example/")
        shop_b = make_retailer("Shop B", source_type=Retailer.Source.SHOPIFY, source_url="https://b.example/")
        page_a = shopify_page([
            {"handle": "ss-box", "title": "Pokemon - Surging Sparks - Booster Box (36 Packs)", "product_type": "Booster Box", "vendor": "Pokemon", "tags": [],
             "images": [{"src": "https://a.example/ss.jpg"}], "variants": [{"price": "139.99", "available": True, "barcode": ""}]},
            {"handle": "single", "title": "Pikachu ex 057/131", "product_type": "Single Card", "vendor": "Pokemon", "tags": [],
             "variants": [{"price": "5", "available": True, "barcode": ""}]},
        ])
        page_b = shopify_page([
            {"handle": "surging", "title": "Pokémon TCG: Surging Sparks Booster Box", "product_type": "Trading Cards", "vendor": "Pokemon", "tags": [],
             "variants": [{"price": "134.95", "available": True, "barcode": ""}]},
        ])
        run_import(shop_a, fetch=lambda url: page_a if "page=1" in url else shopify_page([]))
        run_import(shop_b, fetch=lambda url: page_b if "page=1" in url else shopify_page([]))
        self.assertEqual(Product.objects.count(), 1)
        product = Product.objects.get()
        self.assertEqual(product.name, "Surging Sparks Booster Box")
        self.assertEqual(product.game.slug, "pokemon")
        self.assertEqual(product.image_url, "https://a.example/ss.jpg")
        self.assertEqual(product.listings.count(), 2)
        self.assertEqual(Listing.objects.filter(product=product).order_by("delivered_price").first().retailer, shop_b)

    def test_a_new_product_is_born_in_the_set_its_name_names(self):
        game = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        phoenix = make_set(game, name="Immortal Phoenix", slug="immortal-phoenix", code="")
        shop = make_retailer("Shop A", source_type=Retailer.Source.SHOPIFY, source_url="https://a.example/")
        page = shopify_page([
            {"handle": "ip-box", "title": "Yu-Gi-Oh! Immortal Phoenix Booster Box (24 Packs)", "product_type": "Booster Box",
             "vendor": "Konami", "tags": [], "variants": [{"price": "79.99", "available": True, "barcode": ""}]},
        ])
        run_import(shop, fetch=lambda url: page if "page=1" in url else shopify_page([]))
        self.assertEqual(Product.objects.get().product_set, phoenix)


class SetupShopsTests(TestCase):
    def test_setup_shops_is_idempotent_and_renames(self):
        from io import StringIO

        from django.core.management import call_command

        from .management.commands.setup_shops import SHOPS

        Retailer.objects.create(slug="totalcardsnet", name="Totalcards", website="https://totalcards.net/")
        dollars = Retailer.objects.create(slug="poke-collect", name="Poke-Collect", website="https://poke-collect.com/")
        make_listing(make_product(make_set(make_game())), dollars)
        call_command("setup_shops", stdout=StringIO())
        call_command("setup_shops", stdout=StringIO())
        self.assertEqual(Retailer.objects.count(), len(SHOPS) + 1)
        self.assertEqual(Retailer.objects.get(website="https://totalcards.net/").name, "Total Cards")
        self.assertEqual(Retailer.objects.get(name="Gathering Games").delivery_cost, Decimal("3.99"))
        dollars.refresh_from_db()
        self.assertFalse(dollars.is_active)
        self.assertEqual(dollars.listings.count(), 0)


class WebsiteCrawlIsNotCompleteTests(TestCase):
    def test_products_beyond_the_page_cap_keep_their_stock_state(self):
        retailer = make_retailer("Shop", source_type=Retailer.Source.WEBSITE, source_url="https://shop.example/")
        pset = make_set(make_game())
        unseen = make_product(pset, name="Surging Sparks Booster Box", product_type="booster_box")
        make_listing(unseen, retailer, availability="in_stock", url="https://shop.example/surging-sparks-booster-box")

        def fetch(url):
            if url.endswith("/sitemap.xml"):
                return b"<urlset><url><loc>https://shop.example/pokemon-prismatic-evolutions-elite-trainer-box</loc></url></urlset>"
            if url.endswith("elite-trainer-box"):
                return (b'<script type="application/ld+json">{"@type": "Product", "name": "Prismatic Evolutions Elite Trainer Box",'
                        b' "offers": {"price": "84.99", "priceCurrency": "GBP"}}</script>')
            raise ImportError_("missing")

        from unittest import mock

        with mock.patch("catalogue.importers.time.sleep", lambda s: None):
            run = run_import(retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)
        self.assertEqual(Listing.objects.get(product=unseen).availability, "in_stock")


class UnreachableWebsiteTests(TestCase):
    """A website shop that answers nothing is a failed read that backs off, not a read that found nothing."""

    def setUp(self):
        self.shop = make_retailer("Shop", source_type=Retailer.Source.WEBSITE, source_url="https://shop.example/")

    def test_a_shop_that_answers_nothing_fails_its_read_and_backs_off(self):
        from . import crawl

        def fetch(url):
            raise ImportError_(f"Could not fetch {url}: <urlopen error [Errno 111] Connection refused>")

        began = timezone.now()
        run = run_import(self.shop, fetch=fetch)
        self.assertIn("Connection refused", run.error)
        self.assertFalse(crawl.finish_read(self.shop, run, began, 1.0, now=began))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.error_streak, 1)
        self.assertGreater(self.shop.backoff_until, began)
        self.assertIsNone(self.shop.last_ok_at)

    def test_a_shop_that_answers_robots_but_has_no_sitemap_still_reads_as_empty(self):
        def fetch(url):
            if url.endswith("/robots.txt"):
                return b"User-agent: *\n"
            raise ImportError_(f"Could not fetch {url}: HTTP Error 404")

        run = run_import(self.shop, fetch=fetch)
        self.assertEqual(run.error, "")
        self.assertEqual(run.offers_found, 0)

    def test_discover_shops_still_gets_an_empty_list_for_a_site_that_answers_nothing(self):
        from .importers import sitemap_urls

        def fetch(url):
            raise ImportError_("Connection refused")

        self.assertEqual(sitemap_urls("https://shop.example", fetch=fetch), [])


class SitemapRankingTests(TestCase):
    def test_pages_are_ranked_by_what_their_address_says(self):
        from .importers import page_rank

        self.assertEqual(page_rank("https://shop.example/pokemon-prismatic-evolutions-elite-trainer-box"), 0)
        self.assertEqual(page_rank("https://shop.example/products/12345"), 1)
        self.assertEqual(page_rank("https://shop.example/about-us"), 2)
        # Names a game but reads as a single card or accessory: not fetched at all.
        self.assertIsNone(page_rank("https://shop.example/magic-the-gathering-caged-sun-mystery-booster"))
        self.assertIsNone(page_rank("https://shop.example/pokemon-astral-radiance-142-189-gapejaw-bog-prize-pack-league-promo-non-holo"))
        self.assertIsNone(page_rank("https://shop.example/ultra-pro-pokemon-pikachu-playmat"))

    def test_blog_and_guide_pages_are_skipped_when_the_sitemap_marks_products(self):
        from .importers import sitemap_urls

        products = b"".join(b"<url><loc>https://shop.example/products/pokemon-set-%d-booster-box</loc></url>" % i for i in range(60))
        guides = b"".join(b"<url><loc>https://shop.example/guides/best-booster-box-%d</loc></url>" % i for i in range(40))

        def fetch(url):
            if url.endswith("/sitemap.xml"):
                return b"<urlset>" + guides + products + b"</urlset>"
            raise ImportError_("missing")

        urls = sitemap_urls("https://shop.example", fetch=fetch)
        self.assertEqual(len(urls), 60)
        self.assertTrue(all("/products/" in u for u in urls))

    def test_page_address_words_travel_with_the_offer_as_a_tag(self):
        from .importers import website_offers

        def fetch(url):
            if url.endswith("/sitemap.xml"):
                return b"<urlset><url><loc>https://shop.example/pokemon-kyurem-v-collection-box</loc></url></urlset>"
            if url.endswith("kyurem-v-collection-box"):
                return (
                    b'<script type="application/ld+json">{"@type": "Product", "name": "Kyurem V Collection Box",'
                    b' "offers": {"price": "79.95", "priceCurrency": "GBP"}}</script>'
                )
            raise ImportError_("missing")

        retailer = make_retailer("Shop", source_type=Retailer.Source.WEBSITE, source_url="https://shop.example/")
        [offer] = website_offers(retailer, fetch=fetch, pause=0)
        self.assertEqual(offer.tags, ("pokemon kyurem v collection box",))
        self.assertEqual(classify(offer.title, tags=offer.tags, price=offer.price).game, "pokemon")

    def test_every_sitemap_file_is_read_before_the_page_limit_applies(self):
        from .importers import sitemap_urls

        def fetch(url):
            if url.endswith("/sitemap.xml"):
                return b"<sitemapindex><sitemap><loc>https://shop.example/s1.xml</loc></sitemap><sitemap><loc>https://shop.example/s2.xml</loc></sitemap></sitemapindex>"
            if url.endswith("s1.xml"):
                return b"<urlset>" + b"".join(b"<url><loc>https://shop.example/funko-pop-%d</loc></url>" % i for i in range(5)) + b"</urlset>"
            if url.endswith("s2.xml"):
                return b"<urlset><url><loc>https://shop.example/pokemon-surging-sparks-booster-box</loc></url></urlset>"
            raise ImportError_("missing")

        urls = sitemap_urls("https://shop.example", fetch=fetch, limit=3)
        self.assertEqual(urls[0], "https://shop.example/pokemon-surging-sparks-booster-box")
        self.assertEqual(len(urls), 3)

    def test_page_titles_have_html_entities_decoded(self):
        from .importers import page_offer

        html = '<script type="application/ld+json">{"@type": "Product", "name": "St. Elmo&#039;s Pay", "offers": {"price": "9.99", "priceCurrency": "GBP"}}</script>'
        self.assertEqual(page_offer("https://shop.example/x", html).title, "St. Elmo's Pay")

    def test_sitemap_lists_sealed_looking_pages_first_and_drops_singles(self):
        from .importers import sitemap_urls

        def fetch(url):
            if url.endswith("/sitemap.xml"):
                return (
                    b"<urlset>"
                    b"<url><loc>https://shop.example/magic-the-gathering-caged-sun-mystery-booster</loc></url>"
                    b"<url><loc>https://shop.example/products/9</loc></url>"
                    b"<url><loc>https://shop.example/pokemon-surging-sparks-booster-box</loc></url>"
                    b"<url><loc>https://shop.example/delivery</loc></url>"
                    b"</urlset>"
                )
            raise ImportError_("missing")

        self.assertEqual(
            sitemap_urls("https://shop.example", fetch=fetch),
            [
                "https://shop.example/pokemon-surging-sparks-booster-box",
                "https://shop.example/products/9",
                "https://shop.example/delivery",
            ],
        )


class PageStockTests(TestCase):
    def test_control_characters_in_json_ld_do_not_hide_a_sold_out_page(self):
        from .importers import page_offer

        html = ('<script type="application/ld+json">{"@type": "Product", "name": "Powercode Link Structure Deck", '
                '"description": "line one\tline two", "offers": [{"price": "13.95", "priceCurrency": "GBP", '
                '"availability": "http://schema.org/OutOfStock"}]}</script>'
                '<meta property="product:price:amount" content="13.95"><button class="add-to-cart" disabled>Add to Cart</button>')
        offer = page_offer("https://shop.example/x", html)
        self.assertEqual((offer.title, offer.price, offer.availability), ("Powercode Link Structure Deck", Decimal("13.95"), "out_of_stock"))

    def test_a_disabled_add_to_cart_button_means_sold_out(self):
        from .importers import page_stock_hint

        self.assertEqual(page_stock_hint('<button id="form-action-addToCart" disabled>Add to Cart</button>'), "outofstock")
        self.assertEqual(page_stock_hint('"instock":false, "stock_message": "Out of stock"'), "outofstock")
        self.assertEqual(page_stock_hint('<button>Add to Cart</button> In stock'), "")

    def test_shopify_variant_links_land_on_the_variant(self):
        retailer = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")

        def fetch(url):
            if url.endswith("/meta.json"):
                return b'{"currency": "GBP"}'
            if "/collections/" in url:
                return b'{"products": []}'
            if "page=1" in url:
                return json.dumps({"products": [{
                    "title": "Marvel Super Heroes Commander Deck", "handle": "marvel-commander-deck", "tags": [],
                    "variants": [{"id": 111, "title": "Avengers Assemble", "price": "69.99", "available": True},
                                 {"id": 222, "title": "Doom Prevails", "price": "69.99", "available": True}],
                }, {
                    "title": "Surging Sparks Booster Box", "handle": "surging-sparks", "tags": [],
                    "variants": [{"id": 333, "title": "Default Title", "price": "139.99", "available": True}],
                }]}).encode()
            return b'{"products": []}'

        offers = list(shopify_offers(retailer, fetch=fetch))
        self.assertEqual([o.url for o in offers], [
            "https://shop.example/products/marvel-commander-deck?variant=111",
            "https://shop.example/products/marvel-commander-deck?variant=222",
            "https://shop.example/products/surging-sparks",
        ])


class SessionFetchTests(TestCase):
    def test_the_session_address_is_opened_first_and_its_cookie_kept(self):
        import http.server
        import threading

        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Cookie", "")))
                self.send_response(200)
                if self.path.startswith("/changecurrency"):
                    self.send_header("Set-Cookie", "currency=GBP; Path=/")
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            from .importers import session_fetch

            fetch = session_fetch(f"{base}/changecurrency/3?returnUrl=%2F")
            self.assertEqual(fetch(f"{base}/product-one"), b"ok")
            fetch(f"{base}/product-two")
        finally:
            server.shutdown()
        self.assertEqual(seen[0][0], "/changecurrency/3?returnUrl=%2F")
        self.assertEqual(seen[1], ("/product-one", "currency=GBP"))
        self.assertEqual(seen[2], ("/product-two", "currency=GBP"))

    def test_a_retailer_with_a_session_address_is_read_through_it(self):
        from .importers import retailer_fetch

        plain = make_retailer("Plain", source_type=Retailer.Source.WEBSITE, source_url="https://a.example/")
        session = make_retailer("Session", source_type=Retailer.Source.WEBSITE, source_url="https://b.example/", session_url="https://b.example/changecurrency/3")
        from . import importers

        self.assertIs(retailer_fetch(plain), importers.fetch)
        self.assertIsNot(retailer_fetch(session), importers.fetch)
        self.assertIs(retailer_fetch(session, fetch=len), len)


class SafeUrlTests(TestCase):
    def test_accented_addresses_are_percent_encoded(self):
        from .importers import safe_url

        self.assertEqual(safe_url("https://shop.example/product/pokémon-tcg-‘151’-booster-box?x=é"),
                         "https://shop.example/product/pok%C3%A9mon-tcg-%E2%80%98151%E2%80%99-booster-box?x=%C3%A9")
        self.assertEqual(safe_url("https://shop.example/products.json?limit=250&page=2"), "https://shop.example/products.json?limit=250&page=2")


class CurrencyGuardTests(TestCase):
    def test_a_shop_pricing_in_dollars_is_refused(self):
        retailer = make_retailer("US Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://us.example/")

        def fetch(url):
            if url.endswith("/meta.json"):
                return b'{"name": "US Shop", "currency": "USD", "country": "US"}'
            return b'{"products": [{"title": "Pokemon Surging Sparks Booster Box", "handle": "x", "variants": [{"price": "99.00", "available": true}]}]}'

        run = run_import(retailer, fetch=fetch)
        self.assertIn("USD", run.error)
        self.assertEqual(Listing.objects.filter(retailer=retailer).count(), 0)

    def test_a_shop_without_meta_json_is_still_imported(self):
        retailer = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")

        def fetch(url):
            if url.endswith("/meta.json"):
                raise ImportError_("404")
            if "page=1" in url:
                return b'{"products": [{"title": "Pokemon Surging Sparks Booster Box", "handle": "x", "variants": [{"price": "99.00", "available": true}]}]}'
            return b'{"products": []}'

        run = run_import(retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)


class PreorderCollectionTests(TestCase):
    def test_products_in_the_shops_preorder_collection_are_preorders(self):
        retailer = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")
        product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", ean="0820650851230")

        def fetch(url):
            if url.endswith("/meta.json"):
                return b'{"currency": "GBP"}'
            if "/collections/pre-order/products.json" in url and "page=1" in url:
                return b'{"products": [{"handle": "surging-sparks-booster-box"}]}'
            if "/collections/" in url:
                return b'{"products": []}'
            if "page=1" in url:
                return json.dumps({"products": [{
                    "title": "Surging Sparks Booster Box", "handle": "surging-sparks-booster-box", "tags": [],
                    "variants": [{"price": "139.99", "available": True, "barcode": "0820650851230"}],
                }]}).encode()
            return b'{"products": []}'

        run = run_import(retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)
        self.assertEqual(Listing.objects.get(product=product).availability, Listing.Availability.PREORDER)


class PreorderTagTests(TestCase):
    def run_shop(self, products, collections=None, collection_products=None):
        retailer = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")

        def fetch(url):
            if url.endswith("/meta.json"):
                return b'{"currency": "GBP"}'
            if url.endswith("/collections.json?limit=250"):
                return json.dumps({"collections": [{"handle": h} for h in (collections or [])]}).encode()
            for handle, items in (collection_products or {}).items():
                if f"/collections/{handle}/products.json" in url and "page=1" in url:
                    return json.dumps({"products": [{"handle": i} for i in items]}).encode()
            if "/collections/" in url:
                return b'{"products": []}'
            if "page=1" in url:
                return json.dumps({"products": products}).encode()
            return b'{"products": []}'

        run = run_import(retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)
        return retailer

    def test_an_app_trigger_tag_does_not_make_an_in_stock_product_a_preorder(self):
        product = make_product(make_set(make_game()), name="EFL Premium Box", ean="0820650851230")
        self.run_shop([{
            "title": "Panini EFL Premium Box", "handle": "efl-premium-box",
            "tags": ["Pre-Order - Inventory Trigger", "out-of-stock", "panini"],
            "variants": [{"price": "169.95", "available": True, "barcode": "0820650851230"}],
        }])
        self.assertEqual(Listing.objects.get(product=product).availability, Listing.Availability.IN_STOCK)

    def test_a_plain_preorder_tag_still_counts(self):
        product = make_product(make_set(make_game()), name="EFL Premium Box", ean="0820650851230")
        self.run_shop([{
            "title": "Panini EFL Premium Box", "handle": "efl-premium-box", "tags": ["Pre-order"],
            "variants": [{"price": "169.95", "available": True, "barcode": "0820650851230"}],
        }])
        self.assertEqual(Listing.objects.get(product=product).availability, Listing.Availability.PREORDER)

    def test_a_collection_that_excludes_preorders_is_not_a_preorder_list(self):
        product = make_product(make_set(make_game()), name="Supreme Darkness Booster Pack", ean="4012927154422")
        self.run_shop(
            [{
                "title": "Yu-Gi-Oh! Supreme Darkness Booster Pack", "handle": "supreme-darkness-booster-pack", "tags": ["TCG"],
                "variants": [{"price": "3.49", "available": True, "barcode": "4012927154422"}],
            }],
            collections=["all-products-excluding-pre-orders", "pre-order", "non-pre-order-items"],
            collection_products={
                "all-products-excluding-pre-orders": ["supreme-darkness-booster-pack"],
                "non-pre-order-items": ["supreme-darkness-booster-pack"],
            },
        )
        self.assertEqual(Listing.objects.get(product=product).availability, Listing.Availability.IN_STOCK)

    def test_every_collection_named_for_preorders_is_read(self):
        product = make_product(make_set(make_game()), name="EFL Premium Box", ean="0820650851230")
        self.run_shop(
            [{
                "title": "Panini EFL Premium Box", "handle": "efl-premium-box", "tags": [],
                "variants": [{"price": "169.95", "available": True, "barcode": "0820650851230"}],
            }],
            collections=["all-pre-order-items", "panini", "pokemon-pre-orders"],
            collection_products={"all-pre-order-items": ["efl-premium-box"]},
        )
        self.assertEqual(Listing.objects.get(product=product).availability, Listing.Availability.PREORDER)


class MatchKeyTests(TestCase):
    def test_same_product_under_different_shop_names_shares_a_key(self):
        from .matching import match_key

        same = [
            ("Scarlet & Violet 8 Surging Sparks Booster Box", "Surging Sparks Booster Box 36 Packs"),
            ("Scarlet & Violet Surging Sparks Booster Box", "SV8 Surging Sparks Booster Box"),
            ("SV Prismatic Evolutions Booster Pack", "Prismatic Evolutions Booster Pack"),
            ("Destined Rivals Elite Trainer Box", "Destined Rivals Elite Trainer Box (ETB) Sealed TCG Collection"),
            ("Journey Together Booster Bundle", "Journey Together Booster Bundle Scarlet & Violet Pokémon TCG English"),
            ("Royal Blood Booster Box (OP-10)", "Royal Blood Booster Box"),
            ("Commander Legends: Battle for Baldur's Gate Bundle", "Commander Legends Battle For Baldurs Gate Bundle"),
            ("Marvel's Spider Man Bundle", "Universes Beyond Marvel's Spider Man Bundle Box"),
            ("Prismatic Evolutions ETB", "Prismatic Evolutions Elite Trainer Box"),
            ("Twilight of the Republic Booster", "Twilight of the Republic Booster Pack"),
        ]
        different = [
            ("Rarity Collection II Booster Box", "Rarity Collection II Booster Pack"),
            ("Tales of Aria Booster Pack (Unlimited)", "Tales of Aria Booster Pack (First Edition)"),
            ("Assassin's Creed Booster Pack", "Universes Beyond Assassin's Creed Beyond Booster Pack"),
            ("Surging Sparks Booster Box", "Surging Sparks Booster Bundle"),
            ("Charizard ex Super Premium Collection", "Charizard ex Premium Collection"),
            ("Scarlet & Violet 151 Booster Bundle", "Scarlet & Violet Surging Sparks Booster Bundle"),
            ("Surging Sparks 3 Pack Blister Quagsire", "Surging Sparks 3 Pack Blister Zapdos"),
            ("Journey Together Elite Trainer Box", "Journey Together Pokemon Center Elite Trainer Box"),
            ("Royal Blood Booster Box (OP-10)", "A Fist of Divine Speed Booster Box (OP-11)"),
            ("Marvel Super Heroes Commander Deck Set of 4", "Marvel Super Heroes Commander Deck"),
        ]
        for a, b in same:
            self.assertEqual(match_key(a), match_key(b), (a, b))
        for a, b in different:
            self.assertNotEqual(match_key(a), match_key(b), (a, b))

    def test_an_offer_with_the_same_key_links_to_the_existing_product(self):
        from .importers import Catalogue

        catalogue = Catalogue([(1, "Commander Legends: Battle for Baldur's Gate Bundle"), (2, "Surging Sparks Booster Box")])
        match, value = catalogue.best_match("Magic The Gathering Commander Legends Battle For Baldurs Gate Bundle Box")
        self.assertEqual((match[0], value), (1, 100))


class MergeDuplicatesTests(TestCase):
    def test_duplicates_are_merged_onto_the_product_with_most_listings(self):
        from io import StringIO

        from django.core.management import call_command

        pset = make_set(make_game())
        shop_a, shop_b = make_retailer("Shop A"), make_retailer("Shop B")
        keep = make_product(pset, name="Commander Legends: Battle for Baldur's Gate Bundle", product_type="bundle")
        dupe = make_product(pset, name="Commander Legends Battle For Baldurs Gate Bundle Box", product_type="bundle", image_url="https://img.example/x.jpg")
        other = make_product(pset, name="Commander Legends: Battle for Baldur's Gate Booster Box", product_type="booster_box")
        make_listing(keep, shop_a)
        make_listing(keep, shop_b)
        make_listing(dupe, shop_b)
        call_command("merge_duplicates", stdout=StringIO())
        self.assertFalse(Product.objects.filter(pk=dupe.pk).exists())
        self.assertTrue(Product.objects.filter(pk=other.pk).exists())
        keep.refresh_from_db()
        self.assertEqual(keep.listings.count(), 2)
        self.assertEqual(keep.image_url, "https://img.example/x.jpg")


class WriteSkippingTests(TestCase):
    """A shop read stamps listings whose offer changed nothing instead of rewriting them."""

    def setUp(self):
        from datetime import timedelta

        self.retailer = make_retailer("Northgate Cards", delivery_cost=Decimal("2.00"))
        product_set = make_set(make_game())
        self.products, self.listings = [], []
        for i in range(3):
            product = make_product(product_set, name=f"Prismatic Evolutions Box {i}", ean=f"082065085{i:04d}",
                                   image_url="https://img.example/x.jpg")
            listing = make_listing(product, self.retailer, price="40.00", delivery="2.00",
                                   url=f"https://northgate.example/products/box-{i}", title=f"Box {i}")
            self.products.append(product)
            self.listings.append(listing)
        self.old = timezone.now() - timedelta(hours=5)
        Listing.objects.filter(retailer=self.retailer).update(last_checked=self.old)

    def offer(self, i, price="40.00", **kwargs):
        from .importers import Offer

        return Offer(title=f"Box {i}", url=f"https://northgate.example/products/box-{i}", price=Decimal(price),
                     ean=f"082065085{i:04d}", **kwargs)

    def test_unchanged_offers_are_stamped_not_rewritten(self):
        from unittest import mock

        offers = [self.offer(0), self.offer(1, price="38.00"), self.offer(2)]
        checked_at = timezone.now()
        with mock.patch("catalogue.signals.clear_list_caches") as cleared:
            # Four lookups (links, barcodes, catalogue, ignored), a fetch per offer, nine writes and reads for
            # the one change (its save, its verdict read, its own history and band as it has no other shop,
            # its product, the day's lowest price; with nothing to judge it, its price is not stored as a last
            # good price), one look for verdicts to heal and one stamp for the other two, and the
            # out-of-stock sweep. Rewriting all three would cost about thirty.
            with self.assertNumQueries(19):
                found, updated, unmatched = apply_offers(self.retailer, offers, checked_at=checked_at)
        self.assertEqual((found, updated, unmatched), (3, 3, []))
        self.assertEqual(cleared.call_count, 1)
        for listing in self.listings:
            listing.refresh_from_db()
            self.assertEqual(listing.last_checked, checked_at)
        self.assertEqual([l.price for l in self.listings], [Decimal("40.00"), Decimal("38.00"), Decimal("40.00")])
        # Only the changed product's history moved.
        self.assertEqual(list(DailyLowestPrice.objects.values_list("product_id", flat=True)), [self.products[1].pk])

    def test_a_new_link_or_title_is_still_saved_on_an_unchanged_offer(self):
        from .importers import Offer

        offer = Offer(title="Box 0 (sealed)", url="https://northgate.example/products/box-0?variant=7",
                      price=Decimal("40.00"), ean="0820650850000")
        apply_offers(self.retailer, [offer])
        listing = Listing.objects.get(pk=self.listings[0].pk)
        self.assertEqual((listing.url, listing.title), (offer.url, "Box 0 (sealed)"))
        self.assertGreater(listing.last_checked, self.old)

    def test_a_different_delivery_or_stock_state_is_a_change(self):
        from .importers import unchanged

        listing = self.listings[0]
        self.assertTrue(unchanged(listing, self.offer(0), Decimal("2.00")))
        self.assertFalse(unchanged(listing, self.offer(0), Decimal("3.00")))
        self.assertFalse(unchanged(listing, self.offer(0), None))
        self.assertFalse(unchanged(listing, self.offer(0, availability=Listing.Availability.PREORDER), Decimal("2.00")))
        self.assertFalse(unchanged(listing, self.offer(1), Decimal("2.00")))
        self.assertFalse(unchanged(Listing(product=self.products[0], retailer=self.retailer, price=Decimal("40.00"),
                                           delivery_cost=Decimal("2.00"), url=listing.url), self.offer(0), Decimal("2.00")))

    def test_stamping_happens_before_each_progress_post(self):
        from datetime import timedelta

        from .importers import ImportError_

        product_set = make_set(make_game(name="Lorcana", slug="lorcana"), name="First Chapter", slug="first-chapter")
        listings = []
        for i in range(260):
            product = Product.objects.create(game=product_set.game, product_set=product_set, name=f"Booster {i}",
                                             slug=f"booster-{i}", image_url="https://img.example/x.jpg")
            listings.append(make_listing(product, self.retailer, price="10.00", delivery="2.00",
                                         url=f"https://northgate.example/products/booster-{i}", title=f"Booster {i}"))
        old = timezone.now() - timedelta(hours=5)
        Listing.objects.filter(retailer=self.retailer).update(last_checked=old)
        run = ImportRun.objects.create(retailer=self.retailer)

        def offers():
            from .importers import Offer

            for i, listing in enumerate(listings):
                yield Offer(title=f"Booster {i}", url=listing.url, price=Decimal("10.00"), product_pk=listing.product_id)
            raise ImportError_("The shop stopped answering")

        checked_at = timezone.now()
        with self.assertRaises(ImportError_):
            apply_offers(self.retailer, offers(), checked_at=checked_at, run=run)
        stamped = set(Listing.objects.filter(last_checked=checked_at).values_list("pk", flat=True))
        self.assertTrue({l.pk for l in listings[:250]} <= stamped)
        run.refresh_from_db()
        self.assertEqual((run.offers_found, run.listings_updated), (250, 250))

    def test_stamps_go_out_in_chunks(self):
        from unittest import mock

        from . import importers

        # Per chunk: the stamp and one look for listings whose verdict needs judging again.
        with mock.patch.object(importers, "STAMP_CHUNK", 2), self.assertNumQueries(4):
            importers.stamp_checked([l.pk for l in self.listings], timezone.now())

    def test_a_changed_availability_still_records_a_restock(self):
        from .models import Restock

        Listing.objects.filter(pk=self.listings[0].pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        apply_offers(self.retailer, [self.offer(0), self.offer(1)])
        listing = Listing.objects.get(pk=self.listings[0].pk)
        self.assertEqual(listing.availability, Listing.Availability.IN_STOCK)
        self.assertIsNotNone(listing.back_in_stock_at)
        self.assertEqual(Restock.objects.filter(listing=listing).count(), 1)
        self.assertFalse(Restock.objects.filter(listing=self.listings[1]).exists())

    def test_a_failed_read_still_stamps_every_listing_it_saw(self):
        from unittest import mock

        from django.db import OperationalError

        from . import importers

        def offers(fail):
            yield self.offer(0)
            yield self.offer(1)
            raise fail

        checked_at = timezone.now()
        with self.assertRaises(ImportError_):
            apply_offers(self.retailer, offers(ImportError_("The shop stopped answering")), checked_at=checked_at)
        stamped = set(Listing.objects.filter(last_checked=checked_at).values_list("pk", flat=True))
        self.assertEqual(stamped, {self.listings[0].pk, self.listings[1].pk})
        # A database that cannot take the stamps either does not hide why the read failed.
        with mock.patch.object(importers, "stamp_checked", side_effect=OperationalError("database is locked")), \
                self.assertLogs("catalogue.importers", "WARNING"), self.assertRaises(ImportError_):
            apply_offers(self.retailer, offers(ImportError_("The shop stopped answering")))


class CadenceTests(TestCase):
    """Each shop's reading schedule: when it is due, how long it waits after errors, and pauses."""

    def setUp(self):
        self.now = timezone.now()

    def shop(self, name, **kwargs):
        kwargs.setdefault("source_type", Retailer.Source.SHOPIFY)
        kwargs.setdefault("source_url", f"https://{name.lower().replace(' ', '-')}.example/")
        return make_retailer(name, **kwargs)

    def run_due(self, *args, errors=None, **options):
        """import_prices with a fake read. Returns the names read, in order."""
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        order = []

        def fake_run(retailer, feed_path=None):
            order.append(retailer.name)
            error = (errors or {}).get(retailer.name, "")
            return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now(), error=error)

        with mock.patch("catalogue.management.commands.import_prices.run_import", fake_run):
            call_command("import_prices", *args, stdout=StringIO(), stderr=StringIO(), **options)
        return order

    def test_only_due_shops_are_read_and_next_read_is_stamped_before_the_fetch(self):
        from datetime import timedelta
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        self.shop("Due Shop", next_read_at=self.now - timedelta(minutes=5))
        self.shop("New Shop")
        self.shop("Later Shop", next_read_at=self.now + timedelta(hours=2))
        self.shop("Waiting Shop", backoff_until=self.now + timedelta(hours=1), error_streak=4)
        self.shop("Hidden Shop", is_active=False)
        make_retailer("Hand Shop", source_type=Retailer.Source.MANUAL)
        self.assertEqual(self.run_due(due=True), ["New Shop", "Due Shop"])
        for name in ("New Shop", "Due Shop"):
            # Read now, so the next read is a full interval away.
            self.assertGreater(Retailer.objects.get(name=name).next_read_at, self.now + timedelta(minutes=40))
        self.assertEqual(self.run_due(due=True), [])

        # A read that dies part way has already pushed its shop's next read on, so it cannot loop. It counts
        # as a failed read, closes the run it opened, and the shops after it are still read.
        crashing = self.shop("Crashing Shop")
        self.shop("Steady Shop")
        stamped = []
        order = []

        def dying_run(retailer, feed_path=None):
            order.append(retailer.name)
            if retailer.name == "Steady Shop":
                return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now())
            stamped.append(Retailer.objects.get(pk=retailer.pk).next_read_at)
            ImportRun.objects.create(retailer=retailer)
            raise KeyError("price")

        stderr = StringIO()
        with mock.patch("catalogue.management.commands.import_prices.run_import", dying_run):
            call_command("import_prices", due=True, stdout=StringIO(), stderr=stderr)
        self.assertEqual(order, ["Crashing Shop", "Steady Shop"])
        self.assertGreater(stamped[0], self.now)
        crashing.refresh_from_db()
        self.assertGreater(crashing.next_read_at, self.now + timedelta(minutes=40))
        self.assertEqual(crashing.error_streak, 1)
        self.assertGreater(crashing.backoff_until, self.now)
        self.assertIn("KeyError", crashing.last_error)
        self.assertIn("Traceback", stderr.getvalue())
        crashed_run = ImportRun.objects.get(retailer=crashing)
        self.assertIsNotNone(crashed_run.finished_at)
        self.assertIn("KeyError", crashed_run.error)
        self.assertEqual(Retailer.objects.get(name="Steady Shop").error_streak, 0)
        self.assertNotIn("Crashing Shop", self.run_due(due=True))

    def hourly(self, hours, takes=None, errors=None, late=None):
        """import_prices --due on the hour for ``hours`` hours on a fake clock.

        ``takes`` is how long each shop's read lasts (a minute unless given), ``errors`` the shops whose reads
        fail, and ``late`` how far into the hour a run starts. Returns the hours at which each shop was read.
        """
        from collections import defaultdict
        from datetime import timedelta
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        base = self.now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        clock = [base]
        reads = defaultdict(list)

        def fake_run(retailer, feed_path=None):
            start = clock[0]
            reads[retailer.name].append(round((start - base).total_seconds() / 3600, 2))
            clock[0] += (takes or {}).get(retailer.name, timedelta(minutes=1))
            error = (errors or {}).get(retailer.name, "")
            return ImportRun.objects.create(retailer=retailer, started_at=start, finished_at=clock[0], error=error)

        with mock.patch("django.utils.timezone.now", lambda: clock[0]), \
                mock.patch("catalogue.management.commands.import_prices.time.monotonic", lambda: clock[0].timestamp()), \
                mock.patch("catalogue.management.commands.import_prices.run_import", fake_run):
            for hour in range(hours):
                clock[0] = max(clock[0], base + timedelta(hours=hour) + (late or {}).get(hour, timedelta(seconds=2)))
                call_command("import_prices", due=True, stdout=StringIO(), stderr=StringIO())
        return dict(reads)

    def test_the_hourly_run_reads_a_shop_due_within_a_quarter_hour_of_its_start(self):
        from datetime import timedelta

        self.shop("Soon Shop", next_read_at=self.now + timedelta(minutes=10))
        self.shop("Late Shop", next_read_at=self.now + timedelta(minutes=20))
        self.assertEqual(self.run_due(due=True), ["Soon Shop"])

    def test_the_hourly_run_honours_each_shops_interval_rounded_to_whole_hours(self):
        from datetime import timedelta

        # Read 40 minutes into the run behind a slow shop, and still read every hour, even after a run
        # that started nine minutes late behind the stock watch.
        self.shop("A Slow Shop", read_every_minutes=60)
        self.shop("Hourly Shop", read_every_minutes=60)
        reads = self.hourly(6, takes={"A Slow Shop": timedelta(minutes=40)}, late={2: timedelta(minutes=9)})
        self.assertGreater(reads["Hourly Shop"][0], 0.6)
        self.assertEqual([int(hour) for hour in reads["Hourly Shop"]], [0, 1, 2, 3, 4, 5])

        # An owner's 80 or 120 minutes is every other hour, not every hour.
        Retailer.objects.all().delete()
        self.shop("Eighty Shop", read_every_minutes=80)
        self.shop("Two Hour Shop", read_every_minutes=120)
        self.shop("Seventy Shop", read_every_minutes=70)
        reads = self.hourly(6)
        self.assertEqual([int(hour) for hour in reads["Eighty Shop"]], [0, 2, 4])
        self.assertEqual([int(hour) for hour in reads["Two Hour Shop"]], [0, 2, 4])
        self.assertEqual([int(hour) for hour in reads["Seventy Shop"]], [0, 1, 2, 3, 4, 5])

    def test_a_shop_backing_off_is_not_read_before_its_wait_ends(self):
        from datetime import timedelta

        self.shop("Throttled", next_read_at=self.now - timedelta(minutes=1),
                  backoff_until=self.now + timedelta(minutes=20), error_streak=1,
                  last_error="Could not fetch https://throttled.example/: HTTP Error 429: Too Many Requests")
        self.assertEqual(self.run_due(due=True), [])

        # A shop that keeps failing waits 80 minutes after its fifth error in a row, so the next hour's run
        # leaves it alone and the one after reads it.
        self.shop("Failing Shop", error_streak=4)
        reads = self.hourly(3, errors={"Failing Shop": "Could not fetch https://failing.example/: HTTP Error 503"})
        self.assertEqual(reads["Failing Shop"], [0.0, 2.0])

    def test_a_slow_shop_is_never_read_more_than_a_third_of_the_time(self):
        from datetime import timedelta

        for minutes in (22, 50):
            Retailer.objects.all().delete()
            self.shop("Slow Shop", read_every_minutes=60)
            reads = self.hourly(10, takes={"Slow Shop": timedelta(minutes=minutes)})["Slow Shop"]
            self.assertGreater(len(reads), 1)
            for earlier, later in zip(reads, reads[1:]):
                self.assertGreaterEqual((later - earlier) * 60, 3 * minutes, (minutes, reads))

    def test_a_shop_that_comes_due_during_the_run_is_read_in_the_same_run(self):
        from datetime import timedelta
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        self.shop("First Shop")
        self.shop("Meanwhile Shop", next_read_at=self.now + timedelta(minutes=50))
        order = []

        def slow_run(retailer, feed_path=None):
            order.append(retailer.name)
            if retailer.name == "First Shop":
                # The first read takes long enough for the other shop's turn to come.
                Retailer.objects.filter(name="Meanwhile Shop").update(next_read_at=timezone.now())
            return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now())

        with mock.patch("catalogue.management.commands.import_prices.run_import", slow_run):
            call_command("import_prices", due=True, stdout=StringIO())
        self.assertEqual(order, ["First Shop", "Meanwhile Shop"])

    def test_due_puts_marketplaces_first_then_the_longest_waiting(self):
        from datetime import timedelta

        self.shop("Aardvark Cards", next_read_at=self.now - timedelta(minutes=5))
        self.shop("Zebra Cards", next_read_at=self.now - timedelta(minutes=50))
        self.shop("Never Read")
        Retailer.objects.create(name="eBay", slug="ebay", website="https://www.ebay.co.uk/",
                                source_type=Retailer.Source.EBAY, next_read_at=self.now - timedelta(minutes=1))
        self.assertEqual([r.name for r in Retailer.due(self.now)], ["eBay", "Never Read", "Zebra Cards", "Aardvark Cards"])

    def test_errors_back_off_5_10_20_minutes_up_to_6_hours_and_429_jumps_to_30(self):
        from datetime import timedelta

        from .management.commands.import_prices import http_status

        shop = self.shop("Flaky Shop")
        waits = []
        for _ in range(9):
            shop.read_failed(self.now, error="Could not fetch https://flaky.example/products.json: HTTP Error 503")
            waits.append(shop.backoff_until - self.now)
        minutes = [int(w.total_seconds() // 60) for w in waits]
        self.assertEqual(minutes, [5, 10, 20, 40, 80, 160, 320, 360, 360])
        shop.refresh_from_db()
        self.assertEqual(shop.error_streak, 9)
        self.assertIn("HTTP Error 503", shop.last_error)
        self.assertNotIn(shop, Retailer.due(self.now + timedelta(hours=5)))

        # One read that works forgets the errors.
        shop.read_ok(self.now, 30)
        shop.refresh_from_db()
        self.assertEqual((shop.error_streak, shop.backoff_until, shop.last_error), (0, None, ""))
        self.assertEqual(shop.last_ok_at, self.now)

        # Being told to slow down waits half an hour straight away.
        self.assertEqual(http_status("Could not fetch https://flaky.example/: HTTP Error 429: Too Many Requests"), 429)
        self.assertEqual(http_status("eBay API kept throttling us."), 429)
        self.assertEqual(http_status("eBay API 500: busy"), 500)
        self.assertIsNone(http_status("Set the shop address on the retailer first."))
        self.run_due("flaky-shop", errors={"Flaky Shop": "Could not fetch https://flaky.example/: HTTP Error 429: Too Many Requests"})
        shop.refresh_from_db()
        self.assertEqual(shop.error_streak, 1)
        self.assertAlmostEqual((shop.backoff_until - timezone.now()).total_seconds(), 30 * 60, delta=60)
        # Once the doubling has gone past half an hour, a 429 waits as long as any other error.
        shop.error_streak = 4
        shop.read_failed(self.now, 429)
        self.assertEqual(shop.backoff_until - self.now, timedelta(minutes=80))

    def test_a_failed_read_through_the_command_backs_off_and_a_good_one_resets(self):
        from datetime import timedelta

        shop = self.shop("Flaky Shop")
        self.run_due(due=True, errors={"Flaky Shop": "Could not fetch https://flaky.example/: HTTP Error 503"})
        shop.refresh_from_db()
        self.assertEqual(shop.error_streak, 1)
        self.assertAlmostEqual((shop.backoff_until - timezone.now()).total_seconds(), 5 * 60, delta=60)
        Retailer.objects.filter(pk=shop.pk).update(next_read_at=self.now - timedelta(minutes=1),
                                                   backoff_until=self.now - timedelta(minutes=1))
        self.run_due(due=True)
        shop.refresh_from_db()
        self.assertEqual((shop.error_streak, shop.backoff_until), (0, None))
        self.assertIsNotNone(shop.last_ok_at)

    def test_paused_shops_are_skipped(self):
        self.shop("Paused Shop", reading_paused=True)
        self.shop("Open Shop")
        self.assertEqual(self.run_due(due=True), ["Open Shop"])

    def test_adaptive_cadence_is_three_times_the_read_time_at_least_the_setting(self):
        from datetime import timedelta

        shop = self.shop("Shop", read_every_minutes=45)
        self.assertEqual(shop.cadence_for(self.now, 60), self.now + timedelta(minutes=45))
        self.assertEqual(shop.cadence_for(self.now, 15 * 60), self.now + timedelta(minutes=45))
        self.assertEqual(shop.cadence_for(self.now, 30 * 60), self.now + timedelta(minutes=90))
        shop.read_ok(self.now, 30 * 60)
        shop.refresh_from_db()
        self.assertEqual(shop.last_read_seconds, 1800)
        self.assertEqual(shop.next_read_at, self.now + timedelta(minutes=90))
        # In a run, the setting counts from the run's start and three times the read from the read's end.
        run_start = self.now - timedelta(minutes=40)
        self.assertEqual(shop.cadence_for(self.now, 60, since=run_start), run_start + timedelta(minutes=45))
        self.assertEqual(shop.cadence_for(self.now, 20 * 60, since=run_start), self.now + timedelta(minutes=60))

    def test_a_slug_run_ignores_cadence(self):
        from datetime import timedelta

        self.shop("Paused Shop", reading_paused=True, next_read_at=self.now + timedelta(hours=3),
                  backoff_until=self.now + timedelta(hours=1), error_streak=3)
        self.assertEqual(self.run_due("paused-shop", due=True), ["Paused Shop"])
        shop = Retailer.objects.get(name="Paused Shop")
        self.assertTrue(shop.reading_paused)
        self.assertEqual(shop.error_streak, 0)

    def test_without_due_every_shop_is_read_as_before(self):
        from datetime import timedelta

        self.shop("Later Shop", next_read_at=self.now + timedelta(hours=2))
        self.shop("Due Shop", next_read_at=self.now - timedelta(minutes=5))
        self.assertEqual(self.run_due(), ["Due Shop", "Later Shop"])

    def test_a_marketplace_already_read_today_counts_as_a_good_read_of_no_time(self):
        from datetime import timedelta
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        ebay = Retailer.objects.create(name="eBay", slug="ebay", website="https://www.ebay.co.uk/",
                                       source_type=Retailer.Source.EBAY, last_read_seconds=900)
        earlier = ImportRun.objects.create(retailer=ebay, started_at=self.now - timedelta(hours=3),
                                           finished_at=self.now - timedelta(hours=2), offers_found=5)
        with mock.patch("catalogue.management.commands.import_prices.run_import", lambda r, feed_path=None: earlier):
            call_command("import_prices", due=True, stdout=StringIO())
        ebay.refresh_from_db()
        self.assertEqual(ebay.error_streak, 0)
        self.assertEqual(ebay.last_read_seconds, 0)
        # The last good read is the one that happened, not the skip.
        self.assertEqual(ebay.last_ok_at, earlier.finished_at)
        # Tried again within the hour, so a failure on the day is retried hourly.
        self.assertLessEqual(ebay.next_read_at, timezone.now() + timedelta(minutes=61))

    def test_setup_shops_sets_cadence_once_and_keeps_an_owner_change(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("setup_shops", stdout=StringIO())
        shopify = Retailer.objects.get(slug="total-cards")
        website = Retailer.objects.get(slug="magic-madhouse")
        self.assertEqual((shopify.read_every_minutes, website.read_every_minutes), (45, 60))
        # The owner sets a Shopify shop back to an hour and it is read on that schedule.
        Retailer.objects.filter(pk=shopify.pk).update(read_every_minutes=60, next_read_at=self.now)
        # Another owner change, made before the shop was ever read on a schedule.
        Retailer.objects.filter(slug="gathering-games").update(read_every_minutes=120)
        call_command("setup_shops", stdout=StringIO())
        shopify.refresh_from_db()
        self.assertEqual(shopify.read_every_minutes, 60)
        self.assertEqual(Retailer.objects.get(slug="gathering-games").read_every_minutes, 120)
        self.assertEqual(Retailer.objects.get(slug="the-card-vault").read_every_minutes, 45)

    def test_the_interval_is_edited_from_the_shops_list_and_the_rest_is_read_only(self):
        from django.contrib.auth.models import User

        self.shop("Harbour Games", error_streak=2, last_error="HTTP Error 503")
        self.client.force_login(User.objects.create_superuser("ben", "ben@example.com", "pw"))
        changelist = self.client.get("/admin/catalogue/retailer/").content.decode()
        self.assertIn('name="form-0-read_every_minutes"', changelist)
        shop = Retailer.objects.get(name="Harbour Games")
        page = self.client.get(f"/admin/catalogue/retailer/{shop.pk}/change/").content.decode()
        self.assertIn('name="read_every_minutes"', page)
        for field in ("error_streak", "backoff_until", "next_read_at", "reading_paused", "last_error"):
            self.assertNotIn(f'name="{field}"', page)
        self.assertIn("HTTP Error 503", page)


class PageIndexTests(TestCase):
    """Website shops: every sitemap page is indexed, and each read fetches a short, fair slice."""

    def setUp(self):
        self.retailer = make_retailer("Web Shop", source_type=Retailer.Source.WEBSITE, source_url="https://shop.example/")
        self.fetched = []
        self.pages = []   # (address, lastmod text or "")

    def fetch(self, url):
        if url.endswith("/sitemap.xml"):
            entries = "".join(
                f"<url><loc>{u}</loc>{f'<lastmod>{lastmod}</lastmod>' if lastmod else ''}</url>" for u, lastmod in self.pages
            )
            return f"<urlset>{entries}</urlset>".encode()
        if "sitemap" in url or url.endswith(".xml") or url.endswith("robots.txt"):
            raise ImportError_("missing")
        self.fetched.append(url)
        return b"<html><body>Nothing to price</body></html>"

    def read(self, **kwargs):
        from .importers import website_offers

        self.fetched = []
        list(website_offers(self.retailer, fetch=self.fetch, pause=0, **kwargs))
        return self.fetched

    def address(self, n):
        return f"https://shop.example/products/pokemon-set-{n}-booster-box"

    def test_first_read_of_1500_pages_fetches_600_and_indexes_all_then_the_next_600(self):
        from .models import ShopPage

        self.pages = [(self.address(n), "2026-01-05") for n in range(1500)]
        urls = [u for u, _lastmod in self.pages]
        self.assertEqual(self.read(), urls[:600])
        self.assertEqual(ShopPage.objects.filter(retailer=self.retailer).count(), 1500)
        self.assertEqual(ShopPage.objects.filter(last_fetched_at__isnull=False).count(), 600)
        self.assertEqual(ShopPage.objects.get(url=urls[0]).slug_words, "pokemon set 0 booster box")
        self.assertEqual(self.read(), urls[600:1200])
        self.assertEqual(self.read(), urls[1200:])
        # Every page read and none changed since: nothing to fetch until a day has passed.
        self.assertEqual(self.read(), [])

    def test_indexing_writes_in_batches_never_a_query_per_page(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from .importers import pages_to_read
        from .models import ShopPage

        pages = [(self.address(n), None) for n in range(1500)]
        for _read in range(2):
            with CaptureQueriesContext(connection) as queries:
                chosen = pages_to_read(self.retailer, pages)
            # One insert per batch of rows (SQLite caps the values in one statement) and one read.
            self.assertLessEqual(len(queries), 15)
            self.assertEqual(len(chosen), 600)
        self.assertEqual(ShopPage.objects.count(), 1500)

    def test_a_page_whose_lastmod_moved_is_fetched_first_and_an_unchanged_page_waits_a_day(self):
        from datetime import timedelta

        from .models import ShopPage

        now = timezone.now()
        a, b, c, d = (f"https://shop.example/products/{name}" for name in ("alpha", "bravo", "charlie", "delta"))
        old = (now - timedelta(days=3)).isoformat()
        # The changed page comes after the undated one in the sitemap and was fetched more recently,
        # so only a real priority for changed pages puts it first.
        self.pages = [(a, old), (b, old), (d, ""), (c, old)]
        self.assertEqual(self.read(), [a, b, d, c])
        ShopPage.objects.update(last_fetched_at=now - timedelta(hours=2))
        ShopPage.objects.filter(url=d).update(last_fetched_at=now - timedelta(hours=5))
        self.pages[3] = (c, (now - timedelta(hours=1)).isoformat())
        # Changed since its last fetch: ahead of the undated page.
        self.assertEqual(self.read(limit=1), [c])
        # Alpha and Bravo are dated before their last fetch and were read two hours ago.
        self.assertEqual(self.read(), [d])
        ShopPage.objects.filter(url=a).update(last_fetched_at=now - timedelta(hours=25))
        # Unchanged pages are still read once a day, longest unread first.
        self.assertEqual(self.read(), [a, d])

    def test_a_page_unread_for_a_day_goes_ahead_of_pages_dated_as_changed_on_every_read(self):
        from datetime import timedelta

        from .models import ShopPage

        # Some shops date every page at the time the sitemap was made. Those pages count as changed
        # on every read and must not keep the shop's other pages from ever being read again.
        now = timezone.now()
        busy = [self.address(n) for n in range(3)]
        cold = self.address(9)
        self.pages = [(u, (now - timedelta(minutes=1)).isoformat()) for u in busy]
        self.pages.append((cold, (now - timedelta(days=10)).isoformat()))
        self.read()
        ShopPage.objects.update(last_fetched_at=now - timedelta(hours=2))
        ShopPage.objects.filter(url=cold).update(last_fetched_at=now - timedelta(hours=25))
        self.assertEqual(self.read(limit=2), [cold, busy[0]])
        # Overdue pages are taken longest unread first, whatever the sitemap says about them.
        ShopPage.objects.update(last_fetched_at=now - timedelta(hours=30))
        ShopPage.objects.filter(url=busy[2]).update(last_fetched_at=now - timedelta(hours=40))
        self.assertEqual(self.read(limit=2), [busy[2], busy[0]])

    def test_a_page_dated_in_the_future_counts_as_undated_and_is_not_changed_on_every_read(self):
        from datetime import timedelta

        from .models import ShopPage

        now = timezone.now()
        undated, future = self.address(1), self.address(2)
        self.pages = [(undated, ""), (future, "2099-01-01")]
        self.assertEqual(self.read(), [undated, future])
        self.assertIsNone(ShopPage.objects.get(url=future).lastmod)
        ShopPage.objects.filter(url=undated).update(last_fetched_at=now - timedelta(hours=3))
        ShopPage.objects.filter(url=future).update(last_fetched_at=now - timedelta(hours=1))
        # Taken in turn with the undated page, not ahead of it as a changed page would be.
        self.assertEqual(self.read(limit=1), [undated])

    def test_a_date_at_the_edge_of_the_calendar_is_unreadable_and_never_stops_the_read(self):
        from .models import ShopPage

        # .NET writes its smallest date like this with a British summer time offset; it cannot be
        # stored in UTC, and it used to stop every read of the shop.
        first, last = self.address(1), self.address(2)
        self.pages = [(first, "0001-01-01T00:00:00+01:00"), (last, "9999-12-31T23:59:59-01:00")]
        self.assertEqual(self.read(), [first, last])
        self.assertEqual(ShopPage.objects.filter(lastmod__isnull=True).count(), 2)

    def test_a_sitemap_without_lastmod_is_read_round_robin(self):
        self.pages = [(self.address(n), "") for n in range(5)]
        urls = [u for u, _lastmod in self.pages]
        self.assertEqual(self.read(limit=2), urls[0:2])
        self.assertEqual(self.read(limit=2), urls[2:4])
        self.assertEqual(self.read(limit=2), [urls[4], urls[0]])
        self.assertEqual(self.read(limit=2), urls[1:3])

    def test_sitemap_dates_are_read_in_each_form_shops_use(self):
        from datetime import datetime, timedelta, timezone as tz

        from .importers import sitemap_pages

        self.pages = [
            ("https://shop.example/products/a", "2026-03-01"),
            ("https://shop.example/products/b", "2026-03-01T10:30:00+01:00"),
            ("https://shop.example/products/c", "2026-03-01T10:30:00Z"),
            ("https://shop.example/products/d", "not a date"),
            ("https://shop.example/products/e", ""),
            ("https://shop.example/products/f", "0001-01-01T00:00:00+01:00"),
        ]
        dates = dict(sitemap_pages("https://shop.example", fetch=self.fetch))
        self.assertEqual(dates["https://shop.example/products/a"], datetime(2026, 3, 1, tzinfo=tz.utc))
        self.assertEqual(dates["https://shop.example/products/b"], datetime(2026, 3, 1, 9, 30, tzinfo=tz.utc))
        self.assertEqual(dates["https://shop.example/products/c"], datetime(2026, 3, 1, 10, 30, tzinfo=tz.utc))
        self.assertIsNone(dates["https://shop.example/products/d"])
        self.assertIsNone(dates["https://shop.example/products/e"])
        self.assertIsNone(dates["https://shop.example/products/f"])
        self.assertEqual(dates["https://shop.example/products/b"].utcoffset(), timedelta(0))

    def test_a_page_that_fails_is_stamped_so_it_cannot_hold_the_front_of_every_read(self):
        from .models import ShopPage

        self.pages = [(self.address(n), "") for n in range(3)]
        broken = self.address(0)

        def fetch(url):
            if url == broken:
                self.fetched.append(url)
                raise ImportError_("gone")
            return self.fetch(url)

        self.fetched = []
        from .importers import website_offers

        list(website_offers(self.retailer, fetch=fetch, pause=0, limit=1))
        self.assertEqual(self.fetched, [broken])
        self.assertIsNotNone(ShopPage.objects.get(url=broken).last_fetched_at)
        self.assertEqual(self.read(limit=1), [self.address(1)])

    def test_the_page_names_its_product_once_an_offer_from_it_is_linked(self):
        from unittest import mock

        from .models import ShopPage

        product = make_product(make_set(make_game()), ean="0820650851230")
        page = "https://shop.example/products/pokemon-prismatic-evolutions-elite-trainer-box"
        other = "https://shop.example/products/pokemon-mystery-booster-box"
        self.pages = [(page, ""), (other, "")]

        def fetch(url):
            if url == page:
                return (b'<script type="application/ld+json">{"@type": "Product", "name": "Prismatic Evolutions Elite Trainer Box",'
                        b' "gtin13": "0820650851230", "offers": {"price": "84.99", "priceCurrency": "GBP"}}</script>')
            return self.fetch(url)

        with mock.patch("catalogue.importers.time.sleep", lambda s: None):
            run = run_import(self.retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)
        self.assertEqual(ShopPage.objects.get(url=page).product, product)
        self.assertIsNone(ShopPage.objects.get(url=other).product)
        self.assertEqual(Listing.objects.get(product=product, retailer=self.retailer).price, Decimal("84.99"))

    def test_pages_missing_from_the_sitemap_for_30_days_are_forgotten_by_tidy_all(self):
        from datetime import timedelta
        from io import StringIO

        from django.core.management import call_command

        from .models import ShopPage

        now = timezone.now()
        gone = ShopPage.objects.create(retailer=self.retailer, url=self.address(1), last_seen_at=now - timedelta(days=31))
        kept = ShopPage.objects.create(retailer=self.retailer, url=self.address(2), last_seen_at=now - timedelta(days=29))
        out = StringIO()
        call_command("tidy_all", "--dry-run", stdout=out)
        self.assertIn("1 shop pages would be forgotten.", out.getvalue())
        self.assertTrue(ShopPage.objects.filter(pk=gone.pk).exists())
        call_command("tidy_all", stdout=StringIO())
        self.assertFalse(ShopPage.objects.filter(pk=gone.pk).exists())
        self.assertTrue(ShopPage.objects.filter(pk=kept.pk).exists())


class PreorderPulseTests(TestCase):
    """The pre-order pulse: one look at a Shopify shop's collection list, reading only what changed."""

    def setUp(self):
        from datetime import timedelta

        self.now = timezone.now()
        self.shop = make_retailer("Pulse Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://pulse.example/")
        self.box = make_product(make_set(make_game()), name="Destined Rivals Booster Box", ean="0196214112345")
        self.updated = "2026-10-09T08:00:00+01:00"
        self.hour = timedelta(hours=1)

    def answers(self, collections, products=None, asked=None):
        """A fetch for /collections.json (``collections``: [(handle, count)]) and each collection's products."""

        def fetch(url, *args, **kwargs):
            if asked is not None:
                asked.append(url)
            if url.endswith("/meta.json"):
                return b'{"currency": "GBP"}'
            if url.endswith("/collections.json?limit=250"):
                return json.dumps({"collections": [
                    {"handle": handle, "products_count": count, "updated_at": self.updated} for handle, count in collections
                ]}).encode()
            for handle, items in (products or {}).items():
                if f"/collections/{handle}/products.json" in url:
                    return shopify_page(items if url.endswith("page=1") else [])
            return shopify_page([])

        return fetch

    def box_json(self, available=True, tags=(), published="2026-10-09T08:50:00+01:00"):
        return {"handle": "destined-rivals-booster-box", "title": "Pokemon TCG: Destined Rivals Booster Box",
                "tags": list(tags), "published_at": published, "product_type": "Booster Box",
                "variants": [{"price": "139.99", "available": available, "barcode": "0196214112345"}]}

    def remember(self, handle, count):
        from .importers import parse_published
        from .models import RetailerCollection

        RetailerCollection.objects.create(retailer=self.shop, handle=handle, products_count=count,
                                          updated_at=parse_published(self.updated))

    def test_an_unchanged_collection_list_reads_no_products(self):
        from .importers import poll_collections

        self.remember("pre-orders", 4)
        asked = []
        pulse = poll_collections(self.shop, fetch=self.answers([("pre-orders", 4), ("pokemon", 300)], asked=asked), now=self.now)
        self.assertEqual(asked, ["https://pulse.example/collections.json?limit=250"])
        self.assertEqual((pulse.read, pulse.run, pulse.error), ([], None, ""))
        self.assertFalse(ImportRun.objects.exists())
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.collections_polled_at, self.shop.collections_ok), (self.now, True))

    def test_a_changed_count_reads_only_that_collection_and_the_product_is_a_preorder_at_once(self):
        from .importers import PULSE_NOTE, poll_collections
        from .models import RetailerCollection

        self.remember("pre-orders", 4)
        self.remember("coming-soon", 2)
        asked = []
        fetch = self.answers([("pre-orders", 5), ("coming-soon", 2)], {"pre-orders": [self.box_json()]}, asked)
        pulse = poll_collections(self.shop, fetch=fetch, now=self.now)
        self.assertEqual(pulse.read, ["pre-orders"])
        self.assertFalse([url for url in asked if "/collections/coming-soon/" in url])
        listing = Listing.objects.get(product=self.box, retailer=self.shop)
        self.assertEqual(listing.availability, Listing.Availability.PREORDER)
        self.assertIsNotNone(listing.first_preorder_at)
        self.assertEqual(listing.shop_published_at.isoformat(), "2026-10-09T07:50:00+00:00")
        run = ImportRun.objects.get()
        self.assertEqual((run.note, run.offers_found, run.error), (f"{PULSE_NOTE}: pre-orders", 1, ""))
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(RetailerCollection.objects.get(handle="pre-orders").products_count, 5)
        # The next look finds nothing new.
        asked.clear()
        self.assertEqual(poll_collections(self.shop, fetch=fetch, now=self.now + self.hour).read, [])
        self.assertEqual(len(asked), 1)

    def test_a_changed_update_time_alone_reads_the_collection(self):
        from .importers import poll_collections

        self.remember("pre-orders", 4)
        self.updated = "2026-10-09T09:30:00+01:00"   # same count, the shop changed the collection since
        asked = []
        pulse = poll_collections(self.shop, fetch=self.answers([("pre-orders", 4)], {"pre-orders": [self.box_json()]}, asked),
                                 now=self.now)
        self.assertEqual(pulse.read, ["pre-orders"])
        self.assertIn("https://pulse.example/collections/pre-orders/products.json?limit=250&page=1", asked)
        self.assertEqual(Listing.objects.get(product=self.box).availability, Listing.Availability.PREORDER)

    def test_preorders_are_read_before_a_large_new_arrivals_collection_listed_first(self):
        from .importers import poll_collections
        from .models import RetailerCollection

        def page(number):
            return [{"handle": f"new-{number}-{n}", "title": f"Thing {n}", "tags": [], "variants": []} for n in range(250)]

        asked = []
        base = self.answers([("new-arrivals", 5000), ("pre-orders", 1)], {"pre-orders": [self.box_json()]}, asked)

        def fetch(url, *args, **kwargs):
            if "/collections/new-arrivals/" in url:
                asked.append(url)
                return shopify_page(page(url.rsplit("page=", 1)[1]))
            return base(url)

        # Time for about three requests of products.
        pulse = poll_collections(self.shop, fetch=fetch, now=self.now,
                                 stop=lambda: sum("/products.json" in url for url in asked) >= 3)
        self.assertEqual(pulse.read, ["pre-orders", "new-arrivals"])
        self.assertEqual(Listing.objects.get(product=self.box).availability, Listing.Availability.PREORDER)
        # The new arrivals read part way counts as read, so the next look does not start it again.
        self.assertEqual(RetailerCollection.objects.get(handle="new-arrivals").products_count, 5000)
        asked.clear()
        self.assertEqual(poll_collections(self.shop, fetch=fetch, now=self.now + self.hour).read, [])
        self.assertEqual(asked, ["https://pulse.example/collections.json?limit=250"])

    def test_one_collection_that_cannot_be_read_does_not_block_the_others(self):
        from .importers import poll_collections
        from .models import RetailerCollection

        base = self.answers([("coming-soon", 1), ("pre-orders", 1)], {"pre-orders": [self.box_json()]})

        def fetch(url, *args, **kwargs):
            return b"<html>" if "/collections/coming-soon/" in url else base(url)

        pulse = poll_collections(self.shop, fetch=fetch, now=self.now)
        self.assertEqual(pulse.read, ["pre-orders"])
        self.assertIn("coming-soon", pulse.error)
        self.assertEqual(Listing.objects.get(product=self.box).availability, Listing.Availability.PREORDER)
        self.assertIn("coming-soon", ImportRun.objects.get().error)
        # The broken one is not remembered, so it is tried again at the next look.
        self.assertEqual(list(RetailerCollection.objects.values_list("handle", flat=True)), ["pre-orders"])
        # By hand, both what was read and what failed are said.
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        RetailerCollection.objects.all().delete()
        out = StringIO()
        with mock.patch("catalogue.importers.fetch", fetch):
            call_command("poll_preorders", "--shop", "pulse-shop", stdout=out)
        self.assertEqual(out.getvalue().strip(),
                         "Pulse Shop: read pre-orders, 1 listings checked. https://pulse.example did not return Shopify JSON for coming-soon")

    def test_a_shop_throttling_one_collection_ends_the_look(self):
        from .importers import poll_collections

        asked = []
        base = self.answers([("coming-soon", 1), ("pre-orders", 1)], {"pre-orders": [self.box_json()]}, asked)

        def fetch(url, *args, **kwargs):
            if "/collections/coming-soon/" in url:
                raise ImportError_(f"Could not fetch {url}: HTTP Error 429: Too Many Requests")
            return base(url)

        pulse = poll_collections(self.shop, fetch=fetch, now=self.now)
        self.assertEqual((pulse.read, pulse.status), ([], 429))
        self.assertFalse([url for url in asked if "/collections/pre-orders/" in url])

    def test_a_new_watched_collection_counts_as_a_change_and_a_gone_one_is_forgotten(self):
        from .importers import poll_collections
        from .models import RetailerCollection

        self.remember("old-pre-orders", 3)
        fetch = self.answers([("coming-soon", 1), ("singles", 900)], {"coming-soon": [self.box_json()]})
        self.assertEqual(poll_collections(self.shop, fetch=fetch, now=self.now).read, ["coming-soon"])
        self.assertEqual(list(RetailerCollection.objects.values_list("handle", flat=True)), ["coming-soon"])
        self.assertEqual(Listing.objects.get(product=self.box).availability, Listing.Availability.PREORDER)

    def test_new_arrivals_count_only_when_the_product_says_preorder(self):
        from .importers import poll_collections, preorder_collections

        other = make_product(self.box.product_set, name="Destined Rivals Elite Trainer Box", ean="0196214112352")
        in_stock = {"handle": "destined-rivals-etb", "title": "Pokemon TCG: Destined Rivals Elite Trainer Box", "tags": [],
                    "product_type": "Elite Trainer Box",
                    "variants": [{"price": "49.99", "available": True, "barcode": "0196214112352"}]}
        fetch = self.answers([("new-arrivals", 2)], {"new-arrivals": [in_stock, self.box_json(tags=["Pre-Orders-Live"])]})
        poll_collections(self.shop, fetch=fetch, now=self.now)
        self.assertEqual(Listing.objects.get(product=self.box).availability, Listing.Availability.PREORDER)
        # Being new says nothing about stock: the full read decides that one.
        self.assertFalse(Listing.objects.filter(product=other).exists())
        # So new arrivals never mark a whole-shop read's products as pre-orders.
        names = preorder_collections("https://pulse.example", fetch=self.answers([("new-arrivals", 2), ("coming-soon", 1)]))
        self.assertIn("coming-soon", names)
        self.assertNotIn("new-arrivals", names)

    def test_a_shop_without_a_collection_list_is_looked_at_weekly(self):
        from datetime import timedelta

        from .importers import poll_collections

        def missing(url, *args, **kwargs):
            raise ImportError_(f"Could not fetch {url}: HTTP Error 404: Not Found")

        pulse = poll_collections(self.shop, fetch=missing, now=self.now)
        self.assertTrue(pulse.no_list)
        self.shop.refresh_from_db()
        self.assertIs(self.shop.collections_ok, False)
        self.assertNotIn(self.shop, Retailer.pulse_due(self.now + timedelta(days=6)))
        self.assertIn(self.shop, Retailer.pulse_due(self.now + timedelta(days=7)))
        # Not JSON at all counts the same.
        Retailer.objects.filter(pk=self.shop.pk).update(collections_ok=None)
        self.shop.refresh_from_db()
        self.assertTrue(poll_collections(self.shop, fetch=lambda url, *a, **k: b"<html>", now=self.now).no_list)

    def test_a_shop_is_looked_at_at_most_every_fifteen_minutes(self):
        from datetime import timedelta

        from .importers import poll_collections

        self.assertIn(self.shop, Retailer.pulse_due(self.now))
        poll_collections(self.shop, fetch=self.answers([]), now=self.now)
        self.assertNotIn(self.shop, Retailer.pulse_due(self.now + timedelta(minutes=14)))
        self.assertIn(self.shop, Retailer.pulse_due(self.now + timedelta(minutes=15)))
        # Paused, waiting after errors, inactive or not Shopify: never looked at.
        Retailer.objects.filter(pk=self.shop.pk).update(reading_paused=True)
        self.assertFalse(Retailer.pulse_due(self.now + self.hour).exists())
        Retailer.objects.filter(pk=self.shop.pk).update(reading_paused=False, backoff_until=self.now + 2 * self.hour)
        self.assertFalse(Retailer.pulse_due(self.now + self.hour).exists())
        make_retailer("Feed Shop", source_type=Retailer.Source.FEED, source_url="https://feed.example/f.csv")
        self.assertFalse(Retailer.pulse_due(self.now + self.hour).exists())

    def test_a_shop_pricing_in_another_currency_applies_nothing(self):
        from .importers import poll_collections

        base = self.answers([("pre-orders", 1)], {"pre-orders": [self.box_json()]})

        def fetch(url, *args, **kwargs):
            return b'{"currency": "EUR"}' if url.endswith("/meta.json") else base(url)

        pulse = poll_collections(self.shop, fetch=fetch, now=self.now)
        self.assertIn("EUR", pulse.error)
        self.assertFalse(Listing.objects.exists())
        self.assertIn("EUR", ImportRun.objects.get().error)

    def test_a_pulse_run_is_not_a_read_of_the_shop(self):
        from datetime import timedelta

        from . import crawl, insights
        from .importers import poll_collections

        poll_collections(self.shop, fetch=self.answers([("pre-orders", 1)], {"pre-orders": [self.box_json()]}), now=self.now)
        self.assertTrue(ImportRun.objects.get().ok)
        health = {s["name"]: s for s in insights.report(7)["shops_health"]}
        self.assertIsNone(health["Pulse Shop"]["last_ok"])
        self.assertEqual(health["Pulse Shop"]["preorders"], 1)
        self.assertIsNone(crawl.last_finished())
        # An open pulse never shows the shop as being read.
        ImportRun.objects.update(finished_at=None)
        rows = {row["retailer"].name: row for row in crawl.shops(self.now + timedelta(minutes=1))}
        self.assertEqual(rows["Pulse Shop"]["state"], "Idle")

    def test_poll_preorders_looks_at_due_shops_or_one_by_hand(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command
        from django.core.management.base import CommandError

        fetch = self.answers([("pre-orders", 1)], {"pre-orders": [self.box_json()]})
        out = StringIO()
        with mock.patch("catalogue.importers.fetch", fetch):
            call_command("poll_preorders", stdout=out)
            self.assertEqual(Listing.objects.get(product=self.box).availability, Listing.Availability.PREORDER)
            call_command("poll_preorders", stdout=out)   # just looked at: not due
            call_command("poll_preorders", "--shop", "pulse-shop", stdout=out)   # by hand: looked at now
        self.assertEqual(out.getvalue().splitlines(), [
            "Pulse Shop: read pre-orders, 1 listings checked.", "No shop is due a look.", "Pulse Shop: nothing changed.",
        ])
        with self.assertRaises(CommandError):
            call_command("poll_preorders", "--shop", "nowhere", stdout=out)


class PreorderSignalTests(TestCase):
    def test_preorder_tags_in_their_usual_spellings(self):
        from .importers import PREORDER_TAG

        for tag in ("Pre-Orders-Live", "Coming Soon", "pre-order", "Preorder", "PRE ORDERS", "coming-soon", "pre-order live"):
            self.assertTrue(PREORDER_TAG.match(tag), tag)
        for tag in ("Pre-Order - Inventory Trigger", "not pre-order", "coming soon 2027 maybe", "new-release"):
            self.assertFalse(PREORDER_TAG.match(tag), tag)

    def test_pre_release_event_tickets_are_not_products(self):
        for title in ("Pokemon TCG Destined Rivals Pre-Release Event Ticket", "One Piece OP-13 Pre Release Event Entry",
                      "Riftbound Prerelease Event Saturday"):
            self.assertIsNone(classify(title, "", "", (), Decimal("25")), title)

    def test_the_shops_publishing_time_is_kept_from_the_first_read(self):
        from .importers import Offer

        shop = make_retailer("Shop")
        product = make_product(make_set(make_game()), ean="0820650851230")
        first = timezone.now() - timezone.timedelta(days=2)
        offer = Offer(title="Box", url="https://shop.example/products/box", price=Decimal("40"), ean="0820650851230",
                      published_at=first)
        apply_offers(shop, [offer])
        offer.published_at = timezone.now()   # the shop republished it
        apply_offers(shop, [offer])
        self.assertEqual(Listing.objects.get(product=product).shop_published_at, first)
