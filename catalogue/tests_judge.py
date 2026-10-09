"""The Claude judge: Claude says whether two things are the same product, the site's own rules decide
whether that answer may act, every act has Undo, and the bill can never pass the owner's limit. No test
here touches the network: a fake client stands in for Anthropic."""

import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import autopilot, checks, judge, sanity
from .models import CheckAnswer, ClaudeAsk, ClaudeJudge, Listing, Product, ShopProduct, WorkerState
from .testing import make_game, make_listing, make_product, make_retailer, make_set

Ask = ClaudeAsk
BOX = "Surging Sparks Booster Box"
KEY = "sk-ant-api03-" + "x" * 40 + "AbCd"
URL = "https://api.anthropic.com/v1/messages"


def usage(input_tokens=700, output_tokens=900, write=0, read=0, iterations=None):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, cache_creation_input_tokens=write,
                           cache_read_input_tokens=read, iterations=iterations)


def reply(row, verdict="same", confidence="high", differences=(), reason="The names match word for word.",
          stop_reason="end_turn", model="claude-opus-5-5", use=None, text=None):
    body = text if text is not None else json.dumps({"row": row, "verdict": verdict, "confidence": confidence,
                                                     "differences": list(differences), "reason": reason})
    return SimpleNamespace(stop_reason=stop_reason, stop_details=None, model=model, id="msg_1",
                           content=[SimpleNamespace(type="text", text=body)], usage=use or usage())


def error(cls, status, kind="error", message="no"):
    response = httpx2.Response(status, request=httpx2.Request("POST", URL))
    return cls(message, response=response, body={"type": "error", "error": {"type": kind, "message": message}})


class FakeClient:
    """Answers each request through ``answer(row_key, params)``, which returns a reply or raises."""

    def __init__(self, answer=None):
        self.sent = []
        self.answer = answer or (lambda row_key, params: reply(row_key))
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create_beta))
        self.models = SimpleNamespace(retrieve=lambda model: SimpleNamespace(id=model))

    def row_key(self, params):
        content = params["messages"][0]["content"]
        return json.loads(content[len("<evidence>"):-len("</evidence>")])["row"]

    def create(self, **params):
        self.sent.append(("messages", params))
        return self.answer(self.row_key(params), params)

    def create_beta(self, **params):
        self.sent.append(("beta", params))
        return self.answer(self.row_key(params), params)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.key_file = Path(folder.name) / "claude-key"
        overrides = override_settings(RIPRAPTOR_CLAUDE=True, RIPRAPTOR_CLAUDE_KEY_FILE=str(self.key_file),
                                      RIPRAPTOR_CLAUDE_API_KEY="", RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD=25)
        overrides.enable()
        self.addCleanup(overrides.disable)
        # Nothing here may build a real client.
        patcher = mock.patch.object(judge, "make_client", side_effect=AssertionError("no real client in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = make_game()
        self.set = make_set(self.game, name="Surging Sparks", slug="surging-sparks", code="SSP")
        self.product = make_product(self.set, name=BOX, product_type="booster_box")
        self.shops = 0

    def switch_on(self, may_act=True, **fields):
        judge.write_key(KEY)
        ClaudeJudge.objects.update_or_create(pk=1, defaults={"enabled": True, "may_act": may_act, **fields})

    def shop(self, price, product=None, title=BOX, **kwargs):
        self.shops += 1
        retailer = make_retailer(f"Shop {self.shops}", delivery_cost=Decimal("0"))
        return make_listing(product or self.product, retailer, price=str(price), title=title, **kwargs)

    def found(self, title="Pokemon Surging Sparks Booster Display", price="100.00", confidence=80, product=None):
        self.shops += 1
        retailer = make_retailer(f"Shop {self.shops}", delivery_cost=Decimal("0"))
        product = product or self.product
        return ShopProduct.objects.create(
            retailer=retailer, url=f"{retailer.website}products/box", title=title, price=Decimal(price),
            availability=Listing.Availability.IN_STOCK, suggested=product, product=product, confidence=confidence,
            status=ShopProduct.Status.REVIEW, source=ShopProduct.Source.FINDER,
        )

    def ask(self, answer=None, **kwargs):
        client = FakeClient(answer)
        result = judge.run(client=client, **kwargs)
        return client, result

    def answers(self):
        return list(CheckAnswer.objects.order_by("pk"))


class DueTests(Base):
    def test_nothing_runs_without_a_key_or_while_off_or_paused(self):
        self.found()
        client, result = self.ask()
        self.assertEqual((client.sent, result.note), ([], "Claude needs a key."))
        judge.write_key(KEY)
        client, result = self.ask()
        self.assertEqual((client.sent, result.note), ([], "Claude is off."))
        ClaudeJudge.objects.update_or_create(pk=1, defaults={"enabled": True})
        WorkerState.objects.update_or_create(pk=1, defaults={"paused": True})
        client, result = self.ask()
        self.assertEqual((client.sent, result.note), ([], "Pause all is on."))

    def test_it_runs_hourly_or_when_asked(self):
        self.switch_on()
        self.shop(100)
        self.shop(104)
        self.found()
        now = timezone.now()
        client, _ = self.ask(now=now)
        self.assertEqual(len(client.sent), 1)
        self.found(title="Surging Sparks Booster Display Box")
        client, result = self.ask(now=now + timedelta(minutes=20))
        self.assertEqual((client.sent, result.note), ([], "Claude ran less than an hour ago."))
        ClaudeJudge.objects.filter(pk=1).update(asked_at=now + timedelta(minutes=25))
        client, _ = self.ask(now=now + timedelta(minutes=30))
        self.assertEqual(len(client.sent), 1)

    def test_a_dry_run_sends_nothing(self):
        self.switch_on()
        self.found()
        from io import StringIO

        out = StringIO()
        call_command("judge_checks", "--dry-run", stdout=out)
        self.assertIn("worst case $", out.getvalue())
        self.assertFalse(Ask.objects.exists())

    def test_the_hourly_tidy_and_the_page_never_load_the_anthropic_library(self):
        code = ("import os, sys, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ripraptor.settings'); "
                "django.setup(); import web.views, catalogue.management.commands.tidy_all, catalogue.judge, "
                "catalogue.autopilot; print('anthropic' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).parent.parent,
                             env={**os.environ, "DJANGO_SQLITE_PATH": str(self.key_file.with_name("x.sqlite3"))})
        self.assertEqual(out.stdout.strip().splitlines()[-1], "False", out.stderr)


class FoundTests(Base):
    def setUp(self):
        super().setUp()
        self.shop(100)
        self.shop(104)

    def test_a_sure_same_at_the_going_rate_links_and_undo_says_no(self):
        self.switch_on()
        row = self.found(price="101.00")
        self.ask()
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.LINKED)
        [answer] = self.answers()
        self.assertEqual(answer.kind, CheckAnswer.Kind.LINK)
        self.assertTrue(answer.why.startswith("Claude: The names match word for word."))
        listing = Listing.objects.get(product=self.product, retailer=row.retailer)
        self.assertEqual((answer.listing, listing.sanity), (listing, Listing.Sanity.OK))
        self.assertEqual(answer.ask.action, Ask.Action.ACTED)
        self.assertIn("not compared", autopilot.undo(answer))
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.IGNORED)
        self.assertEqual(Ask.objects.get().owner_answer, "different")

    def test_a_same_only_suggests_without_two_checks_agreeing(self):
        self.switch_on()
        dear = self.found(price="150.00")              # 1.47 times the other shops
        priceless = self.found(price="0")
        rows = [dear, priceless]
        self.ask()
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(row.status, ShopProduct.Status.REVIEW)
        self.assertEqual(self.answers(), [])
        self.assertEqual(set(Ask.objects.values_list("action", flat=True)), {Ask.Action.SUGGESTED})

    def test_no_link_when_no_other_shop_sells_it(self):
        self.switch_on()
        tin = make_product(self.set, name="Surging Sparks Tin", product_type="tin")
        row = self.found(title="Surging Sparks Tin Pikachu", product=tin)
        self.ask()
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.REVIEW)

    def test_trial_medium_low_and_fallback_answers_only_suggest(self):
        row = self.found(price="101.00")
        step = dict(input_tokens=700, output_tokens=900, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        fell_back = usage(iterations=[SimpleNamespace(type="message", model="claude-opus-5-5", **step),
                                      SimpleNamespace(type="fallback_message", model="claude-opus-4-8", **step)])
        cases = [
            ({"may_act": False}, lambda key, params: reply(key)),
            ({}, lambda key, params: reply(key, confidence="medium")),
            ({}, lambda key, params: reply(key, confidence="low")),
            ({}, lambda key, params: reply(key, model="claude-opus-4-8", use=fell_back)),
        ]
        for setup, answer in cases:
            Ask.objects.all().delete()
            self.switch_on(**({"may_act": True, "last_run_at": None} | setup))
            self.ask(answer)
            row.refresh_from_db()
            self.assertEqual(row.status, ShopProduct.Status.REVIEW)
            self.assertEqual(Ask.objects.get().action, Ask.Action.SUGGESTED)
        self.assertEqual(self.answers(), [])

    def test_a_sure_different_refuses_and_undo_links_it(self):
        self.switch_on()
        row = self.found(title="Surging Sparks Booster Box Japanese", price="101.00")
        self.ask(lambda key, params: reply(key, "different", differences=["language"], reason="The shop sells the Japanese box."))
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.IGNORED)
        [answer] = self.answers()
        self.assertEqual(answer.kind, CheckAnswer.Kind.REFUSE)
        self.assertIn("linked", autopilot.undo(answer))
        self.assertEqual(Ask.objects.get().owner_answer, "same")

    def test_a_difference_in_price_alone_never_acts(self):
        self.switch_on()
        row = self.found()
        self.ask(lambda key, params: reply(key, "different", differences=["price"]))
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.REVIEW)

    def test_a_row_rewritten_while_claude_was_asked_is_not_acted_on(self):
        self.switch_on()
        row = self.found(price="101.00")

        def answer(key, params):
            ShopProduct.objects.filter(pk=row.pk).update(title="Surging Sparks Booster Box (Japanese)")
            return reply(key)

        self.ask(answer)
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.REVIEW)
        self.assertEqual(Ask.objects.get().action, Ask.Action.STALE)

    def test_the_link_rolls_back_when_the_new_price_is_doubtful_beside_the_others(self):
        self.switch_on()
        row = self.found(price="101.00")
        with mock.patch.object(sanity, "judge_product", side_effect=lambda pk, **kw: Listing.objects.filter(
                product_id=pk, retailer=row.retailer).update(sanity=Listing.Sanity.DOUBTFUL)):
            self.ask()
        row.refresh_from_db()
        self.assertEqual(row.status, ShopProduct.Status.REVIEW)
        self.assertFalse(Listing.objects.filter(retailer=row.retailer).exists())


class OfferAndPairTests(Base):
    def test_a_sure_different_cheapest_price_is_hidden_and_a_same_is_never_counted(self):
        self.switch_on()
        wrong = self.shop(30, title="Surging Sparks Booster Box Damaged Packaging Single Pack")
        right = self.shop(100)
        sanity.judge_product(self.product.pk)
        self.ask(lambda key, params: reply(key, "different", differences=["kind"]))
        self.assertFalse(Listing.objects.get(pk=wrong.pk).is_active)
        sanity.judge_product(self.product.pk)
        self.assertIsNone(Listing.objects.get(pk=right.pk).trusted_price)
        [answer] = self.answers()
        autopilot.undo(answer)
        self.assertTrue(Listing.objects.get(pk=wrong.pk).is_active)

    def test_a_doubtful_price_claude_calls_same_is_only_a_suggestion(self):
        self.switch_on()
        cheap = self.shop(30)
        self.shop(100)
        sanity.judge_product(self.product.pk)
        self.ask()
        cheap.refresh_from_db()
        self.assertEqual((cheap.is_active, cheap.trusted_price, cheap.sanity), (True, None, Listing.Sanity.DOUBTFUL))
        self.assertEqual(Ask.objects.get().action, Ask.Action.SUGGESTED)

    def test_a_wrong_match_hides_one_price_only_when_the_other_is_surely_the_product(self):
        self.switch_on()
        best = self.shop(20, title="Surging Sparks Booster Box Opened")
        second = self.shop(100)
        self.assertEqual(len(checks.wrong_matches()), 1)
        verdicts = {f"offer:{best.pk}": "different", f"offer:{second.pk}": "unsure"}
        self.ask(lambda key, params: reply(key, verdicts[key], differences=["condition"] if verdicts[key] == "different" else []))
        self.assertTrue(Listing.objects.get(pk=best.pk).is_active)
        Ask.objects.all().delete()
        ClaudeJudge.objects.filter(pk=1).update(last_run_at=None)
        verdicts[f"offer:{second.pk}"] = "same"
        self.ask(lambda key, params: reply(key, verdicts[key], differences=["condition"] if verdicts[key] == "different" else []))
        self.assertFalse(Listing.objects.get(pk=best.pk).is_active)
        self.assertTrue(Listing.objects.get(pk=second.pk).is_active)

    def pair(self):
        keep = make_product(self.set, name="Surging Sparks Elite Trainer Box")
        other = make_product(self.set, name="Scarlet & Violet Surging Sparks Elite Trainer Box")
        self.shop(50, product=keep, title=keep.name)
        self.shop(51, product=other, title=other.name)
        return keep, other

    def test_pairs_never_merge_and_a_sure_different_keeps_them_apart(self):
        self.switch_on()
        keep, other = self.pair()
        self.ask()
        self.assertTrue(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(len(checks.duplicates()), 1)
        Ask.objects.all().delete()
        ClaudeJudge.objects.filter(pk=1).update(last_run_at=None)
        self.ask(lambda key, params: reply(key, "different", differences=["edition"]))
        self.assertEqual(checks.duplicates(), [])
        [answer] = self.answers()
        self.assertEqual((answer.kind, answer.by_owner), (CheckAnswer.Kind.APART, False))
        autopilot.undo(answer)
        self.assertEqual(len(checks.duplicates()), 1)

    def test_the_owner_merges_the_pairs_claude_is_sure_of_and_can_undo_each(self):
        self.switch_on(may_act=False)
        keep, other = self.pair()
        self.ask()
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        page = self.client.get(reverse("checks"))
        self.assertContains(page, "Merge the 1 pair Claude is sure are the same")
        self.assertContains(page, "Claude, ")
        response = self.client.post(reverse("checks"), {"action": "merge_sure", "pair": f"{keep.pk}:{other.pk}"}, follow=True)
        self.assertContains(response, "Merged 1 pair.")
        other.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertEqual(Listing.objects.get(title=other.name).product, keep)
        [answer] = self.answers()
        self.assertEqual((answer.kind, Ask.objects.get().owner_answer), (CheckAnswer.Kind.MERGE, "same"))
        response = self.client.post(reverse("checks"), {"action": "undo", "answer": answer.pk}, follow=True)
        self.assertContains(response, "is its own product again")
        other.refresh_from_db()
        self.assertTrue(other.is_active)
        self.assertEqual(Listing.objects.get(title=other.name).product, other)

    def test_a_merged_away_product_survives_the_hourly_tidy(self):
        from .management.commands.merge_duplicates import merge_undoable

        keep, other = self.pair()
        merge_undoable(keep, [other])
        call_command("tidy_catalogue", stdout=open(os.devnull, "w"))
        self.assertTrue(Product.objects.filter(pk=other.pk).exists())


class AskingTests(Base):
    def setUp(self):
        super().setUp()
        self.shop(100)
        self.shop(104)

    def test_an_unchanged_row_is_never_asked_twice_and_a_changed_one_at_most_three_times(self):
        self.switch_on(may_act=False)
        row = self.found()
        for n in range(5):
            ClaudeJudge.objects.filter(pk=1).update(last_run_at=None)
            self.ask()
            if n < 4:
                ShopProduct.objects.filter(pk=row.pk).update(title=f"Surging Sparks Booster Display v{n}")
        self.assertEqual(Ask.objects.count(), judge.ASK_LIMIT)

    def test_the_request_carries_effort_and_a_schema_and_nothing_the_model_rejects(self):
        self.switch_on(may_act=False)
        self.found()
        self.found(title="Surging Sparks Display")
        client, _ = self.ask()
        self.assertEqual(len(client.sent), 2)
        systems = set()
        for path, params in client.sent:
            self.assertEqual(path, "beta")
            self.assertEqual(params["fallbacks"], "default")
            self.assertEqual(params["betas"], [judge.FALLBACK_BETA])
            self.assertEqual(params["output_config"]["effort"], "medium")
            self.assertEqual(params["output_config"]["format"]["schema"], judge.SCHEMA)
            for banned in ("thinking", "temperature", "top_p", "top_k", "tool_choice", "tools"):
                self.assertNotIn(banned, params)
            self.assertEqual(params["system"][0]["cache_control"], {"type": "ephemeral"})
            systems.add(json.dumps(params["system"], sort_keys=True))
        self.assertEqual(len(systems), 1)

    def test_haiku_is_never_sent_fallbacks_and_one_row_is_not_cached(self):
        self.switch_on(may_act=False, model="claude-haiku-5-5")
        self.found()
        client, _ = self.ask(lambda key, params: reply(key, model="claude-haiku-5-5"))
        [(path, params)] = client.sent
        self.assertEqual(path, "messages")
        self.assertNotIn("fallbacks", params)
        self.assertNotIn("cache_control", params["system"][0])

    def test_refusals_cut_offs_and_bad_answers_never_act(self):
        self.switch_on()
        rows = [self.found(price="101.00", title=f"Surging Sparks Booster Display {n}") for n in range(4)]
        keys = [f"found:{row.pk}" for row in rows]
        replies = {
            keys[0]: lambda key: reply(key, stop_reason="refusal"),
            keys[1]: lambda key: reply(key, stop_reason="max_tokens"),
            keys[2]: lambda key: reply(key, text="not json"),
            keys[3]: lambda key: reply("found:999999"),
        }
        self.ask(lambda key, params: replies[key](key))
        outcomes = dict(Ask.objects.values_list("row_key", "outcome"))
        self.assertEqual([outcomes[k] for k in keys],
                         [Ask.Outcome.REFUSED, Ask.Outcome.CUT_OFF, Ask.Outcome.INVALID, Ask.Outcome.INVALID])
        self.assertEqual(self.answers(), [])

    def test_the_evidence_holds_only_what_the_row_needs(self):
        self.switch_on(may_act=False)
        from .models import StockAlert

        StockAlert.objects.create(product=self.product, email="someone@example.com")
        self.found()
        client, _ = self.ask()
        evidence = Ask.objects.get().evidence
        self.assertEqual(set(evidence), {
            "row", "kind", "ours", "shop", "shop_title", "price", "stock", "url_path", "finder_score",
            "title_reads_as", "set_codes_in_title", "price_band", "shop_titles_elsewhere",
        })
        self.assertNotIn("someone@example.com", json.dumps(client.sent[0][1]))
        self.assertEqual(evidence["price_band"], "close to other shops")


class CostTests(Base):
    def test_each_request_is_priced_exactly_at_its_models_rates(self):
        message = reply("found:1", use=usage(input_tokens=1000, output_tokens=2000, write=600, read=0))
        self.assertEqual(judge.cost_micros(message, "claude-opus-5-5"), 4 * 1000 + 5 * 600 + 20 * 2000)
        steps = [SimpleNamespace(type="message", model="claude-opus-5-5", input_tokens=100, output_tokens=0,
                                 cache_creation_input_tokens=0, cache_read_input_tokens=0),
                 SimpleNamespace(type="fallback_message", model="claude-opus-4-8", input_tokens=100, output_tokens=100,
                                 cache_creation_input_tokens=0, cache_read_input_tokens=0)]
        self.assertEqual(judge.cost_micros(reply("x", use=usage(iterations=steps)), "claude-opus-5-5"), 400 + 500 + 2500)
        self.assertEqual(judge.rates("claude-unknown-9"), judge.DEAREST)
        for model in [*judge.MODELS, "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5"]:
            self.assertIn(model, judge.PRICES)

    def test_nothing_is_sent_that_could_pass_the_monthly_limit_and_the_owner_hears_once(self):
        self.switch_on(monthly_budget_usd=Decimal("0.10"))
        self.shop(100)
        self.shop(104)
        self.found()
        with mock.patch("catalogue.notify.owner", return_value=True) as told:
            client, result = self.ask()
        self.assertEqual(client.sent, [])
        self.assertEqual(result.note, "Reached the monthly limit.")
        self.assertEqual(told.call_args[0][0], judge.AT_LIMIT)

    def test_the_server_ceiling_holds_whatever_the_page_says(self):
        with override_settings(RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD=5):
            state = ClaudeJudge(monthly_budget_usd=Decimal("20"))
            self.assertEqual(judge.monthly_limit_micros(state), 5_000_000)

    def test_a_timeout_keeps_its_reservation_and_ends_the_run_quietly(self):
        self.switch_on(may_act=False)
        self.found()

        def answer(key, params):
            raise anthropic.APITimeoutError(request=httpx2.Request("POST", URL))

        with mock.patch("catalogue.notify.owner") as told:
            self.ask(answer)
        ask = Ask.objects.get()
        self.assertEqual((ask.outcome, ask.cost_micros > 0 or ask.reserved_micros > 0), (Ask.Outcome.SENT, True))
        self.assertGreater(judge.spent_this_month(), 0)
        self.assertEqual(ClaudeJudge.objects.get().problem, "busy")
        told.assert_not_called()

    def test_a_bad_key_or_no_credit_stops_claude_and_the_owner_hears(self):
        for err, problem in ((error(anthropic.AuthenticationError, 401, "authentication_error"), "key"),
                             (error(anthropic.APIStatusError, 402, "billing_error", "Your credit balance is too low"), "credit")):
            Ask.objects.all().delete()
            self.switch_on(may_act=False, last_run_at=None, problem="", problem_at=None)
            self.found(title=f"Surging Sparks Booster Display {problem}")

            def answer(key, params, err=err):
                raise err

            with mock.patch("catalogue.notify.owner", return_value=True) as told:
                self.ask(answer)
            state = ClaudeJudge.objects.get()
            self.assertEqual(state.problem, problem)
            self.assertEqual(told.call_args[0][0], judge.STOPPED)
            self.assertEqual(judge.due(state, timezone.now() + timedelta(hours=2)), "Claude has stopped.")
            self.assertEqual(Ask.objects.get().reserved_micros, 0)


class KeyTests(Base):
    def test_a_good_key_is_saved_where_only_the_site_can_read_it(self):
        saved, message = judge.save_key(KEY, client_factory=lambda key, timeout: FakeClient())
        self.assertTrue(saved, message)
        self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode), 0o600)
        self.assertEqual((judge.key(), ClaudeJudge.objects.get().key_hint), (KEY, "AbCd"))
        judge.forget_key()
        self.assertEqual((judge.key(), self.key_file.exists()), ("", False))

    def test_a_bad_or_refused_key_is_not_saved(self):
        self.assertFalse(judge.save_key("hello", client_factory=lambda key, timeout: FakeClient())[0])
        refusing = FakeClient()

        def refuse(model):
            raise error(anthropic.AuthenticationError, 401, "authentication_error")

        refusing.models = SimpleNamespace(retrieve=refuse)
        self.assertFalse(judge.save_key(KEY, client_factory=lambda key, timeout: refusing)[0])
        self.assertFalse(self.key_file.exists())

    def test_the_server_settings_key_wins(self):
        judge.write_key(KEY)
        with override_settings(RIPRAPTOR_CLAUDE_API_KEY="sk-ant-from-env"):
            self.assertEqual(judge.key(), "sk-ant-from-env")


class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        self.url = reverse("checks")

    def test_the_key_is_saved_from_the_page_and_never_shown_again(self):
        with mock.patch.object(judge, "make_client", side_effect=lambda key, timeout=10: FakeClient()):
            response = self.client.post(self.url, {"action": "claude_key_save", "claude_key": KEY}, follow=True)
        self.assertContains(response, "Key saved. It ends in AbCd")
        self.assertNotContains(response, KEY)
        self.assertContains(response, "Saved key ends in AbCd")
        self.assertEqual(judge.key(), KEY)

    def test_the_buttons_switch_claude_on_into_trial_and_ask_it_to_look(self):
        judge.write_key(KEY)
        response = self.client.post(self.url, {"action": "claude_on"}, follow=True)
        self.assertContains(response, "Claude is on, in trial. It suggests and does not act.")
        response = self.client.post(self.url, {"action": "claude_now"}, follow=True)
        self.assertContains(response, "within 5 minutes")
        self.assertIsNotNone(ClaudeJudge.objects.get().asked_at)
        response = self.client.post(self.url, {"action": "claude_act_on"}, follow=True)
        self.assertContains(response, "Claude is on. It acts only when it is sure")
        response = self.client.post(self.url, {"action": "claude_settings", "model": "claude-haiku-5-5",
                                               "effort": "low", "budget": "99"}, follow=True)
        self.assertContains(response, "a monthly limit from 0 to 25 dollars")
        self.client.post(self.url, {"action": "claude_settings", "model": "claude-haiku-5-5", "effort": "low", "budget": "4"})
        state = ClaudeJudge.objects.get()
        self.assertEqual((state.model, state.effort, state.monthly_budget_usd), ("claude-haiku-5-5", "low", Decimal("4.00")))

    def test_the_claude_box_wording_is_plain_and_the_page_never_writes_on_load(self):
        page = self.client.get(self.url).content.decode()
        self.assertIn("Claude needs a key.", page)
        self.assertFalse(ClaudeJudge.objects.exists())
        start = page.index('<section class="claude">')
        box = page[start:page.index("</section>", start)]
        self.assertNotIn(chr(0x2014), box)
        self.assertNotIn("!", box.replace("<!--", ""))

    def test_claudes_answers_cost_the_same_queries_however_many(self):
        self.switch_on(may_act=False)
        self.shop(100)
        self.shop(104)
        rows = [self.found(title=f"Surging Sparks Booster Display {n}") for n in range(6)]
        Ask.objects.create(kind="found", row_key=f"found:{rows[0].pk}", fingerprint="x", model_asked="m", effort="low",
                           outcome=Ask.Outcome.ANSWERED, verdict="same", confidence="high", reason="Match.")
        with CaptureQueriesContext(connection) as one:
            self.client.get(self.url)
        for row in rows[1:]:
            Ask.objects.create(kind="found", row_key=f"found:{row.pk}", fingerprint="x", model_asked="m", effort="low",
                               outcome=Ask.Outcome.ANSWERED, verdict="same", confidence="high", reason="Match.")
        with CaptureQueriesContext(connection) as six:
            page = self.client.get(self.url)
        self.assertContains(page, "Claude, ", count=6)
        self.assertEqual(len(one), len(six))

    def test_the_owners_taps_are_counted_as_agreement(self):
        self.switch_on(may_act=False)
        self.shop(100)
        self.shop(104)
        row = self.found()
        self.ask()
        self.client.post(self.url, {"action": "link_found", "row": row.pk, "price": "100.00"})
        self.assertEqual(Ask.objects.get().owner_answer, "same")
        self.assertContains(self.client.get(self.url), "Agreed with you 1 of 1 time.")
