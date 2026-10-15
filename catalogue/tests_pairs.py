"""Possible duplicates, from the pairs offered on Things to check to what Claude and the hourly merge may do
with them. No test here touches the network: tests_judge's fake client stands in for Anthropic."""

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.html import escape
from django.utils.timezone import now as timezone_now

from . import autopilot, checks, judge, sanity
from .models import CheckAnswer, ClaudeAsk, ClaudeJudge, Listing, OutboundClick, Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set
from .tests_judge import Base, reply

Ask = ClaudeAsk


class PairBase(Base):
    def pair(self, keep_name="Surging Sparks Elite Trainer Box",
             other_name="Scarlet & Violet Surging Sparks Elite Trainer Box"):
        keep = make_product(self.set, name=keep_name)
        other = make_product(self.set, name=other_name)
        self.shop(50, product=keep, title=keep.name)
        self.shop(51, product=other, title=other.name)
        return keep, other

    def offered(self):
        return [(keep, other) for keep, others in checks.duplicates() for other in others]

    def pairs(self):
        return {frozenset((keep.pk, other.pk)) for keep, other in self.offered()}

    def named(self, name, product_set=None, product_type="booster_box"):
        """A product shown on the site with one shop, filed under ``product_set`` or no set."""
        product = Product.objects.create(game=self.game, product_set=product_set, name=name, product_type=product_type)
        self.shop(40, product=product, title=name)
        return product


class ShopsOnBothTests(PairBase):
    """A shop that shows two products under their own names sells two products, so the pair is never offered.
    eBay and Amazon search for every product, and a hidden listing is not shown, so neither says so."""

    def test_ebay_and_amazon_on_both_still_offer_the_pair_and_claude_merges_it_losing_no_listing(self):
        keep, other = self.pair()
        for source in (Retailer.Source.EBAY, Retailer.Source.AMAZON):
            market = make_retailer(f"Market {source}", source_type=source)
            make_listing(keep, market, url=f"{market.website}item/1")
            make_listing(other, market, url=f"{market.website}item/2")
        self.assertEqual(self.offered(), [(keep, other)])
        listings = Listing.objects.count()
        self.switch_on()
        self.ask()
        other.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertEqual(Listing.objects.count(), listings)
        # Each marketplace's listing for the switched-off product stays with it, where nobody sees it.
        self.assertEqual(Listing.objects.filter(product=keep).count(), 4)
        self.assertEqual(set(Listing.objects.filter(product=other).values_list("retailer__source_type", flat=True)),
                         {Retailer.Source.EBAY, Retailer.Source.AMAZON})

    def test_a_hidden_listing_on_the_other_product_says_nothing(self):
        keep, other = self.pair()
        both = make_retailer("Both")
        make_listing(keep, both)
        make_listing(other, both, url=f"{both.website}p/other", is_active=False)
        self.assertEqual(self.offered(), [(keep, other)])
        self.switch_on()
        self.ask()
        self.assertFalse(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Listing.objects.get(retailer=both, product=other).is_active, False)

    def test_a_shop_showing_both_keeps_the_pair_off_the_list(self):
        keep, other = self.pair()
        both = make_retailer("Both")
        make_listing(keep, both)
        make_listing(other, both, url=f"{both.website}p/other")
        self.assertEqual(self.offered(), [])

    def test_a_shop_listing_both_while_claude_was_asked_stops_the_merge(self):
        keep, other = self.pair()
        both = make_retailer("Both")

        def answer(key, params):
            make_listing(keep, both)
            make_listing(other, both, url=f"{both.website}p/other")
            return reply(key)

        self.switch_on()
        self.ask(answer)
        self.assertTrue(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Ask.objects.get().action, Ask.Action.STALE)

    def test_the_shops_are_read_in_one_query(self):
        keep, other = self.pair()
        with CaptureQueriesContext(connection) as first:
            checks.duplicates()
        for n in range(5):
            extra = make_product(self.set, name=f"Surging Sparks Tin {n}", product_type="tin")
            twin = make_product(self.set, name=f"Scarlet & Violet Surging Sparks Tin {n}", product_type="tin")
            for product in (extra, twin):
                self.shop(20, product=product, title=product.name)
        with CaptureQueriesContext(connection) as more:
            self.assertEqual(len(self.offered()), 6)
        self.assertEqual(len(more), len(first))


class CandidatePairTests(PairBase):
    """Pairs reach Things to check and Claude through any key two names of one product share: the words (across
    product types), one set code, the set a product is filed under, or a barcode. Each pair stands alone."""

    def test_a_booster_display_filed_as_another_type_is_offered_beside_the_box(self):
        box = self.named("Chaos Rising Booster Box")
        display = self.named("Mega Evolution Chaos Rising Booster Display Box", product_type="elite_trainer_box")
        self.assertEqual(self.pairs(), {frozenset((box.pk, display.pk))})

    def test_one_set_code_pairs_two_translations_but_never_a_case_an_unwrapped_box_or_a_pack(self):
        box = self.named("(JAPANESE) Pokemon SV1a Triple Beat Booster Box")
        other = self.named("Triplet Beat sv1a Japanese Booster Box")
        self.named("Triplet Beat sv1a Japanese Booster Box No Shrink")
        self.named("Triple Beat sv1a Booster Box Case (Japanese)")
        self.named("(JAPANESE) Pokemon SV1a Triple Beat Booster Pack", product_type="booster_pack")
        self.named("Triple Beat sv1a Korean Booster Box")
        self.assertEqual(self.pairs(), {frozenset((box.pk, other.pk))})

    def test_a_code_only_name_meets_the_set_it_is_filed_under(self):
        rivals = make_set(self.game, name="Destined Rivals", slug="destined-rivals", code="SV10")
        coded = self.named("SV10 Booster Box", product_set=rivals)
        named = self.named("Destined Rivals Booster Box", product_set=rivals)
        self.named("Destined Rivals Booster Pack", product_set=rivals, product_type="booster_pack")
        self.named("Destined Rivals Pokemon Center Elite Trainer Box", product_set=rivals,
                   product_type="elite_trainer_box")
        self.assertEqual(self.pairs(), {frozenset((coded.pk, named.pk))})
        # Without the set, nothing in the two names says they are one product.
        Product.objects.filter(pk=coded.pk).update(product_set=None)
        self.assertEqual(self.pairs(), set())

    def test_two_products_with_one_barcode_are_offered(self):
        first = self.named("Surging Sparks Booster Bundle", product_type="bundle")
        second = self.named("Surging Sparks Bundle of Boosters", product_type="bundle")
        Product.objects.filter(pk__in=[first.pk, second.pk]).update(ean="0820650853494")
        self.assertEqual(self.pairs(), {frozenset((first.pk, second.pk))})

    def test_each_pair_stands_alone_so_a_lookalike_never_pulls_in_a_third(self):
        # The code pairs the first two and the words the last two; the first and last share nothing.
        pursue = self.named("Pursue Glory Together csv10 Slim Booster Box")
        chasing = self.named("Chasing Glory Together (csv10) Slim Booster Box Simplified Chinese")
        plain = self.named("Chasing Glory Together Slim Booster Box Simplified Chinese")
        self.assertEqual(self.pairs(), {frozenset((pursue.pk, chasing.pk)), frozenset((chasing.pk, plain.pk))})

    def test_a_pair_kept_apart_by_the_owner_or_claude_is_never_offered(self):
        keep, other = self.pair()
        self.assertTrue(checks.still_offered(keep, other))
        autopilot.owner_apart(keep, other)
        self.assertEqual(self.offered(), [])
        self.assertFalse(checks.still_offered(keep, other))
        CheckAnswer.objects.all().delete()
        self.switch_on()
        self.ask(lambda key, params: reply(key, "different", differences=["edition"]))
        self.assertEqual(CheckAnswer.objects.get().kind, CheckAnswer.Kind.APART)
        self.assertEqual(self.offered(), [])

    def test_claudes_apart_waits_when_the_pair_stopped_being_offered_while_it_was_asked(self):
        keep, other = self.pair()
        both = make_retailer("Both")

        def answer(key, params):
            make_listing(keep, both)
            make_listing(other, both, url=f"{both.website}p/other")
            return reply(key, "different", differences=["edition"])

        self.switch_on()
        self.ask(answer)
        self.assertEqual(CheckAnswer.objects.count(), 0)
        self.assertEqual(Ask.objects.get().action, Ask.Action.STALE)

    def test_a_product_paired_with_two_kept_ones_shows_claudes_answer_for_each_pair(self):
        shared = self.named("Chasing Glory Together (csv10) Slim Booster Box Simplified Chinese")
        first = self.named("Pursue Glory Together csv10 Slim Booster Box Simplified Chinese")
        second = self.named("Chasing Glory Together Slim Booster Box Simplified Chinese")
        for product in (first, second):
            self.shop(41, product=product, title=product.name)
        verdicts = {judge.pair_key(first, shared): "same", judge.pair_key(second, shared): "different"}
        self.switch_on(may_act=False)
        self.ask(lambda key, params: reply(key, verdicts[key], differences=[] if verdicts[key] == "same" else ["set"]))
        duplicates = checks.duplicates()
        judge.attach_answers([], [], [], duplicates)
        said = {keep.pk: [other.claude.verdict for other in others] for keep, others in duplicates}
        self.assertEqual(said, {first.pk: ["same"], second.pk: ["different"]})


class JudgeRuleTests(PairBase):
    """Claude keeps a pair apart for good only when sure of a plain difference, merges a fairly sure same only
    when the names agree on every point a rule can check, and is stopped by prices only when they plainly
    disagree."""

    def again(self, verdict="same", confidence="high", differences=()):
        Ask.objects.all().delete()
        ClaudeJudge.objects.filter(pk=1).update(last_run_at=None)
        self.ask(lambda key, params: reply(key, verdict, confidence=confidence, differences=list(differences)))

    def test_a_real_different_pair_is_never_merged(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering")
        eldraine = make_set(magic, name="Throne of Eldraine", slug="throne-of-eldraine", code="ELD")
        set_box = make_product(eldraine, name="Throne of Eldraine Set Booster Box", product_type="booster_box")
        draft = make_product(eldraine, name="Throne of Eldraine Booster Box", product_type="booster_box")
        self.shop(110, product=set_box, title=set_box.name)
        self.shop(100, product=draft, title=draft.name)
        # Offered for a person: shops drop the word "set". The hourly merge leaves it.
        self.assertEqual(self.pairs(), {frozenset((set_box.pk, draft.pk))})
        out = StringIO()
        call_command("merge_duplicates", stdout=out)
        self.assertIn("their booster kinds differ", out.getvalue())
        self.switch_on()
        for verdict, confidence, differences in (("same", "medium", []), ("same", "low", []), ("unsure", "high", []),
                                                 ("different", "medium", ["kind"]), ("different", "high", ["variant"])):
            self.again(verdict, confidence, differences)
            self.assertEqual(self.answers(), [])
        self.again("different", "high", ["kind"])
        self.assertEqual([a.kind for a in self.answers()], [CheckAnswer.Kind.APART])
        self.assertEqual(Product.objects.filter(pk__in=[set_box.pk, draft.pk], is_active=True).count(), 2)
        self.assertEqual(self.offered(), [])

    def test_a_fairly_sure_same_needs_one_language_one_barcode_and_close_prices_too(self):
        keep, other = self.pair()
        self.switch_on()
        Product.objects.filter(pk=keep.pk).update(ean="0820650851230")
        Product.objects.filter(pk=other.pk).update(ean="0820650851247")
        self.again(confidence="medium")
        self.assertEqual(self.answers(), [])
        Product.objects.filter(pk=other.pk).update(ean="")
        Listing.objects.filter(product=other).update(price=Decimal("120.00"))
        self.again(confidence="medium")
        self.assertEqual(self.answers(), [])
        Listing.objects.filter(product=other).update(price=Decimal("51.00"))
        self.again(confidence="medium")
        self.assertEqual([a.kind for a in self.answers()], [CheckAnswer.Kind.MERGE])
        self.assertTrue(self.answers()[0].undo_note)

    def test_the_names_must_agree_for_a_fairly_sure_merge(self):
        keep, other = self.pair()
        row = judge.Evidence({keep.pk, other.pk}).pair(keep, other)
        self.assertTrue(row.agrees)
        japanese = make_product(self.set, name="Surging Sparks Elite Trainer Box Japanese")
        coded = make_product(self.set, name="SSP Elite Trainer Box")
        case = make_product(self.set, name="Surging Sparks Elite Trainer Box Case")
        for product in (japanese, coded, case):
            self.assertFalse(judge.Evidence({keep.pk, product.pk}).pair(keep, product).agrees, product.name)
        # One set code is not enough: two blisters of one set hold different Pokemon.
        first = make_product(self.set, name="Scarlet & Violet 3 Obsidian Flames Premium Checklane Blister Kingambit",
                             product_type="bundle")
        second = make_product(self.set, name="Scarlet & Violet 3 Obsidian Flames Checklane Blister (Annihilape)",
                              product_type="bundle")
        self.assertIn(frozenset((first.pk, second.pk)), self.pairs())
        self.assertFalse(judge.Evidence({first.pk, second.pk}).pair(first, second).agrees)

    def test_prices_say_no_only_when_plainly_apart(self):
        money = [Decimal(n) for n in ("50", "52", "60", "90", "110")]
        fifty, fifty_two, sixty, ninety, hundred_ten = money
        self.assertFalse(judge.prices_disagree([fifty], [ninety]))
        self.assertTrue(judge.prices_disagree([fifty], [hundred_ten]))
        self.assertTrue(judge.prices_disagree([fifty, fifty_two], [ninety, ninety]))
        self.assertFalse(judge.prices_disagree([fifty, fifty_two], [sixty, sixty]))
        self.assertFalse(judge.prices_disagree([], [ninety]))


class AskingOrderTests(PairBase):
    """Pairs take turns with the price rows, so a long list of prices never starves them."""

    def doubtful(self, n):
        product = make_product(self.set, name=f"Surging Sparks Tin {n}", product_type="tin")
        cheap = self.shop(30, product=product, title=product.name, shop_ean="")
        self.shop(100, product=product, title=product.name, shop_ean="")
        sanity.judge_product(product.pk)
        cheap.refresh_from_db()
        self.assertEqual(cheap.sanity, Listing.Sanity.DOUBTFUL)
        return cheap

    def test_a_pair_is_asked_second_however_many_prices_wait(self):
        prices = [self.doubtful(n) for n in range(4)]
        keep, other = self.pair()
        order = [row.key for row in judge.waiting()]
        self.assertEqual(order[1], judge.pair_key(keep, other))
        self.assertEqual(set(order) - {order[1]}, {f"offer:{cheap.pk}" for cheap in prices})
        self.switch_on(may_act=False)
        with mock.patch.object(judge, "ROWS_PER_RUN", 2):
            client, _ = self.ask()
        self.assertIn(judge.pair_key(keep, other), [client.row_key(params) for _, params in client.sent])

    def test_the_pairs_visitors_want_come_first(self):
        first = self.pair()
        second = self.pair("Surging Sparks Booster Bundle", "Scarlet & Violet Surging Sparks Booster Bundle")
        # By name, the bundle comes first; a visitor's click on the box puts it first.
        self.assertEqual([row.key for row in judge.waiting()][0], judge.pair_key(*second))
        OutboundClick.objects.create(product=first[1], retailer=Retailer.objects.first())
        self.assertEqual([row.key for row in judge.waiting()][0], judge.pair_key(*first))

    def test_turns_keep_a_wrong_matchs_two_prices_together(self):
        rows = [judge.Row("offer", "offer:1", {}), judge.Row("offer", "offer:2", {}, partner="offer:3"),
                judge.Row("offer", "offer:3", {}, partner="offer:2"), judge.Row("pair", "pair:1:2", {}),
                judge.Row("pair", "pair:3:4", {}), judge.Row("found", "found:1", {}), judge.Row("found", "found:2", {})]
        lanes = {"offer:1": 0, "offer:2": 0, "offer:3": 0, "pair:1:2": 1, "pair:3:4": 1, "found:1": 2, "found:2": 3}
        with mock.patch.object(judge, "worth", side_effect=lambda row: lanes[row.key]):
            order = [row.key for row in judge.take_turns(rows)]
        self.assertEqual(order, ["offer:1", "pair:1:2", "found:1", "offer:2", "offer:3", "pair:3:4", "found:2"])


class UndoableMergeTests(PairBase):
    """The hourly merge and the owner's Merge these switch the other product off rather than delete it, write
    each merge down with Undo, and never delete a shop's listing."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("checks")

    def hourly(self):
        out = StringIO()
        call_command("tidy_all", stdout=out)
        return out.getvalue()

    def test_every_hourly_merge_can_be_undone_and_is_never_made_again(self):
        keep, other = self.pair()
        listings = Listing.objects.count()
        self.hourly()
        self.assertEqual(Listing.objects.count(), listings)
        self.assertFalse(Product.objects.get(pk=other.pk).is_active)
        [answer] = CheckAnswer.objects.all()
        self.assertEqual((answer.kind, answer.by_owner, answer.product, answer.other),
                         (CheckAnswer.Kind.MERGE, False, keep, other))
        self.assertTrue(answer.undo_note)
        page = self.client.get(self.url)
        self.assertContains(page, escape(f"Merged {other.name} into {keep.name}"))
        self.assertContains(page, "The hourly tidy merged them")
        self.assertNotContains(page, "A merge cannot be undone")
        response = self.client.post(self.url, {"action": "undo", "answer": answer.pk}, follow=True)
        self.assertContains(response, "is its own product again")
        self.assertTrue(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Listing.objects.get(title=other.name).product, other)
        # Undo said they are not one product: the next hour leaves them, and Claude is not asked.
        self.assertIn("you said they are not the same", self.hourly())
        self.assertTrue(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual([row.kind for row in judge.waiting()], [])

    def test_the_hourly_merge_never_deletes_or_hides_a_shops_listing(self):
        keep, other = self.pair()
        both = make_retailer("Both")
        shown = make_listing(keep, both)
        theirs = make_listing(other, both, url=f"{both.website}p/other")
        listings = Listing.objects.count()
        self.assertIn("a shop sells both", self.hourly())
        self.assertEqual(Product.objects.filter(pk__in=[keep.pk, other.pk], is_active=True).count(), 2)
        Listing.objects.filter(pk=shown.pk).update(is_active=False)
        self.assertIn("a shop's price would leave the site", self.hourly())
        self.assertEqual(Product.objects.filter(pk__in=[keep.pk, other.pk], is_active=True).count(), 2)
        self.assertEqual(Listing.objects.count(), listings)
        # Things to check offers it the other way round, so the shop's shown price stays.
        self.assertEqual(self.offered(), [(other, keep)])
        Listing.objects.filter(pk=theirs.pk).update(is_active=False)
        self.hourly()
        self.assertFalse(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Listing.objects.count(), listings)

    def test_marketplaces_on_both_never_stop_the_hourly_merge_and_lose_nothing(self):
        keep, other = self.pair()
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        make_listing(keep, ebay, url=f"{ebay.website}itm/1")
        make_listing(other, ebay, url=f"{ebay.website}itm/2")
        listings = Listing.objects.count()
        self.hourly()
        self.assertFalse(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Listing.objects.count(), listings)
        self.assertEqual(Listing.objects.filter(product=other, retailer=ebay).count(), 1)

    def test_two_others_one_shop_shows_side_by_side_are_never_merged_together(self):
        keep = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        first = make_product(self.set, name="Scarlet & Violet Surging Sparks Elite Trainer Box")
        second = make_product(self.set, name="Pokemon Surging Sparks Elite Trainer Box")
        for product in (keep, keep, first, second):
            self.shop(50, product=product, title=product.name)
        both = make_retailer("Both")
        make_listing(first, both)
        make_listing(second, both, url=f"{both.website}p/second")
        self.assertIn("a shop sells both", self.hourly())
        self.assertEqual(CheckAnswer.objects.filter(kind=CheckAnswer.Kind.MERGE).count(), 1)
        self.assertEqual(Product.objects.filter(pk__in=[first.pk, second.pk], is_active=True).count(), 1)

    def test_the_owners_merge_these_is_his_and_each_pair_can_be_undone(self):
        keep = make_product(self.set, name="SSP Elite Trainer Box")
        first = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        second = make_product(self.set, name="Surging Sparks ETB")
        self.shop(50, product=keep, title=keep.name)
        self.shop(50, product=keep, title=keep.name)
        for product in (first, second):
            self.shop(51, product=product, title=product.name)
        self.assertEqual(Product.objects.filter(is_active=True).count(), 4)   # the hourly merge would join the last two
        [(kept, others)] = [(k, o) for k, o in checks.duplicates() if k == keep]
        response = self.client.post(self.url, {"action": "merge", "keep": keep.pk, "other": [o.pk for o in others]},
                                    follow=True)
        self.assertContains(response, "Each merge is listed under Sorted for you, with Undo.")
        answers = list(CheckAnswer.objects.order_by("pk"))
        self.assertEqual([(a.kind, a.by_owner, a.other_id) for a in answers],
                         [(CheckAnswer.Kind.MERGE, True, other.pk) for other in others])
        self.assertTrue(all(a.undo_note for a in answers))
        self.assertEqual(Product.objects.filter(pk__in=[o.pk for o in others], is_active=True).count(), 0)
        page = self.client.get(self.url)
        self.assertContains(page, "You tapped Merge these")
        # Newest first: an undo puts that one product back with its listing.
        last = answers[-1]
        response = self.client.post(self.url, {"action": "undo", "answer": last.pk}, follow=True)
        self.assertContains(response, "is its own product again")
        self.assertTrue(Product.objects.get(pk=last.other_id).is_active)
        self.assertFalse(judge.too_many_undone(timezone_now()))
