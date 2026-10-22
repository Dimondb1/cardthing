"""Price check: today's cheapest delivered price against the product's own daily record (fair_price.py)."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from . import fair_price
from .models import DailyLowestPrice
from .testing import make_game, make_listing, make_product, make_retailer, make_set


def record(product, prices, today):
    """One DailyLowestPrice a day, the last of ``prices`` yesterday."""
    for back, price in enumerate(reversed(prices), start=1):
        DailyLowestPrice.objects.create(product=product, date=today - timedelta(days=back), price=Decimal(price))


class VerdictTests(TestCase):
    def setUp(self):
        self.today = date(2026, 10, 22)
        self.etb = make_product(make_set(make_game()), slug="pre-etb")

    def test_no_verdict_without_enough_days(self):
        record(self.etb, ["50.00"] * (fair_price.MIN_DAYS - 1), self.today)
        self.assertIsNone(fair_price.verdict(self.etb, Decimal("40.00"), self.today))
        # Today's own row is not history: it is the price being judged.
        DailyLowestPrice.objects.create(product=self.etb, date=self.today, price=Decimal("40.00"))
        self.assertIsNone(fair_price.verdict(self.etb, Decimal("40.00"), self.today))
        DailyLowestPrice.objects.create(product=self.etb, date=self.today - timedelta(days=fair_price.MIN_DAYS),
                                        price=Decimal("50.00"))
        self.assertIsNotNone(fair_price.verdict(self.etb, Decimal("40.00"), self.today))

    def test_days_older_than_the_window_do_not_count(self):
        for back in range(fair_price.WINDOW_DAYS + 1, fair_price.WINDOW_DAYS + 40):
            DailyLowestPrice.objects.create(product=self.etb, date=self.today - timedelta(days=back), price=Decimal("50"))
        self.assertIsNone(fair_price.verdict(self.etb, Decimal("40.00"), self.today))

    def test_only_pokemon_while_on_trial(self):
        lorcana = make_product(make_set(make_game("Disney Lorcana", "lorcana"), name="The First Chapter", slug="tfc"),
                               name="The First Chapter Booster Box", slug="tfc-box")
        record(lorcana, ["100.00"] * 20, self.today)
        self.assertIsNone(fair_price.verdict(lorcana, Decimal("80.00"), self.today))
        self.assertIsNone(fair_price.verdict(self.etb, None, self.today))

    def test_bands_against_the_average_and_the_low(self):
        record(self.etb, ["48.00"] + ["50.00"] * 9 + ["52.00"] * 10, self.today)
        # Average 50.90, lowest 48.00.
        cases = [("48.00", "lowest"), ("47.50", "lowest"), ("48.35", "good"), ("50.90", "fair"), ("53.44", "fair"),
                 ("53.45", "above"), ("58.53", "above"), ("58.54", "high")]
        for price, key in cases:
            with self.subTest(price=price):
                self.assertEqual(fair_price.verdict(self.etb, Decimal(price), self.today).key, key)

    def test_the_verdict_names_its_figures_in_two_queries(self):
        record(self.etb, ["45.00", "45.00"] + ["50.00"] * 16 + ["45.00", "55.00"], self.today)
        with self.assertNumQueries(2):
            verdict = fair_price.verdict(self.etb, Decimal("49.00"), self.today)
        self.assertEqual((verdict.days, verdict.average, verdict.low), (20, Decimal("49.50"), Decimal("45.00")))
        # The latest day at the low, and the first day recorded.
        self.assertEqual(verdict.low_date, self.today - timedelta(days=2))
        self.assertEqual(verdict.since, self.today - timedelta(days=20))
        self.assertEqual([item["key"] for item in verdict.bands], ["lowest", "good", "fair", "above", "high"])
        self.assertEqual([item["key"] for item in verdict.bands if item["on"]], ["fair"])


class ProductPageTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.etb = make_product(make_set(make_game()), slug="pre-etb")
        self.harbour = make_retailer("Harbour Games")

    def test_the_price_check_shows_beside_the_cheapest_price_and_opens_its_sheet(self):
        record(self.etb, ["50.00"] + ["60.00"] * 19, self.today)
        make_listing(self.etb, self.harbour, price="52.00", delivery="0.00")
        response = self.client.get(self.etb.get_absolute_url())
        self.assertEqual(response.context["fair"].key, "good")
        self.assertContains(response, 'class="fair fair--good"')
        self.assertContains(response, 'aria-label="Price check: Good price. Show price history"')
        self.assertContains(response, 'href="#history-title" data-fair-open')
        self.assertContains(response, "data-fair-sheet")
        self.assertContains(response, "Average of 20 days")
        self.assertContains(response, "From the cheapest delivered price we have recorded each day since")
        # The facts sit under the price history too, for anyone without the sheet.
        self.assertContains(response, 'class="fair-facts"', count=2)

    def test_no_price_check_without_the_days_or_a_known_delivery_cost(self):
        record(self.etb, ["60.00"] * 5, self.today)
        make_listing(self.etb, self.harbour, price="52.00", delivery="0.00")
        response = self.client.get(self.etb.get_absolute_url())
        self.assertIsNone(response.context["fair"])
        self.assertNotContains(response, "data-fair-open")
        self.assertNotContains(response, "fair-facts")

        record(self.etb, ["60.00"] * 20, self.today - timedelta(days=5))
        self.etb.listings.update(delivery_known=False)
        response = self.client.get(self.etb.get_absolute_url())
        self.assertIsNone(response.context["fair"])
        self.assertNotContains(response, "data-fair-open")


class AtLowestTests(TestCase):
    """The Deals page's Lowest in 90 days: at the lowest of those days now, and well under their average."""

    def setUp(self):
        self.today = timezone.localdate()
        self.pokemon = make_set(make_game())
        self.harbour = make_retailer("Harbour Games")

    def product(self, slug, history, now, game_set=None):
        product = make_product(game_set or self.pokemon, name=slug.replace("-", " ").title(), slug=slug)
        record(product, history, self.today)
        make_listing(product, self.harbour, price=now, delivery="0.00")
        return product

    def test_only_products_at_their_low_and_well_under_their_average(self):
        deep = self.product("deep", ["100.00"] * 19 + ["80.00"], "70.00")
        shallow = self.product("shallow", ["60.00"] * 19 + ["50.00"], "50.00")
        self.product("flat", ["40.00"] * 20, "40.00")              # never moved: not news
        self.product("above-low", ["90.00"] + ["100.00"] * 19, "92.00")   # under average but not at its low
        self.product("too-new", ["100.00"] * 5, "60.00")             # not enough days
        self.product("huge-fall", ["100.00"] * 20, "20.00")          # a wrong link, not a low
        lorcana = make_set(make_game("Disney Lorcana", "lorcana"), name="The First Chapter", slug="tfc", code="TFC")
        self.product("lorcana-box", ["100.00"] * 20, "80.00", game_set=lorcana)   # not on trial
        with self.assertNumQueries(1):
            rows = fair_price.at_lowest(today=self.today)
        self.assertEqual(rows, [deep, shallow])
        self.assertEqual(rows[0].low_days, 20)
        self.assertEqual(round(rows[0].low_average, 2), Decimal("99.00"))

    def test_days_before_the_window_do_not_set_the_low(self):
        product = make_product(self.pokemon, slug="old-low")
        for back in range(fair_price.LOW_DAYS + 1, fair_price.LOW_DAYS + 5):
            DailyLowestPrice.objects.create(product=product, date=self.today - timedelta(days=back), price=Decimal("30"))
        record(product, ["100.00"] * 20, self.today)
        make_listing(product, self.harbour, price="80.00", delivery="0.00")
        self.assertEqual(fair_price.at_lowest(today=self.today), [product])

    def test_the_deals_page_lists_them(self):
        self.product("deep", ["100.00"] * 19 + ["80.00"], "70.00")
        response = self.client.get("/deals/")
        self.assertContains(response, "Lowest in 90 days")
        self.assertContains(response, "Average £99.00")
        self.assertContains(response, "Deep")
