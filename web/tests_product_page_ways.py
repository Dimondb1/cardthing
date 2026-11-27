"""Ways around a product page and what it promises. The owner, 27 Nov 2026, of the product page on a desktop screen:
"the layout on a product is crazy the image doesnt need to be this big. It would also do with some navi buttons to
movce around the page , such as compare prices pushes you down to the list , but maybe a another one down and another
back up again", and: "we should have the ripraptoer prmise easily available to read, like a seal of approval, trusted
ebay sellers, reviewed sites and we twelll you about good prices"."""

import re
from pathlib import Path

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


class PictureTests(TestCase):
    def test_on_a_desktop_the_picture_is_a_square_that_never_follows_the_column_beside_it(self):
        desk = CSS[CSS.index("@media (min-width: 60rem) {\n  .product-head {"):]
        desk = desk[:desk.index("\n}\n")]
        self.assertIn(".product-media { align-self: start; aspect-ratio: 1; }", desk)
        self.assertNotIn("align-self: stretch", desk)
        # The smaller share of the width is the picture's: the name and the price box are what the page is for.
        self.assertIn("grid-template-columns: minmax(0, var(--split-minor)) minmax(0, var(--split-major));", desk)
        self.assertIn('grid-template-areas: "media intro" "media buy";', desk)


class PromiseTests(Base):
    TITLES = ["The real price", "An honest word on the price", "Shops we have looked at", "eBay sellers with a record"]

    def points(self, html):
        block = html[html.index('id="promise"'):]
        block = block[:block.index("</section>")]
        return block, re.findall(r'<div class="why__point"><dt>([^<]+)</dt><dd>([^<]+)</dd></div>', block)

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
