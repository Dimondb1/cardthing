import json
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import alerts, mail
from catalogue.models import DailyPageView, Listing, Retailer, StockAlert
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

MAIL = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com",
        "RIPRAPTOR_SITE_URL": "https://ripraptor.com", "RIPRAPTOR_ZEPTOMAIL_URL": "https://api.zeptomail.eu/v1.1/email"}


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, text, html, opener=None, unsubscribe=""):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html, "unsubscribe": unsubscribe})
        return {}


@override_settings(**MAIL)
class StockAlertTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), name="30th Celebration Booster Bundle", slug="30th-bundle", product_type="bundle")
        self.shop = make_retailer("Harbour Games")
        self.listing = make_listing(self.product, self.shop, price="29.99", delivery="3.00", availability="out_of_stock")
        self.outbox = Outbox()
        patcher = mock.patch.object(mail, "send", self.outbox)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, email="Ben@Example.com", **extra):
        return self.client.post(reverse("web:alert_ask", args=[self.product.slug]), {"email": email, **extra})

    def test_the_form_shows_only_when_no_shop_has_it(self):
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, "Email me when it is back in stock")
        self.assertContains(page, f'action="/alerts/{self.product.slug}/"')
        Listing.objects.filter(pk=self.listing.pk).update(availability="in_stock")
        self.assertNotContains(self.client.get(self.product.get_absolute_url()), "Email me when it is back in stock")
        # In stock only on eBay still counts as sold out at the shops.
        Listing.objects.filter(pk=self.listing.pk).update(availability="out_of_stock")
        make_listing(self.product, make_retailer("eBay", source_type=Retailer.Source.EBAY), price="45.00")
        self.assertContains(self.client.get(self.product.get_absolute_url()), "Email me when it is back in stock")

    def test_no_email_settings_no_form(self):
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertNotContains(self.client.get(self.product.get_absolute_url()), "Email me when it is back in stock")

    def test_asking_stores_the_address_and_sends_a_confirmation_only(self):
        response = self.ask()
        self.assertRedirects(response, f"{self.product.get_absolute_url()}?alert=sent#alert", fetch_redirect_response=False)
        alert = StockAlert.objects.get()
        self.assertEqual((alert.email, alert.confirmed_at), ("ben@example.com", None))
        self.assertEqual(len(self.outbox.sent), 1)
        message = self.outbox.sent[0]
        self.assertEqual(message["subject"], "Confirm your alert for 30th Celebration Booster Bundle")
        self.assertIn(f"https://ripraptor.com/alerts/confirm/{alert.token}/", message["text"])
        self.assertIn(f"https://ripraptor.com/alerts/stop/{alert.token}/", message["html"])
        self.assertEqual(message["unsubscribe"], f"https://ripraptor.com/alerts/stop/{alert.token}/")
        self.assertIn(">Unsubscribe</a>", message["html"])
        self.assertIn('/static/img/logo.png" width="180" alt="RipRaptor"', message["html"])
        self.assertIn("#ff5a1f", message["html"])
        self.assertContains(self.client.get(self.product.get_absolute_url() + "?alert=sent"), "Check your inbox and confirm")
        # Asking again before confirming resends; after confirming it says so.
        self.ask()
        self.assertEqual((StockAlert.objects.count(), len(self.outbox.sent)), (1, 2))
        alerts.confirm(alert.token)
        self.assertRedirects(self.ask(), f"{self.product.get_absolute_url()}?alert=already#alert", fetch_redirect_response=False)

    def test_robots_and_bad_addresses_are_turned_away(self):
        self.ask(website="spam")
        self.assertRedirects(self.ask("not an email"), f"{self.product.get_absolute_url()}?alert=invalid#alert", fetch_redirect_response=False)
        self.assertEqual((StockAlert.objects.count(), self.outbox.sent), (0, []))

    def test_a_mail_failure_says_so_and_the_form_works_with_csrf(self):
        def fail(*args, **kwargs):
            raise mail.MailError("down")

        with mock.patch.object(mail, "send", fail):
            self.assertRedirects(self.ask(), f"{self.product.get_absolute_url()}?alert=failed#alert", fetch_redirect_response=False)
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        page = strict.get(self.product.get_absolute_url())
        token = page.cookies["csrftoken"].value
        response = strict.post(reverse("web:alert_ask", args=[self.product.slug]), {"email": "a@b.co", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)

    def test_confirmed_alerts_get_one_email_when_a_shop_restocks_then_the_address_is_gone(self):
        self.ask()
        alert = StockAlert.objects.get()
        self.assertContains(self.client.get(reverse("web:alert_confirm", args=[alert.token])), "Alert confirmed")
        self.outbox.sent.clear()
        self.assertEqual(alerts.send_due(), (0, 0))   # still sold out
        Listing.objects.filter(pk=self.listing.pk).update(availability="in_stock", last_checked=timezone.now())
        self.assertEqual(alerts.send_due(), (1, 0))
        message = self.outbox.sent[0]
        self.assertEqual((message["to"], message["subject"]), ("ben@example.com", "Back in stock: 30th Celebration Booster Bundle"))
        self.assertIn("£32.99 delivered at Harbour Games", message["text"])
        self.assertIn(">Unsubscribe</a>", message["html"])
        self.assertTrue(message["unsubscribe"].startswith("https://ripraptor.com/alerts/stop/"))
        self.assertIn("https://ripraptor.com/products/30th-bundle/", message["text"])
        self.assertFalse(StockAlert.objects.exists())
        self.assertEqual(alerts.send_due(), (0, 0))
        events = dict(DailyPageView.objects.filter(kind="alerts").values_list("key", "hits"))
        self.assertEqual(events, {"asked": 1, "confirmed": 1, "sent": 1})

    def test_unconfirmed_alerts_get_nothing_and_expire(self):
        self.ask()
        Listing.objects.filter(pk=self.listing.pk).update(availability="in_stock")
        self.outbox.sent.clear()
        self.assertEqual(alerts.send_due(), (0, 0))
        self.assertEqual(self.outbox.sent, [])
        StockAlert.objects.update(created_at=timezone.now() - timedelta(days=8))
        alerts.send_due()
        self.assertFalse(StockAlert.objects.exists())

    def test_the_stop_link_deletes_the_address(self):
        self.ask()
        token = StockAlert.objects.get().token
        self.assertContains(self.client.get(reverse("web:alert_stop", args=[token])), "Your alert and your email address have been deleted")
        self.assertFalse(StockAlert.objects.exists())
        self.assertContains(self.client.get(reverse("web:alert_confirm", args=[token])), "This link has expired")

    def test_one_address_is_limited(self):
        with mock.patch.object(alerts, "MAX_PER_EMAIL", 1):
            self.ask()
            other = make_product(self.product.product_set, name="30th Celebration Elite Trainer Box", slug="30th-etb")
            make_listing(other, self.shop, availability="out_of_stock")
            response = self.client.post(reverse("web:alert_ask", args=[other.slug]), {"email": "ben@example.com"})
        self.assertIn("alert=limit", response["Location"])

    def test_terms_explain_what_is_kept_and_robots_keep_alert_links_private(self):
        self.assertContains(self.client.get(reverse("web:terms")), "Zoho ZeptoMail")
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /alerts/")


@override_settings(**MAIL)
class ZeptoMailTests(TestCase):
    def test_the_request_zeptomail_receives(self):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data": [{"code": "EM_104"}]}'

        def opener(request, timeout):
            seen["url"], seen["headers"], seen["body"] = request.full_url, dict(request.header_items()), json.loads(request.data)
            return Response()

        mail.send("ben@example.com", "Subject", "text", "<p>html</p>", opener=opener, unsubscribe="https://ripraptor.com/alerts/stop/x/")
        self.assertEqual(seen["url"], "https://api.zeptomail.eu/v1.1/email")
        self.assertEqual(seen["body"]["mime_headers"], {"List-Unsubscribe": "<https://ripraptor.com/alerts/stop/x/>"})
        self.assertEqual(seen["headers"]["Authorization"], "Zoho-enczapikey tok")
        self.assertEqual(seen["body"]["to"], [{"email_address": {"address": "ben@example.com"}}])
        self.assertEqual(seen["body"]["from"], {"address": "alerts@ripraptor.com", "name": "RipRaptor"})

    def test_the_test_command_and_no_settings(self):
        from io import StringIO

        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            out = StringIO()
            call_command("send_stock_alerts", stdout=out)
            self.assertIn("Email is not set up", out.getvalue())
        outbox = Outbox()
        with mock.patch.object(mail, "send", outbox):
            out = StringIO()
            call_command("send_stock_alerts", "--test", "ben@example.com", stdout=out)
        self.assertEqual(outbox.sent[0]["to"], "ben@example.com")
        self.assertIn(">Unsubscribe</a>", outbox.sent[0]["html"])
        self.assertTrue(outbox.sent[0]["unsubscribe"])
