"""SQLite shared by more than one writer: WAL mode, short tidy transactions and the retry on a visitor's writes."""

import sqlite3
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, OperationalError, connection, connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import TestCase
from django.utils import timezone

from . import ebay, insights
from .models import DailyPageView, Listing, OutboundClick, Product
from .pricing import record_check, retry_locked
from .testing import make_game, make_listing, make_product, make_retailer, make_set

def file_connection(path):
    """A connection to the SQLite file at ``path`` with the project's own DATABASES options."""
    config = dict(connections.settings[DEFAULT_DB_ALIAS])
    config["NAME"] = str(path)
    return DatabaseWrapper(config, DEFAULT_DB_ALIAS)


@contextmanager
def default_is_file(path):
    """Make the file at ``path`` this thread's default database, so that transaction.atomic() and every
    query in the code under test reach it, then put the test database back."""
    original = connections[DEFAULT_DB_ALIAS]
    replacement = file_connection(path)
    connections[DEFAULT_DB_ALIAS] = replacement
    try:
        yield
    finally:
        replacement.close()
        connections[DEFAULT_DB_ALIAS] = original


@contextmanager
def file_database():
    """A migrated temporary file database that every query in this thread goes to.

    The TestCase transaction stays open on the test database, untouched, so the code under test
    commits for real on the file; each other thread opens its own connection with ``open_in_thread``.
    """
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "db.sqlite3"
        with default_is_file(path):
            call_command("migrate", verbosity=0, interactive=False)
            yield path


def open_in_thread(path, work):
    """Run ``work`` in this thread on its own connection to the file database, closing it afterwards."""
    with default_is_file(path):
        work()


def rows_in_file(path, table):
    """Count rows straight from the file, past Django, to prove a write reached it."""
    with closing(sqlite3.connect(path)) as raw:
        return raw.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


class WalModeTests(TestCase):
    def test_file_database_opens_in_wal_mode(self):
        if connection.vendor != "sqlite":
            self.skipTest("WAL mode is a SQLite setting.")
        with tempfile.TemporaryDirectory() as folder:
            conn = file_connection(Path(folder) / "db.sqlite3")
            try:
                with conn.cursor() as cursor:
                    cursor.execute("PRAGMA journal_mode")
                    self.assertEqual(cursor.fetchone()[0], "wal")
                    cursor.execute("PRAGMA busy_timeout")
                    self.assertEqual(cursor.fetchone()[0], 20000)
            finally:
                conn.close()

    def test_two_threads_write_and_read_without_locked_errors(self):
        # The reader keeps one read transaction open until the writer is done. In WAL mode the writer
        # commits beside it; with the old rollback journal the writer cannot commit while a reader
        # holds the file, and fails "database is locked" after the busy timeout.
        with file_database() as path:
            pset = make_set(make_game())
            product = make_product(pset)
            listing = make_listing(product, make_retailer("Shop"))
            self.assertEqual(rows_in_file(path, "catalogue_listing"), 1)
            for i in range(5):
                make_listing(make_product(pset, name=f"Box {i}", slug=f"box-{i}"), make_retailer(f"Shop {i}"))
            errors, prices = [], []
            reading, written = threading.Event(), threading.Event()

            def write():
                reading.wait(timeout=30)
                try:
                    row = Listing.objects.get(pk=listing.pk)
                    for i in range(200):
                        availability = Listing.Availability.IN_STOCK if i % 2 else Listing.Availability.OUT_OF_STOCK
                        record_check(row, price=Decimal("50.00") + i, delivery_cost=Decimal("0.00"), availability=availability)
                finally:
                    written.set()

            def read():
                with connection.cursor() as cursor:
                    cursor.execute("BEGIN")
                    try:
                        for _ in range(25):
                            list(Product.objects.for_lists())
                        prices.append(Listing.objects.get(pk=listing.pk).price)
                        reading.set()
                        written.wait(timeout=60)
                        for _ in range(25):
                            list(Product.objects.for_lists())
                        prices.append(Listing.objects.get(pk=listing.pk).price)
                    finally:
                        reading.set()
                        cursor.execute("COMMIT")
                prices.append(Listing.objects.get(pk=listing.pk).price)

            def guarded(work):
                def run():
                    try:
                        open_in_thread(path, work)
                    except Exception as exc:  # noqa: BLE001 - any failure in a thread must fail the test
                        errors.append(exc)
                        reading.set()
                        written.set()
                return run

            threads = [threading.Thread(target=guarded(write)), threading.Thread(target=guarded(read))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
            self.assertEqual(errors, [])
            # The reader saw one snapshot throughout, then the writer's last price once it had finished.
            self.assertEqual(prices, [Decimal("50.00"), Decimal("50.00"), Decimal("249.00")])

    def test_tidy_listings_removes_misfits_while_another_process_writes(self):
        # More listings than one chunk of the walk (2000), so the read is still open when the first
        # listing is deleted, and a page count commits from another connection every few milliseconds.
        with file_database() as path:
            game = make_game()
            pset = make_set(game)
            shop = make_retailer("Shop")
            Product.objects.bulk_create([
                Product(game=game, product_set=pset, name=f"Thing {i} Elite Trainer Box", slug=f"thing-{i}",
                        product_type=Product.Type.ELITE_TRAINER_BOX)
                for i in range(2500)
            ])
            Listing.objects.bulk_create([
                Listing(product=product, retailer=shop, url=f"https://shop.example/p/yugioh-thing-{product.pk}-booster-box",
                        price=Decimal("50.00"), delivery_cost=Decimal("0.00"), last_checked=timezone.now(),
                        availability=Listing.Availability.IN_STOCK)
                for product in Product.objects.all()
            ])
            stop, errors, counted = threading.Event(), [], []

            def count_pages():
                while not stop.is_set():
                    insights.bump(DailyPageView, kind=DailyPageView.Kind.PRODUCT, key="busy")
                    counted.append(1)
                    time.sleep(0.005)

            def guarded():
                try:
                    open_in_thread(path, count_pages)
                except Exception as exc:  # noqa: BLE001 - any failure in a thread must fail the test
                    errors.append(exc)

            writer = threading.Thread(target=guarded)
            writer.start()
            try:
                with self.assertNoLogs("catalogue", "WARNING"):
                    call_command("tidy_listings", stdout=StringIO())
            finally:
                stop.set()
                writer.join(timeout=60)
            self.assertEqual(errors, [])
            self.assertTrue(counted)
            self.assertEqual(Listing.objects.count(), 0)


class RetryLockedTests(TestCase):
    def test_retry_locked_retries_three_times_then_raises(self):
        calls = mock.Mock(side_effect=OperationalError("database is locked"))
        with mock.patch("catalogue.pricing.time.sleep") as sleep:
            with self.assertRaises(OperationalError):
                retry_locked(calls)
        self.assertEqual(calls.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1])

    def test_retry_locked_returns_once_the_lock_clears(self):
        calls = mock.Mock(side_effect=[OperationalError("database is locked"), "done"])
        with mock.patch("catalogue.pricing.time.sleep") as sleep:
            self.assertEqual(retry_locked(calls), "done")
        self.assertEqual(sleep.call_count, 1)

    def test_retry_locked_reraises_other_errors(self):
        calls = mock.Mock(side_effect=OperationalError("no such table: nothing"))
        with mock.patch("catalogue.pricing.time.sleep") as sleep:
            with self.assertRaises(OperationalError):
                retry_locked(calls)
        self.assertEqual(calls.call_count, 1)
        sleep.assert_not_called()

    def test_retry_locked_does_not_retry_a_lock_that_outlasted_the_busy_wait(self):
        calls = mock.Mock(side_effect=OperationalError("database is locked"))
        # The first try reports the lock 20 seconds after it started: SQLite already waited.
        with mock.patch("catalogue.pricing.time.monotonic", side_effect=[0.0, 20.0]), \
                mock.patch("catalogue.pricing.time.sleep") as sleep:
            with self.assertRaises(OperationalError):
                retry_locked(calls)
        self.assertEqual(calls.call_count, 1)
        sleep.assert_not_called()

    def test_a_locked_database_drops_the_count_and_still_serves_the_visitor(self):
        # Another process holds the write lock throughout. The busy wait is cut to 0.3 s here; each
        # count waits it once, is logged and dropped, and the visitor still goes to the shop.
        with file_database() as path:
            listing = make_listing(make_product(make_set(make_game())), make_retailer("Shop"))
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA busy_timeout = 300")
            # Scaled with the busy wait: a lock reported after 0.3 s here is one that waited in full.
            quick = mock.patch("catalogue.pricing.LOCK_QUICK_SECONDS", 0.1)
            holder = sqlite3.connect(path, isolation_level=None)
            try:
                holder.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                with quick, self.assertLogs("catalogue", "WARNING") as logs:
                    response = self.client.get(f"/go/{listing.pk}/", HTTP_USER_AGENT="Mozilla/5.0 (iPhone) Safari/605.1")
                    insights.bump(DailyPageView, kind=DailyPageView.Kind.PRODUCT, key="busy")
                elapsed = time.monotonic() - started
            finally:
                holder.execute("ROLLBACK")
                holder.close()
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(logs.records), 2)
            # Two busy waits of 0.3 s, no retries on top (a retry would add at least 0.5 s and 0.3 s more).
            self.assertLess(elapsed, 1.5)
            self.assertEqual(OutboundClick.objects.count(), 0)
            self.assertEqual(DailyPageView.objects.count(), 0)

    def test_a_click_is_counted_after_a_locked_write(self):
        listing = make_listing(make_product(make_set(make_game())), make_retailer("Shop"))
        real_create = OutboundClick.objects.create
        attempts = []

        def flaky(**kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationalError("database is locked")
            return real_create(**kwargs)

        with mock.patch("catalogue.pricing.time.sleep"), mock.patch.object(OutboundClick.objects, "create", flaky):
            response = self.client.get(f"/go/{listing.pk}/", HTTP_USER_AGENT="Mozilla/5.0 (iPhone) Safari/605.1")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OutboundClick.objects.count(), 1)


class ShortTidyTransactionTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.shop_a, self.shop_b = make_retailer("Shop A"), make_retailer("Shop B")

    def duplicates(self, name, slug):
        keep = make_product(self.set, name=name, slug=slug, product_type="booster_box")
        dupe = make_product(self.set, name=f"{name} Box", slug=f"{slug}-2", product_type="booster_box")
        make_listing(keep, self.shop_a)
        make_listing(keep, self.shop_b)
        make_listing(dupe, self.shop_b)
        return keep, dupe

    def test_tidy_all_dry_run_writes_nothing(self):
        keep, dupe = self.duplicates("Alpha Booster Box", "alpha")
        orphan = make_product(self.set, name="Nobody Sells This Booster Box", slug="orphan")
        Product.objects.filter(pk=orphan.pk).update(image="")
        wrong = make_listing(
            make_product(self.set, name="Zendikar Rising Set Booster Display", slug="zendikar", product_type="booster_box"),
            make_retailer("Card Empire"), url="https://www.cardempire.example/products/zendikar-rising-set-booster-display-japanese",
        )
        untitled = make_listing(
            make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", slug="pre-etb"),
            make_retailer("eBay", source_type="ebay"), url="https://www.ebay.co.uk/itm/123456789012", title="",
        )
        before = (Product.objects.count(), Listing.objects.count())
        out = StringIO()
        requests = []

        def answer(url, headers):
            requests.append(url)
            return {"items": [{"itemId": "v1|123456789012|0", "title": "Prismatic Evolutions Elite Trainer Box"}]}

        # eBay is set up and would answer: a dry run still asks it nothing and saves no title.
        with mock.patch.object(ebay, "credentials", return_value=("app", "cert", "campaign")), \
                mock.patch.object(ebay, "access_token", return_value="token"), mock.patch.object(ebay, "http", answer), \
                mock.patch.object(ebay, "item_id_of", return_value="v1|123456789012|0"), mock.patch.object(ebay, "PAUSE", 0):
            call_command("tidy_all", "--dry-run", stdout=out)
        self.assertEqual(requests, [])
        self.assertEqual(Listing.objects.get(pk=untitled.pk).title, "")
        self.assertIn("eBay titles not fetched in a dry run.", out.getvalue())
        self.assertIn("would remove", out.getvalue())
        self.assertIn("would keep", out.getvalue())
        self.assertEqual((Product.objects.count(), Listing.objects.count()), before)
        self.assertEqual(Product.objects.filter(pk__in=[dupe.pk, orphan.pk]).count(), 2)
        self.assertTrue(Listing.objects.filter(pk=wrong.pk).exists())
        call_command("tidy_all", stdout=StringIO())
        self.assertFalse(Product.objects.filter(pk__in=[dupe.pk, orphan.pk]).exists())
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())

    def test_merge_duplicates_commits_per_group(self):
        from .management.commands import merge_duplicates as command

        _first_keep, first_dupe = self.duplicates("Alpha Booster Box", "alpha")
        _second_keep, second_dupe = self.duplicates("Beta Booster Box", "beta")
        real_merge = command.merge
        seen = []

        def merge_then_fail(keep, others):
            seen.append(keep.name)
            if len(seen) > 1:
                raise RuntimeError("the shop went away mid-merge")
            return real_merge(keep, others)

        with mock.patch.object(command, "merge", merge_then_fail):
            with self.assertRaises(RuntimeError):
                call_command("merge_duplicates", stdout=StringIO())
        self.assertEqual(seen, ["Alpha Booster Box", "Beta Booster Box"])
        self.assertFalse(Product.objects.filter(pk=first_dupe.pk).exists())
        self.assertTrue(Product.objects.filter(pk=second_dupe.pk).exists())

    def test_tidy_catalogue_dry_run_reports_without_deleting(self):
        orphan = make_product(self.set, name="Nobody Sells This Booster Box", slug="orphan")
        Product.objects.filter(pk=orphan.pk).update(image="")
        out = StringIO()
        call_command("tidy_catalogue", "--dry-run", stdout=out)
        self.assertIn("would remove (no shop lists it)", out.getvalue())
        self.assertIn("1 removed", out.getvalue())
        self.assertTrue(Product.objects.filter(pk=orphan.pk).exists())

    def test_tidy_catalogue_keeps_a_product_that_was_merged_into_during_the_run(self):
        # "Alpha  Booster Box" tidies to the name of a later product that has no listings of its own.
        # Its listing moves there, and the later product must not then be removed as unlisted.
        spaced = make_product(self.set, name="Alpha  Booster Box", slug="alpha-1", product_type="booster_box")
        listing = make_listing(spaced, self.shop_a)
        target = make_product(self.set, name="Alpha Booster Box", slug="alpha-2", product_type="booster_box")
        Product.objects.filter(pk__in=[spaced.pk, target.pk]).update(image="")
        out = StringIO()
        call_command("tidy_catalogue", stdout=out)
        self.assertIn("merged: Alpha  Booster Box -> Alpha Booster Box", out.getvalue())
        self.assertNotIn("removed (no shop lists it): Alpha Booster Box", out.getvalue())
        self.assertFalse(Product.objects.filter(pk=spaced.pk).exists())
        self.assertEqual(Listing.objects.get(pk=listing.pk).product_id, target.pk)
