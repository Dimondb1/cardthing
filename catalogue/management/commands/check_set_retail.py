"""
Ask Claude whether UK shops will sell the sets close to their day that no UK shop lists (catalogue/set_retail.py).

    python manage.py check_set_retail            # ask now, within the caps
    python manage.py check_set_retail --dry-run  # the sets it would ask about and the most each could cost

Run by cron once a day. It waits for a Claude judge run going already (the same lock file), and does
nothing until the owner saves a key and switches Claude on in Things to check, or while Pause all is on.
"""

import fcntl
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from catalogue import judge, set_retail

WAIT_SECONDS = 240    # how long it waits for a judge run the owner started from the page


class Command(BaseCommand):
    help = "Ask Claude whether UK shops will sell upcoming sets no shop lists yet, within the monthly limit."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Send nothing; list the sets and their worst case.")

    def handle(self, *args, dry_run=False, **options):
        if dry_run:
            return self.go(dry_run)
        folder = Path(settings.RIPRAPTOR_CACHE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "judge.lock", "w") as lock:
            waited = time.monotonic()
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - waited > WAIT_SECONDS:
                        self.stdout.write("Claude retail check: a judge run is still going; tried again tomorrow.")
                        return None
                    time.sleep(10)
            return self.go(dry_run)

    def go(self, dry_run):
        asked, marked, spent, note, lines = set_retail.run(dry_run=dry_run)
        for line in lines:
            self.stdout.write(line)
        if asked or dry_run:
            self.stdout.write(f"Claude retail check: asked {asked}, marked {marked} as not sold in UK shops, "
                              f"{judge.dollars(spent)} spent. {note}".strip())
        elif note and note not in ("Claude is off.", "Claude needs a key."):
            self.stdout.write(f"Claude retail check: {note}")
        return None
