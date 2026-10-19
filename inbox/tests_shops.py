from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import mail, notify

from . import shops
from .models import ShopEnquiry
from .tests import MAIL, Outbox, Pushes

GOOD = {"name": "Sam Lee", "shop_url": "www.HarbourGames.co.uk/", "email": "Sam@HarbourGames.co.uk",
        "message": "We stock Pokemon and Lorcana."}


@override_settings(**MAIL, RIPRAPTOR_CONTACT_EMAIL="")
class ListShopTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.outbox = Outbox()
        self.pushes = Pushes()
        for patcher in (mock.patch.object(mail, "send", self.outbox),
                        mock.patch.object(notify.urllib.request, "urlopen", self.pushes)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, ip="203.0.113.5", **data):
        return self.client.post(reverse("web:list_shop"), {**GOOD, **data}, REMOTE_ADDR=ip)

    # The page ------------------------------------------------------------------------------------------

    def test_page_is_indexable_and_linked(self):
        response = self.client.get(reverse("web:list_shop"))
        self.assertContains(response, "List your shop")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, "<link rel=\"canonical\" href=\"http://testserver/list-your-shop/\">")
        self.assertNotContains(response, "noindex")
        self.assertContains(self.client.get(reverse("web:about")), f'href="{reverse("web:list_shop")}"')
        self.assertContains(self.client.get(reverse("web:home")), f'href="{reverse("web:list_shop")}"')
        self.assertContains(self.client.get(reverse("web:sitemap")), "/list-your-shop/")
        self.assertContains(self.client.get(reverse("web:terms")), "If you ask us to list your shop")

    def test_page_takes_few_queries(self):
        self.client.get(reverse("web:list_shop"))
        with self.assertNumQueries(1):   # the site wording, read once
            self.client.get(reverse("web:list_shop"))

    def test_page_needs_csrf(self):
        from django.test import Client

        response = Client(enforce_csrf_checks=True).post(reverse("web:list_shop"), GOOD)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ShopEnquiry.objects.exists())

    # Sending -------------------------------------------------------------------------------------------

    def test_valid_form_is_stored_normalised_and_redirects_to_thanks(self):
        response = self.send()
        self.assertRedirects(response, reverse("web:list_shop_thanks"), fetch_redirect_response=False)
        enquiry = ShopEnquiry.objects.get()
        self.assertEqual((enquiry.name, enquiry.shop_url, enquiry.domain, enquiry.email, enquiry.message),
                         ("Sam Lee", "https://www.harbourgames.co.uk/", "harbourgames.co.uk",
                          "sam@harbourgames.co.uk", "We stock Pokemon and Lorcana."))
        self.assertIsNone(enquiry.handled_at)
        thanks = self.client.get(reverse("web:list_shop_thanks"))
        self.assertContains(thanks, "Thanks for asking")
        self.assertContains(thanks, '<meta name="robots" content="noindex')
        # Reloading the thanks page sends nothing again.
        self.client.get(reverse("web:list_shop_thanks"))
        self.assertEqual((ShopEnquiry.objects.count(), len(self.outbox.sent)), (1, 1))

    def test_control_and_format_characters_are_not_stored(self):
        # A NUL or a bidi override would let what the owner reads in admin and the email differ from what
        # was sent. They are stripped from the name and message; the website is refused.
        response = self.send(name="Sa\x00m\u202e Lee", message="Line one\x00\r\nLine\u200b two\u202e")
        self.assertRedirects(response, reverse("web:list_shop_thanks"), fetch_redirect_response=False)
        enquiry = ShopEnquiry.objects.get()
        self.assertEqual((enquiry.name, enquiry.message), ("Sam Lee", "Line one\nLine two"))
        for bad in ("https://n.co.uk/\x00", "https://shop.co.uk/\u202egnp.exe", "shop.co.uk/\u200bx"):
            with self.subTest(bad=bad):
                response = self.send(ip="203.0.113.9", shop_url=bad)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "That website address does not look right")
        self.assertEqual(ShopEnquiry.objects.count(), 1)

    def test_a_name_of_only_hidden_characters_is_missing(self):
        response = self.send(name="\x00\u202e")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShopEnquiry.objects.exists())

    def test_emoji_joiners_survive_in_the_message(self):
        self.send(message="Family packs \U0001f468\u200d\U0001f469\u200d\U0001f467")
        self.assertIn("\u200d", ShopEnquiry.objects.get().message)

    def test_email_note_matches_what_purge_does(self):
        from content.registry import REGISTRY

        self.assertEqual((shops.HANDLED_KEEP_DAYS, shops.OPEN_KEEP_DAYS), (365, 730))
        for key in ("listshop.form.email_note", "terms.data.listshop"):
            with self.subTest(key=key):
                text = REGISTRY[key].default
                self.assertIn("a year after we", text)
                self.assertIn("two years", text)
                self.assertNotIn("after we reply", text)

    def test_message_is_optional(self):
        self.send(message="")
        self.assertEqual(ShopEnquiry.objects.get().message, "")

    def test_bad_input_is_refused_and_kept_on_the_form(self):
        cases = [
            ({"name": "  "}, "Add your name."),
            ({"shop_url": ""}, "That website address does not look right."),
            ({"shop_url": "not a website"}, "That website address does not look right."),
            ({"shop_url": "ftp://harbourgames.co.uk"}, "That website address does not look right."),
            ({"shop_url": "javascript:alert(1)"}, "That website address does not look right."),
            ({"email": ""}, "That email address does not look right."),
            ({"email": "not an email"}, "That email address does not look right."),
            ({"message": "x" * 1001}, "Keep the message under 1,000 characters."),
        ]
        for data, words in cases:
            with self.subTest(data=data):
                response = self.send(**data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, words)
                self.assertContains(response, 'value="Sam Lee"' if "name" not in data else 'name="name"')
        self.assertFalse(ShopEnquiry.objects.exists())
        self.assertEqual(self.outbox.sent, [])

    def test_other_methods_are_refused(self):
        self.assertEqual(self.client.put(reverse("web:list_shop")).status_code, 405)
        self.assertEqual(self.client.post(reverse("web:list_shop_thanks")).status_code, 405)

    def test_oversized_body_is_refused_before_anything_is_read(self):
        response = self.send(message="x" * (shops.MAX_BODY + 10))
        self.assertEqual(response.status_code, 413)
        self.assertFalse(ShopEnquiry.objects.exists())

    def test_honeypot_answers_thanks_and_stores_nothing(self):
        response = self.send(website="http://spam.example")
        self.assertRedirects(response, reverse("web:list_shop_thanks"), fetch_redirect_response=False)
        self.assertFalse(ShopEnquiry.objects.exists())
        self.assertEqual((self.outbox.sent, self.pushes.sent), ([], []))

    # Abuse ---------------------------------------------------------------------------------------------

    def test_one_connection_is_capped_per_hour_and_day(self):
        for n in range(shops.PER_ADDRESS_HOUR):
            self.send(shop_url=f"shop{n}.co.uk")
        response = self.send(shop_url="one-more.co.uk")
        self.assertContains(response, "Try again in an hour.")
        self.assertEqual(ShopEnquiry.objects.count(), shops.PER_ADDRESS_HOUR)
        # Another connection is not held back.
        self.assertEqual(self.send(ip="198.51.100.7", shop_url="other.co.uk").status_code, 302)
        # The day's cap holds after the hour's count has gone.
        cache.delete_many([k for k in list(cache._cache) if ":shop_hour:" in k])
        for n in range(shops.PER_ADDRESS_DAY):
            self.send(ip="192.0.2.9", shop_url=f"day{n}.co.uk")
            cache.delete_many([k for k in list(cache._cache) if ":shop_hour:" in k])
        self.assertContains(self.send(ip="192.0.2.9", shop_url="day-more.co.uk"), "Try again in an hour.")

    def test_the_address_is_counted_hashed(self):
        self.send()
        keys = " ".join(str(k) for k in cache._cache)
        self.assertIn(":shop_hour:", keys)
        self.assertNotIn("203.0.113.5", keys)

    def test_site_wide_hourly_cap(self):
        now = timezone.now()
        ShopEnquiry.objects.bulk_create([
            ShopEnquiry(name="x", shop_url=f"https://s{n}.co.uk/", domain=f"s{n}.co.uk", email="a@b.co", created_at=now)
            for n in range(shops.PER_SITE_HOUR)
        ])
        self.assertContains(self.send(), "Try again in an hour.")
        self.assertEqual(ShopEnquiry.objects.count(), shops.PER_SITE_HOUR)

    def test_same_domain_within_30_days_answers_thanks_and_stores_nothing(self):
        self.send()
        response = self.send(shop_url="http://harbourgames.co.uk/pokemon", email="someone@else.com", ip="198.51.100.7")
        self.assertRedirects(response, reverse("web:list_shop_thanks"), fetch_redirect_response=False)
        self.assertEqual(ShopEnquiry.objects.count(), 1)
        self.assertEqual(len(self.outbox.sent), 1)

    def test_same_domain_may_ask_again_after_30_days_or_once_handled(self):
        self.send()
        ShopEnquiry.objects.update(created_at=timezone.now() - timedelta(days=shops.DUPLICATE_DAYS + 1))
        self.send(ip="198.51.100.7")
        self.assertEqual(ShopEnquiry.objects.count(), 2)
        ShopEnquiry.objects.update(handled_at=timezone.now(), created_at=timezone.now())
        self.send(ip="198.51.100.8")
        self.assertEqual(ShopEnquiry.objects.count(), 3)

    # The owner's email ---------------------------------------------------------------------------------

    def test_owner_is_emailed_the_details_escaped_and_the_shop_is_not(self):
        self.send(name="<b>Sam</b>", message="<script>alert(1)</script>\nSecond line")
        enquiry = ShopEnquiry.objects.get()
        self.assertEqual(len(self.outbox.sent), 1)
        sent = self.outbox.sent[0]
        self.assertEqual(sent["to"], "owner@example.com")
        self.assertEqual(sent["subject"], "Shop asking to be listed: harbourgames.co.uk")
        self.assertIn("Name: <b>Sam</b>", sent["text"])
        self.assertIn("https://www.harbourgames.co.uk/", sent["text"])
        self.assertIn("sam@harbourgames.co.uk", sent["text"])
        self.assertIn(f"/admin/inbox/shopenquiry/{enquiry.pk}/change/", sent["text"])
        self.assertNotIn("<script>", sent["html"])
        self.assertIn("&lt;script&gt;", sent["html"])
        self.assertIn("&lt;b&gt;Sam&lt;/b&gt;", sent["html"])
        # The shop's website is text in the HTML, never a link; the only link is to admin.
        self.assertNotIn('href="https://www.harbourgames.co.uk', sent["html"])
        self.assertEqual(sent["html"].count("<a "), 1)
        # Nothing ever goes to the address typed into the form.
        self.assertNotIn("sam@harbourgames.co.uk", [m["to"] for m in self.outbox.sent])
        # The push is public, so it names nobody.
        push = self.pushes.sent[0]
        self.assertEqual(push.get_header("Title"), "A shop asks to be listed")
        self.assertNotIn(b"harbour", push.data.lower())

    def test_contact_email_is_used_when_set(self):
        with self.settings(RIPRAPTOR_CONTACT_EMAIL="hello@ripraptor.com"):
            self.send()
        self.assertEqual([m["to"] for m in self.outbox.sent], ["hello@ripraptor.com"])

    def test_a_failed_email_keeps_the_enquiry(self):
        def broken(*args, **kwargs):
            raise mail.MailError("down")
        with mock.patch.object(mail, "send", broken):
            response = self.send()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ShopEnquiry.objects.count(), 1)

    # Admin and keeping ---------------------------------------------------------------------------------

    def test_admin_lists_filters_and_marks_handled(self):
        self.send()
        self.send(ip="198.51.100.7", shop_url="northgate.co.uk", name="Ali")
        waiting, done = ShopEnquiry.objects.order_by("pk")
        ShopEnquiry.objects.filter(pk=done.pk).update(handled_at=timezone.now() - timedelta(days=2))
        admin_user = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(admin_user)
        url = reverse("admin:inbox_shopenquiry_changelist")
        self.assertContains(self.client.get(url, {"handled": "no"}), "harbourgames.co.uk")
        self.assertNotContains(self.client.get(url, {"handled": "no"}), "northgate.co.uk")
        self.assertContains(self.client.get(url, {"handled": "yes"}), "northgate.co.uk")
        self.assertContains(self.client.get(url, {"q": "Ali"}), "northgate.co.uk")
        self.assertContains(self.client.get(reverse("admin:index")), "1 shop asking to be listed")
        response = self.client.post(url, {"action": "mark_handled", "_selected_action": [waiting.pk, done.pk]})
        self.assertEqual(response.status_code, 302)
        handled_before = ShopEnquiry.objects.get(pk=done.pk).handled_at
        waiting.refresh_from_db()
        done.refresh_from_db()
        self.assertIsNotNone(waiting.handled_at)
        self.assertEqual(done.handled_at, handled_before)   # an earlier answer keeps its date
        change = self.client.get(reverse("admin:inbox_shopenquiry_change", args=[waiting.pk]))
        self.assertContains(change, "https://www.harbourgames.co.uk/")
        self.assertNotContains(change, 'href="https://www.harbourgames.co.uk/')

    def test_retention_cleanup(self):
        now = timezone.now()

        def make(domain, created_days, handled_days=None):
            return ShopEnquiry.objects.create(
                name="x", shop_url=f"https://{domain}/", domain=domain, email="a@b.co",
                created_at=now - timedelta(days=created_days),
                handled_at=None if handled_days is None else now - timedelta(days=handled_days))

        make("fresh.co.uk", 10)
        make("handled-recently.co.uk", 500, 300)
        make("handled-long-ago.co.uk", 500, 366)
        make("never-handled-old.co.uk", 731)
        make("never-handled-younger.co.uk", 700)
        self.assertEqual(shops.purge(now), 2)
        self.assertEqual(sorted(ShopEnquiry.objects.values_list("domain", flat=True)),
                         ["fresh.co.uk", "handled-recently.co.uk", "never-handled-younger.co.uk"])

    def test_tidy_all_runs_the_cleanup(self):
        ShopEnquiry.objects.create(name="x", shop_url="https://old.co.uk/", domain="old.co.uk", email="a@b.co",
                                   created_at=timezone.now() - timedelta(days=800))
        out = StringIO()
        call_command("tidy_all", stdout=out)
        self.assertIn("1 old shop enquiries deleted.", out.getvalue())
        self.assertFalse(ShopEnquiry.objects.exists())


class NormaliseUrlTests(TestCase):
    def test_good_addresses(self):
        cases = {
            "harbourgames.co.uk": ("https://harbourgames.co.uk/", "harbourgames.co.uk"),
            "HTTP://WWW.Harbourgames.CO.UK:80/Shop?x=1#top": ("http://www.harbourgames.co.uk/Shop?x=1", "harbourgames.co.uk"),
            "https://harbourgames.co.uk.:443": ("https://harbourgames.co.uk/", "harbourgames.co.uk"),
            "shop.example.com/pokemon": ("https://shop.example.com/pokemon", "shop.example.com"),
            "kártya.hu": ("https://xn--krtya-xqa.hu/", "xn--krtya-xqa.hu"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(shops.normalise_url(raw), expected)

    def test_bad_addresses(self):
        for raw in ("", "   ", "localhost", "http://localhost:8000", "http://127.0.0.1/", "https://10.0.0.1",
                    "ftp://shop.co.uk", "mailto:a@shop.co.uk", "javascript:alert(1)", "file:///etc/passwd",
                    "https://user:pw@shop.co.uk", "https://shop.co.uk:8080", "https://shop .co.uk",
                    "https://-shop.co.uk", "https://shop.123", "https://shop", "data:text/html,hi",
                    "https://n.co.uk/\x00", "https://shop.co.uk/\u202egnp.exe", "https://shop.co.uk/a\u2066b",
                    "https://" + "a" * 300 + ".co.uk"):
            with self.subTest(raw=raw), self.assertRaises(shops.Invalid):
                shops.normalise_url(raw)
