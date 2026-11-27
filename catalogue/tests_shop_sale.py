"""A shop's own Black Friday sale, noted by the readers (importers.sale_handles, says_sale, Listing.shop_sale). The
owner, 27 Nov 2026: "Can also the crawlers look for any black Friday deals in their searches that could show up
here". A shop puts a product in its sale by a collection, a tag or the product's name; the readers mark the listing on
the days such sales run (catalogue/sale_days.py) and take the mark off when the shop does. The mark says the shop
calls it a deal, never that it is one: the Black Friday pages check each against the price record
(web/tests_black_friday.py)."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import sale_days
from .importers import SALE_STAMP, Offer, apply_offers, run_import, sale_handles, says_sale
from .models import Listing, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set
from .tests_deals import GEM, box, shop

FRIDAY = date(2026, 11, 27)
SALE = [{"handle": "black-friday-deals", "title": "Black Friday Deals"}]


def season(day=FRIDAY):
    """The day the readers think it is."""
    return mock.patch("catalogue.sale_days.timezone.localdate", return_value=day)


class DaysTests(TestCase):
    def test_black_friday_and_cyber_monday_are_worked_out(self):
        self.assertEqual(sale_days.black_friday(2026), date(2026, 11, 27))
        self.assertEqual(sale_days.cyber_monday(2026), date(2026, 11, 30))
        self.assertEqual(sale_days.black_friday(2024), date(2024, 11, 29))
        self.assertEqual(sale_days.cyber_monday(2024), date(2024, 12, 2))
        self.assertEqual(sale_days.black_friday(2027), date(2027, 11, 26))

    def test_sales_are_looked_for_from_two_weeks_before_to_cyber_monday(self):
        for day, looked in ((date(2026, 11, 12), False), (date(2026, 11, 13), True), (FRIDAY, True),
                            (date(2026, 11, 30), True), (date(2026, 12, 1), False), (date(2026, 6, 1), False)):
            with self.subTest(day=day):
                self.assertEqual(sale_days.on(day), looked)
        with season(date(2026, 6, 1)):
            self.assertFalse(sale_days.on())

    def test_the_site_and_the_readers_share_the_days(self):
        from web import black_friday

        self.assertIs(black_friday.black_friday, sale_days.black_friday)
        self.assertIs(black_friday.cyber_monday, sale_days.cyber_monday)
        self.assertEqual(black_friday.SEASON_FROM, sale_days.SEASON_FROM)
        for day in (date(2026, 11, 12), date(2026, 11, 13), FRIDAY, date(2026, 11, 30), date(2026, 12, 1)):
            with self.subTest(day=day):
                self.assertEqual(black_friday.season(day).showing, sale_days.on(day))


class WordsTests(TestCase):
    def test_a_name_or_a_tag_that_puts_a_product_in_the_sale(self):
        for title, tags in (("Black Friday Deal: Surging Sparks Booster Box", ()), ("Surging Sparks Booster Box", ("black-friday",)),
                            ("Surging Sparks Booster Box", ("Pokemon", "BlackFriday2026")), ("Box", ("cyber-monday",)),
                            ("Box", ("Cyber Weekend",)), ("Box", ("BFCM",)), ("Box", ("bf-deals",)), ("Box", ("BF Sale",)),
                            ("Box", ("bf2026",)), ("Box", ("Black Week",)), ("CYBER MONDAY Elite Trainer Box", None)):
            with self.subTest(title=title, tags=tags):
                self.assertTrue(says_sale(title, tags))

    def test_and_those_that_do_not(self):
        for title, tags in (("Surging Sparks Booster Box", ()), ("Black Bolt Booster Box", ("friday-restock",)),
                            ("Box", ("bf",)), ("Box", ("sale", "pokemon")), ("Blackfire Booster Box", ()),
                            ("Cyber Judge Booster Box", ("japanese",)), ("", None), (None, ())):
            with self.subTest(title=title, tags=tags):
                self.assertFalse(says_sale(title, tags))

    def test_an_offer_is_marked_from_its_name_and_tags_only_on_the_sales_days(self):
        with season():
            self.assertEqual(Offer("Box", "https://shop.example/p/box", Decimal("90"), tags=("black-friday",)).sale,
                             sale_days.BLACK_FRIDAY)
            self.assertEqual(Offer("Black Friday Box", "https://shop.example/p/box", Decimal("90")).sale,
                             sale_days.BLACK_FRIDAY)
            # A plain one is not known to be out of every sale: its shop may have a sale collection.
            self.assertIsNone(Offer("Box", "https://shop.example/p/box", Decimal("90")).sale)
            # What a read already settled is left alone.
            self.assertEqual(Offer("Black Friday Box", "https://shop.example/p/box", Decimal("90"), sale="").sale, "")
        with season(date(2026, 6, 1)):
            self.assertIsNone(Offer("Box", "https://shop.example/p/box", Decimal("90"), tags=("black-friday",)).sale)


class CollectionTests(TestCase):
    def test_the_products_in_the_shops_sale_collections(self):
        listed = [{"handle": "pokemon", "title": "Pokémon"}, *SALE, {"handle": "cyber-monday", "title": "Cyber Monday"},
                  {"handle": "deals", "title": "BFCM offers"}]
        members = {"black-friday-deals": ["a", "b"], "cyber-monday": ["b", "c"], "deals": ["d"], "pokemon": ["z"]}
        with season():
            self.assertEqual(sale_handles("https://solent.example", listed, fetch=shop([], listed, members)),
                             {"a", "b", "c", "d"})
            self.assertEqual(sale_handles("https://solent.example", [{"handle": "pokemon", "title": "Pokémon"}],
                                          fetch=shop([], [], members)), set())

    def test_not_known_out_of_season_or_when_the_shop_could_not_be_read(self):
        members = {"black-friday-deals": ["a"]}
        with season(date(2026, 6, 1)):
            self.assertIsNone(sale_handles("https://solent.example", SALE, fetch=shop([], SALE, members)))
        with season():
            self.assertIsNone(sale_handles("https://solent.example", None, fetch=shop([], SALE, members)))

            from .importers import ImportError_

            def refused(url):
                raise ImportError_("HTTP Error 503")

            self.assertIsNone(sale_handles("https://solent.example", SALE, fetch=refused))


class ReadTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Gem Pack Vol 4 Booster Box", ean=GEM)
        self.retailer = make_retailer("Solent Cards", source_type=Retailer.Source.SHOPIFY,
                                      source_url="https://solent.example/", delivery_cost=Decimal("3.65"))
        patcher = season()
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, fetch):
        run = run_import(self.retailer, fetch=fetch)
        self.assertTrue(run.ok, run.error)
        return Listing.objects.get(product=self.product, retailer=self.retailer)

    def test_a_product_in_the_shops_sale_collection_is_marked(self):
        before = timezone.now()
        listing = self.read(shop([box()], SALE, {"black-friday-deals": ["gem-4-box"]}))
        self.assertEqual(listing.shop_sale, Listing.Sale.BLACK_FRIDAY)
        self.assertGreaterEqual(listing.shop_sale_at, before)
        self.assertEqual(listing.in_sale(), Listing.Sale.BLACK_FRIDAY)
        # The price is the shop's own: the mark changes nothing of it.
        self.assertEqual(listing.price, Decimal("27.99"))

    def test_so_is_one_the_shop_tags_or_names_for_it(self):
        tagged = box()
        tagged["tags"] = ["Pokemon", "Black Friday"]
        self.assertEqual(self.read(shop([tagged], [], {})).shop_sale, Listing.Sale.BLACK_FRIDAY)

    def test_one_in_no_sale_is_not_marked_and_the_mark_goes_when_the_shop_takes_it_out(self):
        self.assertEqual(self.read(shop([box()], SALE, {"black-friday-deals": ["another-box"]})).shop_sale, "")
        self.assertEqual(self.read(shop([box()], SALE, {"black-friday-deals": ["gem-4-box"]})).shop_sale,
                         Listing.Sale.BLACK_FRIDAY)
        listing = self.read(shop([box()], [], {}))
        self.assertEqual(listing.shop_sale, "")
        self.assertIsNone(listing.shop_sale_at)
        self.assertEqual(listing.in_sale(), "")

    def test_a_read_that_cannot_see_the_collections_keeps_the_mark(self):
        first = self.read(shop([box()], SALE, {"black-friday-deals": ["gem-4-box"]}))
        listing = self.read(shop([box()], SALE, {}, broken=True))
        self.assertEqual((listing.shop_sale, listing.shop_sale_at), (Listing.Sale.BLACK_FRIDAY, first.shop_sale_at))

    def test_out_of_season_nothing_is_looked_for_and_an_old_mark_is_stale(self):
        first = self.read(shop([box()], SALE, {"black-friday-deals": ["gem-4-box"]}))
        asked = []

        def watching(url):
            asked.append(url)
            return shop([box()], SALE, {"black-friday-deals": ["gem-4-box"]})(url)

        with season(date(2026, 12, 9)):
            listing = self.read(watching)
        self.assertEqual([url for url in asked if "black-friday-deals" in url], [])
        # Left as it was, and too old to count for anything.
        self.assertEqual(listing.shop_sale_at, first.shop_sale_at)
        self.assertEqual(listing.in_sale(first.shop_sale_at + Listing.SALE_FRESH), "")
        self.assertEqual(listing.in_sale(first.shop_sale_at + Listing.SALE_FRESH - timedelta(minutes=1)),
                         Listing.Sale.BLACK_FRIDAY)


class MarkTests(TestCase):
    """How a mark is kept (importers.apply_offers): stamped a few times a day, not at every read."""

    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", ean="0196214108158")
        self.retailer = make_retailer("Harbour Games")
        self.url = "https://harbour.example/products/surging-sparks-booster-box"

    def offer(self, sale):
        return Offer("Surging Sparks Booster Box", self.url, Decimal("90.00"), ean="0196214108158", sale=sale)

    def apply(self, sale, at, retailer=None):
        apply_offers(retailer or self.retailer, [self.offer(sale)], checked_at=at)
        return Listing.objects.get(product=self.product, retailer=retailer or self.retailer)

    def test_stamped_again_only_once_the_stamp_is_old(self):
        start = timezone.now()
        first = self.apply(sale_days.BLACK_FRIDAY, start)
        self.assertEqual((first.shop_sale, first.shop_sale_at), (Listing.Sale.BLACK_FRIDAY, start))
        again = self.apply(sale_days.BLACK_FRIDAY, start + SALE_STAMP - timedelta(minutes=1))
        self.assertEqual(again.shop_sale_at, start)
        later = self.apply(sale_days.BLACK_FRIDAY, start + SALE_STAMP)
        self.assertEqual(later.shop_sale_at, start + SALE_STAMP)
        self.assertLess(SALE_STAMP * 2, Listing.SALE_FRESH)      # a mark never goes stale between stamps

    def test_a_read_that_could_not_tell_leaves_it_and_one_that_looked_takes_it_off(self):
        start = timezone.now()
        self.apply(sale_days.BLACK_FRIDAY, start)
        kept = self.apply(None, start + timedelta(hours=1))
        self.assertEqual((kept.shop_sale, kept.shop_sale_at), (Listing.Sale.BLACK_FRIDAY, start))
        gone = self.apply("", start + timedelta(hours=2))
        self.assertEqual((gone.shop_sale, gone.shop_sale_at), ("", None))

    def test_a_marketplace_listing_is_never_marked(self):
        with season():
            ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
            make_listing(self.product, ebay, price="80.00")
            offer = Offer("BLACK FRIDAY Surging Sparks Booster Box", "https://www.ebay.co.uk/itm/1", Decimal("80.00"),
                          product_pk=self.product.pk)
            self.assertEqual(offer.sale, sale_days.BLACK_FRIDAY)
            apply_offers(ebay, [offer], checked_at=timezone.now())
        listing = Listing.objects.get(product=self.product, retailer=ebay)
        self.assertEqual((listing.shop_sale, listing.shop_sale_at), ("", None))
