"""Multi-buys ("2 for £50") read from a Shopify shop's own collections, shown beside the price."""

import json
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .importers import deal_terms, run_import
from .models import Listing, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set

GEM = "0820650851230"


def shop(products, collections, members, broken=False):
    """A Shopify shop's answers: its products, its collections.json list and each collection's products.

    ``broken`` makes collections.json fail, as a busy shop does."""

    def fetch(url):
        if url.endswith("/meta.json"):
            return b'{"currency": "GBP"}'
        if url.endswith("/collections.json?limit=250"):
            if broken:
                raise OSError("busy")
            return json.dumps({"collections": collections}).encode()
        for handle, items in members.items():
            if f"/collections/{handle}/products.json" in url and "page=1" in url:
                return json.dumps({"products": [{"handle": i} for i in items]}).encode()
        if "/collections/" in url:
            return b'{"products": []}'
        if "page=1" in url:
            return json.dumps({"products": products}).encode()
        return b'{"products": []}'

    return fetch


def box(price="27.99", handle="gem-4-box", available=True):
    return {
        "title": "Pokemon Gem Pack Vol 4 Booster Box (Simplified Chinese)", "handle": handle, "tags": [],
        "variants": [{"price": price, "available": available, "barcode": GEM}],
    }


TWO_FOR_50 = [{"handle": "2-for-50", "title": "2 for £50"}]


class DealTermsTests(TestCase):
    def test_reads_the_usual_names(self):
        self.assertEqual(deal_terms({"title": "2 for £50"}), (2, Decimal("50")))
        self.assertEqual(deal_terms({"title": "Any 3 for £100"}), (3, Decimal("100")))
        self.assertEqual(deal_terms({"title": "Buy 2 for £45.00"}), (2, Decimal("45.00")))

    def test_leaves_other_collections_alone(self):
        for title in ("3 for 2", "Pokemon", "1 for £10", "20 for £5", "2 for £50 and more", "", None):
            with self.subTest(title=title):
                self.assertIsNone(deal_terms({"title": title}))


class ReadDealTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Gem Pack Vol 4 Booster Box", ean=GEM)
        self.retailer = make_retailer(
            "Solent Cards", source_type=Retailer.Source.SHOPIFY, source_url="https://solent.example/",
            delivery_cost=Decimal("3.65"), free_delivery_over=Decimal("50"),
        )

    def read(self, fetch):
        run = run_import(self.retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)
        return Listing.objects.get(product=self.product, retailer=self.retailer)

    def test_a_product_in_the_shops_multi_buy_collection_carries_the_deal(self):
        listing = self.read(shop([box()], TWO_FOR_50, {"2-for-50": ["gem-4-box"]}))
        self.assertEqual((listing.deal_quantity, listing.deal_price), (2, Decimal("50.00")))
        # £50 reaches the shop's free delivery, so each box is £25 delivered.
        self.assertEqual(listing.deal_delivery, Decimal("0.00"))
        self.assertEqual(listing.deal_each, Decimal("25.00"))
        # The listing's own price stays the price of one.
        self.assertEqual(listing.price, Decimal("27.99"))
        self.assertEqual(listing.delivered_price, Decimal("31.64"))

    def test_the_deal_goes_when_the_shop_ends_it(self):
        self.read(shop([box()], TWO_FOR_50, {"2-for-50": ["gem-4-box"]}))
        listing = self.read(shop([box()], [], {}))
        self.assertIsNone(listing.deal_quantity)
        self.assertIsNone(listing.deal_price)
        self.assertIsNone(listing.deal_each)

    def test_a_read_that_cannot_see_the_collections_keeps_the_deal(self):
        self.read(shop([box()], TWO_FOR_50, {"2-for-50": ["gem-4-box"]}))
        listing = self.read(shop([box()], TWO_FOR_50, {}, broken=True))
        self.assertEqual((listing.deal_quantity, listing.deal_price), (2, Decimal("50.00")))

    def test_a_multi_buy_that_saves_nothing_is_not_kept(self):
        listing = self.read(shop([box(price="24.99")], TWO_FOR_50, {"2-for-50": ["gem-4-box"]}))
        self.assertIsNone(listing.deal_quantity)

    def test_a_multi_buy_far_below_the_single_price_is_not_believed(self):
        listing = self.read(shop([box(price="129.99")], TWO_FOR_50, {"2-for-50": ["gem-4-box"]}))
        self.assertIsNone(listing.deal_quantity)

    def test_the_cheaper_of_two_multi_buys_wins(self):
        collections = TWO_FOR_50 + [{"handle": "3-for-70", "title": "Any 3 for £70"}]
        listing = self.read(shop([box()], collections, {"2-for-50": ["gem-4-box"], "3-for-70": ["gem-4-box"]}))
        self.assertEqual((listing.deal_quantity, listing.deal_price), (3, Decimal("70.00")))

    def test_delivery_below_the_free_threshold_is_shared_out(self):
        self.retailer.free_delivery_over = Decimal("60")
        self.retailer.save()
        listing = self.read(shop([box()], TWO_FOR_50, {"2-for-50": ["gem-4-box"]}))
        self.assertEqual(listing.deal_delivery, Decimal("3.65"))
        self.assertEqual(listing.deal_each, Decimal("26.83"))   # (50 + 3.65) / 2, rounded

    def test_an_unchanged_deal_is_not_written_again(self):
        self.read(shop([box()], TWO_FOR_50, {"2-for-50": ["gem-4-box"]}))
        fetch = shop([box()], TWO_FOR_50, {"2-for-50": ["gem-4-box"]})
        with CaptureQueriesContext(connection) as queries:
            self.read(fetch)
        writes = [q["sql"] for q in queries if q["sql"].startswith("UPDATE") and "deal_quantity" in q["sql"]]
        self.assertEqual(writes, [])


class ShowDealTests(TestCase):
    def setUp(self):
        game = make_game()
        self.product = make_product(make_set(game), name="Gem Pack Vol 4 Booster Box")
        self.cheapest = make_listing(self.product, make_retailer("Japan2UK"), price="28.00", delivery="0.00")
        self.solent = make_retailer("Solent Cards", delivery_cost=Decimal("3.65"), free_delivery_over=Decimal("50"))
        self.with_deal = make_listing(self.product, self.solent, price="27.99", delivery="3.65",
                                      deal_quantity=2, deal_price=Decimal("50.00"))
        self.game = game

    def test_product_page_shows_the_deal_on_the_shops_row_and_under_the_cheapest_price(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "2 for £50.00 here, £25.00 each delivered")
        self.assertContains(response, "Or 2 for £50.00 at Solent Cards, £25.00 each delivered")

    def test_the_ranking_stays_on_buying_one(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.context["cheapest"], self.cheapest)

    def test_a_deal_no_cheaper_per_item_is_only_on_its_row(self):
        Listing.objects.filter(pk=self.with_deal.pk).update(deal_price=Decimal("60.00"))
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "2 for £60.00 here, £30.00 each delivered")
        self.assertNotContains(response, "Or 2 for")

    def test_unknown_delivery_on_the_deal_is_said(self):
        Retailer.objects.filter(pk=self.solent.pk).update(free_delivery_over=None, delivery_cost=None)
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "2 for £50.00 here, £25.00 each plus delivery")
        # Never claimed as cheaper against a delivered price.
        self.assertNotContains(response, "Or 2 for")

    def test_free_delivery_on_one_item_is_free_on_the_deal(self):
        Retailer.objects.filter(pk=self.solent.pk).update(free_delivery_over=None, delivery_cost=None)
        Listing.objects.filter(pk=self.with_deal.pk).update(delivery_cost=Decimal("0.00"))
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "Or 2 for £50.00 at Solent Cards, £25.00 each delivered")

    def test_product_cards_show_a_cheaper_deal_without_more_queries(self):
        url = reverse("web:game", args=[self.game.slug])
        self.client.get(url)   # fill the page's caches
        with CaptureQueriesContext(connection) as with_deal:
            response = self.client.get(url)
        self.assertContains(response, "Or 2 for £50.00 at Solent Cards, £25.00 each delivered")
        Listing.objects.filter(pk=self.with_deal.pk).update(deal_quantity=None, deal_price=None)
        self.client.get(url)
        with CaptureQueriesContext(connection) as without:
            response = self.client.get(url)
        self.assertNotContains(response, "Or 2 for")
        self.assertEqual(len(with_deal), len(without))
