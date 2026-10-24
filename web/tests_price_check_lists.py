"""The Price check in lists: each product's band stored each hour (fair_price.refresh_bands), the Fairest price
sort, the gauge on product cards and the dashboard's coverage. The owner: "Add fairest price to filters, show on
dashboard percentage of products that have price gauge ... I also want to see price gauge in the listings view"."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from catalogue import cardmarket, fair_price
from catalogue.models import CardmarketMatch, CardmarketPrice, DailyLowestPrice, Listing, Product
from catalogue.tests_cardmarket import files
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .tests_dashboard import DashboardCase


def record(product, prices, today):
    for back, price in enumerate(reversed(prices), start=1):
        DailyLowestPrice.objects.create(product=product, date=today - timedelta(days=back), price=Decimal(price))


class BandTests(TestCase):
    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()
        self.game = make_game()
        self.set = make_set(self.game)
        self.harbour, self.northgate = make_retailer("Harbour Games"), make_retailer("Northgate Cards")

    def product(self, slug, price, history=(), shops=2, kind=Product.Type.BOOSTER_BOX):
        product = make_product(self.set, name=slug.replace("-", " ").title(), slug=slug, product_type=kind)
        make_listing(product, self.harbour, price=price, delivery="0.00")
        if shops > 1:
            make_listing(product, self.northgate, price="999.00", delivery="0.00",
                         availability=Listing.Availability.OUT_OF_STOCK)
        record(product, history, self.today)
        return product

    def test_the_stored_band_is_the_verdict_the_product_page_gives(self):
        good = self.product("good-box", "52.00", ["50.00"] + ["60.00"] * 19)
        high = self.product("high-box", "75.00", ["60.00"] * 20)
        lowest = self.product("lowest-box", "40.00", ["50.00"] * 20)
        new = self.product("new-box", "40.00", ["50.00"] * 5)                  # too few days
        lone = self.product("lone-box", "40.00", ["50.00"] * 20, shops=1)      # nothing to compare with
        with self.assertNumQueries(7):   # one is the RRP shops give; an eighth, the exchange rate, once Cardmarket prices one
            changed = fair_price.refresh_bands(self.today)
        self.assertEqual(changed, 3)
        for product in (good, high, lowest, new, lone):
            product.refresh_from_db()
            listing = Listing.objects.filter(product=product, availability=Listing.Availability.IN_STOCK).get()
            live = fair_price.verdict(product, listing.delivered_price, self.today,
                                      shops=product.listings.values("retailer").distinct().count())
            with self.subTest(product=product.slug):
                self.assertEqual(product.price_band, live.band if live else None)
        self.assertEqual([p.price_check.key for p in (good, high, lowest)], ["good", "high", "lowest"])
        self.assertEqual(good.price_basis, fair_price.RECORD)
        # Nothing changed: nothing written. A verdict gone (sold out): emptied.
        self.assertEqual(fair_price.refresh_bands(self.today), 0)
        Listing.objects.filter(product=good).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(fair_price.refresh_bands(self.today), 1)
        good.refresh_from_db()
        self.assertEqual((good.price_band, good.price_basis), (None, ""))

    def test_cardmarkets_price_counts_and_both_take_the_lower_band(self):
        cardmarket.run(["pokemon"], read=files)
        CardmarketPrice.objects.update(date=self.today)
        lone = self.product("sm8b-box", "1199.99", shops=1)
        CardmarketMatch.objects.create(product=lone, cardmarket_id=550461, status=CardmarketMatch.Status.MATCHED)
        fair_price.refresh_bands(self.today)
        lone.refresh_from_db()
        self.assertEqual((lone.price_check.key, lone.price_basis), ("high", fair_price.MARKET))
        self.assertEqual(lone.price_band, fair_price.verdict(lone, Decimal("1199.99"), self.today, shops=1).band)

    def test_the_hourly_tidy_and_its_own_command_keep_them(self):
        product = self.product("good-box", "52.00", ["50.00"] + ["60.00"] * 19)
        out = StringIO()
        call_command("refresh_price_checks", stdout=out)
        self.assertIn("1 Price checks changed.", out.getvalue())
        product.refresh_from_db()
        self.assertEqual(product.price_check.key, "good")
        with open("catalogue/management/commands/tidy_all.py", encoding="utf-8") as handle:
            self.assertIn('call_command("refresh_price_checks", stdout=self.stdout)', handle.read())
        with open("deploy/install.sh", encoding="utf-8") as handle:
            self.assertIn("manage.py refresh_price_checks", handle.read())


class ListTests(BandTests):
    def test_fairest_price_sorts_by_the_price_check_best_first(self):
        self.product("aaa-high", "75.00", ["60.00"] * 20)
        self.product("bbb-none", "10.00")
        self.product("ccc-lowest", "40.00", ["50.00"] * 20)
        self.product("ddd-good", "52.00", ["50.00"] + ["60.00"] * 19)
        fair_price.refresh_bands(self.today)
        html = self.client.get(self.set.get_absolute_url() + "?sort=fair").content.decode()
        start = html.index('<li class="card')
        order = [html.index(name, start) for name in ("Ccc Lowest", "Ddd Good", "Aaa High", "Bbb None")]
        self.assertEqual(order, sorted(order))
        self.assertIn('<option value="fair" selected>Fairest price</option>', html)

    def test_a_card_shows_the_gauge_with_no_query_of_its_own(self):
        lowest = self.product("ccc-lowest", "40.00", ["50.00"] * 20)
        self.product("bbb-none", "10.00")
        url = self.set.get_absolute_url()
        self.client.get(url)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as before:
            self.client.get(url)
        fair_price.refresh_bands(self.today)
        cache.clear()
        self.client.get(url)
        with CaptureQueriesContext(connection) as after:
            html = self.client.get(url).content.decode()
        self.assertEqual(len(after), len(before))
        start = html.index('<li class="card')
        card = html[html.index(lowest.name, start) - 2000:html.index(lowest.name, start) + 2500]
        self.assertIn('<span class="fair fair--lowest fair--small"><span class="visually-hidden">Price check: </span>', card)
        self.assertEqual(html.count('class="fair fair--'), 1)    # the product with no verdict shows none


class CoverageTests(DashboardCase):
    def test_the_dashboard_says_how_many_show_the_price_check_and_from_what(self):
        today = timezone.localdate()
        game = make_game()
        product_set = make_set(game)
        harbour, northgate = make_retailer("Harbour Games"), make_retailer("Northgate Cards")
        products = []
        for n in range(4):
            product = make_product(product_set, name=f"Box {n}", slug=f"box-{n}", product_type=Product.Type.BOOSTER_BOX)
            make_listing(product, harbour, price="50.00")
            make_listing(product, northgate, price="70.00", availability=Listing.Availability.OUT_OF_STOCK)
            products.append(product)
        Product.objects.filter(pk=products[0].pk).update(price_band=4, price_basis=fair_price.RECORD)
        Product.objects.filter(pk=products[1].pk).update(price_band=3, price_basis=fair_price.BOTH)
        Product.objects.filter(pk=products[2].pk).update(price_band=2, price_basis=fair_price.MARKET)
        CardmarketMatch.objects.create(product=products[3], status=CardmarketMatch.Status.CHECK)
        response = self.page()
        prices = response.context["prices"]
        self.assertEqual((prices["priced"], prices["checked"], prices["market"], prices["record"]), (4, 3, 2, 2))
        self.assertEqual((prices["share"], prices["market_share"]), (Decimal(75), Decimal(50)))
        self.assertContains(response, "Price check coverage")
        self.assertContains(response, "75.0%")
        self.assertContains(response, "3 of 4 priced, 2 from Cardmarket")
        self.assertContains(response, "1 Cardmarket match waits for you")
        self.assertContains(response, "/admin/catalogue/cardmarketmatch/?status__exact=check")
        del today

    def test_the_dashboard_says_how_claudes_cardmarket_matching_is_going(self):
        # The owner: "Why aren't I seeing Claude stats about matching Cardmarket products to old price history".
        from catalogue.models import CardmarketProduct, ClaudeAsk, ClaudeJudge

        game = make_game()
        product_set = make_set(game)
        products = [make_product(product_set, name=f"Box {n}", slug=f"box-{n}") for n in range(5)]
        cm = CardmarketProduct.objects.create(pk=1, game=game, name="Box", category_id=1, category="Booster Boxes",
                                              expansion_id=1, seen_at=timezone.now())
        CardmarketPrice.objects.create(product=cm, date=timezone.localdate() - timedelta(days=1), trend=Decimal("50"))
        CardmarketPrice.objects.create(product=cm, date=timezone.localdate(), trend=Decimal("51"))
        status = CardmarketMatch.Status
        for product, state in zip(products, (status.MATCHED, status.CONFIRMED, status.CHECK, status.NONE)):
            CardmarketMatch.objects.create(product=product, status=state, cardmarket=cm if state != status.NONE else None)
        ClaudeAsk.objects.create(kind=ClaudeAsk.Kind.MARKET, row_key="market:1", fingerprint="x", model_asked="m",
                                 effort="low", cost_micros=1_250_000)
        matching = self.page().context["prices"]["matching"]
        self.assertEqual({k: matching[k] for k in ("products", "asked", "left", "matched", "waiting", "none")},
                         {"products": 5, "asked": 4, "left": 1, "matched": 2, "waiting": 1, "none": 1})
        self.assertEqual((matching["asks_month"], matching["spent"], matching["history_days"]), (1, "$1.25", 2))
        cache.clear()
        response = self.page()
        self.assertContains(response, "Claude matching Cardmarket")
        self.assertContains(response, "4 of 5, 1 to go")
        self.assertContains(response, "Spent this month on matching: $1.25, products asked about: 1.")
        self.assertContains(response, "Cardmarket price history: 2 days kept, from")
        # Claude off: matching is paused, and the card says so.
        self.assertContains(response, "Claude is switched off, so matching is paused.")
        ClaudeJudge.objects.update_or_create(pk=1, defaults={"enabled": True})
        cache.clear()
        self.assertNotContains(self.page(), "matching is paused")
