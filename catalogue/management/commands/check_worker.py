"""
Is the background reader alive? Prints how old its heartbeat is and exits with code 1 when it is
older than ten minutes (or there has never been one), so the cron log shows when it stopped.
The hourly cron reads the shops meanwhile.

A reader that ran and stopped also tells the owner, at most once a day (catalogue/notify.py). One that has
never run does not: the hourly cron is then the normal way shops are read.
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
        state = WorkerState.current()
        now = timezone.now()
        if state is None or state.heartbeat_at is None:
            self.stdout.write("The background reader has never run. The hourly cron reads the shops.")
            sys.exit(1)
        age = int((now - state.heartbeat_at).total_seconds() // 60)
        if state.alive(now):
            self.stdout.write(f"The background reader is running: last heartbeat {age} minutes ago.")
            return
        self.stdout.write(
            f"The background reader has stopped: last heartbeat {age} minutes ago. The hourly cron reads the shops."
        )
        try:
            if notify.worker_stopped(state.heartbeat_at, now):
                self.stdout.write("The owner has been told.")
        except DatabaseError:
            logger.exception("Could not note the notice to the owner")
        sys.exit(1)
