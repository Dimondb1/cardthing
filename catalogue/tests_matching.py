from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .importers import Offer, apply_offers
from .languages import language_of
from .matching import match_key
from .models import Listing, Product
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class WrongLinkTests(TestCase):
    """Shop items must not land on a product of another game, set or kind."""

    def setUp(self):
        self.pokemon = make_game()
        self.magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        self.shop = make_retailer("Shop")
        self.box = make_product(make_set(self.magic, name="Invasion"), name="Invasion Booster Box", product_type="booster_box")

    def offer(self, title, price="50"):
        return Offer(title=title, url="https://shop.example/" + title.lower().replace(" ", "-"), price=Decimal(price))

    def test_another_games_box_with_the_same_words_is_not_linked(self):
        apply_offers(self.shop, [self.offer("Pokemon TCG Sun and Moon Crimson Invasion Booster Box", "143")])
        self.assertFalse(Listing.objects.filter(product=self.box).exists())
        self.assertTrue(Product.objects.filter(game=self.pokemon, name__icontains="Crimson Invasion").exists())

    def test_a_single_card_is_never_matched_by_name(self):
        apply_offers(self.shop, [self.offer("Magic The Gathering Marvel Super Heroes Invasion Booster Box Extended Art", "3.95")])
        self.assertFalse(Listing.objects.filter(retailer=self.shop).exists())

    def test_a_title_with_many_extra_words_goes_to_review_not_the_product(self):
        apply_offers(self.shop, [self.offer("Magic Invasion of Chaos Reprint Unlimited Edition Booster Box", "62")])
        self.assertFalse(Listing.objects.filter(product=self.box).exists())

    def test_a_variant_with_an_extra_identifying_word_is_not_linked(self):
        pack = make_product(make_set(make_game(name="Star Wars Unlimited", slug="star-wars-unlimited", short_name="Star Wars"), name="Ashes of the Empire", slug="ashes"), name="Ashes of the Empire Booster Pack", product_type="booster_pack")
        apply_offers(self.shop, [self.offer("Star Wars Unlimited Ashes of the Empire Carbonite Booster Pack", "24.95")])
        self.assertFalse(Listing.objects.filter(product=pack).exists())
        apply_offers(make_retailer("Other"), [self.offer("Star Wars Unlimited Ashes of the Empire Booster Pack (Sealed)", "4.99")])
        self.assertTrue(Listing.objects.filter(product=pack).exists())

    def test_the_same_product_in_the_same_game_still_links(self):
        apply_offers(self.shop, [self.offer("Magic The Gathering Invasion Booster Box", "1200")])
        self.assertTrue(Listing.objects.filter(product=self.box).exists())

    def test_same_words_in_another_game_get_their_own_address(self):
        apply_offers(self.shop, [self.offer("Yu-Gi-Oh Chaos Origins Booster Pack", "4"), self.offer("Riftbound League of Legends Origins Booster Pack", "5")])
        slugs = set(Product.objects.filter(name__icontains="Origins").values_list("slug", flat=True))
        self.assertEqual(len(slugs), 2)


class ShopTitleTests(TestCase):
    def test_game_prefixes_and_separators_come_off(self):
        from .matching import shop_title

        self.assertEqual(shop_title("Disney Lorcana: Shimmering Skies - Booster Box"), "Shimmering Skies Booster Box")
        self.assertEqual(shop_title("Star Wars: Unlimited - A Lawless Time - Booster Box"), "A Lawless Time Booster Box")
        self.assertEqual(shop_title("Pokemon TCG: Mega Evolution Perfect Order - Booster Pack"), "Mega Evolution Perfect Order Booster Pack")
        self.assertEqual(shop_title("Magic: The Gathering - Foundations - Beginner Box"), "Foundations Beginner Box")
        self.assertEqual(shop_title("Yu-Gi-Oh! TCG: Battles of Legend: Chapter 1 Box"), "Battles of Legend Chapter 1 Box")
        self.assertEqual(shop_title("Prismatic Evolutions Elite Trainer Box"), "Prismatic Evolutions Elite Trainer Box")

    def test_a_prefixed_shop_title_matches_our_plain_name_in_full(self):
        from .importers import Catalogue
        from .matching import AUTO_LINK

        catalogue = Catalogue([(1, "A Lawless Time Booster Box", "star-wars-unlimited"), (2, "Shimmering Skies Booster Box", "lorcana")])
        match, value = catalogue.best_match("Star Wars: Unlimited - A Lawless Time - Booster Box", game="star-wars-unlimited")
        self.assertEqual((match[0], value), (1, AUTO_LINK))
        match, value = catalogue.best_match("Disney Lorcana: Shimmering Skies - Booster Box", game="lorcana")
        self.assertEqual((match[0], value), (2, AUTO_LINK))


class TidyListingsTests(TestCase):
    def test_a_product_filed_under_the_wrong_game_is_moved_not_emptied(self):
        pokemon = make_game(name="Pokemon", slug="pokemon", short_name="Pokemon")
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        box = make_product(make_set(pokemon, name="Misc", slug="misc"), name="Zendikar Rising Set Booster Display (30 Count)",
                           product_type="booster_box")
        listing = make_listing(box, make_retailer("Card Empire"),
                               url="https://www.cardempire.co.uk/products/magic-the-gathering-zendikar-rising-set-booster-display-30-count")
        preview = StringIO()
        call_command("tidy_listings", "--dry-run", stdout=preview)
        self.assertIn("would move: Zendikar Rising", preview.getvalue())
        self.assertNotIn("would remove", preview.getvalue())
        box.refresh_from_db()
        self.assertEqual(box.game, pokemon)
        call_command("tidy_listings", stdout=StringIO())
        box.refresh_from_db()
        self.assertEqual(box.game, magic)
        self.assertIsNone(box.product_set)
        self.assertTrue(Listing.objects.filter(pk=listing.pk).exists())

    def test_the_title_beats_a_shop_tag_for_the_game(self):
        from .classify import classify

        sealed = classify("Magic: The Gathering - Zendikar Rising Set Booster Display (30 Count)",
                          vendor="cardempireuk", tags=["Booster Box", "Pokemon"], shop_type="Pokemon cards", price=120)
        self.assertEqual(sealed.game, "magic-the-gathering")

    def test_a_slug_ending_in_booster_is_not_taken_for_a_pack(self):
        yugioh = make_game(name="Yu-Gi-Oh", slug="yu-gi-oh", short_name="Yu-Gi-Oh")
        box = make_product(make_set(yugioh, name="Blazing Dominion", slug="bd"), name="TCG: Blazing Dominion Booster Box",
                           product_type="booster_box")
        pokemon = make_game(name="Pokemon", slug="pokemon", short_name="Pokemon")
        etb = make_product(make_set(pokemon, name="151", slug="151"), name="151 Elite Trainer Box", product_type="elite_trainer_box")
        rebel = make_product(make_set(pokemon, name="Rebel Clash", slug="rc"), name="Rebel Clash Booster Box (36 Sealed Booster Packs)",
                             product_type="booster_box")
        kept = [
            make_listing(box, make_retailer("A"), url="https://a.example/products/yu-gi-oh-tcg-blazing-dominion-booster"),
            make_listing(etb, make_retailer("B"), url="https://b.example/products/pokemon-scarlet-violet-151-booster-etb"),
            make_listing(rebel, make_retailer("C"), url="https://c.example/products/pokemon-rebel-clash-booster-box"),
        ]
        wrong = make_listing(box, make_retailer("D"), url="https://d.example/products/yu-gi-oh-blazing-dominion-booster-pack")
        call_command("tidy_listings", stdout=StringIO())
        for listing in kept:
            self.assertTrue(Listing.objects.filter(pk=listing.pk).exists(), listing.url)
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())

    def test_a_box_address_that_says_36x_packs_is_kept_and_a_multi_buy_address_is_not(self):
        pokemon = make_game(name="Pokemon", slug="pokemon", short_name="Pokemon")
        box = make_product(make_set(pokemon, name="Destined Rivals", slug="dri"), name="Destined Rivals Booster Box",
                           product_type="booster_box")
        kept = make_listing(box, make_retailer("Card Vault"),
                            url="https://cv.example/products/pokemon-tcg-destined-rivals-booster-box-36x-packs")
        wrong = make_listing(box, make_retailer("Other"),
                             url="https://o.example/products/pokemon-destined-rivals-booster-box-36x-packs-plus-6x-booster-packs")
        call_command("tidy_listings", stdout=StringIO())
        self.assertTrue(Listing.objects.filter(pk=kept.pk).exists())
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())

    def test_an_address_in_the_products_own_language_is_kept(self):
        # The hourly merge kept "Chinese Gem Pack Vol 4 Booster Box", and the tidy then deleted every other shop's
        # "...-simplified-chinese-..." listing it had moved there, which their next reads put back (15 Oct 2026).
        pokemon = make_game(name="Pokemon", slug="pokemon", short_name="Pokemon")
        gem = make_product(make_set(pokemon, name="Gem Pack Vol 4", slug="gem-4"), name="Chinese Gem Pack Vol 4 Booster Box",
                           product_type="booster_box")
        kept = [
            make_listing(gem, make_retailer("Solent Cards"),
                         url="https://solentcards.co.uk/products/pokemon-gem-pack-vol-4-cbb4-c-simplified-chinese-booster-box"),
            make_listing(gem, make_retailer("Lvl Up"), url="https://lvlup.example/products/gem-4-simplified-chinese-booster-box"),
        ]
        wrong = [
            make_listing(gem, make_retailer("Japan2UK"), url="https://j2uk.example/products/gem-pack-vol-4-traditional-chinese-box"),
            make_listing(gem, make_retailer("Other"), url="https://other.example/products/gem-pack-vol-4-japanese-booster-box"),
        ]
        english = make_product(make_set(pokemon, name="Surging Sparks", slug="ssp"), name="Surging Sparks Booster Box",
                               product_type="booster_box")
        wrong.append(make_listing(english, make_retailer("Third"), url="https://third.example/surging-sparks-booster-box-japanese"))
        call_command("tidy_listings", stdout=StringIO())
        for listing in kept:
            self.assertTrue(Listing.objects.filter(pk=listing.pk).exists(), listing.url)
        for listing in wrong:
            self.assertFalse(Listing.objects.filter(pk=listing.pk).exists(), listing.url)

    def test_listings_on_the_wrong_product_are_removed(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        box = make_product(make_set(magic, name="Invasion", slug="invasion"), name="Invasion Booster Box", product_type="booster_box")
        shop = make_retailer("Shop")
        wrong = make_listing(box, shop, url="https://shop.example/products/pokemon-tcg-sun-moon-4-crimson-invasion-booster-box")
        right = make_listing(box, make_retailer("Other"), url="https://other.example/magic-the-gathering-invasion-booster-box")
        no_game = make_listing(box, make_retailer("Third"), url="https://third.example/products/invasion-booster-box")
        garbled = make_listing(box, make_retailer("Fourth"), url="https://fourth.example/products/mtg-invasion-bstr-box-p81362")
        japanese = make_listing(box, make_retailer("Fifth"), url="https://fifth.example/magic-the-gathering-invasion-booster-box-japanese")
        pack = make_listing(box, make_retailer("Sixth"), url="https://sixth.example/products/magic-invasion-booster-pack")
        single = make_listing(box, make_retailer("Seventh"), url="https://seventh.example/products/magic-invasion-booster-box-playmat")
        slip = make_listing(box, make_retailer("Eighth"), url="https://eighth.example/products/magic-invasion-booster-booster-box-36-packs")
        call_command("tidy_listings", stdout=StringIO())
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=right.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=no_game.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=garbled.pk).exists())
        self.assertFalse(Listing.objects.filter(pk=japanese.pk).exists())
        self.assertFalse(Listing.objects.filter(pk=pack.pk).exists())
        self.assertFalse(Listing.objects.filter(pk=single.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=slip.pk).exists())


class SeriesDecimalTests(TestCase):
    def test_a_point_release_series_number_is_part_of_the_series(self):
        self.assertEqual(
            match_key("Scarlet & Violet 8.5 Prismatic Evolutions Elite Trainer Box"),
            match_key("Prismatic Evolutions Elite Trainer Box"),
        )
        self.assertEqual(match_key("Scarlet & Violet 151 Booster Bundle"), match_key("151 Booster Bundle"))
        self.assertNotEqual(match_key("151 Booster Bundle"), match_key("Booster Bundle"))


class BoxContentsKeyTests(TestCase):
    def test_a_bracketed_pack_count_is_the_same_box_and_a_bare_multi_buy_keeps_its_pack_mark(self):
        from .matching import match_key

        self.assertEqual(match_key("Pokemon TCG: Scarlet & Violet - Destined Rivals Booster Box (36x Packs)"),
                         match_key("Destined Rivals Booster Box"))
        self.assertIn("#pack", match_key("Pokemon 3x Booster Packs"))
        # Extra packs sold with the box keep their count and mark, so they never share the plain box's key.
        self.assertNotEqual(match_key("Destined Rivals Booster Box (36x Packs) + 6x Booster Packs"),
                            match_key("Destined Rivals Booster Box"))


class ChineseDuplicateTests(TestCase):
    """Titles copied from the Chinese booster boxes listed on ripraptor.com on 13 Oct 2026, where one box
    had a page per shop."""

    def setUp(self):
        self.game = make_game()

    def box(self, name, shop):
        product = Product.objects.create(game=self.game, name=name, product_type="booster_box")
        make_listing(product, make_retailer(shop) if isinstance(shop, str) else shop)
        return product

    def test_one_code_however_a_shop_spells_it(self):
        for a, b in [
            ("Blade Awakening Simplified Chinese JUMBO Booster Box (csv7C)", "Blade Awakening csv7 C Simplified Chinese Jumbo Booster Box"),
            ("Terastal Grand Gathering (csv9.5C) Booster Box Chinese", "Terastal Grand Gathering Simplified Chinese Booster Box (csv9.5 C)"),
            ("Gem Pack Vol. 2 (cbb2C) Booster Box Chinese", "Gem Pack Vol. 2 CBB2 C Simplified Chinese Booster Box"),
            ("Simplified Chinese Pokemon TCG: Gem Pack Volume 4 Booster Box", "Chinese Gem Pack Vol 4 Booster Box"),
            ("Collect 151 Journey 151C Simplified Chinese Slim Booster Box", "Collect 151 Journey 151 C Simplified Chinese Slim Booster Box"),
        ]:
            with self.subTest(a=a):
                self.assertEqual(match_key(a), match_key(b))

    def test_codes_and_sizes_still_tell_boxes_apart(self):
        for a, b in [
            ("Storming Emergence csm1b C Simplified Chinese Jumbo Booster Box", "Storming Emergence csm1c C Simplified Chinese Jumbo Booster Box"),
            ("Blade Awakening csv7 C Simplified Chinese Slim Booster Box", "Blade Awakening csv7 C Simplified Chinese Jumbo Booster Box"),
            ("Gem Pack Vol. 2 CBB2 C Simplified Chinese Booster Box", "Gem Pack Vol. 3 CBB3 C Simplified Chinese Booster Box"),
            ("Collect 151 Journey 151 C Simplified Chinese Jumbo Booster Box", "Collect 151 Hope 151 C Simplified Chinese Jumbo Booster Box"),
        ]:
            with self.subTest(a=a):
                self.assertNotEqual(match_key(a), match_key(b))

    def test_chinese_said_with_a_simplified_code_is_simplified(self):
        from .languages import language_of

        self.assertEqual(language_of("Chinese Gem Pack Vol 4 Booster Box", "pokemon"), "zh-hans")
        self.assertEqual(language_of("Stellar Crystal (csv9C) Slim Booster Box Chinese", "pokemon"), "zh-hans")
        self.assertEqual(language_of("Chinese Terastal Gathering Booster Box", "pokemon"), "zh")
        self.assertEqual(language_of("(TRADITIONAL CHINESE) Pokemon 2023 Shiny Treasure Gift Box", "pokemon"), "zh-hant")

    def test_same_box_two_shops_merge_and_others_stay(self):
        from .management.commands.merge_duplicates import duplicate_groups

        japan2uk = make_retailer("Japan2UK")
        jumbo = self.box("Blade Awakening csv7 C Simplified Chinese Jumbo Booster Box", japan2uk)
        jumbo_too = self.box("Blade Awakening Simplified Chinese JUMBO Booster Box (csv7C)", "Best Booster Boxes")
        self.box("Blade Awakening csv7 C Simplified Chinese Slim Booster Box", japan2uk)
        self.box("Storming Emergence csm1b C Simplified Chinese Jumbo Booster Box", japan2uk)
        self.box("Storming Emergence csm1c C Simplified Chinese Jumbo Booster Box", japan2uk)
        gem = self.box("Gem Pack Vol. 2 (cbb2C) Booster Box Chinese", "The Poke Store")
        gem_too = self.box("Gem Pack Vol. 2 CBB2 C Simplified Chinese Booster Box", japan2uk)
        self.box("Prismatic Evolutions Booster Box", "Total Cards")
        groups = {keep.pk: {o.pk for o in others} for keep, others in duplicate_groups()}
        self.assertEqual(sorted(map(sorted, ({k, *o} for k, o in groups.items()))),
                         sorted([sorted([jumbo.pk, jumbo_too.pk]), sorted([gem.pk, gem_too.pk])]))

        out = StringIO()
        call_command("merge_duplicates", stdout=out)
        self.assertIn("2 products merged", out.getvalue())
        self.assertEqual(Product.objects.filter(name__startswith="Blade Awakening", is_active=True).count(), 2)

    def test_a_box_without_its_code_or_with_another_name_is_suggested_not_merged(self):
        from . import checks
        from .management.commands.merge_duplicates import duplicate_groups

        coded = self.box("Black Crystal Blaze csv5 C Simplified Chinese Slim Booster Box", "Japan2UK")
        bare = self.box("Black Crystal Blaze Slim Booster Box (Simplified Chinese)", "Eterna Cards")
        plain = self.box("Chinese Terastal Gathering Booster Box", "MaxOnCards")
        said = self.box("Terastal Gathering Booster Box (Simplified Chinese)", "Eterna Cards 2")
        chasing = self.box("Chasing Glory Together CSV10 C Slim Booster Box (Simplified Chinese)", "Eterna Cards 3")
        pursue = self.box("Pursue Glory Together csv10 C Simplified Chinese Slim Booster Box", "eBay")
        self.box("Pursue Glory Together csv10 C Simplified Chinese Jumbo Booster Box", "Japan2UK 2")
        self.assertEqual(duplicate_groups(), [])
        pairs = {frozenset([keep.pk, *(o.pk for o in others)]) for keep, others in checks.duplicates()}
        self.assertEqual(pairs, {frozenset([coded.pk, bare.pk]), frozenset([plain.pk, said.pk]),
                                 frozenset([chasing.pk, pursue.pk])})


class GemPackTests(TestCase):
    """Solent Cards' "Gem Pack Vol. 4 CBB4 C Simplified Chinese Booster Box" sat on its own page while five other
    shops' Gem 4 boxes sat on others (15 Oct 2026). A Gem Pack's code only says its volume again."""

    def setUp(self):
        self.game = make_game()

    def test_every_shops_name_for_one_gem_box_is_one_key(self):
        names = ["Gem Pack Vol. 4 CBB4 C Simplified Chinese Booster Box", "Chinese Gem Pack Vol 4 Booster Box",
                 "Simplified Chinese Pokemon TCG: Gem Pack Volume 4 Booster Box", "Gem 4 Simplified Chinese Booster Box",
                 "Chinese TCG: Gem Series 4 Chinese Booster Box", "Gem Pack Vol. 4 Booster Box"]
        self.assertEqual(len({match_key(name, "pokemon") for name in names}), 1)
        for name in names:
            self.assertEqual(language_of(name, "pokemon"), "zh-hans", name)

    def test_other_volumes_kinds_and_codes_stay_apart(self):
        box = match_key("Gem Pack Vol. 4 CBB4 C Simplified Chinese Booster Box", "pokemon")
        self.assertNotEqual(box, match_key("Gem Pack Vol. 6 CBB6 C Simplified Chinese Booster Box", "pokemon"))
        self.assertNotEqual(box, match_key("Gem Pack Vol. 4 CBB4 C Simplified Chinese Booster Pack", "pokemon"))
        # A code that is not the volume's is kept, so the names never meet.
        self.assertNotEqual(box, match_key("Gem Pack Vol. 4 CBB5 C Simplified Chinese Booster Box", "pokemon"))

    def test_the_hourly_merge_joins_them(self):
        for name, shop in [("Gem Pack Vol. 4 CBB4 C Simplified Chinese Booster Box", "Solent Cards"),
                           ("Gem 4 Simplified Chinese Booster Box", "Lvl Up"),
                           ("Chinese TCG: Gem Series 4 Chinese Booster Box", "Iconic")]:
            make_listing(Product.objects.create(game=self.game, name=name, product_type="booster_box"),
                         make_retailer(shop))
        call_command("set_languages", stdout=StringIO())
        out = StringIO()
        call_command("merge_duplicates", stdout=out)
        self.assertIn("2 products merged", out.getvalue())
        self.assertEqual(Listing.objects.filter(product__is_active=True).values("product").distinct().count(), 1)


class HourlyMergeGuardTests(TestCase):
    """The hourly merge (tidy_all runs merge_duplicates) merged Gem Pack Vol 4 and Vol 6 packs and 5 and 10 pack
    bundles, and deleted one shop's listing each time. It now merges only what is certain."""

    def setUp(self):
        self.game = make_game()

    def product(self, name, shop, product_type="booster_pack"):
        product = Product.objects.create(game=self.game, name=name, product_type=product_type)
        make_listing(product, make_retailer(shop) if isinstance(shop, str) else shop)
        return product

    def merge(self):
        out = StringIO()
        call_command("merge_duplicates", stdout=out)
        return out.getvalue()

    def test_a_volume_number_is_never_taken_for_a_pack_count(self):
        self.assertNotEqual(match_key("Gem Pack Vol 4 Booster Pack"), match_key("Gem Pack Vol 6 Booster Pack"))
        self.assertNotEqual(match_key("Gem Pack Volume 4 Booster Pack"), match_key("Gem Pack Volume 6 Booster Pack"))
        # A count after the box is still what the box holds.
        self.assertEqual(match_key("Surging Sparks Booster Box 36 Packs"), match_key("Surging Sparks Booster Box"))

    def test_different_numbers_are_left_apart(self):
        five = self.product("Mystery Bundle 5 Sealed Booster Packs", "Solent Cards", "bundle")
        ten = self.product("Mystery Bundle 10 Sealed Booster Packs", "Japan2UK", "bundle")
        out = self.merge()
        self.assertIn("their numbers differ", out)
        self.assertEqual(Product.objects.filter(pk__in=[five.pk, ten.pk]).count(), 2)

    def test_a_set_booster_box_is_not_a_draft_booster_box(self):
        self.product("Throne of Eldraine Set Booster Box", "Total Cards", "booster_box")
        self.product("Throne of Eldraine Booster Box", "Magic Madhouse", "booster_box")
        self.assertIn("their booster kinds differ", self.merge())
        self.assertEqual(Product.objects.count(), 2)

    def test_a_pair_the_owner_kept_apart_is_never_merged(self):
        from .models import CheckAnswer

        box = self.product("Surging Sparks Booster Box (36 Packs)", "Total Cards", "booster_box")
        other = self.product("Surging Sparks Booster Box", "Magic Madhouse", "booster_box")
        answer = CheckAnswer.objects.create(kind=CheckAnswer.Kind.APART, by_owner=True, what="Not the same",
                                            product=box, other=other)
        self.assertIn("you said they are not the same", self.merge())
        self.assertEqual(Product.objects.count(), 2)
        # Once his answer is undone, they merge as before.
        CheckAnswer.objects.filter(pk=answer.pk).update(undone_at=answer.created_at)
        self.assertIn("1 products merged", self.merge())

    def test_the_same_box_written_two_ways_still_merges(self):
        self.product("Surging Sparks Booster Box (36 Packs)", "Total Cards", "booster_box")
        self.product("Surging Sparks Booster Box", "Magic Madhouse", "booster_box")
        self.assertIn("1 products merged", self.merge())


# Pairs of real shop titles for one product, from the duplicate pages on the 3 Oct 2026 snapshot and Solent
# Cards' read of 15 Oct 2026: (game, one shop's name, another's).
SAME_PRODUCT = [
    ("pokemon", "Surging Sparks Booster Box", "Surging Sparks Booster Display"),
    ("pokemon", "Paldea Evolved Booster Box", "Paldea Evolved Booster Display Box"),
    ("pokemon", "Paldea Evolved Booster Box", "Paldea Evolved Booster Box (36)"),
    ("pokemon", "Mega Evolution Pitch Black Booster Box", "Mega Evolution Pitch Black Boosters Display Box"),
    ("pokemon", "Journey Together Booster Bundle", "Journey Together Bundle"),
    ("pokemon", "Paldean Fates Mini Tin", "Paldean Fates Mini Tins"),
    ("pokemon", "Destined Rivals Booster Box", "SV10 Destined Rivals Booster Box"),
    ("pokemon", "SV10 Booster Box", "Pokemon Scarlet & Violet 10 Booster Box"),
    ("pokemon", "Phantasmal Flames Elite Trainer Box", "Mega Evolution Phantasmal Flames Elite Trainer Box"),
    ("pokemon", "Phantasmal Flames Elite Trainer Box", "ME02 Phantasmal Flames Elite Trainer Box"),
    ("pokemon", "Mega Evolution: Ascended Heroes Elite Trainer Box", "Ascended Heroes ME2.5 Elite Trainer Box (ETB)"),
    ("pokemon", "Mega Evolution Pitch Black Booster Pack", "Pitch Black ME05 Booster Pack"),
    ("pokemon", "Pokemon Center Elite Trainer Box Destined Rivals", "Destined Rivals PC ETB"),
    ("pokemon", "Japanese Mega Brave Booster Box", "M1L Mega Brave Japanese Booster Box"),
    ("pokemon", "Japanese Pokemon Mega Dream Booster Box",
     "Mega Dream EX Japanese Booster Box M2A High Class Packs (10 Booster Packs)"),
    ("pokemon", "Mega Evolution Mega Dream (M2a) Booster Box (Japanese)", "Mega Dream EX Japanese Pokémon M2a Booster Box"),
    ("pokemon", "Shiny Treasure ex SV4A High Class Booster Box (Japanese)",
     "Scarlet & Violet Shiny Treasure (SV4a) Booster Box (Japanese)"),
    ("pokemon", "(JAPANESE) Pokemon SV8a High Class Terastal Festival ex Booster Box", "JAPANESE Terastal Festival Booster Box"),
    ("pokemon", "Terastal Festival SV8A Booster Box 10 Japanese Booster Packs Japanese High Class Packs",
     "JAPANESE Terastal Festival Booster Box"),
    ("pokemon", "Japanese Ninja Spinner Booster Box", "Pokemon Ninja Spinner (m4) Japanese Booster Box"),
    ("pokemon", "Mega Evolution Storm Emeralda Japanese Booster Box", "Pokemon Storm Emeralda m6 Japanese Booster Box"),
    ("pokemon", "JAPANESE Mega Dream Ex Booster Box No Plastic Wrap", "Mega Dream ex High Class m2a No Shrink Japanese Booster Box"),
    ("pokemon", "Sword & Shield Dark Phantasma Japanese Booster Box", "Japanese Dark Phantasma Booster Box S10a"),
    ("pokemon", "2026 Pokémon Mid Autumn Festival Gift Box", "Simplified Chinese 2026 Mid Autumn Festival Gift Box"),
    ("pokemon", "Surging Sparks 3-Pack Blister Pikachu", "Surging Sparks Three Pack Blister Pikachu"),
    ("magic-the-gathering", "Foundations Play Booster Box", "Foundations Play Booster Display"),
    ("magic-the-gathering", "Tarkir Dragonstorm Play Booster Box", "Tarkir: Dragonstorm Play Booster Display (30 Packs)"),
    ("magic-the-gathering", "Murders at Karlov Manor Revenant Recon Commander Deck",
     "Murders at Karlov Manor Commander Decks (Revenant Recon)"),
    ("magic-the-gathering", "Duskmourn House of Horrors Play Booster Box", "Duskmourn: House of Horror Play Booster Box"),
    ("magic-the-gathering", "Adventures In The Forgotten Realms Collector Booster Box",
     "Dungeons & Dragons: Adventures in the Forgotten Realms Collector Booster Box"),
    ("magic-the-gathering", "Edge of Eternities Play Booster Box", "Magic: the Gathering: Edge of Eternities Play Booster Display"),
    ("one-piece", "OP09 Booster Box", "OP-09 Booster Box"),
    ("one-piece", "Emperors in the New World Booster Box (OP-09)", "One Piece Card Game Emperors In The New World Booster Display"),
    ("lorcana", "Archazia's Island Booster Box", "Archazias Island Booster Display"),
    ("star-wars-unlimited", "Legends of the Force Booster Box", "Star Wars Unlimited Legends of the Force Booster Display"),
    ("star-wars-unlimited", "Twilight of the Republic Two Player Starter", "Twilight of the Republic 2 Player Starter"),
    ("yu-gi-oh", "Rarity Collection 2 Booster Box", "Rarity Collection II Booster Box"),
    ("dragon-ball", "Fusion World FB05 New Adventure Booster Box", "CG: New Adventure Booster Box"),
    ("dragon-ball", "Starter Deck Fusion World Son Goku (Mini) FS06", "CG: Fusion World Son Goku Mini Starter Deck"),
    ("digimon", "BT08 New Awakening Booster Box", "New Awakening Booster Display Box BT08"),
    ("riftbound", "Origins Booster Box", "Riftbound Origins Booster Display"),
    ("riftbound", "Origins Booster Box", "Riftbound: League of Legends TCG Origins Booster Box"),
    ("flesh-and-blood", "Rosetta Booster Box", "Flesh and Blood Rosetta Booster Display"),
]
# Pairs that must stay apart however close their words: another set, volume, size, kind, language, booster,
# series or wrapping.
DIFFERENT_PRODUCTS = [
    ("pokemon", "Gem Pack Vol 4 Booster Pack", "Gem Pack Vol 6 Booster Pack"),
    ("pokemon", "Storming Emergence csm1b C Simplified Chinese Jumbo Booster Box",
     "Storming Emergence csm1c C Simplified Chinese Jumbo Booster Box"),
    ("pokemon", "Blade Awakening csv7 C Simplified Chinese Slim Booster Box", "Blade Awakening csv7 C Simplified Chinese Jumbo Booster Box"),
    ("pokemon", "Scarlet & Violet Elite Trainer Box", "Sword & Shield Elite Trainer Box"),
    ("pokemon", "Scarlet & Violet Base Set Booster Box", "Mega Evolution Base Set Booster Box"),
    ("pokemon", "Mega Lucario ex Premium Collection", "Lucario ex Premium Collection"),
    ("pokemon", "Charizard ex Premium Collection", "Charizard Premium Collection"),
    ("pokemon", "Japanese Booster Box M1L", "Japanese Booster Box M1S"),
    ("pokemon", "Mega Brave M1L Booster Box", "Mega Symphonia M1S Booster Box"),
    ("pokemon", "Surging Sparks Booster Box", "Surging Sparks Booster Pack"),
    ("pokemon", "Surging Sparks Booster Box", "Surging Sparks Booster Box Case of 6"),
    ("pokemon", "Scarlet & Violet Journey Together Booster Box", "Scarlet & Violet: Journey Together Booster Box (18)"),
    ("pokemon", "Lumiose City Mini Tin Display (10 Tins)", "Lumiose City Mini Tin"),
    ("pokemon", "151 Booster Bundle Display (10 Bundles)", "151 Booster Bundle"),
    ("pokemon", "Ninja Spinner m4 No Shrink Japanese Booster Box", "Ninja Spinner m4 Japanese Booster Box"),
    ("pokemon", "Surging Sparks 3 Pack Bundle", "Surging Sparks Booster Bundle"),
    ("pokemon", "Scarlet & Violet Prismatic Evolutions Booster Bundle", "Scarlet & Violet—Prismatic Evolutions 2 Pack Bundle"),
    ("one-piece", "Premium Booster PRB-01 Booster Box", "Premium Booster PRB-02 Booster Box"),
    ("one-piece", "The Best PRB 01 Japanese Booster Pack", "The Best PRB 02 Japanese Booster Pack"),
    ("one-piece", "OP-09 Booster Box", "OP-10 Booster Box"),
    ("one-piece", "Extra Booster EB-01 Booster Box", "Extra Booster EB-02 Booster Box"),
    ("dragon-ball", "Starter Deck EX Fusion World FS15", "CG: Fusion World Starter Deck EX"),
    ("magic-the-gathering", "M20 Booster Box", "M21 Booster Box"),
    ("magic-the-gathering", "Core Set 2020 M20 Booster Box", "Core Set 2021 M21 Booster Box"),
    ("magic-the-gathering", "Throne of Eldraine Set Booster Box", "Throne of Eldraine Draft Booster Box"),
    # The hourly tidy takes a "...-collectors-edition-..." address off a name without it, so a person decides.
    ("magic-the-gathering", "Marvel Super Heroes Wakanda Forever Collector Commander Deck",
     "Marvel Super Heroes Collector's Edition Commander Deck Wakanda Forever"),
    ("yu-gi-oh", "Rarity Collection 5 Booster Pack", "Rarity Collection II Booster Pack"),
    ("yu-gi-oh", "Base Set Unlimited Booster Box", "Base Set 1st Edition Booster Box"),
]


class ShopNamingTests(TestCase):
    """One product, many shop names: "Booster Display" for a booster box, plurals, the game's name in words,
    language words, Pokémon's "Booster Bundle", "High Class" and "ex", set codes in any spelling. Each rule
    keeps different products apart."""

    def same(self, game, a, b):
        from .management.commands.merge_duplicates import merge_key
        from .matching import same_product

        key = merge_key(a, game)
        return bool(key) and key == merge_key(b, game) and same_product(a, b, game)

    def test_one_product_under_two_shops_names_shares_a_key(self):
        for game, a, b in SAME_PRODUCT:
            with self.subTest(a=a, b=b):
                self.assertTrue(self.same(game, a, b))

    def test_different_products_stay_apart(self):
        for game, a, b in DIFFERENT_PRODUCTS:
            with self.subTest(a=a, b=b):
                self.assertFalse(self.same(game, a, b))

    def test_a_display_of_tins_bundles_or_decks_is_not_a_booster_box(self):
        for name in ("Lumiose City Mini Tin Display (10 Tins)", "Prismatic Evolutions Booster Bundle Display (10)",
                     "Murders at Karlov Manor Commander Deck Display Blame Game", "Surging Sparks Elite Trainer Box Display"):
            with self.subTest(name=name):
                self.assertNotEqual(match_key(name, "pokemon"),
                                    match_key(name.replace("Display", "Booster Box"), "pokemon"))

    def test_a_name_of_only_a_code_keeps_it(self):
        self.assertEqual(match_key("OP-09 Booster Box", "one-piece"), "booster op9 #box")
        self.assertEqual(match_key("Pokemon SV10 Booster Box", "pokemon"), "booster sv10 #box")
        # A bare series names nothing more, so the two series never meet.
        self.assertNotEqual(match_key("Scarlet & Violet Booster Box", "pokemon"), match_key("Sword & Shield Booster Box", "pokemon"))

    def test_magic_core_set_codes_are_not_pokemon_codes(self):
        self.assertNotEqual(match_key("Core Set M20 Booster Box", "magic-the-gathering"),
                            match_key("Core Set M21 Booster Box", "magic-the-gathering"))

    def test_the_hourly_merge_joins_them_and_leaves_the_rest(self):
        game = make_game()
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")

        def product(game, name, shop):
            item = Product.objects.create(game=game, name=name, product_type="booster_box")
            make_listing(item, make_retailer(shop))

        product(game, "Surging Sparks Booster Box", "Total Cards")
        product(game, "Surging Sparks Booster Display", "Card Vault")
        product(game, "Scarlet & Violet Base Set Booster Box", "Zatu")
        product(game, "Mega Evolution Base Set Booster Box", "Magic Madhouse")
        product(magic, "Final Fantasy Collector Booster Box", "Total Cards 2")
        product(magic, "Magic: the Gathering: Final Fantasy Collector's Booster Display", "Card Vault 2")
        product(magic, "Final Fantasy Play Booster Box", "Zatu 2")
        product(magic, "Doctor Who Masters of Evil Collector Commander Deck", "Lvl Up")
        product(magic, "Doctor Who Collector's Edition Commander Deck Masters of Evil", "Jet")
        out = StringIO()
        call_command("merge_duplicates", stdout=out)
        self.assertIn("2 products merged", out.getvalue())
        self.assertIn("one says Collector's Edition, Case or the like and the other does not", out.getvalue())
        self.assertEqual(sorted(Product.objects.filter(is_active=True).values_list("name", flat=True)), [
            "Doctor Who Collector's Edition Commander Deck Masters of Evil", "Doctor Who Masters of Evil Collector Commander Deck",
            "Final Fantasy Collector Booster Box", "Final Fantasy Play Booster Box", "Mega Evolution Base Set Booster Box",
            "Scarlet & Violet Base Set Booster Box", "Surging Sparks Booster Box"])

    def test_the_kept_page_says_its_language(self):
        from .management.commands.merge_duplicates import duplicate_groups

        game = make_game()
        for name, shop in [("Gem Pack Vol. 4 Booster Box", "Sports Cards Direct"), ("Chinese Gem Pack Vol 4 Booster Box", "Jet"),
                           ("Gem 4 Simplified Chinese Booster Box", "Lvl Up")]:
            make_listing(Product.objects.create(game=game, name=name, product_type="booster_box"), make_retailer(shop))
        [(keep, others)] = duplicate_groups()
        self.assertEqual(keep.name, "Gem 4 Simplified Chinese Booster Box")
        self.assertEqual(len(others), 2)


class KeyLinkTests(TestCase):
    """A shop title whose key equals a product's links at once (importers.Catalogue.best_match), so the key
    links only what is surely the product."""

    def catalogue(self, *names, game="pokemon"):
        from .importers import Catalogue

        return Catalogue([(pk, name, game) for pk, name in enumerate(names, start=1)])

    def test_a_booster_display_title_links_to_the_booster_box(self):
        from .matching import AUTO_LINK

        match, value = self.catalogue("Foundations Play Booster Box", game="magic-the-gathering").best_match(
            "Magic: The Gathering Foundations Play Booster Display (36 Packs)", game="magic-the-gathering")
        self.assertEqual((match[0], value), (1, AUTO_LINK))
        match, value = self.catalogue("Journey Together Booster Bundle").best_match("Pokemon TCG: Journey Together Bundle",
                                                                                    game="pokemon")
        self.assertEqual((match[0], value), (1, AUTO_LINK))

    def test_numbers_codes_and_bare_kinds_are_never_linked_by_key(self):
        from .matching import AUTO_LINK

        for names, title, game in [
            (("Surging Sparks Booster Bundle",), "Pokemon Surging Sparks 3 Pack Bundle", "pokemon"),
            (("OP-09 Booster Box",), "One Piece OP-10 Booster Box", "one-piece"),
            (("Gem Pack Vol 4 Booster Box (Simplified Chinese)",), "Pokemon Gem Pack Vol 6 Booster Box Simplified Chinese",
             "pokemon"),
            (("Sword & Shield Booster Box",), "Pokemon Mega Evolution Booster Box", "pokemon"),
        ]:
            with self.subTest(title=title):
                match, value = self.catalogue(*names, game=game).best_match(title, game=game)
                self.assertLess(value, AUTO_LINK, match)
