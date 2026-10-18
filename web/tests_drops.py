"""The drop tracker (/drops/) and its reminder emails."""

import re
from datetime import date, datetime, timedelta
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import drop_alerts, drops, mail
from catalogue.models import DropAlert, Listing, PreorderOpen, Release, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .tests import CopyStyleTests

UK = ZoneInfo("Europe/London")
MAIL_ON = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com"}


def uk(*args):
    return datetime(*args, tzinfo=UK)


class DropData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.game = make_game()
        cls.magic = make_game("Magic: The Gathering", "magic-the-gathering", short_name="Magic")
        cls.harbour = make_retailer("Harbour Games")
        cls.north = make_retailer("Northgate Cards")
        cls.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        cls.soon = make_set(cls.game, name="Ascended Heroes", slug="ascended-heroes", code="ASC",
                            release_date=cls.today + timedelta(days=3))
        cls.etb = make_product(cls.soon, name="Ascended Heroes Elite Trainer Box", slug="asc-etb")
        cls.cheap = make_listing(cls.etb, cls.harbour, price="49.99", availability=Listing.Availability.PREORDER)
        make_listing(cls.etb, cls.north, price="54.00", availability=Listing.Availability.PREORDER)
        make_listing(cls.etb, cls.ebay, price="20.00", availability=Listing.Availability.PREORDER)
        cls.later = make_set(cls.magic, name="Lorwyn Eclipsed", slug="lorwyn-eclipsed", code="ECL",
                             release_date=cls.today + timedelta(days=70))
        make_product(cls.later, name="Lorwyn Eclipsed Play Booster Box", slug="ecl-box")
        cls.month_only = make_set(cls.game, name="Phantasmal Flames", slug="phantasmal-flames")
        first = (cls.today.replace(day=1) + timedelta(days=40)).replace(day=1)
        cls.month = first
        Release.objects.create(game=cls.game, name="Phantasmal Flames", release_date=first,
                               precision=Release.Precision.MONTH, source="pokemon", official=True,
                               status=Release.Status.ACCEPTED, product_set=cls.month_only)
        # A pending announcement with a day: never shown, and its set has no date of its own.
        cls.rumour = make_set(cls.game, name="Rumoured Set", slug="rumoured-set")
        Release.objects.create(game=cls.game, name="Rumoured Set", release_date=cls.today + timedelta(days=5),
                               precision=Release.Precision.DAY, source="limitless",
                               status=Release.Status.PENDING, product_set=cls.rumour)
        cls.undated = make_set(cls.game, name="Old Undated", slug="old-undated")
        cls.past = make_set(cls.game, name="Journey Together", slug="journey-together",
                            release_date=cls.today - timedelta(days=5))
        jtg = make_product(cls.past, name="Journey Together Booster Box", slug="jtg-box")
        make_listing(jtg, cls.north, price="120.00")
        cls.old = make_set(cls.game, name="Surging Sparks", slug="surging-sparks",
                           release_date=cls.today - timedelta(days=40))

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)


class DropPageTests(DropData):
    def page(self, **params):
        return self.client.get(reverse("web:drops"), params).content.decode()

    def test_only_trusted_dates_are_shown(self):
        html = self.page()
        for name in ("Ascended Heroes", "Lorwyn Eclipsed", "Phantasmal Flames"):
            self.assertIn(name, html)
        # A pending announcement, an undated set and a set long out are never drops.
        for name in ("Rumoured Set", "Old Undated", "Surging Sparks"):
            self.assertNotIn(name, html)

    def test_grouped_soonest_first_with_month_only_as_its_month(self):
        html = self.page()
        self.assertLess(html.index("This week"), html.index("Ascended Heroes"))
        self.assertLess(html.index("Ascended Heroes"), html.index("Phantasmal Flames"))
        self.assertLess(html.index("Phantasmal Flames"), html.index("Lorwyn Eclipsed"))
        card = html[html.index('id="drop-%d"' % self.month_only.pk):]
        card = card[:card.index("</li>")]
        self.assertIn(f"{self.month:%B %Y}", card)
        self.assertIn("Day not announced yet", card)
        self.assertNotIn("data-countdown", card)
        soon = html[html.index('id="drop-%d"' % self.soon.pk):]
        self.assertIn("Out in 3 days", soon[:soon.index("</li>")])
        self.assertIn("data-countdown=", soon[:soon.index("</li>")])

    def test_the_group_rules(self):
        today = date(2026, 10, 28)
        made = []
        for name, day in (("A", date(2026, 10, 30)), ("B", date(2026, 11, 2)), ("C", date(2026, 11, 20))):
            made.append(drops.Drop(make_set(self.game, name=name, slug=name.lower()), day, None))
        made.append(drops.Drop(make_set(self.game, name="M", slug="m"), None, date(2026, 10, 1)))
        groups = drops.group(sorted(made, key=lambda d: (drops.month_start(d.sort_day), d.day is None, d.sort_day)), today)
        self.assertEqual([(key, [d.product_set.name for d in rows]) for key, _, rows in groups],
                         [("week", ["A", "B"]), ("month", ["M"]), ("2026-11-01", ["C"])])

    def test_cheapest_shop_price_and_shop_with_checked_time_and_go_link(self):
        html = self.page()
        card = html[html.index('id="drop-%d"' % self.soon.pk):]
        card = card[:card.index("</li>\n</ol>") if "</li>\n</ol>" in card else len(card)]
        self.assertIn("£49.99", card)
        self.assertIn("Harbour Games", card)
        self.assertIn("checked 1 hour ago", card)
        self.assertNotIn("£20.00", card)   # eBay is left out
        self.assertIn("Pre-orders open at 2 shops", card)
        self.assertIn(f'href="/go/{self.cheap.pk}/"', card)
        for tag in re.findall(r'<a[^>]+href="/go/[^>]+>', html):
            self.assertIn('rel="sponsored nofollow noopener"', tag)

    def test_month_only_set_a_shop_already_sells_is_out(self):
        product = make_product(self.month_only, name="Phantasmal Flames Booster Box", slug="pfl-box")
        make_listing(product, self.harbour, price="100.00")
        upcoming, recent = drops.tracker(self.today)
        self.assertNotIn(self.month_only.pk, [d.pk for d in upcoming + recent])
        self.assertNotIn("Phantasmal Flames", self.page())
        self.assertIsNone(drops.upcoming_drop(self.month_only, self.today))
        with self.settings(**MAIL_ON), mock.patch("catalogue.mail.send") as send:
            response = self.client.post(reverse("web:drop_ask", args=[self.month_only.pk]), {"email": "ben@example.com"})
        self.assertEqual(response.status_code, 404)
        send.assert_not_called()

    def test_month_only_on_pre_order_is_still_to_come(self):
        product = make_product(self.month_only, name="Phantasmal Flames Booster Box", slug="pfl-box")
        make_listing(product, self.harbour, price="100.00", availability=Listing.Availability.PREORDER)
        self.assertIn(self.month_only.pk, [d.pk for d in drops.tracker(self.today)[0]])

    def test_a_product_with_its_own_later_date_is_not_part_of_the_drop(self):
        later = make_product(self.soon, name="Ascended Heroes Premium Collection", slug="asc-premium",
                             release_date=self.today + timedelta(days=90))
        make_listing(later, self.harbour, price="20.00", availability=Listing.Availability.PREORDER)
        drop = drops.upcoming_drop(self.soon, self.today)
        self.assertEqual([o.product for o in drop.offers], [self.etb])
        self.assertEqual(drop.product_count, 1)
        card = self.page()
        card = card[card.index('id="drop-%d"' % self.soon.pk):]
        self.assertNotIn("Premium Collection", card[:card.index("</li>\n")])

    def test_month_only_tile_reads_month_then_year(self):
        html = self.page()
        card = html[html.index('id="drop-%d"' % self.month_only.pk):]
        tile = re.search(r'drop-card__tile[^"]*"[^>]*>(.*?)</p>', card).group(1)
        self.assertEqual(re.findall(r">([^<]+)<", tile), [f"{self.month:%b}", f"{self.month:%Y}"])

    def test_no_shop_says_so(self):
        with self.settings(**MAIL_ON):
            html = self.page()
        card = html[html.index('id="drop-%d"' % self.later.pk):]
        self.assertIn("No UK shop we check lists it yet. Ask for a reminder", card[:card.index("</form>")])

    def test_game_filter_is_a_link(self):
        html = self.page(game="magic-the-gathering")
        self.assertIn("Lorwyn Eclipsed", html)
        self.assertNotIn("Ascended Heroes", html)
        self.assertIn('href="/drops/?game=magic-the-gathering" aria-current="page"', html)
        self.assertIn("Ascended Heroes", self.page(game="nonsense"))

    def test_just_dropped(self):
        html = self.page()
        recent = html[html.index("Just dropped"):]
        self.assertIn("Journey Together", recent)
        self.assertIn("Came out 5 days ago", recent)
        self.assertIn("£120.00", recent)

    def test_form_only_when_email_is_set_up(self):
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertNotIn("data-drop-remind", self.page())
        with self.settings(**MAIL_ON):
            html = self.page()
        self.assertEqual(html.count("data-drop-remind"), 3)
        self.assertIn(f'action="/drops/remind/{self.soon.pk}/"', html)

    def test_query_count_stays_flat(self):
        self.page()
        with CaptureQueriesContext(connection) as few:
            self.page()
        for n in range(4):
            extra = make_set(self.game, name=f"Extra {n}", slug=f"extra-{n}", release_date=self.today + timedelta(days=n + 8))
            product = make_product(extra, name=f"Extra {n} Booster Box", slug=f"extra-{n}-box")
            make_listing(product, self.harbour, availability=Listing.Availability.PREORDER)
        with CaptureQueriesContext(connection) as many:
            html = self.page()
        self.assertIn("Extra 3", html)
        self.assertEqual(len(few), len(many))
        self.assertLessEqual(len(many), 8)

    def test_house_style(self):
        with self.settings(**MAIL_ON):
            for url in (reverse("web:drops"), reverse("web:drop_ask", args=[self.soon.pk]) + "?state=sent"):
                html = self.client.get(url).content.decode()
                self.assertNotIn("!", re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html)))
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())

    def test_linked_from_header_footer_coming_soon_and_sitemap(self):
        home = self.client.get(reverse("web:games")).content.decode()
        self.assertGreaterEqual(home.count('href="/drops/"'), 2)
        self.assertIn('class="coming__tracker"><a href="/drops/"', self.client.get(reverse("web:new")).content.decode())
        self.assertIn("/drops/</loc>", self.client.get("/sitemap.xml").content.decode())

    def test_header_menu_keeps_room_for_drops(self):
        # Adding Drops pushed the tablet header wider than the screen; How it works now lives in the footer only.
        html = self.client.get(reverse("web:home")).content.decode()
        nav = html[html.index('class="masthead__nav"'):]
        nav = nav[:nav.index("</nav>")]
        self.assertIn('href="/drops/"', nav)
        self.assertNotIn('href="/about/"', nav)
        self.assertIn('href="/about/"', html[html.index('class="site-footer__nav"'):])


@override_settings(**MAIL_ON)
class ReminderSignUpTests(DropData):
    def ask(self, email="ben@example.com", product_set=None, json=False, **extra):
        headers = {"HTTP_ACCEPT": "application/json"} if json else {}
        return self.client.post(reverse("web:drop_ask", args=[(product_set or self.soon).pk]),
                                {"email": email, **extra}, **headers)

    @mock.patch("catalogue.mail.send")
    def test_js_off_flow_then_confirm_then_stop(self, send):
        response = self.ask()
        self.assertRedirects(response, reverse("web:drop_ask", args=[self.soon.pk]) + "?state=sent",
                             fetch_redirect_response=False)
        self.assertContains(self.client.get(response.url), "Check your inbox")
        alert = DropAlert.objects.get()
        self.assertIsNone(alert.confirmed_at)
        send.assert_called_once()
        to, subject, text, html = send.call_args.args
        self.assertEqual((to, subject), ("ben@example.com", "Confirm your reminder for Ascended Heroes"))
        self.assertIn(f"/alerts/confirm/drop/{alert.token}/", text)
        self.assertIn(f"/alerts/stop/drop/{alert.token}/", html)
        self.assertContains(self.client.get(reverse("web:drop_confirm", args=[alert.token])), "Reminder confirmed")
        alert.refresh_from_db()
        self.assertIsNotNone(alert.confirmed_at)
        self.assertContains(self.client.get(reverse("web:drop_stop", args=[alert.token])), "Reminder stopped")
        self.assertFalse(DropAlert.objects.exists())
        self.assertContains(self.client.get(reverse("web:drop_confirm", args=[alert.token])), "This link has expired")

    @mock.patch("catalogue.mail.send")
    def test_inline_json(self, send):
        data = self.ask(json=True).json()
        self.assertEqual(data["state"], "sent")
        self.assertIn("Ascended Heroes", data["message"])
        self.assertEqual(self.ask(email="nope", json=True).json()["state"], "invalid")

    @mock.patch("catalogue.mail.send")
    def test_duplicates(self, send):
        self.ask()
        self.ask(email=" BEN@example.com ")
        self.assertEqual(DropAlert.objects.count(), 1)
        self.assertEqual(send.call_count, 1)   # a second ask moments later sends no second confirmation
        DropAlert.objects.update(confirmed_at=timezone.now())
        # An address that already has the reminder gets the same answer as any other, and no email.
        self.assertEqual(self.ask(json=True).json()["state"], "sent")
        self.assertEqual(send.call_count, 1)

    @mock.patch("catalogue.mail.send")
    def test_rate_limits(self, send):
        for n in range(drop_alerts.ASKS_PER_ADDRESS_HOUR):
            self.ask(email=f"p{n}@example.com")
        self.assertEqual(self.ask(email="late@example.com", json=True).json()["state"], "busy")
        self.assertFalse(DropAlert.objects.filter(email="late@example.com").exists())
        cache.clear()
        for n in range(drop_alerts.MAX_PER_EMAIL):
            other = make_set(self.game, name=f"S{n}", slug=f"s{n}", release_date=self.today + timedelta(days=9))
            DropAlert.objects.create(product_set=other, email="many@example.com", confirmed_at=timezone.now())
        send.reset_mock()
        self.assertEqual(self.ask(email="many@example.com", json=True).json()["state"], "sent")
        self.assertFalse(DropAlert.objects.filter(email="many@example.com", product_set=self.soon).exists())
        send.assert_not_called()

    @mock.patch("catalogue.mail.send")
    def test_one_inbox_gets_few_confirmations_however_many_connections_ask(self, send):
        sets = [make_set(self.game, name=f"F{n}", slug=f"f{n}", release_date=self.today + timedelta(days=9))
                for n in range(6)]
        for n, product_set in enumerate(sets):
            response = self.ask(email="victim@example.com", product_set=product_set, json=True,
                                HTTP_X_FORWARDED_FOR=f"10.0.0.{n}")
            self.assertEqual(response.json()["state"], "sent")
        self.assertEqual(send.call_count, drop_alerts.CONFIRMS_PER_EMAIL_DAY)
        self.assertEqual(DropAlert.objects.filter(email="victim@example.com").count(), drop_alerts.CONFIRMS_PER_EMAIL_DAY)
        # Asked again after the resend wait, from new connections: still nothing more today.
        later = timezone.now() + timedelta(minutes=drop_alerts.RESEND_MINUTES + 1)
        with mock.patch("catalogue.drop_alerts.timezone.now", return_value=later):
            for n, product_set in enumerate(sets):
                self.ask(email="victim@example.com", product_set=product_set, HTTP_X_FORWARDED_FOR=f"10.0.1.{n}")
        self.assertEqual(send.call_count, drop_alerts.CONFIRMS_PER_EMAIL_DAY)

    @mock.patch("catalogue.mail.send")
    def test_unconfirmed_requests_do_not_use_up_the_limit(self, send):
        for n in range(drop_alerts.MAX_PER_EMAIL):
            other = make_set(self.game, name=f"U{n}", slug=f"u{n}", release_date=self.today + timedelta(days=9))
            DropAlert.objects.create(product_set=other, email="owner@example.com")
        self.ask(email="owner@example.com")
        self.assertTrue(DropAlert.objects.filter(email="owner@example.com", product_set=self.soon).exists())
        send.assert_called_once()

    @mock.patch("catalogue.mail.send")
    def test_the_answer_never_says_what_is_known_about_an_address(self, send):
        DropAlert.objects.create(product_set=self.soon, email="victim@example.com", confirmed_at=timezone.now())
        known = self.ask(email="victim@example.com", json=True).json()
        fresh = self.ask(email="someone@example.com", json=True).json()
        self.assertEqual(known, fresh)
        self.assertEqual(self.ask(email="victim@example.com").url,
                         reverse("web:drop_ask", args=[self.soon.pk]) + "?state=sent")
        self.assertEqual([c.args[0] for c in send.call_args_list], ["someone@example.com"])
        for state in ("already", "limit"):
            self.assertRedirects(self.client.get(reverse("web:drop_ask", args=[self.soon.pk]), {"state": state}),
                                 reverse("web:drops") + f"#drop-{self.soon.pk}", fetch_redirect_response=False)

    @mock.patch("catalogue.mail.send")
    def test_honeypot_past_and_failure(self, send):
        self.ask(website="spam")
        self.assertFalse(DropAlert.objects.exists())
        self.assertEqual(self.ask(product_set=self.past).status_code, 404)
        send.side_effect = mail.MailError("down")
        self.assertEqual(self.ask(json=True).json()["state"], "failed")


@override_settings(**MAIL_ON)
class ReminderEmailTests(TestCase):
    """Each stage once, at the right UK time, with fixed clocks. Prices are checked against the real clock."""

    def setUp(self):
        game = make_game()
        self.shop = make_retailer("Harbour Games")
        self.set = make_set(game, name="Ascended Heroes", slug="ascended-heroes", release_date=date(2030, 3, 12))
        self.product = make_product(self.set, name="Ascended Heroes Elite Trainer Box", slug="asc-etb")
        self.listing = make_listing(self.product, self.shop, price="49.99", availability=Listing.Availability.PREORDER)
        self.alert = DropAlert.objects.create(product_set=self.set, email="ben@example.com",
                                              created_at=uk(2030, 3, 1, 9), confirmed_at=uk(2030, 3, 1, 9, 5))
        patcher = mock.patch("catalogue.mail.send")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def run_at(self, *when):
        return drop_alerts.send_due(now=uk(*when))

    def subjects(self):
        return [c.args[1] for c in self.send.call_args_list]

    def test_pre_order_then_day_before_then_drop_day_each_once(self):
        self.assertEqual(self.run_at(2030, 3, 5, 10), (0, 0))   # no shop has opened pre-orders yet
        PreorderOpen.objects.create(product=self.product, retailer=self.shop, listing=self.listing,
                                    at=uk(2030, 3, 6, 11), price="49.99")
        self.assertEqual(self.run_at(2030, 3, 6, 11, 15), (1, 0))
        self.assertEqual(self.run_at(2030, 3, 6, 11, 30), (0, 0))
        self.assertEqual(self.run_at(2030, 3, 11, 7, 45), (0, 0))   # before 8am UK
        self.assertEqual(self.run_at(2030, 3, 11, 8, 0), (1, 0))
        self.assertEqual(self.run_at(2030, 3, 11, 8, 15), (0, 0))
        self.assertEqual(self.run_at(2030, 3, 12, 8, 0), (1, 0))
        self.assertEqual(self.subjects(), ["Pre-orders open: Ascended Heroes", "Ascended Heroes is out tomorrow",
                                           "Ascended Heroes is out today"])
        self.assertFalse(DropAlert.objects.exists())   # deleted after the last one
        text = self.send.call_args_list[1].args[2]
        self.assertIn("£49.99 delivered at Harbour Games", text)
        self.assertIn(f"/go/{self.listing.pk}/", text)
        self.assertIn("Checked ", text)
        self.assertIn(f"/alerts/stop/drop/{self.alert.token}/", text)
        self.assertIn("This was your last reminder", self.send.call_args_list[2].args[2])
        for call in self.send.call_args_list:
            self.assertTrue(call.kwargs["unsubscribe"].endswith(f"/alerts/stop/drop/{self.alert.token}/"))

    def test_british_summer_time(self):
        self.set.release_date = date(2030, 7, 12)
        self.set.save()
        # 07:30 UTC is 08:30 in London in July.
        self.assertEqual(drop_alerts.send_due(now=datetime(2030, 7, 11, 7, 30, tzinfo=ZoneInfo("UTC"))), (1, 0))

    def test_nothing_sent_unconfirmed_and_unconfirmed_deleted_after_a_week(self):
        DropAlert.objects.update(confirmed_at=None, created_at=uk(2030, 3, 6, 9))
        self.assertEqual(self.run_at(2030, 3, 11, 9), (0, 0))
        self.assertTrue(DropAlert.objects.exists())
        self.run_at(2030, 3, 13, 10)
        self.assertFalse(DropAlert.objects.exists())

    def test_pre_orders_opened_before_confirming_are_not_news(self):
        PreorderOpen.objects.create(product=self.product, retailer=self.shop, listing=self.listing,
                                    at=uk(2030, 3, 1, 8), price="49.99")
        self.assertEqual(self.run_at(2030, 3, 6, 12), (0, 0))

    def test_pre_orders_email_names_the_shop_that_opened(self):
        # Harbour opened before the reminder was confirmed and is cheapest; Northgate opens later, dearer.
        PreorderOpen.objects.create(product=self.product, retailer=self.shop, listing=self.listing,
                                    at=uk(2030, 2, 20, 8), price="49.99")
        north = make_retailer("Northgate Cards")
        dearer = make_listing(self.product, north, price="55.00", availability=Listing.Availability.PREORDER)
        PreorderOpen.objects.create(product=self.product, retailer=north, listing=dearer, at=uk(2030, 3, 6, 11), price="55.00")
        self.assertEqual(self.run_at(2030, 3, 6, 11, 15), (1, 0))
        text = self.send.call_args.args[2]
        self.assertIn("Northgate Cards started taking pre-orders for Ascended Heroes", text)
        self.assertIn(f"/go/{dearer.pk}/", text)
        self.assertIn(f"/go/{self.listing.pk}/", text)
        self.assertNotIn("Harbour Games started", text)

    def test_an_opening_at_a_hidden_shop_or_closed_listing_is_not_news(self):
        PreorderOpen.objects.create(product=self.product, retailer=self.shop, listing=self.listing,
                                    at=uk(2030, 2, 20, 8), price="49.99")
        hidden = make_retailer("Hidden Shop", is_active=False)
        gone = make_listing(self.product, hidden, price="45.00", availability=Listing.Availability.PREORDER)
        PreorderOpen.objects.create(product=self.product, retailer=hidden, listing=gone, at=uk(2030, 3, 6, 11), price="45.00")
        closed_shop = make_retailer("Closed Shop")
        closed = make_listing(self.product, closed_shop, price="47.00", availability=Listing.Availability.OUT_OF_STOCK)
        PreorderOpen.objects.create(product=self.product, retailer=closed_shop, listing=closed, at=uk(2030, 3, 6, 11), price="47.00")
        self.assertEqual(self.run_at(2030, 3, 6, 11, 15), (0, 0))

    def test_a_product_with_its_own_later_date_stays_out_of_the_emails(self):
        later = make_product(self.set, name="Ascended Heroes Premium Collection", slug="asc-premium",
                             release_date=date(2030, 6, 1))
        later_listing = make_listing(later, self.shop, price="20.00", availability=Listing.Availability.PREORDER)
        PreorderOpen.objects.create(product=later, retailer=self.shop, listing=later_listing, at=uk(2030, 3, 6, 11), price="20.00")
        self.assertEqual(self.run_at(2030, 3, 6, 11, 15), (0, 0))   # its pre-orders are not this drop's
        self.assertEqual(self.run_at(2030, 3, 12, 9), (1, 0))
        self.assertNotIn("Premium Collection", self.send.call_args.args[2])

    def test_day_before_is_sent_again_when_the_date_moves_later(self):
        self.assertEqual(self.run_at(2030, 3, 11, 8, 9), (1, 0))
        self.set.release_date = date(2030, 3, 25)
        self.set.save()
        self.assertEqual(self.run_at(2030, 3, 12, 8, 9), (0, 0))
        self.assertEqual(self.run_at(2030, 3, 24, 8, 9), (1, 0))
        self.assertEqual(self.run_at(2030, 3, 24, 8, 24), (0, 0))
        self.assertEqual(self.run_at(2030, 3, 25, 8, 9), (1, 0))
        self.assertEqual(self.subjects(), ["Ascended Heroes is out tomorrow"] * 2 + ["Ascended Heroes is out today"])
        self.assertIn("Monday 25 March", self.send.call_args_list[1].args[2])

    def test_a_failed_day_before_after_a_move_is_handed_back(self):
        self.run_at(2030, 3, 11, 8, 9)
        self.set.release_date = date(2030, 3, 25)
        self.set.save()
        self.send.side_effect = mail.MailError("down")
        self.assertEqual(self.run_at(2030, 3, 24, 8, 9), (0, 1))
        alert = DropAlert.objects.get()
        self.assertEqual(alert.eve_for, date(2030, 3, 12))
        self.send.side_effect = None
        self.assertEqual(self.run_at(2030, 3, 24, 8, 24), (1, 0))

    def test_a_missed_window_is_skipped_and_a_passed_drop_deleted(self):
        self.assertEqual(self.run_at(2030, 3, 11, 22), (0, 0))
        self.assertEqual(self.run_at(2030, 3, 12, 9), (1, 0))
        self.assertEqual(self.subjects(), ["Ascended Heroes is out today"])
        other = DropAlert.objects.create(product_set=self.set, email="late@example.com", confirmed_at=uk(2030, 3, 1, 9))
        self.run_at(2030, 3, 13, 9)
        self.assertFalse(DropAlert.objects.filter(pk=other.pk).exists())

    def test_month_only_hears_only_about_pre_orders(self):
        self.set.release_date = None
        self.set.save()
        Release.objects.create(game=self.set.game, name=self.set.name, release_date=date(2030, 4, 1),
                               precision=Release.Precision.MONTH, source="pokemon", official=True,
                               status=Release.Status.ACCEPTED, product_set=self.set)
        self.assertEqual(self.run_at(2030, 3, 11, 9), (0, 0))
        PreorderOpen.objects.create(product=self.product, retailer=self.shop, listing=self.listing,
                                    at=uk(2030, 3, 20, 9), price="49.99")
        self.assertEqual(self.run_at(2030, 3, 20, 9, 15), (1, 0))
        self.run_at(2030, 5, 2, 9)
        self.assertFalse(DropAlert.objects.exists())

    def test_a_failed_send_is_tried_again(self):
        self.send.side_effect = mail.MailError("down")
        self.assertEqual(self.run_at(2030, 3, 11, 9), (0, 1))
        self.send.side_effect = None
        self.assertEqual(self.run_at(2030, 3, 11, 9, 15), (1, 0))

    def test_the_command(self):
        out = StringIO()
        with mock.patch("catalogue.drop_alerts.timezone.now", return_value=uk(2030, 3, 11, 9)):
            call_command("send_drop_alerts", stdout=out)
        self.assertIn("1 drop reminder emails sent, 0 failed.", out.getvalue())
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            out = StringIO()
            call_command("send_drop_alerts", stdout=out)
        self.assertIn("Nothing sent", out.getvalue())

    def test_emails_keep_the_house_style(self):
        PreorderOpen.objects.create(product=self.product, retailer=self.shop, listing=self.listing,
                                    at=uk(2030, 3, 6, 11), price="49.99")
        self.run_at(2030, 3, 6, 12)
        self.run_at(2030, 3, 11, 9)
        self.run_at(2030, 3, 12, 9)
        drop_alerts.ask(self.set, "new@example.com")
        self.assertEqual(self.send.call_count, 4)
        for call in self.send.call_args_list:
            for body in call.args[1:4]:
                self.assertNotIn("—", body)
                self.assertNotIn("!", re.sub(r"<[^>]*>", " ", body))
