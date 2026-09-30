from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .delivery import check_delivery, page_text, parse_delivery, sample_variant, shopify_delivery, uk_rate
from .importers import ImportError_
from .models import Retailer
from .testing import make_retailer

DELIVERY_PAGE = b"""<html><body><nav><a href="/pages/delivery">Delivery</a></nav>
<h1>Delivery</h1><p>We send everything by Royal Mail.</p>
<p>Free UK delivery on all orders over &pound;50.</p>
<table><tr><td>Royal Mail Tracked 48</td><td>&pound;3.49</td></tr>
<tr><td>Royal Mail Tracked 24</td><td>&pound;4.99</td></tr>
<tr><td>DPD next day</td><td>&pound;7.99</td></tr></table>
<script>var x = "&pound;999";</script></body></html>"""


class DeliveryPageTests(TestCase):
    def test_page_text_drops_scripts_and_menus(self):
        text = page_text(DELIVERY_PAGE)
        self.assertNotIn("£999", text)
        self.assertIn("Tracked 48 £3.49", text)

    def test_charges_are_read_with_their_sentences(self):
        cost, free_over, cost_quote, free_quote = parse_delivery(page_text(DELIVERY_PAGE))
        self.assertEqual((cost, free_over), (Decimal("3.49"), Decimal("50")))
        self.assertIn("Tracked 48", cost_quote)
        self.assertIn("over £50", free_quote)

    def test_other_wordings(self):
        for text, expected in [
            ("Free delivery available from £40.", (None, Decimal("40"))),
            ("Orders over £75 qualify for free shipping. Standard delivery costs £2.95.", (Decimal("2.95"), Decimal("75"))),
            ("Spend £30 and get free postage. 2nd Class: £1.50", (Decimal("1.50"), Decimal("30"))),
            ("Shipping is £3.99 to the UK.", (Decimal("3.99"), None)),
            ("Our courier service is £25.", (None, None)),
        ]:
            cost, free_over, _c, _f = parse_delivery(text)
            self.assertEqual((cost, free_over), expected, text)

    def test_delivery_page_is_found_through_the_home_page_link(self):
        def fetch(url):
            if url == "https://shop.example/":
                return b'<a href="/pages/how-we-post">Postage and returns</a>'
            if url == "https://shop.example/pages/how-we-post":
                return DELIVERY_PAGE
            raise ImportError_("missing")

        finding = check_delivery("https://shop.example", fetch=fetch)
        self.assertEqual(finding.url, "https://shop.example/pages/how-we-post")
        self.assertEqual((finding.cost, finding.free_over), (Decimal("3.49"), Decimal("50")))
        self.assertTrue(finding.complete)

    def test_no_delivery_page_gives_none(self):
        def fetch(url):
            raise ImportError_("missing")

        self.assertIsNone(check_delivery("https://shop.example", fetch=fetch))


class FakeSession:
    """A Shopify basket that charges £3.49, free from £50."""

    def __init__(self):
        self.quantity = 0
        self.calls = []

    def post(self, url, payload):
        self.calls.append(url)
        if url.endswith("/cart/add.js"):
            self.quantity = payload["items"][0]["quantity"]
        if url.endswith("/cart/clear.js"):
            self.quantity = 0
        return {}

    def get(self, url):
        self.calls.append(url)
        total = Decimal("6.00") * self.quantity
        price = "0.00" if total >= 50 else "3.49"
        return {"shipping_rates": [
            {"name": "Royal Mail Tracked 48", "price": price, "currency": "GBP"},
            {"name": "Click and collect", "price": "0.00", "currency": "GBP"},
            {"name": "DPD next day", "price": "7.99", "currency": "GBP"},
        ]}


PRODUCTS = b'{"products": [{"title": "Sleeves", "variants": [{"id": 1, "price": "1.00", "available": true}]}, {"title": "Booster Pack", "variants": [{"id": 2, "price": "6.00", "available": true, "requires_shipping": true}]}]}'


class BasketCheckTests(TestCase):
    def test_sample_variant_is_cheap_in_stock_and_posted(self):
        import json

        self.assertEqual(sample_variant(json.loads(PRODUCTS)["products"]), (2, Decimal("6.00")))

    def test_uk_rate_is_the_cheapest_real_delivery_option(self):
        session = FakeSession()
        self.assertEqual(uk_rate(session, "https://shop.example", 2, 1), Decimal("3.49"))
        self.assertEqual(session.calls[-1], "https://shop.example/cart/clear.js")

    def test_basket_check_confirms_the_threshold(self):
        cost, confirmed = shopify_delivery("https://shop.example", session=FakeSession(), fetch=lambda url: PRODUCTS, free_over=Decimal("50"))
        self.assertEqual((cost, confirmed), (Decimal("3.49"), True))
        cost, confirmed = shopify_delivery("https://shop.example", session=FakeSession(), fetch=lambda url: PRODUCTS, free_over=Decimal("500"))
        self.assertEqual((cost, confirmed), (Decimal("3.49"), False))


class CheckDeliveryCommandTests(TestCase):
    def test_apply_saves_figures_with_their_source(self):
        from unittest import mock

        retailer = make_retailer("Shop", website="https://shop.example/", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")

        def fetch(url):
            if url.endswith("/pages/delivery"):
                return DELIVERY_PAGE
            if url.endswith("products.json?limit=250"):
                return PRODUCTS
            raise ImportError_("missing")

        out = StringIO()
        with mock.patch("catalogue.importers.fetch", fetch), mock.patch("catalogue.delivery.CartSession", FakeSession):
            call_command("check_delivery", "--apply", stdout=out)
        retailer.refresh_from_db()
        self.assertEqual((retailer.delivery_cost, retailer.free_delivery_over), (Decimal("3.49"), Decimal("50")))
        self.assertIn("basket check", retailer.delivery_note)
        self.assertIn("confirmed by basket", retailer.delivery_note)
        self.assertIn("read ", retailer.delivery_note)
        self.assertIn("saved:", out.getvalue())
