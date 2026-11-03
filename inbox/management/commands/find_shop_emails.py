"""
Find the business address of each shop that has none, from the shop's own website (inbox/shop_emails.py).

    python manage.py find_shop_emails                  # every shop with no address, not looked at in 30 days
    python manage.py find_shop_emails --shop jet-cards # one shop
    python manage.py find_shop_emails --dry-run        # say what would be kept, keep nothing
    python manage.py find_shop_emails --force          # look again, even at shops looked at lately

The Update and badge emails page starts it from a button. Only an address on the shop's own domain, such as info@
or hello@, is kept, and never one the owner typed.
"""

from django.core.management.base import BaseCommand

from inbox import shop_emails


class Command(BaseCommand):
    help = "Find shops' business addresses on their own websites, for Shop outreach."

    def add_arguments(self, parser):
        parser.add_argument("--shop", default="", help="One shop, by its slug.")
        parser.add_argument("--limit", type=int, default=None, help="Look at no more than this many shops.")
        parser.add_argument("--force", action="store_true", help="Look again, even at shops looked at lately.")
        parser.add_argument("--dry-run", action="store_true", help="Say what would be kept, and keep nothing.")

    def handle(self, *args, shop="", limit=None, force=False, dry_run=False, **options):
        result = shop_emails.run(limit=limit, only=shop, force=force, dry_run=dry_run, report=self.stdout.write)
        self.stdout.write(f"{'Would keep' if dry_run else 'Kept'} {result.found} addresses; none found for "
                          f"{result.none} shops; {result.skipped} left for later.")
