"""Claude's jobs and spending (the Claude page, web/views.py claude_page; judge.JOBS, catalogue.ClaudeJob). The owner,
3 Nov 2026, with Sort everything through $67 in three days: "control and set what it does in a settings menu"."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import cardmarket_match, judge, new_pages, rrp_finder, set_retail
from .models import ClaudeAsk, ClaudeJob, ClaudeJudge
from .tests_judge import KEY, Base, FakeClient

Kind = ClaudeAsk.Kind
URL = "/admin/claude/"


def ask(kind, cost=0, held=0, days_ago=0, outcome=ClaudeAsk.Outcome.ANSWERED):
    return ClaudeAsk.objects.create(
        kind=kind, row_key=f"{kind}:{ClaudeAsk.objects.count()}", fingerprint="f", model_asked="claude-opus-5-5",
        effort="medium", cost_micros=cost, reserved_micros=held, outcome=outcome,
        asked_at=timezone.now() - timedelta(days=days_ago))


class JobTests(Base):
    def test_every_kind_of_question_belongs_to_one_job_and_the_page_names_them_the_same(self):
        from web.views import CLAUDE_JOBS

        self.assertEqual(set(judge.JOB_OF_KIND), set(Kind.values))
        self.assertEqual(list(CLAUDE_JOBS), list(judge.JOBS))
        for name, (_link, title, _does) in CLAUDE_JOBS.items():
            self.assertEqual(title, judge.JOBS[name][0])

    def test_a_job_is_on_with_no_limit_of_its_own_until_set_and_reading_never_writes(self):
        jobs = judge.job_settings()
        self.assertTrue(all(job.on and job.monthly_budget_usd is None for job in jobs.values()))
        self.assertFalse(ClaudeJob.objects.exists())
        self.assertEqual(judge.job_room(Kind.PAIR, 5_000_000), "")

    def test_switched_off_or_at_its_own_limit_says_so_and_the_other_jobs_are_untouched(self):
        ClaudeJob.objects.create(name="pairs", on=False)
        ClaudeJob.objects.create(name="market", monthly_budget_usd=Decimal("2.00"))
        ask(Kind.MARKET, cost=1_500_000)
        ask(Kind.MARKET, held=400_000, outcome=ClaudeAsk.Outcome.SENT)   # a question out counts too
        ask(Kind.MARKET, cost=9_000_000, days_ago=40)                     # last month does not
        self.assertEqual(judge.spent_by_job()["market"], 1_900_000)
        self.assertEqual(judge.job_room(Kind.PAIR, 0), "Possible duplicates is switched off on the Claude page")
        self.assertEqual(judge.job_room(Kind.MARKET, 50_000), "")
        self.assertEqual(judge.job_room(Kind.MARKET, 200_000),
                         "Cardmarket matches reached its own monthly limit of $2.00, set on the Claude page")
        self.assertEqual(judge.job_room(Kind.FOUND, 50_000_000), "")   # the shared limit is the run's own check

    def test_the_judge_never_asks_about_a_job_switched_off_and_passes_over_one_at_its_limit(self):
        self.switch_on()
        self.found()
        ClaudeJob.objects.create(name="found", on=False)
        client, result = self.ask()
        self.assertEqual(client.sent, [])
        self.assertEqual(result.note, "1 rows wait for jobs switched off on the Claude page")
        self.assertFalse(ClaudeAsk.objects.exists())
        ClaudeJob.objects.filter(name="found").update(on=True, monthly_budget_usd=Decimal("1.00"))
        ask(Kind.FOUND, cost=990_000)
        client, result = self.ask(force=True)
        self.assertEqual(client.sent, [])
        self.assertEqual(result.note, "Found at another shop reached its own monthly limit of $1.00, set on the Claude page")
        ClaudeJob.objects.filter(name="found").update(monthly_budget_usd=None)
        client, result = self.ask(force=True)
        self.assertEqual(len(client.sent), 1)

    def test_a_batch_leaves_out_the_rows_of_a_job_at_its_limit(self):
        self.switch_on(sort_all=True)
        for _ in range(judge.BATCH_MIN):
            self.found()
        ClaudeJob.objects.create(name="found", monthly_budget_usd=Decimal("0.05"))

        class Batches:
            def __init__(self):
                self.requests = None

            def create(self, requests):
                self.requests = requests
                return type("Batch", (), {"id": "msgbatch_1"})()

        client = FakeClient()
        client.messages.batches = Batches()
        result = judge.run(client=client, force=True)
        self.assertEqual(result.sent, 0)
        self.assertIn("reached its own monthly limit of $0.05", result.note)
        self.assertFalse(ClaudeAsk.objects.exists())

    def test_the_other_jobs_stop_before_asking_when_switched_off_or_at_their_limit(self):
        judge.write_key(KEY)
        ClaudeJudge.objects.update_or_create(pk=1, defaults={"enabled": True, "may_act": True})
        for name in ("market", "pages", "rrps", "retail"):
            ClaudeJob.objects.create(name=name, on=False)
        self.assertIn("Cardmarket matches is switched off on the Claude page", cardmarket_match.run().note)
        self.assertEqual(new_pages.run().note, "New pages is switched off on the Claude page, so new pages go up unchecked")
        self.assertEqual(rrp_finder.run().note, "RRPs is switched off on the Claude page")
        self.assertEqual(set_retail.run()[3], "Sets sold only at events is switched off on the Claude page")
        self.assertFalse(ClaudeAsk.objects.exists())
        ClaudeJob.objects.update(on=True, monthly_budget_usd=Decimal("0.50"))
        ask(Kind.RRP, cost=510_000)   # past its own limit before anything is asked
        self.assertIn("RRPs reached its own monthly limit of $0.50", rrp_finder.run().note)


class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_user("owner", password="pw", is_staff=True))

    def test_the_page_is_for_staff_and_linked_from_admin_home_and_the_claude_box(self):
        self.assertEqual(reverse("claude"), URL)
        self.assertContains(self.client.get(reverse("admin:index")), f'href="{URL}">Claude:')
        judge.write_key(KEY)
        self.assertContains(self.client.get(reverse("checks")), f'<a href="{URL}">Jobs and spending</a>')
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)

    def test_it_shows_what_each_job_asked_and_spent_this_month_and_today(self):
        self.switch_on(sort_all=True)
        ask(Kind.PAIR, cost=3_000_000)
        ask(Kind.PAIR, cost=1_000_000, days_ago=2)
        ask(Kind.PAIR, held=250_000, outcome=ClaudeAsk.Outcome.SENT)
        ask(Kind.RRP, cost=120_000)
        ClaudeJob.objects.create(name="rrps", monthly_budget_usd=Decimal("0.10"))
        html = self.client.get(URL).content.decode()
        self.assertIn("Claude is on and sorting everything.", html)
        self.assertIn("<b>$4.37</b> of $25.00 across every job, 4 questions", html)
        self.assertIn("Today: $3.37, 3 questions", html)
        self.assertIn("Sort everything is on:", html)
        pairs = html[html.index('data-job="pairs"'):html.index('data-job="found"')]
        self.assertIn("$4.25, 3 asked", pairs)
        self.assertIn("$3.25, 2 asked", pairs)
        self.assertIn('name="on_pairs" checked', pairs)
        rrps = html[html.index('data-job="rrps"'):]
        self.assertIn("$0.12, 1 asked <span class=\"at\">(at its limit)</span>", rrps)
        self.assertIn('value="0.10"', rrps)
        for _name, (title, _kinds) in judge.JOBS.items():
            self.assertIn(f"<b>{title}</b>", html)

    def test_saving_switches_and_limits_and_a_bad_limit_saves_nothing(self):
        self.switch_on()
        data = {"action": "claude_jobs", **{f"on_{name}": "on" for name in judge.JOBS}}
        del data["on_pairs"]
        data["cap_market"] = "2.50"
        response = self.client.post(URL, data, follow=True)
        self.assertRedirects(response, URL)
        self.assertContains(response, "Claude&#x27;s jobs are saved. Switched off: Possible duplicates.")
        jobs = judge.job_settings()
        self.assertFalse(jobs["pairs"].on)
        self.assertEqual(jobs["market"].monthly_budget_usd, Decimal("2.50"))
        self.assertTrue(jobs["found"].on and jobs["found"].monthly_budget_usd is None)
        response = self.client.post(URL, {**data, "cap_market": "99"}, follow=True)
        self.assertContains(response, "Cardmarket matches: give a monthly limit from 0 to 25 dollars, or leave it empty. "
                                      "Nothing was saved.")
        self.assertEqual(judge.job_settings()["market"].monthly_budget_usd, Decimal("2.50"))
        response = self.client.post(URL, {**data, "cap_market": "lots"}, follow=True)
        self.assertContains(response, "Nothing was saved.")

    def test_sort_everything_and_claude_can_be_stopped_from_here(self):
        self.switch_on(sort_all=True)
        response = self.client.post(URL, {"action": "claude_sort_off"}, follow=True)
        self.assertRedirects(response, URL)
        self.assertContains(response, "Claude now looks at 25 rows an hour.")
        self.assertFalse(ClaudeJudge.objects.get().sort_all)
        response = self.client.post(URL, {"action": "claude_off"}, follow=True)
        self.assertContains(response, "Claude is off. Nothing is sent and nothing is spent.")
        self.assertFalse(ClaudeJudge.objects.get().enabled)
        self.assertContains(self.client.get(URL), 'value="Switch Claude on"')

    def test_house_style_and_bounded_queries(self):
        from web.tests import CopyStyleTests
        from web.tests_dashboard import visible

        self.switch_on()
        with CaptureQueriesContext(connection) as few:
            html = self.client.get(URL).content.decode()
        text = visible(html)
        self.assertNotIn("!", text)
        self.assertNotIn("—", html)
        for phrase in CopyStyleTests.BANNED:
            self.assertNotIn(phrase, html.lower())
        for n in range(40):
            ask(Kind.PAIR if n % 2 else Kind.OFFER, cost=10_000 * n)
        for name in judge.JOBS:
            ClaudeJob.objects.create(name=name, monthly_budget_usd=Decimal("5"))
        with CaptureQueriesContext(connection) as many:
            self.client.get(URL)
        self.assertEqual(len(few), len(many))


class ReportTests(Base):
    def test_the_text_report_lists_each_jobs_spend(self):
        from content import service as copy
        from web import report

        self.switch_on()
        ask(Kind.PAIR, cost=2_000_000)
        ClaudeJob.objects.create(name="pairs", monthly_budget_usd=Decimal("10"))
        ClaudeJob.objects.create(name="rrps", on=False)
        text = report.build(timezone.now(), copy.load_all())
        claude = text[text.index("\nCLAUDE\n"):]
        self.assertIn("By job, this month", claude)
        self.assertRegex(claude, r"Possible duplicates\s+1\s+\$2\.00\s+yes\s+\$10\.00\n")
        self.assertRegex(claude, r"RRPs\s+0\s+\$0\.00\s+no\s+-\n")
