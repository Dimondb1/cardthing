"""
Let Claude answer the Things to check rows the autopilot leaves (catalogue/judge.py).

    python manage.py judge_checks            # run now if due: asked from the page, or an hour since the last run
    python manage.py judge_checks --dry-run  # list the rows, what would be sent and the most each could cost

Run by cron every five minutes on its own lock. It does nothing until the owner saves a key and switches
Claude on in Things to check, and nothing while Pause all is on.
"""

from django.core.management.base import BaseCommand

from catalogue import judge


class Command(BaseCommand):
    help = "Ask Claude about the Things to check rows the autopilot leaves, within the monthly limit."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Send nothing; list the rows and their evidence.")

    def handle(self, *args, dry_run=False, **options):
        try:
            result = judge.run(dry_run=dry_run, force=dry_run)
        except Exception:
            # The page says so, rather than leave the owner waiting for a run that died.
            if not dry_run:
                judge.record_fault()
            raise
        for line in result.lines:
            self.stdout.write(line)
        if result.asked or dry_run:
            self.stdout.write(f"Claude: asked {result.asked}, sorted {result.acted}, {result.suggested} suggested, "
                              f"{result.left} left, {judge.dollars(result.spent)} spent. {result.note}".strip())
        elif result.note and result.note not in ("Claude is off.", "Claude needs a key.", "Claude ran less than an hour ago."):
            self.stdout.write(f"Claude: {result.note}")
