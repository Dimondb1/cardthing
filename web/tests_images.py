"""Product pictures at the size they are shown, the search engine descriptions and the site's social accounts."""

import html
import json
import re
from pathlib import Path

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue import alerts
from catalogue.images import SLOTS, can_resize, responsive, sized_image_url
from catalogue.models import Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

SHOPIFY = "https://cdn.shopify.com/s/files/1/0548/7253/9391/files/etb-730071.png?v=1737992356"
SHOP_DOMAIN = "https://www.harbourgames.example/cdn/shop/files/etb.jpg?v=1712345678"
BIGCOMMERCE = ("https://cdn11.bigcommerce.com/s-b4ioc4fed9/images/stencil/500x659/products/419833/3422290/"
               "jBUuhjOif77IkEQINuhCXmphn__26810.1773215857.png?c=1")
BIGCOMMERCE_OTHER = ("https://cdn11.bigcommerce.com/s-b4ioc4fed9/products/151102/images/2687308/"
                     "8hMsW9QdgPnMg3W1DHKoWFpu1__64552.1762228120.386.513.jpg?c=1")
OTHER = "https://unicorncards.co.uk/images/thumbs/0071632_yugioh-booster-box-_960.jpeg"


class SizedImageUrlTests(TestCase):
    def test_shopify_gets_the_width_and_keeps_its_version(self):
        self.assertEqual(sized_image_url(SHOPIFY, 400), SHOPIFY + "&width=400")

    def test_shopify_without_a_query_gets_one(self):
        url = "https://cdn.shopify.com/s/files/1/0001/files/box.jpg"
        self.assertEqual(sized_image_url(url, 200), url + "?width=200")

    def test_a_shop_domain_serving_shopify_files_is_resized_too(self):
        self.assertEqual(sized_image_url(SHOP_DOMAIN, 200), SHOP_DOMAIN + "&width=200")

    def test_an_existing_width_is_replaced_never_repeated(self):
        url = "https://cdn.shopify.com/s/files/1/0001/files/box.jpg?width=1600&v=17&width=800"
        sized = sized_image_url(url, 400)
        self.assertEqual(sized, "https://cdn.shopify.com/s/files/1/0001/files/box.jpg?v=17&width=400")
        self.assertEqual(sized.count("width="), 1)
        self.assertEqual(sized_image_url(sized, 800).count("width="), 1)

    def test_the_rest_of_the_query_is_kept_byte_for_byte(self):
        url = "https://cdn.shopify.com/s/files/1/0001/files/box%20one.jpg?v=17&crop=center&x=a%2Bb"
        self.assertEqual(sized_image_url(url, 400), url + "&width=400")

    def test_bigcommerce_stencil_size_becomes_a_width(self):
        self.assertEqual(sized_image_url(BIGCOMMERCE, 400), BIGCOMMERCE.replace("/stencil/500x659/", "/stencil/400w/"))
        resized = sized_image_url(BIGCOMMERCE, 400)
        self.assertEqual(sized_image_url(resized, 800), BIGCOMMERCE.replace("/stencil/500x659/", "/stencil/800w/"))

    def test_other_addresses_are_left_alone(self):
        for url in (BIGCOMMERCE_OTHER, OTHER, "/media/products/box.jpg", "https://img.example/box.jpg",
                    "https://cdn.example.com/s/files/box.jpg", ""):
            with self.subTest(url=url):
                self.assertEqual(sized_image_url(url, 400), url)
                self.assertFalse(can_resize(url))

    def test_a_missing_or_bad_width_changes_nothing(self):
        for width in (None, 0, -5, "wide"):
            with self.subTest(width=width):
                self.assertEqual(sized_image_url(SHOPIFY, width), SHOPIFY)

    def test_responsive_offers_every_width_of_the_slot(self):
        picture = responsive(SHOPIFY, "card")
        self.assertEqual(picture["src"], SHOPIFY + "&width=200")
        self.assertEqual(picture["srcset"],
                         f"{SHOPIFY}&width=200 200w, {SHOPIFY}&width=400 400w, {SHOPIFY}&width=600 600w")
        self.assertEqual(picture["sizes"], SLOTS["card"][1])
        self.assertEqual(picture["full"], SHOPIFY)
        self.assertEqual(responsive(OTHER, "card"), {"src": OTHER, "srcset": "", "sizes": "", "full": OTHER})


CSS_DIR = Path(__file__).resolve().parent / "static" / "css"
ROOT_FONT = 16

# The width in CSS pixels of the picture itself (its box less the padding, and for the 16:9 cover and the swipe
# card the box's height, since a square picture is contained) measured in Chromium at each viewport width.
# "large" from 60rem depends on how tall the name and price box beside it is: 392 to 415 on a real product with
# one shop, 464 to 515 on one with several.
MEASURED = {
    "card": {320: 65, 375: 84, 414: 97, 480: 119, 600: 159, 700: 193, 767: 215,
             768: 84, 800: 89, 959: 116, 1024: 127, 1280: 154, 1920: 154},
    "cover": {320: 34, 375: 44, 414: 52, 540: 75, 700: 104, 767: 117,
              768: 70, 900: 87, 959: 94, 960: 53, 1024: 58, 1280: 71, 1920: 71},
    "trend": {375: 133, 414: 152, 480: 184, 600: 243, 700: 292, 767: 325,
              768: 123, 960: 123, 1024: 132, 1280: 160, 1920: 160},
    "large": {320: 272, 375: 272, 767: 272, 959: 272, 960: (392, 464), 1280: (405, 508), 1920: (415, 515)},
    "swipe": {375: 295, 414: 331, 480: 392, 600: 421, 767: 421, 768: 305, 1280: 305},
}


def _length(value, viewport):
    """A ``sizes`` length in CSS pixels: rem, vw, px or calc(<a>vw - <b>rem)."""
    value = value.strip()
    calc = re.fullmatch(r"calc\(([\d.]+)vw - ([\d.]+)rem\)", value)
    if calc:
        return float(calc.group(1)) * viewport / 100 - float(calc.group(2)) * ROOT_FONT
    unit = re.fullmatch(r"([\d.]+)(rem|vw|px)", value)
    number, kind = float(unit.group(1)), unit.group(2)
    return {"rem": number * ROOT_FONT, "vw": number * viewport / 100, "px": number}[kind]


def sizes_at(sizes, viewport):
    """The width the browser takes from ``sizes`` at ``viewport`` CSS pixels wide."""
    for part in re.split(r",\s*(?![^()]*\))", sizes):
        query = re.match(r"\(min-width: ([\d.]+)rem\)\s*(.+)", part.strip())
        if not query:
            return _length(part, viewport)
        if viewport >= float(query.group(1)) * ROOT_FONT:
            return _length(query.group(2), viewport)
    raise AssertionError(f"{sizes!r} has no default length")


def chosen(widths, sizes_px, density):
    """The copy a browser downloads: the smallest at least as wide as the slot at this density."""
    return next((width for width in sorted(widths) if width >= sizes_px * density), max(widths))


class SizesFollowTheLayoutTests(SimpleTestCase):
    """``sizes`` must describe the picture the CSS actually draws, or a 2x phone gets a blurry copy and a 1x
    desktop a needlessly large one."""

    def test_breakpoints_are_ones_the_stylesheet_uses(self):
        css = (CSS_DIR / "site.css").read_text()
        tokens = (CSS_DIR / "tokens.css").read_text()
        page = float(re.search(r"--page-width: ([\d.]+)rem", tokens).group(1))
        gutter = float(re.search(r"--gutter-wide: ([\d.]+)rem", tokens).group(1))
        deck = float(re.search(r"\.deck-page \{ max-width: ([\d.]+)rem", css).group(1))
        known = {float(value) for value in re.findall(r"@media \(min-width: ([\d.]+)rem\)", css)}
        known |= {page + 2 * gutter, deck}
        for slot, (_width, sizes, _widths) in SLOTS.items():
            for value in re.findall(r"min-width: ([\d.]+)rem", sizes):
                with self.subTest(slot=slot, breakpoint=value):
                    self.assertIn(float(value), known)

    def test_sizes_matches_the_measured_picture(self):
        for slot, measured in MEASURED.items():
            sizes = SLOTS[slot][1]
            for viewport, need in measured.items():
                low, high = need if isinstance(need, tuple) else (need, need)
                with self.subTest(slot=slot, viewport=viewport):
                    said = sizes_at(sizes, viewport)
                    self.assertGreaterEqual(said, low * 0.8)
                    self.assertLessEqual(said, high * 1.35)

    def test_every_screen_gets_a_sharp_copy_and_not_a_needlessly_large_one(self):
        for slot, measured in MEASURED.items():
            _width, sizes, widths = SLOTS[slot]
            for viewport, need in measured.items():
                largest_need = max(need) if isinstance(need, tuple) else need
                for density in (1, 2):
                    with self.subTest(slot=slot, viewport=viewport, density=density):
                        copy = chosen(widths, sizes_at(sizes, viewport), density)
                        # Sharp: within 5% of the pixels the screen draws.
                        self.assertGreaterEqual(copy, largest_need * density * 0.95)
                        # Not oversized: never more than about twice the width the screen draws, unless it is
                        # the smallest copy offered.
                        if copy > min(widths):
                            self.assertLessEqual(copy, largest_need * density * 2.2)

    def test_the_regressions_the_review_found(self):
        card, cover, trend, large = (SLOTS[name] for name in ("card", "cover", "trend", "large"))
        # A 2x screen 700px wide: the card picture is 193px, so it needs a 400 copy, not 200.
        self.assertEqual(chosen(card[2], sizes_at(card[1], 700), 2), 400)
        self.assertEqual(chosen(cover[2], sizes_at(cover[1], 767), 2), 400)
        self.assertEqual(chosen(trend[2], sizes_at(trend[1], 700), 2), 600)
        # A 1x desktop: the 400 to 515px hero takes the 600 copy, not the 1200 one.
        for viewport in (960, 1280, 1920):
            self.assertEqual(chosen(large[2], sizes_at(large[1], viewport), 1), 600)
        # A 2x tablet: the 84px card takes the 200 copy, not the 400 one.
        self.assertEqual(chosen(card[2], sizes_at(card[1], 768), 2), 200)


def img_tags(body, size):
    return re.findall(rf'<span class="product-image product-image--{size}"><img [^>]*>', body)


class PicturesOnPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.pre = make_set(cls.game)
        cls.etb = make_product(cls.pre, image_url=SHOPIFY)
        cls.bundle = make_product(cls.pre, name="Prismatic Evolutions Booster Bundle",
                                  product_type=Product.Type.BUNDLE, image_url=BIGCOMMERCE)
        cls.tin = make_product(cls.pre, name="Prismatic Evolutions Tin", product_type=Product.Type.TIN,
                               image_url=OTHER)
        shop = make_retailer("Harbour Games")
        for product in (cls.etb, cls.bundle, cls.tin):
            make_listing(product, shop, price="50.00")

    def test_product_page_hero_is_sized_eager_and_high_priority(self):
        body = self.client.get(self.etb.get_absolute_url()).content.decode()
        tags = img_tags(body, "large")
        self.assertEqual(len(tags), 1)
        tag = html.unescape(tags[0])
        self.assertIn(f'src="{SHOPIFY}&width=600"', tag)
        self.assertIn(f'srcset="{SHOPIFY}&width=600 600w, {SHOPIFY}&width=1200 1200w"', tag)
        self.assertIn(f'sizes="{SLOTS["large"][1]}"', tag)
        self.assertIn('loading="eager"', tag)
        self.assertIn('fetchpriority="high"', tag)
        self.assertIn('width="400" height="400"', tag)
        self.assertIn(f'data-full="{SHOPIFY}"', tag)
        self.assertNotIn('loading="lazy"', tag)

    def test_share_picture_and_structured_data_ask_for_a_large_copy(self):
        body = html.unescape(self.client.get(self.etb.get_absolute_url()).content.decode())
        self.assertIn(f'<meta property="og:image" content="{SHOPIFY}&width=1200">', body)
        start = body.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
        data = json.loads(body[start:body.index("</script>", start)])
        product = next(block for block in data if block["@type"] == "Product")
        self.assertEqual(product["image"], SHOPIFY + "&width=1200")

    def test_cards_on_list_pages_carry_srcset_and_load_lazily(self):
        body = self.client.get(self.pre.get_absolute_url()).content.decode()
        tags = [html.unescape(tag) for size in ("card", "row") for tag in img_tags(body, size)]
        self.assertTrue(tags)
        shopify = [tag for tag in tags if SHOPIFY in tag]
        bigcommerce = [tag for tag in tags if "bigcommerce" in tag]
        self.assertTrue(shopify and bigcommerce)
        for tag in shopify + bigcommerce:
            self.assertIn("srcset=", tag)
            self.assertIn("sizes=", tag)
            self.assertIn('loading="lazy"', tag)
            self.assertNotIn("fetchpriority", tag)
        self.assertRegex(shopify[0], r"width=\d+ \d+w, .*width=\d+ \d+w")
        shown = re.search(r'src="([^"]*)" srcset="([^"]*)"', bigcommerce[0]).groups()
        self.assertTrue(all("/stencil/" in part and "/stencil/500x659/" not in part for part in shown))

    def test_a_picture_that_cannot_be_resized_is_shown_as_it_is(self):
        body = html.unescape(self.client.get(self.tin.get_absolute_url()).content.decode())
        tag = img_tags(body, "large")[0]
        self.assertIn(f'src="{OTHER}"', tag)
        self.assertNotIn("srcset", tag)
        self.assertIn(f'<meta property="og:image" content="{OTHER}">', body)

    def test_pictures_add_no_queries(self):
        url = self.pre.get_absolute_url()
        self.client.get(url)
        with CaptureQueriesContext(connection) as with_pictures:
            self.client.get(url)
        Product.objects.update(image_url="")
        with CaptureQueriesContext(connection) as without:
            self.client.get(url)
        self.assertEqual(len(with_pictures), len(without))

    def test_home_page_pictures_are_sized(self):
        body = html.unescape(self.client.get(reverse("web:home")).content.decode())
        self.assertNotIn(f'src="{SHOPIFY}"', body)
        self.assertNotIn(f'"{BIGCOMMERCE}"', body)

    def test_swipe_deck_cards_carry_the_sized_picture(self):
        cards = {card["id"]: card for card in self.client.get(reverse("web:deck_api")).json()["cards"]}
        card = cards[self.etb.pk]
        width, _sizes, widths = SLOTS["swipe"]
        self.assertEqual(card["image"], f"{SHOPIFY}&width={width}")
        self.assertEqual(card["srcset"], ", ".join(f"{SHOPIFY}&width={each} {each}w" for each in widths))
        self.assertEqual(card["sizes"], SLOTS["swipe"][1])
        other = cards[self.tin.pk]
        self.assertEqual(card["full"], SHOPIFY)
        self.assertEqual((other["image"], other["srcset"], other["sizes"]), (OTHER, "", ""))

    @override_settings(RIPRAPTOR_SITE_URL="https://ripraptor.example")
    def test_emails_ask_for_an_email_sized_copy(self):
        _text, body = alerts.render_mail("back_in_stock", {"product": self.etb, "listing": None})
        self.assertIn(f'src="{SHOPIFY}&amp;width=400"', body)


class DescriptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.pre = make_set(cls.game)
        cls.etb = make_product(cls.pre)
        make_listing(cls.etb, make_retailer("Harbour Games"), price="50.00")

    def description(self, url):
        body = self.client.get(url).content.decode()
        return html.unescape(re.search(r'<meta name="description" content="([^"]*)">', body).group(1))

    def test_descriptions_fit_what_search_engines_show(self):
        for url in (reverse("web:home"), self.game.get_absolute_url(), self.pre.get_absolute_url(),
                    reverse("web:deals"), reverse("web:drops"), reverse("web:drops") + "?show=out"):
            with self.subTest(url=url):
                text = self.description(url)
                self.assertGreaterEqual(len(text), 120, text)
                self.assertLessEqual(len(text), 160, text)
        # A product's description grows with its name and shop, so only its floor is fixed.
        self.assertGreaterEqual(len(self.description(self.etb.get_absolute_url())), 120)

    def test_home_has_its_own_description_not_a_stub(self):
        text = self.description(reverse("web:home"))
        self.assertIn("UK", text)
        self.assertNotRegex(text, r"\d")   # no numbers that could go stale or be made up


class OrganizationTests(TestCase):
    def test_organization_names_the_sites_own_social_accounts(self):
        body = self.client.get(reverse("web:home")).content.decode()
        blocks = []
        for match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', body, re.S):
            data = json.loads(match.group(1))
            blocks.extend(data if isinstance(data, list) else [data])
        organization = next(block for block in blocks if block["@type"] == "Organization")
        self.assertEqual(organization["sameAs"], [
            "https://www.instagram.com/ripraptor_uk/",
            "https://www.facebook.com/ripraptor",
            "https://www.tiktok.com/@ripraptoruk",
        ])
        self.assertNotIn("LocalBusiness", body)
        for absent in ("twitter.com", "x.com/", "linkedin.com", "youtube.com", "fbq(", "connect.facebook.net"):
            self.assertNotIn(absent, body)
