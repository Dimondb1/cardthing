"""Seasons on a story's picture (web/share.py). The owner, 27 Nov 2026, Black Friday: "Can the share to stories be
depending on the current theme. Like today is black Friday so they theme the posts like that, then xmas.... Easter...".
The card is the same all year; the ground round it, the season's name over it and the art on the ground follow the
season that is on: Black Friday while the site's banner for it shows, else the seasonal theme (web/seasons.py). Black
Friday says how far a price is under its 30-day average only for a product its pages list as a genuine drop."""

from datetime import date
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image, ImageColor

from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from content.registry import REGISTRY

from . import seasons, share, views
from .tests_black_friday import DealCase, steady
from .tests_story_deals import Base as StoryDealsBase

BETWEEN = date(2026, 9, 15)          # no season
FRIDAY = date(2026, 11, 27)          # Black Friday 2026
SIDE = (1, 960)                      # the ground beside the card, outside every theme's art
CARD = (70, 960)                     # inside the card


def rgb(colour):
    return ImageColor.getrgb(colour)[:3]


def on(day):
    """The day the site thinks it is, for Black Friday's season and the year."""
    return mock.patch("web.black_friday.local_today", return_value=day)


class ChoiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_black_friday_while_its_banner_shows(self):
        for day, key in ((date(2026, 11, 12), None), (date(2026, 11, 13), "black_friday"), (FRIDAY, "black_friday"),
                         (date(2026, 11, 30), "black_friday"), (date(2026, 12, 1), None)):
            with self.subTest(day=day):
                theme = share.story_theme(day=day)
                self.assertEqual(theme.key if theme else None, key)

    def test_then_the_seasonal_theme_that_is_on_and_none_with_the_owners_switch_off(self):
        days = ((date(2026, 12, 10), "christmas"), (date(2027, 3, 25), "easter"), (date(2026, 10, 25), "halloween"),
                (date(2026, 8, 1), "summer"), (BETWEEN, None))
        for day, _key in days:
            self.assertIsNone(share.story_theme(day=day))               # the switch is off
        cache.set(seasons.KEY, True)
        for day, key in days:
            with self.subTest(day=day):
                theme = share.story_theme(day=day)
                self.assertEqual(theme.key if theme else None, key)
        # Black Friday comes first where the two meet (a Cyber Monday in December).
        self.assertEqual(share.story_theme(day=date(2024, 12, 2)).key, "black_friday")

    def test_every_seasonal_theme_has_a_look_and_a_name_to_print(self):
        for theme in seasons.THEMES:
            with self.subTest(theme=theme.key):
                self.assertIn(theme.key, share.STORY_THEMES)
        for theme in share.STORY_THEMES.values():
            with self.subTest(theme=theme.key):
                self.assertIn("{year}", REGISTRY[theme.label].default)
                self.assertTrue(theme.art in share.STORY_ART or theme.key == share.BLACK_FRIDAY)

    def test_one_named_outright_and_plain_for_none(self):
        with on(FRIDAY):
            self.assertEqual(share.story_theme("christmas").key, "christmas")
            self.assertIsNone(share.story_theme(share.PLAIN))
            self.assertEqual(share.story_theme().key, "black_friday")

    def test_the_year_is_black_fridays_own_or_todays(self):
        with on(FRIDAY):
            self.assertEqual(share.story_year(share.STORY_THEMES["black_friday"]), 2026)
        self.assertEqual(share.story_year(share.STORY_THEMES["easter"], date(2027, 3, 25)), 2027)


class PictureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="surging-box",
                                    product_type="booster_box")
        make_listing(self.product, make_retailer("Harbour Games"), price="87.00", delivery="0.00")
        make_listing(self.product, make_retailer("Northgate Cards"), price="95.00", delivery="0.00")

    def picture(self, key=None, drop="", product=None):
        product, summary = views.post_product((product or self.product).slug)
        png = share.draw_story(product, summary, None, theme=share.STORY_THEMES.get(key), year=2026, drop=drop)
        image = Image.open(BytesIO(png))
        image.load()
        return image.convert("RGB")

    def card(self, picture):
        """The card's first and last rows, down the picture inside its left edge."""
        white = [y for y in range(share.STORY_HEIGHT) if picture.getpixel((CARD[0], y)) == (255, 255, 255)]
        return white[0], white[-1]

    def test_the_plain_picture_is_as_it_was(self):
        picture = self.picture()
        self.assertEqual(picture.size, (share.STORY_WIDTH, share.STORY_HEIGHT))
        self.assertEqual(picture.getpixel(SIDE), rgb(share.SHELF))
        self.assertEqual(picture.getpixel((540, 20)), rgb(share.SHELF))
        top, foot = self.card(picture)
        self.assertGreaterEqual(top, share.STORY_TOP)
        self.assertLessEqual(foot, share.STORY_FOOT)

    def test_each_season_has_its_ground_and_the_card_stays_white(self):
        for key, theme in share.STORY_THEMES.items():
            with self.subTest(theme=key):
                picture = self.picture(key)
                self.assertEqual(picture.size, (share.STORY_WIDTH, share.STORY_HEIGHT))
                self.assertEqual(picture.getpixel(SIDE), rgb(theme.ground))
                self.assertEqual(picture.getpixel(CARD), (255, 255, 255))

    def test_the_seasons_name_is_over_the_card_which_still_ends_clear_of_the_reply_box(self):
        plain_top, _foot = self.card(self.picture())
        for key, theme in share.STORY_THEMES.items():
            with self.subTest(theme=key):
                picture = self.picture(key)
                top, foot = self.card(picture)
                room = share.story_label_height(theme)
                self.assertEqual(room, share.STORY_LABEL_PAD + share.STORY_LABEL + share.STORY_LABEL_GAP)
                self.assertGreaterEqual(top, share.STORY_TOP + room)
                self.assertGreater(top, plain_top)
                self.assertLessEqual(foot, share.STORY_FOOT)
                # Its name in the theme's ink and the year in the brand's orange, between the top and the card.
                band = picture.crop((120, share.STORY_TOP, share.STORY_WIDTH - 120, top - 8))
                colours = {colour for _count, colour in band.getcolors(maxcolors=1 << 20)}
                self.assertIn(rgb(theme.ink), colours)
                self.assertIn(rgb(theme.accent), colours)

    def test_a_long_name_and_a_line_under_the_price_still_leave_the_photo_room(self):
        long = make_product(self.product.product_set, slug="long-name", product_type="booster_box",
                            name="Scarlet and Violet Prismatic Evolutions Super Premium Collection Display Case")
        make_listing(long, make_retailer("Moor Toys"), price="1249.99", delivery="0.00")
        product, summary = views.post_product(long.slug)
        theme = share.STORY_THEMES["black_friday"]
        png = share.draw_story(product, summary, None, line="Top Rated seller on eBay", theme=theme, year=2026,
                               drop="12% under its 30-day average")
        picture = Image.open(BytesIO(png)).convert("RGB")
        top, foot = self.card(picture)
        self.assertGreaterEqual(top, share.STORY_TOP + share.story_label_height(theme, "x"))
        # The well shrinks for the name, never under its least; the card may then pass the foot by a little.
        self.assertLessEqual(foot, share.STORY_FOOT + (share.STORY_WELL_THEMED_LEAST - 200))

    def test_black_fridays_drop_is_green_under_its_name_and_takes_its_own_room(self):
        theme = share.STORY_THEMES["black_friday"]
        self.assertEqual(share.story_label_height(theme, "10% under its 30-day average"),
                         share.story_label_height(theme) + share.STORY_DROP_GAP + share.STORY_DROP)
        self.assertEqual(share.story_label_height(None, "x"), 0)
        green = rgb(share.PRICE_ON_DARK)

        def greens(picture):
            top, _foot = self.card(picture)
            band = picture.crop((120, share.STORY_TOP, share.STORY_WIDTH - 120, top - 8))
            return sum(count for count, colour in band.getcolors(maxcolors=1 << 20) if colour == green)

        self.assertEqual(greens(self.picture("black_friday")), 0)
        self.assertGreater(greens(self.picture("black_friday", drop="10% under its 30-day average")), 200)

    def test_the_art_is_the_same_every_time_and_keeps_clear_of_the_name(self):
        for key in ("christmas", "easter", "halloween", "summer"):
            theme = share.STORY_THEMES[key]
            with self.subTest(theme=key):
                ground = share.story_ground(theme)
                self.assertEqual(ground.tobytes(), share.story_ground(theme).tobytes())
                # Something is drawn on the ground, above and below where the card goes.
                for strip in ((0, 0, share.STORY_WIDTH, share.STORY_TOP - 30),
                              (0, share.STORY_FOOT + 40, share.STORY_WIDTH, share.STORY_HEIGHT)):
                    self.assertGreater(len(ground.crop(strip).convert("RGB").getcolors(maxcolors=1 << 20)), 1)
                # And nothing where the season's name is printed.
                name = ground.crop((200, 240, 700, 340)).convert("RGB").getcolors(maxcolors=1 << 20)
                self.assertEqual([colour for _count, colour in name], [rgb(theme.ground)])
        plain = share.story_ground(None).convert("RGB").getcolors()
        self.assertEqual([colour for _count, colour in plain], [rgb(share.SHELF)])
        black = share.story_ground(share.STORY_THEMES["black_friday"]).convert("RGB").getcolors()
        self.assertEqual([colour for _count, colour in black], [rgb("#14161c")])

    def test_christmas_adds_no_red_or_green(self):
        # Red means dearer and green cheapest here: the wrap is midnight blue and gold, as on the site.
        ground = share.story_ground(share.STORY_THEMES["christmas"]).convert("RGB")
        for _count, (red, green, blue) in ground.getcolors(maxcolors=1 << 20):
            self.assertFalse(red > 150 and green < 90 and blue < 90, (red, green, blue))
            self.assertFalse(green > 140 and red < 90 and blue < 110, (red, green, blue))


class DropTests(DealCase):
    """Black Friday's line under its name: only what the Black Friday pages themselves would say of the product."""

    def said(self, product):
        found, summary = views.post_product(product.slug)
        return share.story_drop(found, summary)

    def test_a_genuine_drop_says_how_far_under_its_average(self):
        box = self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"))
        self.bands()
        self.assertEqual(self.said(box), "10% under its 30-day average")

    def test_a_product_that_is_no_deal_says_nothing(self):
        box = self.product("Surging Sparks Booster Box", "100.00", history=steady("100.00"))
        self.bands()
        self.assertEqual(self.said(box), "")

    def test_one_listed_for_its_lowest_recorded_price_says_that(self):
        tin = self.product("Surging Sparks Tin", "54.00", others=("58.00",), kind="tin",
                           history={**steady("55.00", days=30), **steady("62.00", days=60, start=31), 45: "54.00"})
        self.bands()
        self.assertEqual(self.said(tin), "Lowest recorded price")

    def test_nothing_when_the_pictures_price_is_not_the_one_the_drop_is_of(self):
        box = self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"))
        self.bands()
        found, summary = views.post_product(box.slug)
        with mock.patch.object(share, "post_lead", return_value=None):
            self.assertEqual(share.story_drop(found, summary), "")
        other = mock.Mock(shown_price=summary.best.shown_price + 5)
        with mock.patch.object(share, "post_lead", return_value=other):
            self.assertEqual(share.story_drop(found, summary), "")

    def test_the_picture_asks_for_it_only_on_black_friday_and_keeps_each_look_apart(self):
        box = self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"))
        self.bands()
        found, summary = views.post_product(box.slug)
        with mock.patch.object(share, "photo_bytes", return_value=None), on(FRIDAY), \
                mock.patch.object(share, "draw_story", wraps=share.draw_story) as drawn:
            plain = share.story_image(found, summary)
            self.assertEqual(drawn.call_args.kwargs["drop"], "")
            friday = share.story_image(found, summary, theme=share.STORY_THEMES["black_friday"])
            self.assertEqual(drawn.call_args.kwargs["drop"], "10% under its 30-day average")
            self.assertEqual(drawn.call_args.kwargs["year"], 2026)
            easter = share.story_image(found, summary, theme=share.STORY_THEMES["easter"])
            self.assertEqual(drawn.call_args.kwargs["drop"], "")
            self.assertEqual(drawn.call_count, 3)
            # Each is kept, under its own key.
            self.assertEqual(share.story_image(found, summary, theme=share.STORY_THEMES["black_friday"]), friday)
            self.assertEqual(share.story_image(found, summary), plain)
            self.assertEqual(drawn.call_count, 3)
        self.assertEqual(len({plain, friday, easter}), 3)


class ServedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="surging-box",
                                    product_type="booster_box")
        make_listing(self.product, make_retailer("Harbour Games"), price="87.00", delivery="0.00")
        self.url = f"/products/{self.product.slug}/story.png"

    def ground(self, **params):
        with mock.patch("catalogue.importers.fetch", side_effect=OSError("down")):
            response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return Image.open(BytesIO(response.content)).convert("RGB").getpixel(SIDE)

    def test_a_visitors_share_to_story_wears_the_season_that_is_on(self):
        with on(BETWEEN):
            self.assertEqual(self.ground(), rgb(share.SHELF))
        with on(FRIDAY):
            self.assertEqual(self.ground(), rgb("#14161c"))
        cache.set(seasons.KEY, True)
        with on(date(2026, 12, 10)), mock.patch("web.seasons.timezone.localdate", return_value=date(2026, 12, 10)):
            self.assertEqual(self.ground(), rgb(share.WRAP))

    def test_only_staff_may_name_a_look(self):
        with on(BETWEEN):
            self.assertEqual(self.ground(theme="christmas"), rgb(share.SHELF))
        with on(FRIDAY):
            self.assertEqual(self.ground(theme="plain"), rgb("#14161c"))
        with override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False):
            self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
            with on(BETWEEN):
                self.assertEqual(self.ground(theme="christmas"), rgb(share.WRAP))
                self.assertEqual(self.ground(theme="no-such"), rgb(share.SHELF))
            with on(FRIDAY):
                self.assertEqual(self.ground(theme="plain"), rgb(share.SHELF))


class StoryDealsLookTests(StoryDealsBase):
    """The Story deals page (Admin): the pictures wear today's season, and the owner can choose another look or none."""

    def test_the_looks_are_offered_with_todays_own_first(self):
        product = self.product("ebay-deal", ebay="50.00")
        self.bands()
        html = self.page().content.decode()
        self.assertIn("<strong>Today&#x27;s (plain)</strong>", html)
        for link, name in (("?theme=plain", "Plain"), ("?theme=black_friday", "Black Friday"),
                           ("?theme=christmas", "Christmas"), ("?theme=easter", "Easter"),
                           ("?theme=halloween", "Halloween"), ("?theme=summer", "Summer holidays")):
            self.assertIn(f'<a href="{link}">{name}</a>', html)
        self.assertIn(f'src="{reverse("story_deal_image", args=[product.slug])}"', html)
        with on(FRIDAY):
            self.assertIn("<strong>Today&#x27;s (Black Friday)</strong>", self.page().content.decode())

    def test_a_chosen_look_is_on_every_picture_and_kept_by_the_forms_and_links(self):
        product = self.product("ebay-deal", ebay="50.00")
        self.bands()
        picture = reverse("story_deal_image", args=[product.slug])
        html = self.page(theme="christmas").content.decode()
        self.assertIn("<strong>Christmas</strong>", html)
        self.assertIn('<a href="?">Today&#x27;s (plain)</a>', html)
        self.assertIn(f'src="{picture}?theme=christmas"', html)
        self.assertIn(f'href="{picture}?theme=christmas" download=', html)
        self.assertIn(f'data-picture="{picture}"', html)                 # the script adds the look from the form
        self.assertIn('<input type="hidden" name="theme" value="christmas">', html)
        self.assertIn('<a href="?from=all&amp;theme=christmas">Every seller</a>', html)
        html = self.page(theme="christmas", p=product.slug, badge="Gift idea").content.decode()
        self.assertIn(f'src="{picture}?badge=Gift+idea&amp;theme=christmas"', html)
        # A look there is not is today's own.
        self.assertIn(f'src="{picture}"', self.page(theme="no-such").content.decode())
        self.assertIn('<a href="?from=all&amp;theme=easter">Easter</a>', self.page(**{"from": "all"}).content.decode())

    def test_the_picture_wears_the_look_chosen(self):
        product = self.product("ebay-deal", ebay="50.00")
        self.bands()

        def ground(**params):
            with mock.patch("catalogue.importers.fetch", side_effect=OSError("down")):
                response = self.client.get(reverse("story_deal_image", args=[product.slug]), params)
            return Image.open(BytesIO(response.content)).convert("RGB").getpixel(SIDE)

        self.assertEqual(ground(), rgb(share.SHELF))
        self.assertEqual(ground(theme="halloween"), rgb(share.NIGHT))
        with on(FRIDAY):
            self.assertEqual(ground(), rgb("#14161c"))
            self.assertEqual(ground(theme="plain"), rgb(share.SHELF))

    def test_the_script_sends_the_look_with_the_owners_words(self):
        script = (Path(__file__).resolve().parent / "static/js/story-deals.js").read_text()
        self.assertIn('["badge", "line", "theme"].forEach((field) => {', script)
