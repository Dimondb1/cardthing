"""
Load the real product catalogue: games, recent sets and their sealed
products. No retailers, prices or images are created; those come from
retailers you add in admin and ``import_prices``.

Check release dates and add barcodes (EANs) in admin. Barcodes are what the
price importers use to match a retailer's product to ours.
"""

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from catalogue.models import Game, Product, ProductSet

T = Product.Type

GAMES = [
    ("Pokémon", "Pokémon", "pokemon", "pokemon, ptcg, pkmn"),
    ("Magic: The Gathering", "Magic", "magic-the-gathering", "mtg, magic"),
    ("One Piece Card Game", "One Piece", "one-piece", "optcg"),
    ("Disney Lorcana", "Lorcana", "lorcana", ""),
]

# game slug -> [(set name, code, release date, [(product name, type)])]
SETS = {
    "pokemon": [
        ("Destined Rivals", "DRI", date(2025, 5, 30), [
            ("Destined Rivals Booster Box", T.BOOSTER_BOX),
            ("Destined Rivals Elite Trainer Box", T.ELITE_TRAINER_BOX),
            ("Destined Rivals Booster Bundle", T.BUNDLE),
        ]),
        ("Journey Together", "JTG", date(2025, 3, 28), [
            ("Journey Together Booster Box", T.BOOSTER_BOX),
            ("Journey Together Elite Trainer Box", T.ELITE_TRAINER_BOX),
            ("Journey Together Booster Bundle", T.BUNDLE),
        ]),
        ("Prismatic Evolutions", "PRE", date(2025, 1, 17), [
            ("Prismatic Evolutions Elite Trainer Box", T.ELITE_TRAINER_BOX),
            ("Prismatic Evolutions Booster Bundle", T.BUNDLE),
            ("Prismatic Evolutions Super-Premium Collection", T.COLLECTION_BOX),
        ]),
        ("Surging Sparks", "SSP", date(2024, 11, 8), [
            ("Surging Sparks Booster Box", T.BOOSTER_BOX),
            ("Surging Sparks Elite Trainer Box", T.ELITE_TRAINER_BOX),
            ("Surging Sparks Booster Bundle", T.BUNDLE),
        ]),
    ],
    "magic-the-gathering": [
        ("Edge of Eternities", "EOE", date(2025, 8, 1), [
            ("Edge of Eternities Play Booster Box", T.BOOSTER_BOX),
            ("Edge of Eternities Collector Booster Box", T.BOOSTER_BOX),
            ("Edge of Eternities Bundle", T.BUNDLE),
        ]),
        ("Final Fantasy", "FIN", date(2025, 6, 13), [
            ("Final Fantasy Play Booster Box", T.BOOSTER_BOX),
            ("Final Fantasy Collector Booster Box", T.BOOSTER_BOX),
            ("Final Fantasy Bundle", T.BUNDLE),
        ]),
        ("Tarkir: Dragonstorm", "TDM", date(2025, 4, 11), [
            ("Tarkir: Dragonstorm Play Booster Box", T.BOOSTER_BOX),
            ("Tarkir: Dragonstorm Bundle", T.BUNDLE),
        ]),
    ],
    "one-piece": [
        ("A Fist of Divine Speed", "OP-11", date(2025, 6, 6), [
            ("A Fist of Divine Speed Booster Box (OP-11)", T.BOOSTER_BOX),
        ]),
        ("Royal Blood", "OP-10", date(2025, 3, 21), [
            ("Royal Blood Booster Box (OP-10)", T.BOOSTER_BOX),
        ]),
    ],
    "lorcana": [
        ("Fabled", "", date(2025, 9, 5), [
            ("Fabled Booster Box", T.BOOSTER_BOX),
            ("Fabled Illumineer's Trove", T.COLLECTION_BOX),
        ]),
        ("Reign of Jafar", "", date(2025, 5, 30), [
            ("Reign of Jafar Booster Box", T.BOOSTER_BOX),
            ("Reign of Jafar Illumineer's Trove", T.COLLECTION_BOX),
        ]),
        ("Archazia's Island", "", date(2025, 3, 7), [
            ("Archazia's Island Booster Box", T.BOOSTER_BOX),
            ("Archazia's Island Illumineer's Trove", T.COLLECTION_BOX),
        ]),
    ],
}


class Command(BaseCommand):
    help = "Load games, sets and products. Safe to run again: existing rows are kept."

    def handle(self, *args, **options):
        created = 0
        with transaction.atomic():
            for order, (name, short, slug, aliases) in enumerate(GAMES, start=1):
                game, _ = Game.objects.get_or_create(
                    slug=slug,
                    defaults={"name": name, "short_name": short, "search_aliases": aliases, "sort_order": order},
                )
                for set_name, code, released, products in SETS.get(slug, []):
                    product_set, _ = ProductSet.objects.get_or_create(
                        game=game, slug=slugify(set_name),
                        defaults={"name": set_name, "code": code, "release_date": released},
                    )
                    for product_name, product_type in products:
                        _, was_created = Product.objects.get_or_create(
                            slug=slugify(product_name),
                            defaults={"game": game, "product_set": product_set,
                                      "name": product_name, "product_type": product_type},
                        )
                        created += was_created
        self.stdout.write(
            f"Catalogue loaded: {created} products added, {Product.objects.count()} in total. "
            "Add barcodes in admin so price imports can match them."
        )
