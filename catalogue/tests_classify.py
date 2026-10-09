from decimal import Decimal
from django.test import TestCase

from .classify import classify, clean_name, tidy_name


class ClassifyTests(TestCase):
    def test_shop_titles_become_clean_names(self):
        cases = {
            "Pokemon - Surging Sparks - Booster Box (36 Packs)": ("pokemon", "booster_box", "Surging Sparks Booster Box"),
            "Pokémon TCG: Surging Sparks Booster Box": ("pokemon", "booster_box", "Surging Sparks Booster Box"),
            "Pokemon TCG: 30th Celebration - Booster Bundle": ("pokemon", "bundle", "30th Celebration Booster Bundle"),
            "Pokemon - Mega Evolution - Pitch Black - Pokemon Center Elite Trainer Box": ("pokemon", "elite_trainer_box", "Mega Evolution Pitch Black Pokemon Center Elite Trainer Box"),
            "Magic the Gathering: Reality Fracture Play Booster Box": ("magic-the-gathering", "booster_box", "Reality Fracture Play Booster Box"),
            "Yu-Gi-Oh! - Immortal Phoenix - Booster Box (24 Packs)": ("yu-gi-oh", "booster_box", "Immortal Phoenix Booster Box"),
            "Disney Lorcana TCG: Hyperia City - Illumineer's Trove Set": ("lorcana", "collection_box", "Hyperia City Illumineer's Trove Set"),
            "Star Wars Unlimited  - Icons 2027 Edition - Booster Box (24 Packs)": ("star-wars-unlimited", "booster_box", "Icons 2027 Edition Booster Box"),
            "One Piece Card Game - Tin Pack Set (TS-03) - Shanks V.1": ("one-piece", "tin", "Tin Pack Set Shanks V.1"),
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                sealed = classify(title, vendor="", tags=(), price=10)
                self.assertIsNotNone(sealed, title)
                self.assertEqual((sealed.game, sealed.product_type, sealed.name), expected)

    def test_things_we_do_not_list(self):
        for title, kwargs in [
            ("Charizard 4 - ME 30th Celebration Classic Collection Holofoil", {"shop_type": "Pokemon Single"}),
            ("Pokemon - Prismatic Evolutions - Glaceon ex - 026/131 - Stamped Promo", {}),
            ("Pokémon - Mega Evolution: Delta Reign Pre-Release Event - Wednesday 6pm", {"shop_type": "Event"}),
            ("Ultra Pro Pokemon Playmat Pikachu", {}),
            ("Pokemon Surging Sparks Booster Box Case (6 boxes)", {}),
            ("Unlock! Short Adventures 15 - Assassin's Creed", {"shop_type": "Board Games"}),
            ("Pokemon Booster Pack x 10", {}),
            ("Eevee - M6a MEGA Expansion 30th Celebration Holofoil", {"price": 0}),
        ]:
            with self.subTest(title=title):
                kwargs.setdefault("price", 10)
                self.assertIsNone(classify(title, **kwargs), title)

    def test_cardfight_and_weiss_schwarz_are_games_with_the_kind_moved_last(self):
        from .classify import classify

        weiss = classify("Weiss Schwarz: Booster Box - Kaguya-Sama: Love Is War? (2023)", "Trading Card Games", price=70)
        self.assertEqual((weiss.game, weiss.product_type, weiss.name), ("weiss-schwarz", "booster_box", "Kaguya Sama: Love Is War? (2023) Booster Box"))
        trial = classify("Weiss Schwarz Trial Deck+ Hatsune Miku: Colorful Stage!", "Trading Card Games", price=20)
        self.assertEqual((trial.game, trial.product_type), ("weiss-schwarz", "deck"))
        cf = classify("Cardfight!! Vanguard: Booster Pack: Chasm of Lost Souls", "Trading Card Games", price=4)
        self.assertEqual((cf.game, cf.product_type, cf.name), ("cardfight-vanguard", "booster_pack", "Chasm of Lost Souls Booster Pack"))
        cf_box = classify("Cardfight!! Vanguard: overDress - Touken Ranbu Online - Booster Box", "Trading Card Games", price=80)
        self.assertEqual((cf_box.game, cf_box.product_type), ("cardfight-vanguard", "booster_box"))

    def test_game_from_vendor_or_tag(self):
        sealed = classify("Surging Sparks Booster Box", vendor="Pokemon", price=100)
        self.assertEqual(sealed.game, "pokemon")
        self.assertEqual(clean_name("Pokemon - Surging Sparks - Elite Trainer Box (EN)"), "Surging Sparks Elite Trainer Box")


class ClassifyLeakTests(TestCase):
    def test_singles_and_multipacks_are_rejected(self):
        for title in [
            "Basic Metal Energy SM1S Collection Sun (Near Mint)",
            "Goliath Daydreamer (PPECL 143) Promo Pack: Lorwyn Eclipsed (Near Mint)",
            "Yu-Gi-Oh! The Crimson King Structure Deck x 3",
            "Pokemon Booster Pack x 10",
            "Power Pack Commander: Marvel Super Heroes (Near Mint)",
            "Caged Sun | Mystery Booster",
            "Snow-Covered Forest | Mystery Booster 2",
            "Ultra Pro MTG M15 Jace Standard Deck Protector 80",
            "Astral Radiance Gapejaw Bog (Prize Pack League Promo Non-Holo)",
            "Vivien's Jaguar (Planeswalker Deck Card) | Core Set 2019",
            "Blue, Loyal Raptor (Borderless Art) | Jurassic World Collection",
            "Generations Radiant Collection RC30 Gardevoir EX Full Art",
        ]:
            self.assertIsNone(classify(title, vendor="Pokemon", price=10), title)

    def test_loose_parts_generic_names_and_cheap_things_are_rejected(self):
        for title, price in [
            ("Challenger Deck 2020 Wolf Token / Wolf Token", 10),
            ("Pokemon GO App Online Tin Code Sheet", 10),
            ("Mega Evolution Lucario Elite Trainer Box Card Divider", 10),
            ("Star Wars Unlimited Deck Pod Red", 10),
            ("Gamegenic Star Wars Unlimited Soft Crate Mandalorian", 10),
            ("Difuzed Star Wars Chewbacca Beanie & Scarf Gift Set", 15),
            ("Battle Pack Blue Eyes White Dragon & Gate Guardian 3.75 Inch Figures", 15),
            ("One Piece Card Game: Booster Pack", 10),
            ("Digimon Card Game: Starter Deck", 10),
            ("Magic The Gathering Hyena Pack Amonkhet", Decimal("0.40")),
        ]:
            self.assertIsNone(classify(title, price=price), title)

    def test_repeated_set_name_after_a_bar_is_dropped(self):
        sealed = classify("Innistrad Booster Box | Innistrad", vendor="Magic The Gathering", price=100)
        self.assertEqual(sealed.name, "Innistrad Booster Box")
        sealed = classify("Ursula&#039;s Return Booster Box", vendor="Lorcana", price=100)
        self.assertEqual(sealed.name, "Ursula's Return Booster Box")
        sealed = classify("Pokemon Kyurem V Collection Box | Sword and Shield", price=80)
        self.assertEqual(sealed.name, "Kyurem V Collection Box Sword and Shield")
        sealed = classify("Magic The Gathering Mercadian Masques Booster Box Mercadian Masques", price=300)
        self.assertEqual(sealed.name, "Mercadian Masques Booster Box")
        self.assertEqual(classify("Pokemon V Heroes Tin Umbreon V", price=20).name, "V Heroes Tin Umbreon V")

    def test_a_listing_that_is_both_pack_and_box_is_a_variant_menu(self):
        for title in ["Pokemon Scarlet & Violet Booster Box / Pack", "Pokemon Journey Together Booster Box Pack",
                      "Yu-Gi-Oh Dimension Force Booster Pack Box", "One Piece Wings of the Captain Booster Pack / Booster Box"]:
            self.assertIsNone(classify(title, price=50), title)
        for title in ["Pokemon Surging Sparks Booster Box (36 Packs)", "Pokemon Surging Sparks Booster Box 36 Packs",
                      "Yu-Gi-Oh Legacy of Destruction 24-Pack Box", "Pokemon Surging Sparks Booster Pack Display Box", "Pokemon 151 Booster Bundle 6 Booster Packs"]:
            self.assertIsNotNone(classify(title, price=50), title)

    def test_a_box_that_says_how_many_packs_are_inside_is_not_a_multi_buy(self):
        # The Card Vault writes every box this way; the multi-buy rule used to refuse them all.
        cases = {
            "Pokemon TCG: Surging Sparks Booster Box (36x Packs)": ("booster_box", "Surging Sparks Booster Box"),
            "One Piece OP-10 Booster Box (24x Packs)": ("booster_box", "OP 10 Booster Box"),
            "Pokemon Prismatic Evolutions Elite Trainer Box (9x Booster Packs)": ("elite_trainer_box", "Prismatic Evolutions Elite Trainer Box"),
        }
        for title, (kind, name) in cases.items():
            sealed = classify(title, price=100)
            self.assertIsNotNone(sealed, title)
            self.assertEqual((sealed.product_type, sealed.name), (kind, name), title)
        # Multi-buys stay refused: no box word, a count outside a box's contents, or boxes counted.
        for title in ["Pokemon 3x Booster Pack Bundle", "Pokemon Mega Evolution 6x Booster Packs",
                      "Pokemon Surging Sparks Booster Box x2", "Pokemon Surging Sparks Booster Box 2x",
                      "Pokemon Surging Sparks 3x Booster Box", "Pokemon Surging Sparks Booster Pack x 36",
                      "Pokemon Surging Sparks Booster Box (4x Packs)", "Pokemon Surging Sparks Booster Box (48x Packs)",
                      # Only the count straight after the box is its contents; any other count is a multi-buy or a bundle.
                      "Pokemon TCG - Scarlet & Violet - Destined Rivals - Booster Box (36x Packs) + 6x Booster Packs",
                      "One Piece OP-10 Booster Box (24x Packs) + 12x Booster Packs", "Destined Rivals 2 Booster Boxes (36x Packs)",
                      "Pokemon TCG Mystery Box (10x Booster Packs)", "Pokemon Mystery Box (10 Booster Packs)",
                      "Destined Rivals 10x Booster Packs (Display Box)", "Lorcana Fabled 24x Booster Packs (without display box)",
                      "Pokemon 12x Booster Packs Gift Box", "Simplified Chinese Gem Pack Vol 3 Box (10x Packs)",
                      "2x Booster Box (36x Packs)", "Pokemon Destined Rivals Booster Box (36x Packs) x2"]:
            self.assertIsNone(classify(title, price=100), title)
        for title in ["Pokemon Surging Sparks Booster Pack Display (36x Packs)", "Disney Lorcana Fabled Sleeved Booster Display - 24x Packs",
                      "Japanese Pokemon Terastal Festival Booster Box (30x Packs)"]:
            self.assertEqual(classify(title, price=100).product_type, "booster_box", title)

    def test_a_word_repeated_by_the_shop_appears_once(self):
        self.assertEqual(classify("Yu-Gi-Oh! Justice Hunters Booster Booster Pack", price=3).name, "Justice Hunters Booster Pack")

    def test_language_editions_keep_their_language(self):
        self.assertEqual(classify("Return to Ravnica Booster Pack [JAPANESE] | Return to Ravnica", vendor="Magic The Gathering", price=10).name,
                         "Return to Ravnica Booster Pack (Japanese)")
        self.assertEqual(classify("Pokemon Korean Sword Booster Box", price=50).name, "Korean Sword Booster Box")
        self.assertEqual(classify("Magic The Gathering Theros Booster Pack", price=5).name, "Theros Booster Pack")

    def test_codes_protectors_and_play_mats_are_not_sealed(self):
        for title in ["Relentless Flame Charizard Online Deck Code", "Acrylic Pokemon Booster Pack Protector Display Holder",
                      "Gamegenic - Magic The Gathering - Reality Fracture - Bastion 100+ XL - Charge the Sanctum",
                      "Ultra Pro Pokemon Charizard 9-Pocket Binder", "Dragon Shield Matte Sleeves Lorcana",
                      "RB1 031 Arcturusmon: Super Rare Foil: AD01: Advanced Booster Digimon Generation",
                      "P 108 Wisdom Training: Promo: AD01: Advanced Booster Digimon Generation",
                      "BT8 094 Digimon Emperor: Rare Foil: AD01: Advanced Booster",
                      "Pokemon Island Guardians GX Premium Collection Play Mat"]:
            self.assertIsNone(classify(title, price=10), title)

    def test_merchandise_makers_are_never_sealed(self):
        self.assertIsNone(classify("Pokemon - Pikachu Gift box", vendor="GB Posters", price=19.99))
        self.assertIsNone(classify("Pikachu Gift Box Set Pokemon", vendor="GB EYE", price=19.99))
        self.assertIsNotNone(classify("Pokemon - Pikachu Gift box", vendor="The Pokemon Company", price=19.99))

    def test_mystery_booster_packs_and_boxes_are_still_sealed(self):
        for title in ["Magic The Gathering Mystery Booster 2 Booster Box", "Magic The Gathering Mystery Booster Pack"]:
            self.assertIsNotNone(classify(title, price=10), title)

    def test_flesh_and_blood_prefix(self):
        sealed = classify("Flesh & Blood Armory Deck Malice", price=10)
        self.assertEqual((sealed.game, sealed.name), ("flesh-and-blood", "Armory Deck Malice"))


class FootballTests(TestCase):
    def test_match_attax_and_topps_products_are_football(self):
        sealed = classify("Topps Match Attax 2025/26 Booster Box (24 Packs)", "", "Topps", (), Decimal("45.00"))
        self.assertEqual((sealed.game, sealed.product_type), ("football", "booster_box"))
        sealed = classify("Match Attax 2025/26 Starter Pack", "", "Topps", (), Decimal("9.99"))
        self.assertEqual((sealed.game, sealed.product_type), ("football", "collection_box"))
        sealed = classify("Topps UEFA Champions League Chrome 2024/25 Hobby Box", "", "", (), Decimal("120.00"))
        self.assertEqual(sealed.game, "football")
        sealed = classify("Panini Premier League 2026 Adrenalyn XL Mega Tin", "", "Panini", (), Decimal("12.00"))
        self.assertEqual((sealed.game, sealed.product_type), ("football", "collection_box"))

    def test_card_vault_style_titles_are_football(self):
        sealed = classify("Panini - 2025/26 WLS Eternity Football (Soccer) - Hobby Box", "Booster Box", "Panini", (), Decimal("119.95"))
        self.assertEqual((sealed.game, sealed.product_type), ("football", "booster_box"))
        sealed = classify("Topps - 2026/27 Premier League Flagship Edition Football (Soccer) - Super Tin", "Super Tin", "Topps", (), Decimal("19.95"))
        self.assertEqual((sealed.game, sealed.product_type), ("football", "tin"))

    def test_a_shops_own_football_category_counts_and_formula_one_chrome_does_not(self):
        sealed = classify("Topps Premier League Debut Edition 2026 Golden Boot Tin", "football", "Topps", (), Decimal("29.99"))
        self.assertEqual((sealed.game, sealed.product_type), ("football", "tin"))
        self.assertIsNone(classify("Topps Chrome Formula 1 2026 Hobby Box", "f1", "Topps", (), Decimal("649.99")))

    def test_football_stickers_and_singles_stay_out(self):
        self.assertIsNone(classify("Panini Premier League 2026 Sticker Collection Packet", "", "Panini", (), Decimal("1.00")))
        self.assertIsNone(classify("Erling Haaland 100 Club Match Attax 2025/26 single card", "", "", (), Decimal("4.00")))


class TidyNameTests(TestCase):
    def test_shop_punctuation_and_stock_tails_go(self):
        self.assertEqual(tidy_name("! Duelist Nexus 1st Edition Booster Box"), "Duelist Nexus 1st Edition Booster Box")
        self.assertEqual(tidy_name("! Cyberse Link Structure Deck: Brand New And Sealed Box!"), "Cyberse Link Structure Deck")
        self.assertEqual(tidy_name("2020 Tin of Lost Memories 1st Edition: : Brand New and Sealed"), "2020 Tin of Lost Memories 1st Edition")
        self.assertEqual(tidy_name("Beyond The Brave Booster Display Box: Pre Order October 8, 2026"), "Beyond The Brave Booster Display Box")
        self.assertEqual(tidy_name("Reality Fracture Bundle: Pre-order 2 Oct"), "Reality Fracture Bundle")
        self.assertEqual(tidy_name("Mega Evolution Perfect Order Elite Trainer Box PRE ORDER"), "Mega Evolution Perfect Order Elite Trainer Box")
        self.assertEqual(tidy_name("PRE ORDER: Magic The Gathering Reality Fracture Secret Lair Bundle"), "Magic The Gathering Reality Fracture Secret Lair Bundle")
        self.assertEqual(tidy_name("PRE ORDER 7+ Day Delivery Time Pokémon TCG: JAPANESE 30th Celebration Booster Box"), "Pokémon TCG: JAPANESE 30th Celebration Booster Box")
        self.assertEqual(tidy_name("Detective Pikachu 1 Booster Pack Brand New And Sealed"), "Detective Pikachu 1 Booster Pack")
        self.assertEqual(tidy_name("Starter Deck: Dawn of the Xyz 1st Edition Brand New and Sealed Box"), "Starter Deck: Dawn of the Xyz 1st Edition")

    def test_ordinary_names_are_left_alone(self):
        self.assertEqual(tidy_name("Scarlet & Violet 8: Surging Sparks Booster Box"), "Scarlet & Violet 8: Surging Sparks Booster Box")
        self.assertEqual(tidy_name("Yu-Gi-Oh! Rarity Collection"), "Yu-Gi-Oh Rarity Collection")


class TidyCatalogueTests(TestCase):
    def test_tidy_removes_renames_and_merges(self):
        from decimal import Decimal
        from io import StringIO

        from django.core.management import call_command

        from catalogue.models import Listing, Product
        from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

        game = make_game()
        pset = make_set(game)
        shop_a, shop_b = make_retailer("Shop A"), make_retailer("Shop B")
        token = make_product(pset, name="Challenger Deck 2020 Wolf Token")
        generic = make_product(pset, name="Booster Pack")
        cheap = make_product(pset, name="Hyena Pack Amonkhet")
        make_listing(cheap, shop_a, price=Decimal("0.40"))
        dupe = make_product(pset, name="Innistrad Booster Box Innistrad")
        keep = make_product(pset, name="Innistrad Booster Box")
        make_listing(dupe, shop_b, price=Decimal("300"))
        entity = make_product(pset, name="Ursula&#039;s Return Booster Box")
        orphan = make_product(pset, name="Nobody Sells This Booster Box")
        for p in (token, generic, cheap, dupe, keep, entity, orphan):
            Product.objects.filter(pk=p.pk).update(image="")
        make_listing(keep, shop_a, price=Decimal("300"))
        make_listing(entity, shop_a, price=Decimal("100"))

        out = StringIO()
        call_command("tidy_catalogue", stdout=out)
        names = set(Product.objects.values_list("name", flat=True))
        self.assertNotIn("Challenger Deck 2020 Wolf Token", names)
        self.assertNotIn("Booster Pack", names)
        self.assertNotIn("Hyena Pack Amonkhet", names)
        self.assertIn("Ursula's Return Booster Box", names)
        self.assertNotIn("Innistrad Booster Box Innistrad", names)
        self.assertNotIn("Nobody Sells This Booster Box", names)
        self.assertTrue(Listing.objects.filter(product=keep, retailer=shop_b).exists())


class TidyAllTests(TestCase):
    def test_tidy_all_removes_a_merchandise_product_linked_before_the_rule_existed(self):
        from io import StringIO

        from django.core.management import call_command

        from catalogue.models import Product
        from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

        box = make_product(make_set(make_game()), name="Pikachu Gift Box", product_type="collection_box")
        Product.objects.filter(pk=box.pk).update(image="")
        make_listing(box, make_retailer("Shop", source_type="website"), url="https://shop.example/gb-posters-pokemon-pikachu-gift-box")
        call_command("tidy_all", stdout=StringIO())
        self.assertFalse(Product.objects.filter(pk=box.pk).exists())
