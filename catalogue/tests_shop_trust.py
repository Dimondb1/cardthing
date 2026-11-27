"""A shop's label out on the site: what a visitor is shown of it (Retailer.shop_mark), and the rule that a product whose
cheapest price is at a shop marked Caution is never put forward as a deal (Retailer.held_back,
offers.Summary.held_back, offers.without_caution_lead). The owner, 27 Nov 2026, told that a badly reviewed shop led the
deals: "most of our top deals are pointing towards them", and of showing the labels and keeping such shops out of the
deals: "build it"."""

from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.text import slugify

from . import offers, picks, pricing, shop_check
from .models import DailyLowestPrice, Listing, Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class Base(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.harbour = make_retailer("Harbour Games")
        self.kestrel = make_retailer("Kestrel Cards")
        self.moor = make_retailer("Moor Toys")

    def product(self, name, **prices):
        """A product priced at the shops named: product("Box", harbour="80.00", kestrel="100.00")."""
        product = make_product(self.set, name=name, slug=slugify(name))
        for shop, price in prices.items():
            make_listing(product, getattr(self, shop), price=price)
        return product

    def caution(self, shop):
        shop_check.confirm(shop, "caution", "Mostly negative public reviews.", "")

    def loaded(self):
        return Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()).order_by("name")

    def summary(self, product):
        return offers.summarise(self.loaded().get(pk=product.pk))


class MarkTests(Base):
    def test_a_visitor_is_shown_checked_and_caution_and_nothing_else(self):
        self.assertEqual((self.harbour.shop_mark, self.harbour.held_back), ("", False))
        for label, mark, held in (("checked", "checked", False), ("caution", "caution", True), ("new", "", False),
                                  ("", "", False)):
            shop_check.confirm(self.harbour, label, "A reason.", "")
            self.assertEqual((self.harbour.shop_mark, self.harbour.held_back), (mark, held), label)

    def test_every_label_but_caution_may_be_put_forward(self):
        self.assertEqual(set(offers.PUT_FORWARD), {"", "checked", "new"})
        self.assertNotIn(Retailer.Trust.CAUTION, offers.PUT_FORWARD)


class DealTests(Base):
    def test_a_saving_at_a_shop_marked_caution_is_worked_out_and_never_a_deal(self):
        box = self.product("Booster Box", harbour="80.00", kestrel="100.00")
        found = self.summary(box)
        self.assertEqual((found.percent, found.held_back, found.is_deal), (20, False, True))
        self.caution(self.kestrel)                       # the dearer shop: the cheapest is still put forward
        found = self.summary(box)
        self.assertEqual((found.held_back, found.is_deal), (False, True))
        self.caution(self.harbour)
        found = self.summary(box)
        self.assertEqual((found.best.retailer, found.percent, found.held_back, found.is_deal),
                         (self.harbour, 20, True, False))

    def test_a_product_nobody_sells_is_not_held_back(self):
        self.assertFalse(self.summary(self.product("Empty Box")).held_back)

    def test_the_biggest_savings_leave_it_out(self):
        box = self.product("Booster Box", harbour="80.00", kestrel="100.00")
        tin = self.product("Tin", kestrel="40.00", moor="50.00")
        self.assertEqual([product for product, _ in offers.biggest_savings(self.loaded())], [box, tin])
        self.caution(self.harbour)
        self.assertEqual([product for product, _ in offers.biggest_savings(self.loaded())], [tin])

    def test_picked_for_you_leaves_it_out(self):
        seen = self.product("Elite Trainer Box", moor="50.00")
        box = self.product("Booster Box", harbour="80.00", kestrel="100.00")
        tin = self.product("Tin", kestrel="40.00", moor="50.00")
        self.assertEqual({product for product, _summary, _tag in picks.picks([seen])}, {box, tin})
        self.caution(self.harbour)
        self.assertEqual({product for product, _summary, _tag in picks.picks([seen])}, {tin})


class LeadTests(Base):
    """offers.without_caution_lead: the same rule for a list of deals built in the database."""

    def names(self):
        return list(offers.without_caution_lead(Product.objects.active().order_by("name")).values_list("name", flat=True))

    def test_with_no_shop_marked_caution_the_list_is_untouched_for_one_small_query(self):
        self.product("Booster Box", harbour="80.00", kestrel="100.00")
        products = Product.objects.active()
        with CaptureQueriesContext(connection) as asked:
            self.assertIs(offers.without_caution_lead(products), products)
        self.assertEqual(len(asked), 1)

    def test_only_a_product_the_shop_leads_is_left_out(self):
        self.product("Booster Box", harbour="80.00", kestrel="100.00")          # the shop is cheapest
        self.product("Bundle", harbour="35.00", kestrel="30.00")                # another shop is cheaper
        self.product("Collection", harbour="60.00")                             # the shop alone
        self.product("Deck")                                                    # nobody sells it
        # In stock leads a pre-order, as on the product page: the shop's cheaper pre-order does not lead.
        tin = self.product("Tin", kestrel="25.00")
        make_listing(tin, self.harbour, price="20.00", availability=Listing.Availability.PREORDER)
        every = ["Booster Box", "Bundle", "Collection", "Deck", "Tin"]
        self.assertEqual(self.names(), every)
        self.caution(self.harbour)
        self.assertEqual(self.names(), ["Bundle", "Deck", "Tin"])
        # The leading offer is the one a list's summary leads with, product by product.
        for product in self.loaded():
            led = offers.summarise(product).held_back
            self.assertEqual(product.name not in self.names(), led, product.name)
        # Switched off, the shop leads nothing; with its label taken off, it is put forward again.
        Retailer.objects.filter(pk=self.harbour.pk).update(is_active=False)
        self.assertEqual(self.names(), every)
        Retailer.objects.filter(pk=self.harbour.pk).update(is_active=True)
        shop_check.confirm(self.harbour, "", "", "")
        self.assertEqual(self.names(), every)

    def test_a_price_drop_to_its_price_is_not_put_forward(self):
        today = timezone.localdate()
        box = self.product("Booster Box", harbour="80.00", kestrel="100.00")
        tin = self.product("Tin", kestrel="40.00", moor="50.00")
        for product, was in ((box, "100.00"), (tin, "50.00")):
            DailyLowestPrice.objects.create(product=product, date=today - timedelta(days=8), price=Decimal(was))
        self.assertEqual(set(pricing.price_drops(days=7, today=today)), {box, tin})
        self.caution(self.harbour)
        self.assertEqual(list(pricing.price_drops(days=7, today=today)), [tin])
        self.assertEqual(list(pricing.price_drops(days=7, today=today, game=self.set.game)), [tin])
