"""The update (deploy/install.sh) checks the address it is given before it changes anything. On 26 Nov 2026 it was run
with a stray "~" after ripraptor.com, wrote the web server's settings for "ripraptor.com~", and the site served every
visitor a blank page until the file was mended by hand."""

import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

INSTALL = Path(settings.BASE_DIR) / "deploy" / "install.sh"


def run_check(address):
    """Run the script's lines up to the check (REPO= comes straight after it), which change nothing, with ``address``."""
    text = INSTALL.read_text()
    head = text[:text.index("\nREPO=")]
    return subprocess.run(["bash", "-c", head + "\necho \"$DOMAIN\"", "install.sh", address],
                          capture_output=True, text=True, timeout=10)


class AddressCheckTests(SimpleTestCase):
    def test_the_check_comes_before_anything_is_installed(self):
        text = INSTALL.read_text()
        self.assertLess(text.index("WEB_ADDRESS"), text.index("apt-get"))
        self.assertLess(text.index("WEB_ADDRESS"), text.index("Caddyfile"))

    def test_a_web_address_goes_through(self):
        for address, kept in (("ripraptor.com", "ripraptor.com"), ("RipRaptor.com", "ripraptor.com"),
                              ("shop.example.co.uk", "shop.example.co.uk"), ("my-site.com", "my-site.com")):
            with self.subTest(address=address):
                done = run_check(address)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.strip(), kept)

    def test_anything_else_stops_before_a_change(self):
        for address in ("ripraptor.com~", "ripraptor.com ", " ripraptor.com", "ripraptor", "https://ripraptor.com",
                        "ripraptor.com/", "ripraptor..com", ".ripraptor.com", "ripraptor.com.", "-ripraptor.com",
                        "ripraptor.com;rm", ""):
            with self.subTest(address=address):
                done = run_check(address)
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(done.stdout, "")
        self.assertIn('"ripraptor.com~" is not a web address', run_check("ripraptor.com~").stderr)


class ControlSocketTests(SimpleTestCase):
    def test_the_site_starts_without_gunicorns_control_socket(self):
        """gunicorn 25.1 opens a control socket in a folder the site may not write, and logged an error at every start.
        The switch that turns it off came in the same version, so the packages ask for at least that one."""
        service = (Path(settings.BASE_DIR) / "deploy" / "ripraptor.service").read_text()
        start = next(line for line in service.splitlines() if line.startswith("ExecStart="))
        self.assertIn("--no-control-socket", start)
        requirements = (Path(settings.BASE_DIR) / "requirements.txt").read_text()
        self.assertIn("gunicorn>=25.1", requirements)
        done = subprocess.run([sys.executable, "-m", "gunicorn", "--help"], capture_output=True, text=True, timeout=30)
        self.assertIn("--no-control-socket", done.stdout)
