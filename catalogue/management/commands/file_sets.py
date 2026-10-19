"""
File products that have no set under their set, once, and say what was filed.

    python manage.py file_sets                 # file by the sets the site has, and list set codes in product
                                              # names for Announced sets to check
    python manage.py file_sets --backfill      # first read each release source's whole list (sets released
                                              # long ago included) and add the sets products name
    python manage.py file_sets --dry-run       # say what would happen and change nothing (with --backfill it
                                              # still reads the sources, and their web pages count against
                                              # the 25 a day)

The hourly tidy_all does the filing and the set codes on its own, and each release source's ordinary read adds
the back catalogue its answer holds; --backfill is for the first time, and reads the publishers' booster lists
and every TCGdex set as well. See catalogue/releases.py (Filing products under sets, The back catalogue).
"""

import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalogue import releases
from catalogue.models import Game, Product


def explain(name, rules, listed, game_slug):
    """Why a product is left with no set, in a few words."""
    if not rules:
        return "the game has no sets"
    chosen, why = releases.choose_set_why(name, rules, game_slug)
    if isinstance(chosen, tuple):
        return f"names {listed[chosen].name}, a set listed inside the release radar's window"
    return why


class DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "File products with no set under their set, optionally reading every release source's whole list first."

    def add_arguments(self, parser):
        parser.add_argument("--backfill", action="store_true", help="Read each release source's whole list first.")
        parser.add_argument("--source", choices=sorted(releases.BY_NAME), help="With --backfill, read only this source.")
        parser.add_argument("--dry-run", action="store_true", help="Change nothing; say what would happen.")
        parser.add_argument("--examples", type=int, default=10, help="Filed and unfiled products to show per game.")

    def handle(self, *args, backfill=False, source=None, dry_run=False, examples=10, **options):
        before = set(Product.objects.filter(is_active=True, product_set__isnull=False).values_list("pk", flat=True))
        if backfill:
            # Not in the transaction below: a dry run writes nothing here but the web pages it used.
            self.backfill(source, dry_run)
        try:
            with transaction.atomic():
                self.file(dry_run, before, examples)
                if dry_run:
                    raise DryRun
        except DryRun:
            self.stdout.write("Dry run: nothing was changed.")

    def backfill(self, only, dry_run):
        self.stdout.write("== release sources, whole lists")
        for name in [only] if only else [s.name for s in releases.SOURCES]:
            result = releases.backfill(name, dry_run=dry_run)
            self.stdout.write(str(result))

    def file(self, dry_run, before, examples):
        self.stdout.write("== filing")
        pick = random.Random(0)
        for game in Game.objects.order_by("name"):
            filed_now = releases.attach_sets(game)
            codes = releases.raise_name_candidates(game, dry_run=dry_run)
            products = list(Product.objects.filter(game=game, is_active=True).select_related("product_set"))
            filed = [p for p in products if p.product_set_id and p.pk not in before]
            left = [p for p in products if not p.product_set_id]
            had = len(products) - len(filed) - len(left)
            self.stdout.write(f"{game.name}: {len(products)} active, {had} had a set, {len(filed)} "
                              f"{'would be ' if dry_run else ''}filed now ({filed_now} with inactive ones), "
                              f"{len(left)} left with none.")
            rules, listed = releases.filing_rules(game, timezone.localdate())
            for product in pick.sample(filed, min(examples, len(filed))):
                why = releases.choose_set_why(product.name, rules, game.slug)[1]
                self.stdout.write(f"   filed  {product.name}  ->  {product.product_set.name}  ({why})")
            for product in pick.sample(left, min(examples, len(left))):
                why = explain(product.name, rules, listed, game.slug)
                self.stdout.write(f"   left   {product.name}  ({why})")
            if codes:
                self.stdout.write(f"   set codes for Announced sets to check: {', '.join(codes)}")
