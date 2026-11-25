"""Root and ripraptor (the account the site, the reader and the cron jobs run as) are kept apart by who owns what. The
update (deploy/install.sh) runs as root, so it must never run or shell-read anything ripraptor can change: the code,
.git, .venv and .env belong to root, every manage.py step runs as ripraptor, and .env is read as text. The site
refuses .env.example's stand-in secret key, and the update replaces one."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from ripraptor.settings import weak_secret_key

DEPLOY = Path(settings.BASE_DIR) / "deploy"
EXAMPLE_KEY = "replace-with-a-long-random-string"


def script_lines(name):
    """The lines root runs in a deploy script: no comments, and not the crontab install.sh hands to ripraptor."""
    text = (DEPLOY / name).read_text()
    if 'echo "PYTHONUNBUFFERED=1' in text:
        start = text.index('echo "PYTHONUNBUFFERED=1')
        text = text[:start] + text[text.index('" | crontab', start):]
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def commands(name):
    """Each simple command root runs, split at && || ; and |, without the echo lines that only print a hint."""
    found = []
    for line in script_lines(name):
        if line.startswith(("echo ", "site()")):
            continue
        found += [part.strip() for part in re.split(r"&&|\|\||;|\|(?!\|)", line) if part.strip()]
    return found


def block(text, first, last):
    start = text.index(first)
    return text[start:text.index(last, start) + len(last)]


class RootRunsNothingTheSiteCanChangeTests(SimpleTestCase):
    def test_every_manage_py_step_runs_as_ripraptor(self):
        for name in ("install.sh", "setup.sh"):
            steps = [part for part in commands(name) if "bin/python manage.py" in part]
            self.assertTrue(steps, name)
            for part in steps:
                with self.subTest(script=name, step=part):
                    self.assertRegex(part, r"^(\[ [^]]+ \] )?site ")

    def test_root_runs_python_only_to_install_packages_or_compile(self):
        for name in ("install.sh", "setup.sh"):
            for part in commands(name):
                if "bin/python manage.py" in part or not re.search(r"(^|\$\()(\S*bin/python|python3)\s", part):
                    continue
                with self.subTest(script=name, step=part):
                    self.assertRegex(part, r"^(\.venv(-new)?/bin/python -I -m pip install |python3 -I -m compileall |"
                                           r"python3 -m venv |SECRET=\$\(python3 -I -c )")

    def test_root_never_shell_reads_env(self):
        for name in ("install.sh", "setup.sh"):
            for line in script_lines(name):
                if ". ./.env" in line and not line.startswith("echo "):
                    with self.subTest(script=name, line=line):
                        self.assertTrue(line.startswith("site()"), line)

    def test_git_never_trusts_a_checkout_root_does_not_own(self):
        install = (DEPLOY / "install.sh").read_text()
        self.assertNotIn("--add safe.directory", install)
        self.assertIn('git config --global --fixed-value --unset-all safe.directory "$DIR"', install)
        # A history that belonged to ripraptor is made again by root, before git runs in it.
        removed = install.index('if [ -e "$DIR/.git" ] && [ "$(stat -c %u "$DIR/.git")" != 0 ]; then rm -rf "${DIR:?}/.git"')
        self.assertLess(removed, install.index('git -C "$DIR" fetch'))
        self.assertNotIn("safe.directory", (DEPLOY / "why_down.sh").read_text())

    def test_the_code_belongs_to_root_and_ripraptor_writes_only_its_data(self):
        install = (DEPLOY / "install.sh").read_text()
        self.assertNotRegex(install, r'chown -R ripraptor(:ripraptor)? "?\$DIR"?(\s|$)')
        prune = r'find "$DIR" -xdev \( -path "$DIR/media" -o -path "$DIR/staticfiles" \) -prune -o '
        self.assertIn(prune + "-exec chown -h root:root {} +", install)
        self.assertIn(prune + "! -type l -exec chmod go-w {} +", install)
        self.assertIn("chown -R ripraptor:ripraptor /var/lib/ripraptor media staticfiles\n", install)
        # Before the first step that runs as ripraptor or reads its folders.
        self.assertLess(install.index(prune), install.index("\nsite "))
        setup = (DEPLOY / "setup.sh").read_text()
        self.assertNotIn("chown -R ripraptor /srv/ripraptor", setup)
        self.assertIn("chown -R root:root /srv/ripraptor", setup)

    def test_packages_made_by_ripraptor_are_made_again_beside_and_moved_in(self):
        install = (DEPLOY / "install.sh").read_text()
        self.assertIn('if [ ! -d "$DIR/.venv" ] || [ "$(stat -c %u "$DIR/.venv")" != 0 ]; then FRESH_VENV=1; fi', install)
        swap = block(install, 'if [ "$FRESH_VENV" = 1 ]; then', "\nelse")
        # One command a line, so set -e stops the update when the new folder cannot be made, before the old one goes.
        self.assertNotIn("&&", swap)
        self.assertLess(swap.index("pip install"), swap.index("mv .venv .venv-old"))
        # The site starts its packages through python -m, which a moved folder still runs.
        unit = (DEPLOY / "ripraptor.service").read_text()
        self.assertIn("ExecStart=/srv/ripraptor/.venv/bin/python -m gunicorn ripraptor.wsgi:application", unit)
        self.assertNotIn(".venv/bin/gunicorn", unit)

    def test_env_belongs_to_root_and_ripraptor_only_reads_it(self):
        install = (DEPLOY / "install.sh").read_text()
        final = install.index("chown root:ripraptor .env && chmod 640 .env")
        last_edit = max(match.start() for match in re.finditer(r">> \.env|sed -i [^\n]* \.env$", install, re.M))
        self.assertGreater(final, last_edit)
        self.assertLess(final, install.index("\nsite "))
        self.assertIn('if [ -L "$DIR/.env" ]; then', install)
        self.assertIn("(umask 077 && cat > .env <<ENV", install)

    def test_both_units_may_write_only_their_data_and_tmp(self):
        for name in ("ripraptor.service", "ripraptor-worker.service"):
            unit = (DEPLOY / name).read_text()
            with self.subTest(unit=name):
                self.assertIn("\nProtectSystem=strict\n", unit)
                self.assertIn("\nReadWritePaths=-/var/lib/ripraptor -/srv/ripraptor/media -/tmp -/var/tmp\n", unit)
                self.assertIn("\nUser=ripraptor\n", unit)

    def test_the_site_makes_no_control_socket(self):
        # gunicorn 25.1 and later make a control socket in the account's home folder unless told not to. The site may
        # not write there (ProtectSystem=strict), so each start logged an error; the flag needs 25.1 at least.
        unit = (DEPLOY / "ripraptor.service").read_text()
        [start] = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
        self.assertIn(" --no-control-socket", start)
        requirements = (Path(settings.BASE_DIR) / "requirements.txt").read_text().splitlines()
        self.assertIn("gunicorn>=25.1", requirements)

    def test_it_still_parses(self):
        for name in ("install.sh", "setup.sh", "why_down.sh"):
            subprocess.run(["bash", "-n", str(DEPLOY / name)], check=True)


class ReadingEnvAsTextTests(SimpleTestCase):
    def run_in(self, env_text, script):
        folder = Path(tempfile.mkdtemp(prefix="ripraptor-env-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(folder)], check=False))
        (folder / ".env").write_text(env_text)
        install = (DEPLOY / "install.sh").read_text()
        [env_value] = [line for line in install.splitlines() if line.startswith("env_value() {")]
        done = subprocess.run(["bash", "-euo", "pipefail", "-c", env_value + "\n" + script], cwd=folder, check=True,
                              capture_output=True, text=True)
        return folder, done.stdout

    def test_a_value_is_read_and_nothing_in_the_file_runs(self):
        folder, said = self.run_in('RIPRAPTOR_CACHE_DIR="/srv/cache"\nPLANTED=$(touch planted)\n`touch planted2`\n',
                                   "env_value RIPRAPTOR_CACHE_DIR; env_value PLANTED")
        self.assertEqual(said.splitlines(), ["/srv/cache", "$(touch planted)"])
        self.assertFalse((folder / "planted").exists())
        self.assertFalse((folder / "planted2").exists())

    def test_the_last_value_wins_as_when_it_was_sourced(self):
        _, said = self.run_in("RIPRAPTOR_WORKER_THREADS=3\nRIPRAPTOR_WORKER_THREADS=2\n",
                              "env_value RIPRAPTOR_WORKER_THREADS")
        self.assertEqual(said.strip(), "2")


class SecretKeyTests(SimpleTestCase):
    def replace_block(self):
        install = (DEPLOY / "install.sh").read_text()
        return block(install, 'KEY="$(env_value DJANGO_SECRET_KEY)"', "unset KEY")

    def updated_env(self, env_text):
        folder = Path(tempfile.mkdtemp(prefix="ripraptor-key-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(folder)], check=False))
        (folder / ".env").write_text(env_text)
        install = (DEPLOY / "install.sh").read_text()
        [env_value] = [line for line in install.splitlines() if line.startswith("env_value() {")]
        subprocess.run(["bash", "-euo", "pipefail", "-c", env_value + "\n" + self.replace_block()], cwd=folder,
                       check=True, capture_output=True, text=True)
        return (folder / ".env").read_text()

    def key_in(self, env_text):
        return re.search(r"^DJANGO_SECRET_KEY=(.*)$", env_text, re.M).group(1)

    def test_the_example_or_a_short_key_is_replaced_by_the_update(self):
        for weak in (EXAMPLE_KEY, "short", "a" * 60):
            with self.subTest(key=weak):
                env = self.updated_env(f"DJANGO_DEBUG=0\nDJANGO_SECRET_KEY={weak}\nDJANGO_ALLOWED_HOSTS=ripraptor.com\n")
                key = self.key_in(env)
                self.assertNotEqual(key, weak)
                self.assertFalse(weak_secret_key(key))
                self.assertEqual(env.count("DJANGO_SECRET_KEY="), 1)
                self.assertIn("DJANGO_ALLOWED_HOSTS=ripraptor.com", env)

    def test_a_good_key_is_kept(self):
        good = "k3y-" + "AbCdEfGhIj0123456789" * 3
        self.assertEqual(self.key_in(self.updated_env(f"DJANGO_SECRET_KEY={good}\n")), good)

    def test_the_settings_refuse_a_weak_key_with_debug_off(self):
        for weak in (EXAMPLE_KEY, "x" * 50, "short-key", "django-insecure-" + "abcdefghij" * 5):
            self.assertTrue(weak_secret_key(weak), weak)
        self.assertFalse(weak_secret_key("test-only-key-" + "0123456789" * 5))
        env = {key: value for key, value in os.environ.items() if not key.startswith(("DJANGO_", "RIPRAPTOR_"))}
        with tempfile.TemporaryDirectory() as folder:
            env.update(DJANGO_SETTINGS_MODULE="ripraptor.settings", DJANGO_DEBUG="0", DJANGO_SECRET_KEY=EXAMPLE_KEY,
                       RIPRAPTOR_CACHE_DIR=folder)
            done = subprocess.run([sys.executable, "-c", "import django; django.setup()"], cwd=settings.BASE_DIR,
                                  env=env, capture_output=True, text=True, timeout=120)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("DJANGO_SECRET_KEY is the example one", done.stderr)
            # With DEBUG on, as on a laptop, the example key is still allowed.
            env["DJANGO_DEBUG"] = "1"
            done = subprocess.run([sys.executable, "-c", "import django; django.setup()"], cwd=settings.BASE_DIR,
                                  env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_manual_setup_writes_a_random_key_not_the_example(self):
        setup = (DEPLOY / "setup.sh").read_text()
        self.assertNotIn("cp .env.example .env", setup)
        self.assertIn('sed "s/^DJANGO_SECRET_KEY=.*/DJANGO_SECRET_KEY=$SECRET/" .env.example > .env', setup)
