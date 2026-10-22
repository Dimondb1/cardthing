"""
Move the sports game to /games/sports-cards/ and the name Sports cards where it still has its football ones,
and file its products under their product lines (catalogue/sports.py). tidy_all files new products each hour;
install.sh runs this once after an update, so the lines are on the site straight away.

    python manage.py file_sports_cards
"""

from django.core.management.base import BaseCommand

from catalogue import sports
from catalogue.models import Game


class Command(BaseCommand):
    help = "Move the sports game to its sports address and name, and file its products under their product lines."

    def handle(self, *args, **options):
        for change in sports.rename_game():
            self.stdout.write(f"Sports cards: {change}.")
        game = Game.objects.filter(slug=sports.GAME).first()
        filed = sports.attach(game) if game is not None else 0
        self.stdout.write(f"{filed} sports card products filed under their product lines.")
