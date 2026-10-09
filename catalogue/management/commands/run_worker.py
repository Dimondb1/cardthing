"""
The background reader. systemd runs it as ripraptor-worker.service:

    python manage.py run_worker                  # for ever, three threads
    python manage.py run_worker --max-threads 2  # on a server with under 1 GB of memory
    python manage.py run_worker --once           # one planning pass in this thread, then stop

See catalogue/worker.py for what it does and the README for how to stop and start it.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from catalogue import worker


class Command(BaseCommand):
    help = "Read shops when their turn comes and check popular listings every ten minutes, all day."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Plan once, run every job due now in this thread, then stop.")
        parser.add_argument("--max-threads", type=int, default=worker.THREADS, help="Jobs run at once (default 3).")

    def handle(self, *args, once=False, max_threads=worker.THREADS, **options):
        held = worker.hold_worker_lock()
        if held is None:
            raise CommandError("Another background reader is running.")
        try:
            reader = worker.Worker(threads=max_threads)
            closed = reader.begin()
            if closed:
                self.stdout.write(f"Closed {closed} earlier runs that stopped before they finished.")
            if once:
                reader.run_once()
                self.stdout.write(f"{reader.jobs_done} jobs done.")
                return
            self.stdout.write(f"Background reader started with {reader.threads} threads.")
            reader.run_forever()
        finally:
            os.close(held)
