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
            # Four lookups (links, barcodes, catalogue, ignored), a fetch per offer, seven writes and reads for
            # the one change (its save, its product, the day's lowest price), one stamp for the other two
            # and the out-of-stock sweep. Rewriting all three would cost about thirty.
            with self.assertNumQueries(16):
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

        with mock.patch.object(importers, "STAMP_CHUNK", 2), self.assertNumQueries(2):
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
