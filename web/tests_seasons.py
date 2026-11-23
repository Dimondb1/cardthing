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
HALLOWEEN = date(2026, 10, 31)
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
        html = self.home(HALLOWEEN)
        self.assertIn('data-season="halloween"', html)
        self.assertIn('id="season-garland-halloween"', html)
        # The hat sits inside the logo's link, drawn in the logo's own units, and every piece is hidden from
        # screen readers.
        wordmark = html[html.index('<a class="wordmark"'):html.index("</a>", html.index('<a class="wordmark"'))]
        self.assertIn('<svg class="season-hat" viewBox="0 0 1200 304"', wordmark)
        hero = html[html.index('<section class="hero"'):html.index("</section>", html.index('<section class="hero"'))]
        self.assertIn('<div class="season-scenes">', hero)
        self.assertEqual(html.count('class="season-scene season-scene--'), 2)
        for svg in re.findall(r"<svg class=\"season-[^>]*>", html):
            self.assertIn('aria-hidden="true"', svg)
        # The product page wears the hat and the decorations, not the home page's pictures.
        with on_day(HALLOWEEN):
            page = self.client.get(self.product.get_absolute_url()).content.decode()
        self.assertIn('class="season-hat"', page)
        self.assertIn("season-garland", page)
        self.assertNotIn("season-scenes", page)
        # Between seasons the site has its usual look.
        html = self.home(BETWEEN)
        self.assertNotIn("data-season", html)
        self.assertNotIn("season-", html)
        self.assertNotIn("gifts", html)

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
            for part in seasons.ART:
                with self.subTest(theme=theme.key, part=part):
                    drawn = (SEASON_DIR / f"{theme.key}_{part}.html").exists()
                    self.assertEqual(drawn, part in theme.art)
                    self.assertEqual(getattr(theme, part), f"web/includes/season/{theme.key}_{part}.html" if drawn else "")
        # Christmas is the wrap: the hat stays, the lights and the pictures are gone.
        self.assertEqual(seasons.BY_KEY["christmas"].art, ("hat",))
        self.assertTrue(all(theme.art == seasons.ART for theme in seasons.THEMES if theme.key != "christmas"))

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


class ChristmasTests(TestCase):
    """Christmas, as the owner asked on 23 Nov 2026: "not just adding some bullshit lights like we've done
    originally". The site is wrapped as a foil booster pack, and the home page helps a present buyer."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        game = make_game()
        self.product = make_product(make_set(game), name="Surging Sparks Booster Box", product_type="booster_box")
        make_listing(self.product, make_retailer("Harbour Games"), price="87.00")
        self.pack = make_product(make_set(game, name="Journey Together", slug="journey-together", code="JTG"),
                                 name="Journey Together Booster Pack", product_type="booster_pack")
        make_listing(self.pack, make_retailer("Northgate Cards"), price="4.25")

    def home(self, day=date(2026, 12, 5), **params):
        with on_day(day):
            seasons.remember()
            return self.client.get(reverse("web:home"), params).content.decode()

    def test_the_site_is_wrapped_with_no_lights_and_no_pictures(self):
        html = self.home()
        self.assertIn('data-season="christmas"', html)
        self.assertIn('class="season-hat"', html)
        self.assertNotIn("season-garland", html)
        self.assertNotIn("season-scene", html)
        # The phone browser's own bar takes the wrap's colour: one tag, which the look scripts leave alone.
        wrap = re.search(r"  --season-wrap: (#[0-9a-f]{6});", TOKENS).group(1)
        self.assertEqual(seasons.BY_KEY["christmas"].bar, wrap)
        self.assertEqual(re.findall(r'<meta name="theme-color"[^>]*>', html), [f'<meta name="theme-color" content="{wrap}">'])
        self.assertIn("""querySelectorAll('meta[name="theme-color"][media]')""", html)
        script = (Path(settings.BASE_DIR) / "web" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn("""querySelectorAll('meta[name="theme-color"][media]')""", script)
        # Any other day the bar is the page's own colour, light or dark.
        with on_day(BETWEEN):
            usual = self.client.get(reverse("web:home")).content.decode()
        self.assertEqual(len(re.findall(r'<meta name="theme-color"[^>]*media=', usual)), 2)
        # The visitor check is a plain page whatever the season.
        check = (Path(settings.BASE_DIR) / "web" / "templates" / "web" / "botcheck.html").read_text(encoding="utf-8")
        self.assertIn('{% include "web/includes/theme_head.html" with season=None %}', check)

    def test_the_wrap_is_drawn_from_tokens_only_where_the_dark_colours_are_known(self):
        block = CSS[CSS.index("/* The Christmas wrap."):CSS.index("/* Gifts by budget")]
        inside = block[block.index("@supports (color: light-dark(black, white)) {"):]
        # Outside the test for the dark colours: only the room for the hat. An older browser keeps its header.
        outside = block[:block.index("@supports")]
        self.assertNotIn("--season-wrap", outside.split("*/")[-1])
        self.assertIn('[data-season="christmas"] :is(.masthead, .hero, .site-footer) { color-scheme: dark; background-color: var(--season-wrap); }', inside)
        # What belongs to the page keeps the page's look, light or dark or the visitor's choice.
        self.assertIn('[data-season="christmas"] :is(.masthead__search, .hero__search, .nav-games__menu) { color-scheme: var(--page-scheme); }', inside)
        for line in ("  :root { --page-scheme: light dark; }", '  :root[data-theme="light"] { color-scheme: light; --page-scheme: light; }',
                     '  :root[data-theme="dark"] { color-scheme: dark; --page-scheme: dark; }'):
            self.assertIn(line, TOKENS)
        # The header's own edge gives way to the headline's on the usual home page, and the footer's is turned over.
        self.assertIn('[data-season="christmas"]:has(.page > .hero) .masthead::after { content: none; }', inside)
        self.assertIn('[data-season="christmas"] .site-footer::after { top: auto; bottom: 100%; transform: scaleY(-1); }', inside)
        # No colour or size is written here (a mask's shape aside), and nothing moves.
        plain = re.sub(r'url\("data:image/svg\+xml,[^"]*"\)|@media \([^)]*\)', "", block)   # and a breakpoint
        self.assertNotRegex(plain, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|\d+px|\d+rem")
        self.assertNotIn("animation", block)
        self.assertNotIn("@keyframes", block)
        for token in ("--season-wrap", "--season-wrap-ridge", "--season-spark", "--season-spark-quiet", "--season-crimp",
                      "--season-crimp-tooth", "--season-crimp-ridge", "--season-print"):
            self.assertIn(f"  {token}:", TOKENS)
            self.assertIn(f"var({token})", block)
        # Midnight blue and gold: no red or green is added beside the prices.
        self.assertNotRegex(block, r"--season-(red|pine)|--color-(price|more)")

    def test_gifts_by_budget_opens_the_price_lists_that_have_something_in_stock(self):
        html = self.home()
        self.assertIn('<h2 id="gifts-title" class="section-head__title">Gifts by budget</h2>', html)
        tiles = re.findall(r'<a class="gifts__tile" href="([^"]+)"><span class="gifts__band">([^<]+)</span>'
                           r'<span class="gifts__suits">([^<]+)</span></a>', html)
        # A pack at 4.25 and a box at 87.00: those two budgets, and no tile for a budget with nothing in stock.
        self.assertEqual(tiles, [("/search/?price=0-10&amp;in_stock=on", "Under £10", "Stocking fillers"),
                                 ("/search/?price=50-100&amp;in_stock=on", "£50 to £100", "Big presents")])
        page = self.client.get("/search/?price=0-10&in_stock=on").content.decode()
        self.assertIn(self.pack.name, page)
        self.assertNotIn(self.product.name, page)
        # Before the budgets have been counted (a staff preview out of season), every tile is there.
        cache.delete(seasons.GIFTS_KEY)
        self.assertEqual(len(re.findall(r'class="gifts__tile"', self.client_home_as_is())), 4)
        self.assertEqual([band for band, _ in seasons.GIFT_BANDS], ["0-10", "10-25", "25-50", "50-100"])
        from web.views import PRICE_BANDS
        self.assertLessEqual({band for band, _ in seasons.GIFT_BANDS}, set(dict(PRICE_BANDS)))

    def client_home_as_is(self):
        with on_day(date(2026, 12, 5)):
            return self.client.get(reverse("web:home")).content.decode()

    def test_the_tiles_are_on_every_home_page_layout_and_nowhere_else(self):
        self.home()
        self.client.force_login(get_user_model().objects.create_user("owner", password="pw", is_staff=True))
        for name in ("classic", "feed", "compact"):
            with self.subTest(look=name), on_day(date(2026, 12, 5)):
                html = self.client.get(reverse("web:home"), {"look": name}).content.decode()
                self.assertEqual(html.count('id="gifts-title"'), 1)
                self.assertIn("Royal Mail", html)
        with on_day(date(2026, 12, 5)):
            for url in (self.product.get_absolute_url(), reverse("web:deals"), reverse("web:search")):
                self.assertNotIn("gifts", self.client.get(url).content.decode(), url)
        # Staff looking at another theme, or at Christmas out of season, see what that day's visitors would.
        with on_day(BETWEEN):
            self.assertNotIn("gifts", self.client.get(reverse("web:home"), {"theme": "halloween"}).content.decode())
            preview = self.client.get(reverse("web:home"), {"theme": "christmas"}).content.decode()
        self.assertIn('id="gifts-title"', preview)

    def test_royal_mails_next_last_posting_day_and_then_none(self):
        said = {
            date(2026, 12, 1): "Royal Mail&#x27;s last posting day for 2nd Class is Fri\xa018\xa0Dec. Shops need time to pack, so order sooner.",
            date(2026, 12, 18): "for 2nd Class is Fri\xa018\xa0Dec.",
            date(2026, 12, 19): "for Tracked 48 is Sat\xa019\xa0Dec.",
            date(2026, 12, 20): "for 1st Class and Tracked 24 is Mon\xa021\xa0Dec.",
            date(2026, 12, 23): "for Special Delivery Guaranteed is Wed\xa023\xa0Dec.",
        }
        for day, words in said.items():
            with self.subTest(day=day):
                self.assertIn(words, self.home(day))
        # After the last day there is no line, and the tiles stay.
        late = self.home(date(2026, 12, 24))
        self.assertNotIn("Royal Mail", late)
        self.assertIn('id="gifts-title"', late)
        # Days are never guessed: a year whose list has not been added shows none.
        self.assertIsNone(seasons.last_post(date(2027, 12, 5)))
        self.assertNotIn("Royal Mail", self.home(date(2027, 12, 5)))
        for year, days in seasons.LAST_POST.items():
            self.assertEqual([day for _, day in days], sorted(day for _, day in days))
            self.assertTrue(all(day.year == year and day.month == 12 and day.day < 25 for _, day in days))

    def test_the_home_page_reads_the_budgets_from_the_cache_alone(self):
        def count(day):
            with on_day(day), CaptureQueriesContext(connection) as queries:
                self.client.get(reverse("web:home"))
            return len(queries)

        self.home()            # the switch and the budgets are kept, the cached lists built
        count(BETWEEN)
        self.assertEqual(count(date(2026, 12, 5)), count(BETWEEN))
        # The budgets are counted with the switch, in one query, and only at Christmas.
        with on_day(date(2026, 12, 5)), CaptureQueriesContext(connection) as queries:
            seasons.remember()
        self.assertEqual(len(queries), 2)
        self.assertEqual(cache.get(seasons.GIFTS_KEY), ["0-10", "50-100"])
        cache.delete(seasons.GIFTS_KEY)
        with on_day(BETWEEN), CaptureQueriesContext(connection) as queries:
            seasons.remember()
        self.assertEqual(len(queries), 1)
        self.assertIsNone(cache.get(seasons.GIFTS_KEY))

    def test_the_christmas_home_page_keeps_the_house_style(self):
        from web.tests import CopyStyleTests

        html = self.home()
        words = re.sub(r"<[^>]*>", " ", html)
        self.assertNotIn("!", words)
        self.assertNotIn("\u2014", html)
        for phrase in CopyStyleTests.BANNED:
            self.assertNotIn(phrase, html.lower())
