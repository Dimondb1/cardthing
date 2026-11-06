"""A shop whose button still says buy while its own words say nobody can today: a raffle that has closed, a box
kept for the shop's VIP members, a page that calls an "in stock" product a pre-order (importers.closed_reason,
description_says_preorder). The owner, 6 Nov 2026, with each shown as stock on the site: "pre-orders that are
closed, raffles closed: otherwise we're advertising old stuff"."""

from decimal import Decimal

from django.test import TestCase

from .importers import Offer, apply_offers, closed_reason, description_says_preorder, product_offers
from .models import Listing, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


def page(body, tags=(), available=True, title="Pokemon TCG: Delta Reign - Booster Box"):
    return {"handle": "delta-box", "title": title, "tags": list(tags), "images": [], "body_html": body,
            "variants": [{"id": 1, "title": "Default Title", "price": "154.44", "available": available, "barcode": ""}]}


class ClosedWordsTests(TestCase):
    def test_a_draw_that_has_ended_is_told_from_the_shops_words(self):
        for text in ("<h2>This raffle is closed.</h2><p>Strictly 1 entry per address.</p>", "The lottery has now ended.",
                     "Entries are closed. Winners will be contacted.", "Raffle closed", "This ballot has finished"):
            with self.subTest(text=text):
                self.assertEqual(closed_reason(text), "closed")
        for text in ("Enter our raffle for a chance to win.", "The draw takes place on Friday.", "", None,
                     "Raffle entry: one per customer."):
            with self.subTest(text=text):
                self.assertEqual(closed_reason(text), "")

    def test_a_product_kept_for_members_is_told_too(self):
        for text in ("Coming Soon – VIP Exclusive. This product will initially be available exclusively to qualifying "
                     "Eclipse Cards VIP customers.", "Due to unforeseen delays, this product will not be available to "
                     "purchase for the scheduled release date.", "Members only release.", "Available only to VIP members"):
            with self.subTest(text=text):
                self.assertEqual(closed_reason(text), "members")
        self.assertEqual(closed_reason("Join our VIP club for early access to future drops."), "")

    def test_a_page_that_calls_the_product_a_pre_order_is_one(self):
        self.assertTrue(description_says_preorder("This is a pre-order product with a 6th November 2026 release date."))
        self.assertTrue(description_says_preorder("Pre-Order item: ships on release day."))
        self.assertFalse(description_says_preorder("Pre-order now to secure your copy"))   # the shop's flag decides
        self.assertFalse(description_says_preorder("Includes a pre-order bonus promo card."))
        self.assertFalse(description_says_preorder(""))


class ReadTests(TestCase):
    def setUp(self):
        self.box = make_product(make_set(make_game(), name="Delta Reign", slug="delta-reign"), name="Delta Reign Booster Box",
                                slug="delta-box", product_type="booster_box")
        self.shop = make_retailer("Double Sleeved", source_type=Retailer.Source.SHOPIFY, source_url="https://sleeved.example/",
                                  delivery_cost=Decimal("0"))

    def offer(self, body, **kwargs):
        [offer] = product_offers("https://sleeved.example", page(body, **kwargs))
        return offer

    def test_a_closed_raffle_and_a_members_only_box_are_not_stock_though_the_shop_says_available(self):
        self.assertEqual(self.offer("<h2>This raffle is closed.</h2>", tags=("Raffle",)).availability,
                         Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.offer("<h2>This raffle is closed.</h2>").availability, Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.offer("Enter now. Start: 01/11/2026 End: 04/11/2026", tags=("Raffle",)).availability,
                         Listing.Availability.LOTTERY)   # an open draw, as before
        vip = self.offer("<p>Coming Soon – VIP Exclusive</p><p>This product will initially be available exclusively to "
                         "qualifying VIP customers.</p>")
        self.assertEqual((vip.availability, vip.closed), (Listing.Availability.OUT_OF_STOCK, "members"))
        self.assertEqual(self.offer("<p>36 booster packs of the newest set.</p>").availability, Listing.Availability.IN_STOCK)

    def test_a_page_calling_an_in_stock_product_a_pre_order_is_a_pre_order(self):
        self.assertEqual(self.offer("<p>This is a pre-order product with a 6th November 2026 release date.</p>").availability,
                         Listing.Availability.PREORDER)
        # A sold-out one stays sold out; an open draw stays a draw.
        self.assertEqual(self.offer("<p>This is a pre-order product.</p>", available=False).availability,
                         Listing.Availability.OUT_OF_STOCK)

    def test_the_rule_holds_for_an_offer_from_any_source_and_reaches_the_listing(self):
        offer = Offer(title="Delta Reign Booster Box", price=Decimal("154.44"), url="https://sleeved.example/products/delta-box",
                      description="This raffle is closed.", product_pk=self.box.pk)
        self.assertEqual((offer.availability, offer.closed), (Listing.Availability.OUT_OF_STOCK, "closed"))
        listing = make_listing(self.box, self.shop, price="154.44")
        apply_offers(self.shop, [offer])
        listing.refresh_from_db()
        self.assertEqual(listing.availability, Listing.Availability.OUT_OF_STOCK)
        # The shop's words change: the draw opens again, and the listing is a draw, not stock.
        apply_offers(self.shop, [Offer(title="Delta Reign Booster Box (Raffle)", price=Decimal("154.44"),
                                       url="https://sleeved.example/products/delta-box", description="Enter now.",
                                       product_pk=self.box.pk)])
        listing.refresh_from_db()
        self.assertEqual(listing.availability, Listing.Availability.LOTTERY)
