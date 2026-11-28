"""The hourly tidy judges a listing once, and again only when something it was judged on changes
(tidy_listings.Judged). The owner, 28 Nov 2026, of a server with a second processor added: "here we are cpu sitting
at 99 percent again"; the hourly tidy read every listing whole and judged it afresh every hour, two thirds of its
processor time for an answer that could not differ."""

from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .management.commands import tidy_listings
from .management.commands.tidy_listings import Judged
from .models import Listing, Product
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class JudgedTests(TestCase):
    def setUp(self):
        cache.delete(Judged.KEY)
        self.addCleanup(cache.delete, Judged.KEY)
        self.set = make_set(make_game())
        self.harbour = make_retailer("Harbour Games")
        self.kestrel = make_retailer("Kestrel Cards")
        self.box = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", slug="pre-etb")
        self.tin = make_product(self.set, name="Prismatic Evolutions Tin", slug="pre-tin", product_type=Product.Type.TIN)
        self.listings = [
            make_listing(self.box, self.harbour, title="Prismatic Evolutions Elite Trainer Box",
                         url="https://www.harbour-games.example/products/prismatic-evolutions-elite-trainer-box"),
            make_listing(self.box, self.kestrel, title="Pokemon Prismatic Evolutions ETB",
                         url="https://www.kestrel-cards.example/products/prismatic-evolutions-etb"),
            make_listing(self.tin, self.harbour, title="Prismatic Evolutions Tin",
                         url="https://www.harbour-games.example/products/prismatic-evolutions-tin"),
        ]

    def tidy(self, dry_run=False):
        """Run the tidy; return (ids of the listings judged, what it said)."""
        out, judged, real = StringIO(), [], tidy_listings.fits

        def fits(listing):
            judged.append(listing.pk)   # noted now: a listing taken off afterwards has no id left
            return real(listing)

        with mock.patch.object(tidy_listings, "fits", fits):
            call_command("tidy_listings", dry_run=dry_run, stdout=out)
        return sorted(judged), out.getvalue()

    def pks(self, *listings):
        return sorted(listing.pk for listing in listings)

    def test_a_listing_found_fine_is_not_judged_again(self):
        judged, said = self.tidy()
        self.assertEqual(judged, self.pks(*self.listings))
        self.assertIn("0 listings. 3 remain.", said)
        judged, said = self.tidy()
        self.assertEqual(judged, [])
        self.assertIn("0 listings. 3 remain.", said)

    def test_a_second_run_reads_no_listing_whole(self):
        self.tidy()
        with CaptureQueriesContext(connection) as asked:
            self.tidy()
        whole = [query["sql"] for query in asked if '"catalogue_listing"."description"' in query["sql"]
                 and "LIKE" not in query["sql"].upper()]
        self.assertEqual(whole, [])

    def test_a_new_listing_is_judged_and_the_rest_are_not(self):
        self.tidy()
        new = make_listing(self.tin, self.kestrel, title="Prismatic Evolutions Tin",
                           url="https://www.kestrel-cards.example/products/prismatic-evolutions-tin")
        self.assertEqual(self.tidy()[0], [new.pk])
        self.assertEqual(self.tidy()[0], [])

    def test_a_changed_title_is_judged_again_and_a_wrong_one_goes(self):
        self.tidy()
        wrong = self.listings[1]
        Listing.objects.filter(pk=wrong.pk).update(title="Magic The Gathering Foundations Bundle")
        judged, said = self.tidy()
        self.assertEqual(judged, [wrong.pk])
        self.assertIn("removed: Prismatic Evolutions Elite Trainer Box <- Kestrel Cards", said)
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())
        # Gone, so nothing is kept for it; the two that stay are still known.
        self.assertEqual(self.tidy()[0], [])
        self.assertEqual(sorted(cache.get(Judged.KEY)[1]), self.pks(self.listings[0], self.listings[2]))

    def test_a_changed_address_or_shop_barcode_is_judged_again(self):
        self.tidy()
        Listing.objects.filter(pk=self.listings[0].pk).update(
            url="https://www.harbour-games.example/products/prismatic-evolutions-etb-2")
        Listing.objects.filter(pk=self.listings[2].pk).update(shop_ean="0196214105140")
        self.assertEqual(self.tidy()[0], self.pks(self.listings[0], self.listings[2]))

    def test_a_product_renamed_has_its_listings_judged_again(self):
        self.tidy()
        Product.objects.filter(pk=self.box.pk).update(name="Prismatic Evolutions Elite Trainer Box (Pokemon Center)")
        self.assertEqual(self.tidy()[0], self.pks(self.listings[0], self.listings[1]))

    def test_a_product_given_a_barcode_of_its_own_has_its_listings_judged_again(self):
        self.tidy()
        Product.objects.filter(pk=self.tin.pk).update(ean="0196214105140")
        self.assertEqual(self.tidy()[0], [self.listings[2].pk])

    def test_new_rules_judge_every_listing_afresh(self):
        self.tidy()
        with mock.patch.object(Judged, "rules_now", return_value="the code after an update"):
            self.assertEqual(self.tidy()[0], self.pks(*self.listings))
            self.assertEqual(self.tidy()[0], [])

    def test_the_rules_are_the_code_that_decides(self):
        self.assertEqual(Judged.rules_now(), Judged.rules_now())
        self.assertEqual(len(Judged.rules_now()), 32)
        for name in ("matching.py", "classify.py", "languages.py", "importers.py",
                     "management/commands/tidy_listings.py"):
            self.assertIn(name, Judged.RULES)

    def test_an_emptied_cache_only_means_judging_them_all_once_more(self):
        self.tidy()
        cache.delete(Judged.KEY)
        self.assertEqual(self.tidy()[0], self.pks(*self.listings))
        cache.set(Judged.KEY, "not what was kept", None)
        self.assertEqual(self.tidy()[0], self.pks(*self.listings))

    def test_a_dry_run_keeps_nothing(self):
        judged, _said = self.tidy(dry_run=True)
        self.assertEqual(judged, self.pks(*self.listings))
        self.assertIsNone(cache.get(Judged.KEY))
        Listing.objects.filter(pk=self.listings[1].pk).update(title="Magic The Gathering Foundations Bundle")
        self.tidy()
        kept = cache.get(Judged.KEY)
        _judged, said = self.tidy(dry_run=True)
        self.assertIn("0 listings.", said)
        self.assertEqual(cache.get(Judged.KEY), kept)

    def test_one_link_on_two_products_is_still_settled_from_every_listing(self):
        # The rule that reads every listing's link (shared_link_misfits) still sees them all, judged or not.
        self.tidy()
        shared = "https://www.kestrel-cards.example/products/prismatic-evolutions-etb"
        twin = make_listing(self.tin, self.kestrel, title="Pokemon Prismatic Evolutions ETB", url=shared)
        judged, said = self.tidy()
        self.assertIn(twin.pk, judged)
        self.assertFalse(Listing.objects.filter(pk=twin.pk).exists(), said)
        self.assertTrue(Listing.objects.filter(pk=self.listings[1].pk).exists())
        self.assertNotIn(twin.pk, cache.get(Judged.KEY)[1])

    def test_the_fingerprint_is_of_what_fits_reads(self):
        row = ("https://shop.example/p", "Title", "", "shopify", "Name", "pokemon", "", "")
        mark = Judged.fingerprint(row)
        self.assertEqual(mark, Judged.fingerprint(row))
        self.assertEqual(len(mark), 8)
        for at, other in enumerate(("https://shop.example/q", "Title 2", "123", "ebay", "Name 2", "lorcana", "ja")):
            changed = list(row)
            changed[at] = other
            self.assertNotEqual(Judged.fingerprint(tuple(changed)), mark, Judged.FIELDS[at])
        # How a shop is read matters only as marketplace or not, as fits() reads it.
        self.assertEqual(Judged.fingerprint((*row[:3], "website", *row[4:])), mark)
        self.assertEqual(Judged.fingerprint((*row[:3], "ebay", *row[4:])), Judged.fingerprint((*row[:3], "amazon", *row[4:])))
