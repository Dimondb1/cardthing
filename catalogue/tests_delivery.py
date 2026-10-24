from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .delivery import check_delivery, free_thresholds, page_text, parse_delivery, sample_variant, shopify_delivery, uk_rate
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
            ("Standard shipping is £4 or free on orders over £150.", (Decimal("4"), Decimal("150"))),
            ("Royal Mail Tracked 48 £3.99 (free on orders over £50)", (Decimal("3.99"), Decimal("50"))),
            # Unicorn Cards' page, 24 Oct 2026: the sealed threshold, not the single cards' one.
            ("UK Shipping. Enjoy FREE Royal Mail 48h Tracked delivery on single card orders over £20 and on sealed "
             "product orders over £30!. You can upgrade your order to Royal Mail Tracked 24.", (None, Decimal("30"))),
            ("Free delivery on single card orders over £10.", (None, None)),
            ("Free international shipping on orders over £150. Free UK shipping on all orders over £60.",
             (None, Decimal("60"))),
            ("FREE tracked international delivery on orders over £150.", (None, None)),
            ("FREE Royal Mail 48h Tracked delivery on all orders over £45.", (None, Decimal("45"))),
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

    def post_form(self, url, fields):
        self.calls.append(url)

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


PRODUCTS = b'{"products": [{"title": "Sleeves", "variants": [{"id": 1, "price": "1.00", "available": true}]}, {"title": "Charizard ex 199/165 (Near Mint)", "variants": [{"id": 3, "price": "4.00", "available": true}]}, {"title": "Pokemon Surging Sparks Booster Pack", "variants": [{"id": 2, "price": "6.00", "available": true, "requires_shipping": true}]}]}'


class BasketCheckTests(TestCase):
    def test_sample_variant_is_a_cheap_in_stock_sealed_product(self):
        import json

        # The single card (id 3) is cheaper but a booster pack pays the real parcel rate.
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


class ShopBasket:
    """A Shopify basket priced as a shop that sells abroad prices it: in dollars until it is told the visitor is
    in the UK. Tracked 48 is £3.00, free over £20 (Kongs Cards, 24 Oct 2026). ``refuse`` holds variants its
    basket will not take."""

    def __init__(self, refuse=()):
        self.prices = {}
        self.basket = Decimal("0")
        self.pounds = False
        self.refuse = set(refuse)
        self.calls = []

    def post_form(self, url, fields):
        self.calls.append(url)
        self.pounds = url.endswith("/localization") and fields.get("country_code") == "GB"

    def post(self, url, payload):
        self.calls.append(url)
        if url.endswith("/cart/add.js"):
            item = payload["items"][0]
            if item["id"] in self.refuse:
                raise ImportError_("Could not fetch cart/add.js: HTTP Error 422: Unprocessable Entity")
            self.basket = self.prices[item["id"]] * item["quantity"]
        if url.endswith("/cart/clear.js"):
            self.basket = Decimal("0")
        return {}

    def get(self, url):
        self.calls.append(url)
        currency = "GBP" if self.pounds else "USD"
        return {"shipping_rates": [
            {"name": "Free UK Delivery", "price": "0.00", "currency": currency},
            {"name": "Royal Mail Tracked 48", "price": "3.00" if self.pounds else "5.00", "currency": currency},
        ][0 if self.basket >= 20 else 1:]}


def shop_products(*rows):
    import json

    return json.dumps({"products": [
        {"title": title, "variants": [{"id": pk, "price": price, "available": True}]} for pk, title, price in rows
    ]}).encode()


class AbroadBasketTests(TestCase):
    PRODUCTS = shop_products((1, "Riftbound Vendetta Vault Bundle", "34.99"), (2, "Riftbound Vendetta Booster Pack", "5.49"),
                             (3, "Pokemon Surging Sparks Booster Pack", "6.00"))

    def basket(self, refuse=()):
        session = ShopBasket(refuse)
        session.prices = {1: Decimal("34.99"), 2: Decimal("5.49"), 3: Decimal("6.00")}
        return session

    def test_the_basket_is_priced_in_pounds_and_under_a_free_threshold_no_page_gave(self):
        session = self.basket()
        cost, confirmed = shopify_delivery("https://shop.example", session=session, fetch=lambda url: self.PRODUCTS)
        # The bundle's basket was free (over £20): a cheaper product gave the real charge, in pounds.
        self.assertEqual((cost, confirmed), (Decimal("3.00"), False))
        self.assertLess(session.calls.index("https://shop.example/localization"),
                        session.calls.index("https://shop.example/cart/add.js"))

    def test_a_product_the_basket_refuses_gives_way_to_the_next(self):
        cost, _ = shopify_delivery("https://shop.example", session=self.basket(refuse={2}), fetch=lambda url: self.PRODUCTS,
                                   free_over=Decimal("20"))
        self.assertEqual(cost, Decimal("3.00"))

    def test_free_everywhere_stays_free(self):
        session = self.basket()
        session.prices = {1: Decimal("34.99"), 2: Decimal("25.00"), 3: Decimal("26.00")}
        products = shop_products((1, "Riftbound Vendetta Vault Bundle", "34.99"), (2, "Riftbound Booster Box", "25.00"),
                                 (3, "Surging Sparks Booster Bundle", "26.00"))
        cost, _ = shopify_delivery("https://shop.example", session=session, fetch=lambda url: products)
        self.assertEqual(cost, Decimal("0"))

    def test_thresholds_name_what_they_are_for(self):
        found = free_thresholds("Enjoy FREE Royal Mail 48h Tracked delivery on single card orders over £20 and on "
                                "sealed product orders over £30!")
        self.assertEqual([(figure, what) for figure, what, _ in found], [(Decimal("20"), "singles"), (Decimal("30"), "sealed")])
        self.assertIn("sealed product orders over £30", found[1][2])


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

    def test_one_shops_fault_never_stops_the_rest_and_unknown_charges_go_first(self):
        from unittest import mock

        known = make_retailer("Alpha Cards", website="https://alpha.example/", delivery_cost=Decimal("2.99"))
        make_retailer("Broken Shop", website="https://broken.example/")
        unknown = make_retailer("Zulu Cards", website="https://zulu.example/")
        make_retailer("eBay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY)
        asked = []

        def fetch(url):
            asked.append(url.split("/")[2])
            if "broken.example" in url:
                raise ValueError("a page that is not a page")
            if url == "https://zulu.example/pages/delivery":
                return b"<p>Free UK delivery on orders over &pound;40. Standard delivery &pound;2.50</p>"
            raise ImportError_("missing")

        out = StringIO()
        with mock.patch("catalogue.importers.fetch", fetch):
            call_command("check_delivery", "--apply", stdout=out)
        unknown.refresh_from_db()
        self.assertEqual((unknown.delivery_cost, unknown.free_delivery_over), (Decimal("2.50"), Decimal("40")))
        self.assertIn("Broken Shop: check failed: a page that is not a page", out.getvalue())
        order = list(dict.fromkeys(asked))
        self.assertEqual(order, ["broken.example", "zulu.example", "alpha.example"])
        self.assertNotIn("www.ebay.co.uk", order)
        asked.clear()
        with mock.patch("catalogue.importers.fetch", fetch):
            call_command("check_delivery", "--unknown", stdout=StringIO())
        self.assertEqual(list(dict.fromkeys(asked)), ["broken.example"])
        known.refresh_from_db()
        self.assertEqual(known.delivery_cost, Decimal("2.99"))
