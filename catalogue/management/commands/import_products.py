"""
Add or update products from a CSV file.

    python manage.py import_products products.csv

Columns: name, game (name or slug), type (booster_box, elite_trainer_box,
bundle, collection_box, deck, tin, booster_pack, gift_set, other), and
optionally set, set_code, ean, release_date (YYYY-MM-DD), image_url.
Products are matched by slug (made from the name) so a file can be loaded
again to correct details.
"""

import csv
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from catalogue.importers import clean_ean
from catalogue.models import Game, Product, ProductSet

TYPES = {value: value for value, _ in Product.Type.choices}
TYPES.update({label.lower(): value for value, label in Product.Type.choices})
TYPES.update({"etb": "elite_trainer_box", "box": "booster_box", "booster": "booster_box"})


class Command(BaseCommand):
    help = "Add or update products from a CSV (name, game, type, set, set_code, ean, release_date, image_url)."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, path, **options):
        try:
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        except OSError as exc:
            raise CommandError(str(exc))
        if not rows:
            raise CommandError("The file has no rows.")
        fields = {name.strip().lower(): name for name in rows[0].keys()}
        for needed in ("name", "game", "type"):
            if needed not in fields:
                raise CommandError(f"The CSV needs a '{needed}' column.")

        def col(row, name):
            return (row.get(fields.get(name, ""), "") or "").strip()

        created = updated = 0
        problems = []
        for number, row in enumerate(rows, start=2):
            name, game_name = col(row, "name"), col(row, "game")
            product_type = TYPES.get(col(row, "type").lower())
            if not name or not game_name or not product_type:
                problems.append(f"row {number}: needs a name, game and a known type")
                continue
            game = Game.objects.filter(slug=slugify(game_name)).first() or Game.objects.filter(name__iexact=game_name).first()
            if game is None:
                problems.append(f"row {number}: no game called '{game_name}'")
                continue
            product_set = None
            if col(row, "set"):
                product_set, _ = ProductSet.objects.get_or_create(
                    game=game, slug=slugify(col(row, "set")),
                    defaults={"name": col(row, "set"), "code": col(row, "set_code")},
                )
            release = None
            if col(row, "release_date"):
                try:
                    release = date.fromisoformat(col(row, "release_date"))
                except ValueError:
                    problems.append(f"row {number}: release_date must be YYYY-MM-DD")
                    continue
            values = {"name": name, "game": game, "product_set": product_set, "product_type": product_type}
            if col(row, "ean"):
                values["ean"] = clean_ean(col(row, "ean"))
            if release:
                values["release_date"] = release
            if col(row, "image_url"):
                values["image_url"] = col(row, "image_url")
            _, was_created = Product.objects.update_or_create(slug=slugify(name)[:220], defaults=values)
            created += was_created
            updated += not was_created
        self.stdout.write(f"{created} products added, {updated} updated.")
        for line in problems:
            self.stdout.write("Skipped " + line)
