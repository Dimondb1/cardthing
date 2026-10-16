"""Claude settles every Things to check row itself: the decision policy as a table, the safe choices when it
still cannot tell, never a merge across a DIFFERENT certainty, pairs kept apart only for now, and the owner's
own answers always first."""

from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from . import autopilot, checks, judge, sanity
from .management.commands import merge_duplicates
from .models import CheckAnswer, ClaudeAsk, ClaudeJudge, Listing, Product
from .testing import make_product
from .tests_judge import BOX, Base, reply

Ask = ClaudeAsk
Kind = CheckAnswer.Kind


def answer(verdict, confidence="high", differences=(), careful=False):
    return {"verdict": verdict, "confidence": confidence, "differences": list(differences), "careful": careful}


class PolicyBase(Base):
    def setUp(self):
        super().setUp()
        self.bundle = make_product(self.set, name="Surging Sparks Booster Bundle", product_type="bundle")

    def doubtful(self, title="Surging Sparks Booster Bundle", **kwargs):
        kwargs.setdefault("shop_ean", "")
        cheap = self.shop(30, product=self.bundle, title=title, **kwargs)
        self.shop(100, product=self.bundle, title=self.bundle.name, shop_ean="")
        sanity.judge_product(self.bundle.pk)
        return cheap

    def wrong(self):
        best = self.shop(20, title=BOX, shop_ean="")
        second = self.shop(100, shop_ean="")
        self.shop(104)
        return best, second

    def pair(self):
        keep = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        other = make_product(self.set, name="Scarlet & Violet Surging Sparks Elite Trainer Box")
        self.shop(50, product=keep, title=keep.name)
        self.shop(51, product=other, title=other.name)
        return keep, other

    def rows(self):
        return {row.key: row for row in judge.open_rows()}


class PolicyTableTests(PolicyBase):
    """Every row kind and every confidence ends in an action, or in a careful second look first."""

    def test_the_policy_table(self):
        cheap = self.doubtful()
        best, second = self.wrong()
        found = self.found(price="101.00")
        keep, other = self.pair()
        rows = self.rows()
        doubtful, cheaper, dearer = rows[f"offer:{cheap.pk}"], rows[f"offer:{best.pk}"], rows[f"offer:{second.pk}"]
        page, pair = rows[f"found:{found.pk}"], rows[judge.pair_key(keep, other)]
        self.assertTrue(cheaper.cheaper and not dearer.cheaper)
        self.assertTrue(pair.agrees)
        self.assertIn(pair.certainty, (judge.CERTAIN, judge.LIKELY))
        careful = judge.CAREFUL_LOOK
        same, settled = answer("same"), answer("same", careful=True)
        table = [
            # (row, answer, the other price's answer, action or why not)
            (doubtful, answer("same"), None, "check"),
            (doubtful, answer("same", "medium"), None, "check"),
            (doubtful, answer("different", differences=["condition"]), None, "hide"),
            (doubtful, answer("different", "medium", ["kind"]), None, "hide"),
            (doubtful, answer("different", "medium", ["condition"]), None, careful),
            (doubtful, answer("different", "medium", ["condition"], careful=True), None, "hide"),
            (doubtful, answer("different", differences=["price"]), None, "check"),
            (doubtful, answer("unsure"), None, careful),
            (doubtful, answer("same", "low"), None, careful),
            (doubtful, answer("unsure", careful=True), None, "check"),
            (cheaper, answer("same"), same, "check"),
            (cheaper, answer("different", differences=["kind"]), None, "hide"),
            (cheaper, answer("unsure"), None, careful),
            (cheaper, answer("unsure", careful=True), None, "hide"),
            (dearer, answer("same"), same, "check"),
            (dearer, answer("different", differences=["kind"]), same, "hide"),
            (dearer, answer("different", differences=["kind"]), answer("unsure"), judge.PARTNER_FIRST),
            (dearer, answer("different", differences=["kind"]), answer("unsure", careful=True), "hide"),
            (dearer, answer("unsure", careful=True), same, "check"),
            (page, answer("same"), None, "link"),
            (page, answer("same", "medium"), None, "link"),
            (page, answer("different", "medium", ["condition"]), None, "refuse"),
            (page, answer("unsure"), None, careful),
            (page, answer("unsure", careful=True), None, "refuse"),
            (pair, answer("same"), None, "merge"),
            (pair, answer("same", "medium"), None, "merge"),
            (pair, answer("different", differences=["edition"]), None, "apart"),
            (pair, answer("different", "medium", ["edition"]), None, "held"),
            (pair, answer("different", differences=["variant"]), None, "held"),
            (pair, answer("unsure"), None, careful),
            (pair, answer("unsure", careful=True), None, "held"),
        ]
        for row, said, partner, expected in table:
            action, why = judge.ruling(row, said, partner)
            self.assertEqual(action or why, expected, (row.key, said, partner))
            self.assertEqual(judge.decide(row, said, True, partner=partner), action)
        self.assertIsNone(judge.decide(doubtful, same, False))
        self.assertTrue(settled["careful"])

    def test_the_certainty_names_match_merge_duplicates(self):
        self.assertEqual((judge.CERTAIN, judge.LIKELY, judge.DIFFERENT),
                         (merge_duplicates.CERTAIN, merge_duplicates.LIKELY, merge_duplicates.DIFFERENT))

    def test_only_a_conflict_with_a_rule_needs_the_owner(self):
        barcode = make_product(self.set, name="Stellar Crown Booster Display", product_type="booster_box",
                               ean="0820650851230")
        cheap = self.shop(30, product=barcode, title=barcode.name, shop_ean="5099999999999")
        self.shop(100, product=barcode, title=barcode.name, shop_ean="")
        sanity.judge_product(barcode.pk)
        row = self.rows()[f"offer:{cheap.pk}"]
        action, why = judge.ruling(row, answer("same"))
        self.assertEqual((action, why), (None, judge.CAREFUL_LOOK))
        self.assertFalse(judge.needs_you(why))
        action, why = judge.ruling(row, answer("same", careful=True))
        self.assertIsNone(action)
        self.assertTrue(judge.needs_you(why))
        # Claude unsure, the rule decides: the barcode is not ours, so the price goes.
        self.assertEqual(judge.ruling(row, answer("unsure", careful=True))[0], "hide")
        for waiting in (judge.CAREFUL_LOOK, judge.PARTNER_FIRST, judge.BARCODE_NEXT_READ):
            self.assertFalse(judge.needs_you(waiting))


class SafeChoiceTests(PolicyBase):
    """Still unsure after the careful second look, each kind of row gets its safe choice, with Undo."""

    def unsure_twice(self):
        self.switch_on()
        self.ask(lambda key, params: reply(key, "unsure", confidence="medium"))
        self.assertEqual(self.answers(), [])
        ClaudeJudge.objects.filter(pk=1).update(last_run_at=None)
        client, _ = self.ask(lambda key, params: reply(key, "unsure", confidence="medium"))
        self.assertEqual({params["output_config"]["effort"] for _, params in client.sent}, {"high"})

    def test_a_doubtful_price_stays_as_shown_with_no_saving_claimed(self):
        cheap = self.doubtful()
        self.unsure_twice()
        [checked] = self.answers()
        self.assertEqual((checked.kind, checked.listing), (Kind.CHECKED, cheap))
        cheap.refresh_from_db()
        self.assertEqual((cheap.is_active, cheap.sanity, cheap.trusted_price), (True, Listing.Sanity.DOUBTFUL, None))
        self.assertEqual(checks.doubtful_count(), 0)

    def test_the_cheaper_price_of_a_wrong_match_is_taken_off_and_the_dearer_stays(self):
        best, second = self.wrong()
        self.unsure_twice()
        self.assertFalse(Listing.objects.get(pk=best.pk).is_active)
        self.assertTrue(Listing.objects.get(pk=second.pk).is_active)
        hid = CheckAnswer.objects.get(kind=Kind.HIDE)
        self.assertIn("back on the site", autopilot.undo(hid))

    def test_a_found_page_is_refused(self):
        self.shop(100)
        self.shop(104)
        row = self.found(price="101.00")
        self.unsure_twice()
        [refused] = self.answers()
        self.assertEqual((refused.kind, refused.shop_product_id), (Kind.REFUSE, row.pk))

    def test_a_pair_is_kept_apart_for_now_and_never_merged(self):
        keep, other = self.pair()
        self.unsure_twice()
        [held] = self.answers()
        self.assertEqual(held.kind, Kind.HELD)
        self.assertEqual(Product.objects.filter(pk__in=[keep.pk, other.pk], is_active=True).count(), 2)
        self.assertEqual(checks.duplicates(), [])
        # Undo hands the pair to the owner: Claude does not ask about it again, and it is not counted against it.
        self.assertIn("back under Possible duplicates", autopilot.undo(held))
        self.assertEqual(Ask.objects.order_by("-pk").first().owner_answer, "look")
        self.assertEqual(len(checks.duplicates()), 1)
        self.assertEqual([row.key for row in judge.waiting()], [])


class HeldPairTests(PolicyBase):
    def hold(self):
        keep, other = self.pair()
        self.switch_on()
        self.ask(lambda key, params: reply(key, "different", confidence="medium", differences=["edition"]))
        held = CheckAnswer.objects.get(kind=Kind.HELD)
        return keep, other, held

    def test_a_hold_is_lifted_when_the_pair_changes_and_claude_asks_again(self):
        keep, other, held = self.hold()
        self.assertEqual(judge.lift_holds(), 0)
        self.assertEqual(judge.waiting(), [])
        # A shop's title for one of them changes: new evidence.
        Listing.objects.filter(product=other).update(title="Surging Sparks Elite Trainer Box (Pokemon)")
        self.assertEqual(judge.lift_holds(), 1)
        self.assertFalse(CheckAnswer.objects.filter(pk=held.pk).exists())
        self.assertEqual([row.key for row in judge.waiting()], [judge.pair_key(keep, other)])
        ClaudeJudge.objects.filter(pk=1).update(last_run_at=None)
        self.ask()
        self.assertEqual(CheckAnswer.objects.get(kind=Kind.MERGE).other, other)

    def test_a_new_description_lifts_a_hold_too(self):
        keep, other, _held = self.hold()
        Listing.objects.filter(product=keep).update(description="9 booster packs, English")
        self.assertEqual(judge.lift_holds(), 1)

    def test_with_claude_off_held_pairs_are_the_owners_to_see_again(self):
        self.hold()
        self.assertEqual(checks.duplicates(), [])
        ClaudeJudge.objects.filter(pk=1).update(enabled=False)
        self.assertEqual(len(checks.duplicates()), 1)
        with override_settings(RIPRAPTOR_CLAUDE=False):
            ClaudeJudge.objects.filter(pk=1).update(enabled=True)
            self.assertEqual(len(checks.duplicates()), 1)

    def test_a_hold_is_never_for_good(self):
        keep, other, _held = self.hold()
        self.assertNotIn(frozenset((keep.pk, other.pk)), checks.kept_apart())
        # The hourly merge and the spreadsheet see only the owner's and Claude's sure Not the same.
        self.assertIn(frozenset((keep.pk, other.pk)), checks.held_apart())


class NeverAcrossDifferentTests(PolicyBase):
    def test_a_sure_same_never_merges_a_pair_a_rule_calls_different(self):
        keep = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        other = make_product(self.set, name="Surging Sparks Pokemon Center Elite Trainer Box")
        self.shop(50, product=keep, title=keep.name)
        self.shop(51, product=other, title=other.name)
        self.assertEqual(merge_duplicates.certainty(keep, other), merge_duplicates.DIFFERENT)
        row = judge.Evidence({keep.pk, other.pk}).pair(keep, other)
        self.assertEqual(judge.ruling(row, answer("same"))[0], "held")
        # The act checks it again, whatever decided it.
        with self.assertRaises(autopilot.Stale):
            judge.still_duplicates(keep, other)

    def test_the_act_refuses_a_merge_the_certainty_forbids(self):
        keep, other = self.pair()
        row = judge.Evidence({keep.pk, other.pk}).pair(keep, other)
        self.switch_on()
        ask = Ask.objects.create(kind="pair", row_key=row.key, fingerprint=row.fingerprint, model_asked="m",
                                 model_answered="m", effort="medium", outcome=Ask.Outcome.ANSWERED, verdict="same",
                                 confidence="high", reason="Same.", evidence=row.evidence)
        with mock.patch("catalogue.management.commands.merge_duplicates.certainty", return_value="different"):
            self.assertFalse(judge.act(row, ask, "merge", timezone.now()))
        self.assertTrue(Product.objects.get(pk=other.pk).is_active)


class OwnerFirstTests(PolicyBase):
    def test_the_owners_not_the_same_is_never_asked_about(self):
        keep, other = self.pair()
        autopilot.owner_apart(keep, other)
        self.assertEqual([row for row in judge.open_rows() if row.kind == "pair"], [])

    def test_a_price_the_owner_answered_is_never_acted_on_again(self):
        cheap = self.doubtful()
        autopilot.owner_hid(cheap)
        Listing.objects.filter(pk=cheap.pk).update(is_active=True)
        self.switch_on()
        client, _ = self.ask(lambda key, params: reply(key, "different", differences=["kind"]))
        self.assertEqual(client.sent, [])


class UndoShareTests(PolicyBase):
    def acts(self, n, kind=Kind.HIDE):
        made = []
        for i in range(n):
            ask = Ask.objects.create(kind="offer", row_key=f"offer:{1000 + i}", fingerprint="x", model_asked="m",
                                     model_answered="m", effort="medium", outcome=Ask.Outcome.ANSWERED,
                                     verdict="different", confidence="high", action=Ask.Action.ACTED)
            made.append(CheckAnswer.objects.create(kind=kind, what="Hid it", ask=ask))
        return made

    def test_three_undone_of_many_acts_is_not_a_pattern(self):
        self.switch_on(acting_since=timezone.now() - timedelta(days=1))
        made = self.acts(100)
        CheckAnswer.objects.filter(pk__in=[a.pk for a in made[:3]]).update(undone_at=timezone.now())
        self.assertFalse(judge.too_many_undone(timezone.now()))
        CheckAnswer.objects.filter(pk__in=[a.pk for a in made[:5]]).update(undone_at=timezone.now())
        with mock.patch("catalogue.notify.owner", return_value=True):
            self.assertTrue(judge.too_many_undone(timezone.now()))
        self.assertFalse(ClaudeJudge.objects.get().may_act)

    def test_undoing_safe_choices_never_counts(self):
        self.switch_on(acting_since=timezone.now() - timedelta(days=1))
        made = self.acts(3, kind=Kind.HELD) + self.acts(3, kind=Kind.CHECKED)
        CheckAnswer.objects.filter(pk__in=[a.pk for a in made]).update(undone_at=timezone.now())
        self.assertFalse(judge.too_many_undone(timezone.now()))
