"""A shop's label on the public site (web/includes/shop_trust.html): "Shop checked" or "Caution" beside the shop's
price on a product page and under Buy now, with the owner's reason and a link to the shop's public reviews. The owner,
27 Nov 2026: "giving the user a bit more confidence in what they're buying and what shop they're buying from", and of
showing the labels: "build it". The rule that keeps a shop marked Caution out of the deals is tested where the deals
are (catalogue/tests_shop_trust.py, tests_black_friday.py, tests_story_deals.py, tests_askrip.py)."""

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalogue import shop_check
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

REVIEWS = "https://uk.reviews.example/review/harbourgames.example"
NOTE = "Shop checked and Caution are our own reading of a shop&#x27;s public reviews. They are not a guarantee."


class ProductPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box")
        self.harbour = make_retailer("Harbour Games")
        self.kestrel = make_retailer("Kestrel Cards")
        self.moor = make_retailer("Moor Toys")
        for shop, price in ((self.harbour, "90.00"), (self.kestrel, "100.00"), (self.moor, "110.00")):
            make_listing(self.box, shop, price=price)

    def page(self):
        cache.clear()
        response = self.client.get(self.box.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def row(self, html, shop):
        """The shop's row of the price list."""
        start = html.index(f'<span class="offer__name">{shop.name}</span>')
        return html[start:html.index("</li>", start)]

    def test_a_shop_with_no_label_or_not_yet_checked_shows_nothing(self):
        shop_check.confirm(self.kestrel, "new", "Too few public reviews for us to say yet.", REVIEWS)
        html = self.page()
        # (The promise lower down the page names the labels in a sentence: only the labels themselves are looked for.)
        for words in ("<strong>Shop checked</strong>", "<strong>Caution</strong>", "offer__trust", "How we label shops",
                      REVIEWS, "Too few public reviews"):
            self.assertNotIn(words, html)

    def test_a_checked_shop_says_so_on_its_row_and_under_buy_now_with_its_reviews(self):
        shop_check.confirm(self.harbour, "checked", "Mostly positive public reviews over several years.", REVIEWS)
        html = self.page()
        link = (f'<a href="{REVIEWS}" target="_blank" rel="nofollow noopener noreferrer">Reviews'
                '<span class="visually-hidden"> of Harbour Games (opens in a new tab)</span></a>')
        mark = ('<strong>Shop checked</strong><span aria-hidden="true"> · </span><span class="visually-hidden">. </span>'
                + link)
        self.assertIn(f'<span class="offer__trust">{mark}</span>', self.row(html, self.harbour))
        self.assertIn(f'<p class="buybox__seller">{mark}</p>', html)         # Harbour Games is the cheapest
        # The reason he gave is his own record of a good shop: only a Caution's is shown.
        self.assertNotIn("Mostly positive public reviews", html)
        # What the labels mean, once, with the way to the terms.
        self.assertEqual(html.count(NOTE), 1)
        self.assertIn(f'<p class="restock-note">{NOTE} <a href="/terms/#terms-shops">How we label shops</a></p>', html)
        # The other shops' rows are as they were.
        for shop in (self.kestrel, self.moor):
            self.assertNotIn("offer__trust", self.row(html, shop))
        # A review site is not a shop we send buyers to: its link is never a counted or a sponsored one.
        self.assertNotIn("sponsored", link)

    def test_a_checked_shop_with_no_link_shows_the_mark_alone(self):
        shop_check.confirm(self.kestrel, "checked", "Long-established shop with no pattern of complaints.", "")
        row = self.row(self.page(), self.kestrel)
        self.assertIn('<span class="offer__trust"><strong>Shop checked</strong></span>', row)
        self.assertNotIn("Reviews", row)

    def test_a_caution_shows_the_owners_reason_and_the_reviews(self):
        shop_check.confirm(self.harbour, "caution", "Mostly negative public reviews, including orders not arriving.",
                           REVIEWS)
        html = self.page()
        said = ('<strong>Caution</strong><span aria-hidden="true"> · </span><span class="visually-hidden">: </span>'
                f'Mostly negative public reviews, including orders not arriving. <a href="{REVIEWS}" target="_blank" '
                'rel="nofollow noopener noreferrer">Reviews')
        self.assertIn(f'<span class="offer__trust">{said}', self.row(html, self.harbour))
        self.assertIn(f'<p class="buybox__seller">{said}', html)             # under Buy now: it is the cheapest
        self.assertEqual(html.count(NOTE), 1)
        # Its price is still listed and compared: the label is beside it, and the buyer chooses.
        self.assertIn("£90.00", self.row(html, self.harbour))

    def test_a_caution_with_no_reason_or_link_is_the_one_word(self):
        shop_check.confirm(self.kestrel, "caution", "", "")
        self.assertIn('<span class="offer__trust"><strong>Caution</strong></span>', self.row(self.page(), self.kestrel))

    def test_the_owners_words_are_never_markup(self):
        shop_check.confirm(self.kestrel, "caution", 'Poor <b>reviews</b> & "late" refunds.', "")
        html = self.page()
        self.assertIn("Poor &lt;b&gt;reviews&lt;/b&gt; &amp; &quot;late&quot; refunds.", html)
        self.assertNotIn("<b>reviews</b>", html)

    def test_a_shop_switched_off_shows_neither_its_price_nor_its_label(self):
        shop_check.confirm(self.kestrel, "caution", "Mostly negative public reviews.", "")
        self.kestrel.is_active = False
        self.kestrel.save(update_fields=["is_active"])
        html = self.page()
        self.assertNotIn("Kestrel Cards", html)
        self.assertNotIn("<strong>Caution</strong>", html)
        self.assertNotIn("Mostly negative public reviews", html)

    def test_the_labels_cost_no_more_queries(self):
        with CaptureQueriesContext(connection) as plain:
            self.page()
        shop_check.confirm(self.harbour, "checked", "Mostly positive public reviews.", REVIEWS)
        shop_check.confirm(self.kestrel, "caution", "Mostly negative public reviews.", REVIEWS)
        with CaptureQueriesContext(connection) as labelled:
            html = self.page()
        self.assertIn("Shop checked", html)
        self.assertEqual(len(labelled), len(plain))


class TermsTests(TestCase):
    def test_the_terms_say_what_the_labels_mean_and_how_a_shop_asks_for_another_look(self):
        html = self.client.get("/terms/").content.decode()
        self.assertIn('<h2 id="terms-shops">Shop labels</h2>', html)
        self.assertIn("Shop checked means we have read the shop&#x27;s public reviews", html)
        self.assertIn("It is not a guarantee", html)
        self.assertIn("contact us and we will look again", html)
