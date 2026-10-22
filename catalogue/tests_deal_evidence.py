"""Deal claims rest on evidence: a saving only against a shop in line with the market, and a verified 30-day low
from our own daily record (catalogue/offers.py)."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from . import offers
from .models import DailyLowestPrice, Product
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class DealEvidenceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.product_set = make_set(make_game())
        self.shops = [make_retailer(name) for name in ("Harbour Games", "Northgate Cards", "Tollgate TCG")]

    def product(self, slug, *prices, history=(), listed_by=2):
        product = make_product(self.product_set, name=slug.replace("-", " ").title(), slug=slug)
        for shop, price in zip(self.shops, prices):
            make_listing(product, shop, price=price, delivery="0.00")
        # Shops listing it but sold out, so it is listed by ``listed_by`` shops in all: a price with a market.
        for shop in self.shops[len(prices):listed_by]:
            make_listing(product, shop, price="999.00", delivery="0.00", availability="out_of_stock")
        for back, price in enumerate(reversed(history), start=1):
            DailyLowestPrice.objects.create(product=product, date=self.today - timedelta(days=back), price=Decimal(price))
        return product

    def summary(self, product):
        loaded = Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()).get(pk=product.pk)
        return offers.summarise(loaded, offers.week_low_map([product.pk], today=self.today))

    def test_one_dear_listing_is_not_a_saving(self):
        # The owner's case: £250 against a lone £699.95 says that shop is dear, not that £250 is a bargain.
        summary = self.summary(self.product("battle-styles-box", "250.00", "699.95"))
        self.assertIsNone(summary.saving)
        self.assertFalse(summary.suspect)
        self.assertFalse(summary.is_deal)
        # A third shop near the dear one shows the market really is there.
        summary = self.summary(self.product("matched-box", "250.00", "699.95", "720.00"))
        self.assertEqual(summary.saving, Decimal("449.95"))
        # A runner-up within half as much again is in line, as before.
        summary = self.summary(self.product("fair-box", "250.00", "300.00"))
        self.assertEqual((summary.saving, summary.percent), (Decimal("50.00"), 17))
        self.assertTrue(summary.is_deal)

    def test_a_tiny_saving_is_true_but_not_a_deal(self):
        summary = self.summary(self.product("tiny", "85.00", "85.49"))
        self.assertEqual(summary.saving, Decimal("0.49"))
        self.assertFalse(summary.is_deal)

    def test_a_verified_30_day_low_needs_14_days_and_is_a_deal_only_under_the_average(self):
        dropped = self.summary(self.product("dropped", "55.00", history=["60.00"] * 20))
        self.assertEqual((dropped.badge, dropped.under), (offers.LOW_30, 8))
        self.assertTrue(dropped.low_deal and dropped.is_deal)
        # A price that never moved is its own low every day: true, and no deal.
        flat = self.summary(self.product("flat", "60.00", history=["60.00"] * 20))
        self.assertFalse(flat.is_deal)
        self.assertIsNone(flat.badge)
        # Too few days to verify a month: the week's badges, and "best in 7 days" only if the week was dearer.
        short = self.summary(self.product("short", "55.00", history=["60.00"] * 10))
        self.assertEqual(short.badge, offers.BEST_WEEK)
        above = self.summary(self.product("above", "65.00", history=["60.00"] * 10))
        self.assertEqual(above.badge, offers.LOWEST_TODAY)
        self.assertFalse(above.is_deal)
        self.assertIsNone(self.summary(self.product("same-week", "60.00", history=["60.00"] * 10)).badge)

    def test_a_product_one_shop_lists_gets_no_badge_whatever_its_history(self):
        dropped = self.summary(self.product("lone-drop", "55.00", history=["60.00"] * 20, listed_by=1))
        self.assertIsNone(dropped.badge)
        self.assertFalse(dropped.is_deal)

    def test_the_lows_are_read_in_one_query(self):
        products = [self.product(f"p{n}", "50.00", history=["55.00"] * 30) for n in range(3)]
        with self.assertNumQueries(1):
            lows = offers.week_low_map([p.pk for p in products], today=self.today)
        row = lows[products[0].pk]
        self.assertEqual((row.week, row.month, row.days), (Decimal("55.00"), Decimal("55.00"), 30))
        self.assertEqual(row.average, Decimal("55.00"))

    def test_featured_deals_are_only_the_ones_with_evidence_strongest_first(self):
        from web.views import featured_pick

        self.product("battle-styles-box", "250.00", "699.95")       # one dear listing: out
        self.product("tiny", "85.00", "85.49")                      # a true saving, too small: out
        self.product("flat", "60.00", history=["60.00"] * 20)       # its own low, never moved: out
        self.product("dropped", "50.00", history=["60.00"] * 20)    # 17% under its month: in
        self.product("fair-box", "250.00", "265.00")                # 6% on a shop in line: in
        candidates = list(Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()))
        rows, _spares = featured_pick(candidates)
        self.assertEqual([product.slug for product, _ in rows], ["dropped", "fair-box"])
