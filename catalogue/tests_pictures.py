"""The picture check (catalogue/pictures.py): a shop's picture that is gone is taken off a product before a list shows
it broken, and the next read gives the product a shop's picture now. The owner, 11 Nov 2026, with Pitch Black and
Ascended Heroes in the sets on the home page: "Why are these products got broken picture links"."""

import urllib.error
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import pictures
from .importers import Offer, apply_offers
from .models import Listing, Product, Retailer, ShopProduct
from .testing import make_game, make_listing, make_product, make_retailer, make_set

DEAD = "https://cdn.shopify.com/s/files/1/0001/files/pitch-black-box.png?v=1"
NEW = "https://cdn.shopify.com/s/files/1/0001/files/pitch-black-box-new.png?v=2"


class Answer:
    """What urlopen gives: a status the caller never reads, a Content-Type, a body."""

    def __init__(self, kind="image/png"):
        self.headers = {"Content-Type": kind}

    def read(self, size=-1):
        return b"\x89"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def refusing(code):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "no", {}, None)
    return urlopen


class LookTests(TestCase):
    def look(self, urlopen):
        with mock.patch("urllib.request.urlopen", urlopen):
            return pictures.look(DEAD)

    def test_only_a_shop_saying_the_file_is_gone_counts(self):
        self.assertEqual(self.look(lambda request, timeout=None: Answer()), pictures.OK)
        self.assertEqual(self.look(refusing(404)), pictures.GONE)
        self.assertEqual(self.look(refusing(410)), pictures.GONE)
        # A page where the picture was (a shop's "not found" page sent with a 200) is no picture either.
        self.assertEqual(self.look(lambda request, timeout=None: Answer("text/html; charset=utf-8")), pictures.GONE)
        for code in (403, 429, 500, 503):
            with self.subTest(code=code):
                self.assertEqual(self.look(refusing(code)), pictures.UNKNOWN)

        def no_answer(request, timeout=None):
            raise urllib.error.URLError("timed out")
        self.assertEqual(self.look(no_answer), pictures.UNKNOWN)

    def test_a_server_that_refuses_head_is_asked_for_the_first_byte(self):
        asked = []

        def urlopen(request, timeout=None):
            asked.append((request.get_method(), request.headers.get("Range")))
            if request.get_method() == "HEAD":
                raise urllib.error.HTTPError(request.full_url, 405, "no", {}, None)
            return Answer()
        self.assertEqual(self.look(urlopen), pictures.OK)
        self.assertEqual(asked, [("HEAD", None), ("GET", "bytes=0-0")])

    def test_not_a_web_address_is_never_asked(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(pictures.look("/media/products/box.png"), pictures.UNKNOWN)
        urlopen.assert_not_called()


class CheckTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.shop = make_retailer("Harbour Games", source_type=Retailer.Source.SHOPIFY, source_url="https://harbour.example/")
        self.box = make_product(make_set(make_game(), name="Pitch Black"), name="Pitch Black Booster Box",
                                slug="pitch-black-box", product_type="booster_box")
        Product.objects.filter(pk=self.box.pk).update(image_url=DEAD, updated_at=self.now - timedelta(days=3))
        make_listing(self.box, self.shop, price="150.00")

    def check(self, answers, **kwargs):
        asked = []

        def look(url):
            asked.append(url)
            return answers.get(url, pictures.OK) if isinstance(answers, dict) else answers
        done = pictures.check(now=self.now, look=look, **kwargs)
        return done, asked

    def test_a_gone_picture_is_taken_off_and_put_aside(self):
        done, _asked = self.check(pictures.GONE)
        self.assertEqual(done, {"checked": 1, "gone": 1, "replaced": 0})
        box = Product.objects.get(pk=self.box.pk)
        self.assertEqual((box.image_url, box.image_failed, box.image_checked_at), ("", DEAD, self.now))
        # The product page's picture changed: its time moves, for the sitemap.
        self.assertGreater(box.updated_at, self.now - timedelta(minutes=1))

    def test_a_picture_that_loads_is_kept_and_not_asked_again_for_a_week(self):
        done, asked = self.check(pictures.OK)
        self.assertEqual(done["gone"], 0)
        box = Product.objects.get(pk=self.box.pk)
        self.assertEqual((box.image_url, box.image_checked_at), (DEAD, self.now))
        _done, asked = self.check(pictures.GONE)
        self.assertEqual(asked, [])

    def test_a_shop_that_does_not_answer_plainly_is_asked_again_a_day_later(self):
        self.check(pictures.UNKNOWN)
        box = Product.objects.get(pk=self.box.pk)
        self.assertEqual(box.image_url, DEAD)
        self.assertEqual(box.image_checked_at, self.now - pictures.CHECK_EVERY + pictures.RETRY_AFTER)
        _done, asked = self.check(pictures.UNKNOWN)
        self.assertEqual(asked, [])
        with mock.patch.object(self, "now", self.now + pictures.RETRY_AFTER + timedelta(minutes=1)):
            _done, asked = self.check(pictures.UNKNOWN)
        self.assertEqual(asked, [DEAD])

    def test_another_shops_picture_takes_its_place_when_it_loads(self):
        other = make_retailer("Kestrel Cards")
        ShopProduct.objects.create(retailer=other, title="Pitch Black Booster Box", url="https://kestrel.example/p/pb",
                                   image_url=NEW, suggested=self.box, status=ShopProduct.Status.LINKED)
        done, asked = self.check({DEAD: pictures.GONE, NEW: pictures.OK})
        self.assertEqual(done, {"checked": 1, "gone": 1, "replaced": 1})
        self.assertEqual(asked, [DEAD, NEW])
        box = Product.objects.get(pk=self.box.pk)
        self.assertEqual((box.image_url, box.image_failed), (NEW, DEAD))

    def test_an_uploaded_picture_is_never_asked_about(self):
        Product.objects.filter(pk=self.box.pk).update(image="products/pitch-black.png")
        _done, asked = self.check(pictures.GONE)
        self.assertEqual(asked, [])

    def test_products_on_sale_are_looked_at_first(self):
        quiet = make_product(self.box.product_set, name="Pitch Black Tin", slug="pitch-black-tin", product_type="tin")
        Product.objects.filter(pk=quiet.pk).update(image_url=NEW)
        _done, asked = self.check(pictures.OK, limit=1)
        self.assertEqual(asked, [DEAD])
        _done, asked = self.check(pictures.OK, limit=1)
        self.assertEqual(asked, [NEW])

    def test_a_dry_run_changes_nothing(self):
        lines = []
        done, _asked = self.check(pictures.GONE, dry_run=True, log=lines.append)
        self.assertEqual(done["gone"], 1)
        box = Product.objects.get(pk=self.box.pk)
        self.assertEqual((box.image_url, box.image_failed, box.image_checked_at), (DEAD, "", None))
        self.assertIn("would take off", lines[0])

    def test_the_next_read_gives_it_the_shops_picture_now_never_the_gone_one(self):
        self.check(pictures.GONE)
        offer = Offer(title="Pitch Black Booster Box", url=self.box.listings.get().url, price=150, image=DEAD,
                      availability=Listing.Availability.IN_STOCK)
        offer.product_pk = self.box.pk
        apply_offers(self.shop, [offer], complete=False)
        self.assertEqual(Product.objects.get(pk=self.box.pk).image_url, "")
        offer = Offer(title="Pitch Black Booster Box", url=self.box.listings.get().url, price=150, image=NEW,
                      availability=Listing.Availability.IN_STOCK)
        offer.product_pk = self.box.pk
        apply_offers(self.shop, [offer], complete=False)
        self.assertEqual(Product.objects.get(pk=self.box.pk).image_url, NEW)


class HourlyTests(TestCase):
    def test_tidy_all_checks_pictures_when_it_is_on(self):
        with mock.patch.object(pictures, "check", return_value={"checked": 3, "gone": 1, "replaced": 0}) as check:
            out = StringIO()
            with override_settings(RIPRAPTOR_CHECK_PICTURES=True):
                call_command("tidy_all", stdout=out)
            check.assert_called_once()
            self.assertIn("3 product pictures checked, 1 found gone and taken off", out.getvalue())
            # Off in the tests unless turned on: they never reach the internet.
            check.reset_mock()
            call_command("tidy_all", stdout=StringIO())
            check.assert_not_called()
