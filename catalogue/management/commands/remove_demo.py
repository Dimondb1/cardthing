"""Remove the fictional demo retailers and their prices, keeping real ones."""

from django.core.management.base import BaseCommand

from catalogue.models import DailyLowestPrice, Listing, OutboundClick, Product, Retailer


class Command(BaseCommand):
    help = "Delete the fictional .example retailers from seed_demo, with their listings, clicks and price history."

    def handle(self, *args, **options):
        demo = Retailer.objects.filter(website__contains=".example")
        names = list(demo.values_list("name", flat=True))
        touched = set(Listing.objects.filter(retailer__in=demo).values_list("product_id", flat=True))
        OutboundClick.objects.filter(retailer__in=demo).delete()
        Listing.objects.filter(retailer__in=demo).delete()
        demo.delete()
        # Price history that came from demo listings only.
        orphaned = Product.objects.filter(pk__in=touched).exclude(listings__isnull=False)
        DailyLowestPrice.objects.filter(product__in=orphaned).delete()
        self.stdout.write(f"Removed {len(names)} demo retailers: {', '.join(names) or 'none'}.")
