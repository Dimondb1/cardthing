"""The stockist finder: which products are looked for where, how a shop's answer is judged, the caps that keep
whole-shop reads first, and the owner's Yes and No on Things to check.

The shop answers are trimmed copies of what two real shops (Gathering Games and The Card Vault, both in
setup_shops) sent to /search/suggest.json and /products/<handle>.js on 9 October 2026.
"""

import copy
import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import crawl, finder, worker
from .importers import ImportError_, run_import
from .models import (
    DailyPageView, ImportRun, Listing, OutboundClick, Product, Retailer, ShopProduct, StockistSearch,
)
from .testing import make_game, make_listing, make_product, make_retailer, make_set

DATA = Path(__file__).resolve().parent / "testdata" / "finder"
GG = "https://gatheringgames.co.uk"
PE_HANDLE = "pokemon-tcg-scarlet-violet-8-5-prismatic-evolutions-elite-trainer-box"
PE_TITLE = "Pokemon TCG: Scarlet & Violet 8.5 - Prismatic Evolutions Elite Trainer Box"
PE_BARCODE = "0196214105133"


def recorded(name):
    return json.loads((DATA / name).read_text())


def answer(data):
    return json.dumps(data).encode()


def product_page(available=False, barcode=PE_BARCODE):
    """The recorded /products/<handle>.js answer, with its stock or barcode changed when a test needs it."""
    data = recorded("gathering_games_product.js")
    for variant in data["variants"]:
        variant["available"] = available
        variant["barcode"] = barcode
    data["available"] = available
    return data


def suggest_with(*titles):
    """A recorded suggest answer whose products carry these titles instead."""
    data = recorded("gathering_games_suggest.json")
    template = data["resources"]["results"]["products"][2]
    products = []
    for n, title in enumerate(titles):
        item = copy.deepcopy(template)
        item["title"], item["handle"] = title, f"found-{n}"
        products.append(item)
    data["resources"]["results"]["products"] = products
    return data


class Shop:
    """A fetch that answers from a table of {url part: answer} and records every address asked."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, url, *args, **kwargs):
        self.calls.append(url)
        for part, reply in self.answers.items():
            if part in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply if isinstance(reply, bytes) else answer(reply)
        raise ImportError_(f"Could not fetch {url}: HTTP Error 404: Not Found")

    def asked(self, part):
        return [url for url in self.calls if part in url]


class Clock:
    def __init__(self):
        self.now = timezone.now()

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class FinderCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        game = make_game()
        self.set = make_set(game)
        self.etb = make_product(self.set, slug="pe-etb")
        self.home = make_retailer("Harbour Games", source_type=Retailer.Source.SHOPIFY, source_url="https://harbour.example/",
                                  delivery_cost=Decimal("0"))
        make_listing(self.etb, self.home, price="60.00")
        self.gg = make_retailer("Gathering Games", source_type=Retailer.Source.SHOPIFY, source_url=f"{GG}/",
                                website=f"{GG}/", delivery_cost=Decimal("0"))
        self.clock = Clock()
        self.sleeps = []

    def find(self, shop, limit=finder.BATCH, **kwargs):
        kwargs.setdefault("clock", self.clock)
        return finder.find(limit, fetch_for=lambda retailer: shop, sleep=self.sleeps.append, **kwargs)

    def unmatched_read(self, retailer, *lines):
        return ImportRun.objects.create(retailer=retailer, finished_at=timezone.now(), unmatched="\n".join(lines))

    def search(self, product, retailer):
        return StockistSearch.objects.get(product=product, retailer=retailer)


class CandidateTests(FinderCase):
    def setUp(self):
        super().setUp()
        self.quiet = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pe-bundle",
                                  product_type="bundle")
        make_listing(self.quiet, self.home, price="30.00")
        self.clicked = make_product(self.set, name="Prismatic Evolutions Surprise Box", slug="pe-surprise",
                                    product_type="collection_box")
        listing = make_listing(self.clicked, self.home, price="25.00")
        OutboundClick.objects.create(listing=listing, product=self.clicked, retailer=self.home)
        self.third = make_retailer("Card Vault", source_type=Retailer.Source.SHOPIFY, source_url="https://vault.example/")
        make_listing(self.etb, self.third, price="62.00")

    def order(self, **kwargs):
        return [c.product for c in finder.candidates(now=self.clock(), **kwargs)]

    def test_a_clicked_single_shop_product_comes_before_a_quiet_one_and_two_shops_leave_it_out(self):
        self.assertEqual(self.order(), [self.clicked, self.quiet])
        first = finder.candidates(now=self.clock())[0]
        self.assertEqual(first.shops, [self.third, self.gg])
        self.assertEqual(first.interest, finder.CLICK_POINTS + finder.NEW_POINTS)

    def test_a_pair_searched_lately_waits_a_hot_one_a_week_and_a_no_for_ever(self):
        now = self.clock()
        StockistSearch.objects.create(product=self.quiet, retailer=self.gg, searched_at=now - timedelta(days=3),
                                      outcome=StockistSearch.Outcome.NONE)
        StockistSearch.objects.create(product=self.quiet, retailer=self.third, searched_at=now - timedelta(days=8),
                                      outcome=StockistSearch.Outcome.NONE)
        self.assertNotIn(self.quiet, self.order())
        # Two more clicks make it hot (interest 10 or more): a pair searched 8 days ago is asked again.
        for _ in range(2):
            OutboundClick.objects.create(product=self.quiet, retailer=self.home)
        hot = {c.product: c.shops for c in finder.candidates(now=now)}
        self.assertEqual(hot[self.quiet], [self.third])
        # The owner said No: never again, however hot and however long ago.
        StockistSearch.objects.filter(product=self.quiet, retailer=self.third).update(
            outcome=StockistSearch.Outcome.IGNORED, searched_at=now - timedelta(days=400))
        self.assertNotIn(self.quiet, self.order())

    def test_a_shop_that_could_not_be_asked_is_asked_again_the_next_day(self):
        StockistSearch.objects.create(product=self.quiet, retailer=self.gg, searched_at=self.clock() - timedelta(hours=25),
                                      outcome=StockistSearch.Outcome.ERROR)
        shops = {c.product: c.shops for c in finder.candidates(now=self.clock())}
        self.assertIn(self.gg, shops[self.quiet])

    def test_paying_shops_are_searched_first_on_a_tie(self):
        self.gg.affiliate_url_template = "https://network.example/click?url={url}"
        self.gg.save()
        first = finder.candidates(now=self.clock())[0]
        self.assertEqual(first.shops, [self.gg, self.third])

    def test_only_active_unpaused_waiting_free_shopify_shops_are_candidates(self):
        make_retailer("Feed Shop", source_type=Retailer.Source.FEED, source_url="https://feed.example/f.csv")
        make_retailer("Off Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://off.example/", is_active=False)
        make_retailer("Paused Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://paused.example/",
                      reading_paused=True)
        make_retailer("Waiting Shop", source_type=Retailer.Source.SHOPIFY, source_url="https://waiting.example/",
                      backoff_until=self.clock() + timedelta(minutes=30))
        self.assertEqual(finder.searchable_shops(self.clock()), [self.third, self.gg, self.home])

    def test_interest_counts_clicks_views_watchlists_alerts_preorders_and_new(self):
        from .models import StockAlert

        today = timezone.localdate(self.clock())
        DailyPageView.objects.create(date=today, kind=DailyPageView.Kind.PRODUCT, key=self.quiet.slug, hits=2)
        DailyPageView.objects.create(date=today - timedelta(days=1), kind=DailyPageView.Kind.WATCHED, key=self.quiet.slug, hits=1)
        DailyPageView.objects.create(date=today - timedelta(days=3), kind=DailyPageView.Kind.PRODUCT, key=self.quiet.slug, hits=9)
        StockAlert.objects.create(product=self.quiet, email="a@example.com", confirmed_at=self.clock())
        Listing.objects.filter(product=self.quiet).update(availability=Listing.Availability.PREORDER)
        Product.objects.filter(pk=self.etb.pk).update(created_at=self.clock() - timedelta(days=30))
        scores = finder.interest_scores(self.clock())
        self.assertEqual(scores[self.quiet.pk], 3 * 2 + 3 * 1 + 10 + 2 + 2)
        self.assertNotIn(self.etb.pk, scores)

    def test_choosing_candidates_takes_a_fixed_number_of_queries(self):
        for n in range(5):
            product = make_product(self.set, name=f"Prismatic Evolutions Mini Tin {n}", slug=f"pe-tin-{n}", product_type="tin")
            make_listing(product, self.home, price="10.00")
        scores = finder.interest_scores(self.clock())
        shops = finder.searchable_shops(self.clock())
        with self.assertNumQueries(4):
            found = finder.candidates(now=self.clock(), shops=shops, scores=scores)
        self.assertEqual(len(found), 7)


class LastReadTests(FinderCase):
    def test_an_unmatched_line_that_agrees_both_ways_links_with_no_search(self):
        Product.objects.filter(pk=self.etb.pk).update(ean=PE_BARCODE)
        self.unmatched_read(self.gg, f"{PE_TITLE} [{PE_BARCODE}] {GG}/products/{PE_HANDLE}")
        shop = Shop({".js": product_page(available=True)})
        result = self.find(shop)
        self.assertEqual(shop.asked("suggest.json"), [])
        self.assertEqual(shop.calls, [f"{GG}/products/{PE_HANDLE}.js"])
        listing = Listing.objects.get(product=self.etb, retailer=self.gg)
        self.assertEqual((listing.price, listing.availability), (Decimal("149.99"), Listing.Availability.IN_STOCK))
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.LINKED)
        self.assertEqual((result.products, result.linked, result.review), (1, 1, 0))

    def test_a_single_card_line_is_never_a_candidate(self):
        self.unmatched_read(self.gg, f"Pokemon Prismatic Evolutions Umbreon ex 161/131 Special Illustration Rare [no barcode] {GG}/products/umbreon")
        shop = Shop({"suggest.json": recorded("empty_suggest.json")})
        self.find(shop)
        self.assertEqual(shop.asked("/products/"), [])
        self.assertEqual(len(shop.asked("suggest.json")), 2)   # the name, then the set code and kind
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.NONE)


class SuggestTests(FinderCase):
    def shop(self, page=None, suggest=None):
        return Shop({"suggest.json": suggest or recorded("gathering_games_suggest.json"), ".js": page or product_page()})

    def test_the_search_is_asked_for_the_game_and_our_name(self):
        shop = self.shop()
        self.find(shop)
        self.assertEqual(shop.asked("suggest.json")[0],
                         f"{GG}/search/suggest.json?q=Pokemon+Prismatic+Evolutions+Elite+Trainer+Box"
                         "&resources%5Btype%5D=product&resources%5Blimit%5D=10")

    def test_an_equal_barcode_links_through_apply_offers_with_the_verdict(self):
        Product.objects.filter(pk=self.etb.pk).update(ean=PE_BARCODE)
        Listing.objects.filter(product=self.etb, retailer=self.home).update(price=Decimal("40.00"))
        self.find(self.shop(page=product_page(available=True)))
        listing = Listing.objects.get(product=self.etb, retailer=self.gg)
        self.assertEqual(listing.url, f"{GG}/products/{PE_HANDLE}")
        self.assertEqual(listing.title, PE_TITLE)
        # £149.99 against £40 at the only other shop: judged like any other price.
        self.assertEqual(listing.sanity, Listing.Sanity.DOUBTFUL)
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.LINKED)

    def test_a_sure_name_links_when_neither_side_has_a_barcode(self):
        result = self.find(self.shop(page=product_page(barcode="")))
        listing = Listing.objects.get(product=self.etb, retailer=self.gg)
        self.assertEqual((listing.price, listing.availability), (Decimal("149.99"), Listing.Availability.OUT_OF_STOCK))
        self.assertEqual(result.linked, 1)

    def test_a_different_barcode_never_links(self):
        Product.objects.filter(pk=self.etb.pk).update(ean="0820650851230")
        self.find(self.shop())
        self.assertFalse(Listing.objects.filter(retailer=self.gg).exists())
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.REVIEW)

    def test_a_barcode_on_one_side_only_waits_for_a_tap(self):
        self.find(self.shop())
        self.assertFalse(Listing.objects.filter(retailer=self.gg).exists())
        row = ShopProduct.objects.get(retailer=self.gg)
        self.assertEqual((row.status, row.source, row.confidence), (ShopProduct.Status.REVIEW, ShopProduct.Source.FINDER, 100))

    def test_a_likely_match_writes_a_review_row_and_no_listing(self):
        Product.objects.filter(pk=self.etb.pk).update(name="Prismatic Evolutions Pokemon Center Elite Trainer Box")
        result = self.find(self.shop(page=product_page(barcode="")))
        self.assertFalse(Listing.objects.filter(retailer=self.gg).exists())
        row = ShopProduct.objects.get(retailer=self.gg)
        self.assertEqual((row.product, row.suggested, row.status), (self.etb, self.etb, ShopProduct.Status.REVIEW))
        self.assertEqual((row.title, row.price, row.url), (PE_TITLE, Decimal("149.99"), f"{GG}/products/{PE_HANDLE}"))
        self.assertTrue(60 <= row.confidence < 100)
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.REVIEW)
        self.assertEqual((result.linked, result.review), (0, 1))

    def test_another_set_of_the_same_kind_is_not_even_likely(self):
        suggest = suggest_with("Pokemon TCG: Mega Evolution Delta Reign - Elite Trainer Box")
        shop = self.shop(suggest=suggest)
        self.find(shop)
        self.assertEqual(shop.asked("/products/"), [])
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.NONE)

    def test_a_single_card_writes_outcome_none(self):
        shop = self.shop(suggest=suggest_with("Pokemon Prismatic Evolutions Umbreon ex 161/131 Special Illustration Rare"))
        self.find(shop)
        self.assertEqual(shop.asked("/products/"), [])
        self.assertFalse(ShopProduct.objects.exists())
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.NONE)

    def test_another_shops_answer_shape_reads_the_same(self):
        found = recorded("card_vault_suggest.json")["resources"]["results"]["products"]
        digimon = make_set(make_game("Digimon Card Game", slug="digimon"), name="Dual Revolution", slug="dual-revolution", code="BT25")
        product = make_product(digimon, name="Dual Revolution Booster Box", slug="dr-digimon", product_type="booster_box")
        hit = finder.Finder().best_result(product, self.gg, GG, found)
        self.assertEqual((hit.handle, hit.value), ("digimon-card-game-dual-revolution-bt25-booster-box-24-packs", 100))
        # The same shop writes "(36x Packs)" on its Pokemon boxes, which the sealed rules read as a multipack:
        # such a title is passed over, never guessed at.
        rivals = make_product(self.set, name="Destined Rivals Booster Box", slug="dr-box", product_type="booster_box")
        self.assertIsNone(finder.Finder().best_result(rivals, self.gg, GG, found))

    def test_every_candidate_is_stamped_even_when_nothing_is_found(self):
        self.find(Shop({"suggest.json": recorded("empty_suggest.json")}))
        self.etb.refresh_from_db()
        self.assertEqual(self.etb.finder_checked_at, self.clock())
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.NONE)


class BudgetTests(FinderCase):
    def setUp(self):
        super().setUp()
        self.other = make_product(self.set, name="Prismatic Evolutions Booster Bundle", slug="pe-bundle", product_type="bundle")
        make_listing(self.other, self.home, price="30.00")

    def empty(self):
        return Shop({"suggest.json": recorded("empty_suggest.json")})

    def test_a_shop_is_asked_at_most_20_times_an_hour(self):
        now = self.clock().timestamp()
        cache.set(finder.LOG_KEY, [(now - 60, self.gg.pk)] * finder.SHOP_HOURLY, 3600)
        vault = make_retailer("Card Vault", source_type=Retailer.Source.SHOPIFY, source_url="https://vault.example/")
        shop = self.empty()
        self.find(shop)
        self.assertEqual(shop.asked(GG), [])
        self.assertTrue(shop.asked("vault.example"))
        self.assertFalse(StockistSearch.objects.filter(retailer=self.gg).exists())
        self.assertTrue(StockistSearch.objects.filter(retailer=vault).exists())

    def test_200_requests_an_hour_in_all(self):
        now = self.clock().timestamp()
        cache.set(finder.LOG_KEY, [(now - 60, 999)] * finder.OVERALL_HOURLY, 3600)
        shop = self.empty()
        self.find(shop)
        self.assertEqual(shop.calls, [])
        self.assertFalse(StockistSearch.objects.exists())
        # An hour later the budget is back.
        self.clock.now += timedelta(minutes=61)
        self.find(shop)
        self.assertTrue(shop.calls)

    def test_a_run_stops_at_its_own_request_limit(self):
        shop = self.empty()
        self.find(shop, requests=3)
        self.assertEqual(len(shop.calls), 3)

    def test_one_second_between_requests_to_one_shop(self):
        self.find(self.empty())
        self.assertEqual(self.sleeps, [finder.SHOP_GAP] * 3)   # four requests to one shop, the clock standing still

    def test_the_finder_stays_under_a_fifth_of_the_readers_requests(self):
        budget = finder.Budget(self.clock(), worker_requests=lambda: 10)
        budget.check(self.gg.pk, self.clock())
        budget.spend(self.gg.pk, self.clock())
        budget.check(self.gg.pk, self.clock())
        budget.spend(self.gg.pk, self.clock())
        with self.assertRaises(finder.NoBudget):
            budget.check(self.gg.pk, self.clock())

    def test_a_404_on_the_search_marks_the_shop_unsearchable_for_a_week(self):
        shop = Shop({"suggest.json": ImportError_(f"Could not fetch {GG}/search: HTTP Error 404: Not Found")})
        self.find(shop, limit=1)
        self.gg.refresh_from_db()
        self.assertFalse(self.gg.suggest_ok)
        self.assertEqual(len(shop.asked("suggest.json")), 1)
        # The next product: only the shop's last read is looked at, no request.
        self.find(shop)
        self.assertEqual(len(shop.asked("suggest.json")), 1)
        self.assertEqual(self.search(self.other, self.gg).outcome, StockistSearch.Outcome.NONE)
        # A week later the search is tried again, and once it answers the shop is searchable again.
        self.clock.now += timedelta(days=8)
        StockistSearch.objects.all().delete()
        self.find(self.empty())
        self.gg.refresh_from_db()
        self.assertTrue(self.gg.suggest_ok)

    def test_an_answer_that_is_not_json_marks_the_shop_unsearchable(self):
        self.find(Shop({"suggest.json": b"<html>Verifying your connection</html>"}), limit=1)
        self.gg.refresh_from_db()
        self.assertFalse(self.gg.suggest_ok)
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.ERROR)

    def test_a_shop_saying_too_many_backs_off_and_is_not_asked_again(self):
        shop = Shop({"suggest.json": ImportError_(f"Could not fetch {GG}/search: HTTP Error 429: Too Many Requests")})
        self.find(shop)
        self.assertEqual(len(shop.calls), 1)
        self.gg.refresh_from_db()
        self.assertGreaterEqual(self.gg.backoff_until, self.clock() + Retailer.BACKOFF_TOO_MANY)
        self.assertTrue(self.gg.suggest_ok)

    def test_nothing_is_ever_sent_to_an_inactive_paused_or_waiting_shop_or_under_pause_all(self):
        shop = self.empty()
        for change in ({"is_active": False}, {"reading_paused": True}, {"backoff_until": self.clock() + timedelta(hours=1)}):
            Retailer.objects.filter(pk=self.gg.pk).update(**change)
            self.find(shop)
            Retailer.objects.filter(pk=self.gg.pk).update(is_active=True, reading_paused=False, backoff_until=None)
        crawl.pause_all()
        self.addCleanup(crawl.resume_all)
        self.find(shop)
        self.assertEqual(shop.calls, [])
        self.assertFalse(StockistSearch.objects.exists())

    def test_a_shop_paused_during_a_batch_is_not_asked_again(self):
        def pause_on_first(url):
            Retailer.objects.filter(pk=self.gg.pk).update(reading_paused=True)
            return answer(recorded("empty_suggest.json"))

        fetch = mock.Mock(side_effect=pause_on_first)
        finder.find(fetch_for=lambda retailer: fetch, clock=self.clock, sleep=self.sleeps.append)
        # The first product's two searches, then nothing for the second product.
        self.assertEqual(fetch.call_count, 2)


@override_settings(RIPRAPTOR_FINDER=True)
class WorkerTests(FinderCase):
    def setUp(self):
        super().setUp()
        # Nothing else is due: no shop read, pulse or probe.
        now = self.clock()
        Retailer.objects.update(next_read_at=now + timedelta(hours=1), collections_polled_at=now, collections_ok=True)
        Listing.objects.update(last_checked=now)

    def test_the_reader_runs_a_batch_every_five_minutes_through_its_buckets(self):
        shop = Shop({"suggest.json": recorded("empty_suggest.json")})
        reader = worker.Worker(clock=self.clock, fetch=shop, sleep=self.clock.sleep)
        self.assertIn(worker.FINDER, [t.kind for t in reader.plan(self.clock())])
        # The reader has made no other request: a fifth of nothing is nothing.
        reader.run_once()
        self.assertEqual(shop.calls, [])
        for _ in range(50):
            reader.politeness.requests.append(self.clock().timestamp())
        reader.last_finder = None
        reader.run_once()
        self.assertEqual(len(shop.asked("suggest.json")), 2)
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.NONE)
        self.assertNotIn(worker.FINDER, [t.kind for t in reader.plan(self.clock())])
        self.clock.now += worker.FINDER_EVERY
        self.assertIn(worker.FINDER, [t.kind for t in reader.plan(self.clock())])

    def test_a_shop_another_job_is_asking_is_left_alone(self):
        shop = Shop({"suggest.json": recorded("empty_suggest.json")})
        reader = worker.Worker(clock=self.clock, fetch=shop, sleep=self.clock.sleep)
        for _ in range(50):
            reader.politeness.requests.append(self.clock().timestamp())
        reader.running[self.gg.pk] = worker.Job(worker.Task(worker.PROBE, self.gg.pk, "shopify", 1, "probe"),
                                                self.clock(), self.clock() + timedelta(minutes=1))
        reader.find_stockists(worker.Job(worker.Task(worker.FINDER, worker.FINDER_KEY, "", 1, "finder"),
                                         self.clock(), self.clock() + worker.FINDER_DEADLINE))
        self.assertEqual(shop.calls, [])

    def test_the_finder_leaves_a_thread_free_for_probes(self):
        reader = worker.Worker(clock=self.clock, fetch=Shop({}), threads=3, executor=mock.Mock())
        for pk in (101, 102):
            reader.running[pk] = worker.Job(worker.Task(worker.PROBE, pk, "shopify", 1, "probe"),
                                            self.clock(), self.clock() + timedelta(minutes=1))
        reader.queue = [worker.Task(worker.FINDER, worker.FINDER_KEY, "", 1, "finder")]
        self.assertEqual(reader.dispatch(self.clock()), 0)

    @override_settings(RIPRAPTOR_FINDER=False)
    def test_the_setting_turns_it_off(self):
        reader = worker.Worker(clock=self.clock, fetch=Shop({}))
        self.assertNotIn(worker.FINDER, [t.kind for t in reader.plan(self.clock())])


class CommandTests(FinderCase):
    def test_the_command_says_what_it_did(self):
        out = StringIO()
        with mock.patch("catalogue.finder.time.sleep"), mock.patch("catalogue.importers.fetch", Shop({"suggest.json": recorded("empty_suggest.json")})):
            call_command("find_stockists", "--requests", "5", stdout=out)
        self.assertEqual(out.getvalue().strip(), "1 product searched, 0 listings added, 0 to check.")


class OwnerTests(FinderCase):
    def setUp(self):
        super().setUp()
        Product.objects.filter(pk=self.etb.pk).update(name="Prismatic Evolutions Pokemon Center Elite Trainer Box")
        self.etb.refresh_from_db()
        self.find(Shop({"suggest.json": recorded("gathering_games_suggest.json"), ".js": product_page(available=True, barcode="")}))
        self.row = ShopProduct.objects.get(retailer=self.gg)
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("checks")

    def test_the_checks_page_lists_the_found_product_in_one_query(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Found at another shop (1)")
        self.assertContains(page, f'might be at Gathering Games as <a href="{self.row.url}"')
        self.assertContains(page, 'Scarlet &amp; Violet 8.5 - Prismatic Evolutions Elite Trainer Box"</a> for £149.99')
        self.assertContains(page, 'value="Yes, link it"')
        self.assertContains(page, 'value="No, not this"')
        from .checks import found_stockists

        with self.assertNumQueries(1):
            rows = found_stockists()
            [(row.suggested.name, row.retailer.name) for row in rows]

    def test_yes_creates_the_listing_and_the_next_shop_read_prices_it(self):
        self.client.post(self.url, {"action": "link_found", "row": self.row.pk, "price": "149.99"})
        listing = Listing.objects.get(product=self.etb, retailer=self.gg)
        self.assertEqual(listing.url, self.row.url)
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.LINKED)
        self.assertContains(self.client.get(self.url), "Found at another shop (0)")
        page = {"products": [{"handle": PE_HANDLE, "title": PE_TITLE, "tags": [],
                              "variants": [{"price": "139.99", "available": True, "barcode": ""}]}]}

        def read(url):
            return answer(page if url.startswith(f"{GG}/products.json") and "page=1" in url else {"products": []})

        run_import(self.gg, fetch=read)
        listing.refresh_from_db()
        self.assertEqual((listing.price, listing.availability), (Decimal("139.99"), Listing.Availability.IN_STOCK))

    def test_no_marks_it_ignored_and_the_shop_is_never_asked_again(self):
        self.client.post(self.url, {"action": "not_found", "row": self.row.pk, "price": "149.99"})
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, ShopProduct.Status.IGNORED)
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.IGNORED)
        self.assertFalse(Listing.objects.filter(retailer=self.gg).exists())
        self.clock.now += timedelta(days=400)
        self.assertEqual(finder.candidates(now=self.clock()), [])

    def test_a_price_that_moved_since_the_page_loaded_is_not_acted_on(self):
        ShopProduct.objects.filter(pk=self.row.pk).update(price=Decimal("120.00"))
        page = self.client.post(self.url, {"action": "link_found", "row": self.row.pk, "price": "149.99"}, follow=True)
        self.assertContains(page, "That price has changed since the page loaded")
        self.assertFalse(Listing.objects.filter(retailer=self.gg).exists())

    def test_the_admin_actions_share_the_same_answers(self):
        self.client.post(reverse("admin:catalogue_shopproduct_changelist"),
                         {"action": "mark_ignored", "_selected_action": [self.row.pk]})
        self.assertEqual(self.search(self.etb, self.gg).outcome, StockistSearch.Outcome.IGNORED)


class InsightsTests(FinderCase):
    def setUp(self):
        super().setUp()
        DailyPageView.objects.create(date=timezone.localdate(), kind=DailyPageView.Kind.PRODUCT, key=self.etb.slug, hits=4)
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))

    def test_popular_one_shop_products_say_how_far_they_were_searched_and_can_be_searched_now(self):
        StockistSearch.objects.create(product=self.etb, retailer=self.gg, outcome=StockistSearch.Outcome.NONE,
                                      searched_at=timezone.now() - timedelta(days=2))
        page = self.client.get(reverse("insights"))
        self.assertContains(page, "Searched at 1 shop, last")
        self.assertContains(page, 'value="Search other shops now"')
        self.assertEqual(finder.candidates(now=timezone.now()), [])
        Product.objects.filter(pk=self.etb.pk).update(finder_checked_at=timezone.now())
        done = self.client.post(reverse("crawl"), {"action": "find_now", "product": self.etb.pk}, follow=True)
        self.assertRedirects(done, reverse("insights"))
        self.assertContains(done, "the background reader is not running")
        self.etb.refresh_from_db()
        self.assertIsNone(self.etb.finder_checked_at)
        # Asked again although it was searched two days ago, with the tap's points.
        first = finder.candidates(now=timezone.now())[0]
        self.assertEqual((first.product, first.shops), (self.etb, [self.gg]))
        self.assertGreaterEqual(first.interest, finder.BOOST_POINTS)
        # Once searched after the tap it waits again.
        StockistSearch.objects.filter(product=self.etb).update(searched_at=timezone.now())
        self.assertEqual(finder.candidates(now=timezone.now()), [])
