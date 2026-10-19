"""
Look after the refer a friend keyring offer.

    python manage.py tidy_referrals

Sends address emails that failed to go, cancels referrals held for review that the owner has not approved in
30 days, and deletes what is no longer needed: unconfirmed sign-ups after 14 days, unused links 90 days after
confirming, unused address links 14 days after they expire, names and addresses 60 days after posting, and
cancelled referrals after 14 days. Also notes in the cache whether the offer is open, for the footer link.
Hourly from cron (deploy/crontab); safe to run at any time.
"""

from django.core.management.base import BaseCommand

from catalogue import referrals


class Command(BaseCommand):
    help = "Send waiting refer a friend emails and delete details no longer needed."

    def handle(self, *args, **options):
        counts = referrals.tidy()
        self.stdout.write("Refer a friend tidy: " + ", ".join(f"{name} {count}" for name, count in counts.items()) + ".")
