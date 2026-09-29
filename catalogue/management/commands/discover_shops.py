"""
Set up shops from a list of addresses and import their prices in one go.

    python manage.py discover_shops https://shop-one.co.uk https://shop-two.com

For each address: works out whether it is a Shopify store or a general
website with product pages, creates the retailer if it does not exist, then
runs a price import with name matching. Prints what was found so you can go
to Admin > Shop products to review for anything uncertain.
"""

import json
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from catalogue.importers import ImportError_, fetch, run_import, sitemap_urls
from catalogue.models import Retailer


def shop_name(url):
    host = urlsplit(url).netloc.removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title()


class Command(BaseCommand):
    help = "Add shops from their addresses and import prices from each."

    def add_arguments(self, parser):
        parser.add_argument("urls", nargs="+")
        parser.add_argument("--no-import", action="store_true", help="Only add the retailers.")

    def handle(self, *args, urls, no_import=False, **options):
        for url in urls:
            base = url.strip().rstrip("/")
            if not base.startswith("http"):
                base = "https://" + base
            host = urlsplit(base).netloc
            base = f"{urlsplit(base).scheme}://{host}"
            source = None
            try:
                data = json.loads(fetch(f"{base}/products.json?limit=1"))
                if "products" in data:
                    source = Retailer.Source.SHOPIFY
            except (ImportError_, json.JSONDecodeError):
                pass
            if source is None and sitemap_urls(base, limit=5):
                source = Retailer.Source.WEBSITE
            if source is None:
                self.stdout.write(f"{host}: no product data or sitemap found. Ask them for a feed.")
                continue
            retailer, created = Retailer.objects.get_or_create(
                slug=slugify(host.removeprefix("www.")),
                defaults={"name": shop_name(base), "website": base + "/", "source_type": source, "source_url": base + "/"},
            )
            if not created and retailer.source_type == Retailer.Source.MANUAL:
                retailer.source_type, retailer.source_url = source, base + "/"
                retailer.save(update_fields=["source_type", "source_url"])
            self.stdout.write(f"{retailer.name}: {retailer.get_source_type_display()} ({'added' if created else 'already set up'})")
            if no_import:
                continue
            run = run_import(retailer)
            if run.error:
                self.stdout.write(f"  import failed: {run.error}")
                continue
            review = run.unmatched.count("maybe") if run.unmatched else 0
            self.stdout.write(
                f"  {run.offers_found} products seen, {run.listings_updated} priced, {review} to review in admin."
            )
        self.stdout.write("Delivery charges default to 0: set each retailer's standard delivery in admin.")
