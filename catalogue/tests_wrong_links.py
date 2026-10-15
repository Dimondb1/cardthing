"""
Shop pages linked to the wrong product, from the October 2026 audit of the live catalogue (export
RR-1-d3c70b072aaf). Each case copies the shop's real title, link and our product's real name.
"""

from decimal import Decimal

from django.test import TestCase, override_settings

from .importers import LinkIndex, Offer, apply_offers, link_key, page_key, product_offers
from .models import Listing, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


MARVEL = "https://travellingman.com/products/magic-the-gathering-marvel-super-heroes-commander-deck"
TWIN_SUNS = "https://travellingman.com/products/star-wars-unlimited-twin-suns-format-deck"


def marvel_page(*variants):
    return {"handle": "magic-the-gathering-marvel-super-heroes-commander-deck",
            "title": "Magic: The Gathering - Marvel Super Heroes Commander Deck", "tags": [], "images": [],
            "variants": [{"id": number, "title": title, "price": price, "available": True, "barcode": ""}
                         for number, title, price in variants]}


class LinkKeyTests(TestCase):
    def test_a_variant_is_part_of_the_link_and_nothing_else_in_the_query_is(self):
        self.assertNotEqual(link_key(f"{MARVEL}?variant=58370149646710"), link_key(f"{MARVEL}?variant=1"))
        self.assertEqual(link_key(f"{MARVEL}?variant=58370149646710&utm_source=x"),
                         link_key("http://www.travellingman.com/en-gb/products/magic-the-gathering-marvel-super-heroes-commander-deck/?variant=58370149646710"))
        self.assertEqual(link_key(f"{MARVEL}?utm_source=x"), link_key(MARVEL))
        self.assertEqual(page_key(f"{MARVEL}?variant=1"), page_key(MARVEL))

    def test_two_listings_on_one_link_go_to_the_one_the_title_names(self):
        links = LinkIndex([(f"{MARVEL}?variant=1", 901), (f"{MARVEL}?variant=1", 903)],
                          names={901: "Marvel Super Heroes Avengers Assemble Commander Deck",
                                 903: "Marvel Super Heroes Fantastic Four Commander Deck"})
        title = "Magic: The Gathering - Marvel Super Heroes Commander Deck (Avengers Assemble)"
        self.assertEqual(links.get(f"{MARVEL}?variant=1", title, "Avengers Assemble"), 901)
        self.assertIsNone(links.get(f"{MARVEL}?variant=1", "Marvel Super Heroes Commander Deck", "Deck"))


@override_settings(RIPRAPTOR_AUTO_CATALOGUE=False)
class VariantListingTests(TestCase):
    """Travelling Man sells both Marvel Super Heroes Commander decks on one page. Its listings 11166 and 11169
    ended on one variant's address, the Fantastic Four product carrying the Avengers Assemble title and price."""

    def setUp(self):
        magic = make_game("Magic: The Gathering", "magic-the-gathering")
        marvel = make_set(magic, "Marvel Super Heroes", "marvel-super-heroes", code="SPM")
        self.avengers = make_product(marvel, "Marvel Super Heroes Avengers Assemble Commander Deck",
                                     slug="avengers", product_type="deck")
        self.four = make_product(marvel, "Marvel Super Heroes Fantastic Four Commander Deck",
                                 slug="fantastic-four", product_type="deck")
        self.shop = make_retailer("Travelling Man", source_type=Retailer.Source.SHOPIFY,
                                  source_url="https://travellingman.com")
        self.page = marvel_page((58370149646710, "Avengers Assemble", "64.99"), (58370149646711, "Fantastic Four", "59.99"))

    def read(self):
        apply_offers(self.shop, product_offers("https://travellingman.com", self.page), complete=False)
        return {listing.product_id: listing for listing in Listing.objects.filter(retailer=self.shop)}

    def test_each_variant_keeps_its_own_listing(self):
        make_listing(self.avengers, self.shop, url=f"{MARVEL}?variant=58370149646710", price="1.00")
        make_listing(self.four, self.shop, url=f"{MARVEL}?variant=58370149646711", price="1.00")
        for _ in range(2):
            listings = self.read()
            self.assertEqual((listings[self.avengers.pk].url, listings[self.avengers.pk].price),
                             (f"{MARVEL}?variant=58370149646710", Decimal("64.99")))
            self.assertEqual((listings[self.four.pk].url, listings[self.four.pk].price),
                             (f"{MARVEL}?variant=58370149646711", Decimal("59.99")))

    def test_two_listings_left_on_one_variant_are_put_right_by_the_next_read(self):
        make_listing(self.avengers, self.shop, url=f"{MARVEL}?variant=58370149646710", price="64.99")
        make_listing(self.four, self.shop, url=f"{MARVEL}?variant=58370149646710", price="64.99",
                     title="Magic: The Gathering - Marvel Super Heroes Commander Deck (Avengers Assemble)")
        listings = self.read()
        self.assertEqual((listings[self.avengers.pk].url, listings[self.avengers.pk].price),
                         (f"{MARVEL}?variant=58370149646710", Decimal("64.99")))
        self.assertEqual((listings[self.four.pk].url, listings[self.four.pk].title, listings[self.four.pk].price),
                         (f"{MARVEL}?variant=58370149646711",
                          "Magic: The Gathering - Marvel Super Heroes Commander Deck (Fantastic Four)", Decimal("59.99")))

    def test_a_link_to_the_bare_page_is_taken_only_by_the_variant_it_names(self):
        star_wars = make_game("Star Wars: Unlimited", "star-wars-unlimited")
        twin = make_set(star_wars, "Twin Suns", "twin-suns", code="TS")
        odds = make_product(twin, "Twin Suns Format Deck (Against the Odds)", slug="odds", product_type="deck")
        make_listing(odds, self.shop, url=TWIN_SUNS, price="1.00")
        page = {"handle": "star-wars-unlimited-twin-suns-format-deck",
                "title": "Star Wars Unlimited Twin Suns Format Deck", "tags": [], "images": [],
                "variants": [{"id": 57809065247094, "title": "Aggresive Negotiations", "price": "25.99", "available": True},
                             {"id": 57809065247095, "title": "Against the Odds", "price": "26.99", "available": True}]}
        apply_offers(self.shop, product_offers("https://travellingman.com", page), complete=False)
        listing = Listing.objects.get(retailer=self.shop, product=odds)
        self.assertEqual((listing.url, listing.price), (f"{TWIN_SUNS}?variant=57809065247095", Decimal("26.99")))

    def test_a_page_with_one_thing_still_finds_its_listing_whatever_variant_it_was(self):
        listing = make_listing(self.avengers, self.shop, url=f"{MARVEL}?variant=1", price="1.00")
        apply_offers(self.shop, [Offer(title="Marvel Super Heroes Commander Deck", url=MARVEL, price=Decimal("60"))],
                     complete=False)
        listing.refresh_from_db()
        self.assertEqual((listing.url, listing.price), (MARVEL, Decimal("60.00")))
