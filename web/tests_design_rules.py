"""The site's layout and design rules (web/design.py), enforced: a page that breaks one fails here. The owner, 25 Nov
2026: "we need this site to stick to strict layout and design rules and should be implemented and enforced"."""

import re
from html.parser import HTMLParser
from pathlib import Path

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse

from catalogue.models import SiteLook
from web import design, look, urls

from .tests import PageTestCase

WEB = Path(__file__).resolve().parent
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
PRODUCT_LINK = re.compile(r"^/products/[^/]+/(?:#.*)?$")
LOOKS = (look.CLASSIC, look.COMPACT, look.FEED)


class Reader(HTMLParser):
    """Reads a page for what the rules ask about: the body's classes, its main headings, and for each link to a
    product's page the classes of everything it sits inside."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.products, self.h1, self.body = [], [], 0, ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "body":
            self.body = attrs.get("class") or ""
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and PRODUCT_LINK.match(attrs.get("href") or ""):
            self.products.append({name for held in self.stack for name in held} | set(classes))
        if tag not in VOID:
            self.stack.append(classes)

    def handle_endtag(self, tag):
        if tag not in VOID and self.stack:
            self.stack.pop()


def read(html):
    reader = Reader()
    reader.feed(html)
    return reader


class RulesCase(PageTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def wear(self, mode):
        SiteLook.objects.update_or_create(pk=1, defaults={"compact": mode == look.COMPACT, "feed_home": mode == look.FEED})
        cache.clear()
        look.remember()

    def list_urls(self):
        """An address of each LIST page."""
        return {
            "home": reverse("web:home"),
            "search": reverse("web:search") + "?q=etb",
            "game": self.game.get_absolute_url(),
            "set": self.pre.get_absolute_url(),
            "per_card": reverse("web:per_card"),
            "black_friday": reverse("web:black_friday"),
            "black_friday_game": reverse("web:black_friday_game", args=[self.game.slug]),
        }

    def pages(self):
        return [url for url in self.public_urls() if not url.startswith("/go/")] + list(self.list_urls().values())


class KindTests(RulesCase):
    def test_every_address_is_a_page_of_a_kind_or_named_as_not_a_page(self):
        names = {pattern.name for pattern in urls.urlpatterns if pattern.name}
        known = set(design.PAGES) | design.NOT_PAGES
        self.assertEqual(names - known, set(), "Give each new address a kind in web/design.py (PAGES), or list it in "
                                                "NOT_PAGES when it is not a page.")
        self.assertEqual(known - names, set(), "web/design.py names addresses that no longer exist.")
        self.assertEqual(set(design.PAGES) & design.NOT_PAGES, set())
        self.assertEqual(set(design.PAGES.values()), {design.LIST, design.STANDARD})

    def test_every_list_page_is_tried_here(self):
        lists = {name for name, kind in design.PAGES.items() if kind == design.LIST}
        self.assertEqual(set(self.list_urls()), lists, "Add the new LIST page's address to list_urls().")
        # The pages still to bring over are standard pages until they are.
        for name in design.LIST_LATER:
            self.assertEqual(design.PAGES[name], design.STANDARD)


class ShellTests(RulesCase):
    def test_a_list_page_wears_the_compact_look(self):
        self.wear(look.COMPACT)
        for name, url in self.list_urls().items():
            html = self.client.get(url).content.decode()
            page = read(html)
            with self.subTest(page=name):
                self.assertIn("compact-look", page.body.split())
                self.assertIn('class="feed-dock"', html)                       # the search box and games at the foot
                self.assertTrue('class="cx-status"' in html or "cx-hero" in html)   # the numbers
                self.assertNotIn('class="cards"', html)                        # its products are rows, never cards
                self.assertNotIn('class="page-title"', html)                   # and no standard page's big heading

    def test_a_list_page_in_the_classic_look_has_no_dock(self):
        self.wear(look.CLASSIC)
        for name, url in self.list_urls().items():
            html = self.client.get(url).content.decode()
            with self.subTest(page=name):
                self.assertEqual(read(html).body.split(), [])
                self.assertNotIn('class="feed-dock"', html)
                self.assertNotIn('class="crows"', html)

    def test_a_standard_page_keeps_the_usual_shell_in_every_look(self):
        standard = [reverse("web:deals"), reverse("web:drops"), reverse("web:prices"), reverse("web:about"),
                    reverse("web:terms"), reverse("web:watchlist"), self.etb.get_absolute_url()]
        for mode in LOOKS:
            self.wear(mode)
            for url in standard:
                html = self.client.get(url).content.decode()
                with self.subTest(look=mode, url=url):
                    self.assertNotIn("compact-look", read(html).body.split())
                    self.assertNotIn('class="feed-dock"', html)

    def test_every_page_has_one_main_heading(self):
        for mode in LOOKS:
            self.wear(mode)
            for url in self.pages():
                response = self.client.get(url)
                if response.status_code != 200:
                    continue
                with self.subTest(look=mode, url=url):
                    self.assertEqual(read(response.content.decode()).h1, 1)


class ProductPartTests(RulesCase):
    def test_a_product_is_only_shown_through_a_shared_part(self):
        allowed = design.PRODUCT_PARTS | design.PROSE
        for mode in LOOKS:
            self.wear(mode)
            for url in self.pages():
                response = self.client.get(url)
                if response.status_code != 200:
                    continue
                for held in read(response.content.decode()).products:
                    with self.subTest(look=mode, url=url):
                        self.assertTrue(held & allowed, f"A product link outside every shared part: inside {sorted(held)}")


class StylesheetTests(SimpleTestCase):
    def setUp(self):
        self.css = re.sub(r"/\*.*?\*/", "", (WEB / "static/css/site.css").read_text(), flags=re.S)

    def test_sizes_weights_corners_and_fonts_are_tokens(self):
        for prop, bare in design.TOKEN_ONLY.items():
            values = [found.strip() for found in re.findall(r"(?<![\w-])" + prop + r"\s*:\s*([^;}]+)", self.css)]
            own = {value for value in values if "var(" not in value and value not in design.PLAIN}
            with self.subTest(property=prop):
                self.assertEqual(own - bare, set(), f"{prop} takes its value from tokens.css")

    def test_no_more_bare_lengths_or_timings_than_before(self):
        rules = re.sub(r"@media[^{]*\{", "{", self.css)   # a breakpoint cannot be a token (test_only_the_sites_breakpoints)
        found = {
            "px": len(re.findall(r"(?<![\w.-])\d+(?:\.\d+)?px\b", rules)),
            "rem": len(re.findall(r"(?<![\w.-])\d*\.?\d+rem\b", rules)),
            "time": len(re.findall(r"(?<![\w.-])\d+(?:\.\d+)?m?s\b", rules)),
        }
        # The numbers are exact, so one that fell is lowered in web/design.py and cannot creep back up.
        self.assertEqual(found, design.RAW_MOST, "Fewer than before: lower RAW_MOST in web/design.py to match."
                         if all(found[unit] <= most for unit, most in design.RAW_MOST.items()) else
                         "A new rule in site.css uses a value of its own: use a token from tokens.css.")

    def test_only_the_sites_breakpoints(self):
        used = set(re.findall(r"\((?:min|max)-width: ([0-9.]+)rem\)", self.css))
        self.assertEqual(used - design.BREAKPOINTS, set())


class TemplateTests(SimpleTestCase):
    def test_a_template_styles_nothing_itself(self):
        root = WEB / "templates"
        for path in sorted((root / "web").rglob("*.html")):
            name = str(path.relative_to(root))
            if "/email/" in name:
                continue   # an email carries its own styles: no stylesheet reaches it
            text = re.sub(r"\{#.*?#\}", "", path.read_text())
            found = (len(re.findall(r'\sstyle="', text)), len(re.findall(r"<style\b", text)))
            attributes, blocks = design.TEMPLATE_STYLES.get(name, (0, 0))
            with self.subTest(template=name):
                self.assertLessEqual(found[0], attributes, "A style attribute: give the element a class in site.css.")
                self.assertLessEqual(found[1], blocks, "A style block: the rule belongs in site.css.")
