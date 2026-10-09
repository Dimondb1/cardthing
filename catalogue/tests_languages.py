"""The language of a product or a shop's title is read in one place (catalogue/languages.py): words,
sellers' codes, scripts, and the set codes and names only another language has. English products never
read as foreign, and a product in one language is never matched, merged or filed with another's."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from . import autopilot, languages
from .classify import classify
from .importers import Catalogue
from .matching import SUGGEST
from .models import Product
from .ordering import apply_languages
from .testing import make_game, make_listing, make_product, make_retailer, make_set

POKEMON = "pokemon"


class ReadingTests(TestCase):
    def read(self, text, game=POKEMON, loose=False):
        return languages.language_of(text, game, loose=loose)

    def test_set_codes_only_another_language_has_are_read(self):
        for text in ("151 (sv2a) Booster Box (Sealed/Shrink)", "Cyber Judge SV5M Booster Box (30 Booster Packs)",
                     "Future Flash SV4M Booster Box", "Eevee Heroes S6A Booster Pack", "VSTAR Universe s12a Booster Box",
                     "Mega Evolution Mega Dream (M2a) Booster Pack", "Tag All Stars SM12a Booster Box", "VMAX Climax s8b",
                     "Black Bolt (sv11B) Booster Box", "Mega Brave M1L Booster Box"):
            self.assertEqual(self.read(text), "ja", text)

    def test_japanese_only_kinds_and_set_names_are_read(self):
        for text in ("Terastal Festival ex Booster Box", "Shiny Treasure ex High Class Pack", "Ruler of the Black Flame Booster Pack",
                     "Lost Abyss S11 Booster Box", "Stellar Miracle Deck Build Box", "Mega Dream EX Booster Box"):
            self.assertEqual(self.read(text), "ja", text)

    def test_english_numbering_names_and_words_stay_english(self):
        cases = [
            ("Scarlet & Violet 151 SV3.5 Booster Bundle", POKEMON), ("SV08 Surging Sparks Booster Box", POKEMON),
            ("Black Bolt Booster Box", POKEMON), ("White Flare Elite Trainer Box", POKEMON),
            ("Paldean Fates SV4.5 Elite Trainer Box", POKEMON), ("Prismatic Evolutions SV8.5 Booster Bundle", POKEMON),
            ("Crown Zenith SWSH12.5 Elite Trainer Box", POKEMON), ("Sun & Moon SM6 Forbidden Light Booster Box", POKEMON),
            ("Scarlet & Violet ex Booster Pack", POKEMON), ("Surging Sparks SV8 Booster Box", POKEMON),
            ("Magic 2015 M15 Booster Box", "magic-the-gathering"), ("Zendikar Intro Pack Kor Armory", "magic-the-gathering"),
            ("Shimmering Skies Starter Deck Elsa & Wreck It Ralph", "lorcana"),
            ("The First Chapter Starter Deck Cruella De Vil and Aladdin", "lorcana"),
            ("Panini Luminance DFB Germany 2026 Hobby Box", "football"), ("Asian English OCG Booster Box", "yu-gi-oh"),
            ("2025 World Championship Deck Yuya Okita (JP Raging Bolt)", POKEMON), ("OP-09 Booster Box", "one-piece"),
            ("Pokemon Center Japan Pikachu Plush", POKEMON),
        ]
        for text, game in cases:
            self.assertEqual(self.read(text, game), "", text)

    def test_a_stated_language_beats_a_code_and_a_letter_after_the_code_names_one(self):
        cases = [
            ("151 SV2A Korean Booster Pack", "ko"), ("Terastal Festival SV8A Korean Booster Box", "ko"),
            ("s7D F Traditional Chinese Booster Box", "zh-hant"), ("Mega Dream m2a F Booster Box", "zh-hant"),
            ("Terastal Festival SV8A-T Booster Box", "th"), ("Black Bolt (sv11B) Booster Box English", ""),
            ("Terastal Festival Japanese Booster Box", "ja"),
        ]
        for text, code in cases:
            self.assertEqual(self.read(text), code, text)

    def test_chinese_and_one_piece_codes(self):
        for text in ("Gem Pack Vol. 2 Booster Box", "CSV9C Booster Box", "Pokemon CBB3 C Gem Pack Vol 3"):
            self.assertEqual(self.read(text), "zh-hans", text)
        self.assertEqual(self.read("One Piece OPC-01 Booster Box", "one-piece"), "zh-hans")
        self.assertEqual(self.read("One Piece OPC 15 Booster Box", "one-piece"), "zh-hans")
        self.assertEqual(self.read("O-Pee-Chee OPC-23 Hockey Hobby Box", "football"), "")
        self.assertEqual(self.read("One Piece OPK-05 Booster Box", "one-piece"), "ko")

    def test_sellers_codes_count_only_where_they_cannot_be_words(self):
        for text, code in (("Booster Box [JP]", "ja"), ("Booster Box (DE)", "de"), ("Booster Box - KR", "ko"),
                           ("151 JP Version Booster Box", "ja"), ("151 JPN Booster Box", "ja"), ("Booster Box (CHS)", "zh-hans")):
            self.assertEqual(self.read(text), code, text)
        self.assertEqual(self.read("151 JP Booster Box"), "")
        self.assertEqual(self.read("Booster Box (SC)"), "")
        self.assertEqual(self.read("Snow White Make It Edition Deck", "lorcana"), "")
        self.assertEqual(self.read("151 JP Booster Box", loose=True), "ja")
        self.assertEqual(self.read("CHS Pokémon 30th Celebration Booster Pack", loose=True), "zh-hans")
        self.assertEqual(self.read("Pokemon 151 Booster Pack KOR", loose=True), "ko")
        self.assertEqual(self.read("ZENDIKAR KOR ARMORY INTRO PACK", "magic-the-gathering", loose=True), "")
        self.assertEqual(self.read("Pokemon 151 Booster Pack SC Sealed", loose=True), "")

    def test_scripts_and_native_words(self):
        for text, code in (("ポケモンカード 151", "ja"), ("포켓몬 카드 151", "ko"), ("โปเกมอน 151", "th"), ("宝可梦 151", "zh-hans"), ("寶可夢 151", "zh-hant"), ("中文 151", "zh"),
                           ("Pokemon Karmesin & Purpur Top-Trainer-Box", "de"), ("Coffret Dresseur d'Élite Pokemon", "fr"),
                           ("Display Pokémon (Français)", "fr"), ("Booster Box Japonais", "ja")):
            self.assertEqual(self.read(text), code, text)


class CheckedRulesTests(TestCase):
    """The cases the fact-check of the rules named, each read the way it should be."""

    def read(self, text, game=POKEMON, loose=False):
        return languages.language_of(text, game, loose=loose)

    def test_words_that_name_a_language_without_being_one(self):
        for text, game in (("Pokemon 151 Booster Box - ENGLISH (NOT Japanese)", POKEMON),
                           ("Portal Second Age: 2 Player Starter Set (Rulebook in Dutch)", "magic-the-gathering"),
                           ("Panini Chinese New Year Basketball Box", "football"), ("Booster Box Made in Japan", POKEMON),
                           ("Pokemon 151 ENG Booster Bundle sv2a", POKEMON), ("FIFA World Cup JPN KOR GER Hobby Box", "football"),
                           ("Asian English Booster Box", "yu-gi-oh")):
            self.assertEqual(self.read(text, game), "", text)

    def test_codes_and_names_count_only_inside_their_game(self):
        for text, game in (("Airsoft M1A Rifle Booster Box", "magic-the-gathering"), ("Power Rangers Wild Force Deck", "lorcana"),
                           ("Counter Strike CS2 C Case", "football"), ("Tarkir: Dragonstorm Play Booster Box", "magic-the-gathering"),
                           ("Pokemon Mega Charizard X ex Inferno X Premium Collection", POKEMON), ("Terastal Festival Booster Box", "")):
            self.assertEqual(self.read(text, game), "", text)
        self.assertEqual(self.read("Inferno X (M2) Booster Box"), "ja")

    def test_language_letters_after_codes(self):
        cases = [("SV9F Booster Box", "zh-hant"), ("S11A-T Booster Box", "th"), ("MA2T Booster Box", "th"),
                 ("Ties of Fate sv9s I Booster Box", "id"), ("MA4I Booster Box", "id"), ("SV9 F Booster Box", ""),
                 ("m6 F Booster Box", ""), ("s5I Booster Box", "ja"), ("sv10a Booster Box", "")]
        for text, code in cases:
            self.assertEqual(self.read(text), code, text)

    def test_names_kinds_and_codes_the_first_rules_missed(self):
        for text in ("Ninja Spinner Booster Box", "Abyss Eye Booster Box", "Storm Emeralda Booster Box",
                     "Jet-Black Spirit Booster Box", "Terastal Fest ex Booster Box", "Tag All Stars Booster Box",
                     "Premium Trainer Box ex", "Battle Master Deck Terapagos", "Booster Box japanische Version", "Japense Booster Box"):
            self.assertEqual(self.read(text), "ja", text)
        for text in ("CSV8 Brilliant Fantasy Slim Booster Box", "151C Hope Booster Box"):
            self.assertEqual(self.read(text), "zh-hans", text)

    def test_a_mixed_bundle_is_mixed_and_never_acted_on(self):
        title = "Destined Rivals / Glory of Team Rocket Mega Bundle"
        self.assertTrue(languages.mixed(title, POKEMON))
        self.assertTrue(languages.mixed("Booster Box Japanese and Korean", POKEMON))
        self.assertFalse(languages.mixed("Terastal Festival Booster Box", POKEMON))
        self.assertFalse(languages.mixed("Center Japan Mega Brave / Mega Symphonia Card Display Frame", POKEMON))
        product = make_product(make_set(make_game()), name="Destined Rivals Booster Bundle", product_type="booster_bundle")
        self.assertEqual(autopilot.language_differs(product, title), "")

    def test_a_plain_title_only_suggests_our_product_in_another_language(self):
        # The name key drops set codes, so "151 (sv2a)" and a plain "151" title share it.
        catalogue = Catalogue([(1, "151 (sv2a) Booster Box", POKEMON)])
        self.assertEqual(catalogue.best_match("Pokemon 151 Booster Box", POKEMON), ((1, "151 (sv2a) Booster Box"), SUGGEST))
        self.assertEqual(catalogue.best_match("Pokemon 151 sv2a Booster Box", POKEMON)[1], 100)
        catalogue = Catalogue([(1, "151 Booster Box (Japanese)", POKEMON)])
        self.assertLess(catalogue.best_match("Pokemon 151 Booster Box", POKEMON)[1], 100)
        catalogue = Catalogue([(1, "151 Booster Box (Japanese)", POKEMON), (2, "151 Booster Box", POKEMON)])
        self.assertEqual(catalogue.best_match("Pokemon 151 Booster Box", POKEMON), ((2, "151 Booster Box"), 100))
        self.assertEqual(catalogue.best_match("Pokemon 151 Booster Box Japanese", POKEMON)[0][0], 1)

    def test_the_refresh_changes_nothing_the_second_time(self):
        make_product(make_set(make_game()), name="151 (sv2a) Booster Box", product_type="booster_box")
        Product.objects.update(language="")
        self.assertEqual(languages.refresh_languages(), 1)
        self.assertEqual(languages.refresh_languages(), 0)


class ProductTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.set = make_set(self.game, name="Black Bolt", slug="black-bolt", code="SV10.5")

    def test_a_product_keeps_its_language_and_the_filter_uses_it(self):
        jp = make_product(self.set, name="151 (sv2a) Booster Box (Sealed/Shrink)", product_type="booster_box")
        en = make_product(self.set, name="151 Booster Bundle", product_type="booster_bundle")
        hans = make_product(self.set, name="Gem Pack Vol. 2 Booster Box", product_type="booster_box")
        hant = make_product(self.set, name="s7D Traditional Chinese Booster Box", product_type="booster_box")
        de = make_product(self.set, name="Top-Trainer-Box Karmesin", product_type="elite_trainer_box")
        self.assertEqual((jp.language, en.language, hans.language, hant.language, de.language), ("ja", "", "zh-hans", "zh-hant", "de"))
        every = Product.objects.all()
        self.assertEqual(set(apply_languages(every, ["ja"])), {jp})
        self.assertEqual(set(apply_languages(every, ["en"])), {en})
        self.assertEqual(set(apply_languages(every, ["zh"])), {hans, hant})
        self.assertEqual(set(apply_languages(every, ["other"])), {de})
        self.assertEqual(jp.language_name, "Japanese")

    def test_a_japanese_set_makes_its_products_japanese(self):
        japanese = make_set(self.game, name="Terastal Festival", slug="terastal-festival", code="sv8a")
        product = make_product(japanese, name="Booster Box", product_type="booster_box")
        self.assertEqual(product.language, "ja")

    def test_old_products_are_set_by_the_refresh_and_found_by_language(self):
        jp = make_product(self.set, name="151 (sv2a) Booster Box", product_type="booster_box")
        Product.objects.filter(pk=jp.pk).update(language="", search_text=" 151 sv2a booster box ")
        self.assertEqual(languages.refresh_languages(dry_run=True), 1)
        self.assertEqual(languages.refresh_languages(), 1)
        jp.refresh_from_db()
        self.assertEqual(jp.language, "ja")
        self.assertIn(" japanese ", jp.search_text)
        self.assertEqual(languages.refresh_languages(), 0)

    def test_the_product_page_says_the_language(self):
        jp = make_product(self.set, name="151 (sv2a) Booster Box (Sealed/Shrink)", product_type="booster_box")
        page = self.client.get(jp.get_absolute_url())
        self.assertContains(page, "Booster box · Japanese")
        self.assertContains(page, "<dd>Japanese</dd>", html=True)
        en = make_product(self.set, name="Black Bolt Booster Box", product_type="booster_box")
        self.assertContains(self.client.get(en.get_absolute_url()), "<dd>English</dd>", html=True)
        make_listing(jp, make_retailer("Japan Shop", delivery_cost=Decimal("0")), price="120.00")
        japanese_only = self.client.get(reverse("web:search") + "?q=151&lang=ja")
        self.assertContains(japanese_only, jp.name)
        self.assertNotContains(self.client.get(reverse("web:search") + "?q=151&lang=en"), jp.name)


class MatchingTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.set = make_set(self.game, name="Black Bolt", slug="black-bolt", code="SV10.5")

    def test_new_products_say_their_language_in_words(self):
        for title in ("Pokemon 151 (sv2a) Booster Box", "Pokemon 151 Booster Box [JP]"):
            sealed = classify(title, vendor="Pokemon")
            self.assertTrue(sealed.name.endswith("(Japanese)"), sealed.name)
        sealed = classify("Pokemon 151 JP Version Booster Box", vendor="Pokemon")
        self.assertEqual(languages.stated(sealed.name, POKEMON), "ja")
        # A bare JP in a shop's title may be a player's country, as on this English deck.
        sealed = classify("Pokemon 2025 World Championship Deck Yuya Okita (JP Raging Bolt)", vendor="Pokemon")
        self.assertNotIn("Japanese", sealed.name)
        self.assertEqual(classify("Pokemon 151 Booster Box Japanese", vendor="Pokemon").name.count("Japanese"), 1)
        self.assertNotIn("(", classify("Pokemon Black Bolt Booster Box", vendor="Pokemon").name)

    def test_a_title_in_another_language_never_matches_by_name(self):
        catalogue = Catalogue([(1, "Black Bolt Booster Box", POKEMON), (2, "151 Booster Bundle", POKEMON)])
        for title in ("Pokemon Black Bolt (sv11B) Booster Box", "Pokemon Black Bolt Booster Box Japanese",
                      "Pokemon 151 Booster Bundle Korean"):
            _, value = catalogue.best_match(title, POKEMON)
            self.assertLess(value, SUGGEST, title)
        match, value = catalogue.best_match("Pokemon Black Bolt Booster Box", POKEMON)
        self.assertEqual((match[0], value), (1, 100))

    def test_two_languages_are_never_duplicates(self):
        from .management.commands.merge_duplicates import duplicate_groups

        make_product(self.set, name="Black Bolt Booster Box", product_type="booster_box")
        make_product(self.set, name="Black Bolt (sv11B) Booster Box", product_type="booster_box")
        self.assertEqual(duplicate_groups(), [])
        self.assertEqual(duplicate_groups(loose=True), [])

    def test_a_coded_product_is_never_filed_under_the_english_set(self):
        from .releases import choose_set, set_rules

        rules = set_rules(POKEMON, [(1, "Black Bolt", "SV10.5"), (2, "Black Bolt (Japanese)", "sv11B")])
        self.assertEqual(choose_set("Black Bolt (sv11B) Booster Box", rules, POKEMON), 2)
        self.assertEqual(choose_set("Black Bolt Booster Box", rules, POKEMON), 1)

    def test_the_autopilot_refuses_a_title_in_another_language(self):
        english = make_product(self.set, name="Black Bolt Booster Box", product_type="booster_box")
        self.assertEqual(autopilot.contradiction(english, "Pokemon Black Bolt (sv11B) Booster Box"),
                         "the shop's title says Japanese, not English")
        japanese = make_product(self.set, name="Black Bolt Booster Box (Japanese)", product_type="booster_box")
        self.assertEqual(autopilot.contradiction(japanese, "Black Bolt Booster Box Korean"),
                         "the shop's title says Korean, not Japanese")
        # A code on our side only implies Japanese: Korean reuses the codes, so a Korean title is left alone.
        coded = make_product(self.set, name="Terastal Festival Booster Box", product_type="booster_box")
        self.assertEqual(autopilot.contradiction(coded, "Terastal Festival Korean Booster Box"), "")
        self.assertEqual(autopilot.contradiction(english, "Pokemon Black Bolt Booster Box Sealed"), "")

    def test_ebay_never_takes_another_language_and_reads_sellers_codes(self):
        from . import ebay

        english = make_product(self.set, name="Black Bolt Booster Box", product_type="booster_box")
        self.assertTrue(ebay.junk(english, "Pokemon Black Bolt sv11B Booster Box"))
        self.assertTrue(ebay.junk(english, "Pokemon Black Bolt JP Booster Box"))
        lorcana = make_product(make_set(make_game(name="Lorcana", slug="lorcana"), slug="shimmering"),
                               name="Shimmering Skies Starter Deck Elsa & Wreck It Ralph", product_type="deck")
        self.assertFalse(ebay.junk(lorcana, "Disney Lorcana Shimmering Skies Starter Deck Elsa Wreck-It Ralph"))

    def test_a_listing_keeps_its_language_with_the_shop(self):
        jp = make_product(self.set, name="151 (sv2a) Booster Box", product_type="booster_box")
        make_listing(jp, make_retailer("Japan Shop", delivery_cost=Decimal("0")), price="120.00")
        self.assertEqual(Product.objects.for_lists().get(pk=jp.pk).language, "ja")
