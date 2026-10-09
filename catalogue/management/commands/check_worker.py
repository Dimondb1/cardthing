"""
Is the background reader alive? Prints how old its heartbeat is and exits with code 1 when it is
older than ten minutes (or there has never been one), so the cron log shows when it stopped.
The hourly cron reads the shops meanwhile.

A reader that stopped also tells the owner, at most once a day (catalogue/notify.py). So does one that has
never sent a heartbeat, once an earlier check has already found none: on the server the reader is a service
that should beat within seconds of starting, so a reader that fails every time it starts is reported too.
"""

import logging
import sys

from django.core.management.base import BaseCommand
from django.db import DatabaseError
from django.utils import timezone

from catalogue import notify
from catalogue.models import WorkerState

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Say how old the background reader's heartbeat is; exit 1 when it is older than ten minutes."

    def handle(self, *args, **options):
        now = timezone.now()
        self.check_disk(now)
        state = WorkerState.current()
        if state is None or state.heartbeat_at is None:
            self.stdout.write("The background reader has never run. The hourly cron reads the shops.")
            self.tell(notify.worker_never_beat, now)
            sys.exit(1)
        age = int((now - state.heartbeat_at).total_seconds() // 60)
        if state.alive(now):
            self.stdout.write(f"The background reader is running: last heartbeat {age} minutes ago.")
            return
        self.stdout.write(
            f"The background reader has stopped: last heartbeat {age} minutes ago. The hourly cron reads the shops."
        )
        self.tell(notify.worker_stopped, state.heartbeat_at, now)
        sys.exit(1)

    def check_disk(self, now):
        """The disk the database is on: a full disk stops the whole site, so the owner hears well before."""
        from django.db import connection

        name = connection.settings_dict.get("NAME")
        if connection.vendor != "sqlite" or not name or connection.is_in_memory_db():
            return
        from pathlib import Path

        percent = notify.disk_used(Path(name).parent)[0]
        self.stdout.write(f"The server's disk is {percent} percent full.")
        try:
            notify.disk_nearly_full(Path(name).parent, now)
        except DatabaseError:
            logger.exception("Could not note the disk notice to the owner")

    def tell(self, notice, *args):
        try:
            if notice(*args):
                self.stdout.write("The owner has been told.")
        except DatabaseError:
            logger.exception("Could not note the notice to the owner")
