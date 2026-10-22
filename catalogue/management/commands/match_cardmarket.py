"""
Ask Claude which Cardmarket product each of ours is (catalogue/cardmarket_match.py), Pokémon first, then every
other game Cardmarket covers.

    python manage.py match_cardmarket                  # what cron runs hourly, within Claude's limits
    python manage.py match_cardmarket --game pokemon   # one game
    python manage.py match_cardmarket --dry-run        # how many would be asked, asking none
"""

from django.core.management.base import BaseCommand

from catalogue import cardmarket, cardmarket_match


class Command(BaseCommand):
    help = "Match our products to Cardmarket's with Claude."

    def add_arguments(self, parser):
        parser.add_argument("--game", action="append", choices=sorted(cardmarket.GAMES))
        parser.add_argument("--dry-run", action="store_true", help="Shortlist and count, asking Claude nothing.")
        parser.add_argument("--limit", type=int, default=cardmarket_match.ROWS_PER_RUN)

    def handle(self, *args, game=None, dry_run=False, limit=None, **options):
        result = cardmarket_match.run(game or cardmarket_match.ORDER, dry_run=dry_run, limit=limit)
        what = "would ask" if dry_run else "asked"
        self.stdout.write(f"Cardmarket matching: {what} {result.asked}, matched {result.matched}, to check {result.check}, "
                          f"none {result.none}, spent ${result.spent / 1_000_000:.2f}. {result.note}".strip())
