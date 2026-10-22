"""
Name the sports game Sports cards where it still has its football name, and file its products under their
product lines (catalogue/sports.py). tidy_all files new products each hour; install.sh runs this once after an
update, so the lines are on the site straight away.

    python manage.py file_sports_cards
"""

from django.core.management.base import BaseCommand

from catalogue import sports
from catalogue.models import Game


class Command(BaseCommand):
    help = "Name the sports game Sports cards and file its products under their product lines."

    def handle(self, *args, **options):
        if sports.rename_game():
            self.stdout.write(f"Football cards is now {sports.NAME}.")
        game = Game.objects.filter(slug=sports.GAME).first()
        filed = sports.attach(game) if game is not None else 0
        self.stdout.write(f"{filed} sports card products filed under their product lines.")
