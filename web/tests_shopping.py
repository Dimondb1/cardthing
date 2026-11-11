"""The shopping list (web/shopping.py): the products a visitor means to buy and the cheapest way to buy the lot, each
shop's delivery charged once under its own rules, against buying each product on its own."""

import itertools
import random
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Listing, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import shopping

D = Decimal


class ListCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.product_set = make_set(make_game())
        self.box = make_product(self.product_set, name="Surging Sparks Booster Box", slug="ss-box",
                                product_type="booster_box")
        self.etb = make_product(self.product_set, name="Surging Sparks Elite Trainer Box", slug="ss-etb")
        self.tin = make_product(self.product_set, name="Surging Sparks Tin", slug="ss-tin", product_type="tin")

    def shop(self, name, delivery=None, free_over=None, **kwargs):
        return make_retailer(name, delivery_cost=None if delivery is None else D(delivery),
                             free_delivery_over=None if free_over is None else D(free_over), **kwargs)

    def offer(self, product, shop, price, delivery=None, **kwargs):
        """A listing whose delivery is the shop's rule for one item, as an import sets it."""
        if delivery is None:
            charge = shop.delivery_for(D(price))
            delivery, kwargs["delivery_known"] = (charge if charge is not None else D("0")), charge is not None
        return make_listing(product, shop, price=price, delivery=str(delivery), **kwargs)

    def on_list(self, *products):
        self.client.cookies[shopping.COOKIE] = ".".join(product.slug for product in products)


class PlanTests(ListCase):
    def test_one_shop_charges_delivery_once(self):
        harbour = self.shop("Harbour Games", delivery="3.99")
        self.offer(self.box, harbour, "140.00")
        self.offer(self.etb, harbour, "45.00")
        plan = shopping.plan([self.box.slug, self.etb.slug])
        self.assertEqual(plan.shops, 1)
        self.assertEqual(plan.total, D("188.99"))
        self.assertEqual(plan.separately, D("192.98"))
        self.assertEqual(plan.saving, D("3.99"))

    def test_a_free_delivery_threshold_can_make_one_shop_cheapest(self):
        # On its own the box is cheapest at Kestrel and the ETB at Moor; together Harbour's free delivery over £150
        # makes it cheapest for both.
        harbour = self.shop("Harbour Games", delivery="4.99", free_over="150.00")
        kestrel = self.shop("Kestrel Cards", delivery="0.00")
        moor = self.shop("Moor Toys", delivery="2.00")
        self.offer(self.box, harbour, "139.00")    # 143.99 alone
        self.offer(self.box, kestrel, "142.00")    # 142.00 alone, the cheapest alone
        self.offer(self.etb, harbour, "44.00")     # 48.99 alone
        self.offer(self.etb, moor, "45.00")        # 47.00 alone, the cheapest alone
        plan = shopping.plan([self.box.slug, self.etb.slug])
        self.assertEqual([order.retailer for order in plan.orders], [harbour])
        self.assertEqual(plan.total, D("183.00"))
        self.assertEqual(plan.separately, D("189.00"))
        self.assertEqual(plan.saving, D("6.00"))
        self.assertEqual(plan.orders[0].delivery, D("0.00"))

    def test_no_saving_when_each_alone_is_cheapest(self):
        harbour = self.shop("Harbour Games", delivery="0.00")
        kestrel = self.shop("Kestrel Cards", delivery="0.00")
        self.offer(self.box, harbour, "140.00")
        self.offer(self.etb, kestrel, "45.00")
        plan = shopping.plan([self.box.slug, self.etb.slug])
        self.assertEqual((plan.total, plan.separately, plan.saving), (D("185.00"), D("185.00"), None))

    def test_marketplace_offers_are_each_their_own_order(self):
        ebay = self.shop("eBay", source_type=Retailer.Source.EBAY)
        self.offer(self.box, ebay, "130.00", delivery="5.00")
        self.offer(self.etb, ebay, "40.00", delivery="3.00")
        plan = shopping.plan([self.box.slug, self.etb.slug])
        self.assertEqual(plan.shops, 2)
        self.assertEqual(plan.total, D("178.00"))
        self.assertTrue(all(order.marketplace for order in plan.orders))

    def test_unknown_delivery_is_used_only_when_nothing_else_has_it(self):
        mystery = self.shop("Mystery Cards")   # no delivery rule
        harbour = self.shop("Harbour Games", delivery="3.99")
        self.offer(self.box, mystery, "100.00")
        self.offer(self.box, harbour, "140.00")
        self.offer(self.tin, mystery, "20.00")
        plan = shopping.plan([self.box.slug, self.tin.slug])
        box_order = next(order for order in plan.orders if self.box in [l.product for l in order.listings])
        self.assertEqual(box_order.retailer, harbour)
        tin_order = next(order for order in plan.orders if order.retailer == mystery)
        self.assertIsNone(tin_order.delivery)
        self.assertEqual(plan.unknown, 1)
        # The total holds every product and the delivery we know; no saving is claimed past a charge we do not.
        self.assertEqual(plan.total, D("163.99"))
        self.assertIsNone(plan.saving)

    def test_a_shop_without_a_rule_but_known_item_charges_is_counted_as_separate_parcels(self):
        feed = self.shop("Feed Cards")
        self.offer(self.box, feed, "140.00", delivery="2.50", delivery_known=True)
        self.offer(self.etb, feed, "45.00", delivery="2.50", delivery_known=True)
        plan = shopping.plan([self.box.slug, self.etb.slug])
        self.assertEqual(plan.orders[0].delivery, D("5.00"))

    def test_out_of_stock_products_are_listed_apart(self):
        harbour = self.shop("Harbour Games", delivery="0.00")
        self.offer(self.box, harbour, "140.00")
        self.offer(self.tin, harbour, "20.00", availability=Listing.Availability.OUT_OF_STOCK)
        plan = shopping.plan([self.box.slug, self.tin.slug])
        self.assertEqual(plan.missing, [self.tin])
        self.assertEqual(plan.total, D("140.00"))

    def test_a_preorder_counts_only_when_nobody_has_it_in_stock(self):
        harbour = self.shop("Harbour Games", delivery="0.00")
        kestrel = self.shop("Kestrel Cards", delivery="0.00")
        self.offer(self.box, harbour, "150.00")
        self.offer(self.box, kestrel, "120.00", availability=Listing.Availability.PREORDER)
        self.offer(self.etb, kestrel, "50.00", availability=Listing.Availability.PREORDER)
        plan = shopping.plan([self.box.slug, self.etb.slug])
        chosen = {listing.product: listing for order in plan.orders for listing in order.listings}
        self.assertEqual(chosen[self.box].retailer, harbour)
        self.assertEqual(chosen[self.etb].availability, Listing.Availability.PREORDER)

    def test_a_doubtful_price_is_left_out(self):
        harbour = self.shop("Harbour Games", delivery="0.00")
        kestrel = self.shop("Kestrel Cards", delivery="0.00")
        self.offer(self.box, harbour, "150.00")
        self.offer(self.box, kestrel, "40.00", sanity=Listing.Sanity.DOUBTFUL)
        plan = shopping.plan([self.box.slug])
        self.assertEqual(plan.total, D("150.00"))

    def test_the_search_finds_the_cheapest_on_a_long_list(self):
        """Past EXACT_LIMIT the plan is searched, not tried in full: on random lists it matches the full search."""
        rng = random.Random(7)
        shops = [self.shop(f"Shop {n}", delivery=str(rng.choice([0, 2.99, 3.99, 4.99])),
                           free_over=rng.choice([None, "60.00", "100.00"])) for n in range(5)]
        products = [make_product(self.product_set, name=f"Product {n}", slug=f"p-{n}") for n in range(7)]
        for product in products:
            for shop in rng.sample(shops, 4):
                self.offer(product, shop, f"{rng.randint(20, 90)}.00")
        options = [shopping.candidates(list(Listing.objects.filter(product=product).select_related("retailer")))
                   for product in products]
        best = min(shopping.plan_cost(choice) for choice in itertools.product(*options))
        starts = [listed[0] for listed in options]
        self.assertEqual(shopping.plan_cost(shopping.improve(starts, options)), best)


class CookieTests(ListCase):
    def test_bad_and_repeated_slugs_are_ignored(self):
        request = type("R", (), {"COOKIES": {shopping.COOKIE: "a.a.B!.b..c"}})()
        self.assertEqual(shopping.read(request), ["a", "b", "c"])

    def test_the_list_holds_twenty(self):
        slugs = [f"p{n}" for n in range(shopping.MAX_ITEMS)]
        self.assertEqual(shopping.added(slugs, "one-more"), slugs)


class PageTests(ListCase):
    def setUp(self):
        super().setUp()
        self.harbour = self.shop("Harbour Games", delivery="3.99", free_over="150.00")
        self.box_offer = self.offer(self.box, self.harbour, "140.00")
        self.offer(self.etb, self.harbour, "45.00")

    def test_add_to_list_works_without_script(self):
        page = self.client.get(self.box.get_absolute_url())
        self.assertContains(page, reverse("web:list_add", args=[self.box.slug]))
        self.assertContains(page, "Add to list")
        response = self.client.post(reverse("web:list_add", args=[self.box.slug]))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], self.box.get_absolute_url())
        self.assertEqual(response.cookies[shopping.COOKIE].value, self.box.slug)
        self.assertTrue(response.cookies[shopping.COOKIE]["httponly"])
        page = self.client.get(self.box.get_absolute_url())
        self.assertContains(page, "See your list (1)")
        self.assertNotContains(page, reverse("web:list_add", args=[self.box.slug]))

    def test_add_to_list_with_script_gets_the_new_button(self):
        self.on_list(self.etb)
        response = self.client.post(reverse("web:list_add", args=[self.box.slug]), HTTP_X_LIST="1")
        self.assertEqual(response.json()["count"], 2)
        self.assertIn("See your list (2)", response.json()["html"])
        self.assertEqual(response.cookies[shopping.COOKIE].value, f"{self.etb.slug}.{self.box.slug}")

    def test_the_list_page_shows_the_plan_and_the_saving(self):
        self.on_list(self.box, self.etb)
        page = self.client.get(reverse("web:shopping_list"))
        self.assertContains(page, "Cheapest way to buy the lot")
        self.assertContains(page, "from 1 shop, delivery included")
        # £185 with free delivery over £150, against £143.99 + £48.99 one at a time.
        self.assertContains(page, "£185.00 with free delivery.")
        self.assertContains(page, "the same would cost £192.98. You save £7.98.")
        self.assertContains(page, f"/go/{self.box_offer.pk}/?b=list")
        self.assertContains(page, 'rel="sponsored nofollow noopener"')
        self.assertContains(page, "Prices on this list were checked between")
        self.assertEqual(page["Cache-Control"], "private, no-store")
        self.assertContains(page, 'content="noindex')

    def test_remove_and_clear(self):
        self.on_list(self.box, self.etb)
        response = self.client.post(reverse("web:list_remove", args=[self.box.slug]))
        self.assertEqual(response.cookies[shopping.COOKIE].value, self.etb.slug)
        response = self.client.post(reverse("web:list_clear"))
        self.assertEqual(response.cookies[shopping.COOKIE].value, "")
        self.assertContains(self.client.get(reverse("web:shopping_list")), "Your list is empty")

    def test_an_empty_list_says_how_to_fill_it(self):
        self.assertContains(self.client.get(reverse("web:shopping_list")), "Tap Add to list on any product")

    def test_the_page_costs_the_same_queries_however_long_the_list(self):
        self.on_list(self.box)
        with CaptureQueriesContext(connection) as one:
            self.client.get(reverse("web:shopping_list"))
        more = [make_product(self.product_set, name=f"Extra {n}", slug=f"extra-{n}") for n in range(6)]
        for product in more:
            self.offer(product, self.harbour, "30.00")
        self.on_list(self.box, self.etb, *more)
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("web:shopping_list"))
        self.assertEqual(len(many), len(one))

    def test_a_buy_click_is_saved_as_from_the_list(self):
        from catalogue.models import OutboundClick

        self.on_list(self.box)
        self.client.get(f"/go/{self.box_offer.pk}/?b=list", HTTP_REFERER="http://testserver/shopping-list/",
                        HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile Safari/604.1",
                        HTTP_SEC_FETCH_SITE="same-origin", HTTP_SEC_FETCH_MODE="navigate", HTTP_SEC_FETCH_DEST="document")
        click = OutboundClick.objects.get()
        self.assertEqual(click.button, "list")

    def test_the_terms_say_what_the_list_keeps(self):
        self.assertContains(self.client.get(reverse("web:terms")), "Your shopping list is kept in a small cookie")

    def test_the_list_stays_out_of_search_engines(self):
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /shopping-list/")
