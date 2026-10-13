"""Throwaway review probe. Deleted after the run."""
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from .ebay import junk, search_queries
from .importers import run_import
from .models import Product, Retailer
from .testing import make_retailer
from .tests_importers import shopify_page


def shop_item(handle, title, kind="Booster Box", price="59.99", vendor="Bandai", tags=()):
    return {"handle": handle, "title": title, "product_type": kind, "vendor": vendor, "tags": list(tags),
            "variants": [{"price": price, "available": True, "barcode": ""}]}


class Probe(TestCase):
    def load(self):
        call_command("import_products", str(Path(settings.BASE_DIR) / "catalogue" / "data" / "union_arena.csv"),
                     stdout=StringIO())

    def shop(self, name, items):
        shop = make_retailer(name, source_type=Retailer.Source.SHOPIFY, source_url=f"https://{name.lower()}.example/",
                             delivery_cost=Decimal("3.00"))
        page = shopify_page(items)
        run = run_import(shop, fetch=lambda url: page if "page=1" in url else shopify_page([]))
        self.assertTrue(run.ok, run.error)
        return shop

    def dump(self, label):
        print(f"\n=== {label}")
        for p in Product.objects.filter(game__slug="union-arena").order_by("pk"):
            print(f"  pk={p.pk} active={p.is_active} lang={p.language!r} type={p.product_type} name={p.name!r} "
                  f"listings={[l.retailer.slug for l in p.listings.all()]}")

    def test_japanese_ua_code_without_the_word_japanese(self):
        self.load()
        hxh = Product.objects.get(name="Hunter x Hunter Booster Box")
        self.shop("ShopJ", [shop_item("hxh-ua", "Union Arena Hunter x Hunter Booster Box UA02BT")])
        self.dump("after importing a UA02BT box (no word Japanese)")
        print("  eBay junk('Union Arena Hunter x Hunter Booster Box UA02BT Sealed'):",
              junk(hxh, "Union Arena Hunter x Hunter Booster Box UA02BT Sealed"))
        print("  eBay junk('Union Arena Hunter x Hunter Booster Box UE02BT Sealed'):",
              junk(hxh, "Union Arena Hunter x Hunter Booster Box UE02BT Sealed"))
        out = StringIO()
        call_command("merge_duplicates", "--dry-run", stdout=out)
        print("  merge_duplicates --dry-run:\n   ", out.getvalue().replace("\n", "\n    "))
        call_command("merge_duplicates", stdout=StringIO())
        self.dump("after merge_duplicates (strict, the hourly run)")

    def test_shop_titles_for_the_other_csv_products(self):
        self.load()
        self.shop("ShopB", [
            shop_item("bleach-box", "Union Arena TCG: Bleach: Thousand-Year Blood War [UE01BT] Booster Box"),
            shop_item("bleach-sd", "Union Arena - BLEACH Thousand-Year Blood War Starter Deck UE01ST", "Starter Deck", "19.99"),
            shop_item("tg-sd", "Union Arena TCG: Tokyo Ghoul UE18ST - Starter Deck", "Starter Deck", "19.99"),
            shop_item("rz-sd", "Union Arena: Re:ZERO -Starting Life in Another World- Starter Deck [UE12ST]", "Starter Deck", "19.99"),
            shop_item("rz-sd-2", "Union Arena Re:Zero Starter Deck UE12ST", "Starter Deck", "19.99"),
            shop_item("hxh-tail", "Hunter x Hunter Booster Box UE02BT - Union Arena"),
            shop_item("hxh-hyphen", "Union-Arena Hunter x Hunter Booster Box UE02BT"),
        ])
        self.dump("after a shop with titles for the CSV products")
        call_command("merge_duplicates", stdout=StringIO())
        self.dump("after merge_duplicates (strict)")
        for p in Product.objects.filter(game__slug="union-arena"):
            print("  ebay queries", repr(p.name), "->", search_queries(p))
