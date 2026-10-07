"""
Turn each listing's last "back in stock" time into a Restock row, once.

    python manage.py backfill_restocks

Restocks were stamped on listings before they were kept as their own rows,
so the restock log and the product page's restock line would start empty.
Safe to run again: a listing whose stamp is already a row is skipped.
"""

from django.core.management.base import BaseCommand
from django.db.models import F

from catalogue.models import Listing, Restock
from catalogue.pricing import MARKETPLACES


class Command(BaseCommand):
    help = "Create Restock rows from listings' back_in_stock_at stamps."

    def handle(self, *args, **options):
        listings = (
            Listing.objects.filter(back_in_stock_at__isnull=False)
            .exclude(retailer__source_type__in=MARKETPLACES)
            .exclude(restocks__at=F("back_in_stock_at"))
            .select_related("retailer")
        )
        created = 0
        for listing in listings.iterator():
            Restock.objects.create(
                product_id=listing.product_id, retailer_id=listing.retailer_id, listing=listing,
                at=listing.back_in_stock_at, price=listing.delivered_price, delivery_known=listing.delivery_known,
            )
            created += 1
        self.stdout.write(f"{created} restocks recorded.")
