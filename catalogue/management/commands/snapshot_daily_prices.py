from django.core.management.base import BaseCommand

from catalogue.pricing import snapshot_all


class Command(BaseCommand):
    help = (
        "Record today's cheapest delivered price for every product. Run once a day, "
        "for example from cron, so price history has no gaps."
    )

    def handle(self, *args, **options):
        count = snapshot_all()
        self.stdout.write(f"Recorded prices for {count} products.")
