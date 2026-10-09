"""The release radar (catalogue/releases.py): parsers on saved answers, the publishing rules, shop titles,
filing products under sets, the Things to check buttons and the background reader's release scans."""

import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import releases, worker
from .importers import run_import
from .models import Product, ProductSet, Release, ReleaseSourceState, Retailer
from .releases import DAY, MONTH, ReleaseSignal
from .testing import make_game, make_listing, make_product, make_retailer, make_set

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "releases"
# The day the fixtures were saved, so the publishing rules see the same "recent" whenever the tests run.
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=dt_timezone.utc)
EM_DASH = chr(0x2014)


def fixture(name):
    return (FIXTURES / name).read_bytes()


def found(signals, name):
    matches = [s for s in signals if s.name == name]
    assert matches, f"{name} not in {[s.name for s in signals]}"
    return matches[0]


class ParserTests(TestCase):
    """Each parser on its saved answer returns the sample rows from the verified list."""

    def test_scryfall_lists_paper_sets_with_their_dates(self):
        signals = releases.parse_scryfall(fixture("scryfall_sets.json"))
        nauctis = found(signals, "Nauctis: The Sunken Realm")
        self.assertEqual((nauctis.code.lower(), nauctis.release_date, nauctis.precision), ("nau", date(2027, 2, 5), DAY))
        names = {s.name for s in signals}
        self.assertIn("Star Trek Commander", names)
        # Tokens, promos, Arena-only and masterpiece sets are not sets anyone buys sealed.
        self.assertFalse(names & {"Nauctis: The Sunken Realm Tokens", "Reality Fracture Promos", "Alchemy: Reality Fracture", "Stardates"})

    def test_scryfall_keeps_only_recent_sets_when_asked(self):
        signals = releases.parse_scryfall(fixture("scryfall_sets.json"), since=date(2026, 4, 12))
        self.assertNotIn("Final Fantasy", {s.name for s in signals})
        self.assertIn("Reality Fracture", {s.name for s in signals})

    def test_tcgdex_list_and_set_page(self):
        ids = releases.parse_tcgdex_list(fixture("tcgdex_sets.json"))
        self.assertEqual(ids[:3], ["30th", "30th-c", "me05"])
        self.assertIn("me03", ids)
        # TCG Pocket's sets are a video game's.
        self.assertFalse({"B2a", "A4a", "A1", "P-A"} & set(ids))
        perfect = releases.parse_tcgdex_set(fixture("tcgdex_set_me03.json"))
        self.assertEqual((perfect.name, perfect.code, perfect.release_date, perfect.precision),
                         ("Perfect Order", "me03", date(2026, 3, 27), DAY))

    def test_pokemon_uk_news_index_and_article(self):
        articles = releases.parse_pokemon_index(fixture("pokemon_uk_news.html"))
        urls = [url for url, _title in articles]
        delta = "https://www.pokemon.com/uk/news/play-at-a-pokemon-tcg-mega-evolution-delta-reign-prerelease-event"
        self.assertEqual(urls, [delta])
        signals = releases.parse_pokemon_article(fixture("pokemon_delta_reign.html"), delta)
        self.assertEqual([(s.name, s.release_date, s.precision, s.url) for s in signals],
                         [("Delta Reign", date(2026, 11, 6), DAY, delta)])

    def test_a_pokemon_video_game_article_with_a_release_date_gives_nothing(self):
        self.assertEqual(releases.parse_pokemon_article(fixture("pokemon_video_game.html"), "https://www.pokemon.com/uk/news/x"), [])

    def test_ygoprodeck_filters_by_date_on_our_side(self):
        signals = releases.parse_ygoprodeck(fixture("ygoprodeck_cardsets.json"), since=date(2026, 4, 12))
        maestros = found(signals, "Magnificent Maestros")
        self.assertEqual((maestros.code, maestros.release_date), ("MAMS", date(2026, 11, 12)))
        self.assertNotIn("Legacy of Destruction", {s.name for s in signals})

    def test_lorcast(self):
        signals = releases.parse_lorcast(fixture("lorcast_sets.json"))
        hyperia = found(signals, "Hyperia City")
        self.assertEqual((hyperia.code, hyperia.release_date), ("14", date(2026, 10, 16)))
        self.assertFalse({"Promo Set 1", "Challenge Promo", "Format Coconut", "PD1"} & {s.name for s in signals})

    def test_swudb_reads_base_sets_and_month_first_dates(self):
        signals = releases.parse_swudb(fixture("swudb_sets.json"))
        icons = found(signals, "Icons 2027")
        self.assertEqual((icons.code, icons.release_date), ("IC27", date(2026, 11, 20)))
        self.assertNotIn("Homeworlds - OP Promo", {s.name for s in signals})

    def test_bandai_one_piece(self):
        signals = releases.parse_bandai_onepiece(fixture("bandai_onepiece_page1.html"))
        dominance = found(signals, "The Dominance of God")
        self.assertEqual((dominance.code, dominance.release_date, dominance.precision), ("OP-18", date(2026, 11, 20), DAY))
        self.assertEqual(dominance.url, "https://en.onepiece-cardgame.com/products/op18.html")
        # Boosters only: gift collections, sleeves and mini cases are products, not sets.
        self.assertTrue(all(s.code[:2] in ("OP", "EB", "PR") for s in signals), signals)
        self.assertIn("OP-17", {s.code for s in releases.parse_bandai_onepiece(fixture("bandai_onepiece_page2.html"))})

    def test_dragon_ball_fusion_world(self):
        signals = releases.parse_dbs_fusion(fixture("dbs_fusion.html"))
        hope = found(signals, "Brightness of Hope")
        self.assertEqual((hope.code, hope.release_date), ("FB11", date(2026, 10, 16)))
        self.assertFalse(any("SLEEVE" in s.name.upper() or "STARTER" in s.name.upper() for s in signals))

    def test_vanguard(self):
        signals = releases.parse_vanguard(fixture("bushiroad_vanguard.html"))
        dawn = found(signals, "Parallactic Dawn")
        self.assertEqual((dawn.code, dawn.release_date), ("VGE-DZ-BT16", date(2026, 10, 30)))
        self.assertFalse(any("Playmat" in s.name or "Start Deck" in s.name for s in signals))
        # 'October 24th, 2025' is a date too.
        self.assertEqual(found(signals, "Dragonsoul Resonance").release_date, date(2025, 10, 24))

    def test_weiss_schwarz(self):
        signals = releases.parse_weiss(fixture("bushiroad_ws.html"))
        persona = found(signals, "Persona 30th Anniversary")
        self.assertEqual(persona.release_date, date(2026, 12, 18))
        self.assertEqual(len([s for s in signals if s.name == "Kaiju No.8"]), 1)   # the trial deck is left out

    def test_flesh_and_blood(self):
        signals = releases.parse_fabtcg(fixture("fabtcg_coming_soon.html"))
        smash = found(signals, "Smash Palace: Chorus of Steel")
        self.assertEqual((smash.code, smash.release_date), ("", date(2026, 10, 30)))
        self.assertNotIn("Armory Deck: Dr. Mortimer", {s.name for s in signals})

    def test_dates(self):
        cases = {
            "November 20, 2026": (date(2026, 11, 20), DAY), "October 24th, 2025": (date(2025, 10, 24), DAY),
            "6 Nov 2026": (date(2026, 11, 6), DAY), "28 Sept 2026": (date(2026, 9, 28), DAY),
            "Delivery Month November 2026": (date(2026, 11, 1), MONTH), "2027-02-05": (date(2027, 2, 5), DAY),
            ".": (None, releases.NONE), "Coming soon": (None, releases.NONE),
        }
        for text, expected in cases.items():
            self.assertEqual(releases.parse_date(text), expected, text)
        self.assertEqual(releases.parse_us_short_date("3/13/26"), (date(2026, 3, 13), DAY))

    def test_a_changed_page_parses_nothing_and_says_so(self):
        for parse in (releases.parse_bandai_onepiece, releases.parse_dbs_fusion, releases.parse_vanguard,
                      releases.parse_weiss, releases.parse_fabtcg, releases.parse_pokemon_index):
            with self.assertRaises(releases.NothingFound):
                parse(b"<html><body>We have moved.</body></html>")
        with self.assertRaises(releases.SourceError):
            releases.parse_scryfall(b"<html>not json</html>")


def source(name):
    return releases.BY_NAME[name]


def signal(game, name, when=None, precision=DAY, code=""):
    return ReleaseSignal(game, name, code, when, precision if when else releases.NONE)


class AcceptTests(TestCase):
    def setUp(self):
        self.pokemon = make_game()
        self.magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        self.op = make_game(name="One Piece Card Game", slug="one-piece")

    def test_an_official_day_creates_the_set_with_its_date_and_source(self):
        releases.accept(source("bandai_onepiece"), [signal("one-piece", "The Dominance of God", date(2026, 11, 20), code="OP-18")], now=NOW)
        product_set = ProductSet.objects.get(game=self.op)
        self.assertEqual((product_set.name, product_set.code, product_set.release_date, product_set.release_date_source),
                         ("The Dominance of God", "OP-18", date(2026, 11, 20), "bandai_onepiece"))
        row = Release.objects.get()
        self.assertEqual((row.status, row.product_set, row.official), (Release.Status.ACCEPTED, product_set, True))

    def test_a_lone_community_source_only_waits(self):
        releases.accept(source("tcgdex_sets"), [signal("pokemon", "Delta Reign", date(2026, 11, 6))], now=NOW)
        self.assertFalse(ProductSet.objects.exists())
        self.assertEqual(Release.objects.get().status, Release.Status.PENDING)

    def test_two_community_sources_agreeing_on_the_day_add_the_set(self):
        releases.accept(source("tcgdex_sets"), [signal("pokemon", "Delta Reign", date(2026, 11, 6))], now=NOW)
        releases.accept(source("swudb_sets"), [signal("pokemon", "Delta Reign", date(2026, 11, 6))], now=NOW)
        product_set = ProductSet.objects.get()
        self.assertEqual(product_set.release_date, date(2026, 11, 6))
        self.assertEqual(set(Release.objects.values_list("status", flat=True)), {Release.Status.ACCEPTED})
        self.assertEqual(set(Release.objects.values_list("product_set", flat=True)), {product_set.pk})

    def test_community_sources_a_day_apart_publish_nothing(self):
        releases.accept(source("tcgdex_sets"), [signal("pokemon", "Delta Reign", date(2026, 11, 6))], now=NOW)
        releases.accept(source("swudb_sets"), [signal("pokemon", "Delta Reign", date(2026, 11, 7))], now=NOW)
        self.assertFalse(ProductSet.objects.exists())

    def test_a_month_only_date_never_reaches_a_set(self):
        releases.accept(source("bandai_onepiece"), [signal("one-piece", "Premium Booster", date(2026, 11, 1), MONTH, "PRB-03")], now=NOW)
        product_set = ProductSet.objects.get()
        self.assertIsNone(product_set.release_date)
        self.assertEqual(Release.objects.get().precision, MONTH)
        releases.accept(source("tcgdex_sets"), [signal("pokemon", "Delta Reign", date(2026, 11, 1), MONTH)], now=NOW)
        releases.accept(source("swudb_sets"), [signal("pokemon", "Delta Reign", date(2026, 11, 1), MONTH)], now=NOW)
        self.assertFalse(ProductSet.objects.filter(game=self.pokemon).exists())

    def test_the_owners_date_is_never_overwritten(self):
        owned = make_set(self.op, name="The Dominance of God", slug="the-dominance-of-god", code="OP-18",
                         release_date=date(2026, 11, 21), release_date_source="owner")
        by_hand = make_set(self.op, name="Older Set", slug="older-set", code="OP-16", release_date=date(2026, 6, 1))
        releases.accept(source("bandai_onepiece"), [
            signal("one-piece", "The Dominance of God", date(2026, 11, 20), code="OP-18"),
            signal("one-piece", "Older Set", date(2026, 6, 5), code="OP-16"),
        ], now=NOW)
        owned.refresh_from_db()
        by_hand.refresh_from_db()
        self.assertEqual((owned.release_date, owned.release_date_source), (date(2026, 11, 21), "owner"))
        # A date there before any source was read was put there by hand: it stays too.
        self.assertEqual(by_hand.release_date, date(2026, 6, 1))
        self.assertEqual(ProductSet.objects.count(), 2)

    def test_a_source_may_move_a_date_it_set_itself(self):
        releases.accept(source("scryfall_sets"), [signal("magic-the-gathering", "Star Trek", date(2026, 11, 13), code="TRK")], now=NOW)
        releases.accept(source("scryfall_sets"), [signal("magic-the-gathering", "Star Trek", date(2026, 11, 20), code="TRK")], now=NOW)
        self.assertEqual(ProductSet.objects.get().release_date, date(2026, 11, 20))

    def test_a_game_without_a_game_row_is_skipped_and_never_added(self):
        stats = releases.accept(source("lorcast_sets"), [signal("lorcana", "Hyperia City", date(2026, 10, 16), code="14")], now=NOW)
        self.assertEqual(stats["skipped, game not added"], 1)
        self.assertFalse(Release.objects.exists())
        self.assertFalse(ProductSet.objects.exists())
        self.assertFalse(type(self.pokemon).objects.filter(slug="lorcana").exists())

    def test_a_set_released_long_ago_is_recorded_and_changes_nothing(self):
        releases.accept(source("scryfall_sets"), [signal("magic-the-gathering", "Final Fantasy", date(2025, 6, 13), code="FIN")], now=NOW)
        self.assertTrue(Release.objects.exists())
        self.assertFalse(ProductSet.objects.exists())

    def test_no_product_is_ever_created(self):
        releases.accept(source("bandai_onepiece"), [signal("one-piece", "The Dominance of God", date(2026, 11, 20), code="OP-18")], now=NOW)
        self.assertFalse(Product.objects.exists())


class FakeFetch:
    """Answers from saved files by address, and remembers every request with its headers."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        for part, body in self.answers.items():
            if part in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise releases.SourceError(f"{url} answered 404")


class ScanTests(TestCase):
    def setUp(self):
        self.magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        self.pokemon = make_game()
        self.sleeps = []

    def scan(self, name, fetch, now=NOW, **kwargs):
        return releases.scan(name, fetch=fetch, now=now, sleep=self.sleeps.append, **kwargs)

    def test_scryfall_is_read_with_its_headers_and_not_twice_in_a_day(self):
        fetch = FakeFetch({"api.scryfall.com/sets": fixture("scryfall_sets.json")})
        result = self.scan("scryfall_sets", fetch)
        self.assertTrue(result.ok, result)
        url, headers = fetch.calls[0]
        self.assertTrue(headers["User-Agent"].startswith("RipRaptor release check"))
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(ProductSet.objects.get(code="NAU").release_date, date(2027, 2, 5))
        self.assertTrue(self.scan("scryfall_sets", fetch, now=NOW + timedelta(hours=23)).skipped)
        self.assertEqual(len(fetch.calls), 1)
        self.scan("scryfall_sets", fetch, now=NOW + timedelta(hours=24, minutes=1))
        self.assertEqual(len(fetch.calls), 2)
        state = ReleaseSourceState.objects.get(name="scryfall_sets")
        self.assertEqual((state.last_error, state.last_ok_at), ("", NOW + timedelta(hours=24, minutes=1)))

    def test_fabtcg_is_asked_with_a_browser_user_agent(self):
        make_game(name="Flesh and Blood", slug="flesh-and-blood")
        fetch = FakeFetch({"fabtcg.com/coming-soon": fixture("fabtcg_coming_soon.html")})
        self.scan("fabtcg_coming_soon", fetch)
        self.assertTrue(fetch.calls[0][1]["User-Agent"].startswith("Mozilla/5.0"))
        self.assertEqual(ProductSet.objects.get(name="Smash Palace: Chorus of Steel").release_date, date(2026, 10, 30))

    def test_a_raising_parser_leaves_its_error_and_writes_no_rows(self):
        for body in (b"<html>Down for maintenance</html>", b'{"object": "list", "data": []}'):
            ReleaseSourceState.objects.all().delete()
            result = self.scan("scryfall_sets", FakeFetch({"scryfall": body}))
            self.assertFalse(result.ok)
            self.assertTrue(ReleaseSourceState.objects.get(name="scryfall_sets").last_error)
            self.assertFalse(Release.objects.exists())
            self.assertFalse(ProductSet.objects.exists())

    def test_an_unreachable_source_leaves_its_error(self):
        result = self.scan("scryfall_sets", FakeFetch({"scryfall": releases.SourceError("https://api.scryfall.com/sets answered 503")}))
        self.assertIn("503", ReleaseSourceState.objects.get(name="scryfall_sets").last_error)
        self.assertFalse(result.ok)

    def test_tcgdex_opens_at_most_40_sets_100_ms_apart_and_never_one_it_has_dated(self):
        listing = json.dumps([{"id": f"x{n:02d}", "name": f"Set {n}"} for n in range(60)]).encode()

        def detail(n):
            return json.dumps({"id": f"x{n:02d}", "name": f"Example Set {n}", "releaseDate": "2026-11-06",
                               "serie": {"id": "me"}}).encode()

        answers = {f"/v2/en/sets/x{n:02d}": detail(n) for n in range(60)}
        answers["/v2/en/sets?"] = listing
        fetch = FakeFetch(answers)
        self.scan("tcgdex_sets", fetch)
        self.assertEqual(len(fetch.calls), 1 + releases.TCGDEX_DETAILS)
        self.assertEqual(set(self.sleeps), {0.1})
        self.assertEqual(Release.objects.filter(source="tcgdex_sets").count(), 40)
        fetch.calls.clear()
        self.scan("tcgdex_sets", fetch, now=NOW + timedelta(hours=7))
        opened = [url for url, _h in fetch.calls if "/sets/x" in url]
        self.assertEqual(len(opened), 20)
        self.assertNotIn("https://api.tcgdex.net/v2/en/sets/x00", opened)

    def test_pokemon_news_adds_the_set_from_its_article(self):
        delta = "/uk/news/play-at-a-pokemon-tcg-mega-evolution-delta-reign-prerelease-event"
        fetch = FakeFetch({delta: fixture("pokemon_delta_reign.html"), "/uk/news/": fixture("pokemon_uk_news.html")})
        self.scan("pokemon_uk_news", fetch)
        product_set = ProductSet.objects.get(name="Delta Reign")
        self.assertEqual((product_set.release_date, product_set.release_date_source), (date(2026, 11, 6), "pokemon_uk_news"))
        self.assertEqual(self.sleeps, [1.0])
        # An article already read with its date is not opened again.
        fetch.calls.clear()
        self.scan("pokemon_uk_news", fetch, now=NOW + timedelta(hours=7))
        self.assertEqual(len(fetch.calls), 1)

    def test_web_pages_stop_at_25_a_day(self):
        make_game(name="One Piece Card Game", slug="one-piece")
        ReleaseSourceState.objects.create(name="dbs_fusion", pages_day=timezone.localtime(NOW).date(), pages_today=24)
        fetch = FakeFetch({"?page=2": fixture("bandai_onepiece_page2.html"), "onepiece": fixture("bandai_onepiece_page1.html")})
        result = self.scan("bandai_onepiece", fetch)
        self.assertEqual(len(fetch.calls), 1)   # page 2 waits for tomorrow
        self.assertTrue(result.ok)
        self.assertEqual(releases.pages_left(NOW), 0)
        make_game(name="Weiss Schwarz", slug="weiss-schwarz")
        result = self.scan("bushiroad_ws", FakeFetch({}))
        self.assertIn("web pages a day", result.skipped)
        self.assertGreater(ReleaseSourceState.objects.get(name="bushiroad_ws").next_at, NOW + timedelta(hours=6))

    def test_a_dry_run_writes_nothing(self):
        fetch = FakeFetch({"scryfall": fixture("scryfall_sets.json")})
        with mock.patch("catalogue.releases.fetch_url", fetch):
            out = StringIO()
            call_command("scan_releases", source="scryfall_sets", dry_run=True, stdout=out)
        self.assertIn("Nauctis: The Sunken Realm [NAU]: 2027-02-05", out.getvalue())
        self.assertFalse(Release.objects.exists())
        self.assertFalse(ReleaseSourceState.objects.exists())

    def test_the_command_reads_only_sources_that_are_due(self):
        for item in releases.SOURCES:
            ReleaseSourceState.objects.create(name=item.name, next_at=timezone.now() + timedelta(hours=1))
        fetch = FakeFetch({})
        with mock.patch("catalogue.releases.fetch_url", fetch):
            out = StringIO()
            call_command("scan_releases", stdout=out)
        self.assertEqual(fetch.calls, [])
        self.assertIn("skipped", out.getvalue())


class AttachSetsTests(TestCase):
    def setUp(self):
        self.pokemon = make_game()

    def product(self, game, name):
        return Product.objects.create(game=game, name=name, product_type=Product.Type.BOOSTER_BOX)

    def test_a_set_name_files_the_product(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        phoenix = make_set(ygo, name="Immortal Phoenix", slug="immortal-phoenix", code="")
        product = self.product(ygo, "Yu-Gi-Oh! Immortal Phoenix Booster Box (24 Packs)")
        self.assertEqual(releases.attach_sets(ygo), 1)
        product.refresh_from_db()
        self.assertEqual(product.product_set, phoenix)

    def test_series_words_alone_never_file_a_product(self):
        make_set(self.pokemon, name="Scarlet & Violet", slug="scarlet-violet", code="")
        etb = self.product(self.pokemon, "Scarlet & Violet Surging Sparks ETB")
        releases.attach_sets(self.pokemon)
        etb.refresh_from_db()
        self.assertIsNone(etb.product_set)
        sparks = make_set(self.pokemon, name="Surging Sparks", slug="surging-sparks", code="")
        releases.attach_sets(self.pokemon)
        etb.refresh_from_db()
        self.assertEqual(etb.product_set, sparks)

    def test_one_word_never_files_a_product(self):
        lorcana = make_game(name="Disney Lorcana", slug="lorcana")
        make_set(lorcana, name="Fabled", slug="fabled", code="")
        box = self.product(lorcana, "Fabled Booster Box")
        releases.attach_sets(lorcana)
        box.refresh_from_db()
        self.assertIsNone(box.product_set)

    def test_the_longest_matching_name_wins(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        make_set(magic, name="Star Trek", slug="star-trek", code="TRK")
        commander = make_set(magic, name="Star Trek Commander", slug="star-trek-commander", code="TRC")
        deck = self.product(magic, "Star Trek Commander Deck")
        releases.attach_sets(magic)
        deck.refresh_from_db()
        self.assertEqual(deck.product_set, commander)

    def test_a_whole_token_code_files_the_product(self):
        op = make_game(name="One Piece Card Game", slug="one-piece")
        dominance = make_set(op, name="The Dominance of God", slug="the-dominance-of-god", code="OP-18")
        box = self.product(op, "OP-18 Divine Rule Japanese Booster Box")
        other = self.product(op, "OP-180 Booster Box")
        releases.attach_sets(op)
        box.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(box.product_set, dominance)
        self.assertIsNone(other.product_set)

    def test_a_product_with_a_set_keeps_it(self):
        old = make_set(self.pokemon, name="Prismatic Evolutions", slug="prismatic-evolutions")
        make_set(self.pokemon, name="Surging Sparks", slug="surging-sparks", code="")
        product = make_product(old, name="Surging Sparks Booster Box")
        releases.attach_sets(self.pokemon)
        product.refresh_from_db()
        self.assertEqual(product.product_set, old)

    def test_adding_a_set_files_products_already_listed(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        product = self.product(ygo, "Yu-Gi-Oh! Magnificent Maestros Booster Box")
        releases.accept(source("ygoprodeck_sets"), [signal("yu-gi-oh", "Magnificent Maestros", date(2026, 11, 12), code="MAMS")], now=NOW)
        releases.accept(releases.Source("other", "Other", "yu-gi-oh", "", releases.JSON, False, 6, 0),
                        [signal("yu-gi-oh", "Magnificent Maestros", date(2026, 11, 12))], now=NOW)
        product.refresh_from_db()
        self.assertEqual(product.product_set.name, "Magnificent Maestros")

    def test_tidy_all_files_products(self):
        ygo = make_game(name="Yu-Gi-Oh!", slug="yu-gi-oh")
        make_set(ygo, name="Immortal Phoenix", slug="immortal-phoenix", code="")
        product = self.product(ygo, "Immortal Phoenix Booster Box")
        # A product no shop lists is removed by tidy_catalogue, so this one has a listing.
        make_listing(product, make_retailer("Harbour Games"), price="80.00")
        call_command("tidy_all", stdout=StringIO())
        product.refresh_from_db()
        self.assertEqual(product.product_set.name, "Immortal Phoenix")


def shopify_fetch(products):
    def fetch(url):
        if url.endswith("/meta.json"):
            return b'{"currency": "GBP"}'
        if "page=1" in url and "/collections/" not in url:
            return json.dumps({"products": products}).encode()
        if url.endswith("/collections.json?limit=250"):
            return b'{"collections": []}'
        return b'{"products": []}'
    return fetch


class ShopSignalTests(TestCase):
    def setUp(self):
        self.op = make_game(name="One Piece Card Game", slug="one-piece")
        self.pokemon = make_game()
        self.shop = make_retailer("Total Example", source_type=Retailer.Source.SHOPIFY, source_url="https://shop.example/")
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))

    def read(self, products):
        run = run_import(self.shop, fetch=shopify_fetch(products))
        self.assertTrue(run.ok, run.error)

    OP18 = {"title": "One Piece Card Game - OP-18 - Divine Rule - Japanese Booster Box (24 Packs)", "handle": "op18-jp",
            "tags": ["Pre-order"], "variants": [{"price": "89.99", "available": True, "barcode": ""}]}
    TICKET = {"title": "Pokémon - Mega Evolution: Delta Reign Pre-Release Event - Wednesday 6pm (28/10/26)",
              "handle": "delta-reign-prerelease-wed", "tags": [], "variants": [{"price": "25.00", "available": True}]}

    def test_a_preorder_with_a_new_code_writes_one_pending_shop_release(self):
        self.read([self.OP18])
        self.read([self.OP18])
        row = Release.objects.get()
        self.assertEqual((row.name, row.code, row.source, row.status, row.official, row.release_date),
                         ("OP-18", "OP-18", "shop:total-example", Release.Status.PENDING, False, None))
        self.assertEqual(row.note, self.OP18["title"])
        self.assertFalse(ProductSet.objects.exists())

    def test_a_code_a_set_already_carries_writes_nothing(self):
        make_set(self.op, name="The Dominance of God", slug="the-dominance-of-god", code="OP-18")
        self.read([self.OP18])
        self.assertFalse(Release.objects.exists())

    def test_marketplace_titles_are_not_shop_evidence(self):
        from .importers import Offer, apply_offers
        from .models import Listing

        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        offer = Offer(title=self.OP18["title"], url="https://www.ebay.co.uk/itm/1", price=89,
                      availability=Listing.Availability.PREORDER)
        apply_offers(ebay, [offer], complete=False)
        self.assertFalse(Release.objects.exists())

    def test_an_in_stock_product_with_a_code_writes_nothing(self):
        self.read([{**self.OP18, "tags": []}])
        self.assertFalse(Release.objects.exists())

    def test_a_prerelease_ticket_is_a_candidate_and_never_a_product(self):
        self.read([self.TICKET])
        row = Release.objects.get()
        self.assertEqual((row.game, row.name, row.status), (self.pokemon, "Delta Reign", Release.Status.PENDING))
        self.assertFalse(Product.objects.exists())
        response = self.client.get(reverse("checks"))
        self.assertContains(response, "Announced sets to check (1)")
        self.assertContains(response, "Delta Reign")

    def test_not_a_set_dismisses_for_good(self):
        self.read([self.OP18])
        row = Release.objects.get()
        self.client.post(reverse("checks"), {"action": "not_a_set", "release": row.pk})
        self.read([self.OP18])
        row = Release.objects.get()
        self.assertEqual(row.status, Release.Status.DISMISSED)
        self.assertContains(self.client.get(reverse("checks")), "Announced sets to check (0)")

    def test_add_set_creates_the_set_with_the_owners_name_and_date_and_files_products(self):
        self.read([self.OP18])
        row = Release.objects.get()
        product = Product.objects.create(game=self.op, name="OP-18 Booster Box", product_type=Product.Type.BOOSTER_BOX)
        when = timezone.localdate() + timedelta(days=40)
        self.client.post(reverse("checks"), {"action": "add_set", "release": row.pk, "name": "The Dominance of God",
                                             "date": when.isoformat()})
        product_set = ProductSet.objects.get()
        self.assertEqual((product_set.name, product_set.code, product_set.release_date, product_set.release_date_source),
                         ("The Dominance of God", "OP-18", when, "owner"))
        product.refresh_from_db()
        self.assertEqual(product.product_set, product_set)
        row.refresh_from_db()
        self.assertEqual(row.status, Release.Status.ACCEPTED)

    def test_add_set_prefills_the_name_unless_it_is_a_bare_code(self):
        self.read([self.OP18, self.TICKET])
        page = self.client.get(reverse("checks")).content.decode()
        self.assertIn('value="Delta Reign"', page)
        self.assertNotIn('name="name" maxlength="120" required value="OP-18"', page)


class ChecksPageTests(TestCase):
    def setUp(self):
        self.pokemon = make_game()
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.day = timezone.localdate() + timedelta(days=30)

    def test_a_two_day_disagreement_is_shown_and_use_this_date_applies_one(self):
        releases.accept(source("pokemon_uk_news"), [signal("pokemon", "Delta Reign", self.day)])
        releases.accept(source("tcgdex_sets"), [signal("pokemon", "Delta Reign", self.day + timedelta(days=2))])
        product_set = ProductSet.objects.get()
        self.assertEqual(product_set.release_date, self.day)
        response = self.client.get(reverse("checks"))
        self.assertContains(response, "Release dates to confirm (1)")
        self.assertContains(response, 'value="Use this date"', count=2)
        later = Release.objects.get(source="tcgdex_sets")
        self.client.post(reverse("checks"), {"action": "use_release_date", "release": later.pk})
        product_set.refresh_from_db()
        self.assertEqual((product_set.release_date, product_set.release_date_source), (later.release_date, "owner"))
        self.assertContains(self.client.get(reverse("checks")), "Release dates to confirm (0)")
        # The owner's answer stands when the sources read again.
        releases.accept(source("pokemon_uk_news"), [signal("pokemon", "Delta Reign", self.day)])
        product_set.refresh_from_db()
        self.assertEqual(product_set.release_date, later.release_date)

    def test_a_day_apart_is_not_a_disagreement(self):
        releases.accept(source("pokemon_uk_news"), [signal("pokemon", "Delta Reign", self.day)])
        releases.accept(source("tcgdex_sets"), [signal("pokemon", "Delta Reign", self.day + timedelta(days=1))])
        self.assertEqual(releases_checks().release_disagreements(), [])

    def test_a_source_silent_for_14_days_shows(self):
        ReleaseSourceState.objects.create(name="lorcast_sets", last_ok_at=timezone.now() - timedelta(days=15),
                                          last_error="https://api.lorcast.com/v0/sets answered 500")
        ReleaseSourceState.objects.create(name="swudb_sets", last_ok_at=timezone.now() - timedelta(days=2))
        response = self.client.get(reverse("checks"))
        self.assertContains(response, "Release sources not answering (1)")
        self.assertContains(response, "Lorcast")
        self.assertNotContains(response, "SWU-DB")

    def test_a_stale_row_is_not_acted_on(self):
        row = Release.objects.create(game=self.pokemon, name="Delta Reign", source="tcgdex_sets")
        releases.dismiss(row)
        response = self.client.post(reverse("checks"), {"action": "add_set", "release": row.pk, "name": "Delta Reign"}, follow=True)
        self.assertContains(response, "That row has changed since the page loaded")
        self.assertFalse(ProductSet.objects.exists())

    def test_the_lists_are_one_query_each(self):
        for n in range(3):
            Release.objects.create(game=self.pokemon, name=f"Example {n}", source="tcgdex_sets", release_date=self.day,
                                   precision=DAY)
            Release.objects.create(game=self.pokemon, name=f"Example {n}", source="swudb_sets",
                                   release_date=self.day + timedelta(days=5), precision=DAY)
        checks = releases_checks()
        with self.assertNumQueries(1):
            self.assertEqual(len(checks.release_candidates()), 6)
        with self.assertNumQueries(1):
            self.assertEqual(len(checks.release_disagreements()), 3)
        with self.assertNumQueries(1):
            checks.stale_release_sources()

    def test_owner_dates_typed_in_admin_are_marked_as_the_owners(self):
        product_set = make_set(self.pokemon, name="Delta Reign", slug="delta-reign", code="")
        self.client.post(reverse("admin:catalogue_productset_change", args=[product_set.pk]), {
            "game": self.pokemon.pk, "name": "Delta Reign", "slug": "delta-reign", "code": "",
            "release_date": self.day.isoformat(),
        })
        product_set.refresh_from_db()
        self.assertEqual((product_set.release_date, product_set.release_date_source), (self.day, "owner"))
        releases.accept(source("pokemon_uk_news"), [signal("pokemon", "Delta Reign", self.day + timedelta(days=3))])
        product_set.refresh_from_db()
        self.assertEqual(product_set.release_date, self.day)

    def test_the_admin_list_is_read_only_with_a_hide_action(self):
        row = Release.objects.create(game=self.pokemon, name="Delta Reign", source="tcgdex_sets")
        url = reverse("admin:catalogue_release_changelist")
        self.assertContains(self.client.get(url), "Delta Reign")
        self.client.post(url, {"action": "hide", "_selected_action": [row.pk]})
        row.refresh_from_db()
        self.assertEqual(row.status, Release.Status.DISMISSED)
        self.assertEqual(self.client.get(reverse("admin:catalogue_release_add")).status_code, 403)

    def test_the_insights_page_lists_every_source(self):
        ReleaseSourceState.objects.create(name="scryfall_sets", last_ok_at=timezone.now(), signals_found=7)
        ReleaseSourceState.objects.create(name="dbs_fusion", last_error="The answer held no sets.")
        response = self.client.get(reverse("insights"))
        self.assertContains(response, "Release sources")
        self.assertContains(response, "Flesh and Blood site")
        self.assertContains(response, "The answer held no sets.")


def releases_checks():
    from . import checks

    return checks


class WorkerReleaseTests(TestCase):
    def setUp(self):
        self.magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        self.now = timezone.now()

    def make_worker(self, fetch):
        return worker.Worker(clock=lambda: self.now, sleep=lambda s: None, release_fetch=fetch, threads=3)

    @override_settings(RIPRAPTOR_RELEASES=True)
    def test_the_reader_plans_one_source_at_a_time_and_reads_it(self):
        for item in releases.SOURCES:
            if item.name != "scryfall_sets":
                ReleaseSourceState.objects.create(name=item.name, next_at=self.now + timedelta(hours=3))
        fetch = FakeFetch({"scryfall": fixture("scryfall_sets.json")})
        reader = self.make_worker(fetch)
        tasks = [t for t in reader.plan(self.now) if t.kind == worker.RELEASE_SCAN]
        self.assertEqual([(t.source, t.retailer_id) for t in tasks], [("scryfall_sets", worker.RELEASE_KEY)])
        reader.run_once()
        self.assertEqual(len(fetch.calls), 1)
        self.assertTrue(ProductSet.objects.filter(code="NAU").exists())
        self.assertEqual([t for t in reader.plan(self.now) if t.kind == worker.RELEASE_SCAN], [])

    def test_off_in_tests_and_by_setting(self):
        self.assertEqual([t for t in self.make_worker(FakeFetch({})).plan(self.now) if t.kind == worker.RELEASE_SCAN], [])

    def test_a_scan_has_its_own_deadline(self):
        task = worker.Task(worker.RELEASE_SCAN, worker.RELEASE_KEY, "", 1.0, "Release source Scryfall", source="scryfall_sets")
        self.assertEqual(worker.deadline_for(task), worker.RELEASE_DEADLINE)


class ProductPagesTests(TestCase):
    """An accepted set changes the public pages only through its set: LatestDropsTests pin the rest."""

    def test_a_set_page_for_an_added_set_renders(self):
        game = make_game()
        releases.accept(source("pokemon_uk_news"), [signal("pokemon", "Delta Reign", timezone.localdate() + timedelta(days=20))])
        product_set = ProductSet.objects.get()
        self.assertEqual(self.client.get(product_set.get_absolute_url()).status_code, 200)
        self.assertEqual(product_set.game, game)
        self.assertNotIn(EM_DASH, product_set.name)
