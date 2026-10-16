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


MAXON = "https://maxoncards.co.uk"


class ListingPageTests(TestCase):
    """MaxOnCards' category pages became 46 listings and shop items: "/shop/magic-the-gathering/english" carried
    a Reality Fracture Play Booster Pack at £4.99 on our Play Booster Box (listing 19305)."""

    def test_a_shop_section_or_collection_is_a_list_of_products(self):
        from .importers import listing_page, page_rank

        for path in ("/shop/pokemon/japanese/booster-boxes", "/shop/magic-the-gathering/english", "/shop/dandadan",
                     "/shop/pokemon/english/3-pack-blisters", "/shop/pokemon/english/collection-boxes",
                     "/shop/pokemon/english/blister-packs", "/shop/yu-gi-oh/english", "/shop/one-piece/english/starter-decks",
                     "/shop/pokemon/english/super-premium-collections", "/shop/pokemon/english/other",
                     "/collections/pokemon-booster-boxes", "/en-gb/collections/one-piece"):
            self.assertTrue(listing_page(MAXON + path), path)
            self.assertIsNone(page_rank(MAXON + path), path)
        for url in (f"{MAXON}/product/pokemon-tcg-mega-evolution-perfect-order-elite-trainer-box-pokemon-center-exclusive",
                    "https://shop.example/collections/pokemon/products/surging-sparks-booster-box",
                    "https://shop.example/shop/pokemon-151-booster-packs",
                    "https://shop.example/shop/surging-sparks-booster-box",
                    "https://unicorncards.co.uk/flesh-and-blood-tcg-silver-age-chapter-1-deck-kayo",
                    "https://www.ebay.co.uk/itm/389598234635"):
            self.assertFalse(listing_page(url), url)

    def test_a_page_whose_data_names_several_products_gives_no_offer(self):
        import json

        from .importers import page_offer

        def product(name, price):
            return {"@type": "Product", "name": name, "offers": {"price": price, "priceCurrency": "GBP"}}

        items = {"@type": "ItemList", "itemListElement": [
            product("Magic: The Gathering – Reality Fracture Play Booster Pack", "4.99"),
            product("Magic: The Gathering – Reality Fracture Play Booster Box", "149.99")]}
        html = f'<script type="application/ld+json">{json.dumps(items)}</script>'
        self.assertIsNone(page_offer("https://shop.example/whatever", html))
        one = f'<script type="application/ld+json">{json.dumps(product("Reality Fracture Play Booster Box", "149.99"))}</script>'
        self.assertEqual(page_offer("https://shop.example/reality-fracture-box", one).price, Decimal("149.99"))
        self.assertIsNone(page_offer(f"{MAXON}/shop/magic-the-gathering/english", one))

    @override_settings(RIPRAPTOR_AUTO_CATALOGUE=False)
    def test_a_read_never_lists_a_category_page_and_drops_the_one_it_had(self):
        magic = make_game("Magic: The Gathering", "magic-the-gathering")
        box = make_product(make_set(magic, "Reality Fracture", "reality-fracture", code="RF"),
                           "Reality Fracture Play Booster Box", product_type="booster_box")
        shop = make_retailer("MaxOnCards", source_type=Retailer.Source.WEBSITE, source_url=MAXON)
        make_listing(box, shop, url=f"{MAXON}/shop/magic-the-gathering/english", price="4.99")
        offer = Offer(title="Magic: The Gathering – Reality Fracture Play Booster Box",
                      url=f"{MAXON}/shop/magic-the-gathering/english/booster-boxes", price=Decimal("149.99"))
        found, updated, unmatched = apply_offers(shop, [offer], complete=True)
        self.assertEqual(updated, 0)
        self.assertIn("a page listing many products", unmatched[0])
        listing = Listing.objects.get(retailer=shop)
        self.assertEqual((listing.price, listing.availability), (Decimal("4.99"), Listing.Availability.OUT_OF_STOCK))


class ContradictionTests(TestCase):
    """matching.contradiction on real shop titles and our real product names from the export."""

    WRONG = [
        # (shop title, our product, game, our language, why)
        ("One Piece Card Game - OP15 - Booster Box - (24 Packs)", "OP18 Sealed Booster Box", "one-piece", "", "set code"),
        ("One Piece OP19 Booster Box", "OP18 Sealed Booster Box", "one-piece", "", "set code"),
        ("One Piece TCG - OP-01 & OP-02 - Booster Box", "OP18 Sealed Booster Box", "one-piece", "", "set code"),
        ("Pokemon TCG Scarlet Violet Future Flash SV4M  new  Booster Box Japanese Edition",
         "Japanese TCG: Scarlet ex SV1S Japanese Booster Box", "pokemon", "ja", "set code"),
        ("Pokemon TCG: Sword & Shield Base Set - Booster Pack", "Scarlet & Violet Base Set Booster Pack", "pokemon", "",
         "set code"),
        ("Dragon Ball Super CG: Booster Box - Fusion World ST10 Story Booster 01",
         "Fusion World ST01 Story Booster 01 Booster Box", "dragon-ball", "", "set code"),
        ("Dragon ball super masters TCG Ultimate Advent booster pack factory sealed BT26", "Ultimate Masters Booster Pack",
         "magic-the-gathering", "", "game"),
        ('ONE PIECE OP-16 Card Game "THE TIME OF BATTLE" Japanese Booster Pack',
         "The Time of Battle OP 16 English Booster Pack", "one-piece", "", "language"),
        ("Pokemon TCG: 30th Celebration - Booster Box [JP]", "30th Celebration Booster Box", "pokemon", "", "language"),
        ("Pokémon CENTER TCG Cards SWSH EVOLVING SKIES ELITE TRAINER BOX ETB COIN", "Evolving Skies Elite Trainer Box",
         "pokemon", "", "Center"),
        ("Pokemon Center SV Journey Together Enhanced Booster Display Box 36 Pack",
         "Scarlet & Violet: Journey Together Enhanced Booster Display Box", "pokemon", "", "Center"),
        ("Ultra Limit - Booster Box [FB04] (Damaged)", "Ultra Limit Booster Box (Normal)", "dragon-ball", "", "condition"),
        ("Pokemon TCG Prismatic Evolutions Mini Tin Umbreon Sealed New (Slight Dent)", "Prismatic Evolutions Mini Tin Umbreon",
         "pokemon", "", "condition"),
        ("Pokemon Ninja Spinner m4 Japanese Booster Box (No Shrink Booster Box)",
         "Ninja Spinner m4 Japanese Booster Box (Shrinked Booster Box)", "pokemon", "ja", "condition"),
        ("WAVE 2 Pokémon TCG: 30th Anniversary Products (RIPPED SEALS) (Booster Bundle)",
         "WAVE 2 Pokémon TCG: 30th Anniversary Products (RIPPED SEALS) (Elite Trainer Box)", "pokemon", "", "unit"),
        ("PRE-ORDER: Magic The Gathering - Star Trek Collector Booster (Booster Box)", "Star Trek Collector Booster",
         "magic-the-gathering", "", "unit"),
        ("Double Pack Set Vol.7 [DP-07] (Double Pack)", "Double Pack Set Vol.7 (Case of Double Packs (8 Units))",
         "yu-gi-oh", "", "unit"),
        ("Magic: The Gathering - Aetherdrift Play Booster (Booster Pack)", "Aetherdrift Play Booster Box (Booster Box)",
         "magic-the-gathering", "", "unit"),
        ("Crown Zenith Elite Trainer Box", "Crown Zenith: 4 Booster Packs", "pokemon", "", "unit"),
        ("Pokemon Perfect Order 3-Pack Blister", "Perfect Order 1 Pack Blister", "pokemon", "", "packs"),
        ("Pokémon Scarlet & Violet Obsidian Flames blister 2 packs", "Scarlet & Violet Obsidian Flames 3 Pack Blister",
         "pokemon", "", "packs"),
    ]
    RIGHT = [
        ("One Piece (OP15) Adventure on Kami's Island Booster Pack", "Adventure on Kami's Island OP 15 EB 04 English Booster Pack",
         "one-piece", ""),
        ("One Piece Starter Decks ST31-ST36 (Blue Kuzan (ST-33))", "Starter Decks ST31 ST36 (Blue Kuzan)", "one-piece", ""),
        ("Dragon Ball Super Card Game Masters: Impact Beyond Dimensions (BT31) Booster Box",
         "Masters Impact Beyond Dimensions B31 English Booster Box", "dragon-ball", ""),
        ("One Piece TCG EBC-03 EB-03 Heroines Edition Booster Box 24 Packs Sealed Chinese",
         "Heroines Edition EBC 03 Chinese Booster Box", "one-piece", "zh-hans"),
        ("Union Arena TCG UE10BT Attack on Titan (16 Booster Packs)", "Attack on Titan Booster Box", "union-arena", ""),
        ("Pokémon TCG: Perfect Order - Pokémon Center Elite Trainer Box (Exclusive)", "Center Perfect Order Elite Trainer Box",
         "pokemon", ""),
        ("One Piece TCG OP-09 Emperors in the New World Booster Box - Unsealed by Bandai",
         "Emperors In The New World Booster Box (Booster Box)", "one-piece", ""),
        ("Pokémon Terastal Festival Japanese Booster Box BEWARE OF FAKES & RESEALED BOXES",
         "JAPANESE Terastal Festival Booster Box", "pokemon", "ja"),
        ("Disney Lorcana: Rise of the Floodborn - Set 2 Booster Pack", "Rise Of The Floodborn 1 pack", "lorcana", ""),
        ("Weiss Schwarz - OSHI NO KO Vol. 3 - Booster Pack", "OSHI NO KO Vol. 3 Booster Pack", "weiss-schwarz", ""),
        ("Pokemon TCG: Surging Sparks Booster Box (36x Packs)", "Surging Sparks Booster Box", "pokemon", ""),
        ("Prismatic Evolutions Booster Bundle (6 Booster Packs)", "Prismatic Evolutions Booster Bundle", "pokemon", ""),
        ("Pokemon TCG: Paldea Evolved - Booster Box + Elite Trainer Box Bundle",
         "Elite Trainer + Booster Box Bundle Scarlet & Violet Paldea Evolved", "pokemon", ""),
        ("Mystery Japanese/Chinese booster pack", "Mystery Japanese/Chinese booster pack", "pokemon", "ja"),
        ("Pokemon Dreams Come True AC2b Traditional Chinese Booster Box", "Dreams Come True AC2b Traditional Chinese Booster Box",
         "pokemon", "asia"),
    ]

    def test_titles_that_plainly_name_another_product(self):
        from .matching import contradiction

        for title, name, game, language, why in self.WRONG:
            self.assertIn(why, contradiction(name, title, game, language), title)

    def test_titles_that_name_the_same_product_in_other_words(self):
        from .matching import contradiction

        for title, name, game, language in self.RIGHT:
            self.assertEqual(contradiction(name, title, game, language), "", title)


@override_settings(RIPRAPTOR_AUTO_CATALOGUE=False)
class RefusedPathTests(TestCase):
    """Every way a page reaches a product refuses a title that plainly names another one."""

    def setUp(self):
        one_piece = make_game("One Piece Card Game", "one-piece")
        self.op18 = make_product(make_set(one_piece, "OP18", "op18", code="OP18"), "OP18 Sealed Booster Box",
                                 slug="op18-box", product_type="booster_box")
        self.op16 = make_product(make_set(one_piece, "The Time of Battle", "op16", code="OP16"),
                                 "The Time of Battle OP 16 English Booster Pack", slug="op16-pack", product_type="booster_pack")
        self.shop = make_retailer("Cosmic Collectables", source_type=Retailer.Source.SHOPIFY,
                                  source_url="https://cosmiccollectables.co.uk")

    def test_a_listing_s_own_link_never_prices_a_title_of_another_set_or_language(self):
        url = "https://cosmiccollectables.co.uk/products/one-piece-card-game-op15-booster-box-24-packs"
        make_listing(self.op18, self.shop, url=url, price="119.95")
        titan = "https://titancards.co.uk/products/one-piece-op-16-card-game-the-time-of-battle-booster-pack"
        make_listing(self.op16, self.shop, url=titan, price="4.99")
        offers = [Offer(title="One Piece Card Game - OP15 - Booster Box - (24 Packs)", url=url, price=Decimal("99.95")),
                  Offer(title='ONE PIECE OP-16 Card Game "THE TIME OF BATTLE" Japanese Booster Pack', url=titan,
                        price=Decimal("3.99"))]
        found, updated, unmatched = apply_offers(self.shop, offers, complete=True)
        self.assertEqual(updated, 0)
        self.assertTrue(any("another set code, not OP18 Sealed Booster Box" in line for line in unmatched))
        self.assertTrue(any("another language" in line for line in unmatched))
        prices = dict(Listing.objects.values_list("product_id", "price"))
        self.assertEqual(prices, {self.op18.pk: Decimal("119.95"), self.op16.pk: Decimal("4.99")})
        self.assertFalse(Listing.objects.exclude(availability=Listing.Availability.OUT_OF_STOCK).exists())

    def test_our_own_valid_barcode_still_settles_it(self):
        Listing.objects.all().delete()
        type(self.op18).objects.filter(pk=self.op18.pk).update(ean="4012927170304")
        offer = Offer(title="One Piece OP19 Booster Box", url="https://eternacards.co.uk/products/one-piece-op19-booster-box",
                      price=Decimal("118.95"), ean="4012927170304")
        apply_offers(self.shop, [offer], complete=False)
        self.assertEqual(Listing.objects.get().product, self.op18)

    def test_an_owner_yes_waiting_for_a_price_is_refused_too(self):
        from .models import ShopProduct

        url = "https://eternacards.co.uk/products/one-piece-op19-booster-box"
        ShopProduct.objects.create(retailer=self.shop, url=url, title="One Piece OP19 Booster Box", suggested=self.op18,
                                   status=ShopProduct.Status.LINKED)
        apply_offers(self.shop, [Offer(title="One Piece OP19 Booster Box", url=url, price=Decimal("118.95"))],
                     complete=False)
        self.assertFalse(Listing.objects.exists())

    def test_the_stockist_finder_judges_a_contradiction_as_no_match(self):
        from . import finder

        self.assertEqual(finder.judge(self.op18, "One Piece OP19 Booster Box"), 0)


class TidyTests(TestCase):
    """The hourly tidy takes off listings the export showed on the wrong product, and nothing else."""

    def setUp(self):
        one_piece = make_game("One Piece Card Game", "one-piece")
        self.op18 = make_product(make_set(one_piece, "OP18", "op18", code="OP18"), "OP18 Sealed Booster Box",
                                 slug="op18-box", product_type="booster_box")
        self.op16 = make_product(make_set(one_piece, "The Time of Battle", "op16", code="OP16"),
                                 "The Time of Battle OP 16 English Booster Pack", slug="op16-pack", product_type="booster_pack")
        magic = make_game("Magic: The Gathering", "magic-the-gathering")
        marvel = make_set(magic, "Marvel Super Heroes", "marvel-super-heroes", code="SPM")
        self.avengers = make_product(marvel, "Marvel Super Heroes Avengers Assemble Commander Deck", slug="avengers",
                                     product_type="deck")
        self.four = make_product(marvel, "Marvel Super Heroes Fantastic Four Commander Deck", slug="four", product_type="deck")
        self.fracture = make_product(make_set(magic, "Reality Fracture", "reality-fracture", code="RF"),
                                     "Reality Fracture Play Booster Box", slug="rf-box", product_type="booster_box")

    def tidy(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("tidy_listings", *args, stdout=out)
        return out.getvalue()

    def test_wrong_listings_go_and_right_ones_stay(self):
        cosmic = make_retailer("Cosmic Collectables")
        titan = make_retailer("Titan Cards")
        travelling = make_retailer("Travelling Man")
        maxon = make_retailer("MaxOnCards")
        wrong = [
            make_listing(self.op18, cosmic, title="One Piece Card Game - OP15 - Booster Box - (24 Packs)",
                         url="https://cosmiccollectables.co.uk/products/one-piece-card-game-op15-booster-box-24-packs"),
            make_listing(self.op16, titan, title='ONE PIECE OP-16 Card Game "THE TIME OF BATTLE" Japanese Booster Pack',
                         url="https://titancards.co.uk/products/one-piece-op-16-card-game-the-time-of-battle-booster-pack"),
            make_listing(self.four, travelling, url=f"{MARVEL}?variant=58370149646710",
                         title="Magic: The Gathering - Marvel Super Heroes Commander Deck (Avengers Assemble)"),
            make_listing(self.fracture, maxon, url=f"{MAXON}/shop/magic-the-gathering/english",
                         title="Magic: The Gathering – Reality Fracture Play Booster Pack"),
        ]
        right = [
            make_listing(self.avengers, travelling, url=f"{MARVEL}?variant=58370149646710", title=""),
            make_listing(self.op16, cosmic, title="One Piece Card Game: The Time of Battle (OP-16) Booster Pack",
                         url="https://cosmiccollectables.co.uk/products/op16-pack"),
        ]
        preview = self.tidy("--dry-run")
        for listing in wrong:
            self.assertIn(f"would remove: {listing.product.name} <- {listing.retailer.name} {listing.url}", preview)
        self.assertEqual(Listing.objects.count(), 6)
        self.tidy()
        self.assertEqual(set(Listing.objects.values_list("pk", flat=True)), {listing.pk for listing in right})

    def test_two_products_on_one_link_with_no_title_naming_one_both_stay(self):
        shop = make_retailer("Travelling Man")
        make_listing(self.avengers, shop, url=f"{MARVEL}?variant=1", title="")
        make_listing(self.four, shop, url=f"{MARVEL}?variant=1", title="Marvel Super Heroes Commander Deck")
        self.tidy()
        self.assertEqual(Listing.objects.count(), 2)

    def test_our_own_valid_barcode_keeps_a_listing_whatever_the_title(self):
        type(self.op18).objects.filter(pk=self.op18.pk).update(ean="4012927170304")
        self.op18.refresh_from_db()
        make_listing(self.op18, make_retailer("Eterna Cards"), title="One Piece OP19 Booster Box", shop_ean="4012927170304",
                     url="https://eternacards.co.uk/products/one-piece-op19-booster-box")
        self.tidy()
        self.assertEqual(Listing.objects.count(), 1)


@override_settings(RIPRAPTOR_AUTO_CATALOGUE=False)
class SharedBarcodeTests(TestCase):
    """Chaos Origins carried Glorious Gallery's barcode, so Unicorn Cards' Glorious Gallery page (listing 11998)
    went to Chaos Origins by barcode."""

    def setUp(self):
        yugioh = make_game("Yu-Gi-Oh!", "yu-gi-oh")
        self.gallery = make_product(make_set(yugioh, "Glorious Gallery", "glorious-gallery", code="BLGG"),
                                    "Battles of Legend: Glorious Gallery Booster Box (1st Edition)", slug="gallery",
                                    product_type="booster_box", ean="4012927170304")
        self.chaos = make_product(make_set(yugioh, "Chaos Origins", "chaos-origins", code="CHOR"),
                                  "Chaos Origins Booster Box (1st Edition)", slug="chaos", product_type="booster_box",
                                  ean="4012927170304")
        self.shop = make_retailer("Unicorn Cards", source_type=Retailer.Source.WEBSITE, source_url="https://unicorncards.co.uk")

    def test_a_barcode_two_products_carry_names_neither(self):
        from .importers import barcode_owners, ean_key

        self.assertNotIn(ean_key("4012927170304"), barcode_owners())
        offer = Offer(title="YuGiOh! Battles of Legend: Glorious Gallery 1st Edition Booster Display Box",
                      url="https://unicorncards.co.uk/yugioh-battles-of-legend-glorious-gallery-1st-edition-booster-display-box",
                      price=Decimal("59.90"), ean="4012927170304")
        apply_offers(self.shop, [offer], complete=False)
        self.assertEqual(list(Listing.objects.values_list("product_id", flat=True)), [self.gallery.pk])

    def test_nor_does_it_keep_the_wrong_one_of_two_listings_on_its_page(self):
        from io import StringIO

        from django.core.management import call_command

        url = "https://unicorncards.co.uk/yugioh-battles-of-legend-glorious-gallery-1st-edition-booster-display-box"
        make_listing(self.gallery, self.shop, url=url, title="")
        make_listing(self.chaos, self.shop, url=url, shop_ean="4012927170304",
                     title="YuGiOh! Battles of Legend: Glorious Gallery 1st Edition Booster Display Box")
        call_command("tidy_listings", stdout=StringIO())
        self.assertEqual(list(Listing.objects.values_list("product_id", flat=True)), [self.gallery.pk])


class MergeRuleTests(TestCase):
    """The hourly merge reads two of our names by the rule a read applies to a shop's title."""

    def test_editions_conditions_and_units_are_never_merged(self):
        from types import SimpleNamespace

        from .management.commands.merge_duplicates import why_not_merge

        def product(pk, name, game="pokemon"):
            return SimpleNamespace(pk=pk, name=name, game_slug=game)

        for keep, other, game in (
            ("Black Bolt Elite Trainer Box (Normal)", "Black Bolt Elite Trainer Box (Damaged)", "pokemon"),
            ("Perfect Order Elite Trainer Box", "Center Perfect Order Elite Trainer Box", "pokemon"),
            ("Ninja Spinner m4 Japanese Booster Box (Shrinked Booster Box)",
             "Ninja Spinner m4 Japanese Booster Box (No Shrink Booster Box)", "pokemon"),
            ("Star Trek Collector Booster", "Star Trek Collector Booster (Booster Box)", "magic-the-gathering"),
        ):
            self.assertTrue(why_not_merge(product(1, keep, game), product(2, other, game)), other)
        self.assertEqual(why_not_merge(product(1, "Surging Sparks Booster Box"),
                                       product(2, "Surging Sparks Booster Display Box")), "")
