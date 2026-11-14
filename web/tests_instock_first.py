"""Lists lead with the cheapest price in stock today, as the product page does; a pre-order only when no shop has the
product in stock, and a cheaper pre-order is mentioned under the price. The owner, 14 Nov 2026, on Delta Reign's
booster pack: "It's showing a reserve price as the lowest not the cheapest available price today. It should"."""

from datetime import date
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from catalogue import offers, pricing
from catalogue.models import DailyLowestPrice, Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

D = Decimal
PRE = Listing.Availability.PREORDER


class InStockFirstTests(TestCase):
    def setUp(self):
        self.pack = make_product(make_set(make_game()), name="Delta Reign Booster Pack", slug="dr-pack",
                                 product_type="booster_pack")
        self.wolves = make_retailer("Wolves Pokemart")
        self.harbour = make_retailer("Harbour Games")

    def priced(self):
        return Product.objects.with_prices().get(pk=self.pack.pk)

    def test_the_cheapest_in_stock_leads_not_a_cheaper_pre_order(self):
        make_listing(self.pack, self.wolves, price="5.49", availability=PRE)
        make_listing(self.pack, self.harbour, price="5.95")
        found = self.priced()
        self.assertEqual((found.lowest_known, found.lowest_price), (D("5.95"), D("5.95")))
        product = Product.objects.filter(pk=self.pack.pk).prefetch_related(offers.buyable_prefetch()).get()
        summary = offers.summarise(product)
        self.assertEqual((summary.best.retailer, summary.cheaper_preorder.retailer), (self.harbour, self.wolves))

    def test_with_nobody_in_stock_the_pre_order_leads(self):
        make_listing(self.pack, self.wolves, price="5.49", availability=PRE)
        make_listing(self.pack, self.harbour, price="5.95", availability=PRE)
        self.assertEqual(self.priced().lowest_known, D("5.49"))
        product = Product.objects.filter(pk=self.pack.pk).prefetch_related(offers.buyable_prefetch()).get()
        summary = offers.summarise(product)
        self.assertEqual((summary.best.retailer, summary.cheaper_preorder), (self.wolves, None))

    def test_in_stock_with_delivery_unknown_still_leads_as_plus_delivery(self):
        make_listing(self.pack, self.wolves, price="5.49", availability=PRE)
        make_listing(self.pack, self.harbour, price="5.20", delivery_known=False)
        found = self.priced()
        self.assertEqual((found.lowest_known, found.lowest_price), (None, D("5.20")))

    def test_the_daily_record_keeps_the_in_stock_price(self):
        make_listing(self.pack, self.wolves, price="5.49", availability=PRE)
        make_listing(self.pack, self.harbour, price="5.95")
        pricing.snapshot_all(date(2026, 11, 14))
        self.assertEqual(DailyLowestPrice.objects.get(product=self.pack).price, D("5.95"))

    def test_a_compact_row_says_the_pre_order_is_cheaper(self):
        make_listing(self.pack, self.wolves, price="5.49", availability=PRE)
        make_listing(self.pack, self.harbour, price="5.95")
        product = (Product.objects.for_lists().filter(pk=self.pack.pk).prefetch_related(offers.buyable_prefetch())
                   .get())
        html = render_to_string("web/includes/compact_row.html", {"product": product,
                                                                    "summary": offers.summarise(product)},
                                request=RequestFactory().get("/"))
        self.assertIn("£5.95", html)
        self.assertIn("Harbour Games", html)
        self.assertNotIn("Wolves Pokemart", html)
        self.assertIn("Pre-order from £5.49", html)
