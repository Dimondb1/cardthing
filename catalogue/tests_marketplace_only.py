"""A price that only eBay or Amazon sellers give (catalogue/sanity.py ONLY_MARKETPLACE, fair_price, web/views.py
product_detail). On 24 Oct 2026 the Holiday Calendar 2025 page led with "£22.08 delivered, Lowest yet" at eBay:
two promo cards from the calendar. No UK shop listed it, so nothing measured the price. The owner: "Will this
happen again or can we minimise risk better"."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from . import checks, ebay, sanity
from .models import DailyLowestPrice, Listing, Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class MarketplaceOnlyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.set = make_set(make_game())
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.calendar = make_product(self.set, name="Holiday Calendar 2025", slug="holiday-2025",
                                     product_type=Product.Type.COLLECTION_BOX)
        self.listing = make_listing(self.calendar, self.ebay, price="19.36", delivery="2.72",
                                    url="https://www.ebay.co.uk/itm/9?campid=1", title="Pokemon Holiday Calendar 2025")
        today = timezone.localdate()
        # Three weeks at £45: £22.08 today would be a "Lowest yet" if anything vouched for it.
        for back in range(1, 22):
            DailyLowestPrice.objects.create(product=self.calendar, date=today - timedelta(days=back), price=Decimal("45.00"))

    def page(self):
        cache.clear()
        return self.client.get(self.calendar.get_absolute_url()).content.decode()

    def test_a_price_only_ebay_gives_is_doubtful_and_put_to_claude(self):
        sanity.judge_product(self.calendar.pk)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.sanity, Listing.Sanity.DOUBTFUL)
        self.assertEqual(self.listing.sanity_reason, sanity.ONLY_MARKETPLACE)
        self.assertIn(self.listing, checks.doubtful_waiting())
        # Still shown, so a visitor can buy it, but never called a low.
        html = self.page()
        self.assertIn("£22.08", html)
        self.assertNotIn("Lowest yet", html)
        self.assertNotIn('class="fair fair--', html)

    def test_a_shop_listing_it_even_sold_out_gives_the_ebay_price_something_to_be_measured_by(self):
        make_listing(self.calendar, make_retailer("Harbour Games"), price="44.99",
                     availability=Listing.Availability.OUT_OF_STOCK)
        sanity.judge_product(self.calendar.pk)
        self.listing.refresh_from_db()
        self.assertNotEqual(self.listing.sanity_reason, sanity.ONLY_MARKETPLACE)

    def test_our_record_needs_two_uk_shops_and_ebay_is_not_one(self):
        # One shop (sold out) and eBay: once two listings, enough for "Lowest yet" from eBay's own price.
        make_listing(self.calendar, make_retailer("Harbour Games"), price="44.99",
                     availability=Listing.Availability.OUT_OF_STOCK)
        Listing.objects.filter(pk=self.listing.pk).update(sanity=Listing.Sanity.OK, sanity_reason="")
        self.assertNotIn("Lowest yet", self.page())
        from .fair_price import refresh_bands

        refresh_bands()
        self.calendar.refresh_from_db()
        self.assertIsNone(self.calendar.price_band)
        # A second UK shop makes our record count again.
        make_listing(self.calendar, make_retailer("Northgate Cards"), price="46.00",
                     availability=Listing.Availability.OUT_OF_STOCK)
        self.assertIn("Lowest yet", self.page())

    def test_a_doubtful_cheapest_price_gets_no_price_check(self):
        for name in ("Harbour Games", "Northgate Cards"):
            make_listing(self.calendar, make_retailer(name), price="44.99", availability=Listing.Availability.OUT_OF_STOCK)
        Listing.objects.filter(pk=self.listing.pk).update(sanity=Listing.Sanity.DOUBTFUL, sanity_reason="well under")
        self.assertNotIn("Lowest yet", self.page())
        from .fair_price import refresh_bands

        refresh_bands()
        self.calendar.refresh_from_db()
        self.assertIsNone(self.calendar.price_band)

    def test_the_hourly_tidy_puts_a_price_left_alone_to_claude(self):
        # Judged OK while a shop listed it; the shop's listing then went, and nothing checked the price again.
        Listing.objects.filter(pk=self.listing.pk).update(sanity=Listing.Sanity.OK, sanity_reason="")
        with mock.patch.object(ebay, "http", mock.Mock(side_effect=AssertionError("no eBay call"))):
            call_command("tidy_listings", stdout=StringIO())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.sanity, Listing.Sanity.DOUBTFUL)
