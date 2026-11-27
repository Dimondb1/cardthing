"""The shop check's findings (catalogue/shop_check.py): a file of what a search for each shop's public record turned
up, taken in for the owner to read, and the label only he sets. The owner, 27 Nov 2026: "Look each shop up the way a
person would ... and use that to propose our own label per shop: Checked, New or Caution. You confirm each one in
Admin"."""

import json
from datetime import date
from decimal import Decimal

from django.test import TestCase

from . import shop_check
from .models import Retailer, ShopCheck
from .testing import make_retailer


def finding(**fields):
    entry = {"slug": "harbour-games", "name": "Harbour Games", "website": "https://www.harbourgames.example/",
             "label": "checked", "reason": "Mostly positive public reviews over several years.",
             "review_site": "Trustpilot", "rating_seen": 4.7, "review_count_seen": 312,
             "reviews_url": "https://uk.reviews.example/review/harbourgames.example", "company": "",
             "complaints": "", "sources": ["https://uk.reviews.example/review/harbourgames.example"],
             "confidence": "high", "notes": ""}
    entry.update(fields)
    return entry


class LoadTests(TestCase):
    def setUp(self):
        self.harbour = make_retailer("Harbour Games")
        Retailer.objects.filter(pk=self.harbour.pk).update(slug="harbour-games", website="https://harbourgames.example/")
        self.kestrel = make_retailer("Kestrel Cards")
        Retailer.objects.filter(pk=self.kestrel.pk).update(slug="kestrel-cards", website="https://www.kestrel.example/")

    def load(self, *entries):
        return shop_check.load(json.dumps(list(entries)))

    def test_a_finding_is_kept_for_its_shop_and_sets_no_label(self):
        self.assertEqual(self.load(finding()), {"saved": 1, "unknown": [], "bad": []})
        found = ShopCheck.objects.get(retailer=self.harbour)
        self.assertEqual((found.label, found.reason), ("checked", "Mostly positive public reviews over several years."))
        self.assertEqual((found.review_site, found.rating_seen, found.review_count_seen),
                         ("Trustpilot", Decimal("4.7"), 312))
        self.assertEqual(found.sources, ["https://uk.reviews.example/review/harbourgames.example"])
        self.assertEqual(found.confidence, "high")
        self.harbour.refresh_from_db()
        self.assertEqual((self.harbour.trust, self.harbour.trust_note, self.harbour.trust_on, self.harbour.reviews_url),
                         ("", "", None, ""))

    def test_a_shop_is_matched_by_its_slug_else_by_its_website(self):
        done = self.load(finding(slug="some-other-slug", website="http://kestrel.example/shop", label="new"),
                         finding(slug="nobody", name="Nobody Cards", website="https://nobody.example/"),
                         finding(slug="", name="", website=""))
        self.assertEqual(done, {"saved": 1, "unknown": ["Nobody Cards", "(no name)"], "bad": []})
        self.assertEqual(ShopCheck.objects.get().retailer, self.kestrel)

    def test_only_the_three_labels(self):
        done = self.load(finding(label="scam"), finding(slug="kestrel-cards", name="Kestrel Cards", label=" Caution "))
        self.assertEqual(done, {"saved": 1, "unknown": [], "bad": ["Harbour Games"]})
        self.assertEqual(ShopCheck.objects.get().label, "caution")

    def test_a_later_finding_takes_the_earlier_ones_place_and_a_confirmed_label_stays(self):
        self.load(finding())
        shop_check.confirm(self.harbour, "checked", "Mostly positive public reviews.", "")
        self.load(finding(label="caution", reason="Mostly negative public reviews."))
        self.assertEqual(ShopCheck.objects.count(), 1)
        self.assertEqual(ShopCheck.objects.get().label, "caution")
        self.harbour.refresh_from_db()
        self.assertEqual(self.harbour.trust, "checked")

    def test_a_second_look_that_proposes_another_label_than_his_is_raised_once(self):
        self.load(finding())
        found = ShopCheck.objects.get()
        self.assertFalse(found.answered)
        self.assertFalse(shop_check.differs(self.harbour, found))            # no label yet: simply waiting
        shop_check.confirm(self.harbour, "new", "My own reading.", "")
        found.refresh_from_db()
        self.assertTrue(found.answered)
        self.assertFalse(shop_check.differs(self.harbour, found))            # he read it and chose otherwise
        self.load(finding())                                                 # looked up again, the same proposal
        found.refresh_from_db()
        self.assertFalse(shop_check.differs(self.harbour, found))            # nothing new for him to read
        self.load(finding(label="caution"))                                  # looked up again, and it found more
        found.refresh_from_db()
        self.assertFalse(found.answered)
        self.assertTrue(shop_check.differs(self.harbour, found))
        self.load(finding(label="new"))                                      # ... or it now agrees with him
        found.refresh_from_db()
        self.assertFalse(shop_check.differs(self.harbour, found))
        self.assertFalse(shop_check.differs(self.harbour, None))

    def test_what_is_taken_in_is_cleaned(self):
        self.load(finding(
            reason="A  reason\nover\ttwo lines " + "x" * 400, review_site="none", rating_seen="9.5",
            review_count_seen="lots", reviews_url="javascript:alert(1)", confidence="certain",
            sources=["javascript:alert(1)", "https://a.example/one", "ftp://a.example/two", "https://a.example/one",
                     "not a link", "https://a.example/with space", 7, "http://b.example/"] + [f"https://c.example/{n}" for n in range(30)],
            complaints="c" * 3000, notes=None, company=["not", "text"]))
        found = ShopCheck.objects.get()
        self.assertTrue(found.reason.startswith("A reason over two lines x"))
        self.assertEqual(len(found.reason), 300)
        self.assertEqual((found.review_site, found.rating_seen, found.review_count_seen, found.reviews_url,
                          found.confidence, found.notes), ("", None, None, "", "", ""))
        self.assertEqual(found.sources[:2], ["https://a.example/one", "http://b.example/"])
        self.assertEqual(len(found.sources), shop_check.SOURCES_MOST)
        self.assertEqual(len(found.complaints), 1000)

    def test_a_file_that_is_not_findings_is_refused(self):
        for text in ("", "not json", "42", '{"shops": 3}', json.dumps([{}] * (shop_check.MOST + 1))):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                shop_check.load(text)
        self.assertEqual(shop_check.load('{"shops": []}')["saved"], 0)
        self.assertEqual(shop_check.load(json.dumps(["a string", 3, None]))["saved"], 0)

    def test_only_a_plain_web_address_is_a_link(self):
        for value in ("https://shop.example/reviews", "http://shop.example"):
            self.assertEqual(shop_check.web_link(value), value)
        for value in ("javascript:alert(1)", "data:text/html,x", "//shop.example", "shop.example", "https://",
                      "https://shop.example/a b", "https://shop.example/" + "a" * 400, None, 5, "https://[bad"):
            with self.subTest(value=value):
                self.assertEqual(shop_check.web_link(value), "")


class ConfirmTests(TestCase):
    def setUp(self):
        self.shop = make_retailer("Harbour Games")

    def test_the_owners_label_reason_link_and_day(self):
        shop_check.confirm(self.shop, "caution", " Mostly negative public reviews,\nincluding orders not arriving. ",
                           "https://reviews.example/harbour", today=date(2026, 11, 27))
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.trust, self.shop.get_trust_display()), ("caution", "Caution"))
        self.assertEqual(self.shop.trust_note, "Mostly negative public reviews, including orders not arriving.")
        self.assertEqual((self.shop.trust_on, self.shop.reviews_url), (date(2026, 11, 27), "https://reviews.example/harbour"))

    def test_no_label_takes_it_all_off_and_a_label_we_do_not_have_is_none(self):
        shop_check.confirm(self.shop, "checked", "Good.", "https://reviews.example/harbour")
        for label in ("", "trusted"):
            shop_check.confirm(self.shop, "checked", "Good.", "https://reviews.example/harbour")
            shop_check.confirm(self.shop, label, "Still here", "javascript:alert(1)")
            self.shop.refresh_from_db()
            with self.subTest(label=label):
                self.assertEqual((self.shop.trust, self.shop.trust_note, self.shop.trust_on, self.shop.reviews_url),
                                 ("", "", None, ""))

    def test_a_new_shop_has_no_label(self):
        self.assertEqual((self.shop.trust, self.shop.trust_on), ("", None))
        self.assertEqual(Retailer.Trust.values, ["checked", "new", "caution"])
