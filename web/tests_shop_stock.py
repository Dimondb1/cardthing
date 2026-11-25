"""Shop stock (web/shop_stock.py): every product one shop lists, its price, whether it is in stock and when it was last
checked, on an Admin page, with a list of every shop to pick from. The owner, 25 Nov 2026: "Is there a place in admin
to view a shops inventory and current stock levels? If not can you create it?"."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Listing, Product, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import shop_report, shop_stock

A = Listing.Availability


class StockCase(TestCase):
    def setUp(self):
        product_set = make_set(make_game())
        self.box = make_product(product_set, name="Surging Sparks Booster Box", slug="surging-sparks-booster-box",
                                product_type="booster_box")
        self.etb = make_product(product_set, name="Surging Sparks Elite Trainer Box", slug="surging-sparks-etb")
        self.tin = make_product(product_set, name="Surging Sparks Tin", slug="surging-sparks-tin", product_type="tin")
        self.blister = make_product(product_set, name="Surging Sparks Blister", slug="surging-sparks-blister")
        self.harbour = make_retailer("Harbour Games")
        self.kestrel = make_retailer("Kestrel Cards")
        # Harbour: the box in stock and cheapest, the ETB on pre-order, the tin out of stock, the blister read days
        # ago (in stock then).
        self.harbour_box = make_listing(self.box, self.harbour, price="140.00", delivery="3.99")
        self.harbour_etb = make_listing(self.etb, self.harbour, price="45.00", availability=A.PREORDER)
        self.harbour_tin = make_listing(self.tin, self.harbour, price="20.00", availability=A.OUT_OF_STOCK)
        self.harbour_blister = make_listing(self.blister, self.harbour, price="5.00", hours_ago=24 * 10)
        # Kestrel: dearer for the box, cheaper for the ETB and the only one with the tin.
        make_listing(self.box, self.kestrel, price="150.00")
        make_listing(self.etb, self.kestrel, price="40.00")
        make_listing(self.tin, self.kestrel, price="22.00")
        self.user = get_user_model().objects.create_user("owner", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.url = reverse("shop_stock_shop", args=[self.harbour.slug])

    def page(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response

    def names(self, response):
        return [row["listing"].product.name for row in response.context["rows"]]


class AccessTests(StockCase):
    def test_only_the_owner_and_staff_open_it(self):
        self.assertEqual(reverse("shop_stock"), "/admin/shop-stock/")
        self.assertEqual(self.url, "/admin/shop-stock/harbour-games/")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("shop_stock")).status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        from guides.apps import EDITORS

        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertEqual(self.client.get(reverse("shop_stock")).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_never_cached_or_indexed(self):
        for url in (reverse("shop_stock"), self.url):
            response = self.client.get(url)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_an_unknown_shop_is_not_found(self):
        self.assertEqual(self.client.get(reverse("shop_stock_shop", args=["no-such-shop"])).status_code, 404)

    def test_admin_home_crawl_health_and_the_shops_settings_lead_here(self):
        self.assertContains(self.client.get(reverse("admin:index")), f'href="{reverse("shop_stock")}"')
        # Crawl health lists the shops read on a schedule, not ones priced by hand.
        Retailer.objects.filter(pk=self.harbour.pk).update(source_type=Retailer.Source.SHOPIFY)
        self.assertContains(self.client.get(reverse("crawl")), f'href="{self.url}"')
        settings_page = self.client.get(reverse("admin:catalogue_retailer_change", args=[self.harbour.pk]))
        self.assertContains(settings_page, f'href="{self.url}"')
        self.assertContains(self.client.get(reverse("admin:catalogue_retailer_add")), "Shown once the shop is saved.")


class ShopListTests(StockCase):
    def test_every_shop_with_what_it_lists_and_has_in_stock(self):
        response = self.client.get(reverse("shop_stock"))
        shops = {shop.name: shop for shop in response.context["shops"]}
        # The stale blister is listed but not counted as in stock.
        self.assertEqual((shops["Harbour Games"].listed, shops["Harbour Games"].in_stock,
                          shops["Harbour Games"].preorder), (4, 1, 1))
        self.assertEqual((shops["Kestrel Cards"].listed, shops["Kestrel Cards"].in_stock), (3, 3))
        self.assertContains(response, f'href="{self.url}"')

    def test_a_switched_off_shop_comes_last_and_says_so(self):
        Retailer.objects.filter(pk=self.harbour.pk).update(is_active=False)
        response = self.client.get(reverse("shop_stock"))
        self.assertEqual([shop.name for shop in response.context["shops"]], ["Kestrel Cards", "Harbour Games"])
        self.assertContains(response, "Switched off")

    def test_find_a_shop(self):
        response = self.client.get(reverse("shop_stock"), {"q": "kestrel"})
        self.assertEqual([shop.name for shop in response.context["shops"]], ["Kestrel Cards"])
        self.assertContains(self.client.get(reverse("shop_stock"), {"q": "zzz"}), "No shop matches")

    def test_one_query_for_the_list_whatever_the_shops(self):
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("shop_stock"))
        for n in range(5):
            shop = make_retailer(f"Shop {n}")
            make_listing(self.box, shop, price=f"{160 + n}.00")
        with CaptureQueriesContext(connection) as many:
            response = self.client.get(reverse("shop_stock"))
        self.assertEqual(len(response.context["shops"]), 7)
        self.assertEqual(len(many), len(few))


class ShopPageTests(StockCase):
    def test_counts_and_in_stock_first(self):
        response = self.page()
        counts = response.context["counts"]
        self.assertEqual((counts["listed"], counts["in_stock"], counts["preorder"], counts["out_of_stock"],
                          counts["stale"], counts["hidden"]), (4, 1, 1, 1, 1, 0))
        self.assertContains(response, "1 in stock of 4 listed")
        self.assertContains(response, f"1 not checked in the last {settings.RIPRAPTOR_STALE_AFTER_HOURS} hours")
        self.assertEqual(self.names(response), ["Surging Sparks Booster Box", "Surging Sparks Elite Trainer Box",
                                                "Surging Sparks Tin", "Surging Sparks Blister"])
        states = [row["state"] for row in response.context["rows"]]
        self.assertEqual(states, ["In stock", "Pre-order", "Out of stock", "In stock when last read"])

    def test_each_row_has_its_price_delivery_and_links(self):
        response = self.page()
        self.assertContains(response, "£143.99")
        self.assertContains(response, "£140.00 + £3.99 delivery")
        self.assertContains(response, "£45.00 + free delivery")
        self.assertContains(response, f'href="{self.harbour_box.url}"')
        self.assertContains(response, f'href="{self.box.get_absolute_url()}"')
        self.assertContains(response, reverse("admin:catalogue_listing_change", args=[self.harbour_box.pk]))

    def test_whether_it_is_the_cheapest_and_who_is_when_not(self):
        rows = {row["listing"].product_id: row for row in self.page().context["rows"]}
        self.assertEqual(rows[self.box.pk]["cheapest"], "This shop")
        self.assertTrue(rows[self.box.pk]["mine"])
        self.assertEqual(rows[self.etb.pk]["cheapest"], "£40.00 at Kestrel Cards")
        self.assertEqual(rows[self.tin.pk]["cheapest"], "£22.00 at Kestrel Cards")
        self.assertEqual(rows[self.blister.pk]["cheapest"], "Not for sale at any shop now")

    def test_a_filter_shows_one_state(self):
        self.assertEqual(self.names(self.page(show="in_stock")), ["Surging Sparks Booster Box"])
        self.assertEqual(self.names(self.page(show="preorder")), ["Surging Sparks Elite Trainer Box"])
        self.assertEqual(self.names(self.page(show="out_of_stock")), ["Surging Sparks Tin"])
        self.assertEqual(self.names(self.page(show="stale")), ["Surging Sparks Blister"])
        # Anything else is everything listed.
        self.assertEqual(len(self.names(self.page(show="nonsense"))), 4)

    def test_hidden_listings_only_under_hidden(self):
        Listing.objects.filter(pk=self.harbour_tin.pk).update(is_active=False)
        Product.objects.filter(pk=self.blister.pk).update(is_active=False)
        response = self.page()
        self.assertEqual(self.names(response), ["Surging Sparks Booster Box", "Surging Sparks Elite Trainer Box"])
        self.assertEqual(response.context["counts"]["hidden"], 2)
        hidden = self.page(show="hidden")
        self.assertEqual({row["state"] for row in hidden.context["rows"]},
                         {"Hidden from the site", "Hidden: its product is hidden or merged"})
        # Its product hidden, it names no cheapest shop.
        self.assertEqual([row["cheapest"] for row in hidden.context["rows"]
                          if row["listing"].product_id == self.blister.pk], [""])

    def test_filters_with_none_are_left_out_but_the_stock_states_stay(self):
        Listing.objects.filter(pk=self.harbour_blister.pk).update(last_checked=timezone.now())
        codes = [item["code"] for item in self.page().context["filters"]]
        self.assertEqual(codes, ["listed", "in_stock", "preorder", "out_of_stock"])
        Listing.objects.filter(pk=self.harbour_tin.pk).update(availability=A.LOTTERY)
        codes = [item["code"] for item in self.page().context["filters"]]
        self.assertIn("lottery", codes)

    def test_an_ebay_price_goes_stale_in_ebays_window(self):
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        make_listing(self.box, ebay, price="130.00", hours_ago=30)
        make_listing(self.etb, ebay, price="44.00", hours_ago=2)
        response = self.client.get(reverse("shop_stock_shop", args=[ebay.slug]))
        counts = response.context["counts"]
        self.assertEqual((counts["in_stock"], counts["stale"]), (1, 1))
        self.assertContains(response, f"1 not checked in the last {settings.RIPRAPTOR_EBAY_STALE_AFTER_HOURS} hours")

    def test_a_kept_out_or_doubted_price_says_so(self):
        Listing.objects.filter(pk=self.harbour_box.pk).update(sanity=Listing.Sanity.EXCLUDED)
        Listing.objects.filter(pk=self.harbour_etb.pk).update(sanity=Listing.Sanity.DOUBTFUL)
        response = self.page()
        self.assertContains(response, "Kept out of the comparison: the price looks wrong for this product")
        self.assertContains(response, "Doubted: it may not be this product")

    def test_a_closed_listing_says_why(self):
        Listing.objects.filter(pk=self.harbour_tin.pk).update(closed_why=Listing.Closed.MEMBERS)
        self.assertContains(self.page(), "Shop members only")

    def test_a_switched_off_or_paused_shop_says_so(self):
        Retailer.objects.filter(pk=self.harbour.pk).update(is_active=False)
        self.assertContains(self.page(), "This shop is switched off")
        Retailer.objects.filter(pk=self.harbour.pk).update(is_active=True, reading_paused=True)
        self.assertContains(self.page(), "Reading is paused")

    def test_find_a_product_by_our_name_or_the_shops(self):
        Listing.objects.filter(pk=self.harbour_tin.pk).update(title="SS Collector Tin Wave 2")
        self.assertEqual(self.names(self.page(q="booster box")), ["Surging Sparks Booster Box"])
        self.assertEqual(self.names(self.page(q="collector tin")), ["Surging Sparks Tin"])
        self.assertContains(self.page(q="zzz"), "Nothing under All listed matches")
        # The search stays when the filter changes.
        self.assertContains(self.page(q="tin"), "?show=in_stock&amp;q=tin")

    def test_pages_of_rows(self):
        with mock.patch.object(shop_stock, "PER_PAGE", 3):
            first = self.page()
            self.assertEqual(len(first.context["rows"]), 3)
            self.assertContains(first, "Page 1 of 2")
            self.assertContains(first, "page=2")
            self.assertEqual(self.names(self.page(page=2)), ["Surging Sparks Blister"])

    def test_the_queries_do_not_grow_with_the_rows(self):
        with CaptureQueriesContext(connection) as few:
            self.page()
        product_set = self.box.product_set
        for n in range(8):
            product = make_product(product_set, name=f"Surging Sparks Pack {n}", slug=f"surging-sparks-pack-{n}",
                                   product_type="booster_pack")
            make_listing(product, self.harbour, price=f"{4 + n}.00")
            make_listing(product, self.kestrel, price=f"{3 + n}.00")
        with CaptureQueriesContext(connection) as many:
            response = self.page()
        self.assertEqual(len(response.context["rows"]), 12)
        self.assertEqual(len(many), len(few))


class LeadRowsTests(StockCase):
    def test_the_lead_shop_its_price_and_whether_delivery_is_known(self):
        leads = shop_report.lead_rows()
        self.assertEqual(leads[self.box.pk], (self.harbour.pk, "Harbour Games", Decimal("143.99"), True))
        self.assertEqual(shop_report.lead_now()[self.etb.pk], self.kestrel.pk)
        self.assertEqual(set(shop_report.lead_rows([self.tin.pk])), {self.tin.pk})

    def test_unknown_delivery_shows_the_item_price(self):
        Listing.objects.filter(pk=self.harbour_box.pk).update(delivery_known=False)
        Listing.objects.filter(product=self.box).exclude(pk=self.harbour_box.pk).delete()
        self.assertEqual(shop_report.lead_rows([self.box.pk])[self.box.pk][2:], (self.harbour_box.price, False))


class StaleWindowTests(StockCase):
    def test_a_listing_just_inside_the_window_is_current(self):
        Listing.objects.filter(pk=self.harbour_blister.pk).update(
            last_checked=timezone.now() - timedelta(hours=71))
        self.assertEqual(self.page().context["counts"]["stale"], 0)
