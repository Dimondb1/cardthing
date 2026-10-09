import json
import datetime
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


class PreorderAlertTests(TestCase):
    """Alerts asked for since RIPRAPTOR_PREORDER_ALERTS_FROM also hear, once, when a shop opens pre-orders."""

    def setUp(self):
        self.now = timezone.now()
        self.cutoff = self.now - timedelta(days=2)
        overrides = self.settings(RIPRAPTOR_PREORDER_ALERTS_FROM=self.cutoff, **MAIL)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.product = make_product(make_set(make_game()), name="Ascended Heroes Elite Trainer Box", slug="ascended-etb")
        self.shop = make_retailer("Harbour Games")
        self.listing = make_listing(self.product, self.shop, price="49.99", delivery="3.00", availability="out_of_stock")
        self.outbox = Outbox()
        patcher = mock.patch.object(mail, "send", self.outbox)
        patcher.start()
        self.addCleanup(patcher.stop)

    def alert(self, created_at, email="ben@example.com"):
        return StockAlert.objects.create(product=self.product, email=email, created_at=created_at, confirmed_at=created_at)

    def open_preorders(self, listing=None):
        Listing.objects.filter(pk=(listing or self.listing).pk).update(availability="preorder", last_checked=timezone.now())

    def test_an_alert_after_the_cut_off_hears_once_when_a_shop_opens_preorders(self):
        alert = self.alert(self.now - timedelta(hours=1))
        self.assertEqual(alerts.send_due(), (0, 0))   # nothing at any shop yet
        self.open_preorders()
        self.assertEqual(alerts.send_due(), (1, 0))
        message = self.outbox.sent[0]
        self.assertEqual(message["to"], "ben@example.com")
        self.assertEqual(message["subject"], "Pre-orders open: Ascended Heroes Elite Trainer Box, £52.99 at Harbour Games")
        self.assertEqual(message["unsubscribe"], f"https://ripraptor.com/alerts/stop/{alert.token}/")
        self.assertIn(f'href="https://ripraptor.com/alerts/stop/{alert.token}/"', message["html"])
        self.assertIn(">Unsubscribe</a>", message["html"])
        self.assertIn('/static/img/logo.png" width="180" alt="RipRaptor"', message["html"])
        self.assertIn("Cheapest pre-order now: £52.99 delivered at Harbour Games", message["text"])
        self.assertIn("https://ripraptor.com/products/ascended-etb/", message["text"])
        import re

        for body in (message["text"], re.sub(r"<[^>]*>", " ", message["html"])):
            self.assertNotIn("!", body)
            self.assertNotIn("\u2014", body)
        self.assertFalse(StockAlert.objects.exists())
        # One email: the address is gone, so stock arriving later sends nothing.
        Listing.objects.filter(pk=self.listing.pk).update(availability="in_stock")
        self.assertEqual(alerts.send_due(), (0, 0))
        self.assertEqual(len(self.outbox.sent), 1)

    def test_a_shop_in_stock_sends_back_in_stock_not_preorder(self):
        self.alert(self.now - timedelta(hours=1))
        self.open_preorders()
        make_listing(self.product, make_retailer("Dragon Vault"), price="60.00", availability="in_stock")
        self.assertEqual(alerts.send_due(), (1, 0))
        self.assertEqual(self.outbox.sent[0]["subject"], "Back in stock: Ascended Heroes Elite Trainer Box")
        self.assertIn("£60.00 delivered at Dragon Vault", self.outbox.sent[0]["text"])

    def test_an_alert_before_the_cut_off_waits_for_stock(self):
        self.alert(self.cutoff - timedelta(minutes=1))
        self.open_preorders()
        self.assertEqual(alerts.send_due(), (0, 0))
        self.assertTrue(StockAlert.objects.exists())
        Listing.objects.filter(pk=self.listing.pk).update(availability="in_stock")
        self.assertEqual(alerts.send_due(), (1, 0))
        self.assertEqual(self.outbox.sent[0]["subject"], "Back in stock: Ascended Heroes Elite Trainer Box")

    def test_without_the_setting_nobody_hears_about_preorders(self):
        self.alert(self.now - timedelta(hours=1))
        self.open_preorders()
        with self.settings(RIPRAPTOR_PREORDER_ALERTS_FROM=None):
            self.assertEqual(alerts.send_due(), (0, 0))
            self.assertFalse(alerts.preorder_alerts_on())
        self.assertEqual(self.outbox.sent, [])

    def test_marketplace_preorders_never_count(self):
        self.alert(self.now - timedelta(hours=1))
        make_listing(self.product, make_retailer("eBay", source_type=Retailer.Source.EBAY), price="45.00", availability="preorder")
        make_listing(self.product, make_retailer("Amazon", source_type=Retailer.Source.AMAZON), price="44.00", availability="preorder")
        self.assertEqual(alerts.shop_stock(self.product, allow_preorder=True), (None, None))
        self.assertEqual(alerts.send_due(), (0, 0))
        self.assertTrue(StockAlert.objects.exists())

    def test_shop_stock_prefers_stock_and_then_the_cheapest_preorder(self):
        dear = make_listing(self.product, make_retailer("Dragon Vault"), price="70.00", availability="preorder")
        self.open_preorders()
        self.assertEqual(alerts.shop_stock(self.product), (None, None))
        self.assertEqual(alerts.shop_stock(self.product, allow_preorder=True), (self.listing, "preorder"))
        Listing.objects.filter(pk=dear.pk).update(availability="in_stock")
        with self.assertNumQueries(1):
            self.assertEqual(alerts.shop_stock(self.product, allow_preorder=True), (dear, "in_stock"))

    def test_an_unknown_delivery_says_plus_delivery(self):
        Listing.objects.filter(pk=self.listing.pk).update(delivery_known=False)
        self.alert(self.now - timedelta(hours=1))
        self.open_preorders()
        alerts.send_due()
        self.assertEqual(self.outbox.sent[0]["subject"],
                         "Pre-orders open: Ascended Heroes Elite Trainer Box, £49.99 plus delivery at Harbour Games")

    def test_the_form_asks_about_preorders_and_hides_when_a_shop_has_them(self):
        url = self.product.get_absolute_url()
        page = self.client.get(url)
        self.assertContains(page, "Tell me when a shop has it or opens pre-orders")
        self.assertNotContains(page, "Email me when it is back in stock")
        self.assertContains(page, f'action="/alerts/{self.product.slug}/"')
        self.open_preorders()
        self.assertNotContains(self.client.get(url), 'id="alert"')
        # A pre-order on eBay alone is not a shop opening.
        Listing.objects.filter(pk=self.listing.pk).update(availability="out_of_stock")
        make_listing(self.product, make_retailer("eBay", source_type=Retailer.Source.EBAY), price="45.00", availability="preorder")
        self.assertContains(self.client.get(url), "Tell me when a shop has it or opens pre-orders")
        # A cut-off still to come keeps the old promise.
        with self.settings(RIPRAPTOR_PREORDER_ALERTS_FROM=self.now + timedelta(days=1)):
            self.assertContains(self.client.get(url), "Email me when it is back in stock")

    def test_the_confirmation_promises_what_will_be_sent(self):
        self.client.post(reverse("web:alert_ask", args=[self.product.slug]), {"email": "ben@example.com"})
        alert = StockAlert.objects.get()
        self.assertIn("is in stock or open for pre-order at a UK shop we check", self.outbox.sent[0]["text"])
        self.assertContains(self.client.get(reverse("web:alert_confirm", args=[alert.token])),
                            "is in stock or open for pre-order at a UK shop we check")
        StockAlert.objects.update(created_at=self.cutoff - timedelta(days=1), confirmed_at=None)
        self.assertContains(self.client.get(reverse("web:alert_confirm", args=[alert.token])), "is back in stock at a UK shop we check")

    def test_the_preorder_email_promises_nothing_the_site_will_not_do(self):
        # Amazon in stock does not stop a shop pre-order email, so the email must not say nobody has it in stock.
        make_listing(self.product, make_retailer("Amazon", source_type=Retailer.Source.AMAZON), price="44.00", availability="in_stock")
        self.alert(self.now - timedelta(hours=1))
        self.open_preorders()
        self.assertEqual(alerts.send_due(), (1, 0))
        message = self.outbox.sent[0]
        self.assertTrue(message["subject"].startswith("Pre-orders open:"))
        for body in (message["text"], message["html"]):
            self.assertNotIn("in stock yet", body)
            # The form is hidden while a shop has it on pre-order, so the email must not send them back to it.
            self.assertNotIn("Ask again", body)
            self.assertIn("The product page shows when a shop has it in stock.", body)
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, "Amazon")
        self.assertNotContains(page, 'id="alert"')

    def test_old_and_new_alerts_on_one_product_each_get_what_they_asked_for(self):
        # send_due caches the shop check per product and per promise; whichever alert comes first, the old one
        # (back in stock only) must not borrow the new one's pre-order answer, nor the other way round.
        for ordering in (["-created_at"], ["created_at"]):
            with self.subTest(ordering=ordering), mock.patch.object(StockAlert._meta, "ordering", ordering):
                StockAlert.objects.all().delete()
                self.outbox.sent.clear()
                Listing.objects.filter(pk=self.listing.pk).update(availability="out_of_stock")
                old = self.alert(self.cutoff - timedelta(days=1), email="old@example.com")
                self.alert(self.now - timedelta(hours=1), email="new@example.com")
                self.open_preorders()
                self.assertEqual(alerts.send_due(), (1, 0))
                self.assertEqual([(m["to"], m["subject"][:16]) for m in self.outbox.sent],
                                 [("new@example.com", "Pre-orders open:")])
                self.assertEqual(list(StockAlert.objects.all()), [old])

    def test_the_terms_still_describe_the_store(self):
        terms = self.client.get(reverse("web:terms"))
        for sentence in ("Nothing is sent until you confirm", "You get one email when a shop has the product, and your address is then",
                         "Unconfirmed requests are deleted after a week", "Every email has a link that deletes your"):
            self.assertContains(terms, sentence)
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /alerts/")


class PreorderSettingTests(TestCase):
    def test_the_setting_reads_a_date_or_a_moment_and_refuses_a_typo(self):
        from zoneinfo import ZoneInfo

        from ripraptor.settings import preorder_alerts_from

        self.assertIsNone(preorder_alerts_from(""))
        self.assertEqual(preorder_alerts_from("2026-10-09T12:30:00+00:00"),
                         timezone.datetime(2026, 10, 9, 12, 30, tzinfo=datetime.timezone.utc))
        self.assertEqual(preorder_alerts_from("2026-10-09"),
                         timezone.datetime(2026, 10, 9, tzinfo=ZoneInfo("Europe/London")))
        with self.assertRaises(ValueError):
            preorder_alerts_from("9 Oct")

    def test_install_writes_the_install_moment_once(self):
        import re
        import subprocess
        import tempfile
        from pathlib import Path

        from django.conf import settings as django_settings

        from ripraptor.settings import preorder_alerts_from

        script = (Path(django_settings.BASE_DIR) / "deploy" / "install.sh").read_text()
        line = next(l for l in script.splitlines() if l.startswith("grep -q RIPRAPTOR_PREORDER_ALERTS_FROM .env ||"))
        # Before the service restarts, so the site reads it from its first request.
        self.assertLess(script.index(line), script.index("systemctl restart ripraptor"))
        folder = tempfile.mkdtemp()
        env = Path(folder) / ".env"
        env.write_text("DJANGO_DEBUG=0\n")
        for _ in range(2):
            subprocess.run(["bash", "-c", line], cwd=folder, check=True)
        found = re.findall(r"^RIPRAPTOR_PREORDER_ALERTS_FROM=(.+)$", env.read_text(), re.M)
        self.assertEqual(len(found), 1)
        moment = preorder_alerts_from(found[0])
        self.assertLess(abs((timezone.now() - moment).total_seconds()), 60)


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
