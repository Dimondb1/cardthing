"""Sending a shop email from the app (inbox/sending.py), through ZeptoMail by default or the owner's own Zoho mailbox.
The owner, 3 Nov 2026: "I want to send emails each time from the app ... zepto for mass mail and zoho is my normal
emails", then, after the mailbox timed out on the server, "I want to use zepto to send out". No test reaches a real
mail server: the test runner catches every SMTP email in mail.outbox, and ZeptoMail's web address is answered here."""

import io
import json
import os
import urllib.error
import smtplib
import stat
import tempfile
import shutil
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from . import outreach, sending
from .models import OutreachEmail, OutreachMail, ShopContact
from .tests_outreach import OutreachTestCase

MAILBOX = "ben@ripraptor.example"


@override_settings(RIPRAPTOR_SITE_URL="https://ripraptor.example")
class SendingCase(OutreachTestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.enterContext(override_settings(RIPRAPTOR_OUTREACH_PASSWORD_FILE=os.path.join(folder, "password"),
                                            RIPRAPTOR_OUTREACH_TOKEN_FILE=os.path.join(folder, "token")))
        outreach.ensure_contacts()
        ShopContact.objects.filter(retailer=self.shop).update(email="hello@harbour.example")
        self.contact = ShopContact.objects.get(retailer=self.shop)

    def switch_on(self, **fields):
        """Through the Zoho mailbox. ZeptoTests switch on through ZeptoMail."""
        OutreachMail.objects.update_or_create(pk=1, defaults={"on": True, "service": "mailbox", "username": MAILBOX,
                                                              **fields})
        sending.save_password("zoho-app-pass-1234")

    def sent(self):
        return OutreachEmail.objects.count()


class SetUpTests(SendingCase):
    def test_nothing_sends_until_it_is_switched_on_and_has_a_mailbox_and_a_password(self):
        self.assertEqual(sending.why_not(), "Sending from RipRaptor is switched off.")
        OutreachMail.objects.update_or_create(pk=1, defaults={"on": True, "service": "mailbox"})
        self.assertIn("mail server and your mailbox are needed", sending.why_not())
        OutreachMail.objects.update(username=MAILBOX)
        self.assertIn("app password is needed", sending.why_not())
        sending.save_password("zoho-app-pass-1234")
        self.assertEqual((sending.why_not(), sending.ready()), ("", True))

    def test_the_password_is_kept_in_a_file_only_the_site_can_read_and_never_in_the_database(self):
        self.switch_on()
        path = sending.password_path()
        self.assertEqual(path.read_text(), "zoho-app-pass-1234")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        row = OutreachMail.objects.get()
        self.assertEqual(row.password_hint, "1234")
        self.assertNotIn("zoho-app-pass", " ".join(str(value) for value in row.__dict__.values()))

    def test_through_the_mailbox_it_goes_by_the_mailbox_and_not_by_zeptomail(self):
        self.switch_on()
        with mock.patch("catalogue.mail.send", side_effect=AssertionError("ZeptoMail must not be used")):
            outreach.send_now(self.contact, "update")
        self.assertEqual(len(mail.outbox), 1)


class SendNowTests(SendingCase):
    def test_it_goes_from_the_owner_with_replies_back_to_them_and_is_recorded_like_mark_as_sent(self):
        self.switch_on(from_name="Ben Dimond")
        self.click(self.etb, 1)
        subject, address = outreach.send_now(self.contact, "update")
        self.assertEqual(address, "hello@harbour.example")
        [message] = mail.outbox
        self.assertEqual((message.subject, message.to), (subject, ["hello@harbour.example"]))
        self.assertEqual(message.from_email, f"Ben Dimond <{MAILBOX}>")
        self.assertEqual((message.reply_to, message.bcc), ([MAILBOX], [MAILBOX]))   # replies, and a copy, to the owner
        self.assertTrue(message.body.startswith("Hi all at Harbour Games,\n\n"))
        self.assertTrue(message.body.endswith("just reply and say so, and I won't."))
        self.assertEqual(message.body, outreach.draft(self.shop, "update", outreach.figures(self.shop))[1])
        # As Mark as sent: logged, Emailed, a follow-up in two weeks.
        contact = ShopContact.objects.get(retailer=self.shop)
        self.assertEqual((contact.status, contact.follow_up_on),
                         (ShopContact.Status.EMAILED, timezone.localdate() + timedelta(days=14)))
        self.assertEqual(OutreachEmail.objects.get().subject, subject)

    def test_it_can_send_from_an_alias_and_without_a_copy(self):
        self.switch_on(from_address="ben@alias.example", copy_to_me=False)
        outreach.send_now(self.contact, "badge")
        [message] = mail.outbox
        self.assertEqual((message.from_email, message.reply_to, message.bcc),
                         ("Ben Dimond <ben@alias.example>", ["ben@alias.example"], []))

    def refused(self, contact=None, kind="update", **expect):
        with self.assertRaises(sending.SendError) as caught:
            outreach.send_now(contact or self.contact, kind)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.sent(), 0)
        return str(caught.exception)

    def test_it_refuses_and_sends_nothing_when_it_is_not_set_up(self):
        self.assertIn("switched off", self.refused())

    def test_it_refuses_a_shop_with_no_address_or_a_bad_one(self):
        self.switch_on()
        ShopContact.objects.filter(pk=self.contact.pk).update(email="")
        self.assertIn("no email address saved", self.refused(ShopContact.objects.get(pk=self.contact.pk)))
        ShopContact.objects.filter(pk=self.contact.pk).update(email="not an address")
        self.assertIn("no email address saved", self.refused(ShopContact.objects.get(pk=self.contact.pk)))

    def test_it_refuses_a_shop_that_asked_not_to_be_emailed_and_an_unknown_email(self):
        self.switch_on()
        ShopContact.objects.filter(pk=self.contact.pk).update(status=ShopContact.Status.DO_NOT_EMAIL)
        self.assertIn("asked not to be emailed", self.refused(ShopContact.objects.get(pk=self.contact.pk)))
        ShopContact.objects.filter(pk=self.contact.pk).update(status=ShopContact.Status.NEW)
        self.assertIn("not one of the drafts", self.refused(ShopContact.objects.get(pk=self.contact.pk), kind="nope"))

    def test_the_same_email_is_not_sent_twice_in_a_week_but_another_is_and_it_can_be_again_later(self):
        self.switch_on()
        outreach.send_now(self.contact, "update")
        mail.outbox.clear()
        again = ShopContact.objects.get(pk=self.contact.pk)
        with self.assertRaisesMessage(sending.SendError, "was sent this email on"):
            outreach.send_now(again, "update")
        self.assertEqual(mail.outbox, [])
        outreach.send_now(again, "affiliate")   # a different email is a different matter
        self.assertEqual(len(mail.outbox), 1)
        OutreachEmail.objects.filter(kind="update").update(sent_at=timezone.now() - timedelta(days=8))
        outreach.send_now(ShopContact.objects.get(pk=self.contact.pk), "update")
        self.assertEqual(len(mail.outbox), 2)

    def test_the_days_limit_counts_emails_marked_as_sent_too_and_tomorrow_starts_again(self):
        self.switch_on(daily_limit=2)
        other = ShopContact.objects.get(retailer=self.other)
        ShopContact.objects.filter(pk=other.pk).update(email="info@quay.example")
        outreach.mark_sent(ShopContact.objects.get(pk=other.pk), "update", "By hand")   # one by the mail app
        self.assertEqual((sending.sent_today(), sending.room_today()), (1, 1))
        outreach.send_now(self.contact, "update")
        self.assertEqual(sending.room_today(), 0)
        extra = ShopContact.objects.get(pk=other.pk)
        with self.assertRaisesMessage(sending.SendError, "Today's limit is reached"):
            outreach.send_now(extra, "badge")
        self.assertEqual(len(mail.outbox), 1)
        OutreachEmail.objects.update(sent_at=timezone.now() - timedelta(days=1, hours=1))
        self.assertEqual(sending.room_today(), 2)

    def test_a_refused_send_is_worded_for_the_owner_notes_the_problem_and_records_nothing(self):
        self.switch_on()
        cases = {
            smtplib.SMTPAuthenticationError(535, b"bad"): "did not accept the mailbox or its app password",
            smtplib.SMTPRecipientsRefused({"x": (550, b"no")}): "The shop's address was refused",
            smtplib.SMTPSenderRefused(553, b"no", "ben@x"): "would not send from that address",
            smtplib.SMTPServerDisconnected("gone"): "Could not reach the mail server",
            TimeoutError("slow"): "Could not reach the mail server",
        }
        for error, words in cases.items():
            with self.subTest(words), mock.patch.object(EmailBackend, "send_messages", side_effect=error):
                with self.assertRaisesMessage(sending.SendError, words):
                    outreach.send_now(ShopContact.objects.get(pk=self.contact.pk), "update")
                self.assertIn(words, OutreachMail.objects.get().problem)
                self.assertEqual(self.sent(), 0)
        outreach.send_now(ShopContact.objects.get(pk=self.contact.pk), "update")   # then it works, and the note clears
        self.assertEqual(OutreachMail.objects.get().problem, "")
        self.assertEqual(self.sent(), 1)


class AdminCase(SendingCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        self.url = reverse("admin:inbox_outreachmail_change", args=[OutreachMail.load().pk])

    def post(self, **fields):
        data = {"on": "on", "service": "mailbox", "host": "smtp.zoho.com", "port": "465", "username": MAILBOX,
                "password": "zoho-app-pass-1234", "from_name": "Ben Dimond", "from_address": "", "reply_to": "",
                "copy_to_me": "on", "daily_limit": "30", **fields}
        return self.client.post(self.url, {key: value for key, value in data.items() if value is not None}, follow=True)


class SendingPageTests(AdminCase):
    def test_the_list_opens_the_one_row_and_says_how_to_set_it_up(self):
        response = self.client.get(reverse("admin:inbox_outreachmail_changelist"))
        self.assertRedirects(response, self.url)
        page = self.client.get(self.url)
        self.assertContains(page, "Sending from RipRaptor is switched off.")
        self.assertContains(page, "accounts.zoho.com")
        self.assertContains(page, "never needed")   # the normal Zoho password
        self.assertContains(page, "smtp.zoho.com")

    def test_saving_the_mailbox_and_password_turns_sending_on_and_never_shows_the_password_again(self):
        response = self.post()
        self.assertContains(response, "Password saved. It is not shown again.")
        self.assertContains(response, "Ready: Send now is on every shop email.")
        self.assertContains(response, "Through your Zoho mailbox. App password saved (ends 1234).")
        self.assertNotContains(response, "zoho-app-pass-1234")
        self.assertEqual(sending.password(), "zoho-app-pass-1234")
        # Saved again without a password: the one saved is kept.
        response = self.post(password="")
        self.assertEqual(sending.password(), "zoho-app-pass-1234")
        self.assertContains(response, "Ready:")

    def test_it_cannot_be_switched_on_without_a_mailbox_or_a_password_or_with_another_port(self):
        self.assertContains(self.post(username="", password="x"), "Add your mailbox address to switch sending on.")
        self.assertContains(self.post(password=""), "Add the app password to switch sending on.")
        self.assertContains(self.post(port="25"), "Use 465 (Zoho&#x27;s usual) or 587.")
        self.assertFalse(OutreachMail.objects.get().on)

    def test_a_test_goes_to_the_owners_own_mailbox_marked_as_a_test_and_a_failure_is_said(self):
        response = self.post(send_test="on")
        self.assertContains(response, f"Test sent to {MAILBOX}.")
        [message] = mail.outbox
        self.assertEqual((message.to, message.subject.startswith("[Test] ")), ([MAILBOX], True))
        self.assertEqual(self.sent(), 0)   # a test is never recorded against a shop
        with mock.patch.object(EmailBackend, "send_messages", side_effect=smtplib.SMTPAuthenticationError(535, b"bad")):
            response = self.post(send_test="on")
        self.assertContains(response, "The test was not sent: Zoho did not accept the mailbox or its app password.")
        self.assertContains(response, "Last problem:")

    def test_admins_home_page_leads_to_it(self):
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn(f'href="{reverse("admin:inbox_outreachmail_changelist")}" class="section">Sending shop emails', html)


class ButtonTests(AdminCase):
    def setUp(self):
        super().setUp()
        self.page = reverse("admin:inbox_shopcontact_update_emails")
        self.shop_page = reverse("admin:inbox_shopcontact_change", args=[self.contact.pk])

    def test_send_now_is_there_only_when_sending_is_set_up_with_a_way_to_set_it_up_when_not(self):
        html = self.client.get(self.page).content.decode()
        self.assertNotIn('name="_send"', html)
        self.assertIn("Sending from RipRaptor is switched off.", html)
        self.assertIn(f'href="{reverse("admin:inbox_outreachmail_changelist")}">Set it up</a>', html)
        self.switch_on()
        html = self.client.get(self.page).content.decode()
        self.assertIn('name="_send" value="update" data-confirm="Send this email to Harbour Games now?">Send now', html)
        self.assertIn("Sending from RipRaptor is on: 30 more can go today.", html)
        self.assertNotIn("Set it up", html)
        # Save address stays the first button, so Enter in the box saves rather than sends.
        self.assertLess(html.index('name="_save"'), html.index('name="_send"'))

    def test_pressing_it_sends_marks_the_shop_and_the_shop_drops_off_the_list(self):
        self.switch_on()
        response = self.client.post(self.page, {"contact": self.contact.pk, "email": "hello@harbour.example",
                                                "_send": "1"}, follow=True)
        self.assertRedirects(response, self.page)
        self.assertContains(response, "Sent to Harbour Games at hello@harbour.example. Follow up on")
        [message] = mail.outbox
        self.assertEqual(message.to, ["hello@harbour.example"])
        self.assertEqual(OutreachEmail.objects.get().kind, "update")
        self.assertNotContains(response, "Harbour Games</h2>")

    def test_an_address_typed_in_the_same_press_is_saved_and_used(self):
        self.switch_on()
        ShopContact.objects.filter(pk=self.contact.pk).update(email="")
        self.client.post(self.page, {"contact": self.contact.pk, "email": " info@harbour.example ", "_send": "1"})
        self.assertEqual(mail.outbox[0].to, ["info@harbour.example"])
        self.assertEqual(ShopContact.objects.get(pk=self.contact.pk).email, "info@harbour.example")

    def test_a_refused_press_says_why_sends_nothing_and_records_nothing(self):
        self.switch_on()
        ShopContact.objects.filter(pk=self.contact.pk).update(status=ShopContact.Status.DO_NOT_EMAIL)
        response = self.client.post(self.page, {"contact": self.contact.pk, "email": "hello@harbour.example",
                                                "_send": "1"}, follow=True)
        self.assertContains(response, "asked not to be emailed, so nothing was sent.")
        self.assertEqual((mail.outbox, self.sent()), ([], 0))
        with mock.patch.object(EmailBackend, "send_messages", side_effect=smtplib.SMTPServerDisconnected("gone")):
            ShopContact.objects.filter(pk=self.contact.pk).update(status=ShopContact.Status.NEW)
            response = self.client.post(self.page, {"contact": self.contact.pk, "email": "hello@harbour.example",
                                                    "_send": "1"}, follow=True)
        self.assertContains(response, "Could not reach the mail server")
        self.assertEqual(self.sent(), 0)

    def test_each_draft_on_a_shops_own_page_has_send_now_and_it_sends_that_one(self):
        self.switch_on()
        html = self.client.get(self.shop_page).content.decode()
        for kind, _name in outreach.DRAFTS:
            self.assertIn(f'name="_send" value="{kind}" data-confirm="Send this email to Harbour Games now?">Send now', html)
        post = {"email": "hello@harbour.example", "status": "new", "notes": "", "_send": "badge"}
        response = self.client.post(self.shop_page, post, follow=True)
        self.assertContains(response, "Sent to Harbour Games at hello@harbour.example.")
        self.assertEqual([message.subject for message in mail.outbox], ["A little RipRaptor badge for Harbour Games"])
        self.assertEqual(OutreachEmail.objects.get().kind, "badge")

    def test_a_shops_own_page_says_how_to_set_it_up_when_it_is_not(self):
        html = self.client.get(self.shop_page).content.decode()
        self.assertNotIn('name="_send"', html)
        self.assertIn("Sending from RipRaptor is switched off.", html)
        self.assertIn(">Set it up</a> to send from here.", html)

    def test_the_press_asks_first(self):
        from pathlib import Path

        from django.conf import settings

        script = (Path(settings.BASE_DIR) / "web" / "static" / "js" / "copy-mail.js").read_text()
        self.assertIn('event.target.closest("button[data-confirm]")', script)
        self.assertIn("window.confirm(sender.dataset.confirm)", script)


class FakeZeptoMail:
    """Answers ZeptoMail's web address as it does, or with an error, and keeps each request."""

    def __init__(self):
        self.requests, self.error = [], None

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return io.BytesIO(b'{"data": [{"code": "EM_104", "message": "Email request received"}]}')

    def bodies(self):
        return [json.loads(request.data) for request in self.requests]


SENT_FROM, REPLIES = "info@ripraptor.example", "ben@gmail.example"


@override_settings(RIPRAPTOR_ZEPTOMAIL_TOKEN="alerts-token", RIPRAPTOR_MAIL_FROM="alerts@ripraptor.example",
                   RIPRAPTOR_ZEPTOMAIL_URL="https://zeptomail.example/v1.1/email")
class ZeptoCase(SendingCase):
    def setUp(self):
        super().setUp()
        self.zepto = FakeZeptoMail()
        self.enterContext(mock.patch("urllib.request.urlopen", self.zepto))

    def switch_on(self, **fields):
        OutreachMail.objects.update_or_create(pk=1, defaults={
            "on": True, "from_address": SENT_FROM, "reply_to": REPLIES, **fields})


class ZeptoTests(ZeptoCase):
    def test_zeptomail_is_the_default_so_a_row_saved_before_moves_to_it(self):
        self.assertEqual(OutreachMail.load().service, OutreachMail.Service.ZEPTOMAIL)

    def test_it_goes_through_zeptomail_with_the_alerts_token_as_plain_text_with_replies_and_a_copy_to_the_owner(self):
        self.switch_on(from_name="Ben Dimond")
        self.assertEqual(sending.why_not(), "")   # nothing more to set up: the alert emails' token is on the server
        subject, address = outreach.send_now(self.contact, "update")
        self.assertEqual(mail.outbox, [])   # no SMTP at all
        [request] = self.zepto.requests
        self.assertEqual((request.full_url, request.get_header("Authorization")),
                         ("https://zeptomail.example/v1.1/email", "Zoho-enczapikey alerts-token"))
        [body] = self.zepto.bodies()
        self.assertEqual(body, {
            "from": {"address": SENT_FROM, "name": "Ben Dimond"},
            "to": [{"email_address": {"address": "hello@harbour.example"}}],
            "subject": subject,
            "textbody": outreach.draft(self.shop, "update", outreach.figures(self.shop))[1],
            "reply_to": [{"address": REPLIES}],
            "bcc": [{"email_address": {"address": REPLIES}}],
            "track_opens": False, "track_clicks": False,
        })   # no HTML, no tracking and no unsubscribe header: a personal email
        self.assertEqual(address, "hello@harbour.example")
        contact = ShopContact.objects.get(pk=self.contact.pk)
        self.assertEqual((contact.status, OutreachEmail.objects.get().subject), (ShopContact.Status.EMAILED, subject))

    def test_replies_go_to_the_sent_from_address_unless_set_and_the_copy_can_be_turned_off(self):
        self.switch_on(reply_to="", copy_to_me=False)
        outreach.send_now(self.contact, "badge")
        [body] = self.zepto.bodies()
        self.assertEqual(body["reply_to"], [{"address": SENT_FROM}])
        self.assertNotIn("bcc", body)

    def test_it_needs_a_sent_from_address_on_the_alerts_domain_and_a_token(self):
        self.switch_on(from_address="")
        self.assertEqual(sending.why_not(), "Sending from RipRaptor is not set up: the Sent from address is needed.")
        OutreachMail.objects.update(from_address="ben@gmail.example", username="ben@gmail.example")
        self.assertEqual(sending.why_not(),
                         "Sending from RipRaptor is not set up: the Sent from address must be on ripraptor.example.")
        OutreachMail.objects.update(from_address="ben@news.ripraptor.example")
        self.assertEqual(sending.why_not(), "")
        with override_settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertIn("ZeptoMail has no token on the server", sending.why_not())
            with self.assertRaisesMessage(sending.SendError, "ZeptoMail has no token"):
                outreach.send_now(self.contact, "update")
        self.assertEqual(self.zepto.requests, [])

    def test_a_token_of_its_own_is_kept_in_a_private_file_used_instead_and_can_be_forgotten(self):
        self.switch_on()
        sending.save_token("shop-token-9876")
        self.assertEqual(stat.S_IMODE(sending.token_path().stat().st_mode), 0o600)
        self.assertEqual(OutreachMail.objects.get().token_hint, "9876")
        outreach.send_now(self.contact, "update")
        self.assertEqual(self.zepto.requests[0].get_header("Authorization"), "Zoho-enczapikey shop-token-9876")
        sending.forget_token()
        self.assertEqual((sending.token(), OutreachMail.objects.get().token_hint), ("alerts-token", ""))
        sending.forget_token()   # twice is fine

    def test_a_refusal_is_worded_for_the_owner_noted_and_nothing_is_recorded(self):
        self.switch_on()
        cases = {
            urllib.error.HTTPError("u", 401, "no", None, io.BytesIO(b"bad token")): "ZeptoMail did not accept the token",
            urllib.error.HTTPError("u", 400, "no", None, io.BytesIO(b"bad from")): "ZeptoMail refused the email",
            urllib.error.URLError("down"): "ZeptoMail could not be reached",
        }
        for error, words in cases.items():
            self.zepto.error = error
            with self.subTest(words), self.assertRaisesMessage(sending.SendError, words):
                outreach.send_now(ShopContact.objects.get(pk=self.contact.pk), "update")
            self.assertIn(words, OutreachMail.objects.get().problem)
            self.assertEqual(self.sent(), 0)
        self.zepto.error = None
        outreach.send_now(ShopContact.objects.get(pk=self.contact.pk), "update")
        self.assertEqual((OutreachMail.objects.get().problem, self.sent()), ("", 1))

    def test_the_alert_emails_are_sent_as_before(self):
        from catalogue import mail as zeptomail

        zeptomail.send("fan@example.com", "Back in stock", "text", "<p>html</p>", unsubscribe="https://x.example/stop")
        [body] = self.zepto.bodies()
        self.assertEqual(body["from"], {"address": "alerts@ripraptor.example", "name": "RipRaptor"})
        self.assertEqual((body["textbody"], body["htmlbody"]), ("text", "<p>html</p>"))
        self.assertEqual(body["mime_headers"], {"List-Unsubscribe": "<https://x.example/stop>"})
        for key in ("reply_to", "bcc", "track_opens", "track_clicks"):
            self.assertNotIn(key, body)
        self.assertEqual(self.zepto.requests[0].get_header("Authorization"), "Zoho-enczapikey alerts-token")

    def test_a_mailbox_that_times_out_points_to_zeptomail(self):
        self.assertIn("choose ZeptoMail under Send through", sending.explain(TimeoutError("timed out")))


class ZeptoPageTests(ZeptoCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        self.url = reverse("admin:inbox_outreachmail_change", args=[OutreachMail.load().pk])

    def post(self, **fields):
        data = {"on": "on", "service": "zeptomail", "host": "smtp.zoho.com", "port": "465", "username": "",
                "password": "", "from_name": "Ben Dimond", "from_address": SENT_FROM, "reply_to": REPLIES,
                "copy_to_me": "on", "daily_limit": "30", **fields}
        return self.client.post(self.url, {key: value for key, value in data.items() if value is not None}, follow=True)

    def test_the_page_says_what_zeptomail_risks(self):
        page = self.client.get(self.url)
        self.assertContains(page, "ZeptoMail, as the alert emails")
        self.assertContains(page, "Zoho can close the account for too many bounces or complaints")

    def test_switched_on_with_an_address_and_a_test_it_sends_through_zeptomail_to_the_owners_inbox(self):
        response = self.post(send_test="on")
        self.assertContains(response, f"Test sent to {REPLIES}.")
        self.assertContains(response, "Ready: Send now is on every shop email.")
        self.assertContains(response, "Through ZeptoMail, with the alert emails&#x27; token.")
        [body] = self.zepto.bodies()
        self.assertEqual(body["to"], [{"email_address": {"address": REPLIES}}])
        self.assertTrue(body["subject"].startswith("[Test] "))
        self.assertEqual(self.sent(), 0)

    def test_an_address_off_the_domain_or_no_token_anywhere_cannot_be_switched_on(self):
        self.assertContains(self.post(from_address="ben@gmail.example"),
                            "Through ZeptoMail it must be an address on ripraptor.example")
        self.assertContains(self.post(from_address=""), "Add the address shops see")
        with override_settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertContains(self.post(), "ZeptoMail has no token on the server. Paste one to switch sending on.")
            self.assertContains(self.post(token="shop-token-9876"), "Ready:")
        self.assertEqual(sending.own_token(), "shop-token-9876")

    def test_a_pasted_token_is_saved_never_shown_and_can_be_forgotten(self):
        response = self.post(token="shop-token-9876")
        self.assertContains(response, "Token saved. It is not shown again.")
        self.assertContains(response, "own token (ends 9876).")
        self.assertNotContains(response, "shop-token-9876")
        self.post(token="")   # saved again empty: kept
        self.assertEqual(sending.own_token(), "shop-token-9876")
        response = self.post(forget_token="on")
        self.assertContains(response, "Token forgotten: shop emails use the alert emails&#x27; token.")
        self.assertEqual(sending.own_token(), "")

    def test_send_now_on_the_update_emails_page_goes_through_zeptomail(self):
        self.switch_on()
        page = reverse("admin:inbox_shopcontact_update_emails")
        response = self.client.post(page, {"contact": self.contact.pk, "email": "hello@harbour.example", "_send": "1"},
                                    follow=True)
        self.assertContains(response, "Sent to Harbour Games at hello@harbour.example.")
        self.assertEqual(self.zepto.bodies()[0]["to"], [{"email_address": {"address": "hello@harbour.example"}}])
        self.assertEqual(mail.outbox, [])
