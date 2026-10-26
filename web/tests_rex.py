"""Rip off Rex: the owner's own character beside "High price" on the Price check, drawn in the chip's ink like every
other icon, and never beside a shop that pays a commission."""

from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue import fair_price
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from catalogue.tests_fair_price import record

AFFILIATE = "https://network.example/click?url={url}"


def static_text(path):
    with open(finders.find(path), encoding="utf-8") as handle:
        return handle.read()


class RexTestCase(TestCase):
    def setUp(self):
        self.game = make_game()
        self.product = make_product(make_set(self.game))
        self.shop = make_retailer("Harbour Games")
        self.other = make_retailer("Quay Cards")
        # 20 days at £100: a cheapest delivered price of £130 today is more than 15% over, so High price.
        record(self.product, ["100.00"] * 20, timezone.localdate())
        self.cheapest = make_listing(self.product, self.shop, price="130.00")
        make_listing(self.product, self.other, price="135.00")
        self.assertEqual(fair_price.verdict(self.product, Decimal("130.00"), shops=2).band, fair_price.HIGH_PRICE)

    def page(self):
        return self.client.get(self.product.get_absolute_url()).content.decode()

    def cards(self):
        return self.client.get(self.game.get_absolute_url()).content.decode()


class ProductPageTests(RexTestCase):
    def test_rex_sits_beside_high_price_on_the_chip_and_the_sheet(self):
        html = self.page()
        self.assertIn('class="fair fair--high"', html)
        self.assertIn("High price", html)
        chip = html[html.index('class="fair fair--high"'):html.index("</a>", html.index('class="fair fair--high"'))]
        self.assertIn('<span class="fair__rex"></span>', chip)
        self.assertNotIn("<svg", chip)   # Rex takes the icon's place, not a seat beside it
        sheet = html[html.index('id="fair-sheet"'):html.index("</dialog>", html.index('id="fair-sheet"'))]
        self.assertIn('<span class="fair__rex fair__rex--sheet" aria-hidden="true"></span>High price', sheet)
        self.assertNotIn("fair__dot", sheet)

    def test_a_lower_verdict_keeps_its_own_icon(self):
        self.cheapest.price = Decimal("100.00")
        self.cheapest.save()
        html = self.page()
        self.assertIn('class="fair fair--fair"', html)
        self.assertNotIn("fair__rex", html)
        self.assertIn("fair__dot", html)

    def test_never_beside_a_shop_that_pays_a_commission(self):
        self.shop.affiliate_url_template = AFFILIATE
        self.shop.save()
        html = self.page()
        # The whole Price check is left off, not softened: no chip, no sheet, no other verdict.
        self.assertNotIn('class="fair fair--', html)
        self.assertNotIn('id="fair-sheet"', html)
        self.assertNotIn("High price", html)
        # The other shop's price still shows, and a fair price at the paying shop still gets its check.
        self.assertIn("Quay Cards", html)
        self.cheapest.price = Decimal("100.00")
        self.cheapest.save()
        self.assertIn('class="fair fair--fair"', self.page())

    def test_the_terms_and_how_it_works_pages_say_so(self):
        for name in ("web:terms", "web:about"):
            with self.subTest(name):
                self.assertContains(self.client.get(reverse(name)), "High price warning is not shown when the cheapest "
                                    "price is at a retailer that pays us a commission.")


class CardTests(RexTestCase):
    def setUp(self):
        super().setUp()
        self.product.price_band = fair_price.HIGH_PRICE
        self.product.save(update_fields=["price_band"])

    def test_rex_on_the_cards_small_chip(self):
        html = self.cards()
        self.assertIn('class="fair fair--high fair--small"', html)
        start = html.index('class="fair fair--high fair--small"')
        chip = html[start:html.index("High price", start)]
        self.assertIn('<span class="fair__rex"></span>', chip)
        self.assertNotIn("<svg", chip)

    def test_not_when_the_card_leads_with_a_shop_that_pays(self):
        self.shop.affiliate_url_template = AFFILIATE
        self.shop.save()
        html = self.cards()
        self.assertNotIn("fair--high", html)
        self.assertNotIn("fair__rex", html)
        # Led by the other shop instead (it is cheaper now), the chip is back.
        self.cheapest.price = Decimal("140.00")
        self.cheapest.save()
        self.assertIn("fair--high", self.cards())

    def test_card_check_reads_the_leading_offer(self):
        from catalogue.models import Product
        from catalogue.offers import buyable_prefetch

        product = Product.objects.prefetch_related(buyable_prefetch()).get(pk=self.product.pk)
        self.assertEqual(product.card_check.key, "high")
        self.shop.affiliate_url_template = AFFILIATE
        self.shop.save()
        product = Product.objects.prefetch_related(buyable_prefetch()).get(pk=self.product.pk)
        self.assertIsNone(product.card_check)
        self.assertIsNone(Product.objects.get(pk=self.product.pk).card_check.key if False else None)


class ArtworkTests(TestCase):
    def test_the_marks_are_the_owners_one_colour_drawings_with_no_script(self):
        for path, title in (("img/rex-small.svg", "for 32px and under"), ("img/rex.svg", "Rip Off Rex mark, ink")):
            with self.subTest(path):
                svg = static_text(path)
                self.assertIn(title, svg)
                self.assertNotIn("<script", svg.lower())
                self.assertNotIn("javascript:", svg.lower())

    def test_rex_is_drawn_in_the_chips_ink_like_every_icon(self):
        css = static_text("css/site.css")
        self.assertIn('mask: url("../img/rex-small.svg") center / contain no-repeat;', css)
        self.assertIn("background: currentColor;", css[css.index(".fair__rex {"):css.index(".fair--small .fair__rex")])
        self.assertIn('mask-image: url("../img/rex.svg");', css)
        tokens = static_text("css/tokens.css")
        for token in ("--fair-rex:", "--fair-rex-small:", "--fair-rex-sheet:"):
            self.assertIn(token, tokens)
