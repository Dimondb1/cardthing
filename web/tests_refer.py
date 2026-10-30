"""Refer a friend: the keyring offer's pages, emails, rules against abuse, owner controls and tidying."""

import re
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import mail, referrals
from catalogue.models import KeyringClaim, PromoMark, Referral, ReferralSettings, Referrer

from .tests import CopyStyleTests

MAIL_ON = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com",
           "RIPRAPTOR_SITE_URL": "https://ripraptor.com", "RIPRAPTOR_CONTACT_EMAIL": "owner@ripraptor.com"}
URL = re.compile(r"https://ripraptor\.com(/[^\s\"<]+)")
ADDRESS = {"name": "Sam Example", "line1": "12 High Street", "line2": "", "town": "Leeds", "postcode": "ls1 4ap"}


@override_settings(**MAIL_ON)
class ReferTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.row = ReferralSettings.objects.create(pk=1, is_on=True, keyrings_left=10)
        self.sent = []
        patcher = mock.patch("catalogue.mail.send", side_effect=self.capture)
        patcher.start()
        self.addCleanup(patcher.stop)

    def capture(self, to, subject, text, html, opener=None, unsubscribe=""):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html, "unsubscribe": unsubscribe})

    # Helpers -------------------------------------------------------------------------------------

    def post(self, url, data, ip="81.2.69.1", agent="Browser A", **extra):
        return self.client.post(url, data, REMOTE_ADDR=ip, HTTP_USER_AGENT=agent, **extra)

    def get(self, url, ip="81.2.69.1", agent="Browser A"):
        return self.client.get(url, REMOTE_ADDR=ip, HTTP_USER_AGENT=agent)

    def link_in(self, message, start):
        for path in URL.findall(message["text"]):
            if path.startswith(start):
                return path
        self.fail(f"No {start} link in {message['subject']}")

    def sign_up(self, email="ref@example.com", **kw):
        return self.post(reverse("web:refer"), {"email": email, "agree": "1"}, **kw)

    def referrer(self, email="ref@example.com", **kw):
        """A confirmed referrer, as the flow makes one."""
        before = len(self.sent)
        self.sign_up(email, **kw)
        self.assertEqual(len(self.sent), before + 1)
        self.get(self.link_in(self.sent[-1], "/refer/confirm/"))
        return Referrer.objects.get(email_key=referrals.email_key(email))

    def ask_friend(self, referrer, email="friend@example.com", **kw):
        return self.post(reverse("web:refer_friend", args=[referrer.code]), {"email": email, "agree": "1"}, **kw)

    def claim(self, referrer, email="friend@example.com", ip="51.9.9.9", agent="Browser B"):
        before = len(self.sent)
        self.ask_friend(referrer, email, ip=ip, agent=agent)
        self.assertEqual(len(self.sent), before + 1, "no confirmation sent to the friend")
        path = self.link_in(self.sent[-1], "/refer/claim/")
        return self.post(path, {}, ip=ip, agent=agent)

    def referral(self, email="friend@example.com"):
        return Referral.objects.get(friend_key=referrals.email_key(email))


class FlowTests(ReferTestCase):
    def test_the_whole_offer_end_to_end(self):
        # The referrer gives their own address and confirms it.
        response = self.sign_up("Ref@Example.com")
        self.assertRedirects(response, reverse("web:refer") + "?sent=1", fetch_redirect_response=False)
        self.assertContains(self.get(response["Location"]), "Check your inbox")
        self.assertEqual([m["to"] for m in self.sent], ["Ref@Example.com"])
        self.assertEqual(self.sent[0]["subject"], "Confirm your email to get your RipRaptor link")
        referrer = Referrer.objects.get()
        self.assertIsNone(referrer.confirmed_at)
        page = self.get(self.link_in(self.sent[0], "/refer/confirm/"))
        referrer.refresh_from_db()
        self.assertIsNotNone(referrer.confirmed_at)
        share = f"https://ripraptor.com/r/{referrer.code}/"
        self.assertContains(page, share)
        self.assertContains(page, "https://wa.me/?text=")
        self.assertContains(page, 'href="sms:?&amp;body=')
        self.assertContains(page, 'href="mailto:?subject=')
        self.assertContains(page, "Copy link")
        self.assertEqual(self.sent[1]["subject"], "Your RipRaptor link to share")
        self.assertIn(share, self.sent[1]["text"])

        # The friend follows the link and gives their own address.
        landing = self.get(f"/r/{referrer.code}/", ip="51.9.9.9", agent="Browser B")
        self.assertContains(landing, "A free RipRaptor keyring for you")
        self.ask_friend(referrer, ip="51.9.9.9", agent="Browser B")
        self.assertEqual(self.sent[2]["to"], "friend@example.com")
        claim_path = self.link_in(self.sent[2], "/refer/claim/")
        self.assertEqual(self.referral().status, Referral.Status.WAITING)

        # Opening the link (as a mail scanner would) completes nothing; pressing the button does.
        self.assertContains(self.get(claim_path, ip="51.9.9.9", agent="Browser B"), "Claim my keyring")
        self.assertEqual(self.referral().status, Referral.Status.WAITING)
        done = self.post(claim_path, {}, ip="51.9.9.9", agent="Browser B")
        self.assertContains(done, "We will email you both")
        referral = self.referral()
        self.assertEqual(referral.status, Referral.Status.COMPLETE)
        self.row.refresh_from_db()
        self.assertEqual(self.row.keyrings_left, 8)

        # The owner is told without either address in the subject, then both are asked for an address.
        owner = [m for m in self.sent if m["to"] == "owner@ripraptor.com"]
        self.assertEqual(len(owner), 1)
        self.assertEqual(owner[0]["subject"], "Referral complete")
        self.assertNotIn("example.com", owner[0]["text"] + owner[0]["html"])
        asks = [m for m in self.sent if m["subject"] == "Where should we send your RipRaptor keyring?"]
        self.assertEqual(sorted(m["to"] for m in asks), ["Ref@Example.com", "friend@example.com"])

        # Each sends a UK address; the owner sees them to post.
        for message, line1 in zip(asks, ("12 High Street", "3 Mill Lane")):
            path = self.link_in(message, "/refer/address/")
            self.assertContains(self.get(path), "Where should we send your keyring?")
            response = self.post(path, {**ADDRESS, "line1": line1})
            self.assertContains(response, "We have your address")
            # The link is one-time: it never shows the address again or takes another.
            again = self.post(path, {**ADDRESS, "line1": "99 Other Road"})
            self.assertNotContains(again, line1)
        claims = KeyringClaim.objects.filter(submitted_at__isnull=False)
        self.assertEqual(sorted(claims.values_list("line1", flat=True)), ["12 High Street", "3 Mill Lane"])
        self.assertEqual(set(claims.values_list("postcode", flat=True)), {"LS1 4AP"})

        admin = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(admin)
        listing = self.client.get(reverse("admin:catalogue_keyringclaim_changelist") + "?state=to_post")
        self.assertContains(listing, "3 Mill Lane")
        self.client.post(reverse("admin:catalogue_keyringclaim_changelist"),
                         {"action": "mark_posted", "_selected_action": list(claims.values_list("pk", flat=True))})
        self.assertEqual(KeyringClaim.objects.filter(posted_at__isnull=False).count(), 2)

    def test_a_link_works_only_once_its_owner_has_confirmed(self):
        self.sign_up()
        referrer = Referrer.objects.get()
        self.assertEqual(self.get(f"/r/{referrer.code}/").status_code, 404)

    def test_the_friend_hears_nothing_more_until_they_confirm(self):
        referrer = self.referrer()
        before = len(self.sent)
        self.ask_friend(referrer, ip="51.9.9.9", agent="Browser B")
        self.assertEqual(len(self.sent), before + 1)
        self.assertFalse(KeyringClaim.objects.exists())
        self.assertFalse(PromoMark.objects.exists())

    def test_a_confirmed_referrer_asking_again_gets_their_link_again(self):
        referrer = self.referrer()
        Referrer.objects.filter(pk=referrer.pk).update(confirm_sent_at=timezone.now() - timedelta(hours=1))
        self.sign_up()
        self.assertEqual(self.sent[-1]["subject"], "Your RipRaptor link to share")

    def test_emails_carry_a_delete_link_that_needs_a_button(self):
        self.sign_up()
        stop = self.link_in(self.sent[0], "/refer/stop/")
        self.assertEqual(self.sent[0]["unsubscribe"], "https://ripraptor.com" + stop)
        self.assertContains(self.get(stop), "Delete my details")
        self.assertTrue(Referrer.objects.exists())   # opening the link deletes nothing
        self.assertContains(self.post(stop, {}), "Your details are deleted")
        self.assertFalse(Referrer.objects.exists())

    def test_deleting_after_completion_gives_the_keyring_back_and_keeps_the_fingerprint(self):
        referrer = self.referrer()
        self.claim(referrer)
        ask = [m for m in self.sent if m["to"] == "friend@example.com" and "address" in m["text"]][-1]
        self.post(self.link_in(ask, "/refer/stop/"), {})
        self.assertFalse(KeyringClaim.objects.filter(role=KeyringClaim.Role.FRIEND).exists())
        self.assertEqual(self.referral().friend_email, "")
        self.row.refresh_from_db()
        self.assertEqual(self.row.keyrings_left, 9)
        self.assertTrue(PromoMark.objects.filter(digest=referrals.email_key("friend@example.com")).exists())


class AbuseRuleTests(ReferTestCase):
    def test_email_addresses_are_compared_as_people(self):
        same = ["a.b+x@gmail.com", "AB@googlemail.com", "ab@gmail.com"]
        self.assertEqual({referrals.normalise_email(e) for e in same}, {"ab@gmail.com"})
        self.assertEqual(referrals.normalise_email("first.last+shop@example.com"), "first.last@example.com")
        self.assertNotEqual(referrals.email_key("a.b@example.com"), referrals.email_key("ab@example.com"))

    def test_a_referrer_cannot_refer_themselves(self):
        referrer = self.referrer("sam.smith@gmail.com")
        before = len(self.sent)
        response = self.ask_friend(referrer, "samsmith+keyring@gmail.com", ip="51.9.9.9")
        self.assertEqual(response.status_code, 302)   # the same answer as any address
        self.assertEqual(len(self.sent), before)
        self.assertFalse(Referral.objects.exists())

    def test_a_friend_must_be_new(self):
        first = self.referrer("one@example.com")
        second = self.referrer("two@example.com", ip="51.1.1.1")
        before = len(self.sent)
        self.ask_friend(first, "two@example.com", ip="51.9.9.9")
        self.assertEqual(len(self.sent), before)
        # A friend who has had a keyring through one link cannot claim through another.
        self.claim(first, "new@example.com")
        before = len(self.sent)
        self.ask_friend(second, "new@example.com", ip="51.9.9.10")
        self.assertEqual(len(self.sent), before)

    def test_one_keyring_per_email_address_ever_even_after_details_are_deleted(self):
        referrer = self.referrer()
        self.claim(referrer)
        Referral.objects.all().delete()
        Referrer.objects.all().delete()
        again = self.referrer("other@example.com", ip="51.2.2.2")
        before = len(self.sent)
        self.ask_friend(again, "friend+again@example.com", ip="51.9.9.9")
        self.assertEqual(len(self.sent), before)
        # Nor can either take part as a referrer.
        before = len(self.sent)
        self.sign_up("ref@example.com", ip="51.3.3.3")
        self.assertEqual(len(self.sent), before)

    def test_each_referrer_earns_up_to_the_set_number(self):
        referrer = self.referrer()
        self.claim(referrer, "f1@example.com", ip="51.9.9.1")
        self.assertContains(self.get(f"/r/{referrer.code}/", ip="51.9.9.2"), "This link has already been used.")
        before = len(self.sent)
        self.ask_friend(referrer, "f2@example.com", ip="51.9.9.2")
        self.assertEqual(len(self.sent), before)
        ReferralSettings.objects.filter(pk=1).update(max_per_referrer=2)
        self.claim(referrer, "f2@example.com", ip="51.9.9.2")
        self.assertEqual(Referral.objects.filter(status=Referral.Status.COMPLETE).count(), 2)

    def test_the_same_connection_as_the_referrer_is_held_for_review(self):
        referrer = self.referrer()
        self.claim(referrer, ip="81.2.69.1", agent="Browser A")
        referral = self.referral()
        self.assertEqual(referral.status, Referral.Status.REVIEW)
        self.assertIn("Same browser", referral.review_reason)
        self.assertFalse([m for m in self.sent if "address" in m["subject"].lower() or "send your" in m["subject"]])
        owner = [m for m in self.sent if m["to"] == "owner@ripraptor.com"]
        self.assertEqual(owner[0]["subject"], "Referral for review")
        self.assertIsNone(referral.emails_sent_at)

    def test_the_same_internet_address_with_another_browser_is_held_too(self):
        referrer = self.referrer()
        self.claim(referrer, ip="81.2.69.1", agent="Other")
        self.assertEqual(self.referral().status, Referral.Status.REVIEW)

    def test_an_ipv6_block_counts_as_one_address(self):
        referrer = self.referrer(ip="2001:db8:1:1::5")
        self.claim(referrer, ip="2001:db8:1:1::abcd", agent="Other")
        self.assertEqual(self.referral().status, Referral.Status.REVIEW)

    def test_a_second_claim_from_one_connection_is_held(self):
        one = self.referrer("one@example.com", ip="51.1.1.1")
        two = self.referrer("two@example.com", ip="51.2.2.2")
        self.claim(one, "f1@example.com", ip="51.9.9.9")
        self.claim(two, "f2@example.com", ip="51.9.9.9", agent="Other")
        self.assertEqual(self.referral("f1@example.com").status, Referral.Status.COMPLETE)
        held = self.referral("f2@example.com")
        self.assertEqual(held.status, Referral.Status.REVIEW)
        self.assertIn("other claim", held.review_reason)

    def test_the_owner_approves_or_cancels_a_held_referral(self):
        admin = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        referrer = self.referrer()
        self.claim(referrer, ip="81.2.69.1", agent="Browser A")
        held = self.referral()
        self.client.force_login(admin)
        before = len(self.sent)
        self.client.post(reverse("admin:catalogue_referral_changelist"),
                         {"action": "approve", "_selected_action": [held.pk]})
        held.refresh_from_db()
        self.assertEqual(held.status, Referral.Status.COMPLETE)
        self.assertEqual(len(self.sent), before + 2)

        other = self.referrer("two@example.com", ip="51.4.4.4")
        self.claim(other, "f2@example.com", ip="51.4.4.4", agent="Browser B")
        second = self.referral("f2@example.com")
        self.assertEqual(second.status, Referral.Status.REVIEW)
        before = len(self.sent)
        self.row.refresh_from_db()
        left = self.row.keyrings_left
        self.client.post(reverse("admin:catalogue_referral_changelist"),
                         {"action": "cancel", "_selected_action": [second.pk]})
        second.refresh_from_db()
        self.assertEqual(second.status, Referral.Status.CANCELLED)
        self.assertEqual(len(self.sent), before)
        self.row.refresh_from_db()
        self.assertEqual(self.row.keyrings_left, left + 2)

    def test_a_cancelled_referral_stops_its_address_links(self):
        referrer = self.referrer()
        self.claim(referrer)
        ask = [m for m in self.sent if "/refer/address/" in m["text"]][0]
        referrals.cancel(self.referral())
        self.assertEqual(self.get(self.link_in(ask, "/refer/address/")).status_code, 404)

    def test_one_keyring_per_postal_address(self):
        referrer = self.referrer()
        self.claim(referrer)
        asks = [self.link_in(m, "/refer/address/") for m in self.sent if "/refer/address/" in m["text"]]
        self.post(asks[0], ADDRESS)
        response = self.post(asks[1], {**ADDRESS, "line1": "12, High St.", "postcode": "LS14AP"})
        self.assertContains(response, "This address has already had a keyring", status_code=400)
        self.assertEqual(KeyringClaim.objects.filter(submitted_at__isnull=False).count(), 1)


class AddressTests(ReferTestCase):
    def setUp(self):
        super().setUp()
        referrer = self.referrer()
        self.claim(referrer)
        self.path = self.link_in([m for m in self.sent if "/refer/address/" in m["text"]][0], "/refer/address/")

    def test_uk_postcodes_only(self):
        for good, shown in (("sw1a1aa", "SW1A 1AA"), ("BT1 1AA", "BT1 1AA"), ("  m1   1ae ", "M1 1AE")):
            self.assertEqual(referrals.clean_postcode(good), shown)
        for bad in ("JE2 3AB", "GY1 1AA", "IM1 1AA", "12345", "75008", "SW1A", "Q1 1AA", ""):
            self.assertIsNone(referrals.clean_postcode(bad), bad)
        response = self.post(self.path, {**ADDRESS, "postcode": "JE2 3AB"})
        self.assertContains(response, "We can only post to UK addresses", status_code=400)

    def test_missing_fields_are_refused(self):
        response = self.post(self.path, {**ADDRESS, "town": " "})
        self.assertContains(response, "Fill in your name", status_code=400)
        self.assertFalse(KeyringClaim.objects.filter(submitted_at__isnull=False).exists())

    def test_extra_fields_are_ignored_and_long_ones_cut(self):
        self.post(self.path, {**ADDRESS, "name": "N" * 500, "email": "x@example.com", "phone": "0700"})
        claim = KeyringClaim.objects.get(submitted_at__isnull=False)
        self.assertEqual(len(claim.name), 80)

    def test_the_link_expires_after_30_days(self):
        KeyringClaim.objects.update(created_at=timezone.now() - timedelta(days=31))
        self.assertContains(self.get(self.path), "Address links last 30 days")
        self.post(self.path, ADDRESS)
        self.assertFalse(KeyringClaim.objects.filter(submitted_at__isnull=False).exists())

    def test_the_address_form_is_escaped(self):
        response = self.post(self.path, {**ADDRESS, "postcode": "<script>x</script>"})
        self.assertNotContains(response, "<script>x</script>", status_code=400)
        self.assertContains(response, "&lt;script&gt;", status_code=400)


class OwnerControlTests(ReferTestCase):
    def assert_ended(self, response):
        self.assertContains(response, "This offer has ended.")
        self.assertNotContains(response, "<form class=\"contact-form")

    def test_switched_off_everything_stops(self):
        referrer = self.referrer()
        self.ask_friend(referrer, ip="51.9.9.9")
        claim_path = self.link_in(self.sent[-1], "/refer/claim/")
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        self.assert_ended(self.get(reverse("web:refer")))
        before = len(self.sent)
        self.sign_up("new@example.com")
        self.assertEqual(len(self.sent), before)
        self.assertEqual(Referrer.objects.count(), 1)
        self.assert_ended(self.get(f"/r/{referrer.code}/"))
        self.assert_ended(self.post(claim_path, {}))
        self.assertEqual(self.referral().status, Referral.Status.WAITING)

    def test_switched_off_stops_address_links_too(self):
        self.claim(self.referrer())
        path = self.link_in([m for m in self.sent if "/refer/address/" in m["text"]][0], "/refer/address/")
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        self.assert_ended(self.get(path))
        self.post(path, ADDRESS)
        self.assertFalse(KeyringClaim.objects.filter(submitted_at__isnull=False).exists())

    def test_each_completed_referral_reserves_two_and_the_last_ones_stop_new_people(self):
        ReferralSettings.objects.filter(pk=1).update(keyrings_left=3)
        referrer = self.referrer()
        self.claim(referrer)
        self.row.refresh_from_db()
        self.assertEqual(self.row.keyrings_left, 1)
        self.assert_ended(self.get(reverse("web:refer")))
        self.assertNotContains(self.get(reverse("web:home")), 'href="/refer/"')
        # Those already promised one can still send their address.
        path = self.link_in([m for m in self.sent if "/refer/address/" in m["text"]][0], "/refer/address/")
        self.assertContains(self.post(path, ADDRESS), "We have your address")

    def test_two_friends_racing_for_the_last_two_keyrings(self):
        ReferralSettings.objects.filter(pk=1).update(keyrings_left=2, max_per_referrer=5)
        referrer = self.referrer()
        paths = []
        for n in (1, 2):
            self.ask_friend(referrer, f"f{n}@example.com", ip=f"51.9.9.{n}")
            paths.append(self.link_in(self.sent[-1], "/refer/claim/"))
        self.assertContains(self.post(paths[0], {}, ip="51.9.9.1"), "We will email you both")
        self.assert_ended(self.post(paths[1], {}, ip="51.9.9.2"))
        self.assertEqual(Referral.objects.filter(status=Referral.Status.WAITING).count(), 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.keyrings_left, 0)

    def test_after_the_last_day_nobody_new_joins(self):
        referrer = self.referrer()
        ReferralSettings.objects.filter(pk=1).update(ends_on=timezone.localdate() - timedelta(days=1))
        self.assert_ended(self.get(reverse("web:refer")))
        self.assert_ended(self.get(f"/r/{referrer.code}/"))
        ReferralSettings.objects.filter(pk=1).update(ends_on=timezone.localdate())
        self.assertContains(self.get(reverse("web:refer")), "Email me my link")

    def test_without_email_set_up_the_offer_is_closed(self):
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assert_ended(self.get(reverse("web:refer")))

    def test_the_settings_page_is_one_row_and_saving_it_updates_the_footer(self):
        admin = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(admin)
        response = self.client.get(reverse("admin:catalogue_referralsettings_changelist"))
        self.assertRedirects(response, reverse("admin:catalogue_referralsettings_change", args=[1]))
        self.client.post(reverse("admin:catalogue_referralsettings_change", args=[1]),
                         {"keyrings_left": "40", "max_per_referrer": "1", "ends_on": ""})
        self.row.refresh_from_db()
        self.assertFalse(self.row.is_on)
        self.client.logout()
        self.assertNotContains(self.get(reverse("web:home")), 'href="/refer/"')


class LinkTests(ReferTestCase):
    def test_footer_and_watchlist_link_only_while_open(self):
        referrals.refresh_flag()
        for url in (reverse("web:home"), reverse("web:watchlist")):
            self.assertContains(self.get(url), "Refer a friend, get a free keyring")
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        referrals.refresh_flag()
        for url in (reverse("web:home"), reverse("web:watchlist")):
            self.assertNotContains(self.get(url), "Refer a friend, get a free keyring")

    def test_an_emptied_cache_hides_the_link_until_the_refer_page_or_tidy_runs(self):
        cache.clear()
        self.assertNotContains(self.get(reverse("web:home")), 'href="/refer/"')
        call_command("tidy_referrals", stdout=StringIO())
        self.assertContains(self.get(reverse("web:home")), 'href="/refer/"')

    def test_the_footer_link_adds_no_query(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.get(reverse("web:about"))
            return len(queries)

        count()   # a visitor's first page of the day writes their row; later ones only add to it
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        referrals.refresh_flag()
        off = count()
        ReferralSettings.objects.filter(pk=1).update(is_on=True)
        referrals.refresh_flag()
        self.assertEqual(count(), off)

    def test_terms_carry_the_offer_rules_and_data_use(self):
        html = self.get(reverse("web:terms")).content.decode()
        self.assertIn('id="terms-refer"', html)
        for words in ("UK residents only", "Under 18s need a parent or guardian", "whilst stocks last",
                      "new to RipRaptor offers", "change or cancel the offer at any time",
                      "deleted 60 days after your keyring is posted", "Unconfirmed sign-ups are deleted after 14 days"):
            self.assertIn(words, html)
        self.assertNotIn("delivery time.", html.replace("cannot promise a delivery time.", ""))
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        self.assertNotIn('id="terms-refer"', self.get(reverse("web:terms")).content.decode())


class SecurityTests(ReferTestCase):
    def test_csrf_is_needed_on_every_form(self):
        strict = Client(enforce_csrf_checks=True)
        referrer = self.referrer()
        self.ask_friend(referrer, ip="51.9.9.9")
        claim_path = self.link_in(self.sent[-1], "/refer/claim/")
        for url in (reverse("web:refer"), f"/r/{referrer.code}/", claim_path,
                    self.link_in(self.sent[-1], "/refer/stop/")):
            with self.subTest(url=url):
                self.assertEqual(strict.post(url, {"email": "x@example.com", "agree": "1"}).status_code, 403)
        self.assertEqual(self.referral().status, Referral.Status.WAITING)

    def test_the_hidden_field_stops_robots(self):
        self.post(reverse("web:refer"), {"email": "bot@example.com", "agree": "1", "website": "spam"})
        self.assertFalse(Referrer.objects.exists())
        self.assertEqual(self.sent, [])

    def test_the_terms_must_be_ticked(self):
        response = self.post(reverse("web:refer"), {"email": "x@example.com"})
        self.assertContains(response, "Tick the box")
        self.assertFalse(Referrer.objects.exists())

    def test_a_bad_address_is_refused(self):
        response = self.post(reverse("web:refer"), {"email": "not-an-address", "agree": "1"})
        self.assertContains(response, "Enter a full email address")

    def test_no_enumeration(self):
        """A stranger typing an address sees the same answer whether it is new, waiting, confirmed or used."""
        referrer = self.referrer("known@example.com")
        self.claim(referrer, "used@example.com")
        answers = set()
        for n, email in enumerate(("new@example.com", "known@example.com", "used@example.com", "new@example.com")):
            response = self.sign_up(email, ip=f"51.7.7.{n}")
            answers.add((response.status_code, response["Location"]))
        self.assertEqual(answers, {(302, reverse("web:refer") + "?sent=1")})
        other = self.referrer("other@example.com", ip="51.8.8.8")
        answers = set()
        for n, email in enumerate(("brand@example.com", "known@example.com", "used@example.com", "other@example.com")):
            response = self.ask_friend(other, email, ip=f"51.6.6.{n}")
            answers.add((response.status_code, response["Location"]))
        self.assertEqual(answers, {(302, f"/r/{other.code}/?sent=1")})

    def test_too_many_posts_from_one_connection(self):
        for n in range(referrals.ASKS_PER_NETWORK_HOUR):
            self.sign_up(f"p{n}@example.com")
        before = len(self.sent)
        response = self.sign_up("one-more@example.com")
        self.assertContains(response, "Too many requests", status_code=429)
        self.assertEqual(len(self.sent), before)
        self.assertFalse(Referrer.objects.filter(email="one-more@example.com").exists())

    def test_at_most_three_confirmations_a_day_to_one_person(self):
        for n in range(6):
            Referrer.objects.update(confirm_sent_at=timezone.now() - timedelta(hours=1))
            self.sign_up(f"victim+{n}@example.com", ip=f"51.5.5.{n}")
        self.assertEqual(len(self.sent), referrals.CONFIRMS_PER_EMAIL_DAY)

    def test_asking_twice_quickly_sends_once(self):
        self.sign_up()
        self.sign_up(ip="51.5.5.5")
        self.assertEqual(len(self.sent), 1)

    def test_the_site_sends_at_most_so_many_confirmations_an_hour(self):
        with mock.patch.object(referrals, "MAILS_PER_HOUR", 2):
            for n in range(4):
                self.sign_up(f"p{n}@example.com", ip=f"51.4.4.{n}")
        self.assertEqual([m["to"] for m in self.sent if m["to"] != "owner@ripraptor.com"],
                         ["p0@example.com", "p1@example.com"])
        # Real sign-ups are dropped too once it trips, so the owner is told, once in the hour.
        owner = [m for m in self.sent if m["to"] == "owner@ripraptor.com"]
        self.assertEqual([m["subject"] for m in owner], [referrals.OWNER_CAP_SUBJECT])

    def test_wrong_codes_and_tokens_are_404_then_429_without_reading_the_database(self):
        for n in range(referrals.MISSES_PER_NETWORK_HOUR):
            url = (f"/r/nosuchcode{n}/", f"/refer/confirm/{'x' * 32}/", f"/refer/claim/{'y' * 32}/",
                   f"/refer/address/{'z' * 32}/")[n % 4]
            self.assertEqual(self.get(url).status_code, 404, url)
        with CaptureQueriesContext(connection) as queries:
            response = self.get("/r/anothercode/")
        self.assertEqual(response.status_code, 429)
        self.assertFalse([q for q in queries if "referr" in q["sql"].lower() or "keyring" in q["sql"].lower()])
        self.assertEqual(self.get("/r/anothercode/", ip="51.1.2.3").status_code, 404)   # another connection is fine

    def test_codes_and_tokens_are_random(self):
        a, b = self.referrer("a@example.com"), self.referrer("b@example.com", ip="51.1.1.1")
        self.assertNotEqual(a.code, b.code)
        for value in (a.code, b.code):
            self.assertRegex(value, r"^[A-Za-z0-9_-]{11}$")
            self.assertFalse(value.isdigit())
        self.assertGreaterEqual(len(a.token), 32)

    def test_large_bodies_are_refused_before_django_reads_them(self):
        response = self.client.post(reverse("web:refer"), {"email": "x@example.com", "agree": "1", "pad": "x" * 5000})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(Referrer.objects.exists())

    def test_only_get_and_post(self):
        self.assertEqual(self.client.put(reverse("web:refer")).status_code, 405)

    def test_emails_hold_no_visitor_input_and_no_open_redirects(self):
        self.sign_up('"<b>x</b>"@example.com')
        for message in self.sent:
            self.assertNotIn("<b>x</b>", message["html"])
        response = self.post(reverse("web:refer") + "?next=https://evil.example/", {"email": "a@example.com", "agree": "1"})
        self.assertEqual(response["Location"], reverse("web:refer") + "?sent=1")

    def test_private_pages_are_not_indexed_and_carry_no_third_party_script(self):
        robots = self.client.get("/robots.txt").content.decode()
        self.assertIn("Disallow: /refer/", robots)
        self.assertIn("Disallow: /r/", robots)
        referrer = self.referrer()
        self.ask_friend(referrer, ip="51.9.9.9")
        urls = [reverse("web:refer"), self.link_in(self.sent[0], "/refer/confirm/"), f"/r/{referrer.code}/",
                self.link_in(self.sent[-1], "/refer/claim/"), self.link_in(self.sent[-1], "/refer/stop/")]
        with self.settings(RIPRAPTOR_GA_ID="G-TEST123", RIPRAPTOR_AWIN_PUBLISHER_ID="1"):
            for url in urls:
                with self.subTest(url=url):
                    response = self.get(url, ip="51.9.9.9")
                    html = response.content.decode()
                    self.assertIn('<meta name="robots" content="noindex', html)
                    self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
                    self.assertEqual(response["Cache-Control"], "no-store")
                    if url != reverse("web:refer"):
                        self.assertNotIn("analytics.js", html)
                        self.assertNotIn("dwin2.com", html)


class TidyTests(ReferTestCase):
    def run_tidy(self, days):
        with mock.patch("catalogue.referrals.timezone.now", return_value=timezone.now() + timedelta(days=days)):
            call_command("tidy_referrals", stdout=StringIO())

    def test_unconfirmed_sign_ups_go_after_14_days(self):
        self.sign_up()
        referrer = self.referrer("ok@example.com", ip="51.1.1.1")
        self.ask_friend(referrer, ip="51.9.9.9")
        self.run_tidy(13)
        self.assertEqual(Referrer.objects.count(), 2)
        self.run_tidy(15)
        self.assertEqual(list(Referrer.objects.values_list("email", flat=True)), ["ok@example.com"])
        self.assertFalse(Referral.objects.exists())

    def test_an_unused_link_goes_after_90_days(self):
        self.referrer()
        self.run_tidy(89)
        self.assertTrue(Referrer.objects.exists())
        self.run_tidy(91)
        self.assertFalse(Referrer.objects.exists())

    def test_addresses_and_emails_go_60_days_after_posting(self):
        self.claim(self.referrer())
        for message in [m for m in self.sent if "/refer/address/" in m["text"]]:
            self.post(self.link_in(message, "/refer/address/"), {**ADDRESS, "line1": f"{len(message['to'])} Road"})
        referrals.mark_posted(KeyringClaim.objects.all())
        self.run_tidy(59)
        self.assertEqual(KeyringClaim.objects.count(), 2)
        self.run_tidy(61)
        self.assertFalse(KeyringClaim.objects.exists())
        self.assertFalse(Referral.objects.exists())
        self.assertFalse(Referrer.objects.exists())
        self.assertEqual(PromoMark.objects.filter(kind=PromoMark.Kind.EMAIL).count(), 2)
        self.assertEqual(PromoMark.objects.filter(kind=PromoMark.Kind.ADDRESS).count(), 2)

    def test_unused_address_links_go_14_days_after_they_expire(self):
        self.claim(self.referrer())
        self.run_tidy(43)
        self.assertEqual(KeyringClaim.objects.count(), 2)
        self.run_tidy(45)
        self.assertFalse(KeyringClaim.objects.exists())
        self.assertFalse(Referral.objects.exists())

    def test_cancelled_referrals_go_after_14_days(self):
        self.claim(self.referrer())
        referrals.cancel(self.referral())
        self.run_tidy(15)
        self.assertFalse(Referral.objects.exists())

    def test_a_held_referral_not_approved_in_30_days_is_cancelled(self):
        self.claim(self.referrer(), ip="81.2.69.1", agent="Browser A")
        self.run_tidy(31)
        self.assertEqual(self.referral().status, Referral.Status.CANCELLED)
        self.row.refresh_from_db()
        self.assertEqual(self.row.keyrings_left, 10)

    def test_address_emails_that_failed_are_sent_by_the_next_tidy(self):
        referrer = self.referrer()
        self.ask_friend(referrer, ip="51.9.9.9")
        claim_path = self.link_in(self.sent[-1], "/refer/claim/")
        with mock.patch("catalogue.mail.send", side_effect=mail.MailError("down")):
            self.post(claim_path, {}, ip="51.9.9.9")
        referral = self.referral()
        self.assertEqual(referral.status, Referral.Status.COMPLETE)
        self.assertIsNone(referral.emails_sent_at)
        before = len(self.sent)
        call_command("tidy_referrals", stdout=StringIO())
        self.assertEqual(len(self.sent), before + 2)
        call_command("tidy_referrals", stdout=StringIO())
        self.assertEqual(len(self.sent), before + 2)


class WinKeyringSectionTests(ReferTestCase):
    """The owner, 30 Oct 2026: a home page section to win a keyring, naming the first winner, with the keyrings left
    and the photo of the real keyring on the page it leads to."""

    def home(self):
        return self.get(reverse("web:home")).content.decode()

    def test_the_home_page_shows_it_with_the_keyrings_left_while_the_offer_is_open(self):
        referrals.refresh_flag()
        html = self.home()
        section = html[html.index('aria-labelledby="keyring-title"'):html.index("</section>", html.index("keyring-title"))]
        self.assertIn("Win a free RipRaptor keyring today", section)
        self.assertIn("Matt from Portsmouth has already won one.", section)
        self.assertIn("Another 10 keyrings left to win.", section)
        self.assertIn("you both get one by post. UK only, whilst stocks last.", section)
        self.assertIn('<a class="button" href="/refer/">Get my link', section)
        self.assertIn('img/keyring-photo.webp', section)
        self.assertIn('loading="lazy"', section)
        self.assertNotIn("!", re.sub(r"<[^>]*>", " ", section))

    def test_the_count_follows_the_keyrings_given(self):
        referrer = self.referrer()
        self.claim(referrer)
        self.assertIn("Another 8 keyrings left to win.", self.home())

    def test_it_goes_when_the_offer_closes_or_runs_out(self):
        ReferralSettings.objects.filter(pk=1).update(keyrings_left=1)
        referrals.refresh_flag()
        self.assertNotIn("keyring-title", self.home())
        ReferralSettings.objects.filter(pk=1).update(keyrings_left=9, is_on=False)
        referrals.refresh_flag()
        self.assertNotIn("keyring-title", self.home())

    def test_a_flag_written_before_the_count_was_kept_shows_nothing(self):
        cache.set(referrals.FLAG_KEY, {"on": True, "stock": True, "ends": ""}, None)
        self.assertIsNone(referrals.keyrings_left())
        self.assertNotIn("keyring-title", self.home())

    def test_the_winner_line_can_be_emptied_in_admin(self):
        from content.models import SiteContent

        referrals.refresh_flag()
        SiteContent.objects.filter(key="home.keyring.winner").update(content="")
        cache.clear()
        referrals.refresh_flag()
        html = self.home()
        self.assertIn("Another 10 keyrings left to win.", html)
        self.assertNotIn("keyring-offer__winner", html)

    def test_it_adds_no_query_to_the_home_page(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.get(reverse("web:home"))
            return len(queries)

        count()
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        referrals.refresh_flag()
        off = count()
        ReferralSettings.objects.filter(pk=1).update(is_on=True)
        referrals.refresh_flag()
        self.assertEqual(count(), off)

    def test_the_refer_pages_show_the_photo(self):
        html = self.get(reverse("web:refer")).content.decode()
        self.assertIn('class="refer-layout__art refer-layout__art--photo"', html)
        self.assertIn('img/keyring-photo.jpg" alt="The RipRaptor keyring held in a hand', html)
        self.assertIn('width="900" height="600"', html)

    def test_the_photo_carries_no_location_or_camera_details(self):
        # The phone's original had GPS in it: the copies on the site are re-encoded without any EXIF.
        from django.contrib.staticfiles import finders
        from PIL import Image

        for name in ("img/keyring-photo.webp", "img/keyring-photo.jpg"):
            with Image.open(finders.find(name)) as image:
                self.assertEqual(image.size, (900, 600))
                self.assertEqual(len(image.getexif()), 0, name)
                self.assertNotIn("exif", image.info, name)


class QueryCountTests(ReferTestCase):
    def test_pages_take_a_fixed_number_of_queries(self):
        referrer = self.referrer()
        for n in range(5):
            Referrer.objects.create(email=f"x{n}@example.com", email_key=f"k{n}", confirmed_at=timezone.now())
        with self.assertNumQueries(2):   # settings, wording
            self.get(reverse("web:refer"))
        with self.assertNumQueries(4):   # settings, referrer, keyrings it has earned, wording
            self.get(f"/r/{referrer.code}/", ip="51.9.9.9")


class CopyTests(ReferTestCase):
    def test_every_refer_page_and_email_keeps_the_house_style(self):
        referrer = self.referrer()
        self.claim(referrer)
        addresses = [self.link_in(m, "/refer/address/") for m in self.sent if "/refer/address/" in m["text"]]
        other = self.referrer("other@example.com", ip="51.3.3.3")
        self.ask_friend(other, "pal@example.com", ip="51.4.4.4")
        urls = [reverse("web:refer"), reverse("web:refer") + "?sent=1", self.link_in(self.sent[0], "/refer/confirm/"),
                f"/r/{other.code}/", f"/r/{referrer.code}/", self.link_in(self.sent[-1], "/refer/claim/"),
                self.link_in(self.sent[-1], "/refer/stop/"), addresses[0], "/r/nosuchcode/", reverse("web:terms")]
        for url in urls:
            html = self.get(url, ip="51.4.4.4").content.decode()
            visible = re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html))
            with self.subTest(url=url):
                self.assertNotIn("!", visible)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())
        for message in self.sent:
            with self.subTest(subject=message["subject"]):
                self.assertNotIn("!", message["subject"] + message["text"])
                self.assertNotIn("—", message["html"] + message["text"])


class StockTests(ReferTestCase):
    """Keyrings promised but never posted always go back to Keyrings left, whichever way their claim goes."""

    def left(self):
        self.row.refresh_from_db()
        return self.row.keyrings_left

    def asks(self):
        return [self.link_in(m, "/refer/address/") for m in self.sent if "/refer/address/" in m["text"]]

    def run_tidy(self, days):
        with mock.patch("catalogue.referrals.timezone.now", return_value=timezone.now() + timedelta(days=days)):
            call_command("tidy_referrals", stdout=StringIO())

    def test_unused_address_links_give_their_keyrings_back(self):
        self.claim(self.referrer())
        self.assertEqual(self.left(), 8)
        self.run_tidy(45)
        self.assertFalse(KeyringClaim.objects.exists())
        self.assertEqual(self.left(), 10)

    def test_one_address_sent_and_one_link_unused_gives_one_back(self):
        self.claim(self.referrer())
        self.post(self.asks()[0], ADDRESS)
        self.run_tidy(45)
        self.assertEqual(KeyringClaim.objects.count(), 1)
        self.assertEqual(self.left(), 9)

    def test_an_address_never_marked_posted_goes_after_90_days_and_its_keyring_comes_back(self):
        self.claim(self.referrer())
        for path, line1 in zip(self.asks(), ("12 High Street", "3 Mill Lane")):
            self.post(path, {**ADDRESS, "line1": line1})
        self.run_tidy(89)
        self.assertEqual(KeyringClaim.objects.filter(submitted_at__isnull=False).count(), 2)
        self.run_tidy(91)
        self.assertFalse(KeyringClaim.objects.exists())
        self.assertFalse(Referral.objects.exists())
        self.assertFalse(Referrer.objects.exists())
        self.assertEqual(self.left(), 10)
        # The fingerprints stay, so neither address can take part again.
        self.assertEqual(PromoMark.objects.filter(kind=PromoMark.Kind.ADDRESS).count(), 2)

    def test_the_owner_is_reminded_once_a_day_of_addresses_waiting_to_be_posted(self):
        self.claim(self.referrer())
        self.post(self.asks()[0], ADDRESS)
        before = len(self.sent)
        self.run_tidy(13)
        self.assertEqual(len(self.sent), before)
        self.run_tidy(15)
        self.run_tidy(15)
        reminders = [m for m in self.sent[before:] if m["subject"] == referrals.OWNER_REMIND_SUBJECT]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["to"], "owner@ripraptor.com")
        self.assertIn("1 keyring address has waited", reminders[0]["text"])
        self.assertNotIn("Sam Example", reminders[0]["text"] + reminders[0]["html"])

    def test_deleting_in_admin_gives_keyrings_back(self):
        admin = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(admin)
        self.claim(self.referrer())
        self.claim(self.referrer("two@example.com", ip="51.2.2.2"), "f2@example.com", ip="51.8.8.8")
        self.assertEqual(self.left(), 6)
        claim = KeyringClaim.objects.filter(referral=self.referral()).first()
        self.client.post(reverse("admin:catalogue_keyringclaim_delete", args=[claim.pk]), {"post": "yes"})
        self.assertEqual(self.left(), 7)
        self.client.post(reverse("admin:catalogue_referral_changelist"), {
            "action": "delete_selected", "post": "yes", "_selected_action": [self.referral("f2@example.com").pk]})
        self.assertFalse(Referral.objects.filter(friend_key=referrals.email_key("f2@example.com")).exists())
        self.assertEqual(self.left(), 9)

    def test_a_cancelled_referral_is_never_to_post_and_its_address_goes_at_once(self):
        self.claim(self.referrer())
        self.post(self.asks()[0], ADDRESS)
        referrals.cancel(self.referral())
        self.assertEqual(self.left(), 10)
        claims = KeyringClaim.objects.all()
        self.assertFalse(referrals.to_post(claims).exists())
        self.assertEqual(referrals.mark_posted(claims), 0)
        self.assertEqual(set(claims.values_list("name", "line1", "postcode")), {("", "", "")})
        admin = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(admin)
        page = self.client.get(reverse("admin:catalogue_keyringclaim_changelist") + "?state=to_post")
        self.assertContains(page, "0 keyrings to post")
        # Deleting the cancelled referral later gives nothing back twice.
        referrals.delete_referrals(Referral.objects.all())
        self.assertEqual(self.left(), 10)


class FindingTests(ReferTestCase):
    def test_a_second_referrer_cannot_take_a_friends_referral(self):
        alice = self.referrer("alice@example.com", ip="51.1.1.1")
        mallory = self.referrer("mallory@example.com", ip="51.2.2.2")
        self.ask_friend(alice, ip="51.9.9.9", agent="Browser B")
        first = self.sent[-1]
        before = len(self.sent)
        with mock.patch("catalogue.referrals.timezone.now", return_value=timezone.now() + timedelta(minutes=11)):
            self.ask_friend(mallory, ip="51.3.3.3", agent="Browser C")
        self.assertEqual(len(self.sent), before)   # nothing sent on Mallory's behalf
        self.post(self.link_in(first, "/refer/claim/"), {}, ip="51.9.9.9", agent="Browser B")
        referral = self.referral()
        self.assertEqual(referral.status, Referral.Status.COMPLETE)
        self.assertEqual(referral.referrer.email, "alice@example.com")

    def test_the_same_referrer_asking_again_later_resends_the_same_link(self):
        alice = self.referrer("alice@example.com", ip="51.1.1.1")
        self.ask_friend(alice, ip="51.9.9.9")
        with mock.patch("catalogue.referrals.timezone.now", return_value=timezone.now() + timedelta(minutes=11)):
            self.ask_friend(alice, ip="51.9.9.9")
        claims = [self.link_in(m, "/refer/claim/") for m in self.sent if "/refer/claim/" in m["text"]]
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0], claims[1])

    def test_a_burst_of_completed_referrals_is_held(self):
        for n in range(referrals.BURST_PER_HOUR + 1):
            referrer = self.referrer(f"r{n}@example.com", ip=f"51.1.{n}.1")
            self.claim(referrer, f"f{n}@example.com", ip=f"51.2.{n}.1", agent=f"Agent {n}")
        statuses = [self.referral(f"f{n}@example.com").status for n in range(referrals.BURST_PER_HOUR + 1)]
        self.assertEqual(statuses[:-1], [Referral.Status.COMPLETE] * referrals.BURST_PER_HOUR)
        self.assertEqual(statuses[-1], Referral.Status.REVIEW)
        self.assertIn("completed across the site", self.referral(f"f{referrals.BURST_PER_HOUR}@example.com").review_reason)

    def test_delete_posts_with_unknown_tokens_count_as_misses(self):
        path = reverse("web:refer_stop", args=["x" * 32])
        for _ in range(referrals.ASKS_PER_NETWORK_HOUR):
            self.assertEqual(self.post(path, {}).status_code, 200)
        self.assertEqual(self.post(path, {}).status_code, 429)
        # Each unknown token is a miss, so the miss block applies to POSTs as it does to GETs.
        cache.clear()
        with mock.patch.object(referrals, "ASKS_PER_NETWORK_HOUR", 100), mock.patch.object(referrals, "ASKS_PER_NETWORK_DAY", 100):
            for _ in range(referrals.MISSES_PER_NETWORK_HOUR):
                self.post(path, {}, ip="51.7.7.7")
        with self.assertNumQueries(1):   # the wording only: no token is looked up
            self.assertEqual(self.get(path, ip="51.7.7.7").status_code, 429)

    def test_a_rate_limited_friend_post_reads_nothing_but_the_wording(self):
        referrer = self.referrer()
        for n in range(referrals.ASKS_PER_NETWORK_HOUR):
            self.ask_friend(referrer, f"x{n}@example.com", ip="51.6.6.6")
        with self.assertNumQueries(1):   # wording
            response = self.ask_friend(referrer, "late@example.com", ip="51.6.6.6")
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many requests", status_code=429)

    def test_a_flat_number_in_either_line_is_one_address_and_a_neighbour_is_marked_to_check(self):
        referrer = self.referrer()
        self.claim(referrer)
        asks = [self.link_in(m, "/refer/address/") for m in self.sent if "/refer/address/" in m["text"]]
        self.post(asks[0], {**ADDRESS, "line1": "Flat 1, 12 High Street"})
        same = self.post(asks[1], {**ADDRESS, "line1": "12 High Street", "line2": "Flat 1"})
        self.assertContains(same, "This address has already had a keyring", status_code=400)
        neighbour = self.post(asks[1], {**ADDRESS, "line1": "Flat 2, 12 High Street"})
        self.assertContains(neighbour, "We have your address")
        flags = dict(KeyringClaim.objects.values_list("line1", "same_postcode"))
        self.assertEqual(flags, {"Flat 1, 12 High Street": False, "Flat 2, 12 High Street": True})

    def test_the_admin_list_shows_whole_addresses_and_the_home_page_counts_what_waits(self):
        admin = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.claim(self.referrer())
        path = [self.link_in(m, "/refer/address/") for m in self.sent if "/refer/address/" in m["text"]][0]
        self.post(path, {**ADDRESS, "line2": "Flat 4"})
        self.claim(self.referrer("two@example.com", ip="51.3.3.3"), "f2@example.com", ip="51.3.3.3",
                   agent="Browser A")   # the friend on the referrer's own connection: held
        self.client.force_login(admin)
        listing = self.client.get(reverse("admin:catalogue_keyringclaim_changelist") + "?state=to_post")
        self.assertContains(listing, "12 High Street, Flat 4, Leeds, LS1 4AP")
        home = self.client.get(reverse("admin:index"))
        self.assertContains(home, "1 keyring to post")
        self.assertContains(home, "1 referral for review")
        self.assertContains(home, "?status__exact=review")

    def test_the_claimed_page_promises_only_what_is_true_when_held(self):
        referrer = self.referrer()
        response = self.claim(referrer, ip="81.2.69.1", agent="Browser A")
        self.assertEqual(self.referral().status, Referral.Status.REVIEW)
        self.assertContains(response, "when your keyrings are ready")
        self.assertNotContains(response, "to ask where to send your keyrings")

    def test_the_terms_stay_while_a_friends_details_outlive_the_referrer(self):
        self.claim(self.referrer())
        Referrer.objects.all().delete()
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        self.assertContains(self.get(reverse("web:terms")), 'id="terms-refer"')

    def test_wording_is_true(self):
        terms = self.get(reverse("web:terms")).content.decode()
        self.assertNotIn("we never email an address someone else typed in", terms)
        self.assertIn("We never email a friend on your behalf", terms)
        self.assertIn("one keyring per household".lower(), terms.lower().replace("each household can have one keyring",
                                                                                 "one keyring per household"))
        self.sign_up()
        confirm = self.sent[-1]
        self.assertNotIn("free", confirm["text"].lower())
        self.assertNotIn("commission", confirm["html"])
        stop = self.get(self.link_in(confirm, "/refer/stop/"))
        self.assertContains(stop, "name and postal address")
        self.assertContains(self.get(f"/r/{self.referrer('r2@example.com', ip='51.5.5.5').code}/"), "One per household")


class ReferCardTests(ReferTestCase):
    """The card that now and then invites a visitor to refer a friend, and the keyring art on the offer's pages."""

    CARD = "data-refer-card"

    def setUp(self):
        super().setUp()
        referrals.refresh_flag()

    def static_text(self, path):
        from django.contrib.staticfiles import finders

        return open(finders.find(path), encoding="utf-8").read()

    def test_the_card_shows_only_while_the_offer_is_advertised(self):
        html = self.get(reverse("web:about")).content.decode()
        self.assertIn(self.CARD, html)
        self.assertIn("refer-card.js", html)
        self.assertIn('href="/refer/"', html)
        self.assertIn("Raptors save in packs.", html)
        self.assertIn("Get my link", html)
        self.assertIn("No thanks", html)
        ReferralSettings.objects.filter(pk=1).update(is_on=False)
        referrals.refresh_flag()
        html = self.get(reverse("web:about")).content.decode()
        self.assertNotIn(self.CARD, html)
        self.assertNotIn("refer-card.js", html)

    def test_the_card_is_hidden_until_the_script_shows_it_and_is_labelled(self):
        html = self.get(reverse("web:home")).content.decode()
        start = html.index("<aside class=\"install refer-card\"")
        tag = html[start:html.index(">", start)]
        self.assertIn(" hidden", tag)
        self.assertIn('role="complementary"', tag)
        self.assertIn('aria-label="Refer a friend"', tag)

    def test_never_on_the_offers_own_pages_forms_the_swipe_page_or_errors(self):
        referrer = self.referrer()
        for url in (reverse("web:refer"), f"/r/{referrer.code}/", reverse("web:deck"), reverse("web:contact"),
                    reverse("web:list_shop"), "/no-such-page/", "/r/nosuchcode/"):
            with self.subTest(url=url):
                self.assertNotIn(self.CARD, self.get(url, ip="51.4.4.4").content.decode())

    def test_asking_for_a_link_turns_the_card_off_for_good(self):
        response = self.client.get(reverse("web:refer") + "?sent=1")
        self.assertContains(response, "data-refer-asked")
        self.assertNotContains(response, self.CARD)
        # The share page means the same.
        self.referrer()
        share = self.get(self.link_in(self.sent[0], "/refer/confirm/"))
        self.assertContains(share, "data-refer-asked")

    def test_never_over_the_buy_box_on_a_product_page(self):
        from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

        product = make_product(make_set(make_game()))
        make_listing(product, make_retailer("Harbour Games"), price="52.00")
        html = self.get(product.get_absolute_url()).content.decode()
        self.assertIn("buybox", html)
        self.assertNotIn(self.CARD, html)
        self.assertNotIn("refer-card.js", html)

    def test_the_cards_keyring_sits_on_a_soft_panel_and_steps_aside_on_the_smallest_phones(self):
        css = self.static_text("css/site.css")
        rule = css[css.index(".keyring--card {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("background: var(--color-brand-soft)", rule)
        self.assertIn("border-radius: var(--radius)", rule)
        self.assertIn("@media (max-width: 22.5rem) { .keyring--card { display: none; } }", css)

    def test_the_card_adds_no_query_on_the_home_or_product_page(self):
        from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

        product = make_product(make_set(make_game()))
        make_listing(product, make_retailer("Harbour Games"), price="52.00")

        def count(url):
            self.get(url)
            with CaptureQueriesContext(connection) as queries:
                self.get(url)
            return len(queries)

        for url, card in ((reverse("web:home"), True), (product.get_absolute_url(), False)):
            with self.subTest(url=url):
                on = count(url)
                self.assertEqual(self.CARD in self.get(url).content.decode(), card)
                ReferralSettings.objects.filter(pk=1).update(is_on=False)
                referrals.refresh_flag()
                self.assertEqual(count(url), on)
                ReferralSettings.objects.filter(pk=1).update(is_on=True)
                referrals.refresh_flag()

    def test_the_script_is_polite_and_offline(self):
        script = self.static_text("js/refer-card.js")
        self.assertNotIn("fetch(", script)
        self.assertNotIn("XMLHttpRequest", script)
        self.assertNotIn("sendBeacon", script)
        # Every storage read and write is wrapped, so a browser that blocks storage never throws.
        for line in script.splitlines():
            if "localStorage." in line or "sessionStorage." in line or "[name].getItem" in line:
                with self.subTest(line=line.strip()):
                    self.assertIn("try {", line)
        self.assertIn("const CHANCE = 0.2;", script)
        self.assertIn("if (pages < 2 ||", script)          # never the first page
        self.assertIn("get(SHOWN) === today()", script)    # at most once a day
        self.assertIn('[data-consent]:not([hidden]), [data-install]:not([hidden])', script)
        self.assertIn('e.key === "Escape"', script)
        self.assertNotIn(".focus()", script.split("const show")[1].split("const onScroll")[0])   # focus never stolen
        # The home screen card waits while this one is up.
        self.assertIn("[data-refer-card]:not([hidden])", self.static_text("js/install.js"))

    def test_the_card_moves_only_with_transform_and_honours_reduced_motion(self):
        css = self.static_text("css/site.css")
        self.assertIn(".refer-card", css)
        self.assertIn("transition: transform var(--motion-slow) var(--ease-out);", css)
        self.assertIn("@media (prefers-reduced-motion: reduce) { .install { transition: none; transform: none; } }", css)
        block = css[css.index("/* The pack:"):]
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,6}\b")
        rules = re.sub(r"@media \([^)]*\)", "", block)   # breakpoints cannot be tokens: custom properties do not work there
        self.assertNotRegex(rules, r"(?<![\w-])\d+(\.\d+)?(px|rem|ms)\b")

    def test_the_keyring_picture_is_small_sized_and_described(self):
        from django.contrib.staticfiles import finders
        from PIL import Image

        for name, limit in (("img/keyring.webp", 60_000), ("img/keyring.png", 150_000)):
            path = finders.find(name)
            with self.subTest(name=name):
                self.assertLess(open(path, "rb").read().__len__(), limit)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (900, 258))
                    self.assertIn("A", image.getbands())
        referrer = self.referrer()
        alt = 'alt="RipRaptor 3D keyring: the ripraptor logo in orange on a white base"'
        # The card keeps the drawn keyring; the refer a friend pages show the owner's photo (WinKeyringSectionTests).
        for url in (reverse("web:refer"), f"/r/{referrer.code}/", reverse("web:about")):
            html = self.get(url, ip="51.4.4.4").content.decode()
            with self.subTest(url=url):
                if url == reverse("web:about"):
                    self.assertIn('width="900" height="258"', html)
                    self.assertIn(alt, html)
                else:
                    self.assertIn('width="900" height="600"', html)
                self.assertIn('<span class="pack', html)
                self.assertIn('aria-hidden="true"><span class="mark pack__friend">', html)

    def test_the_new_wording_is_in_the_registry(self):
        from content.registry import ENTRIES

        keys = {entry.key for entry in ENTRIES}
        for key in ("refer.kicker", "refer.keyring.alt", "refer.nudge.label", "refer.nudge.text", "refer.nudge.go",
                    "refer.nudge.dismiss"):
            self.assertIn(key, keys)
