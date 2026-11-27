"""Ways around a product page and what it promises. The owner, 27 Nov 2026, of the product page on a desktop screen:
"the layout on a product is crazy the image doesnt need to be this big. It would also do with some navi buttons to
movce around the page , such as compare prices pushes you down to the list , but maybe a another one down and another
back up again", and: "we should have the ripraptoer prmise easily available to read, like a seal of approval, trusted
ebay sellers, reviewed sites and we twelll you about good prices"."""

import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import views

CSS = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "site.css").read_text()


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.set = make_set(make_game())
        self.box = make_product(self.set, name="Surging Sparks Booster Box", slug="surging-sparks-booster-box")
        self.bundle = make_product(self.set, name="Surging Sparks Booster Bundle", slug="surging-sparks-booster-bundle")
        self.harbour = make_retailer("Harbour Games")
        make_listing(self.box, self.harbour, price="90.00")
        make_listing(self.bundle, self.harbour, price="30.00")

    def page(self, product=None):
        response = self.client.get((product or self.box).get_absolute_url())
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def rows(self, html):
        """The chips of each jump row, as [(address, words)]."""
        found = re.findall(r'<nav class="chips" aria-label="On this page"><ul class="chips__list">(.*?)</ul></nav>', html)
        return [re.findall(r'<a class="chip" href="([^"]+)"[^>]*>([^<]+)', row) for row in found]


class JumpTests(Base):
    def test_a_row_under_the_price_box_one_under_the_prices_and_one_at_the_foot(self):
        html = self.page()
        first, second, last = self.rows(html)
        parts = [("#offers-title", "Prices"), ("#history-title", "Price history"), ("#details-title", "Details"),
                 ("#promise-title", "Our promise"), ("#related-title", "More like this")]
        self.assertEqual(first, parts)                          # at the top, nowhere to go but down
        self.assertEqual(second, [("#top", "Top")] + parts)     # down again, or back up
        self.assertEqual(last, second)
        # In the page's order: under the price box, between the price list and the history, and after everything.
        places = [match.start() for match in re.finditer(r'<nav class="chips" aria-label="On this page">', html)]
        self.assertLess(html.index('class="buybox'), places[0])
        self.assertLess(places[0], html.index('id="offers-title"'))
        self.assertLess(html.index('id="offers-title"'), places[1])
        self.assertLess(places[1], html.index('id="history-title"'))
        self.assertLess(html.index('id="related-title"'), places[2])

    def test_every_chip_leads_to_a_heading_on_the_page_that_takes_focus(self):
        html = self.page()
        for address, _words in self.rows(html)[0]:
            self.assertRegex(html, rf'<h2 id="{address[1:]}"[^>]*tabindex="-1"')
        # Plain links, so they work without script; product.js only smooths the way. Top is the browser's own.
        self.assertIn('<a class="chip" href="#offers-title" data-jump>Prices</a>', html)
        self.assertIn('<a class="chip" href="#top">Top<span aria-hidden="true"> ↑</span></a>', html)

    def test_a_part_the_page_does_not_have_gets_no_chip(self):
        lone = make_product(make_set(make_game(name="Lorcana", slug="lorcana"), name="Azurite Sea", slug="azurite-sea"),
                            name="Azurite Sea Booster Box", slug="azurite-sea-booster-box")
        html = self.page(lone)                                  # nobody lists it, and nothing else is in its set
        first, last = self.rows(html)
        self.assertEqual(first, [("#details-title", "Details"), ("#promise-title", "Our promise")])
        self.assertEqual(last, [("#top", "Top")] + first)
        self.assertNotIn('id="offers-title"', html)

    def test_the_parts_stop_clear_of_what_is_stuck_to_the_top(self):
        self.assertIn("#offers-title, #history-title, #details-title, #promise-title, #related-title "
                      "{ scroll-margin-top: calc(var(--sticky-top, 0px) + var(--space-3)); }", CSS)


class DesktopLayoutTests(Base):
    """The owner, 27 Nov 2026, with an eBay product page beside ours: "still looks unbalnced, look an ebay product page
    it is all in far better proprotion. Please ensure design skill is used to lay out the destop product page more
    ffectively"."""

    def desk(self):
        block = CSS[CSS.index("@media (min-width: 60rem) {\n  /* A golden split: the picture takes the larger share"):]
        return block[:block.index("\n}\n")]

    def test_the_picture_takes_the_larger_share_and_the_name_and_price_box_are_a_narrow_rail(self):
        desk = self.desk()
        self.assertIn(".product-media, .product-head > .ask { float: left; clear: left; "
                      "width: calc(var(--split-major-share) - var(--split-gap) / 2); }", desk)
        self.assertIn(".product-intro, .buybox { float: right; clear: right; "
                      "width: calc(var(--split-minor-share) - var(--split-gap) / 2); }", desk)
        self.assertIn(".product-head { display: flow-root; }", desk)

    def test_the_picture_fits_the_first_screen_and_never_follows_the_rail(self):
        desk = self.desk()
        self.assertIn(".product-media { aspect-ratio: 1; max-height: var(--product-media-desk); }", desk)
        self.assertNotIn("stretch", desk)
        tokens = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "tokens.css").read_text()
        self.assertIn("--product-media-desk: clamp(20rem, 100vh - 14rem, 42rem);", tokens)

    def test_ask_rips_conversation_is_the_last_part_of_the_head_not_part_of_the_price_box(self):
        # Under the price box on a phone, under the picture on a desktop: so the rail stays short and the two
        # columns end near each other.
        with mock.patch("web.askrip.closed_reason", return_value=""):
            html = self.page()
        head = html[html.index('<div class="product-head'):html.index('<nav class="chips"')]
        buybox = head[head.index('<section class="buybox'):]
        buybox = buybox[:buybox.index("</section>") + len("</section>")]
        self.assertNotIn('id="ask"', buybox)
        self.assertEqual(head.count('<section class="ask" id="ask"'), 1)
        self.assertLess(head.index('<section class="buybox'), head.index('<section class="ask" id="ask"'))
        self.assertIn('.product-head { display: grid; grid-template-columns: minmax(0, 1fr); '
                      'grid-template-areas: "intro" "media" "buy" "ask";', CSS)
        self.assertIn(".product-head > .ask { grid-area: ask; min-width: 0; }", CSS)

    def test_the_rail_does_not_repeat_the_questions_ask_rips_open_column_already_gives(self):
        self.assertIn("@media (min-width: 80rem) { html.rip-side .product-intro .ask-strip { display: none; } }", CSS)


class PromiseTests(Base):
    TITLES = ["The real price", "An honest word on the price", "Shops we have looked at", "eBay sellers with a record"]

    def points(self, html):
        block = html[html.index('id="promise"'):]
        block = block[:block.index("</section>")]
        return block, re.findall(r'<div class="why__point"><dt><span class="why__mark" aria-hidden="true"><svg [^>]*>.*?</svg>'
                                 r'</span>([^<]+)</dt><dd>([^<]+)</dd></div>', block, re.S)

    def test_the_product_page_carries_the_promise(self):
        block, points = self.points(self.page())
        self.assertIn('<h2 id="promise-title" class="section-title" tabindex="-1">The RipRaptor promise</h2>', block)
        self.assertIn("Our job is to find you a good deal and tell you what we know about who is selling it.", block)
        self.assertEqual([title for title, _text in points], self.TITLES)
        self.assertEqual(self.page().count('id="promise"'), 1)

    def test_how_it_works_carries_it_too_and_every_footer_links_to_it(self):
        about = self.client.get("/about/").content.decode()
        _block, points = self.points(about)
        self.assertEqual([title for title, _text in points], self.TITLES)
        self.assertLess(about.index('id="promise"'), about.index('id="why-title"'))
        for address in ("/", "/about/", "/terms/", self.box.get_absolute_url()):
            self.assertIn('<a href="/about/#promise">Our promise</a>', self.client.get(address).content.decode(), address)

    def test_each_point_is_something_the_site_does(self):
        said = dict(self.points(self.page())[1])
        self.assertEqual(said["The real price"],
                         "Every price we compare includes delivery, so the cheapest is the cheapest you will pay.")
        self.assertIn("When a price is high, we say so.", said["An honest word on the price"])
        self.assertIn("A shop we mark Caution is never featured as a deal.", said["Shops we have looked at"])
        self.assertIn("sellers whose feedback meets our minimum", said["eBay sellers with a record"])
        # The eBay point rests on the least feedback and ratings the owner sets (Admin, eBay settings).
        from catalogue.models import EbaySettings

        self.assertGreater(EbaySettings._meta.get_field("min_feedback").default, 0)
        self.assertGreater(EbaySettings._meta.get_field("min_ratings").default, 0)

    def test_the_promise_costs_no_query_of_its_own(self):
        request = self.client.get("/about/").wsgi_request
        views.promise_points(request)                           # the request's copy is read once
        with CaptureQueriesContext(connection) as asked:
            self.assertEqual(len(views.promise_points(request)), 4)
        self.assertEqual(len(asked), 0)
