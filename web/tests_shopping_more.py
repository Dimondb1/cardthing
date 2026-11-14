"""The shopping list, more convenient (web/shopping.py): how many of each product, and next to the cheapest way the one
with the fewest shops. The owner, 14 Nov 2026: "provide the most convenient purchase based on availability, delivery
as it could be cheaper overall to get it through one seller"."""

from decimal import Decimal

from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from catalogue.models import Retailer
from web import shopping

from .tests_shopping import ListCase

D = Decimal


class CookieTests(ListCase):
    def request(self, value):
        request = RequestFactory().get("/")
        request.COOKIES[shopping.COOKIE] = value
        return request

    def test_quantities_ride_in_the_cookie_and_an_old_cookie_still_reads(self):
        self.assertEqual(shopping.read_items(self.request("ss-box.ss-etb")), [("ss-box", 1), ("ss-etb", 1)])
        self.assertEqual(shopping.read_items(self.request("ss-box~3.ss-etb~99.ss-tin~x.ss-box~2")),
                         [("ss-box", 3), ("ss-etb", 10), ("ss-tin", 1)])
        self.assertEqual(shopping.read(self.request("ss-box~3.ss-etb")), ["ss-box", "ss-etb"])

    def test_writing_keeps_quantities_and_the_count_for_the_header(self):
        response = shopping.write(HttpResponse(), ["ss-box", "ss-etb"], counts={"ss-box": 2})
        self.assertEqual(response.cookies[shopping.COOKIE].value, "ss-box~2.ss-etb")
        self.assertEqual(response.cookies[shopping.COUNT_COOKIE].value, "2")
        self.assertTrue(response.cookies[shopping.COOKIE]["httponly"])
        self.assertFalse(response.cookies[shopping.COUNT_COOKIE]["httponly"])   # the header's script reads it
        cleared = shopping.write(HttpResponse(), [])
        self.assertEqual(cleared.cookies[shopping.COUNT_COOKIE]["max-age"], 0)


class QuantityPlanTests(ListCase):
    def test_two_of_something_counts_twice_and_can_reach_free_delivery(self):
        harbour = self.shop("Harbour Games", delivery="4.99", free_over="100.00")
        self.offer(self.etb, harbour, "60.00")
        plan = shopping.plan([self.etb.slug], {self.etb.slug: 2})
        [order] = plan.orders
        self.assertEqual((order.subtotal, order.delivery, plan.total), (D("120.00"), D("0.00"), D("120.00")))
        self.assertTrue(plan.many)
        self.assertEqual(order.listings[0].qty, 2)

    def test_an_ebay_offers_delivery_is_counted_for_each_one(self):
        ebay = Retailer.objects.create(name="eBay", slug="ebay", website="https://www.ebay.co.uk/",
                                       source_type=Retailer.Source.EBAY)
        self.offer(self.box, ebay, "100.00", delivery="5.00", url="https://www.ebay.co.uk/itm/1")
        plan = shopping.plan([self.box.slug], {self.box.slug: 3})
        self.assertEqual(plan.total, D("315.00"))

    def test_separately_is_each_product_alone_for_the_quantity_wanted(self):
        harbour = self.shop("Harbour Games", delivery="4.00")
        self.offer(self.box, harbour, "100.00")
        self.offer(self.etb, harbour, "40.00")
        plan = shopping.plan([self.box.slug, self.etb.slug], {self.etb.slug: 2})
        self.assertEqual(plan.total, D("184.00"))
        self.assertEqual(plan.separately, D("188.00"))   # the box alone 104, two ETBs alone 84
        self.assertEqual(plan.saving, D("4.00"))


class FewestTests(ListCase):
    def setUp(self):
        super().setUp()
        # Cheapest: the box at Kestrel and the ETB at Moor, each with free delivery. Harbour has both, a little dearer.
        self.harbour = self.shop("Harbour Games", delivery="3.00")
        self.kestrel = self.shop("Kestrel Cards", delivery="0.00")
        self.moor = self.shop("Moor Toys", delivery="0.00")
        self.offer(self.box, self.kestrel, "100.00")
        self.offer(self.etb, self.moor, "40.00")
        self.offer(self.box, self.harbour, "101.00")
        self.offer(self.etb, self.harbour, "41.00")

    def test_next_to_the_cheapest_split_it_offers_everything_from_one_shop(self):
        plan = shopping.plan([self.box.slug, self.etb.slug])
        self.assertEqual((plan.shops, plan.total), (2, D("140.00")))
        self.assertEqual((plan.fewest.shops, plan.fewest.total, plan.fewest.extra), (1, D("145.00"), D("5.00")))
        self.assertEqual(plan.fewest.orders[0].retailer, self.harbour)

    def test_no_fewest_when_the_cheapest_is_already_one_shop(self):
        plan = shopping.plan([self.box.slug])
        self.assertIsNone(plan.fewest)

    def test_without_one_shop_for_everything_it_takes_the_fewest_shops(self):
        self.offer(self.tin, self.kestrel, "10.00")
        self.offer(self.tin, self.shop("Wren Games", delivery="0.00"), "9.00")
        plan = shopping.plan([self.box.slug, self.etb.slug, self.tin.slug])
        self.assertEqual(plan.shops, 3)   # box at Kestrel, ETB at Moor, tin at Wren
        self.assertEqual(plan.total, D("149.00"))
        # Two shops at the least cost: the box and the tin at Kestrel, the ETB at Moor.
        self.assertEqual((plan.fewest.shops, plan.fewest.total, plan.fewest.extra), (2, D("150.00"), D("1.00")))
        self.assertEqual({order.retailer for order in plan.fewest.orders}, {self.kestrel, self.moor})

    def test_the_page_shows_both_ways_and_switches_without_script(self):
        self.on_list(self.box, self.etb)
        page = self.client.get(reverse("web:shopping_list"))
        self.assertContains(page, "All from one shop")
        self.assertContains(page, "£5.00 more")
        self.assertEqual([order.retailer for order in page.context["orders"]], [self.kestrel, self.moor])
        page = self.client.get(reverse("web:shopping_list"), {"way": "fewest"})
        self.assertEqual([order.retailer for order in page.context["orders"]], [self.harbour])
        self.assertContains(page, 'href="/shopping-list/?way=fewest" aria-current="true"')


class QuantityPageTests(ListCase):
    def setUp(self):
        super().setUp()
        harbour = self.shop("Harbour Games", delivery="3.00")
        self.offer(self.box, harbour, "100.00")
        self.offer(self.etb, harbour, "40.00")

    def test_the_number_box_sets_how_many_and_the_page_says_to_check_the_basket(self):
        self.on_list(self.box, self.etb)
        response = self.client.post(reverse("web:list_quantity", args=[self.etb.slug]), {"quantity": "3"})
        self.assertRedirects(response, reverse("web:shopping_list"), status_code=303, fetch_redirect_response=False)
        self.assertEqual(self.client.cookies[shopping.COOKIE].value, "ss-box.ss-etb~3")
        page = self.client.get(reverse("web:shopping_list"))
        self.assertContains(page, "each, 3 wanted")
        self.assertContains(page, "check each basket has them all")
        self.assertEqual(page.context["plan"].total, D("223.00"))

    def test_adding_and_removing_keep_the_other_quantities(self):
        self.client.cookies[shopping.COOKIE] = "ss-box~2"
        self.client.post(reverse("web:list_add", args=[self.etb.slug]))
        self.assertEqual(self.client.cookies[shopping.COOKIE].value, "ss-box~2.ss-etb")
        self.client.post(reverse("web:list_remove", args=[self.etb.slug]))
        self.assertEqual(self.client.cookies[shopping.COOKIE].value, "ss-box~2")

    def test_several_products_with_quantities_can_be_added_at_once(self):
        response = self.client.post(reverse("web:list_add_many"), {
            "pick_0": self.box.slug, "qty_0": "2", "pick_1": "", "qty_1": "5", "pick_2": "no-such-product",
            "qty_2": "1", "pick_3": self.etb.slug, "qty_3": "40"})
        self.assertRedirects(response, reverse("web:shopping_list"), status_code=303, fetch_redirect_response=False)
        self.assertEqual(self.client.cookies[shopping.COOKIE].value, "ss-box~2.ss-etb~10")

    def test_the_header_links_to_the_list(self):
        page = self.client.get(reverse("web:home"))
        self.assertContains(page, 'class="masthead__list" href="/shopping-list/"')
        self.assertContains(page, "data-list-count")
