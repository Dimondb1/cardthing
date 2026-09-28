from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from . import pricing
from .models import DailyLowestPrice, Listing, OutboundClick, Product
from .search import apply_search, normalise
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class SearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        game = make_game()
        cls.pre = make_set(game)
        cls.etb = make_product(cls.pre)
        cls.bundle = make_product(
            cls.pre, name="Prismatic Evolutions Booster Bundle", product_type=Product.Type.BUNDLE
        )
        op = make_game(name="One Piece Card Game", slug="one-piece", search_aliases="")
        cls.royal = make_product(
            make_set(op, name="Royal Blood", slug="royal-blood", code="OP-10"),
            name="Royal Blood Booster Box",
            product_type=Product.Type.BOOSTER_BOX,
        )

    def search(self, query):
        queryset, _terms = apply_search(Product.objects.all(), query)
        return set(queryset)

    def test_normalise(self):
        self.assertEqual(normalise("Pokémon"), "pokemon")
        self.assertEqual(normalise("Archazia's Island"), "archazias island")
        self.assertEqual(normalise("Tarkir: Dragonstorm"), "tarkir dragonstorm")

    def test_accents_and_aliases(self):
        self.assertEqual(self.search("pokemon etb"), {self.etb})

    def test_word_prefix(self):
        self.assertEqual(self.search("prism"), {self.etb, self.bundle})

    def test_set_code_with_or_without_punctuation(self):
        self.assertEqual(self.search("op10"), {self.royal})
        self.assertEqual(self.search("OP-10"), {self.royal})

    def test_terms_match_word_starts_only(self):
        # "ox" appears inside "box" but is not the start of a word.
        self.assertEqual(self.search("ox"), set())

    def test_renaming_a_set_updates_search(self):
        self.pre.name = "Shimmering Evolutions"
        self.pre.save()
        self.assertIn(self.etb, self.search("shimmering"))


class PriceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = make_product(make_set(make_game()))
        cls.harbour = make_retailer("Harbour Games")
        cls.north = make_retailer("Northgate Cards")
        cls.kestrel = make_retailer("Kestrel Collectables")

    def annotated(self):
        return Product.objects.with_prices().get(pk=self.product.pk)

    def test_delivered_price_includes_delivery(self):
        listing = make_listing(self.product, self.harbour, price="50.00", delivery="3.49")
        listing.refresh_from_db()
        self.assertEqual(listing.delivered_price, Decimal("53.49"))

    def test_cheapest_uses_delivered_price(self):
        make_listing(self.product, self.harbour, price="50.00", delivery="4.99")
        make_listing(self.product, self.north, price="52.00", delivery="0.00")
        self.assertEqual(self.annotated().lowest_price, Decimal("52.00"))

    def test_out_of_stock_and_stale_listings_are_ignored(self):
        make_listing(self.product, self.harbour, price="40.00",
                     availability=Listing.Availability.OUT_OF_STOCK)
        make_listing(self.product, self.north, price="41.00", hours_ago=24 * 30)
        make_listing(self.product, self.kestrel, price="60.00")
        product = self.annotated()
        self.assertEqual(product.lowest_price, Decimal("60.00"))
        self.assertEqual(product.in_stock_count, 1)
        self.assertEqual(product.listing_count, 3)

    def test_inactive_retailer_is_ignored(self):
        make_listing(self.product, self.harbour, price="40.00")
        self.harbour.is_active = False
        self.harbour.save()
        self.assertIsNone(self.annotated().lowest_price)

    def test_preorders_count_as_buyable(self):
        make_listing(self.product, self.harbour, price="45.00",
                     availability=Listing.Availability.PREORDER)
        product = self.annotated()
        self.assertEqual(product.lowest_price, Decimal("45.00"))
        self.assertEqual(product.preorder_count, 1)
        self.assertEqual(product.in_stock_count, 0)

    def test_record_check_keeps_the_lowest_price_of_the_day(self):
        listing = make_listing(self.product, self.harbour, price="50.00")
        pricing.record_check(listing, price=Decimal("48.00"), delivery_cost=Decimal("0"),
                             availability=Listing.Availability.IN_STOCK)
        pricing.record_check(listing, price=Decimal("55.00"), delivery_cost=Decimal("0"),
                             availability=Listing.Availability.IN_STOCK)
        today = DailyLowestPrice.objects.get(product=self.product, date=timezone.localdate())
        self.assertEqual(today.price, Decimal("48.00"))

    def test_price_drops(self):
        make_listing(self.product, self.harbour, price="45.00")
        DailyLowestPrice.objects.create(
            product=self.product, date=timezone.localdate() - timedelta(days=7), price="55.00"
        )
        drops = list(pricing.price_drops())
        self.assertEqual(drops, [self.product])
        self.assertEqual(drops[0].previous_price, Decimal("55.00"))

    def test_price_rise_is_not_a_drop(self):
        make_listing(self.product, self.harbour, price="60.00")
        DailyLowestPrice.objects.create(
            product=self.product, date=timezone.localdate() - timedelta(days=7), price="55.00"
        )
        self.assertEqual(list(pricing.price_drops()), [])

    def test_popular_orders_by_recent_clicks(self):
        other = make_product(self.product.product_set, name="Prismatic Evolutions Booster Bundle")
        for product, count in ((self.product, 2), (other, 5)):
            for _ in range(count):
                OutboundClick.objects.create(product=product, retailer=self.harbour)
        OutboundClick.objects.create(
            product=self.product, retailer=self.harbour,
            created_at=timezone.now() - timedelta(days=30),
        )
        self.assertEqual(pricing.popular(), [other, self.product])

    def test_affiliate_link_encodes_the_product_url(self):
        self.harbour.affiliate_url_template = "https://network.example/click?id=9&url={url}"
        url = self.harbour.outbound_url("https://shop.example/p?a=1&b=2")
        self.assertEqual(
            url,
            "https://network.example/click?id=9&url=https%3A%2F%2Fshop.example%2Fp%3Fa%3D1%26b%3D2",
        )
