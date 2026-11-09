"""Shop delivery (web/delivery.py): the shops whose prices lack a delivery charge, counted on the dashboard and set on
one page. The owner, 9 Nov 2026: "I need an easy way on dashboard to see how many shops don't have delivery and an
easy way to update them at once"."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from catalogue.models import Listing, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class DeliveryCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        product_set = make_set(make_game())
        self.products = [make_product(product_set, name=f"Product {n}", slug=f"product-{n}") for n in range(3)]
        self.known = make_retailer("Known Cards", delivery_cost=Decimal("2.99"))
        self.harbour = make_retailer("Harbour Games")
        self.kestrel = make_retailer("Kestrel Cards")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        for product in self.products:
            make_listing(product, self.known, price="40.00", delivery="2.99")
            make_listing(product, self.harbour, price="40.00", delivery_known=False)
            make_listing(product, self.ebay, price="38.00", delivery_known=False)
        make_listing(self.products[0], self.kestrel, price="80.00", delivery_known=False)
        self.staff = get_user_model().objects.create_user("owner", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse("shop_delivery")

    def unknown(self, shop):
        return Listing.objects.filter(retailer=shop, delivery_known=False).count()


class PageTests(DeliveryCase):
    def test_it_lists_the_shops_without_delivery_most_prices_first(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        html = response.content.decode()
        self.assertIn("2 shops without a delivery charge", html)
        self.assertIn("4 of their prices show as plus delivery", html)
        self.assertLess(html.index("Harbour Games"), html.index("Kestrel Cards"))   # three prices before one
        self.assertNotIn("Known Cards", html)          # its delivery is known
        self.assertNotIn(">eBay<", html)               # a marketplace's delivery comes with each listing
        self.assertIn(f'name="cost-{self.harbour.pk}"', html)
        self.assertIn(f'name="free-{self.harbour.pk}"', html)
        self.assertIn("Save delivery for every shop typed in", html)

    def test_only_staff_open_it(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_typed_charges_are_saved_together_and_applied_to_the_prices_at_once(self):
        response = self.client.post(self.url, {
            "shop": [self.harbour.pk, self.kestrel.pk],
            f"cost-{self.harbour.pk}": "£3.99", f"free-{self.harbour.pk}": "50",
            f"cost-{self.kestrel.pk}": "", f"free-{self.kestrel.pk}": "",
        }, follow=True)
        self.harbour.refresh_from_db()
        self.assertEqual((self.harbour.delivery_cost, self.harbour.free_delivery_over), (Decimal("3.99"), Decimal("50.00")))
        self.assertIn("Set by hand on Shop delivery", self.harbour.delivery_note)
        self.assertEqual(self.unknown(self.harbour), 0)
        self.assertEqual(set(Listing.objects.filter(retailer=self.harbour).values_list("delivery_cost", flat=True)),
                         {Decimal("3.99")})
        # Nothing typed for Kestrel: it is left as it was.
        self.kestrel.refresh_from_db()
        self.assertIsNone(self.kestrel.delivery_cost)
        self.assertEqual(self.unknown(self.kestrel), 1)
        self.assertContains(response, "Saved delivery for 1 shop. 3 prices now include delivery.")
        self.assertNotContains(response, "Harbour Games</a></td>")   # off the list now
        self.assertContains(response, "1 shop without a delivery charge")

    def test_free_delivery_and_a_value_it_starts_from(self):
        self.client.post(self.url, {"shop": [self.kestrel.pk], f"cost-{self.kestrel.pk}": "free"})
        self.kestrel.refresh_from_db()
        self.assertEqual(self.kestrel.delivery_cost, Decimal("0.00"))
        self.assertEqual(self.unknown(self.kestrel), 0)
        # A free-delivery value alone makes the prices over it delivered, and leaves the rest plus delivery.
        self.client.post(self.url, {"shop": [self.harbour.pk], f"free-{self.harbour.pk}": "30"})
        self.harbour.refresh_from_db()
        self.assertIsNone(self.harbour.delivery_cost)
        self.assertEqual(self.unknown(self.harbour), 0)   # every price is £40, over £30

    def test_a_typing_slip_saves_nothing_for_that_shop_and_says_so(self):
        response = self.client.post(self.url, {
            "shop": [self.harbour.pk, self.kestrel.pk],
            f"cost-{self.harbour.pk}": "3.9.9", f"cost-{self.kestrel.pk}": "4.50",
        }, follow=True)
        self.harbour.refresh_from_db()
        self.kestrel.refresh_from_db()
        self.assertIsNone(self.harbour.delivery_cost)
        self.assertEqual(self.kestrel.delivery_cost, Decimal("4.50"))
        self.assertContains(response, "Harbour Games: “3.9.9” is not a delivery charge.")
        self.assertContains(response, "Saved delivery for 1 shop. 1 price now includes delivery.")
        # Silly sizes are slips too.
        self.client.post(self.url, {"shop": [self.harbour.pk], f"cost-{self.harbour.pk}": "399"})
        self.harbour.refresh_from_db()
        self.assertIsNone(self.harbour.delivery_cost)

    def test_admin_home_and_things_to_check_lead_here(self):
        self.assertContains(self.client.get(reverse("admin:index")), f'href="{self.url}"')
        self.staff.is_superuser = True
        self.staff.save()
        self.assertContains(self.client.get(reverse("checks")), f'href="{self.url}"')


class DashboardTests(DeliveryCase):
    def test_the_shops_card_counts_them_and_leads_to_the_page(self):
        html = self.client.get(reverse("web:dashboard")).content.decode()
        self.assertIn("2 shops have no delivery charge, so 4 of their prices show as plus delivery.", html)
        self.assertIn(f'<a href="{self.url}">Set their delivery</a>', html)
        Retailer.objects.filter(pk__in=[self.harbour.pk, self.kestrel.pk]).update(delivery_cost=Decimal("3.00"))
        Listing.objects.update(delivery_known=True)
        cache.clear()
        html = self.client.get(reverse("web:dashboard")).content.decode()
        self.assertIn("Every shop&#x27;s prices include delivery.", html)
