"""Sports cards filed by product line (catalogue/sports.py). The owner: "can we not class these as sports cards and
then classify them by their proper product line as it feels they are just mashed up together." The names below
are shops' own, from the live catalogue."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from . import releases, sports
from .classify import find_game
from .models import Game, Product, ProductSet
from .testing import make_game, make_set


def product(game, name):
    return Product.objects.create(game=game, name=name, product_type=Product.Type.BOOSTER_BOX)


class LineTests(TestCase):
    def test_brand_line_and_league_whatever_order_the_shop_writes_them(self):
        for name, line in (
            ("Panini 2023/24 Prizm Premier League Football (Soccer) Hobby Box", "Panini Prizm Premier League"),
            ("Panini 2021/22 Premier League Prizm Football (Soccer) Blaster Box", "Panini Prizm Premier League"),
            ("2023/24 Prizm Premier League Football (Soccer) International Hobby Box", "Panini Prizm Premier League"),
            ("Panini 2022/23 Select English Premier League Football (Soccer) Tmall Hobby Box",
             "Panini Select Premier League"),
            ("Panini 2024 Donruss Optic American Football (NFL) Mega Box", "Panini Donruss Optic NFL"),
            ("Panini 2024 Donruss American Football (NFL) Winter Tin", "Panini Donruss NFL"),
            ("Panini 2023/24 Donruss Football (Soccer) Hobby Box", "Panini Donruss Football"),
            ("Topps Match Attax Extra 2025/26 The X Tin", "Topps Match Attax Extra"),
            ("Topps Match Attax UCC 2026/27 Booster Tin 1 Strike Team", "Topps Match Attax UEFA Club Competitions"),
            ("Topps UEFA Champion's League Football (Soccer) Match Attax 2023/24 Mega Tin",
             "Topps Match Attax UEFA Club Competitions"),
            ("Topps 2021/22 Chrome Merlin UEFA Champions League Football (Soccer) Hobby Box",
             "Topps Merlin Chrome UEFA Club Competitions"),
            ("Topps 2024/25 Chrome Merlin UEFA Club Competitions Football (Soccer) Hobby Box",
             "Topps Merlin Chrome UEFA Club Competitions"),
            ("Topps UEFA Euro 2024 Football (Soccer) Match Attax Booster Pack (8 Cards)", "Topps Match Attax Euro"),
            ("Topps 2023/24 Chrome Basketball (NBA) Value Box", "Topps Chrome NBA"),
            ("Topps 2024 Pro Debut Baseball (MLB) Jumbo Box", "Topps Pro Debut MLB"),
            ("Panini Donruss WNBA Basketball 2026 Hobby Box", "Panini Donruss WNBA"),
            ("Panini 2023 Chronicles Racing (NASCAR) Hobby Box", "Panini Chronicles NASCAR"),
            ("Topps UWCL Women's Chrome 2026 Sapphire Hobby Box", "Topps Chrome Sapphire Women's Champions League"),
            ("Topps Arsenal 2026/27 Collector Tin", "Topps Collector Tins"),
            ("Topps Manchester City 2025/26 Collectors Tin", "Topps Collector Tins"),
            ("Futera Club Unique Real Madrid 2025/26 Hobby Box", "Futera Club Unique"),
            ("Panini 2025/26 Noir Road To World Cup Football (Soccer) Hobby Box", "Panini Noir Road to World Cup"),
            ("Panini 2023 FIFA Women's World Cup Adrenalyn XL Football (Soccer) Booster Box",
             "Panini Adrenalyn XL Women's World Cup"),
            ("Panini 2023/24 Premier League Adrenalyn XL PLUS Football (Soccer) Pocket Tin",
             "Panini Adrenalyn XL Plus Premier League"),
            ("Panini Football EFL 2025/26 Official Trading Cards Premium Box", "Panini EFL"),
            ("Topps 2026 Premier League Debut Edition Football (Soccer) Hall of Fame Super Tin (Assorted)",
             "Topps Debut Edition Premier League"),
            ("Topps Premier League Debut Edition 2026 Golden Boot Tin", "Topps Debut Edition Premier League"),
        ):
            self.assertEqual(sports.set_name(name), line, name)

    def test_a_line_that_names_its_brand_needs_no_brand_and_an_everyday_word_does(self):
        self.assertEqual(sports.set_name("Premier League 2022/23 Adrenalyn XL Football (Soccer) Classic Tin"),
                         "Panini Adrenalyn XL Premier League")
        self.assertEqual(sports.set_name("Match Attax 101: Road to UEFA Nations League Final Mega Tin"),
                         "Topps Match Attax 101 Road to Nations League Final")
        self.assertEqual(sports.set_name("Panini 2022 One Football (NFL) Hobby Box"), "Panini One NFL")
        # "One" and "Limited" are lines only beside Panini's name; here they are a shop's words.
        self.assertIsNone(sports.set_name("One Limited Edition Football Hobby Box"))

    def test_a_name_that_names_no_line_is_left_unfiled_rather_than_guessed(self):
        for name in ("DAKA Netherlands 2024 Regular Hobby Box", "Futera 1882 Hobby Box",
                     "Leaf Trading Cards 2023 Leaf Football (Soccer) Blaster Box",
                     "Panini The Best of England Football (Soccer) Collection Box"):
            self.assertIsNone(sports.set_name(name), name)

    def test_basketball_baseball_and_racing_are_sports_cards_too(self):
        self.assertEqual(find_game("Panini 2023/24 Mosaic Basketball (NBA) International Hobby Box"), sports.GAME)
        self.assertEqual(find_game("Topps 2024 Finest Baseball (MLB) Hobby Box"), sports.GAME)
        self.assertEqual(find_game("Panini 2024 Donruss Racing (NASCAR) Hobby Box"), sports.GAME)
        self.assertEqual(find_game("Pokemon Basketball Pikachu Booster Box"), "pokemon")


class FilingTests(TestCase):
    def setUp(self):
        self.game = make_game(name="Football cards", short_name="Football", slug=sports.GAME)
        self.hobby = product(self.game, "Panini 2023/24 Prizm Premier League Football (Soccer) Hobby Box")
        self.blaster = product(self.game, "Panini 2021/22 Premier League Prizm Football (Soccer) Blaster Box")
        self.nfl = product(self.game, "Panini 2024 Prizm American Football (NFL) Hobby Box")
        self.unknown = product(self.game, "DAKA Netherlands 2024 Regular Hobby Box")

    def set_of(self, item):
        item.refresh_from_db()
        return item.product_set

    def test_each_line_gets_one_set_holding_every_season(self):
        self.assertEqual(releases.attach_sets(self.game), 3)
        line = self.set_of(self.hobby)
        self.assertEqual((line.name, line.slug, line.code, line.release_date),
                         ("Panini Prizm Premier League", "panini-prizm-premier-league", "", None))
        self.assertEqual(self.set_of(self.blaster), line)
        self.assertEqual(self.set_of(self.nfl).name, "Panini Prizm NFL")
        self.assertIsNone(self.set_of(self.unknown))
        self.assertEqual(ProductSet.objects.filter(game=self.game).count(), 2)
        # Again: nothing more to file and no set added twice.
        self.assertEqual(releases.attach_sets(self.game), 0)
        self.assertEqual(ProductSet.objects.filter(game=self.game).count(), 2)

    def test_a_set_the_owner_renamed_keeps_its_products_and_a_product_they_moved_stays(self):
        renamed = make_set(self.game, name="Prizm Premier League (Panini)", slug="panini-prizm-premier-league", code="")
        elsewhere = make_set(self.game, name="Gifts", slug="gifts", code="")
        Product.objects.filter(pk=self.nfl.pk).update(product_set=elsewhere)
        releases.attach_sets(self.game, product_ids=[self.hobby.pk, self.nfl.pk])
        self.assertEqual(self.set_of(self.hobby), renamed)
        self.assertEqual(self.set_of(self.nfl), elsewhere)
        self.assertIsNone(self.set_of(self.blaster))   # not asked for

    def test_other_games_are_filed_by_their_release_rules_as_before(self):
        pokemon = make_game()
        make_set(pokemon, name="Surging Sparks", slug="surging-sparks", code="SSP")
        item = product(pokemon, "Panini Prizm Premier League Surging Sparks Booster Box")
        releases.attach_sets(pokemon)
        self.assertEqual(self.set_of(item).name, "Surging Sparks")

    def test_the_install_step_moves_the_game_to_its_sports_address_and_name_once_and_files_its_products(self):
        from datetime import date

        from .models import DailyPageView

        Game.objects.filter(pk=self.game.pk).update(slug="football")
        views = DailyPageView.objects
        views.create(date=date(2026, 10, 20), kind=DailyPageView.Kind.GAME, key="football", hits=7)
        views.create(date=date(2026, 10, 21), kind=DailyPageView.Kind.GAME, key="football", hits=5)
        views.create(date=date(2026, 10, 21), kind=DailyPageView.Kind.GAME, key=sports.GAME, hits=2)
        out = StringIO()
        call_command("file_sports_cards", stdout=out)
        self.assertEqual(out.getvalue(), "Sports cards: its address is now /games/sports-cards/.\n"
                                         "Sports cards: its name is now Sports cards.\n"
                                         "3 sports card products filed under their product lines.\n")
        self.game.refresh_from_db()
        self.assertEqual((self.game.name, self.game.short_name, self.game.slug),
                         ("Sports cards", "Sports", "sports-cards"))
        # Its page views move with it, so the dashboard keeps its history.
        self.assertEqual(sorted(views.filter(kind=DailyPageView.Kind.GAME).values_list("date", "key", "hits")),
                         [(date(2026, 10, 20), sports.GAME, 7), (date(2026, 10, 21), sports.GAME, 7)])
        # A name the owner gives it in Admin stays.
        Game.objects.filter(pk=self.game.pk).update(name="Sport and football cards")
        out = StringIO()
        call_command("file_sports_cards", stdout=out)
        self.assertEqual(out.getvalue(), "0 sports card products filed under their product lines.\n")
        self.assertEqual(Game.objects.get(pk=self.game.pk).name, "Sport and football cards")

    def test_an_import_between_the_new_code_and_the_rename_finds_the_game_at_its_old_address(self):
        Game.objects.filter(pk=self.game.pk).update(slug="football")
        self.assertEqual(Game.objects.get(slug__in=sports.known_as(sports.GAME)), self.game)
        self.assertEqual(sports.known_as("pokemon"), ("pokemon",))

    def test_the_game_page_offers_the_lines_and_a_line_has_its_own_page(self):
        from .testing import make_listing, make_retailer

        shop = make_retailer("Harbour Games")
        for item in (self.hobby, self.blaster, self.nfl):
            make_listing(item, shop, price="90.00")
        releases.attach_sets(self.game)
        html = self.client.get("/games/sports-cards/").content.decode()
        # Lines have no dates: with no views yet, the one with more in stock comes first, not the first by name.
        self.assertLess(html.index("Panini Prizm Premier League"), html.index("Panini Prizm NFL"))
        page = self.client.get("/games/sports-cards/panini-prizm-premier-league/")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn(self.hobby.name, html)
        self.assertIn(self.blaster.name, html)
        self.assertNotIn(self.nfl.name, html)


class OldAddressTests(TestCase):
    """The game's football pages, which search engines have listed, send visitors to the same page at its sports
    address for good, with the filters they carried."""

    def setUp(self):
        self.game = make_game(name="Sports cards", short_name="Sports", slug=sports.GAME)
        make_set(self.game, name="Panini Prizm Premier League", slug="panini-prizm-premier-league", code="")

    def test_the_game_its_lines_and_its_feed_move_for_good(self):
        for old, new in (
            ("/games/football/", "/games/sports-cards/"),
            ("/games/football/?type=booster_box&sort=price", "/games/sports-cards/?type=booster_box&sort=price"),
            ("/games/football/panini-prizm-premier-league/", "/games/sports-cards/panini-prizm-premier-league/"),
            ("/feeds/football.xml", "/feeds/sports-cards.xml"),
        ):
            response = self.client.get(old)
            self.assertEqual((response.status_code, response["Location"]), (301, new), old)
        self.assertEqual(self.client.get("/games/sports-cards/panini-prizm-premier-league/").status_code, 200)
        self.assertEqual(self.client.get("/feeds/sports-cards.xml").status_code, 200)

    def test_until_the_game_has_moved_its_old_address_still_works(self):
        Game.objects.filter(pk=self.game.pk).update(slug="football")
        self.assertEqual(self.client.get("/games/football/").status_code, 200)
        self.assertEqual(self.client.get("/feeds/football.xml").status_code, 200)

    def test_another_game_is_not_moved(self):
        self.assertEqual(self.client.get("/games/footballs/").status_code, 404)
