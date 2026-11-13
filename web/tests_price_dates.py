"""The Price check and the price history name the same days. A review, 13 Nov 2026: "The page gives two different
dates for the £99.99 low: 12 Nov in the price check and 13 Nov in the price history. The chart also starts on 16 Aug,
while the text says the record began on 20 Oct"."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from catalogue.models import DailyLowestPrice
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .charts import price_chart


class ChartStartTests(TestCase):
    def test_the_chart_starts_on_the_first_day_recorded_not_the_windows(self):
        today = date(2026, 11, 13)
        points = [(date(2026, 10, 20), Decimal("110.00")), (date(2026, 11, 1), Decimal("105.00")),
                  (today, Decimal("99.99"))]
        chart = price_chart(points, 90, today)
        self.assertEqual((chart["start"], chart["end"]), (date(2026, 10, 20), today))
        # The first point sits at the left edge.
        self.assertTrue(chart["path"].startswith("M0.0,"))

    def test_a_record_longer_than_the_window_starts_at_the_window(self):
        today = date(2026, 11, 13)
        points = [(today - timedelta(days=200), Decimal("110.00")), (today, Decimal("99.99"))]
        self.assertEqual(price_chart(points, 90, today)["start"], today - timedelta(days=89))


class LowestDateTests(TestCase):
    def test_today_at_the_lowest_is_named_by_both(self):
        today = timezone.localdate()
        box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="surging-box")
        make_listing(box, make_retailer("Harbour Games"), price="99.99", delivery="0.00")
        make_listing(box, make_retailer("Kestrel Cards"), price="120.00", delivery="0.00")
        for back in range(1, 21):
            DailyLowestPrice.objects.create(product=box, date=today - timedelta(days=back),
                                            price=Decimal("99.99") if back == 1 else Decimal("110.00"))
        DailyLowestPrice.objects.create(product=box, date=today, price=Decimal("99.99"))
        page = self.client.get(box.get_absolute_url())
        fair = page.context["fair"]
        self.assertEqual((fair.low, fair.low_date), (Decimal("99.99"), today))
        self.assertEqual(page.context["chart"]["low_date"], today)
