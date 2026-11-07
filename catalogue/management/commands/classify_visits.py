"""
Mark the visitors and clicks of the last 36 days that were scripts though they looked like people
(catalogue/automated.py): those that sent a shop link without our page, floods of one-page visits from abroad with
no link to us, and clicks from browsers that never ran one of our pages. tidy_all runs it hourly, the background
reader on today every few minutes; the installer runs it once after an update.

    python manage.py classify_visits
    python manage.py classify_visits --days 7
"""

from django.core.management.base import BaseCommand

from catalogue import automated


class Command(BaseCommand):
    help = "Mark the automated visitors and clicks of the last days, and leave them out of every count."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=automated.WINDOW_DAYS)

    def handle(self, *args, days=automated.WINDOW_DAYS, **options):
        done = automated.classify(days=days)
        if done["direct"] or done["flood"] or done["clicks"]:
            from catalogue.signals import clear_list_caches

            clear_list_caches(force=True)
        self.stdout.write(f"{done['direct']} visitors that sent a shop link without our page, {done['flood']} in "
                          f"floods, {done['clicks']} clicks and {done['views']} page counts put right.")
