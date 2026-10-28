"""Share on the product page: Reddit's post form, links to copy for Instagram and TikTok, and the share sheet."""

import re
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.utils.html import escape

from catalogue.discord import when_words
from catalogue.models import Listing
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class ShareTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="30th Celebration Booster Pack", slug="30th-pack",
                                    product_type="booster_pack")
        self.listing = make_listing(self.product, make_retailer("Harbour Games"), price="4.99", delivery="2.99")

    def reddit_text(self):
        share = self.client.get(self.product.get_absolute_url()).context["share"]
        return parse_qs(urlsplit(share["reddit"]).query)["text"][0]

    def test_the_reddit_post_has_text_under_its_title(self):
        # The owner, 28 Oct 2026: "include some text in the body ... so it looks like a reddit post".
        when = when_words(self.listing.last_checked)
        self.assertEqual(self.reddit_text(), f"Cheapest UK price right now: £7.98 delivered, checked {when}. "
                                             "The link compares every UK shop's price with delivery included.")
        self.assertNotIn("Harbour", self.reddit_text())   # the page compares every shop; the post names none
        Listing.objects.filter(pk=self.listing.pk).update(delivery_known=False)
        self.assertIn("Cheapest UK price right now: £4.99 plus delivery, checked", self.reddit_text())
        Listing.objects.filter(pk=self.listing.pk).update(delivery_known=True, availability=Listing.Availability.PREORDER)
        self.assertIn("Cheapest UK pre-order price right now: £7.98 delivered, checked", self.reddit_text())
        Listing.objects.filter(pk=self.listing.pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.reddit_text(), "No UK shop we compare has it in stock right now. The link shows every UK "
                                             "shop's price and stock as it changes.")

    def test_share_sits_next_to_save_and_its_panel_works_without_script(self):
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, 'href="#share" aria-controls="share" data-panel-open><svg class="icon-share"')
        self.assertContains(page, '</svg>Share<span class="visually-hidden"> 30th Celebration Booster Pack</span>')
        self.assertContains(page, 'id="share"')
        # Without script: Reddit is a plain link and the address is in a box to copy.
        self.assertContains(page, '<input id="share-link" class="alert-box__input share-box__link" type="text" '
                                  'value="http://testserver/products/30th-pack/" readonly data-share-link>')
        # Shown whether or not alert emails are set up.
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertContains(self.client.get(self.product.get_absolute_url()), 'href="#share"')

    def test_each_network_gets_the_product_link_marked_with_where_it_was_shared(self):
        share = self.client.get(self.product.get_absolute_url()).context["share"]
        reddit = urlsplit(share["reddit"])
        self.assertEqual((reddit.netloc, reddit.path), ("www.reddit.com", "/submit"))
        asked = parse_qs(reddit.query)
        self.assertEqual(asked["title"], ["30th Celebration Booster Pack: UK prices compared"])
        for link, source in ((asked["url"][0], "reddit"), (share["instagram"], "instagram"),
                             (share["tiktok"], "tiktok"), (share["copy"], "copy_link"), (share["native"], "share_sheet")):
            with self.subTest(source=source):
                parts = urlsplit(link)
                self.assertEqual(parts.path, "/products/30th-pack/")
                self.assertEqual(parse_qs(parts.query), {"utm_source": [source], "utm_medium": ["social"],
                                                         "utm_campaign": ["product_share"]})
        page = self.client.get(self.product.get_absolute_url())
        # The official logos, each named for screen readers by what it does.
        for label, logo in (("Post to Reddit (opens in a new tab)", "reddit"), ("Copy link for Instagram", "instagram"),
                            ("Copy link for TikTok", "tiktok")):
            with self.subTest(logo=logo):
                self.assertRegex(page.content.decode(), rf'aria-label="{re.escape(label)}"[^>]*><span class="share-box__logo '
                                                        rf'share-box__logo--{logo}"><svg class="share-box__mark"')
        self.assertContains(page, "Paste it into a story&#x27;s link sticker")
        # A shared link is the same page: it keeps its plain canonical address.
        tagged = self.client.get(share["instagram"].replace("http://testserver", ""))
        self.assertContains(tagged, '<link rel="canonical" href="http://testserver/products/30th-pack/">')


class CardShareTests(TestCase):
    """Each product card has a share icon that opens the page's one Share sheet, or without script the product
    page's own Share panel."""

    def setUp(self):
        self.set = make_set(make_game())
        self.product = make_product(self.set, name="30th Celebration Booster Pack", slug="30th-pack",
                                    product_type="booster_pack")
        self.listing = make_listing(self.product, make_retailer("Harbour Games"), price="4.99", delivery="2.99")

    def test_a_card_shares_its_product_through_the_page_sheet(self):
        for url in (self.set.get_absolute_url(), self.set.game.get_absolute_url(), "/search/?q=celebration"):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertContains(page, 'class="button button--quiet button--icon card__share" href="/products/30th-pack/#share" '
                                          'data-card-share="30th Celebration Booster Pack"')
                self.assertContains(page, '<span class="visually-hidden">Share 30th Celebration Booster Pack</span>')
                # One sheet for the page, filled by share.js for the card tapped.
                self.assertContains(page, "data-share-dialog", count=1)
                self.assertContains(page, 'data-reddit-title="{product}: UK prices compared"')
                self.assertContains(page, "js/share.js")
                # Each card carries its own post text, the product page's words for its cheapest price.
                when = when_words(self.listing.last_checked)
                self.assertContains(page, 'data-card-reddit="' + escape(
                    f"Cheapest UK price right now: £7.98 delivered, checked {when}. The link compares every UK "
                    "shop's price with delivery included.") + '"')

    def test_without_script_the_icon_opens_the_product_page_share_panel(self):
        page = self.client.get("/products/30th-pack/")
        self.assertContains(page, '<div class="buybox-panel share-box" id="share" data-share')
