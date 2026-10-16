"""The $100 month: Sort everything sends a backlog through the Message Batches API at half price, one batch at
a time, inside the monthly limit; every run collects a batch that has ended and acts on its answers by the
same rules. No test here touches the network."""

import json
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.utils import timezone

from . import judge
from .models import CheckAnswer, ClaudeAsk, ClaudeJudge, ShopProduct
from .tests_judge import Base, FakeClient, reply, usage

Ask = ClaudeAsk


class FakeBatches(FakeClient):
    """A FakeClient with messages.batches: create keeps the requests, retrieve says ``status``, results
    answers each through ``answer`` (or ``result_for``, which may give another result type)."""

    def __init__(self, answer=None, result_for=None):
        super().__init__(answer)
        self.batches, self.status, self.retrieved = {}, "in_progress", []
        self.result_for = result_for
        self.messages.batches = SimpleNamespace(create=self.create_batch, retrieve=self.retrieve_batch,
                                                results=self.batch_results)

    def create_batch(self, requests):
        batch_id = f"msgbatch_{len(self.batches) + 1}"
        self.batches[batch_id] = requests
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def retrieve_batch(self, batch_id):
        self.retrieved.append(batch_id)
        return SimpleNamespace(id=batch_id, processing_status=self.status)

    def batch_results(self, batch_id):
        for request in self.batches[batch_id]:
            key = self.row_key(request["params"])
            if self.result_for is not None:
                result = self.result_for(key, request)
            else:
                result = SimpleNamespace(type="succeeded", message=self.answer(key, request["params"]))
            yield SimpleNamespace(custom_id=request["custom_id"], result=result)


class BatchBase(Base):
    def backlog(self, n=judge.BATCH_MIN + 1):
        self.shop(100)
        self.shop(104)
        return [self.found(title=f"Surging Sparks Booster Display {i}", price="101.00") for i in range(n)]

    def go(self, client, **kwargs):
        return judge.run(client=client, **kwargs)


class SendTests(BatchBase):
    def test_sort_everything_sends_the_backlog_in_one_batch_at_half_price(self):
        rows = self.backlog()
        self.switch_on(sort_all=True)
        client = FakeBatches()
        result = self.go(client)
        self.assertEqual(client.sent, [])
        [(batch_id, requests)] = client.batches.items()
        self.assertEqual((result.sent, len(requests)), (len(rows), len(rows)))
        for request in requests:
            self.assertRegex(request["custom_id"], r"^[A-Za-z0-9_-]{1,64}$")
            # No server-side fallback on Batches.
            self.assertNotIn("fallbacks", request["params"])
            self.assertNotIn("betas", request["params"])
            self.assertEqual(request["params"]["model"], "claude-opus-5-5")
        asks = Ask.objects.all()
        self.assertEqual({ask.batch_id for ask in asks}, {batch_id})
        self.assertEqual({ask.outcome for ask in asks}, {Ask.Outcome.SENT})
        params = requests[0]["params"]
        first = asks.get(pk=int(requests[0]["custom_id"].removeprefix("ask-")))
        self.assertEqual(first.reserved_micros, judge.worst_case_micros(params, "claude-opus-5-5", batch=True))
        self.assertLess(judge.worst_case_micros(params, "claude-opus-5-5", batch=True),
                        judge.worst_case_micros(params, "claude-opus-5-5") / 2)
        self.assertIn(f"{len(rows)} sent in a batch at half price", ClaudeJudge.objects.get().last_run_note)
        # Waiting rows are not sent again while their batch is out.
        self.assertEqual(judge.waiting(), [])

    def test_a_short_list_is_asked_one_by_one(self):
        self.backlog(judge.BATCH_MIN - 1)
        self.switch_on(sort_all=True)
        client = FakeBatches()
        self.go(client)
        self.assertEqual(client.batches, {})
        self.assertEqual(len(client.sent), judge.BATCH_MIN - 1)

    def test_without_sort_everything_rows_are_asked_one_by_one(self):
        self.backlog()
        self.switch_on()
        client = FakeBatches()
        self.go(client)
        self.assertEqual(client.batches, {})
        self.assertEqual(len(client.sent), judge.BATCH_MIN + 1)

    def test_a_batch_never_takes_the_month_past_the_limit(self):
        self.backlog(40)
        self.switch_on(sort_all=True, monthly_budget_usd=Decimal("1.00"))
        client = FakeBatches()
        self.go(client)
        [requests] = client.batches.values()
        self.assertLess(len(requests), 40)
        self.assertLessEqual(judge.spent_this_month(), judge.monthly_limit_micros(ClaudeJudge.objects.get()))

    def test_one_batch_holds_at_most_its_budget(self):
        self.backlog(30)
        self.switch_on(sort_all=True)
        client = FakeBatches()
        one = judge.worst_from_characters(len(judge.SYSTEM_PROMPT) + 3000, "claude-opus-5-5", batch=True)
        with mock.patch.object(judge, "BATCH_BUDGET_USD", Decimal(one * 5) / 1_000_000):
            self.go(client)
        [requests] = client.batches.values()
        self.assertLessEqual(len(requests), 5)
        self.assertLessEqual(sum(Ask.objects.values_list("reserved_micros", flat=True)), one * 5)

    def test_a_batch_anthropic_refuses_to_take_costs_nothing(self):
        self.backlog()
        self.switch_on(sort_all=True)
        client = FakeBatches()
        import anthropic
        import httpx2

        response = httpx2.Response(529, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages/batches"))
        busy = anthropic.APIStatusError("overloaded", response=response, body={"type": "error", "error": {"type": "overloaded_error"}})
        client.messages.batches.create = mock.Mock(side_effect=busy)
        self.go(client)
        self.assertEqual(judge.spent_this_month(), 0)
        self.assertEqual(set(Ask.objects.values_list("outcome", flat=True)), {Ask.Outcome.ERROR})
        self.assertEqual(len(judge.waiting()), judge.BATCH_MIN + 1)


class CollectTests(BatchBase):
    def sent(self, client, n=judge.BATCH_MIN + 1):
        rows = self.backlog(n)
        self.switch_on(sort_all=True)
        self.go(client)
        return rows

    def test_a_batch_still_working_is_only_looked_at(self):
        client = FakeBatches()
        self.sent(client)
        state = ClaudeJudge.objects.get()
        self.assertEqual(judge.due(state, timezone.now()), "")
        later = timezone.now() + judge.timedelta(minutes=5)
        with mock.patch.object(judge.autopilot, "run") as autopilot_run:
            result = judge.run(client=client, now=later)
        autopilot_run.assert_not_called()
        self.assertEqual(result.note, "Waiting for Anthropic's batch")
        self.assertEqual(len(client.batches), 1)
        self.assertEqual(client.sent, [])
        self.assertEqual(ClaudeJudge.objects.get().last_run_at, state.last_run_at)

    def test_an_ended_batch_is_collected_at_half_price_and_acted_on(self):
        client = FakeBatches()
        rows = self.sent(client)
        client.status = "ended"
        result = judge.run(client=client, now=timezone.now() + judge.timedelta(minutes=5))
        self.assertEqual(result.collected, len(rows))
        asks = list(Ask.objects.all())
        self.assertEqual({ask.outcome for ask in asks}, {Ask.Outcome.ANSWERED})
        self.assertEqual({ask.reserved_micros for ask in asks}, {0})
        full = judge.cost_micros(reply("x"), "claude-opus-5-5")
        self.assertEqual({ask.cost_micros for ask in asks}, {-(-full // 2)})
        # The same rules act on them, free: every page was linked.
        self.assertEqual(CheckAnswer.objects.filter(kind=CheckAnswer.Kind.LINK).count(), len(rows))
        self.assertEqual(ShopProduct.objects.filter(pk__in=[r.pk for r in rows], status=ShopProduct.Status.LINKED).count(),
                         len(rows))
        self.assertEqual(judge.pending_batch_ids(), [])
        self.assertIn(f"{len(rows)} answers back from a batch", ClaudeJudge.objects.get().last_run_note)

    def test_questions_the_batch_did_not_answer_are_free_and_asked_again(self):
        def result_for(key, request):
            if key.endswith("0") or key.endswith("2"):
                return SimpleNamespace(type="expired")
            if key.endswith("4"):
                return SimpleNamespace(type="errored", error=SimpleNamespace(type="api_error"))
            return SimpleNamespace(type="succeeded", message=reply(key))

        client = FakeBatches(result_for=result_for)
        self.sent(client)
        client.status = "ended"
        judge.run(client=client, now=timezone.now() + judge.timedelta(minutes=5))
        failed = Ask.objects.filter(outcome=Ask.Outcome.ERROR)
        self.assertTrue(failed.exists())
        self.assertEqual({(ask.reserved_micros, ask.cost_micros) for ask in failed}, {(0, 0)})
        # Asked again: too few for a batch, so one by one in the same run.
        self.assertEqual({client.row_key(params) for _, params in client.sent}, {ask.row_key for ask in failed})

    def test_a_batch_anthropic_no_longer_holds_is_settled_and_not_a_fault(self):
        import anthropic
        import httpx2

        client = FakeBatches()
        self.sent(client)
        response = httpx2.Response(404, request=httpx2.Request("GET", "https://api.anthropic.com/v1/messages/batches/x"))
        client.messages.batches.retrieve = mock.Mock(side_effect=anthropic.NotFoundError(
            "gone", response=response, body={"type": "error", "error": {"type": "not_found_error"}}))
        judge.run(client=client, now=timezone.now() + judge.timedelta(minutes=5), force=True)
        # Its questions are settled as not sent, owing nothing, and sent again in a new batch.
        self.assertEqual(judge.pending_batch_ids(), ["msgbatch_2"])
        self.assertFalse(Ask.objects.filter(batch_id="msgbatch_1", outcome=Ask.Outcome.SENT).exists())
        self.assertEqual(set(Ask.objects.filter(batch_id="msgbatch_1").values_list("reserved_micros", flat=True)), {0})
        self.assertEqual(ClaudeJudge.objects.get().problem, "")

    def test_every_question_refused_is_the_sites_fault(self):
        client = FakeBatches(result_for=lambda key, request: SimpleNamespace(
            type="errored", error=SimpleNamespace(type="invalid_request")))
        self.sent(client)
        client.status = "ended"
        with mock.patch("catalogue.notify.owner", return_value=True):
            judge.run(client=client, now=timezone.now() + judge.timedelta(minutes=5))
        self.assertEqual(ClaudeJudge.objects.get().problem, "bug")


class BudgetTests(BatchBase):
    def test_the_ceiling_is_100_dollars_a_month(self):
        if "RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD" not in os.environ:
            with mock.patch.dict(os.environ, {}, clear=False):
                from importlib import reload

                from ripraptor import settings as site_settings

                self.assertEqual(reload(site_settings).RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD, 100)
        self.assertEqual(ClaudeJudge().monthly_budget_usd, Decimal("100.00"))
        self.assertTrue(settings.RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD)

    def test_the_page_limit_never_passes_the_ceiling(self):
        self.switch_on(monthly_budget_usd=Decimal("500.00"))
        self.assertEqual(judge.monthly_limit_micros(ClaudeJudge.objects.get()),
                         settings.RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD * 1_000_000)

    def test_the_caps_let_100_dollars_be_spent_on_a_backlog_within_days(self):
        typical = judge.cost_micros(reply("x", use=usage(input_tokens=1700, output_tokens=900)), "claude-opus-5-5")
        # One by one: an hourly run's rows, a day of them.
        self.assertGreaterEqual(judge.ROWS_PER_RUN * 24 * typical, 25_000_000)
        self.assertLessEqual(judge.ROWS_PER_RUN * typical, judge.RUN_BUDGET_USD * 1_000_000)
        # Batches: a few thousand rows within days at half price, one batch at a time.
        self.assertGreaterEqual(judge.BATCH_ROWS, 200)
        self.assertGreaterEqual(judge.OPEN_ROWS, 2000)

    def test_a_batch_answer_costs_half(self):
        message = reply("x")
        self.assertEqual(judge.cost_micros(message, "claude-opus-5-5", batch=True),
                         -(-judge.cost_micros(message, "claude-opus-5-5") // 2))

    def test_the_dry_run_says_it_would_send_a_batch(self):
        self.backlog()
        self.switch_on(sort_all=True)
        client = FakeBatches()
        result = judge.run(client=client, dry_run=True)
        self.assertEqual(result.note, f"Would send {judge.BATCH_MIN + 1} rows in a batch at half price")
        self.assertEqual((client.sent, client.batches), ([], {}))
        self.assertTrue(json.loads(result.lines[2]))
