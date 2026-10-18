"""
Send the drop tracker's reminder emails that are due.

    python manage.py send_drop_alerts

Each confirmed reminder hears at most three times, each once: when a shop opens pre-orders before the drop,
on the morning before a dated drop and on its morning (from 8am UK time). It is deleted after the drop-day
email. Unconfirmed reminders go after a week, and every reminder once its drop has passed. Safe to run
every 15 minutes, and twice at once: each email is claimed before it is sent. Meant for the cron entry
beside send_stock_alerts; test the email settings with send_stock_alerts --test.
"""

from django.core.management.base import BaseCommand

from catalogue import drop_alerts, mail


class Command(BaseCommand):
    help = "Send drop reminder emails that are due."

    def handle(self, *args, **options):
        if not mail.enabled():
            self.stdout.write("Email is not set up (RIPRAPTOR_ZEPTOMAIL_TOKEN, RIPRAPTOR_MAIL_FROM). Nothing sent.")
            return
        sent, failed = drop_alerts.send_due(stdout=self.stdout)
        self.stdout.write(f"{sent} drop reminder emails sent, {failed} failed.")
