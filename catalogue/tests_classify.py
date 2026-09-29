from django.test import TestCase

from .classify import classify, clean_name


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
        ]:
            self.assertIsNone(classify(title, vendor="Pokemon", price=10), title)

    def test_flesh_and_blood_prefix(self):
        sealed = classify("Flesh & Blood Armory Deck Malice", price=10)
        self.assertEqual((sealed.game, sealed.name), ("flesh-and-blood", "Armory Deck Malice"))
