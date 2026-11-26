"""The crawler controls (CrawlerSettings): the owner's brief of 26 Nov 2026. The server ran at about 90% while the shops
were read and 38% with reading paused; "website responsiveness must take priority over background scraping activity",
with the defaults: one job at once, new jobs held at 75%, again under 55%, measured over 60 seconds, every setting
changed in Admin with no restart, and the jobs running allowed to finish."""

import io
import json
import os
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import crawl, crawl_gate, response_times, worker
from .models import CrawlerSettings, Product, ReadPriority, Retailer, WorkerState
from .tests_worker import HeldExecutor, WorkerTestCase


class Meter:
    """Stands in for server_health.CpuMeter: the processors at whatever the test says."""

    def __init__(self, cpu=None):
        self.cpu = cpu

    def look(self, window):
        pass

    def average(self, window):
        return self.cpu


class SettingsTests(TestCase):
    def test_the_owners_defaults(self):
        controls = CrawlerSettings.current()
        self.assertIsNone(CrawlerSettings.objects.first())   # read without writing
        self.assertEqual((controls.max_jobs, controls.cpu_pause_percent, controls.cpu_resume_percent,
                          controls.cpu_window_seconds), (1, 75, 55, 60))
        self.assertEqual((controls.cpu_max_percent, controls.slow_seconds, controls.job_gap_seconds,
                          controls.read_at_least_every_minutes), (90, 2, 5, 60))

    def test_start_again_must_be_under_hold(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            CrawlerSettings(cpu_pause_percent=60, cpu_resume_percent=60).full_clean()
        CrawlerSettings(cpu_pause_percent=60, cpu_resume_percent=40).full_clean()

    def test_how_often_a_shop_is_read(self):
        shop = Retailer(name="Slow Shop", read_every_minutes=180)
        self.assertEqual(shop.every_minutes, 60)                 # every shop at least hourly, unless changed
        CrawlerSettings.objects.create(pk=1, read_at_least_every_minutes=120)
        self.assertEqual(shop.every_minutes, 120)
        CrawlerSettings.objects.filter(pk=1).update(read_at_least_every_minutes=0)
        self.assertEqual(shop.every_minutes, 180)                # each shop's own setting decides
        self.assertEqual(Retailer(name="Quick", read_every_minutes=20).every_minutes, 20)


class AdminTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(self.owner)
        self.change = reverse("admin:catalogue_crawlersettings_change", args=[1])

    def form(self, **changes):
        fields = {"max_jobs": 1, "job_gap_seconds": 5, "cpu_pause_percent": 75, "cpu_resume_percent": 55,
                  "cpu_window_seconds": 60, "cpu_max_percent": 90, "slow_seconds": 2, "read_at_least_every_minutes": 60}
        return {**fields, **changes}

    def test_the_list_opens_the_one_row(self):
        response = self.client.get(reverse("admin:catalogue_crawlersettings_changelist"))
        self.assertRedirects(response, self.change)
        page = self.client.get(self.change)
        self.assertContains(page, "Most jobs at once")
        self.assertContains(page, "Not running, so nothing is read")
        self.assertContains(page, ">Pause all</button>")
        self.assertContains(reverse("admin:index") and self.client.get(reverse("admin:index")), "Crawler settings")

    def test_a_change_is_saved_and_said_to_need_no_restart(self):
        response = self.client.post(self.change, self.form(max_jobs=2, cpu_pause_percent=70), follow=True)
        self.assertContains(response, "within 15 seconds, with no restart")
        saved = CrawlerSettings.objects.get(pk=1)
        self.assertEqual((saved.max_jobs, saved.cpu_pause_percent), (2, 70))

    def test_start_again_over_hold_is_refused(self):
        response = self.client.post(self.change, self.form(cpu_resume_percent=80))
        self.assertContains(response, "Make this lower than the figure new jobs are held at")
        self.assertFalse(CrawlerSettings.objects.filter(cpu_resume_percent=80).exists())

    def test_pause_all_and_resume_all(self):
        pause = reverse("admin:catalogue_crawlersettings_pause")
        self.assertEqual(self.client.get(pause).status_code, 405)
        self.client.post(pause, {"action": "pause_all"})
        self.assertTrue(crawl.all_paused())
        self.assertContains(self.client.get(self.change), ">Resume all</button>")
        self.client.post(pause, {"action": "resume_all"})
        self.assertFalse(crawl.all_paused())

    def test_an_editor_cannot(self):
        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        from guides.apps import EDITORS

        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.client.post(reverse("admin:catalogue_crawlersettings_pause"), {"action": "pause_all"})
        self.assertFalse(crawl.all_paused())
        self.assertNotEqual(self.client.get(self.change).status_code, 200)

    def test_now_says_what_the_reader_is_doing(self):
        from django.utils import timezone

        now = timezone.now()
        WorkerState.objects.create(pk=1, heartbeat_at=now, held_since=now - timedelta(minutes=3), cpu_now=82,
                                   slowed=False, running=[{"label": "Read Big Shop", "minutes": 4}])
        page = self.client.get(self.change)
        self.assertContains(page, "New jobs held since")
        self.assertContains(page, "82% busy over the last 60 seconds")
        self.assertContains(page, "Read Big Shop (4 min)")

    def test_priorities_in_the_shop_and_product_lists(self):
        from catalogue.testing import make_game, make_product, make_retailer, make_set

        make_retailer("Shop A")
        make_product(make_set(make_game()), name="Box", slug="box", product_type="booster_box")
        shops = self.client.get(reverse("admin:catalogue_retailer_changelist")).content.decode()
        self.assertIn("Reading priority", shops)
        products = self.client.get(reverse("admin:catalogue_product_changelist")).content.decode()
        self.assertIn("Stock checks: high priority", products)


class ReaderTestCase(WorkerTestCase):
    def setUp(self):
        super().setUp()
        CrawlerSettings.objects.all().delete()   # the owner's defaults, not the old limits WorkerTestCase gives

    def controls(self, **values):
        CrawlerSettings.objects.update_or_create(pk=1, defaults=values)


class LimitTests(ReaderTestCase):
    def test_one_job_at_a_time_by_default(self):
        self.shop("Shop A", due=True)
        self.shop("Shop B", due=True)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor)
        reader.queue = reader.plan(self.clock())
        self.assertEqual(reader.dispatch(self.clock()), 1)
        self.clock.advance(minutes=1)
        self.assertEqual(reader.dispatch(self.clock()), 0)   # still running: nothing else starts
        self.assertEqual(len(executor.jobs), 1)

    def test_a_change_works_within_seconds_with_no_restart(self):
        for name in ("Shop A", "Shop B", "Shop C"):
            self.shop(name, due=True)
        hot = self.product("hot-box", views=3)
        self.listing(hot, self.shop("Hot Shop"), 30)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor)
        reader.queue = reader.plan(self.clock())
        self.assertEqual(reader.dispatch(self.clock()), 1)
        self.controls(max_jobs=3, job_gap_seconds=0)
        self.clock.advance(seconds=5)
        self.assertEqual(reader.dispatch(self.clock()), 0)   # read again after SETTINGS_EVERY, not at once
        self.clock.advance(seconds=15)
        with self.assertLogs("catalogue.worker", level="INFO") as logs:
            started = reader.dispatch(self.clock())
        self.assertEqual(started, 2)                          # two more: a second read, then the check
        self.assertIn("Crawler settings changed: at most 3 jobs at once", "\n".join(logs.output))
        self.assertEqual(sorted(t.kind for t in executor.jobs), [worker.PROBE, worker.SHOP_READ, worker.SHOP_READ])

    def test_the_wait_between_jobs(self):
        self.controls(max_jobs=4, job_gap_seconds=30)
        for name in ("Shop A", "Shop B"):
            self.shop(name, due=True)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor)
        reader.queue = reader.plan(self.clock())
        self.assertEqual(reader.dispatch(self.clock()), 1)
        self.clock.advance(seconds=20)
        self.assertEqual(reader.dispatch(self.clock()), 0)
        self.clock.advance(seconds=10)
        self.assertEqual(reader.dispatch(self.clock()), 1)

    def test_with_one_job_reads_and_checks_take_turns(self):
        for name in ("Shop A", "Shop B"):
            self.shop(name, due=True)
        self.listing(self.product("hot-box", views=3), self.shop("Hot Shop"), 30)
        reader = self.make_worker(executor=HeldExecutor())
        reader.queue = reader.plan(self.clock())
        self.assertEqual([t.kind for t in reader.in_turn()][:2], [worker.SHOP_READ, worker.SHOP_READ])
        reader.last_kind = worker.SHOP_READ
        self.assertEqual(reader.in_turn()[0].kind, worker.PROBE)

    def test_a_command_reading_shops_counts_as_a_job(self):
        self.shop("Shop A", due=True)
        reader = self.make_worker(executor=HeldExecutor())
        reader.queue = reader.plan(self.clock())
        with crawl_gate.reading("delivery check", where=str(self.folder)):
            self.assertEqual(crawl_gate.running_elsewhere(str(self.folder)), ["delivery check"])
            self.assertEqual(reader.dispatch(self.clock()), 0)
        self.assertEqual(crawl_gate.running_elsewhere(str(self.folder)), [])
        self.assertEqual(reader.dispatch(self.clock()), 1)

    def test_a_note_left_by_a_command_that_died_is_ignored(self):
        note = self.folder / (crawl_gate.NOTE + "delivery check")
        note.write_text("999999999")
        self.assertEqual(crawl_gate.running_elsewhere(str(self.folder)), [])
        self.assertFalse(note.exists())


class HoldTests(ReaderTestCase):
    def test_busy_processors_hold_new_jobs_and_the_running_ones_finish(self):
        self.controls(max_jobs=2, job_gap_seconds=0)
        for name in ("Shop A", "Shop B", "Shop C"):
            self.shop(name, due=True)
        meter = Meter(40)
        executor = HeldExecutor()
        reader = self.make_worker(executor=executor, meter=meter)
        reader.tick(self.clock())
        self.assertEqual(len(executor.jobs), 1)               # one read, a slot kept for checks
        running = dict(reader.running)
        meter.cpu = 80
        self.clock.advance(seconds=5)
        with self.assertLogs("catalogue.worker", level="WARNING") as logs:
            reader.tick(self.clock())
        self.assertIn("no new jobs until under 55%; the 1 running finish first", logs.output[0])
        self.assertIsNotNone(reader.held_since)
        self.assertEqual(reader.running, running)               # the read in flight is left to finish
        # Held: nothing new starts and nothing is planned, however long it lasts and however a slot frees up.
        reader.running.clear()
        with mock.patch.object(reader, "plan", wraps=reader.plan) as plan:
            for _ in range(30):
                self.clock.advance(seconds=5)
                meter.cpu = 60                                  # under the hold, not yet under 55
                reader.tick(self.clock())
            plan.assert_not_called()
        self.assertEqual(len(executor.jobs), 1)
        state = WorkerState.objects.get(pk=1)
        self.assertIsNotNone(state.held_since)
        self.assertEqual(state.cpu_now, 60)
        # Under 55: planned afresh at once and started again.
        meter.cpu = 50
        self.clock.advance(seconds=5)
        reader.tick(self.clock())
        self.assertIsNone(reader.held_since)
        self.assertEqual(len(executor.jobs), 2)

    def test_no_meter_no_hold(self):
        reader = self.make_worker(executor=HeldExecutor())
        reader.watch_processors(self.clock())
        self.assertIsNone(reader.held_since)

    def test_very_busy_processors_slow_the_running_jobs(self):
        meter = Meter(95)
        reader = self.make_worker(meter=meter)
        reader.watch_processors(self.clock())
        self.assertTrue(reader.slowed)
        self.assertEqual(reader.slow_down(), 2)
        shop = self.shop("Shop A")
        before = self.clock()
        reader.politeness.wait_turn(shop)
        self.assertEqual((self.clock() - before).total_seconds(), 2)   # the two seconds more
        meter.cpu = 50
        reader.watch_processors(self.clock())
        self.assertFalse(reader.slowed)
        self.assertEqual(reader.slow_down(), 0)

    def test_the_heartbeat_says_what_is_running(self):
        self.shop("Shop A", due=True)
        reader = self.make_worker(executor=HeldExecutor(), meter=Meter(30))
        reader.tick(self.clock())
        reader.beat(self.clock())
        state = WorkerState.objects.get(pk=1)
        self.assertEqual([job["label"] for job in state.running], ["Read Shop A"])
        self.assertEqual(state.cpu_now, 30)
        self.assertIsNone(state.held_since)


class PriorityTests(ReaderTestCase):
    def test_high_priority_shops_are_read_first_and_low_last(self):
        self.shop("A Low", due=True, read_priority=ReadPriority.LOW)
        self.shop("B Normal", due=True)
        self.shop("C High", due=True, read_priority=ReadPriority.HIGH)
        reader = self.make_worker(executor=HeldExecutor())
        reads = [t.label for t in reader.plan(self.clock()) if t.kind == worker.SHOP_READ]
        self.assertEqual(reads, ["Read C High", "Read B Normal", "Read A Low"])

    def test_a_high_priority_product_is_checked_as_the_popular_are_and_a_low_one_never_on_its_own(self):
        shop = self.shop("Shop A")
        quiet = self.product("quiet-box")
        self.listing(quiet, shop, 30)
        reader = self.make_worker(executor=HeldExecutor())
        self.assertEqual([t for t in reader.plan(self.clock()) if t.kind == worker.PROBE], [])
        Product.objects.filter(pk=quiet.pk).update(check_priority=ReadPriority.HIGH)
        probes = [t for t in reader.plan(self.clock()) if t.kind == worker.PROBE]
        self.assertEqual((len(probes), probes[0].tier), (1, "hot"))
        popular = self.product("popular-box", views=3, check_priority=ReadPriority.LOW)
        self.listing(popular, self.shop("Shop B"), 30)
        probes = [t for t in reader.plan(self.clock()) if t.kind == worker.PROBE]
        self.assertEqual([t.retailer_id for t in probes], [shop.pk])

    def test_a_high_priority_shops_checks_go_first(self):
        low = self.shop("Low Shop", read_priority=ReadPriority.LOW)
        high = self.shop("High Shop", read_priority=ReadPriority.HIGH)
        for shop in (low, high):
            self.listing(self.product(f"box-{shop.slug}", views=3), shop, 30)
        reader = self.make_worker(executor=HeldExecutor())
        probes = [t.retailer_id for t in reader.plan(self.clock()) if t.kind == worker.PROBE]
        self.assertEqual(probes, [high.pk, low.pk])


class GateTests(TestCase):
    def test_paused_held_or_free(self):
        from django.utils import timezone

        self.assertEqual(crawl_gate.why_not(), "")
        state = WorkerState.objects.create(pk=1, heartbeat_at=timezone.now(), held_since=timezone.now())
        self.assertEqual(crawl_gate.why_not(), crawl_gate.HELD)
        WorkerState.objects.filter(pk=1).update(heartbeat_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(crawl_gate.why_not(), "")             # a reader that stopped holds nothing
        crawl.pause_all()
        self.assertEqual(crawl_gate.why_not(), crawl_gate.PAUSED)
        self.assertFalse(crawl_gate.may_read(WorkerState.objects.get(pk=state.pk)))

    def test_waiting_for_a_turn(self):
        from django.utils import timezone

        WorkerState.objects.create(pk=1, heartbeat_at=timezone.now(), held_since=timezone.now())
        clock, slept = [0.0], []

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds
            if clock[0] >= 60:
                WorkerState.objects.filter(pk=1).update(held_since=None)

        self.assertEqual(crawl_gate.wait_for_turn(600, sleep=sleep, clock=lambda: clock[0]), "")
        self.assertEqual(slept, [30, 30])
        WorkerState.objects.filter(pk=1).update(held_since=timezone.now())
        self.assertEqual(crawl_gate.wait_for_turn(clock[0] + 45, sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                                                  clock=lambda: clock[0]), crawl_gate.HELD)


class DeliveryCheckTests(TestCase):
    def test_pause_all_stops_it_before_a_shop(self):
        from catalogue.testing import make_retailer

        make_retailer("Shop A", website="https://shop-a.example")
        crawl.pause_all()
        out = io.StringIO()
        with mock.patch("catalogue.management.commands.check_delivery.check_delivery") as check:
            call_command("check_delivery", stdout=out)
        check.assert_not_called()
        self.assertIn("Pause all is on", out.getvalue())

    def test_it_waits_while_new_jobs_are_held(self):
        from django.utils import timezone

        from catalogue.management.commands import check_delivery
        from catalogue.testing import make_retailer

        make_retailer("Shop A", website="https://shop-a.example")
        WorkerState.objects.create(pk=1, heartbeat_at=timezone.now(), held_since=timezone.now())
        waited = []

        def sleep(seconds):
            waited.append(seconds)
            WorkerState.objects.filter(pk=1).update(held_since=None)

        with mock.patch.object(check_delivery.Command, "sleep", staticmethod(sleep)), \
                mock.patch("catalogue.management.commands.check_delivery.check_delivery", return_value=None) as check:
            call_command("check_delivery", stdout=io.StringIO())
        self.assertEqual(waited, [30])
        check.assert_called_once()


class ResponseTimeTests(TestCase):
    def setUp(self):
        import tempfile

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        moved = override_settings(RIPRAPTOR_CACHE_DIR=folder.name)
        moved.enable()
        self.addCleanup(moved.disable)
        fresh = mock.patch.object(response_times, "CURRENT", response_times.Minute())
        fresh.start()
        self.addCleanup(fresh.stop)

    def test_a_minute_is_written_when_the_next_begins_and_collected_a_minute_later(self):
        for ms in (100, 300, 1200):
            response_times.record("/products/box/", ms, now=60 * 100 + 5)
        response_times.record("/admin/", 5000, now=60 * 100 + 6)        # Admin is left out
        self.assertFalse(response_times.path().exists())               # not until the minute is over
        response_times.record("/", 50, now=60 * 101 + 1)
        line = json.loads(response_times.path().read_text())
        self.assertEqual(line, {"minute": 100, "pages": 3, "ms": 1600, "slowest": 1200, "slow": 1})
        self.assertEqual(response_times.collect()["pages"], 0)          # set aside now, read at the next
        self.assertEqual(response_times.collect(), {"pages": 3, "average": 533, "slowest": 1200, "slow": 1})
        self.assertEqual(response_times.collect()["pages"], 0)

    def test_the_middleware_times_a_page_and_never_admin(self):
        from web.middleware import ResponseTimeMiddleware

        timed = ResponseTimeMiddleware(lambda request: "page")
        with mock.patch.object(response_times, "record") as record:
            self.assertEqual(timed(RequestFactory().get("/products/box/")), "page")
        self.assertEqual(record.call_args.args[0], "/products/box/")
        self.assertFalse(response_times.counted("/admin/crawl/"))
        self.assertFalse(response_times.counted("/static/css/site.css"))
        self.assertTrue(response_times.counted("/black-friday/"))

    def test_a_folder_that_cannot_be_written_loses_the_minute_not_the_page(self):
        with mock.patch("builtins.open", side_effect=OSError("read only")):
            response_times.write({"minute": 1, "pages": 1, "ms": 1, "slowest": 1, "slow": 0})
        self.assertEqual(response_times.collect()["pages"], 0)

    def test_the_site_counts_its_pages(self):
        self.client.get(reverse("web:about"))
        self.assertGreaterEqual(response_times.CURRENT.pages, 1)


class StartTests(TestCase):
    def test_the_reader_measures_the_processors(self):
        source = (worker.__file__.rsplit("/", 1)[0] + "/management/commands/run_worker.py")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("meter=server_health.CpuMeter()", text)
        self.assertTrue(os.path.exists(source))
