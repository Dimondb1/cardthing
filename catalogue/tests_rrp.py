"""The RRP (catalogue/fair_price.py shop_rrps, rrp_cap; importers list prices; discord). The owner, 24 Oct 2026:
"Can we get data on rrp and ensure that deals we share on Discord aren't massively overpriced, not just a
pointless discount. Cardmarket was supposed to help with this"."""

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from . import discord, fair_price
from .importers import NO_LIST_PRICE, Offer, apply_offers, js_product, page_offer, product_offers
from .models import DailyLowestPrice, DiscordChannel, Listing, Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class ShopListPriceTests(TestCase):
    def test_shopify_gives_its_compare_at_price_and_none_when_it_has_none(self):
        product = {"handle": "etb", "title": "Ascended Heroes Elite Trainer Box", "variants": [
            {"id": 1, "title": "Default Title", "price": "44.99", "compare_at_price": "49.99", "available": True}]}
        [offer] = product_offers("https://shop.example", product)
        self.assertEqual((offer.price, offer.list_price), (Decimal("44.99"), Decimal("49.99")))
        product["variants"][0]["compare_at_price"] = None
        [offer] = product_offers("https://shop.example", product)
        self.assertEqual(offer.list_price, NO_LIST_PRICE)
        # A product's .js answer gives pence.
        js = js_product({"handle": "etb", "title": "Ascended Heroes Elite Trainer Box",
                         "variants": [{"id": 1, "title": "Default Title", "price": 4499, "compare_at_price": 4999,
                                       "available": True}]})
        self.assertEqual(next(product_offers("https://shop.example", js)).list_price, Decimal("49.99"))

    def test_a_page_gives_its_list_price_msrp_or_srp_and_the_sale_price_stays_the_price(self):
        page = """<script type="application/ld+json">{"@type": "Product", "name": "Surging Sparks Booster Bundle",
            "offers": {"@type": "Offer", "price": "24.99", "priceCurrency": "GBP", "priceSpecification": [
              {"@type": "UnitPriceSpecification", "priceType": "https://schema.org/%s", "price": "26.99",
               "priceCurrency": "GBP"}]}}</script>"""
        for kind in ("ListPrice", "StrikethroughPrice", "MSRP", "SRP"):
            offer = page_offer("https://shop.example/bundle", page % kind)
            self.assertEqual((offer.price, offer.list_price), (Decimal("24.99"), Decimal("26.99")))
        offer = page_offer("https://shop.example/bundle", page % "SalePrice")
        self.assertEqual(offer.list_price, NO_LIST_PRICE)

    def test_a_read_keeps_it_clears_it_and_a_read_that_did_not_look_leaves_it(self):
        shop = make_retailer("Harbour Games", source_type=Retailer.Source.SHOPIFY)
        product = make_product(make_set(make_game()), name="Ascended Heroes Elite Trainer Box")
        url = "https://harbour.example/products/etb"
        listing = make_listing(product, shop, price="44.99", url=url, title=product.name)

        def read(list_price):
            apply_offers(shop, [Offer(title=product.name, url=url, price=Decimal("44.99"), product_pk=product.pk,
                                      list_price=list_price)], complete=False)
            listing.refresh_from_db()
            return listing.list_price

        self.assertEqual(read(Decimal("49.99")), Decimal("49.99"))
        self.assertEqual(read(None), Decimal("49.99"))
        self.assertIsNone(read(NO_LIST_PRICE))


class ShopRrpTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.product = make_product(self.set, name="Ascended Heroes Elite Trainer Box")

    def give(self, name, price, list_price, **kwargs):
        listing = make_listing(self.product, make_retailer(name, **kwargs), price=price)
        Listing.objects.filter(pk=listing.pk).update(list_price=list_price)
        return listing

    def test_two_shops_giving_the_same_rrp_to_the_pound_settle_it(self):
        self.give("Harbour Games", "44.99", "49.99")
        self.assertEqual(fair_price.shop_rrps(), {})   # one shop's word is not enough
        self.give("Northgate Cards", "47.50", "49.95")
        self.give("Fenland Games", "46.00", "49.99")
        self.assertEqual(fair_price.shop_rrps(), {self.product.pk: (Decimal("49.99"), 3)})

    def test_a_split_an_unbelievable_figure_and_ebay_settle_nothing(self):
        self.give("Harbour Games", "44.99", "49.99")
        self.give("Northgate Cards", "44.99", "59.99")
        self.assertEqual(fair_price.shop_rrps(), {})   # one says £49.99, one £59.99
        self.give("eBay", "30.00", "59.99", source_type=Retailer.Source.EBAY)
        self.give("Fenland Games", "20.00", "59.99")   # three times its own price: not believed
        self.assertEqual(fair_price.shop_rrps(), {})


class PriceCheckTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.today = timezone.localdate()

    def product(self, price="50.00", usual=None, **kwargs):
        product = make_product(self.set, name=f"Box {Product.objects.count()}", slug=f"box-{Product.objects.count()}",
                               **kwargs)
        for name in ("Harbour Games", "Northgate Cards"):
            make_listing(product, Retailer.objects.filter(name=name).first() or make_retailer(name),
                         price=price, delivery="0.00")
        if usual:
            DailyLowestPrice.objects.bulk_create([
                DailyLowestPrice(product=product, date=self.today - timedelta(days=back), price=Decimal(usual))
                for back in range(1, 21)
            ])
        return product

    def test_the_rrp_is_a_ceiling_never_a_reason_to_praise(self):
        alone = self.product("50.00", rrp=Decimal("49.99"))
        under = self.product("30.00", rrp=Decimal("49.99"))
        lowest = self.product("40.00", usual="60.00", rrp=Decimal("27.99"))   # our record says lowest yet
        above = self.product("62.00", rrp=Decimal("49.99"))
        verdicts = {p: fair_price.verdict(p, p.listings.first().delivered_price) for p in (alone, under, lowest, above)}
        self.assertEqual(verdicts[alone].key, "fair")      # within 15% of the RRP, and nothing else to go by
        self.assertEqual(verdicts[under].key, "fair")      # well under it, but the RRP alone never says good
        self.assertEqual(verdicts[lowest].key, "high")     # 43% over the RRP, however low for us
        self.assertEqual(verdicts[above].key, "above")
        self.assertEqual((verdicts[alone].rrp, verdicts[alone].rrp_shops), (Decimal("49.99"), None))
        # Without an RRP our record's verdict stands.
        self.assertEqual(fair_price.verdict(self.product("40.00", usual="60.00"), Decimal("40.00")).key, "lowest")

    def test_the_hourly_refresh_keeps_the_shops_rrp_and_the_owners_wins(self):
        shops = self.product("62.00")
        Listing.objects.filter(product=shops).update(list_price=Decimal("49.99"))
        owner = self.product("62.00", rrp=Decimal("59.99"))
        Listing.objects.filter(product=owner).update(list_price=Decimal("49.99"))
        fair_price.refresh_bands()
        shops.refresh_from_db(), owner.refresh_from_db()
        self.assertEqual((shops.shop_rrp, shops.shop_rrp_shops, shops.price_band, shops.price_basis),
                         (Decimal("49.99"), 2, fair_price.ABOVE, fair_price.RRP))
        self.assertEqual(shops.rrp_now, (Decimal("49.99"), 2))
        self.assertEqual((owner.rrp_now, owner.price_band), ((Decimal("59.99"), None), fair_price.FAIR))
        # A shop stops giving it: the product's goes at the next refresh.
        Listing.objects.filter(product=shops, retailer__name="Northgate Cards").update(list_price=None)
        fair_price.refresh_bands()
        shops.refresh_from_db()
        self.assertEqual((shops.shop_rrp, shops.price_band), (None, None))

    def test_the_product_page_shows_the_rrp_and_who_gives_it(self):
        cache.clear()
        product = self.product("52.00")
        Listing.objects.filter(product=product).update(list_price=Decimal("49.99"))
        fair_price.refresh_bands()
        html = self.client.get(product.get_absolute_url()).content.decode()
        self.assertIn("Given by 2 UK shops", html)
        self.assertIn("A price more than 15% over the RRP is never called fair", html)


@override_settings(RIPRAPTOR_SITE_URL="https://ripraptor.com")
class DiscordDealTests(TestCase):
    """A price drop is posted only when the new price is fair or better; with nothing to measure it, not at all."""

    def setUp(self):
        self.now = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        self.set = make_set(make_game())
        self.shop = make_retailer("Harbour Games")
        DiscordChannel.objects.create(label="#deals", webhook_url="https://discord.com/api/webhooks/1/x",
                                      price_drops=True, min_drop_percent=5)
        discord.run(now=self.now, post=lambda url, data: None)   # the first run only notes what is there

    def drop(self, name, rrp=None):
        product = make_product(self.set, name=name, rrp=rrp)
        make_listing(product, self.shop, price="97.01", delivery="2.99")   # £100.00 delivered
        DailyLowestPrice.objects.create(product=product, date=timezone.localdate(self.now) - timedelta(days=1),
                                        price=Decimal("120.00"))
        return product

    def test_a_drop_to_a_price_still_far_over_the_rrp_or_with_nothing_to_measure_it_is_not_posted(self):
        self.drop("Inflated Booster Box", rrp=Decimal("69.99"))   # 43% over its RRP: a pointless discount
        self.drop("Unmeasured Booster Box")                        # one shop, one day, no RRP, no Cardmarket
        self.drop("Fair Booster Box", rrp=Decimal("99.99"))
        sent = []
        discord.run(now=self.now + timedelta(minutes=10), post=lambda url, data: sent.append(data))
        self.assertEqual([embed["title"] for data in sent for embed in data["embeds"]], ["Fair Booster Box"])
