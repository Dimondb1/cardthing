"""Report a problem with a listing (web/views.py report_listing, inbox/service.py report): a quiet Report link beside
each shop's offer opens a form under the price list, and a report lands in the Message us inbox for the owner to
check. No name or email is asked for, and one report never hides a shop's price."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from catalogue.models import Listing
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from inbox import service as inbox
from inbox.models import Conversation, Message


@mock.patch("inbox.service.notify.owner")
class ReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.box = make_product(make_set(make_game()), name="Super Electric Breaker Japanese Booster Box", slug="seb-box",
                                product_type="booster_box", language="ja")
        self.cheap = make_listing(self.box, make_retailer("Harbour Games"), price="42.99", delivery="3.99")
        self.dear = make_listing(self.box, make_retailer("Northgate Cards"), price="179.99", delivery="2.99")
        self.url = self.box.get_absolute_url()

    def post(self, **data):
        return self.client.post(f"{self.url}report/", {"listing": self.cheap.pk, **data})

    def test_every_offer_has_a_report_link_and_the_form_names_the_shop(self, notify):
        html = self.client.get(self.url).content.decode()
        for listing in (self.cheap, self.dear):
            self.assertIn(f'<a class="offer__report" href="?report={listing.pk}#report" rel="nofollow">Report'
                          f'<span class="visually-hidden"> a problem with the listing at {listing.retailer.name}</span></a>', html)
        self.assertNotIn('id="report"', html)   # the form opens only when asked for
        html = self.client.get(self.url, {"report": self.cheap.pk}).content.decode()
        form = html[html.index('id="report"'):html.index("</section>", html.index('id="report"'))]
        self.assertIn("Report a problem with this listing", form)
        self.assertIn("The offer at Harbour Games.", form)
        self.assertIn(f'name="listing" value="{self.cheap.pk}"', form)
        self.assertIn('<input type="radio" name="reason" value="language">', form)
        self.assertIn('name="csrfmiddlewaretoken"', form)
        self.assertIn("We do not ask for your name or email.", form)
        self.assertNotIn('type="email"', form)
        # Another product's listing opens nothing.
        other = make_listing(make_product(make_set(make_game("Disney Lorcana", "lorcana"), slug="x", code="X"), slug="y"),
                             self.cheap.retailer)
        self.assertNotIn('id="report"', self.client.get(self.url, {"report": other.pk}).content.decode())

    def test_a_report_reaches_the_inbox_and_the_visitor_sees_thanks(self, notify):
        response = self.post(reason="language", note="The picture shows the Korean box.")
        self.assertEqual(response["Location"], f"{self.url}?report_state=sent#report")
        conversation = Conversation.objects.get()
        self.assertEqual((conversation.name, conversation.email, conversation.unread), ("Listing report", "", True))
        body = conversation.messages.get().body
        self.assertTrue(body.startswith(f"Listing report: Another language (listing {self.cheap.pk})\n"))
        self.assertIn("Shop: Harbour Games", body)
        self.assertIn("Shown: £46.98 delivered, in stock", body)
        self.assertIn(f"/admin/catalogue/listing/{self.cheap.pk}/change/", body)
        self.assertIn("Note: The picture shows the Korean box.", body)
        notify.assert_called_once()
        html = self.client.get(response["Location"]).content.decode()
        self.assertIn('<p class="report__done" role="status">Thanks. We will check that listing.</p>', html)
        # The listing is untouched: one unchecked report never hides a price.
        self.cheap.refresh_from_db()
        self.assertEqual(self.cheap.sanity, Listing.Sanity.OK)

    def test_the_same_report_twice_in_an_hour_is_kept_once(self, notify):
        self.post(reason="price")
        self.post(reason="price")
        self.post(reason="stock")
        self.assertEqual(Message.objects.count(), 2)

    def test_robots_and_floods_are_kept_out_and_a_missing_reason_asks_again(self, notify):
        self.post(reason="price", website="http://spam.example")
        self.assertFalse(Message.objects.exists())
        response = self.post(note="no reason")
        self.assertEqual(response["Location"], f"{self.url}?report={self.cheap.pk}&report_state=reason#report")
        self.assertIn("Choose what is wrong, then send it again.", self.client.get(response["Location"]).content.decode())
        self.assertEqual(self.post(reason="nonsense").status_code, 302)
        self.assertFalse(Message.objects.exists())
        with mock.patch.object(inbox, "REPORTS_PER_HOUR", 1):
            self.post(reason="price")
            response = self.client.post(f"{self.url}report/", {"listing": self.dear.pk, "reason": "price"})
        self.assertEqual(response["Location"], f"{self.url}?report_state=busy#report")
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(self.client.get(f"{self.url}report/").status_code, 405)

    def test_the_buy_links_keep_their_tracking(self, notify):
        html = self.client.get(self.url).content.decode()
        self.assertIn(f'href="/go/{self.cheap.pk}/?b=price_list"', html)
        self.assertIn('rel="sponsored nofollow noopener"', html)
