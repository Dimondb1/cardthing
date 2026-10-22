"""
Download Cardmarket's sealed product catalogue and today's price guide, and the ECB's euro to pound rate, and
keep them (catalogue/cardmarket.py).

    python manage.py import_cardmarket                 # every game Cardmarket has (what cron runs daily)
    python manage.py import_cardmarket --game pokemon  # one game
"""

from django.core.management.base import BaseCommand

from catalogue import cardmarket


class Command(BaseCommand):
    help = "Download Cardmarket's sealed catalogue and price guide, and the ECB rate."

    def add_arguments(self, parser):
        parser.add_argument("--game", action="append", choices=sorted(cardmarket.GAMES),
                            help="Only this game (may be given more than once).")

    def handle(self, *args, game=None, **options):
        rate, done, errors = cardmarket.run(game)
        if rate is not None:
            self.stdout.write(f"Rate for {rate.date}: £{rate.gbp_per_eur} to €1.")
        for row in done:
            self.stdout.write(f"{row.game}: {row.products} sealed products ({row.new} new), {row.prices} prices for {row.day}.")
        for error in errors:
            self.stdout.write(f"Not read: {error}")
