"""
Read the release sources that are due: publishers' and community lists of announced sets and dates.

    python manage.py scan_releases                         # every source that is due
    python manage.py scan_releases --source scryfall_sets  # one source, when it is due
    python manage.py scan_releases --dry-run               # show what the due sources say, write nothing
                                                           # but the web pages it used (they count
                                                           # against the 25 a day)

The background reader does this on its own every few hours; this is for a first look on a new server.
A source is read only when its turn has come (Scryfall once a day, the rest every 6 hours), so running
this again straight away reads nothing. What the rules let through becomes a set with its date; the rest
waits on the Things to check page. See catalogue/releases.py.
"""

from django.core.management.base import BaseCommand, CommandError

from catalogue import releases


class Command(BaseCommand):
    help = "Read the release sources that are due for announced sets and release dates."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=sorted(releases.BY_NAME), help="Read only this source.")
        parser.add_argument("--dry-run", action="store_true", help="Show what each source says and write nothing but the web pages used, "
                                                                    "which count against the day's allowance.")

    def handle(self, *args, source=None, dry_run=False, **options):
        names = [source] if source else [s.name for s in releases.SOURCES]
        if source and source not in releases.BY_NAME:
            raise CommandError(f"No release source called {source}.")
        for name in names:
            result = releases.scan(name, dry_run=dry_run)
            self.stdout.write(str(result))
            if dry_run:
                for signal in result.signals:
                    when = signal.release_date.isoformat() if signal.release_date else "no date"
                    if signal.precision == releases.MONTH:
                        when = f"{signal.release_date:%B %Y}, month only"
                    code = f" [{signal.code}]" if signal.code else ""
                    self.stdout.write(f"  {signal.name}{code}: {when}")
