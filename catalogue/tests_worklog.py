"""The background reader's own log (catalogue/worklog.py), kept for the eBay health page's Log to copy box. The
owner, 16 Nov 2026: "Instead of guessing and hoping why aren't you producing an output log I could screenshot to you
or a log that would show in the admin area that I could copy and paste to you"."""

import fcntl
import logging
import os
from datetime import timedelta
from unittest import mock

from django.test import override_settings

from . import ebay, worker, worklog
from .models import Retailer
from .tests_ebay_search_only import KEYS
from .tests_worker import HeldExecutor, WorkerTestCase


class KeeperTests(WorkerTestCase):
    def setUp(self):
        super().setUp()
        self.keeper = worklog.install()
        self.addCleanup(worklog.remove, self.keeper)
        self.log = logging.getLogger("catalogue.worker")

    def test_warnings_errors_and_ebay_lines_are_kept_and_the_rest_is_not(self):
        self.log.info("Stockist finder: 3 products")            # an ordinary line: not kept
        self.log.info("eBay: checked 20 items: 18 in stock, 2 gone, 0 not reached")
        self.log.warning("Busy Shop: Looking for pre-orders: HTTP Error 429")
        logging.getLogger("web.warming").warning("Could not build the home page")   # any other logger's warning too
        try:
            raise ValueError("a bad row")
        except ValueError:
            self.log.exception("The planning pass failed")
        lines = worklog.read_lines()
        self.assertEqual(len(lines), 4 + 4)
        self.assertRegex(lines[0], r"^\d\d \w{3} \d\d:\d\d:\d\d INFO eBay: checked 20 items: 18 in stock")
        self.assertIn("WARNING Busy Shop: Looking for pre-orders", lines[1])
        self.assertIn("WARNING Could not build the home page", lines[2])
        self.assertIn("ERROR The planning pass failed", lines[3])
        # The end of the traceback is kept under it: where it failed and the error itself.
        self.assertTrue(all(line.startswith("    ") for line in lines[4:]))
        self.assertEqual(lines[-1], "    ValueError: a bad row")
        entries = worklog.entries()
        self.assertEqual([bad for bad, _lines in entries], [False, True, True, True])
        self.assertEqual(len(entries[-1][1]), 5)

    def test_the_file_is_trimmed_and_a_restart_goes_on_from_it(self):
        with mock.patch.object(worklog, "TRIM_AT", 30), mock.patch.object(worklog, "KEEP", 10):
            for n in range(29):
                self.log.warning("line %d", n)
            self.assertEqual(len(worklog.read_lines()), 29)
            self.log.warning("line 29")
            lines = worklog.read_lines()
            self.assertEqual(len(lines), 10)
            self.assertTrue(lines[-1].endswith("line 29"))
            # The reader restarted, as every update does: the lines from before are still there.
            worklog.remove(self.keeper)
            self.keeper = worklog.install()
            self.addCleanup(worklog.remove, self.keeper)
            self.log.warning("after the restart")
            self.assertEqual(len(worklog.read_lines()), 11)
            self.assertEqual(self.keeper.count, 11)

    @override_settings(RIPRAPTOR_EBAY_CERT_ID="PRD-cert-9f8e7d6c", RIPRAPTOR_PUSH_TOKEN="tok_1234567890")
    def test_no_key_and_no_email_address_is_kept(self):
        self.log.warning("eBay API 401 for cert PRD-cert-9f8e7d6c, token tok_1234567890, tell sam.lee@example.co.uk")
        [line] = worklog.read_lines()
        self.assertTrue(line.endswith("eBay API 401 for cert [hidden], token [hidden], tell [email]"))
        self.assertEqual(worklog.scrub("see /itm/123?campid=5 at 10:00"), "see /itm/123?campid=5 at 10:00")

    def test_a_log_that_cannot_be_written_never_stops_the_reader(self):
        with mock.patch.object(worklog, "path", side_effect=OSError("disk full")), \
                mock.patch.object(logging, "raiseExceptions", False):
            self.log.warning("still running")   # nothing raised
        self.assertEqual(worklog.read_lines(), [])


@KEYS
class WorkerLogTests(WorkerTestCase):
    def setUp(self):
        super().setUp()
        ebay.forget_live()
        self.addCleanup(ebay.forget_live)
        self.ebay = self.shop("eBay", source_type=Retailer.Source.EBAY, source_url="https://www.ebay.co.uk/")
        self.box = self.product("surging-box", views=3)
        self.row = self.listing(self.box, self.ebay, 120, price="82.00", url="https://www.ebay.co.uk/itm/123")

    def test_what_the_reader_planned_is_noted_for_the_log(self):
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=self.clock() - timedelta(hours=3))
        other = self.shop("Probe Shop")
        self.listing(self.product("hot-box", views=3), other, 30)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor)
        reader.queue = reader.plan(self.clock())
        plan = worklog.last_plan()
        self.assertEqual(plan["ebay"], {"running": "", "read": "due, with room to start"})
        self.assertEqual(plan["queue"], {"reads": 1, "reads_with_room": 1, "checks": 1, "pulses": 0, "other": 0})
        self.assertEqual((plan["running"], plan["lock"]["held_since"], plan["lock"]["refused"]), ([], "", ""))
        # The read under way: the next plan says eBay is running and the reader holds the lock.
        reader.dispatch(self.clock())
        self.clock.advance(minutes=2)
        reader.plan(self.clock())
        plan = worklog.last_plan()
        self.assertEqual(plan["ebay"]["running"], "Read eBay")
        self.assertIn({"label": "Read eBay", "minutes": 2}, plan["running"])
        self.assertTrue(plan["lock"]["held_since"])

    def test_reads_kept_waiting_for_the_import_lock_are_said_with_why(self):
        """What stopped eBay's checks on 16 Nov 2026 left no line anywhere. A wait past the lock's ordinary
        turn-taking is now a warning, with why and which read is next, and its end is a line too."""
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=self.clock() - timedelta(hours=3))
        held = os.open(self.import_lock, os.O_RDONLY | os.O_CREAT)
        self.addCleanup(os.close, held)
        fcntl.flock(held, fcntl.LOCK_EX)
        reader = self.make_worker(executor=HeldExecutor())
        reader.queue = reader.plan(self.clock())
        with self.assertNoLogs("catalogue.worker", "WARNING"):
            reader.dispatch(self.clock())   # a moment's wait is not worth a line
            for _turn in range(2):
                self.clock.advance(minutes=2)
                reader.running.clear()
                reader.dispatch(self.clock())
        reader.running.clear()
        self.clock.advance(minutes=2)
        reader.queue = reader.plan(self.clock())
        with self.assertLogs("catalogue.worker", "WARNING") as logged:
            reader.dispatch(self.clock())
        self.assertEqual(logged.records[0].getMessage(), "Reads have waited 6 minutes for the import lock: another "
                                                         "command holds it. Next in line: Read eBay")
        self.assertEqual(worklog.last_plan()["lock"]["refused"], "another command holds it")
        # Not said again a minute later, and said to be over once the lock is free.
        reader.running.clear()
        self.clock.advance(minutes=1)
        with self.assertNoLogs("catalogue.worker", "WARNING"):
            reader.dispatch(self.clock())
        fcntl.flock(held, fcntl.LOCK_UN)
        reader.running.clear()
        with self.assertLogs("catalogue.worker", "INFO") as logged:
            reader.dispatch(self.clock())
        self.assertIn("The import lock is free again after 7 minutes: reads go on",
                      [record.getMessage() for record in logged.records])
        # A refusal hours later is a new wait, not one that began this morning.
        reader.running.clear()
        reader.import_lock.release()
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reader.lock_refused_since = self.clock() - timedelta(hours=3)
        reader.lock_refused_last = self.clock() - timedelta(hours=3)
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=self.clock() - timedelta(minutes=1))
        reader.queue = reader.plan(self.clock())
        with self.assertNoLogs("catalogue.worker", "WARNING"):
            reader.dispatch(self.clock())
        self.assertEqual(reader.lock_refused_since, self.clock())

    def test_a_lock_file_that_cannot_be_opened_keeps_reads_waiting_and_checks_going(self):
        Retailer.objects.filter(pk=self.ebay.pk).update(next_read_at=self.clock() - timedelta(hours=3))
        self.listing(self.product("hot-box", views=3), self.shop("Probe Shop"), 30)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor)
        reader.queue = reader.plan(self.clock())
        with mock.patch.object(worker.os, "open", side_effect=PermissionError(13, "Permission denied")):
            reader.dispatch(self.clock())   # raised from here, this stopped every job being started
        self.assertEqual([task.kind for task in executor.jobs], [worker.PROBE])
        self.assertIn("its file cannot be opened", reader.import_lock.refused)

    def test_each_ebay_read_leaves_a_line(self):
        reader = self.make_worker()
        now = self.clock()
        # Its hourly read: this hour's share of the day's searches, and said so.
        from .tests_ebay import FakeApi

        read = worker.Task(worker.SHOP_READ, self.ebay.pk, Retailer.Source.EBAY, 1.0, "Read eBay")
        with mock.patch.object(ebay, "http", FakeApi([])), mock.patch.object(ebay, "PAUSE", 0), \
                self.assertLogs("catalogue.worker", "INFO") as logged:
            reader.run_job(worker.Job(read, now, now + worker.MARKETPLACE_READ_DEADLINE))
        self.assertRegex(logged.records[-1].getMessage(),
                         r"^eBay: this hour's search finished in 0 minutes: \d+ products searched, \d+ listings updated$")


class InstallTests(WorkerTestCase):
    @override_settings(RIPRAPTOR_WORKER_THREADS=2)
    def test_run_worker_keeps_the_log_from_its_first_line(self):
        from django.core.management import call_command
        from io import StringIO

        def keepers():
            return [handler for name in ("catalogue", "") for handler in logging.getLogger(name).handlers
                    if isinstance(handler, worklog.Keeper)]

        seen = []

        def run_once(reader):
            seen.extend(keepers())
            logging.getLogger("catalogue.worker").warning("eBay: a line from the reader")

        before = keepers()
        with mock.patch.object(worker, "WORKER_LOCK", str(self.folder / "worker.lock")), \
                mock.patch.object(worker.Worker, "run_once", run_once):
            call_command("run_worker", "--once", stdout=StringIO())
        # Kept while the reader ran, on the catalogue loggers and every other, and taken off when it stopped.
        self.assertEqual(len(seen), 2)
        self.assertTrue(worklog.read_lines()[-1].endswith("WARNING eBay: a line from the reader"))
        self.assertEqual(keepers(), before)
