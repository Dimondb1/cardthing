"""
Tell Bing and the other IndexNow search engines which product pages changed since the last run, and once a day which
game, set and guide pages did (catalogue/indexnow.py). The hourly cron runs it.

    python manage.py indexnow [--dry-run] [--check]
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalogue import indexnow


class Command(BaseCommand):
    help = "Send the product pages changed since the last run to IndexNow (Bing and others)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count the pages and send nothing.")
        parser.add_argument("--check", action="store_true",
                            help="Say where the key file is, whether the site serves it, and what the last run sent.")

    def handle(self, *args, dry_run=False, check=False, **options):
        if check:
            found = indexnow.check()
            served = {True: "served by the site", False: "NOT served by the site",
                      None: "not checked: the site could not be reached from here"}[found["served"]]
            self.stdout.write(f"IndexNow is {'on' if settings.RIPRAPTOR_INDEXNOW else 'off (RIPRAPTOR_INDEXNOW=0)'}.")
            self.stdout.write(f"Key file: {found['key_url']} ({served}).")
            last = found["last"]
            self.stdout.write(f"Last accepted run: {last['at']:%-d %b %Y %H:%M} UTC, {last['sent']} addresses, "
                              f"{last['pages']} of them game, set, guide and list pages." if last
                              else "Last accepted run: none recorded yet.")
            sent = found["pages_sent"]
            self.stdout.write(f"Game, set and guide pages last sent: {sent:%-d %b %Y %H:%M} UTC." if sent
                              else "Game, set and guide pages last sent: not yet. They go with the next run.")
            return
        if not settings.RIPRAPTOR_INDEXNOW and not dry_run:
            self.stdout.write("IndexNow is off (RIPRAPTOR_INDEXNOW=0).")
            return
        try:
            sent = indexnow.run(dry_run=dry_run)
        except indexnow.IndexNowError as exc:
            raise CommandError(f"{exc}. The same pages go again next hour.") from exc
        self.stdout.write(f"{sent} changed pages {'would be sent' if dry_run else 'sent'} to IndexNow.")
