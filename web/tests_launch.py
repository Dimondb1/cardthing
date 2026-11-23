"""The app's opening (site.css, The app's opening; includes/launch_head.html). The owner, 23 Nov 2026, of the site
opened from its home screen icon: "could we introduce a slicker intro animation". The first page since the icon was
tapped carries on from the phone's own launch screen: the raptor bites, an orange tear runs out from him and the
ground parts along it. Only there: a visitor from a search wants the price, not an opening."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

STATIC = Path(settings.BASE_DIR) / "web" / "static"
CSS = (STATIC / "css" / "site.css").read_text(encoding="utf-8")
TOKENS = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
BLOCK = CSS[CSS.index("/* The app's opening ---"):CSS.index("/* Seasonal themes (web/seasons.py) ---")]


def milliseconds(token):
    return int(re.search(rf"  {token}: (\d+)ms;", TOKENS).group(1))


class HeadScriptTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()))
        make_listing(self.product, make_retailer())

    def test_every_public_page_marks_a_launch_before_the_first_paint(self):
        for url in (reverse("web:home"), self.product.get_absolute_url(), reverse("web:deals"), reverse("web:terms")):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                head = html[:html.index("</head>")]
                script = re.search(r"<script nonce=\"[^\"]+\">(\(function \(\) \{ try \{ var s = sessionStorage.*?)</script>", head)
                self.assertIsNotNone(script)
                # Before the stylesheets, so the opening is there on the very first paint.
                self.assertLess(head.index("ripraptor.launched"), head.index("css/tokens.css"))

    def test_only_from_the_home_screen_icon_once_a_launch_and_never_on_the_swipe_page(self):
        script = (Path(settings.BASE_DIR) / "web" / "templates" / "web" / "includes" / "launch_head.html").read_text(encoding="utf-8")
        code = script[script.index("<script"):]
        # An installed app's window, however the phone names it, or the address that asks to see it.
        self.assertIn('matchMedia("(display-mode: standalone), (display-mode: minimal-ui), (display-mode: fullscreen)").matches || navigator.standalone === true', code)
        self.assertIn("/[?&]intro=1(&|$)/.test(location.search)", code)
        # Once: the launch is remembered for the session, so the next page opens as usual.
        self.assertIn('first = s.getItem("ripraptor.launched") === null', code)
        self.assertIn('if (app) s.setItem("ripraptor.launched", "1");', code)
        self.assertIn('swipe = location.pathname.indexOf("/swipe/") === 0', code)
        self.assertIn('if (asked || (app && first && swipe === false)) document.documentElement.classList.add("is-launching");', code)
        # The house style reads every page's words, scripts included: no exclamation mark anywhere in it.
        self.assertNotIn("!", code)

    def test_the_admin_and_the_visitor_check_have_no_opening(self):
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        self.assertNotIn("ripraptor.launched", self.client.get(reverse("admin:index"), follow=True).content.decode())
        check = (Path(settings.BASE_DIR) / "web" / "templates" / "web" / "botcheck.html").read_text(encoding="utf-8")
        self.assertNotIn("launch_head", check)


class OpeningTests(TestCase):
    def test_it_is_short_and_only_transform_and_opacity_move(self):
        hold, slow, normal, fast = (milliseconds(name) for name in ("--launch-hold", "--motion-slow", "--motion-normal", "--motion-fast"))
        # No movement is over 400ms (the project's rule), and the whole opening is under 600ms.
        self.assertLessEqual(max(hold, slow, normal, fast), 400)
        self.assertLess(hold + slow, 600)
        self.assertGreater(hold, fast)   # the tear is drawn inside the bite
        for name, body in re.findall(r"@keyframes (launch-[a-z]+) \{(.*)\}\n", BLOCK):
            with self.subTest(keyframes=name):
                self.assertEqual(set(re.findall(r"([a-z-]+):", body)) - {"transform", "opacity"}, set())
        self.assertEqual(set(re.findall(r"@keyframes (launch-[a-z]+)", BLOCK)), {"launch-up", "launch-down", "launch-out", "launch-rip", "launch-fade"})
        # Every duration and delay is a token.
        for timing in re.findall(r"animation: ([^;]+);", BLOCK):
            self.assertNotRegex(timing, r"\d+m?s\b")

    def test_reduced_motion_has_no_opening_and_the_page_under_it_always_takes_taps(self):
        rules = BLOCK[BLOCK.index("@media (prefers-reduced-motion: no-preference) {"):BLOCK.index("@keyframes launch-up")]
        # Every piece is drawn inside the no-preference block: with reduced motion nothing covers the page at all.
        self.assertEqual(BLOCK.count(".is-launching"), rules.count(".is-launching"))
        pieces = re.findall(r"  (\.is-launching[^{]*)\{([^}]*)\}", rules)
        drawn = [body for _selector, body in pieces if 'content: ""' in body]
        self.assertEqual(len(drawn), 3)   # the two halves (one rule), the tear, the raptor
        for body in drawn:
            self.assertIn("pointer-events: none", body)
            self.assertIn("position: fixed", body)
        # The halves stay gone and the raptor and the tear stay faded once they have played.
        self.assertIn("animation: launch-up var(--motion-slow) var(--ease-standard) var(--launch-hold) forwards;", rules)
        self.assertIn("launch-out var(--motion-fast) var(--ease-standard) var(--launch-hold) forwards", rules)
        self.assertIn("launch-fade var(--motion-normal) var(--ease-standard) var(--launch-hold) forwards", rules)

    def test_no_colour_or_size_is_written_in_it(self):
        plain = re.sub(r'url\("data:image/svg\+xml,[^"]*"\)', "url()", BLOCK.split("*/", 1)[1])
        self.assertNotRegex(plain, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|\d+px|\d+rem")
        for token in ("--launch-hold", "--launch-tooth", "--mark-launch", "--color-paper", "--color-brand"):
            self.assertIn(f"var({token})", BLOCK)
        for token in ("--launch-hold", "--launch-tooth", "--mark-launch"):
            self.assertIn(f"  {token}:", TOKENS)

    def test_the_pieces_are_taken_away_once_it_has_played(self):
        script = (STATIC / "js" / "site.js").read_text(encoding="utf-8")
        part = script[script.index("// The app's opening"):]
        self.assertIn('if (root.classList.contains("is-launching")) {', part)
        self.assertIn('if (event.animationName === "launch-down") done();', part)
        self.assertIn("setTimeout(done, 3000);", part)
