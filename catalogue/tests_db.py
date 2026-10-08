"""SQLite shared by more than one writer: WAL mode, short tidy transactions and the retry on a visitor's writes."""

import tempfile
import threading
from contextlib import contextmanager
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.db import OperationalError, connection, connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import TestCase, override_settings

from .models import Listing, Product
from .pricing import record_check, retry_locked
from .testing import make_game, make_listing, make_product, make_retailer, make_set

FILE_ALIAS = "walfile"


def file_connection(path):
    """A connection to the SQLite file at ``path`` with the project's own DATABASES options."""
    config = dict(connection.settings_dict)
    config["NAME"] = str(path)
    return DatabaseWrapper(config, FILE_ALIAS)


class FileRouter:
    """Send every query to the temporary file database while a test uses one."""

    def db_for_read(self, model, **hints):
        return FILE_ALIAS

    def db_for_write(self, model, **hints):
        return FILE_ALIAS

    def allow_relation(self, obj1, obj2, **hints):
        return True


@contextmanager
def file_database():
    """A migrated temporary file database that every query in this thread goes to.

    The connection is not in settings.DATABASES, so the test runner neither creates a test copy of it
    nor blocks it; each thread opens its own connection with ``open_in_thread``.
    """
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "db.sqlite3"
        connections[FILE_ALIAS] = file_connection(path)
        try:
            with override_settings(DATABASE_ROUTERS=[FileRouter()]):
                call_command("migrate", database=FILE_ALIAS, verbosity=0, interactive=False)
                yield path
        finally:
            connections[FILE_ALIAS].close()
            del connections[FILE_ALIAS]


def open_in_thread(path, work):
    """Run ``work`` in this thread on its own connection to the file database, closing it afterwards."""
    connections[FILE_ALIAS] = file_connection(path)
    try:
        work()
    finally:
        connections[FILE_ALIAS].close()
        del connections[FILE_ALIAS]


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
        with file_database() as path:
            pset = make_set(make_game())
            product = make_product(pset)
            listing = make_listing(product, make_retailer("Shop"))
            self.assertEqual(listing._state.db, FILE_ALIAS)
            for i in range(5):
                make_listing(make_product(pset, name=f"Box {i}", slug=f"box-{i}"), make_retailer(f"Shop {i}"))
            errors = []

            def write():
                row = Listing.objects.get(pk=listing.pk)
                for i in range(200):
                    availability = Listing.Availability.IN_STOCK if i % 2 else Listing.Availability.OUT_OF_STOCK
                    record_check(row, price=Decimal("50.00") + i, delivery_cost=Decimal("0.00"), availability=availability)

            def read():
                for _ in range(50):
                    list(Product.objects.for_lists())

            def guarded(work):
                def run():
                    try:
                        open_in_thread(path, work)
                    except Exception as exc:  # noqa: BLE001 - any failure in a thread must fail the test
                        errors.append(exc)
                return run

            threads = [threading.Thread(target=guarded(write)), threading.Thread(target=guarded(read))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
            self.assertEqual(errors, [])
            self.assertEqual(Listing.objects.get(pk=listing.pk).price, Decimal("249.00"))


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

    def test_a_click_is_counted_after_a_locked_write(self):
        from .models import OutboundClick

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
        before = (Product.objects.count(), Listing.objects.count())
        out = StringIO()
        call_command("tidy_all", "--dry-run", stdout=out)
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
