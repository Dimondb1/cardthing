"""Claude on whether UK shops sell a set: asked with every announced set (set_judge), and once per release date
for sets close to their day that no shop lists (set_retail). A set is marked as not sold in UK shops only
on a sure answer that a cited page naming the set backs, never on unsure. No test touches the network."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from . import autopilot, judge, set_judge, set_retail
from .models import CheckAnswer, ClaudeAsk, ClaudeJudge, ProductSet
from .testing import make_game, make_listing, make_product, make_retailer, make_set
from .tests_judge import Base
from .tests_set_judge import NEWS, SAYS, SetBase, searched

Ask = ClaudeAsk
WIKI = "https://mtg.wiki/page/Mystery_Booster_Commander_Edition"
MBC_SAYS = ("Mystery Booster Commander Edition is sold only at MagicCon and direct from Wizards as a Festival in a "
            "Box and a Secret Lair presale.")
ONLY_AT_EVENTS = f"Phantasmal Flames is sold only at the World Championships, never in shops. {SAYS}"


class SetJudgeSoldTests(SetBase):
    def test_a_sure_cited_events_only_answer_marks_the_set_it_adds(self):
        self.switch_on()
        self.announce()
        self.ask(self.answering(sold="not_shops", cited=((NEWS, "Phantasmal Flames | Pokemon.com", ONLY_AT_EVENTS),)))
        added = ProductSet.objects.get(name="Phantasmal Flames")
        self.assertTrue(added.not_in_shops)
        self.assertEqual(added.not_in_shops_note, NEWS)
        self.assertIn("sold only at events or direct", CheckAnswer.objects.get().what)
        self.assertEqual(Ask.objects.get().set_answer["sold"], "not_shops")
        self.assertTrue(Ask.objects.get().set_answer["sold_backed"])

    def test_shops_or_a_set_it_cannot_settle_marks_nothing(self):
        self.switch_on()
        self.announce()
        self.ask(self.answering(sold="unsure"))
        self.assertFalse(ProductSet.objects.get(name="Phantasmal Flames").not_in_shops)

    def test_an_unsure_answer_never_marks_a_set_even_saying_events_only(self):
        self.switch_on()
        there = ProductSet.objects.create(game=self.game, name="Phantasmal Flames", slug="phantasmal-flames", code="")
        self.announce(name="Pokemon TCG Mega Evolution Phantasmal Flames")
        for _ in range(2):   # the first look and the careful second look
            self.ask(self.answering(decision="unsure", confidence="low", sold="not_shops",
                                    cited=((NEWS, "Phantasmal Flames | Pokemon.com", ONLY_AT_EVENTS),)))
        there.refresh_from_db()
        self.assertFalse(there.not_in_shops)
        self.assertTrue(self.held().exists())

    def test_events_only_with_no_quote_naming_the_set_marks_nothing(self):
        self.switch_on()
        there = ProductSet.objects.create(game=self.game, name="Phantasmal Flames", slug="phantasmal-flames", code="")
        self.announce(name="Pokemon TCG Mega Evolution Phantasmal Flames")
        self.ask(self.answering(sold="not_shops", same_as=there.pk, decision="existing",
                                cited=((NEWS, "Pokemon news", "Some sets are sold only at events."),),
                                results=((NEWS, "Pokemon news"),)))
        there.refresh_from_db()
        self.assertFalse(there.not_in_shops)

    def test_a_fairly_sure_answer_files_but_does_not_mark(self):
        self.switch_on()
        there = ProductSet.objects.create(game=self.game, name="Phantasmal Flames", slug="phantasmal-flames", code="")
        self.announce(name="Pokemon TCG Mega Evolution Phantasmal Flames")
        for _ in range(2):
            self.ask(self.answering(decision="existing", same_as=there.pk, confidence="medium", sold="not_shops",
                                    cited=((NEWS, "Phantasmal Flames | Pokemon.com", ONLY_AT_EVENTS),)))
        there.refresh_from_db()
        self.assertEqual(CheckAnswer.objects.get().kind, CheckAnswer.Kind.SAME_SET)
        self.assertFalse(there.not_in_shops)

    def test_undo_of_a_filing_clears_the_mark_claude_made(self):
        self.switch_on()
        there = ProductSet.objects.create(game=self.game, name="Phantasmal Flames", slug="phantasmal-flames", code="")
        self.announce(name="Pokemon TCG Mega Evolution Phantasmal Flames")
        self.ask(self.answering(sold="not_shops", cited=((NEWS, "Phantasmal Flames | Pokemon.com", ONLY_AT_EVENTS),)))
        there.refresh_from_db()
        self.assertTrue(there.not_in_shops)
        autopilot.undo(CheckAnswer.objects.get())
        there.refresh_from_db()
        self.assertEqual((there.not_in_shops, there.not_in_shops_note), (False, ""))

    def test_a_set_a_uk_shop_lists_is_never_marked(self):
        self.switch_on()
        there = ProductSet.objects.create(game=self.game, name="Phantasmal Flames", slug="phantasmal-flames", code="ME02")
        make_listing(make_product(there, name="Phantasmal Flames Booster Box", slug="pf-box"),
                     make_retailer("Harbour Games"))
        self.announce(name="Pokemon TCG Mega Evolution Phantasmal Flames")
        self.ask(self.answering(sold="not_shops", same_as=there.pk, decision="existing",
                                cited=((NEWS, "Phantasmal Flames | Pokemon.com", ONLY_AT_EVENTS),)))
        there.refresh_from_db()
        self.assertFalse(there.not_in_shops)
        answer = CheckAnswer.objects.get()
        self.assertEqual(answer.kind, CheckAnswer.Kind.SAME_SET)
        self.assertNotIn("sold only at events or direct", answer.what)
        self.assertNotIn("not_in_shops", answer.undo_note)

    def test_a_marketplace_listing_alone_does_not_stop_the_mark(self):
        self.switch_on()
        there = ProductSet.objects.create(game=self.game, name="Phantasmal Flames", slug="phantasmal-flames", code="ME02")
        make_listing(make_product(there, name="Phantasmal Flames Booster Box", slug="pf-box"),
                     make_retailer("eBay seller", source_type="ebay"))
        self.announce(name="Pokemon TCG Mega Evolution Phantasmal Flames")
        self.ask(self.answering(sold="not_shops", same_as=there.pk, decision="existing",
                                cited=((NEWS, "Phantasmal Flames | Pokemon.com", ONLY_AT_EVENTS),)))
        there.refresh_from_db()
        self.assertTrue(there.not_in_shops)

    def test_a_quote_that_gives_only_a_date_does_not_back_events_only(self):
        pages = {NEWS: {"url": NEWS, "title": "Phantasmal Flames | Pokemon.com", "cited": [SAYS]}}
        self.assertFalse(set_judge.sold_backed("not_shops", pages, "Phantasmal Flames", "ME02"))
        pages[NEWS]["cited"].append("Phantasmal Flames is sold only at the World Championships.")
        self.assertTrue(set_judge.sold_backed("not_shops", pages, "Phantasmal Flames", "ME02"))
        self.assertTrue(set_judge.says_not_shops(MBC_SAYS))

    def test_a_date_only_quote_marks_nothing(self):
        self.switch_on()
        self.announce()
        self.ask(self.answering(sold="not_shops", cited=((NEWS, "Phantasmal Flames | Pokemon.com", SAYS),)))
        added = ProductSet.objects.get(name="Phantasmal Flames")
        self.assertFalse(added.not_in_shops)
        self.assertFalse(Ask.objects.get().set_answer["sold_backed"])

    def test_the_prompt_asks_and_the_version_moved(self):
        self.assertEqual(set_judge.SET_PROMPT_VERSION, 2)
        self.assertIn("sold", set_judge.SETTLE_TOOL["input_schema"]["required"])
        self.assertIn('"not_shops"', set_judge.SET_PROMPT)


def retail_reply(row, sold="not_shops", confidence="high", cited=((WIKI, "Mystery Booster Commander Edition", MBC_SAYS),),
                 results=((WIKI, "Mystery Booster Commander Edition - MTG Wiki"),), sources=(WIKI,), searches=2,
                 model="claude-opus-5-5"):
    content = []
    for n in range(searches):
        content.append(SimpleNamespace(type="server_tool_use", id=f"srv{n}", name="web_search", input={"query": "mbc"}))
        content.append(SimpleNamespace(type="web_search_tool_result", tool_use_id=f"srv{n}", content=[
            SimpleNamespace(type="web_search_result", url=url, title=title, encrypted_content="x", page_age="")
            for url, title in results]))
    content.append(SimpleNamespace(type="text", text="The wiki says:", citations=[
        SimpleNamespace(type="web_search_result_location", url=url, title=title, cited_text=text, encrypted_index="y")
        for url, title, text in cited]))
    content.append(SimpleNamespace(type="tool_use", id="toolu_1", name="settle_retail", input={
        "row": row, "sold": sold, "confidence": confidence, "sources": list(sources),
        "reason": "The wiki says it is sold only at events and direct from Wizards."}))
    return SimpleNamespace(stop_reason="tool_use", stop_details=None, model=model, id="msg_retail", content=content,
                           usage=searched(searches))


class RetailCheckTests(Base):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.magic = make_game("Magic: The Gathering", "magic-the-gathering", short_name="Magic")
        self.mbc = make_set(self.magic, name="Mystery Booster Commander Edition", slug="mbc", code="MBC",
                            release_date=self.today)

    def answering(self, **kwargs):
        def answer(row_key, params):
            assert row_key.startswith("retail:"), row_key
            return retail_reply(row_key, **kwargs)
        return answer

    def check(self, answer=None, **kwargs):
        from .tests_judge import FakeClient

        client = FakeClient(answer or self.answering())
        return client, set_retail.run(client=client, **kwargs)

    def test_a_set_on_its_day_with_no_shop_is_asked_once_and_marked(self):
        self.switch_on()
        client, (asked, marked, spent, note, _) = self.check()
        self.assertEqual((asked, marked, note), (1, 1, ""))
        [(_, params)] = client.sent
        search, settle = params["tools"]
        self.assertEqual(settle["name"], "settle_retail")
        self.assertIn("mtg.wiki", search["allowed_domains"])
        self.mbc.refresh_from_db()
        self.assertTrue(self.mbc.not_in_shops)
        self.assertEqual(self.mbc.not_in_shops_note, WIKI)
        ask = Ask.objects.get()
        self.assertEqual((ask.kind, ask.action, ask.verdict), (Ask.Kind.RETAIL, Ask.Action.ACTED, "not_shops"))
        self.assertGreater(spent, 0)
        self.assertGreater(ask.cost_micros, 0)
        self.assertIn("Mystery Booster Commander Edition", ask.subject)

    def test_a_shop_listing_that_arrives_while_claude_is_asked_stops_the_mark(self):
        self.switch_on()

        def answer(row_key, params):
            make_listing(make_product(self.mbc, name="Mystery Booster Commander Edition Box", slug="mbc-box"),
                         make_retailer("Harbour Games"))
            return retail_reply(row_key)

        _, (asked, marked, _, _, _) = self.check(answer)
        self.mbc.refresh_from_db()
        self.assertEqual((asked, marked, self.mbc.not_in_shops), (1, 0, False))

    def test_a_date_only_quote_never_marks(self):
        self.switch_on()
        says = "Mystery Booster Commander Edition releases on 19 October 2026."
        _, (asked, marked, _, _, _) = self.check(self.answering(
            cited=((WIKI, "Mystery Booster Commander Edition", says),)))
        self.mbc.refresh_from_db()
        self.assertEqual((asked, marked, self.mbc.not_in_shops), (1, 0, False))

    def test_asked_once_per_release_date(self):
        self.switch_on()
        self.check(self.answering(sold="unsure"))
        client, (asked, *_rest) = self.check()
        self.assertEqual((asked, client.sent), (0, []))
        self.mbc.refresh_from_db()
        self.assertFalse(self.mbc.not_in_shops)
        # The date moves: a new window, asked once more.
        ProductSet.objects.filter(pk=self.mbc.pk).update(release_date=self.today + timedelta(days=3))
        _, (asked, marked, *_rest) = self.check()
        self.assertEqual((asked, marked), (1, 1))

    def test_unsure_shops_unbacked_or_not_sure_never_mark(self):
        self.switch_on()
        cases = [
            {"sold": "unsure"}, {"sold": "shops"}, {"confidence": "medium"},
            {"cited": ((WIKI, "Commander products", "Some products are sold only at events."),),
             "results": ((WIKI, "Commander products"),)},
        ]
        for n, case in enumerate(cases):
            ProductSet.objects.filter(pk=self.mbc.pk).update(release_date=self.today + timedelta(days=n))
            _, (asked, marked, *_rest) = self.check(self.answering(**case))
            self.assertEqual((asked, marked), (1, 0), case)
        self.mbc.refresh_from_db()
        self.assertFalse(self.mbc.not_in_shops)

    def test_suggest_only_marks_nothing(self):
        self.switch_on(may_act=False)
        _, (asked, marked, *_rest) = self.check()
        self.assertEqual((asked, marked), (1, 0))
        self.assertEqual(Ask.objects.get().action, Ask.Action.SUGGESTED)
        self.mbc.refresh_from_db()
        self.assertFalse(self.mbc.not_in_shops)

    def test_only_sets_near_their_day_that_no_shop_lists(self):
        self.switch_on()
        far = make_set(self.magic, name="Far Away", slug="far", code="FAR", release_date=self.today + timedelta(days=40))
        old = make_set(self.magic, name="Long Out", slug="old", code="OLD", release_date=self.today - timedelta(days=30))
        listed = make_set(self.magic, name="Listed Set", slug="listed", code="LST", release_date=self.today + timedelta(days=4))
        make_listing(make_product(listed, name="Listed Set Box", slug="lst-box"), make_retailer("Harbour Games"))
        make_set(self.magic, name="Marked", slug="marked", code="MRK", release_date=self.today, not_in_shops=True)
        undated = make_set(self.magic, name="No Date", slug="nodate", code="NDT")
        soon = make_set(self.magic, name="Soon Set", slug="soon", code="SOO", release_date=self.today + timedelta(days=14))
        ids = [s.pk for s, _ in set_retail.candidates(self.today)]
        self.assertEqual(ids, [self.mbc.pk, soon.pk])
        for left in (far, old, listed, undated):
            self.assertNotIn(left.pk, ids)

    def test_capped_by_the_monthly_limit_the_run_and_the_searches(self):
        self.switch_on(monthly_budget_usd=Decimal("0.01"))
        client, (asked, _, _, note, _) = self.check()
        self.assertEqual((asked, client.sent, note), (0, [], "Reached the monthly limit"))
        self.assertFalse(Ask.objects.exists())
        ClaudeJudge.objects.update(monthly_budget_usd=Decimal("25"))
        for n in range(5):
            make_set(self.magic, name=f"Event Set {n}", slug=f"event-{n}", code=f"EV{n}", release_date=self.today)
        with mock.patch.object(set_retail, "RETAIL_PER_RUN", 2):
            client, (asked, _, _, note, _) = self.check()
        self.assertEqual((asked, len(client.sent), note), (2, 2, "Reached the limit for one run"))
        with mock.patch.object(set_judge, "SEARCHES_PER_RUN", set_judge.SET_SEARCHES):
            client, (asked, *_rest) = self.check()
        self.assertEqual(asked, 1)

    def test_nothing_while_claude_is_off(self):
        client, (asked, _, _, note, _) = self.check()
        self.assertEqual((asked, client.sent), (0, []))
        self.assertTrue(note)

    def test_the_judge_rows_ignore_retail_answers(self):
        self.switch_on()
        self.check()
        self.assertFalse(any(row.key.startswith("retail:") for row in judge.waiting()))

    def test_the_command_dry_run_sends_nothing(self):
        self.switch_on()
        out = StringIO()
        call_command("check_set_retail", "--dry-run", stdout=out)
        self.assertIn(f"retail:{self.mbc.pk} Mystery Booster Commander Edition: worst case $", out.getvalue())
        self.assertFalse(Ask.objects.exists())
