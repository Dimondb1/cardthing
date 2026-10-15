from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .importers import Offer, apply_offers
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
        self.assertEqual(Product.objects.filter(name__startswith="Blade Awakening").count(), 2)

    def test_a_box_without_its_code_or_with_another_name_is_suggested_not_merged(self):
        from . import checks
        from .management.commands.merge_duplicates import duplicate_groups

        coded = self.box("Gem Pack Vol. 6 CBB6 C Simplified Chinese Booster Box", "Japan2UK")
        bare = self.box("Gem Pack Vol 6 Booster Box (Simplified Chinese)", "Eterna Cards")
        plain = self.box("Chinese Terastal Gathering Booster Box", "MaxOnCards")
        said = self.box("Terastal Gathering Booster Box (Simplified Chinese)", "Eterna Cards 2")
        chasing = self.box("Chasing Glory Together CSV10 C Slim Booster Box (Simplified Chinese)", "Eterna Cards 3")
        pursue = self.box("Pursue Glory Together csv10 C Simplified Chinese Slim Booster Box", "eBay")
        self.box("Pursue Glory Together csv10 C Simplified Chinese Jumbo Booster Box", "Japan2UK 2")
        self.assertEqual(duplicate_groups(), [])
        pairs = {frozenset([keep.pk, *(o.pk for o in others)]) for keep, others in checks.duplicates()}
        self.assertEqual(pairs, {frozenset([coded.pk, bare.pk]), frozenset([plain.pk, said.pk]),
                                 frozenset([chasing.pk, pursue.pk])})


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

    def test_the_same_box_written_two_ways_still_merges(self):
        self.product("Surging Sparks Booster Box (36 Packs)", "Total Cards", "booster_box")
        self.product("Surging Sparks Booster Box", "Magic Madhouse", "booster_box")
        self.assertIn("1 products merged", self.merge())
