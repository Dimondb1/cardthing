"""Admin, Email sending (web/email_admin.py): which way every site email goes, connecting Zoho Mail, and a test email
that says what happened. The owner, 14 Nov 2026: "I've tried to send an email from the site and it said it's failing.
However, you might need to verify that"."""

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from catalogue import zoho_mail
from catalogue.tests_zoho_mail import ZohoCase

URL = reverse("email_sending")


class PageCase(ZohoCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))

    def post(self, **fields):
        return self.client.post(URL, fields, follow=True)

    def connect_page(self, **fields):
        return self.post(**{"action": "connect", "region": "eu", "client_id": "client-id",
                            "client_secret": "client-secret", "code": "good-code", "use_now": "on", **fields})


class PageTests(PageCase):
    def test_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)
        response = self.client.post(URL, {"action": "forget"})
        self.assertEqual(response.status_code, 302)

    def test_admins_home_page_leads_to_it(self):
        self.assertContains(self.client.get(reverse("admin:index")), URL)

    def test_it_says_the_way_now_and_how_to_connect(self):
        page = self.client.get(URL)
        self.assertContains(page, "Every email the site sends goes through ZeptoMail.")
        self.assertContains(page, zoho_mail.SCOPES)
        self.assertContains(page, "Self Client")
        self.assertContains(page, 'name="client_secret"')
        self.assertNotContains(page, "Disconnect Zoho Mail")
        self.assertEqual(page["Cache-Control"], "private, no-store")

    def test_connecting_switches_every_email_over_and_never_shows_the_secret_again(self):
        response = self.connect_page()
        self.assertContains(response, "Connected to Zoho Mail: ben@ripraptor.example. Every email the site sends now "
                                      "goes through it, from alerts@ripraptor.example.")
        self.assertContains(response, "Every email the site sends goes through Zoho Mail, from alerts@ripraptor.example.")
        self.assertNotContains(response, "client-secret")
        self.assertNotContains(response, "refresh-1")
        self.assertTrue(zoho_mail.in_use())

    def test_a_failed_connection_says_why_and_changes_nothing(self):
        response = self.connect_page(code="old-code")
        self.assertContains(response, "Zoho Mail is not connected: Zoho did not accept the code.")
        self.assertFalse(zoho_mail.connected())

    def test_the_test_email_goes_the_chosen_way_and_says_so(self):
        response = self.post(action="test", to="ben@ripraptor.example")
        self.assertContains(response, "Test sent to ben@ripraptor.example through ZeptoMail.")
        self.connect_page()
        response = self.post(action="test", to="ben@ripraptor.example")
        self.assertContains(response, "Test sent to ben@ripraptor.example through Zoho Mail.")
        [sent] = self.zoho.sent()
        self.assertEqual((sent["toAddress"], sent["subject"], sent["mailFormat"]),
                         ("ben@ripraptor.example", "[Test] RipRaptor email sending", "plaintext"))

    def test_a_test_zeptomail_refuses_for_its_review_says_so_plainly(self):
        self.zoho.zepto = (403, {"error": {"code": "TM_4001", "details": [{"code": "SM_128"}]}})
        response = self.post(action="test", to="ben@ripraptor.example")
        self.assertContains(response, "Not sent through ZeptoMail: ZeptoMail has not finished reviewing your account")

    def test_without_any_way_to_send_the_test_says_so(self):
        with override_settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            response = self.post(action="test", to="ben@ripraptor.example")
        self.assertContains(response, "Email is not set up")
        self.assertEqual(self.zoho.requests, [])

    def test_choosing_the_way_and_the_from_address_and_disconnecting(self):
        self.connect_page()
        response = self.post(action="choose", route="zoho", from_address="hello@ripraptor.example")
        self.assertContains(response, "now goes through Zoho Mail, from hello@ripraptor.example")
        response = self.post(action="choose", route="zeptomail", from_address="hello@ripraptor.example")
        self.assertContains(response, "goes through ZeptoMail again")
        self.assertFalse(zoho_mail.in_use())
        response = self.post(action="choose", route="zoho", from_address="someone@else.example")
        self.assertContains(response, "Zoho Mail cannot send from someone@else.example")
        self.assertFalse(zoho_mail.in_use())
        response = self.post(action="forget")
        self.assertContains(response, "Zoho Mail is disconnected")
        self.assertFalse(self.file.exists())
