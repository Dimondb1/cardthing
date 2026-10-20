"""
Send the watchlist page's search alert emails that are due.

    python manage.py send_search_alerts

Each confirmed alert is emailed about products newly matching what it describes: in stock at a UK shop,
of its game, type and language, at or under its price delivered. One email per alert per run, naming up to
six products and never a shop. Unconfirmed alerts go after a week and confirmed ones six months after they
were confirmed. Safe to run every 15 minutes, and twice at once: each email is claimed before it is sent.
Meant for the cron entry beside send_drop_alerts; test the email settings with send_stock_alerts --test.
"""

import time

from django.core.management.base import BaseCommand

from catalogue import mail, search_alerts


class Command(BaseCommand):
    help = "Send search alert emails that are due."

    def handle(self, *args, **options):
        if not mail.enabled():
            self.stdout.write("Email is not set up (RIPRAPTOR_ZEPTOMAIL_TOKEN, RIPRAPTOR_MAIL_FROM). Nothing sent.")
            return
        started = time.monotonic()
        sent, failed = search_alerts.send_due(stdout=self.stdout)
        self.stdout.write(f"{sent} search alert emails sent, {failed} failed, in {time.monotonic() - started:.1f}s.")
