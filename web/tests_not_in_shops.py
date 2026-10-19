"""Sets sold only at events or direct by the publisher (ProductSet.not_in_shops): kept off the drop tracker
and the Coming soon rows, no reminders, and their set page says why."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import drop_alerts, drops
from catalogue.models import DropAlert, Listing, ProductSet
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

MAIL_ON = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com"}
LINE = "Sold at events or direct by the publisher, not in UK shops."


class NotInShopsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.magic = make_game("Magic: The Gathering", "magic-the-gathering", short_name="Magic")
        cls.shop = make_retailer("Harbour Games")
        cls.normal = make_set(cls.magic, name="Lorwyn Eclipsed", slug="lorwyn-eclipsed", code="ECL",
                              release_date=cls.today + timedelta(days=5))
        box = make_product(cls.normal, name="Lorwyn Eclipsed Play Booster Box", slug="ecl-box")
        make_listing(box, cls.shop, price="140.00", availability=Listing.Availability.PREORDER)
        # Out in five days, and (oddly) one shop lists a pre-order: a flagged set stays off even so.
        cls.mbc = make_set(cls.magic, name="Mystery Booster Commander Edition", slug="mystery-booster-commander",
                           code="MBC", release_date=cls.today + timedelta(days=5), not_in_shops=True)
        crate = make_product(cls.mbc, name="Mystery Booster Commander Edition Festival in a Box", slug="mbc-box")
        make_listing(crate, cls.shop, price="249.99", availability=Listing.Availability.PREORDER)
        # Out three days ago with no shop: would be under Out now.
        cls.event = make_set(cls.magic, name="Convention Exclusive Set", slug="convention-exclusive", code="CES",
                             release_date=cls.today - timedelta(days=3), not_in_shops=True)
        make_product(cls.event, name="Convention Exclusive Set Box", slug="ces-box")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_kept_off_both_drop_tabs(self):
        upcoming, recent = drops.tracker(self.today)
        self.assertEqual([d.pk for d in upcoming], [self.normal.pk])
        self.assertEqual(recent, [])
        for params in ({}, {"show": "out"}):
            html = self.client.get(reverse("web:drops"), params).content.decode()
            self.assertNotIn("Mystery Booster Commander", html)
            self.assertNotIn("Convention Exclusive", html)
        self.assertIn("Lorwyn Eclipsed", self.client.get(reverse("web:drops")).content.decode())
        self.assertIsNone(drops.upcoming_drop(self.mbc, self.today))

    def test_kept_off_coming_soon_on_home_and_game_pages(self):
        home = self.client.get("/")
        game = self.client.get(self.magic.get_absolute_url())
        for response in (home, game):
            self.assertEqual([row.pk for row in response.context["coming_soon"]], [self.normal.pk])
        self.assertIn("Lorwyn Eclipsed", home.content.decode())
        self.assertNotIn("Mystery Booster Commander Edition", home.content.decode())

    def test_the_set_page_says_why_where_the_date_would_be(self):
        html = self.client.get(self.mbc.get_absolute_url()).content.decode()
        self.assertEqual(html.count(LINE), 1)
        self.assertIn("MBC", html)
        self.assertNotIn("Date not announced yet", html)
        self.assertIn(LINE, self.client.get(self.event.get_absolute_url()).content.decode())
        self.assertNotIn(LINE, self.client.get(self.normal.get_absolute_url()).content.decode())

    @override_settings(**MAIL_ON)
    @mock.patch("catalogue.mail.send")
    def test_reminder_sign_up_is_refused(self, send):
        response = self.client.post(reverse("web:drop_ask", args=[self.mbc.pk]), {"email": "ben@example.com"})
        self.assertEqual(response.status_code, 404)
        # Even asked for directly, nothing is stored or sent.
        self.assertEqual(drop_alerts.ask(self.mbc, "ben@example.com"), "sent")
        self.assertFalse(DropAlert.objects.exists())
        send.assert_not_called()
        # A normal drop still takes one.
        self.client.post(reverse("web:drop_ask", args=[self.normal.pk]), {"email": "ben@example.com"})
        self.assertEqual(DropAlert.objects.get().product_set, self.normal)

    @mock.patch("catalogue.mail.send")
    def test_reminders_already_waiting_are_deleted_unsent(self, send):
        now = timezone.now()
        waiting = DropAlert.objects.create(product_set=self.normal, email="ben@example.com", created_at=now,
                                           confirmed_at=now)
        DropAlert.objects.create(product_set=self.normal, email="sam@example.com", created_at=now, confirmed_at=now)
        ProductSet.objects.filter(pk=self.normal.pk).update(not_in_shops=True)
        # Due the day before, at 10am: nothing goes.
        moment = timezone.make_aware(timezone.datetime.combine(self.normal.release_date - timedelta(days=1),
                                                               timezone.datetime.min.time())) + timedelta(hours=10)
        self.assertEqual(drop_alerts.send_due(now=moment), (0, 0))
        send.assert_not_called()
        self.assertFalse(DropAlert.objects.filter(pk=waiting.pk).exists())
        self.assertFalse(DropAlert.objects.exists())

    def test_the_owner_ticks_and_unticks_it_in_admin(self):
        self.client.force_login(get_user_model().objects.create_superuser("admin", "a@example.com", "pw"))
        changelist = self.client.get(reverse("admin:catalogue_productset_changelist"))
        self.assertContains(changelist, "not sold in UK shops")
        self.assertContains(changelist, "not_in_shops__exact=1")
        url = reverse("admin:catalogue_productset_change", args=[self.normal.pk])
        form = {"game": self.magic.pk, "name": self.normal.name, "slug": self.normal.slug, "code": "ECL",
                "release_date": self.normal.release_date.isoformat(), "not_in_shops": "on",
                "not_in_shops_note": "https://magic.wizards.com/en/news/example"}
        self.assertEqual(self.client.post(url, form).status_code, 302)
        self.normal.refresh_from_db()
        self.assertTrue(self.normal.not_in_shops)
        self.assertEqual(self.normal.not_in_shops_note, "https://magic.wizards.com/en/news/example")
        self.assertNotIn("Lorwyn Eclipsed", self.client.get(reverse("web:drops")).content.decode())
        del form["not_in_shops"]
        self.client.post(url, form)
        self.normal.refresh_from_db()
        self.assertFalse(self.normal.not_in_shops)
        self.assertIn("Lorwyn Eclipsed", self.client.get(reverse("web:drops")).content.decode())
