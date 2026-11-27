"""The how it works page's pictures and list icons, from the marketing team's drawings. The owner, 27 Nov 2026: "Can
we update the how it works page to use these graphics". The page's words are unchanged: the pictures only show how
the facts fit together, and each says in its alt text what it shows (views.FIGURES, web/includes/explain_figure.html)."""

import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from content.registry import REGISTRY

from . import views

STATIC = Path(settings.BASE_DIR) / "web" / "static" / "about"
ICONS = Path(settings.BASE_DIR) / "web" / "templates" / "web" / "includes" / "icons" / "why"


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def page(self):
        response = self.client.get("/about/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def figures(self, html):
        """[(file, width, height, alt)] of the page's pictures, in the page's order."""
        return re.findall(r'<figure class="explain-figure"><img src="/static/about/([^"]+)" width="(\d+)" height="(\d+)" '
                          r'alt="([^"]+)" loading="lazy" decoding="async"></figure>', html)


class PictureTests(Base):
    def test_seven_pictures_each_where_the_words_it_shows_are(self):
        html = self.page()
        found = self.figures(html)
        self.assertEqual([name for name, *_ in found], [
            "hiw-1-how-it-works.svg", "hiw-2-delivered-price.svg", "hiw-4-stock-you-can-buy.svg",
            "hiw-5-checked-every-hour.svg", "hiw-3-price-check.svg", "hiw-7-alerts.svg", "hiw-6-how-we-make-money.svg"])
        # Each under the words it shows.
        def after(picture, words):
            self.assertLess(html.index(words), html.index(picture), picture)
        after("hiw-1-how-it-works.svg", "each shop checked every hour")
        self.assertLess(html.index("hiw-1-how-it-works.svg"), html.index('id="promise"'))
        after("hiw-2-delivered-price.svg", "The price you will pay")
        after("hiw-4-stock-you-can-buy.svg", "Only stock you can buy")
        after("hiw-5-checked-every-hour.svg", "Prices you can trust")
        after("hiw-3-price-check.svg", "The usual UK price")
        after("hiw-7-alerts.svg", "Alerts that do the waiting")
        # In How RipRaptor makes money, after its first paragraph and before the second.
        money = html[html.index('id="money-title"'):]
        self.assertLess(money.index("You pay the same price either way."), money.index("hiw-6-how-we-make-money.svg"))
        self.assertLess(money.index("hiw-6-how-we-make-money.svg"), money.index("Retailers cannot pay to change that order."))

    def test_every_picture_has_its_size_and_says_what_it_shows(self):
        for name, width, height, alt in self.figures(self.page()):
            drawing = (STATIC / name).read_text()
            self.assertIn(f'viewBox="0 0 {width} {height}"', drawing, name)
            self.assertGreater(len(alt), 40, name)
        self.assertIn('alt="A price checked within the last hour counts. One we have not been able to check for 72 hours '
                      'stops counting as the cheapest."', self.page())
        for key in views.FIGURES:
            entry = REGISTRY[f"about.figure.{key}.alt"]
            self.assertNotIn("—", entry.default)
            self.assertNotIn("!", entry.default)

    def test_the_page_words_are_all_still_there(self):
        html = self.page()
        for words in ("Some links to retailers are affiliate links.", "Price comparisons are always sorted by delivered price",
                      "The Rip&#x27;s Deals row on the home page is different",
                      "The Price check&#x27;s High price warning is not shown"):
            self.assertIn(words, html)
        self.assertEqual(html.count("Some links to retailers are affiliate links."), 1)

    @override_settings(RIPRAPTOR_STALE_AFTER_HOURS=48)
    def test_the_prices_picture_shows_only_while_a_price_counts_for_72_hours(self):
        # It draws 72 hours; the page's words follow the setting. A picture that disagreed with them is left out.
        html = self.page()
        self.assertNotIn("hiw-5-checked-every-hour.svg", html)
        self.assertIn("not been able to check for 48 hours stops counting", html)
        self.assertEqual(len(self.figures(html)), 6)

    def test_the_drawings_hold_no_script_and_load_nothing_from_elsewhere(self):
        for path in [*STATIC.glob("*.svg"), *ICONS.glob("*.svg")]:
            drawing = path.read_text()
            for found in (r"<script", r"\son\w+\s*=", r"<foreignObject", r"javascript:"):
                self.assertNotRegex(drawing, re.compile(found, re.I), path.name)
            for link in re.findall(r'(?:href|src)\s*=\s*"([^"]+)"', drawing):
                self.assertTrue(link.startswith(("#", "data:")), f"{path.name}: {link}")


class IconTests(Base):
    def marks(self, html, block_id):
        block = html[html.index(block_id):]
        block = block[:block.index("</dl>")]
        return re.findall(r'<dt><span class="why__mark" aria-hidden="true"><svg class="why__icon" [^>]*>', block)

    def test_every_promise_and_why_point_has_its_icon_hidden_from_screen_readers(self):
        html = self.page()
        self.assertEqual(len(self.marks(html, 'id="promise"')), len(views.PROMISE_POINTS))
        shown = len(views.said_now(views.WHY_POINTS))
        self.assertEqual(len(self.marks(html, 'id="why-title"')), shown)

    def test_every_icon_named_is_there(self):
        for name in {*views.PROMISE_ICONS.values(), *views.WHY_ICONS.values()}:
            self.assertTrue((ICONS / f"{name}.svg").exists(), name)
        self.assertEqual(set(views.PROMISE_ICONS), set(views.PROMISE_POINTS))
        self.assertEqual(set(views.WHY_ICONS), set(views.WHY_POINTS))

    def test_the_list_with_pictures_stays_one_column_so_their_words_stay_readable(self):
        css = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "site.css").read_text()
        self.assertIn("@media (min-width: 48rem) { .why--pictures { grid-template-columns: minmax(0, 1fr); } }", css)
        self.assertLess(css.index("@media (min-width: 48rem) { .why {"), css.index(".why--pictures {"))
        html = self.page()
        self.assertIn('<dl class="why why--marked why--pictures">', html)
        self.assertEqual(html.count("why--pictures"), 1)          # the promise, with no pictures, keeps two columns

    def test_the_icons_are_in_the_soft_orange_circle_from_tokens(self):
        css = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "site.css").read_text()
        rule = css[css.index(".why__mark {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("background: var(--color-brand-soft);", rule)
        self.assertIn("color: var(--color-brand-text);", rule)


class FilterTests(TestCase):
    def test_wording_in_paragraphs_splits_after_the_first(self):
        from django.utils.safestring import mark_safe

        from .templatetags.ripraptor import after_first_paragraph, first_paragraph

        html = mark_safe("<p>One.</p>\n<p>Two.</p>\n<p>Three.</p>")
        self.assertEqual(first_paragraph(html), "<p>One.</p>")
        self.assertEqual(after_first_paragraph(html), "<p>Two.</p>\n<p>Three.</p>")
        self.assertEqual(after_first_paragraph(mark_safe("<p>Only.</p>")), "")
        self.assertEqual(first_paragraph("<b>not safe</b>"), "&lt;b&gt;not safe&lt;/b&gt;")
