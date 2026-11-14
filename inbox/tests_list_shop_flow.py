"""List your shop, more automated (inbox/shops.py): a shop gives its details and discount code, the shop checker looks at
its website at once without adding it, the owner approves it in admin, and only then is the shop added, its code saved
and one welcome email sent. The owner, 14 Nov 2026: "they need to provide the info it then goes to the shop checker
function we already have and is checked, need to make sure this a cannot be abused, I will then approve it and then
it sends this message via email"."""

import socket
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import shop_adding
from catalogue.models import Retailer, ShopToAdd
from catalogue.testing import make_retailer
from catalogue.tests_shop_adding import shop, shopify

from . import shops
from .models import OutreachEmail, OutreachMail, ShopContact, ShopEnquiry
from .tests_sending import FakeZeptoMail

PUBLIC, PRIVATE = "93.184.216.34", "10.0.0.5"


def resolve(host, *args, **kwargs):
    """Fake DNS: every example host is on the public internet but intranet.example and localhost."""
    address = PRIVATE if host in ("intranet.example", "localhost") else PUBLIC
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


@override_settings(RIPRAPTOR_SITE_URL="https://ripraptor.example", RIPRAPTOR_ZEPTOMAIL_TOKEN="alerts-token",
                   RIPRAPTOR_MAIL_FROM="alerts@ripraptor.example", RIPRAPTOR_ZOHO_MAIL_FILE="",
                   RIPRAPTOR_ZEPTOMAIL_URL="https://zeptomail.example/v1.1/email",
                   RIPRAPTOR_INBOX_NOTIFY_EMAIL="", RIPRAPTOR_CONTACT_EMAIL="", RIPRAPTOR_NTFY_TOPIC="")
class FlowCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.enterContext(mock.patch("catalogue.shop_adding.socket.getaddrinfo", resolve))
        self.zepto = FakeZeptoMail()
        self.enterContext(mock.patch("urllib.request.urlopen", self.zepto))
        OutreachMail.objects.update_or_create(pk=1, defaults={
            "on": True, "service": "zeptomail", "from_address": "info@ripraptor.example",
            "reply_to": "ben@ripraptor.example", "from_name": "Ben Dimond"})

    def form(self, address="203.0.113.7", **fields):
        data = {"name": "Sam Example", "shop_url": "examplecards.co.uk", "email": "hello@examplecards.co.uk",
                "discount_code": "RIPRAPTOR5", "discount_terms": "5% off your first order", "message": "", **fields}
        return self.client.post(reverse("web:list_shop"), data, REMOTE_ADDR=address)

    def enquiry(self):
        return ShopEnquiry.objects.select_related("shop_check", "retailer").get()

    def run_check(self, pages=None):
        row = ShopToAdd.objects.get(host="examplecards.co.uk")
        return shop_adding.check(row, get=shop(pages or shopify("https://examplecards.co.uk")))

    def sent_to(self, address):
        return [body for body in self.zepto.bodies() if body["to"] == [{"email_address": {"address": address}}]]


class AskTests(FlowCase):
    def test_the_form_takes_a_discount_code_and_hands_the_website_to_the_checker_held(self):
        response = self.form()
        self.assertRedirects(response, reverse("web:list_shop_thanks"))
        enquiry = self.enquiry()
        self.assertEqual((enquiry.discount_code, enquiry.discount_terms), ("RIPRAPTOR5", "5% off your first order"))
        row = enquiry.shop_check
        self.assertEqual((row.host, row.address, row.hold, row.status),
                         ("examplecards.co.uk", "https://examplecards.co.uk", True, ShopToAdd.Status.WAITING))
        # Nothing is fetched while the form is sent, nothing is added and nothing goes to the shop.
        self.assertFalse(Retailer.objects.exists())
        self.assertEqual(self.sent_to("hello@examplecards.co.uk"), [])

    def test_the_page_has_the_new_boxes_and_the_terms_say_what_happens(self):
        page = self.client.get(reverse("web:list_shop"))
        self.assertContains(page, 'name="discount_code"')
        self.assertContains(page, 'name="discount_terms"')
        self.assertContains(page, "We check your shop and email you once it is listed.")
        self.assertContains(self.client.get(reverse("web:terms")), "we email you once to say so")

    def test_a_code_with_spaces_or_symbols_is_refused_and_nothing_is_kept(self):
        for code in ("RIP RAPTOR", "<script>", "X" * 41, "-LEADING"):
            with self.subTest(code=code):
                response = self.form(discount_code=code)
                self.assertContains(response, "Use only letters, numbers, dashes and underscores in the code")
        self.assertFalse(ShopEnquiry.objects.exists())
        self.assertFalse(ShopToAdd.objects.exists())

    def test_terms_without_a_code_are_dropped_and_no_code_is_fine(self):
        self.form(discount_code="", discount_terms="10% off")
        self.assertEqual((self.enquiry().discount_code, self.enquiry().discount_terms), ("", ""))

    def test_a_shop_the_checker_has_seen_keeps_that_look(self):
        row = ShopToAdd.objects.create(host="examplecards.co.uk", address="https://examplecards.co.uk",
                                       status=ShopToAdd.Status.NOT_ADDED, note="No sitemap.")
        self.form()
        self.assertEqual(self.enquiry().shop_check, row)
        self.assertEqual(ShopToAdd.objects.count(), 1)

    def test_past_the_days_checks_the_request_is_kept_and_the_owner_checks_it(self):
        now = timezone.now()
        for n in range(shops.PUBLIC_CHECKS_DAY):
            ShopToAdd.objects.create(host=f"shop{n}.example", address=f"https://shop{n}.example", hold=True,
                                     created_at=now - timedelta(hours=1))
        self.form()
        enquiry = self.enquiry()
        self.assertIsNone(enquiry.shop_check)
        row = shops.check_again(enquiry)
        self.assertEqual((row.host, row.hold, row.status), ("examplecards.co.uk", True, ShopToAdd.Status.WAITING))

    def test_the_owner_hears_with_the_code_and_whether_the_email_is_the_shops(self):
        with override_settings(RIPRAPTOR_INBOX_NOTIFY_EMAIL="ben@ripraptor.example"):
            self.form(email="someone@gmail.example")
        [note] = self.sent_to("ben@ripraptor.example")
        self.assertIn("Discount code: RIPRAPTOR5 (5% off your first order)", note["textbody"])
        self.assertIn("someone@gmail.example (not on the shop website domain)", note["textbody"])
        self.assertIn("Approve it", note["textbody"])

    def test_email_matches_only_the_shops_own_domain(self):
        self.form()
        enquiry = self.enquiry()
        self.assertTrue(shops.email_matches(enquiry))
        for address, matches in (("info@shop.examplecards.co.uk", True), ("a@gmail.example", False),
                                 ("a@notexamplecards.co.uk", False)):
            enquiry.email = address
            self.assertEqual(shops.email_matches(enquiry), matches, address)


class CheckTests(FlowCase):
    def test_a_held_shop_that_passes_waits_for_approval_and_is_not_added(self):
        self.form()
        row = self.run_check()
        self.assertEqual((row.status, row.found_name, row.found_source),
                         (ShopToAdd.Status.PASSED, "Example Cards", Retailer.Source.SHOPIFY))
        self.assertIsNone(row.retailer)
        self.assertFalse(Retailer.objects.exists())

    def test_a_held_shop_on_a_private_address_is_never_read(self):
        self.form(shop_url="intranet.example", email="a@intranet.example")
        reads = []
        row = shop_adding.check(ShopToAdd.objects.get(host="intranet.example"),
                                get=lambda url: reads.append(url) or b"")
        self.assertEqual((row.status, row.note),
                         (ShopToAdd.Status.NOT_ADDED, "Its address is not a website on the public internet."))
        self.assertEqual(reads, [])

    def test_the_public_reader_refuses_private_addresses_and_redirects_to_them(self):
        get = shop_adding.public_get(deadline=None)
        for url in ("https://intranet.example/robots.txt", "http://localhost/", "ftp://examplecards.co.uk/",
                    "https://examplecards.co.uk:8443/"):
            with self.subTest(url=url), self.assertRaises(shop_adding.NotPublic):
                get(url)
        redirects = shop_adding.PublicRedirects()
        with self.assertRaises(shop_adding.NotPublic):
            redirects.redirect_request(None, None, 302, "Found", {}, "http://intranet.example/admin/")
        with self.assertRaises(shop_adding.NotPublic):
            redirects.redirect_request(None, None, 302, "Found", {}, "file:///etc/passwd")

    def test_a_pasted_shop_is_still_added_at_once(self):
        shop_adding.take("examplecards.co.uk")
        row = self.run_check()
        self.assertEqual(row.status, ShopToAdd.Status.ADDED)
        self.assertEqual(row.retailer.name, "Example Cards")


class ApproveTests(FlowCase):
    def ready(self, **fields):
        self.form(**fields)
        self.run_check()
        return self.enquiry()

    def test_approving_adds_the_shop_with_its_code_and_sends_the_welcome_email_once(self):
        enquiry = self.ready()
        shop_, why = shops.approve(enquiry)
        self.assertEqual(why, "")
        shop_.refresh_from_db()
        self.assertEqual((shop_.name, shop_.is_active, shop_.discount_code, shop_.discount_terms),
                         ("Example Cards", True, "RIPRAPTOR5", "5% off your first order"))
        enquiry = self.enquiry()
        self.assertEqual(enquiry.retailer, shop_)
        self.assertIsNotNone(enquiry.approved_at)
        self.assertIsNotNone(enquiry.handled_at)
        self.assertIsNotNone(enquiry.welcome_sent_at)
        self.assertEqual(ShopContact.objects.get(retailer=shop_).email, "hello@examplecards.co.uk")
        self.assertEqual(OutreachEmail.objects.get().kind, "welcome")
        [email] = self.sent_to("hello@examplecards.co.uk")
        self.assertEqual(email["subject"], "Example Cards is now on RipRaptor")
        self.assertEqual(email["from"], {"address": "info@ripraptor.example", "name": "Ben Dimond"})
        self.assertEqual(email["reply_to"], [{"address": "ben@ripraptor.example"}])
        text = email["textbody"]
        self.assertTrue(text.startswith("Hello,\n\nThank you for asking for Example Cards to be listed on RipRaptor."))
        self.assertIn("very soon. We have also listed your discount code.\n\n", text)
        self.assertIn("https://ripraptor.example/list-your-shop/#badge", text)
        self.assertIn("We are @ripraptor_uk (https://www.instagram.com/ripraptor_uk/).", text)
        self.assertTrue(text.endswith("Thanks,\nBen Dimond"))
        self.assertNotIn("htmlbody", email)
        # Approving again sends nothing more.
        shop_again, why = shops.approve(self.enquiry())
        self.assertEqual((shop_again, why), (shop_, "The welcome email was already sent."))
        self.assertEqual(len(self.sent_to("hello@examplecards.co.uk")), 1)

    def test_without_a_code_there_is_no_code_line_and_no_stray_space(self):
        enquiry = self.ready(discount_code="", discount_terms="")
        shops.approve(enquiry)
        [email] = self.sent_to("hello@examplecards.co.uk")
        self.assertIn("in our price comparisons very soon.\n\nA few things", email["textbody"])
        self.assertNotIn("discount code.", email["textbody"].split("A few things")[0])
        self.assertEqual(Retailer.objects.get().discount_code, "")

    def test_a_shop_that_did_not_pass_or_is_not_checked_yet_cannot_be_approved(self):
        self.form()
        with self.assertRaisesMessage(shops.NotReady, "The check has not finished yet"):
            shops.approve(self.enquiry())
        self.run_check({"https://examplecards.co.uk/robots.txt": ""})
        with self.assertRaisesMessage(shops.NotReady, "It did not pass the check: No product feed or sitemap found"):
            shops.approve(self.enquiry())
        self.assertFalse(Retailer.objects.exists())
        self.assertEqual(self.zepto.requests, [])
        self.assertIsNone(self.enquiry().approved_at)

    def test_a_shop_already_listed_gets_its_code_and_the_email(self):
        existing = make_retailer("Example Cards", website="https://examplecards.co.uk/")
        self.form()
        row = self.run_check()
        self.assertEqual(row.status, ShopToAdd.Status.ALREADY)
        shop_, why = shops.approve(self.enquiry())
        self.assertEqual((shop_, why), (existing, ""))
        self.assertEqual(Retailer.objects.get().discount_code, "RIPRAPTOR5")

    def test_when_email_cannot_be_sent_the_shop_is_still_listed_and_it_can_be_sent_later(self):
        enquiry = self.ready()
        OutreachMail.objects.update(on=False)
        shop_, why = shops.approve(enquiry)
        self.assertEqual(why, "Sending from RipRaptor is switched off.")
        self.assertTrue(Retailer.objects.filter(pk=shop_.pk, is_active=True).exists())
        self.assertIsNone(self.enquiry().welcome_sent_at)
        OutreachMail.objects.update(on=True)
        self.assertEqual(shops.send_welcome(self.enquiry()), "")
        self.assertEqual(len(self.sent_to("hello@examplecards.co.uk")), 1)

    def test_a_shop_that_asked_not_to_be_emailed_is_not(self):
        existing = make_retailer("Example Cards", website="https://examplecards.co.uk/")
        ShopContact.objects.create(retailer=existing, email="hello@examplecards.co.uk",
                                   status=ShopContact.Status.DO_NOT_EMAIL)
        self.form()
        self.run_check()
        _shop, why = shops.approve(self.enquiry())
        self.assertEqual(why, "The shop asked not to be emailed, so no welcome email was sent.")
        self.assertEqual(self.sent_to("hello@examplecards.co.uk"), [])

    def test_no_smtp_email_ever_leaves(self):
        shops.approve(self.ready())
        self.assertEqual(django_mail.outbox, [])


class AdminTests(FlowCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))

    def page(self):
        return reverse("admin:inbox_shopenquiry_change", args=[self.enquiry().pk])

    def press(self, button):
        return self.client.post(self.page(), {button: "1", "handled_at_0": "", "handled_at_1": "", "note": ""},
                                follow=True)

    def test_the_page_shows_the_check_the_email_check_the_preview_and_the_buttons(self):
        self.form(email="someone@gmail.example")
        self.run_check()
        page = self.client.get(self.page())
        self.assertContains(page, "Passed: waiting for your approval")
        self.assertContains(page, "Not on the shop&#x27;s own domain (examplecards.co.uk)")
        self.assertContains(page, "Example Cards is now on RipRaptor")
        self.assertContains(page, "We have also listed your discount code.")
        self.assertContains(page, 'name="_approve"')
        self.assertContains(page, 'name="_recheck"')
        listing = self.client.get(reverse("admin:inbox_shopenquiry_changelist"))
        self.assertContains(listing, "Passed: waiting for your approval")

    def test_approve_lists_the_shop_and_says_where_the_email_went(self):
        self.form()
        self.run_check()
        response = self.press("_approve")
        self.assertContains(response, "Example Cards is listed and switched on. The welcome email went to "
                                      "hello@examplecards.co.uk.")
        self.assertNotContains(response, 'name="_approve"')
        self.assertEqual(len(self.sent_to("hello@examplecards.co.uk")), 1)

    def test_approve_before_the_check_says_why_and_check_again_queues_it(self):
        self.form()
        self.run_check({"https://examplecards.co.uk/robots.txt": ""})
        response = self.press("_approve")
        self.assertContains(response, "Not approved: It did not pass the check")
        response = self.press("_recheck")
        self.assertContains(response, "The website will be checked within a few minutes.")
        self.assertEqual(ShopToAdd.objects.get().status, ShopToAdd.Status.WAITING)

    def test_a_failed_welcome_can_be_sent_from_the_page(self):
        self.form()
        self.run_check()
        OutreachMail.objects.update(on=False)
        response = self.press("_approve")
        self.assertContains(response, "but the welcome email was not sent: Sending from RipRaptor is switched off.")
        self.assertContains(response, 'name="_send_welcome"')
        OutreachMail.objects.update(on=True)
        response = self.press("_send_welcome")
        self.assertContains(response, "The welcome email went to hello@examplecards.co.uk.")
