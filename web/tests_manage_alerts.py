"""Your alerts: the watchlist page's box that emails a link, the page the link opens, and the link at the foot of
every alert email (catalogue/manage_alerts.py)."""

import re
import time
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core import signing
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import alerts, drop_alerts, mail, manage_alerts, search_alerts
from catalogue.models import (
    DailyPageView, DropAlert, Listing, Product, Release, SearchAlert, SearchAlertSent, StockAlert,
)
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .tests import CopyStyleTests

MAIL_ON = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com",
           "RIPRAPTOR_SITE_URL": "https://ripraptor.com"}
NEUTRAL = "If that address has alerts, we have emailed a link to manage them."
TOKEN_IN = re.compile(r"/alerts/manage/([^/\s\"<]+)/")


def tokens_in(text):
    return TOKEN_IN.findall(text)


class AlertsData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.game = make_game()
        cls.magic = make_game("Magic: The Gathering", "magic-the-gathering", short_name="Magic")
        cls.shop = make_retailer("Harbour Games")
        cls.dated = make_set(cls.game, name="Ascended Heroes", slug="ascended-heroes", code="ASC",
                             release_date=cls.today + timedelta(days=9))
        cls.month_only = make_set(cls.game, name="Phantasmal Flames", slug="phantasmal-flames", code="PFL")
        cls.month = (cls.today.replace(day=1) + timedelta(days=40)).replace(day=1)
        Release.objects.create(game=cls.game, name="Phantasmal Flames", release_date=cls.month,
                               precision=Release.Precision.MONTH, source="pokemon", official=True,
                               status=Release.Status.ACCEPTED, product_set=cls.month_only)
        cls.bundle = make_product(cls.dated, name="Ascended Heroes Booster Bundle", slug="asc-bundle",
                                  product_type="bundle")
        cls.etb = make_product(cls.dated, name="Ascended Heroes Elite Trainer Box", slug="asc-etb")
        cls.box = make_product(cls.dated, name="Ascended Heroes Booster Box", slug="asc-box", product_type="booster_box")
        make_listing(cls.bundle, cls.shop, price="29.99", availability=Listing.Availability.OUT_OF_STOCK)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def all_kinds(self, email="ben@example.com"):
        """One alert of every kind for ``email``: back in stock (unconfirmed), Track price (confirmed, a month),
        Track price (unconfirmed), a drop reminder with a day, one with only a month, and a search alert."""
        now = timezone.now()
        return {
            "back": StockAlert.objects.create(product=self.bundle, email=email),
            "track": StockAlert.objects.create(product=self.etb, email=email, max_price=Decimal("45.00"),
                                               watch_days=30, confirmed_at=now),
            "track_waiting": StockAlert.objects.create(product=self.box, email=email, max_price=Decimal("120.00"),
                                                       watch_days=91),
            "drop": DropAlert.objects.create(product_set=self.dated, email=email, confirmed_at=now),
            "month": DropAlert.objects.create(product_set=self.month_only, email=email, confirmed_at=now),
            "search": SearchAlert.objects.create(email=email, game=self.game, product_type=Product.Type.BOOSTER_PACK,
                                                 language="en", max_price=Decimal("5.00"), confirmed_at=now),
        }

    def manage_url(self, email="ben@example.com", emailed=False):
        return reverse("web:alerts_manage", args=[manage_alerts.token_for(email, emailed)])


# The box on the watchlist page --------------------------------------------------------------------

@override_settings(**MAIL_ON)
class AskTests(AlertsData):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("catalogue.mail.send")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, email="ben@example.com", json=False, ip=None, **data):
        meta = {"HTTP_ACCEPT": "application/json"} if json else {}
        if ip:
            meta["HTTP_X_FORWARDED_FOR"] = ip
        return self.client.post(reverse("web:manage_ask"), {"email": email, **data}, **meta)

    def test_the_box_is_on_the_watchlist_page_as_a_plain_form(self):
        html = self.client.get(reverse("web:watchlist")).content.decode()
        self.assertIn('id="manage"', html)
        self.assertIn("Your alerts", html)
        self.assertIn("Email me a link", html)
        self.assertIn(f'action="{reverse("web:manage_ask")}#manage"', html)
        box = html[html.index('id="manage"'):]
        self.assertIn('method="post"', box)
        self.assertIn("csrfmiddlewaretoken", box)
        self.assertIn('name="website"', box)
        self.assertIn('type="email" name="email"', box)
        self.assertIn("button button--quiet button--small", box)
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertNotContains(self.client.get(reverse("web:watchlist")), 'id="manage"')

    def test_known_and_unknown_addresses_get_the_same_answer_and_only_a_known_one_an_email(self):
        StockAlert.objects.create(product=self.bundle, email="ben@example.com")   # not confirmed: still counts
        known, unknown = self.ask(" Ben@Example.com "), self.ask("nobody@example.com")
        self.assertEqual(known.status_code, 302)
        self.assertEqual(known.url, unknown.url)
        self.assertEqual(known.url, reverse("web:watchlist") + "?manage=sent#manage")
        self.assertNotIn("ben", known.url)
        page = self.client.get(known.url).content.decode()
        self.assertIn("Check your inbox", page)
        self.assertIn(NEUTRAL, page)
        self.assertIn("Use another address", page)
        self.assertEqual(self.ask("ben@example.com", json=True).json(),
                         self.ask("someone@example.com", json=True).json())
        self.assertEqual(self.ask("x@example.com", json=True).json(),
                         {"state": "sent", "title": "Check your inbox", "message": NEUTRAL})
        self.assertEqual([c.args[0] for c in self.send.call_args_list], ["ben@example.com", "ben@example.com"])

    def test_every_kind_of_alert_counts(self):
        DropAlert.objects.create(product_set=self.dated, email="drop@example.com")
        SearchAlert.objects.create(email="search@example.com", game=self.game)
        StockAlert.objects.create(product=self.etb, email="track@example.com", max_price=Decimal("40.00"), watch_days=7)
        for email in ("drop@example.com", "search@example.com", "track@example.com"):
            self.ask(email)
        self.assertEqual([c.args[0] for c in self.send.call_args_list],
                         ["drop@example.com", "search@example.com", "track@example.com"])

    def test_the_email_links_to_the_page_for_an_hour(self):
        StockAlert.objects.create(product=self.bundle, email="ben@example.com")
        self.ask()
        to, subject, text, html = self.send.call_args.args
        self.assertEqual((to, subject), ("ben@example.com", "Your RipRaptor alerts"))
        self.assertEqual(self.send.call_args.kwargs.get("unsubscribe", ""), "")
        self.assertIn("It works for an hour.", text)
        self.assertIn("See my alerts", html)
        self.assertIn("If this was not you, ignore this email and nothing changes.", text)
        self.assertIn('/static/img/logo.png" width="180" alt="RipRaptor"', html)
        # No Unsubscribe button: this email is not an alert, and the page it opens has the Stop buttons.
        self.assertNotIn(">Unsubscribe</a>", html)
        self.assertNotIn("Manage all your alerts", html)
        token = tokens_in(text)[0]
        self.assertEqual(tokens_in(html)[0], token)
        self.assertIn(f"https://ripraptor.com/alerts/manage/{token}/", text)
        self.assertEqual(manage_alerts.email_for(token), "ben@example.com")
        self.assertNotIn("ben@example.com", self.ask().url)
        with mock.patch("django.core.signing.time.time", return_value=time.time() + manage_alerts.ASKED_MAX_AGE + 5):
            self.assertIsNone(manage_alerts.email_for(token))

    def test_a_link_is_asked_for_by_one_connection_ten_times_an_hour(self):
        for n in range(manage_alerts.ASKS_PER_ADDRESS_HOUR):
            self.assertEqual(self.ask(f"p{n}@example.com").url, reverse("web:watchlist") + "?manage=sent#manage")
        StockAlert.objects.create(product=self.bundle, email="late@example.com")
        busy = self.ask("late@example.com")
        self.assertEqual(busy.url, reverse("web:watchlist") + "?manage=busy#manage")
        page = self.client.get(busy.url).content.decode()
        self.assertIn("Too many links were asked for from here just now. Try again in an hour.", page)
        self.assertNotIn(NEUTRAL, page)
        data = self.ask("late@example.com", json=True).json()
        self.assertEqual(data["state"], "busy")
        self.send.assert_not_called()
        # Another connection is not held up.
        self.ask("late@example.com", ip="10.0.0.9")
        self.assertEqual(self.send.call_count, 1)

    def test_one_inbox_gets_few_links_however_many_connections_ask(self):
        StockAlert.objects.create(product=self.bundle, email="victim@example.com")
        for n in range(6):
            response = self.ask("victim@example.com", json=True, ip=f"10.0.0.{n}")
            self.assertEqual(response.json()["state"], "sent")
        self.assertEqual(self.send.call_count, manage_alerts.LINKS_PER_EMAIL_DAY)

    def test_the_counts_are_one_way_hashes(self):
        self.ask("ben@example.com", ip="192.0.2.7")
        keys = list(cache._cache.keys())   # the local memory cache the tests use
        self.assertTrue(keys)
        for key in keys:
            self.assertNotIn("ben@example.com", key)
            self.assertNotIn("192.0.2.7", key)

    def test_honeypot(self):
        StockAlert.objects.create(product=self.bundle, email="ben@example.com")
        self.assertEqual(self.ask(website="spam").url, reverse("web:watchlist"))
        self.send.assert_not_called()
        self.assertFalse(DailyPageView.objects.filter(key="manage_asked").exists())

    def test_a_bad_address_comes_back_at_the_box(self):
        response = self.ask("not an email", p="a-slug")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "That email address does not look right.", status_code=400)
        self.assertContains(response, 'value="not an email"', status_code=400)
        self.assertContains(response, 'name="p" value="a-slug"', status_code=400)
        self.assertEqual(self.ask("nope", json=True).status_code, 400)
        self.send.assert_not_called()

    def test_a_failed_send_still_gets_the_same_answer(self):
        StockAlert.objects.create(product=self.bundle, email="ben@example.com")
        self.send.side_effect = mail.MailError("down")
        with self.assertLogs("catalogue.manage_alerts", "WARNING"):
            self.assertEqual(self.ask(json=True).json()["message"], NEUTRAL)

    def test_the_saved_list_is_kept(self):
        response = self.ask(p="a-slug,b-slug")
        self.assertEqual(response.url, reverse("web:watchlist") + "?p=a-slug,b-slug&manage=sent#manage")
        page = self.client.get(response.url).content.decode()
        self.assertIn('href="/watchlist/?p=a-slug,b-slug#manage"', page)

    def test_get_is_not_allowed_and_the_csrf_token_is_needed(self):
        self.assertEqual(self.client.get(reverse("web:manage_ask")).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse("web:manage_ask"), {"email": "a@example.com"}).status_code, 403)

    def test_counted_like_the_other_alerts(self):
        StockAlert.objects.create(product=self.bundle, email="ben@example.com")
        self.ask()
        self.ask("nobody@example.com")
        events = dict(DailyPageView.objects.filter(kind="alerts").values_list("key", "hits"))
        self.assertEqual(events, {"manage_asked": 2, "manage_sent": 1})


# The page the link opens ---------------------------------------------------------------------------

@override_settings(**MAIL_ON)
class PageTests(AlertsData):
    def test_it_lists_every_kind_for_that_address_only_in_plain_words(self):
        mine = self.all_kinds()
        theirs = self.all_kinds("someone@example.com")
        page = self.client.get(self.manage_url())
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("Every alert for ben@example.com.", html)
        self.assertNotIn("someone@example.com", html)
        for heading in ("Back in stock", "Track price", "Drop reminders", "Search alerts"):
            self.assertIn(f">{heading}</h2>", html)
        self.assertLess(html.index(">Back in stock<"), html.index(">Track price<"))
        self.assertLess(html.index(">Track price<"), html.index(">Drop reminders<"))
        self.assertLess(html.index(">Drop reminders<"), html.index(">Search alerts<"))
        self.assertIn(f'href="{self.bundle.get_absolute_url()}">Ascended Heroes Booster Bundle</a>', html)
        ends = timezone.localtime(mine["track"].confirmed_at + timedelta(days=30)).date()
        self.assertIn(f"At or under £45.00 · Until {ends.day} {ends:%b %Y}", html)
        self.assertIn("At or under £120.00", html)
        self.assertEqual(html.count("Waiting for you to confirm"), 2)   # the back in stock alert and one Track price
        day = self.dated.release_date
        self.assertIn(f"Pokémon · {day:%a} {day.day} {day:%b} · Out in 9 days", html)
        self.assertIn(f"Pokémon · {self.month:%B %Y} · Day not announced yet", html)
        self.assertIn(f'href="/drops/#drop-{self.dated.pk}"', html)
        self.assertIn("English Pokémon booster packs up to £5.00 delivered, in stock", html)
        self.assertIn('href="/search/?game=pokemon&amp;type=booster_pack&amp;lang=en&amp;in_stock=on&amp;sort=price"', html)
        for alert in mine.values():
            kind = "drop" if isinstance(alert, DropAlert) else "search" if isinstance(alert, SearchAlert) else "stock"
            self.assertIn(f'name="stop" value="{kind}-{alert.pk}"', html)
        self.assertNotIn(f'value="stock-{theirs["back"].pk}"', html)
        self.assertNotIn(f'value="drop-{theirs["drop"].pk}"', html)
        self.assertNotIn(f'value="search-{theirs["search"].pk}"', html)

    def test_an_emailed_link_works_for_a_week(self):
        self.all_kinds()
        later = time.time() + manage_alerts.ASKED_MAX_AGE + 60
        asked, emailed = self.manage_url(), self.manage_url(emailed=True)
        with mock.patch("django.core.signing.time.time", return_value=later):
            self.assertContains(self.client.get(asked), "This link has expired")
            self.assertContains(self.client.get(emailed), "Every alert for ben@example.com.")
        with mock.patch("django.core.signing.time.time", return_value=time.time() + manage_alerts.EMAILED_MAX_AGE + 60):
            self.assertContains(self.client.get(emailed), "This link has expired")

    def test_a_bad_or_old_link_shows_the_box_for_a_new_one_and_stops_nothing(self):
        mine = self.all_kinds()
        good = manage_alerts.token_for("ben@example.com")
        forged = signing.dumps({"e": "ben@example.com"}, salt="other")
        for token in ("nonsense", good[:-2] + ("AA" if not good.endswith("AA") else "BB"), forged):
            url = reverse("web:alerts_manage", args=[token])
            with self.subTest(token=token):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "This link has expired")
                self.assertContains(page, "A link from this box works for an hour, and one from an alert email for a week.")
                self.assertContains(page, f'action="{reverse("web:manage_ask")}#manage"')
                self.assertContains(page, "Email me a link")
                self.assertNotContains(page, "ben@example.com")
                self.client.post(url, {"stop": "all"})
                self.client.post(url, {"stop": f"stock-{mine['back'].pk}"})
        self.assertEqual(StockAlert.objects.count() + DropAlert.objects.count() + SearchAlert.objects.count(), 6)

    def test_nothing_left_says_so_and_links_to_the_watchlist(self):
        page = self.client.get(self.manage_url()).content.decode()
        self.assertIn("There are no alerts for this address.", page)
        self.assertIn(f'href="{reverse("web:watchlist")}"', page)
        self.assertIn("Back to the watchlist", page)
        self.assertNotIn('value="all"', page)

    def test_it_is_private(self):
        self.all_kinds()
        with self.settings(RIPRAPTOR_GA_ID="G-TEST123", RIPRAPTOR_ADSENSE_CLIENT="ca-pub-1", RIPRAPTOR_AWIN_PUBLISHER_ID="999"):
            for url in (self.manage_url(), reverse("web:alerts_manage", args=["expired-or-mistyped"])):
                response = self.client.get(url)
                html = response.content.decode()
                with self.subTest(url=url):
                    self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
                    self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
                    self.assertEqual(response["Cache-Control"], "no-store")
                    self.assertIn('<meta name="referrer" content="strict-origin">', html)
                    for marker in ("analytics.js", "G-TEST123", "data-consent", "googlesyndication", "dwin2.com",
                                   "googletagmanager", "refer-card"):
                        self.assertNotIn(marker, html)
            # A public page still carries them, so the switch is the address and not the settings.
            self.assertIn("dwin2.com", self.client.get(reverse("web:about")).content.decode())
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /alerts/")

    def test_the_server_and_the_script_agree_it_is_private(self):
        from django.contrib.staticfiles import finders

        from web.context_processors import NO_REFER_CARD, PRIVATE_LINK

        path = reverse("web:alerts_manage", args=["abc"])
        self.assertTrue(PRIVATE_LINK.match(path))
        self.assertTrue(NO_REFER_CARD.match(path))
        script = open(finders.find("js/analytics.js"), encoding="utf-8").read()
        self.assertIn(PRIVATE_LINK.pattern.replace("/", r"\/"), script)

    def test_query_count_does_not_grow_with_the_alerts(self):
        self.all_kinds()

        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(self.manage_url()).status_code, 200)
            return len(queries)

        few = count()
        for n in range(4):
            extra = make_set(self.game, name=f"Extra {n}", slug=f"extra-{n}", release_date=self.today + timedelta(days=20 + n))
            product = make_product(extra, name=f"Extra {n} Tin", slug=f"extra-{n}-tin", product_type="tin")
            StockAlert.objects.create(product=product, email="ben@example.com", max_price=Decimal("9.00"),
                                      confirmed_at=timezone.now())
            DropAlert.objects.create(product_set=extra, email="ben@example.com")
            SearchAlert.objects.create(email="ben@example.com", game=self.magic, max_price=Decimal(10 + n))
        self.assertEqual(count(), few)
        self.assertLessEqual(few, 4)   # the wording, then one each for the three kinds


# Stopping -------------------------------------------------------------------------------------------

@override_settings(**MAIL_ON)
class StopTests(AlertsData):
    def stop(self, value, email="ben@example.com", follow=False):
        return self.client.post(self.manage_url(email), {"stop": value}, follow=follow)

    def left(self, email):
        return (StockAlert.objects.filter(email=email).count() + DropAlert.objects.filter(email=email).count()
                + SearchAlert.objects.filter(email=email).count())

    def test_stop_deletes_that_alert_only_and_says_so(self):
        mine = self.all_kinds()
        self.all_kinds("someone@example.com")
        for name in ("back", "track", "drop", "search"):
            alert = mine[name]
            kind = "drop" if name == "drop" else "search" if name == "search" else "stock"
            response = self.stop(f"{kind}-{alert.pk}")
            with self.subTest(name=name):
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, self.manage_url() + "?done=one")
                self.assertFalse(type(alert).objects.filter(pk=alert.pk).exists())
        self.assertEqual(self.left("ben@example.com"), 2)
        self.assertEqual(self.left("someone@example.com"), 6)
        page = self.client.get(self.manage_url() + "?done=one").content.decode()
        self.assertIn('role="status">Alert stopped and deleted.</p>', page)
        self.assertNotIn("Ascended Heroes Booster Bundle", page)

    def test_a_link_never_stops_another_address_alert(self):
        theirs = self.all_kinds("someone@example.com")
        self.all_kinds()
        for name, alert in theirs.items():
            kind = "drop" if isinstance(alert, DropAlert) else "search" if isinstance(alert, SearchAlert) else "stock"
            response = self.stop(f"{kind}-{alert.pk}")
            with self.subTest(name=name):
                self.assertEqual(response.url, self.manage_url())   # nothing happened, so nothing is said
        for wrong in ("stock-", "stock-abc", "nope-1", "", "drop-99999", "stock-1-2"):
            self.stop(wrong)
        self.assertEqual(self.left("someone@example.com"), 6)
        self.assertEqual(self.left("ben@example.com"), 6)

    def test_stop_all_deletes_every_alert_for_the_address_and_nothing_else(self):
        mine = self.all_kinds()
        SearchAlertSent.objects.create(alert=mine["search"], product=self.box, sent_at=timezone.now())
        self.all_kinds("someone@example.com")
        response = self.stop("all")
        self.assertEqual(response.url, self.manage_url() + "?done=all")
        self.assertEqual(self.left("ben@example.com"), 0)
        self.assertFalse(SearchAlertSent.objects.exists())
        self.assertEqual(self.left("someone@example.com"), 6)
        page = self.client.get(response.url).content.decode()
        self.assertIn("All your alerts are stopped and deleted.", page)
        self.assertIn("There are no alerts for this address.", page)
        self.assertIn(f'href="{reverse("web:watchlist")}"', page)
        # Pressed again, there is nothing to stop and nothing to say.
        self.assertEqual(self.stop("all").url, self.manage_url())
        events = dict(DailyPageView.objects.filter(kind="alerts").values_list("key", "hits"))
        self.assertEqual(events, {"manage_stopped_all": 1})

    def test_the_buttons_are_plain_forms_and_need_the_csrf_token(self):
        mine = self.all_kinds()
        html = self.client.get(self.manage_url()).content.decode()
        action = f'action="{self.manage_url()}"'
        self.assertEqual(html.count(action), 7)   # Stop all and one for each of the six alerts
        self.assertEqual(html.count('method="post"'), 7)
        self.assertEqual(html.count("csrfmiddlewaretoken"), 7)
        self.assertIn('class="button button--quiet button--small" type="submit" name="stop" value="all">Stop all</button>', html)
        self.assertIn(f'value="stock-{mine["back"].pk}">Stop<span class="visually-hidden">: Ascended Heroes Booster Bundle</span>', html)
        self.assertNotIn("<script src=\"/static/js/search-alert.js", html)
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self.manage_url(), {"stop": "all"}).status_code, 403)
        self.assertEqual(self.client.get(self.manage_url() + "?stop=all").status_code, 200)   # a GET stops nothing
        self.assertEqual(self.left("ben@example.com"), 6)
        self.assertEqual(self.client.put(self.manage_url()).status_code, 405)
        self.stop(f"stock-{mine['back'].pk}")
        events = dict(DailyPageView.objects.filter(kind="alerts").values_list("key", "hits"))
        self.assertEqual(events, {"manage_stopped": 1})

    def test_case_and_spaces_in_the_address_do_not_matter(self):
        self.all_kinds()
        self.assertContains(self.client.get(self.manage_url(" BEN@Example.com ")), "Every alert for ben@example.com.")
        self.client.post(self.manage_url("Ben@Example.com"), {"stop": "all"})
        self.assertEqual(self.left("ben@example.com"), 0)


# The link at the foot of every alert email -------------------------------------------------------

@override_settings(**MAIL_ON)
class EmailLinkTests(AlertsData):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("catalogue.mail.send")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def assert_manage_link(self, call, email="ben@example.com"):
        to, subject, text, html = call.args
        self.assertEqual(to, email)
        self.assertIn("Manage all your alerts", text)
        self.assertIn("Manage all your alerts</a>", html)
        token = tokens_in(text)[0]
        self.assertEqual(tokens_in(html), [token])
        self.assertIn(f"https://ripraptor.com/alerts/manage/{token}/", html)
        self.assertEqual(manage_alerts.email_for(token), email)
        # A week's link, not an hour's.
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 6 * 86400):
            self.assertEqual(manage_alerts.email_for(token), email)
        # The stop link is still there.
        self.assertIn("/alerts/stop/", text)
        self.assertIn("/alerts/stop/", html)

    def test_back_in_stock_confirmation_and_email(self):
        alerts.ask(self.bundle, "Ben@Example.com")
        self.assert_manage_link(self.send.call_args)
        StockAlert.objects.update(confirmed_at=timezone.now())
        Listing.objects.filter(product=self.bundle).update(availability="in_stock", last_checked=timezone.now())
        self.send.reset_mock()
        self.assertEqual(alerts.send_due(), (1, 0))
        call = self.send.call_args
        self.assertEqual(call.args[1], "Back in stock: Ascended Heroes Booster Bundle")
        self.assertIn("Manage all your alerts", call.args[2])
        self.assertEqual(manage_alerts.email_for(tokens_in(call.args[2])[0]), "ben@example.com")

    def test_track_price_confirmation_and_email(self):
        make_listing(self.etb, self.shop, price="60.00", delivery="0.00")
        alerts.ask(self.etb, "ben@example.com", max_price=Decimal("50.00"), days=30)
        self.assert_manage_link(self.send.call_args)
        StockAlert.objects.update(confirmed_at=timezone.now())
        Listing.objects.filter(product=self.etb).update(price=Decimal("45.00"), last_checked=timezone.now())
        self.send.reset_mock()
        self.assertEqual(alerts.send_due(), (1, 0))
        self.assertTrue(self.send.call_args.args[1].startswith("Under your price"))
        self.assertIn("Manage all your alerts", self.send.call_args.args[2])
        self.assertIn("Manage all your alerts</a>", self.send.call_args.args[3])

    def test_drop_reminder_confirmation_and_email(self):
        drop_alerts.ask(self.dated, "ben@example.com")
        self.assert_manage_link(self.send.call_args)
        alert = DropAlert.objects.get()
        alert.confirmed_at = timezone.now()
        subject, text, html, stop_url = drop_alerts.stage_mail(alert, drop_alerts.EVE, [], {})
        self.assertIn("Manage all your alerts", text)
        self.assertIn("Manage all your alerts</a>", html)
        self.assertEqual(manage_alerts.email_for(tokens_in(text)[0]), "ben@example.com")

    def test_search_alert_confirmation_and_email(self):
        search_alerts.ask("ben@example.com", self.game, Product.Type.BOOSTER_BOX)
        self.assert_manage_link(self.send.call_args)
        SearchAlert.objects.update(confirmed_at=timezone.now() - timedelta(days=1))
        make_listing(self.box, self.shop, price="120.00", delivery="0.00")
        self.send.reset_mock()
        self.assertEqual(search_alerts.send_due(), (1, 0))
        self.assert_manage_link(self.send.call_args)

    def test_every_alert_email_template_carries_it_and_others_do_not(self):
        listing = make_listing(self.box, self.shop, price="120.00")
        context = {"product": self.box, "listing": listing, "max_price": Decimal("130.00"), "product_url": "u",
                   "stop_url": "https://ripraptor.com/alerts/stop/x/", "values": {"set": "S", "site_name": "RipRaptor"},
                   "product_set": self.dated, "items": [], "offers": [], "stage": "eve",
                   "manage_url": "https://ripraptor.com/alerts/manage/TOKEN/"}
        for template in ("confirm", "back_in_stock", "price_drop", "preorder_open", "drop_confirm", "drop_reminder",
                         "search_confirm", "search_match"):
            text, html = alerts.render_mail(template, dict(context), {})
            with self.subTest(template=template):
                self.assertIn("Manage all your alerts: https://ripraptor.com/alerts/manage/TOKEN/", text)
                self.assertIn('<a href="https://ripraptor.com/alerts/manage/TOKEN/" style="color:#cc4410;">Manage all your alerts</a>', html)
                self.assertNotIn("—", text + html)
        # Emails that are not alerts have no such link.
        text, html = alerts.render_mail("test", {"subject": "Test"}, {})
        self.assertNotIn("Manage all your alerts", text + html)
        self.assertIn(">Unsubscribe</a>", html)


# The terms and the house style ----------------------------------------------------------------------

@override_settings(**MAIL_ON)
class WordingTests(AlertsData):
    def test_the_terms_say_it(self):
        self.assertContains(self.client.get(reverse("web:terms")),
                            "You can see and stop all your alerts from the watchlist page, using a link we email you")

    def test_the_pages_keep_the_house_style(self):
        self.all_kinds()
        StockAlert.objects.create(product=self.bundle, email="late@example.com")
        urls = [reverse("web:watchlist"), reverse("web:watchlist") + "?manage=sent", reverse("web:watchlist") + "?manage=busy",
                self.manage_url(), self.manage_url() + "?done=one", self.manage_url() + "?done=all",
                self.manage_url("nobody@example.com"), reverse("web:alerts_manage", args=["old"])]
        for url in urls:
            html = self.client.get(url).content.decode()
            visible = re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html))
            with self.subTest(url=url):
                self.assertNotIn("!", visible)
                for mark in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(mark, visible)
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())

    @mock.patch("catalogue.mail.send")
    def test_the_email_keeps_the_house_style(self, send):
        StockAlert.objects.create(product=self.bundle, email="ben@example.com")
        self.client.post(reverse("web:manage_ask"), {"email": "ben@example.com"})
        to, subject, text, html = send.call_args.args
        for part in (subject, text, html):
            self.assertNotIn("—", part)
            self.assertNotIn("!", re.sub(r"<[^>]*>", " ", part).replace("<!doctype", ""))

    def test_dates_read_like_the_rest_of_the_site(self):
        # The six-month limit, for a Track price alert kept the usual time.
        alert = StockAlert.objects.create(product=self.etb, email="ben@example.com", max_price=Decimal("40.00"),
                                          confirmed_at=timezone.make_aware(timezone.datetime(2026, 10, 1, 12)))
        self.assertIsNone(alerts.watch_ends(alert))
        html = self.client.get(self.manage_url()).content.decode()
        ends = date(2026, 10, 1) + timedelta(days=alerts.CONFIRMED_DAYS)
        self.assertIn(f"Until {ends.day} {ends:%b %Y}", html)
