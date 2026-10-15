"""What search engines are told: the sitemap's pages and dates, and the answers at a removed product's address."""

import re
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from catalogue import pricing, sanity
from catalogue.housekeeping import prune_removed
from catalogue.models import (
    DailyLowestPrice, Listing, PreorderOpen, Product, ProductAlias, RemovedProduct, StockAlert,
)
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

    def test_a_listing_back_from_out_of_date_stamps_though_its_price_is_the_same(self):
        from catalogue.importers import Offer, apply_offers
        from catalogue.models import Retailer

        cache.clear()
        shop = make_retailer("Reader", source_type=Retailer.Source.SHOPIFY, source_url="https://reader.example",
                             delivery_cost=Decimal("0"))
        product = make_product(self.set, name="Surging Sparks Elite Trainer Box", slug="ss-etb")
        listing = make_listing(product, shop, price="50.00", url="https://reader.example/products/ss-etb", hours_ago=100)
        Product.objects.filter(pk=product.pk).update(updated_at=timezone.now() - timedelta(days=60))
        url = product.get_absolute_url()
        # Out of date: no buy button, and not in the sitemap.
        self.assertNotContains(self.client.get(url), "Buy now")
        self.assertNotIn(url, sitemap_entries(self.client))
        at = timezone.now() - timedelta(minutes=5)
        apply_offers(shop, [Offer(title=product.name, url=listing.url, price=Decimal("50.00"))], checked_at=at)
        listing.refresh_from_db()
        self.assertEqual(listing.changed_at, at)
        cache.clear()
        self.assertContains(self.client.get(url), "Buy now")
        self.assertEqual(sitemap_entries(self.client)[url], day(at))

        # The same through a single check, and only for a listing a visitor can buy.
        for availability, stamped in ((IN_STOCK, True), (OUT, False)):
            Listing.objects.filter(pk=self.listing.pk).update(
                availability=availability, last_checked=timezone.now() - timedelta(hours=100), changed_at=None)
            self.assertEqual(self.check("100.00", availability=availability, at=at), at if stamped else None)

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


class RemovedProductTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.set = make_set(cls.game)
        cls.shop = make_retailer("Shop A")
        cls.other_shop = make_retailer("Shop B")

    def setUp(self):
        cache.clear()

    def orphan(self, name="Nobody Sells This Booster Box", slug="orphan", **kwargs):
        product = make_product(self.set, name=name, slug=slug, **kwargs)
        Product.objects.filter(pk=product.pk).update(image="")
        return product

    def test_a_product_tidy_removes_answers_410_with_its_set_and_game(self):
        product = self.orphan()
        ProductAlias.objects.create(slug="orphan-old-address", product=product)
        call_command("tidy_catalogue", stdout=StringIO())
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())
        for slug in ("orphan", "orphan-old-address"):
            response = self.client.get(f"/products/{slug}/")
            self.assertEqual(response.status_code, 410, slug)
            self.assertContains(response, "Nobody Sells This Booster Box is no longer listed", status_code=410)
            self.assertContains(response, f'href="{self.set.get_absolute_url()}"', status_code=410)
            self.assertContains(response, "Prismatic Evolutions products", status_code=410)
            self.assertContains(response, f'href="{self.game.get_absolute_url()}"', status_code=410)
            self.assertContains(response, "All Pokémon products", status_code=410)
            self.assertContains(response, '<meta name="robots" content="noindex, follow">', status_code=410)

    def test_an_unknown_address_is_still_404(self):
        self.assertEqual(self.client.get("/products/never-existed/").status_code, 404)

    def test_a_product_address_still_answers_only_get(self):
        RemovedProduct.objects.create(slug="gone", name="Gone Booster Box", game=self.game)
        self.assertEqual(self.client.post("/products/gone/").status_code, 405)

    def test_the_410_page_keeps_the_house_style(self):
        from web.tests import CopyStyleTests

        RemovedProduct.objects.create(slug="gone", name="Gone Booster Box", game=self.game, product_set=self.set)
        html = self.client.get("/products/gone/").content.decode()
        visible = re.sub(r"<[^>]*>", " ", html)
        self.assertNotIn("!", visible)
        for phrase in CopyStyleTests.BANNED:
            self.assertNotIn(phrase, html.lower())

    def test_a_removed_product_of_a_hidden_game_or_without_a_set_still_answers_410(self):
        RemovedProduct.objects.create(slug="setless", name="Setless Box", game=self.game)
        response = self.client.get("/products/setless/")
        self.assertContains(response, f'href="{self.game.get_absolute_url()}"', status_code=410)
        self.assertNotContains(response, "Prismatic Evolutions products", status_code=410)
        type(self.game).objects.filter(pk=self.game.pk).update(is_active=False)
        response = self.client.get("/products/setless/")
        self.assertEqual(response.status_code, 410)
        self.assertNotContains(response, "All Pokémon products", status_code=410)

    def test_a_live_product_or_an_old_address_wins_over_a_removal(self):
        RemovedProduct.objects.create(slug="back-again", name="Back Again Box", game=self.game)
        RemovedProduct.objects.create(slug="merged-away", name="Merged Box", game=self.game)
        back = make_product(self.set, name="Back Again Box", slug="back-again")
        ProductAlias.objects.create(slug="merged-away", product=back)
        self.assertEqual(self.client.get("/products/back-again/").status_code, 200)
        response = self.client.get("/products/merged-away/")
        self.assertEqual((response.status_code, response["Location"]), (301, back.get_absolute_url()))
        # Housekeeping forgets both: the address answers for itself again.
        self.assertEqual(prune_removed(), 2)

    def test_removals_are_forgotten_after_a_year(self):
        now = timezone.now()
        RemovedProduct.objects.create(slug="old", name="Old Box", game=self.game, removed_at=now - timedelta(days=366))
        RemovedProduct.objects.create(slug="recent", name="Recent Box", game=self.game, removed_at=now - timedelta(days=300))
        out = StringIO()
        call_command("tidy_all", stdout=out)
        self.assertIn("1 removed product addresses forgotten.", out.getvalue())
        self.assertEqual(list(RemovedProduct.objects.values_list("slug", flat=True)), ["recent"])
        self.assertEqual(self.client.get("/products/old/").status_code, 404)

    def test_a_dry_run_records_nothing(self):
        self.orphan()
        call_command("tidy_catalogue", "--dry-run", stdout=StringIO())
        self.assertFalse(RemovedProduct.objects.exists())

    def test_a_rename_onto_another_product_merges_and_leaves_a_301(self):
        spaced = self.orphan(name="Alpha  Booster Box", slug="alpha-1", product_type="booster_box")
        make_listing(spaced, self.shop, url="https://a.example/alpha")
        today = timezone.localdate()
        DailyLowestPrice.objects.create(product=spaced, date=today - timedelta(days=3), price="90.00")
        StockAlert.objects.create(product=spaced, email="someone@example.com")
        PreorderOpen.objects.create(product=spaced, retailer=self.shop, price="95.00")
        target = self.orphan(name="Alpha Booster Box", slug="alpha-2", product_type="booster_box")
        make_listing(target, self.other_shop, url="https://b.example/alpha")
        out = StringIO()
        call_command("tidy_catalogue", stdout=out)
        self.assertIn("merged: Alpha  Booster Box -> Alpha Booster Box", out.getvalue())
        response = self.client.get("/products/alpha-1/")
        self.assertEqual((response.status_code, response["Location"]), (301, target.get_absolute_url()))
        self.assertEqual(target.listings.count(), 2)
        self.assertTrue(DailyLowestPrice.objects.filter(product=target, date=today - timedelta(days=3)).exists())
        self.assertTrue(StockAlert.objects.filter(product=target, email="someone@example.com").exists())
        self.assertTrue(PreorderOpen.objects.filter(product=target).exists())
        self.assertFalse(RemovedProduct.objects.exists())
        # The kept page gained a shop, so the sitemap says it changed.
        self.assertIsNotNone(target.listings.get(retailer=self.shop).changed_at)

    def test_a_rename_never_merges_another_type_or_a_hidden_product(self):
        collector = self.orphan(name="Beta  Booster Box", slug="beta-1", product_type="collector_booster_box")
        make_listing(collector, self.shop, url="https://a.example/beta")
        plain = self.orphan(name="Beta Booster Box", slug="beta-2", product_type="booster_box")
        make_listing(plain, self.other_shop, url="https://b.example/beta")
        shown = self.orphan(name="Gamma  Booster Box", slug="gamma-1", product_type="booster_box")
        make_listing(shown, self.shop, url="https://a.example/gamma")
        hidden = self.orphan(name="Gamma Booster Box", slug="gamma-2", product_type="booster_box", is_active=False)
        make_listing(hidden, self.other_shop, url="https://b.example/gamma")
        call_command("tidy_catalogue", stdout=StringIO())
        for product in (collector, plain, shown, hidden):
            self.assertTrue(Product.objects.filter(pk=product.pk).exists(), product.slug)
        # Renamed in place: the address stays.
        collector.refresh_from_db()
        self.assertEqual((collector.name, collector.slug), ("Beta Booster Box", "beta-1"))
        self.assertEqual(self.client.get("/products/beta-1/").status_code, 200)
        self.assertEqual(self.client.get("/products/gamma-1/").status_code, 200)

    def test_changing_an_address_in_admin_keeps_the_old_one(self):
        from django.contrib.admin.sites import site

        product = make_product(self.set, name="Delta Booster Box", slug="delta")
        product.slug = "delta-booster-box"
        site._registry[Product].save_model(None, product, SimpleNamespace(initial={"slug": "delta"}, changed_data=["slug"]),
                                           True)
        response = self.client.get("/products/delta/")
        self.assertEqual((response.status_code, response["Location"]), (301, "/products/delta-booster-box/"))
        # And back again: the address in use is never also an old address.
        product.slug = "delta"
        site._registry[Product].save_model(
            None, product, SimpleNamespace(initial={"slug": "delta-booster-box"}, changed_data=["slug"]), True)
        self.assertEqual(self.client.get("/products/delta/").status_code, 200)
        self.assertEqual(list(ProductAlias.objects.values_list("slug", flat=True)), ["delta-booster-box"])
