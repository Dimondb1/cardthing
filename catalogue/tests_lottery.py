"""Products a shop sells by a draw (tagged Lottery): listed under their own label, never as stock."""

import json
from decimal import Decimal

from django.test import TestCase

from . import probe
from .importers import Offer, apply_offers, product_offers, says_lottery
from .models import Listing, Restock, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


def page(tags=("Lottery",), available=True, title="Pokemon TCG: 30th Celebration - Booster Pack"):
    return {"handle": "30th-pack", "title": title, "tags": list(tags), "images": [],
            "variants": [{"id": 1, "title": "Default Title", "price": "4.99", "available": available, "barcode": ""}]}


class LotteryTests(TestCase):
    def setUp(self):
        self.pack = make_product(make_set(make_game(), name="30th Celebration", slug="30th-celebration"),
                                 name="30th Celebration Booster Pack", slug="30th-pack", product_type="booster_pack")
        self.obsidia = make_retailer("Obsidia TCG", source_type=Retailer.Source.SHOPIFY, source_url="https://obsidia.example/",
                                     delivery_cost=Decimal("2.99"))

    def test_a_draw_is_told_by_its_tag_or_title_in_any_case(self):
        self.assertTrue(says_lottery(tags=("LOTTERY",)))
        self.assertTrue(says_lottery(tags=("lottery",)))
        self.assertTrue(says_lottery("Ascended Heroes ETB (Raffle Entry)"))
        self.assertTrue(says_lottery(tags=("Ballot",)))
        self.assertFalse(says_lottery("Lotte Booster Box", tags=("Pokemon", "New")))
        offer = next(product_offers("https://obsidia.example", page()))
        self.assertEqual(offer.availability, Listing.Availability.LOTTERY)
        # A draw that is not open is simply out of stock; a pre-order by draw is still a draw.
        self.assertEqual(next(product_offers("https://obsidia.example", page(available=False))).availability,
                         Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(next(product_offers("https://obsidia.example", page(tags=("Lottery", "Pre-Order")))).availability,
                         Listing.Availability.LOTTERY)
        self.assertEqual(Offer("Raffle: Booster Box", "https://x.example/p", Decimal("1")).availability,
                         Listing.Availability.LOTTERY)

    def test_a_read_turns_stock_into_a_draw_without_a_restock_and_back_into_stock_with_one(self):
        listing = make_listing(self.pack, self.obsidia, price="4.99", delivery="2.99", url="https://obsidia.example/products/30th-pack")
        apply_offers(self.obsidia, product_offers("https://obsidia.example", page()), complete=False)
        listing.refresh_from_db()
        self.assertEqual(listing.availability, Listing.Availability.LOTTERY)
        self.assertFalse(Listing.objects.buyable().exists())
        self.assertFalse(Restock.objects.exists())
        # The draw ends and the shop sells it normally: that is stock coming back.
        apply_offers(self.obsidia, product_offers("https://obsidia.example", page(tags=())), complete=False)
        listing.refresh_from_db()
        self.assertEqual(listing.availability, Listing.Availability.IN_STOCK)
        self.assertEqual(Restock.objects.count(), 1)

    def test_a_single_listing_check_reads_the_draw_too(self):
        listing = make_listing(self.pack, self.obsidia, price="4.99", url="https://obsidia.example/products/30th-pack")
        answer = {"title": "Pokemon TCG: 30th Celebration - Booster Pack", "tags": ["Lottery"],
                  "variants": [{"id": 1, "price": 499, "available": True}]}
        result = probe.ask(listing, fetch=lambda url: json.dumps(answer).encode())
        self.assertEqual(result, (Decimal("4.99"), Listing.Availability.LOTTERY))

    def test_the_product_page_lists_the_draw_under_its_own_label_never_as_the_cheapest(self):
        make_listing(self.pack, self.obsidia, price="4.99", delivery="2.99", availability=Listing.Availability.LOTTERY,
                     url="https://obsidia.example/products/30th-pack")
        harbour = make_retailer("Harbour Games")
        make_listing(self.pack, harbour, price="9.99", delivery="1.95")
        response = self.client.get(self.pack.get_absolute_url())
        html = response.content.decode()
        self.assertEqual(response.context["cheapest"].retailer, harbour)
        self.assertIn("at Harbour Games", html)
        self.assertIn("Enter lottery", html)
        self.assertIn('stock-inline--lottery">Lottery', html)
        self.assertIn("only those drawn can buy", html)
        self.assertIn('rel="sponsored nofollow noopener"', html)
        self.assertEqual(response.context["in_stock_count"], 1)
        # Not among the shops that have it, and not a price for search engines.
        self.assertNotIn('"lowPrice": "7.98"', html)
        # Sold out at every other shop: the draw still shows, and nothing is in stock.
        Listing.objects.filter(retailer=harbour).update(availability=Listing.Availability.OUT_OF_STOCK)
        response = self.client.get(self.pack.get_absolute_url())
        self.assertIsNone(response.context["cheapest"])
        self.assertContains(response, "Enter lottery")
        self.assertNotContains(response, "Buy now")

    def test_lists_do_not_count_a_draw_as_stock(self):
        from .models import Product

        make_listing(self.pack, self.obsidia, price="4.99", availability=Listing.Availability.LOTTERY)
        row = Product.objects.with_prices().get(pk=self.pack.pk)
        self.assertEqual((row.in_stock_count, row.preorder_count, row.lowest_price), (0, 0, None))
