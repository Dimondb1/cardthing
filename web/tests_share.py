"""Share on the product page: Reddit's post form, links to copy for Instagram and TikTok, and the share sheet."""

from urllib.parse import parse_qs, urlsplit

from django.test import TestCase

from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class ShareTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="30th Celebration Booster Pack", slug="30th-pack",
                                    product_type="booster_pack")
        make_listing(self.product, make_retailer("Harbour Games"), price="4.99", delivery="2.99")

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
                self.assertContains(page, f'aria-label="{label}"><span class="share-box__logo share-box__logo--{logo}">'
                                          '<svg class="share-box__mark"')
        self.assertContains(page, "Paste it into a story&#x27;s link sticker")
        # A shared link is the same page: it keeps its plain canonical address.
        tagged = self.client.get(share["instagram"].replace("http://testserver", ""))
        self.assertContains(tagged, '<link rel="canonical" href="http://testserver/products/30th-pack/">')
