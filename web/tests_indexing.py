"""What search engines are told: the sitemap's pages and the dates their content last changed."""

import re
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from catalogue import pricing, sanity
from catalogue.models import DailyLowestPrice, Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web.sitemaps import RECENT_DAYS, ProductSitemap, SetSitemap

IN_STOCK, PREORDER, OUT = Listing.Availability.IN_STOCK, Listing.Availability.PREORDER, Listing.Availability.OUT_OF_STOCK


def sitemap_entries(client):
    """{location path: lastmod date string or None} from /sitemap.xml."""
    body = client.get("/sitemap.xml").content.decode()
    entries = {}
    for block in re.findall(r"<url>(.*?)</url>", body, re.S):
        loc = re.search(r"<loc>http://testserver([^<]*)</loc>", block).group(1)
        mod = re.search(r"<lastmod>([^<]*)</lastmod>", block)
        entries[loc] = mod.group(1) if mod else None
    return entries


def day(moment):
    return timezone.localtime(moment).date().isoformat()


class SitemapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.set = make_set(cls.game, release_date=timezone.localdate() - timedelta(days=200))
        cls.shop = make_retailer("Harbour Games")
        cls.other_shop = make_retailer("Northgate Cards")
        today = timezone.localdate()

        def product(name, **kwargs):
            return make_product(cls.set, name=name, slug=name.lower().replace(" ", "-"), **kwargs)

        cls.in_stock = product("In Stock Box")
        make_listing(cls.in_stock, cls.shop)
        cls.preorder = product("Preorder Box")
        make_listing(cls.preorder, cls.shop, availability=PREORDER)
        cls.coming = product("Coming Box", release_date=today + timedelta(days=20))
        cls.later_set = make_set(cls.game, name="Later Set", slug="later-set", release_date=today + timedelta(days=40))
        cls.in_later_set = make_product(cls.later_set, name="Later Set Booster Box", slug="later-box")
        cls.recently_sold_out = product("Recently Sold Out Box")
        make_listing(cls.recently_sold_out, cls.shop, availability=OUT)
        DailyLowestPrice.objects.create(product=cls.recently_sold_out, date=today - timedelta(days=RECENT_DAYS - 1),
                                        price="50.00")
        cls.long_gone = product("Long Gone Box")
        make_listing(cls.long_gone, cls.shop, availability=OUT)
        DailyLowestPrice.objects.create(product=cls.long_gone, date=today - timedelta(days=RECENT_DAYS + 1), price="50.00")
        cls.never_priced = product("Never Priced Box")
        make_listing(cls.never_priced, cls.shop, availability=OUT)
        cls.unlisted = product("Unlisted Box")
        cls.stale = product("Stale Box")
        make_listing(cls.stale, cls.shop, hours_ago=200)
        cls.kept_out = product("Kept Out Box")
        make_listing(cls.kept_out, cls.shop, sanity=Listing.Sanity.EXCLUDED)
        cls.hidden = product("Hidden Box", is_active=False)
        make_listing(cls.hidden, cls.shop)
        cls.hidden_listing = product("Hidden Listing Box")
        make_listing(cls.hidden_listing, cls.shop, is_active=False)

    def setUp(self):
        cache.clear()

    def test_lists_products_worth_indexing_and_leaves_out_the_long_tail(self):
        entries = sitemap_entries(self.client)
        listed = {p.slug for p in Product.objects.all() if p.get_absolute_url() in entries}
        self.assertEqual(listed, {"in-stock-box", "preorder-box", "coming-box", "later-box", "recently-sold-out-box"})
        # The games, sets and fixed pages stay.
        for url in (self.game.get_absolute_url(), self.set.get_absolute_url(), self.later_set.get_absolute_url(),
                    "/", "/deals/", "/terms/"):
            self.assertIn(url, entries)

    def test_a_page_left_out_stays_reachable_and_indexable(self):
        url = self.never_priced.get_absolute_url()
        self.assertNotIn(url, sitemap_entries(self.client))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "noindex")
        # Its set page's out of stock tab links it, so a crawler still finds it.
        self.assertContains(self.client.get(self.set.get_absolute_url()), "?tab=out-of-stock")
        self.assertContains(self.client.get(self.set.get_absolute_url(), {"tab": "out-of-stock"}), url)

    def test_no_changefreq_or_priority(self):
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertNotIn("<changefreq>", body)
        self.assertNotIn("<priority>", body)

    def test_lastmod_is_the_last_shown_change_not_the_last_check(self):
        now = timezone.now()
        Product.objects.filter(pk=self.in_stock.pk).update(updated_at=now - timedelta(days=40))
        listing = self.in_stock.listings.get()
        Listing.objects.filter(pk=listing.pk).update(changed_at=now - timedelta(days=12))
        url = self.in_stock.get_absolute_url()
        self.assertEqual(sitemap_entries(self.client)[url], day(now - timedelta(days=12)))

        # A check that finds the same price and stock moves nothing.
        listing.refresh_from_db()
        pricing.record_check(listing, price=listing.price, delivery_cost=listing.delivery_cost, availability=IN_STOCK)
        self.assertEqual(sitemap_entries(self.client)[url], day(now - timedelta(days=12)))

        # A new price does.
        listing.refresh_from_db()
        checked = now - timedelta(days=2)
        pricing.record_check(listing, price=Decimal("44.00"), delivery_cost=listing.delivery_cost,
                             availability=IN_STOCK, checked_at=checked)
        self.assertEqual(sitemap_entries(self.client)[url], day(checked))

    def test_lastmod_follows_the_products_own_edits_and_ignores_hidden_listings(self):
        now = timezone.now()
        Product.objects.filter(pk=self.recently_sold_out.pk).update(updated_at=now - timedelta(days=9))
        self.recently_sold_out.listings.update(changed_at=now - timedelta(days=20))
        hidden = make_listing(self.recently_sold_out, self.other_shop, is_active=False)
        Listing.objects.filter(pk=hidden.pk).update(changed_at=now - timedelta(days=1))
        self.assertEqual(sitemap_entries(self.client)[self.recently_sold_out.get_absolute_url()],
                         day(now - timedelta(days=9)))

    def test_lastmod_falls_back_to_a_restock_before_changes_were_recorded(self):
        now = timezone.now()
        Product.objects.filter(pk=self.in_stock.pk).update(updated_at=now - timedelta(days=40))
        self.in_stock.listings.update(changed_at=None, back_in_stock_at=now - timedelta(days=6))
        item = next(p for p in ProductSitemap().items() if p.pk == self.in_stock.pk)
        self.assertEqual(ProductSitemap().lastmod(item), now - timedelta(days=6))

    def test_a_set_changes_when_one_of_its_products_does(self):
        now = timezone.now()
        Product.objects.filter(product_set=self.set).update(updated_at=now - timedelta(days=50))
        Listing.objects.filter(product__product_set=self.set).update(changed_at=now - timedelta(days=30))
        self.preorder.listings.update(changed_at=now - timedelta(days=3))
        entries = sitemap_entries(self.client)
        self.assertEqual(entries[self.set.get_absolute_url()], day(now - timedelta(days=3)))
        # A set whose products have no recorded change carries their latest edit.
        self.assertEqual(entries[self.later_set.get_absolute_url()], day(self.in_later_set.updated_at))
        empty = make_set(self.game, name="Empty Set", slug="empty-set")
        item = next(s for s in SetSitemap().items() if s.pk == empty.pk)
        self.assertIsNone(SetSitemap().lastmod(item))

    def test_two_queries_a_section_however_many_products(self):
        # A count and a page each for products, games and sets; the fixed pages need none.
        with self.assertNumQueries(6):
            self.client.get("/sitemap.xml")
        for n in range(15):
            product = make_product(self.set, name=f"Extra Box {n}", slug=f"extra-{n}")
            make_listing(product, self.shop)
            make_listing(product, self.other_shop, availability=OUT)
        with self.assertNumQueries(6):
            response = self.client.get("/sitemap.xml")
        self.assertContains(response, "/products/extra-14/")


class ChangedAtTests(TestCase):
    """Listing.changed_at moves when what the page shows for a shop changes, and only then."""

    def setUp(self):
        self.set = make_set(make_game())
        self.product = make_product(self.set, name="Surging Sparks Booster Box", product_type="booster_box")
        self.shop = make_retailer("Shop", delivery_cost=Decimal("0"))
        self.listing = make_listing(self.product, self.shop, price="100.00")

    def check(self, price, availability=IN_STOCK, at=None):
        self.listing.refresh_from_db()
        pricing.record_check(self.listing, price=Decimal(price), delivery_cost=Decimal("0.00"),
                             availability=availability, checked_at=at)
        self.listing.refresh_from_db()
        return self.listing.changed_at

    def test_a_check_stamps_only_a_change(self):
        first = timezone.now() - timedelta(hours=5)
        self.assertIsNone(self.check("100.00", at=first))
        self.assertEqual(self.check("95.00", at=first), first)
        later = first + timedelta(hours=2)
        self.assertEqual(self.check("95.00", at=later), first)
        self.assertEqual(self.check("95.00", availability=OUT, at=later), later)
        latest = later + timedelta(hours=1)
        # No price, out of stock as it already was: nothing new.
        self.assertEqual(self.check("0", availability=OUT, at=latest), later)

    def test_a_shop_read_stamps_what_it_changed_and_nothing_else(self):
        from catalogue.importers import Offer, apply_offers
        from catalogue.models import Retailer

        shop = make_retailer("Reader", source_type=Retailer.Source.SHOPIFY, source_url="https://reader.example",
                             delivery_cost=Decimal("0"))
        same = make_listing(self.product, shop, price="50.00", url="https://reader.example/products/same")
        other = make_product(self.set, name="Surging Sparks Elite Trainer Box", slug="ss-etb")
        swept = make_listing(other, shop, price="40.00", url="https://reader.example/products/gone")
        apply_offers(shop, [Offer(title="Surging Sparks Booster Box", url=same.url, price=Decimal("50.00"))])
        same.refresh_from_db()
        swept.refresh_from_db()
        self.assertIsNone(same.changed_at)
        self.assertGreater(same.last_checked, timezone.now() - timedelta(minutes=1))
        self.assertEqual(swept.availability, OUT)
        self.assertEqual(swept.changed_at, swept.last_checked)

    def test_a_price_lost_to_out_of_stock_stamps(self):
        at = timezone.now() - timedelta(minutes=5)
        self.assertEqual(self.check("0", availability=OUT, at=at), at)

    def test_a_price_kept_out_of_the_comparison_stamps(self):
        for n, price in enumerate(("120", "125", "130")):
            make_listing(self.product, make_retailer(f"Peer {n}", delivery_cost=Decimal("0")), price=price)
        Listing.objects.filter(pk=self.listing.pk).update(price=Decimal("9.99"))
        at = timezone.now() - timedelta(minutes=1)
        sanity.judge_product(self.product.pk, now=at)
        self.listing.refresh_from_db()
        self.assertEqual((self.listing.sanity, self.listing.changed_at), (Listing.Sanity.EXCLUDED, at))
        # The peers' verdicts did not change, so neither did their dates.
        self.assertFalse(Listing.objects.exclude(pk=self.listing.pk).filter(changed_at__isnull=False).exists())

    def test_new_delivery_rules_stamp_the_listings_they_change(self):
        self.shop.delivery_cost = Decimal("3.99")
        self.shop.save()
        pricing.apply_delivery_rules(self.shop)
        self.listing.refresh_from_db()
        self.assertIsNotNone(self.listing.changed_at)

    def test_a_product_save_moves_updated_at_only_for_what_the_page_shows(self):
        before = timezone.now() - timedelta(days=3)
        Product.objects.filter(pk=self.product.pk).update(updated_at=before)
        self.product.refresh_from_db()
        self.product.finder_checked_at = timezone.now()
        self.product.save(update_fields=["finder_checked_at"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.updated_at, before)
        self.product.image_url = "https://img.example/box.jpg"
        self.product.save(update_fields=["image_url"])
        self.product.refresh_from_db()
        self.assertGreater(self.product.updated_at, before)
