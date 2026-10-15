"""
The owner's catalogue as a spreadsheet (catalogue/workbook.py and catalogue/data_jobs.py).

    python manage.py data_files                                     # run the queued jobs from the Your data page
    python manage.py data_files --export data.xlsx                  # write the workbook to a file
    python manage.py data_files --export data.xlsx --left-out zatu-games
    python manage.py data_files --check data.xlsx                   # what an edited file would change

--export writes the workbook a download would give, and keeps it as a download with the values it was made
with, so the file can be uploaded on the Your data page like one made there. --left-out adds one shop's
items its latest read left out.

--check reads an edited workbook and prints, in plain words, every change it would make and every change it
would not, with why. It changes nothing: changes are made only from the Your data page, after its preview.

With no arguments it runs what the Your data page queued (catalogue/data_jobs.py): downloads to make,
uploads to check, files to apply or undo. The page starts it at once in its own process; a lock file in
the cache directory keeps a second copy from running. It runs at a lower priority than the site, and is the
first process the memory killer stops on the 1 GB server, so the site's own workers keep going.
"""

import fcntl
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalogue import data_jobs
from catalogue.models import DataChange, DataExport, Retailer

# The preview's sections, in its order.
SECTIONS = (
    (DataChange.State.READY, "Ready"),
    (DataChange.State.HELD, "Held for your say"),
    (DataChange.State.PROBLEM, "Not made"),
    (DataChange.State.CONFLICT, "Changed on the site since your download"),
    (DataChange.State.SAME, "Already so"),
)


class Command(BaseCommand):
    help = "Run the Your data page's queued spreadsheet jobs, or write or check a workbook."

    def add_arguments(self, parser):
        parser.add_argument("--export", metavar="PATH", help="Write the workbook to this file.")
        parser.add_argument("--left-out", metavar="SHOP_SLUG", help="Add the items this shop's latest read left out.")
        parser.add_argument("--check", metavar="PATH", help="Print what this edited workbook would change. "
                            "Changes nothing.")

    def handle(self, *args, export=None, left_out=None, check=None, **options):
        if export and check:
            raise CommandError("Give --export or --check, not both.")
        if export:
            return self.export(Path(export), left_out)
        if left_out:
            raise CommandError("--left-out goes with --export.")
        if check:
            return self.check_file(Path(check))
        return self.run_jobs()

    def run_jobs(self):
        folder = Path(settings.RIPRAPTOR_CACHE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "data-files.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return None   # a runner is going already, and takes this job too
            if not getattr(settings, "TESTING", False):
                lower_priority()
            data_jobs.run(timezone.now() + data_jobs.RUN_FOR)
        return None

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

    def check_file(self, path):
        from catalogue import workbook, workbook_plan

        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CommandError(str(exc))
        try:
            parsed = workbook.read_upload(data)
            export = workbook_plan.export_for(parsed.reference)
            changes = workbook_plan.plan(export, parsed)
        except workbook.FileRefused as exc:
            raise CommandError(str(exc))
        write = self.stdout.write
        write(f"File {export.reference}, downloaded {timezone.localtime(export.created_at):%d/%m/%Y %H:%M}.")
        write(workbook_plan.summary(changes))
        write("Nothing was changed: this was a check.")
        for state, heading in SECTIONS:
            rows = [change for change in changes if change.state == state]
            if not rows:
                continue
            write(f"\n{heading} ({len(rows):,})")
            for change in rows:
                write(f"  {change.what}")
                if change.why and state != DataChange.State.SAME:
                    write(f"    {change.why}")
                if change.warning:
                    write(f"    Note: {change.warning}")
        if parsed.unread:
            write(f"\nNot read ({len(parsed.unread):,})")
            for line in parsed.unread:
                write(f"  {line}")


def lower_priority():
    """Below the site's workers for the processor, and first to go when memory runs out: a spreadsheet can
    wait, a visitor cannot."""
    try:
        os.nice(10)
    except OSError:
        pass
    try:
        Path("/proc/self/oom_score_adj").write_text("500")
    except OSError:
        pass
