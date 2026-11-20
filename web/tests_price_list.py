"""Usual UK prices (web/price_list.py) and the usual UK price line on product pages. The owner, 20 Nov 2026, on making
RipRaptor the UK price benchmark by making visible what the site already knows: "We kind of have all this stuff
already"."""

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import fair_price
from catalogue.models import DailyLowestPrice, Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from guides import state as guide_state


def record(product, prices, today):
    for back, price in enumerate(reversed(prices), start=1):
        DailyLowestPrice.objects.create(product=product, date=today - timedelta(days=back), price=Decimal(price))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.today = timezone.localdate()
        self.game = make_game()
        self.set = make_set(self.game)
        self.harbour, self.northgate = make_retailer("Harbour Games"), make_retailer("Northgate Cards")

    def product(self, slug, price, history=(), shops=2, kind=Product.Type.BOOSTER_BOX, shop=None, **fields):
        product = make_product(self.set, name=slug.replace("-", " ").title(), slug=slug, product_type=kind, **fields)
        make_listing(product, shop or self.harbour, price=price, delivery="0.00")
        if shops > 1:
            make_listing(product, self.northgate, price="999.00", delivery="0.00",
                         availability=Listing.Availability.OUT_OF_STOCK)
        record(product, history, self.today)
        return product

    def bands(self):
        fair_price.refresh_bands(self.today)

    def page(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response


class PriceListTests(Base):
    def setUp(self):
        super().setUp()
        self.box = self.product("good-box", "52.00", ["50.00"] + ["60.00"] * 19, rrp=Decimal("54.99"))
        self.etb = self.product("dear-etb", "44.00", ["40.00"] * 20, kind=Product.Type.ELITE_TRAINER_BOX)
        self.new = self.product("new-box", "40.00", ["50.00"] * 5)                  # too few days
        self.lone = self.product("lone-box", "40.00", ["50.00"] * 20, shops=1)      # one shop says nothing
        self.bands()
        self.url = reverse("web:prices_game", args=["pokemon"])

    def test_only_products_with_a_usual_price_listed_with_the_product_pages_figure(self):
        found = self.page(self.url).context
        rows = {row.slug: row for row in found["rows"]}
        self.assertEqual(set(rows), {"good-box", "dear-etb"})
        listing = Listing.objects.get(product=self.box, retailer=self.harbour)
        verdict = fair_price.verdict(self.box, listing.delivered_price, self.today, shops=2)
        box = rows["good-box"]
        self.assertEqual(box.usual, verdict.average)
        self.assertEqual(box.usual, Decimal("59.50"))
        self.assertEqual((box.today, box.rrp_figure, box.check.key), (Decimal("52.00"), Decimal("54.99"), "good"))
        html = self.page(self.url).content.decode()
        self.assertIn("Pokémon: usual UK prices", html)
        self.assertIn('<td class="price-table__usual" data-label="Usual UK price">£59.50</td>', html)
        self.assertIn('<td class="price-table__today" data-label="Cheapest today">£52.00</td>', html)
        self.assertIn("Today&#x27;s prices checked", html)
        self.assertIn("over the last 365 days", html)
        self.assertIn("at least 2 UK shops list it and we have 14 days of its prices", html)

    def test_a_kind_at_a_time_and_a_choice_it_does_not_have_shows_all(self):
        found = self.page(self.url, type="elite_trainer_box").context
        self.assertEqual([row.slug for row in found["rows"]], ["dear-etb"])
        self.assertEqual(found["kinds"], [("booster_box", "Booster box"), ("elite_trainer_box", "Elite Trainer Box")])
        self.assertIn('aria-current="page">Elite Trainer Box</a>', self.page(self.url, type="elite_trainer_box")
                      .content.decode())
        self.assertEqual(len(self.page(self.url, type="tin").context["rows"]), 2)

    def test_never_high_price_beside_a_shop_that_pays_a_commission(self):
        paid = make_retailer("Kestrel Cards", affiliate_url_template="https://track.example/?u={url}")
        self.product("paid-box", "75.00", ["60.00"] * 20, shop=paid)
        self.product("plain-box", "75.00", ["60.00"] * 20)
        self.bands()
        rows = {row.slug: row for row in self.page(self.url).context["rows"]}
        self.assertIsNone(rows["paid-box"].check)
        self.assertEqual(rows["plain-box"].check.key, "high")
        self.assertEqual(rows["paid-box"].usual, Decimal("60.00"))   # the figure is still given

    def test_the_index_lists_the_games_with_a_list_and_an_unknown_game_is_not_found(self):
        html = self.page(reverse("web:prices")).content.decode()
        self.assertIn(f'href="{self.url}">Pokémon</a>', html)
        self.assertIn("2 products", html)
        self.assertEqual(self.client.get(reverse("web:prices_game", args=["no-such-game"])).status_code, 404)
        self.assertIn(f'href="{reverse("web:prices")}"', self.page(reverse("web:home")).content.decode())

    def test_both_pages_are_in_the_sitemap(self):
        xml = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("/prices/</loc>", xml)
        self.assertIn(f"{self.url}</loc>", xml)

    def test_the_same_queries_however_many_products(self):
        with CaptureQueriesContext(connection) as few:
            self.page(self.url)
        for number in range(6):
            self.product(f"more-box-{number}", "52.00", ["60.00"] * 20)
        self.bands()
        with CaptureQueriesContext(connection) as many:
            found = self.page(self.url).context
        self.assertEqual(len(found["rows"]), 8)
        self.assertEqual(len(many), len(few))


class UsualLineTests(Base):
    def test_the_usual_uk_price_under_the_cheapest_with_a_link_to_check_a_price(self):
        box = self.product("good-box", "52.00", ["50.00"] + ["60.00"] * 19)
        cache.set(guide_state.KEY, {"live": True, "doors": {}}, None)
        html = self.page(box.get_absolute_url()).content.decode()
        self.assertIn('<span class="buybox__rrp-figure">Usual UK price £59.50</span>', html)
        self.assertIn(f'href="{reverse("web:fair_price")}?product=good-box">Check a price you were offered</a>', html)

    def test_no_line_without_a_record_and_no_link_while_the_checker_is_closed(self):
        new = self.product("new-box", "40.00", ["50.00"] * 5)
        self.assertNotIn("buybox__usual", self.page(new.get_absolute_url()).content.decode())
        box = self.product("good-box", "52.00", ["50.00"] + ["60.00"] * 19)
        html = self.page(box.get_absolute_url()).content.decode()
        self.assertIn("Usual UK price £59.50", html)
        self.assertNotIn("Check a price you were offered", html)
