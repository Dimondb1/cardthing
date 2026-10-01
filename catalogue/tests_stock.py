import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import pricing
from .management.commands.watch_stock import check_listing, shopify_js_url
from .models import Listing, OutboundClick, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class BackInStockTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", product_type="booster_box")
        self.shop = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")

    def test_a_listing_that_comes_back_is_stamped_and_shown(self):
        listing = make_listing(self.product, self.shop, availability="out_of_stock", url="https://shop.example/products/surging-sparks-booster-box")
        pricing.record_check(listing, price=Decimal("139.99"), delivery_cost=Decimal("0"), availability="in_stock")
        listing.refresh_from_db()
        self.assertIsNotNone(listing.back_in_stock_at)
        self.assertEqual([p.pk for p, _ in pricing.back_in_stock()], [self.product.pk])
        self.assertContains(self.client.get(reverse("web:home")), "Back in stock")

    def test_staying_in_stock_or_a_new_listing_is_not_a_restock(self):
        listing = make_listing(self.product, self.shop, availability="in_stock")
        pricing.record_check(listing, price=Decimal("1"), delivery_cost=Decimal("0"), availability="in_stock")
        listing.refresh_from_db()
        self.assertIsNone(listing.back_in_stock_at)
        self.assertEqual(pricing.back_in_stock(), [])

    def test_old_restocks_drop_off(self):
        listing = make_listing(self.product, self.shop, availability="in_stock")
        Listing.objects.filter(pk=listing.pk).update(back_in_stock_at=timezone.now() - timedelta(hours=72))
        self.assertEqual(pricing.back_in_stock(), [])


class WatchStockTests(TestCase):
    def test_shopify_listing_is_read_from_the_product_json(self):
        product = make_product(make_set(make_game()))
        shop = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")
        listing = make_listing(product, shop, availability="out_of_stock", url="https://shop.example/products/etb?variant=1")
        self.assertEqual(shopify_js_url(listing.url), "https://shop.example/products/etb.js")
        payload = json.dumps({"variants": [{"price": 4999, "available": True}, {"price": 5999, "available": True}]}).encode()
        self.assertEqual(check_listing(listing, fetch=lambda url: payload), (Decimal("49.99"), "in_stock"))
        sold_out = json.dumps({"variants": [{"price": 4999, "available": False}]}).encode()
        self.assertEqual(check_listing(listing, fetch=lambda url: sold_out), (Decimal("49.99"), "out_of_stock"))

    def test_command_updates_stock_and_reports_restocks(self):
        product = make_product(make_set(make_game()))
        shop = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")
        listing = make_listing(product, shop, availability="out_of_stock", url="https://shop.example/products/etb", hours_ago=5)
        payload = json.dumps({"variants": [{"price": 4999, "available": True}]}).encode()
        out = StringIO()
        with mock.patch("catalogue.importers.fetch", lambda url: payload):
            call_command("watch_stock", "--pause", "0", stdout=out)
        listing.refresh_from_db()
        self.assertEqual((listing.availability, listing.price), ("in_stock", Decimal("49.99")))
        self.assertIsNotNone(listing.back_in_stock_at)
        self.assertIn("1 back in stock", out.getvalue())


class ShopReportTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Paldea Evolved Booster Box")
        self.small = make_retailer(name="Jet Cards", slug="jet-cards")
        self.big = make_retailer(name="Big Shop", slug="big-shop")
        make_listing(self.product, self.small, price="90.00")
        make_listing(self.product, self.big, price="99.00")
        OutboundClick.objects.create(product=self.product, retailer=self.small)

    def run_report(self, *args):
        out = StringIO()
        call_command("shop_report", *args, stdout=out)
        return out.getvalue()

    def test_one_shop_report_names_wins_and_clicks(self):
        text = self.run_report("jet-cards")
        self.assertIn("Cheapest UK shop on: 1 products", text)
        self.assertIn("Clicks sent in the last 30 days: 1", text)
        self.assertIn("Big Shop: 1 products", text)
        self.assertIn("£9.00 under Big Shop", text)

    def test_table_lists_every_active_shop(self):
        text = self.run_report()
        self.assertIn("Jet Cards", text)
        self.assertIn("Big Shop", text)

    def test_unknown_slug_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_report("nowhere")
