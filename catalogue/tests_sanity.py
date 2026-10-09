"""Price verdicts: every price is judged against the other shops, and an impossible one leaves the comparison."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import alerts, ebay, offers, pricing, sanity
from .models import DailyLowestPrice, Listing, Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set

OK, DOUBTFUL, EXCLUDED = Listing.Sanity.OK, Listing.Sanity.DOUBTFUL, Listing.Sanity.EXCLUDED
IN_STOCK = Listing.Availability.IN_STOCK


class Shops:
    """A product and a helper that adds a free-delivery shop selling it at a price."""

    def setUp(self):
        cache.clear()
        self.set = make_set(make_game())
        self.product = make_product(self.set, name="Surging Sparks Booster Box", product_type="booster_box")
        self.shops = 0

    def shop(self, price, product=None, **kwargs):
        self.shops += 1
        retailer = make_retailer(f"Shop {self.shops}", delivery_cost=Decimal("0"), **kwargs)
        return make_listing(product or self.product, retailer, price=str(price))

    def check(self, listing, price):
        """A price check through the one write path, as an import or the stock watcher makes it."""
        return pricing.record_check(listing, price=Decimal(str(price)), delivery_cost=Decimal("0.00"),
                                    availability=IN_STOCK)

    def verdicts(self, *listings):
        return [Listing.objects.get(pk=listing.pk).sanity for listing in listings]

    def summary(self, product=None):
        product = Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()).get(pk=(product or self.product).pk)
        return offers.summarise(product, offers.week_low_map([product.pk]))


class VerdictTests(Shops, TestCase):
    def test_a_price_far_under_three_shops_is_excluded(self):
        shops = [self.shop(120), self.shop(125), self.shop(130)]
        odd = self.shop(130)
        self.check(odd, "9.99")
        odd.refresh_from_db()
        self.assertEqual(odd.sanity, EXCLUDED)
        self.assertIn("under a third of what 3 other shops charge, around £125.00", odd.sanity_reason)
        self.assertEqual(odd.sanity_ratio, Decimal("0.08"))
        self.assertEqual(self.verdicts(*shops), [OK, OK, OK])
        self.assertEqual(Product.objects.for_lists().get(pk=self.product.pk).lowest_price, Decimal("120.00"))
        self.assertEqual([row.price for row in DailyLowestPrice.objects.filter(product=self.product)], [Decimal("120.00")])

    def test_two_shops_far_apart_are_both_doubtful_and_never_excluded(self):
        right = self.shop(120)
        wrong = self.shop(120)
        DailyLowestPrice.objects.create(product=self.product, date=timezone.localdate() - timedelta(days=2), price="125.00")
        self.check(wrong, "9.99")
        self.assertEqual(self.verdicts(right, wrong), [DOUBTFUL, DOUBTFUL])
        self.assertIn("the two shops disagree", Listing.objects.get(pk=right.pk).sanity_reason)
        summary = self.summary()
        self.assertTrue(summary.suspect)
        self.assertIsNone(summary.saving)
        self.assertIsNone(summary.badge)
        from web.feeds import drop_entries

        DailyLowestPrice.objects.create(product=self.product, date=timezone.localdate() - timedelta(days=7), price="12.00")
        self.assertEqual(drop_entries(None, None, timezone.now()), [])

    def test_pass_two_judges_without_the_prices_pass_one_kept_out(self):
        shops = [self.shop(100), self.shop(105), self.shop(110)]
        odd = self.shop(110)
        self.check(odd, 12)
        self.assertEqual(self.verdicts(*shops, odd), [OK, OK, OK, EXCLUDED])
        # Against 100 and 10 the median is 55, which would make 150 doubtful; without the 10 it agrees with 100.
        box = make_product(self.set, name="Stellar Crown Booster Box", slug="stellar", product_type="booster_box")
        low, high, wild = self.shop(100, box), self.shop(150, box), self.shop(150, box)
        self.check(wild, 10)
        self.assertEqual(self.verdicts(low, high, wild), [OK, OK, EXCLUDED])

    def test_a_genuine_clearance_is_doubtful_and_still_shown_as_cheapest(self):
        for _ in range(3):
            self.shop(100)
        clearance = self.shop(100)
        self.check(clearance, 45)
        self.assertEqual(self.verdicts(clearance), [DOUBTFUL])
        summary = self.summary()
        self.assertEqual((summary.best.pk, summary.saving, summary.suspect), (clearance.pk, None, True))
        self.assertEqual(Product.objects.for_lists().get(pk=self.product.pk).lowest_price, Decimal("45.00"))
        page = self.client.get(self.product.get_absolute_url())
        self.assertEqual(page.context["current"][0].pk, clearance.pk)

    def test_one_wrong_peer_and_a_right_new_price_are_both_doubtful(self):
        wrong = self.shop(160)
        DailyLowestPrice.objects.create(product=self.product, date=timezone.localdate() - timedelta(days=7), price="60.00")
        right = self.shop(160)
        self.check(right, 45)
        self.assertEqual(self.verdicts(wrong, right), [DOUBTFUL, DOUBTFUL])
        self.assertFalse(Listing.objects.filter(sanity=EXCLUDED).exists())
        # 45 against 60 a week ago would be a drop, but a doubtful cheapest price is not news.
        self.assertEqual(list(pricing.price_drops()), [])
        from web.feeds import drop_entries

        self.assertEqual(drop_entries(None, None, timezone.now()), [])
        Listing.objects.filter(pk=wrong.pk).update(price=Decimal("48.00"))
        sanity.judge_product(self.product.pk)
        self.assertEqual([p.pk for p in pricing.price_drops()], [self.product.pk])

    def test_two_shops_keep_a_real_saving_inside_the_one_shop_band(self):
        # 50 against 100 and 45 against 140 are within 0.30 to 3.33: two shops may honestly differ this much.
        self.shop(100)
        half = self.shop(100)
        self.check(half, 50)
        box = make_product(self.set, name="Stellar Crown Booster Box", slug="stellar", product_type="booster_box")
        dear, cheap = self.shop(140, box), self.shop(140, box)
        self.check(cheap, 45)
        self.assertEqual(self.verdicts(half, dear, cheap), [OK, OK, OK])
        summary = self.summary()
        self.assertEqual((summary.suspect, summary.saving, summary.percent), (False, Decimal("50.00"), 50))
        self.assertEqual(self.summary(box).percent, 68)

    def test_a_price_of_nothing_never_makes_the_other_shop_doubtful(self):
        shop = self.shop(120)
        nothing = self.shop(120)
        Listing.objects.filter(pk=nothing.pk).update(price=Decimal("0.00"))
        result = sanity.judge_product(self.product.pk)
        self.assertEqual((result[shop.pk][0], result[nothing.pk][0]), (OK, DOUBTFUL))
        self.shop(125)
        sanity.judge_product(self.product.pk)
        self.assertEqual(self.verdicts(shop, nothing), [OK, EXCLUDED])

    def test_an_excluded_price_stays_out_when_the_other_shops_sell_out(self):
        peers = [self.shop(price) for price in (120, 125, 130)]
        odd = self.shop(130)
        self.check(odd, "9.99")
        odd.refresh_from_db()
        ok_before = odd.last_ok_price
        for peer in peers:
            pricing.record_check(peer, price=peer.price, delivery_cost=Decimal("0.00"),
                                 availability=Listing.Availability.OUT_OF_STOCK)
        self.check(odd, "9.99")
        odd.refresh_from_db()
        self.assertEqual((odd.sanity, odd.last_ok_price), (EXCLUDED, ok_before))
        self.assertIsNone(Product.objects.for_lists().get(pk=self.product.pk).lowest_price)
        # One shop back is not enough to let it in, and the shop is not made doubtful by it.
        pricing.record_check(peers[0], price=Decimal("120.00"), delivery_cost=Decimal("0.00"), availability=IN_STOCK)
        self.assertEqual(self.verdicts(peers[0], odd), [OK, EXCLUDED])
        # Two back judge it afresh.
        pricing.record_check(peers[1], price=Decimal("125.00"), delivery_cost=Decimal("0.00"), availability=IN_STOCK)
        self.assertIn("2 other shops", Listing.objects.get(pk=odd.pk).sanity_reason)

    def test_a_lone_ok_price_is_not_kept_as_the_last_good_price(self):
        lone = self.shop(120)
        self.check(lone, 118)
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.last_ok_price), (OK, None))
        self.shop(125)
        self.check(lone, 119)
        self.assertEqual(Listing.objects.get(pk=lone.pk).last_ok_price, Decimal("119.00"))

    def test_last_seen_leaves_out_an_excluded_price(self):
        peers = [self.shop(price) for price in (120, 125, 130)]
        odd = self.shop(130)
        self.check(odd, "9.99")
        # A complete import sweeps everything out of stock with one update and no judging.
        Listing.objects.filter(pk__in=[p.pk for p in peers] + [odd.pk]).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(Product.objects.for_lists().get(pk=self.product.pk).last_price, Decimal("120.00"))

    def test_a_marketplace_is_judged_but_never_judges(self):
        ebay_shop = make_retailer("eBay", slug="ebay", source_type=Retailer.Source.EBAY)
        shops = [self.shop(120), self.shop(125)]
        cheap = make_listing(self.product, ebay_shop, price="20.00")
        sanity.judge_product(self.product.pk)
        self.assertEqual(self.verdicts(*shops, cheap), [OK, OK, EXCLUDED])
        # Alone with one shop, the marketplace price never makes the shop doubtful.
        Listing.objects.filter(pk=shops[1].pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        sanity.judge_product(self.product.pk)
        # Kept out by two shops, it stays out with one left.
        self.assertEqual(self.verdicts(shops[0], cheap), [OK, EXCLUDED])
        Listing.objects.filter(pk=cheap.pk).update(sanity=OK)
        sanity.judge_product(self.product.pk)
        self.assertEqual(self.verdicts(shops[0], cheap), [OK, DOUBTFUL])

    def test_the_reason_says_when_an_item_price_was_compared(self):
        shops = [self.shop(120), self.shop(125)]
        Listing.objects.filter(pk=shops[0].pk).update(delivery_known=False)
        odd = self.shop(125)
        self.check(odd, 10)
        self.assertIn("item price", Listing.objects.get(pk=odd.pk).sanity_reason)

    def test_an_ok_verdict_clears_an_exclusion(self):
        for price in (120, 125, 130):
            self.shop(price)
        odd = self.shop(130)
        self.check(odd, "9.99")
        self.check(odd, 119)
        odd.refresh_from_db()
        self.assertEqual((odd.sanity, odd.sanity_reason, odd.sanity_ratio, odd.last_ok_price), (OK, "", None, Decimal("119.00")))
        self.assertIsNotNone(odd.sanity_at)


class TrustTests(Shops, TestCase):
    def setUp(self):
        super().setUp()
        for price in (120, 125, 130):
            self.shop(price)
        self.odd = self.shop(130)
        self.check(self.odd, "9.99")

    def test_an_owner_confirmed_price_holds_while_it_moves_little(self):
        sanity.trust(Listing.objects.get(pk=self.odd.pk))
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.sanity_reason), (OK, "confirmed by owner"))
        self.check(self.odd, "10.49")
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.trusted_price), (OK, Decimal("9.99")))
        self.check(self.odd, "12.00")
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.trusted_price, self.odd.trusted_at), (EXCLUDED, None, None))

    def test_trust_runs_out_while_the_price_stays_the_same(self):
        from .importers import stamp_checked

        sanity.trust(Listing.objects.get(pk=self.odd.pk))
        Listing.objects.filter(pk=self.odd.pk).update(trusted_at=timezone.now() - timedelta(days=40))
        stamp_checked([self.odd.pk], timezone.now())
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.trusted_price), (EXCLUDED, None))
        sanity.trust(Listing.objects.get(pk=self.odd.pk))
        Listing.objects.filter(pk=self.odd.pk).update(trusted_at=timezone.now() - timedelta(days=40))
        self.check(Listing.objects.get(pk=self.odd.pk), "9.99")
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.trusted_price), (EXCLUDED, None))

    def test_a_month_old_trust_no_longer_applies(self):
        Listing.objects.filter(pk=self.odd.pk).update(
            trusted_price=Decimal("9.99"), trusted_at=timezone.now() - timedelta(days=31)
        )
        sanity.judge_product(self.product.pk)
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.trusted_price), (EXCLUDED, None))
        Listing.objects.filter(pk=self.odd.pk).update(
            trusted_price=Decimal("9.99"), trusted_at=timezone.now() - timedelta(days=29)
        )
        sanity.judge_product(self.product.pk)
        self.assertEqual(self.verdicts(self.odd), [OK])


class JudgeOnChangeTests(Shops, TestCase):
    def test_an_unchanged_check_costs_no_extra_queries(self):
        listing = self.shop(120)
        self.shop(125)
        self.check(listing, 120)
        with CaptureQueriesContext(connection) as unchanged:
            self.check(listing, 120)
        with mock.patch.object(sanity, "judge_product") as judge, CaptureQueriesContext(connection) as without:
            self.check(listing, 120)
        judge.assert_not_called()
        # The same queries as a check with judging switched off: nothing is judged.
        self.assertEqual(len(unchanged), len(without))
        with mock.patch.object(sanity, "judge_product", return_value={}) as judge:
            self.check(listing, 121)
        judge.assert_called_once()

    def test_a_listing_with_a_verdict_is_judged_even_when_its_price_is_unchanged(self):
        listing = self.shop(120)
        Listing.objects.filter(pk=listing.pk).update(sanity=DOUBTFUL, sanity_reason="old")
        listing.refresh_from_db()
        self.check(listing, 120)
        self.assertEqual(self.verdicts(listing), [OK])

    def test_an_unchanged_doubtful_listing_heals_on_the_next_import(self):
        from .importers import Offer, apply_offers

        self.product.ean = "0820650853333"
        self.product.save()
        shop = make_retailer("Harbour Games", delivery_cost=Decimal("0"))
        mine = make_listing(self.product, shop, price="45.00", url="https://harbour.example/products/box")
        other = self.shop(160)
        sanity.judge_product(self.product.pk)
        self.assertEqual(self.verdicts(mine, other), [DOUBTFUL, DOUBTFUL])
        # The other shop corrects its price without a check of ours seeing it.
        Listing.objects.filter(pk=other.pk).update(price=Decimal("48.00"))
        offer = Offer(title="Surging Sparks Booster Box", url="https://harbour.example/products/box",
                      price=Decimal("45.00"), ean="0820650853333")
        with mock.patch.object(pricing, "record_check") as write:
            apply_offers(shop, [offer], complete=False)
        write.assert_not_called()
        self.assertEqual(self.verdicts(mine, other), [OK, OK])

    def test_a_shop_back_from_out_of_date_judges_the_others_again(self):
        from .importers import stamp_checked

        peer = self.shop(120)
        odd = self.shop(120)
        old = timezone.now() - timedelta(days=5)
        Listing.objects.filter(pk=peer.pk).update(last_checked=old)
        self.check(odd, "9.99")
        self.assertEqual(self.verdicts(odd), [OK])
        # The other shop is read again with the same price: the cheap one is judged against it.
        stamp_checked([peer.pk], timezone.now())
        self.assertEqual(self.verdicts(peer, odd), [DOUBTFUL, DOUBTFUL])
        Listing.objects.filter(pk=peer.pk).update(last_checked=old, sanity=OK)
        Listing.objects.filter(pk=odd.pk).update(sanity=OK)
        self.check(Listing.objects.get(pk=peer.pk), 120)
        self.assertEqual(self.verdicts(peer, odd), [DOUBTFUL, DOUBTFUL])

    def test_a_new_listing_is_judged(self):
        from .importers import Offer, apply_offers

        for price in (120, 125):
            self.shop(price)
        self.product.ean = "0820650853333"
        self.product.save()
        shop = make_retailer("Harbour Games", delivery_cost=Decimal("0"))
        apply_offers(shop, [Offer(title="Surging Sparks Booster Box", url="https://harbour.example/products/box",
                                  price=Decimal("50.00"), ean="0820650853333")], complete=False)
        self.assertEqual(Listing.objects.get(retailer=shop).sanity, DOUBTFUL)

    def test_thirty_listings_take_two_queries_and_one_update_per_changed_verdict(self):
        listings = [self.shop(100 + i) for i in range(30)]
        sanity.judge_product(self.product.pk)
        with self.assertNumQueries(1):
            sanity.judge_product(self.product.pk)
        Listing.objects.filter(pk=listings[0].pk).update(price=Decimal("5.00"))
        with CaptureQueriesContext(connection) as queries:
            result = sanity.judge_product(self.product.pk)
        changed = sum(1 for verdict in result.values() if verdict[0] != OK)
        self.assertEqual(changed, 1)
        self.assertLessEqual(len(queries), 2 + changed)


class KeptOutTests(Shops, TestCase):
    def setUp(self):
        super().setUp()
        for price in (120, 125, 130):
            self.shop(price)
        self.odd = self.shop(130)
        self.check(self.odd, "9.99")
        self.odd.refresh_from_db()

    def test_an_excluded_price_leaves_every_comparison(self):
        self.assertFalse(Listing.objects.buyable().filter(pk=self.odd.pk).exists())
        self.assertFalse(self.odd.is_buyable)
        product = Product.objects.for_lists().prefetch_related(offers.buyable_prefetch()).get(pk=self.product.pk)
        self.assertEqual((product.lowest_price, product.in_stock_count), (Decimal("120.00"), 3))
        self.assertNotIn(self.odd.pk, [listing.pk for listing in product.offers])
        self.assertEqual(alerts.shop_stock(self.product).delivered_price, Decimal("120.00"))

    def test_the_feed_drop_is_the_counted_price(self):
        from web.feeds import build_feed

        DailyLowestPrice.objects.create(product=self.product, date=timezone.localdate() - timedelta(days=7), price="140.00")
        entries = [entry.title for entry in build_feed("deals", None).entries if entry.key.startswith("drop-")]
        self.assertEqual(len(entries), 1)
        self.assertIn("£120.00", entries[0])
        self.assertNotIn("9.99", entries[0])

    def test_the_product_page_shows_it_as_not_counted(self):
        page = self.client.get(self.product.get_absolute_url())
        self.assertNotIn(self.odd.pk, [listing.pk for listing in page.context["current"]])
        self.assertIn(self.odd.pk, [listing.pk for listing in page.context["unavailable"]])
        self.assertContains(page, "Not counted: far from the other shops")
        self.assertNotContains(page, '"lowPrice": "9.99"')

    def test_admin_lists_it_with_its_verdict(self):
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        page = self.client.get(reverse("admin:catalogue_listing_changelist") + "?sanity__exact=excluded")
        self.assertContains(page, "Shop 4")
        self.assertEqual(list(page.context["cl"].result_list), [self.odd])
        change = self.client.get(reverse("admin:catalogue_listing_change", args=[self.odd.pk]))
        self.assertContains(change, "under a third of what 3 other shops charge")


class RestockAtAnExcludedPriceTests(Shops, TestCase):
    """A shop coming back at an impossible price is not announced anywhere."""

    def setUp(self):
        super().setUp()
        for price in (120, 125, 130):
            self.shop(price)
        self.odd = self.shop(130)
        Listing.objects.filter(pk=self.odd.pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.odd.refresh_from_db()

    def test_no_restock_is_kept(self):
        from .models import Restock

        self.check(self.odd, "9.99")
        self.assertEqual(self.verdicts(self.odd), [EXCLUDED])
        self.assertFalse(Restock.objects.exists())
        self.assertEqual(pricing.back_in_stock(), [])

    def test_a_restock_whose_price_was_kept_out_later_is_not_repeated(self):
        from web.feeds import restock_entries

        self.check(self.odd, 130)
        self.assertEqual(len(restock_entries(None, None, timezone.now())), 1)
        self.assertEqual([listing.pk for _, listing in pricing.back_in_stock()], [self.odd.pk])
        Listing.objects.filter(pk=self.odd.pk).update(sanity=EXCLUDED, price=Decimal("9.99"))
        self.assertEqual(restock_entries(None, None, timezone.now()), [])
        self.assertEqual(pricing.restock_log(), [])
        self.assertEqual(pricing.back_in_stock(), [])
        for url in ("/", reverse("web:deals")):
            self.assertNotContains(self.client.get(url), "9.99")


class ShopPriceTests(Shops, TestCase):
    def test_ebay_floor_ignores_excluded_and_prefers_ok_prices(self):
        for price in (120, 125, 130):
            self.shop(price)
        odd = self.shop(130)
        self.check(odd, "9.99")
        tin = make_product(self.set, name="Surging Sparks Tin", slug="tin", product_type="tin")
        self.shop(30, tin)
        doubtful = self.shop(14, tin)
        Listing.objects.filter(pk=doubtful.pk).update(sanity=DOUBTFUL)
        deck = make_product(self.set, name="Surging Sparks Deck", slug="deck", product_type="deck")
        only_doubtful, only_excluded = self.shop(20, deck), self.shop(2, deck)
        Listing.objects.filter(pk=only_doubtful.pk).update(sanity=DOUBTFUL)
        Listing.objects.filter(pk=only_excluded.pk).update(sanity=EXCLUDED)
        prices = ebay.shop_prices()
        self.assertEqual(prices[self.product.pk], Decimal("120.00"))
        self.assertEqual(prices[tin.pk], Decimal("30.00"))
        self.assertEqual(prices[deck.pk], Decimal("20.00"))


class ChecksPageSanityTests(Shops, TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        super().setUp()
        for price in (120, 125, 130):
            self.shop(price)
        self.odd = self.shop(130)
        self.check(self.odd, "9.99")
        box = make_product(self.set, name="Stellar Crown Booster Box", slug="stellar", product_type="booster_box")
        self.shop(140, box)
        self.shop(140, box)
        self.clearance = self.shop(140, box)
        self.check(self.clearance, 45)
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("checks")

    def test_page_lists_doubtful_and_excluded_prices_in_plain_words(self):
        page = self.client.get(self.url).content.decode()
        self.assertIn("Doubtful prices (1)", page)
        self.assertIn("Excluded automatically (1)", page)
        self.assertIn("This price is right", page)
        self.assertIn("Show it anyway", page)
        self.assertIn("well under what 2 other shops charge, around £140.00", page)
        # A product already under doubtful prices is not listed again as a wrong match.
        self.assertIn("Wrong matches (0)", page)
        section = page[page.index('<div class="chk">'):page.index("Possible duplicates")]
        self.assertNotIn("\u2014", section)
        self.assertNotIn("!", section)
        from content.registry import ENTRIES

        kept_out = next(entry for entry in ENTRIES if entry.key == "product.price.kept_out")
        self.assertNotIn("\u2014", kept_out.default)
        self.assertNotIn("!", kept_out.default)

    def test_trust_posts_the_price_and_warns_when_it_changed(self):
        stale = self.client.post(self.url, {"action": "trust", "listing": self.clearance.pk, "price": "44.00"}, follow=True)
        self.assertContains(stale, "That price has changed since the page loaded")
        self.assertEqual(self.verdicts(self.clearance), [DOUBTFUL])
        self.client.post(self.url, {"action": "trust", "listing": self.clearance.pk, "price": "45.00"})
        self.assertEqual(self.verdicts(self.clearance), [OK])
        self.assertContains(self.client.get(self.url), "Doubtful prices (0)")

    def test_show_puts_an_excluded_price_back(self):
        stale = self.client.post(self.url, {"action": "show", "listing": self.odd.pk, "price": "130.00"}, follow=True)
        self.assertContains(stale, "That price has changed since the page loaded")
        self.client.post(self.url, {"action": "show", "listing": self.odd.pk, "price": "9.99"})
        odd = Listing.objects.get(pk=self.odd.pk)
        self.assertEqual((odd.sanity, odd.sanity_reason, odd.trusted_price), (OK, "confirmed by owner", Decimal("9.99")))
        self.assertTrue(Listing.objects.buyable().filter(pk=odd.pk).exists())
        self.assertContains(self.client.get(self.url), "Excluded automatically (0)")

    def test_hide_from_a_doubtful_row_checks_the_price_too(self):
        self.client.post(self.url, {"action": "hide", "listing": self.clearance.pk, "price": "46.00"})
        self.assertTrue(Listing.objects.get(pk=self.clearance.pk).is_active)
        self.client.post(self.url, {"action": "hide", "listing": self.clearance.pk, "price": "45.00"})
        self.assertFalse(Listing.objects.get(pk=self.clearance.pk).is_active)

    def test_suspect_savings_lists_a_doubtful_product(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("suspect_savings", stdout=out)
        self.assertIn("Stellar Crown Booster Box: £45.00 at Shop 7 against £140.00 at Shop 5", out.getvalue())

    def test_hide_judges_the_other_shop_again(self):
        tin = make_product(self.set, name="Stellar Crown Tin", slug="tin", product_type="tin")
        wrong = self.shop(160, tin)
        right = self.shop(160, tin)
        self.check(right, 45)
        self.assertEqual(self.verdicts(wrong, right), [DOUBTFUL, DOUBTFUL])
        self.client.post(self.url, {"action": "hide", "listing": wrong.pk, "price": "160.00"})
        self.assertEqual(self.verdicts(right), [OK])
        self.assertContains(self.client.get(self.url), "Doubtful prices (1)")

    def test_a_switched_off_shop_or_product_leaves_the_lists(self):
        Retailer.objects.filter(pk=self.clearance.retailer_id).update(is_active=False)
        Product.objects.filter(pk=self.odd.product_id).update(is_active=False)
        page = self.client.get(self.url)
        self.assertContains(page, "Doubtful prices (0)")
        self.assertContains(page, "Excluded automatically (0)")

    def test_the_headings_count_every_row_not_just_the_rows_shown(self):
        from . import checks

        for i in range(checks.SANITY_ROWS + 5):
            product = make_product(self.set, name=f"Tin {i}", slug=f"tin-{i}", product_type="tin")
            Listing.objects.filter(pk=self.shop(20, product).pk).update(sanity=DOUBTFUL)
        page = self.client.get(self.url)
        self.assertContains(page, f"Doubtful prices ({checks.SANITY_ROWS + 6})")
        self.assertEqual(len(page.context["doubtful"]), checks.SANITY_ROWS)

    def test_saving_a_listing_in_admin_judges_it(self):
        url = reverse("admin:catalogue_listing_change", args=[self.clearance.pk])
        form = self.client.get(url).context["adminform"].form
        data = {name: value for name, value in form.initial.items() if value is not None}
        data.update(price="138.00", last_checked_0=timezone.localtime().strftime("%Y-%m-%d"),
                    last_checked_1=timezone.localtime().strftime("%H:%M:%S"))
        data.pop("last_checked", None)
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        self.assertEqual(self.verdicts(self.clearance), [OK])
