"""The shop's own description: kept from what every read already fetches, written only when it changes,
and sent to the Claude judge as data."""

import json
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from . import ebay
from .importers import (
    DESCRIPTION_CHARS, Offer, apply_offers, clean_description, feed_offers, page_offer, product_offers,
)
from .models import Listing, Retailer, ShopProduct
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class CleanDescriptionTests(TestCase):
    def test_markup_becomes_plain_text(self):
        markup = ("<p>Contains <strong>36</strong> booster packs&nbsp;&amp; a card.</p><script>alert(1)</script>"
                  "<style>p{}</style><ul><li>English</li><li>Sealed</li></ul>\x00")
        self.assertEqual(clean_description(markup), "Contains 36 booster packs & a card. English Sealed")

    def test_nothing_is_empty(self):
        self.assertEqual(clean_description(None), "")
        self.assertEqual(clean_description("<p> </p>"), "")

    def test_a_long_description_is_cut_at_a_word(self):
        text = clean_description("<p>" + "booster " * 1000 + "</p>")
        self.assertLessEqual(len(text), DESCRIPTION_CHARS)
        self.assertGreater(len(text), DESCRIPTION_CHARS - 20)
        self.assertTrue(text.endswith("booster"))

    def test_a_huge_description_is_only_read_in_part(self):
        self.assertLessEqual(len(clean_description("x" * 10_000_000)), DESCRIPTION_CHARS)


class SourcesTests(TestCase):
    def test_shopify_variants_share_one_description(self):
        product = {"handle": "box", "title": "Surging Sparks Booster Box", "body_html": "<p>36 packs</p>",
                   "variants": [{"id": 1, "title": "Box", "price": "120.00", "available": True},
                                {"id": 2, "title": "Case", "price": "700.00", "available": True}]}
        first, second = product_offers("https://shop.example", product)
        self.assertEqual(first.description, "36 packs")
        self.assertIs(first.description, second.description)

    def test_a_shopify_answer_without_a_description_leaves_the_listing_alone(self):
        product = {"handle": "box", "title": "Box", "variants": [{"price": "1.00", "available": True}]}
        self.assertIsNone(next(product_offers("https://shop.example", product)).description)
        product["body_html"] = None
        self.assertEqual(next(product_offers("https://shop.example", product)).description, "")

    def test_a_website_page_gives_its_schema_description_else_its_og_description(self):
        data = {"@type": "Product", "name": "Surging Sparks Booster Box", "description": "<b>36</b> packs, English",
                "offers": {"price": "120.00", "priceCurrency": "GBP"}}
        page = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        self.assertEqual(page_offer("https://shop.example/p/box", page).description, "36 packs, English")
        del data["description"]
        page = (f'<meta property="og:description" content="Japanese edition, 30 packs">'
                f'<script type="application/ld+json">{json.dumps(data)}</script>')
        self.assertEqual(page_offer("https://shop.example/p/box", page).description, "Japanese edition, 30 packs")
        meta = ('<meta property="product:price:amount" content="12.00"><meta property="og:title" content="Pack">'
                '<meta name="description" content="One pack">')
        self.assertEqual(page_offer("https://shop.example/p/pack", meta).description, "One pack")

    def test_feeds_carry_a_description_only_when_they_have_one(self):
        with_column = "title,url,price,description\nBox,https://shop.example/box,10.00,<p>36 packs</p>\n"
        self.assertEqual(next(feed_offers(with_column)).description, "36 packs")
        without = "title,url,price\nBox,https://shop.example/box,10.00\n"
        self.assertIsNone(next(feed_offers(without)).description)
        xml = ('<rss xmlns:g="http://base.google.com/ns/1.0"><channel><item><title>Box</title>'
               '<link>https://shop.example/box</link><g:price>10.00 GBP</g:price>'
               '<g:description>Booster box, 36 packs</g:description></item></channel></rss>')
        self.assertEqual(next(feed_offers(xml)).description, "Booster box, 36 packs")

    def test_ebay_uses_the_description_it_already_sent(self):
        self.assertIsNone(ebay.item_description({"title": "Box"}))
        self.assertEqual(ebay.item_description({"shortDescription": "Sealed <b>box</b>"}), "Sealed box")
        self.assertEqual(ebay.item_description({"description": "<div>Full text</div>"}), "Full text")


class StoredOnChangeTests(TestCase):
    def setUp(self):
        self.retailer = make_retailer("Poke Collect", source_type=Retailer.Source.SHOPIFY,
                                      source_url="https://poke-collect.example")
        self.product = make_product(make_set(make_game()), ean="0820650851230")

    def offer(self, description):
        return Offer(title="Prismatic ETB", url="https://poke-collect.example/products/etb", price=Decimal("50"),
                     ean="0820650851230", description=description)

    def description(self):
        return Listing.objects.defer(None).get(product=self.product).description

    def description_writes(self, offer):
        with CaptureQueriesContext(connection) as queries:
            apply_offers(self.retailer, [offer])
        return [q for q in queries.captured_queries if q["sql"].startswith("UPDATE") and '"description"' in q["sql"]]

    def test_stored_then_written_only_when_it_changes(self):
        apply_offers(self.retailer, [self.offer("36 packs")])
        self.assertEqual(self.description(), "36 packs")
        self.assertEqual(self.description_writes(self.offer("36 packs")), [])
        self.assertEqual(len(self.description_writes(self.offer("Now 30 packs"))), 1)
        self.assertEqual(self.description(), "Now 30 packs")

    def test_a_read_that_carries_none_leaves_it(self):
        apply_offers(self.retailer, [self.offer("36 packs")])
        self.assertEqual(self.description_writes(self.offer(None)), [])
        self.assertEqual(self.description(), "36 packs")
        apply_offers(self.retailer, [self.offer("")])
        self.assertEqual(self.description(), "")

    def test_an_unchanged_read_is_no_slower(self):
        apply_offers(self.retailer, [self.offer("36 packs")])
        with CaptureQueriesContext(connection) as same:
            apply_offers(self.retailer, [self.offer("36 packs")])
        with CaptureQueriesContext(connection) as without:
            apply_offers(self.retailer, [self.offer(None)])
        self.assertEqual(len(same.captured_queries), len(without.captured_queries))

    def test_the_read_lets_go_of_a_description_once_written(self):
        offer = self.offer("36 packs")
        apply_offers(self.retailer, [offer])
        self.assertIsNone(offer.description)

    def test_lists_never_load_it(self):
        make_listing(self.product, self.retailer)
        listing = Listing.objects.get(product=self.product)
        self.assertIn("description", listing.get_deferred_fields())
        self.assertNotIn("description", Listing.objects.defer(None).get(product=self.product).get_deferred_fields())

    def test_a_row_matched_by_name_keeps_the_description(self):
        offer = Offer(title="Pokemon TCG Prismatic Evolutions Elite Trainer Box",
                      url="https://poke-collect.example/products/etb-2", price=Decimal("50"),
                      description="9 packs, English")
        with self.settings(RIPRAPTOR_AUTO_CATALOGUE=False):
            apply_offers(self.retailer, [offer])
        self.assertEqual(ShopProduct.objects.get(url=offer.url).description, "9 packs, English")
