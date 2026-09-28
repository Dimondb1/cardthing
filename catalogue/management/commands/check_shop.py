"""
Find out whether a shop can be imported before adding it as a retailer.

    python manage.py check_shop https://www.example-cards.co.uk/

Tries the Shopify products endpoint and reports what it finds. This only
reads the shop's public product list once. Check the shop's terms before
setting up a regular import.
"""

import json

from django.core.management.base import BaseCommand

from catalogue.importers import ImportError_, clean_ean, fetch


class Command(BaseCommand):
    help = "Report whether a shop publishes Shopify product data we can import."

    def add_arguments(self, parser):
        parser.add_argument("url", help="The shop's home page address.")

    def handle(self, *args, url, **options):
        base = url.rstrip("/")
        if not base.startswith("http"):
            base = "https://" + base
        try:
            raw = fetch(f"{base}/products.json?limit=250&page=1")
        except ImportError_ as exc:
            self.stdout.write(f"Not reachable: {exc}")
            self.stdout.write("Either the shop is not on Shopify, blocks automated requests, or the address is wrong.")
            return
        try:
            products = json.loads(raw).get("products", [])
        except json.JSONDecodeError:
            self.stdout.write("Reachable, but not a Shopify shop (no product data at /products.json).")
            self.stdout.write("It may still offer a CSV feed through an affiliate network.")
            return

        variants = [v for p in products for v in p.get("variants", [])]
        with_barcode = sum(1 for v in variants if clean_ean(v.get("barcode")))
        tcg_words = ("booster", "elite trainer", "bundle", "pokemon", "pokémon", "magic", "lorcana", "one piece")
        tcg = sum(1 for p in products if any(w in p.get("title", "").lower() for w in tcg_words))
        self.stdout.write(f"Shopify shop. First page: {len(products)} products, {len(variants)} variants.")
        self.stdout.write(f"Variants with a barcode: {with_barcode} of {len(variants)} (barcodes are how prices are matched).")
        self.stdout.write(f"Products that look like sealed TCG: {tcg}.")
        if len(products) == 250:
            self.stdout.write("There are more pages; the importer follows them.")
        if with_barcode == 0:
            self.stdout.write("No barcodes: this shop can only be matched by hand for now.")
        else:
            self.stdout.write("Add it in Admin > Retailers with price source 'Shopify store' and this address.")
