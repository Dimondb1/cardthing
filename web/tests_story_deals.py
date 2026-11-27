"""Story deals (web/story_deals.py): the owner's page of great deals ready to post as Instagram stories. The owner, 22
Nov 2026: "I need a list of ebay products to add to my insta stories easily ... a one tap carousel of great deals
where I can customise the advert slightly if I need to"."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import fair_price
from catalogue.models import DailyLowestPrice, Listing, Product, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import share, story_deals

URL = "/admin/story-deals/"


def record(product, prices, today):
    for back, price in enumerate(reversed(prices), start=1):
        DailyLowestPrice.objects.create(product=product, date=today - timedelta(days=back), price=Decimal(price))


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.today = timezone.localdate()
        self.set = make_set(make_game())
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.harbour, self.northgate = make_retailer("Harbour Games"), make_retailer("Northgate Cards")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        # Between seasons, so the pictures are the plain ones whatever day the tests run (web/tests_story_themes.py
        # has the seasons' own).
        season = mock.patch("web.black_friday.local_today", return_value=date(2026, 9, 1))
        season.start()
        self.addCleanup(season.stop)

    def product(self, slug, ebay=None, shop="70.00", history=("60.00",) * 20, **fields):
        """A product two UK shops list (one out of stock), with an eBay seller's price when ``ebay`` is given."""
        fields.setdefault("product_type", Product.Type.BOOSTER_BOX)
        product = make_product(self.set, name=slug.replace("-", " ").title(), slug=slug, **fields)
        if shop:
            make_listing(product, self.harbour, price=shop, delivery="0.00")
        make_listing(product, self.northgate, price="999.00", delivery="0.00",
                     availability=Listing.Availability.OUT_OF_STOCK)
        if ebay:
            make_listing(product, self.ebay, price=ebay, delivery="0.00", seller_name="cardvault_uk",
                         seller_feedback=Decimal("99.8"), seller_ratings=1234)
        record(product, history, self.today)
        return product

    def bands(self):
        fair_price.refresh_bands(self.today)

    def page(self, **params):
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200)
        return response

    def slugs(self, **params):
        return [card["product"].slug for card in self.page(**params).context["cards"]]


class ListTests(Base):
    def setUp(self):
        super().setUp()
        self.deal = self.product("ebay-deal", ebay="50.00")                 # eBay cheapest, well under the usual
        self.shop_deal = self.product("shop-deal", shop="50.00")            # a shop cheapest, a good price
        self.dear = self.product("ebay-dear", ebay="61.00")                 # eBay cheapest, only a fair price
        self.beaten = self.product("ebay-beaten", ebay="55.00", shop="50.00")   # a shop beats the eBay seller
        self.bands()

    def test_only_great_deals_where_an_ebay_seller_is_cheapest(self):
        self.assertEqual(self.slugs(), ["ebay-deal"])
        html = self.page().content.decode()
        self.assertIn("1 deal ready for a story", html)
        self.assertIn("£50.00 delivered &middot; eBay: cardvault_uk (99.8% of 1234)", html)
        self.assertIn("Lowest yet", html)

    def test_every_seller_shows_the_shops_deals_too_the_lowest_yet_first(self):
        self.assertEqual(set(self.slugs(**{"from": "all"})), {"ebay-deal", "shop-deal", "ebay-beaten"})
        self.assertEqual(self.page(**{"from": "nonsense"}).context["source"], "ebay")

    def test_a_doubtful_price_or_one_sold_out_is_never_offered(self):
        Listing.objects.filter(product=self.deal, retailer=self.ebay).update(sanity=Listing.Sanity.DOUBTFUL)
        self.assertEqual(self.slugs(), [])
        Listing.objects.filter(product=self.deal, retailer=self.ebay).update(
            sanity=Listing.Sanity.OK, availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.slugs(), [])
        self.assertIn("No deals to post now", self.page().content.decode())

    def test_a_deal_at_a_shop_marked_caution_is_never_offered_for_a_story(self):
        from catalogue import shop_check

        shop_check.confirm(self.harbour, "caution", "Mostly negative public reviews.", "")
        # Harbour Games leads shop-deal and ebay-beaten; the eBay seller's own deal is untouched.
        self.assertEqual(self.slugs(**{"from": "all"}), ["ebay-deal"])

    def test_each_card_has_its_picture_one_tap_share_and_the_link_for_the_sticker(self):
        card = self.page().context["cards"][0]
        picture = reverse("story_deal_image", args=["ebay-deal"])
        self.assertEqual((card["picture"], card["shown"], card["file"]), (picture, picture, "ebay-deal-story.png"))
        self.assertEqual(card["link"], "http://testserver/products/ebay-deal/"
                                       "?utm_source=instagram_story&utm_medium=social&utm_campaign=story_deals")
        html = self.page().content.decode()
        self.assertIn(f'<a class="deal__share" href="{picture}" download="ebay-deal-story.png" data-deal-share', html)
        self.assertIn('js/story-deals.js', html)

    def test_the_owners_own_words_go_on_the_one_card_they_were_typed_on(self):
        self.product("ebay-other", ebay="40.00")
        self.bands()
        found = self.page(p="ebay-deal", badge="  Deal of   the day ", line="Top Rated seller on eBay" + "x" * 40).context
        cards = {card["product"].slug: card for card in found["cards"]}
        self.assertEqual(cards["ebay-deal"]["badge"], "Deal of the day")
        self.assertEqual(len(cards["ebay-deal"]["line"]), share.STORY_LINE_MOST)
        self.assertIn("badge=Deal+of+the+day", cards["ebay-deal"]["shown"])
        self.assertEqual((cards["ebay-other"]["badge"], cards["ebay-other"]["shown"]),
                         ("", reverse("story_deal_image", args=["ebay-other"])))

    def test_an_ebay_price_not_checked_in_the_last_three_hours_is_never_offered(self):
        """The owner, 24 Nov 2026: "the products offered are. Sometimes really old". An eBay item sells in hours, so
        a story never sends people to one checked longer ago than FRESH, though its price still counts on the site."""
        Listing.objects.filter(product=self.deal, retailer=self.ebay).update(
            last_checked=timezone.now() - story_deals.FRESH - timedelta(minutes=1))
        self.assertEqual(self.slugs(), [])
        self.assertNotIn("ebay-deal", self.slugs(**{"from": "all"}))
        html = self.page().content.decode()
        self.assertIn('<p class="note" id="waiting">1 more eBay deal waits for a fresh check. eBay deals are checked '
                      "again every two hours, so it should be back soon.</p>", html)
        self.assertIn("a price checked in the last 3 hours.", html)
        # Checked again: offered again, and nothing waits.
        Listing.objects.filter(product=self.deal, retailer=self.ebay).update(last_checked=timezone.now())
        self.assertEqual(self.slugs(), ["ebay-deal"])
        self.assertNotIn('id="waiting"', self.page().content.decode())
        self.assertEqual(story_deals.waiting_count(), 0)

    def test_a_shops_price_is_not_held_to_ebays_three_hours(self):
        Listing.objects.filter(product=self.shop_deal, retailer=self.harbour).update(
            last_checked=timezone.now() - timedelta(hours=10))
        self.assertIn("shop-deal", self.slugs(**{"from": "all"}))

    def test_the_same_queries_however_many_deals(self):
        with CaptureQueriesContext(connection) as few:
            self.page()
        for number in range(6):
            self.product(f"more-deal-{number}", ebay="50.00")
        self.bands()
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(len(self.page().context["cards"]), 7)
        self.assertEqual(len(many), len(few))


class AccessTests(Base):
    def test_staff_only_never_cached_or_indexed_and_linked_from_the_admin_home(self):
        response = self.page()
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(f'href="{URL}" class="section">Story deals</a>', self.client.get(reverse("admin:index")).content.decode())
        product = self.product("ebay-deal", ebay="50.00")
        picture = reverse("story_deal_image", args=[product.slug])
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)
        self.assertEqual(self.client.get(picture).status_code, 302)

    def test_a_guides_editor_is_turned_away(self):
        from guides.apps import EDITORS

        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertNotEqual(self.client.get(URL).status_code, 200)


class PictureTests(Base):
    def picture(self, product, **words):
        from io import BytesIO

        from PIL import Image

        with mock.patch("catalogue.importers.fetch", side_effect=OSError("down")):
            response = self.client.get(reverse("story_deal_image", args=[product.slug]), words)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        image = Image.open(BytesIO(response.content))
        image.load()
        return image.convert("RGB"), response.content

    def test_the_picture_is_a_story_and_the_owners_words_change_it(self):
        product = self.product("ebay-deal", ebay="50.00")
        self.bands()
        plain, plain_bytes = self.picture(product)
        self.assertEqual(plain.size, (share.STORY_WIDTH, share.STORY_HEIGHT))
        badged, badged_bytes = self.picture(product, badge="Deal of the day")
        lined, lined_bytes = self.picture(product, line="Top Rated seller on eBay")
        self.assertNotEqual(plain_bytes, badged_bytes)
        self.assertNotEqual(plain_bytes, lined_bytes)
        self.assertNotEqual(badged_bytes, lined_bytes)
        # The product page's own picture is never one with the owner's words on it.
        with mock.patch("catalogue.importers.fetch", side_effect=OSError("down")):
            public = self.client.get(f"/products/{product.slug}/story.png").content
        self.assertEqual(public, plain_bytes)

    def test_the_words_are_one_line_printable_and_cut_to_their_limit(self):
        self.assertEqual(share.story_words("  Deal\n of\tthe   day\x00 ", 24), "Deal of the day")
        self.assertEqual(len(share.story_words("x" * 99, share.STORY_BADGE_MOST)), share.STORY_BADGE_MOST)
        product = self.product("ebay-deal", ebay="50.00")
        with mock.patch.object(share, "draw_story", return_value=b"png") as drawn, \
                mock.patch.object(share, "photo_bytes", return_value=None):
            self.client.get(reverse("story_deal_image", args=[product.slug]), {"badge": "b" * 99, "line": "l" * 99})
        self.assertEqual(drawn.call_args.kwargs, {"badge": "b" * share.STORY_BADGE_MOST, "line": "l" * share.STORY_LINE_MOST,
                                                  "theme": None, "year": None, "drop": ""})

    def test_no_such_product_is_not_found(self):
        self.assertEqual(self.client.get(reverse("story_deal_image", args=["no-such"])).status_code, 404)
        self.assertEqual(story_deals.picture_url("a-box", "Deal", ""), "/admin/story-deals/a-box/story.png?badge=Deal")
