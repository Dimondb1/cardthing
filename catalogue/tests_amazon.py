import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import amazon
from .importers import Offer, run_import
from .models import ImportRun, Listing, OutboundClick, Product, Retailer
from .testing import make_game, make_product, make_retailer, make_set

import os
import tempfile

KEY_FOLDER = tempfile.mkdtemp(prefix="ripraptor-amazon-tests-")
KEYS = {
    "RIPRAPTOR_AMAZON_CREDENTIAL_ID": "amzn1.application-oa2-client.test",
    "RIPRAPTOR_AMAZON_CREDENTIAL_SECRET": "secret",
    "RIPRAPTOR_AMAZON_PARTNER_TAG": "ripraptor-21",
    "RIPRAPTOR_AMAZON_KEY_FILE": os.path.join(KEY_FOLDER, "amazon-keys"),
}
NO_KEYS = {**KEYS, "RIPRAPTOR_AMAZON_CREDENTIAL_ID": "", "RIPRAPTOR_AMAZON_CREDENTIAL_SECRET": "",
           "RIPRAPTOR_AMAZON_KEY_FILE": os.path.join(KEY_FOLDER, "none")}


def api_item(asin, title, price="89.99", ean="0820650853500", stock="IN_STOCK", condition="New", seller="Amazon.co.uk",
             currency="GBP"):
    """One item as the Creators API gives it (offersV2)."""
    return {
        "asin": asin,
        "detailPageURL": f"https://www.amazon.co.uk/dp/{asin}?tag=ripraptor-21&linkCode=ogi",
        "itemInfo": {
            "title": {"displayValue": title, "label": "Title", "locale": "en_GB"},
            "externalIds": {"eans": {"displayValues": [ean] if ean else [], "label": "EAN"}},
        },
        "offersV2": {"listings": [{
            "price": {"money": {"amount": float(price), "currency": currency, "displayAmount": f"£{price}"}},
            "availability": {"type": stock, "maxOrderQuantity": 1, "minOrderQuantity": 1},
            "condition": {"value": condition, "subCondition": "Unknown"},
            "merchantInfo": {"name": seller},
            "isBuyBoxWinner": True,
        }]},
    }


class FakeAmazon:
    """Amazon's token address and catalogue as amazon.post meets them: (status, answer, Retry-After)."""

    def __init__(self, answers=None, token_status=200):
        self.answers = answers or {}
        self.token_status = token_status
        self.sent = []
        self.tokens = 0

    def __call__(self, url, body, headers):
        self.sent.append((url, body, headers))
        if url == amazon.TOKEN_URL:
            self.tokens += 1
            if self.token_status != 200:
                return self.token_status, {"error": "invalid_client", "error_description": "Client authentication failed"}, None
            return 200, {"access_token": f"token-{self.tokens}", "token_type": "bearer", "expires_in": 3600}, None
        found = self.answers.get(url.rsplit("/", 1)[-1], (200, {}))
        if isinstance(found, list):
            found = found.pop(0)
        return (*found, None) if len(found) == 2 else found


@override_settings(**KEYS)
class TokenAndCallTests(TestCase):
    def setUp(self):
        amazon.forget_token()
        self.addCleanup(amazon.forget_token)

    def test_a_token_is_asked_for_with_the_credential_and_kept(self):
        api = FakeAmazon({"getItems": (200, {"itemsResult": {"items": []}})})
        amazon.call("getItems", {"itemIds": ["B0TEST"]}, "cid", "sec", send=api)
        amazon.call("getItems", {"itemIds": ["B0TEST"]}, "cid", "sec", send=api)
        url, body, _headers = api.sent[0]
        self.assertEqual(url, "https://api.amazon.co.uk/auth/o2/token")
        self.assertEqual(body, {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "sec",
                                "scope": "creatorsapi::default"})
        self.assertEqual(api.tokens, 1)
        url, body, headers = api.sent[1]
        self.assertEqual(url, "https://creatorsapi.amazon/catalog/v1/getItems")
        self.assertEqual(headers, {"Authorization": "Bearer token-1", "x-marketplace": "www.amazon.co.uk"})

    def test_an_ended_token_is_renewed_once(self):
        api = FakeAmazon({"getItems": [(401, {"type": "UnauthorizedException", "reason": "TokenExpired"}),
                                       (200, {"itemsResult": {"items": []}})]})
        amazon.call("getItems", {}, "cid", "sec", send=api)
        self.assertEqual(api.tokens, 2)

    def test_throttled_it_waits_as_amazon_asks_then_asks_again(self):
        waits = []
        api = FakeAmazon({"searchItems": [(429, {"type": "ThrottleException", "retryAfterSeconds": 2}),
                                          (200, {"searchResult": {"items": []}})]})
        amazon.call("searchItems", {}, "cid", "sec", send=api, sleep=waits.append)
        self.assertEqual(waits, [2.0])

    def test_nothing_found_is_no_items_and_other_errors_say_what_amazon_said(self):
        api = FakeAmazon({"getItems": (404, {"type": "ResourceNotFoundException", "message": "No items found"})})
        self.assertEqual(amazon.call("getItems", {}, "cid", "sec", send=api), {})
        api = FakeAmazon({"getItems": (403, {"type": "AccessDeniedException", "reason": "AssociateNotEligible",
                                             "message": "Not eligible"})})
        with self.assertRaisesRegex(amazon.AmazonError, "AssociateNotEligible"):
            amazon.call("getItems", {}, "cid", "sec", send=api)
        with self.assertRaisesRegex(amazon.AmazonError, "Client authentication failed"):
            amazon.call("getItems", {}, "other", "sec", send=FakeAmazon(token_status=401))

    def test_missing_keys_are_a_clear_error(self):
        with override_settings(**NO_KEYS), self.assertRaisesRegex(amazon.AmazonError, "Admin, Amazon"):
            amazon.credentials()


class ItemTests(TestCase):
    def test_an_amazon_sold_item_becomes_an_offer_with_amazons_delivery_rules(self):
        offer = amazon.item_offer(api_item("B0TEST", "Pokemon TCG Prismatic Evolutions Elite Trainer Box"), Offer)
        self.assertEqual(offer.price, Decimal("89.99"))
        self.assertIsNone(offer.delivery)   # the shop's rules: Amazon's published rates
        self.assertEqual(offer.ean, "0820650853500")
        self.assertEqual(offer.availability, Listing.Availability.IN_STOCK)
        self.assertEqual(offer.tags, ("B0TEST",))
        self.assertIn("tag=ripraptor-21", offer.url)

    def test_stock_pre_order_and_the_rest(self):
        for kind, expected in (("IN_STOCK_SCARCE", Listing.Availability.IN_STOCK),
                               ("PREORDER", Listing.Availability.PREORDER),
                               ("OUT_OF_STOCK", Listing.Availability.OUT_OF_STOCK),
                               ("UNAVAILABLE", Listing.Availability.OUT_OF_STOCK)):
            with self.subTest(kind=kind):
                self.assertEqual(amazon.item_offer(api_item("B0TEST", "Box", stock=kind), Offer).availability, expected)

    def test_another_seller_used_another_currency_or_no_offer_is_skipped(self):
        """Another seller's delivery charge is not given, so it would be a guess."""
        self.assertIsNone(amazon.item_offer(api_item("B0TEST", "Box", seller="Card Kings Ltd"), Offer))
        self.assertIsNone(amazon.item_offer(api_item("B0TEST", "Box", condition="Used"), Offer))
        self.assertIsNone(amazon.item_offer(api_item("B0TEST", "Box", currency="USD"), Offer))
        item = api_item("B0TEST", "Box")
        item["offersV2"] = {}
        self.assertIsNone(amazon.item_offer(item, Offer))

    def test_only_amazons_uk_store_and_pounds(self):
        """The owner, 13 Nov 2026: "The compare on amazon link is taking uses .com instead of co.uk"."""
        item = api_item("B0TEST", "Box")
        item["detailPageURL"] = "https://www.amazon.com/dp/B0TEST?tag=ripraptor-21"
        self.assertIsNone(amazon.item_offer(item, Offer))
        item = api_item("B0TEST", "Box")
        del item["offersV2"]["listings"][0]["price"]["money"]["currency"]
        self.assertIsNone(amazon.item_offer(item, Offer))

    def test_both_names_amazon_gives_the_lookup_answer(self):
        items = [api_item("B0TEST", "Box")]
        self.assertEqual(amazon.items_of({"itemsResult": {"items": items}}, "itemsResult"), items)
        self.assertEqual(amazon.items_of({"itemResults": {"items": items}}, "itemsResult"), items)
        self.assertEqual(amazon.items_of({"searchResult": {"items": items}}, "searchResult"), items)


@override_settings(**KEYS)
class LookupTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.box = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle")
        self.calls = []

    def fake_api(self, answers):
        def api(operation, payload):
            self.calls.append((operation, payload))
            return answers.get(operation, {})
        return api

    def test_barcode_search_stores_the_asin_and_returns_the_offer(self):
        api = self.fake_api({"searchItems": {"searchResult": {"items": [
            api_item("B0OTHER", "Some other tin", ean="5012345678900"),
            api_item("B0ETB", "Pokemon Scarlet and Violet Prismatic Evolutions Elite Trainer Box"),
        ]}}})
        offers = amazon.amazon_offers(None, limit=5, call_api=api, pause=0)
        self.etb.refresh_from_db()
        self.assertEqual(self.etb.amazon_asin, "B0ETB")
        self.assertIsNotNone(self.etb.amazon_checked_at)
        self.assertEqual([o.tags[0] for o in offers if o.ean == "0820650853500"], ["B0ETB"])
        search = [p for op, p in self.calls if op == "searchItems"]
        self.assertEqual(search[0]["keywords"], "0820650853500")
        self.assertEqual(search[0]["partnerTag"], "ripraptor-21")
        self.assertEqual(search[0]["marketplace"], "www.amazon.co.uk")
        self.assertIn("offersV2.listings.merchantInfo", search[0]["resources"])

    def test_name_search_keeps_only_a_result_that_matches_the_product(self):
        self.etb.delete()
        api = self.fake_api({"searchItems": {"searchResult": {"items": [
            api_item("B0SINGLE", "Pokemon Prismatic Evolutions Umbreon ex single card", ean=""),
            api_item("B0BUNDLE", "Pokemon TCG Scarlet and Violet Prismatic Evolutions Booster Bundle", ean=""),
        ]}}})
        offers = amazon.amazon_offers(None, limit=5, call_api=api, pause=0)
        self.box.refresh_from_db()
        self.assertEqual(self.box.amazon_asin, "B0BUNDLE")
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].product_pk, self.box.pk)

    def test_nothing_found_is_remembered_and_tried_last_next_time(self):
        api = self.fake_api({"searchItems": {}})
        amazon.amazon_offers(None, limit=1, call_api=api, pause=0)
        self.assertEqual(len(self.calls), 1)
        first = self.calls[0][1]["keywords"]
        amazon.amazon_offers(None, limit=1, call_api=api, pause=0)
        self.assertNotEqual(self.calls[1][1]["keywords"], first)

    def test_a_clicked_product_is_looked_up_first_and_ties_keep_the_barcode_first_order(self):
        cache.clear()
        self.addCleanup(cache.clear)
        api = self.fake_api({"searchItems": {}})
        amazon.amazon_offers(None, limit=1, call_api=api, pause=0)
        self.assertEqual(self.calls[-1][1]["keywords"], "0820650853500")
        Product.objects.update(amazon_checked_at=None)
        shop = make_retailer("Shop A")
        OutboundClick.objects.create(product=self.box, retailer=shop)
        amazon.amazon_offers(None, limit=1, call_api=api, pause=0)
        self.assertEqual(self.calls[-1][1]["keywords"], self.box.name)

    def test_known_products_are_refreshed_ten_a_request_before_new_lookups(self):
        Product.objects.filter(pk=self.etb.pk).update(amazon_asin="B0ETB")
        api = self.fake_api({
            "getItems": {"itemsResult": {"items": [api_item("B0ETB", "Elite Trainer Box", price="79.00")]}},
            "searchItems": {},
        })
        offers = amazon.amazon_offers(None, limit=2, call_api=api, pause=0)
        self.assertEqual(self.calls[0][0], "getItems")
        self.assertEqual(self.calls[0][1]["itemIds"], ["B0ETB"])
        self.assertEqual(offers[0].price, Decimal("79.00"))
        self.assertEqual(sum(1 for op, _ in self.calls if op == "searchItems"), 1)

    def test_a_run_takes_a_share_of_the_days_lookups(self):
        from .importers import AMAZON_EVERY_HOURS

        api = self.fake_api({"searchItems": {}})
        with override_settings(RIPRAPTOR_AMAZON_DAILY_LIMIT=8):
            amazon.amazon_offers(None, call_api=api, pause=0)
        self.assertLessEqual(len(self.calls), 8 * AMAZON_EVERY_HOURS // 24)


@override_settings(**KEYS)
class ImportTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.retailer = Retailer.objects.create(
            name="Amazon", slug="amazon", website="https://www.amazon.co.uk/",
            source_type=Retailer.Source.AMAZON, delivery_cost=Decimal("4.99"), free_delivery_over=Decimal("35"),
        )

    def test_import_creates_a_listing_with_amazons_tracked_link_and_its_delivery(self):
        answer = {"searchResult": {"items": [api_item("B0ETB", "Prismatic Evolutions Elite Trainer Box")]}}
        with mock.patch.object(amazon, "call", return_value=answer), mock.patch.object(amazon, "PAUSE", 0):
            run = run_import(self.retailer)
        self.assertEqual(run.error, "")
        listing = Listing.objects.get(retailer=self.retailer, product=self.etb)
        self.assertEqual(listing.url, "https://www.amazon.co.uk/dp/B0ETB?tag=ripraptor-21&linkCode=ogi")
        self.assertEqual(listing.price, Decimal("89.99"))
        self.assertEqual(listing.delivered_price, Decimal("89.99"))   # free over £35

    def test_import_runs_every_six_hours(self):
        ImportRun.objects.create(retailer=self.retailer, offers_found=10,
                                 finished_at=timezone.now() - datetime.timedelta(hours=2))
        with mock.patch.object(amazon, "call") as called:
            run_import(self.retailer)
        called.assert_not_called()
        ImportRun.objects.update(finished_at=timezone.now() - datetime.timedelta(hours=7))
        with mock.patch.object(amazon, "call", return_value={}) as called, mock.patch.object(amazon, "PAUSE", 0):
            run_import(self.retailer)
        called.assert_called()

    def test_missing_keys_are_reported_not_raised(self):
        with override_settings(**NO_KEYS):
            run = run_import(self.retailer)
        self.assertIn("Admin, Amazon", run.error)

    def test_terms_page_carries_the_amazon_line_only_while_amazon_is_a_shop(self):
        html = self.client.get(reverse("web:terms")).content.decode()
        self.assertIn("As an Amazon Associate", html)
        self.retailer.is_active = False
        self.retailer.save()
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG=""):
            html = self.client.get(reverse("web:terms")).content.decode()
        self.assertNotIn("As an Amazon Associate", html)

    def test_terms_page_carries_the_amazon_line_while_the_search_link_is_tagged(self):
        self.retailer.delete()
        html = self.client.get(reverse("web:terms")).content.decode()
        self.assertIn("As an Amazon Associate", html)
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG=""):
            html = self.client.get(reverse("web:terms")).content.decode()
        self.assertNotIn("As an Amazon Associate", html)

    def test_setup_shops_adds_amazon_only_with_keys(self):
        self.retailer.delete()
        out = StringIO()
        with override_settings(**{**NO_KEYS, "RIPRAPTOR_AMAZON_PARTNER_TAG": ""}):
            call_command("setup_shops", stdout=out)
        self.assertFalse(Retailer.objects.filter(slug="amazon").exists())
        # The tag alone: switched off, never read, there to count the Amazon buttons' clicks against.
        with override_settings(**NO_KEYS):
            call_command("setup_shops", stdout=out)
        self.assertFalse(Retailer.objects.get(slug="amazon").is_active)
        self.assertNotIn(Retailer.objects.get(slug="amazon"), list(Retailer.due()))
        Retailer.objects.filter(slug="amazon").delete()
        call_command("setup_shops", stdout=out)
        shop = Retailer.objects.get(slug="amazon")
        self.assertEqual(shop.source_type, Retailer.Source.AMAZON)
        self.assertTrue(shop.is_active)
        self.assertEqual(shop.delivery_cost, Decimal("4.99"))


class OtherStoreTests(TestCase):
    def test_a_listing_linking_to_another_amazon_store_is_taken_off_and_searched_again(self):
        from .testing import make_listing

        shop = Retailer.objects.create(name="Amazon", slug="amazon", website="https://www.amazon.co.uk/",
                                       source_type=Retailer.Source.AMAZON)
        box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="surging-box")
        Product.objects.filter(pk=box.pk).update(amazon_asin="B0COM")
        wrong = make_listing(box, shop, price="120.00", url="https://www.amazon.com/dp/B0COM?tag=ripraptor-21")
        other = make_product(box.product_set, name="Surging Sparks Tin", slug="surging-tin")
        right = make_listing(other, shop, price="20.00", url="https://www.amazon.co.uk/dp/B0UK?tag=ripraptor-21")
        self.assertEqual(amazon.forget_other_stores(shop), 1)
        wrong.refresh_from_db()
        right.refresh_from_db()
        self.assertEqual((wrong.is_active, right.is_active), (False, True))
        self.assertEqual(Product.objects.get(pk=box.pk).amazon_asin, "")

    @override_settings(**KEYS)
    def test_the_keys_check_says_when_amazon_answers_from_another_store(self):
        item = api_item("B0TEST", "Box")
        item["detailPageURL"] = "https://www.amazon.com/dp/B0TEST"
        api = FakeAmazon({"searchItems": (200, {"searchResult": {"items": [item]}})})
        ok, said = amazon.check(send=api)
        self.assertFalse(ok)
        self.assertIn("www.amazon.com, not amazon.co.uk", said)

    def test_a_click_on_an_amazon_button_is_counted_with_no_amazon_shop_yet(self):
        """The owner, 13 Nov 2026: "do we record how many amazon button clicks we make on our dashboard"."""
        box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="surging-box")
        url = box.get_absolute_url()
        from catalogue import clicks

        browser = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            response = self.client.get(f"/go/search/{box.slug}/amazon/?b=amazon", HTTP_REFERER=f"http://testserver{url}",
                                       HTTP_USER_AGENT=browser, HTTP_COOKIE=f"{clicks.PAGE_COOKIE}=1")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://www.amazon.co.uk/s?"))
        self.assertIn("tag=ripraptor-21", response["Location"])
        shop = Retailer.objects.get(source_type=Retailer.Source.AMAZON)
        self.assertFalse(shop.is_active)
        click = OutboundClick.objects.get()
        self.assertEqual((click.retailer, click.button), (shop, "amazon"))

    def test_working_keys_switch_a_switched_off_amazon_on(self):
        shop = amazon.ensure_shop(active=False)
        self.assertFalse(shop.is_active)
        self.assertEqual(amazon.ensure_shop(active=False).pk, shop.pk)
        self.assertTrue(amazon.ensure_shop(active=True).is_active)


@override_settings(**KEYS)
class NeverComparedTests(TestCase):
    """Amazon's price is never part of the comparison, the price history or any alert, and is shown on its own row
    with when it was checked and Amazon's disclaimer: Amazon's rules forbid price tracking and alerting on an
    Associates site unless Amazon agrees, and ask for the time and the disclaimer beside every price."""

    def setUp(self):
        from .testing import make_listing

        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="surging-box")
        self.shop = make_retailer("Harbour Games")
        make_listing(self.box, self.shop, price="150.00", delivery="0.00")
        self.amazon = Retailer.objects.create(name="Amazon", slug="amazon", website="https://www.amazon.co.uk/",
                                              source_type=Retailer.Source.AMAZON, delivery_cost=Decimal("4.99"),
                                              free_delivery_over=Decimal("35"))
        self.on_amazon = make_listing(self.box, self.amazon, price="120.00", delivery="0.00",
                                      url="https://www.amazon.co.uk/dp/B0BOX?tag=ripraptor-21")

    def test_it_is_never_the_cheapest_nor_in_the_record(self):
        from . import pricing

        self.assertEqual(list(Listing.objects.current()), list(Listing.objects.filter(retailer=self.shop)))
        self.assertEqual(Product.objects.with_prices().get(pk=self.box.pk).lowest_known, Decimal("150.00"))
        self.assertEqual(pricing.update_daily_lowest(self.box).price, Decimal("150.00"))

    def test_the_product_page_shows_it_on_its_own_row_with_the_time_and_amazons_disclaimer(self):
        page = self.client.get(self.box.get_absolute_url())
        self.assertEqual(page.context["cheapest"].retailer, self.shop)
        self.assertEqual(page.context["amazon_offer"], self.on_amazon)
        self.assertIsNone(page.context["amazon_search"])
        html = page.content.decode()
        row = html[html.index("offer--amazon-priced"):]
        row = row[:row.index("</li>")]
        self.assertIn("£120.00", row)
        self.assertIn("Amazon&#x27;s price and stock at", row)
        self.assertIn("Product prices and availability are accurate as of the date/time indicated", row)
        self.assertIn("As an Amazon Associate, RipRaptor earns from qualifying purchases.", row)
        self.assertIn("Buy on Amazon", row)
        self.assertIn(f"/go/{self.on_amazon.pk}/", row)

    def test_after_a_day_unchecked_it_goes_and_the_search_row_comes_back(self):
        Listing.objects.filter(pk=self.on_amazon.pk).update(
            last_checked=timezone.now() - datetime.timedelta(hours=25))
        page = self.client.get(self.box.get_absolute_url())
        self.assertIsNone(page.context["amazon_offer"])
        self.assertNotContains(page, "offer--amazon-priced")
        self.assertContains(page, "Check price and stock on Amazon")

    def test_rip_is_never_told_it(self):
        from web import askrip

        found, _amounts, _checked = askrip.facts(self.box)
        self.assertEqual([offer["shop"] for offer in found["offers"]], ["Harbour Games"])
        self.assertNotIn("not_for_sale_now_at", found)


@override_settings(**KEYS)
class KeysPageTests(TestCase):
    """Admin, Amazon: the keys typed in a page of their own, never in a message (the owner, 13 Nov 2026: "I have this
    and a secret password thing ... I now need you to build it in")."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.staff = get_user_model().objects.create_user("owner", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse("amazon_keys")
        self.addCleanup(lambda: os.path.exists(KEYS["RIPRAPTOR_AMAZON_KEY_FILE"])
                        and os.unlink(KEYS["RIPRAPTOR_AMAZON_KEY_FILE"]))

    def test_keys_are_saved_privately_tested_and_amazon_added(self):
        import stat

        with override_settings(RIPRAPTOR_AMAZON_CREDENTIAL_ID="", RIPRAPTOR_AMAZON_CREDENTIAL_SECRET=""), \
                mock.patch.object(amazon, "check", return_value=(True, 7)):
            response = self.client.post(self.url, {"credential_id": "amzn1.application-oa2-client.abc",
                                                   "secret": "s3cret", "tag": "ripraptor-21"}, follow=True)
            self.assertContains(response, "Amazon accepted the keys and answered a test search with 7 items")
            path = KEYS["RIPRAPTOR_AMAZON_KEY_FILE"]
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(amazon.keys(), ("amzn1.application-oa2-client.abc", "s3cret", "ripraptor-21"))
            self.assertTrue(Retailer.objects.filter(source_type=Retailer.Source.AMAZON, is_active=True).exists())
            # The secret is never shown back; an empty box keeps it.
            page = self.client.get(self.url)
            self.assertNotContains(page, "s3cret")
            self.assertContains(page, "Saved. Leave empty to keep it")
            self.client.post(self.url, {"credential_id": "amzn1.application-oa2-client.abc", "secret": "",
                                        "tag": "ripraptor-21"})
            self.assertEqual(amazon.keys()[1], "s3cret")

    def test_keys_amazon_refuses_are_saved_and_say_why(self):
        with override_settings(RIPRAPTOR_AMAZON_CREDENTIAL_ID="", RIPRAPTOR_AMAZON_CREDENTIAL_SECRET=""), \
                mock.patch.object(amazon, "check", return_value=(False, "AssociateNotEligible")):
            response = self.client.post(self.url, {"credential_id": "x", "secret": "y", "tag": "ripraptor-21"},
                                        follow=True)
        self.assertContains(response, "Amazon did not accept them yet: AssociateNotEligible")
        self.assertFalse(Retailer.objects.filter(source_type=Retailer.Source.AMAZON).exists())

    def test_it_names_amazons_rule_on_price_tracking(self):
        self.assertContains(self.client.get(self.url), "must not have price tracking or price alerts")

    def test_only_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)


class AmazonPolicyTests(TestCase):
    """Amazon Associates rules the site keeps: no Amazon picture is stored, and Amazon's statement shows
    on every page and beside its buttons while Amazon links carry our tag."""

    def test_no_amazon_picture_is_asked_for_or_kept(self):
        from . import amazon
        from .importers import Offer

        self.assertFalse([name for name in amazon.RESOURCES if name.startswith("images")])
        offer = amazon.item_offer(api_item("B0TEST", "Surging Sparks Booster Box", "99.99"), Offer)
        self.assertEqual(offer.image, "")

    def test_the_statement_is_on_every_page_and_beside_the_amazon_buttons_while_tagged(self):
        game = make_game()
        product = make_product(make_set(game))
        line = "As an Amazon Associate, RipRaptor earns from qualifying purchases."
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG="ripraptor-21"):
            for url in (reverse("web:home"), game.get_absolute_url(), reverse("web:about")):
                with self.subTest(url=url):
                    self.assertContains(self.client.get(url), line, count=1)
            page = self.client.get(product.get_absolute_url())
            self.assertContains(page, f"/go/search/{product.slug}/amazon/")
            # The footer, and beside the Amazon button in the price box.
            self.assertContains(page, line, count=2)
        with override_settings(RIPRAPTOR_AMAZON_PARTNER_TAG=""):
            self.assertNotContains(self.client.get(product.get_absolute_url()), line)

    def test_the_privacy_part_of_the_terms_is_linked_from_every_footer_and_names_shop_cookies(self):
        home = self.client.get(reverse("web:home"))
        self.assertContains(home, f'href="{reverse("web:terms")}#terms-data">Privacy</a>')
        terms = self.client.get(reverse("web:terms"))
        self.assertContains(terms, 'id="terms-data"')
        self.assertContains(terms, "may set its own cookies to record that you came from RipRaptor")
