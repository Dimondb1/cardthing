"""Cold page speed: the narrowed list queries, the listing_offer index, and keeping the cached lists built."""

import multiprocessing
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import signals, worker
from catalogue.models import Listing, Product, Retailer
from catalogue.testing import cache_lists, make_game, make_listing, make_product, make_retailer, make_set
from web import listcache, views, warming

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
        self.assertEqual(warming.due(), [])
        for key in signals.LIST_CACHE_KEYS:
            self.assertIsNotNone(listcache.peek(key), key)
        self.assertIsNotNone(listcache.peek(f"{signals.COMING_SOON_CACHE_KEY}:{self.game.slug}"))
        self.assertIn("home deals", took)
        self.assertEqual(listcache.peek(signals.HOME_CACHE_KEY)[0][0][0].pk, self.deal.pk)

    def test_without_fresh_it_builds_only_what_is_due(self):
        warming.warm_pages()
        cache_lists(signals.DEALS_CACHE_KEY, "kept")
        cache.delete(signals.HOME_CACHE_KEY)
        took = warming.warm_pages()
        self.assertEqual(list(took), ["home deals"])
        self.assertEqual(listcache.peek(signals.DEALS_CACHE_KEY), "kept")
        self.assertIsNotNone(listcache.peek(signals.HOME_CACHE_KEY))

    def test_a_forced_clear_makes_every_list_due_and_the_warm_replaces_them_in_place(self):
        warming.warm_pages()
        signals.clear_list_caches_now(force=True)
        self.assertEqual(warming.missing(), [])   # still there for visitors
        self.assertEqual(len(warming.due()), len(warming.cached_keys(warming.active_games())))
        with mock.patch.object(cache, "delete", wraps=cache.delete) as deleted, \
                mock.patch.object(cache, "delete_many", wraps=cache.delete_many) as deleted_many:
            warming.warm_pages()
        self.assertEqual(warming.due(), [])
        lists = set(warming.cached_keys(warming.active_games()))
        self.assertFalse([c for c in deleted.call_args_list if c.args[0] in lists])
        self.assertFalse([c for c in deleted_many.call_args_list if lists & set(c.args[0])])

    def test_fresh_builds_everything_again(self):
        cache_lists(signals.DEALS_CACHE_KEY, "old")
        warming.warm_pages(fresh=True)
        self.assertIsInstance(listcache.peek(signals.DEALS_CACHE_KEY), dict)

    def test_a_price_change_shows_after_the_clear_and_the_warm(self):
        warming.warm_pages()
        Listing.objects.filter(product=self.deal, retailer=self.shop).update(price=Decimal("70.00"))
        signals.clear_list_caches_now(force=True)
        warming.warm_pages()
        (product, summary), = listcache.peek(signals.DEALS_CACHE_KEY)["savings"]
        self.assertEqual(summary.best.delivered_price, Decimal("70.10"))
        self.assertEqual(product.lowest_known, Decimal("70.10"))

    def test_lists_are_kept_past_their_maximum_staleness_so_the_cache_is_never_empty(self):
        with mock.patch.object(cache, "set", wraps=cache.set) as setter:
            warming.warm_pages(fresh=True)
        self.assertTrue(setter.call_args_list)
        for call in setter.call_args_list:
            if isinstance(call.args[1], listcache.Entry):
                self.assertEqual(call.args[2], listcache.KEEP_SECONDS)
        self.assertGreater(listcache.KEEP_SECONDS, settings.RIPRAPTOR_LIST_MAX_STALE_SECONDS)

    def test_the_command_builds_them_all_and_says_how_long_it_took(self):
        out = StringIO()
        call_command("warm_pages", stdout=out)
        self.assertEqual(warming.missing(), [])
        self.assertRegex(out.getvalue(), r"\d+ page lists ready in \d+\.\d s\.")
        cache_lists(signals.DEALS_CACHE_KEY, "kept")
        call_command("warm_pages", "--missing", stdout=StringIO())
        self.assertEqual(listcache.peek(signals.DEALS_CACHE_KEY), "kept")

    def test_the_command_with_due_builds_only_what_was_cleared(self):
        call_command("warm_pages", stdout=StringIO())
        out = StringIO()
        call_command("warm_pages", "--due", stdout=out)
        self.assertIn("0 page lists ready", out.getvalue())
        signals.clear_list_caches_now(force=True)
        out = StringIO()
        call_command("warm_pages", "--due", stdout=out)
        self.assertIn(f"{len(warming.cached_keys(warming.active_games()))} page lists ready", out.getvalue())

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

    def test_the_reader_builds_what_is_due_and_looks_again_after_a_forced_clear(self):
        reader = self.reader(warm=True)
        now = timezone.now()
        with mock.patch.object(warming, "warm_pages", wraps=warming.warm_pages) as warm:
            reader.keep_pages_warm(now)
            self.assertEqual(warm.call_args_list, [mock.call()])
            self.assertEqual(warming.missing(), [])
            # Nothing cleared and looked a moment ago: a turn costs one small cache read.
            reader.keep_pages_warm(now + timedelta(seconds=5))
            self.assertEqual(len(warm.call_args_list), 1)
            # A job's forced clear: the next turn builds them again, in place.
            built = listcache.entry(signals.HOME_CACHE_KEY).built_at
            signals.clear_list_caches_now(force=True)
            self.assertEqual(warming.missing(), [])
            reader.keep_pages_warm(now + timedelta(seconds=10))
            self.assertEqual(len(warm.call_args_list), 2)
            self.assertEqual(warming.due(), [])
            self.assertGreater(listcache.entry(signals.HOME_CACHE_KEY).built_at, built)
            # Every WARM_CHECK it looks again for ordinary clears and ageing lists.
            reader.keep_pages_warm(now + timedelta(seconds=10) + worker.WARM_CHECK)
            self.assertEqual(len(warm.call_args_list), 3)

    def test_the_reader_builds_a_missing_list_on_the_next_turn(self):
        reader = self.reader(warm=True)
        now = timezone.now()
        reader.keep_pages_warm(now)
        cache.delete(signals.DEALS_CACHE_KEY)
        reader.keep_pages_warm(now + timedelta(seconds=5))
        self.assertEqual(warming.missing(), [])

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


@override_settings(RIPRAPTOR_SERVE_STALE=True)
class StaleWhileRevalidateTests(Catalogue):
    """A cleared list is served as it is while the jobs build it again; only a missing or too old one is
    built by a visitor, and then by one visitor at a time (web.listcache)."""

    KEY = "web:test-list"

    def builder(self, value="new", pause=0.0):
        calls = []

        def build():
            calls.append(value)
            if pause:
                time.sleep(pause)
            return value
        return build, calls

    def test_a_clear_marks_the_lists_stale_and_deletes_none(self):
        warming.warm_pages()
        before = {key: listcache.entry(key).built_at for key in warming.cached_keys(warming.active_games())}
        signals.clear_list_caches_now(force=True)
        for key, built in before.items():
            self.assertEqual(listcache.entry(key).built_at, built, key)
            self.assertTrue(listcache.is_stale(key), key)

    def test_a_stale_list_is_served_without_a_build(self):
        listcache.store(self.KEY, lambda: "old")
        signals.clear_list_caches_now(force=True)
        build, calls = self.builder()
        self.assertEqual(listcache.get_or_build(self.KEY, build), "old")
        self.assertEqual(calls, [])

    def test_a_cleared_home_page_is_served_with_the_queries_of_a_warm_one(self):
        warming.warm_pages()
        url = reverse("web:home")
        self.client.get(url)
        with CaptureQueriesContext(connection) as warm:
            self.client.get(url)
        Listing.objects.filter(product=self.deal, retailer=self.shop).update(price=Decimal("70.00"))
        signals.clear_list_caches_now(force=True)
        with CaptureQueriesContext(connection) as stale:
            response = self.client.get(url)
        self.assertEqual(len(stale), len(warm))
        self.assertContains(response, "80.10")   # the copy as built, with its checked times, until the jobs run
        warming.warm_pages()
        self.assertContains(self.client.get(url), "70.10")

    def test_past_the_maximum_staleness_a_visitor_builds_it_first(self):
        with mock.patch("web.listcache.time.time", return_value=10_000.0):
            listcache.store(self.KEY, lambda: "old")
        build, calls = self.builder()
        later = 10_000.0 + settings.RIPRAPTOR_LIST_MAX_STALE_SECONDS
        with mock.patch("web.listcache.time.time", return_value=later - 1):
            self.assertEqual(listcache.get_or_build(self.KEY, build), "old")
        with mock.patch("web.listcache.time.time", return_value=later):
            self.assertEqual(listcache.get_or_build(self.KEY, build), "new")
        self.assertEqual(calls, ["new"])

    def test_a_list_past_the_stale_rule_drops_out_at_the_next_build(self):
        warming.warm_pages()
        self.assertIn(self.deal.pk, [product.pk for product, _ in listcache.peek(signals.DEALS_CACHE_KEY)["savings"]])
        Listing.objects.filter(product=self.deal).update(
            last_checked=timezone.now() - timedelta(hours=settings.RIPRAPTOR_STALE_AFTER_HOURS + 1))
        signals.clear_list_caches_now(force=True)
        warming.warm_pages()
        savings = listcache.peek(signals.DEALS_CACHE_KEY)["savings"]
        self.assertNotIn(self.deal.pk, [product.pk for product, _ in savings])

    def test_the_jobs_leave_a_list_another_process_is_building(self):
        listcache.store(self.KEY, lambda: "old")
        cache.add(listcache.lock_key(self.KEY), 1, 60)   # another process holds the build lock
        build, calls = self.builder()
        self.assertEqual(listcache.get_or_build(self.KEY, build, fresh=True), "old")
        self.assertEqual(calls, [])

    def test_a_cold_visitor_waits_for_the_build_another_process_started(self):
        cache.add(listcache.lock_key(self.KEY), 1, 60)
        build, calls = self.builder("mine")

        def other_process_finishes(seconds):
            listcache.store(self.KEY, lambda: "theirs")

        with mock.patch("web.listcache.time.sleep", side_effect=other_process_finishes):
            self.assertEqual(listcache.get_or_build(self.KEY, build), "theirs")
        self.assertEqual(calls, [])

    def test_a_cold_visitor_builds_its_own_once_the_wait_runs_out(self):
        cache.add(listcache.lock_key(self.KEY), 1, 60)
        build, calls = self.builder("mine")
        with mock.patch.object(listcache, "WAIT_SECONDS", 0.05), mock.patch.object(listcache, "WAIT_STEP", 0.01):
            self.assertEqual(listcache.get_or_build(self.KEY, build), "mine")
        self.assertEqual(calls, ["mine"])

    def test_a_cold_visitor_takes_over_when_the_other_build_failed(self):
        cache.add(listcache.lock_key(self.KEY), 1, 60)
        build, calls = self.builder("mine")
        with mock.patch("web.listcache.time.sleep",
                        side_effect=lambda seconds: cache.delete(listcache.lock_key(self.KEY))):
            self.assertEqual(listcache.get_or_build(self.KEY, build), "mine")
        self.assertEqual(calls, ["mine"])
        self.assertIsNone(cache.get(listcache.lock_key(self.KEY)))   # and lets go of the lock

    def test_concurrent_cold_visitors_run_one_build(self):
        build, calls = self.builder("built", pause=0.3)
        results = []
        threads = [threading.Thread(target=lambda: results.append(listcache.get_or_build(self.KEY, build)))
                   for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["built"] * 6)
        self.assertEqual(calls, ["built"])

    def test_a_failed_build_lets_go_of_the_lock(self):
        def fail():
            raise RuntimeError("database busy")

        with self.assertRaises(RuntimeError):
            listcache.get_or_build(self.KEY, fail)
        self.assertIsNone(cache.get(listcache.lock_key(self.KEY)))

    def test_without_serving_stale_a_cleared_list_is_built_by_the_next_request(self):
        listcache.store(self.KEY, lambda: "old")
        signals.clear_list_caches_now(force=True)
        build, calls = self.builder()
        with override_settings(RIPRAPTOR_SERVE_STALE=False):
            self.assertEqual(listcache.get_or_build(self.KEY, build), "new")
        self.assertEqual(calls, ["new"])


@override_settings(RIPRAPTOR_SERVE_STALE=True, RIPRAPTOR_CACHE_CLEAR_SECONDS=30)
class OwnerEditsTests(Catalogue):
    """The owner's own changes are never served from a copy built before them (web.middleware)."""

    def setUp(self):
        super().setUp()
        signals._last_clear = None

    def home_names(self):
        featured, _, spares = views.home_lists()
        return [product.name for product, _ in featured + spares]

    def owner_request(self, method="post", staff=True):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        request = getattr(RequestFactory(), method)("/admin/catalogue/product/1/change/")
        request.user = (mock.Mock(is_staff=True) if staff else AnonymousUser())
        return request

    def hide_deal(self, request):
        from web.middleware import OwnerEditsMiddleware

        def admin_save(request):
            self.deal.is_active = False
            self.deal.save()   # what Django Admin's change form does: an ordinary save
            return "saved"

        return OwnerEditsMiddleware(admin_save)(request)

    def test_a_product_the_owner_hides_leaves_the_home_page_at_once(self):
        warming.warm_pages()
        self.assertIn("Deal Booster Box", self.home_names())
        self.assertEqual(self.hide_deal(self.owner_request()), "saved")
        self.assertTrue(listcache.is_due(signals.HOME_CACHE_KEY))   # the reader builds it on its next turn
        self.assertNotIn("Deal Booster Box", self.home_names())     # and no visitor is served the old copy
        self.assertFalse(listcache.owner_editing.get())

    def test_the_same_save_by_a_job_is_served_stale_until_the_jobs_build_it(self):
        warming.warm_pages()
        self.hide_deal(self.owner_request(staff=False))
        self.assertIn("Deal Booster Box", self.home_names())
        self.hide_deal(self.owner_request(method="get"))
        self.assertIn("Deal Booster Box", self.home_names())

    def test_an_owner_fix_on_the_site_is_hard_too(self):
        from django.contrib.auth import get_user_model

        warming.warm_pages()
        self.assertIn("Deal Booster Box", self.home_names())
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        for listing in self.deal.listings.all():
            self.client.post(reverse("checks"), {"action": "hide", "listing": listing.pk})
        self.assertIn("hard_at", listcache.cleared())
        self.assertNotIn("Deal Booster Box", self.home_names())

    def test_the_reader_builds_after_a_hard_clear_on_its_next_turn(self):
        warming.warm_pages()
        reader = worker.Worker(warm=True)
        now = timezone.now()
        reader.keep_pages_warm(now)
        self.hide_deal(self.owner_request())
        reader.keep_pages_warm(now + timedelta(seconds=5))
        self.assertFalse(listcache.is_due(signals.HOME_CACHE_KEY))
        self.assertNotIn("Deal Booster Box", [p.name for p, _ in listcache.peek(signals.HOME_CACHE_KEY)[0]
                                              + listcache.peek(signals.HOME_CACHE_KEY)[2]])


@override_settings(RIPRAPTOR_SERVE_STALE=True)
class ClearMarksTests(Catalogue):
    """Two clears at once never undo each other (web.listcache.mark_stale)."""

    def test_an_ordinary_clear_never_drops_a_forced_one(self):
        listcache.mark_stale(force=True, now=100.0)
        listcache.mark_stale(now=99.0)   # a slower process that took its time before the forced clear
        self.assertEqual(listcache.cleared(), {"at": 100.0, "forced_at": 100.0})
        listcache.mark_stale(hard=True, now=120.0)
        listcache.mark_stale(now=130.0)
        self.assertEqual(listcache.cleared(), {"at": 130.0, "forced_at": 120.0, "hard_at": 120.0})

    def test_a_mark_ahead_of_the_clock_is_ignored_and_replaced(self):
        listcache.mark_stale(force=True, now=time.time() + 3600)   # made before the clock was set back
        self.assertEqual(listcache.cleared(), {})
        listcache.mark_stale()
        self.assertLess(listcache.cleared()["at"], time.time() + 1)

    def test_a_price_saved_between_the_home_and_deals_builds_leaves_the_deals_stale(self):
        real = views.home_drops_new

        def out_now_then_a_price_change(fresh=False):
            Listing.objects.filter(product=self.deal, retailer=self.shop).update(price=Decimal("60.00"))
            signals.clear_list_caches_now(force=True)
            return real(fresh=fresh)

        with mock.patch.object(views, "home_drops_new", out_now_then_a_price_change):
            warming.warm_pages(fresh=True)
        self.assertTrue(listcache.is_due(signals.DEALS_CACHE_KEY))   # built from offers read before the change
        warming.warm_pages()
        prices = [str(offer.price) for product, _ in listcache.peek(signals.DEALS_CACHE_KEY)["savings"]
                  for offer in product.offers]
        self.assertIn("60.00", prices)


def file_cache(folder, **options):
    from django.core.cache.backends.filebased import FileBasedCache

    return FileBasedCache(folder, {"OPTIONS": options})


def build_in_process(folder, key, log, barrier):
    """One process asking for a missing list at the same moment as the others (ProcessLockTests)."""
    cache = file_cache(folder)

    def build():
        with open(log, "a") as handle:
            handle.write(f"{os.getpid()}\n")
        time.sleep(0.3)
        return "built"

    barrier.wait()
    assert listcache.get_or_build(key, build, cache=cache) == "built"


def mark_in_process(folder, force, barrier):
    cache = file_cache(folder)
    barrier.wait()
    for _ in range(40):
        listcache.mark_stale(force=force, cache=cache)


class ProcessLockTests(TestCase):
    """The shared file cache across real processes, as the web workers and the reader use it in production.
    Django's FileBasedCache.add is not atomic between processes, so the build lock and the marks keep files of
    their own (web.listcache.FileState)."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="ripraptor-listcache-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.context = multiprocessing.get_context("fork")

    def run_all(self, target, args_list):
        """Run ``target`` in a forked process per ``args``. os.fork, not multiprocessing.Process, which refuses
        to start children from the parallel test runner's own (daemon) processes."""
        pids = []
        for args in args_list:
            pid = os.fork()
            if pid == 0:   # the child: never returns into the test runner
                code = 1
                try:
                    target(*args)
                    code = 0
                finally:
                    os._exit(code)
            pids.append(pid)
        codes = [os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) for pid in pids]
        self.assertEqual(codes, [0] * len(pids))

    @override_settings(RIPRAPTOR_SERVE_STALE=True)
    def test_cold_visitors_in_three_processes_run_one_build(self):
        log = os.path.join(self.folder, "builds.log")
        for trial in range(5):
            barrier = self.context.Barrier(3)
            self.run_all(build_in_process, [(self.folder, f"web:test-list:{trial}", log, barrier)] * 3)
        with open(log) as handle:
            self.assertEqual(len(handle.read().split()), 5)

    def test_a_forced_clear_survives_ordinary_ones_made_at_the_same_time(self):
        barrier = self.context.Barrier(4)
        self.run_all(mark_in_process, [(self.folder, True, barrier)] + [(self.folder, False, barrier)] * 3)
        marks = listcache.cleared(cache=file_cache(self.folder))
        self.assertIn("forced_at", marks)
        self.assertGreaterEqual(marks["at"], marks["forced_at"])

    def test_the_marks_are_never_culled_with_the_cache(self):
        cache = file_cache(self.folder, MAX_ENTRIES=30)
        listcache.store("web:test-list", lambda: "rows", cache=cache)
        listcache.mark_stale(force=True, cache=cache)
        for number in range(100):
            cache.set(f"other:{number}", number)
        cache.clear()
        self.assertIn("forced_at", listcache.cleared(cache=cache))

    def test_a_held_file_lock_is_released_when_its_process_ends(self):
        cache = file_cache(self.folder)
        held = listcache.state(cache).take("web:test-list")
        self.assertIsNotNone(held)
        self.assertIsNone(listcache.state(cache).take("web:test-list"))
        listcache.state(cache).release(held)
        again = listcache.state(cache).take("web:test-list")
        self.assertIsNotNone(again)
        listcache.state(cache).release(again)


class InstallTests(TestCase):
    def test_install_builds_the_lists_after_emptying_the_cache(self):
        text = (DEPLOY / "install.sh").read_text()
        cleared = text.index("cache.clear()")
        warmed = re.search(r"\.venv/bin/python manage\.py warm_pages\"( \|\| true)?\n", text)
        self.assertIsNotNone(warmed, "install.sh does not build the page lists")
        self.assertGreater(warmed.start(), cleared)
        self.assertEqual(warmed.group(1), " || true")   # a failure never stops the install
