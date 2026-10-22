"""Every click to a shop says where it came from and which button it was: the page, the button, the shop, the
product and its game, saved on our server and sent to Analytics as affiliate_click (catalogue/clicks.py)."""

import json
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import DailyPageView, OutboundClick, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

BROWSER = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"


class AffiliateClickTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.product = make_product(make_set(cls.game), slug="pre-etb")
        cls.harbour = make_retailer("Harbour Games")
        cls.listing = make_listing(cls.product, cls.harbour, price="50.00", delivery="0.00")
        cls.north = make_retailer("Northgate Cards")
        make_listing(cls.product, cls.north, price="55.00", delivery="0.00")

    def setUp(self):
        cache.clear()

    def click(self, url, referer="http://testserver/products/pre-etb/"):
        headers = {"HTTP_USER_AGENT": BROWSER}
        if referer:
            headers["HTTP_REFERER"] = referer
        return self.client.get(url, **headers)

    def test_a_click_keeps_its_page_button_and_the_days_visitor_token(self):
        self.click(self.listing.get_outbound_url() + "?b=buy_now")
        row = OutboundClick.objects.get()
        self.assertEqual((row.source, row.button, row.suspect), ("product", "buy_now", False))
        # The same one-way token the visitor's day is counted under, so a visit that clicked can be counted.
        self.assertEqual(len(row.visitor), 32)
        self.click(self.listing.get_outbound_url() + "?b=card", referer="http://testserver/games/pokemon/")
        self.click(self.listing.get_outbound_url() + "?b=deal", referer="http://testserver/deals/")
        self.click(self.listing.get_outbound_url() + "?b=swipe", referer="http://testserver/swipe/")
        self.assertEqual(list(OutboundClick.objects.order_by("pk").values_list("source", "button"))[1:],
                         [("game", "card"), ("deals", "deal"), ("swipe", "swipe")])
        self.assertEqual(len(set(OutboundClick.objects.values_list("visitor", flat=True))), 1)

    def test_a_button_or_page_it_does_not_know_is_left_blank(self):
        self.click(self.listing.get_outbound_url() + "?b=<script>", referer="http://testserver/about/")
        self.click(self.listing.get_outbound_url())
        self.assertEqual(list(OutboundClick.objects.order_by("pk").values_list("source", "button")),
                         [("", ""), ("product", "")])
        # A watchlist link from before the button was named still says where it came from.
        self.click(self.listing.get_outbound_url() + "?from=watchlist", referer="")
        self.assertEqual(OutboundClick.objects.order_by("pk").last().source, "watchlist")

    def test_every_link_to_a_shop_names_its_button_and_what_analytics_reports(self):
        html = self.client.get(self.product.get_absolute_url()).content.decode()
        go = self.listing.get_outbound_url()
        self.assertIn(f'<a class="buy-now" href="{go}?b=buy_now" data-click="buy_now" data-click-shop="Harbour Games" '
                      f'data-click-product="{self.product.name}" data-click-game="Pokémon"', html)
        self.assertIn(f'href="{go}?b=price_list" data-click="price_list" data-click-shop="Harbour Games"', html)
        self.assertIn('data-click="price_list" data-click-shop="Northgate Cards"', html)
        self.assertIn('data-page="product"', html)
        self.assertIn('data-compare="buy_box"', html)
        cards = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn(f'href="{go}?b=card" data-click="card"', cards)
        self.assertIn('data-compare="card"', cards)
        self.assertIn('data-page="game"', cards)
        # No link to a shop is left without its button.
        for page in (html, cards):
            self.assertNotIn(f'href="{go}"', page)

    def test_the_swipe_cards_link_names_the_swipe_button_and_its_game(self):
        data = json.loads(self.client.get(reverse("web:deck_api")).content)
        card = next(item for item in data["cards"] if item["name"] == self.product.name)
        self.assertEqual(card["buy"], f"{self.listing.get_outbound_url()}?b=swipe")
        self.assertEqual(card["game"], "Pokémon")

    @override_settings(RIPRAPTOR_EBAY_CAMPAIGN_ID="5339999999")
    def test_a_marketplace_search_is_saved_against_the_sites_ebay_shop_when_there_is_one(self):
        url = reverse("web:go_search", args=[self.product.slug, "ebay"]) + "?b=ebay"
        self.click(url)
        self.assertFalse(OutboundClick.objects.exists())   # no eBay shop: nothing to count it against
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY, website="https://www.ebay.co.uk/")
        response = self.click(url)
        self.assertEqual(response.status_code, 302)
        row = OutboundClick.objects.get()
        self.assertEqual((row.retailer, row.listing, row.button, row.source), (ebay, None, "ebay", "product"))

    def test_a_compare_tap_is_counted_by_where_it_was_and_the_page(self):
        note = reverse("web:note_api")
        for query in ("what=compare&where=buy_box&page=product", "what=compare&where=card&page=game",
                      "what=compare&where=buy_box&page=product", "what=compare&where=nowhere&page=product",
                      "what=compare&where=card&page=admin"):
            self.client.get(f"{note}?{query}", HTTP_USER_AGENT=BROWSER)
        rows = dict(DailyPageView.objects.filter(kind=DailyPageView.Kind.TAPS).values_list("key", "hits"))
        self.assertEqual(rows, {"buy_box:product": 2, "card:game": 1})
