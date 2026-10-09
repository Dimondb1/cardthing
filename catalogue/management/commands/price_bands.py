"""
Work out the usual price range of each kind of product in each game and set, and print it.

    python manage.py price_bands
    python manage.py price_bands --dry-run

A band needs eight or more products with an OK, buyable, delivered shop price. A price with fewer than
two other shops to compare against is judged against its band: far under it is kept out, well under it
or far over it is doubtful. snapshot_daily_prices rebuilds the bands every night, so this is only
needed to look at them or to rebuild them at once.
"""

from django.core.management.base import BaseCommand

from catalogue.models import Game, ProductSet
from catalogue.sanity import BAND_MIN_PRODUCTS, rebuild_bands
from catalogue.types import type_label


class Command(BaseCommand):
    help = "Rebuild the usual price range of each product type per game and set, and print the table."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Work the bands out and print them without saving.")

    def handle(self, *args, **options):
        bands = rebuild_bands(dry_run=options["dry_run"])
        games = {game.pk: game for game in Game.objects.filter(pk__in={band[0] for band in bands})}
        sets = dict(ProductSet.objects.filter(pk__in={band[2] for band in bands if band[2]}).values_list("pk", "name"))
        for game_id, product_type, set_id, n, p10, mid, p90 in bands:
            game = games[game_id]
            where = sets.get(set_id, "all sets")
            self.stdout.write(
                f"{game.name}, {type_label(game.slug, product_type)}, {where}: "
                f"{n} products, £{p10} / £{mid} / £{p90} (cheapest tenth / middle / dearest tenth)"
            )
        verb = "would be saved" if options["dry_run"] else "saved"
        self.stdout.write(f"{len(bands)} bands {verb}. A band needs {BAND_MIN_PRODUCTS} or more products.")
