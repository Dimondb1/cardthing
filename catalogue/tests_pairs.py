"""Possible duplicates, from the pairs offered on Things to check to what Claude and the hourly merge may do
with them. No test here touches the network: tests_judge's fake client stands in for Anthropic."""

from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import checks
from .models import ClaudeAsk, Listing, Product, Retailer
from .testing import make_listing, make_product, make_retailer
from .tests_judge import Base, reply

Ask = ClaudeAsk


class PairBase(Base):
    def pair(self, keep_name="Surging Sparks Elite Trainer Box",
             other_name="Scarlet & Violet Surging Sparks Elite Trainer Box"):
        keep = make_product(self.set, name=keep_name)
        other = make_product(self.set, name=other_name)
        self.shop(50, product=keep, title=keep.name)
        self.shop(51, product=other, title=other.name)
        return keep, other

    def offered(self):
        return [(keep, other) for keep, others in checks.duplicates() for other in others]


class ShopsOnBothTests(PairBase):
    """A shop that shows two products under their own names sells two products, so the pair is never offered.
    eBay and Amazon search for every product, and a hidden listing is not shown, so neither says so."""

    def test_ebay_and_amazon_on_both_still_offer_the_pair_and_claude_merges_it_losing_no_listing(self):
        keep, other = self.pair()
        for source in (Retailer.Source.EBAY, Retailer.Source.AMAZON):
            market = make_retailer(f"Market {source}", source_type=source)
            make_listing(keep, market, url=f"{market.website}item/1")
            make_listing(other, market, url=f"{market.website}item/2")
        self.assertEqual(self.offered(), [(keep, other)])
        listings = Listing.objects.count()
        self.switch_on()
        self.ask()
        other.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertEqual(Listing.objects.count(), listings)
        # Each marketplace's listing for the switched-off product stays with it, where nobody sees it.
        self.assertEqual(Listing.objects.filter(product=keep).count(), 4)
        self.assertEqual(set(Listing.objects.filter(product=other).values_list("retailer__source_type", flat=True)),
                         {Retailer.Source.EBAY, Retailer.Source.AMAZON})

    def test_a_hidden_listing_on_the_other_product_says_nothing(self):
        keep, other = self.pair()
        both = make_retailer("Both")
        make_listing(keep, both)
        make_listing(other, both, url=f"{both.website}p/other", is_active=False)
        self.assertEqual(self.offered(), [(keep, other)])
        self.switch_on()
        self.ask()
        self.assertFalse(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Listing.objects.get(retailer=both, product=other).is_active, False)

    def test_a_shop_showing_both_keeps_the_pair_off_the_list(self):
        keep, other = self.pair()
        both = make_retailer("Both")
        make_listing(keep, both)
        make_listing(other, both, url=f"{both.website}p/other")
        self.assertEqual(self.offered(), [])

    def test_a_shop_listing_both_while_claude_was_asked_stops_the_merge(self):
        keep, other = self.pair()
        both = make_retailer("Both")

        def answer(key, params):
            make_listing(keep, both)
            make_listing(other, both, url=f"{both.website}p/other")
            return reply(key)

        self.switch_on()
        self.ask(answer)
        self.assertTrue(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Ask.objects.get().action, Ask.Action.STALE)

    def test_the_shops_are_read_in_one_query(self):
        keep, other = self.pair()
        with CaptureQueriesContext(connection) as first:
            checks.duplicates()
        for n in range(5):
            extra = make_product(self.set, name=f"Surging Sparks Tin {n}", product_type="tin")
            twin = make_product(self.set, name=f"Scarlet & Violet Surging Sparks Tin {n}", product_type="tin")
            for product in (extra, twin):
                self.shop(20, product=product, title=product.name)
        with CaptureQueriesContext(connection) as more:
            self.assertEqual(len(self.offered()), 6)
        self.assertEqual(len(more), len(first))
