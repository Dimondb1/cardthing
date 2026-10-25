"""Operational hygiene: database backups, the nightly checkpoint, runs cut short and the cron timeouts."""

import datetime
import fcntl
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from . import ebay, insights
from .importers import STOPPED, close_abandoned_runs, run_import
from .management.commands import snapshot_daily_prices
from .management.commands.backup_db import backup_name
from .models import ImportRun, Retailer
from .testing import make_game, make_product, make_retailer, make_set
from .tests_db import file_database, rows_in_file
from .tests_ebay import KEYS, FakeApi, item

DEPLOY = Path(settings.BASE_DIR) / "deploy"
LOG = " >> /var/log/ripraptor-import.log 2>&1"
# What each file puts before every command: install.sh with its $DIR, deploy/crontab with the path.
PREFIX = re.compile(r"^cd (\$DIR|/srv/ripraptor) && set -a && \. \./\.env && set \+a && ")
SCHEDULE = re.compile(r"^((?:\S+\s+){5})(.*)$")
# Each command's budget plus five minutes, in seconds.
TIMEOUTS = {
    "import_prices": 3300,
    "tidy_all": 1500,
    "watch_stock": 540,
    "send_stock_alerts": 540,
    "send_drop_alerts": 540,
    "send_search_alerts": 540,
    "post_discord": 300,
    "snapshot_daily_prices": 1800,
    "check_delivery": 3000,
    "fetch_geoip": 1800,
    "check_worker": 600,
    "backup_db": 1800,
    "judge_checks": 900,
    "check_set_retail": 1200,
    "tidy_referrals": 600,
    "warm_pages": 240,
    "import_cardmarket": 900,
    "match_cardmarket": 840,
}


def cron_lines(text):
    """(schedule, command) for every cron line, with the per-file prefix and the log redirect removed."""
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or "=" in raw.split(" ", 1)[0]:
            continue
        found = SCHEDULE.match(raw)
        schedule, command = " ".join(found.group(1).split()), found.group(2)
        lines.append((schedule, PREFIX.sub("", command).replace(LOG, "")))
    return lines


def install_block():
    """The crontab install.sh pipes to crontab, as written in the script."""
    text = (DEPLOY / "install.sh").read_text()
    start = text.index('echo "PYTHONUNBUFFERED=1')
    return text[start + len('echo "'):text.index('" | crontab', start)]


def install_cron():
    return cron_lines(install_block())


def file_cron():
    return cron_lines((DEPLOY / "crontab").read_text())


class WarmCronTests(TestCase):
    def test_the_page_lists_are_built_every_five_minutes_on_their_own_lock_when_due(self):
        lines = [(schedule, command) for schedule, command in install_cron() if "warm_pages" in command]
        self.assertEqual(len(lines), 1)
        schedule, command = lines[0]
        self.assertEqual(schedule, "1-59/5 * * * *")
        self.assertIn("flock -n /tmp/ripraptor-warm.lock ", command)
        self.assertTrue(command.endswith("manage.py warm_pages --due"), command)


class InstallStepTests(TestCase):
    def test_the_installer_loads_the_hand_added_products_after_the_shops(self):
        text = (DEPLOY / "install.sh").read_text()
        shops = text.index(".venv/bin/python manage.py setup_shops\n")
        loaded = text.index(".venv/bin/python manage.py import_products catalogue/data/union_arena.csv")
        self.assertGreater(loaded, shops)
        self.assertLess(loaded, text.index("systemctl restart ripraptor"))


class WhyDownTests(TestCase):
    """deploy/why_down.sh, the owner's report for when the site was down, is valid and only reads."""

    def test_it_parses_and_never_changes_anything(self):
        script = DEPLOY / "why_down.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        body = "\n".join(line for line in script.read_text().splitlines() if not line.lstrip().startswith("#"))
        for writes in (r"systemctl (?:start|stop|restart|reload|enable|disable|kill)", r"\brm\b", r"\bkill\b",
                       r"\bcrontab\b", r"\bapt(?:-get)? +(?:install|remove|purge|upgrade|update)\b", r"\bchown\b",
                       r"\bchmod\b", r"\bmanage\.py\b", r"\bsed -i\b", r">>?\s*(?!/dev/null|&)[\w/$\"]"):
            self.assertIsNone(re.search(writes, body), writes)


def minutes(field):
    """The minutes a cron minute (or hour) field fires at: *, */n, a-b/n, a-b or a number."""
    if field == "*":
        return set(range(60))
    spread, _, step = field.partition("/")
    first, last = (0, 59) if spread == "*" else map(int, spread.split("-")) if "-" in spread else (int(spread),) * 2
    return set(range(first, last + 1, int(step or 1)))


class MemoryTests(TestCase):
    """On 14 Oct 2026 a 1 GB server with no swap ran out of memory and the kernel killed the site's
    gunicorn. The update gives it swap, has the kernel stop background work first and starts no more than
    two cron commands in one minute (WorkerThreadsInstallTests: and runs the reader with two jobs)."""

    def test_install_adds_swap_once_before_the_heavy_steps(self):
        text = (DEPLOY / "install.sh").read_text()
        swap = text.index('if [ -z "$(swapon --show --noheadings 2>/dev/null)" ]; then')
        self.assertLess(swap, text.index("pip install"))
        block = text[swap:text.index("sysctl -q -p")]
        self.assertIn("[ ! -f /swapfile ]", block)
        self.assertIn("fallocate -l 1G /swapfile", block)
        self.assertIn("grep -q '^/swapfile ' /etc/fstab ||", block)
        self.assertIn("vm.swappiness=10", block)

    def test_the_kernel_stops_the_reader_before_the_site(self):
        site = (DEPLOY / "ripraptor.service").read_text()
        reader = (DEPLOY / "ripraptor-worker.service").read_text()
        web = int(re.search(r"^OOMScoreAdjust=(-?\d+)$", site, re.M).group(1))
        background = int(re.search(r"^OOMScoreAdjust=(-?\d+)$", reader, re.M).group(1))
        self.assertLess(web, 0)
        self.assertGreater(background, 0)
        self.assertIn("MemorySwapMax=100M", reader)

    def test_no_more_than_two_cron_commands_start_in_one_minute(self):
        starts = {}
        for schedule, command in install_cron():
            minute, hour = schedule.split()[:2]
            for h in (set(range(24)) if hour == "*" else minutes(hour)):
                for m in minutes(minute):
                    starts.setdefault((h, m), []).append(command.split("manage.py ")[1].split()[0])
        crowded = {at: names for at, names in starts.items() if len(names) > 2}
        self.assertEqual(crowded, {})


class OneAddressTests(TestCase):
    """www. forwards permanently to the address without it: serving both, Google indexed the site twice."""

    def test_caddy_sends_www_to_the_bare_domain(self):
        text = (DEPLOY / "install.sh").read_text()
        caddy = text[text.index("cat > /etc/caddy/Caddyfile <<CADDY"):text.index("\nCADDY\n")]
        self.assertIn("www.$DOMAIN, http://www.$DOMAIN {\n    redir https://$DOMAIN{uri} permanent\n}", caddy)
        self.assertIn("    reverse_proxy 127.0.0.1:8000", caddy[caddy.index("\n$DOMAIN {"):])
        self.assertNotIn("$DOMAIN, www.$DOMAIN", caddy)

    def test_http_www_reaches_the_bare_domain_in_one_hop(self):
        # The owner's speed test (Oct 2026): http://www. went to https://www. (Caddy's own redirect) and only then
        # to https:// on the bare domain. Naming the http:// address sends it straight there; the sample matches.
        install = (DEPLOY / "install.sh").read_text()
        sample = (DEPLOY / "caddy.Caddyfile").read_text()
        self.assertIn("http://www.$DOMAIN {", install)
        self.assertIn("www.cardscout.example, http://www.cardscout.example {\n"
                      "    redir https://cardscout.example{uri} permanent\n}", sample)


class SpreadsheetDeployTests(TestCase):
    """A deploy restarts the web app, and the spreadsheet runner it started with it: the update waits for a
    file being made first, so it is never stopped part way and the database is not shrunk or migrated under it."""

    def test_install_waits_for_a_spreadsheet_being_made(self):
        from . import data_jobs

        text = (DEPLOY / "install.sh").read_text()
        self.assertIn('DATA_LOCK="${RIPRAPTOR_CACHE_DIR:-/var/lib/ripraptor/cache}/data-files.lock"', text)
        self.assertEqual(data_jobs.lock_path().name, "data-files.lock")
        wait = text.index('sudo -u ripraptor timeout 1800 flock "$DATA_LOCK" true')
        self.assertGreater(wait, text.index("set -a; . ./.env; set +a"))
        for later in ("manage.py compact_db", "manage.py migrate", "systemctl restart ripraptor"):
            self.assertLess(wait, text.index(later), later)


class UploadLimitTests(TestCase):
    """Django reads a whole request body before a view can refuse it, so Caddy refuses a body far over the
    largest upload (a 10 MB spreadsheet) before it reaches a web worker."""

    def test_both_caddyfiles_cap_the_request_body_alike(self):
        install = (DEPLOY / "install.sh").read_text()
        written = install[install.index("cat > /etc/caddy/Caddyfile <<CADDY"):install.index("\nCADDY\n")]
        sample = (DEPLOY / "caddy.Caddyfile").read_text()
        cap = "    request_body {\n        max_size 25MB\n    }\n    reverse_proxy 127.0.0.1:8000"
        site = written[written.index("\n$DOMAIN {") + 1:]
        self.assertIn(cap, site)
        self.assertEqual(site.replace("$DOMAIN", "cardscout.example"),
                         sample[sample.index("\ncardscout.example {") + 1:].rstrip("\n"))


class CronTests(TestCase):
    def test_install_sh_and_crontab_carry_identical_cron_lines(self):
        installed = install_cron()
        self.assertEqual(len(installed), 19)
        self.assertEqual(installed, file_cron())

    def test_delivery_is_read_weekly_for_every_shop_and_daily_where_still_unknown(self):
        lines = [(schedule, command) for schedule, command in install_cron() if "check_delivery" in command]
        self.assertEqual([schedule for schedule, _ in lines], ["30 3 * * 0", "30 4 * * *"])
        self.assertTrue(lines[0][1].endswith("check_delivery --apply"))
        # The daily one waits a while for a shop read to finish, and otherwise tries the next day.
        self.assertRegex(lines[1][1], r"^flock -w \d+ /tmp/ripraptor-import\.lock timeout .*check_delivery --apply --unknown$")

    def test_both_files_load_the_settings_before_every_command(self):
        for name, block in (("install.sh", install_block()), ("crontab", (DEPLOY / "crontab").read_text())):
            commands = [SCHEDULE.match(raw.strip()).group(2) for raw in block.splitlines() if "manage.py" in raw]
            self.assertEqual(len(commands), 19, name)
            for command in commands:
                self.assertRegex(command, PREFIX, name)

    def test_every_cron_line_has_a_timeout_wrapper(self):
        seen = set()
        for schedule, command in install_cron():
            for part in command.split(" && "):
                # A flock may come first, so its wait is not counted; see the tests below.
                found = re.match(r"(?:flock (?:-n |-w \d+ )?\S+ )*timeout -k (\d+) (\d+) .*manage\.py (\w+)", part)
                self.assertIsNotNone(found, f"No timeout before: {part}")
                kill, seconds, name = int(found.group(1)), int(found.group(2)), found.group(3)
                self.assertEqual(seconds, TIMEOUTS[name], name)
                if schedule.split()[0].endswith("/10"):
                    # Killed outright before the next run ten minutes later starts.
                    self.assertLess(seconds + kill, 600, name)
                if name == "warm_pages":
                    # Killed outright before the next run five minutes later starts.
                    self.assertLess(seconds + kill, 300, name)
                seen.add(name)
        self.assertEqual(seen, set(TIMEOUTS))

    def test_a_blocking_lock_wait_does_not_count_against_the_timeout(self):
        blocking = []
        for _, command in install_cron():
            for part in command.split(" && "):
                lock = re.search(r"flock (-\w+ )?(\d+ )?(/\S+)", part)
                if lock and not lock.group(1):
                    # flock waits without a limit, then starts the timeout once it holds the lock.
                    self.assertRegex(part, r"^flock /\S+ timeout -k \d+ \d+ \.venv/bin/python manage\.py ", part)
                    blocking.append(re.search(r"manage\.py (\w+)", part).group(1))
        self.assertEqual(sorted(blocking), ["check_delivery", "snapshot_daily_prices"])

    @skipUnless(shutil.which("flock") and shutil.which("timeout"), "needs flock and timeout")
    def test_the_snapshot_line_runs_after_a_lock_held_longer_than_its_limit(self):
        """The real snapshot line, scaled down: the lock is held 2 s and the limit is 1 s."""
        line = next(command for _, command in install_cron() if "snapshot_daily_prices" in command)
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "import.lock"
            command = (
                line.replace("/tmp/ripraptor-import.lock", str(lock))
                .replace("timeout -k 60 1800", "timeout -k 1 1")
                .replace(".venv/bin/python manage.py snapshot_daily_prices", "echo SNAPSHOT_RAN")
            )
            self.assertIn("echo SNAPSHOT_RAN", command)
            with open(lock, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                started = time.monotonic()
                waiter = subprocess.Popen(["sh", "-c", command], stdout=subprocess.PIPE, text=True)
                time.sleep(2)
                fcntl.flock(held, fcntl.LOCK_UN)
            out, _ = waiter.communicate(timeout=20)
        self.assertEqual(waiter.returncode, 0)
        self.assertEqual(out.strip(), "SNAPSHOT_RAN")
        self.assertGreaterEqual(time.monotonic() - started, 2)

    @skipUnless(shutil.which("flock") and shutil.which("timeout"), "needs flock and timeout")
    def test_tidy_waits_for_a_read_holding_the_import_lock(self):
        """The real tidy_all line, scaled down: a read holds the import lock for 2 s and tidy runs after it."""
        line = next(command for _, command in install_cron() if "tidy_all" in command)
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "import.lock"
            command = (
                line.replace("/tmp/ripraptor-import.lock", str(lock))
                .replace("/tmp/ripraptor-tidy.lock", str(Path(folder) / "tidy.lock"))
                .replace(".venv/bin/python manage.py tidy_all", "echo TIDY_RAN")
            )
            self.assertIn("echo TIDY_RAN", command)
            with open(lock, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                started = time.monotonic()
                waiter = subprocess.Popen(["sh", "-c", command], stdout=subprocess.PIPE, text=True)
                time.sleep(2)
                self.assertIsNone(waiter.poll())
                fcntl.flock(held, fcntl.LOCK_UN)
            out, _ = waiter.communicate(timeout=20)
        self.assertEqual((waiter.returncode, out.strip()), (0, "TIDY_RAN"))
        self.assertGreaterEqual(time.monotonic() - started, 2)

    def test_the_hourly_import_still_waits_for_the_lock(self):
        hourly = [command for schedule, command in install_cron() if schedule == "0 * * * *"]
        self.assertIn("flock -w 1800 /tmp/ripraptor-import.lock", hourly[0])

    def test_the_hourly_import_reads_only_the_shops_that_are_due(self):
        hourly = [command for schedule, command in install_cron() if schedule == "0 * * * *"]
        self.assertIn("manage.py import_prices --due ", hourly[0])

    def test_no_import_or_stock_watch_runs_beside_a_live_background_reader(self):
        """Every cron read of the shops is a fallback: none is a bare import_prices or watch_stock."""
        for name, text in (("install.sh", install_block()), ("crontab", (DEPLOY / "crontab").read_text())):
            reads = [line for line in text.splitlines() if re.search(r"manage\.py (import_prices|watch_stock)\b", line)]
            self.assertEqual(len(reads), 2, name)
            for line in reads:
                self.assertIn(" --if-worker-dead 30 ", line, name)
            self.assertIn(" import_prices --due --if-worker-dead 30 ", reads[0], name)
        # install.sh no longer starts an import of its own: the reader reads every shop on its first start.
        text = (DEPLOY / "install.sh").read_text()
        self.assertEqual(len(re.findall(r"manage\.py import_prices", text)), 1)
        self.assertNotIn("nohup", text)

    def test_tidy_runs_hourly_on_its_own_lock_and_the_reader_is_checked_and_backed_up(self):
        lines = dict((command.split("manage.py ")[1].split()[0], (schedule, command)) for schedule, command in install_cron())
        self.assertEqual(lines["tidy_all"][0], "5 * * * *")
        # Tidying merges and moves products, so it waits for the reads in flight and none starts while it runs.
        self.assertIn(
            "flock -n /tmp/ripraptor-tidy.lock flock -w 1800 /tmp/ripraptor-import.lock timeout -k 60 1500 ",
            lines["tidy_all"][1],
        )
        self.assertNotIn("tidy_all", lines["import_prices"][1])
        self.assertEqual(lines["check_worker"][0], "20 * * * *")
        self.assertEqual(lines["backup_db"][0], "40 0 * * *")
        self.assertIn("backup_db --keep 5", lines["backup_db"][1])
        # The stock alerts never depend on the reader.
        self.assertNotIn("--if-worker-dead", lines["send_stock_alerts"][1])
        # The drop reminders run every 15 minutes on their own lock, at minutes no other command starts on.
        self.assertEqual(lines["send_drop_alerts"][0], "9-59/15 * * * *")
        self.assertIn("flock -n /tmp/ripraptor-drops.lock .venv/bin/python manage.py send_drop_alerts", lines["send_drop_alerts"][1])
        self.assertNotIn("--if-worker-dead", lines["send_drop_alerts"][1])
        # The watchlist's search alerts likewise, every 15 minutes at 4 past, on their own lock.
        self.assertEqual(lines["send_search_alerts"][0], "4-59/15 * * * *")
        self.assertIn("flock -n /tmp/ripraptor-search-alerts.lock .venv/bin/python manage.py send_search_alerts",
                      lines["send_search_alerts"][1])
        self.assertNotIn("--if-worker-dead", lines["send_search_alerts"][1])
        # The Discord posts every ten minutes at 8 past, a minute no other command starts on, on their own lock.
        self.assertEqual(lines["post_discord"][0], "8-58/10 * * * *")
        self.assertIn("flock -n /tmp/ripraptor-discord.lock .venv/bin/python manage.py post_discord", lines["post_discord"][1])

    def test_the_worker_unit_restarts_it_under_a_watchdog_and_install_starts_it(self):
        unit = (DEPLOY / "ripraptor-worker.service").read_text()
        for setting in ("Type=notify", "WatchdogSec=180", "MemoryMax=32%", "Restart=always", "RestartSec=10",
                        "StartLimitIntervalSec=0", "Nice=10", "KillSignal=SIGINT", "TimeoutStopSec=60",
                        "User=ripraptor", "EnvironmentFile=/srv/ripraptor/.env", "manage.py run_worker"):
            self.assertIn(setting, unit)
        text = (DEPLOY / "install.sh").read_text()
        self.assertIn("cp deploy/ripraptor-worker.service /etc/systemd/system/", text)
        self.assertIn("systemctl restart ripraptor-worker", text)
        # Restarted after the migration, so the new code never runs on the old tables.
        self.assertGreater(text.index("systemctl restart ripraptor-worker"), text.index("manage.py migrate"))

    def test_install_backs_up_with_backup_db_not_cp(self):
        text = (DEPLOY / "install.sh").read_text()
        self.assertIn("manage.py backup_db --keep 5", text)
        self.assertNotRegex(text, r"\bcp\b[^\n]*db\.sqlite3")

    def test_install_empties_the_shared_cache_after_migrate_and_the_restart(self):
        """The cache folder outlives a restart, so the new code must not read lists the old code pickled."""
        from django.core.cache import cache
        from django.core.cache.backends.filebased import FileBasedCache

        text = (DEPLOY / "install.sh").read_text()
        found = re.search(r"sudo -u ripraptor bash -c \"cd \$DIR && set -a && \. \./\.env && set \+a && "
                          r"\.venv/bin/python manage\.py shell -c '([^']+)'\"", text)
        self.assertIsNotNone(found, "install.sh does not empty the cache")
        self.assertGreater(found.start(), text.index("manage.py migrate"))
        self.assertGreater(found.start(), text.index("systemctl restart ripraptor"))

        # The command it runs empties a file cache that another process filled.
        folder = tempfile.mkdtemp(prefix="ripraptor-cache-test-")
        self.addCleanup(shutil.rmtree, folder, True)
        old_worker = FileBasedCache(folder, {})
        old_worker.set("web:home-lists:v4", "lists pickled by the old code", 300)
        with override_settings(CACHES={"default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": folder,
        }}):
            self.assertEqual(cache.get("web:home-lists:v4"), "lists pickled by the old code")
            call_command("shell", command=found.group(1), stdout=StringIO())
        self.assertIsNone(FileBasedCache(folder, {}).get("web:home-lists:v4"))


class BackupTests(TestCase):
    def test_backup_db_writes_a_file_that_opens_with_the_same_product_count(self):
        with file_database() as path, tempfile.TemporaryDirectory() as folder:
            product_set = make_set(make_game())
            for n in range(3):
                make_product(product_set, name=f"Prismatic Evolutions Booster {n}", slug=f"pev-{n}")
            out = StringIO()
            call_command("backup_db", dir=folder, stdout=out)
            copies = list(Path(folder).glob("*.bak"))
            self.assertEqual(len(copies), 1)
            self.assertTrue(copies[0].name.startswith(f"{path.name}."))
            self.assertEqual(rows_in_file(copies[0], "catalogue_product"), 3)
            self.assertEqual(rows_in_file(copies[0], "catalogue_product"), rows_in_file(path, "catalogue_product"))
            with closing(sqlite3.connect(copies[0])) as copy:
                self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(Path(folder).glob("*.part")), [])
            self.assertIn("Backed up to", out.getvalue())

    def test_backup_db_keeps_only_the_newest_five(self):
        with file_database() as path, tempfile.TemporaryDirectory() as folder:
            old = timezone.now() - datetime.timedelta(days=10)
            for days in range(6):
                (Path(folder) / backup_name(path, old + datetime.timedelta(days=days))).write_bytes(b"")
            other = Path(folder) / "something-else.bak"
            other.write_bytes(b"")
            call_command("backup_db", dir=folder, keep=5, stdout=StringIO())
            kept = sorted(p.name for p in Path(folder).glob(f"{path.name}.*.bak"))
            self.assertEqual(len(kept), 5)
            self.assertEqual(kept[-1], backup_name(path))
            self.assertNotIn(backup_name(path, old), kept)
            self.assertTrue(other.exists())   # files that are not this database's backups are left alone

    def test_default_folder_is_backups_beside_the_database(self):
        with file_database() as path:
            call_command("backup_db", stdout=StringIO())
            self.assertEqual(len(list((path.parent / "backups").glob("*.bak"))), 1)

    def test_an_in_memory_database_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("backup_db", dir=tempfile.gettempdir(), stdout=StringIO())

    def test_the_name_sorts_by_time(self):
        self.assertRegex(backup_name("/var/lib/ripraptor/db.sqlite3"), r"^db\.sqlite3\.\d{8}-\d{4}\.bak$")


class CheckpointTests(TestCase):
    def test_snapshot_runs_a_truncate_checkpoint(self):
        fake = mock.MagicMock(vendor="sqlite")
        cursor = fake.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (0, 0, 0)
        with mock.patch.object(snapshot_daily_prices, "connection", fake), mock.patch.object(
            snapshot_daily_prices, "snapshot_all", return_value=4
        ) as snapshot:
            out = StringIO()
            call_command("snapshot_daily_prices", stdout=out)
        snapshot.assert_called_once()
        cursor.execute.assert_called_once_with("PRAGMA wal_checkpoint(TRUNCATE)")
        self.assertIn("Recorded prices for 4 products.", out.getvalue())

    def test_a_busy_checkpoint_is_reported_not_raised(self):
        fake = mock.MagicMock(vendor="sqlite")
        fake.cursor.return_value.__enter__.return_value.fetchone.return_value = (1, 10, 3)
        with mock.patch.object(snapshot_daily_prices, "connection", fake):
            err = StringIO()
            call_command("snapshot_daily_prices", stdout=StringIO(), stderr=err)
        self.assertIn("could not be emptied", err.getvalue())

    def test_the_checkpoint_empties_the_wal_file_on_a_real_database(self):
        with file_database() as path:
            make_game()
            wal = Path(f"{path}-wal")
            self.assertGreater(wal.stat().st_size, 0)
            call_command("snapshot_daily_prices", stdout=StringIO())
            self.assertEqual(wal.stat().st_size, 0)
            self.assertEqual(rows_in_file(path, "catalogue_game"), 1)


class AbandonedRunTests(TestCase):
    def setUp(self):
        self.retailer = make_retailer()
        self.now = timezone.now()

    def run_started(self, hours, retailer=None, **kwargs):
        run = ImportRun.objects.create(retailer=retailer or self.retailer, **kwargs)
        ImportRun.objects.filter(pk=run.pk).update(started_at=self.now - datetime.timedelta(hours=hours))
        return run

    def test_close_abandoned_runs_closes_a_4h_old_run_with_the_fixed_text_and_leaves_a_1h_old_one(self):
        stuck = self.run_started(4)
        running = self.run_started(1)
        finished = self.run_started(5, finished_at=self.now - datetime.timedelta(hours=4))
        self.assertEqual(close_abandoned_runs(now=self.now), 1)
        stuck.refresh_from_db()
        running.refresh_from_db()
        finished.refresh_from_db()
        self.assertEqual(stuck.error, "Stopped before it finished.")
        self.assertEqual(stuck.error, STOPPED)
        self.assertEqual(stuck.finished_at, self.now)
        self.assertFalse(stuck.ok)
        self.assertIsNone(running.finished_at)
        self.assertEqual(running.error, "")
        self.assertEqual(finished.error, "")
        self.assertEqual(finished.finished_at, self.now - datetime.timedelta(hours=4))

    @override_settings(**KEYS)
    def test_a_closed_run_does_not_count_as_todays_ebay_fetch(self):
        make_product(make_set(make_game()), ean="0820650853500")
        marketplace = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )
        # Cut short four hours ago after posting progress: offers found, never finished.
        self.run_started(4, retailer=marketplace, offers_found=40)
        close_abandoned_runs()
        api = FakeApi([item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="79.99")])
        with mock.patch.object(ebay, "http", api), mock.patch.object(ebay, "PAUSE", 0):
            run = run_import(marketplace)
        self.assertTrue(api.searches())
        self.assertEqual(run.error, "")

    def test_import_prices_closes_abandoned_runs_before_reading(self):
        stuck = self.run_started(4)
        seen = []

        def fake_run(retailer, feed_path=None):
            stuck.refresh_from_db()
            seen.append(stuck.error)
            return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now())

        self.retailer.source_type = Retailer.Source.SHOPIFY
        self.retailer.source_url = "https://harbour.example/"
        self.retailer.save()
        out = StringIO()
        with mock.patch("catalogue.management.commands.import_prices.run_import", fake_run):
            call_command("import_prices", stdout=out)
        self.assertEqual(seen, [STOPPED])
        self.assertIn("Closed 1 earlier runs that stopped before they finished.", out.getvalue())


class ClosedRunInsightsTests(TestCase):
    """A closed run's finished_at is when it was closed, so Insights must not read it as when it ran."""

    def setUp(self):
        self.now = timezone.now()

    def add_run(self, retailer, started_hours, finished_hours=None, **kwargs):
        finished = None if finished_hours is None else self.now - datetime.timedelta(hours=finished_hours)
        return ImportRun.objects.create(
            retailer=retailer, started_at=self.now - datetime.timedelta(hours=started_hours),
            finished_at=finished, **kwargs
        )

    def test_a_closed_ebay_run_is_not_the_last_ebay_run_that_found_offers(self):
        marketplace = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )
        self.add_run(marketplace, 5, offers_found=40)               # cut short after posting progress
        good = self.add_run(marketplace, 4, 3, offers_found=120)
        self.assertEqual(close_abandoned_runs(now=self.now), 1)
        self.assertEqual(insights.ebay_coverage()["last_run"], good.finished_at)

    def test_shop_health_reports_the_read_that_started_last(self):
        shop = make_retailer(source_type=Retailer.Source.SHOPIFY, source_url="https://harbour.example/")
        self.add_run(shop, 5)                                        # died five hours ago
        good = self.add_run(shop, 4, 3.5)
        close_abandoned_runs(now=self.now)
        health = {row["name"]: row for row in insights.report(30)["shops_health"]}[shop.name]
        self.assertEqual(health["last_ok"], good.finished_at)
        self.assertEqual(health["problem"], "")

    def test_a_closed_run_that_is_the_latest_still_shows_as_the_problem(self):
        shop = make_retailer(source_type=Retailer.Source.SHOPIFY, source_url="https://harbour.example/")
        self.add_run(shop, 5, 4.5)
        self.add_run(shop, 4)
        close_abandoned_runs(now=self.now)
        health = {row["name"]: row for row in insights.report(30)["shops_health"]}[shop.name]
        self.assertEqual(health["problem"], STOPPED)


class WorkerThreadsInstallTests(TestCase):
    """install.sh gives a small server two reader jobs at once, once, and never changes a value set by hand."""

    def run_line(self, env_text, mem_kb):
        text = (DEPLOY / "install.sh").read_text()
        line = "\n".join(l for l in text.splitlines() if "RIPRAPTOR_WORKER_THREADS" in l and not l.startswith("#"))
        folder = Path(tempfile.mkdtemp(prefix="ripraptor-install-test-"))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / ".env").write_text(env_text)
        (folder / "meminfo").write_text(f"MemTotal:       {mem_kb} kB\nMemFree:        1000 kB\n")
        line = line.replace("/proc/meminfo", str(folder / "meminfo"))
        subprocess.run(["bash", "-euo", "pipefail", "-c", line], cwd=folder, check=True)
        return (folder / ".env").read_text()

    def test_a_server_under_1_gb_gets_two_threads(self):
        self.assertIn("RIPRAPTOR_WORKER_THREADS=2", self.run_line("DJANGO_DEBUG=0\n", 480000))

    def test_a_1_gb_server_gets_two_threads_too(self):
        # A "1 GB" droplet reports about 984,000 kB; with three jobs its reader hit 300 MB eight times a night.
        self.assertIn("RIPRAPTOR_WORKER_THREADS=2", self.run_line("DJANGO_DEBUG=0\n", 985000))

    def test_a_2_gb_server_keeps_the_default(self):
        self.assertNotIn("RIPRAPTOR_WORKER_THREADS", self.run_line("DJANGO_DEBUG=0\n", 2000000))

    def test_a_value_set_by_hand_stays(self):
        env = self.run_line("RIPRAPTOR_WORKER_THREADS=3\n", 480000)
        self.assertEqual(env.count("RIPRAPTOR_WORKER_THREADS"), 1)
        self.assertIn("RIPRAPTOR_WORKER_THREADS=3", env)
        self.assertIn("RIPRAPTOR_WORKER_THREADS=4", self.run_line("RIPRAPTOR_WORKER_THREADS=4\n", 2000000))

    def test_a_server_resized_to_2_gb_loses_the_two_written_for_it(self):
        # The owner's 1 GB server, resized: the next update lets its reader run three jobs at once.
        env = self.run_line("DJANGO_DEBUG=0\nRIPRAPTOR_WORKER_THREADS=2\n", 2000000)
        self.assertNotIn("RIPRAPTOR_WORKER_THREADS", env)
        self.assertIn("DJANGO_DEBUG=0", env)
        # Still 1 GB: it keeps its two.
        self.assertIn("RIPRAPTOR_WORKER_THREADS=2", self.run_line("RIPRAPTOR_WORKER_THREADS=2\n", 985000))

    def test_the_update_says_how_many_jobs_the_reader_runs(self):
        # So the owner can see a resize took effect from the update's own output.
        text = (DEPLOY / "install.sh").read_text()
        [line] = [l for l in text.splitlines() if l.startswith('echo "The background reader runs')]
        folder = Path(tempfile.mkdtemp(prefix="ripraptor-install-test-"))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "meminfo").write_text("MemTotal:       2000000 kB\n")
        line = line.replace("/proc/meminfo", str(folder / "meminfo"))
        said = subprocess.run(["bash", "-euo", "pipefail", "-c", line], check=True, capture_output=True, text=True).stdout
        self.assertEqual(said.strip(), "The background reader runs 3 jobs at once on this server's 1953 MB of memory.")

    def test_it_is_written_before_the_reader_starts(self):
        text = (DEPLOY / "install.sh").read_text()
        self.assertLess(text.index("grep -q RIPRAPTOR_WORKER_THREADS"), text.index("systemctl restart ripraptor-worker"))


class CommandMethodTests(TestCase):
    """A command's own method named like one of Django's (check, execute) replaces it: check_delivery's check()
    took the place of Django's system checks, so every run from cron or the console stopped before it began, while
    the tests, which call commands without those checks, passed."""

    ALLOWED = {"handle", "add_arguments", "help", "requires_system_checks", "requires_migrations_checks",
               "output_transaction", "suppressed_base_arguments", "leave_locale_alone", "stealth_options"}

    def test_no_command_replaces_a_method_django_calls(self):
        from importlib import import_module

        from django.core.management import get_commands
        from django.core.management.base import BaseCommand

        ours = {name: app for name, app in get_commands().items() if app in ("catalogue", "web", "content", "inbox")}
        self.assertIn("check_delivery", ours)
        clashes = []
        for name, app in ours.items():
            command = import_module(f"{app}.management.commands.{name}").Command
            for attribute in vars(command):
                if attribute in self.ALLOWED or attribute.startswith("__"):
                    continue
                if hasattr(BaseCommand, attribute):
                    clashes.append(f"{name}.{attribute}")
        self.assertEqual(clashes, [])

    def test_check_delivery_runs_with_djangos_checks(self):
        out = StringIO()
        call_command("check_delivery", "no-such-shop", skip_checks=False, stdout=out, stderr=StringIO())
