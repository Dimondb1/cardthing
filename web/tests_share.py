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


class PostTests(TestCase):
    """Share as post (web/share.py): a square picture of the product and its price, and a caption, for an Instagram
    post. The owner, 5 Nov 2026: "a share as post button so it created an insta post with a realistic not spammy
    caption"."""

    def setUp(self):
        self.game = make_game()
        self.product = make_product(make_set(self.game), name="Surging Sparks Booster Box", slug="surging-box",
                                    product_type="booster_box", image_url="https://cdn.example/surging.jpg")
        self.harbour, self.northgate = make_retailer("Harbour Games"), make_retailer("Northgate Cards")
        self.listing = make_listing(self.product, self.harbour, price="87.00", delivery="0.00")
        make_listing(self.product, self.northgate, price="95.00", delivery="0.00")
        self.url = f"/products/{self.product.slug}/post/"

    def caption(self):
        response = self.client.get(self.url)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        return response.json()

    def test_the_caption_reads_as_a_post_the_owner_would_write(self):
        found = self.caption()
        when = when_words(self.listing.last_checked)
        self.assertEqual(found["caption"], (
            "Surging Sparks Booster Box\n\n"
            f"£87.00 delivered today, the cheapest of 2 UK shops we compare. Checked {when}.\n\n"
            "Every UK shop's delivered price, compared at ripraptor.com. Link in bio. Scan the code in the picture for "
            "today's price.\n\n"
            "#pokemontcg #pokemoncards #tcg #sealedproduct #ukcollectors"))
        self.assertEqual(found["image"], f"http://testserver/products/{self.product.slug}/post.png")
        self.assertEqual(found["name"], "Surging Sparks Booster Box")
        for mark in ("!", "—"):
            self.assertNotIn(mark, found["caption"])
        self.assertLessEqual(found["caption"].count("#"), 6)

    def test_the_caption_says_one_shop_a_pre_order_or_none_and_what_the_price_check_makes_of_it(self):
        from catalogue import fair_price
        from catalogue.models import Product

        Listing.objects.filter(retailer=self.northgate).delete()
        self.assertIn("£87.00 delivered today at the one UK shop we found it at.", self.caption()["caption"])
        Product.objects.filter(pk=self.product.pk).update(price_band=fair_price.GOOD_PRICE)
        self.assertIn("at the one UK shop we found it at. A good price by our Price check. Checked", self.caption()["caption"])
        Product.objects.filter(pk=self.product.pk).update(price_band=fair_price.LOWEST)
        self.assertIn("The lowest price we have seen for it.", self.caption()["caption"])
        Listing.objects.filter(pk=self.listing.pk).update(availability=Listing.Availability.PREORDER, delivery_known=False)
        caption = self.caption()["caption"]
        self.assertIn("On pre-order at £87.00 plus delivery, the cheapest of the UK shops taking orders.", caption)
        Listing.objects.filter(pk=self.listing.pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        caption = self.caption()["caption"]
        self.assertIn("No UK shop we compare has it in stock right now.", caption)
        self.assertNotIn("Checked", caption)
        self.assertIn("#pokemontcg", caption)

    def test_a_game_without_its_own_tags_gets_the_general_ones_only(self):
        other = make_game("Battle Spirits Saga", "battle-spirits-saga")
        product = make_product(make_set(other, "Dawn", "dawn", code="BSS"), name="Dawn Booster Box", slug="dawn-box")
        make_listing(product, self.harbour, price="60.00")
        self.assertTrue(self.client.get(f"/products/{product.slug}/post/").json()["caption"].endswith(
            "\n\n#tcg #sealedproduct #ukcollectors"))

    def test_a_product_nobody_can_see_has_no_post(self):
        from catalogue.models import Product

        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(f"/products/{self.product.slug}/post.png").status_code, 404)

    def test_the_picture_is_a_square_png_with_the_shops_photo_when_it_comes_and_without_it_when_not(self):
        from io import BytesIO
        from unittest import mock

        from django.core.cache import cache
        from PIL import Image


        cache.clear()
        self.addCleanup(cache.clear)
        photo = BytesIO()
        Image.new("RGB", (600, 400), "#ff0000").save(photo, "PNG")
        with mock.patch("catalogue.importers.fetch", return_value=photo.getvalue()) as fetched:
            response = self.client.get(f"/products/{self.product.slug}/post.png")
            self.assertEqual(response["Content-Type"], "image/png")
            self.assertEqual(response["X-Robots-Tag"], "noindex")
            with Image.open(BytesIO(response.content)) as picture:
                self.assertEqual((picture.size, picture.format), ((1080, 1080), "PNG"))
                self.assertEqual(picture.getpixel((540, 300))[:3], (255, 0, 0))   # the photo, centred in the well
                self.assertEqual(picture.getpixel((80, 80))[:3], (246, 246, 248))  # the well around it
            fetched.assert_called_once()
            # Kept for the next tap at the same price: the shop's photo is not fetched again.
            self.client.get(f"/products/{self.product.slug}/post.png")
            fetched.assert_called_once()
        cache.clear()
        with mock.patch("catalogue.importers.fetch", side_effect=OSError("down")):
            response = self.client.get(f"/products/{self.product.slug}/post.png")
        with Image.open(BytesIO(response.content)) as picture:
            self.assertEqual(picture.size, (1080, 1080))
            self.assertEqual(picture.getpixel((540, 300))[:3], (246, 246, 248))   # the well, empty

    def test_the_picture_carries_a_code_that_opens_the_product_page_as_a_visit_from_instagram(self):
        # The owner, 5 Nov 2026: "a QR code that some phones can long hold to take you to the site". A post carries
        # no link; the code does. Its address is the page on the site itself, tagged so the dashboard counts the
        # scan as a visit from Instagram (a camera passes no Referer).
        from io import BytesIO
        from unittest import mock

        from django.core.cache import cache
        from django.test import RequestFactory
        from PIL import Image

        from catalogue import insights
        from web import share

        cache.clear()
        self.addCleanup(cache.clear)
        url = share.post_url(self.product)
        self.assertEqual(url, f"https://ripraptor.com/products/{self.product.slug}/?utm_source=instagram.com")
        parts = urlsplit(url)
        self.assertEqual(insights.source_of(RequestFactory().get(f"{parts.path}?{parts.query}")), "instagram.com")
        with mock.patch("catalogue.importers.fetch", side_effect=OSError("down")):
            response = self.client.get(f"/products/{self.product.slug}/post.png")
        matrix = share.code_matrix(url)
        cell, left, top = share.code_cells(matrix)
        self.assertGreaterEqual(cell, 5)   # a module is a few pixels even on a phone's screen, so a camera can read it
        ink = (0x1f, 0x24, 0x30)
        with Image.open(BytesIO(response.content)) as picture:
            # Every module of the code is drawn, dark or light, at the foot right.
            for row, modules in enumerate(matrix):
                for column, dark in enumerate(modules):
                    pixel = picture.getpixel((left + column * cell + cell // 2, top + row * cell + cell // 2))[:3]
                    self.assertEqual(pixel == ink, dark, (row, column))
            # The quiet zone: paper on every side of it, at least four modules wide (the standard's floor).
            quiet = 4 * cell
            for x, y in ((left - quiet, top + 100), (left + 100, top - quiet), (left + cell * len(matrix) + quiet - 1, top + 100),
                         (left + 100, top + cell * len(matrix) + quiet - 1)):
                self.assertEqual(picture.getpixel((x, y))[:3], (255, 255, 255), (x, y))
            # The site's name sits by the logo now, clear of the code; nothing else is drawn at the foot right.
            self.assertEqual(picture.getpixel((share.CODE_X - 10, 1016))[:3], (255, 255, 255))
        self.assertIn("qrcode", open("requirements.txt", encoding="utf-8").read())

    def test_the_share_panel_and_the_cards_sheet_offer_post(self):
        html = self.client.get(self.product.get_absolute_url()).content.decode()
        self.assertIn(f'data-share-post="/products/{self.product.slug}/post/"', html)
        self.assertIn('<li hidden data-share-post data-share-making="Making the picture." data-share-shared="Caption copied. '
                      'Choose Instagram, make a post with the picture and paste the caption." data-share-saved="Picture '
                      'saved and caption copied. Open Instagram, start a post with the picture and paste the caption." '
                      'data-share-failed="The picture could not be made. Try again in a moment."><button '
                      'class="share-box__item" type="button" aria-label="Share as post">', html)
        self.assertIn('<span class="share-box__name" aria-hidden="true">Post</span>', html)
        sheet = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertIn('<li hidden data-share-post', sheet)

    def test_the_caption_costs_a_few_queries_however_many_shops(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            self.client.get(self.url)
        self.assertLessEqual(len(queries), 8)
