"""
Create the UK shops RipRaptor reads prices from, with their delivery rules.

Safe to run again: existing shops keep any changes made in admin. Delivery
figures come from each shop's own delivery page and the date they were read
is in the note, so they can be checked.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from catalogue.models import Retailer

S = Retailer.Source
SHOPS = [
    # slug, name, website, source type, delivery, free over, note
    ("total-cards", "Total Cards", "https://totalcards.net/", S.SHOPIFY, "3.75", "20",
     "Free delivery on most orders over £20 (read 29 Sep 2026)"),
    ("gathering-games", "Gathering Games", "https://gatheringgames.co.uk/", S.SHOPIFY, "3.99", "100",
     "Royal Mail Tracked 48 £3.99, free over £100 (read 29 Sep 2026)"),
    ("poke-collect", "Poke-Collect", "https://poke-collect.com/", S.SHOPIFY, "0", None,
     "Delivery charge not yet confirmed"),
    ("magic-madhouse", "Magic Madhouse", "https://magicmadhouse.co.uk/", S.WEBSITE, "0", "40",
     "Free UK delivery from £40; standard charge not yet confirmed (read 29 Sep 2026)"),
]


class Command(BaseCommand):
    help = "Add the shops RipRaptor reads prices from. Run import_prices afterwards."

    def handle(self, *args, **options):
        for slug, name, website, source, cost, free, note in SHOPS:
            existing = Retailer.objects.filter(website=website).first() or Retailer.objects.filter(slug=slug).first()
            if existing:
                if existing.name != name or existing.slug != slug:
                    existing.name, existing.slug = name, slug
                    existing.save(update_fields=["name", "slug"])
                self.stdout.write(f"{name}: already set up")
                continue
            Retailer.objects.create(
                slug=slug, name=name, website=website, source_type=source, source_url=website,
                delivery_cost=Decimal(cost), free_delivery_over=Decimal(free) if free else None, delivery_note=note,
            )
            self.stdout.write(f"{name}: added")
