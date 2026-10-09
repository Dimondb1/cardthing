import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import ebay
from .importers import Offer, run_import
from .models import ImportRun, Listing, OutboundClick, Product, Retailer
from .testing import make_game, make_listing, make_product, make_set

KEYS = {
    "RIPRAPTOR_EBAY_APP_ID": "RipRapto-RipRapto-PRD-abc",
    "RIPRAPTOR_EBAY_CERT_ID": "PRD-secret",
    "RIPRAPTOR_EBAY_CAMPAIGN_ID": "5339012345",
}


def item(title, price="79.99", postage="0.00", item_id="v1|123|0", feedback="99.5", currency="GBP", postage_known=True):
    row = {
        "itemId": item_id,
        "title": title,
        "price": {"value": price, "currency": currency},
        "itemWebUrl": f"https://www.ebay.co.uk/itm/{item_id.split('|')[1]}",
        "itemAffiliateWebUrl": f"https://www.ebay.co.uk/itm/{item_id.split('|')[1]}?mkevt=1&campid=5339012345",
        "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
        "seller": {"username": "cardshop", "feedbackPercentage": feedback},
        "condition": "New",
    }
    if postage_known:
        row["shippingOptions"] = [{"shippingCost": {"value": postage, "currency": "GBP"}}]
    return row


def limits(searches=4900, bulk=5000):
    return {"rateLimits": [{"resources": [
        {"name": "buy.browse", "rates": [{"remaining": searches, "limit": 5000, "reset": "2026-10-02T07:00:00.000Z"}]},
        {"name": "buy.browse.item.bulk", "rates": [{"remaining": bulk, "limit": 5000, "reset": "2026-10-02T07:00:00.000Z"}]},
    ]}]}


class FakeApi:
    def __init__(self, results, items=None, searches_left=4900, fail_after=None):
        self.results = results
        self.items = items or []
        self.searches_left = searches_left
        self.fail_after = fail_after
        self.calls = []

    def __call__(self, url, headers, data=None):
        self.calls.append((url, headers, data))
        if url == ebay.TOKEN_URL:
            return {"access_token": "tok", "expires_in": 7200}
        if url.startswith(ebay.LIMITS_URL):
            return limits(self.searches_left)
        if url.startswith(ebay.ITEMS_URL):
            return {"items": self.items}
        searches = sum(1 for u, _, _ in self.calls if u.startswith(ebay.SEARCH_URL))
        if self.fail_after is not None and searches > self.fail_after:
            raise ebay.EbayError("eBay API 429: Too many requests.")
        return {"itemSummaries": self.results}

    def searches(self):
        return [u for u, _, _ in self.calls if u.startswith(ebay.SEARCH_URL)]


class TokenTests(TestCase):
    def test_token_request_uses_basic_auth_and_client_credentials(self):
        api = FakeApi([])
        self.assertEqual(ebay.access_token("app", "cert", request=api), "tok")
        url, headers, data = api.calls[0]
        self.assertEqual(url, ebay.TOKEN_URL)
        self.assertEqual(headers["Authorization"], "Basic YXBwOmNlcnQ=")
        self.assertIn(b"grant_type=client_credentials", data)

    def test_no_token_is_a_clear_error(self):
        with self.assertRaises(ebay.EbayError):
            ebay.access_token("app", "cert", request=lambda *a, **k: {"error": "invalid_client"})

    def test_missing_keys_are_a_clear_error(self):
        with self.assertRaises(ebay.EbayError):
            ebay.credentials()


class ItemTests(TestCase):
    def setUp(self):
        self.product = make_product(make_set(make_game()), ean="0820650853500")

    def test_result_becomes_an_offer_with_postage_and_affiliate_link(self):
        offer = ebay.item_offer(item("Pokemon Prismatic Evolutions Elite Trainer Box", postage="3.49"), self.product, Offer)
        self.assertEqual(offer.price, Decimal("79.99"))
        self.assertEqual(offer.delivery, Decimal("3.49"))
        self.assertEqual(offer.product_pk, self.product.pk)
        self.assertIn("campid=5339012345", offer.url)

    def test_unknown_postage_low_feedback_and_other_currency_are_skipped(self):
        self.assertIsNone(ebay.item_offer(item("Box", postage_known=False), self.product, Offer))
        self.assertIsNone(ebay.item_offer(item("Box", feedback="80.0"), self.product, Offer))
        self.assertIsNone(ebay.item_offer(item("Box", currency="EUR"), self.product, Offer))

    def test_search_uses_barcode_when_we_have_one_and_name_otherwise(self):
        self.assertIn("gtin=0820650853500", ebay.search_url(self.product))
        self.product.ean = ""
        self.assertIn("q=Pokemon+Prismatic+Evolutions+Elite+Trainer+Box", ebay.search_url(self.product))
        self.assertIn("conditions%3A%7BNEW%7D", ebay.search_url(self.product))


@override_settings(**KEYS)
class LookupTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.bundle = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle")
        self.retailer = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )

    def test_cheapest_matching_result_wins_and_singles_are_skipped(self):
        api = FakeApi([
            item("Prismatic Evolutions Umbreon ex 161/131 single card", price="40.00", item_id="v1|1|0"),
            item("Pokemon TCG Prismatic Evolutions Elite Trainer Box sealed", price="79.99", item_id="v1|2|0"),
            item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="85.00", item_id="v1|3|0"),
        ])
        offers = ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("79.99"))
        self.assertEqual(offers[0].product_pk, self.etb.pk)
        search_url = api.searches()[0]
        headers = api.calls[-1][1]
        self.assertIn("gtin=0820650853500", search_url)
        self.assertEqual(headers["X-EBAY-C-MARKETPLACE-ID"], "EBAY_GB")
        self.assertIn("affiliateCampaignId=5339012345", headers["X-EBAY-C-ENDUSERCTX"])
        self.etb.refresh_from_db()
        self.assertIsNotNone(self.etb.ebay_checked_at)

    def test_the_cheapest_delivered_match_wins_even_when_listed_later(self):
        api = FakeApi([
            item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="80.00", postage="4.99", item_id="v1|2|0"),
            item("Pokemon TCG Prismatic Evolutions Elite Trainer Box sealed", price="82.00", postage="0.00", item_id="v1|3|0"),
        ])
        offers = ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertEqual(offers[0].price + offers[0].delivery, Decimal("82.00"))

    def test_name_search_needs_a_full_match(self):
        self.etb.delete()
        api = FakeApi([item("Prismatic Evolutions Booster Bundle x2 bundles", price="30.00")])
        self.assertEqual(ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0), [])
        api = FakeApi([item("Pokemon Prismatic Evolutions Booster Bundle", price="30.00")])
        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        self.assertEqual([o.product_pk for o in offers], [self.bundle.pk])

    def test_widely_stocked_products_are_looked_up_first(self):
        from .testing import make_retailer

        make_listing(self.bundle, make_retailer("Shop A"), price="30.00")
        make_listing(self.bundle, make_retailer("Shop B"), price="31.00")
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("Booster+Bundle", api.searches()[0])

    def test_a_clicked_product_is_looked_up_before_an_unclicked_one(self):
        from .testing import make_retailer

        # Without interest the barcode puts the Elite Trainer Box first.
        cache.clear()
        self.addCleanup(cache.clear)
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("gtin=0820650853500", api.searches()[0])
        Product.objects.update(ebay_checked_at=None)
        # A click on the bundle moves it ahead, even of a product never tried.
        OutboundClick.objects.create(product=self.bundle, retailer=make_retailer("Shop A"))
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("Booster+Bundle", api.searches()[0])
        # Searched today and still wanted, the bundle waits behind the box, never searched.
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("gtin=0820650853500", api.searches()[0])
        # Three days on, its clicks put it first again.
        Product.objects.update(ebay_checked_at=timezone.now() - datetime.timedelta(days=3, minutes=1))
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("Booster+Bundle", api.searches()[0])

    def test_a_new_or_pre_order_product_searched_lately_does_not_go_before_one_never_searched(self):
        from .testing import make_retailer

        cache.clear()
        self.addCleanup(cache.clear)
        # An old box never searched, and a bundle added today, on pre-order and searched an hour ago.
        Product.objects.filter(pk=self.etb.pk).update(created_at=timezone.now() - datetime.timedelta(days=60))
        make_listing(self.bundle, make_retailer("Shop A"), availability=Listing.Availability.PREORDER)
        Product.objects.filter(pk=self.bundle.pk).update(ebay_checked_at=timezone.now() - datetime.timedelta(hours=1))
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("gtin=0820650853500", api.searches()[0])
        # Then the one searched longest ago, as before.
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("Booster+Bundle", api.searches()[0])

    def test_equal_interest_keeps_the_existing_order(self):
        from .testing import make_retailer

        cache.clear()
        self.addCleanup(cache.clear)
        shop = make_retailer("Shop A")
        for product in (self.etb, self.bundle):
            OutboundClick.objects.create(product=product, retailer=shop)
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("gtin=0820650853500", api.searches()[0])
        # The box was tried and found nothing, so the bundle, never tried, goes first next time.
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("Booster+Bundle", api.searches()[0])

    def test_search_other_shops_now_also_moves_a_product_up_the_ebay_queue(self):
        from . import finder

        cache.clear()
        self.addCleanup(cache.clear)
        finder.boost(self.bundle.pk)
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("Booster+Bundle", api.searches()[0])

    def test_a_duplicate_catalogue_entry_does_not_steal_the_match(self):
        twin = make_product(self.set, name="Pokemon Prismatic Evolutions Elite Trainer Box", slug="pev-etb-twin")
        api = FakeApi([item("Pokemon Prismatic Evolutions Elite Trainer Box", price="79.99")])
        offers = ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertEqual([o.product_pk for o in offers], [self.etb.pk])
        self.assertIsNotNone(twin.pk)

    def test_known_listings_are_refreshed_in_bulk_without_spending_searches(self):
        make_listing(self.bundle, self.retailer, price="30.00", url="https://www.ebay.co.uk/itm/9?campid=1")
        fresh = item("Pokemon Prismatic Evolutions Booster Bundle", price="28.50", item_id="v1|9|0")
        fresh["itemId"] = "v1|9|0"
        api = FakeApi([], items=[fresh])
        offers = ebay.ebay_offers(self.retailer, limit=0, request=api, pause=0)
        bulk = [u for u, _, _ in api.calls if u.startswith(ebay.ITEMS_URL)]
        self.assertEqual(len(bulk), 1)
        self.assertIn("item_ids=v1%7C9%7C0", bulk[0].replace("|", "%7C"))
        self.assertEqual(api.searches(), [])
        self.assertEqual([(o.product_pk, o.price) for o in offers], [(self.bundle.pk, Decimal("28.50"))])

    def test_one_ended_listing_does_not_stop_the_rest_being_refreshed(self):
        etb_listing = make_listing(self.etb, self.retailer, price="80.00", url="https://www.ebay.co.uk/itm/7?campid=1")
        make_listing(self.bundle, self.retailer, price="30.00", url="https://www.ebay.co.uk/itm/9?campid=1")
        live = item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="78.00", item_id="v1|7|0")
        live["itemId"] = "v1|7|0"
        bulk_calls = []

        def api(url, headers, data=None):
            if url.startswith(ebay.ITEMS_URL):
                bulk_calls.append(url)
                if "9" in url.split("item_ids=")[1]:
                    raise ebay.EbayError('eBay API 404: {"errors":[{"errorId":11001,"message":"The specified item Id was not found."}]}')
                return {"items": [live]}
            return FakeApi([])(url, headers, data)

        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        by_product = {o.product_pk: o for o in offers}
        self.assertEqual(by_product[self.etb.pk].price, Decimal("78.00"))
        # The ended one was searched for again, found nothing, and is marked sold out.
        self.assertEqual(by_product[self.bundle.pk].availability, Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(len(bulk_calls), 3)   # the batch, then each listing on its own
        self.assertIsNotNone(etb_listing.pk)

    def test_a_listing_the_bulk_lookup_no_longer_knows_is_searched_again_then_marked_sold_out(self):
        make_listing(self.bundle, self.retailer, price="30.00", url="https://www.ebay.co.uk/itm/9?campid=1")
        api = FakeApi([], items=[])
        offers = ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertIn("q=Pokemon+Prismatic+Evolutions+Booster+Bundle", api.searches()[0])
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].availability, Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(offers[0].url, "https://www.ebay.co.uk/itm/9?campid=1")

    def test_a_used_up_allowance_is_an_error_so_the_next_hour_tries_again(self):
        api = FakeApi([], searches_left=20)
        with self.assertRaises(ebay.EbayError) as caught:
            ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        self.assertIn("resets at 2026-10-02T07:00:00.000Z", str(caught.exception))
        self.assertEqual(api.searches(), [])

    def test_the_run_stays_inside_what_is_left_today(self):
        for n in range(5):
            make_product(self.set, name=f"Set {n} Booster Box", slug=f"set-{n}-box")
        api = FakeApi([], searches_left=ebay.KEEP_BACK + 2)
        ebay.ebay_offers(self.retailer, limit=100, request=api, pause=0)
        self.assertEqual(len(api.searches()), 2)

    def test_hitting_the_wall_mid_run_keeps_what_was_found(self):
        api = FakeApi([item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="79.99")], fail_after=1)
        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        self.assertEqual(len(offers), 1)
        self.etb.refresh_from_db()
        self.assertIsNotNone(self.etb.ebay_checked_at)

    def test_a_dropped_connection_mid_run_skips_that_product_and_keeps_going(self):
        make_product(self.set, name="Set 9 Booster Box", slug="set-9-box")
        api = FakeApi([item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="79.99")])
        real = api.__call__
        state = {"n": 0}

        def flaky(url, headers, data=None):
            if url.startswith(ebay.SEARCH_URL):
                state["n"] += 1
                if state["n"] == 1:
                    raise ebay.EbayError("eBay API unreachable: [Errno 104] Connection reset by peer")
            return real(url, headers, data)

        products = Product.objects.filter(is_active=True).count()
        ebay.ebay_offers(self.retailer, limit=50, request=flaky, pause=0)   # does not raise
        self.assertEqual(state["n"], products)   # every product was still looked up

    def test_eBay_down_for_good_stops_the_run_and_keeps_what_was_found(self):
        for n in range(10):
            make_product(self.set, name=f"Set {n} Booster Box", slug=f"set-{n}-box")
        api = FakeApi([item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="79.99")])
        real = api.__call__
        state = {"n": 0}

        def dies(url, headers, data=None):
            if url.startswith(ebay.SEARCH_URL):
                state["n"] += 1
                if state["n"] > 1:
                    raise ebay.EbayError("eBay API unreachable: [Errno 104] Connection reset by peer")
            return real(url, headers, data)

        offers = ebay.ebay_offers(self.retailer, limit=20, request=dies, pause=0)
        self.assertEqual(state["n"], 1 + ebay.GIVE_UP_AFTER)
        self.assertEqual(len(offers), 1)

    def test_progress_is_written_to_the_run_every_hundred_products(self):
        for n in range(120):
            make_product(self.set, name=f"Set {n} Booster Box", slug=f"set-{n}-box")
        run = ImportRun.objects.create(retailer=self.retailer)
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=150, request=api, pause=0, run=run)
        run.refresh_from_db()
        # The limit counts searches; the run reports the products it got through.
        self.assertEqual(run.offers_found, Product.objects.filter(ebay_checked_at__isnull=False).count())
        self.assertLessEqual(len(api.searches()), 150)

    def test_limit_counts_products_not_results(self):
        api = FakeApi([])
        ebay.ebay_offers(self.retailer, limit=1, request=api, pause=0)
        self.assertEqual(len(api.searches()), 1)


@override_settings(**KEYS)
class ImportTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", ean="0820650853500")
        self.retailer = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )

    def test_import_creates_a_listing_with_postage_in_the_delivered_price(self):
        api = FakeApi([item("Pokemon Prismatic Evolutions Elite Trainer Box", price="79.99", postage="3.49")])
        with mock.patch.object(ebay, "http", api), mock.patch.object(ebay, "PAUSE", 0):
            run = run_import(self.retailer)
        self.assertEqual(run.error, "")
        listing = Listing.objects.get(retailer=self.retailer, product=self.etb)
        self.assertEqual(listing.price, Decimal("79.99"))
        self.assertEqual(listing.delivery_cost, Decimal("3.49"))
        self.assertEqual(listing.delivered_price, Decimal("83.48"))
        self.assertIn("campid=5339012345", listing.url)

    def test_a_partial_run_leaves_unchecked_products_alone(self):
        other = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle")
        kept = make_listing(other, self.retailer, price="30.00")
        with mock.patch.object(ebay, "ebay_offers", return_value=[]):
            run_import(self.retailer)
        kept.refresh_from_db()
        self.assertEqual(kept.availability, Listing.Availability.IN_STOCK)

    def test_import_runs_at_most_once_a_day(self):
        ImportRun.objects.create(retailer=self.retailer, offers_found=10, finished_at=timezone.now() - datetime.timedelta(hours=2))
        with mock.patch.object(ebay, "http") as called:
            run = run_import(self.retailer)
        called.assert_not_called()
        self.assertEqual(run.error, "")

    def test_a_skipped_hour_does_not_postpone_tomorrows_fetch(self):
        # A real fetch 19 hours ago, then the hourly import skipped eBay (as it should).
        ImportRun.objects.create(retailer=self.retailer, offers_found=5, finished_at=timezone.now() - datetime.timedelta(hours=19))
        with mock.patch.object(ebay, "http") as called:
            run_import(self.retailer)
        called.assert_not_called()
        self.assertEqual(ImportRun.objects.filter(retailer=self.retailer).count(), 1)   # the skip left no run behind
        # Later the real fetch is older than the daily window, so eBay must be read again.
        ImportRun.objects.update(finished_at=timezone.now() - datetime.timedelta(hours=25))
        api = FakeApi([item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="79.99")])
        with mock.patch.object(ebay, "http", api):
            run = run_import(self.retailer)
        self.assertTrue(api.searches())
        self.assertEqual(run.error, "")

    def test_old_empty_skip_runs_do_not_block_the_fetch(self):
        # What the server holds now: a stack of empty runs from hourly skips, the newest minutes old.
        for hours in (3, 2, 1):
            ImportRun.objects.create(retailer=self.retailer, finished_at=timezone.now() - datetime.timedelta(hours=hours))
        api = FakeApi([item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="79.99")])
        with mock.patch.object(ebay, "http", api):
            run_import(self.retailer)
        self.assertTrue(api.searches())

    def test_missing_keys_are_reported_not_raised(self):
        with override_settings(RIPRAPTOR_EBAY_APP_ID=""):
            run = run_import(self.retailer)
        self.assertIn("RIPRAPTOR_EBAY_APP_ID", run.error)

    def test_setup_shops_adds_ebay_only_with_keys(self):
        self.retailer.delete()
        out = StringIO()
        with override_settings(RIPRAPTOR_EBAY_APP_ID=""):
            call_command("setup_shops", stdout=out)
        self.assertFalse(Retailer.objects.filter(slug="ebay").exists())
        call_command("setup_shops", stdout=out)
        self.assertEqual(Retailer.objects.get(slug="ebay").source_type, Retailer.Source.EBAY)
        self.assertEqual(Product.objects.count(), 1)


class HttpRetryTests(TestCase):
    def test_a_reset_connection_is_retried_then_succeeds(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": 1}'
        calls = {"n": 0}

        def opener(request, timeout=30):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionResetError(104, "Connection reset by peer")
            return Response()

        with mock.patch("urllib.request.urlopen", opener), mock.patch("time.sleep"):
            self.assertEqual(ebay.http("https://api.ebay.com/x", {}), {"ok": 1})
        self.assertEqual(calls["n"], 3)

    def test_a_connection_that_never_comes_back_is_an_error(self):
        def opener(request, timeout=30):
            raise ConnectionResetError(104, "Connection reset by peer")

        with mock.patch("urllib.request.urlopen", opener), mock.patch("time.sleep"):
            with self.assertRaises(ebay.EbayError):
                ebay.http("https://api.ebay.com/x", {})


class ThisProductTests(TestCase):
    def setUp(self):
        self.game = make_game(name="Star Wars Unlimited", slug="star-wars-unlimited", search_aliases="swu")
        self.set = make_set(self.game, name="Jump to Lightspeed", slug="jtl", code="JTL")
        self.box = make_product(self.set, name="Jump To Lightspeed Booster Box", slug="jtl-box", product_type="booster_box")
        self.pack = make_product(self.set, name="Jump to Lightspeed Booster Pack", slug="jtl-pack", product_type="booster_pack")
        self.han = make_product(self.set, name="Jump to Lightspeed Spotlight Deck Han Solo", slug="jtl-han", product_type="deck")
        self.spotlight = make_product(self.set, name="Jump to Lightspeed Spotlight Deck", slug="jtl-spot", product_type="deck")
        self.specifics = ebay.Specifics(Product.objects.values_list("pk", "name"))

    def ok(self, product, title):
        return ebay.is_this_product(product, title, self.specifics)

    def test_padded_seller_titles_are_accepted(self):
        self.assertTrue(self.ok(self.box, "Star Wars Unlimited Jump to Lightspeed : Sealed Booster Box of 24 Packs"))
        self.assertTrue(self.ok(self.han, "Star Wars: Unlimited - Jump to Lightspeed Spotlight Deck : Han Solo"))
        self.assertTrue(self.ok(self.box, "Star Wars Unlimited Jump to Lightspeed Booster Display - English New"))

    def test_the_wrong_thing_is_refused(self):
        self.assertFalse(self.ok(self.box, "Star Wars Unlimited Jump to Lightspeed Booster Pack x3"))
        self.assertFalse(self.ok(self.pack, "Star Wars Unlimited Jump to Lightspeed 5 Booster Packs sealed"))
        self.assertFalse(self.ok(self.pack, "Star Wars Unlimited Jump to Lightspeed Booster Pack Japanese"))
        self.assertFalse(self.ok(self.box, "Jump to Lightspeed Booster Box Sleeves 60 pack"))
        self.assertFalse(self.ok(self.box, "Jump to Lightspeed Booster Box Play Mat"))
        self.assertFalse(self.ok(self.han, "Star Wars Unlimited Jump to Lightspeed Spotlight Deck Display (6)"))
        self.assertFalse(self.ok(self.han, "Jump to Lightspeed Spotlight Deck Han Solo (Deck Only)"))
        self.assertFalse(self.ok(self.box, "Jump to Lightspeed Booster Box 20 tokens from the box"))

    def test_stickers_and_too_short_names_are_refused(self):
        self.assertFalse(self.ok(self.han, "Jump to Lightspeed Spotlight Deck Han Solo Sticker Collection"))
        short = make_product(self.set, name="151 Booster Pack", slug="151-pack", product_type="booster_pack")
        self.assertFalse(self.ok(short, "Pokemon Collect 151: Surprise Slim Booster Pack 151C Sealed"))

    def test_a_more_specific_product_of_ours_keeps_its_listing(self):
        self.assertFalse(self.ok(self.spotlight, "Star Wars Unlimited Jump to Lightspeed Spotlight Deck Han Solo"))
        self.assertTrue(self.ok(self.han, "Star Wars Unlimited Jump to Lightspeed Spotlight Deck Han Solo"))

    def test_queries_lead_with_the_game_and_fall_back_to_a_plainer_name(self):
        football = make_game(name="Football cards", slug="football", search_aliases="")
        tin = make_product(make_set(football, name="WSL", slug="wsl", code="WSL"),
                           name="Women's WSL Eternity 2025/26 Official Trading Card Hobby Box (Soccer)", slug="wsl-box",
                           product_type="booster_box")
        self.assertEqual(ebay.search_queries(self.box)[0], "Star Wars Unlimited Jump To Lightspeed Booster Box")
        queries = ebay.search_queries(tin)
        self.assertEqual(queries[0], "Women's WSL Eternity 2025/26 Official Trading Card Hobby Box (Soccer)")
        self.assertEqual(queries[1], "Women's WSL Eternity 2025/26 Hobby Box")


@override_settings(**KEYS)
class FallbackSearchTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.retailer = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )

    def test_a_barcode_with_no_results_falls_back_to_the_name(self):
        product = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle",
                               product_type="bundle", ean="0196214108325")
        state = {"n": 0}
        real = FakeApi([item("Pokemon TCG Prismatic Evolutions Booster Bundle", price="29.99")])

        def api(url, headers, data=None):
            if url.startswith(ebay.SEARCH_URL):
                state["n"] += 1
                if "gtin=" in url:
                    return {}
            return real(url, headers, data)

        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        self.assertEqual([o.product_pk for o in offers], [product.pk])
        self.assertEqual(state["n"], 2)

    def test_a_listing_far_below_the_shops_price_is_refused(self):
        from .testing import make_retailer

        bundle = make_product(self.set, name="The Hobbit Bundle", slug="hobbit-bundle", product_type="bundle")
        make_listing(bundle, make_retailer("Shop"), price="50.00")
        api = FakeApi([
            item("MTG The Hobbit Bundle 50 card bundle", price="8.66", item_id="v1|1|0"),
            item("Magic The Gathering The Hobbit Bundle Sealed", price="46.00", item_id="v1|2|0"),
        ])
        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        self.assertEqual([(o.product_pk, o.price) for o in offers], [(bundle.pk, Decimal("46.00"))])

    def test_a_sold_out_shop_still_sets_the_floor(self):
        from .testing import make_retailer

        pack = make_product(self.set, name="Darkness Ablaze Booster Pack", slug="da-pack", product_type="booster_pack")
        make_listing(pack, make_retailer("Shop"), price="3.95", delivery="3.95",
                     availability=Listing.Availability.OUT_OF_STOCK)
        api = FakeApi([
            item("Pokemon Darkness Ablaze Booster Pack", price="1.36", item_id="v1|1|0"),
            item("Pokemon TCG Darkness Ablaze Booster Pack Sealed", price="5.50", item_id="v1|2|0"),
        ])
        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        self.assertEqual([(o.product_pk, o.price) for o in offers], [(pack.pk, Decimal("5.50"))])

    def test_a_known_listing_now_too_cheap_is_searched_for_again(self):
        from .testing import make_retailer

        pack = make_product(self.set, name="Darkness Ablaze Booster Pack", slug="da-pack", product_type="booster_pack")
        make_listing(pack, make_retailer("Shop"), price="4.95", availability=Listing.Availability.OUT_OF_STOCK)
        make_listing(pack, self.retailer, price="1.36", url="https://www.ebay.co.uk/itm/1?campid=1")
        cheap = item("Pokemon Darkness Ablaze Booster Pack", price="1.36", item_id="v1|1|0")
        api = FakeApi([item("Pokemon TCG Darkness Ablaze Booster Pack Sealed", price="5.50", item_id="v1|2|0")],
                      items=[cheap])
        offers = ebay.ebay_offers(self.retailer, limit=5, request=api, pause=0)
        # Taken off the site first, then replaced by the genuine listing the search found.
        self.assertEqual([(o.product_pk, o.price, o.availability) for o in offers], [
            (pack.pk, Decimal("1.36"), Listing.Availability.OUT_OF_STOCK),
            (pack.pk, Decimal("5.50"), Listing.Availability.IN_STOCK),
        ])
        self.assertEqual(len(api.searches()), 1)

    def test_a_known_listing_in_another_language_is_hidden_even_when_searches_run_out(self):
        pack = make_product(self.set, name="30th Celebration Booster Pack", slug="30th-pack", product_type="booster_pack")
        make_listing(pack, self.retailer, price="5.44", url="https://www.ebay.co.uk/itm/1?campid=1")
        chinese = item("CHS Pokémon TCG 30th Celebration Booster Pack", price="5.44", item_id="v1|1|0")
        api = FakeApi([], items=[chinese])
        offers = ebay.ebay_offers(self.retailer, limit=0, request=api, pause=0)
        self.assertEqual([(o.product_pk, o.availability) for o in offers], [(pack.pk, Listing.Availability.OUT_OF_STOCK)])
        self.assertEqual(api.searches(), [])

    def test_tidy_fetches_missing_titles_and_hides_a_chinese_pack(self):
        pack = make_product(self.set, name="30th Celebration Booster Pack", slug="30th-pack", product_type="booster_pack")
        chinese = make_listing(pack, self.retailer, price="5.44", url="https://www.ebay.co.uk/itm/1?campid=1")
        etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", slug="pev-etb")
        english = make_listing(etb, self.retailer, price="80.00", url="https://www.ebay.co.uk/itm/2?campid=1")
        rows = [item("CHS Pokémon TCG 30th Celebration Booster Pack", price="5.44", item_id="v1|1|0"),
                item("Pokemon TCG Prismatic Evolutions Elite Trainer Box", price="80.00", item_id="v1|2|0")]
        api = FakeApi([], items=rows)
        out = StringIO()
        with mock.patch.object(ebay, "http", api), mock.patch.object(ebay, "PAUSE", 0):
            call_command("tidy_listings", stdout=out)
        chinese.refresh_from_db()
        english.refresh_from_db()
        self.assertEqual(chinese.title, "CHS Pokémon TCG 30th Celebration Booster Pack")
        self.assertEqual(chinese.availability, Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(english.availability, Listing.Availability.IN_STOCK)
        self.assertIn("2 eBay titles fetched", out.getvalue())
        self.assertEqual(api.searches(), [])

    def test_titles_are_not_fetched_when_every_listing_has_one(self):
        etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", slug="pev-etb")
        make_listing(etb, self.retailer, price="80.00", url="https://www.ebay.co.uk/itm/2?campid=1",
                     title="Pokemon TCG Prismatic Evolutions Elite Trainer Box")
        api = FakeApi([])
        self.assertEqual(ebay.fill_titles(request=api, pause=0), 0)
        self.assertEqual(api.calls, [])

    def test_language_shorthand_and_foreign_script_are_another_edition(self):
        pack = make_product(self.set, name="30th Celebration Booster Pack", slug="30th-pack", product_type="booster_pack")
        jp = make_product(self.set, name="Japanese Pokemon: Abyss Eye Booster Pack", slug="abyss-jp", product_type="booster_pack")
        for title in ("CHS Pokémon TCG 30th Celebration Booster Pack", "Pokemon 30th Celebration Booster Pack (SC) Sealed",
                      "Pokemon 30th Celebration Booster Pack S-Chinese", "Pokemon 30th Celebration Booster Pack JPN",
                      "宝可梦 Pokemon 30th Celebration Booster Pack", "Pokemon TCG 30th Celebration Booster Pack KOR",
                      "Pokemon 30th Celebration JP Booster Pack"):
            self.assertTrue(ebay.junk(pack, title), title)
        # "SC" alone is also a set code, and an all-capitals "KOR" may be Magic's Kor.
        self.assertFalse(ebay.junk(pack, "Pokemon 30th Celebration Booster Pack SC Sealed"))
        self.assertFalse(ebay.junk(pack, "Pokemon TCG 30th Celebration Booster Pack English Sealed"))
        self.assertFalse(ebay.junk(pack, "Pokemon TCG 30th Celebration Booster Pack TCG Sealed"))
        self.assertFalse(ebay.junk(jp, "Pokemon Abyss Eye Booster Pack JPN Sealed"))
        self.assertFalse(ebay.junk(jp, "ポケモン Pokemon Abyss Eye Booster Pack Japanese"))
        self.assertTrue(ebay.junk(jp, "Pokemon Abyss Eye Booster Pack CHS"))

    def test_the_import_keeps_the_ebay_title_and_tidy_hides_one_the_rules_now_refuse(self):
        from io import StringIO

        from django.core.management import call_command

        pack = make_product(self.set, name="XY Breakpoint Booster Pack", slug="xy-bp-pack", product_type="booster_pack")
        api = FakeApi([item("Pokemon XY Breakpoint Booster Pack Sealed", price="9.00", item_id="v1|1|0")])
        with mock.patch.object(ebay, "http", api), mock.patch.object(ebay, "PAUSE", 0):
            run_import(self.retailer)
        listing = Listing.objects.get(retailer=self.retailer, product=pack)
        self.assertEqual(listing.title, "Pokemon XY Breakpoint Booster Pack Sealed")
        # A match saved before the rule existed: its title now fails, so the tidy-up hides it.
        Listing.objects.filter(pk=listing.pk).update(title="Pokemon XY Breakpoint Sampling Pack Booster Pack NEW and SEALED")
        call_command("tidy_listings", stdout=StringIO())
        listing.refresh_from_db()
        self.assertEqual(listing.availability, Listing.Availability.OUT_OF_STOCK)

    def test_tidy_hides_an_ebay_price_far_under_the_shops(self):
        from io import StringIO

        from django.core.management import call_command

        from .testing import make_retailer

        pack = make_product(self.set, name="Darkness Ablaze Booster Pack", slug="da-pack", product_type="booster_pack")
        make_listing(pack, make_retailer("Shop"), price="3.95", delivery="3.95",
                     availability=Listing.Availability.OUT_OF_STOCK)
        cheap = make_listing(pack, self.retailer, price="1.36", url="https://www.ebay.co.uk/itm/1?campid=1")
        fair = make_product(self.set, name="Darkness Ablaze Elite Trainer Box", slug="da-etb")
        make_listing(fair, make_retailer("Other"), price="60.00")
        kept = make_listing(fair, self.retailer, price="45.00", url="https://www.ebay.co.uk/itm/2?campid=1")
        with mock.patch.object(ebay, "http", FakeApi([])), mock.patch.object(ebay, "PAUSE", 0):
            call_command("tidy_listings", stdout=StringIO())
        cheap.refresh_from_db()
        kept.refresh_from_db()
        self.assertEqual(cheap.availability, Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(kept.availability, Listing.Availability.IN_STOCK)

    def test_tidy_hiding_an_ebay_price_clears_the_lists_inside_the_window(self):
        from django.core.cache import cache

        from .signals import HOME_CACHE_KEY
        from .testing import inside_the_cache_window, make_retailer

        pack = make_product(self.set, name="Darkness Ablaze Booster Pack", slug="da-pack", product_type="booster_pack")
        make_listing(pack, make_retailer("Shop"), price="3.95", delivery="3.95",
                     availability=Listing.Availability.OUT_OF_STOCK)
        make_listing(pack, self.retailer, price="1.36", url="https://www.ebay.co.uk/itm/1?campid=1")
        inside_the_cache_window(self)
        cache.set(HOME_CACHE_KEY, "lists", 300)
        with mock.patch.object(ebay, "http", FakeApi([])), mock.patch.object(ebay, "PAUSE", 0):
            call_command("tidy_listings", stdout=StringIO())
        self.assertIsNone(cache.get(HOME_CACHE_KEY))

    def test_check_command_shows_the_pick_and_why_others_were_refused(self):
        from io import StringIO

        from django.core.management import call_command

        from .testing import make_retailer

        pack = make_product(self.set, name="Darkness Ablaze Booster Pack", slug="da-pack", product_type="booster_pack")
        make_listing(pack, make_retailer("Shop"), price="3.95", delivery="3.95",
                     availability=Listing.Availability.OUT_OF_STOCK)
        api = FakeApi([
            item("Pokemon Darkness Ablaze Booster Pack", price="1.36", item_id="v1|1|0"),
            item("Pokemon Darkness Ablaze Booster Pack X1 Sealed New choose your artwork", price="5.99", item_id="v1|2|0"),
            item("Pokemon Darkness Ablaze Sleeved Booster Pack x3", price="17.00", item_id="v1|3|0"),
            item("Pokemon Darkness Ablaze Sampling Pack Booster Pack NEW and SEALED", price="12.94", item_id="v1|8|0"),
            item("Pokemon Darkness Ablaze 3 Card Sample Booster Pack", price="4.50", item_id="v1|9|0"),
            item("50 X Darkness Ablaze Booster Pack TCGO Codes Pokemon Trading Card Game Online", price="3.48", item_id="v1|4|0"),
            item("1x Pokemon Sword & Darkness Ablaze Fun Pack New Sealed Booster Pack UK", price="4.16", item_id="v1|5|0"),
            item("Pokémon - Sword & Shield - Darkness Ablaze - Booster x2", price="18.85", item_id="v1|6|0"),
        ])
        out = StringIO()
        with mock.patch.object(ebay, "http", api), \
                mock.patch.object(ebay, "credentials", return_value=("a", "c", "5339000000")):
            call_command("ebay_check", "https://ripraptor.com/products/da-pack/", stdout=out)
        text = out.getvalue()
        self.assertIn("PICK    £5.99  Pokemon Darkness Ablaze Booster Pack X1 Sealed", text)
        self.assertIn("too cheap to be the sealed product (under £3.16)", text)
        self.assertEqual(text.count("a multi-buy, an online code, a part or another language"), 6)

    def test_check_command_says_when_the_allowance_is_spent(self):
        from io import StringIO

        from django.core.management import call_command

        make_product(self.set, name="Darkness Ablaze Booster Pack", slug="da-pack", product_type="booster_pack")
        out = StringIO()
        with mock.patch.object(ebay, "http", FakeApi([], searches_left=0)), \
                mock.patch.object(ebay, "credentials", return_value=("a", "c", "5339000000")):
            call_command("ebay_check", "da-pack", stdout=out)
        self.assertIn("searches for today are used up", out.getvalue())

    def test_coverage_report(self):
        from io import StringIO

        from django.core.management import call_command

        a = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle", product_type="bundle")
        b = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", slug="pev-etb")
        Product.objects.filter(pk__in=[a.pk, b.pk]).update(ebay_checked_at=timezone.now())
        make_listing(a, self.retailer, price="29.99", url="https://www.ebay.co.uk/itm/1")
        out = StringIO()
        call_command("ebay_report", stdout=out)
        text = out.getvalue()
        self.assertIn("Matched on eBay:             1 (50% of those looked up)", text)
        self.assertIn("Looked up on eBay:           2", text)


class ImportOrderTests(TestCase):
    def test_marketplaces_are_read_before_the_shops(self):
        from io import StringIO

        from django.core.management import call_command

        from .testing import make_retailer

        make_retailer("Aardvark Cards", source_type=Retailer.Source.SHOPIFY, source_url="https://a.example/")
        Retailer.objects.create(name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY)
        order = []

        def fake_run(retailer, feed_path=None):
            order.append(retailer.name)
            return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now())

        with mock.patch("catalogue.management.commands.import_prices.run_import", fake_run):
            out = StringIO()
            call_command("import_prices", stdout=out)
        self.assertEqual(order, ["eBay", "Aardvark Cards"])
        self.assertRegex(out.getvalue(), r"eBay: 0 offers, 0 listings updated in \d+s")


@override_settings(**KEYS)
class SaveAsFoundTests(TestCase):
    def setUp(self):
        self.set = make_set(make_game())
        self.retailer = Retailer.objects.create(
            name="eBay", slug="ebay", website="https://www.ebay.co.uk/", source_type=Retailer.Source.EBAY
        )
        self.etb = make_product(self.set, name="Prismatic Evolutions Elite Trainer Box", slug="pev-etb")
        self.bundle = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pev-bundle", product_type="bundle")

    def test_prices_found_before_an_interruption_are_kept(self):
        searches = {"n": 0}
        base = FakeApi([])

        def api(url, headers, data=None):
            if url.startswith(ebay.SEARCH_URL):
                searches["n"] += 1
                if searches["n"] == 1:
                    return {"itemSummaries": [item("Pokemon TCG Prismatic Evolutions Booster Bundle", price="29.99")]}
                raise KeyboardInterrupt   # the run is stopped part-way
            return base(url, headers, data)

        with mock.patch.object(ebay, "http", api), mock.patch.object(ebay, "PAUSE", 0):
            with self.assertRaises(KeyboardInterrupt):
                run_import(self.retailer)
        # The first price was saved before the stop.
        self.assertEqual(Listing.objects.get(retailer=self.retailer).product, self.bundle)
        # The interrupted run did not finish, so it does not count as today's fetch.
        self.assertFalse(ImportRun.objects.filter(retailer=self.retailer, finished_at__isnull=False).exists())
