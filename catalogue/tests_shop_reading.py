"""Shops the reader cannot read as before (the owner's log, 23 Nov 2026): a shop closed behind its password page, a
shop the owner paused, and shops with more products than Shopify will list."""

import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from . import crawl, importers, insights
from .importers import ImportError_, run_import, sealed_section, sealed_sections, section_words, shopify_offers
from .models import Listing, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set


def page(products):
    return json.dumps({"products": products}).encode()


def box(handle, title, price="129.99"):
    return {"handle": handle, "title": title, "product_type": "Booster Boxes", "tags": [],
            "variants": [{"price": price, "available": True}]}


class ClosedShopTests(TestCase):
    def setUp(self):
        self.shop = make_retailer("Tayler TCG", source_type=Retailer.Source.SHOPIFY, source_url="https://taylertcg.example/")
        self.product = make_product(make_set(make_game()))
        self.listing = make_listing(self.product, self.shop, price="49.99")

    def closed(self, url):
        raise ImportError_(f"Could not fetch {url}: HTTP Error 401: Unauthorized")

    def test_a_shop_behind_its_password_page_comes_off_the_site_and_is_looked_at_again_tomorrow(self):
        began = timezone.now()
        run = run_import(self.shop, fetch=self.closed)
        self.assertTrue(run.error.startswith(importers.SHOP_CLOSED))
        self.assertIn("HTTP Error 401", run.error)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.availability, Listing.Availability.OUT_OF_STOCK)
        now = timezone.now()
        self.assertFalse(crawl.finish_read(self.shop, run, began, 2, now=now))
        self.shop.refresh_from_db()
        # Not a failing shop: nobody is told each day, and nothing is tried until tomorrow.
        self.assertEqual((self.shop.error_streak, self.shop.failing_since), (0, None))
        self.assertEqual((self.shop.backoff_until, self.shop.next_read_at), (now + timedelta(hours=24), now + timedelta(hours=24)))
        self.assertTrue(self.shop.last_error.startswith("Closed for now: the shop's website is behind its password page"))
        self.assertNotIn(self.shop, Retailer.due(now + timedelta(hours=23)))
        self.assertNotIn(self.shop, Retailer.pulse_due(now + timedelta(hours=23)))
        self.assertEqual(insights.reading_state(self.shop, now),
                         f"{importers.SHOP_CLOSED}, looked at again {insights.clock(now + timedelta(hours=24), now)}")
        # Open again: an ordinary read brings it back.
        product = {"handle": "etb", "title": "Pokemon TCG: Prismatic Evolutions - Elite Trainer Box", "tags": [],
                   "variants": [{"price": "54.99", "available": True}]}
        later = now + timedelta(hours=24)
        run = run_import(self.shop, fetch=lambda url: page([product]) if "page=1" in url else page([]))
        self.assertEqual(run.error, "")
        self.assertTrue(crawl.finish_read(self.shop, run, later - timedelta(minutes=1), 2, now=later))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.last_error, "")

    def test_any_other_failure_is_an_ordinary_failed_read(self):
        def busy(url):
            raise ImportError_(f"Could not fetch {url}: HTTP Error 503: Service Unavailable")

        run = run_import(self.shop, fetch=busy)
        self.assertFalse(run.error.startswith(importers.SHOP_CLOSED))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.availability, Listing.Availability.IN_STOCK)   # its prices keep their state
        crawl.finish_read(self.shop, run, timezone.now(), 2)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.error_streak, 1)
        # A website shop answering 401 is not taken for a closed Shopify shop.
        site = make_retailer("Site Shop", source_type=Retailer.Source.WEBSITE, source_url="https://site.example/")
        self.assertFalse(importers.closed_behind_password(site, "HTTP Error 401: Unauthorized"))


class PausedShopTests(TestCase):
    def test_goblin_gaming_is_paused_once_and_stays_as_the_owner_leaves_it(self):
        goblin = make_retailer("Goblin Gaming", slug="goblin-gaming", website="https://www.goblingaming.co.uk/",
                               source_type=Retailer.Source.SHOPIFY, source_url="https://www.goblingaming.co.uk/")
        call_command("setup_shops", stdout=StringIO())
        goblin.refresh_from_db()
        self.assertTrue(goblin.reading_paused)
        self.assertIn("Cloudflare check", goblin.paused_note)
        self.assertTrue(insights.reading_state(goblin, timezone.now()).startswith("Paused: Paused on 23 Nov 2026"))
        # Unpaused in Admin: a later update leaves it running.
        Retailer.objects.filter(pk=goblin.pk).update(reading_paused=False)
        call_command("setup_shops", stdout=StringIO())
        goblin.refresh_from_db()
        self.assertFalse(goblin.reading_paused)


class SealedSectionTests(TestCase):
    def setUp(self):
        make_game()   # Pokemon
        make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        self.words = section_words()

    def test_a_section_is_sealed_and_of_our_games_or_of_none(self):
        kept = ["pokemon-english-booster-boxes", "pokemon-japanese-booster-packs", "pokemon-english-elite-trainer-boxes",
                "magic-the-gathering-commander-decks", "pokemon-english-tins-blisters", "booster-boxes", "bundle",
                "card-game-pre-orders", "sealed-products", "advent-calendars", "pokemon-sealed"]
        left = ["pokemon-singles", "pokemon-sleeves", "deck-boxes", "playmats", "one-piece-booster-boxes",
                "digimon-booster-packs", "new-arrivals", "pokemon", "board-games", "acrylic-displays",
                "battle-spirits-japanese-booster-boxes"]
        for handle in kept:
            with self.subTest(kept=handle):
                self.assertTrue(sealed_section(handle, handle.replace("-", " ").title(), self.words))
        for handle in left:
            with self.subTest(left=handle):
                self.assertFalse(sealed_section(handle, handle.replace("-", " ").title(), self.words))

    def test_the_biggest_sealed_sections_are_picked_from_every_page_of_the_list(self):
        first = [{"handle": f"other-{n}", "title": f"Other {n}", "products_count": 5} for n in range(248)]
        first += [{"handle": "pokemon-booster-boxes", "title": "Pokemon Booster Boxes", "products_count": 40},
                  {"handle": "pokemon-singles", "title": "Pokemon Singles", "products_count": 90000}]
        second = [{"handle": "pokemon-elite-trainer-boxes", "title": "Pokemon ETBs", "products_count": 60},
                  {"handle": "pokemon-booster-packs", "title": "Pokemon Booster Packs", "products_count": 0}]

        def fetch(url):
            return json.dumps({"collections": first if url.endswith("page=1") else second if url.endswith("page=2") else []}).encode()

        self.assertEqual(sealed_sections("https://big.example", fetch=fetch),
                         ["pokemon-elite-trainer-boxes", "pokemon-booster-boxes"])


class BigShopTests(TestCase):
    """A shop past Shopify's 25,000: read whole once, then by its sealed sections."""

    def setUp(self):
        make_game()
        self.shop = make_retailer("Japan Big", source_type=Retailer.Source.SHOPIFY, source_url="https://big.example/")
        self.asked = []

    def fetch(self, url):
        self.asked.append(url)
        if url.endswith("/meta.json"):
            return b'{"currency": "GBP"}'
        if "/collections.json" in url:
            return json.dumps({"collections": [
                {"handle": "pokemon-booster-boxes", "title": "Pokemon Booster Boxes", "products_count": 2},
                {"handle": "pokemon-singles", "title": "Pokemon Singles", "products_count": 80000},
            ]}).encode()
        if "/collections/pokemon-booster-boxes/products.json" in url:
            return page([box("old-box", "Pokemon TCG: Evolving Skies Booster Box")]) if url.endswith("page=1") else page([])
        if url.endswith("/products/known-tin.js"):
            return json.dumps({"handle": "known-tin", "title": "Pokemon TCG: Paldean Fates Tin", "type": "Tins", "tags": [],
                               "variants": [{"id": 1, "title": "Default Title", "price": 1999, "available": True}]}).encode()
        if url.startswith("https://big.example/products.json"):
            number = int(url.rsplit("page=", 1)[1])
            if number > importers.SHOPIFY_PAGE_LIMIT:
                raise ImportError_(f"Could not fetch {url}: HTTP Error 400: Bad Request")
            return page([box(f"single-{number}", f"Pokemon Single Card {number}", price="0.50")])
        return b"{}"

    def test_past_the_limit_it_picks_its_sealed_sections_and_reads_by_them_from_the_next_read(self):
        short = []
        list(shopify_offers(self.shop, fetch=self.fetch, short=short))
        self.assertTrue(short[0].startswith("page 101 of https://big.example/products.json"))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.auto_collection, "pokemon-booster-boxes")
        self.assertIsNotNone(self.shop.auto_collection_at)
        # The next read: its sealed section, its newest two pages, and a product already listed outside the section,
        # read on its own, so nothing known drops out. Never the hundred pages.
        product = make_product(make_set(make_game(name="Pokemon Two", slug="pokemon-two"), name="Paldean Fates", slug="paldean-fates"),
                               name="Paldean Fates Tin")
        make_listing(product, self.shop, url="https://big.example/products/known-tin")
        self.asked.clear()
        urls = sorted(offer.url for offer in shopify_offers(self.shop, fetch=self.fetch))
        self.assertIn("https://big.example/products/old-box", urls)
        self.assertIn("https://big.example/products/known-tin", urls)
        whole = [u for u in self.asked if u.startswith("https://big.example/products.json")]
        self.assertLessEqual(len(whole), importers.NEWEST_PAGES)
        self.assertFalse(any("/collections.json?limit=250&page=" in u for u in self.asked))   # picked again only after a week

    def test_the_sections_are_picked_again_after_a_week_and_the_owners_own_choice_wins(self):
        importers.keep_sections(self.shop, ["old-section"], now=timezone.now() - timedelta(days=8))
        list(shopify_offers(self.shop, fetch=self.fetch))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.auto_collection, "pokemon-booster-boxes")
        # The owner's Shopify collections are read instead, and never replaced.
        self.shop.collection = "my-sealed"
        self.shop.auto_collection_at = timezone.now() - timedelta(days=30)
        self.shop.save()
        self.asked.clear()
        list(shopify_offers(self.shop, fetch=self.fetch))
        self.assertTrue(any("/collections/my-sealed/products.json" in u for u in self.asked))
        self.assertFalse(any("/collections/pokemon-booster-boxes/" in u for u in self.asked))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.auto_collection, "pokemon-booster-boxes")

    def test_a_shop_within_the_limit_is_read_whole_as_before(self):
        def small(url):
            if url.endswith("/meta.json"):
                return b'{"currency": "GBP"}'
            if url.startswith("https://big.example/products.json"):
                return page([box("one", "Pokemon TCG: Evolving Skies Booster Box")]) if url.endswith("page=1") else page([])
            return b"{}"

        self.assertEqual(len(list(shopify_offers(self.shop, fetch=small))), 1)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.auto_collection, "")
