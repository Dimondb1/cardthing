"""One price history label per card and buy box, and the Featured deals disclosure (web/templates/web/includes/
badge.html, catalogue/offers.py). A site review on 23 Oct 2026 found cards saying "Lowest today" beside "Above
average", or "Lowest in 30 days" beside "Fair price": two judgements of the same price over different periods."""

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from catalogue.models import DailyLowestPrice, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class LabelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()
        self.set = make_set(make_game())
        self.shops = [make_retailer(name) for name in ("Harbour Games", "Northgate Cards")]
        # Down from £60 every day for three weeks to £55 today: a verified 30-day low.
        self.box = make_product(self.set, name="Prismatic Evolutions Booster Box", slug="pre-box",
                                product_type=Product.Type.BOOSTER_BOX)
        make_listing(self.box, self.shops[0], price="55.00")
        make_listing(self.box, self.shops[1], price="58.00")
        for back in range(1, 21):
            DailyLowestPrice.objects.create(product=self.box, date=self.today - timedelta(days=back),
                                            price=Decimal("60.00"))

    def card(self, url="/search/?q=prismatic"):
        html = self.client.get(url).content.decode()
        card = html[html.index('<li class="card'):]
        return card[:card.index("</li>")]

    def test_without_a_price_check_the_card_shows_the_verified_low(self):
        card = self.card()
        self.assertIn("Lowest in 30 days", card)
        self.assertNotIn('class="fair ', card)

    def test_with_a_price_check_that_is_the_cards_one_history_label(self):
        Product.objects.filter(pk=self.box.pk).update(price_band=4)   # Good price
        card = self.card()
        self.assertIn('class="fair fair--good fair--small"', card)
        self.assertNotIn("Lowest in 30 days", card)
        self.assertNotIn('class="badge badge--good"', card)

    def test_the_buy_box_follows_the_same_rule(self):
        html = self.client.get(self.box.get_absolute_url()).content.decode()
        box = html[html.index('class="buybox'):html.index('class="buy-now"')]
        fair = 'data-fair-open' in box
        self.assertEqual("Lowest in 30 days" in box, not fair)

    def test_lowest_today_is_never_a_badge(self):
        # Dearer than earlier in the week: once "Lowest today". Which shop is cheapest is the shop list's mark.
        DailyLowestPrice.objects.filter(product=self.box).delete()
        for back in range(1, 6):
            DailyLowestPrice.objects.create(product=self.box, date=self.today - timedelta(days=back),
                                            price=Decimal("50.00"))
        card = self.card()
        self.assertNotIn("Lowest today", card)
        self.assertNotIn('class="badge badge--good"', card)
        self.assertIn('<span class="shops__flag">Cheapest</span>', card)


class FeaturedDisclosureTests(TestCase):
    def test_the_line_sits_under_the_featured_deals_heading_with_how_it_works(self):
        cache.clear()
        game = make_game()
        product_set = make_set(game)
        shops = [make_retailer(name) for name in ("Harbour Games", "Northgate Cards")]
        box = make_product(product_set, name="Prismatic Evolutions Booster Box", slug="pre-box",
                           product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, shops[0], price="50.00")
        make_listing(box, shops[1], price="70.00")
        html = self.client.get("/").content.decode()
        self.assertIn('id="featured-title"', html)
        section = html[html.index('id="featured-title"'):]
        section = section[:section.index("</section>")]
        self.assertIn('<p class="section-head__note">Featured deals favour shops that pay us a commission. Price '
                      'comparisons are always cheapest delivered first, and no shop can pay to move up. '
                      '<a href="/about/">How it works</a></p>', section)
        # True to the product page: its shops are listed cheapest delivered first.
        page = self.client.get(box.get_absolute_url()).content.decode()
        self.assertLess(page.index("£50.00"), page.index("£70.00"))
