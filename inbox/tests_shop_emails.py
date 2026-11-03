"""Finding a shop's business address on its own website (inbox/shop_emails.py). The owner, 3 Nov 2026: "Can you add
the email addresses to the shops". No test reads a real shop: every page comes from a fake site."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue.importers import ImportError_
from catalogue.shop_pages import Page, Reader
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from . import outreach, shop_emails
from .models import ShopContact


class Site:
    """A fake website: the pages it has, by address, and every address asked for. Anything else is a 404."""

    def __init__(self, pages=None):
        self.pages = pages or {}
        self.asked = []

    def __call__(self, url, retries=0, deadline=None):
        self.asked.append(url)
        if url in self.pages:
            body = self.pages[url]
            if isinstance(body, Exception):
                raise body
            return Page(body.encode(), url)
        raise ImportError_(f"Could not fetch {url}: HTTP Error 404")

    def pages_read(self):
        return [url for url in self.asked if not url.endswith("/robots.txt")]


def reader_for(*sites, **options):
    """A Reader whose shops answer from ``sites``, in the order the shops are asked (or one site for all)."""
    def fetch_for(retailer):
        return sites[0] if len(sites) == 1 else sites[retailer.pk % len(sites)]
    return Reader(fetch_for=fetch_for, sleep=lambda seconds: None, **options)


def cloudflare(address, key=0x5A):
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in address)


HOME = "https://www.harbour-games.example/"


class ExtractTests(TestCase):
    def test_every_way_a_page_shows_an_address_is_read_and_junk_is_not(self):
        page = f"""
        <a href="mailto:Hello@Harbour-Games.example?subject=Hi">Email us</a>
        <p>Or write to info&#64;harbour-games.example, or sales@harbour-games.example.</p>
        <a class="__cf_email__" data-cfemail="{cloudflare('orders@harbour-games.example')}">[email protected]</a>
        <script type="application/ld+json">{{"@type": "Organization", "email": "mailto:support@harbour-games.example"}}</script>
        <script>var tracker = "pixel@sentry.io"; var u = "ghost@harbour-games.example";</script>
        <img src="/logo@2x.png"><p>you@example.com, noreply@harbour-games.example, not an address@</p>
        """
        self.assertEqual(shop_emails.candidates(page), [
            "hello@harbour-games.example", "support@harbour-games.example", "orders@harbour-games.example",
            "info@harbour-games.example", "sales@harbour-games.example", "noreply@harbour-games.example"])

    def test_the_hidden_address_decodes_and_a_bad_code_gives_nothing(self):
        self.assertEqual(shop_emails.decode_cloudflare(cloudflare("a@b.example", 0x11)), "a@b.example")
        self.assertEqual(shop_emails.decode_cloudflare("zz"), "")

    def test_a_shops_own_domain_ignores_www_and_the_sub_domain_but_not_another_site(self):
        for name, own in (("www.example.com", "example.com"), ("shop.example.co.uk", "example.co.uk"),
                          ("example.co.uk", "example.co.uk"), ("cards.example.org.uk", "example.org.uk")):
            self.assertEqual(shop_emails.registrable(name), own)

    def test_only_a_business_address_on_the_shops_own_domain_is_good(self):
        own = "harbour-games.example"
        judge = shop_emails.judge
        self.assertEqual(judge("info@harbour-games.example", own)[0], "good")
        self.assertEqual(judge("Customer.Service@shop.harbour-games.example", own)[0], "good")
        self.assertEqual(judge("sam@harbour-games.example", own)[0], "person")
        self.assertEqual(judge("harbourgames@gmail.com", own)[0], "free")
        self.assertEqual(judge("info@webagency.example", own)[0], "other")
        for local in ("noreply", "no-reply", "privacy", "dpo", "careers"):
            self.assertEqual(judge(f"{local}@harbour-games.example", own)[0], "other", local)

    def test_the_most_useful_role_comes_first_and_the_others_are_named(self):
        own = "harbour-games.example"
        self.assertEqual(shop_emails.best(["sales@harbour-games.example", "info@harbour-games.example",
                                           "x@gmail.com", "sam@harbour-games.example"], own),
                         ("info@harbour-games.example", {"free", "person"}))
        self.assertEqual(shop_emails.best(["x@gmail.com"], own), ("", {"free"}))
        self.assertEqual(shop_emails.best([], own), ("", set()))


class FindTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.shop = make_retailer("Harbour Games", website=HOME)

    def look(self, pages):
        site = Site(pages)
        return shop_emails.find(self.shop, reader_for(site)), site

    def test_an_address_on_the_home_page_is_found_with_the_page_it_was_on(self):
        found, site = self.look({HOME: '<a href="mailto:info@harbour-games.example">Email</a>'})
        self.assertEqual((found.address, found.page), ("info@harbour-games.example", HOME))
        self.assertEqual(site.pages_read(), [HOME])   # it stops once it has one

    def test_the_contact_page_the_home_page_links_to_is_read_when_the_home_page_has_none(self):
        found, site = self.look({
            HOME: '<a href="/blog">Blog</a> <a href="/pages/get-in-touch">Contact us</a>',
            f"{HOME}pages/get-in-touch": "<p>Email hello@harbour-games.example</p>",
        })
        self.assertEqual((found.address, found.page), ("hello@harbour-games.example", f"{HOME}pages/get-in-touch"))
        self.assertEqual(site.pages_read(), [HOME, f"{HOME}pages/get-in-touch"])

    def test_the_usual_contact_pages_are_tried_when_nothing_links_to_one(self):
        found, site = self.look({HOME: "<p>Welcome</p>", f"{HOME}contact": "<p>sales@harbour-games.example</p>"})
        self.assertEqual(found.address, "sales@harbour-games.example")
        self.assertEqual(site.pages_read(), [HOME, f"{HOME}pages/contact", f"{HOME}pages/contact-us", f"{HOME}contact"])

    def test_no_more_than_four_pages_and_never_another_site(self):
        found, site = self.look({HOME: '<a href="https://other.example/contact">Contact</a><p>nothing</p>'})
        self.assertEqual(found.note, "no address on its home or contact pages")
        self.assertEqual(len(site.pages_read()), shop_emails.PAGES_PER_SHOP)
        self.assertNotIn("https://other.example/contact", site.asked)

    def test_why_there_is_none_is_said(self):
        notes = {
            "only a free-mail address is on its pages": "<p>harbourgames@gmail.com</p>",
            "only an address that looks like a person's is on its pages": "<p>sam@harbour-games.example</p>",
            "no address on its home or contact pages": "<p>info@webagency.example</p>",
        }
        for note, page in notes.items():
            with self.subTest(note):
                found, _site = self.look({HOME: page})
                self.assertEqual((found.address, found.note), ("", note))

    def test_a_shop_whose_robots_txt_says_no_or_that_does_not_answer_is_not_pushed(self):
        found, site = self.look({f"{HOME}robots.txt": "User-agent: *\nDisallow: /\n", HOME: "info@harbour-games.example"})
        self.assertEqual(found.note, "its robots.txt asks robots not to read its pages")
        self.assertEqual(site.pages_read(), [])
        cache.clear()
        found, _site = self.look({HOME: ImportError_(f"Could not fetch {HOME}: HTTP Error 500")})
        self.assertEqual((found.address, found.note), ("", "its pages could not be read"))


class RunTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.harbour = make_retailer("Harbour Games", website=HOME)
        self.quay = make_retailer("Quay Cards", website="https://quay-cards.example/")
        self.site = Site({
            HOME: '<a href="mailto:info@harbour-games.example">Email</a>',
            "https://quay-cards.example/": "<p>Come and see us</p>",
        })
        self.lines = []

    def run_it(self, **kwargs):
        kwargs.setdefault("reader", reader_for(self.site))
        return shop_emails.run(report=self.lines.append, **kwargs)

    def contact(self, shop):
        return ShopContact.objects.get(retailer=shop)

    def test_an_address_is_kept_with_where_it_was_found_and_a_miss_is_noted(self):
        result = self.run_it()
        self.assertEqual((result.found, result.none, result.skipped), (1, 1, 0))
        found, none = self.contact(self.harbour), self.contact(self.quay)
        self.assertEqual((found.email, found.email_found_on, found.email_note), ("info@harbour-games.example", HOME, ""))
        self.assertIsNotNone(found.email_looked_at)
        self.assertEqual((none.email, none.email_note), ("", "no address on its home or contact pages"))
        self.assertIsNotNone(none.email_looked_at)
        self.assertIn(f"Harbour Games: info@harbour-games.example (on {HOME})", self.lines)
        self.assertIn("Quay Cards: none, no address on its home or contact pages.", self.lines)
        self.assertFalse(shop_emails.running())

    def test_an_address_the_owner_typed_is_never_looked_for_or_replaced(self):
        outreach.ensure_contacts()
        ShopContact.objects.filter(retailer=self.harbour).update(email="owner@typed.example")
        self.run_it()
        self.assertNotIn(HOME, self.site.asked)
        self.assertEqual(self.contact(self.harbour).email, "owner@typed.example")
        # Typed while a shop was being looked at: the find does not replace it.
        contact = self.contact(self.quay)
        ShopContact.objects.filter(pk=contact.pk).update(email="typed@later.example")
        shop_emails.save(contact, shop_emails.Found(address="info@quay-cards.example", page="x"), timezone.now())
        self.assertEqual(self.contact(self.quay).email, "typed@later.example")
        self.assertEqual(self.contact(self.quay).email_found_on, "")

    def test_a_shop_is_looked_at_once_a_month_unless_forced(self):
        self.run_it()
        self.site.asked.clear()
        self.run_it()
        self.assertEqual(self.site.asked, [])
        ShopContact.objects.update(email_looked_at=timezone.now() - timedelta(days=31))
        self.run_it()
        self.assertEqual(self.site.pages_read()[0], "https://quay-cards.example/")   # Harbour has its address now
        self.site.asked.clear()
        self.run_it(force=True)
        self.assertIn("https://quay-cards.example/", self.site.asked)

    def test_shops_that_must_not_be_asked_are_left_alone(self):
        waiting = make_retailer("Waiting Cards", website="https://waiting.example/",
                                backoff_until=timezone.now() + timedelta(hours=2))
        paused = make_retailer("Paused Cards", website="https://paused.example/", reading_paused=True)
        off = make_retailer("Off Cards", website="https://off.example/", is_active=False)
        no_site = make_retailer("No Site Cards", website="")
        outreach.ensure_contacts()
        refused = make_retailer("Refused Cards", website="https://refused.example/")
        outreach.ensure_contacts()
        ShopContact.objects.filter(retailer=refused).update(status=ShopContact.Status.DO_NOT_EMAIL)
        result = self.run_it()
        self.assertEqual(result.skipped, 2)
        for shop in (waiting, paused, off, no_site, refused):
            self.assertFalse(any(shop.website and url.startswith(shop.website) for url in self.site.asked), shop.name)
            self.assertIsNone(self.contact(shop).email_looked_at, shop.name)
        self.assertIn("Waiting Cards: left for now, the shop is waiting after errors.", self.lines)
        self.assertIn("Paused Cards: left for now, reading this shop is paused.", self.lines)

    def test_nothing_is_read_while_pause_all_is_on_or_another_run_is_going(self):
        with mock.patch("inbox.shop_emails.crawl.all_paused", return_value=True):
            result = self.run_it()
        self.assertTrue(result.paused)
        self.assertEqual(self.site.asked, [])
        cache.set(shop_emails.RUNNING_KEY, "now", 600)
        result = self.run_it()
        self.assertTrue(result.already)
        self.assertEqual(self.site.asked, [])

    def test_a_dry_run_keeps_nothing_and_a_limit_stops_early(self):
        result = self.run_it(dry_run=True)
        self.assertEqual(result.found, 1)
        self.assertEqual(ShopContact.objects.exclude(email="").count(), 0)
        self.assertEqual(ShopContact.objects.exclude(email_looked_at=None).count(), 0)
        self.assertIn(f"Harbour Games: info@harbour-games.example (on {HOME})", self.lines)
        self.run_it(limit=1)
        self.assertEqual(ShopContact.objects.exclude(email_looked_at=None).count(), 1)

    def test_a_shop_being_read_now_waits_and_a_run_out_of_pages_stops(self):
        with mock.patch.object(Reader, "being_read", return_value=True):
            result = self.run_it()
        self.assertEqual(result.skipped, 2)
        self.assertEqual(ShopContact.objects.exclude(email_looked_at=None).count(), 0)
        cache.clear()
        result = self.run_it(reader=reader_for(self.site, pages=1))
        self.assertTrue(result.stopped)
        self.assertEqual(ShopContact.objects.exclude(email_looked_at=None).count(), 0)

    def test_one_shop_failing_does_not_stop_the_rest(self):
        with mock.patch("inbox.shop_emails.find", side_effect=[RuntimeError("boom"), shop_emails.Found(note="n")]), \
                self.assertLogs("inbox.shop_emails", "WARNING") as logged:
            result = self.run_it()
        self.assertEqual((result.skipped, result.none), (1, 1))
        self.assertIn("Harbour Games failed", logged.output[0])

    def test_the_command_reports_and_a_dry_run_changes_nothing(self):
        out = StringIO()
        with mock.patch.object(shop_emails, "Reader", lambda **options: reader_for(self.site, **options)):
            call_command("find_shop_emails", "--dry-run", "--shop", "harbour-games", stdout=out)
        self.assertIn("info@harbour-games.example", out.getvalue())
        self.assertIn("Would keep 1 addresses; none found for 0 shops; 0 left for later.", out.getvalue())
        self.assertEqual(ShopContact.objects.exclude(email="").count(), 0)

    def test_the_run_is_never_started_from_a_test(self):
        self.assertFalse(shop_emails.start_now())
        self.assertFalse(shop_emails.running())


class PageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        self.url = reverse("admin:inbox_shopcontact_update_emails")
        product = make_product(make_set(make_game()), name="Surging Sparks Booster Box")
        self.harbour = make_retailer("Harbour Games", website=HOME)
        self.quay = make_retailer("Quay Cards", website="https://quay-cards.example/")
        for shop in (self.harbour, self.quay):
            make_listing(product, shop)
        outreach.ensure_contacts()

    def contact(self, shop):
        return ShopContact.objects.get(retailer=shop)

    def test_the_button_is_there_while_addresses_are_missing_and_gone_once_none_are(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="_find"')
        self.assertContains(response, "Find the missing addresses")
        ShopContact.objects.update(email="a@b.example")
        self.assertNotContains(self.client.get(self.url), 'name="_find"')

    def test_pressing_it_starts_a_run_and_says_so(self):
        with mock.patch("inbox.shop_emails.start_now", return_value=True) as start:
            response = self.client.post(self.url, {"_find": "1"}, follow=True)
        start.assert_called_once_with()
        self.assertRedirects(response, self.url)
        self.assertContains(response, "Looking for the missing addresses now.")
        with mock.patch("inbox.shop_emails.start_now", return_value=False), \
                mock.patch("inbox.shop_emails.running", return_value=True):
            self.assertContains(self.client.post(self.url, {"_find": "1"}, follow=True), "Already looking for addresses.")
        with mock.patch("inbox.shop_emails.start_now", return_value=False):
            self.assertContains(self.client.post(self.url, {"_find": "1"}, follow=True),
                                "Could not start looking for addresses.")

    def test_while_it_runs_the_page_says_how_far_it_has_got(self):
        ShopContact.objects.filter(retailer=self.harbour).update(email_looked_at=timezone.now())
        cache.set(shop_emails.RUNNING_KEY, "now", 600)
        response = self.client.get(self.url)
        self.assertContains(response, "Looking for addresses now.")
        self.assertContains(response, "1 of 2 shops looked at so far.")
        self.assertNotContains(response, 'name="_find"')

    def test_a_found_address_names_its_page_and_a_miss_says_why(self):
        ShopContact.objects.filter(retailer=self.harbour).update(
            email="info@harbour-games.example", email_found_on=f"{HOME}pages/contact")
        ShopContact.objects.filter(retailer=self.quay).update(
            email_note="only a free-mail address is on its pages", email_looked_at=timezone.now())
        html = self.client.get(self.url).content.decode()
        self.assertIn(f'Found automatically on <a href="{HOME}pages/contact" target="_blank" rel="noopener '
                      'noreferrer">www.harbour-games.example/pages/contact</a>. Check it looks right before you send.',
                      html)
        self.assertIn(f"Looked {timezone.localdate():%-d %b} and found none: only a free-mail address is on its pages. "
                      "Add it by hand.", html)

    def test_an_address_typed_over_a_found_one_is_the_owners_own(self):
        contact = self.contact(self.harbour)
        ShopContact.objects.filter(pk=contact.pk).update(email="info@harbour-games.example", email_found_on=HOME)
        self.client.post(self.url, {"contact": contact.pk, "email": "info@harbour-games.example", "_save": "1"})
        self.assertEqual(self.contact(self.harbour).email_found_on, HOME)   # unchanged: still the found one
        self.client.post(self.url, {"contact": contact.pk, "email": "sam@harbour-games.example", "_save": "1"})
        self.assertEqual((self.contact(self.harbour).email, self.contact(self.harbour).email_found_on),
                         ("sam@harbour-games.example", ""))

    def test_changing_it_on_the_shops_own_page_does_the_same(self):
        contact = self.contact(self.harbour)
        ShopContact.objects.filter(pk=contact.pk).update(email="info@harbour-games.example", email_found_on=HOME)
        url = reverse("admin:inbox_shopcontact_change", args=[contact.pk])
        post = {"email": "info@harbour-games.example", "status": "new", "notes": "", "_save": "Save"}
        self.client.post(url, post)
        self.assertEqual(self.contact(self.harbour).email_found_on, HOME)
        self.client.post(url, {**post, "email": "hello@harbour-games.example"})
        self.assertEqual(self.contact(self.harbour).email_found_on, "")
