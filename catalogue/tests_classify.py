from decimal import Decimal
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

    def test_mystery_booster_packs_and_boxes_are_still_sealed(self):
        for title in ["Magic The Gathering Mystery Booster 2 Booster Box", "Magic The Gathering Mystery Booster Pack"]:
            self.assertIsNotNone(classify(title, price=10), title)

    def test_flesh_and_blood_prefix(self):
        sealed = classify("Flesh & Blood Armory Deck Malice", price=10)
        self.assertEqual((sealed.game, sealed.name), ("flesh-and-blood", "Armory Deck Malice"))


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
        for p in (token, generic, cheap, dupe, keep, entity):
            Product.objects.filter(pk=p.pk).update(image="")

        out = StringIO()
        call_command("tidy_catalogue", stdout=out)
        names = set(Product.objects.values_list("name", flat=True))
        self.assertNotIn("Challenger Deck 2020 Wolf Token", names)
        self.assertNotIn("Booster Pack", names)
        self.assertNotIn("Hyena Pack Amonkhet", names)
        self.assertIn("Ursula's Return Booster Box", names)
        self.assertNotIn("Innistrad Booster Box Innistrad", names)
        self.assertTrue(Listing.objects.filter(product=keep, retailer=shop_b).exists())
