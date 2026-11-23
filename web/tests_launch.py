"""The app's opening (site.css, The app's opening; includes/launch.html and launch_head.html; site.js). Opened from its
home screen icon, the phone shows its own launch screen, and the site carries on from it.

The owner, 23 Nov 2026, of the first version: Android's launch screen "fades out, then the site's intro fades in a Rip
twice the size: a double splash", the screen "never splits along" the zigzag, "the logo's off-white square background
shows as a box", Rip "stays visible as a ghost" and the page "appears in pieces". Wanted: the launch screen dark like
the site, no second raptor, the logo with no background, a real split along the zigzag, and the page "fully rendered
underneath before the reveal, so it appears in one go"."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web.views import LAUNCH_GROUND

STATIC = Path(settings.BASE_DIR) / "web" / "static"
TEMPLATES = Path(settings.BASE_DIR) / "web" / "templates" / "web"
CSS = (STATIC / "css" / "site.css").read_text(encoding="utf-8")
TOKENS = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
SCRIPT = (STATIC / "js" / "site.js").read_text(encoding="utf-8")
BLOCK = CSS[CSS.index("/* The app's opening ---"):CSS.index("/* Seasonal themes (web/seasons.py) ---")]
RULES = BLOCK.split("*/", 1)[1]


def milliseconds(token):
    return int(re.search(rf"  {token}: (\d+)ms;", TOKENS).group(1))


class LaunchScreenTests(TestCase):
    def test_the_phones_launch_screen_is_the_colour_the_site_opens_in(self):
        data = self.client.get(reverse("web:manifest")).json()
        self.assertEqual((data["background_color"], data["theme_color"]), (LAUNCH_GROUND, LAUNCH_GROUND))
        self.assertIn(f"  --launch-ground: {LAUNCH_GROUND};", TOKENS)
        # The site's dark page, and the same in either look: the launch screen cannot follow the device.
        self.assertIn(f"--color-paper: light-dark(#ffffff, {LAUNCH_GROUND});", TOKENS)
        self.assertNotIn("light-dark", re.search(r"  --launch-ground: [^;]+;", TOKENS).group(0))
        head = (TEMPLATES / "includes" / "launch_head.html").read_text(encoding="utf-8")
        self.assertIn(f'm.setAttribute("content", "{LAUNCH_GROUND}");', head)

    def test_the_icons_on_it_have_no_background_of_their_own(self):
        for name in ("img/icon-192.png", "img/icon-512.png"):
            with self.subTest(icon=name):
                icon = Image.open(finders.find(name))
                self.assertEqual(icon.mode, "RGBA")
                for corner in ((0, 0), (icon.width - 1, 0), (0, icon.height - 1), (icon.width - 1, icon.height - 1)):
                    self.assertEqual(icon.getpixel(corner)[3], 0)
                # The raptor is there, solid, in the brand's orange, about four fifths of the width as before.
                alpha = icon.getchannel("A")
                box = alpha.point(lambda a: 255 if a > 128 else 0).getbbox()
                self.assertGreater((box[2] - box[0]) / icon.width, 0.75)
                self.assertGreater(alpha.histogram()[255] / (icon.width * icon.height), 0.2)
                self.assertEqual({icon.getpixel((x, y))[:3] for x in range(0, icon.width, 7) for y in range(0, icon.height, 7)
                                  if icon.getpixel((x, y))[3] == 255}, {(255, 90, 31)})
        self.assertTrue(any(icon.get("purpose") == "maskable" for icon in self.client.get(reverse("web:manifest")).json()["icons"]))


class PageTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()))
        make_listing(self.product, make_retailer())

    def test_every_public_page_can_open_this_way_and_marks_it_before_the_first_paint(self):
        for url in (reverse("web:home"), self.product.get_absolute_url(), reverse("web:deals"), reverse("web:terms")):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                head = html[:html.index("</head>")]
                self.assertLess(head.index("ripraptor.launched"), head.index("css/tokens.css"))
                # The cover is the first thing in the page, so it is on the first paint, and hidden from readers.
                body = html[html.index("<body"):]
                self.assertLess(body.index('<div class="launch" aria-hidden="true">'), body.index('class="skip-link"'))
                cover = re.search(r'<div class="launch" aria-hidden="true">.*?<span class="launch__line"></span></div>', body).group(0)
                self.assertEqual(cover.count('class="launch__half '), 2)
                self.assertEqual(cover.count('class="launch__tear"'), 2)
                self.assertIn('<span class="launch__line"></span>', body)

    def test_only_from_the_home_screen_icon_once_a_launch_never_on_the_swipe_page_or_with_reduced_motion(self):
        head = (TEMPLATES / "includes" / "launch_head.html").read_text(encoding="utf-8")
        code = head[head.index("<script"):]
        self.assertIn('matchMedia("(display-mode: standalone), (display-mode: minimal-ui), (display-mode: fullscreen)").matches || navigator.standalone === true', code)
        self.assertIn("/[?&]intro=1(&|$)/.test(location.search)", code)
        self.assertIn('first = s.getItem("ripraptor.launched") === null', code)
        self.assertIn('if (app) s.setItem("ripraptor.launched", "1");', code)
        self.assertIn('swipe = location.pathname.indexOf("/swipe/") === 0', code)
        self.assertIn('still = matchMedia("(prefers-reduced-motion: reduce)").matches', code)
        self.assertIn('if (still || (asked === false && (app === false || first === false || swipe))) return;', code)
        # The browser's bar takes the launch colour too, and remembers its own for site.js to put back.
        self.assertIn('m.setAttribute("data-launch-was", m.getAttribute("content"));', code)
        self.assertNotIn("!", code)   # the house style reads scripts too

    def test_the_admin_and_the_visitor_check_have_no_opening(self):
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        admin = self.client.get(reverse("admin:index"), follow=True).content.decode()
        self.assertNotIn("ripraptor.launched", admin)
        self.assertNotIn('class="launch"', admin)
        check = (TEMPLATES / "botcheck.html").read_text(encoding="utf-8")
        self.assertNotIn("launch", check)


class OpeningTests(TestCase):
    def test_no_raptor_of_its_own_only_the_launch_colour_until_the_page_is_ready(self):
        # The phone's launch screen has the raptor; the site's first frame is the same colour with nothing on it.
        self.assertNotIn("raptor.png", RULES)
        self.assertNotIn("chomp", RULES)
        self.assertIn(".launch__half {\n    position: absolute; left: 0; right: 0; background: var(--launch-ground);", RULES)
        # The tear and the edges stay unseen until site.js says the page is ready.
        self.assertIn("background: var(--color-brand); transform: scaleX(0);", RULES)
        self.assertIn("background: var(--color-brand); opacity: 0;", RULES)
        # Ready means loaded, with its fonts and the pictures on screen, then a pause for the launch screen to go.
        part = SCRIPT[SCRIPT.index("// The app's opening"):]
        self.assertIn('if (document.readyState === "complete") resolve(); else addEventListener("load", resolve, { once: true });', part)
        self.assertIn("document.fonts ? document.fonts.ready : null, ...onScreen()", part)
        self.assertIn("img.decode().catch(() => {})", part)
        self.assertIn("const SETTLE = 350;", part)
        self.assertIn("const MOST = 2500;", part)
        self.assertIn('root.classList.add("is-launch-go")', part)

    def test_it_splits_along_the_tear_and_each_movement_is_short(self):
        go = RULES[RULES.index("/* The page is ready (site.js)"):RULES.index("@keyframes launch-tear")]
        self.assertIn(".is-launch-go .launch__line { animation: launch-tear var(--motion-slow) var(--ease-out) forwards, launch-gone var(--launch-split) steps(1, start) calc(var(--motion-slow) + var(--motion-fast)) forwards; }", go)
        self.assertIn(".is-launch-go .launch__tear { animation: launch-here var(--motion-slow) steps(1, end) forwards; }", go)
        self.assertIn(".is-launch-go .launch__half { animation: launch-up-go var(--launch-split) var(--ease-standard) calc(var(--motion-slow) + var(--motion-fast)) forwards; }", go)
        self.assertIn(".is-launch-go .launch__half--bottom { animation-name: launch-down-go; }", go)
        self.assertIn("@keyframes launch-up-go { to { transform: translateY(-100%); } }", RULES)
        self.assertIn("@keyframes launch-down-go { to { transform: translateY(100%); } }", RULES)
        # The halves fit along one row of teeth, and each orange edge rides with its half.
        self.assertIn('<div class="launch__half launch__half--top"><span class="launch__tear"></span></div>',
                      (TEMPLATES / "includes" / "launch.html").read_text(encoding="utf-8"))
        # Nothing over 400ms, only transform and opacity, every time a token.
        self.assertLessEqual(max(milliseconds(name) for name in ("--launch-split", "--motion-slow", "--motion-fast")), 400)
        for name, body in re.findall(r"@keyframes (launch-[a-z-]+) \{(.*)\}\n", RULES):
            with self.subTest(keyframes=name):
                self.assertEqual(set(re.findall(r"([a-z-]+):", body)) - {"transform", "opacity"}, set())
        for timing in re.findall(r"animation: ([^;]+);", RULES):
            self.assertNotRegex(timing, r"\d+m?s\b")

    def test_it_opens_by_itself_if_the_page_never_says_it_is_ready(self):
        self.assertIn("animation: launch-up var(--launch-split) var(--ease-standard) var(--launch-most) forwards;", RULES)
        self.assertIn("  --launch-most: 3s;", TOKENS)
        part = SCRIPT[SCRIPT.index("// The app's opening"):]
        self.assertIn("setTimeout(go, MOST);", part)
        self.assertIn("setTimeout(done, MOST + 2000);", part)
        self.assertIn('root.addEventListener("animationstart", (event) => { if (/^launch-up/.test(event.animationName)) restore(); });', part)
        self.assertIn('root.addEventListener("animationend", (event) => { if (/^launch-down/.test(event.animationName)) done(); });', part)

    def test_reduced_motion_has_none_the_pieces_take_no_taps_and_no_colour_is_written(self):
        moving = RULES[RULES.index("@media (prefers-reduced-motion: no-preference) {"):RULES.index("@keyframes launch-tear")]
        self.assertEqual(RULES.count(".is-launching"), moving.count(".is-launching"))
        self.assertIn(".launch { display: none; }", RULES)
        self.assertIn(".is-launching .launch { display: block; position: fixed; inset: 0; z-index: 40; pointer-events: none; }", moving)
        plain = re.sub(r'url\("data:image/svg\+xml,[^"]*"\)', "url()", RULES)
        self.assertNotRegex(plain, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|\d+px|\d+rem")
        for token in ("--launch-ground", "--launch-tooth", "--launch-split", "--launch-most", "--color-brand"):
            self.assertIn(f"var({token})", RULES)
