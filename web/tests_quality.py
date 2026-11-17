"""Catalogue quality (web/quality.py): one Admin page that counts what is wrong with the catalogue, with a Log to
copy. The audit of 17 Nov 2026 found duplicates, pages that were no product and shop wording in names from
outside, because nothing inside showed them in one place; the owner approved this page."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Listing, Product, ProductAlias
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import quality

BOX, PACK = Product.Type.BOOSTER_BOX, Product.Type.BOOSTER_PACK


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
class QualityPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game(name="One Piece Card Game", slug="one-piece", short_name="One Piece")
        self.set = make_set(self.game, name="Heroines Edition Vol.2", slug="heroines-edition-vol-2", code="EB-05")
        self.harbour, self.tollgate, self.topaz = (make_retailer(name) for name in ("Harbour Games", "Tollgate TCG", "Topaz Cards"))
        # A real comparison, and a pair that waits because one shop sells both.
        self.box = make_product(self.set, name="Heroines Edition Vol.2 Booster Box", product_type=BOX)
        make_listing(self.box, self.harbour, price="119.95")
        make_listing(self.box, self.tollgate, price="121.00")
        self.twin = make_product(self.set, name="Heroines Edition Vol.2 Booster Display Box", product_type=BOX)
        make_listing(self.twin, self.harbour, price="125.00")
        # A name with its shop's name on it, one filed under no set with a code, and one that may be no product.
        self.named = make_product(self.set, name="Heroines Edition Vol.2 Booster Pack Topaz Cards", product_type=PACK)
        make_listing(self.named, self.topaz, price="4.50")
        self.loose = Product.objects.create(game=self.game, name="EB-05 Heroines Edition Vol.2 Japanese Booster Box",
                                            product_type=BOX)
        make_listing(self.loose, self.tollgate, price="91.90", availability=Listing.Availability.OUT_OF_STOCK)
        self.event = Product.objects.create(game=self.game, name="One Piece Heroines Battle Vol.2", product_type=PACK)
        make_listing(self.event, self.tollgate, price="10.00")
        ProductAlias.objects.create(slug="an-old-address", product=self.box)
        self.url = reverse("catalogue_quality")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))

    def test_the_page_counts_what_is_wrong_and_says_why_a_pair_waits(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.context["data"]
        self.assertEqual(dict(data["comparison"]), {
            "Products on the site": 5, "In stock at two shops or more (a real comparison)": 1,
            "In stock at exactly one shop": 3, "With a price to quote now (in stock or on pre-order)": 4,
            "With no price to quote now": 1})
        duplicates = data["duplicates"]
        self.assertEqual(duplicates["pairs"], 1)
        self.assertEqual(duplicates["reasons"], [("A shop sells both", 1)])
        self.assertEqual({duplicates["listed"][0]["keep"].pk, duplicates["listed"][0]["other"].pk}, {self.box.pk, self.twin.pk})
        names = {label: (count, some) for label, count, some in data["names"]}
        self.assertEqual(names["Names carrying the name of a shop that lists them"],
                         (1, ["Heroines Edition Vol.2 Booster Pack Topaz Cards"]))
        self.assertEqual(names["Names with no word for a kind of product"], (1, ["One Piece Heroines Battle Vol.2"]))
        filing = {label: count for label, count, _some in data["filing"]}
        self.assertEqual((filing["Filed under no set"], filing["Of those, with one set code in the name (could be filed)"]), (2, 1))
        self.assertEqual(dict(data["languages"]), {"English": 4, "Japanese": 1})
        self.assertEqual(dict(data["addresses"])["Old addresses that redirect to a live page"], 1)
        self.assertContains(response, "A shop sells both")
        self.assertContains(response, "Log to copy")

    def test_the_log_holds_the_whole_page_as_text(self):
        log = self.client.get(self.url).context["log"]
        self.assertTrue(log.startswith("RipRaptor catalogue quality, "))
        for line in ("A REAL COMPARISON", "In stock at two shops or more (a real comparison): 1", "POSSIBLE DUPLICATES",
                     "Pairs waiting: 1", "    1 x A shop sells both",
                     "Heroines Edition Vol.2 Booster Box  |  Heroines Edition Vol.2 Booster Display Box  |  A shop sells both",
                     "Names carrying the name of a shop that lists them: 1",
                     "    Heroines Edition Vol.2 Booster Pack Topaz Cards", "By language: English 4, Japanese 1",
                     "NOT PRODUCTS", "ADDRESSES"):
            self.assertIn(line, log)

    def test_the_sitemap_is_counted_as_the_sitemap_counts_it(self):
        # The sold-out Japanese box has never had a price recorded, so the sitemap leaves it out.
        offered = dict(self.client.get(self.url).context["data"]["offered"])
        self.assertEqual(offered["Product pages the sitemap offers"], 4)
        self.assertEqual(offered["Product pages on the site but left out (no shop has priced them for 30 days)"], 1)

    def test_it_only_reads_and_is_for_staff_alone(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        response = self.client.get(self.url)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        before = (Product.objects.filter(is_active=True).count(), ProductAlias.objects.count())
        self.client.get(self.url)
        self.assertEqual((Product.objects.filter(is_active=True).count(), ProductAlias.objects.count()), before)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_more_products_cost_no_more_queries(self):
        with CaptureQueriesContext(connection) as first:
            quality.gather()
        for number in range(12):
            extra = make_product(self.set, name=f"Heroines Edition Vol.2 Mini Tin Number {number}", product_type=Product.Type.TIN)
            make_listing(extra, self.harbour, price="9.99", url=f"https://harbour.example/tin-{number}")
        with CaptureQueriesContext(connection) as second:
            quality.gather()
        self.assertEqual(len(first), len(second))
        self.assertLess(len(first), 40)

    def test_admin_home_links_to_it(self):
        self.assertContains(self.client.get(reverse("admin:index")), reverse("catalogue_quality"))
