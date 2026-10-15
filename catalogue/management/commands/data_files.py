"""
The owner's catalogue as a spreadsheet (catalogue/workbook.py and catalogue/data_jobs.py).

    python manage.py data_files --export data.xlsx                  # write the workbook to a file
    python manage.py data_files --export data.xlsx --left-out zatu-games

--export writes the workbook a download would give, and keeps it as a download with the values it was made
with, so the file can be uploaded on the Your data page like one made there. --left-out adds one shop's
items its latest read left out.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalogue import data_jobs
from catalogue.models import DataExport, Retailer


class Command(BaseCommand):
    help = "Write the catalogue as an Excel workbook for the owner to tidy and upload."

    def add_arguments(self, parser):
        parser.add_argument("--export", metavar="PATH", help="Write the workbook to this file.")
        parser.add_argument("--left-out", metavar="SHOP_SLUG", help="Add the items this shop's latest read left out.")

    def handle(self, *args, export=None, left_out=None, **options):
        if export:
            return self.export(Path(export), left_out)
        if left_out:
            raise CommandError("--left-out goes with --export.")
        raise CommandError("Say what to do: --export PATH writes the workbook.")

    def export(self, path, left_out):
        retailer = None
        if left_out:
            retailer = Retailer.objects.filter(slug=left_out).first()
            if retailer is None:
                raise CommandError(f"No shop has the slug {left_out}.")
        row = DataExport.objects.create(left_out_retailer=retailer)
        data = data_jobs.build_export(row)
        if data is None:
            row.refresh_from_db(fields=["error"])
            raise CommandError(row.error)
        path.write_bytes(data)
        row.refresh_from_db(fields=["counts"])
        rows = ", ".join(f"{name} {count:,}" for name, count in row.counts.items())
        self.stdout.write(f"Wrote {path} ({len(data) / 1e6:.1f} MB), file {row.reference}: {rows}.")
