"""Print a random sample of possible duplicates from each certainty level, to check the levels by hand.

    python manage.py certainty_sample --per-level 20
"""

import random

from django.core.management.base import BaseCommand

from catalogue.checks import duplicates
from catalogue.management.commands.merge_duplicates import CERTAINTY_LABELS


class Command(BaseCommand):
    help = "Print a random sample of possible duplicates from each certainty level."

    def add_arguments(self, parser):
        parser.add_argument("--per-level", type=int, default=20)
        parser.add_argument("--seed", type=int, default=None)

    def handle(self, *args, per_level=20, seed=None, **options):
        rows = [(keep, other) for keep, others in duplicates() for other in others]
        rng = random.Random(seed)
        for level, label in CERTAINTY_LABELS.items():
            chosen = [row for row in rows if row[1].certainty == level]
            self.stdout.write(f"== {label}: {len(chosen)} pairs, sample {min(per_level, len(chosen))}")
            for keep, other in rng.sample(chosen, min(per_level, len(chosen))):
                self.stdout.write(f"  keep {keep.pk} {keep.name}  |  other {other.pk} {other.name}")
