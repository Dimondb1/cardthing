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
from .models import Listing, OutboundClick, Restock, Retailer
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


class RestockRecordTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", product_type="booster_box")
        self.shop = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")

    def flip(self, listing, availability, at):
        pricing.record_check(listing, price=Decimal("139.99"), delivery_cost=Decimal("2.99"), availability=availability, checked_at=at)

    def test_a_restock_is_kept_with_its_delivered_price(self):
        listing = make_listing(self.product, self.shop, availability="out_of_stock")
        self.flip(listing, "in_stock", timezone.now())
        restock = Restock.objects.get()
        self.assertEqual((restock.product, restock.retailer, restock.listing, restock.price), (self.product, self.shop, listing, Decimal("142.98")))

    def test_a_flicker_within_two_hours_counts_once(self):
        listing = make_listing(self.product, self.shop, availability="out_of_stock")
        start = timezone.now() - timedelta(hours=5)
        self.flip(listing, "in_stock", start)
        self.flip(listing, "out_of_stock", start + timedelta(minutes=20))
        self.flip(listing, "in_stock", start + timedelta(minutes=40))
        self.assertEqual(Restock.objects.count(), 1)
        self.flip(listing, "out_of_stock", start + timedelta(hours=3))
        self.flip(listing, "in_stock", start + timedelta(hours=4))
        self.assertEqual(Restock.objects.count(), 2)

    def test_marketplaces_are_left_out(self):
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        listing = make_listing(self.product, ebay, availability="out_of_stock")
        self.flip(listing, "in_stock", timezone.now())
        listing.refresh_from_db()
        self.assertIsNotNone(listing.back_in_stock_at)
        self.assertEqual(Restock.objects.count(), 0)

    def test_summary_counts_the_window_and_finds_the_busy_hours(self):
        other = make_retailer("Other")
        now = timezone.now()
        for days_ago in range(1, 7):
            at = timezone.make_aware(timezone.datetime.combine(timezone.localdate(now) - timedelta(days=days_ago), timezone.datetime.min.time())) + timedelta(hours=9, minutes=30)
            Restock.objects.create(product=self.product, retailer=self.shop if days_ago > 1 else other, at=at, price="140.00")
        Restock.objects.create(product=self.product, retailer=self.shop, at=now - timedelta(days=45, hours=3), price="140.00")
        summary = pricing.restock_summary(self.product, now=now)
        self.assertEqual((summary["count"], summary["days"], summary["latest"].retailer, summary["band"]), (6, 30, other, ("9am", "11am")))

    def test_summary_falls_back_to_the_last_restock_ever_and_is_none_without_any(self):
        self.assertIsNone(pricing.restock_summary(self.product))
        old = Restock.objects.create(product=self.product, retailer=self.shop, at=timezone.now() - timedelta(days=200), price="140.00")
        summary = pricing.restock_summary(self.product)
        self.assertEqual((summary["count"], summary["latest"], summary["band"]), (0, old, None))

    def test_log_groups_by_day_newest_first_and_marks_sold_out_again(self):
        live = make_listing(self.product, self.shop, availability="in_stock")
        gone = make_listing(make_product(self.product.product_set, name="Surging Sparks Elite Trainer Box", slug="ss-etb"), self.shop, availability="out_of_stock")
        now = timezone.now()
        Restock.objects.create(product=live.product, retailer=self.shop, listing=live, at=now - timedelta(minutes=5), price="140.00")
        Restock.objects.create(product=gone.product, retailer=self.shop, listing=gone, at=now - timedelta(days=1, hours=1), price="50.00")
        Restock.objects.create(product=gone.product, retailer=self.shop, listing=gone, at=now - timedelta(days=9), price="50.00")
        log = pricing.restock_log(days=7, now=now)
        self.assertEqual([day for day, _ in log], [timezone.localdate(now), timezone.localdate(now - timedelta(days=1, hours=1))])
        self.assertEqual([[e.still_in_stock for e in events] for _, events in log], [[True], [False]])

    def test_backfill_turns_stamps_into_rows_once(self):
        listing = make_listing(self.product, self.shop, availability="in_stock")
        when = timezone.now() - timedelta(hours=3)
        Listing.objects.filter(pk=listing.pk).update(back_in_stock_at=when)
        ebay = make_listing(self.product, make_retailer("eBay", source_type=Retailer.Source.EBAY), availability="in_stock")
        Listing.objects.filter(pk=ebay.pk).update(back_in_stock_at=when)
        out = StringIO()
        call_command("backfill_restocks", stdout=out)
        call_command("backfill_restocks", stdout=out)
        self.assertEqual(out.getvalue(), "1 restocks recorded.\n0 restocks recorded.\n")
        self.assertEqual(Restock.objects.get().at, when)


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


class WatchedFirstTests(TestCase):
    def test_viewed_and_watched_products_take_half_the_budget_before_clicked_ones(self):
        from catalogue.models import DailyPageView

        game_set = make_set(make_game())
        shop = make_retailer("Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")
        viewed = make_product(game_set, name="Viewed Box", slug="viewed-box", product_type="booster_box")
        saved = make_product(game_set, name="Saved Box", slug="saved-box", product_type="booster_box")
        clicked = make_product(game_set, name="Clicked Box", slug="clicked-box", product_type="booster_box")
        quiet = make_product(game_set, name="Quiet Box", slug="quiet-box", product_type="booster_box")
        listings = {
            p.slug: make_listing(p, shop, availability="out_of_stock", url=f"https://shop.example/products/{p.slug}", hours_ago=5)
            for p in (viewed, saved, clicked, quiet)
        }
        today = timezone.localdate()
        DailyPageView.objects.create(date=today, kind="product", key="viewed-box", hits=9)
        DailyPageView.objects.create(date=today - timedelta(days=1), kind="watched", key="saved-box", hits=4)
        DailyPageView.objects.create(date=today - timedelta(days=5), kind="product", key="quiet-box", hits=50)   # too old
        OutboundClick.objects.create(product=clicked, retailer=shop)
        asked = []

        def fetch(url):
            asked.append(url)
            return json.dumps({"variants": [{"price": 4999, "available": False}]}).encode()

        with mock.patch("catalogue.importers.fetch", fetch):
            call_command("watch_stock", "--limit", "4", "--pause", "0", stdout=StringIO())
        # Half the budget (two) to the most watched, then the clicked one, then the rest by age.
        self.assertEqual(asked, [f"https://shop.example/products/{slug}.js" for slug in ("viewed-box", "saved-box", "clicked-box", "quiet-box")])
        self.assertEqual(len(listings), 4)


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
