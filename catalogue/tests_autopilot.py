"""The autopilot answers the Things to check rows its evidence settles, writes each answer down with its
reason, and every answer but a merge can be undone with one tap."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import autopilot, checks, sanity
from .models import (
    CheckAnswer, DailyLowestPrice, Listing, Product, ProductSet, Release, ShopProduct,
)
from .testing import make_game, make_listing, make_product, make_retailer, make_set

Kind = CheckAnswer.Kind
OK, DOUBTFUL = Listing.Sanity.OK, Listing.Sanity.DOUBTFUL
BOX = "Surging Sparks Booster Box"


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.set = make_set(self.game, name="Surging Sparks", slug="surging-sparks", code="SSP")
        self.product = make_product(self.set, name=BOX, product_type="booster_box")
        self.shops = 0

    def shop(self, price, product=None, title=BOX, **kwargs):
        self.shops += 1
        retailer = make_retailer(f"Shop {self.shops}", delivery_cost=Decimal("0"))
        return make_listing(product or self.product, retailer, price=str(price), title=title, **kwargs)

    def found(self, title=BOX, price="100.00", confidence=100, product=None):
        self.shops += 1
        retailer = make_retailer(f"Shop {self.shops}", delivery_cost=Decimal("0"))
        product = product or self.product
        return ShopProduct.objects.create(
            retailer=retailer, url=f"{retailer.website}products/box", title=title, price=Decimal(price),
            availability=Listing.Availability.IN_STOCK, suggested=product, product=product, confidence=confidence,
            status=ShopProduct.Status.REVIEW, source=ShopProduct.Source.FINDER,
        )

    def history(self, price, days=10, product=None):
        today = timezone.localdate()
        for n in range(1, days + 1):
            DailyLowestPrice.objects.create(product=product or self.product, date=today - timedelta(days=n),
                                            price=Decimal(str(price)))

    def kinds(self):
        return list(CheckAnswer.objects.order_by("pk").values_list("kind", flat=True))


class FoundAtAnotherShopTests(Base):
    def test_a_sure_name_at_the_going_rate_is_linked_and_undo_says_no(self):
        self.shop(100)
        self.shop(110)
        row = self.found(price="104.00")
        autopilot.run()
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.LINKED)
        answer = CheckAnswer.objects.get()
        self.assertEqual(answer.kind, Kind.LINK)
        self.assertIn("close to the £105.00 other shops charge", answer.why)
        linked = Listing.objects.get(product=self.product, retailer=row.retailer)
        self.assertEqual((answer.listing, linked.price), (linked, Decimal("104.00")))
        self.assertIn("not compared", autopilot.undo(answer))
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.IGNORED)
        self.assertFalse(Listing.objects.filter(pk=linked.pk).exists())
        self.assertIsNotNone(CheckAnswer.objects.get().undone_at)
        self.assertEqual(autopilot.undo(CheckAnswer.objects.get()), "")

    def test_a_likely_name_or_no_shop_to_compare_waits_for_the_owner(self):
        likely = self.found(confidence=90)
        alone = self.found(product=make_product(self.set, name="Surging Sparks Elite Trainer Box"),
                           title="Surging Sparks Elite Trainer Box")
        self.shop(100)
        autopilot.run()
        for row in (likely, alone):
            row.refresh_from_db()
            self.assertEqual(row.status, ShopProduct.Status.REVIEW)
        self.assertEqual(self.kinds(), [])

    def test_another_kind_another_set_code_or_a_far_price_is_refused_and_undo_links_it(self):
        self.shop(100)
        pack = self.found(title="Surging Sparks Booster Pack", price="4.50", confidence=70)
        dear = self.found(price="400.00")
        autopilot.run()
        for row in (pack, dear):
            row.refresh_from_db()
            self.assertEqual(row.status, ShopProduct.Status.IGNORED)
        answers = {a.shop_product_id: a for a in CheckAnswer.objects.all()}
        self.assertEqual(answers[pack.pk].why, "the shop's title says booster pack, not booster box")
        self.assertEqual(answers[dear.pk].why, "£400.00 is far from the £100.00 other shops charge")
        self.assertIn("linked", autopilot.undo(answers[dear.pk]))
        self.assertTrue(Listing.objects.filter(product=self.product, retailer=dear.retailer).exists())

    def test_a_title_naming_another_sets_code_is_refused(self):
        one_piece = make_game(name="One Piece", slug="one-piece")
        emperors = make_set(one_piece, name="Emperors in the New World", slug="emperors", code="OP-09")
        box = make_product(emperors, name="Emperors in the New World Booster Box", product_type="booster_box")
        row = self.found(product=box, title="One Piece OP10 Royal Blood Booster Box", confidence=70)
        same = self.found(product=box, title="One Piece OP-09 Emperors in the New World Booster Box", confidence=70)
        autopilot.run()
        row.refresh_from_db()
        same.refresh_from_db()
        self.assertEqual((row.status, same.status), (ShopProduct.Status.IGNORED, ShopProduct.Status.REVIEW))
        self.assertEqual(CheckAnswer.objects.get().why, "the shop's title names OP-10, another set")

    def test_a_bare_booster_title_is_not_taken_for_a_pack(self):
        self.assertEqual(autopilot.contradiction(self.product, "Surging Sparks Booster"), "")
        self.assertEqual(autopilot.contradiction(self.product, "Surging Sparks Booster Box (36 Packs)"), "")
        self.assertNotEqual(autopilot.contradiction(self.product, "Surging Sparks Sleeved Booster"), "")


class DoubtfulPriceTests(Base):
    def test_the_cheapest_price_this_product_has_always_had_is_counted_and_undo_judges_it_again(self):
        self.history(100)
        right = self.shop(98)
        case = self.shop(400, title=f"{BOX} Case")
        sanity.judge_product(self.product.pk)
        self.assertEqual([Listing.objects.get(pk=x.pk).sanity for x in (right, case)], [DOUBTFUL, DOUBTFUL])
        self.assertEqual(list(checks.doubtful_waiting()), [right])
        autopilot.run()
        right.refresh_from_db()
        self.assertEqual((right.sanity, right.trusted_price), (OK, Decimal("98.00")))
        answer = CheckAnswer.objects.get()
        self.assertEqual(answer.kind, Kind.TRUST)
        self.assertIn("usual lowest price of £100.00", answer.why)
        autopilot.undo(answer)
        right.refresh_from_db()
        self.assertEqual((right.sanity, right.trusted_price), (DOUBTFUL, None))

    def test_no_history_or_a_name_that_does_not_agree_waits_for_the_owner(self):
        right = self.shop(98)
        self.shop(400)
        sanity.judge_product(self.product.pk)
        autopilot.run()
        self.assertEqual(self.kinds(), [])
        self.history(100)
        Listing.objects.filter(pk=right.pk).update(title="Surging Sparks Booster Box Pokemon Center Exclusive")
        autopilot.run()
        self.assertEqual(self.kinds(), [])

    def test_a_price_whose_title_names_another_kind_is_hidden_even_when_dearer(self):
        self.shop(100)
        self.shop(105)
        etb = self.shop(250, title="Surging Sparks Elite Trainer Box")
        sanity.judge_product(self.product.pk)
        self.assertEqual(Listing.objects.get(pk=etb.pk).sanity, DOUBTFUL)
        self.assertEqual(checks.doubtful_count(), 0)
        autopilot.run()
        self.assertFalse(Listing.objects.get(pk=etb.pk).is_active)
        answer = CheckAnswer.objects.get()
        self.assertEqual((answer.kind, answer.listing_id), (Kind.HIDE, etb.pk))
        autopilot.undo(answer)
        self.assertTrue(Listing.objects.get(pk=etb.pk).is_active)

    def test_a_wrong_match_whose_title_says_pack_is_hidden(self):
        pack = self.shop(20, title="Surging Sparks Booster Pack")
        self.shop(100)
        self.assertEqual(len(checks.wrong_matches()), 1)
        autopilot.run()
        self.assertFalse(Listing.objects.get(pk=pack.pk).is_active)
        self.assertEqual(checks.wrong_matches(), [])
        self.assertEqual(self.kinds(), [Kind.HIDE])

    def test_only_the_cheapest_buyable_doubtful_price_is_listed(self):
        cheap = self.shop(30, title="")
        dear = self.shop(100, title="")
        sanity.judge_product(self.product.pk)
        self.assertEqual((checks.sanity_counts()["doubtful"], list(checks.doubtful_waiting())), (2, [cheap]))
        # Out of stock, the cheap one shows nowhere; the dear one is now the cheapest a visitor sees.
        Listing.objects.filter(pk=cheap.pk).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(list(checks.doubtful_waiting()), [dear])


class DuplicateTests(Base):
    def pair(self, ean="0820650853456", other_ean="0820650853456"):
        keep = make_product(self.set, name="Surging Sparks Elite Trainer Box", ean=ean)
        other = make_product(self.set, name="Surging Sparks Elite Trainer Box Exclusive", ean=other_ean)
        self.shop(50, product=keep, title=keep.name)
        return keep, other

    def test_the_same_barcode_is_merged(self):
        keep, other = self.pair()
        self.assertEqual(len(checks.duplicates()), 1)
        autopilot.run()
        self.assertFalse(Product.objects.filter(pk=other.pk).exists())
        self.assertEqual(self.kinds(), [Kind.MERGE])
        self.assertEqual(autopilot.undo(CheckAnswer.objects.get()), "")

    def test_different_barcodes_wait_and_a_shop_selling_both_or_not_the_same_keeps_them_apart(self):
        keep, other = self.pair(other_ean="")
        autopilot.run()
        self.assertEqual(self.kinds(), [])
        self.assertEqual(len(checks.duplicates()), 1)
        autopilot.owner_apart(keep, other)
        self.assertEqual(checks.duplicates(), [])
        CheckAnswer.objects.all().delete()
        both = make_retailer("Both")
        make_listing(keep, both)
        make_listing(other, both)
        self.assertEqual(checks.duplicates(), [])


class AnnouncedSetTests(Base):
    def row(self, name="Destined Rivals"):
        return Release.objects.create(game=self.game, name=name, source="tcgdex_sets",
                                      release_date=timezone.localdate() + timedelta(days=40),
                                      precision=Release.Precision.DAY)

    def test_a_set_products_already_name_is_added_without_its_date_and_undo_takes_it_away(self):
        etb = Product.objects.create(game=self.game, name="Destined Rivals Elite Trainer Box",
                                     product_type="elite_trainer_box")
        row = self.row()
        autopilot.run()
        product_set = ProductSet.objects.get(name="Destined Rivals")
        self.assertIsNone(product_set.release_date)
        etb.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual((etb.product_set, row.status), (product_set, Release.Status.ACCEPTED))
        answer = CheckAnswer.objects.get()
        self.assertIn("TCGdex announced it and 1 product on the site already name it", answer.why)
        self.assertIn("not a set", autopilot.undo(answer))
        etb.refresh_from_db()
        row.refresh_from_db()
        self.assertFalse(ProductSet.objects.filter(name="Destined Rivals").exists())
        self.assertEqual((etb.product_set, row.status), (None, Release.Status.DISMISSED))

    def test_a_set_nothing_names_or_a_product_filed_elsewhere_names_waits(self):
        self.row()
        self.row(name="Surging Sparks Collection")
        autopilot.run()
        self.assertEqual(self.kinds(), [])
        self.assertEqual(Release.objects.filter(status=Release.Status.PENDING).count(), 2)


class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("checks")

    def test_sorted_for_you_lists_each_answer_with_undo(self):
        self.shop(100)
        pack = self.found(title="Surging Sparks Booster Pack", price="4.50", confidence=70)
        response = self.client.post(self.url, {"action": "autopilot_now"}, follow=True)
        self.assertContains(response, "Sorted 1 thing.")
        self.assertContains(response, "Sorted for you (1)")
        self.assertContains(response, "Not this product: 1.")
        self.assertContains(response, "&quot;Surging Sparks Booster Pack&quot; at Shop 2 is not Surging Sparks Booster Box")
        self.assertContains(response, 'value="Undo"', count=1)
        answer = CheckAnswer.objects.get()
        response = self.client.post(self.url, {"action": "undo", "answer": answer.pk}, follow=True)
        self.assertContains(response, "Undone: linked")
        pack.refresh_from_db()
        self.assertEqual(pack.status, ShopProduct.Status.LINKED)
        response = self.client.post(self.url, {"action": "undo", "answer": answer.pk}, follow=True)
        self.assertContains(response, "can no longer be undone")
        response = self.client.post(self.url, {"action": "autopilot_now"}, follow=True)
        self.assertContains(response, "Nothing more can be sorted without you")

    def test_not_the_same_keeps_a_pair_apart(self):
        keep = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        other = make_product(self.set, name="Surging Sparks Elite Trainer Box Exclusive")
        self.shop(50, product=keep, title=keep.name)
        self.assertContains(self.client.get(self.url), 'value="Not the same"', count=1)
        response = self.client.post(self.url, {"action": "apart", "keep": keep.pk, "other": other.pk}, follow=True)
        self.assertContains(response, "They are not suggested together again")
        self.assertContains(response, "Possible duplicates (0)")
        self.assertContains(response, "Sorted for you (0)")
        response = self.client.post(self.url, {"action": "apart", "keep": keep.pk, "other": other.pk}, follow=True)
        self.assertContains(response, "That group has changed")

    def test_the_heading_counts_only_doubtful_prices_shown_as_the_cheapest(self):
        self.shop(30, title="")
        self.shop(100, title="")
        sanity.judge_product(self.product.pk)
        page = self.client.get(self.url)
        self.assertContains(page, "Doubtful prices (1)")
        self.assertContains(page, "1 other doubtful price is dearer than another shop or out of stock")

    def test_the_answers_cost_the_same_queries_however_many(self):
        def answers(n):
            for i in range(n):
                CheckAnswer.objects.create(kind=Kind.HIDE, what=f"Hid {i}", why="a reason",
                                           listing=self.shop(100 + i), product=self.product)

        answers(1)
        with CaptureQueriesContext(connection) as one:
            self.client.get(self.url)
        answers(5)
        with CaptureQueriesContext(connection) as six:
            page = self.client.get(self.url)
        self.assertContains(page, "Sorted for you (6)")
        self.assertEqual(len(one), len(six))


class BusyDatabaseTests(Base):
    def test_one_answer_the_database_refuses_is_skipped_and_the_rest_go_on(self):
        from unittest import mock

        from django.db import OperationalError

        self.shop(100)
        first = self.found(title="Surging Sparks Booster Pack", price="4.50", confidence=70)
        second = self.found(price="400.00")
        from . import finder

        calls = []

        def ignore(row):
            calls.append(row.pk)
            if row.pk == first.pk:
                raise OperationalError("database is locked")
            return original(row)

        original = finder.ignore
        with mock.patch.object(finder, "ignore", side_effect=ignore), self.assertLogs("catalogue.autopilot", "WARNING"):
            done = autopilot.run()
        self.assertEqual(len(done), 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), (ShopProduct.Status.REVIEW, ShopProduct.Status.IGNORED))
        self.assertEqual(CheckAnswer.objects.get().shop_product, second)


class HourlyTests(Base):
    def test_tidy_all_runs_the_autopilot_only_when_it_is_on(self):
        self.shop(100)
        row = self.found(title="Surging Sparks Booster Pack", price="4.50", confidence=70)
        with override_settings(RIPRAPTOR_AUTOPILOT=False):
            call_command("tidy_all", stdout=StringIO())
        self.assertEqual(self.kinds(), [])
        out = StringIO()
        with override_settings(RIPRAPTOR_AUTOPILOT=True):
            call_command("tidy_all", "--dry-run", stdout=out)
            self.assertIn("1 things to check would be answered", out.getvalue())
            self.assertEqual(self.kinds(), [])
            call_command("tidy_all", stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual((row.status, self.kinds()), (ShopProduct.Status.IGNORED, [Kind.REFUSE]))
