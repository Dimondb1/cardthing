"""
Add barcodes to products from a CSV file.

    python manage.py import_barcodes barcodes.csv

The file needs a ``ean`` column and one of ``slug`` or ``name`` to say which
product it belongs to. Names are matched exactly, ignoring case. A row whose
product cannot be found is reported and skipped.
"""

import csv

from django.core.management.base import BaseCommand, CommandError

from catalogue.importers import clean_ean
from catalogue.models import Product


class Command(BaseCommand):
    help = "Set product barcodes from a CSV with columns ean and slug (or name)."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, path, **options):
        try:
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        except OSError as exc:
            raise CommandError(str(exc))
        fields = {name.strip().lower(): name for name in (rows[0].keys() if rows else [])}
        if "ean" not in fields or not ({"slug", "name"} & set(fields)):
            raise CommandError("The CSV needs an ean column and a slug or name column.")

        updated, skipped = 0, []
        for row in rows:
            ean = clean_ean(row.get(fields["ean"], ""))
            slug = (row.get(fields.get("slug", ""), "") or "").strip()
            name = (row.get(fields.get("name", ""), "") or "").strip()
            if not ean:
                skipped.append(f"{slug or name}: no valid barcode")
                continue
            product = None
            if slug:
                product = Product.objects.filter(slug=slug).first()
            if product is None and name:
                product = Product.objects.filter(name__iexact=name).first()
            if product is None:
                skipped.append(f"{slug or name}: product not found")
                continue
            if product.ean != ean:
                product.ean = ean
                product.save(update_fields=["ean"])
                updated += 1
        self.stdout.write(f"Barcodes set on {updated} products.")
        for line in skipped:
            self.stdout.write("Skipped " + line)
