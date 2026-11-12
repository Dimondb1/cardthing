"""An eBay price counts for RIPRAPTOR_EBAY_STALE_AFTER_HOURS, not the days a shop's does (models.fresh,
ebay_stale_cutoff): unconfirmed for longer, it is not shown as buyable until it is checked again. The owner, 12 Nov
2026: "As ebay hasn't updated in 8 hours ... most things become out of stock and th site doesn't know ... This sort
of time to update is not good enough"."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from .importers import stamp_checked
from .models import Listing, Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


@override_settings(RIPRAPTOR_EBAY_STALE_AFTER_HOURS=4, RIPRAPTOR_STALE_AFTER_HOURS=72)
class EbayFreshnessTests(TestCase):
    def setUp(self):
        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="surging-box",
                                product_type="booster_box")
        self.shop = make_retailer("Harbour Games")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.at_shop = make_listing(self.box, self.shop, price="150.00", hours_ago=5)
        self.on_ebay = make_listing(self.box, self.ebay, price="120.00", hours_ago=5,
                                    url="https://www.ebay.co.uk/itm/123")

    def test_an_ebay_price_five_hours_old_is_not_buyable_and_a_shops_is(self):
        self.on_ebay.refresh_from_db()
        self.at_shop.refresh_from_db()
        self.assertTrue(self.on_ebay.is_stale)
        self.assertFalse(self.on_ebay.is_buyable)
        self.assertTrue(self.at_shop.is_buyable)
        self.assertEqual(list(Listing.objects.current()), [self.at_shop])
        # Lists and the cheapest price leave it out too.
        priced = Product.objects.with_prices().get(pk=self.box.pk)
        self.assertEqual(priced.lowest_known, Decimal("150.00"))
        self.assertEqual(list(Product.objects.priced()), [self.box])

    def test_checked_inside_four_hours_it_counts(self):
        Listing.objects.filter(pk=self.on_ebay.pk).update(last_checked=timezone.now() - timedelta(hours=3))
        self.assertEqual(Product.objects.with_prices().get(pk=self.box.pk).lowest_known, Decimal("120.00"))
        self.assertEqual(set(Listing.objects.current()), {self.at_shop, self.on_ebay})

    def test_the_product_page_leads_with_the_shop_and_says_when_ebay_was_checked(self):
        page = self.client.get(self.box.get_absolute_url())
        self.assertEqual(page.context["cheapest"].retailer, self.shop)
        self.assertContains(page, "Not checked since")

    def test_a_check_that_brings_it_back_counts_as_a_change(self):
        before = Listing.objects.get(pk=self.on_ebay.pk).changed_at
        now = timezone.now()
        stamp_checked([self.on_ebay.pk], now)
        after = Listing.objects.get(pk=self.on_ebay.pk)
        self.assertEqual(after.last_checked, now)
        self.assertNotEqual(after.changed_at, before)
        self.assertEqual(after.changed_at, now)
