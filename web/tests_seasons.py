"""Seasonal themes (web/seasons.py). The owner, 2 Nov 2026: "I want ripraptor to have seasonal themes, make sure
that there is a seasonal theme created with art work ... and make sure that the themes automatically enable via an
on on the admin"."""

import re
from datetime import date
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import SeasonalThemes
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import pagecache, seasons

SEASON_DIR = Path(settings.BASE_DIR) / "web" / "templates" / "web" / "includes" / "season"
CSS = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "site.css").read_text(encoding="utf-8")
TOKENS = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
CHRISTMAS_DAY = date(2026, 12, 25)
BETWEEN = date(2026, 11, 2)


def on_day(day):
    return mock.patch("web.seasons.timezone.localdate", return_value=day)


class DatesTests(TestCase):
    def test_each_theme_has_its_dates_and_none_overlap(self):
        cases = {
            date(2026, 10, 16): None, date(2026, 10, 17): "halloween", date(2026, 10, 31): "halloween",
            date(2026, 11, 1): None, date(2026, 11, 30): None, date(2026, 12, 1): "christmas",
            date(2026, 12, 26): "christmas", date(2026, 12, 27): None, date(2027, 7, 19): None,
            date(2027, 7, 20): "summer", date(2027, 8, 31): "summer", date(2027, 9, 1): None,
        }
        for day, key in cases.items():
            with self.subTest(day=day):
                theme = seasons.theme_for(day)
                self.assertEqual(theme.key if theme else None, key)
        for year in range(2026, 2040):
            days = [(t.key, *t.window(year)) for t in seasons.THEMES]
            for (_, _, last), (_, first, _) in zip(days, days[1:]):
                self.assertLess(last, first)

    def test_easter_moves_with_easter_sunday(self):
        self.assertEqual([seasons.easter_sunday(y) for y in (2025, 2026, 2027, 2028)],
                         [date(2025, 4, 20), date(2026, 4, 5), date(2027, 3, 28), date(2028, 4, 16)])
        easter = seasons.BY_KEY["easter"]
        self.assertEqual(easter.window(2027), (date(2027, 3, 19), date(2027, 3, 29)))   # Friday to Easter Monday
        self.assertEqual(easter.next_window(BETWEEN), (date(2027, 3, 19), date(2027, 3, 29)))
        self.assertEqual(seasons.BY_KEY["christmas"].next_window(BETWEEN), (date(2026, 12, 1), date(2026, 12, 26)))


class SwitchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", product_type="booster_box")
        make_listing(product, make_retailer("Harbour Games"), price="87.00")
        self.product = product

    def home(self, day, **params):
        with on_day(day):
            return self.client.get(reverse("web:home"), params).content.decode()

    def test_a_theme_dresses_the_site_by_itself_during_its_dates(self):
        seasons.remember()   # never opened in Admin: on, as the owner asked
        html = self.home(CHRISTMAS_DAY)
        self.assertIn('data-season="christmas"', html)
        self.assertIn('id="season-garland-christmas"', html)
        # The hat sits inside the logo's link, drawn in the logo's own units, and every piece is hidden from
        # screen readers.
        wordmark = html[html.index('<a class="wordmark"'):html.index("</a>", html.index('<a class="wordmark"'))]
        self.assertIn('<svg class="season-hat" viewBox="0 0 1200 304"', wordmark)
        hero = html[html.index('<section class="hero"'):html.index("</section>", html.index('<section class="hero"'))]
        self.assertIn('<div class="season-scenes">', hero)
        self.assertEqual(html.count('class="season-scene season-scene--'), 2)
        for svg in re.findall(r"<svg class=\"season-[^>]*>", html):
            self.assertIn('aria-hidden="true"', svg)
        # The product page wears the hat and the lights, not the home page's pictures.
        with on_day(CHRISTMAS_DAY):
            page = self.client.get(self.product.get_absolute_url()).content.decode()
        self.assertIn('class="season-hat"', page)
        self.assertNotIn("season-scenes", page)
        # Between seasons the site has its usual look.
        html = self.home(BETWEEN)
        self.assertNotIn("data-season", html)
        self.assertNotIn("season-", html)

    def test_the_owners_switch_turns_every_theme_off_and_on_at_once(self):
        row = SeasonalThemes.load()
        row.on = False
        row.save()
        self.assertNotIn("data-season", self.home(CHRISTMAS_DAY))
        row.on = True
        row.save()
        self.assertIn('data-season="christmas"', self.home(CHRISTMAS_DAY))
        # An emptied cache (a deploy) leaves the usual look until warm_pages puts the switch back.
        cache.clear()
        self.assertNotIn("data-season", self.home(CHRISTMAS_DAY))
        with mock.patch("web.management.commands.warm_pages.warm_pages", return_value={}):
            call_command("warm_pages", "--due", stdout=open("/dev/null", "w"))
        self.assertIn('data-season="christmas"', self.home(CHRISTMAS_DAY))

    def test_staff_can_see_any_theme_now_and_nobody_else_can(self):
        seasons.remember()
        self.assertNotIn("data-season", self.home(BETWEEN, theme="halloween"))
        staff = get_user_model().objects.create_user("owner", password="pw", is_staff=True)
        self.client.force_login(staff)
        for key in ("halloween", "christmas", "easter", "summer"):
            with self.subTest(key):
                self.assertIn(f'data-season="{key}"', self.home(BETWEEN, theme=key))
        self.assertNotIn("data-season", self.home(BETWEEN, theme="birthday"))

    def test_no_page_gains_a_query(self):
        def count(day):
            with on_day(day), CaptureQueriesContext(connection) as queries:
                self.client.get(reverse("web:home"))
            return len(queries)

        seasons.remember()
        count(BETWEEN)   # build the cached lists first
        self.assertEqual(count(CHRISTMAS_DAY), count(BETWEEN))


@override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
class KeptPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.game = make_game()
        make_listing(make_product(make_set(self.game)), make_retailer())
        seasons.remember()

    def test_a_page_kept_in_one_season_is_not_served_in_the_next(self):
        url = self.game.get_absolute_url()
        with on_day(CHRISTMAS_DAY):
            self.client.get(url)
            self.assertEqual(self.client.get(url)[pagecache.HEADER], "cached")
        with on_day(date(2026, 12, 27)):
            page = self.client.get(url)
        self.assertNotIn(pagecache.HEADER, page)
        self.assertNotIn("data-season", page.content.decode())

    def test_a_staff_preview_is_never_kept(self):
        url = self.game.get_absolute_url()
        self.client.force_login(get_user_model().objects.create_user("owner", password="pw", is_staff=True))
        self.assertIn('data-season="christmas"', self.client.get(url, {"theme": "christmas"}).content.decode())
        self.client.logout()
        page = self.client.get(url, {"theme": "christmas"})
        self.assertNotIn(pagecache.HEADER, page)
        self.assertNotIn("data-season", page.content.decode())


class AdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))

    def test_admins_home_page_leads_to_it(self):
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn(f'href="{reverse("admin:catalogue_seasonalthemes_changelist")}" class="section">Seasonal themes',
                      html)

    def test_the_list_opens_the_one_row_with_each_themes_dates_and_a_way_to_see_it(self):
        response = self.client.get(reverse("admin:catalogue_seasonalthemes_changelist"))
        self.assertRedirects(response, reverse("admin:catalogue_seasonalthemes_change", args=[1]))
        with mock.patch("django.utils.timezone.localdate", return_value=BETWEEN):
            html = self.client.get(reverse("admin:catalogue_seasonalthemes_change", args=[1])).content.decode()
        self.assertIn("Nothing showing today.", html)
        self.assertIn("<strong>Christmas</strong>, 1 Dec to 26 Dec: next from Tue 1 Dec 2026.", html)
        self.assertIn("<strong>Easter</strong>, 19 Mar to 29 Mar: next from Fri 19 Mar 2027.", html)
        self.assertIn('href="/?theme=summer"', html)
        # Soonest first.
        self.assertEqual(re.findall(r"<li><strong>([A-Z][a-z ]+)</strong>", html),
                         ["Christmas", "Easter", "Summer holidays", "Halloween"])
        self.assertIn('name="on"', html)

    def test_unticking_on_takes_effect_at_once(self):
        seasons.remember()
        url = reverse("admin:catalogue_seasonalthemes_change", args=[SeasonalThemes.load().pk])
        self.client.post(url, {})   # an unticked box is not sent
        self.assertFalse(SeasonalThemes.load().on)
        self.assertFalse(seasons.switched_on())


class ArtworkTests(TestCase):
    def test_every_theme_has_its_hat_decorations_and_pictures(self):
        for theme in seasons.THEMES:
            for part in ("hat", "garland", "scene"):
                with self.subTest(theme=theme.key, part=part):
                    self.assertTrue((SEASON_DIR / f"{theme.key}_{part}.html").exists())

    def test_every_colour_is_a_token(self):
        used = set()
        for path in SEASON_DIR.glob("*.html"):
            art = path.read_text(encoding="utf-8")
            self.assertNotRegex(art, r"#[0-9a-fA-F]{3,6}\b|rgb\(|fill=\"(?!url\()[a-z]|stroke=\"[a-z]", path.name)
            used |= set(re.findall(r'class="(s-[a-z-]+)"', art))
        for name in used:
            self.assertRegex(CSS, rf"\.{name}\b[^{{]*\{{ (fill|stroke)", name)
        for token in set(re.findall(r"var\((--season-[a-z-]+)\)", CSS)):
            self.assertIn(f"  {token}:", TOKENS)
        # Still decoration: nothing in it moves.
        block = CSS[CSS.index("/* Seasonal themes"):CSS.index("/* The drop spotlight")]
        self.assertNotIn("@keyframes", block)
        self.assertNotIn("animation", block)
