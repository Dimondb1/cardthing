"""The background reader: planning, politeness, back-off, deadlines, the cron fallback and SQLite beside the site."""

import fcntl
import json
import os
import runpy
import socket
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import heat, insights, mail, notify, probe, worker
from .importers import STOPPED, ImportError_
from .models import DailyPageView, ImportRun, Listing, Product, Retailer, StockAlert, WorkerState
from .testing import make_game, make_listing, make_product, make_retailer, make_set
from .tests_db import file_database, open_in_thread


class FakeClock:
    """A clock the test moves by hand; sleeping moves it on instead of waiting."""

    def __init__(self, start=None):
        self.now = start or timezone.now()

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class HeldExecutor:
    """Takes jobs and never runs them, so they stay in flight."""

    threaded = False

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append(args[0].task)


def product_json(handle, price="99.99", available=True):
    return {
        "handle": handle, "title": handle.replace("-", " ").title(), "product_type": "Booster Box", "vendor": "",
        "tags": [], "images": [],
        "variants": [{"id": 1, "title": "Default Title", "price": price, "available": available, "barcode": ""}],
    }


def shop_answers(shops, js=None, seen=None, during=None):
    """A fetch that answers like the Shopify shops in ``shops`` ({base: [product json]}) and the probes in ``js``."""

    def fetch(url, *args, **kwargs):
        if seen is not None:
            seen.append(url)
        if during is not None:
            during(url)
        if js and url in js:
            return js[url]
        for base, products in shops.items():
            if not url.startswith(base):
                continue
            if url == f"{base}/meta.json":
                return b'{"currency": "GBP"}'
            if url.startswith(f"{base}/collections.json"):
                return b'{"collections": []}'
            if "/collections/" in url:
                return b'{"products": []}'
            if url.startswith(f"{base}/products.json"):
                page = int(url.rsplit("page=", 1)[1])
                return json.dumps({"products": products if page == 1 else []}).encode()
        raise ImportError_(f"Could not fetch {url}: HTTP Error 404: Not Found")

    return fetch


def js_answer(price_pence, available=True):
    return json.dumps({"variants": [{"price": price_pence, "available": available}]}).encode()


class WorkerTestCase(TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.set = make_set(make_game())
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.import_lock = str(self.folder / "import.lock")
        moved = mock.patch.object(worker, "IMPORT_LOCK", self.import_lock)
        moved.start()
        self.addCleanup(moved.stop)
        cache_dir = override_settings(RIPRAPTOR_CACHE_DIR=str(self.folder))
        cache_dir.enable()
        self.addCleanup(cache_dir.disable)

    def make_worker(self, fetch=None, **kwargs):
        kwargs.setdefault("page_pause", 0)
        return worker.Worker(clock=self.clock, fetch=fetch, sleep=self.clock.sleep, **kwargs)

    def shop(self, name, due=False, **kwargs):
        slug = name.lower().replace(" ", "-")
        kwargs.setdefault("source_type", Retailer.Source.SHOPIFY)
        kwargs.setdefault("source_url", f"https://{slug}.example/")
        kwargs.setdefault("read_every_minutes", 45)
        if not due:
            kwargs.setdefault("next_read_at", self.clock() + timedelta(hours=1))
        # The pre-order pulse has just looked, so its request does not mix with the reads and probes a test
        # counts; the pulse tests set this themselves.
        kwargs.setdefault("collections_polled_at", self.clock())
        kwargs.setdefault("collections_ok", True)
        return make_retailer(name, **kwargs)

    def product(self, slug, views=0, old=True, **kwargs):
        """A product: HOT with 3 views today, WARM when one shop sells it, COLD when two do."""
        product = make_product(self.set, name=slug.replace("-", " ").title(), slug=slug, product_type="booster_box", **kwargs)
        if old:
            Product.objects.filter(pk=product.pk).update(created_at=self.clock() - timedelta(days=60))
        if views:
            DailyPageView.objects.create(date=timezone.localdate(self.clock()), kind="product", key=slug, hits=views)
        return product

    def listing(self, product, shop, minutes_ago, **kwargs):
        kwargs.setdefault("url", f"{shop.source_url}products/{product.slug}")
        listing = make_listing(product, shop, **kwargs)
        Listing.objects.filter(pk=listing.pk).update(last_checked=self.clock() - timedelta(minutes=minutes_ago))
        listing.refresh_from_db()
        return listing


class HeatTests(WorkerTestCase):
    def test_points_add_up_in_four_queries(self):
        shop, other = self.shop("Shop A"), self.shop("Shop B")
        viewed = self.product("viewed-box", views=9)   # 4 x 9 is capped at 20, plus 5 for one shop
        compared = self.product("compared-box")        # two shops, old, nothing else: no points
        alerted = self.product("alerted-box")
        fresh = self.product("fresh-box", old=False)
        for product in (viewed, compared, alerted, fresh):
            self.listing(product, shop, 30)
        self.listing(compared, other, 30)
        self.listing(alerted, other, 30, availability="preorder")
        StockAlert.objects.create(product=alerted, email="a@example.com", confirmed_at=self.clock())
        DailyPageView.objects.create(date=timezone.localdate(self.clock()), kind="watched", key="compared-box", hits=1)
        with self.assertNumQueries(4):
            scores = heat.heat_scores(self.clock())
        self.assertEqual(scores[viewed.pk], 25)
        self.assertEqual(scores[compared.pk], 6)          # watchlisted only
        self.assertEqual(scores[alerted.pk], 8 + 6)       # alert and a pre-order, two shops
        self.assertEqual(scores[fresh.pk], 5 + 5)         # new and one shop
        self.assertEqual(heat.tier_counts(scores), (3, 1))


class PlanTests(WorkerTestCase):
    def test_a_due_read_comes_first_then_hot_then_warm_one_job_per_shop(self):
        due = self.shop("Due Shop", due=True)
        hot_shop, warm_shop = self.shop("Hot Shop"), self.shop("Warm Shop")
        hot = self.product("hot-box", views=3)
        warm = self.product("warm-box")
        self.listing(hot, due, 30)                 # its shop is about to be read: not probed
        hot_listing = self.listing(hot, hot_shop, 30)
        self.listing(self.product("hot-too", views=3), hot_shop, 5)   # checked 5 minutes ago: skipped
        warm_listing = self.listing(warm, warm_shop, 120)
        tasks = self.make_worker().plan(self.clock())
        self.assertEqual([(t.kind, t.retailer_id, t.tier) for t in tasks], [
            (worker.SHOP_READ, due.pk, ""), (worker.PROBE, hot_shop.pk, "hot"), (worker.PROBE, warm_shop.pk, "warm"),
        ])
        self.assertEqual(tasks[1].listings, [hot_listing.pk])
        self.assertEqual(tasks[2].listings, [warm_listing.pk])
        self.assertEqual(len({t.retailer_id for t in tasks}), len(tasks))

    def test_a_hot_listing_waits_ten_minutes_and_a_warm_one_an_hour(self):
        shop = self.shop("Shop")
        self.listing(self.product("hot-box", views=3), shop, 9)
        self.listing(self.product("warm-box"), shop, 50)
        self.listing(self.product("cold-box"), shop, 600)
        Listing.objects.create(product=Product.objects.get(slug="cold-box"), retailer=self.shop("Other"),
                               url="https://other.example/products/cold-box", price=Decimal("10"),
                               delivery_cost=Decimal("0"), last_checked=self.clock() - timedelta(minutes=600))
        self.assertEqual(self.make_worker().plan(self.clock()), [])
        self.clock.advance(minutes=2)
        tasks = self.make_worker().plan(self.clock())
        self.assertEqual([t.tier for t in tasks], ["hot"])
        self.assertEqual(len(tasks[0].listings), 1)

    def test_shops_being_read_marketplaces_paused_and_backing_off_shops_are_not_probed(self):
        busy, paused = self.shop("Busy Shop"), self.shop("Paused Shop", reading_paused=True)
        waiting = self.shop("Waiting Shop", backoff_until=self.clock() + timedelta(minutes=20))
        ebay = self.shop("eBay", source_type=Retailer.Source.EBAY, source_url="")
        for shop in (busy, paused, waiting, ebay):
            self.listing(self.product(f"box-{shop.slug}", views=3), shop, 30)
        reader = self.make_worker(executor=HeldExecutor())
        reader.running[busy.pk] = worker.Job(worker.Task(worker.SHOP_READ, busy.pk, "shopify", 1, "Read"), self.clock(), self.clock())
        self.assertEqual(reader.plan(self.clock()), [])

    def test_planning_takes_a_fixed_number_of_queries(self):
        def count():
            reader = self.make_worker()
            with CaptureQueriesContext(connection) as queries:
                reader.plan(self.clock())
            return len(queries)

        self.shop("Due Shop", due=True)
        self.listing(self.product("hot-box", views=3), self.shop("Hot Shop"), 30)
        few = count()
        for n in range(12):
            shop = self.shop(f"Shop {n:02d}", due=n % 3 == 0)
            for m in range(3):
                self.listing(self.product(f"box-{n}-{m}", views=m), shop, 20 + 30 * m)
        self.assertEqual(count(), few)
        # Pause all, the shops due, four for how hot each product is, the listings to check and the shops
        # due a pre-order pulse.
        self.assertEqual(few, 8)


class PolitenessTests(WorkerTestCase):
    def test_a_bucket_of_one_a_second_with_a_burst_of_three(self):
        bucket = worker.TokenBucket(1.0, 3, lambda: self.clock().timestamp())
        self.assertEqual([bucket.take() for _ in range(3)], [0, 0, 0])
        self.assertAlmostEqual(bucket.take(), 1.0)
        self.clock.advance(seconds=0.5)
        self.assertAlmostEqual(bucket.take(), 0.5)
        self.clock.advance(seconds=0.5)
        self.assertEqual(bucket.take(), 0)
        self.assertGreater(bucket.take(), 0)

    def test_the_two_a_second_limit_holds_across_threads(self):
        politeness = worker.Politeness(lambda: self.clock().timestamp(), self.clock.sleep)
        taken, start = [], threading.Barrier(8)

        def ask():
            start.wait()
            taken.append(politeness.everyone.take())

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(taken), 8)
        self.assertEqual(taken.count(0), 2)

    def test_a_website_shop_is_asked_once_every_two_seconds(self):
        shop = self.shop("Web Shop", source_type=Retailer.Source.WEBSITE)
        asked = []
        politeness = worker.Politeness(lambda: self.clock().timestamp(), self.clock.sleep)
        polite = politeness.wrap(shop, lambda url: asked.append((url, self.clock())))
        for n in range(3):
            polite(f"https://web-shop.example/p/{n}")
        self.assertEqual([round((at - asked[0][1]).total_seconds(), 3) for _url, at in asked], [0, 2, 4])
        self.assertEqual(politeness.requests_last_hour(), 3)

    def test_at_most_two_reads_and_one_website_read_run_at_once(self):
        for name in ("Web A", "Web B"):
            self.shop(name, due=True, source_type=Retailer.Source.WEBSITE)
        self.shop("Shopify A", due=True)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor)
        reader.queue = reader.plan(self.clock())
        reads = [t for t in reader.queue if t.kind == worker.SHOP_READ]
        self.assertEqual(len(reads), 2)
        self.assertEqual(sum(t.source_type == Retailer.Source.WEBSITE for t in reads), 1)
        reader.dispatch(self.clock())
        self.assertEqual([t.kind for t in executor.jobs], [worker.SHOP_READ, worker.SHOP_READ])
        # The next pass plans no read while both are running, and the import lock is held for them.
        self.clock.advance(minutes=1)
        self.assertEqual([t for t in reader.plan(self.clock()) if t.kind == worker.SHOP_READ], [])
        self.assertTrue(reader.import_lock.held)

    def test_a_read_waits_while_another_command_holds_the_import_lock_and_probes_go_on(self):
        self.shop("Due Shop", due=True)
        probed = self.shop("Probe Shop")
        self.listing(self.product("hot-box", views=3), probed, 30)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor)
        held = os.open(self.import_lock, os.O_RDONLY | os.O_CREAT)
        self.addCleanup(os.close, held)
        fcntl.flock(held, fcntl.LOCK_EX)
        reader.queue = reader.plan(self.clock())
        reader.dispatch(self.clock())
        self.assertEqual([t.kind for t in executor.jobs], [worker.PROBE])
        self.assertIsNone(Retailer.objects.get(name="Due Shop").next_read_at)

    def test_after_twenty_minutes_of_reads_the_import_lock_is_let_go_before_another_read(self):
        lock = worker.ImportLock(self.import_lock)
        self.assertTrue(lock.acquire(self.clock()))
        self.clock.advance(minutes=21)
        self.assertFalse(lock.acquire(self.clock()))
        lock.release()
        self.assertFalse(lock.held)
        self.assertTrue(lock.acquire(self.clock()))
        lock.release()


class ShopReadTests(WorkerTestCase):
    def read_once(self, shop, fetch):
        reader = self.make_worker(fetch=fetch)
        reader.run_once()
        shop.refresh_from_db()
        return reader

    def test_failed_reads_back_off_5_10_20_minutes_and_a_429_waits_30(self):
        shop = self.shop("Flaky Shop", due=True)

        def broken(url, *args, **kwargs):
            raise ImportError_(f"Could not fetch {url}: HTTP Error 503: Service Unavailable")

        waits = []
        for _ in range(3):
            self.read_once(shop, broken)
            waits.append(shop.backoff_until - self.clock())
            self.clock.advance(hours=7)
        self.assertEqual(waits, [timedelta(minutes=5), timedelta(minutes=10), timedelta(minutes=20)])
        self.assertEqual(shop.error_streak, 3)
        self.assertIn("HTTP Error 503", shop.last_error)

        busy = self.shop("Busy Shop", due=True)
        Retailer.objects.filter(pk=shop.pk).update(reading_paused=True)

        def throttled(url, *args, **kwargs):
            raise ImportError_(f"Could not fetch {url}: HTTP Error 429: Too Many Requests")

        self.read_once(busy, throttled)
        self.assertEqual(busy.backoff_until - self.clock(), timedelta(minutes=30))

        self.clock.advance(hours=2)
        before = self.clock()
        self.read_once(busy, shop_answers({"https://busy-shop.example": []}))
        self.assertEqual((busy.error_streak, busy.backoff_until, busy.last_error), (0, None, ""))
        # The next read is counted from the start of this one.
        self.assertEqual(busy.next_read_at, before + timedelta(minutes=45))

    def test_a_paused_shop_or_a_paused_worker_reads_nothing_and_the_heartbeat_goes_on(self):
        paused = self.shop("Paused Shop", due=True, reading_paused=True)
        asked = []
        self.read_once(paused, shop_answers({}, seen=asked))
        self.assertEqual(asked, [])
        Retailer.objects.filter(pk=paused.pk).update(reading_paused=False)
        WorkerState.objects.update_or_create(pk=1, defaults={"paused": True})
        reader = self.read_once(paused, shop_answers({}, seen=asked))
        self.assertEqual((asked, reader.jobs_done), ([], 0))
        self.assertEqual(WorkerState.objects.get().heartbeat_at, self.clock())

    def test_a_read_that_crashes_is_closed_and_backs_off(self):
        shop = self.shop("Odd Shop", due=True)
        with mock.patch("catalogue.importers.apply_offers", side_effect=ZeroDivisionError("odd")), \
                self.assertLogs("catalogue", "ERROR"):
            self.read_once(shop, shop_answers({"https://odd-shop.example": [product_json("thing")]}))
        run = ImportRun.objects.get()
        self.assertIsNotNone(run.finished_at)
        self.assertIn("ZeroDivisionError", run.error)
        self.assertEqual(shop.error_streak, 1)


class DeadlineTests(WorkerTestCase):
    def test_a_read_past_its_deadline_closes_its_run_and_exits_with_code_1(self):
        shop = self.shop("Slow Shop", due=True)
        reader = self.make_worker(executor=HeldExecutor())
        reader.queue = reader.plan(self.clock())
        reader.dispatch(self.clock())
        run = ImportRun.objects.create(retailer=shop, started_at=self.clock())
        self.assertEqual(WorkerState.objects.get().in_flight, [{"retailer": shop.pk, "since": self.clock().isoformat()}])
        self.clock.advance(minutes=19)
        with mock.patch.object(worker.os, "_exit") as exit_:
            reader.check_deadlines(self.clock())
            exit_.assert_not_called()
            self.clock.advance(minutes=2)
            with self.assertLogs("catalogue", "ERROR"):
                reader.check_deadlines(self.clock())
        exit_.assert_called_once_with(1)
        run.refresh_from_db()
        self.assertEqual((run.error, run.finished_at), (STOPPED, self.clock()))
        shop.refresh_from_db()
        self.assertEqual((shop.last_error, shop.error_streak), (worker.STUCK, 1))
        self.assertIn("Read Slow Shop ran past its 20 minute limit", WorkerState.objects.get().note)

    def test_a_failed_planning_pass_still_beats_and_is_tried_again(self):
        reader = self.make_worker(executor=HeldExecutor())
        with mock.patch.object(reader, "plan", side_effect=RuntimeError("busy")), self.assertLogs("catalogue", "ERROR"):
            reader.tick(self.clock())
        self.assertEqual(WorkerState.objects.get().heartbeat_at, self.clock())
        self.shop("Due Shop", due=True)
        self.clock.advance(seconds=61)
        reader.tick(self.clock())
        self.assertEqual([t.kind for t in reader.executor.jobs], [worker.SHOP_READ])

    def test_a_probe_has_a_minute_and_a_marketplace_read_an_hour(self):
        task = worker.Task(worker.PROBE, 1, "shopify", 1, "Check")
        self.assertEqual(worker.deadline_for(task), timedelta(seconds=60))
        task = worker.Task(worker.SHOP_READ, 1, Retailer.Source.EBAY, 1, "Read")
        self.assertEqual(worker.deadline_for(task), timedelta(minutes=60))
        task = worker.Task(worker.SHOP_READ, 1, Retailer.Source.WEBSITE, 1, "Read")
        self.assertEqual(worker.deadline_for(task), timedelta(minutes=45))

    def test_on_start_the_runs_left_in_flight_and_old_runs_are_closed(self):
        shop, other = self.shop("Cut Shop"), self.shop("Other Shop")
        since = self.clock() - timedelta(minutes=10)
        WorkerState.objects.create(pk=1, in_flight=[{"retailer": shop.pk, "since": since.isoformat()}])
        cut = ImportRun.objects.create(retailer=shop, started_at=since + timedelta(seconds=1))
        hand_run = ImportRun.objects.create(retailer=other, started_at=self.clock() - timedelta(minutes=30))
        ancient = ImportRun.objects.create(retailer=other, started_at=self.clock() - timedelta(hours=4))
        self.assertEqual(self.make_worker().begin(), 2)
        cut.refresh_from_db(), hand_run.refresh_from_db(), ancient.refresh_from_db()
        self.assertEqual((cut.error, ancient.error, hand_run.finished_at), (STOPPED, STOPPED, None))
        state = WorkerState.objects.get()
        self.assertEqual((state.in_flight, state.started_at), ([], self.clock()))


class RunWorkerOnceTests(WorkerTestCase):
    def test_one_pass_reads_a_shop_probes_two_hot_listings_and_holds_the_import_lock_only_while_reading(self):
        read = self.shop("Read Shop", due=True)
        mine = self.product("read-box")
        self.listing(mine, read, 120, price="80.00")
        probed = self.shop("Probe Shop")
        first, second = self.product("first-box", views=3), self.product("second-box", views=3)
        one = self.listing(first, probed, 30, availability="out_of_stock")
        two = self.listing(second, probed, 30, price="60.00")
        lock_held = []

        def check_lock(url):
            fd = os.open(self.import_lock, os.O_RDONLY | os.O_CREAT)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                lock_held.append((url, False))
            except BlockingIOError:
                lock_held.append((url, True))
            finally:
                os.close(fd)

        fetch = shop_answers(
            {"https://read-shop.example": [product_json("read-box", price="75.00")]},
            js={"https://probe-shop.example/products/first-box.js": js_answer(4999),
                "https://probe-shop.example/products/second-box.js": js_answer(5499)},
            during=check_lock,
        )
        out = StringIO()
        with mock.patch("catalogue.importers.fetch", fetch), mock.patch.object(worker, "WORKER_LOCK", str(self.folder / "w.lock")), \
                mock.patch("catalogue.importers.PAGE_PAUSE", 0), mock.patch.object(worker, "RATES", {}), \
                mock.patch.object(worker, "GLOBAL_RATE", (1000.0, 1000)):
            call_command("run_worker", "--once", stdout=out)
        self.assertIn("2 jobs done.", out.getvalue())
        read.refresh_from_db()
        self.assertIsNotNone(read.last_ok_at)
        self.assertEqual(Listing.objects.get(product=mine).price, Decimal("75.00"))
        one.refresh_from_db(), two.refresh_from_db()
        self.assertEqual((one.availability, one.price, one.back_in_stock_at is not None), ("in_stock", Decimal("49.99"), True))
        self.assertEqual(two.price, Decimal("54.99"))
        self.assertTrue(all(held for url, held in lock_held if url.startswith("https://read-shop")))
        self.assertFalse(any(held for url, held in lock_held if url.startswith("https://probe-shop")))
        fd = os.open(self.import_lock, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = WorkerState.objects.get()
        self.assertEqual((state.jobs_done, state.in_flight), (2, []))
        self.assertIsNotNone(state.heartbeat_at)
        self.assertEqual(state.requests_last_hour, len(lock_held))

    def test_a_second_worker_does_not_start(self):
        path = str(self.folder / "w.lock")
        held = worker.hold_worker_lock(path)
        self.addCleanup(os.close, held)
        from django.core.management.base import CommandError

        with mock.patch.object(worker, "WORKER_LOCK", path), self.assertRaisesMessage(CommandError, "Another background reader"):
            call_command("run_worker", "--once", stdout=StringIO())

    def test_the_watchdog_hears_the_heartbeat(self):
        path = str(self.folder / "notify.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        listener.bind(path)
        self.addCleanup(listener.close)
        listener.settimeout(5)
        with mock.patch.dict(os.environ, {"NOTIFY_SOCKET": path}):
            reader = self.make_worker()
            reader.begin()
            reader.beat(self.clock())
        self.assertEqual(listener.recv(100), b"READY=1")
        self.assertEqual(listener.recv(100), b"WATCHDOG=1")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(worker.sd_notify("WATCHDOG=1"))


class ProbeTests(WorkerTestCase):
    def preorder(self, release_date):
        shop = self.shop("Shop")
        product = self.product(f"box-{release_date}", release_date=release_date)
        return self.listing(product, shop, 30, availability="preorder")

    def test_a_preorder_becomes_in_stock_only_once_its_release_date_has_come(self):
        today = timezone.localdate()
        cases = [(None, "preorder"), (today + timedelta(days=3), "preorder"), (today, "in_stock")]
        answer = js_answer(4999)
        for when, expected in cases:
            with self.subTest(release_date=when):
                Retailer.objects.all().delete()
                listing = Listing.objects.select_related("retailer", "product__product_set").get(pk=self.preorder(when).pk)
                self.assertEqual(probe.probe_listing(listing, fetch=lambda url: answer), (Decimal("49.99"), expected))

    def test_a_set_release_date_counts(self):
        self.set.release_date = timezone.localdate() - timedelta(days=1)
        self.set.save()
        listing = Listing.objects.select_related("retailer", "product__product_set").get(pk=self.preorder(None).pk)
        self.assertEqual(probe.probe_listing(listing, fetch=lambda url: js_answer(4999))[1], "in_stock")

    def test_marketplace_listings_are_never_probed(self):
        asked = []
        for source in (Retailer.Source.EBAY, Retailer.Source.AMAZON):
            shop = self.shop(f"Market {source}", source_type=source)
            listing = self.listing(self.product(f"box-{source}", views=3), shop, 30,
                                   url=f"https://market.example/products/box-{source}")
            self.assertIsNone(probe.probe_listing(listing, fetch=lambda url: asked.append(url)))
        self.assertEqual(asked, [])
        self.assertEqual(self.make_worker().plan(self.clock()), [])


class FallbackTests(WorkerTestCase):
    def beat(self, minutes_ago):
        WorkerState.objects.update_or_create(pk=1, defaults={"heartbeat_at": timezone.now() - timedelta(minutes=minutes_ago)})

    def run_import_prices(self):
        read = []

        def fake_run(retailer, feed_path=None):
            read.append(retailer.name)
            return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now())

        out = StringIO()
        with mock.patch("catalogue.management.commands.import_prices.run_import", fake_run):
            call_command("import_prices", "--due", "--if-worker-dead", "30", stdout=out, stderr=StringIO())
        return read, out.getvalue()

    def test_import_prices_stands_aside_while_the_worker_beats_and_reads_when_it_stops(self):
        self.shop("Due Shop", due=True)
        self.beat(2)
        read, said = self.run_import_prices()
        self.assertEqual(read, [])
        self.assertIn("The background reader is running", said)
        self.beat(45)
        self.assertEqual(self.run_import_prices()[0], ["Due Shop"])

    def test_import_prices_reads_when_the_worker_never_ran(self):
        self.shop("Due Shop", due=True)
        self.assertEqual(self.run_import_prices()[0], ["Due Shop"])

    def test_watch_stock_stands_aside_while_the_worker_beats(self):
        shop = self.shop("Shop")
        self.listing(self.product("box"), shop, 120, availability="out_of_stock")
        asked = []

        def fetch(url):
            asked.append(url)
            return js_answer(4999)

        self.beat(2)
        out = StringIO()
        with mock.patch("catalogue.importers.fetch", fetch):
            call_command("watch_stock", "--pause", "0", "--if-worker-dead", "30", stdout=out)
            self.assertEqual(asked, [])
            self.assertIn("The background reader is running", out.getvalue())
            self.beat(31)
            call_command("watch_stock", "--pause", "0", "--if-worker-dead", "30", stdout=StringIO())
        self.assertEqual(asked, ["https://shop.example/products/box.js"])

    def test_check_worker_exits_1_on_a_stale_or_missing_heartbeat(self):
        with self.assertRaises(SystemExit) as stopped:
            call_command("check_worker", stdout=StringIO())
        self.assertEqual(stopped.exception.code, 1)
        self.beat(11)
        out = StringIO()
        with self.assertRaises(SystemExit) as stopped:
            call_command("check_worker", stdout=out)
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("has stopped: last heartbeat 11 minutes ago", out.getvalue())
        self.beat(1)
        out = StringIO()
        call_command("check_worker", stdout=out)
        self.assertIn("is running", out.getvalue())

    def test_worker_not_running_leads_the_improvements_only_when_stale(self):
        shop = self.shop("Broken Shop")
        ImportRun.objects.create(retailer=shop, finished_at=timezone.now(), error="HTTP 503")
        titles = [item["title"] for item in insights.report(30)["improvements"]]
        self.assertNotIn("Worker not running", titles)
        self.beat(3)
        self.assertNotIn("Worker not running", [item["title"] for item in insights.report(30)["improvements"]])
        self.beat(12)
        titles = [item["title"] for item in insights.report(30)["improvements"]]
        self.assertEqual(titles[:2], ["Worker not running", "1 shop not updating"])


class CrawlPageWorkerTests(WorkerTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("crawl")

    def test_the_heartbeat_line_is_green_while_it_beats_and_red_when_it_stops(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Background reader not started.")
        WorkerState.objects.create(pk=1, heartbeat_at=timezone.now() - timedelta(minutes=2), started_at=timezone.now(),
                                   requests_last_hour=120, errors_last_hour=3, jobs_done=40, last_job="Read Shop")
        page = self.client.get(self.url)
        self.assertContains(page, '<p class="ok" id="worker-line"><strong>Background reader running.</strong>')
        self.assertContains(page, "In the last hour: 120 requests to shops, 3 errors.")
        self.assertNotContains(self.client.get(reverse("admin:index")), "Background reader stopped")
        WorkerState.objects.filter(pk=1).update(heartbeat_at=timezone.now() - timedelta(minutes=11))
        page = self.client.get(self.url)
        self.assertContains(page, '<p class="bad" id="worker-line"><strong>Background reader stopped.</strong>')
        self.assertContains(self.client.get(reverse("admin:index")), "Background reader stopped at")

    def test_hot_and_warm_counts_and_pause_all_writes_the_row(self):
        shop = self.shop("Shop")
        self.listing(self.product("hot-box", views=3), shop, 30)
        self.listing(self.product("warm-box"), shop, 30)
        self.assertContains(self.client.get(self.url), "Checked every ten minutes: 1 product. Every hour: 1 product.")
        self.client.post(self.url, {"action": "pause_all"})
        self.assertTrue(WorkerState.objects.get().paused)
        self.assertEqual(self.make_worker().plan(self.clock()), [])
        self.client.post(self.url, {"action": "resume_all"})
        self.assertFalse(WorkerState.objects.get().paused)
        WorkerState.objects.filter(pk=1).update(heartbeat_at=timezone.now())
        page = self.client.post(self.url, {"action": "read_now", "shop": shop.pk}, follow=True)
        self.assertContains(page, "Shop will be read within a few minutes.")


class VariantProbeTests(WorkerTestCase):
    """A listing for one variant of a Shopify product is priced and stocked from that variant alone."""

    def variants(self, case_available=True, box_available=True):
        return json.dumps({"variants": [
            {"id": 1, "price": 10000, "available": box_available},
            {"id": 2, "price": 60000, "available": case_available},
        ]}).encode()

    def case_listing(self):
        shop = self.shop("Shop")
        return self.listing(self.product("thing", views=3), shop, 30, price="600.00",
                            url="https://shop.example/products/thing?variant=2")

    def test_the_probe_reads_the_variant_the_listing_names(self):
        listing = self.case_listing()
        self.assertEqual(probe.probe_listing(listing, fetch=lambda url: self.variants()), (Decimal("600.00"), "in_stock"))
        # The case sold out while the box is still for sale: the case listing is out of stock.
        self.assertEqual(probe.probe_listing(listing, fetch=lambda url: self.variants(case_available=False)),
                         (Decimal("600.00"), "out_of_stock"))
        gone = json.dumps({"variants": [{"id": 1, "price": 10000, "available": True}]}).encode()
        self.assertIsNone(probe.probe_listing(listing, fetch=lambda url: gone))

    def test_a_worker_pass_keeps_the_variant_price(self):
        listing = self.case_listing()
        asked = []
        reader = self.make_worker(fetch=shop_answers({}, js={"https://shop.example/products/thing.js": self.variants()}, seen=asked))
        reader.run_once()
        listing.refresh_from_db()
        self.assertEqual(asked, ["https://shop.example/products/thing.js"])
        self.assertEqual((listing.price, listing.availability), (Decimal("600.00"), "in_stock"))
        self.assertGreater(listing.last_checked, self.clock() - timedelta(minutes=1))


class FeedProbeTests(WorkerTestCase):
    def test_feed_listings_are_never_probed_so_no_affiliate_link_is_followed(self):
        feed = self.shop("Feed Shop", source_type=Retailer.Source.FEED, source_url="https://feed.example/feed.csv")
        self.listing(self.product("box", views=3), feed, 30, url="https://www.awin1.com/pclick.php?p=1&a=2&m=3")
        asked = []
        reader = self.make_worker(fetch=shop_answers({}, seen=asked))
        self.assertEqual(reader.plan(self.clock()), [])
        reader.run_once()
        with mock.patch("catalogue.importers.fetch", shop_answers({}, seen=asked)):
            out = StringIO()
            call_command("watch_stock", "--pause", "0", stdout=out)
        self.assertEqual(asked, [])
        self.assertIn("0 checked", out.getvalue())


class ProbeBackOffTests(WorkerTestCase):
    def hot_listings(self, shop, count):
        return [self.listing(self.product(f"box-{n}", views=3), shop, 30) for n in range(count)]

    def failing(self, error, asked):
        def fetch(url, *args, **kwargs):
            asked.append(url)
            raise ImportError_(f"Could not fetch {url}: {error}")

        return fetch

    def test_a_429_backs_the_shop_off_30_minutes_after_one_request(self):
        shop = self.shop("Busy Shop")
        self.hot_listings(shop, 8)
        asked = []
        reader = self.make_worker(fetch=self.failing("HTTP Error 429: Too Many Requests", asked))
        with self.assertLogs("catalogue", "WARNING"):
            reader.run_once()
        shop.refresh_from_db()
        self.assertEqual(len(asked), 1)
        self.assertEqual(shop.backoff_until - self.clock(), timedelta(minutes=30))
        self.assertTrue(shop.last_error.startswith("Checking single listings: Could not fetch"))
        self.assertIn("HTTP Error 429", shop.last_error)
        self.clock.advance(minutes=1)
        self.assertEqual(reader.plan(self.clock()), [])
        # Nor is the shop read whole until its wait is over.
        Retailer.objects.filter(pk=shop.pk).update(next_read_at=self.clock())
        self.assertEqual(reader.plan(self.clock()), [])

    def test_three_failures_in_a_row_back_the_shop_off_and_missing_listings_do_not_count(self):
        gone_shop, broken_shop = self.shop("Gone Shop"), self.shop("Broken Shop")
        self.hot_listings(gone_shop, 5)
        asked = []
        reader = self.make_worker(fetch=self.failing("HTTP Error 404: Not Found", asked))
        reader.run_once()
        gone_shop.refresh_from_db()
        self.assertEqual((len(asked), gone_shop.backoff_until, gone_shop.error_streak), (5, None, 0))

        Listing.objects.all().delete()
        for n in range(5):
            self.listing(self.product(f"other-{n}", views=3), broken_shop, 30)
        asked = []
        reader = self.make_worker(fetch=self.failing("HTTP Error 503: Service Unavailable", asked))
        with self.assertLogs("catalogue", "WARNING"):
            reader.run_once()
        broken_shop.refresh_from_db()
        self.assertEqual(len(asked), worker.PROBE_ERRORS_IN_A_ROW)
        self.assertEqual((broken_shop.backoff_until - self.clock(), broken_shop.error_streak), (timedelta(minutes=5), 1))

    def test_failures_in_a_row_are_counted_across_jobs(self):
        shop = self.shop("Slow Shop")
        listings = self.hot_listings(shop, 3)
        asked = []
        reader = self.make_worker(fetch=self.failing("timed out", asked))
        for listing in listings:
            # One listing a job, as when each request uses up the job's time.
            task = worker.Task(worker.PROBE, shop.pk, "shopify", 1, "Check", tier="hot", listings=[listing.pk])
            reader.executor = worker.InlineExecutor()
            if listing is listings[-1]:
                with self.assertLogs("catalogue", "WARNING"):
                    reader.start(task, self.clock())
            else:
                reader.start(task, self.clock())
        shop.refresh_from_db()
        self.assertEqual(len(asked), 3)
        self.assertIsNotNone(shop.backoff_until)


class SlowShopTests(WorkerTestCase):
    """A shop that sends its answer a byte at a time cannot hold a probe past its limit."""

    def serve(self, delay, body=b"x" * 50):
        import http.server

        class Drip(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        if delay:
                            stopped.wait(delay)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        stopped = threading.Event()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drip)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(stopped.set)
        return f"http://127.0.0.1:{server.server_address[1]}/products/box.js"

    def test_a_dripping_answer_is_given_up_at_the_deadline(self):
        import time

        from . import importers

        url = self.serve(0.2)
        started = time.monotonic()
        with self.assertRaisesMessage(ImportError_, "ran out of time"):
            importers.fetch(url, retries=0, deadline=time.monotonic() + 1)
        self.assertLess(time.monotonic() - started, 3)
        started = time.monotonic()
        session = importers.session_fetch(url)
        with self.assertRaisesMessage(ImportError_, "ran out of time"):
            session(url, retries=0, deadline=time.monotonic() + 1)
        # The visit to the session address and the page share the one deadline.
        self.assertLess(time.monotonic() - started, 3)

    def test_a_prompt_answer_is_read_whole_with_a_deadline(self):
        import time

        from . import importers

        url = self.serve(0, body=b"y" * 200000)
        self.assertEqual(importers.fetch(url, retries=0, deadline=time.monotonic() + 5), b"y" * 200000)
        self.assertEqual(importers.session_fetch(url)(url, retries=0, deadline=time.monotonic() + 5), b"y" * 200000)

    def test_every_probe_request_carries_a_deadline(self):
        shop = self.shop("Shop")
        self.listing(self.product("box", views=3), shop, 30)
        calls = []

        def fetch(url, retries=2, deadline=None):
            calls.append((retries, deadline))
            return js_answer(4999)

        reader = worker.Worker(clock=self.clock, sleep=self.clock.sleep, page_pause=0)
        with mock.patch("catalogue.importers.fetch", fetch), mock.patch.object(worker.time, "monotonic", return_value=100.0):
            reader.run_once()
        self.assertEqual(calls, [(0, 100.0 + worker.PROBE_FETCH_SECONDS)])


class StuckProbeTests(WorkerTestCase):
    def test_a_stuck_probe_backs_its_shop_off_and_hands_the_other_reads_back(self):
        stuck_shop = self.shop("Tarpit Shop")
        self.listing(self.product("box", views=3), stuck_shop, 30)
        reading = self.shop("Read Shop", due=True)
        reader = self.make_worker(executor=HeldExecutor())
        reader.queue = reader.plan(self.clock())
        reader.dispatch(self.clock())
        self.assertEqual({t.kind for t in reader.executor.jobs}, {worker.SHOP_READ, worker.PROBE})
        run = ImportRun.objects.create(retailer=reading, started_at=self.clock())
        self.clock.advance(seconds=61)
        with mock.patch.object(worker.os, "_exit") as exit_, self.assertLogs("catalogue", "ERROR"):
            reader.check_deadlines(self.clock())
        exit_.assert_called_once_with(1)
        stuck_shop.refresh_from_db(), reading.refresh_from_db(), run.refresh_from_db()
        self.assertEqual((stuck_shop.last_error, stuck_shop.error_streak), (worker.STUCK, 1))
        self.assertEqual(stuck_shop.backoff_until, self.clock() + timedelta(minutes=5))
        # The read beside it did nothing wrong: closed, and due again at once.
        self.assertEqual((run.error, reading.next_read_at, reading.error_streak), (STOPPED, self.clock(), 0))
        self.assertEqual(WorkerState.objects.get().in_flight, [])
        # The fresh process reads the shop it lost and leaves the stuck one alone.
        self.clock.advance(seconds=10)
        fresh = self.make_worker(executor=HeldExecutor())
        fresh.begin()
        self.assertEqual([(t.kind, t.retailer_id) for t in fresh.plan(self.clock())], [(worker.SHOP_READ, reading.pk)])

    def test_a_shop_read_gets_twice_its_last_healthy_read_when_that_is_longer(self):
        def limit(seconds, source=Retailer.Source.SHOPIFY):
            return worker.deadline_for(worker.Task(worker.SHOP_READ, 1, source, 1, "Read", last_read_seconds=seconds))

        self.assertEqual(limit(None), timedelta(minutes=20))
        self.assertEqual(limit(300), timedelta(minutes=20))
        self.assertEqual(limit(900), timedelta(minutes=30))
        self.assertEqual(limit(900, Retailer.Source.WEBSITE), timedelta(minutes=45))
        self.assertEqual(limit(5 * 3600), timedelta(hours=2))
        big = self.shop("Big Shop", due=True, last_read_seconds=900)
        task = self.make_worker().plan(self.clock())[0]
        self.assertEqual((task.retailer_id, worker.deadline_for(task)), (big.pk, timedelta(minutes=30)))


class PulseTests(WorkerTestCase):
    """The pre-order pulse: each Shopify shop's collection list every 15 minutes, between reads and probes."""

    def pulse_shop(self, name, **kwargs):
        kwargs.setdefault("collections_polled_at", None)
        kwargs.setdefault("collections_ok", None)
        return self.shop(name, **kwargs)

    def collections(self, base, handles, products=None, seen=None, status=None):
        """A fetch answering /collections.json for ``base`` with ``handles`` ({handle: count}) and their products."""

        def fetch(url, *args, **kwargs):
            if seen is not None:
                seen.append(url)
            if status is not None:
                raise ImportError_(f"Could not fetch {url}: HTTP Error {status}: Too Many Requests")
            if url == f"{base}/meta.json":
                return b'{"currency": "GBP"}'
            if url == f"{base}/collections.json?limit=250":
                return json.dumps({"collections": [
                    {"handle": h, "products_count": n, "updated_at": "2026-10-09T08:00:00Z"} for h, n in handles.items()
                ]}).encode()
            for handle, items in (products or {}).items():
                if url.startswith(f"{base}/collections/{handle}/products.json"):
                    return json.dumps({"products": items if url.endswith("page=1") else []}).encode()
            return b'{"products": []}'

        return fetch

    def test_a_shop_due_a_pulse_is_planned_once_per_shop_after_reads_and_probes(self):
        due = self.pulse_shop("Pulse Shop")
        reading = self.pulse_shop("Read Shop", due=True)
        probed = self.pulse_shop("Probe Shop")
        self.listing(self.product("hot-box", views=3), probed, 30)
        self.pulse_shop("Paused Shop", reading_paused=True)
        self.pulse_shop("Waiting Shop", backoff_until=self.clock() + timedelta(minutes=20))
        self.shop("Just Pulsed")
        self.pulse_shop("Feed Shop", source_type=Retailer.Source.FEED, source_url="https://feed.example/f.csv")
        tasks = self.make_worker().plan(self.clock())
        self.assertEqual([(t.kind, t.retailer_id) for t in tasks], [
            (worker.SHOP_READ, reading.pk), (worker.PROBE, probed.pk), (worker.PULSE, due.pk),
        ])
        self.assertEqual(tasks[2].priority, worker.WEIGHTS[worker.PULSE] * worker.NEVER_READ_MINUTES)
        self.assertEqual(worker.deadline_for(tasks[2]), timedelta(seconds=60))

    def test_a_pulse_finds_a_new_preorder_and_is_not_repeated_within_fifteen_minutes(self):
        shop = self.pulse_shop("Pulse Shop")
        product = self.product("dr-box", ean="0196214112345")
        base = shop.source_url.rstrip("/")
        item = product_json("dr-box", price="139.99")
        item["variants"][0]["barcode"] = "0196214112345"
        seen = []
        fetch = self.collections(base, {"pre-orders": 1}, {"pre-orders": [item]}, seen)
        reader = self.make_worker(fetch=fetch)
        start = self.clock()
        reader.run_once()
        listing = Listing.objects.get(product=product, retailer=shop)
        self.assertEqual(listing.availability, Listing.Availability.PREORDER)
        shop.refresh_from_db()
        self.assertEqual(shop.collections_polled_at, start)
        self.assertTrue(ImportRun.objects.get().note.startswith("Pre-order pulse"))
        # The new pre-order is hot, so it is probed too; the collection list waits its 15 minutes.
        seen.clear()
        self.clock.advance(minutes=14)
        reader.run_once()
        self.assertEqual([url for url in seen if "/collections" in url], [])
        self.clock.advance(minutes=1)
        reader.run_once()
        self.assertEqual([url for url in seen if "/collections" in url], [f"{base}/collections.json?limit=250"])

    def test_a_shop_that_throttles_the_pulse_backs_off(self):
        shop = self.pulse_shop("Busy Shop")
        reader = self.make_worker(fetch=self.collections(shop.source_url.rstrip("/"), {}, status=429))
        reader.run_once()
        shop.refresh_from_db()
        self.assertEqual(shop.backoff_until - self.clock(), timedelta(minutes=30))
        self.assertTrue(shop.last_error.startswith("Looking for pre-orders:"))
        self.clock.advance(minutes=20)
        self.assertEqual(reader.plan(self.clock()), [])

    def test_a_pulse_out_of_time_leaves_the_rest_for_the_next_look(self):
        from .models import RetailerCollection

        shop = self.pulse_shop("Slow Shop")
        base = shop.source_url.rstrip("/")
        answer = self.collections(base, {"pre-orders": 1, "coming-soon": 1})

        def slow(url, *args, **kwargs):
            if "/products.json" in url:
                self.clock.advance(seconds=45)
            return answer(url)

        self.make_worker(fetch=slow).run_once()
        self.assertEqual(list(RetailerCollection.objects.values_list("handle", flat=True)), ["pre-orders"])

    def test_pulses_of_different_shops_start_apart(self):
        for name in ("Shop A", "Shop B"):
            self.pulse_shop(name)
        started = []
        reader = self.make_worker(fetch=lambda url, *a, **k: started.append(self.clock()) or b'{"collections": []}')
        reader.run_once()
        self.assertEqual(len(started), 2)
        self.assertGreaterEqual((started[1] - started[0]).total_seconds(), worker.PULSE_GAP)

    def test_a_stuck_pulse_closes_its_run(self):
        shop = self.pulse_shop("Tarpit Shop")
        reader = self.make_worker(executor=HeldExecutor())
        reader.queue = reader.plan(self.clock())
        reader.dispatch(self.clock())
        self.assertEqual([t.kind for t in reader.executor.jobs], [worker.PULSE])
        run = ImportRun.objects.create(retailer=shop, started_at=self.clock(), note="Pre-order pulse: pre-orders")
        self.clock.advance(seconds=61)
        with mock.patch.object(worker.os, "_exit") as exit_, self.assertLogs("catalogue", "ERROR"):
            reader.check_deadlines(self.clock())
        exit_.assert_called_once_with(1)
        run.refresh_from_db()
        self.assertEqual(run.error, STOPPED)


class StopTests(WorkerTestCase):
    def reading(self):
        shop = self.shop("Read Shop", due=True)
        reader = self.make_worker(executor=HeldExecutor())
        reader.queue = reader.plan(self.clock())
        reader.dispatch(self.clock())
        run = ImportRun.objects.create(retailer=shop, started_at=self.clock())
        self.clock.advance(minutes=3)
        return shop, reader, run

    def assert_handed_back(self, shop, run):
        shop.refresh_from_db(), run.refresh_from_db()
        self.assertEqual((run.error, run.finished_at, shop.next_read_at), (STOPPED, self.clock(), self.clock()))
        self.assertEqual(WorkerState.objects.get().in_flight, [])

    def test_stop_closes_the_reads_in_flight_and_makes_their_shops_due(self):
        shop, reader, run = self.reading()
        with mock.patch.object(worker.os, "_exit") as exit_:
            reader.stop()
        exit_.assert_called_once_with(0)
        self.assert_handed_back(shop, run)

    def test_ctrl_c_or_sigint_from_systemd_stops_the_loop_cleanly(self):
        shop, reader, run = self.reading()

        def interrupted(seconds):
            raise KeyboardInterrupt

        reader.sleep = interrupted
        reader.last_beat = None
        with mock.patch.object(worker.os, "_exit") as exit_:
            reader.run_forever()
        exit_.assert_called_once_with(0)
        self.assert_handed_back(shop, run)
        # The loop beat once before it was stopped.
        self.assertEqual(WorkerState.objects.get().heartbeat_at, self.clock())


class SettingsTests(WorkerTestCase):
    def test_the_worker_pauses_half_a_second_between_shopify_pages(self):
        shop = self.shop("Big Shop", due=True)
        reader = self.make_worker(page_pause=None, fetch=shop_answers({"https://big-shop.example": [product_json("box")]}))
        self.assertEqual(reader.page_pause, 0.5)
        with mock.patch("catalogue.importers.time.sleep") as slept:
            reader.run_once()
        shop.refresh_from_db()
        self.assertIsNotNone(shop.last_ok_at)
        # Page 1 held the products, page 2 was empty: one pause, before page 2.
        slept.assert_called_once_with(0.5)

    @override_settings(RIPRAPTOR_WORKER_THREADS=2)
    def test_the_thread_count_comes_from_the_environment_setting(self):
        built = []
        real = worker.Worker

        def spy(*args, **kwargs):
            built.append(kwargs.get("threads"))
            return real(*args, **kwargs)

        with mock.patch.object(worker, "WORKER_LOCK", str(self.folder / "w.lock")), mock.patch.object(worker, "Worker", spy):
            call_command("run_worker", "--once", stdout=StringIO())
            call_command("run_worker", "--once", "--max-threads", "1", stdout=StringIO())
        self.assertEqual(built, [2, 1])


NOTIFY = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com",
          "RIPRAPTOR_SITE_URL": "https://ripraptor.com", "RIPRAPTOR_INBOX_NOTIFY_EMAIL": "owner@example.com",
          "RIPRAPTOR_NTFY_TOPIC": "rr-test-topic", "RIPRAPTOR_NTFY_URL": "https://ntfy.sh",
          "RIPRAPTOR_CRAWL_PUSHES": True}


@override_settings(**NOTIFY)
class NoticeTests(WorkerTestCase):
    """The owner hears about crawl problems on their phone, once a day each, with no shop, product or price in the push."""

    def setUp(self):
        super().setUp()
        self.pushes, self.emails = [], []
        for patcher in (
            mock.patch.object(notify.urllib.request, "urlopen", lambda request, timeout=None: self.pushes.append(request) or mock.MagicMock()),
            mock.patch.object(mail, "send", lambda to, subject, text, html, **kw: self.emails.append((to, subject, text))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def failing_shop(self, name, hours, **kwargs):
        return self.shop(name, error_streak=9, last_error="HTTP 503", failing_since=self.clock() - timedelta(hours=hours),
                         backoff_until=self.clock() + timedelta(hours=1), **kwargs)

    def doubtful(self, n):
        shop = self.shop("Doubt Shop")
        for i in range(n):
            listing = make_listing(self.product(f"doubt-{i}"), shop, price="1.99")
            Listing.objects.filter(pk=listing.pk).update(sanity=Listing.Sanity.DOUBTFUL)

    def titles(self):
        return [request.get_header("Title") for request in self.pushes]

    def assert_nothing_named(self, request, *names):
        for text in (request.get_header("Title"), request.data.decode()):
            for name in names:
                self.assertNotIn(name, text)
        self.assertEqual(request.data, b"Open admin to read it.")

    def test_a_shop_25_hours_into_back_off_pushes_once_without_its_name(self):
        began = self.failing_shop("Grimm Cards", 25).failing_since
        self.failing_shop("Fresh Fail", 23)                      # not a day yet
        self.failing_shop("Paused Fail", 30, reading_paused=True)  # the owner already knows

        def still_down(url, *args, **kwargs):
            raise ImportError_(f"Could not fetch {url}: HTTP Error 503: Service Unavailable")

        # Each pass reads the shops whose wait is over, and they fail again.
        reader = self.make_worker(fetch=still_down)
        reader.run_once()
        self.assertEqual(self.titles(), ["A shop keeps failing"])
        push = self.pushes[0]
        self.assertEqual(push.full_url, "https://ntfy.sh/rr-test-topic")
        self.assertEqual(push.get_header("Click"), "https://ripraptor.com/admin/crawl/")
        self.assert_nothing_named(push, "Grimm", "503")
        # The email goes to the owner alone, so it says which shop and why.
        self.assertEqual(len(self.emails), 1)
        to, subject, text = self.emails[0]
        self.assertEqual((to, subject), ("owner@example.com", "A shop keeps failing"))
        self.assertIn("Grimm Cards: HTTP 503", text)
        self.assertNotIn("Fresh Fail", text)
        self.assertNotIn("Paused Fail", text)
        self.clock.advance(hours=1)
        reader.run_once()
        self.assertEqual(len(self.pushes), 1)
        self.clock.advance(hours=23, minutes=1)
        reader.run_once()
        self.assertEqual(self.titles(), ["A shop keeps failing"] * 2)
        # Failing again kept the day it began.
        self.assertEqual(Retailer.objects.get(name="Grimm Cards").failing_since, began)

    def test_failing_since_starts_with_the_first_failure_and_ends_with_a_good_read(self):
        shop = self.shop("Shop")
        first = self.clock()
        shop.read_failed(first, 503, "HTTP 503")
        self.clock.advance(hours=3)
        shop.read_failed(self.clock(), 503, "HTTP 503")
        shop.refresh_from_db()
        self.assertEqual((shop.error_streak, shop.failing_since), (2, first))
        shop.read_ok(self.clock(), 30)
        shop.refresh_from_db()
        self.assertIsNone(shop.failing_since)

    def test_a_stale_heartbeat_makes_check_worker_push_once_a_day(self):
        def check():
            out = StringIO()
            with self.assertRaises(SystemExit):
                call_command("check_worker", stdout=out)
            return out.getvalue()

        WorkerState.objects.update_or_create(pk=1, defaults={"heartbeat_at": timezone.now() - timedelta(minutes=5)})
        call_command("check_worker", stdout=StringIO())
        self.assertEqual(self.pushes, [])
        WorkerState.objects.filter(pk=1).update(heartbeat_at=timezone.now() - timedelta(minutes=15))
        self.assertIn("The owner has been told.", check())
        self.assertNotIn("The owner has been told.", check())
        self.assertEqual(self.titles(), ["RipRaptor crawl stopped"])
        self.assertEqual(self.pushes[0].get_header("Click"), "https://ripraptor.com/admin/crawl/")
        self.assertEqual(self.pushes[0].data, b"Open admin to read it.")
        sent = WorkerState.objects.get().notices[notify.WORKER_STOPPED]
        WorkerState.objects.filter(pk=1).update(notices={
            notify.WORKER_STOPPED: (datetime.fromisoformat(sent) - timedelta(hours=25)).isoformat(),
        })
        check()
        self.assertEqual(self.titles(), ["RipRaptor crawl stopped"] * 2)

    def test_a_reader_that_never_beats_is_pushed_about_from_the_second_check(self):
        def check():
            out = StringIO()
            with self.assertRaises(SystemExit) as stopped:
                call_command("check_worker", stdout=out)
            self.assertEqual(stopped.exception.code, 1)
            return out.getvalue()

        def seen_ago(minutes):
            state = WorkerState.objects.get()
            seen = datetime.fromisoformat(state.notices[notify.NEVER_BEAT_SEEN])
            WorkerState.objects.filter(pk=1).update(notices={
                **state.notices, notify.NEVER_BEAT_SEEN: (seen - timedelta(minutes=minutes)).isoformat(),
            })

        # The first check only notes the time: a reader still starting up is not reported.
        self.assertNotIn("The owner has been told.", check())
        self.assertEqual(self.pushes, [])
        self.assertIsNone(WorkerState.objects.get().heartbeat_at)
        seen_ago(5)
        check()
        self.assertEqual(self.pushes, [])
        # An hour on, the next check still finds no heartbeat.
        seen_ago(55)
        self.assertIn("The owner has been told.", check())
        self.assertNotIn("The owner has been told.", check())
        self.assertEqual(self.titles(), ["RipRaptor crawl stopped"])
        self.assertEqual(self.pushes[0].get_header("Click"), "https://ripraptor.com/admin/crawl/")
        self.assertEqual(self.pushes[0].data, b"Open admin to read it.")
        self.assertIn("never sent a heartbeat", self.emails[0][2])
        # The same once-a-day guard as a reader that stopped: a beat then a stop the same day is not sent again.
        WorkerState.objects.filter(pk=1).update(heartbeat_at=timezone.now() - timedelta(minutes=15))
        check()
        self.assertEqual(len(self.pushes), 1)

    def test_eleven_doubtful_prices_push_once_and_nine_do_not(self):
        self.doubtful(9)
        reader = self.make_worker()
        reader.notice_problems(self.clock())
        self.assertEqual(self.pushes, [])
        for i in range(9, 11):
            listing = make_listing(self.product(f"doubt-{i}"), Retailer.objects.get(name="Doubt Shop"), price="1.99")
            Listing.objects.filter(pk=listing.pk).update(sanity=Listing.Sanity.DOUBTFUL)
        reader.notice_problems(self.clock())
        reader.notice_problems(self.clock())
        self.assertEqual(self.titles(), ["Prices to check"])
        self.assertEqual(self.pushes[0].get_header("Click"), "https://ripraptor.com/admin/checks/")
        self.assert_nothing_named(self.pushes[0], "Doubt", "1.99", "11")
        self.assertIn("11 doubtful prices", self.emails[0][2])

    def test_notices_are_checked_once_a_planning_pass_and_never_while_paused(self):
        reader = self.make_worker(executor=HeldExecutor())
        with mock.patch.object(reader, "notice_problems") as noticed:
            reader.tick(self.clock())
            self.clock.advance(seconds=5)
            reader.tick(self.clock())
            self.assertEqual(noticed.call_count, 1)
            self.clock.advance(seconds=60)
            reader.tick(self.clock())
            self.assertEqual(noticed.call_count, 2)
        self.failing_shop("Grimm Cards", 25)
        self.doubtful(11)
        WorkerState.objects.update_or_create(pk=1, defaults={"paused": True})
        reader.notice_problems(self.clock())
        self.assertEqual(self.pushes, [])

    def test_doubtful_prices_waiting_is_an_improvement_at_weight_72(self):
        self.doubtful(11)
        items = {item["title"]: item for item in insights.report(30)["improvements"]}
        self.assertEqual(items["11 doubtful prices waiting"]["weight"], 72)
        self.assertEqual(items["11 doubtful prices waiting"]["link"], "/admin/checks/")

    def test_ntfy_down_still_emails_and_never_raises(self):
        self.failing_shop("Grimm Cards", 25)
        with mock.patch.object(notify.urllib.request, "urlopen", side_effect=OSError("down")):
            self.make_worker().notice_problems(self.clock())
        self.assertEqual(len(self.emails), 1)

    @override_settings(RIPRAPTOR_NTFY_TOPIC="", RIPRAPTOR_INBOX_NOTIFY_EMAIL="")
    def test_no_settings_sends_nothing_and_raises_nothing(self):
        self.failing_shop("Grimm Cards", 25)
        self.doubtful(11)
        with self.assertRaises(SystemExit):
            call_command("check_worker", stdout=StringIO())   # never beat
        self.make_worker().run_once()
        WorkerState.objects.filter(pk=1).update(heartbeat_at=timezone.now() - timedelta(minutes=15))
        with self.assertRaises(SystemExit):
            call_command("check_worker", stdout=StringIO())
        self.assertEqual((self.pushes, self.emails), ([], []))
        # Nothing was sent, so nothing is noted: setting a topic later sends at once.
        self.assertEqual(WorkerState.objects.get().notices, {})

    @override_settings(RIPRAPTOR_CRAWL_PUSHES=False)
    def test_crawl_pushes_off_sends_nothing_about_crawling(self):
        self.failing_shop("Grimm Cards", 25)
        self.doubtful(11)
        self.make_worker().notice_problems(self.clock())
        WorkerState.objects.filter(pk=1).update(heartbeat_at=timezone.now() - timedelta(minutes=15))
        with self.assertRaises(SystemExit):
            call_command("check_worker", stdout=StringIO())
        self.assertEqual((self.pushes, self.emails), ([], []))


class TestRunSendsNothingTests(TestCase):
    def test_a_test_run_from_a_shell_holding_the_servers_env_blanks_the_owners_addresses(self):
        server_env = {"RIPRAPTOR_NTFY_TOPIC": "owner-topic", "RIPRAPTOR_INBOX_NOTIFY_EMAIL": "owner@example.com",
                      "RIPRAPTOR_ZEPTOMAIL_TOKEN": "real-token"}
        path = Path(__file__).resolve().parent.parent / "ripraptor" / "settings.py"
        with mock.patch.dict(os.environ, server_env):
            with mock.patch.object(sys, "argv", ["manage.py", "test"]):
                tested = runpy.run_path(str(path))
            with mock.patch.object(sys, "argv", ["manage.py", "check_worker"]):
                served = runpy.run_path(str(path))
        for name in server_env:
            self.assertEqual(tested[name], "")
            self.assertEqual(served[name], server_env[name])


class SharedDatabaseTests(TestCase):
    def test_a_worker_pass_writes_listings_while_the_site_counts_pages_without_locked_errors(self):
        """A real file database: the worker reads a shop and probes listings while another connection
        writes a page count every few milliseconds, as gunicorn does."""
        with file_database() as path, tempfile.TemporaryDirectory() as folder:
            pset = make_set(make_game())
            read = make_retailer("Read Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://read-shop.example/")
            probed = make_retailer("Probe Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://probe-shop.example/",
                                   next_read_at=timezone.now() + timedelta(hours=1))
            products, js = [], {}
            today = timezone.localdate()
            for n in range(300):
                product = make_product(pset, name=f"Box {n}", slug=f"box-{n}", product_type="booster_box")
                make_listing(product, read, url=f"https://read-shop.example/products/box-{n}", hours_ago=2)
                products.append(product_json(f"box-{n}", price=f"{50 + n}.00"))
            for n in range(20):
                product = Product.objects.get(slug=f"box-{n}")
                make_listing(product, probed, url=f"https://probe-shop.example/products/box-{n}",
                             availability="out_of_stock", hours_ago=2)
                js[f"https://probe-shop.example/products/box-{n}.js"] = js_answer(4000 + n)
                DailyPageView.objects.create(date=today, kind="product", key=product.slug, hits=3)
            stop, errors, counted = threading.Event(), [], []

            def count_pages():
                while not stop.is_set():
                    insights.bump(DailyPageView, kind=DailyPageView.Kind.PRODUCT, key="busy")
                    counted.append(1)
                    stop.wait(0.003)

            def guarded():
                try:
                    open_in_thread(path, count_pages)
                except Exception as exc:  # noqa: BLE001 - any failure in a thread must fail the test
                    errors.append(exc)

            site = threading.Thread(target=guarded)
            with mock.patch.object(worker, "RATES", {}), mock.patch.object(worker, "GLOBAL_RATE", (1000.0, 1000)):
                reader = worker.Worker(fetch=shop_answers({"https://read-shop.example": products}, js=js), page_pause=0,
                                       import_lock=str(Path(folder) / "import.lock"))
                site.start()
                try:
                    with self.assertNoLogs("catalogue", "WARNING"):
                        reader.run_once()
                finally:
                    stop.set()
                    site.join(timeout=60)
            self.assertEqual(errors, [])
            self.assertGreater(len(counted), 10)
            self.assertEqual(reader.jobs_done, 2)
            self.assertEqual(Listing.objects.get(retailer=read, product__slug="box-299").price, Decimal("349.00"))
            self.assertEqual(Listing.objects.filter(retailer=probed, availability="in_stock").count(), 20)
            self.assertTrue(ImportRun.objects.get(retailer=read).ok)
            self.assertEqual(DailyPageView.objects.get(key="busy").hits, len(counted))
