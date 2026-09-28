from django.core.management.base import BaseCommand

from content.models import SiteContent
from content.registry import REGISTRY
from content.service import sync_defaults


class Command(BaseCommand):
    help = (
        "Create any missing site text from content/registry.py. Edited wording is "
        "kept. This also runs automatically after migrate."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete rows whose key is no longer in the registry.",
        )
        parser.add_argument(
            "--reset",
            nargs="+",
            metavar="KEY",
            help="Put these keys back to their default wording. Use 'all' for every key.",
        )

    def handle(self, *args, prune=False, reset=None, **options):
        sync_defaults(prune=prune, stdout=self.stdout)
        if reset:
            rows = SiteContent.objects.all()
            if reset != ["all"]:
                unknown = [key for key in reset if key not in REGISTRY]
                if unknown:
                    self.stderr.write("Unknown keys: " + ", ".join(unknown))
                rows = rows.filter(key__in=reset)
            count = 0
            for row in rows:
                row.content = row.default_content
                row.active = True
                row.save()
                count += 1
            self.stdout.write(f"Reset {count} to the default wording.")
