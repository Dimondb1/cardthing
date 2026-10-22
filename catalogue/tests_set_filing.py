"""Filing products under sets (catalogue/releases.py): the code and name rules, the back catalogue the release
sources list, set codes in product names for games no source lists, and the file_sets command."""

from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from . import checks, releases
from .models import Product, ProductSet, Release
from .testing import make_game, make_set
from .tests_releases import NOW, FakeFetch, fixture, signal, source


def product(game, name, **kwargs):
    kwargs.setdefault("product_type", Product.Type.BOOSTER_BOX)
    return Product.objects.create(game=game, name=name, **kwargs)


def set_of(item):
    item.refresh_from_db()
    return item.product_set


class CodeTests(TestCase):
    def setUp(self):
        self.digimon = make_game(name="Digimon Card Game", slug="digimon")

    def test_one_spelling_for_a_code(self):
        self.assertEqual(releases.code_key("BT 15"), "bt15")
        self.assertEqual(releases.code_key("BT-15"), "bt15")
        self.assertEqual(releases.code_key("ST09"), releases.code_key("ST9"))
        self.assertEqual(releases.code_key("OP-01"), "op1")
        # Pokémon keeps its zeros: a source's SV08 is not the shops' SV8.
        self.assertNotEqual(releases.code_key("SV08", "pokemon"), releases.code_key("SV8", "pokemon"))
        self.assertEqual(releases.set_code_keys("OP15-EB04"), {"op15"})
        self.assertEqual(releases.set_code_keys("BRO"), frozenset())

    def test_a_spaced_or_unpadded_code_files_the_product(self):
        exceed = make_set(self.digimon, name="Exceed Apocalypse", slug="exceed-apocalypse", code="BT15")
        dragon = make_set(self.digimon, name="Ancient Dragon", slug="ancient-dragon", code="ST09")
        spaced = product(self.digimon, "Trading Card Game BT 15 Booster Pack")
        dashed = product(self.digimon, "BT-15 Booster Box")
        short = product(self.digimon, "Starter Deck (ST9)")
        releases.attach_sets(self.digimon)
        self.assertEqual(set_of(spaced), exceed)
        self.assertEqual(set_of(dashed), exceed)
        self.assertEqual(set_of(short), dragon)

    def test_a_run_of_sets_is_never_filed_by_its_codes(self):
        make_set(self.digimon, name="Beginning Observer", slug="beginning-observer", code="BT16")
        make_set(self.digimon, name="New Evolution", slug="new-evolution", code="BT01")
        runs = [product(self.digimon, name) for name in (
            "Special Booster Ver.2.0 (BT16 17) Booster Box", "Release Special Booster Ver.1.0 BT01 03",
            "Special Booster BT01-03 Display")]
        pack = product(self.digimon, "BT16 Booster Pack 24 Packs")
        releases.attach_sets(self.digimon)
        for item in runs:
            self.assertIsNone(set_of(item), item.name)
        self.assertEqual(set_of(pack).code, "BT16")

    def test_codes_of_two_sets_file_nothing(self):
        make_set(self.digimon, name="Next Adventure", slug="next-adventure", code="BT07")
        make_set(self.digimon, name="Draconic Roar", slug="draconic-roar", code="EX03")
        both = product(self.digimon, "BT07 and EX03 Booster Bundle")
        releases.attach_sets(self.digimon)
        self.assertIsNone(set_of(both))

    def test_a_code_two_sets_share_files_nothing(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        make_set(ygo, name="2013 Collectible Tins Wave 1", slug="tins-1", code="CT10")
        make_set(ygo, name="2013 Collectible Tins Wave 2", slug="tins-2", code="CT10")
        tin = product(ygo, "CT10 Tin")
        releases.attach_sets(ygo)
        self.assertIsNone(set_of(tin))

    def test_a_product_in_another_language_is_never_filed_by_the_code(self):
        make_set(self.digimon, name="Exceed Apocalypse", slug="exceed-apocalypse", code="BT15")
        japanese = product(self.digimon, "Exceed Apocalypse BT 15 Japanese Booster Pack")
        releases.attach_sets(self.digimon)
        self.assertIsNone(set_of(japanese))

    def test_a_filed_product_is_never_moved(self):
        old = make_set(self.digimon, name="Great Legend", slug="great-legend", code="BT04")
        make_set(self.digimon, name="Exceed Apocalypse", slug="exceed-apocalypse", code="BT15")
        filed = product(self.digimon, "BT15 Booster Box", product_set=old)
        self.assertEqual(releases.attach_sets(self.digimon), 0)
        self.assertEqual(set_of(filed), old)


class KoreanSetTests(TestCase):
    """The owner's case: with Korean picked, Pokémon's page offered no sets, because a Korean product never files
    under the Japanese set it is a printing of, and no source lists Korean sets."""

    def setUp(self):
        self.pokemon = make_game()
        self.anniversary = make_set(self.pokemon, name="25th Anniversary Collection (Japanese)",
                                    slug="25th-anniversary-collection-japanese", code="s8a",
                                    release_date=date(2021, 10, 22))
        self.terastal = make_set(self.pokemon, name="Terastal Festival ex (Japanese)",
                                 slug="terastal-festival-ex-japanese", code="sv8a")
        self.english = make_set(self.pokemon, name="Surging Sparks", slug="surging-sparks", code="SV08")

    def test_a_korean_product_files_under_the_korean_twin_of_its_japanese_set(self):
        boxes = [product(self.pokemon, name) for name in (
            "25th Anniversary Collection s8a Korean Booster Box", "Pokemon TCG Korean s8a Booster Pack")]
        by_name = product(self.pokemon, "Pokemon Korean Terastal Festival ex Booster Box")
        japanese = product(self.pokemon, "25th Anniversary Collection s8a Japanese Booster Box")
        unknown = product(self.pokemon, "Pokemon Korean Mystery Box")
        self.assertEqual(boxes[0].language, "ko")
        self.assertEqual(releases.attach_sets(self.pokemon), 4)
        twin = set_of(boxes[0])
        self.assertEqual((twin.name, twin.code, twin.release_date), ("25th Anniversary Collection (Korean)", "s8a", None))
        self.assertEqual(set_of(boxes[1]), twin)
        self.assertEqual(set_of(by_name).name, "Terastal Festival ex (Korean)")
        self.assertEqual(set_of(japanese), self.anniversary)
        self.assertIsNone(set_of(unknown))
        # A Korean product added later files straight under the twin, by its code, and no second twin is added.
        later = product(self.pokemon, "s8a Korean Booster Box")
        releases.attach_sets(self.pokemon, product_ids=[later.pk])
        self.assertEqual(set_of(later), twin)
        self.assertEqual(ProductSet.objects.filter(name__endswith="(Korean)").count(), 2)
        # English sources never take the twin as the set they speak of, though it shares the Japanese code.
        self.assertIsNone(releases.find_set(self.pokemon, "Something New", "s8a"))

    def test_only_games_whose_korean_sets_are_the_japanese_ones(self):
        lorcana = make_game(name="Disney Lorcana", slug="lorcana")
        make_set(lorcana, name="The First Chapter (Japanese)", slug="the-first-chapter-japanese", code="TFC")
        korean = product(lorcana, "The First Chapter Korean Booster Box")
        releases.attach_sets(lorcana)
        self.assertIsNone(set_of(korean))
        self.assertEqual(releases.korean_twin_name("[JP] Battle Partners"), "Battle Partners (Korean)")
        self.assertEqual(releases.korean_twin_name("Japanese Paradise Dragona"), "Paradise Dragona (Korean)")

    def test_the_game_page_offers_korean_sets_with_korean_picked(self):
        product(self.pokemon, "25th Anniversary Collection s8a Korean Booster Box")
        releases.attach_sets(self.pokemon)
        response = self.client.get("/games/pokemon/?lang=ko")
        self.assertContains(response, '>25th Anniversary Collection (Korean)<span class="set-nav__count')
        self.assertNotContains(response, '>25th Anniversary Collection (Japanese)<span class="set-nav__count')


class NameTests(TestCase):
    def setUp(self):
        self.rift = make_game(name="Riftbound", slug="riftbound")

    def test_a_one_word_set_takes_names_that_start_with_it_and_go_on_with_the_kind(self):
        origins = make_set(self.rift, name="Origins", slug="origins", code="")
        filed = [product(self.rift, name) for name in (
            "Origins Booster Box", "Set 1 Origins Booster Display", "Set One: Origins Champion Deck Jinx",
            "League of Legends Trading Card Game Set 1 Origins Booster Pack")]
        left = [product(self.rift, name) for name in (
            "Gift of the Origins Bundle", "Origins Proving Grounds", "League of Legends Origins OGN 01 Chinese Booster Box")]
        releases.attach_sets(self.rift)
        for item in filed:
            self.assertEqual(set_of(item), origins, item.name)
        for item in left:
            self.assertIsNone(set_of(item), item.name)

    def test_one_word_names_two_sets_share_or_kinds_of_product_file_nothing(self):
        make_set(self.rift, name="Vault", slug="vault", code="")
        make_set(self.rift, name="Radiance", slug="radiance", code="")
        make_set(self.rift, name="Radiance Set", slug="radiance-set", code="")
        vault = product(self.rift, "Vault Bundle")
        rules = releases.set_rules("riftbound", ProductSet.objects.values_list("pk", "name", "code"))
        self.assertFalse(next(r for r in rules if r.words == ("vault",)).one_word)
        releases.attach_sets(self.rift)
        self.assertIsNone(set_of(vault))

    def test_a_pokemon_name_set_never_takes_a_product_about_the_pokemon(self):
        pokemon = make_game()
        make_set(pokemon, name="Deoxys", slug="deoxys", code="")
        make_set(pokemon, name="Evolutions", slug="evolutions", code="")
        battle = product(pokemon, "Deoxys VMAX & VSTAR Battle Box")
        mega = product(pokemon, "Mega Evolutions Base Set Booster Pack")
        releases.attach_sets(pokemon)
        self.assertIsNone(set_of(battle))
        self.assertIsNone(set_of(mega))

    def test_the_whole_name_as_a_phrase(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        phoenix = make_set(ygo, name="Immortal Phoenix", slug="immortal-phoenix", code="")
        apart = product(ygo, "Phoenix Immortal Booster Box")
        whole = product(ygo, "Immortal Phoenix Booster Box")
        releases.attach_sets(ygo)
        self.assertIsNone(set_of(apart))
        self.assertEqual(set_of(whole), phoenix)

    def test_two_sets_that_fit_file_nothing_unless_one_holds_the_other(self):
        pokemon = make_game()
        make_set(pokemon, name="Black & White", slug="black-white", code="")
        make_set(pokemon, name="Dragons Exalted", slug="dragons-exalted", code="")
        theme = product(pokemon, "DragonSnarl Hydreigon: Black & White Dragons Exalted Theme Deck")
        releases.attach_sets(pokemon)
        self.assertIsNone(set_of(theme))
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        make_set(magic, name="Final Fantasy", slug="final-fantasy", code="FIN")
        commander = make_set(magic, name="Final Fantasy Commander", slug="final-fantasy-commander", code="FIC")
        deck = product(magic, "Final Fantasy Commander Deck Revival Trance")
        releases.attach_sets(magic)
        self.assertEqual(set_of(deck), commander)

    def test_a_set_another_sets_name_holds_takes_only_its_boosters(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        final = make_set(magic, name="Final Fantasy", slug="final-fantasy", code="FIN")
        make_set(magic, name="Final Fantasy Commander", slug="final-fantasy-commander", code="FIC")
        play = product(magic, "Final Fantasy Play Booster Box")
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        make_set(ygo, name="Legendary Duelists", slug="legendary-duelists", code="")
        make_set(ygo, name="Legendary Duelists: Rage of Ra", slug="rage-of-ra", code="")
        nine = product(ygo, "Legendary Duelists 9: Duels From the Deep Booster Box")
        pokemon = make_game()
        make_set(pokemon, name="Team Rocket", slug="team-rocket", code="")
        make_set(pokemon, name="Team Rocket Returns", slug="team-rocket-returns", code="")
        tin = product(pokemon, "Team Rocket Tin (Mewtwo ex)")
        for game in (magic, ygo, pokemon):
            releases.attach_sets(game)
        self.assertEqual(set_of(play), final)
        self.assertIsNone(set_of(nine))
        self.assertIsNone(set_of(tin))

    def test_the_reason_is_given(self):
        make_set(self.rift, name="Origins", slug="origins", code="")
        rules = releases.set_rules("riftbound", ProductSet.objects.values_list("pk", "name", "code"))
        self.assertEqual(releases.choose_set_why("Origins Booster Box", rules, "riftbound")[1], "one word")
        self.assertEqual(releases.choose_set_why("Origins OGN 01 Chinese Booster Box", rules, "riftbound")[1],
                         "no set in Chinese")
        self.assertEqual(releases.choose_set_why("Spiritforged Booster Box", rules, "riftbound")[1],
                         "names no set the site has")


class BackCatalogueTests(TestCase):
    def setUp(self):
        self.magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        self.pokemon = make_game()

    def listed(self, game, name, when, source_name, code=""):
        releases.accept(source(source_name), [signal(game.slug, name, when, code=code)], now=NOW)
        return Release.objects.get(game=game, name=name, source=source_name)

    def test_a_set_released_long_ago_is_added_when_a_product_names_it(self):
        box = product(self.magic, "Final Fantasy Play Booster Box")
        row = self.listed(self.magic, "Final Fantasy", date(2025, 6, 13), "scryfall_sets", code="FIN")
        added = ProductSet.objects.get(game=self.magic)
        self.assertEqual((added.name, added.code, added.release_date, added.release_date_source),
                         ("Final Fantasy", "FIN", date(2025, 6, 13), "scryfall_sets"))
        self.assertEqual(set_of(box), added)
        row.refresh_from_db()
        self.assertEqual((row.status, row.product_set), (Release.Status.ACCEPTED, added))
        # Recorded, and off the owner's list.
        self.assertNotIn(row, checks.release_candidates(NOW))

    def test_no_product_no_set(self):
        self.listed(self.magic, "Edge of Eternities", date(2025, 8, 1), "scryfall_sets", code="EOE")
        self.assertFalse(ProductSet.objects.exists())

    def test_tidy_files_a_later_product(self):
        self.listed(self.magic, "Edge of Eternities", date(2025, 8, 1), "scryfall_sets", code="EOE")
        box = product(self.magic, "Edge of Eternities Collector Booster Box")
        self.assertEqual(releases.attach_sets(self.magic, now=NOW), 1)
        self.assertEqual(set_of(box).name, "Edge of Eternities")

    def test_a_dismissed_row_adds_nothing(self):
        row = self.listed(self.magic, "Edge of Eternities", date(2025, 8, 1), "scryfall_sets", code="EOE")
        releases.dismiss(row)
        box = product(self.magic, "Edge of Eternities Booster Box")
        releases.attach_sets(self.magic, now=NOW)
        self.assertIsNone(set_of(box))
        self.assertFalse(ProductSet.objects.exists())

    def test_a_lone_community_set_inside_the_window_still_waits(self):
        lorcana = make_game(name="Disney Lorcana", slug="lorcana")
        box = product(lorcana, "Wilds Unknown Booster Box")
        row = self.listed(lorcana, "Wilds Unknown", NOW.date() - timedelta(days=30), "lorcast_sets")
        self.assertIsNone(set_of(box))
        self.assertFalse(ProductSet.objects.exists())
        self.assertIn(row, checks.release_candidates(NOW))

    def test_a_listed_set_keeps_a_product_from_a_set_whose_name_is_part_of_its(self):
        make_set(self.pokemon, name="Black & White", slug="black-white", code="")
        theme = product(self.pokemon, "Black & White Dragons Exalted Theme Deck")
        storm = product(self.pokemon, "Plasma Storm Booster Pack")
        self.listed(self.pokemon, "Dragons Exalted", date(2012, 8, 15), "tcgdex_sets", code="bw6")
        self.listed(self.pokemon, "Plasma Storm", date(2013, 2, 6), "tcgdex_sets", code="bw8")
        # Two sets fit, so neither: not the series set, and the listed set is not added on a doubtful name.
        self.assertIsNone(set_of(theme))
        self.assertFalse(ProductSet.objects.filter(name="Dragons Exalted").exists())
        self.assertEqual(set_of(storm).name, "Plasma Storm")
        # TCGdex's ids are not the codes shops write, so its sets carry none.
        self.assertEqual(set_of(storm).code, "")

    def test_the_owners_date_is_never_changed(self):
        mine = make_set(self.magic, name="Final Fantasy", slug="final-fantasy", code="FIN", release_date=date(2025, 6, 1),
                        release_date_source=releases.OWNER)
        product(self.magic, "Final Fantasy Booster Box")
        self.listed(self.magic, "Final Fantasy", date(2025, 6, 13), "scryfall_sets", code="FIN")
        mine.refresh_from_db()
        self.assertEqual(mine.release_date, date(2025, 6, 1))
        self.assertEqual(ProductSet.objects.count(), 1)

    def test_an_ordinary_read_records_the_back_catalogue_and_files_products(self):
        box = product(self.magic, "Final Fantasy Play Booster Box")
        fetch = FakeFetch({"api.scryfall.com/sets": fixture("scryfall_sets.json")})
        result = releases.scan("scryfall_sets", fetch=fetch, now=NOW, sleep=lambda s: None)
        self.assertTrue(result.ok, result)
        self.assertEqual(set_of(box).name, "Final Fantasy")
        self.assertTrue(Release.objects.filter(name="Edge of Eternities", status=Release.Status.PENDING).exists())
        self.assertFalse(ProductSet.objects.filter(name="Edge of Eternities").exists())

    def test_backfill_reads_the_publishers_booster_list_and_skips_news(self):
        op = make_game(name="One Piece Card Game", slug="one-piece")
        box = product(op, "OP-03 Pillars of Strength Booster Box")
        fetch = FakeFetch({"subcategory=boosters&page=2": fixture("bandai_onepiece_boosters_page2.html"),
                           "subcategory=boosters&page=3": b"<html><body>No products.</body></html>",
                           "subcategory=boosters": fixture("bandai_onepiece_page1.html")})
        result = releases.backfill("bandai_onepiece", fetch=fetch, now=NOW, sleep=lambda s: None)
        self.assertTrue(result.ok, result)
        self.assertEqual(fetch.calls[0][0], "https://en.onepiece-cardgame.com/products/?subcategory=boosters")
        pillars = set_of(box)
        self.assertEqual((pillars.name, pillars.code, pillars.release_date), ("Pillars of Strength", "OP-03", date(2023, 6, 30)))
        self.assertEqual(releases.backfill("pokemon_uk_news", fetch=fetch, now=NOW).skipped,
                         "it lists only sets still to come")

    def test_backfill_dry_run_records_nothing(self):
        product(self.magic, "Final Fantasy Play Booster Box")
        fetch = FakeFetch({"api.scryfall.com/sets": fixture("scryfall_sets.json")})
        result = releases.backfill("scryfall_sets", fetch=fetch, now=NOW, dry_run=True)
        self.assertTrue(result.signals)
        self.assertFalse(Release.objects.exists())
        self.assertFalse(ProductSet.objects.exists())


class NameCandidateTests(TestCase):
    def setUp(self):
        self.digimon = make_game(name="Digimon Card Game", slug="digimon")

    def test_codes_in_names_wait_under_announced_sets_to_check(self):
        make_set(self.digimon, name="Exceed Apocalypse", slug="exceed-apocalypse", code="BT15")
        for name in ("BT27 Ignition of X Booster Pack", "Ignition of X BT 27 Booster Box", "Starter Deck (ST9)",
                     "Exceed Apocalypse BT 15 Japanese Booster Pack", "Special Booster Ver.2.0 (BT18 19) Booster Box"):
            product(self.digimon, name)
        self.assertEqual(releases.raise_name_candidates(self.digimon), ["BT27", "ST09"])
        row = Release.objects.get(name="BT27")
        self.assertEqual((row.source, row.code, row.status, row.official), ("shop:names", "BT27", Release.Status.PENDING, False))
        self.assertIn("Ignition of X BT 27 Booster Box", row.note)
        listed = checks.name_sources(checks.release_candidates(NOW))
        self.assertEqual({r.name for r in listed}, {"BT27", "ST09"})
        self.assertEqual(listed[0].source_label, "product names on the site")
        # Asked once: a second hour, or a row the owner dismissed, raises nothing.
        releases.dismiss(row)
        self.assertEqual(releases.raise_name_candidates(self.digimon), [])
        # Names never add a set.
        self.assertEqual(ProductSet.objects.count(), 1)

    def test_only_games_no_source_lists(self):
        pokemon = make_game()
        product(pokemon, "SV08 Booster Box")
        self.assertEqual(releases.raise_name_candidates(pokemon), [])

    def test_the_owners_add_set_files_by_the_code(self):
        box = product(self.digimon, "Ignition of X BT 27 Booster Box")
        releases.raise_name_candidates(self.digimon)
        releases.add_set(Release.objects.get(name="BT27"), "Ignition of X")
        self.assertEqual(set_of(box).name, "Ignition of X")


class FileSetsCommandTests(TestCase):
    def test_dry_run_changes_nothing_and_says_why(self):
        rift = make_game(name="Riftbound", slug="riftbound")
        make_set(rift, name="Origins", slug="origins", code="")
        box = product(rift, "Origins Booster Box")
        left = product(rift, "Spiritforged SFD Booster Box")
        out = StringIO()
        call_command("file_sets", "--dry-run", stdout=out)
        text = out.getvalue()
        self.assertIn("Riftbound: 2 active, 0 had a set, 1 would be filed now", text)
        self.assertIn("left   Spiritforged SFD Booster Box  (names no set the site has)", text)
        self.assertIn("set codes for Announced sets to check: SFD", text)
        self.assertIn("Dry run: nothing was changed.", text)
        self.assertIsNone(set_of(box))
        self.assertIsNone(set_of(left))
        self.assertFalse(Release.objects.exists())

    def test_files_for_real(self):
        rift = make_game(name="Riftbound", slug="riftbound")
        origins = make_set(rift, name="Origins", slug="origins", code="")
        box = product(rift, "Origins Booster Box")
        call_command("file_sets", stdout=StringIO())
        self.assertEqual(set_of(box), origins)


class ReviewedFilingTests(TestCase):
    """Product names the review found filed under the wrong set, each now left without one."""

    def file(self, game, sets, names):
        for name, code in sets:
            make_set(game, name=name, slug=releases.name_key(name), code=code)
        items = {name: product(game, name) for name in names}
        releases.attach_sets(game)
        return {name: (set_of(item).name if set_of(item) else None) for name, item in items.items()}

    def test_a_set_name_printed_in_two_sets_never_files_by_name(self):
        pokemon = make_game()
        got = self.file(pokemon, [("Radiant Collection", ""), ("Plasma Storm", "")], [
            "Generations Radiant Collection RC10 Dedenne", "Radiant Collection RC5/RC32 Charizard Holo Mint Pokemon Card",
            "Plasma Storm Booster Pack"])
        self.assertEqual(got, {"Generations Radiant Collection RC10 Dedenne": None,
                               "Radiant Collection RC5/RC32 Charizard Holo Mint Pokemon Card": None,
                               "Plasma Storm Booster Pack": "Plasma Storm"})

    def test_a_longer_sets_other_words_anywhere_keep_the_product_from_the_shorter_set(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        got = self.file(magic, [("Star Trek", "TRK"), ("Star Trek Commander", "TRC"), ("Commander Legends", "CMR"),
                                ("Commander Legends: Battle for Baldur's Gate", "CLB")], [
            "Star Trek Collector Commander Deck Federation Fleet", "Star Trek Collector Booster Box",
            "D&D Battle for Baldurs Gate Commander Legends Draft Booster Box", "Commander Legends Draft Booster Box",
            "Commander Legends: Battle for Baldur's Gate Set Booster Box"])
        self.assertEqual(got, {
            "Star Trek Collector Commander Deck Federation Fleet": None,
            "Star Trek Collector Booster Box": "Star Trek",
            "D&D Battle for Baldurs Gate Commander Legends Draft Booster Box": None,
            "Commander Legends Draft Booster Box": "Commander Legends",
            "Commander Legends: Battle for Baldur's Gate Set Booster Box": "Commander Legends: Battle for Baldur's Gate"})

    def test_a_commander_deck_never_goes_under_a_set_without_commander_in_its_name(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        got = self.file(magic, [("Ikoria: Lair of Behemoths", "IKO"), ("Commander 2020", "C20")], [
            "Ikoria Lair of Behemoths Commander Deck Arcane Maelstrom", "Commander 2020 Deck Arcane Maelstrom",
            "Ikoria Lair of Behemoths Booster Box"])
        self.assertEqual(got, {"Ikoria Lair of Behemoths Commander Deck Arcane Maelstrom": None,
                               "Commander 2020 Deck Arcane Maelstrom": "Commander 2020",
                               "Ikoria Lair of Behemoths Booster Box": "Ikoria: Lair of Behemoths"})

    def test_a_reprint_goes_only_under_a_reprint_edition(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        got = self.file(ygo, [("Invasion of Chaos", ""), ("Retro Pack 2", ""), ("Legendary Decks II", ""),
                              ("Dark Crisis", ""), ("Dark Crisis (25th Anniversary Edition)", "")], [
            "Invasion of Chaos Booster Pack (YGO 25th Anniversary)",
            "Retro Pack 2 2025 European Print Unlimited Edition Booster Display Box",
            "Legendary Decks II (2024 Reprint)", "Dark Crisis Booster Pack (YGO 25th Anniversary)",
            "Dark Crisis 25th Anniversary Reprint Booster Pack", "Retro Pack 2 Booster Box"])
        self.assertEqual(got, {
            "Invasion of Chaos Booster Pack (YGO 25th Anniversary)": None,
            "Retro Pack 2 2025 European Print Unlimited Edition Booster Display Box": None,
            "Legendary Decks II (2024 Reprint)": None,
            "Dark Crisis Booster Pack (YGO 25th Anniversary)": None,
            "Dark Crisis 25th Anniversary Reprint Booster Pack": "Dark Crisis (25th Anniversary Edition)",
            "Retro Pack 2 Booster Box": "Retro Pack 2"})

    def test_a_product_naming_two_sets_in_any_order_files_nothing(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        got = self.file(ygo, [("Legendary Collection: 25th Anniversary Edition", "LC01"), ("Metal Raiders", ""),
                              ("Metal Raiders (25th Anniversary Edition)", "")], [
            "Legendary Collection 25th Anniversary Reprint Metal Raiders Booster Box",
            "Legendary Collection 25th Anniversary Edition (Case Of 6 Boxes)"])
        self.assertEqual(got, {"Legendary Collection 25th Anniversary Reprint Metal Raiders Booster Box": None,
                               "Legendary Collection 25th Anniversary Edition (Case Of 6 Boxes)":
                                   "Legendary Collection: 25th Anniversary Edition"})

    def test_a_series_a_manga_or_a_pin_badge_is_not_the_set(self):
        pokemon = make_game()
        got = self.file(pokemon, [("Black & White", ""), ("Sun & Moon", ""), ("Legendary Collection", "")], [
            "Toxic Tricks Scolipede: Black & White Series Pokemon Theme Deck", "Sun & Moon Complete Manga Box Set",
            "Official Pin Badge Pikachu (Legendary Collection)", "Sun & Moon Booster Box"])
        self.assertEqual(got, {"Toxic Tricks Scolipede: Black & White Series Pokemon Theme Deck": None,
                               "Sun & Moon Complete Manga Box Set": None,
                               "Official Pin Badge Pikachu (Legendary Collection)": None,
                               "Sun & Moon Booster Box": "Sun & Moon"})
        # A set with the word in its own name still takes it.
        dbs = make_game(name="Dragon Ball Super Card Game", slug="dragon-ball")
        got = self.file(dbs, [("Manga Booster 02", "SB02")], ["Fusion World SB02 Manga Booster 02 Booster Box"])
        self.assertEqual(got, {"Fusion World SB02 Manga Booster 02 Booster Box": "Manga Booster 02"})


class BackCatalogueReviewTests(TestCase):
    def test_two_sets_sharing_a_code_stay_apart_with_their_own_dates(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        anniversary = product(ygo, "Legend of Blue Eyes White Dragon 25th Anniversary Booster Box")
        original = product(ygo, "Legend of Blue Eyes White Dragon Booster Box")
        coded = product(ygo, "LOB Booster Pack")
        releases.accept(source("ygoprodeck_sets"), [
            signal("yu-gi-oh", "Legend of Blue Eyes White Dragon", date(2002, 3, 8), code="LOB"),
            signal("yu-gi-oh", "Legend of Blue Eyes White Dragon (25th Anniversary Edition)", date(2023, 4, 20), code="LOB"),
        ], now=NOW)
        self.assertEqual(
            sorted(ProductSet.objects.values_list("name", "code", "release_date", "release_date_source")),
            [("Legend of Blue Eyes White Dragon", "", date(2002, 3, 8), "ygoprodeck_sets"),
             ("Legend of Blue Eyes White Dragon (25th Anniversary Edition)", "", date(2023, 4, 20), "ygoprodeck_sets")])
        self.assertEqual(set_of(anniversary).name, "Legend of Blue Eyes White Dragon (25th Anniversary Edition)")
        self.assertEqual(set_of(original).name, "Legend of Blue Eyes White Dragon")
        # The code alone says neither.
        self.assertIsNone(set_of(coded))

    def test_a_back_catalogue_set_the_owner_deletes_stays_deleted(self):
        pokemon = make_game()
        box = product(pokemon, "Plasma Storm Booster Pack")
        # A second source's row of the same name, pointed at the set when it is added.
        other = Release.objects.create(game=pokemon, name="Plasma Storm", source="pokemon_uk_news", official=True,
                                       release_date=date(2013, 2, 6), first_seen_at=NOW, last_seen_at=NOW)
        releases.accept(source("tcgdex_sets"), [signal("pokemon", "Plasma Storm", date(2013, 2, 6), code="bw8")], now=NOW)
        added = set_of(box)
        other.refresh_from_db()
        self.assertEqual(other.product_set, added)
        Product.objects.filter(pk=box.pk).update(product_set=None)
        added.delete()
        self.assertEqual(releases.attach_sets(pokemon, now=NOW), 0)
        self.assertFalse(ProductSet.objects.exists())
        self.assertIsNone(set_of(box))


class NameRowsOrderTests(TestCase):
    def test_codes_from_names_never_push_a_sources_row_off_the_list(self):
        digimon = make_game(name="Digimon Card Game", slug="digimon")
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        announced = Release.objects.create(
            game=magic, name="Edge of Eternities", source="scryfall_sets", code="EOE", release_date=NOW.date() + timedelta(days=30),
            precision=releases.DAY, first_seen_at=NOW - timedelta(days=2), last_seen_at=NOW)
        for number in range(1, checks.RELEASE_ROWS + 11):
            Release.objects.create(game=digimon, name=f"BT{number:02d}", code=f"BT{number:02d}", source=releases.NAMES_SOURCE,
                                   first_seen_at=NOW, last_seen_at=NOW)
        rows = checks.release_candidates(NOW)
        self.assertEqual(len(rows), checks.RELEASE_ROWS)
        self.assertEqual(rows[0], announced)
