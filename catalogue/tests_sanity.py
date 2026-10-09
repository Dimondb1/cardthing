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

    def test_a_price_kept_out_later_the_same_day_leaves_no_false_low_in_history(self):
        first = self.shop(120)
        odd = self.shop(130)
        self.check(odd, "9.99")
        today = DailyLowestPrice.objects.filter(product=self.product)
        # Two shops cannot say which one is wrong, so the low is recorded while it is only doubtful.
        self.assertEqual([row.price for row in today], [Decimal("9.99")])
        # Two more shops arrive, as a price check that changes something (a new listing) would bring them.
        self.check(self.shop(124), 125)
        self.check(self.shop(129), 130)
        self.assertEqual(self.verdicts(odd, first), [EXCLUDED, OK])
        self.assertEqual([row.price for row in today.all()], [Decimal("120.00")])

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
        # Plus two when a price is newly kept out: today's history is worked out again without it.
        self.assertLessEqual(len(queries), 2 + changed + 2)


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
        self.assertEqual(alerts.shop_stock(self.product)[0].delivered_price, Decimal("120.00"))

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


class Boxes(Shops):
    """Shops plus eight or so other booster boxes of the same game and set, to make a band from."""

    def boxes(self, prices, product_set=None, product_type="booster_box"):
        made = []
        for price in prices:
            i = len(Product.objects.all())
            product = make_product(product_set or self.set, name=f"Box {i}", slug=f"box-{i}", product_type=product_type)
            made.append(self.shop(price, product))
        return made

    def band(self, product_set=None):
        from .models import TypeBand

        return TypeBand.objects.get(product_type="booster_box", product_set=product_set)


EIGHT = (95, 100, 110, 115, 120, 130, 150, 150)


class BandTests(Boxes, TestCase):
    def test_eight_products_make_a_band_and_seven_do_not(self):
        from .models import TypeBand

        self.boxes(EIGHT[:7])
        self.assertEqual(sanity.rebuild_bands(), [])
        self.assertFalse(TypeBand.objects.exists())
        self.boxes([EIGHT[7]])
        sanity.rebuild_bands()
        game, mine = self.band(), self.band(self.set)
        for band in (game, mine):
            self.assertEqual((band.n, band.p10, band.median, band.p90), (8, Decimal("95.00"), Decimal("115.00"), Decimal("150.00")))
        self.assertIsNotNone(game.computed_at)

    def test_a_band_that_falls_under_eight_is_deleted(self):
        from .models import TypeBand

        boxes = self.boxes(EIGHT)
        sanity.rebuild_bands()
        self.assertEqual(TypeBand.objects.count(), 2)
        Listing.objects.filter(pk=boxes[0].pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        sanity.rebuild_bands()
        self.assertFalse(TypeBand.objects.exists())

    def test_doubtful_excluded_marketplace_and_unknown_delivery_prices_do_not_feed_bands(self):
        self.boxes(EIGHT)
        doubtful, excluded, unknown = self.boxes([5, 1, 2])
        Listing.objects.filter(pk=doubtful.pk).update(sanity=DOUBTFUL)
        Listing.objects.filter(pk=excluded.pk).update(sanity=EXCLUDED)
        Listing.objects.filter(pk=unknown.pk).update(delivery_known=False)
        marketplace = make_retailer("eBay", slug="ebay", source_type=Retailer.Source.EBAY)
        product = make_product(self.set, name="Box on eBay", slug="box-ebay", product_type="booster_box")
        make_listing(product, marketplace, price="3.00")
        sanity.rebuild_bands()
        band = self.band()
        self.assertEqual((band.n, band.p10), (8, Decimal("95.00")))

    def test_a_product_counts_in_its_set_band_and_its_game_band(self):
        other = make_set(self.set.game, name="Stellar Crown", slug="stellar-crown")
        self.boxes(EIGHT[:4])
        self.boxes(EIGHT[4:], product_set=other)
        sanity.rebuild_bands()
        self.assertEqual(self.band().n, 8)
        from .models import TypeBand

        self.assertFalse(TypeBand.objects.exclude(product_set=None).exists())

    def test_price_bands_dry_run_writes_nothing(self):
        from io import StringIO

        from django.core.management import call_command

        from .models import TypeBand

        self.boxes(EIGHT)
        out = StringIO()
        call_command("price_bands", "--dry-run", stdout=out)
        self.assertFalse(TypeBand.objects.exists())
        self.assertIn("Pokémon, Booster box, all sets: 8 products, £95.00 / £115.00 / £150.00", out.getvalue())
        self.assertIn("Prismatic Evolutions: 8 products", out.getvalue())
        self.assertIn("2 bands would be saved", out.getvalue())
        call_command("price_bands", stdout=StringIO())
        self.assertEqual(TypeBand.objects.count(), 2)
        for text in (out.getvalue(), sanity.__doc__):
            self.assertNotIn("\u2014", text)
            self.assertNotIn("!", text)

    def test_the_nightly_snapshot_rebuilds_the_bands(self):
        from io import StringIO

        from django.core.management import call_command

        from .management.commands import snapshot_daily_prices

        self.boxes(EIGHT)
        out = StringIO()
        # The checkpoint cannot run inside a test's transaction; CheckpointTests covers it.
        with mock.patch.object(snapshot_daily_prices, "checkpoint", return_value=None):
            call_command("snapshot_daily_prices", stdout=out)
        self.assertEqual(self.band().p90, Decimal("150.00"))
        self.assertIn("Rebuilt 2 price bands.", out.getvalue())

    def test_admin_shows_bands_read_only(self):
        from django.contrib.auth import get_user_model

        self.boxes(EIGHT)
        sanity.rebuild_bands()
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        page = self.client.get(reverse("admin:catalogue_typeband_changelist"))
        self.assertContains(page, "95.00")
        self.assertEqual(self.client.get(reverse("admin:catalogue_typeband_add")).status_code, 403)


class LoneShopBandTests(Boxes, TestCase):
    def setUp(self):
        super().setUp()
        self.boxes(EIGHT)
        sanity.rebuild_bands()
        self.lone = self.shop(100)

    def test_far_below_the_band_is_excluded(self):
        self.check(self.lone, 12)
        lone = Listing.objects.get(pk=self.lone.pk)
        self.assertEqual((lone.sanity, lone.sanity_ratio), (EXCLUDED, Decimal("0.13")))
        self.assertIn("far below every booster box in Pokémon, usually from £95.00", lone.sanity_reason)
        self.assertNotIn("\u2014", lone.sanity_reason)
        self.assertIsNone(Product.objects.for_lists().get(pk=self.product.pk).lowest_price)

    def test_well_below_the_band_is_doubtful(self):
        self.check(self.lone, 40)
        lone = Listing.objects.get(pk=self.lone.pk)
        self.assertEqual(lone.sanity, DOUBTFUL)
        self.assertIn("under half the usual low of £95.00 for its type (booster box in Pokémon)", lone.sanity_reason)

    def test_far_above_the_band_is_doubtful_and_never_excluded(self):
        self.check(self.lone, 700)
        lone = Listing.objects.get(pk=self.lone.pk)
        self.assertEqual((lone.sanity, lone.sanity_ratio), (DOUBTFUL, Decimal("4.67")))
        self.assertIn("over four times the usual high of £150.00 for its type", lone.sanity_reason)
        self.check(self.lone, 590)
        self.assertEqual(self.verdicts(self.lone), [OK])

    def test_a_price_the_band_kept_out_comes_back_when_it_is_right_again(self):
        self.check(self.lone, 12)
        self.check(self.lone, 105)
        lone = Listing.objects.get(pk=self.lone.pk)
        # Judged by the band, an OK lone price is kept as the shop's last good price.
        self.assertEqual((lone.sanity, lone.sanity_reason, lone.last_ok_price), (OK, "", Decimal("105.00")))

    def test_the_set_band_wins_over_the_game_band(self):
        from .models import TypeBand

        TypeBand.objects.filter(product_set=None).update(p10=Decimal("20.00"), p90=Decimal("40.00"))
        self.check(self.lone, 22)
        self.assertEqual(self.verdicts(self.lone), [EXCLUDED])
        # A product without a set, or in a set with no band, uses the game band.
        loose = Product.objects.create(game=self.set.game, name="Mystery Booster Box", slug="mystery", product_type="booster_box")
        other = make_product(make_set(self.set.game, name="Stellar Crown", slug="stellar-crown"),
                             name="Stellar Crown Booster Box", slug="stellar", product_type="booster_box")
        for product in (loose, other):
            listing = self.shop(100, product)
            self.check(listing, 22)
            self.assertEqual(self.verdicts(listing), [OK])

    def test_another_type_or_game_has_no_band(self):
        tin = make_product(self.set, name="Surging Sparks Tin", slug="tin", product_type="tin")
        listing = self.shop(100, tin)
        self.check(listing, 2)
        self.assertEqual(self.verdicts(listing), [OK])
        magic = make_set(make_game(name="Magic", slug="magic-the-gathering"), name="Foundations", slug="foundations")
        box = make_product(magic, name="Foundations Play Booster Box", slug="fdn", product_type="booster_box")
        listing = self.shop(100, box)
        self.check(listing, 2)
        self.assertEqual(self.verdicts(listing), [OK])

    def test_one_other_shop_still_leaves_the_band_to_judge(self):
        other = self.shop(40)
        self.check(self.lone, 40)
        self.assertEqual(self.verdicts(self.lone, other), [DOUBTFUL, DOUBTFUL])

    def test_two_other_shops_agreeing_are_all_that_is_asked(self):
        others = [self.shop(40), self.shop(40)]
        Listing.objects.filter(pk=self.lone.pk).update(last_ok_price=Decimal("120.00"))
        for days in range(1, 11):
            DailyLowestPrice.objects.create(product=self.product, date=timezone.localdate() - timedelta(days=days), price="150.00")
        with mock.patch.object(sanity, "own_verdict", wraps=sanity.own_verdict) as own:
            self.check(self.lone, 40)
        own.assert_not_called()
        self.assertEqual(self.verdicts(self.lone, *others), [OK, OK, OK])
        with self.assertNumQueries(1):
            sanity.judge_product(self.product.pk)

    def test_a_lone_listing_costs_one_query_for_its_evidence(self):
        sanity.judge_product(self.product.pk)
        with self.assertNumQueries(2):
            sanity.judge_product(self.product.pk)


class LoneShopHistoryTests(Shops, TestCase):
    def test_a_lone_shop_is_judged_against_its_last_good_price(self):
        lone = self.shop(120)
        other = self.shop(125)
        sanity.judge_product(self.product.pk)
        self.assertEqual(Listing.objects.get(pk=lone.pk).last_ok_price, Decimal("120.00"))
        Listing.objects.filter(pk=other.pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.check(lone, 40)
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.sanity_ratio), (DOUBTFUL, Decimal("0.33")))
        self.assertIn("was £120.00 last time at this shop", lone.sanity_reason)
        self.check(lone, 118)
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.sanity_reason, lone.last_ok_price), (OK, "", Decimal("118.00")))
        # Three times dearer is still a price the shop may have; over it is doubtful, never kept out.
        self.check(lone, 400)
        self.assertEqual(self.verdicts(lone), [DOUBTFUL])
        self.check(lone, 5)
        self.assertEqual(self.verdicts(lone), [DOUBTFUL])

    def history(self, days, price="90.00"):
        """``days`` days of history before today, the lowest of them ``price``."""
        for day in range(1, days + 1):
            DailyLowestPrice.objects.create(product=self.product, date=timezone.localdate() - timedelta(days=day),
                                            price=Decimal(price) + day - 1)

    def test_far_under_the_ninety_day_low_is_doubtful_and_never_excluded(self):
        self.history(10)
        lone = self.shop(100)
        self.check(lone, 20)
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.sanity_ratio), (DOUBTFUL, Decimal("0.22")))
        self.assertIn("under a third of the lowest in 90 days, £90.00", lone.sanity_reason)
        self.check(lone, 1)
        self.assertEqual(self.verdicts(lone), [DOUBTFUL])

    def test_too_little_history_judges_nothing(self):
        self.history(3)
        lone = self.shop(100)
        self.check(lone, 20)
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.last_ok_price), (OK, None))

    def test_only_the_ninety_days_before_today_count(self):
        for day in (0, *range(91, 101)):
            DailyLowestPrice.objects.create(product=self.product, date=timezone.localdate() - timedelta(days=day), price="90.00")
        lone = self.shop(100)
        self.check(lone, 20)
        self.assertEqual(self.verdicts(lone), [OK])

    def test_a_doubtful_price_cannot_vouch_for_itself_the_next_day(self):
        self.history(10)
        lone = self.shop(100)
        now = timezone.now()

        def check(price, days):
            pricing.record_check(lone, price=Decimal(price), delivery_cost=Decimal("0.00"), availability=IN_STOCK,
                                 checked_at=now + timedelta(days=days))
            return Listing.objects.get(pk=lone.pk)

        doubted = check("20.00", 0)
        self.assertEqual((doubted.sanity, doubted.last_ok_price), (DOUBTFUL, None))
        # Doubtful prices stay in the daily history, so tomorrow's 90-day low is this very price.
        self.assertEqual(DailyLowestPrice.objects.get(product=self.product, date=timezone.localdate(now)).price,
                         Decimal("20.00"))
        sanity.judge_product(self.product.pk, now=now + timedelta(days=1))
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.last_ok_price), (DOUBTFUL, None))
        self.assertIn("£90.00", lone.sanity_reason)
        # It is judged by the history before it was doubted, at one more query, however its reason moves.
        with self.assertNumQueries(3):
            sanity.judge_product(self.product.pk, now=now + timedelta(days=1))
        moved = check("21.00", 1)
        self.assertEqual((moved.sanity, moved.sanity_ratio, moved.sanity_at), (DOUBTFUL, Decimal("0.23"), doubted.sanity_at))
        self.assertEqual(check("21.00", 2).sanity, DOUBTFUL)
        # A price the history before the doubt agrees with clears it and is kept as the last good price.
        cleared = check("88.00", 3)
        self.assertEqual((cleared.sanity, cleared.last_ok_price), (OK, Decimal("88.00")))
        self.assertEqual(cleared.sanity_at, now + timedelta(days=3))

    def test_the_ninety_day_low_stops_a_price_walking_down_in_one_day(self):
        # Each step is within what the shop's last good price allows; the history before today is not.
        self.history(10)
        lone = self.shop(120)
        Listing.objects.filter(pk=lone.pk).update(last_ok_price=Decimal("120.00"))
        self.check(lone, 50)
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.last_ok_price), (OK, Decimal("50.00")))
        self.check(lone, 20)
        lone.refresh_from_db()
        self.assertEqual((lone.sanity, lone.last_ok_price), (DOUBTFUL, Decimal("50.00")))
        self.assertIn("under a third of the lowest in 90 days", lone.sanity_reason)


class KeptOutComesBackTests(Shops, TestCase):
    """A price the other shops kept out, once fewer than two of them are left."""

    def setUp(self):
        super().setUp()
        self.peers = [self.shop(price) for price in (120, 125, 130)]
        self.odd = self.shop(130)
        sanity.judge_product(self.product.pk)
        self.check(self.odd, "9.99")
        for peer in self.peers:
            pricing.record_check(peer, price=peer.price, delivery_cost=Decimal("0.00"),
                                 availability=Listing.Availability.OUT_OF_STOCK)
        self.odd.refresh_from_db()
        self.reason = self.odd.sanity_reason
        self.assertEqual((self.odd.sanity, self.odd.last_ok_price), (EXCLUDED, Decimal("130.00")))

    def test_a_corrected_price_comes_back_on_its_own_evidence(self):
        self.check(self.odd, 128)
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.sanity_reason, self.odd.last_ok_price), (OK, "", Decimal("128.00")))
        self.assertEqual(Product.objects.for_lists().get(pk=self.product.pk).lowest_price, Decimal("128.00"))

    def test_a_small_change_to_the_wrong_price_stays_out(self):
        self.check(self.odd, "10.49")
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.sanity_reason), (EXCLUDED, self.reason))

    def test_an_unchanged_price_stays_out_even_when_its_evidence_agrees(self):
        Listing.objects.filter(pk=self.odd.pk).update(last_ok_price=Decimal("9.99"))
        self.check(self.odd, "9.99")
        self.assertEqual(self.verdicts(self.odd), [EXCLUDED])

    def test_a_changed_price_with_no_evidence_stays_out(self):
        Listing.objects.filter(pk=self.odd.pk).update(last_ok_price=None)
        self.check(self.odd, 128)
        self.odd.refresh_from_db()
        self.assertEqual((self.odd.sanity, self.odd.sanity_reason), (EXCLUDED, self.reason))

    def test_a_changed_price_the_one_shop_left_disagrees_with_stays_out(self):
        pricing.record_check(self.peers[0], price=Decimal("300.00"), delivery_cost=Decimal("0.00"), availability=IN_STOCK)
        self.check(self.odd, 80)
        self.assertEqual(self.verdicts(self.odd), [EXCLUDED])


class LoneBandPageTests(Boxes, TestCase):
    def setUp(self):
        super().setUp()
        self.boxes(EIGHT)
        sanity.rebuild_bands()
        self.lone = self.shop(100)

    def test_the_page_says_a_band_kept_it_out_without_naming_other_shops(self):
        self.check(self.lone, 12)
        page = self.client.get(self.product.get_absolute_url())
        self.assertIn(self.lone.pk, [listing.pk for listing in page.context["unavailable"]])
        self.assertContains(page, "Not counted: far below the usual price for this kind of product.")
        self.assertNotContains(page, "far from the other shops")

    def test_two_shops_agreeing_far_below_the_band_are_doubtful_not_hidden(self):
        other = self.shop(12)
        self.check(self.lone, 12)
        self.assertEqual(self.verdicts(self.lone, other), [DOUBTFUL, DOUBTFUL])
        self.assertIn(sanity.BAND_KEPT_OUT, Listing.objects.get(pk=self.lone.pk).sanity_reason)
        self.assertEqual(Product.objects.for_lists().get(pk=self.product.pk).lowest_price, Decimal("12.00"))
        self.assertTrue(self.summary().suspect)

    def test_one_other_shop_far_away_leaves_the_band_to_keep_it_out(self):
        other = self.shop(100)
        self.check(self.lone, 12)
        self.assertEqual(self.verdicts(self.lone, other), [EXCLUDED, OK])
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, "Not counted: far below the usual price for this kind of product.")
