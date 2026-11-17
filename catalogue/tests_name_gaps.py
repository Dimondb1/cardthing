"""The gaps that made one product two pages, closed after the audit of 17 Nov 2026 (the owner: "Approve all"):
a name written joined or split ("Superheroes", "Super Heroes"), a shop's own name on its titles ("| Topaz Cards"),
sales wording ("Buy ... 2026", "Official Factory"), One Piece's "Extra Booster", text damaged in a shop's feed
("â??") and a name written all in small letters. Each example is a page the audit found live."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from .classify import classify, proper_case, tidy_name
from .importers import Offer, apply_offers
from .management.commands.merge_duplicates import CERTAIN, certainty, duplicate_pairs, shown_products, why_not_merge
from .matching import match_key, mend, own_name_off, same_letters, same_product, squash_key
from .models import CheckAnswer, Listing, Product, ProductAlias, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set

BOX, PACK, DECK, BUNDLE = (Product.Type.BOOSTER_BOX, Product.Type.BOOSTER_PACK, Product.Type.DECK, Product.Type.BUNDLE)


class SameLettersTests(SimpleTestCase):
    def test_a_name_joined_or_split_is_one_name(self):
        for first, second, game in (
            ("Marvel Super Heroes Gift Bundle", "Marvel Superheroes Gift Bundle", "magic-the-gathering"),
            ("Marvel Super Heroes Prerelease Pack", "Marvel Super Heroes Pre-Release Pack", "magic-the-gathering"),
            ("Marvel Super Heroes Jumpstart Booster Box", "Marvel Super Heroes Jump Start Booster Box", "magic-the-gathering"),
            ("30th Celebration Knockout Collection", "30th Celebration Knock Out Collection", "pokemon"),
            ("Learn Together Deck Set LT-01", "Learn Together Deck Set LT01", "one-piece"),
            ("Magnificent Maestros Tuck Box", "Magnificent Maestros Tuckbox", "yu-gi-oh"),
            ("Legends of the Force Spotlight Deck Qui-Gon Jinn", "Legends of the Force Spotlight Deck QuiGonJinn",
             "star-wars-unlimited"),
            ("Start Deck 100 CS4Da C Simplified Chinese Starter Deck", "Start Deck 100 CS4DaC Simplified Chinese Starter Deck",
             "pokemon"),
            ("Hatsune Miku Colourful Stage WonderlandsxShowtime Trial Deck",
             "Hatsune Miku Colourful Stage Wonderlands x Showtime Trial Deck", "weiss-schwarz"),
            # The game's own name and "English" are every name's, written or not.
            ("Pokémon TCG: 151 Booster Pack", "151 English Booster Pack", "pokemon"),
        ):
            with self.subTest(first=first):
                self.assertTrue(same_letters(first, second, game))
                # The key of whole words never paired them, which is why each was two pages.
                self.assertNotEqual(match_key(first, game), match_key(second, game)) if "151" not in first else None

    def test_every_number_and_every_letter_must_be_the_same(self):
        for first, second, game in (
            # What the words' key sets aside as what a pack holds stays here: these are other products.
            ("Chaos Rising 1 Pack Blister", "Chaos Rising 3 Pack Blister", "pokemon"),
            ("Gem Pack 4 Chinese Booster Pack", "Gem Pack 6 Chinese Booster Pack", "pokemon"),
            ("Rarity Collection 5 Booster Pack", "Rarity Collection II Booster Pack", "yu-gi-oh"),
            ("Gem Pack Vol 1 2 Booster Box", "Gem Pack Vol 12 Booster Box", "pokemon"),
            ("Surging Sparks Booster Box", "Surging Sparks Booster Pack", "pokemon"),
            ("Surging Sparks Booster Box", "Surging Sparks Booster Box Case", "pokemon"),
            ("OP-09 Booster Box", "OP-10 Booster Box", "one-piece"),
            # Other words in another order is another matter, for the words' key.
            ("Booster Box Surging Sparks", "Surging Sparks Booster Box", "pokemon"),
        ):
            with self.subTest(first=first):
                self.assertFalse(same_letters(first, second, game))

    def test_a_name_that_says_only_its_kind_has_no_key(self):
        for name in ("Booster Box", "Elite Trainer Box", "Pokémon TCG Booster Pack", "English Booster Bundle", ""):
            with self.subTest(name=name):
                self.assertEqual(squash_key(name, "pokemon"), "")
        self.assertFalse(same_letters("Booster Box", "Booster Box", "pokemon"))

    def test_shop_notes_are_set_aside_as_for_the_words(self):
        self.assertTrue(same_letters("Marvel Superheroes Gift Bundle (Pre Order)", "Marvel Super Heroes Gift Bundle",
                                     "magic-the-gathering"))


class ExtraBoosterTests(SimpleTestCase):
    """One Piece's "Extra Booster" is the name of its EB sets, which shops add or leave out."""

    def test_the_box_is_the_booster_box_and_the_pack_the_booster_pack(self):
        box = "EB05 Heroines Edition Vol.2 Booster Box"
        for other in ("One Piece Heroines Edition Vol.2 Booster Box Extra Booster", "Heroines Edition Vol.2 Extra Booster Box",
                      "One Piece Card Game: Extra Booster - Heroines Edition Vol.2 Booster Display"):
            with self.subTest(other=other):
                self.assertEqual(match_key(box, "one-piece"), match_key(other, "one-piece"))
                self.assertTrue(same_product(box, other, "one-piece"))
        pack = "EB05 Heroines Edition Vol.2 Booster Pack"
        self.assertEqual(match_key(pack, "one-piece"), match_key("Heroines Edition Vol.2 Extra Booster Pack", "one-piece"))
        self.assertNotEqual(match_key(box, "one-piece"), match_key(pack, "one-piece"))
        self.assertEqual(match_key("Extra Booster Memorial Collection EB-01 Booster Box", "one-piece"),
                         match_key("Memorial Collection EB01 Booster Box", "one-piece"))

    def test_another_volume_or_code_is_still_another_set(self):
        first = "Heroines Edition EB-03 Booster Box"
        second = "One Piece Heroines Edition Vol.2 Booster Box Extra Booster"
        self.assertFalse(same_product(first, second, "one-piece"))
        self.assertFalse(same_product(first, "EB05 Heroines Edition Extra Booster Box", "one-piece"))

    def test_premium_booster_boxes_no_longer_read_as_packs(self):
        self.assertEqual(match_key("Premium Booster PRB-01 Booster Box", "one-piece"),
                         match_key("One Piece Premium Booster Box PRB-01", "one-piece"))
        self.assertNotEqual(match_key("Premium Booster PRB-01 Booster Box", "one-piece"),
                            match_key("Premium Booster PRB-01 Booster Pack", "one-piece"))

    def test_only_one_piece_reads_it_so(self):
        # Cardfight Vanguard's Extra Boosters are sets of their own beside its Booster sets.
        self.assertNotEqual(match_key("The Answer of Truth Extra Booster Box", "cardfight-vanguard"),
                            match_key("The Answer of Truth Booster Box", "cardfight-vanguard"))


class OwnNameTests(SimpleTestCase):
    def test_a_shops_own_name_comes_off_the_end_and_the_start_of_its_titles(self):
        for title, shop, wanted in (
            ("Girls Band Cry Premium Booster Box | Topaz Cards", "Topaz Cards", "Girls Band Cry Premium Booster Box"),
            ("Oshi no Ko Vol.2 Booster Box (English) - Topaz Cards", "Topaz Cards", "Oshi no Ko Vol.2 Booster Box (English)"),
            ("Makeine Booster Box Sealed Display Topaz Cards", "Topaz Cards", "Makeine Booster Box Sealed Display"),
            ("Girls Band Cry Premium Booster Box – TopazCards", "Topaz Cards", "Girls Band Cry Premium Booster Box"),
            ("A Lawless Time Booster Box Trading Card Game Titan Cards", "Titan Cards", "A Lawless Time Booster Box Trading Card Game"),
            ("Surging Sparks Booster Box at Total Cards", "Total Cards", "Surging Sparks Booster Box"),
            ("Surging Sparks Booster Box (The Card Vault)", "The Card Vault", "Surging Sparks Booster Box"),
            ("Northumbrian Tin Soldier One Piece: Heroines Battle Vol.2", "Northumbrian Tin Soldier", "One Piece: Heroines Battle Vol.2"),
            ("Cosmic Eclipse Booster Box - Eclipse Cards", "Eclipse Cards", "Cosmic Eclipse Booster Box"),
        ):
            with self.subTest(title=title):
                self.assertEqual(own_name_off(title, shop), wanted)

    def test_it_is_left_where_it_may_be_the_products_own_words(self):
        for title, shop in (
            # Shops named in the words products are named in.
            ("Cyber Eden Booster Display Box", "Display Box"),
            ("Lillie Premium Tournament Collection Pokemon TCG Box", "TCG Box"),
            ("Final Fantasy Game Collection", "The Game Collection"),
            ("Surging Sparks Booster Box MY TCG", "MY TCG"),
            # Its name inside the product's, with no separator and no kind of product before it.
            ("Pokemon Cosmic Eclipse Cards", "Eclipse Cards"),
            # A mystery box or pack is the shop's own make.
            ("Binderly One Piece Mystery Slab Pack", "Binderly"),
            ("Topaz Cards Pokémon Mystery Box", "Topaz Cards"),
            # Nothing would be left, or the name is in the middle.
            ("Topaz Cards", "Topaz Cards"),
            ("Girls Band Cry by Topaz Cards Premium Booster Box", "Topaz Cards"),
            # Another shop's name is nothing to this shop's titles.
            ("Girls Band Cry Premium Booster Box | Topaz Cards", "Harbour Games"),
            ("Surging Sparks Booster Box", ""),
        ):
            with self.subTest(title=title):
                self.assertEqual(own_name_off(title, shop), title)


class TidyNameTests(SimpleTestCase):
    def test_sales_wording_goes(self):
        for title, wanted in (
            ("Buy Lorcana Reign of Jafar Booster Box 2026", "Lorcana Reign of Jafar Booster Box"),
            ("Buy Magic the Gathering Modern Masters 2017 Booster Box", "Magic the Gathering Modern Masters 2017 Booster Box"),
            ("Card Game SV Destined Rivals Booster Box Official Factory", "SV Destined Rivals Booster Box"),
            ("Booster Pack ME Ascended Heroes Official Factory Sealed", "Booster Pack ME Ascended Heroes"),
            ("Elite Trainer Box SV Prismatic Evolutions - Factory Sealed", "Elite Trainer Box SV Prismatic Evolutions"),
            ("Japanese TCG: Super Electric Breaker SV8 Japanese Booster Box", "Super Electric Breaker SV8 Japanese Booster Box"),
            ("Korean TCG 151 Korean Booster Pack", "151 Korean Booster Pack"),
            ("Chinese Pokémon TCG: Terastal Gathering CSV9.5 C Chinese Booster Pack", "Terastal Gathering CSV9.5 C Chinese Booster Pack"),
        ):
            with self.subTest(title=title):
                self.assertEqual(tidy_name(title), wanted)

    def test_what_is_part_of_the_name_stays(self):
        for title in (
            "Holiday Calendar 2025",                             # the year names the product
            "Scarlet & Violet Destined Rivals Booster Box 2026",  # a year without "Buy" is left: it may name it
            "Buy 12 Booster Packs Get 5 Off",                    # an offer, which is no product: never made to look like one
            "Buy Both Marvel Super Heroes Scene Boxes for 45",
            "Japanese Pokemon: 30th Celebration Booster Box",    # its only word for the language
            "Factory Sealed Booster Box Case",                   # not at the end
            "Prismatic Evolutions Elite Trainer Box",
        ):
            with self.subTest(title=title):
                self.assertEqual(tidy_name(title), title)

    def test_a_new_page_is_named_without_them(self):
        for title, wanted in (
            ("Buy Lorcana Reign of Jafar Booster Box 2026", "Reign of Jafar Booster Box"),
            ("Pokemon Card Game SV Destined Rivals Booster Box Official Factory Sealed", "SV Destined Rivals Booster Box"),
            ("Japanese TCG: Pokemon Super Electric Breaker SV8 Japanese Booster Box", "Super Electric Breaker SV8 Japanese Booster Box"),
        ):
            with self.subTest(title=title):
                self.assertEqual(classify(title, price=Decimal("100")).name, wanted)

    def test_damaged_text_is_mended(self):
        for text, wanted in (
            ("Universes Beyond: Warhammer 40,000 Commander Deck â?? Tyranid Swarm",
             "Universes Beyond: Warhammer 40,000 Commander Deck - Tyranid Swarm"),
            ("Collectorâ??s Edition Commander Deck", "Collector's Edition Commander Deck"),
            ("PokÃ©mon Surging Sparks Booster Box", "Pokémon Surging Sparks Booster Box"),
            ("Commander Deck â€“ Tyranid Swarm", "Commander Deck – Tyranid Swarm"),
            ("Illumineerâ€™s Trove", "Illumineer’s Trove"),
            # Nothing wrong with these: an accent, a pound sign, a question.
            ("Pokémon Surging Sparks Booster Box", "Pokémon Surging Sparks Booster Box"),
            ("Flabébé Tin £9.99", "Flabébé Tin £9.99"),
            ("Who's That Pokémon? Box", "Who's That Pokémon? Box"),
            ("", ""),
        ):
            with self.subTest(text=text):
                self.assertEqual(mend(text), wanted)
        self.assertEqual(tidy_name("Universes Beyond: Warhammer 40,000 Commander Deck â?? Tyranid Swarm"),
                         "Universes Beyond: Warhammer 40,000 Commander Deck - Tyranid Swarm")

    def test_a_name_all_in_small_letters_gets_capitals(self):
        for name, wanted in (
            ("one piece card game extra booster box eb 05", "One Piece Card Game Extra Booster Box EB 05"),
            ("op09 emperors in the new world booster box", "OP09 Emperors in the New World Booster Box"),
            ("charizard ex premium collection", "Charizard ex Premium Collection"),
            ("pokemon tcg surging sparks etb", "Pokemon TCG Surging Sparks ETB"),
            ("lord of the rings: tales of middle-earth bundle", "Lord of the Rings: Tales of Middle-earth Bundle"),
        ):
            with self.subTest(name=name):
                self.assertEqual(proper_case(name), wanted)
        # A name with any capital in it is as the shop meant it.
        for name in ("Charizard ex Premium Collection", "iPhone Booster Box", "EB05 heroines edition", "151", ""):
            with self.subTest(name=name):
                self.assertEqual(proper_case(name), name)


class ImportTests(TestCase):
    """A shop's product joins the page other shops' prices are on, and no second page is made."""

    def setUp(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        heroes = make_set(magic, name="Marvel Super Heroes", slug="marvel-super-heroes", code="MSH")
        self.bundle = make_product(heroes, name="Marvel Super Heroes Gift Bundle", slug="marvel-super-heroes-gift-bundle",
                                   product_type=BUNDLE)
        weiss = make_game(name="Weiss Schwarz", slug="weiss-schwarz")
        self.cry = make_product(make_set(weiss, name="Girls Band Cry", slug="girls-band-cry", code="GBC"),
                                name="Girls Band Cry Premium Booster Box", slug="girls-band-cry-premium-booster-box",
                                product_type=BOX)
        self.harbour = make_retailer("Harbour Games")
        self.topaz = make_retailer("Topaz Cards")
        make_listing(self.bundle, self.harbour, price="60.00")
        make_listing(self.cry, self.harbour, price="80.00")

    def offer(self, title, slug, price="55.00", **extra):
        return Offer(title=title, url=f"https://topaz.example/products/{slug}", price=Decimal(price), **extra)

    def test_a_name_written_joined_joins_the_page(self):
        before = Product.objects.count()
        apply_offers(self.topaz, [self.offer("Magic: The Gathering - Marvel Superheroes Gift Bundle", "msh-gift-bundle")])
        self.assertEqual(Product.objects.count(), before)
        self.assertEqual(set(self.bundle.listings.values_list("retailer__name", flat=True)), {"Harbour Games", "Topaz Cards"})

    def test_another_language_never_joins_it(self):
        before = Product.objects.count()
        apply_offers(self.topaz, [self.offer("Magic: The Gathering - Marvel Superheroes Gift Bundle (Japanese)", "msh-gift-bundle-jp")])
        self.assertEqual(self.bundle.listings.count(), 1)
        self.assertEqual(Product.objects.count(), before + 1)

    def test_the_shops_own_name_on_its_title_joins_the_page(self):
        before = Product.objects.count()
        apply_offers(self.topaz, [self.offer("Weiss Schwarz Girls Band Cry Premium Booster Box | Topaz Cards", "gbc-premium", "85.00")])
        self.assertEqual(Product.objects.count(), before)
        listing = Listing.objects.get(product=self.cry, retailer=self.topaz)
        self.assertEqual(listing.price, Decimal("85.00"))

    def test_a_new_page_is_never_named_after_the_shop(self):
        apply_offers(self.topaz, [self.offer("Weiss Schwarz Makeine Too Many Losing Heroines Booster Box | Topaz Cards", "makeine", "90.00")])
        made = Product.objects.get(listings__retailer=self.topaz)
        self.assertEqual(made.name, "Makeine Too Many Losing Heroines Booster Box")
        self.assertNotIn("topaz", made.slug)

    def test_a_marketplaces_titles_are_left_as_they_are(self):
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        offer = Offer(title="Girls Band Cry Premium Booster Box eBay", url="https://www.ebay.co.uk/itm/1", price=Decimal("70"),
                      product_pk=self.cry.pk)
        apply_offers(ebay, [offer])
        self.assertEqual(offer.title, "Girls Band Cry Premium Booster Box eBay")


class TidyTests(TestCase):
    """The hourly tidy brings the pages already made into line."""

    def setUp(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        self.heroes = make_set(magic, name="Marvel Super Heroes", slug="marvel-super-heroes", code="MSH")
        self.harbour, self.tollgate, self.topaz = (make_retailer(name) for name in ("Harbour Games", "Tollgate TCG", "Topaz Cards"))

    def product(self, name, kind=BOX, shop=None, price="100.00", product_set=None):
        from django.utils.text import slugify

        made = make_product(product_set or self.heroes, name=name, slug=slugify(name), product_type=kind)
        if shop is not None:
            make_listing(made, shop, price=price)
        return made

    def merge(self):
        out = StringIO()
        call_command("merge_duplicates", stdout=out)
        return out.getvalue()

    def test_the_hourly_merge_joins_a_name_written_joined_and_split(self):
        keep = self.product("Marvel Super Heroes Jumpstart Booster Box", shop=self.harbour)
        make_listing(keep, self.tollgate, price="105.00")
        other = self.product("Marvel Super Heroes Jump Start Booster Box", shop=self.topaz)
        # Without the letters, "Jumpstart Booster" and "Start Booster" read as two kinds of booster.
        self.assertEqual(why_not_merge(keep, other), "")
        self.assertEqual(certainty(keep, other), CERTAIN)
        self.assertIn("merged: Marvel Super Heroes Jump Start Booster Box", self.merge())
        other.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertEqual(ProductAlias.objects.get(slug=other.slug).product, keep)
        self.assertEqual(keep.listings.count(), 3)
        # Listed under Sorted for you, with Undo.
        self.assertTrue(CheckAnswer.objects.filter(kind=CheckAnswer.Kind.MERGE, product=keep, other=other).exists())

    def test_it_never_joins_another_size_or_number(self):
        pokemon = make_set(make_game(), name="Chaos Rising", slug="chaos-rising", code="ME04")
        one = self.product("Chaos Rising 1 Pack Blister", kind=PACK, shop=self.harbour, product_set=pokemon)
        three = self.product("Chaos Rising 3 Pack Blister", kind=PACK, shop=self.topaz, product_set=pokemon)
        self.merge()
        for product in (one, three):
            product.refresh_from_db()
            self.assertTrue(product.is_active)

    def test_a_pair_a_shop_sells_both_of_waits_for_the_owner(self):
        keep = self.product("Marvel Super Heroes Gift Bundle", kind=BUNDLE, shop=self.harbour)
        other = self.product("Marvel Superheroes Gift Bundle", kind=BUNDLE, shop=self.harbour, price="62.00")
        self.assertIn("a shop sells both", self.merge())
        other.refresh_from_db()
        self.assertTrue(other.is_active)
        # And is listed under Possible duplicates, as certain as a pair can be.
        pairs = duplicate_pairs(shown_products())
        self.assertIn({keep.pk, other.pk}, [{first.pk, second.pk} for first, second in pairs])

    def test_one_pieces_extra_booster_pages_are_merged(self):
        one_piece = make_set(make_game(name="One Piece Card Game", slug="one-piece"), name="One Piece Heroines Edition Vol.2",
                             slug="heroines-edition-vol2", code="EB-05")
        keep = self.product("EB05 Heroines Edition Vol.2 Booster Box", shop=self.harbour, product_set=one_piece)
        make_listing(keep, self.tollgate, price="219.99")
        other = self.product("One Piece Heroines Edition Vol.2 Booster Box Extra Booster", shop=self.topaz, price="199.00",
                             product_set=one_piece)
        pack = self.product("EB05 Heroines Edition Vol.2 Booster Pack", kind=PACK, shop=self.harbour, price="4.99",
                            product_set=one_piece)
        self.merge()
        other.refresh_from_db()
        pack.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertTrue(pack.is_active)
        self.assertEqual(keep.listings.count(), 3)

    def test_the_catalogue_tidy_takes_a_shops_name_off_a_page_and_merges_it_into_its_twin(self):
        weiss = make_set(make_game(name="Weiss Schwarz", slug="weiss-schwarz"), name="Girls Band Cry", slug="girls-band-cry", code="GBC")
        keep = self.product("Girls Band Cry Premium Booster Box", shop=self.harbour, product_set=weiss)
        named = self.product("Girls Band Cry Premium Booster Box Topaz Cards", shop=self.topaz, price="85.00", product_set=weiss)
        alone = self.product("Makeine Booster Box Sealed Display Topaz Cards", shop=self.topaz, price="90.00", product_set=weiss)
        # Another shop's product whose name ends in those words is left: it is not Topaz's title.
        other_shop = self.product("Oshi no Ko Booster Box Topaz Cards", shop=self.tollgate, product_set=weiss)
        out = StringIO()
        call_command("tidy_catalogue", stdout=out)
        named.refresh_from_db()
        self.assertFalse(named.is_active)
        self.assertEqual(ProductAlias.objects.get(slug=named.slug).product, keep)
        # Listed under Sorted for you with Undo, as every merge the site makes by itself is.
        answer = CheckAnswer.objects.get(kind=CheckAnswer.Kind.MERGE, product=keep, other=named)
        self.assertFalse(answer.by_owner)
        self.assertIn("with the shop's wording taken off", answer.why)
        self.assertEqual(set(keep.listings.values_list("retailer__name", flat=True)), {"Harbour Games", "Topaz Cards"})
        alone.refresh_from_db()
        self.assertEqual(alone.name, "Makeine Booster Box Sealed Display")
        self.assertEqual(alone.slug, "makeine-booster-box-sealed-display-topaz-cards")   # a rename never changes an address
        other_shop.refresh_from_db()
        self.assertEqual(other_shop.name, "Oshi no Ko Booster Box Topaz Cards")

    def test_a_merge_the_owner_undid_is_not_made_again(self):
        from catalogue import autopilot

        weiss = make_set(make_game(name="Weiss Schwarz", slug="weiss-schwarz"), name="Girls Band Cry", slug="girls-band-cry", code="GBC")
        keep = self.product("Girls Band Cry Premium Booster Box", shop=self.harbour, product_set=weiss)
        named = self.product("Girls Band Cry Premium Booster Box Topaz Cards", shop=self.topaz, price="85.00", product_set=weiss)
        call_command("tidy_catalogue", stdout=StringIO())
        self.assertTrue(autopilot.undo(CheckAnswer.objects.get(kind=CheckAnswer.Kind.MERGE, product=keep, other=named)))
        named.refresh_from_db()
        self.assertTrue(named.is_active)
        call_command("tidy_catalogue", stdout=StringIO())
        self.merge()
        named.refresh_from_db()
        # Renamed, and left beside its twin: his Undo stands.
        self.assertTrue(named.is_active)
        self.assertEqual(named.name, "Girls Band Cry Premium Booster Box")
        self.assertEqual(Listing.objects.filter(product=named).count(), 1)

    def test_the_catalogue_tidy_mends_and_capitalises_names(self):
        deck = self.product("Universes Beyond: Warhammer 40,000 Commander Deck â?? Tyranid Swarm", kind=DECK, shop=self.harbour)
        small = self.product("marvel super heroes scene box heroes united", kind=Product.Type.COLLECTION_BOX, shop=self.harbour)
        call_command("tidy_catalogue", stdout=StringIO())
        deck.refresh_from_db()
        small.refresh_from_db()
        self.assertEqual(deck.name, "Universes Beyond: Warhammer 40,000 Commander Deck - Tyranid Swarm")
        self.assertEqual(small.name, "Marvel Super Heroes Scene Box Heroes United")
