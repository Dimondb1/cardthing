"""Search alerts: the watchlist page's Alert me form, its emails and the matcher (catalogue/search_alerts.py)."""

import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import mail, search_alerts
from catalogue.models import Listing, Product, Retailer, SearchAlert, SearchAlertSent
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .tests import CopyStyleTests

MAIL_ON = {"RIPRAPTOR_ZEPTOMAIL_TOKEN": "tok", "RIPRAPTOR_MAIL_FROM": "alerts@ripraptor.com"}
PACK = Product.Type.BOOSTER_PACK


class AlertData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pokemon = make_game()
        cls.magic = make_game("Magic: The Gathering", "magic-the-gathering", short_name="Magic")
        cls.one_piece = make_game("One Piece Card Game", "one-piece", short_name="One Piece")
        cls.set = make_set(cls.pokemon, name="Ascended Heroes", slug="ascended-heroes")
        cls.magic_set = make_set(cls.magic, name="Lorwyn Eclipsed", slug="lorwyn-eclipsed", code="ECL")
        cls.harbour = make_retailer("Harbour Games")
        cls.north = make_retailer("Northgate Cards", website="https://northgatecards.co.uk/",
                                  source_url="https://shop.northgate-tcg.co.uk/")
        cls.closed = make_retailer("Closed Cave", is_active=False)
        cls.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY, website="https://www.ebay.co.uk/")
        cls.amazon = make_retailer("Amazon", source_type=Retailer.Source.AMAZON, website="https://www.amazon.co.uk/")
        cls.shops = [cls.harbour, cls.north, cls.closed, cls.ebay, cls.amazon]

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def pack(self, name, price="3.50", delivery="1.00", shop=None, game_set=None, **listing):
        product = make_product(game_set or self.set, name=name, product_type=PACK)
        make_listing(product, shop or self.harbour, price=price, delivery=delivery, **listing)
        return product


# Describing an alert -------------------------------------------------------------------------------

class SummaryTests(AlertData):
    def test_reads_like_the_owner_asked_it(self):
        self.assertEqual(search_alerts.summary(self.pokemon, PACK, "en", Decimal("5")),
                         "English Pokémon booster packs up to £5.00 delivered, in stock")
        self.assertEqual(search_alerts.summary(self.magic, "booster_box", "", Decimal("120")),
                         "Magic play booster boxes up to £120.00 delivered, in stock")
        self.assertEqual(search_alerts.summary(self.one_piece, "", "other"), "One Piece products in other languages, in stock")
        self.assertEqual(search_alerts.summary(self.pokemon, "elite_trainer_box", "ja"),
                         "Japanese Pokémon Elite Trainer Boxes, in stock")

    def test_plurals(self):
        for label, plural in (("Booster pack", "booster packs"), ("Elite Trainer Box", "Elite Trainer Boxes"),
                              ("Tin", "tins"), ("Proving grounds", "proving grounds"), ("Other", "other products"),
                              ("Collector's set", "collector's sets")):
            self.assertEqual(search_alerts.plural(label), plural)


# The form ------------------------------------------------------------------------------------------

@override_settings(**MAIL_ON)
class FormTests(AlertData):
    def ask(self, json=False, **data):
        values = {"game": "pokemon", "type": PACK, "lang": "en", "max": "5", "email": "ben@example.com", **data}
        headers = {"HTTP_ACCEPT": "application/json"} if json else {}
        return self.client.post(reverse("web:search_alert_ask"), values, **headers)

    def test_the_form_is_on_the_watchlist_page_with_english_suggested(self):
        html = self.client.get(reverse("web:watchlist")).content.decode()
        self.assertIn('id="alert"', html)
        self.assertIn("Alert me", html)
        self.assertIn("Set up alert", html)
        self.assertIn('<option value="en" selected>English</option>', html)
        self.assertIn('name="website"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn('<option value="magic-the-gathering">Magic</option>', html)

    def test_no_form_while_email_is_not_set_up(self):
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertNotContains(self.client.get(reverse("web:watchlist")), 'id="alert"')

    @mock.patch("catalogue.mail.send")
    def test_prices_are_read_kindly_and_kept_in_bounds(self, send):
        self.ask(max="£4.5")
        self.assertEqual(SearchAlert.objects.get().max_price, Decimal("4.50"))
        for wrong in ("0.10", "abc", "999999", "nan", "-3"):
            response = self.ask(max=wrong, email=f"{len(wrong)}@example.com")
            with self.subTest(wrong=wrong):
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "Enter a price from £0.50 to £5,000, or leave it empty.", status_code=400)
                self.assertContains(response, "Check the choices marked below.", status_code=400)
                # The visitor's choices and address stay in the form.
                self.assertContains(response, 'value="ben@example.com"'.replace("ben", f"{len(wrong)}"), status_code=400)
        self.assertEqual(SearchAlert.objects.count(), 1)
        self.ask(max="", email="any@example.com")
        self.assertIsNone(SearchAlert.objects.get(email="any@example.com").max_price)

    @mock.patch("catalogue.mail.send")
    def test_bad_choices_are_refused(self, send):
        self.assertEqual(self.ask(game="").status_code, 400)
        self.assertEqual(self.ask(type="pogs").status_code, 400)
        self.assertEqual(self.ask(lang="xx").status_code, 400)
        self.assertEqual(self.ask(email="nope").status_code, 400)
        self.assertEqual(self.ask(email="nope", json=True).json()["state"], "invalid")
        self.assertFalse(SearchAlert.objects.exists())
        send.assert_not_called()

    @mock.patch("catalogue.mail.send")
    def test_js_off_flow_then_confirm_then_stop(self, send):
        response = self.client.post(reverse("web:search_alert_ask"), {
            "game": "pokemon", "type": PACK, "lang": "en", "max": "5", "email": "ben@example.com", "p": "a-slug,b-slug",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("web:watchlist")
                         + "?p=a-slug,b-slug&alert=sent&game=pokemon&lang=en&type=booster_pack&max=5.00#alert")
        page = self.client.get(response.url).content.decode()
        self.assertIn("Check your inbox", page)
        self.assertIn("English Pokémon booster packs up to £5.00 delivered, in stock", page)
        self.assertIn("Set up another alert", page)
        alert = SearchAlert.objects.get()
        self.assertEqual((alert.email, alert.game, alert.product_type, alert.language, alert.max_price),
                         ("ben@example.com", self.pokemon, PACK, "en", Decimal("5.00")))
        self.assertIsNone(alert.confirmed_at)
        send.assert_called_once()
        to, subject, text, html = send.call_args.args
        self.assertEqual(to, "ben@example.com")
        self.assertEqual(subject, "Confirm your alert: English Pokémon booster packs up to £5.00 delivered, in stock")
        self.assertIn(f"/alerts/confirm/search/{alert.token}/", text)
        self.assertIn(f"/alerts/stop/search/{alert.token}/", html)
        self.assertIn("deleted after six months", text)
        self.assertIn("Stop this alert", text)
        self.assertIn("Stop this alert", html)
        self.assertEqual(send.call_args.kwargs["unsubscribe"], f"https://ripraptor.com/alerts/stop/search/{alert.token}/")
        confirmed = self.client.get(reverse("web:search_alert_confirm", args=[alert.token]))
        self.assertContains(confirmed, "Alert confirmed")
        self.assertContains(confirmed, "English Pokémon booster packs up to £5.00 delivered, in stock")
        alert.refresh_from_db()
        self.assertIsNotNone(alert.confirmed_at)
        stopped = self.client.get(reverse("web:search_alert_stop", args=[alert.token]))
        self.assertContains(stopped, "Alert stopped")
        # It says what really happened: this alert went, the address only if nothing else uses it.
        self.assertContains(stopped, "This alert has been deleted.")
        self.assertNotContains(stopped, "Your alert and your email address have been deleted.")
        self.assertFalse(SearchAlert.objects.exists())
        self.assertContains(self.client.get(reverse("web:search_alert_confirm", args=[alert.token])), "This link has expired")

    @mock.patch("catalogue.mail.send")
    def test_inline_json(self, send):
        data = self.ask(json=True).json()
        self.assertEqual(data["state"], "sent")
        self.assertEqual(data["title"], "Check your inbox")
        self.assertIn("English Pokémon booster packs up to £5.00 delivered, in stock", data["message"])

    @mock.patch("catalogue.mail.send")
    def test_any_type_is_read_back(self, send):
        data = self.ask(type="", json=True).json()
        self.assertEqual(data["summary"], "English Pokémon products up to £5.00 delivered, in stock")
        self.assertNotIn(" for .", data["message"])
        self.assertIn("English Pokémon products up to £5.00 delivered, in stock", data["message"])
        response = self.ask(type="", email="js-off@example.com")
        page = self.client.get(response.url).content.decode()
        self.assertIn("English Pokémon products up to £5.00 delivered, in stock", page)
        self.assertNotIn("we will email you when it is back", page)

    @mock.patch("catalogue.mail.send")
    def test_any_language_is_read_back_without_javascript(self, send):
        response = self.ask(lang="")
        self.assertIn("lang=&", response.url)
        page = self.client.get(response.url).content.decode()
        self.assertIn("Pokémon booster packs up to £5.00 delivered, in stock", page)
        self.assertNotIn("English Pokémon", page)
        self.assertEqual(SearchAlert.objects.get().language, "")

    def test_a_mistake_comes_back_at_the_form(self):
        html = self.client.get(reverse("web:watchlist")).content.decode()
        self.assertIn(f'action="{reverse("web:search_alert_ask")}#alert"', html)
        # watch.js rewrites the list's address only on the watchlist's own address, never the form's.
        self.assertIn(f'data-watchlist-url="{reverse("web:watchlist")}"', html)
        response = self.ask(max="0.10", p="a-slug")
        self.assertContains(response, f'data-watchlist-url="{reverse("web:watchlist")}"', status_code=400)
        self.assertContains(response, 'name="p" value="a-slug"', status_code=400)

    @mock.patch("catalogue.mail.send")
    def test_confirming_cannot_pass_ten_alerts(self, send):
        """Requests made over several days are each under the limit when asked; confirming checks it again."""
        waiting = [SearchAlert.objects.create(email="many@example.com", game=self.pokemon, max_price=Decimal(10 + n))
                   for n in range(search_alerts.MAX_PER_EMAIL + 3)]
        for alert in waiting:
            self.client.get(reverse("web:search_alert_confirm", args=[alert.token]))
        confirmed = SearchAlert.objects.filter(email="many@example.com", confirmed_at__isnull=False)
        self.assertEqual(confirmed.count(), search_alerts.MAX_PER_EMAIL)
        page = self.client.get(reverse("web:search_alert_confirm", args=[waiting[-1].token]))
        self.assertContains(page, "Alert not set")
        self.assertContains(page, "This address already has 10 alerts")
        self.assertIsNone(SearchAlert.objects.get(pk=waiting[-1].pk).confirmed_at)
        # Stop one, and the waiting request can be confirmed.
        self.client.get(reverse("web:search_alert_stop", args=[confirmed.first().token]))
        self.assertContains(self.client.get(reverse("web:search_alert_confirm", args=[waiting[-1].token])), "Alert confirmed")

    @mock.patch("catalogue.mail.send")
    def test_duplicates(self, send):
        self.ask()
        self.ask(email=" BEN@example.com ")
        self.assertEqual(SearchAlert.objects.count(), 1)
        self.assertEqual(send.call_count, 1)   # asked again moments later: no second confirmation
        SearchAlert.objects.update(confirmed_at=timezone.now())
        self.assertEqual(self.ask(json=True).json()["state"], "sent")
        self.assertEqual(send.call_count, 1)
        # A different description is a different alert.
        self.ask(max="6")
        self.assertEqual(SearchAlert.objects.count(), 2)

    @mock.patch("catalogue.mail.send")
    def test_ten_confirmed_alerts_per_address(self, send):
        for n in range(search_alerts.MAX_PER_EMAIL):
            SearchAlert.objects.create(email="many@example.com", game=self.pokemon, max_price=Decimal(10 + n),
                                       confirmed_at=timezone.now())
        self.assertEqual(self.ask(email="many@example.com", json=True).json()["state"], "sent")
        self.assertEqual(SearchAlert.objects.filter(email="many@example.com").count(), search_alerts.MAX_PER_EMAIL)
        send.assert_not_called()

    @mock.patch("catalogue.mail.send")
    def test_unconfirmed_requests_do_not_use_up_the_limit(self, send):
        for n in range(search_alerts.MAX_PER_EMAIL):
            SearchAlert.objects.create(email="owner@example.com", game=self.pokemon, max_price=Decimal(10 + n))
        self.ask(email="owner@example.com")
        send.assert_called_once()

    @mock.patch("catalogue.mail.send")
    def test_rate_limit_per_connection(self, send):
        for n in range(search_alerts.ASKS_PER_ADDRESS_HOUR):
            self.ask(email=f"p{n}@example.com")
        data = self.ask(email="late@example.com", json=True).json()
        self.assertEqual(data["state"], "busy")
        self.assertIn("Try again in an hour", data["message"])
        self.assertFalse(SearchAlert.objects.filter(email="late@example.com").exists())
        response = self.ask(email="late@example.com")
        self.assertIn("alert=busy", response.url)
        self.assertContains(self.client.get(response.url), "Too many alerts were asked for from here just now.")

    @mock.patch("catalogue.mail.send")
    def test_one_inbox_gets_few_confirmations_however_many_connections_ask(self, send):
        for n in range(6):
            response = self.ask(email="victim@example.com", max=str(10 + n), json=True, HTTP_X_FORWARDED_FOR=f"10.0.0.{n}")
            self.assertEqual(response.json()["state"], "sent")
        self.assertEqual(send.call_count, search_alerts.CONFIRMS_PER_EMAIL_DAY)

    @mock.patch("catalogue.mail.send")
    def test_the_answer_never_says_what_is_known_about_an_address(self, send):
        SearchAlert.objects.create(email="victim@example.com", game=self.pokemon, product_type=PACK, language="en",
                                   max_price=Decimal("5.00"), confirmed_at=timezone.now())
        known = self.ask(email="victim@example.com", json=True).json()
        fresh = self.ask(email="someone@example.com", json=True).json()
        self.assertEqual(known, fresh)
        self.assertEqual(self.ask(email="victim@example.com").url, self.ask(email="other@example.com").url)
        self.assertNotIn("victim", self.ask(email="victim@example.com").url)
        self.assertEqual([c.args[0] for c in send.call_args_list], ["someone@example.com", "other@example.com"])

    @mock.patch("catalogue.mail.send")
    def test_honeypot_and_failure(self, send):
        self.assertEqual(self.ask(website="spam").url, reverse("web:watchlist"))
        self.assertFalse(SearchAlert.objects.exists())
        send.side_effect = mail.MailError("down")
        self.assertEqual(self.ask(json=True).json()["state"], "failed")
        self.assertIn("alert=failed", self.ask(email="x@example.com").url)

    def test_get_is_not_allowed_and_the_csrf_token_is_needed(self):
        self.assertEqual(self.client.get(reverse("web:search_alert_ask")).status_code, 405)
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse("web:search_alert_ask"), {"game": "pokemon", "email": "a@example.com"}).status_code, 403)

    def test_browse_pages_link_to_the_form_with_their_filters(self):
        self.pack("Ascended Heroes Booster Pack")   # the type filter offers only types the list holds
        html = self.client.get(reverse("web:search"), {"game": "pokemon", "type": PACK, "lang": "en", "price": "0-10"}).content.decode()
        self.assertIn('href="/watchlist/?game=pokemon&amp;type=booster_pack&amp;lang=en&amp;max=10#alert"', html)
        self.assertIn("Email me when products like these are in stock", html)
        # No language filter is any language, so the form does not suggest English.
        game_page = self.client.get(self.pokemon.get_absolute_url()).content.decode()
        self.assertIn('href="/watchlist/?game=pokemon&amp;lang=#alert"', game_page)
        form = self.client.get("/watchlist/?game=pokemon&lang=").content.decode()
        self.assertIn('<option value="" selected>Any language</option>', form)
        self.assertNotIn('<option value="en" selected>', form)
        # No game chosen: nothing to describe yet.
        self.assertNotIn("Email me when products like these", self.client.get(reverse("web:search")).content.decode())
        page = self.client.get(reverse("web:watchlist"), {"game": "magic-the-gathering", "type": "booster_box",
                                                          "lang": "ja", "max": "120"}).content.decode()
        self.assertIn('<option value="magic-the-gathering" selected>Magic</option>', page)
        self.assertIn('<option value="booster_box" selected>', page)
        self.assertIn('<option value="ja" selected>Japanese</option>', page)
        self.assertIn('value="120"', page)

    def test_without_email_set_up_browse_pages_do_not_offer_it(self):
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            html = self.client.get(reverse("web:search"), {"game": "pokemon"}).content.decode()
        self.assertNotIn("Email me when products like these", html)


# The matcher ---------------------------------------------------------------------------------------

@override_settings(**MAIL_ON)
class MatcherTests(AlertData):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("catalogue.mail.send")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        self.cheap = self.pack("Ascended Heroes Booster Pack", price="3.50", delivery="1.00")   # £4.50 delivered
        make_listing(self.cheap, self.north, price="4.00", delivery="2.50")
        self.alert = self.make_alert()

    def make_alert(self, **kwargs):
        values = {"email": "ben@example.com", "game": self.pokemon, "product_type": PACK, "language": "en",
                  "max_price": Decimal("5.00"), "confirmed_at": timezone.now() - timedelta(days=1), **kwargs}
        return SearchAlert.objects.create(**values)

    def run_at(self, now=None):
        return search_alerts.send_due(now=now)

    def emailed(self):
        return [c.args for c in self.send.call_args_list]

    def test_only_true_matches(self):
        # Each of these differs from a match in one way.
        self.pack("Ascended Heroes Booster Pack Sleeved", price="5.50")                               # over the price
        self.pack("Ascended Heroes Booster Pack (Japanese)", price="2.00")                             # language
        etb = make_product(self.set, name="Ascended Heroes Elite Trainer Box", product_type="elite_trainer_box")
        make_listing(etb, self.harbour, price="3.00")                                                  # type
        self.pack("Lorwyn Eclipsed Play Booster Pack", price="3.00", game_set=self.magic_set)          # game
        self.pack("Sold Out Booster Pack", price="2.00", availability=Listing.Availability.OUT_OF_STOCK)
        self.pack("Pre-order Booster Pack", price="2.00", availability=Listing.Availability.PREORDER)
        self.pack("Stale Booster Pack", price="2.00", hours_ago=200)
        self.pack("Hidden Listing Booster Pack", price="2.00", is_active=False)
        self.pack("Closed Shop Booster Pack", price="2.00", shop=self.closed)
        self.pack("Ebay Only Booster Pack", price="2.00", shop=self.ebay)
        self.pack("Amazon Only Booster Pack", price="2.00", shop=self.amazon)
        self.pack("Kept Out Booster Pack", price="2.00", sanity=Listing.Sanity.EXCLUDED)
        self.pack("Unknown Delivery Booster Pack", price="2.00", delivery_known=False)
        retired = self.pack("Retired Booster Pack", price="2.00")
        Product.objects.filter(pk=retired.pk).update(is_active=False)
        self.assertEqual(self.run_at(), (1, 0))
        to, subject, text, html = self.emailed()[0]
        self.assertEqual(to, "ben@example.com")
        self.assertEqual(subject, "In stock: English Pokémon booster packs up to £5.00 delivered")
        self.assertIn("Ascended Heroes Booster Pack", text)
        self.assertIn("from £4.50 delivered", text)   # the cheaper of the two shops
        self.assertIn("See prices: https://ripraptor.com/products/", text)
        for name in ("Sleeved", "Japanese", "Elite Trainer", "Lorwyn", "Sold Out", "Pre-order", "Stale", "Hidden",
                     "Closed Shop", "Ebay Only", "Amazon Only", "Kept Out", "Unknown Delivery", "Retired"):
            with self.subTest(name=name):
                self.assertNotIn(name, text)
                self.assertNotIn(name, html)

    def test_without_a_price_any_in_stock_shop_price_counts(self):
        self.alert.delete()
        self.make_alert(max_price=None)
        self.pack("Unknown Delivery Booster Pack", price="2.00", delivery_known=False)
        self.pack("Dear Booster Pack", price="40.00")
        self.run_at()
        text = self.emailed()[0][2]
        self.assertIn("from £2.00 plus delivery", text)
        self.assertIn("Dear Booster Pack", text)
        # Cheapest first.
        self.assertLess(text.index("Unknown Delivery"), text.index("Ascended Heroes"))
        self.assertLess(text.index("Ascended Heroes"), text.index("Dear Booster Pack"))

    def test_the_email_never_names_or_links_a_shop(self):
        self.pack("Second Booster Pack", price="2.00", shop=self.north)
        Product.objects.filter(pk=self.cheap.pk).update(image_url="https://cdn.shopify.com/s/files/1/pack.png?v=1")
        self.run_at()
        to, subject, text, html = self.emailed()[0]
        preheader = re.search(r'<div style="display:none;max-height:0;overflow:hidden;">(.*?)</div>', html).group(1)
        self.assertIn("Second Booster Pack", preheader)
        everything = " ".join((subject, text, html)).lower()
        for shop in Retailer.objects.all():
            for value in (shop.name, shop.slug, shop.website, shop.source_url):
                if value:
                    with self.subTest(value=value):
                        self.assertNotIn(value.lower(), everything)
            for host in (shop.website, shop.source_url):
                if host:
                    self.assertNotIn(host.split("//")[1].strip("/").removeprefix("www."), everything)
        for listing in Listing.objects.all():
            self.assertNotIn(listing.url.lower(), everything)
        self.assertNotIn("/go/", everything)
        # A shop's picture is left out even on a shared CDN: its folder belongs to one shop.
        self.assertNotIn("cdn.shopify.com", everything)
        self.assertNotIn("<img src=\"https://cdn", html)
        self.assertIn(f"https://ripraptor.com{self.cheap.get_absolute_url()}", html)

    def test_a_picture_that_could_name_the_shop_is_left_out(self):
        words = search_alerts.shop_words()
        for url in ("https://harbour-games.example/images/pack.jpg",
                    "https://cdn.shopify.com/s/files/1/0530/files/pack-harbour-games-2.png",
                    "https://cdn.shopify.com/s/files/1/0530/files/pack-northgatecards.png",
                    "https://cdn.shopify.com/s/files/1/0530/files/pack-northgate-tcg.png",
                    "https://bucket.supabase.co/storage/pack.png"):
            with self.subTest(url=url):
                self.cheap.image_url = url
                self.assertEqual(search_alerts.email_image(self.cheap, words), "")
        # Shared CDNs give the shop away by folder (Shopify's /s/files/1/<shop>/, BigCommerce's s-<store>) or by
        # a shop's SKU prefix, so they are left out however neutral the file name looks.
        for url in ("https://cdn.shopify.com/s/files/1/0530/files/booster-pack.png",
                    "https://cdn.shopify.com/s/files/1/0916/9970/8282/files/ZBG-FAB2608EN-BLAZE.jpg",
                    "https://cdn11.bigcommerce.com/s-abc/images/stencil/1280x1280/products/1/2/pack.jpg"):
            with self.subTest(url=url):
                self.cheap.image_url = url
                self.assertEqual(search_alerts.email_image(self.cheap, words), "")
        # A picture stored on RipRaptor is ours, unless its file name names a shop.
        self.cheap.image_url = "https://cdn.shopify.com/s/files/1/0530/files/booster-pack.png"
        self.cheap.image.name = "products/ascended-heroes-pack.png"
        self.assertEqual(search_alerts.email_image(self.cheap, words),
                         "https://ripraptor.com/media/products/ascended-heroes-pack.png")
        self.cheap.image.name = "products/harbour-games-pack.png"
        self.assertEqual(search_alerts.email_image(self.cheap, words), "")
        self.cheap.image.name = ""
        self.cheap.image_url = ""
        self.assertEqual(search_alerts.email_image(self.cheap, words), "")

    def test_no_repeat_until_it_has_gone_and_come_back_after_a_week(self):
        now = timezone.now()
        self.assertEqual(self.run_at(now), (1, 0))
        self.assertEqual(self.run_at(now + timedelta(minutes=15)), (0, 0))
        record = SearchAlertSent.objects.get()
        self.assertIsNone(record.gone_at)
        # Sold out everywhere: recorded as gone.
        Listing.objects.filter(product=self.cheap).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.run_at(now + timedelta(hours=1)), (0, 0))
        record.refresh_from_db()
        self.assertEqual(record.gone_at, now + timedelta(hours=1))
        # Back two days later: still within the week, so nothing yet.
        Listing.objects.filter(product=self.cheap).update(availability=Listing.Availability.IN_STOCK)
        self.assertEqual(self.run_at(now + timedelta(days=2)), (0, 0))
        # A week after the last email it has come back, so it is sent again.
        self.assertEqual(self.run_at(now + timedelta(days=7, minutes=1)), (1, 0))
        record = SearchAlertSent.objects.get()
        self.assertEqual(record.sent_at, now + timedelta(days=7, minutes=1))
        self.assertIsNone(record.gone_at)
        self.assertEqual(self.run_at(now + timedelta(days=15)), (0, 0))

    def test_a_listing_that_only_went_unchecked_has_not_gone(self):
        """A shop's reader failing for a few days is not the product selling out: no second email when it is
        read again with the same stock and price."""
        now = timezone.now()
        listings = Listing.objects.filter(product=self.cheap)
        self.assertEqual(self.run_at(now), (1, 0))
        listings.update(last_checked=timezone.now() - timedelta(hours=200))   # both shops unchecked too long
        self.assertEqual(self.run_at(now + timedelta(days=1)), (0, 0))
        self.assertIsNone(SearchAlertSent.objects.get().gone_at)
        listings.update(last_checked=timezone.now())
        self.assertEqual(self.run_at(now + timedelta(days=8)), (0, 0))
        self.assertEqual(len(self.emailed()), 1)
        # Sold out at one shop while the other is unchecked: still in stock as far as we know, not gone.
        listings.filter(retailer=self.harbour).update(last_checked=timezone.now() - timedelta(hours=200))
        listings.filter(retailer=self.north).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.run_at(now + timedelta(days=9))
        self.assertIsNotNone(SearchAlertSent.objects.get().gone_at)   # a current listing says it sold out

    def test_going_over_the_price_and_back_counts_as_gone(self):
        now = timezone.now()
        self.run_at(now)
        Listing.objects.filter(product=self.cheap).update(price=Decimal("9.00"))
        self.run_at(now + timedelta(hours=1))
        self.assertIsNotNone(SearchAlertSent.objects.get().gone_at)
        Listing.objects.filter(product=self.cheap).update(price=Decimal("3.00"))
        self.assertEqual(self.run_at(now + timedelta(days=8)), (1, 0))

    def test_never_sent_again_while_it_stays_in_stock(self):
        now = timezone.now()
        self.run_at(now)
        self.assertEqual(self.run_at(now + timedelta(days=30)), (0, 0))

    def test_up_to_six_products_and_a_link_to_the_rest(self):
        for n in range(8):
            self.pack(f"Extra {n} Booster Pack", price=f"{1 + n * 0.1:.2f}", delivery="0.00")
        self.assertEqual(self.run_at(), (1, 0))
        text, html = self.emailed()[0][2:]
        shown = [n for n in range(8) if f"Extra {n} Booster Pack" in text]
        self.assertEqual(shown, [0, 1, 2, 3, 4, 5])   # the six cheapest
        self.assertNotIn("Ascended Heroes Booster Pack", text)
        self.assertIn("And 3 more", text)
        self.assertIn("https://ripraptor.com/search/?game=pokemon&type=booster_pack&lang=en&in_stock=on&sort=price", text)
        # The rest were told about by that link, so they are not sent one by one later.
        self.assertEqual(SearchAlertSent.objects.count(), 9)
        self.assertEqual(self.run_at(), (0, 0))

    def test_one_email_per_alert_and_alerts_are_separate(self):
        other = self.make_alert(email="amy@example.com")
        dear = self.make_alert(email="cal@example.com", max_price=Decimal("2.00"))
        self.assertEqual(self.run_at(), (2, 0))
        self.assertEqual(sorted(c[0] for c in self.emailed()), ["amy@example.com", "ben@example.com"])
        self.assertTrue(SearchAlertSent.objects.filter(alert=other).exists())
        self.assertFalse(SearchAlertSent.objects.filter(alert=dear).exists())

    def test_unconfirmed_alerts_hear_nothing(self):
        SearchAlert.objects.update(confirmed_at=None)
        self.assertEqual(self.run_at(), (0, 0))

    def test_a_failed_send_is_handed_back(self):
        now = timezone.now()
        self.send.side_effect = mail.MailError("down")
        self.assertEqual(self.run_at(now), (0, 1))
        self.assertFalse(SearchAlertSent.objects.exists())
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.sent_at)
        self.send.side_effect = None
        self.assertEqual(self.run_at(now + timedelta(minutes=15)), (1, 0))

    def test_a_second_run_that_read_the_alert_earlier_cannot_send_it(self):
        stale = SearchAlert.objects.get(pk=self.alert.pk)
        self.assertEqual(self.run_at(), (1, 0))
        self.assertIsNone(search_alerts.claim(stale, [self.cheap.pk], {}, timezone.now()))

    def test_query_count_is_one_per_group_not_per_alert(self):
        self.run_at()   # everything already sent: later runs only look

        def count():
            with CaptureQueriesContext(connection) as queries:
                self.run_at()
            return len(queries)

        one_group = count()
        for n in range(5):
            self.make_alert(email=f"same{n}@example.com")   # the same game, type and language: same query
        self.run_at()
        self.assertEqual(count(), one_group)
        self.make_alert(email="magic@example.com", game=self.magic, product_type="booster_box", max_price=Decimal("120"))
        self.make_alert(email="op@example.com", game=self.one_piece, product_type="booster_box", language="")
        self.assertEqual(count(), one_group + 2)

    def test_tidy_deletes_unconfirmed_expired_and_old_records(self):
        now = timezone.now()
        old_unconfirmed = SearchAlert.objects.create(email="a@example.com", game=self.pokemon,
                                                     created_at=now - timedelta(days=8))
        new_unconfirmed = SearchAlert.objects.create(email="b@example.com", game=self.pokemon,
                                                     created_at=now - timedelta(days=6))
        expired = self.make_alert(email="c@example.com", confirmed_at=now - timedelta(days=search_alerts.KEEP_DAYS + 1))
        SearchAlertSent.objects.create(alert=expired, product=self.cheap, sent_at=now - timedelta(days=30))
        gone = SearchAlertSent.objects.create(alert=self.alert, product=self.pack("Old Booster Pack", price="9.00"),
                                              sent_at=now - timedelta(days=8), gone_at=now - timedelta(days=7))
        search_alerts.tidy(now)
        left = set(SearchAlert.objects.values_list("pk", flat=True))
        self.assertNotIn(old_unconfirmed.pk, left)
        self.assertNotIn(expired.pk, left)
        self.assertIn(new_unconfirmed.pk, left)
        self.assertIn(self.alert.pk, left)
        self.assertFalse(SearchAlertSent.objects.filter(pk=gone.pk).exists())
        self.assertFalse(SearchAlertSent.objects.filter(alert_id=expired.pk).exists())

    def test_the_stop_link_deletes_the_alert_and_what_it_was_sent(self):
        self.run_at()
        to, subject, text, html = self.emailed()[0]
        # The email promises what the link does: this alert goes, not every alert for the address.
        for part in (text, html):
            self.assertIn("Stop this alert", part)
            self.assertIn("Stopping deletes this alert at once.", part)
            self.assertNotIn("delete your email address", part)
            self.assertNotIn("Unsubscribing deletes your email address", part)
        self.client.get(reverse("web:search_alert_stop", args=[self.alert.token]))
        self.assertFalse(SearchAlert.objects.exists())
        self.assertFalse(SearchAlertSent.objects.exists())

    def test_the_command(self):
        from io import StringIO

        out = StringIO()
        call_command("send_search_alerts", stdout=out)
        self.assertIn("1 search alert emails sent, 0 failed", out.getvalue())
        with self.settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            out = StringIO()
            call_command("send_search_alerts", stdout=out)
            self.assertIn("Nothing sent", out.getvalue())


# House style -----------------------------------------------------------------------------------------

@override_settings(**MAIL_ON)
class SearchAlertCopyTests(AlertData):
    def assert_house_style(self, text):
        visible = re.sub(r"<[^>]*>", " ", text)
        self.assertNotIn("!", visible)
        for phrase in CopyStyleTests.BANNED:
            self.assertNotIn(phrase, text.lower())

    def test_pages_keep_the_house_style(self):
        for url in (reverse("web:watchlist"),
                    reverse("web:watchlist") + "?alert=sent&game=pokemon&type=booster_pack&lang=en&max=5",
                    reverse("web:watchlist") + "?alert=busy",
                    reverse("web:search_alert_confirm", args=["nope"]),
                    reverse("web:search_alert_stop", args=["nope"])):
            with self.subTest(url=url):
                self.assert_house_style(self.client.get(url).content.decode())

    @mock.patch("catalogue.mail.send")
    def test_emails_keep_the_house_style(self, send):
        game = make_game("Yu-Gi-Oh!", "yu-gi-oh", short_name="Yu-Gi-Oh")
        search_alerts.ask("ben@example.com", game, PACK, "en", Decimal("5"))
        product = make_product(make_set(game, name="Blazing Dominion", slug="blazing-dominion"),
                               name="Blazing Dominion Booster Pack", product_type=PACK)
        make_listing(product, self.harbour, price="3.00")
        SearchAlert.objects.update(confirmed_at=timezone.now())
        search_alerts.send_due()
        self.assertEqual(send.call_count, 2)
        for args in send.call_args_list:
            for part in args.args[1:]:
                self.assert_house_style(part)
