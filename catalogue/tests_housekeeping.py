"""The database stays small: old import records are cleared, the update shrinks it before copying it, and the
copy makes room first and never fills the disk."""

import datetime
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from . import housekeeping, notify
from .importers import UNMATCHED_KEPT, unmatched_text
from .management.commands.backup_db import backup_name
from .models import ImportRun
from .testing import make_retailer
from .tests_db import file_database

LINES = "\n".join(f"Some board game {n} [no barcode] https://shop.example/products/{n}" for n in range(200))


def run(retailer, days_ago=0, **fields):
    started = timezone.now() - datetime.timedelta(days=days_ago)
    fields.setdefault("finished_at", started + datetime.timedelta(minutes=5))
    fields.setdefault("unmatched", LINES)
    return ImportRun.objects.create(retailer=retailer, started_at=started, **fields)


class PruneRunsTests(TestCase):
    def test_only_each_shops_latest_good_whole_read_keeps_its_list(self):
        shop, other = make_retailer("Shop"), make_retailer("Other")
        old_read = run(shop, days_ago=2)
        latest_read = run(shop, days_ago=1)
        pulse = run(shop, note="Pre-order pulse: pre-order")
        failed = run(shop, error="HTTP Error 500")
        others = run(other, days_ago=3)
        cleared, deleted = housekeeping.prune_runs()
        self.assertEqual((cleared, deleted), (3, 0))
        texts = {r.pk: r.unmatched for r in ImportRun.objects.all()}
        self.assertEqual(texts[latest_read.pk], LINES)
        self.assertEqual(texts[others.pk], LINES)
        for kept_empty in (old_read, pulse, failed):
            self.assertEqual(texts[kept_empty.pk], "")

    def test_runs_older_than_a_month_go_but_each_shops_latest_and_latest_good_stay(self):
        shop, quiet, ebay = make_retailer("Shop"), make_retailer("Quiet"), make_retailer("eBay", slug="ebay")
        gone = [run(shop, days_ago=40), run(shop, days_ago=35, error="timeout")]
        recent = run(shop, days_ago=2)
        only = run(quiet, days_ago=60)          # a shop not read for two months keeps its one record
        good = run(ebay, days_ago=45)           # the daily gate and the finder look at the latest good read
        later_failure = run(ebay, days_ago=40, error="429")
        housekeeping.prune_runs()
        left = set(ImportRun.objects.values_list("pk", flat=True))
        self.assertEqual(left, {recent.pk, only.pk, good.pk, later_failure.pk})
        self.assertFalse(left & {r.pk for r in gone})

    def test_tidy_all_clears_records_every_hour(self):
        shop = make_retailer("Shop")
        old = run(shop, days_ago=1)
        run(shop)
        out = StringIO()
        call_command("tidy_all", stdout=out)
        old.refresh_from_db()
        self.assertEqual(old.unmatched, "")
        self.assertIn("1 lists of unmatched products cleared", out.getvalue())

    def test_a_run_keeps_at_most_three_thousand_unmatched_lines(self):
        lines = [f"line {n}" for n in range(UNMATCHED_KEPT + 250)]
        text = unmatched_text(lines)
        self.assertEqual(text.count("\n"), UNMATCHED_KEPT)
        self.assertTrue(text.endswith("... and 250 more not listed"))
        self.assertEqual(unmatched_text(["a", "b"]), "a\nb")


class CompactTests(TestCase):
    def test_compact_db_gives_the_space_back_on_a_real_file(self):
        with file_database() as path:
            shop = make_retailer("Shop")
            big = "x" * 1024 * 1024
            for days in range(1, 6):
                run(shop, days_ago=days, unmatched=big)
            run(shop)
            before, _ = housekeeping.file_sizes()
            with mock.patch("catalogue.management.commands.compact_db.VACUUM_OVER", 1024 * 1024):
                out = StringIO()
                call_command("compact_db", stdout=out)
            self.assertLess(path.stat().st_size, before - 3 * 1024 * 1024)
            wal = path.with_name(path.name + "-wal")
            self.assertTrue(not wal.exists() or wal.stat().st_size == 0)
            self.assertIn("Database is now", out.getvalue())


class BackupRoomTests(TestCase):
    def test_unfinished_copies_go_first_and_older_named_copies_go_only_after_a_good_copy(self):
        with file_database() as path, tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            stamp = backup_name(path, timezone.now() - datetime.timedelta(days=2))
            cut_short = [folder / f"{stamp}.part", folder / f"{path.name}.20261009-1218"]
            older_style = [folder / f"{path.stem}-20261007-214811.sqlite3", folder / f"{path.stem}-20261007-214346.sqlite3-journal"]
            for file in cut_short + older_style:
                file.write_bytes(b"old")
            unrelated = folder / "notes.txt"
            unrelated.write_bytes(b"keep")
            out = StringIO()
            call_command("backup_db", dir=str(folder), stdout=out)
            self.assertEqual(len(list(folder.glob(f"{path.name}.*.bak"))), 1)
            for file in cut_short + older_style:
                self.assertFalse(file.exists(), file.name)
            self.assertTrue(unrelated.exists())
            self.assertIn("Removed an unfinished copy", out.getvalue())
            self.assertIn("Removed an older copy", out.getvalue())

    def test_no_room_means_nothing_is_written_and_older_copies_survive(self):
        with file_database() as path, tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            older = folder / f"{path.stem}-20261007-214811.sqlite3"
            older.write_bytes(b"the only good copy")
            full = mock.Mock(free=10 * 1024 * 1024, total=23 * 1024 ** 3, used=23 * 1024 ** 3)
            with mock.patch("catalogue.management.commands.backup_db.shutil.disk_usage", return_value=full):
                with self.assertRaises(CommandError) as raised:
                    call_command("backup_db", dir=str(folder), stdout=StringIO())
            self.assertIn("Not enough disk space", str(raised.exception))
            self.assertIn("Nothing was changed", str(raised.exception))
            self.assertEqual(list(folder.glob("*.bak")) + list(folder.glob("*.part")), [])
            self.assertTrue(older.exists())

    def test_the_oldest_copies_make_room_but_one_always_stays(self):
        with file_database() as path, tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            old = timezone.now() - datetime.timedelta(days=10)
            copies = [folder / backup_name(path, old + datetime.timedelta(days=n)) for n in range(3)]
            for copy in copies:
                copy.write_bytes(b"copy")
            frees = iter([0, 0, 10 ** 12, 10 ** 12])
            usage = lambda _folder: mock.Mock(free=next(frees, 10 ** 12))  # noqa: E731
            with mock.patch("catalogue.management.commands.backup_db.shutil.disk_usage", side_effect=usage):
                call_command("backup_db", dir=str(folder), stdout=StringIO())
            self.assertFalse(copies[0].exists())
            self.assertFalse(copies[1].exists())
            self.assertTrue(copies[2].exists())


class DiskWarningTests(TestCase):
    def test_the_owner_hears_once_when_the_disk_is_nearly_full(self):
        nearly = mock.Mock(total=100, used=90, free=10)
        with mock.patch("shutil.disk_usage", return_value=nearly), \
                mock.patch.object(notify, "crawl_problem", return_value=True) as told:
            self.assertEqual(notify.disk_nearly_full("/var/lib/ripraptor"), 90)
        told.assert_called_once()
        self.assertEqual(told.call_args[0][0], notify.DISK_FULL)
        fine = mock.Mock(total=100, used=58, free=42)
        with mock.patch("shutil.disk_usage", return_value=fine), \
                mock.patch.object(notify, "crawl_problem", return_value=True) as told:
            notify.disk_nearly_full("/var/lib/ripraptor")
        told.assert_not_called()

    def test_the_hourly_check_looks_at_the_disk(self):
        with file_database(), mock.patch.object(notify, "disk_nearly_full", return_value=40) as checked:
            with self.assertRaises(SystemExit):
                call_command("check_worker", stdout=StringIO())
        checked.assert_called_once()


class InstallOrderTests(TestCase):
    def test_install_shrinks_the_database_before_copying_it_and_rotates_the_log(self):
        text = (Path(__file__).resolve().parent.parent / "deploy" / "install.sh").read_text()
        self.assertLess(text.index("manage.py compact_db"), text.index("manage.py backup_db"))
        self.assertLess(text.index("manage.py backup_db"), text.index("manage.py migrate"))
        self.assertIn("/etc/logrotate.d/ripraptor", text)
        self.assertIn("maxsize 50M", text)
