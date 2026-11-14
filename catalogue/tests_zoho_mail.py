"""Every site email through the owner's Zoho mailbox, over Zoho Mail's web API (catalogue/zoho_mail.py), while ZeptoMail
refuses them until it reviews the account. The owner, 14 Nov 2026: "can you look into whether we can just move the
outgoing emails onto Zoho while we know that works"."""

import io
import json
import stat
import tempfile
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from . import mail, zoho_mail

ACCOUNT = {
    "accountId": "2560636000000008002", "primaryEmailAddress": "ben@ripraptor.example", "type": "ZOHO_ACCOUNT",
    "sendMailDetails": [{"fromAddress": "ben@ripraptor.example", "mode": "mailbox", "status": True},
                        {"fromAddress": "alerts@ripraptor.example", "mode": "extfrom", "status": True},
                        {"fromAddress": "old@elsewhere.example", "mode": "extfrom", "status": False}],
    "emailAddress": [{"mailId": "ben@ripraptor.example", "isPrimary": True, "isConfirmed": True},
                     {"mailId": "hello@ripraptor.example", "isAlias": True, "isConfirmed": True}],
}


class Answer(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode())
        self.status = status


class FakeZoho:
    """Zoho's sign-in server and Zoho Mail's API, as their documentation describes them. Keeps every request."""

    def __init__(self):
        self.requests = []
        self.codes = {"good-code": "refresh-1"}
        self.refreshes = 0
        self.expired = 0          # how many sends answer 401 before one is taken
        self.refuse = None        # a send's error answer, as (status, body)
        self.zepto = None         # ZeptoMail's error answer, as (status, body)

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url
        if "/oauth/v2/token" in url:
            fields = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            if fields["grant_type"] == "authorization_code":
                refresh = self.codes.pop(fields["code"], None)
                if refresh is None or fields["client_secret"] != "client-secret":
                    return Answer({"error": "invalid_code" if refresh is None else "invalid_client_secret"})
                return Answer({"access_token": "access-1", "refresh_token": refresh, "expires_in": 3600,
                               "api_domain": "https://www.zohoapis.eu", "token_type": "Bearer"})
            self.refreshes += 1
            return Answer({"access_token": f"access-r{self.refreshes}", "expires_in": 3600, "token_type": "Bearer"})
        if url.endswith("/api/accounts"):
            return Answer({"status": {"code": 200, "description": "success"}, "data": [ACCOUNT]})
        if "/messages" in url:
            if self.expired:
                self.expired -= 1
                raise urllib.error.HTTPError(url, 401, "Unauthorized", None, io.BytesIO(
                    b'{"status": {"code": 401, "description": "Invalid OAuthtoken"}, "data": {}}'))
            if self.refuse:
                status, body = self.refuse
                raise urllib.error.HTTPError(url, status, "no", None, io.BytesIO(json.dumps(body).encode()))
            return Answer({"status": {"code": 200, "description": "success"}, "data": {"messageId": "1"}})
        if "zeptomail" in url:
            if self.zepto:
                status, body = self.zepto
                raise urllib.error.HTTPError(url, status, "no", None, io.BytesIO(json.dumps(body).encode()))
            return Answer({"data": [{"code": "EM_104", "message": "Email request received"}]})
        raise AssertionError(f"unexpected request to {url}")

    def sent(self):
        return [json.loads(request.data) for request in self.requests if "/messages" in request.full_url]


class ZohoCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "zoho-mail"
        self.enterContext(override_settings(
            RIPRAPTOR_ZOHO_MAIL_FILE=str(self.file), RIPRAPTOR_MAIL_FROM="alerts@ripraptor.example",
            RIPRAPTOR_ZEPTOMAIL_TOKEN="zepto-token", RIPRAPTOR_ZEPTOMAIL_URL="https://zeptomail.example/v1.1/email"))
        self.zoho = FakeZoho()
        self.enterContext(mock.patch("urllib.request.urlopen", self.zoho))

    def connect(self, region="eu", use=True):
        data = zoho_mail.connect(region, " client-id ", "client-secret", "good-code")
        if use:
            zoho_mail.use(True)
        return data


class ConnectTests(ZohoCase):
    def test_a_code_becomes_a_lasting_connection_kept_in_a_private_file(self):
        data = self.connect(use=False)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        kept = json.loads(self.file.read_text())
        self.assertEqual((kept["refresh_token"], kept["account_id"], kept["client_id"], kept["region"]),
                         ("refresh-1", "2560636000000008002", "client-id", "eu"))
        self.assertEqual(data["addresses"], ["ben@ripraptor.example", "alerts@ripraptor.example",
                                             "hello@ripraptor.example"])
        # The site's own sending address, as the mailbox may send from it.
        self.assertEqual((data["mailbox"], data["from_address"]), ("ben@ripraptor.example", "alerts@ripraptor.example"))
        self.assertTrue(zoho_mail.connected())
        self.assertFalse(zoho_mail.in_use())   # not until chosen

    def test_it_signs_in_at_the_accounts_region_as_zohos_documentation_shows(self):
        self.connect(region="uk")
        token, accounts = self.zoho.requests[:2]
        self.assertEqual((token.get_method(), urllib.parse.urlsplit(token.full_url)[:3]),
                         ("POST", ("https", "accounts.zoho.uk", "/oauth/v2/token")))
        self.assertEqual(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(token.full_url).query)), {
            "grant_type": "authorization_code", "client_id": "client-id", "client_secret": "client-secret",
            "code": "good-code"})
        self.assertEqual(accounts.full_url, "https://mail.zoho.uk/api/accounts")
        self.assertEqual(accounts.get_header("Authorization"), "Zoho-oauthtoken access-1")

    def test_a_used_or_old_code_is_said_plainly_and_nothing_is_saved(self):
        self.connect(use=False)
        with self.assertRaisesMessage(mail.MailError, "Codes last only a few minutes and work once"):
            zoho_mail.connect("eu", "client-id", "client-secret", "good-code")
        self.file.unlink()
        self.zoho.codes["new-code"] = "refresh-2"
        with self.assertRaisesMessage(mail.MailError, "Copy it again from the API Console"):
            zoho_mail.connect("eu", "client-id", "wrong-secret", "new-code")
        self.assertFalse(self.file.exists())
        with self.assertRaisesMessage(mail.MailError, "Choose where your Zoho account is"):
            zoho_mail.connect("mars", "client-id", "client-secret", "new-code")

    def test_connecting_again_keeps_the_choice_and_the_from_address(self):
        self.connect()
        zoho_mail.use(True, "hello@ripraptor.example")
        self.zoho.codes["again"] = "refresh-2"
        zoho_mail.connect("eu", "client-id", "client-secret", "again")
        kept = zoho_mail.saved()
        self.assertEqual((kept["use"], kept["from_address"], kept["refresh_token"]),
                         (True, "hello@ripraptor.example", "refresh-2"))

    def test_it_sends_only_from_the_mailboxs_own_addresses(self):
        self.connect()
        with self.assertRaisesMessage(mail.MailError, "Zoho Mail cannot send from old@elsewhere.example"):
            zoho_mail.use(True, "old@elsewhere.example")
        self.assertEqual(zoho_mail.saved()["from_address"], "alerts@ripraptor.example")

    def test_disconnecting_deletes_the_file_and_goes_back_to_zeptomail(self):
        self.connect()
        zoho_mail.forget()
        self.assertFalse(self.file.exists())
        self.assertFalse(zoho_mail.in_use())
        zoho_mail.forget()   # twice is fine
        mail.send("fan@example.com", "Back in stock", "text", "<p>html</p>")
        self.assertEqual(self.zoho.requests[-1].full_url, "https://zeptomail.example/v1.1/email")


class SendTests(ZohoCase):
    def test_every_site_email_goes_through_zoho_mail_once_it_is_chosen(self):
        self.connect()
        mail.send("fan@example.com", "Back in stock", "text", "<p>html</p>", unsubscribe="https://x.example/stop")
        [request] = [request for request in self.zoho.requests if "/messages" in request.full_url]
        self.assertEqual(request.full_url, "https://mail.zoho.eu/api/accounts/2560636000000008002/messages")
        self.assertEqual(request.get_header("Authorization"), "Zoho-oauthtoken access-1")
        self.assertEqual(self.zoho.sent(), [{
            "fromAddress": "alerts@ripraptor.example", "toAddress": "fan@example.com", "subject": "Back in stock",
            "content": "<p>html</p>", "mailFormat": "html", "askReceipt": "no", "encoding": "UTF-8"}])
        self.assertFalse(any("zeptomail" in request.full_url for request in self.zoho.requests))

    def test_email_counts_as_set_up_through_zoho_mail_alone(self):
        with override_settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertFalse(mail.enabled())
            self.connect()
            self.assertTrue(mail.enabled())

    def test_a_plain_text_email_with_a_copy_from_one_of_the_mailboxs_addresses(self):
        self.connect()
        mail.send("shop@example.com", "Hello", "Plain words", "", sender=("hello@ripraptor.example", "Ben"),
                  bcc="ben@ripraptor.example", reply_to="ben@ripraptor.example", tracking=False)
        mail.send("shop@example.com", "Hello", "Plain words", "", sender=("someone@else.example", "Ben"))
        first, second = self.zoho.sent()
        self.assertEqual((first["fromAddress"], first["mailFormat"], first["content"], first["bccAddress"]),
                         ("hello@ripraptor.example", "plaintext", "Plain words", "ben@ripraptor.example"))
        self.assertEqual(second["fromAddress"], "alerts@ripraptor.example")

    def test_the_access_token_is_kept_and_renewed_when_zoho_stops_taking_it(self):
        self.connect()
        mail.send("a@example.com", "One", "text", "")
        mail.send("b@example.com", "Two", "text", "")
        self.assertEqual(self.zoho.refreshes, 0)   # the first token, kept for the hour
        self.zoho.expired = 1
        mail.send("c@example.com", "Three", "text", "")
        self.assertEqual(self.zoho.refreshes, 1)
        self.assertEqual(self.zoho.requests[-1].get_header("Authorization"), "Zoho-oauthtoken access-r1")
        self.assertEqual(len(self.zoho.sent()), 4)

    def test_a_refusal_is_a_mail_error_in_zohos_words(self):
        self.connect()
        self.zoho.refuse = (400, {"status": {"code": 400, "description": "Invalid Input"},
                                  "data": {"moreInfo": "From address not verified"}})
        with self.assertRaisesMessage(mail.MailError, "Zoho Mail refused the email (400): From address not verified"):
            mail.send("fan@example.com", "Back in stock", "text", "")

    def test_back_to_zeptomail_with_one_choice(self):
        self.connect()
        zoho_mail.use(False)
        mail.send("fan@example.com", "Back in stock", "text", "")
        self.assertEqual(self.zoho.requests[-1].full_url, "https://zeptomail.example/v1.1/email")
        self.assertEqual(self.zoho.sent(), [])


class ZeptoMailReviewTests(ZohoCase):
    def test_zeptomails_review_refusal_is_said_plainly(self):
        from inbox import sending

        self.zoho.zepto = (403, {"error": {"code": "TM_4001", "message": "Access Denied", "details": [
            {"code": "SM_128", "message": "Your account is yet to be reviewed", "target": ""}]}})
        with self.assertRaises(mail.MailError) as caught:
            mail.send("fan@example.com", "Back in stock", "text", "")
        self.assertIn("ZeptoMail has not finished reviewing your account", str(caught.exception))
        words = sending.explain(caught.exception)
        self.assertTrue(words.startswith("ZeptoMail has not finished reviewing your account"))
        self.assertIn("send through Zoho Mail: Admin, Email sending", words)
        self.assertNotIn("did not accept the token", words)

    def test_a_shop_email_needs_no_zeptomail_token_while_zoho_mail_carries_it(self):
        from inbox import sending
        from inbox.models import OutreachMail

        OutreachMail.objects.update_or_create(pk=1, defaults={"on": True, "from_address": "alerts@ripraptor.example"})
        with override_settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertIn("ZeptoMail has no token", sending.why_not())
            self.connect()
            self.assertEqual(sending.why_not(), "")
