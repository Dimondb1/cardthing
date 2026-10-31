"""Every alert for a drop (the home page's drop spotlight): the drop reminder's emails, then restock emails for
RESTOCK_DAYS after the drop, one confirm link for all of it (catalogue/drop_alerts.py)."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue import drop_alerts
from catalogue.models import DropAlert, Listing, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .tests import CopyStyleTests
from .tests_drops import MAIL_ON

UK = ZoneInfo("Europe/London")


def uk(*args):
    return datetime(*args, tzinfo=UK)


class RestockEmailTests(TestCase):
    """Fixed clocks for the stages; prices are checked against the real clock, as the drop reminders' tests do."""

    def setUp(self):
        game = make_game()
        self.shop = make_retailer("Harbour Games")
        self.other = make_retailer("Northgate Cards")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.set = make_set(game, name="Delta Reign", slug="delta-reign", release_date=date(2030, 3, 12))
        self.etb = make_product(self.set, name="Delta Reign Elite Trainer Box", slug="dri-etb", rrp=Decimal("54.99"))
        self.box = make_product(self.set, name="Delta Reign Booster Box", slug="dri-box", product_type="booster_box")
        self.etb_listing = make_listing(self.etb, self.shop, price="54.99", availability=Listing.Availability.PREORDER)
        self.alert = DropAlert.objects.create(product_set=self.set, email="ben@example.com", created_at=uk(2030, 3, 1, 9),
                                              confirmed_at=uk(2030, 3, 1, 9, 5), restocks_asked=True, restocks=True)
        patcher = mock.patch("catalogue.mail.send")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def run_at(self, *when):
        return drop_alerts.send_due(now=uk(*when))

    def subjects(self):
        return [c.args[1] for c in self.send.call_args_list]

    def stock(self, listing):
        Listing.objects.filter(pk=listing.pk).update(availability=Listing.Availability.IN_STOCK)

    def sell_out(self, listing):
        Listing.objects.filter(pk=listing.pk).update(availability=Listing.Availability.OUT_OF_STOCK)

    def test_the_drop_day_email_is_not_the_last_and_restocks_follow(self):
        self.stock(self.etb_listing)
        self.assertEqual(self.run_at(2030, 3, 12, 8, 0), (1, 0))
        self.assertEqual(self.subjects(), ["Delta Reign is out today"])
        text = self.send.call_args.args[2]
        self.assertNotIn("This was your last reminder", text)
        self.assertIn("for 30 days after when a UK shop has it in stock again at a fair price", text)
        self.alert.refresh_from_db()
        # The ETB in stock was in the drop-day email, so it is not news after it.
        self.assertEqual(self.alert.restock_seen, [self.etb.pk])
        # The box comes in at another shop: news, an hour after the drop-day email.
        box_listing = make_listing(self.box, self.other, price="150.00")
        self.assertEqual(self.run_at(2030, 3, 12, 8, 30), (0, 0))
        self.assertEqual(self.run_at(2030, 3, 12, 9, 0), (1, 0))
        self.assertEqual(self.subjects()[-1], "Back in stock: Delta Reign")
        text = self.send.call_args.args[2]
        self.assertIn("Delta Reign Booster Box", text)
        self.assertIn("£150.00 delivered at Northgate Cards", text)
        self.assertNotIn("Elite Trainer Box", text)
        self.assertIn(self.box.get_absolute_url(), text)
        self.assertNotIn("/go/", text)
        self.assertEqual(self.send.call_args.kwargs["unsubscribe"], drop_alerts.link("web:drop_stop", self.alert.token))
        # Nothing new: nothing sent.
        self.assertEqual(self.run_at(2030, 3, 12, 11, 0), (0, 0))
        # The box sells out and comes back: news again.
        self.sell_out(box_listing)
        self.assertEqual(self.run_at(2030, 3, 13, 9, 0), (0, 0))
        self.stock(box_listing)
        self.assertEqual(self.run_at(2030, 3, 13, 9, 15), (1, 0))
        self.assertTrue(DropAlert.objects.exists())

    def test_a_restock_waits_for_the_hours_and_the_gap(self):
        DropAlert.objects.update(day_sent_at=uk(2030, 3, 12, 8), restock_seen=[])
        self.stock(self.etb_listing)
        self.assertEqual(self.run_at(2030, 3, 13, 23, 30), (0, 0))   # after 11pm
        self.assertEqual(self.run_at(2030, 3, 14, 6, 45), (0, 0))    # before 7am
        self.assertEqual(self.run_at(2030, 3, 14, 7, 0), (1, 0))     # still in stock at 7am: sent
        make_listing(self.box, self.other, price="150.00")
        self.assertEqual(self.run_at(2030, 3, 14, 7, 30), (0, 0))    # within the hour since the last
        self.assertEqual(self.run_at(2030, 3, 14, 8, 0), (1, 0))
        self.assertEqual(len(self.send.call_args_list), 2)

    def test_scalper_prices_and_marketplaces_are_not_news(self):
        DropAlert.objects.update(day_sent_at=uk(2030, 3, 12, 8), restock_seen=[])
        # The ETB's RRP is £54.99: £69.99 is over a quarter more.
        Listing.objects.filter(pk=self.etb_listing.pk).update(availability=Listing.Availability.IN_STOCK,
                                                              price=Decimal("69.99"))
        make_listing(self.box, self.ebay, price="140.00")
        self.assertEqual(self.run_at(2030, 3, 13, 10), (0, 0))
        # At £68.70, just under a quarter over, it is fair.
        Listing.objects.filter(pk=self.etb_listing.pk).update(price=Decimal("68.70"))
        self.assertEqual(self.run_at(2030, 3, 13, 10, 15), (1, 0))

    def test_a_first_look_after_signing_up_late_is_not_news(self):
        DropAlert.objects.update(day_sent_at=None, restock_seen=None)
        self.stock(self.etb_listing)
        # The drop day passed before it was confirmed: what is in stock then is noted, not sent.
        self.assertEqual(self.run_at(2030, 3, 14, 10), (0, 0))
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.restock_seen, [self.etb.pk])
        make_listing(self.box, self.other, price="150.00")
        self.assertEqual(self.run_at(2030, 3, 14, 10, 15), (1, 0))

    def test_a_reminder_without_restocks_ends_on_the_day(self):
        DropAlert.objects.update(restocks=False, restocks_asked=False)
        self.assertEqual(self.run_at(2030, 3, 12, 8, 0), (1, 0))
        self.assertIn("This was your last reminder", self.send.call_args.args[2])
        self.assertFalse(DropAlert.objects.exists())

    def test_restocks_end_after_their_days(self):
        DropAlert.objects.update(day_sent_at=uk(2030, 3, 12, 8), restock_seen=[])
        self.run_at(2030, 4, 11, 10)
        self.assertTrue(DropAlert.objects.exists())   # day 30
        self.stock(self.etb_listing)
        self.assertEqual(self.run_at(2030, 4, 13, 10), (0, 0))
        self.assertFalse(DropAlert.objects.exists())

    def test_a_failed_send_is_handed_back(self):
        from catalogue.mail import MailError

        DropAlert.objects.update(day_sent_at=uk(2030, 3, 12, 8), restock_seen=[])
        self.stock(self.etb_listing)
        self.send.side_effect = MailError("down")
        self.assertEqual(self.run_at(2030, 3, 13, 10), (0, 1))
        self.alert.refresh_from_db()
        self.assertEqual((self.alert.restock_seen, self.alert.restock_sent_at), ([], None))
        self.send.side_effect = None
        self.assertEqual(self.run_at(2030, 3, 13, 10, 15), (1, 0))

    def test_the_emails_keep_the_house_style(self):
        self.stock(self.etb_listing)
        self.run_at(2030, 3, 12, 8, 0)
        make_listing(self.box, self.other, price="150.00")
        self.run_at(2030, 3, 12, 9, 30)
        self.assertEqual(len(self.send.call_args_list), 2)
        for call in self.send.call_args_list:
            for part in call.args[2:]:
                self.assertNotIn("—", part)
                self.assertNotIn("!", part.replace("<!", ""))
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, part.lower())


@override_settings(**MAIL_ON)
class EveryAlertSignUpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.today = timezone.localdate()
        game = make_game()
        self.set = make_set(game, name="Delta Reign", slug="delta-reign", release_date=self.today + timedelta(days=6))
        make_product(self.set, name="Delta Reign Elite Trainer Box", slug="dri-etb")
        patcher = mock.patch("catalogue.mail.send")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, every=True, json=False, **extra):
        headers = {"HTTP_ACCEPT": "application/json"} if json else {}
        fields = {"email": "ben@example.com", "from": "home", **extra}
        if every:
            fields["every"] = "1"
        return self.client.post(reverse("web:drop_ask", args=[self.set.pk]), fields, **headers)

    def test_one_confirm_link_turns_on_every_alert(self):
        response = self.ask()
        self.assertRedirects(response, reverse("web:drop_ask", args=[self.set.pk]) + "?state=sent&from=home",
                             fetch_redirect_response=False)
        # The page it comes back to leads home, to the spotlight.
        self.assertContains(self.client.get(response.url), 'href="/#drop-spotlight"')
        alert = DropAlert.objects.get()
        self.assertEqual((alert.restocks_asked, alert.restocks), (True, False))
        to, subject, text, html = self.send.call_args.args
        self.assertEqual(subject, "Confirm every alert for Delta Reign")
        self.assertIn("for 30 days after, whenever a UK shop has one of its products in stock again", text)
        self.assertIn("Confirm my alerts", html)
        page = self.client.get(reverse("web:drop_confirm", args=[alert.token]))
        self.assertContains(page, "for 30 days after whenever a UK shop has one of its products in stock again")
        alert.refresh_from_db()
        self.assertTrue(alert.confirmed_at and alert.restocks)

    def test_a_confirmed_reminder_is_only_upgraded_by_its_own_link(self):
        alert = DropAlert.objects.create(product_set=self.set, email="ben@example.com",
                                         confirmed_at=timezone.now() - timedelta(days=1))
        self.ask()
        alert.refresh_from_db()
        # A stranger typing the address turns nothing on: only the link emailed to it does.
        self.assertEqual((alert.restocks_asked, alert.restocks), (True, False))
        self.assertEqual(self.send.call_args.args[1], "Confirm every alert for Delta Reign")
        self.client.get(reverse("web:drop_confirm", args=[alert.token]))
        alert.refresh_from_db()
        self.assertTrue(alert.restocks)
        # Asked again once on: nothing more is sent.
        self.send.reset_mock()
        self.ask()
        self.send.assert_not_called()

    def test_after_the_drop_only_every_alert_is_taken(self):
        self.set.release_date = self.today - timedelta(days=3)
        self.set.save()
        self.assertEqual(self.ask(every=False).status_code, 404)
        self.assertEqual(self.ask(json=True).json()["state"], "sent")
        self.assertIn("for 30 days after Delta Reign came out", self.send.call_args.args[2])
        alert = DropAlert.objects.get()
        self.client.get(reverse("web:drop_confirm", args=[alert.token]))
        self.assertContains(self.client.get(reverse("web:drop_confirm", args=[alert.token])),
                            "We will email you for 30 days whenever a UK shop has a Delta Reign product")
        # Kept while its restock days run, not deleted as a passed drop.
        drop_alerts.tidy(timezone.now())
        self.assertTrue(DropAlert.objects.exists())

    def test_once_the_restock_days_are_up_nothing_is_taken(self):
        self.set.release_date = self.today - timedelta(days=drop_alerts.RESTOCK_DAYS + 1)
        self.set.save()
        self.assertEqual(self.ask().status_code, 404)
        self.assertFalse(DropAlert.objects.exists())

    def test_the_drop_reminders_own_form_is_unchanged(self):
        self.ask(every=False)
        alert = DropAlert.objects.get()
        self.assertFalse(alert.restocks_asked)
        self.assertEqual(self.send.call_args.args[1], "Confirm your reminder for Delta Reign")
