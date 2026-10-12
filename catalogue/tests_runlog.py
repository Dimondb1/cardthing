import logging
import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import crawl, housekeeping, runlog
from .importers import run_import
from .models import ImportRun, Retailer
from .testing import make_retailer

log = logging.getLogger("catalogue.importers")


class RunLogTests(TestCase):
    """What a failed read logged is kept on its run and shown in admin."""

    def setUp(self):
        self.shop = make_retailer("Harbour Games", source_type=Retailer.Source.SHOPIFY, source_url="https://harbour.example/")

    def failing_fetch(self, url, *args, **kwargs):
        log.info("Reading page 1 of %s", url)
        raise OSError("Could not fetch https://harbour.example/products.json?limit=250&page=1: HTTP Error 503")

    def test_a_failed_read_keeps_what_it_logged(self):
        run = run_import(self.shop, fetch=self.failing_fetch)
        run.refresh_from_db()
        self.assertTrue(run.error)
        self.assertIn("INFO Reading page 1 of https://harbour.example/", run.log)
        self.assertIn("ERROR Import for Harbour Games failed: Could not fetch", run.log)
        self.assertRegex(run.log.splitlines()[0], r"^\d\d:\d\d:\d\d INFO ")

    def test_a_clean_read_keeps_nothing(self):
        def fetch(url, *args, **kwargs):
            log.info("Reading %s", url)
            return b'{"products": []}'

        run = run_import(self.shop, fetch=fetch)
        run.refresh_from_db()
        self.assertEqual(run.error, "")
        self.assertEqual(run.log, "")

    def test_a_long_read_saves_as_it_goes_and_a_clean_end_clears_it(self):
        ticks = iter(range(0, 1000, 20))
        run = ImportRun.objects.create(retailer=self.shop)
        with runlog.capture(run, clock=lambda: next(ticks)) as captured:
            log.info("eBay: refreshing 40 known listings")
            self.assertEqual(ImportRun.objects.get(pk=run.pk).log, "")   # 20 seconds in: not yet
            log.info("eBay: 100 products checked, 12 matched so far")
            self.assertIn("100 products checked", ImportRun.objects.get(pk=run.pk).log)   # 40 seconds: saved
        self.assertTrue(captured.saved)
        self.assertFalse(captured.warned)

    def test_only_this_threads_lines_are_kept(self):
        run = ImportRun.objects.create(retailer=self.shop)
        with runlog.capture(run) as captured:
            other = threading.Thread(target=lambda: log.warning("Another shop's read: HTTP Error 500"))
            other.start()
            other.join()
            log.warning("This shop: timed out")
        self.assertIn("This shop: timed out", captured.text())
        self.assertNotIn("Another shop", captured.text())

    def test_keys_in_addresses_are_blanked(self):
        self.assertEqual(
            runlog.redact("Could not fetch https://productdata.awin.com/datafeed/download/apikey/abc123secret/fid/1/"
                          " and https://x.example/feed?token=zzz&page=2"),
            "Could not fetch https://productdata.awin.com/datafeed/download/apikey/…/fid/1/ and https://x.example/feed?token=…&page=2",
        )
        run = ImportRun.objects.create(retailer=self.shop)
        with runlog.capture(run) as captured:
            log.error("Could not fetch https://x.example/feed?apikey=hunter2")
        self.assertNotIn("hunter2", captured.text())
        self.assertNotIn("hunter2", ImportRun.objects.get(pk=run.pk).log)

    def test_the_log_is_kept_to_its_last_lines(self):
        run = ImportRun.objects.create(retailer=self.shop)
        with runlog.capture(run) as captured:
            for n in range(runlog.LINES + 50):
                log.warning("line %d %s", n, "x" * 1000)
        lines = captured.text().splitlines()
        self.assertEqual(len(lines), runlog.LINES)
        self.assertIn("line 50 ", lines[0])
        self.assertTrue(all(len(line) <= runlog.LINE_CHARS + 1 for line in lines))

    def test_a_crash_keeps_the_lines_and_adds_the_traceback(self):
        began = timezone.now() - timedelta(seconds=1)

        def crashing(*args, **kwargs):
            log.info("Reading the shop")
            raise KeyError("variants")

        with mock.patch("catalogue.importers._run_import", crashing):
            try:
                run_import(self.shop)
            except KeyError as exc:
                crash = exc
            else:
                self.fail("the crash should reach the worker")
        try:
            raise crash
        except KeyError as exc:
            crawl.crash_read(self.shop, began, exc)
        run = ImportRun.objects.get(retailer=self.shop)
        self.assertIn("INFO Reading the shop", run.log)
        self.assertIn("The read stopped on an unexpected error: KeyError", run.log)
        self.assertIn("Traceback (most recent call last)", run.log)
        self.assertLess(run.log.index("Reading the shop"), run.log.index("Traceback"))

    def test_a_marketplace_read_skipped_today_leaves_the_earlier_log_alone(self):
        ebay = make_retailer("eBay", slug="ebay", source_type=Retailer.Source.EBAY)
        earlier = ImportRun.objects.create(retailer=ebay, finished_at=timezone.now(), offers_found=5, log="kept")
        self.assertEqual(run_import(ebay).pk, earlier.pk)
        self.assertEqual(ImportRun.objects.get(pk=earlier.pk).log, "kept")

    def test_logs_are_cleared_after_a_week(self):
        old = ImportRun.objects.create(retailer=self.shop, started_at=timezone.now() - timedelta(days=8),
                                       finished_at=timezone.now(), error="x", log="old lines")
        new = ImportRun.objects.create(retailer=self.shop, finished_at=timezone.now(), error="y", log="new lines")
        housekeeping.prune_runs()
        self.assertEqual(ImportRun.objects.get(pk=old.pk).log, "")
        self.assertEqual(ImportRun.objects.get(pk=new.pk).log, "new lines")

    def test_crawl_health_links_each_failure_to_its_log(self):
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        run = run_import(self.shop, fetch=self.failing_fetch)
        self.shop.refresh_from_db()
        self.shop.read_failed(timezone.now(), 503, run.error)
        page = self.client.get(reverse("crawl")).content.decode()
        link = reverse("admin:catalogue_importrun_change", args=[run.pk])
        self.assertIn(f'<a href="{link}">See what happened</a>', page)
        detail = self.client.get(link).content.decode()
        self.assertIn("What happened", detail)
        self.assertIn("INFO Reading page 1 of https://harbour.example/", detail)
        self.assertIn('<pre style="white-space: pre-wrap;', detail)
