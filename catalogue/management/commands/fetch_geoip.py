"""
Download this month's free country database for visitor locations.

    python manage.py fetch_geoip

Saves it at RIPRAPTOR_GEOIP_DB. Run monthly from cron; the installer does.
"""

from django.core.management.base import BaseCommand, CommandError

from catalogue import geo


class Command(BaseCommand):
    help = "Download the DB-IP country database used for visitor countries."

    def handle(self, *args, **options):
        try:
            path = geo.fetch()
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"Country database saved to {path}")
