"""Cold page speed: the narrowed list queries, the listing_offer index, and keeping the cached lists built."""

import re
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import signals, worker
from catalogue.models import Listing, Product, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import views, warming

DEPLOY = Path(settings.BASE_DIR) / "deploy"


class Catalogue(TestCase):
    """Two shops, and products in every state a list tells apart."""

    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.set = make_set(cls.game)
        cls.shop = make_retailer("Harbour Games", affiliate_url_template="https://aff.example/?u={url}")
        cls.other = make_retailer("Castle Cards")
        cls.closed = make_retailer("Closed Shop", is_active=False)

        def product(name):
            return make_product(cls.set, name=name)

        cls.deal = product("Deal Booster Box")                        # in stock at two shops, one cheaper
        make_listing(cls.deal, cls.shop, price="80.00", delivery="0.10")
        make_listing(cls.deal, cls.other, price="99.90", delivery="0.20")
        cls.single = product("Single Shop Bundle")                    # in stock at one shop
        make_listing(cls.single, cls.other, price="30.00", delivery="3.99")
        cls.preorder = product("Preorder Tin")                        # pre-order only
        make_listing(cls.preorder, cls.shop, price="20.00", availability=Listing.Availability.PREORDER)
        cls.unknown = product("Unknown Delivery Deck")                # in stock, delivery not known
        make_listing(cls.unknown, cls.shop, price="15.00", delivery_known=False)
        cls.stale = product("Stale Collection Box")                   # in stock, but checked too long ago
        make_listing(cls.stale, cls.shop, price="40.00", hours_ago=settings.RIPRAPTOR_STALE_AFTER_HOURS + 1)
        cls.excluded = product("Excluded Gift Set")                   # its only price is kept out
        make_listing(cls.excluded, cls.shop, price="5.00", sanity=Listing.Sanity.EXCLUDED)
        cls.doubtful = product("Doubtful Booster Pack")               # shown, but doubtful
        make_listing(cls.doubtful, cls.shop, price="4.00", sanity=Listing.Sanity.DOUBTFUL)
        cls.sold_out = product("Sold Out Elite Trainer Box")
        make_listing(cls.sold_out, cls.shop, price="45.00", availability=Listing.Availability.OUT_OF_STOCK)
        cls.hidden = product("Hidden Listing Box")                    # its listing is switched off
        make_listing(cls.hidden, cls.shop, price="45.00", is_active=False)
        cls.shut = product("Closed Shop Box")                         # only at a shop that is switched off
        make_listing(cls.shut, cls.closed, price="45.00")
        cls.bare = product("No Listings Box")

    def setUp(self):
        cache.clear()


class NarrowedQuerysetTests(Catalogue):
    def test_in_stock_is_exactly_in_stock_count_of_one_or_more(self):
        having = set(Product.objects.for_lists().filter(in_stock_count__gte=1).values_list("pk", flat=True))
        where = set(Product.objects.for_lists().in_stock().values_list("pk", flat=True))
        self.assertEqual(where, having)
        self.assertEqual(where, {self.deal.pk, self.single.pk, self.unknown.pk, self.doubtful.pk})

    def test_priced_is_exactly_a_lowest_price(self):
        having = set(Product.objects.for_lists().filter(lowest_price__isnull=False).values_list("pk", flat=True))
        where = set(Product.objects.for_lists().priced().values_list("pk", flat=True))
        self.assertEqual(where, having)
        self.assertEqual(where, {self.deal.pk, self.single.pk, self.unknown.pk, self.doubtful.pk, self.preorder.pk})

    def test_prices_worked_out_from_price_and_delivery_match_the_stored_delivered_price(self):
        for product in Product.objects.for_lists():
            listings = [row for row in Listing.objects.filter(product=product)
                        if row.is_buyable and row.sanity != Listing.Sanity.EXCLUDED]
            known = [row.delivered_price for row in listings if row.delivery_known]
            self.assertEqual(product.lowest_known, min(known) if known else None, product.name)
        deal = Product.objects.for_lists().get(pk=self.deal.pk)
        self.assertEqual(deal.lowest_known, Decimal("80.10"))
        self.assertEqual(deal.last_price, Decimal("80.10"))
        self.assertEqual(Product.objects.for_lists().get(pk=self.unknown.pk).lowest_price, Decimal("15.00"))

    def test_the_list_query_reads_the_listing_offer_index_not_the_listing_rows(self):
        products = Product.objects.for_lists().in_stock()
        with connection.cursor() as cursor:
            sql, params = products.query.sql_with_params()
            cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
            plan = " / ".join(row[-1] for row in cursor.fetchall())
        self.assertIn("COVERING INDEX listing_offer", plan)
        self.assertNotRegex(plan, r"SEARCH catalogue_listing USING INDEX catalogue_listing_product_id")

    def test_the_index_is_declared_on_the_model(self):
        names = {index.name for index in Listing._meta.indexes}
        self.assertIn("listing_offer", names)


class ListBuildTests(Catalogue):
    def test_featured_and_savings_rows_are_full_list_products_with_the_offers_they_were_picked_from(self):
        featured, _trending, _spares = views.home_lists()
        savings = views.deals_lists()["savings"]
        self.assertEqual([product.pk for product, _ in savings], [self.deal.pk])
        for product, summary in [*featured, *savings]:
            with self.assertNumQueries(0):
                self.assertIsNotNone(product.lowest_price)
                self.assertEqual(product.game.slug, "pokemon")
                self.assertEqual(product.product_set.name, self.set.name)
                self.assertIs(summary.best, product.offers[0])
                for offer in product.offers:
                    self.assertIs(offer.product, product)
                    self.assertTrue(offer.retailer.name)

    def test_a_product_gone_between_the_pick_and_the_load_is_left_out(self):
        candidates = list(views.deal_candidates())
        Product.objects.filter(pk=self.deal.pk).update(is_active=False)
        rows, = views.with_list_fields([(product, None) for product in candidates])
        self.assertNotIn(self.deal.pk, [product.pk for product, _ in rows])

    def test_home_and_deals_built_together_read_the_offers_once(self):
        candidates = views.shared_candidates()
        with CaptureQueriesContext(connection) as both:
            views.home_lists(candidates=candidates)
            views.deals_lists(candidates=candidates)
        reads = [q for q in both.captured_queries if 'FROM "catalogue_listing"' in q["sql"]
                 and '"catalogue_listing"."product_id" IN' in q["sql"]]
        self.assertEqual(len(reads), 2)   # the candidates' offers once, and the trending products' offers


class FlatQueryCountTests(TestCase):
    """The pages' query counts on an empty cache do not grow with the number of products."""

    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.set = make_set(cls.game)
        cls.shops = [make_retailer(f"Shop {n}") for n in range(3)]

    def add(self, count):
        start = Product.objects.count()
        for n in range(start, start + count):
            product = make_product(self.set, name=f"Booster Box {n}")
            for i, shop in enumerate(self.shops):
                make_listing(product, shop, price=f"{50 + n + i}.00",
                             availability=Listing.Availability.PREORDER if n % 4 == 0 else Listing.Availability.IN_STOCK)
        return product

    def cold(self, url):
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return len(queries)

    def test_cold_pages_keep_their_query_count_however_many_products(self):
        product = self.add(3)
        urls = [reverse("web:home"), reverse("web:deals"), reverse("web:drops"), reverse("web:drops") + "?show=out",
                self.game.get_absolute_url(), self.set.get_absolute_url(), product.get_absolute_url(),
                reverse("web:search") + "?q=booster"]
        few = {url: self.cold(url) for url in urls}
        self.add(20)
        many = {url: self.cold(url) for url in urls}
        self.assertEqual(many, few)
        # A handful of queries each, whatever the catalogue holds.
        for url, count in many.items():
            self.assertLessEqual(count, 20, url)

    def test_after_warming_a_visitor_builds_nothing(self):
        self.add(5)
        for name in ("home", "deals"):
            url = reverse(f"web:{name}")
            cache.clear()
            warming.warm_pages()
            with CaptureQueriesContext(connection) as warmed:
                self.client.get(url)
            with CaptureQueriesContext(connection) as again:
                self.client.get(url)
            self.assertEqual(len(warmed), len(again), name)


class WarmPagesTests(Catalogue):
    def test_it_fills_every_cached_list_the_pages_read(self):
        self.assertEqual(len(warming.missing()), len(warming.cached_keys(warming.active_games())))
        took = warming.warm_pages()
        self.assertEqual(warming.missing(), [])
        for key in signals.LIST_CACHE_KEYS:
            self.assertIsNotNone(cache.get(key), key)
        self.assertIsNotNone(cache.get(f"{signals.COMING_SOON_CACHE_KEY}:{self.game.slug}"))
        self.assertIn("home deals", took)
        self.assertEqual(cache.get(signals.HOME_CACHE_KEY)[0][0][0].pk, self.deal.pk)

    def test_without_fresh_it_builds_only_what_is_missing(self):
        warming.warm_pages()
        cache.set(signals.DEALS_CACHE_KEY, "kept", 300)
        cache.delete(signals.HOME_CACHE_KEY)
        warming.warm_pages()
        self.assertEqual(cache.get(signals.DEALS_CACHE_KEY), "kept")
        self.assertIsNotNone(cache.get(signals.HOME_CACHE_KEY))

    def test_fresh_builds_everything_again(self):
        cache.set(signals.DEALS_CACHE_KEY, "old", 300)
        warming.warm_pages(fresh=True)
        self.assertIsInstance(cache.get(signals.DEALS_CACHE_KEY), dict)

    def test_a_price_change_shows_after_the_clear_and_the_warm(self):
        warming.warm_pages()
        Listing.objects.filter(product=self.deal, retailer=self.shop).update(price=Decimal("70.00"))
        signals.clear_list_caches_now(force=True)
        warming.warm_pages()
        (product, summary), = cache.get(signals.DEALS_CACHE_KEY)["savings"]
        self.assertEqual(summary.best.delivered_price, Decimal("70.10"))
        self.assertEqual(product.lowest_known, Decimal("70.10"))

    def test_lists_still_expire_after_the_usual_time(self):
        with mock.patch.object(cache, "set", wraps=cache.set) as setter:
            warming.warm_pages(fresh=True)
        self.assertTrue(setter.call_args_list)
        for call in setter.call_args_list:
            self.assertEqual(call.args[2], settings.RIPRAPTOR_HOME_CACHE_SECONDS)

    def test_the_command_builds_them_all_and_says_how_long_it_took(self):
        out = StringIO()
        call_command("warm_pages", stdout=out)
        self.assertEqual(warming.missing(), [])
        self.assertRegex(out.getvalue(), r"\d+ page lists ready in \d+\.\d s\.")
        cache.set(signals.DEALS_CACHE_KEY, "kept", 300)
        call_command("warm_pages", "--missing", stdout=StringIO())
        self.assertEqual(cache.get(signals.DEALS_CACHE_KEY), "kept")

    def test_a_failure_at_the_end_of_a_job_is_logged_not_raised(self):
        with mock.patch.object(warming, "warm_pages", side_effect=RuntimeError("boom")), \
                self.assertLogs("catalogue", "ERROR"):
            self.assertIsNone(warming.warm_quietly())

    def test_tidy_all_ends_by_building_them(self):
        out = StringIO()
        call_command("tidy_all", stdout=out)
        self.assertIn("== page lists", out.getvalue())
        self.assertEqual(warming.missing(), [])

    def test_tidy_all_dry_run_builds_nothing(self):
        out = StringIO()
        call_command("tidy_all", "--dry-run", stdout=out)
        self.assertNotIn("== page lists", out.getvalue())

    def test_the_hourly_import_ends_by_building_them(self):
        Retailer.objects.filter(pk=self.shop.pk).update(
            source_type=Retailer.Source.SHOPIFY, next_read_at=timezone.now() + timedelta(days=1))
        with mock.patch("web.warming.warm_quietly") as warm:
            call_command("import_prices", "--due", stdout=StringIO())
        warm.assert_called_once_with()

    def test_the_stock_watch_builds_them_when_a_listing_changed(self):
        listing = Listing.objects.get(product=self.sold_out)
        Listing.objects.filter(pk=listing.pk).update(last_checked=timezone.now() - timedelta(hours=2))
        Retailer.objects.filter(pk=self.shop.pk).update(source_type=Retailer.Source.SHOPIFY)
        with mock.patch("catalogue.management.commands.watch_stock.check_listing",
                        return_value=(Decimal("25.00"), Decimal("3.99"), Listing.Availability.IN_STOCK)), \
                mock.patch("catalogue.management.commands.watch_stock.record_probe", return_value="changed"), \
                mock.patch("web.warming.warm_quietly") as warm:
            call_command("watch_stock", "--pause", "0", stdout=StringIO())
        warm.assert_called_once_with()


class WorkerWarmingTests(Catalogue):
    def reader(self, **kwargs):
        return worker.Worker(clock=timezone.now, sleep=lambda seconds: None, **kwargs)

    def test_the_reader_builds_the_lists_and_again_before_they_expire(self):
        reader = self.reader(warm=True)
        now = timezone.now()
        with mock.patch.object(warming, "warm_pages", wraps=warming.warm_pages) as warm:
            reader.keep_pages_warm(now)
            self.assertEqual(warm.call_args_list, [mock.call(fresh=True)])
            self.assertEqual(warming.missing(), [])
            # Nothing cleared and not yet due: a turn costs no build.
            reader.keep_pages_warm(now + timedelta(seconds=30))
            self.assertEqual(len(warm.call_args_list), 1)
            # A job's price change cleared them: the next turn builds what is missing.
            signals.clear_list_caches_now(force=True)
            reader.keep_pages_warm(now + timedelta(seconds=35))
            self.assertEqual(warm.call_args_list[-1], mock.call())
            self.assertEqual(warming.missing(), [])
            # A minute before they would expire, all of them again.
            due = now + timedelta(seconds=settings.RIPRAPTOR_HOME_CACHE_SECONDS) - worker.WARM_AHEAD
            reader.keep_pages_warm(due)
            self.assertEqual(warm.call_args_list[-1], mock.call(fresh=True))

    def test_tick_keeps_them_warm_and_a_failure_never_stops_the_reader(self):
        reader = self.reader(warm=True)
        with mock.patch.object(warming, "warm_pages", side_effect=RuntimeError("busy")), \
                self.assertLogs("catalogue.worker", "ERROR"):
            reader.keep_pages_warm(timezone.now())

    def test_a_reader_without_warm_leaves_them_to_visitors(self):
        with mock.patch.object(warming, "warm_pages") as warm:
            self.reader().keep_pages_warm(timezone.now())
        warm.assert_not_called()

    def test_run_worker_turns_it_on(self):
        source = (Path(settings.BASE_DIR) / "catalogue/management/commands/run_worker.py").read_text()
        self.assertIn("worker.Worker(threads=max_threads, warm=True)", source)


class InstallTests(TestCase):
    def test_install_builds_the_lists_after_emptying_the_cache(self):
        text = (DEPLOY / "install.sh").read_text()
        cleared = text.index("cache.clear()")
        warmed = re.search(r"\.venv/bin/python manage\.py warm_pages\"( \|\| true)?\n", text)
        self.assertIsNotNone(warmed, "install.sh does not build the page lists")
        self.assertGreater(warmed.start(), cleared)
        self.assertEqual(warmed.group(1), " || true")   # a failure never stops the install
