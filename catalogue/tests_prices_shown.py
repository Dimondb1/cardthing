"""Unknown delivery is never shown as free, and savings are only claimed between comparable prices."""

from decimal import Decimal
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import offers, pricing
from .models import DailyLowestPrice, Listing, Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


def unknown(listing):
    Listing.objects.filter(pk=listing.pk).update(delivery_known=False, delivery_cost=0)
    listing.refresh_from_db()
    return listing


class DeliveryRuleTests(TestCase):
    def test_a_missing_charge_is_unknown_not_free(self):
        shop = make_retailer("Shop", delivery_cost=None, free_delivery_over=Decimal("40"))
        self.assertIsNone(shop.delivery_for(Decimal("38.95")))
        self.assertEqual(shop.delivery_for(Decimal("40.00")), Decimal("0.00"))

    def test_a_charge_known_only_up_to_a_value(self):
        shop = make_retailer("Shop", delivery_cost=Decimal("1.95"), delivery_cost_up_to=Decimal("20"))
        self.assertEqual(shop.delivery_for(Decimal("12.00")), Decimal("1.95"))
        self.assertIsNone(shop.delivery_for(Decimal("25.00")))

    def test_new_shops_start_unknown(self):
        self.assertIsNone(make_retailer("Shop").delivery_cost)

    def test_record_check_stores_unknown_delivery_as_unknown(self):
        product = make_product(make_set(make_game()))
        listing = make_listing(product, make_retailer("Shop"), price="38.95")
        pricing.record_check(listing, price=Decimal("38.95"), delivery_cost=None, availability=Listing.Availability.IN_STOCK)
        listing.refresh_from_db()
        self.assertFalse(listing.delivery_known)
        self.assertEqual(listing.shown_price, Decimal("38.95"))
        # An item price is not a delivered price, so it never enters the price history.
        self.assertFalse(DailyLowestPrice.objects.filter(product=product).exists())

    def test_changing_a_shops_rules_reprices_its_listings(self):
        shop = make_retailer("Shop", delivery_cost=Decimal("0"))
        listing = make_listing(make_product(make_set(make_game())), shop, price="30.00")
        shop.delivery_cost = None
        shop.save()
        self.assertEqual(pricing.apply_delivery_rules(shop), 1)
        listing.refresh_from_db()
        self.assertFalse(listing.delivery_known)

    def test_setup_shops_turns_a_never_known_zero_into_unknown_and_keeps_admin_charges(self):
        mm = Retailer.objects.create(name="Magic Madhouse", slug="magic-madhouse", website="https://magicmadhouse.co.uk/",
                                     delivery_cost=Decimal("0"), free_delivery_over=Decimal("40"))
        set_by_owner = Retailer.objects.create(name="120HP", slug="120hp", website="https://www.120hp.co.uk/",
                                               delivery_cost=Decimal("3.50"))
        listing = make_listing(make_product(make_set(make_game())), mm, price="38.95")
        call_command("setup_shops", stdout=StringIO())
        mm.refresh_from_db()
        set_by_owner.refresh_from_db()
        listing.refresh_from_db()
        self.assertIsNone(mm.delivery_cost)
        self.assertEqual(set_by_owner.delivery_cost, Decimal("3.50"))
        self.assertFalse(listing.delivery_known)
        self.assertEqual(Retailer.objects.get(slug="card-empire").delivery_cost_up_to, Decimal("20"))


class OfferOrderTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="Judgment Booster", product_type="booster_pack")
        self.known = make_listing(self.product, make_retailer("Known"), price="40.00", delivery="2.00")
        self.mystery = unknown(make_listing(self.product, make_retailer("Mystery"), price="30.00"))

    def summary(self):
        product = Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()).get(pk=self.product.pk)
        return product, offers.summarise(product)

    def test_confirmed_delivered_prices_come_first_and_no_saving_is_claimed(self):
        product, summary = self.summary()
        self.assertEqual(summary.best, self.known)
        self.assertEqual(summary.second, self.mystery)
        self.assertIsNone(summary.saving)
        self.assertEqual(product.lowest_price, Decimal("42.00"))
        self.assertEqual(product.lowest_known, Decimal("42.00"))

    def test_with_only_unknown_delivery_the_list_price_is_the_item_price(self):
        self.known.delete()
        product, summary = self.summary()
        self.assertEqual(product.lowest_price, Decimal("30.00"))
        self.assertIsNone(product.lowest_known)
        self.assertEqual(summary.best, self.mystery)
        self.assertIsNone(summary.badge)


class SavingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.set = make_set(make_game())
        self.shop = make_retailer("Magic Madhouse", delivery_cost=Decimal("0"))
        self.ebay = make_retailer("eBay", slug="ebay", source_type=Retailer.Source.EBAY)

    def summary(self, product):
        product = Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()).get(pk=product.pk)
        return offers.summarise(product)

    def test_a_gap_too_large_to_be_real_is_never_a_saving(self):
        pack = make_product(self.set, name="Judgment Booster", product_type="booster_pack")
        make_listing(pack, self.shop, price="38.95")
        make_listing(pack, self.ebay, price="999.95")
        DailyLowestPrice.objects.create(product=pack, date=timezone.localdate() - timezone.timedelta(days=2), price="50.00")
        product = Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()).get(pk=pack.pk)
        summary = offers.summarise(product, offers.week_low_map([pack.pk]))
        self.assertIsNone(summary.saving)
        self.assertIsNone(summary.badge)
        self.assertTrue(summary.suspect)
        out = StringIO()
        call_command("suspect_savings", stdout=out)
        self.assertIn("Judgment Booster: £38.95 at Magic Madhouse against £999.95 at eBay", out.getvalue())

    def test_a_suspect_or_unknown_delivery_product_is_never_featured(self):
        from web.views import featured_deals

        pack = make_product(self.set, name="Judgment Booster", product_type="booster_pack")
        make_listing(pack, self.shop, price="38.95")
        make_listing(pack, self.ebay, price="999.95")
        DailyLowestPrice.objects.create(product=pack, date=timezone.localdate() - timezone.timedelta(days=2), price="50.00")
        bundle = make_product(self.set, name="Booster Bundle", slug="bundle", product_type="bundle")
        unknown(make_listing(bundle, self.shop, price="20.00"))
        make_listing(bundle, self.ebay, price="25.00")
        products = Product.objects.for_lists().prefetch_related(offers.buyable_prefetch())
        self.assertEqual(featured_deals(products), [])

    def test_a_fall_too_large_to_be_real_is_not_a_price_drop(self):
        box = make_product(self.set, name="Archazia's Island Booster Box", product_type="booster_box")
        make_listing(box, self.shop, price="19.99")
        before = timezone.localdate() - timezone.timedelta(days=7)
        DailyLowestPrice.objects.create(product=box, date=before, price="107.00")
        self.assertEqual(list(pricing.price_drops()), [])
        Listing.objects.filter(product=box).update(price=Decimal("90.00"))
        self.assertEqual([p.name for p in pricing.price_drops()], [box.name])

    def test_home_never_prints_the_false_saving(self):
        pack = make_product(self.set, name="Judgment Booster", product_type="booster_pack")
        make_listing(pack, self.shop, price="38.95")
        make_listing(pack, self.ebay, price="999.95")
        for url in (reverse("web:home"), reverse("web:search"), pack.get_absolute_url()):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "961")


class ShownPriceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.product = make_product(make_set(make_game()), name="Judgment Booster", product_type="booster_pack")

    def test_unknown_delivery_reads_plus_delivery_everywhere(self):
        unknown(make_listing(self.product, make_retailer("Magic Madhouse"), price="38.95"))
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, "Lowest item price")
        self.assertContains(page, "+ delivery")
        self.assertContains(page, "Delivery charge not confirmed")
        self.assertContains(page, "Check the total at the shop")
        self.assertContains(page, "Not confirmed")
        self.assertNotContains(page, "£38.95 delivered")
        self.assertNotContains(page, ">Cheapest<")
        search = self.client.get(reverse("web:search"))
        self.assertContains(search, "+ delivery")
        prices = self.client.get(reverse("web:product_prices_api", args=[self.product.slug])).json()["listings"][0]
        self.assertEqual((prices["total"], prices["note"]), ("£38.95 + delivery", "Delivery charge not confirmed"))

    def test_the_delivered_total_is_the_big_number(self):
        make_listing(self.product, make_retailer("Zatu"), price="21.50", delivery="2.94")
        make_listing(self.product, make_retailer("Other"), price="30.00", delivery="1.00")
        page = self.client.get(self.product.get_absolute_url()).content.decode()
        self.assertIn('<span class="price price--lg">£24.44</span>', page)
        self.assertIn("£21.50 plus £2.94 delivery", page)
        self.assertIn("Compare 2 shops", page)
        card = self.client.get(reverse("web:search")).content.decode()
        self.assertIn("£24.44", card)

    def test_one_shop_says_so(self):
        make_listing(self.product, make_retailer("Zatu"), price="21.50", delivery="2.94")
        self.assertContains(self.client.get(self.product.get_absolute_url()), "1 shop found")

    def test_about_page_separates_featured_deals_from_the_price_order(self):
        page = self.client.get(reverse("web:about"))
        self.assertContains(page, "Retailers cannot pay to change that order")
        self.assertContains(page, "featured deals row on the home page is different")


class MergeTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.shop = make_retailer("Shop", delivery_cost=Decimal("3.00"))
        self.other_shop = make_retailer("Other", delivery_cost=Decimal("3.00"))

    def test_a_count_with_and_without_its_noun_merges_and_keeps_history(self):
        from .models import ProductAlias, Restock, StockAlert

        keep = make_product(self.set, name="Prismatic Evolutions Booster Bundle Display (10 Bundles)", slug="display-10-bundles",
                            product_type="bundle")
        dup = make_product(self.set, name="Prismatic Evolutions Booster Bundle Display (10)", slug="display-10", product_type="bundle")
        make_listing(keep, self.shop, price="1200.00")
        make_listing(keep, make_retailer("Third"), price="1210.00")
        listing = make_listing(dup, self.other_shop, price="1252.99")
        day = timezone.localdate()
        DailyLowestPrice.objects.create(product=keep, date=day, price="1203.00")
        DailyLowestPrice.objects.create(product=dup, date=day, price="1190.00")
        DailyLowestPrice.objects.create(product=dup, date=day - timezone.timedelta(days=3), price="1250.00")
        Restock.objects.create(product=dup, retailer=self.other_shop, listing=listing, price="1255.99")
        StockAlert.objects.create(product=dup, email="a@example.com")
        call_command("merge_duplicates", stdout=StringIO())
        self.assertFalse(Product.objects.filter(pk=dup.pk).exists())
        self.assertEqual(keep.listings.count(), 3)
        self.assertEqual(DailyLowestPrice.objects.get(product=keep, date=day).price, Decimal("1190.00"))
        self.assertTrue(DailyLowestPrice.objects.filter(product=keep, date=day - timezone.timedelta(days=3)).exists())
        self.assertEqual(Restock.objects.get().product, keep)
        self.assertEqual(StockAlert.objects.get().product, keep)
        self.assertEqual(ProductAlias.objects.get(slug="display-10").product, keep)
        self.assertRedirects(self.client.get("/products/display-10/"), keep.get_absolute_url(), status_code=301)

    def test_loose_merges_filler_words_only_when_asked_and_keeps_genuine_variants(self):
        names = ["Pitch Black Pokémon Center Elite Trainer Box (Exclusive)",
                 "Mega Evolution Pitch Black Pokemon Center Elite Trainer Box",
                 "Crown Zenith Pokemon Center Elite Trainer Box",
                 "Crown Zenith Pokemon Center Elite Trainer Box Plus",
                 "Scarlet & Violet Elite Trainer Box",
                 "Sword & Shield Elite Trainer Box"]
        for i, name in enumerate(names):
            make_listing(make_product(self.set, name=name, slug=f"p{i}"), self.shop, price="60.00")
        call_command("merge_duplicates", stdout=StringIO())
        self.assertEqual(Product.objects.count(), 6)
        out = StringIO()
        call_command("merge_duplicates", "--loose", "--dry-run", stdout=out)
        self.assertEqual(Product.objects.count(), 6)
        self.assertIn("1 products merged", out.getvalue())
        call_command("merge_duplicates", "--loose", stdout=StringIO())
        left = set(Product.objects.values_list("name", flat=True))
        self.assertEqual(len(left), 5)
        self.assertEqual(len(left & set(names[:2])), 1)
        self.assertTrue(set(names[2:]) <= left)


class ChecksPageTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        cache.clear()
        self.set = make_set(make_game())
        self.shop = make_retailer("Magic Madhouse", delivery_cost=Decimal("0"))
        self.ebay = make_retailer("eBay", slug="ebay", source_type=Retailer.Source.EBAY)
        self.pack = make_product(self.set, name="Judgment Booster", product_type="booster_pack")
        self.cheap = make_listing(self.pack, self.shop, price="38.95")
        self.dear = make_listing(self.pack, self.ebay, price="999.95")
        self.a = make_product(self.set, name="Pitch Black Pokémon Center Elite Trainer Box (Exclusive)", slug="pb-a")
        self.b = make_product(self.set, name="Mega Evolution Pitch Black Pokemon Center Elite Trainer Box", slug="pb-b")
        make_listing(self.a, self.shop, price="80.00")
        mystery = make_retailer("120HP", delivery_cost=None)
        unknown(make_listing(self.b, mystery, price="85.00"))
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("checks")

    def test_page_lists_all_three_and_is_staff_only(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Wrong matches (1)")
        self.assertContains(page, "£38.95 at Magic Madhouse")
        self.assertContains(page, "Possible duplicates (1)")
        self.assertContains(page, "Unknown delivery charges (1)")
        self.assertContains(page, "1 price without a known delivery charge")
        self.assertContains(self.client.get(reverse("admin:index")), "Things to check")
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_hide_button_hides_the_wrong_listing(self):
        self.client.post(self.url, {"action": "hide", "listing": self.dear.pk})
        self.dear.refresh_from_db()
        self.assertFalse(self.dear.is_active)
        self.assertContains(self.client.get(self.url), "Wrong matches (0)")

    def test_merge_button_merges_only_the_offered_group(self):
        stale = self.client.post(self.url, {"action": "merge", "keep": self.a.pk, "other": [self.pack.pk]}, follow=True)
        self.assertContains(stale, "That group has changed")
        self.assertEqual(Product.objects.filter(pk__in=[self.a.pk, self.b.pk]).count(), 2)
        self.client.post(self.url, {"action": "merge", "keep": self.a.pk, "other": [self.b.pk]})
        self.assertFalse(Product.objects.filter(pk=self.b.pk).exists())
        self.assertEqual(self.a.listings.count(), 2)
        self.assertRedirects(self.client.get("/products/pb-b/"), self.a.get_absolute_url(), status_code=301)


class UnpricedTests(TestCase):
    """A price of nothing is not a price: it never becomes a product's lowest price."""

    def setUp(self):
        self.shop = make_retailer("Harbour Games", delivery_cost=Decimal("2.00"))
        self.product = make_product(make_set(make_game()), ean="0820650851230", image_url="https://img.example/x.jpg")

    def test_a_zero_price_preorder_changes_nothing_and_writes_no_history(self):
        from .models import Restock

        listing = make_listing(self.product, self.shop, price="45.00", delivery="2.00", hours_ago=5,
                               availability=Listing.Availability.OUT_OF_STOCK)
        old = listing.last_checked
        self.assertIsNone(pricing.record_check(listing, price=Decimal("0.00"), delivery_cost=Decimal("2.00"),
                                               availability=Listing.Availability.PREORDER))
        listing.refresh_from_db()
        self.assertEqual((listing.price, listing.delivery_cost, listing.delivery_known), (Decimal("45.00"), Decimal("2.00"), True))
        self.assertEqual((listing.availability, listing.last_checked), (Listing.Availability.OUT_OF_STOCK, old))
        self.assertIsNone(listing.back_in_stock_at)
        self.assertFalse(DailyLowestPrice.objects.exists())
        # In stock at nothing is treated the same way: a shop never sells for nothing.
        pricing.record_check(listing, price=Decimal("0"), delivery_cost=None, availability=Listing.Availability.IN_STOCK)
        listing.refresh_from_db()
        self.assertEqual((listing.price, listing.delivery_known), (Decimal("45.00"), True))
        self.assertEqual((listing.availability, listing.last_checked), (Listing.Availability.OUT_OF_STOCK, old))
        self.assertIsNone(listing.back_in_stock_at)
        self.assertFalse(Restock.objects.exists())
        self.assertFalse(DailyLowestPrice.objects.exists())

    def test_no_price_never_brings_an_old_price_back_as_buyable_or_freshly_checked(self):
        from datetime import timedelta

        from .importers import Offer, apply_offers

        listing = make_listing(self.product, self.shop, price="20.00", delivery="2.00", hours_ago=24 * 30,
                               availability=Listing.Availability.OUT_OF_STOCK, url="https://harbour.example/products/pe-etb")
        old = listing.last_checked
        for state in (Listing.Availability.IN_STOCK, Listing.Availability.PREORDER):
            offer = Offer(title="ETB", url=listing.url, price=Decimal("0"), ean="0820650851230", availability=state)
            self.assertEqual(apply_offers(self.shop, [offer])[1], 0)
            listing.refresh_from_db()
            self.assertEqual((listing.availability, listing.last_checked), (Listing.Availability.OUT_OF_STOCK, old))
            self.assertIsNone(Product.objects.with_prices().get(pk=self.product.pk).lowest_known)
        # A listing in stock at a real price keeps it, but the check does not make that price fresher.
        Listing.objects.filter(pk=listing.pk).update(availability=Listing.Availability.IN_STOCK,
                                                     last_checked=timezone.now() - timedelta(hours=5))
        listing.refresh_from_db()
        old = listing.last_checked
        offer = Offer(title="ETB", url=listing.url, price=Decimal("0"), ean="0820650851230")
        apply_offers(self.shop, [offer])
        listing.refresh_from_db()
        self.assertEqual((listing.availability, listing.price, listing.last_checked),
                         (Listing.Availability.IN_STOCK, Decimal("20.00"), old))

    def test_a_zero_price_new_offer_creates_no_listing_and_is_listed_as_unmatched(self):
        from .importers import Offer, apply_offers

        offer = Offer(title="Prismatic Evolutions ETB", url="https://harbour.example/products/pe-etb", price=Decimal("0.00"),
                      ean="0820650851230", availability=Listing.Availability.PREORDER)
        found, updated, unmatched = apply_offers(self.shop, [offer])
        self.assertEqual((found, updated), (1, 0))
        self.assertEqual(unmatched, ["Prismatic Evolutions ETB [0820650851230] https://harbour.example/products/pe-etb (no price)"])
        self.assertFalse(Listing.objects.exists())
        self.assertFalse(DailyLowestPrice.objects.exists())
        # Called directly, record_check does not create one either.
        new = Listing(product=self.product, retailer=self.shop, url=offer.url)
        self.assertIsNone(pricing.record_check(new, price=Decimal("0"), delivery_cost=None, availability=Listing.Availability.PREORDER))
        self.assertFalse(Listing.objects.exists())

    def test_a_zero_price_offer_updates_the_stock_of_an_existing_listing_through_an_import(self):
        from .importers import Offer, apply_offers

        listing = make_listing(self.product, self.shop, price="45.00", delivery="2.00", hours_ago=5,
                               url="https://harbour.example/products/pe-etb")
        offer = Offer(title="ETB", url=listing.url, price=Decimal("0"), ean="0820650851230",
                      availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(apply_offers(self.shop, [offer])[1], 1)
        listing.refresh_from_db()
        self.assertEqual((listing.price, listing.availability), (Decimal("45.00"), Listing.Availability.OUT_OF_STOCK))
        self.assertGreater(listing.last_checked, timezone.now() - timezone.timedelta(minutes=1))

    def test_a_priceless_variant_never_stands_in_for_a_priced_one(self):
        from .importers import Offer, apply_offers

        offers = [
            Offer(title="ETB", url="https://harbour.example/products/pe-etb", price=Decimal("45.00"), ean="0820650851230"),
            Offer(title="ETB (pre-order)", url="https://harbour.example/products/pe-etb-2", price=Decimal("0"),
                  ean="0820650851230", availability=Listing.Availability.PREORDER),
        ]
        apply_offers(self.shop, offers)
        listing = Listing.objects.get(product=self.product, retailer=self.shop)
        self.assertEqual((listing.price, listing.availability), (Decimal("45.00"), Listing.Availability.IN_STOCK))
        self.assertEqual(self.product.daily_prices.get().price, Decimal("47.00"))

    def test_a_priceless_variant_read_first_takes_nothing_over(self):
        from .importers import Offer, apply_offers

        listing = make_listing(self.product, self.shop, price="45.00", delivery="2.00", hours_ago=5,
                               url="https://harbour.example/products/pe-etb?variant=2", title="ETB")

        def read(price, availability=Listing.Availability.IN_STOCK):
            offers = [
                Offer(title="ETB (Deposit)", url="https://harbour.example/products/pe-etb?variant=1", price=Decimal("0"),
                      ean="0820650851230"),
                Offer(title="ETB", url="https://harbour.example/products/pe-etb?variant=2", price=Decimal(price),
                      ean="0820650851230", availability=availability),
            ]
            checked_at = timezone.now()
            found, updated, unmatched = apply_offers(self.shop, offers, checked_at=checked_at)
            self.assertEqual((found, updated, unmatched), (2, 1, []))
            listing.refresh_from_db()
            self.assertEqual((listing.url, listing.title), ("https://harbour.example/products/pe-etb?variant=2", "ETB"))
            self.assertEqual(listing.last_checked, checked_at)

        # The priced variant is unchanged: the listing is stamped and keeps its link.
        read("45.00")
        self.assertEqual(listing.price, Decimal("45.00"))
        # The priced variant moved: the new price is saved, not skipped.
        read("50.00")
        self.assertEqual((listing.price, listing.availability), (Decimal("50.00"), Listing.Availability.IN_STOCK))
        self.assertEqual(self.product.daily_prices.get().price, Decimal("52.00"))
        # The priced variant sold out: an in stock variant without a price does not keep it buyable.
        read("50.00", Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(listing.availability, Listing.Availability.OUT_OF_STOCK)

    def test_a_product_seen_only_without_a_price_is_not_swept_or_stamped(self):
        from .importers import Offer, apply_offers

        listing = make_listing(self.product, self.shop, price="45.00", delivery="2.00", hours_ago=5,
                               url="https://harbour.example/products/pe-etb", title="ETB")
        old = listing.last_checked
        offers = [
            Offer(title="ETB (Deposit)", url="https://harbour.example/products/pe-etb?variant=1", price=Decimal("0"),
                  ean="0820650851230", availability=Listing.Availability.OUT_OF_STOCK),
            Offer(title="ETB (Reserve)", url="https://harbour.example/products/pe-etb?variant=3", price=Decimal("0"),
                  ean="0820650851230"),
        ]
        # One variant says in stock, so the complete-run sweep must not take it out either.
        self.assertEqual(apply_offers(self.shop, offers, complete=True), (2, 0, []))
        listing.refresh_from_db()
        self.assertEqual((listing.url, listing.title, listing.availability, listing.last_checked),
                         ("https://harbour.example/products/pe-etb", "ETB", Listing.Availability.IN_STOCK, old))
        # Every variant without a price says sold out: the listing goes out of stock and nothing else changes.
        checked_at = timezone.now()
        self.assertEqual(apply_offers(self.shop, offers[:1], checked_at=checked_at)[1], 1)
        listing.refresh_from_db()
        self.assertEqual((listing.url, listing.price, listing.availability, listing.last_checked),
                         ("https://harbour.example/products/pe-etb", Decimal("45.00"), Listing.Availability.OUT_OF_STOCK,
                          checked_at))
