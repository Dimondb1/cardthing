"""A shop's single cards named after the product they come from (catalogue/importers.py not_sealed_reason,
remove_not_sealed; tidy_listings). Magic Madhouse sells the cards of My First Battle Deck as "My First Battle Deck:
Pikachu Deck / Electabuzz" at £2.45, filed under "Pokémon Singles", in "Mint / Near Mint" condition: twenty were
pages on the site on 24 Oct 2026. The owner: "Magic Madhouse singles are making it into the site. They have a
condition as well. Don't want"."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .importers import Offer, apply_offers, page_offer
from .models import Listing, Product, RemovedProduct, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set

# The shape of Magic Madhouse's page for a single card (BigCommerce): the product's data marks the offer used, and
# the breadcrumb files it under singles.
SINGLE = """<html><head><title>My First Battle Deck: Pikachu Deck / Electabuzz | Magic Madhouse</title>
<script type="application/ld+json">{"@context": "https://schema.org/", "@type": "Product",
 "name": "My First Battle Deck: Pikachu Deck / Electabuzz", "sku": "PE-FBD1-P08",
 "description": "My First Battle Deck: Pikachu Deck / Electabuzz is a Basic Pokemon Card from the &quot;My First Battle Deck&quot;",
 "offers": [{"@type": "Offer", "price": "2.45", "priceCurrency": "GBP", "availability": "http://schema.org/InStock",
   "itemCondition": "https://schema.org/UsedCondition",
   "url": "https://magicmadhouse.example/pokemon-my-first-battle-deck-pikachu-deck-electabuzz"}]}</script></head>
<body><ul class="breadcrumbs" itemscope itemtype="http://schema.org/BreadcrumbList">
<li itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem"><a itemprop="item" href="/"><span itemprop="name">Home</span></a></li>
<li itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem"><a itemprop="item" href="/pokemon/"><span itemprop="name">Pokémon</span></a></li>
<li itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem"><a itemprop="item" href="/pokemon/singles/"><span itemprop="name">Pokémon Singles</span></a></li>
</ul></body></html>"""
SEALED = """<html><head><script type="application/ld+json">{"@type": "Product", "name": "My First Battle Deck Pikachu & Bulbasaur",
 "description": "Two ready-to-play 15-card decks for new players.",
 "offers": {"@type": "Offer", "price": "14.95", "priceCurrency": "GBP", "itemCondition": "https://schema.org/NewCondition"}}</script>
</head><body><ul itemscope itemtype="https://schema.org/BreadcrumbList">
<li><span itemprop="name">Pokémon</span></li><li><span itemprop="name">Pokémon Sealed Product</span></li></ul></body></html>"""


class PageTests(TestCase):
    def test_a_single_card_page_says_so_three_ways_and_a_sealed_page_none(self):
        url = "https://magicmadhouse.example/pokemon-my-first-battle-deck-pikachu-deck-electabuzz"
        self.assertEqual(page_offer(url, SINGLE).not_sealed, "the shop sells it used")
        # Each sign holds on its own.
        unmarked = SINGLE.replace("https://schema.org/UsedCondition", "https://schema.org/NewCondition")
        self.assertEqual(page_offer(url, unmarked).not_sealed, "the shop files it under single cards")
        plain = unmarked.replace("Pokémon Singles", "Lightning Type")
        self.assertEqual(page_offer(url, plain).not_sealed, "the shop calls it a single card")
        self.assertEqual(page_offer("https://magicmadhouse.example/my-first-battle-deck", SEALED).not_sealed, "")
        # A trail in the product's schema.org data counts as well as the page's microdata.
        crumbs = """<script type="application/ld+json">{"@graph": [
            {"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": 1, "name": "Single Cards"}]},
            {"@type": "Product", "name": "Surging Sparks Booster Box Pikachu ex", "offers": {"@type": "Offer",
             "price": "3.50", "priceCurrency": "GBP"}}]}</script>"""
        self.assertEqual(page_offer("https://shop.example/pikachu-ex", crumbs).not_sealed, "the shop files it under single cards")


class RemovalTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.shop = make_retailer("Magic Madhouse", source_type=Retailer.Source.WEBSITE)
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.url = "https://magicmadhouse.example/pokemon-my-first-battle-deck-pikachu-deck-electabuzz"
        self.card = make_product(self.set, name="My First Battle Deck: Pikachu Deck / Electabuzz", slug="mfbd-electabuzz",
                                 product_type=Product.Type.DECK)
        self.listing = make_listing(self.card, self.shop, price="2.45", url=self.url, title=self.card.name,
                                    description="My First Battle Deck: Pikachu Deck / Electabuzz is a Basic Pokemon Card "
                                                "from the \"My First Battle Deck\"")
        self.on_ebay = make_listing(self.card, self.ebay, price="1.99", url="https://www.ebay.co.uk/itm/5?campid=1")
        # A real deck the shop also sells, which stays.
        self.deck = make_product(self.set, name="My First Battle Deck Pikachu & Bulbasaur", slug="mfbd",
                                 product_type=Product.Type.DECK)
        self.deck_listing = make_listing(self.deck, self.shop, price="14.95", url="https://magicmadhouse.example/mfbd")

    def test_a_read_of_the_page_removes_the_listing_and_the_ebay_one_it_brought(self):
        offer = page_offer(self.url, SINGLE)
        apply_offers(self.shop, [offer, Offer(title=self.deck.name, url="https://magicmadhouse.example/mfbd",
                                              price=Decimal("14.95"))], complete=False)
        self.assertFalse(Listing.objects.filter(product=self.card).exists())
        self.assertTrue(Listing.objects.filter(pk=self.deck_listing.pk).exists())

    def test_a_new_single_never_becomes_a_product(self):
        Listing.objects.filter(product=self.card).delete()
        Product.objects.filter(pk=self.card.pk).delete()
        found, _updated, unmatched = apply_offers(self.shop, [page_offer(self.url, SINGLE)], complete=False)
        self.assertFalse(Product.objects.filter(name__contains="Electabuzz").exists())
        self.assertIn("[the shop sells it used]", unmatched[0])

    def test_the_hourly_tidy_removes_those_linked_before_and_retires_the_page(self):
        out = StringIO()
        call_command("tidy_listings", stdout=out)
        self.assertIn("removed (a single card): My First Battle Deck: Pikachu Deck / Electabuzz <- Magic Madhouse", out.getvalue())
        self.assertFalse(Listing.objects.filter(pk__in=[self.listing.pk, self.on_ebay.pk]).exists())
        self.assertTrue(Listing.objects.filter(pk=self.deck_listing.pk).exists())
        call_command("tidy_catalogue", stdout=StringIO())
        self.assertFalse(Product.objects.filter(pk=self.card.pk).exists())
        self.assertTrue(RemovedProduct.objects.filter(slug="mfbd-electabuzz").exists())
        self.assertEqual(self.client.get("/products/mfbd-electabuzz/").status_code, 410)

    def test_a_product_another_shop_sells_keeps_its_other_listings(self):
        other = make_listing(self.card, make_retailer("Harbour Games"), price="3.00")
        call_command("tidy_listings", stdout=StringIO())
        self.assertTrue(Listing.objects.filter(pk__in=[other.pk, self.on_ebay.pk]).count() == 2)

    def test_a_sealed_box_whose_description_names_a_card_stays(self):
        etb = make_product(self.set, name="Surging Sparks Elite Trainer Box", slug="ssp-etb")
        kept = make_listing(etb, self.shop, price="49.99", url="https://magicmadhouse.example/ssp-etb", title=etb.name,
                            description="Includes nine booster packs and a promo. The Pikachu ex promo is a full art "
                                        "card from Surging Sparks.")
        call_command("tidy_listings", stdout=StringIO())
        self.assertTrue(Listing.objects.filter(pk=kept.pk).exists())
