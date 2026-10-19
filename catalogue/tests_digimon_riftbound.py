"""Digimon and Riftbound sets from their publishers' sites (catalogue/releases.py): the two parsers on saved
pages, the Riftbound reader, and the code and name rules that file these games' products."""

from datetime import date

from django.test import TestCase

from . import releases
from .models import Product, ProductSet, Release
from .testing import make_game, make_set
from .tests_releases import NOW, FakeFetch, fixture, found

DAY, MONTH = releases.DAY, releases.MONTH


def product(game, name, **kwargs):
    kwargs.setdefault("product_type", Product.Type.BOOSTER_BOX)
    return Product.objects.create(game=game, name=name, **kwargs)


def set_of(item):
    item.refresh_from_db()
    return item.product_set


def riftbound_fetch():
    return FakeFetch({
        "card-gallery": fixture("riftbound_gallery.html"),
        "news/announcements/the-vendetta-overview": fixture("riftbound_vendetta_overview.html"),
        "news/announcements/the-unleashed-overview": fixture("riftbound_unleashed_overview.html"),
        "/en-us/news/": fixture("riftbound_news.html"),
    })


class DigimonParserTests(TestCase):
    def setUp(self):
        self.signals = releases.parse_digimon(fixture("digimon_products.html"))

    def test_boosters_and_decks_with_codes_as_published(self):
        ignition = found(self.signals, "Ignition of X")
        self.assertEqual((ignition.game_slug, ignition.code), ("digimon", "BT-27"))
        self.assertEqual(ignition.url, "https://world.digimoncard.com/products/pack/ver27/")
        self.assertEqual(found(self.signals, "Exceed Apocalypse").code, "BT15")
        self.assertEqual(found(self.signals, "Ultimate Ancient Dragon").code, "ST-9")
        self.assertEqual(found(self.signals, "Fable Waltz").code, "ST19")
        self.assertEqual(found(self.signals, "Resurgence Booster").code, "RB01")
        self.assertEqual(len(self.signals), 69)

    def test_the_kind_of_booster_is_not_part_of_the_name(self):
        self.assertEqual(found(self.signals, "Chivalrous XIII").code, "EX-13")
        self.assertEqual(found(self.signals, "Digimon Generation").code, "AD-01")
        self.assertEqual(found(self.signals, "Distancia Cero").code, "LM-09")
        self.assertEqual(found(self.signals, "Hackers' Slumber").code, "BT-23")
        self.assertEqual(found(self.signals, "Digimon Alysion Yukinamon").code, "ST-26")

    def test_digimon_keep_their_published_capitals(self):
        self.assertEqual(found(self.signals, "UlforceVeedramon").code, "ST-8")
        self.assertEqual(found(self.signals, "RagnaLoardmon").code, "ST-13")
        self.assertEqual(found(self.signals, "Beelzemon").code, "ST14")

    def test_the_release_date_never_the_pre_release_or_us_shipping_date(self):
        self.assertEqual(found(self.signals, "Ignition of X").release_date, date(2026, 12, 11))
        self.assertEqual(found(self.signals, "Distancia Cero").release_date, date(2026, 11, 27))
        self.assertEqual(found(self.signals, "Gaia Red").release_date, date(2021, 1, 29))
        knight = found(self.signals, "Another Knight")
        self.assertEqual((knight.release_date, knight.precision), (date(2026, 3, 1), MONTH))

    def test_a_run_keeps_its_code_and_other_products_are_left_out(self):
        self.assertEqual(found(self.signals, "Special Booster Ver.2.0").code, "BT18-19")
        self.assertEqual(found(self.signals, "Release Special Booster Ver.1.5").code, "BT01-03")
        names = " ".join(s.name for s in self.signals).lower()
        for other in ("sleeves", "binder", "heroines", "gift box", "double pack", "adventure box", "time stranger set"):
            self.assertNotIn(other, names)
        self.assertFalse([s for s in self.signals if s.code.startswith(("PB", "GB", "DP", "AB", "PD"))])

    def test_an_empty_page_is_an_error(self):
        with self.assertRaises(releases.NothingFound):
            releases.parse_digimon(b"<html><body>Maintenance</body></html>")


class RiftboundParserTests(TestCase):
    def test_the_gallery_lists_every_set_with_its_code(self):
        self.assertEqual(releases.parse_riftbound_sets(fixture("riftbound_gallery.html")), [
            ("OGN", "Origins"), ("OGS", "Proving Grounds"), ("RAD", "Radiance"), ("SFD", "Spiritforged"),
            ("UNL", "Unleashed"), ("VEN", "Vendetta")])
        with self.assertRaises(releases.NothingFound):
            releases.parse_riftbound_sets(b"<html><body>No data</body></html>")

    def test_the_news_page_lines_date_some_sets(self):
        news = releases.parse_riftbound_news(fixture("riftbound_news.html"))
        self.assertEqual(news[0].published, date(2026, 10, 15))
        names = ["Origins", "Proving Grounds", "Radiance", "Spiritforged", "Unleashed", "Vendetta"]

        def first_date(name):
            for article in news:
                if releases.names_set(article, name, names):
                    when, _ = releases.article_date(f"{article.title}. {article.text}", article.published)
                    if when:
                        return when
            return None

        self.assertEqual(first_date("Origins"), date(2025, 10, 31))
        # 'releases on 2/13', in a line from 13 January 2026.
        self.assertEqual(first_date("Spiritforged"), date(2026, 2, 13))
        self.assertEqual(first_date("Radiance"), date(2026, 10, 23))
        self.assertIsNone(first_date("Vendetta"))

    def test_an_overview_gives_the_english_release(self):
        vendetta = releases.parse_riftbound_article(fixture("riftbound_vendetta_overview.html"))
        self.assertEqual(vendetta.published, date(2026, 7, 7))
        self.assertEqual(releases.article_date(vendetta.text, vendetta.published), (date(2026, 7, 31), DAY))
        unleashed = releases.parse_riftbound_article(fixture("riftbound_unleashed_overview.html"))
        # The Chinese release was 4/10; the English one 5/8.
        self.assertEqual(releases.article_date(unleashed.text, unleashed.published), (date(2026, 5, 8), DAY))

    def test_dates_in_words(self):
        cases = [
            ("Release : July 31 for English and Chinese; Oct. 23 for French", date(2026, 7, 7), date(2026, 7, 31)),
            ("Release: October 23, 2026 English, French, and Chinese", None, date(2026, 10, 23)),
            ("Riftbound launches in English on October 31, 2025.", None, date(2025, 10, 31)),
            ("Spiritforged releases on 1/10", date(2026, 12, 20), date(2027, 1, 10)),
            ("Spiritforged launches in December 2025 for China and February 2026 in English.", None, None),
            ("French Origins releases May 29th", date(2026, 4, 7), None),
            ("The Chinese language release will be on 4/10", date(2026, 3, 16), None),
            ("Radiance drops October 23", None, None),   # no year and no article date
            ("Core Rules: Radiance Patch Notes (Effective October 16, 2026)", None, None),
        ]
        for text, published, expected in cases:
            self.assertEqual(releases.article_date(text, published)[0], expected, text)


class RiftboundReadTests(TestCase):
    def setUp(self):
        self.riftbound = make_game(name="Riftbound", slug="riftbound")

    def test_a_read_dates_each_set_from_the_news_or_its_overview(self):
        fetch = riftbound_fetch()
        result = releases.scan("riftbound_sets", fetch=fetch, now=NOW, sleep=lambda s: None)
        self.assertTrue(result.ok, result)
        self.assertEqual([url for url, _ in fetch.calls], [
            "https://riftbound.leagueoflegends.com/en-us/card-gallery/",
            "https://riftbound.leagueoflegends.com/en-us/news/",
            "https://riftbound.leagueoflegends.com/en-us/news/announcements/the-unleashed-overview",
            "https://riftbound.leagueoflegends.com/en-us/news/announcements/the-vendetta-overview",
        ])
        dated = {s.name: (s.code, s.release_date) for s in result.signals}
        self.assertEqual(dated, {
            "Origins": ("OGN", date(2025, 10, 31)), "Proving Grounds": ("OGS", None),
            "Radiance": ("RAD", date(2026, 10, 23)), "Spiritforged": ("SFD", date(2026, 2, 13)),
            "Unleashed": ("UNL", date(2026, 5, 8)), "Vendetta": ("VEN", date(2026, 7, 31))})
        # A news line that links off the site (a video, the merch store) points the row at the news page instead.
        urls = {s.name: s.url for s in result.signals}
        self.assertEqual(urls["Radiance"], "https://riftbound.leagueoflegends.com/en-us/news/")
        self.assertEqual(urls["Spiritforged"], "https://riftbound.leagueoflegends.com/en-us/news/")
        self.assertEqual(urls["Vendetta"],
                         "https://riftbound.leagueoflegends.com/en-us/news/announcements/the-vendetta-overview")
        self.assertTrue(all(releases.on_riftbound_site(url) for url in urls.values()), urls)
        self.assertTrue(all(row.official for row in Release.objects.filter(source="riftbound_sets")))
        # The next read opens no overview again, and takes no date away.
        later = riftbound_fetch()
        releases.ReleaseSourceState.objects.filter(name="riftbound_sets").update(next_at=None)
        releases.scan("riftbound_sets", fetch=later, now=NOW, sleep=lambda s: None)
        self.assertEqual(len(later.calls), 2)
        self.assertEqual(Release.objects.get(source="riftbound_sets", name="Vendetta").release_date, date(2026, 7, 31))

    def test_out_of_pages_still_lists_the_sets(self):
        fetch = riftbound_fetch()
        source = releases.BY_NAME["riftbound_sets"]
        pages = iter([None, None])

        def take_page():
            if next(pages, "out") == "out":
                raise releases.OutOfPages()

        reader = releases.Reader(source, fetch, lambda s: None, take_page=take_page)
        signals = releases.read_riftbound(source, reader, None, releases.Known())
        self.assertEqual(len(signals), 6)
        self.assertIsNone(found(signals, "Vendetta").release_date)


class DigimonFilingTests(TestCase):
    def setUp(self):
        self.digimon = make_game(name="Digimon Card Game", slug="digimon")

    def test_codes_with_spaces_dashes_or_zeros_are_one_code(self):
        dragon = make_set(self.digimon, name="Ultimate Ancient Dragon", slug="ultimate-ancient-dragon", code="ST-9")
        exceed = make_set(self.digimon, name="Exceed Apocalypse", slug="exceed-apocalypse", code="BT15")
        names = {"Starter Deck Ancient Dragon (ST9)": dragon, "ST09 Starter Deck": dragon, "ST-9 Deck": dragon,
                 "Exceed Apocalypse BT 15 Booster Box": exceed, "BT-15 Booster Pack": exceed}
        items = {name: product(self.digimon, name) for name in names}
        releases.attach_sets(self.digimon)
        for name, expected in names.items():
            self.assertEqual(set_of(items[name]), expected, name)

    def test_a_japanese_box_goes_only_under_a_japanese_set(self):
        make_set(self.digimon, name="Exceed Apocalypse", slug="exceed-apocalypse", code="BT15")
        japanese = product(self.digimon, "Exceed Apocalypse BT 15 Japanese Booster Box")
        releases.attach_sets(self.digimon)
        self.assertIsNone(set_of(japanese))
        own = make_set(self.digimon, name="Exceed Apocalypse (Japanese)", slug="exceed-apocalypse-japanese", code="BT15")
        releases.attach_sets(self.digimon)
        self.assertEqual(set_of(japanese), own)

    def test_a_special_booster_goes_only_under_the_set_of_its_exact_name(self):
        make_set(self.digimon, name="Secret Crisis", slug="secret-crisis", code="BT18")
        make_set(self.digimon, name="Over the X", slug="over-the-x", code="BT19")
        two = product(self.digimon, "Special Booster Ver.2.0 (BT18 19) Booster Box")
        half = product(self.digimon, "Special Booster Ver.2.5 (BT19 20) Booster Box")
        releases.attach_sets(self.digimon)
        self.assertIsNone(set_of(two))
        self.assertEqual(releases.set_code_keys("BT18-19", "digimon"), frozenset())
        special = make_set(self.digimon, name="Special Booster Ver.2.0", slug="special-booster-ver20", code="BT18-19")
        releases.attach_sets(self.digimon)
        self.assertEqual(set_of(two), special)
        self.assertIsNone(set_of(half))

    def test_limited_packs_by_code_and_premium_bandai_sets_never(self):
        cero = make_set(self.digimon, name="Distancia Cero", slug="distancia-cero", code="LM-09")
        stranger = make_set(self.digimon, name="Time Stranger", slug="time-stranger", code="BT-24")
        make_set(self.digimon, name="Digimon Liberator", slug="digimon-liberator", code="EX07")
        pack = product(self.digimon, "LM09 Limited Card Pack Distancia Cero Booster Pack")
        box = product(self.digimon, "BT24 Time Stranger Booster Box")
        premium = [product(self.digimon, name) for name in (
            "Digimon Story Time Stranger Set PB 25", "Digimon Liberator Debuggers Set", "Gift Box 2023 GB03")]
        releases.attach_sets(self.digimon)
        self.assertEqual(set_of(pack), cero)
        self.assertEqual(set_of(box), stranger)
        for item in premium:
            self.assertIsNone(set_of(item), item.name)

    def test_a_backfill_from_the_publishers_page_files_the_back_catalogue(self):
        names = ["BT27 Ignition of X Booster Pack", "BT14 Blast Ace Booster Box", "Starter Deck (ST9)",
                 "EX14 Malevolent VII Booster Pack", "Exceed Apocalypse BT 15 Japanese Booster Box",
                 "Special Booster Ver.2.0 (BT18 19) Booster Box", "Starter Deck Digimon Data Squad"]
        items = {name: product(self.digimon, name) for name in names}
        fetch = FakeFetch({"digimoncard.com/products": fixture("digimon_products.html")})
        result = releases.backfill("digimon_products", fetch=fetch, now=NOW, sleep=lambda s: None)
        self.assertTrue(result.ok, result)
        filed = {name: (set_of(item).name if set_of(item) else None) for name, item in items.items()}
        self.assertEqual(filed, {
            "BT27 Ignition of X Booster Pack": "Ignition of X", "BT14 Blast Ace Booster Box": "Blast Ace",
            "Starter Deck (ST9)": "Ultimate Ancient Dragon", "EX14 Malevolent VII Booster Pack": None,
            "Exceed Apocalypse BT 15 Japanese Booster Box": None,
            "Special Booster Ver.2.0 (BT18 19) Booster Box": "Special Booster Ver.2.0",
            "Starter Deck Digimon Data Squad": "Digimon Data Squad"})
        blast = ProductSet.objects.get(name="Blast Ace")
        self.assertEqual((blast.code, blast.release_date, blast.release_date_source),
                         ("BT14", date(2023, 11, 17), "digimon_products"))
        # A run's code is kept as published but files nothing by itself.
        self.assertEqual(ProductSet.objects.get(name="Special Booster Ver.2.0").code, "BT18-19")
        # EX14 is not on the English site: it waits under Announced sets to check.
        self.assertEqual(releases.raise_name_candidates(self.digimon, dry_run=True), ["EX14"])


class RiftboundFilingTests(TestCase):
    def setUp(self):
        self.riftbound = make_game(name="Riftbound", slug="riftbound")

    def test_three_letter_codes_are_codes_only_for_riftbound(self):
        self.assertEqual(releases.set_code_keys("OGN", "riftbound"), {"ogn"})
        self.assertEqual(releases.set_code_keys("OGN", "magic-the-gathering"), frozenset())
        origins = make_set(self.riftbound, name="Origins", slug="origins", code="OGN")
        make_set(self.riftbound, name="Spiritforged", slug="spiritforged", code="SFD")
        display = product(self.riftbound, "OGN Booster Display English")
        chinese = product(self.riftbound, "League of Legends Origins OGN 01 Chinese Slim Booster Box")
        jumbo = product(self.riftbound, "Spiritforged SFD BD02 Chinese Jumbo Booster Pack")
        releases.attach_sets(self.riftbound)
        self.assertEqual(set_of(display), origins)
        self.assertIsNone(set_of(chinese))
        self.assertIsNone(set_of(jumbo))

    def test_shop_numbers_are_not_codes_but_do_not_hide_the_name(self):
        unleashed = make_set(self.riftbound, name="Unleashed", slug="unleashed", code="UNL")
        origins = make_set(self.riftbound, name="Origins", slug="origins", code="OGN")
        names = {"Unleashed RB 03 English Booster Pack": unleashed,
                 "League of Legends Trading Card Game Unleashed Booster Box": unleashed,
                 "Set 1 Origins Booster Box": origins, "RB 03 Booster Box": None,
                 "Vendetta Zed Vs Shen RB 04SU01 English Showdown Deck": None}
        items = {name: product(self.riftbound, name) for name in names}
        releases.attach_sets(self.riftbound)
        for name, expected in names.items():
            self.assertEqual(set_of(items[name]), expected, name)

    def test_a_backfill_from_the_publishers_site_files_products(self):
        names = ["Set 1 Origins Booster Box", "Spiritforged Booster Box", "Unleashed RB 03 English Booster Pack",
                 "Set 4: Vendetta Booster Pack", "Origins Proving Grounds Box Set", "Set 06 Legacy Booster Pack",
                 "League of Legends Origins OGN 01 Chinese Jumbo Booster Box"]
        items = {name: product(self.riftbound, name) for name in names}
        result = releases.backfill("riftbound_sets", fetch=riftbound_fetch(), now=NOW, sleep=lambda s: None)
        self.assertTrue(result.ok, result)
        filed = {name: (set_of(item).name if set_of(item) else None) for name, item in items.items()}
        self.assertEqual(filed, {
            "Set 1 Origins Booster Box": "Origins", "Spiritforged Booster Box": "Spiritforged",
            "Unleashed RB 03 English Booster Pack": "Unleashed", "Set 4: Vendetta Booster Pack": "Vendetta",
            "Origins Proving Grounds Box Set": "Proving Grounds", "Set 06 Legacy Booster Pack": None,
            "League of Legends Origins OGN 01 Chinese Jumbo Booster Box": None})
        origins = ProductSet.objects.get(name="Origins")
        self.assertEqual((origins.code, origins.release_date, origins.release_date_source),
                         ("OGN", date(2025, 10, 31), "riftbound_sets"))
        self.assertFalse(ProductSet.objects.filter(name__icontains="legacy").exists())
