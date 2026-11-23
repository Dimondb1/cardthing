"""Dark mode (web/static/css/tokens.css, Dark mode): every colour token given a second value, the page following
the device with a choice in the footer, and product photos kept on a light ground. The owner, 23 Nov 2026: "Could we
add a dark mode ... make sure it doesn't leave shit looking products ... make sure it looks amazing"."""

import re
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

CSS = Path(__file__).resolve().parent / "static" / "css"
PAIR = re.compile(r"light-dark\(((?:[^(),]|\([^()]*\))+),\s*((?:[^(),]|\([^()]*\))+)\)")
TOKEN = re.compile(r"(--[\w-]+):\s*([^;]+);")


def tokens():
    """(the tokens' values before the dark block, {token: its value in the dark block})."""
    text = (CSS / "tokens.css").read_text()
    start = text.index("@supports (color: light-dark(#000, #fff))")
    base = {}
    for name, value in TOKEN.findall(text[:start]):
        base[name] = " ".join(value.split("/*")[0].split())
    dark = dict((name, " ".join(value.split("/*")[0].split())) for name, value in TOKEN.findall(text[start:]))
    dark.pop("--page-scheme", None)   # not a colour: the page's own look as a value (the Christmas wrap's islands)
    return base, dark


def luminance(colour):
    red, green, blue = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return sum(weight * (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
               for weight, c in ((0.2126, red), (0.7152, green), (0.0722, blue)))


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class TokenTests(TestCase):
    def test_the_light_look_is_untouched_each_tokens_first_value_is_the_one_it_had(self):
        base, dark = tokens()
        self.assertGreater(len(dark), 30)
        for name, value in dark.items():
            with self.subTest(token=name):
                self.assertIn("light-dark(", value)
                self.assertEqual(PAIR.sub(lambda pair: pair.group(1).strip(), value), base[name])

    def test_the_dark_words_can_be_read_on_the_dark_page(self):
        _base, dark = tokens()
        night = {name: PAIR.search(value).group(2).strip() for name, value in dark.items() if value.startswith("light-dark(#")}
        for ink in ("--color-ink", "--color-ink-muted", "--color-ink-subtle", "--color-brand-text", "--color-price",
                    "--color-more", "--color-in-stock", "--color-preorder", "--color-out"):
            for ground in ("--color-paper", "--color-shelf"):
                with self.subTest(ink=ink, ground=ground):
                    self.assertGreaterEqual(contrast(night[ink], night[ground]), 4.5)
        for ink, ground in (("--color-price", "--color-price-soft"), ("--color-brand-text", "--color-brand-soft"),
                            ("--color-fair-bad-ink", "--color-fair-bad-bg"), ("--color-price-ink", "--color-price")):
            with self.subTest(ink=ink, ground=ground):
                self.assertGreaterEqual(contrast(night[ink], night[ground]), 4.5)
        # The picture wells stay light, a shade softer than by day.
        self.assertGreater(luminance(night["--color-well"]), 0.75)

    def test_the_choice_overrides_the_device_either_way_and_old_browsers_keep_the_light_look(self):
        text = (CSS / "tokens.css").read_text()
        block = text[text.index("@supports (color: light-dark(#000, #fff))"):]
        self.assertIn("color-scheme: light dark;", block)
        self.assertIn(':root[data-theme="light"] { color-scheme: light; --page-scheme: light; }', block)
        self.assertIn(':root[data-theme="dark"] { color-scheme: dark; --page-scheme: dark; }', block)
        # Nothing dark is said outside the block a browser without light-dark() skips.
        self.assertNotIn("light-dark(", text[:text.index("@supports (color: light-dark(#000, #fff))")].split("*/")[-1])
        self.assertIn('@property --color-well { syntax: "<color>"; inherits: true;', text)


class WellTests(TestCase):
    """A shop's photo has a white background, which a well blends away. On a dark well that blend turns it to mud."""

    def test_every_well_a_photo_is_blended_into_is_marked_light(self):
        text = re.sub(r"/\*.*?\*/", "", (CSS / "site.css").read_text(), flags=re.S)   # without its comments
        rule = re.search(r"\.product-image, \.product-media,[^{]+\{ color-scheme: light; \}", text)
        self.assertIsNotNone(rule)
        light = {selector.strip() for selector in rule.group(0).split("{")[0].split(",")}
        blended = re.findall(r"([^{}]+)\{[^{}]*mix-blend-mode: multiply", text)
        self.assertGreaterEqual(len(blended), 4)
        for selectors in blended:
            for selector in selectors.split(","):
                well = selector.strip().split(" ")[0]
                with self.subTest(well=well):
                    self.assertIn(well, light)
        # And a well's ground is the well's own colour, never the page's quiet grey, which goes dark.
        for well in sorted(light - {".partners__link", ".spotlight__thumb", ".find__picture", ".cx-set__pic"}):
            bodies = re.findall(r"(?:^|[\s,}])" + re.escape(well) + r" \{([^}]*)\}", text)
            with self.subTest(ground=well):
                self.assertTrue(any("background: var(--color-well)" in body for body in bodies), bodies[:2])

    def test_the_stylesheet_names_no_colour_of_its_own(self):
        text = (CSS / "site.css").read_text()
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", text), [])


class PageTests(TestCase):
    def test_the_choice_is_applied_before_the_first_paint_and_offered_in_the_footer(self):
        html = self.client.get(reverse("web:about")).content.decode()
        head = html[:html.index("</head>")]
        self.assertIn('<meta name="color-scheme" content="light dark">', head)
        self.assertIn('<meta name="theme-color" content="#14161c" media="(prefers-color-scheme: dark)">', head)
        script = re.search(r'<script nonce="([^"]+)">\(function \(\) \{ try \{ var t = localStorage\.getItem\("ripraptor\.theme"\)', head)
        self.assertIsNotNone(script)
        self.assertLess(head.index("ripraptor.theme"), head.index("css/tokens.css"))   # before the colours are read
        # Hidden until site.js shows it: without script the page follows the device and there is nothing to press.
        self.assertIn('<div class="theme-choice" role="group" aria-label="Appearance" data-theme-choice hidden>', html)
        for value, words in (("", "Match my device"), ("light", "Light"), ("dark", "Dark")):
            self.assertRegex(html, rf'data-theme-set="{value}" aria-pressed="(true|false)">{words}</button>')

    def test_the_terms_say_the_choice_is_kept_in_the_browser(self):
        self.assertIn("Your choice of a light or dark look is kept there too.",
                      self.client.get(reverse("web:terms")).content.decode())
