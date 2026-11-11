"""A shop whose button still says buy while its own words say nobody can today: a raffle that has closed, a box
kept for the shop's VIP members, a page that calls an "in stock" product a pre-order (importers.closed_reason,
description_says_preorder). The owner, 6 Nov 2026, with each shown as stock on the site: "pre-orders that are
closed, raffles closed: otherwise we're advertising old stuff"."""

import json
from decimal import Decimal

from django.test import TestCase

from . import probe
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



class ProbeTests(TestCase):
    """The ten-minute stock probe (probe.ask, /products/<handle>.js) reads the page's own words as a shop read does.
    It once put Eclipse's VIP-only Delta Reign ETB back in stock after every hourly read took it out: the owner,
    6 Nov 2026, "VIP product is back messing up listings"."""

    def setUp(self):
        self.etb = make_product(make_set(make_game(), name="Delta Reign", slug="delta-reign"),
                                name="Delta Reign Elite Trainer Box", slug="delta-etb", product_type="elite_trainer_box")
        self.shop = make_retailer("Eclipse Cards", source_type=Retailer.Source.SHOPIFY,
                                  source_url="https://eclipse.example/", delivery_cost=Decimal("4.99"))
        self.listing = make_listing(self.etb, self.shop, price="49.99", url="https://eclipse.example/products/delta-etb",
                                    availability=Listing.Availability.OUT_OF_STOCK)

    def ask(self, description, tags=()):
        answer = {"title": "Pokémon TCG: Mega Evolution—Delta Reign Elite Trainer Box", "tags": list(tags),
                  "description": description, "variants": [{"id": 1, "price": 4999, "available": True}]}
        return probe.ask(self.listing, fetch=lambda url: json.dumps(answer).encode())

    def test_a_members_only_box_stays_out_of_stock_though_the_shop_says_available(self):
        result = self.ask("<p>Coming Soon – VIP Exclusive</p><p>This product will initially be available exclusively "
                          "to qualifying Eclipse Cards VIP customers.</p>")
        self.assertEqual(result, (Decimal("49.99"), Listing.Availability.OUT_OF_STOCK))
        # Saving it is no restock, so no "Back in stock" row and no alert email.
        self.assertEqual(probe.record_probe(self.listing, result), "same")
        self.assertEqual(Listing.objects.get(pk=self.listing.pk).availability, Listing.Availability.OUT_OF_STOCK)

    def test_a_closed_draw_and_a_page_calling_the_product_a_pre_order_read_the_same(self):
        self.assertEqual(self.ask("<h2>This raffle is closed.</h2>", tags=("Raffle",))[1], Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.ask("<p>Enter now for a chance to win.</p>", tags=("Raffle",))[1], Listing.Availability.LOTTERY)
        self.assertEqual(self.ask("<p>This is a pre-order product with a 14th November release date.</p>")[1],
                         Listing.Availability.PREORDER)
        # Plain words, and an answer with no description at all: in stock, as before.
        self.assertEqual(self.ask("<p>9 booster packs and a promo card.</p>")[1], Listing.Availability.IN_STOCK)
        answer = {"title": "Delta Reign Elite Trainer Box", "tags": [], "variants": [{"id": 1, "price": 4999, "available": True}]}
        self.assertEqual(probe.ask(self.listing, fetch=lambda url: json.dumps(answer).encode())[1],
                         Listing.Availability.IN_STOCK)


class CollectionOnlyTests(TestCase):
    """A product kept for the shop's members or sold for collection only, told by a tag or wording a description, title
    or tag carries. TCG Group, 11 Nov 2026: its Delta Reign booster box was tagged "tier1_only_pokemon" with nothing in
    its description, so the site led with its £150 as the cheapest delivered and "Lowest yet". The owner: "This product
    is collection only"."""

    def test_the_shops_tags_say_members_or_collection_only(self):
        for tags, why in ((("tier1_only_pokemon",), "members"), (("Tier 1 Only",), "members"), (("tier2_only",), "members"),
                          (("VIP_only",), "members"), (("members-only",), "members"), (("Collection Only",), "collection"),
                          (("instore_only",), "collection"), (("Click & Collect Only",), "collection"),
                          (("local pickup only",), "collection")):
            with self.subTest(tags=tags):
                self.assertEqual(closed_reason("", tags), why)
        for tags in (("pokemon", "booster box", "Delta Reign"), ("premium collection",), ("tier1",), ("collection",)):
            with self.subTest(tags=tags):
                self.assertEqual(closed_reason("", tags), "")

    def test_the_shops_words_say_collection_only(self):
        for text in ("Collection in store only.", "Available for collection only from our Leeds shop.",
                     "In-store pick-up only.", "This item is not available for delivery.", "This item cannot be posted."):
            with self.subTest(text=text):
                self.assertEqual(closed_reason(text), "collection")
        for text in ("Pokemon 151 Ultra Premium Collection only one per customer.", "Premium Collection box with 6 packs.",
                     "Free collection in store, or delivery from £3.99."):
            with self.subTest(text=text):
                self.assertEqual(closed_reason(text), "")
        self.assertEqual(closed_reason("", (), "Delta Reign Booster Box (Collection Only)"), "collection")
        self.assertEqual(closed_reason("", (), "Delta Reign Premium Collection"), "")


class CollectionReadTests(TestCase):
    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from .models import DailyLowestPrice

        self.box = make_product(make_set(make_game(), name="Delta Reign", slug="delta-reign"), name="Delta Reign Booster Box",
                                slug="delta-box", product_type="booster_box")
        self.group = make_retailer("TCG Group", source_type=Retailer.Source.SHOPIFY, source_url="https://group.example/",
                                   delivery_cost=Decimal("0"))
        self.other = make_retailer("Moor Toys", delivery_cost=Decimal("0"))
        self.listing = make_listing(self.box, self.group, price="150.00", delivery="0",
                                    url="https://group.example/products/delta-box")
        make_listing(self.box, self.other, price="164.99", delivery="0")
        today = timezone.localdate()
        # Three days the members-only £150 was the day's low, and one before it at another shop's price.
        for days, price in ((3, "150.00"), (2, "150.00"), (1, "150.00"), (5, "159.99")):
            DailyLowestPrice.objects.create(product=self.box, date=today - timedelta(days=days), price=Decimal(price))
        DailyLowestPrice.objects.create(product=self.box, date=today, price=Decimal("150.00"))
        self.DailyLowestPrice, self.today = DailyLowestPrice, today

    def read(self, tags=("pokemon", "tier1_only_pokemon")):
        [offer] = product_offers("https://group.example", page("", tags=tags, title="Delta Reign Booster Box"))
        offer.product_pk = self.box.pk
        apply_offers(self.group, [offer], complete=False)
        self.listing.refresh_from_db()

    def test_a_tagged_box_is_not_stock_says_why_and_leaves_the_record(self):
        self.read()
        self.assertEqual((self.listing.availability, self.listing.closed_why),
                         (Listing.Availability.OUT_OF_STOCK, "members"))
        record = dict(self.DailyLowestPrice.objects.filter(product=self.box).values_list("date", "price"))
        self.assertNotIn(Decimal("150.00"), record.values())
        # Today is worked out again from what anyone can buy: Moor Toys at £164.99. The earlier day stays.
        self.assertEqual(record[self.today], Decimal("164.99"))
        self.assertIn(Decimal("159.99"), record.values())

    def test_the_product_page_leads_with_a_shop_anyone_can_buy_from_and_says_why_not_the_other(self):
        self.read()
        page_html = self.client.get(self.box.get_absolute_url()).content.decode()
        self.assertIn("at Moor Toys", page_html)
        self.assertIn("Shop members only", page_html)

    def test_the_tag_going_puts_it_back_in_stock(self):
        self.read()
        self.read(tags=("pokemon",))
        self.assertEqual((self.listing.availability, self.listing.closed_why), (Listing.Availability.IN_STOCK, ""))

    def test_the_record_is_cleaned_once_even_when_the_probe_took_it_out_first(self):
        Listing.objects.filter(pk=self.listing.pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.read()
        self.assertFalse(self.DailyLowestPrice.objects.filter(product=self.box, price=Decimal("150.00")).exists())
        # A later read changes nothing more: a new day at £150 from another shop would stay.
        self.DailyLowestPrice.objects.create(product=self.box, date=self.today - __import__("datetime").timedelta(days=9),
                                             price=Decimal("150.00"))
        self.read()
        self.assertTrue(self.DailyLowestPrice.objects.filter(product=self.box, price=Decimal("150.00")).exists())


class CollectionProbeTests(ProbeTests):
    def test_a_tag_keeps_it_out_of_stock_every_ten_minutes(self):
        self.assertEqual(self.ask("<p>36 booster packs.</p>", tags=("tier1_only_pokemon",))[1],
                         Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.ask("<p>36 booster packs.</p>", tags=("pokemon",))[1], Listing.Availability.IN_STOCK)
