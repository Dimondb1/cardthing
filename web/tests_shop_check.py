"""Shop check (Admin, web/shop_check.py): the owner reads what a search for each shop turned up and gives the shop
his own label. The owner, 27 Nov 2026: "use that to propose our own label per shop: Checked, New or Caution. You
confirm each one in Admin". Nothing is set by a program, and nothing here reaches a visitor."""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from catalogue import shop_check
from catalogue.models import Retailer, ShopCheck
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from catalogue.tests_shop_check import finding

URL = "/admin/shop-check/"


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.harbour = self.shop("Harbour Games", "harbour-games", "https://harbourgames.example/")
        self.kestrel = self.shop("Kestrel Cards", "kestrel-cards", "https://www.kestrel.example/")
        self.moor = self.shop("Moor Toys", "moor-toys", "https://moortoys.example/")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))

    def shop(self, name, slug, website):
        shop = make_retailer(name)
        Retailer.objects.filter(pk=shop.pk).update(slug=slug, website=website)
        shop.refresh_from_db()
        return shop

    def findings(self):
        return [finding(),
                finding(slug="kestrel-cards", name="Kestrel Cards", label="caution", rating_seen=2.1,
                        review_count_seen=14, reason="Mostly negative public reviews, including orders not arriving.",
                        complaints="Several reviewers say orders never arrived.", confidence="medium",
                        reviews_url="https://uk.reviews.example/review/kestrel.example",
                        sources=["https://uk.reviews.example/review/kestrel.example", "https://forum.example/t/1"]),
                finding(slug="moor-toys", name="Moor Toys", label="new", review_site="none", rating_seen=None,
                        review_count_seen=None, reviews_url="", sources=[], confidence="low",
                        reason="Too few public reviews for us to say yet.")]

    def load(self, entries=None):
        return self.client.post(URL, {"action": "load", "findings": json.dumps(entries or self.findings())}, follow=True)

    def page(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def trust(self, shop):
        shop.refresh_from_db()
        return shop.trust


class AccessTests(Base):
    def test_staff_only_never_cached_or_indexed_and_linked_from_the_admin_home(self):
        response = self.client.get(URL)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(f'href="{URL}" class="section">Shop check</a>', self.client.get(reverse("admin:index")).content.decode())
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)
        self.assertEqual(self.client.post(URL, {"action": "together", "label": "checked"}).status_code, 302)
        self.assertEqual(self.trust(self.harbour), "")

    def test_a_guides_editor_is_turned_away(self):
        from guides.apps import EDITORS

        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertNotEqual(self.client.get(URL).status_code, 200)
        self.client.post(URL, {"action": "load", "findings": json.dumps(self.findings())})
        self.assertEqual(ShopCheck.objects.count(), 0)


class LoadTests(Base):
    def test_pasted_findings_are_loaded_and_set_no_label(self):
        html = self.load().content.decode()
        self.assertIn("3 shops loaded. Read each one below and confirm its label.", html)
        self.assertEqual(ShopCheck.objects.count(), 3)
        self.assertEqual([self.trust(shop) for shop in (self.harbour, self.kestrel, self.moor)], ["", "", ""])

    def test_a_chosen_file_is_loaded_too(self):
        sent = SimpleUploadedFile("findings.json", ("﻿" + json.dumps(self.findings())).encode(), "application/json")
        html = self.client.post(URL, {"action": "load", "file": sent}, follow=True).content.decode()
        self.assertIn("3 shops loaded.", html)

    def test_what_cannot_be_read_says_so_and_changes_nothing(self):
        for sent, said in (({"findings": "not json"}, "could not be read as JSON"),
                           ({"findings": ""}, "Choose the findings file, or paste what it holds."),
                           ({"file": SimpleUploadedFile("f.json", b"\xff\xfe\x00bad")}, "could not be read as text"),
                           ({"file": SimpleUploadedFile("f.json", b" " * (2 * 1024 * 1024 + 1))}, "too large")):
            with self.subTest(said=said):
                html = self.client.post(URL, {"action": "load", **sent}, follow=True).content.decode()
                self.assertIn(said, html)
        self.assertEqual(ShopCheck.objects.count(), 0)

    def test_entries_for_no_shop_here_or_with_no_label_are_named(self):
        html = self.load([finding(slug="nobody", name="Nobody Cards", website="https://nobody.example/"),
                          finding(label="dodgy")]).content.decode()
        self.assertIn("0 shops loaded.", html)
        self.assertIn("Not a shop here, so left out: Nobody Cards.", html)
        self.assertIn("No label we know, so left out: Harbour Games.", html)


class PageTests(Base):
    def test_each_finding_is_a_card_with_what_was_found_and_its_links_cautions_first(self):
        self.load()
        html = self.page()
        self.assertIn("<li>Waiting for you: 3</li>", html)
        self.assertIn("1 proposed Caution, first below. Each is confirmed by itself.", html)
        self.assertLess(html.index("Kestrel Cards"), html.index("Moor Toys"))
        self.assertLess(html.index("Moor Toys"), html.index('id="shop-%d"' % self.harbour.pk))
        card = html[html.index('id="shop-%d"' % self.kestrel.pk):html.index('id="shop-%d"' % self.moor.pk)]
        self.assertIn('<span class="label label--caution">Proposed: Caution</span> <span class="muted">(medium confidence)</span>', card)
        self.assertIn("Mostly negative public reviews, including orders not arriving.", card)
        self.assertIn("Trustpilot: 2.1 from 14 reviews, as the search showed. It may be out of date.", card)
        self.assertIn("<strong>Complaints found:</strong> Several reviewers say orders never arrived.", card)
        # The links open away from Admin without saying where they came from.
        self.assertIn('<a href="https://forum.example/t/1" target="_blank" rel="noopener noreferrer nofollow">', card)
        self.assertIn('<meta name="referrer" content="no-referrer">', html)
        # The form starts from the proposal: his to change.
        self.assertIn('<option value="caution" selected>Caution</option>', card)
        self.assertIn('name="note" value="Mostly negative public reviews, including orders not arriving."', card)
        self.assertIn('name="reviews_url" value="https://uk.reviews.example/review/kestrel.example"', card)
        self.assertIn('name="hide" value="1"> Also switch this shop off now', card)
        moor = html[html.index('id="shop-%d"' % self.moor.pk):html.index('id="shop-%d"' % self.harbour.pk)]
        self.assertIn("No independent review page was found.", moor)
        self.assertIn("No links.", moor)

    def test_shops_with_no_finding_are_named_and_a_marketplace_is_never_here(self):
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.load([finding()])
        html = self.page()
        self.assertIn("<li>Not looked up yet: 2</li>", html)
        self.assertIn("Shops with no finding loaded and no label: Kestrel Cards, Moor Toys.", html)
        self.assertNotIn("eBay", html)
        self.client.post(URL, {"action": "confirm", "shop": ebay.pk, "label": "caution"})
        self.assertEqual(self.trust(ebay), "")

    def test_text_from_a_file_is_never_markup(self):
        self.load([finding(reason='<script>alert(1)</script>', notes='"><img src=x onerror=alert(1)>',
                           sources=["https://a.example/?q=<b>"])])
        html = self.page()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_the_same_queries_however_many_shops(self):
        self.load()
        with CaptureQueriesContext(connection) as few:
            self.page()
        for n in range(6):
            shop = self.shop(f"Extra Shop {n}", f"extra-{n}", f"https://extra{n}.example/")
            ShopCheck.objects.create(retailer=shop, label="new", loaded_at=timezone.now())
        with CaptureQueriesContext(connection) as many:
            self.page()
        self.assertEqual(len(many), len(few))


class ConfirmTests(Base):
    def test_confirming_one_sets_the_owners_label_reason_and_link(self):
        self.load()
        response = self.client.post(URL, {"action": "confirm", "shop": self.kestrel.pk, "label": "caution",
                                          "note": "Mostly negative public reviews.",
                                          "reviews_url": "https://uk.reviews.example/review/kestrel.example"})
        self.assertRedirects(response, f"{URL}#shop-{self.kestrel.pk}", fetch_redirect_response=False)
        self.kestrel.refresh_from_db()
        self.assertEqual((self.kestrel.trust, self.kestrel.trust_note, self.kestrel.trust_on),
                         ("caution", "Mostly negative public reviews.", timezone.localdate()))
        self.assertEqual(self.kestrel.reviews_url, "https://uk.reviews.example/review/kestrel.example")
        self.assertTrue(self.kestrel.is_active)                       # not switched off unless he ticks it
        html = self.page()
        self.assertIn("<li>Waiting for you: 2</li>", html)
        self.assertIn("<li>Caution: 1</li>", html)
        confirmed = html[html.index("<h2>Confirmed</h2>"):]
        self.assertIn('<span class="label label--caution">Caution</span>', confirmed)
        self.assertIn("Mostly negative public reviews.", confirmed)

    def test_he_may_give_another_label_than_the_one_proposed_or_none(self):
        self.load()
        self.client.post(URL, {"action": "confirm", "shop": self.kestrel.pk, "label": "new", "note": "Mixed reviews."})
        self.assertEqual(self.trust(self.kestrel), "new")
        self.client.post(URL, {"action": "confirm", "shop": self.kestrel.pk, "label": "", "note": "x"})
        self.kestrel.refresh_from_db()
        self.assertEqual((self.kestrel.trust, self.kestrel.trust_note, self.kestrel.trust_on), ("", "", None))
        self.assertIn("<li>Waiting for you: 3</li>", self.page())          # back among those waiting

    def test_a_shop_can_be_switched_off_in_the_same_tap(self):
        self.load()
        product = make_product(make_set(make_game()), name="Surging Sparks Booster Box")
        make_listing(product, self.kestrel, price="50.00")
        make_listing(product, self.harbour, price="90.00")
        html = self.client.post(URL, {"action": "confirm", "shop": self.kestrel.pk, "label": "caution",
                                      "note": "Mostly negative public reviews.", "hide": "1"}, follow=True).content.decode()
        self.assertIn("Kestrel Cards: Caution. Switched off: its prices leave the site within a few minutes.", html)
        self.kestrel.refresh_from_db()
        self.assertFalse(self.kestrel.is_active)
        page = self.client.get(product.get_absolute_url()).content.decode()
        self.assertNotIn("Kestrel Cards", page)
        self.assertIn("Harbour Games", page)

    def test_those_proposed_checked_or_new_can_be_confirmed_together_a_caution_never(self):
        self.load()
        html = self.page()
        self.assertIn("Confirm all 1 proposed Checked</button>", html)
        self.assertIn("Confirm all 1 proposed New, not yet checked</button>", html)
        self.assertNotIn("proposed Caution</button>", html)
        html = self.client.post(URL, {"action": "together", "label": "checked"}, follow=True).content.decode()
        self.assertIn("1 shop confirmed as Checked.", html)
        self.harbour.refresh_from_db()
        self.assertEqual((self.harbour.trust, self.harbour.trust_note),
                         ("checked", "Mostly positive public reviews over several years."))
        self.assertEqual(self.harbour.reviews_url, "https://uk.reviews.example/review/harbourgames.example")
        self.assertEqual([self.trust(self.kestrel), self.trust(self.moor)], ["", ""])
        html = self.client.post(URL, {"action": "together", "label": "caution"}, follow=True).content.decode()
        self.assertIn("Only shops proposed Checked or New can be confirmed together.", html)
        self.assertEqual(self.trust(self.kestrel), "")

    def test_confirming_together_leaves_a_label_already_given(self):
        self.load()
        shop_check.confirm(self.harbour, "new", "My own reading.", "")
        self.client.post(URL, {"action": "together", "label": "checked"})
        self.harbour.refresh_from_db()
        self.assertEqual((self.harbour.trust, self.harbour.trust_note), ("new", "My own reading."))

    def second_look(self, label, **fields):
        return finding(slug="moor-toys", name="Moor Toys", label=label, **fields)

    def test_a_second_look_that_disagrees_with_his_label_comes_back_first(self):
        self.load()
        self.client.post(URL, {"action": "together", "label": "new"})            # Moor Toys: New
        self.client.post(URL, {"action": "together", "label": "checked"})        # Harbour Games: Checked
        self.assertNotIn("Looked up again", self.page())
        # Both are looked up again. Harbour Games gets the same answer; Moor Toys does not.
        self.load([finding(), self.second_look(
            "caution", rating_seen=1.9, review_count_seen=31,
            reason="Mostly negative public reviews, including orders not arriving.",
            sources=["https://uk.reviews.example/review/moortoys.example"])])
        self.assertEqual(self.trust(self.moor), "new")                           # his label stays until he changes it
        html = self.page()
        self.assertIn("<li>Waiting for you: 2</li>", html)                       # with the caution he never answered
        self.assertIn("<strong>1 looked up again, with a different answer from the label you gave.</strong>", html)
        self.assertIn("<li>New, not yet checked: 1</li>", html)                  # still counted under his label
        waiting = html[html.index("<h2>Waiting for you</h2>"):html.index("<h2>Confirmed</h2>")]
        confirmed = html[html.index("<h2>Confirmed</h2>"):]
        self.assertLess(waiting.index("Moor Toys"), waiting.index("Kestrel Cards"))
        self.assertIn("<strong>Looked up again.</strong> You labelled this shop New, not yet checked on "
                      f"{date_format(timezone.localdate(), 'j M Y')}. The newer finding proposes otherwise.", waiting)
        self.assertIn('<span class="label label--caution">Proposed: Caution</span>', waiting)
        self.assertNotIn("Moor Toys", confirmed)
        self.assertIn("Harbour Games", confirmed)
        self.assertEqual(html.count(f'id="shop-{self.moor.pk}"'), 1)
        # He reads it and answers: it leaves those waiting whichever label he gives, and is not raised again.
        self.client.post(URL, {"action": "confirm", "shop": self.moor.pk, "label": "new", "note": "Read. Too few to say."})
        self.assertNotIn("Looked up again", self.page())
        self.load([self.second_look("caution")])
        html = self.page()
        self.assertNotIn("Looked up again", html)
        self.assertIn("<li>Waiting for you: 1</li>", html)

    def test_a_shop_looked_up_again_is_never_swept_up_with_the_others(self):
        self.load()
        self.client.post(URL, {"action": "together", "label": "new"})            # Moor Toys: New
        self.load([self.second_look("checked")])
        html = self.page()
        self.assertIn("Looked up again", html)
        self.assertIn("Confirm all 1 proposed Checked</button>", html)           # Harbour Games alone
        self.client.post(URL, {"action": "together", "label": "checked"})
        self.assertEqual([self.trust(self.harbour), self.trust(self.moor)], ["checked", "new"])

    def test_a_label_reaches_the_site_only_once_he_confirms_it(self):
        # A finding is his to read: nothing of it is shown. His own label and reason are (web/tests_shop_labels.py).
        self.load()
        product = make_product(make_set(make_game()), name="Surging Sparks Booster Box")
        make_listing(product, self.kestrel, price="50.00")
        before = self.client.get(product.get_absolute_url()).content.decode()
        self.assertIn("Kestrel Cards", before)
        for words in ("Caution", "Mostly negative public reviews", "orders never arrived", "from 14 reviews"):
            self.assertNotIn(words, before)
        self.client.post(URL, {"action": "confirm", "shop": self.kestrel.pk, "label": "caution",
                               "note": "Mostly negative public reviews."})
        cache.clear()
        after = self.client.get(product.get_absolute_url()).content.decode()
        self.assertIn("<strong>Caution</strong>", after)
        self.assertIn("Mostly negative public reviews.", after)
        # Never what the search showed of a review site's own scores, nor the complaints he read.
        for words in ("orders never arrived", "from 14 reviews", "Trustpilot"):
            self.assertNotIn(words, after)
