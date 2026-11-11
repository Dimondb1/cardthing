"""Discount codes (Retailer.discount, discount_code.html) and Topaz Cards, the shop that brought the first. The owner,
11 Nov 2026, with Topaz Cards' RIPRAPTOR5 (5% off a first order): "can you add it and include compatability for
discount codes"."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import shopping


class CodeCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="ss-box",
                                product_type="booster_box")
        self.topaz = make_retailer("Topaz Cards", delivery_cost=Decimal("3.99"), discount_code="RIPRAPTOR5",
                                   discount_terms="5% off your first order")
        self.moor = make_retailer("Moor Toys", delivery_cost=Decimal("0.00"))
        make_listing(self.box, self.topaz, price="130.00", delivery="3.99")
        make_listing(self.box, self.moor, price="140.00")


class ShownTests(CodeCase):
    def test_beside_the_shops_price_and_in_the_price_box_when_it_leads(self):
        page = self.client.get(self.box.get_absolute_url())
        self.assertContains(page, '<code class="code-chip__code">RIPRAPTOR5</code>', count=2)
        self.assertContains(page, "5% off your first order", count=2)
        self.assertContains(page, 'data-copy-code="RIPRAPTOR5"', count=2)

    def test_not_in_the_price_box_when_another_shop_leads(self):
        Retailer.objects.filter(pk=self.topaz.pk).update(delivery_cost=Decimal("30.00"))
        self.topaz.listings.update(delivery_cost=Decimal("30.00"))
        page = self.client.get(self.box.get_absolute_url())
        self.assertContains(page, '<code class="code-chip__code">RIPRAPTOR5</code>', count=1)

    def test_the_price_compared_stays_without_it(self):
        page = self.client.get(self.box.get_absolute_url())
        self.assertContains(page, "£133.99")
        self.assertNotContains(page, "£126.99")   # 5% off would be a price no shopper is sure to pay

    def test_gone_after_its_last_day(self):
        Retailer.objects.filter(pk=self.topaz.pk).update(discount_ends=timezone.localdate() - timedelta(days=1))
        self.assertNotContains(self.client.get(self.box.get_absolute_url()), "RIPRAPTOR5")
        Retailer.objects.filter(pk=self.topaz.pk).update(discount_ends=timezone.localdate())
        self.assertContains(self.client.get(self.box.get_absolute_url()), "RIPRAPTOR5")

    def test_on_the_shops_page_and_the_shopping_list(self):
        self.assertContains(self.client.get(reverse("web:shops")), "RIPRAPTOR5")
        self.client.cookies[shopping.COOKIE] = self.box.slug
        self.assertContains(self.client.get(reverse("web:shopping_list")), "RIPRAPTOR5")

    def test_a_shop_with_no_code_shows_none(self):
        Retailer.objects.filter(pk=self.topaz.pk).update(discount_code="", discount_terms="")
        self.assertNotContains(self.client.get(self.box.get_absolute_url()), "code-chip")

    def test_the_terms_say_what_a_code_means(self):
        self.assertContains(self.client.get(reverse("web:terms")), "The prices we compare are before any code.")


class TopazTests(TestCase):
    def test_setup_adds_topaz_with_its_delivery_and_code(self):
        call_command("setup_shops", stdout=StringIO())
        topaz = Retailer.objects.get(slug="topaz-cards")
        self.assertEqual((topaz.name, topaz.website, topaz.source_type), ("Topaz Cards", "https://topaz-cards.co.uk/",
                                                                          Retailer.Source.WEBSITE))
        self.assertEqual((topaz.delivery_cost, topaz.free_delivery_over), (Decimal("3.99"), None))
        self.assertEqual(topaz.discount, ("RIPRAPTOR5", "5% off your first order"))

    def test_a_code_changed_in_admin_stays(self):
        call_command("setup_shops", stdout=StringIO())
        Retailer.objects.filter(slug="topaz-cards").update(discount_code="RIPRAPTOR10", discount_terms="10% off")
        call_command("setup_shops", stdout=StringIO())
        self.assertEqual(Retailer.objects.get(slug="topaz-cards").discount, ("RIPRAPTOR10", "10% off"))

    def test_a_code_ended_in_admin_stays_ended(self):
        call_command("setup_shops", stdout=StringIO())
        yesterday = timezone.localdate() - timedelta(days=1)
        Retailer.objects.filter(slug="topaz-cards").update(discount_ends=yesterday)
        call_command("setup_shops", stdout=StringIO())
        topaz = Retailer.objects.get(slug="topaz-cards")
        self.assertIsNone(topaz.discount)
        self.assertEqual(topaz.discount_ends, yesterday)
