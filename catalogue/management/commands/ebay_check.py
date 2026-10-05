"""
Show what eBay has for one product and which listing the site would pick.

    python manage.py ebay_check https://ripraptor.com/products/darkness-ablaze-booster-pack/

Takes a product page address or its slug. Prints the eBay listing saved now,
then eBay's current results with the price delivered and, for each, whether
the import would accept it and why not. Spends one or two searches of the
daily allowance and saves nothing.
"""

from django.core.management.base import BaseCommand, CommandError

from catalogue import ebay
from catalogue.importers import Catalogue, Offer
from catalogue.models import Listing, Product, ProductAlias


class Command(BaseCommand):
    help = "Show eBay's results for one product and which one the import would pick."

    def add_arguments(self, parser):
        parser.add_argument("product", help="product page address or slug")

    def handle(self, *args, product, **options):
        slug = product.rstrip("/").rsplit("/", 1)[-1]
        found = Product.objects.select_related("game").filter(slug=slug).first()
        if found is None:
            alias = ProductAlias.objects.select_related("product__game").filter(slug=slug).first()
            found = alias.product if alias else None
        if found is None:
            raise CommandError(f"No product with the address {slug}.")
        product = found
        shops = ebay.shop_prices()
        self.stdout.write(f"{product.name}")
        if product.pk in shops:
            self.stdout.write(f"Cheapest any shop last showed: £{shops[product.pk]:.2f} delivered, "
                              f"so eBay must be at least £{shops[product.pk] * ebay.PRICE_FLOOR:.2f}")
        saved = Listing.objects.filter(product=product, retailer__source_type="ebay", is_active=True).first()
        if saved:
            self.stdout.write(f"Saved now: £{saved.delivered_price} delivered, {saved.get_availability_display().lower()}, "
                              f"{saved.url.split('?')[0]}")
        else:
            self.stdout.write("Saved now: no eBay listing")

        app, cert, campaign = ebay.credentials()
        headers = ebay.headers_for(ebay.access_token(app, cert), campaign)
        urls = ([ebay.search_url(product)] if product.ean else []) + [
            ebay.search_url(product, q) for q in ebay.search_queries(product)
        ]
        items = []
        for url in urls:
            items = ebay.http(url, headers).get("itemSummaries", []) or []
            if items:
                break
        if not items:
            self.stdout.write("eBay has no results for it.")
            return
        catalogue = Catalogue(Product.objects.filter(is_active=True).values_list("pk", "name", "game__slug"))
        specifics = ebay.Specifics(
            Product.objects.filter(is_active=True, game__slug=product.game.slug).values_list("pk", "name")
        )
        rows = []
        for item in items:
            offer, reason = ebay.verdict(product, item, Offer, catalogue, specifics, shops)
            total = offer.price + offer.delivery if offer else None
            rows.append((total, item.get("title", ""), reason, item.get("itemWebUrl", "").split("?")[0]))
        accepted = [row for row in rows if row[2] is None]
        pick = min(accepted, key=lambda row: row[0]) if accepted else None
        self.stdout.write(f"eBay results now ({len(rows)}):")
        for total, title, reason, url in sorted(rows, key=lambda row: (row[0] is None, row[0] or 0)):
            price = f"£{total:.2f}" if total is not None else "  -  "
            mark = "PICK" if pick and url == pick[3] else ("ok" if reason is None else "no")
            self.stdout.write(f"  {mark:4} {price:>8}  {title}")
            if reason:
                self.stdout.write(f"                 {reason}")
            self.stdout.write(f"                 {url}")
