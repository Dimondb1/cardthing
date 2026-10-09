"""
Run every cleanup in the right order after an import or a code update.

    python manage.py tidy_all

tidy_listings (a shop item linked to the wrong product), merge_duplicates
(one product under two names), tidy_catalogue (products that no longer pass
the rules), then files products without a set under the set their name or
code names (catalogue/releases.py attach_sets), sets each product's language from its name and set
(catalogue/languages.py), forgets website shop pages
no sitemap has listed for 30 days, clears old import records (catalogue/housekeeping.py) so
the database stays small, and lets the autopilot answer the Things to check rows the evidence
settles (catalogue/autopilot.py). Run by start.bat, update_prices and the server's hourly import,
so a rule added in code is applied to data already in the database.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Run tidy_listings, merge_duplicates and tidy_catalogue in order, file products under their sets, "
            "then forget pages gone from shop sitemaps.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what each would do and change nothing.")

    def handle(self, *args, dry_run=False, **options):
        for name in ("tidy_listings", "merge_duplicates", "tidy_catalogue"):
            self.stdout.write(f"== {name}")
            call_command(name, dry_run=dry_run, stdout=self.stdout)
        from django.db import transaction

        from catalogue.importers import forget_pages
        from catalogue.releases import attach_sets
        from catalogue.models import Game

        self.stdout.write("== sets")
        filed = 0
        for game in Game.objects.all():
            # One transaction per game, so a writer waiting behind this one waits only briefly.
            with transaction.atomic():
                filed += attach_sets(game)
                if dry_run:
                    transaction.set_rollback(True)
        self.stdout.write(f"{filed} products {'would be filed' if dry_run else 'filed'} under their sets.")

        from catalogue.languages import refresh_languages

        self.stdout.write("== languages")
        changed = refresh_languages(dry_run=dry_run)
        self.stdout.write(f"{changed} product languages {'would change' if dry_run else 'set'}.")

        self.stdout.write("== shop pages")
        gone = forget_pages(dry_run=dry_run)
        self.stdout.write(f"{gone} shop pages {'would be forgotten' if dry_run else 'forgotten'}.")

        self.stdout.write("== import records")
        if dry_run:
            self.stdout.write("Old import records would be cleared.")
        else:
            from catalogue.housekeeping import prune_runs

            cleared, deleted = prune_runs()
            self.stdout.write(f"{cleared} lists of unmatched products cleared, {deleted} old runs deleted.")

        from catalogue import autopilot

        if autopilot.enabled():
            self.stdout.write("== autopilot")
            done = autopilot.run(dry_run=dry_run)
            for kind, what, why in done:
                self.stdout.write(f"{what}: {why}.")
            self.stdout.write(f"{len(done)} things to check {'would be answered' if dry_run else 'answered'}.")
