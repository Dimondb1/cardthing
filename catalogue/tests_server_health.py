"""Server health (catalogue/server_health.py, web/server_health.py): the processors, memory and disk sampled each
minute by the background reader, an Admin page, a notice when the processors stay busy, and the reader easing off.
The owner, 25 Nov 2026: "the server is running at 99% cpu how do we ensure that we monitor this from the ripraptor
admin"."""

from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import server_health, worker
from catalogue.models import ServerSample
from catalogue.server_health import JOBS, OTHER, READER, WEB_SERVER, WEBSITE, Reading, Sampler

STAT = "cpu  {user} 0 {system} {idle} 0 0 0 0 0 0\ncpu0 1 2 3 4\n"
MEMINFO = "MemTotal:        2000000 kB\nMemFree:          100000 kB\nMemAvailable:     500000 kB\n"


def proc_stat(pid, name, ticks):
    """A /proc/<pid>/stat line with ``ticks`` of user time and none of system time."""
    rest = ["S"] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 30
    return f"{pid} ({name}) " + " ".join(rest)


class FakeProc:
    """Stands in for /proc: the processors' totals and each process's ticks, moved on by the test."""

    def __init__(self):
        self.busy, self.idle = 1000, 9000
        self.procs = {1: (WEBSITE, 100), 2: (READER, 500)}

    def read(self, path):
        if path == "/proc/stat":
            return STAT.format(user=self.busy, system=0, idle=self.idle)
        if path == "/proc/meminfo":
            return MEMINFO
        if path == "/proc/loadavg":
            return "1.50 1.20 1.00 2/300 999"
        return None

    def sampler(self):
        return Sampler(read=self.read, procs=lambda: dict(self.procs), cpus=lambda: 2, disk=lambda: 41)


class ReadingTests(TestCase):
    def test_reading_linux_files(self):
        self.assertEqual(server_health.cpu_times("cpu  10 5 20 60 5 0 0 0 0 0\n"), (35, 100))
        # A name with spaces and brackets is read past.
        line = proc_stat(7, "python (manage) x", 42)
        self.assertEqual(server_health.process_ticks(line), 42)
        self.assertIsNone(server_health.process_ticks("broken"))
        self.assertEqual(server_health.memory_mb(MEMINFO), (1465, 1953))
        self.assertEqual(server_health.load_average("0.42 0.3 0.2 1/2 3"), 0.42)

    def test_programs_by_command_line(self):
        program = server_health.program_for
        self.assertEqual(program(["/srv/ripraptor/.venv/bin/python", "-m", "gunicorn", "ripraptor.wsgi"]), WEBSITE)
        self.assertEqual(program([".venv/bin/python", "manage.py", "run_worker"]), READER)
        self.assertEqual(program([".venv/bin/python", "manage.py", "refresh_bands"]), JOBS)
        self.assertEqual(program(["/usr/bin/caddy", "run"]), WEB_SERVER)
        self.assertEqual(program(["/usr/sbin/sshd"]), OTHER)
        self.assertEqual(program([]), OTHER)

    def test_reading_this_server(self):
        # On the machine the tests run on: a sample a moment after the first, within range.
        sampler = Sampler()
        self.assertIsNone(sampler.take())
        reading = sampler.take()
        if reading is not None:   # a clock that did not move between the two gives none
            self.assertTrue(0 <= reading.cpu <= 100)
            self.assertGreaterEqual(reading.cpus, 1)
            self.assertGreater(reading.memory_total_mb, 0)


class SamplerTests(TestCase):
    def test_the_share_busy_and_by_program_between_two_looks(self):
        proc = FakeProc()
        sampler = proc.sampler()
        self.assertIsNone(sampler.take())   # one look says nothing about a share
        proc.busy += 900
        proc.idle += 100
        proc.procs = {1: (WEBSITE, 300), 2: (READER, 1100), 3: (JOBS, 50)}   # 3 started since
        reading = sampler.take()
        self.assertEqual(reading.cpu, 90.0)
        self.assertEqual(reading.programs[WEBSITE], 20.0)
        self.assertEqual(reading.programs[READER], 60.0)
        self.assertEqual(reading.programs[JOBS], 5.0)
        self.assertEqual((reading.cpus, reading.load, reading.disk_used_percent), (2, 1.5, 41))
        self.assertEqual((reading.memory_used_mb, reading.memory_total_mb), (1465, 1953))

    def test_no_time_passed_gives_nothing(self):
        proc = FakeProc()
        sampler = proc.sampler()
        sampler.take()
        self.assertIsNone(sampler.take())

    def test_the_meter_averages_over_its_window(self):
        """The reader's look every few seconds (Crawler settings' window): the busy share between the first look in the
        window and the last."""
        seconds, stat = [0.0], ["cpu  0 0 0 0 0 0 0 0 0 0\n"]
        meter = server_health.CpuMeter(read=lambda path: stat[0], clock=lambda: seconds[0])

        def look(after, busy, idle, window=60):
            seconds[0] += after
            user, rest = [int(part) for part in stat[0].split()[1:3]], [int(part) for part in stat[0].split()[4:5]]
            stat[0] = f"cpu  {user[0] + busy} 0 0 {rest[0] + idle} 0 0 0 0 0 0\n"
            meter.look(window)
            return meter.average(window)

        self.assertIsNone(look(0, 0, 0))
        self.assertIsNone(look(5, 50, 50))          # five seconds is too few to go on
        self.assertIsNone(look(5, 50, 50))
        self.assertAlmostEqual(look(5, 50, 50), 50)  # fifteen seconds: half busy
        for _ in range(12):
            look(5, 90, 10)                          # a minute at 90%
        self.assertAlmostEqual(meter.average(60), 90)
        self.assertLessEqual(len(meter.looks), 14)   # only the looks in the window are kept
        self.assertAlmostEqual(look(5, 10, 90, window=10), 50)   # a shorter window sees the last ten seconds only

    def test_the_meter_gives_nothing_without_proc(self):
        meter = server_health.CpuMeter(read=lambda path: None, clock=lambda: 0)
        meter.look(60)
        self.assertIsNone(meter.average(60))


def sample(at, cpu, **programs):
    return ServerSample.objects.create(at=at, cpu=cpu, cpus=2, load=1.0, memory_used_mb=900, memory_total_mb=1953,
                                       disk_used_percent=40, programs=programs or {READER: cpu * 0.7, WEBSITE: cpu * 0.2})


class BusyTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def minutes(self, cpus, start=0):
        """A sample a minute, oldest first, ending ``start`` minutes ago."""
        for i, cpu in enumerate(reversed(cpus)):
            sample(self.now - timedelta(minutes=start + i), cpu)

    def test_busy_after_fifteen_minutes_at_ninety_or_more(self):
        self.minutes([40] * 5 + [95] * 20)
        busy = server_health.busy_now(self.now)
        self.assertEqual(busy.minutes, 19)
        self.assertEqual(busy.cpu, 95)

    def test_one_calm_minute_is_not_busy(self):
        self.minutes([95] * 10 + [60] + [95] * 5)
        self.assertIsNone(server_health.busy_now(self.now))

    def test_a_reader_that_just_started_is_not_busy(self):
        self.minutes([99] * 4)
        self.assertIsNone(server_health.busy_now(self.now))

    def test_the_owner_is_told_with_what_is_using_them(self):
        self.minutes([97] * 16)
        with mock.patch("catalogue.notify.owner", return_value=True) as owner:
            self.assertTrue(server_health.notice_busy(self.now))
        subject, title, path, detail = owner.call_args.args[:4]
        self.assertEqual((subject, path), (server_health.SERVER_BUSY, "/admin/server/"))
        self.assertIn("at least 90% busy for 15 minutes", detail)
        self.assertIn("background reader 68%", detail)

    def test_not_told_when_calm(self):
        self.minutes([30] * 16)
        with mock.patch("catalogue.notify.owner") as owner:
            self.assertFalse(server_health.notice_busy(self.now))
        owner.assert_not_called()

    def test_old_samples_go(self):
        sample(self.now - timedelta(days=8), 50)
        sample(self.now - timedelta(days=6), 50)
        self.assertEqual(server_health.prune(self.now), 1)
        self.assertEqual(ServerSample.objects.count(), 1)


class FakeSampler:
    def __init__(self, cpus):
        self.cpus = list(cpus)

    def take(self):
        cpu = self.cpus.pop(0)
        return Reading(cpu=cpu, cpus=2, load=2.0, memory_used_mb=900, memory_total_mb=1953, disk_used_percent=40,
                       programs={READER: cpu})


class WorkerTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def reader(self, sampler):
        return worker.Worker(clock=lambda: self.now, sleep=lambda seconds: None, sampler=sampler)

    def test_the_reader_samples_once_a_minute_with_what_it_was_doing(self):
        reader = self.reader(FakeSampler([50, 92]))
        reader.keep_server_sampled(self.now)
        reader.keep_server_sampled(self.now + timedelta(seconds=30))   # not a minute yet
        self.assertEqual(ServerSample.objects.count(), 1)
        first = ServerSample.objects.get()
        self.assertEqual((first.held, first.jobs, first.shop_requests, first.pages), (False, 0, 0, 0))
        reader.held_since = self.now
        reader.running[1] = worker.Job(worker.Task(worker.SHOP_READ, 1, "shopify", 1, "Read"), self.now, self.now)
        reader.keep_server_sampled(self.now + timedelta(minutes=1))
        latest = ServerSample.objects.order_by("-at").first()
        self.assertEqual((latest.cpu, latest.held, latest.jobs), (92, True, 1))
        self.assertEqual(list(ServerSample.objects.order_by("at").values_list("cpu", flat=True)), [50, 92])

    def test_a_failing_sample_never_stops_the_reader(self):
        broken = mock.Mock()
        broken.take.side_effect = OSError("no /proc")
        reader = self.reader(broken)
        with self.assertLogs("catalogue.worker", level="ERROR"):
            reader.keep_server_sampled(self.now)
        self.assertEqual(ServerSample.objects.count(), 0)

    def test_without_a_sampler_nothing_is_sampled(self):
        reader = self.reader(None)
        reader.keep_server_sampled(self.now)
        self.assertEqual(ServerSample.objects.count(), 0)

    def test_the_service_gives_the_website_the_processors_first(self):
        unit = (Path(__file__).resolve().parent.parent / "deploy" / "ripraptor-worker.service").read_text()
        self.assertIn("CPUWeight=20", unit)
        self.assertIn("Nice=10", unit)


class PageTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = get_user_model().objects.create_user("owner", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.url = reverse("server_health")

    def test_only_the_owner_and_staff(self):
        self.assertEqual(self.url, "/admin/server/")
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        from guides.apps import EDITORS

        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_before_any_sample(self):
        response = self.client.get(self.url)
        self.assertContains(response, "No samples yet")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_calm(self):
        for i in range(30):
            sample(self.now - timedelta(minutes=i), 35)
        response = self.client.get(self.url)
        self.assertContains(response, "Fine: processors 35% busy")
        self.assertContains(response, "Background reader (reading the shops)")
        self.assertContains(response, '<polyline class="line"')
        self.assertContains(response, "Each hour as a table")
        self.assertContains(response, "Log to copy")

    def test_busy_says_so_here_and_on_admin_home(self):
        for i in range(20):
            sample(self.now - timedelta(minutes=i), 96)
        ServerSample.objects.update(held=True)
        response = self.client.get(self.url)
        self.assertContains(response, "Busy: processors at least 90% for")
        self.assertContains(response, "starts no new job while they are over 75%")
        self.assertContains(response, '<rect class="held"')
        home = self.client.get(reverse("admin:index"))
        self.assertContains(home, "Server busy: processors 96% for")
        self.assertContains(home, f'href="{self.url}"')

    def test_admin_home_links_here_when_calm_and_says_nothing(self):
        sample(self.now, 20)
        home = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn(f'href="{self.url}"', home)
        self.assertNotIn("Server busy", home)

    def test_no_sample_for_long_means_the_reader_is_not_running(self):
        sample(self.now - timedelta(minutes=30), 99)
        self.assertContains(self.client.get(self.url), "No sample since")

    def test_queries_do_not_grow_with_the_samples(self):
        for i in range(10):
            sample(self.now - timedelta(minutes=i), 40)
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url)
        for i in range(10, 600):
            sample(self.now - timedelta(minutes=i), 40)
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertEqual(len(many), len(few))
