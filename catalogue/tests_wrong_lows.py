"""Wrong prices in the daily record (catalogue/pricing.py wrong_lows, prune_wrong_lows). The owner, 6 Nov 2026, with
a £230 box whose Price check said "Lowest £13.99" from one wrong day: "once upon a time, it was listed at being
£13.99, which is pretty unlikely"."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from . import fair_price, pricing
from .models import DailyLowestPrice
from .testing import make_game, make_product, make_set


def rows(prices, today=None):
    """[(date, price)] oldest first, the last one yesterday."""
    today = today or timezone.localdate()
    return [(today - timedelta(days=len(prices) - i), Decimal(price)) for i, price in enumerate(prices)]


class WrongLowsTests(TestCase):
    def test_a_short_dip_far_under_the_median_with_a_normal_price_after_it_is_wrong(self):
        record = rows(["230"] * 10 + ["13.99"] + ["230"] * 10)
        self.assertEqual(pricing.wrong_lows(record), [record[10]])
        # Up to three days at a time, and a dip at the start of the record too.
        record = rows(["13.99"] * 3 + ["230"] * 20 + ["20", "25", "30"] + ["230"] * 5)
        self.assertEqual(pricing.wrong_lows(record), record[:3] + record[23:26])

    def test_a_real_fall_stays(self):
        # Four days and more at the low: a fall, left alone.
        record = rows(["230"] * 10 + ["50"] * 4 + ["230"] * 10)
        self.assertEqual(pricing.wrong_lows(record), [])
        # A fall still going: no normal price after it to say it was wrong.
        record = rows(["230"] * 20 + ["50"] * 2)
        self.assertEqual(pricing.wrong_lows(record), [])
        # A fall that lasted: the median moved with it.
        record = rows(["230"] * 10 + ["50"] * 30)
        self.assertEqual(pricing.wrong_lows(record), [])

    def test_a_dip_to_a_quarter_of_the_median_is_a_price(self):
        self.assertEqual(pricing.wrong_lows(rows(["100"] * 10 + ["25"] + ["100"] * 10)), [])
        self.assertEqual(len(pricing.wrong_lows(rows(["100"] * 10 + ["24.99"] + ["100"] * 10))), 1)

    def test_too_few_days_give_no_median_to_trust(self):
        self.assertEqual(pricing.wrong_lows(rows(["230"] * 6 + ["13.99"] + ["230"] * 6)), [])


class PruneTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.box = make_product(make_set(make_game()), name="Strongest Warriors Booster Box", slug="osw-box")
        for day, price in rows(["230"] * 20 + ["13.99"] + ["230"] * 11, self.today):
            DailyLowestPrice.objects.create(product=self.box, date=day, price=price)

    def test_the_wrong_day_goes_and_the_price_check_reads_the_real_low(self):
        # With the wrong day in the record, £215 is only a fair price: the average is dragged down and the "low" is
        # one no shop will ever reach again.
        before = fair_price.verdict(self.box, Decimal("215.00"), self.today)
        self.assertEqual((before.low, before.key), (Decimal("13.99"), "fair"))
        self.assertEqual(pricing.prune_wrong_lows(today=self.today), 1)
        self.assertFalse(DailyLowestPrice.objects.filter(price=Decimal("13.99")).exists())
        self.assertEqual(DailyLowestPrice.objects.count(), 31)
        after = fair_price.verdict(self.box, Decimal("215.00"), self.today)
        self.assertEqual((after.low, after.key), (Decimal("230.00"), "lowest"))
        self.assertEqual(pricing.prune_wrong_lows(today=self.today), 0)

    def test_days_outside_the_price_checks_window_are_left_alone(self):
        old = self.today - timedelta(days=fair_price.WINDOW_DAYS + 10)
        for day, price in rows(["230"] * 20 + ["13.99"] + ["230"] * 11, old):
            DailyLowestPrice.objects.create(product=self.box, date=day, price=price)
        self.assertEqual(pricing.prune_wrong_lows(today=self.today), 1)
        self.assertEqual(DailyLowestPrice.objects.filter(price=Decimal("13.99")).count(), 1)

    def test_the_hourly_price_check_refresh_removes_them_before_storing_the_bands(self):
        out = StringIO()
        call_command("refresh_price_checks", stdout=out)
        self.assertIn("1 wrong prices removed from the daily record.", out.getvalue())
        self.assertFalse(DailyLowestPrice.objects.filter(price=Decimal("13.99")).exists())
