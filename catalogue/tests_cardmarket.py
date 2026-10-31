"""Cardmarket's sealed catalogue and daily price guide, kept as our own copy (cardmarket.py), and Claude matching
its products to ours (cardmarket_match.py)."""

import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from . import cardmarket, cardmarket_match, judge
from .models import (
    CardmarketMatch, CardmarketPrice, CardmarketProduct, ClaudeAsk, ClaudeJudge, ExchangeRate,
)
from .testing import make_game, make_listing, make_product, make_retailer, make_set

KEY = "sk-ant-api03-" + "x" * 40 + "AbCd"
CATALOGUE = {"version": 1, "createdAt": "2026-10-21T13:49:06+0200", "products": [
    {"idProduct": 550461, "name": "GX Ultra Shiny Booster Box", "idCategory": 53, "categoryName": "Pokémon Display",
     "idExpansion": 3856, "idMetacard": 0, "dateAdded": "2021-03-15 15:11:38"},
    {"idProduct": 550476, "name": "GX Ultra Shiny Booster", "idCategory": 52, "categoryName": "Pokémon Booster",
     "idExpansion": 3856, "idMetacard": 0, "dateAdded": "2021-03-15 15:14:16"},
    {"idProduct": 784949, "name": "Surging Sparks Booster Box", "idCategory": 53, "categoryName": "Pokémon Display",
     "idExpansion": 5800, "idMetacard": 0, "dateAdded": "2024-09-01 10:00:00"},
    {"idProduct": 784950, "name": "Surging Sparks 6 Booster Box Case", "idCategory": 53,
     "categoryName": "Pokémon Display", "idExpansion": 5800, "idMetacard": 0, "dateAdded": "2024-09-01 10:00:00"},
    {"idProduct": 784951, "name": "Surging Sparks Booster Box (18 Boosters)", "idCategory": 53,
     "categoryName": "Pokémon Display", "idExpansion": 5800, "idMetacard": 0, "dateAdded": "2024-09-01 10:00:00"},
    {"idProduct": 900001, "name": "Surging Sparks Coin", "idCategory": 1017, "categoryName": "Pokémon Coins",
     "idExpansion": 5800, "idMetacard": 0, "dateAdded": "2024-09-01 10:00:00"},
]}
GUIDE = ('{"version":1,"createdAt":"2026-10-22T02:45:11+0200","priceGuides":['
         '{"idProduct":550461,"idCategory":53,"avg":849.99,"low":600,"trend":689.17,"avg1":null,"avg7":null,"avg30":null},'
         '{"idProduct":784949,"idCategory":53,"avg":260.5,"low":230,"trend":250.24,"avg1":null,"avg7":null,"avg30":null},'
         '{"idProduct":784950,"idCategory":53,"avg":1600,"low":1500,"trend":1576.97,"avg1":null,"avg7":null,"avg30":null},'
         '{"idProduct":111111,"idCategory":51,"avg":3.5,"low":1,"trend":2.2,"avg1":1,"avg7":2,"avg30":3}]}')
ECB = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='2026-10-21'><Cube currency='USD' rate='1.1234'/><Cube currency='GBP' rate='0.83825'/></Cube></Cube>
</gesmes:Envelope>"""


def files(url):
    if url == cardmarket.ECB_URL:
        return ECB.encode()
    if "productList" in url:
        return json.dumps(CATALOGUE).encode()
    return GUIDE.encode()


class ImportTests(TestCase):
    def setUp(self):
        self.pokemon = make_game()

    def test_the_sealed_catalogue_and_todays_prices_are_kept_and_singles_left_out(self):
        rate, done, errors = cardmarket.run(["pokemon"], read=files)
        self.assertEqual(errors, [])
        self.assertEqual((rate.date, rate.gbp_per_eur), (date(2026, 10, 21), Decimal("0.83825")))
        [row] = done
        self.assertEqual((row.products, row.new, row.prices, row.day), (6, 6, 3, date(2026, 10, 22)))
        box = CardmarketProduct.objects.get(pk=550461)
        self.assertEqual((box.game, box.category, box.added_on), (self.pokemon, "Pokémon Display", date(2021, 3, 15)))
        price = CardmarketPrice.objects.get(product=box)
        self.assertEqual((price.trend, price.avg, price.low), (Decimal("689.17"), Decimal("849.99"), Decimal("600.00")))
        self.assertFalse(CardmarketPrice.objects.filter(product_id=111111).exists())   # a single: not ours
        # A second run the same day updates, never doubles; the history is one row a day.
        cardmarket.run(["pokemon"], read=files)
        self.assertEqual(CardmarketPrice.objects.count(), 3)
        self.assertEqual(cardmarket.in_pounds(price.trend, rate), Decimal("577.70"))

    def test_one_game_failing_never_stops_the_others(self):
        make_game("Disney Lorcana", "lorcana")

        def broken(url):
            if "_19.json" in url:
                raise cardmarket.CardmarketError("Lorcana could not be read")
            return files(url)

        _rate, done, errors = cardmarket.run(read=broken)
        self.assertEqual([row.game for row in done], ["pokemon"])
        self.assertEqual(errors, ["Lorcana could not be read"])


def reply(product_key, match, confidence="high", reason="The names and kind match."):
    body = json.dumps({"product": product_key, "match": match, "confidence": confidence, "reason": reason})
    return SimpleNamespace(stop_reason="end_turn", stop_details=None, model="claude-opus-5-5", id="msg_1",
                           content=[SimpleNamespace(type="text", text=body)],
                           usage=SimpleNamespace(input_tokens=900, output_tokens=120, cache_creation_input_tokens=0,
                                                 cache_read_input_tokens=0, iterations=None))


class FakeClient:
    def __init__(self, answer):
        self.sent = []
        self.answer = answer
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params):
        content = params["messages"][0]["content"]
        evidence = json.loads(content[len("<evidence>"):-len("</evidence>")])
        self.sent.append(evidence)
        self.asked_with = (params["model"], params["output_config"]["effort"])
        return self.answer(evidence)


class MatchTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        overrides = override_settings(RIPRAPTOR_CLAUDE_KEY_FILE=str(Path(folder.name) / "claude-key"),
                                      RIPRAPTOR_CLAUDE_API_KEY="", RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD=25)
        overrides.enable()
        self.addCleanup(overrides.disable)
        patcher = mock.patch.object(judge, "make_client", side_effect=AssertionError("no real client in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pokemon = make_game()
        cardmarket.run(["pokemon"], read=files)
        self.shiny = make_product(make_set(self.pokemon, name="Ultra Shiny GX", slug="sm8b", code="SM8b"),
                                  name="Ultra Shiny GX sm8b Japanese Booster Box", slug="sm8b-box",
                                  product_type="booster_box", language="ja")
        make_listing(self.shiny, make_retailer("Japan2UK"), price="1199.99", delivery="0.00")
        self.sparks = make_product(make_set(self.pokemon, name="Surging Sparks", slug="ssp", code="SSP"),
                                   name="Surging Sparks Booster Box", slug="ssp-box", product_type="booster_box")
        self.lonely = make_product(make_set(self.pokemon, name="Quiet Tide", slug="qt", code=""),
                                   name="Quiet Tide Elite Trainer Box", slug="qt-etb")

    def switch_on(self):
        judge.write_key(KEY)
        ClaudeJudge.objects.update_or_create(pk=1, defaults={"enabled": True})

    def test_the_shortlist_is_the_sets_products_of_our_kind_best_first(self):
        pool = list(CardmarketProduct.objects.all())
        # The box itself first; the case and the 18-booster box are there for Claude to rule out; the coin is not.
        found = [c.pk for c in cardmarket_match.shortlist(self.sparks, pool)]
        self.assertEqual((found[0], sorted(found)), (784949, [784949, 784950, 784951]))
        self.assertEqual([c.pk for c in cardmarket_match.shortlist(self.shiny, pool)], [550461])
        self.assertEqual(cardmarket_match.shortlist(self.lonely, pool), [])

    def test_the_products_people_look_at_are_matched_first(self):
        from catalogue.models import DailyPageView

        make_listing(self.sparks, make_retailer("Harbour Games"), price="150.00")
        make_listing(self.lonely, make_retailer("Northgate Cards"), price="50.00")
        # Selling now, then by name, while nobody has looked.
        self.assertEqual([p.slug for p in cardmarket_match.to_do(["pokemon"])], ["qt-etb", "ssp-box", "sm8b-box"])
        today = timezone.localdate()
        DailyPageView.objects.create(kind=DailyPageView.Kind.PRODUCT, key="sm8b-box", date=today, hits=40)
        DailyPageView.objects.create(kind=DailyPageView.Kind.PRODUCT, key="ssp-box", date=today, hits=3)
        # A week ago does not count.
        DailyPageView.objects.create(kind=DailyPageView.Kind.PRODUCT, key="qt-etb",
                                     date=today - timedelta(days=cardmarket_match.VIEW_DAYS), hits=90)
        self.assertEqual([p.slug for p in cardmarket_match.to_do(["pokemon"])], ["sm8b-box", "ssp-box", "qt-etb"])

    def test_nothing_is_asked_while_claude_is_off(self):
        result = cardmarket_match.run()
        self.assertIn("switched off", result.note)
        self.assertFalse(CardmarketMatch.objects.exists())

    def test_claude_matches_and_a_sure_answer_is_used_and_counted_like_the_judges(self):
        self.switch_on()
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], evidence["candidates"][0]["id"]))
        result = cardmarket_match.run(client=client)
        self.assertEqual((result.asked, result.matched, result.none), (2, 2, 1))
        shiny = CardmarketMatch.objects.get(product=self.shiny)
        self.assertEqual((shiny.cardmarket_id, shiny.status), (550461, CardmarketMatch.Status.MATCHED))
        # What Claude saw: ours with its language and price, the candidates with Cardmarket's price in pounds.
        sent = next(e for e in client.sent if e["ours"]["key"] == f"product-{self.shiny.pk}")
        self.assertEqual((sent["ours"]["language"], sent["ours"]["price_gbp"]), ("Japanese", "1199.99"))
        self.assertEqual(sent["candidates"][0]["trend_gbp"], "577.70")
        # No candidate: noted as none without asking.
        self.assertEqual(CardmarketMatch.objects.get(product=self.lonely).status, CardmarketMatch.Status.NONE)
        asks = ClaudeAsk.objects.filter(kind=ClaudeAsk.Kind.MARKET)
        self.assertEqual(asks.count(), 2)
        self.assertTrue(all(ask.cost_micros > 0 and ask.reserved_micros == 0 for ask in asks))
        self.assertEqual(judge.spent_this_month(), sum(ask.cost_micros for ask in asks))
        # Asked once: the same evidence is never asked again.
        again = cardmarket_match.run(client=client)
        self.assertEqual(again.asked, 0)

    def test_an_unsure_answer_or_a_far_off_price_waits_for_the_owner(self):
        self.switch_on()

        def answer(evidence):
            if evidence["ours"]["key"] == f"product-{self.sparks.pk}":
                return reply(evidence["ours"]["key"], 784949, confidence="medium")
            return reply(evidence["ours"]["key"], 550461)

        # The shop's price for the Shiny box is far from Cardmarket's: more than four times it.
        self.shiny.listings.update(price=Decimal("2999.99"))
        cardmarket_match.run(client=FakeClient(answer))
        self.assertEqual(CardmarketMatch.objects.get(product=self.sparks).status, CardmarketMatch.Status.CHECK)
        self.assertEqual(CardmarketMatch.objects.get(product=self.shiny).status, CardmarketMatch.Status.CHECK)

    def test_the_owners_answer_is_never_asked_again_and_a_wrong_answer_is_not_saved(self):
        self.switch_on()
        CardmarketMatch.objects.create(product=self.shiny, cardmarket_id=550461, status=CardmarketMatch.Status.CONFIRMED)
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], 123456789))   # not a candidate
        result = cardmarket_match.run(client=client)
        self.assertEqual([e["ours"]["key"] for e in client.sent], [f"product-{self.sparks.pk}"])
        self.assertEqual(result.matched, 0)
        self.assertFalse(CardmarketMatch.objects.filter(product=self.sparks).exists())
        self.assertEqual(ClaudeAsk.objects.get(kind="market").outcome, ClaudeAsk.Outcome.INVALID)

    def test_a_quick_model_at_low_effort_whatever_the_judge_uses(self):
        # The owner: "It could have done 8 in the last 3 hours." The judge's Opus at medium effort was slow;
        # picking one of a few products needs neither.
        self.switch_on()
        ClaudeJudge.objects.filter(pk=1).update(model="claude-opus-5-5", effort="high")
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], evidence["candidates"][0]["id"]))
        cardmarket_match.run(client=client)
        self.assertEqual(client.asked_with, ("claude-sonnet-5-5", "low"))
        self.assertEqual(set(ClaudeAsk.objects.filter(kind="market").values_list("model_asked", "effort")),
                         {("claude-sonnet-5-5", "low")})

    def test_many_products_are_asked_several_at_a_time_and_every_answer_saved(self):
        self.switch_on()
        game_set = make_set(self.pokemon, name="Surging Sparks", slug="ssp-more", code="SSP")
        more = [make_product(game_set, name=f"Surging Sparks Booster Box {n}", slug=f"ssp-box-{n}",
                             product_type="booster_box") for n in range(cardmarket_match.WORKERS * 2 + 1)]
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], evidence["candidates"][0]["id"]))
        result = cardmarket_match.run(client=client)
        self.assertEqual(result.asked, len(more) + 2)
        self.assertEqual(CardmarketMatch.objects.filter(product__in=more, status=CardmarketMatch.Status.MATCHED).count(),
                         len(more))
        self.assertEqual(ClaudeAsk.objects.filter(kind="market", reserved_micros=0).count(), len(more) + 2)

    def test_a_run_stops_when_anthropic_stays_busy(self):
        import anthropic

        from .tests_judge import error

        self.switch_on()
        game_set = make_set(self.pokemon, name="Surging Sparks", slug="ssp-more", code="SSP")
        for n in range(cardmarket_match.BUSY_LIMIT + cardmarket_match.WORKERS):
            make_product(game_set, name=f"Surging Sparks Booster Box {n}", slug=f"ssp-box-{n}", product_type="booster_box")

        def busy(evidence):
            raise error(anthropic.RateLimitError, 429, "rate_limit_error")

        result = cardmarket_match.run(client=FakeClient(busy))
        self.assertEqual(result.note, "Anthropic was too busy; the next run carries on")
        self.assertLess(len(result.errors), cardmarket_match.BUSY_LIMIT + 2 * cardmarket_match.WORKERS)
        self.assertFalse(CardmarketMatch.objects.exclude(status=CardmarketMatch.Status.NONE).exists())

    def test_the_monthly_limit_stops_a_run_before_it_spends_and_says_who_spent_it(self):
        self.switch_on()
        # The limit is shared with Things to check: $2.00 of this month's $3.00 went on the judge, $1.50 on matching.
        ClaudeJudge.objects.filter(pk=1).update(monthly_budget_usd=Decimal("3.00"))
        for kind, cost in ((ClaudeAsk.Kind.OFFER, 2_000_000), (ClaudeAsk.Kind.MARKET, 1_500_000)):
            ClaudeAsk.objects.create(kind=kind, row_key="x", fingerprint="f", model_asked="m", effort="low",
                                     outcome=ClaudeAsk.Outcome.ANSWERED, cost_micros=cost)
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], 0))
        result = cardmarket_match.run(client=client)
        self.assertEqual(client.sent, [])
        self.assertEqual(result.note, "Reached Claude's monthly limit: $3.50 of $3.00 spent this month, $1.50 of it on matching")
        self.assertEqual(ExchangeRate.objects.count(), 1)
        # Kept for the dashboard: a run that asked nothing keeps only why.
        self.assertEqual(ClaudeJudge.current().market_run_note, result.note + ".")

    def test_each_run_is_kept_for_the_dashboard_with_what_stopped_it(self):
        from io import StringIO

        from django.core.management import call_command

        cardmarket_match.run()
        state = ClaudeJudge.current()
        self.assertEqual(state.market_run_note, "Claude is switched off or has no key (Things to check).")
        self.assertIsNotNone(state.market_run_at)
        self.switch_on()
        # A dry run changes nothing.
        out = StringIO()
        call_command("match_cardmarket", "--dry-run", stdout=out)
        self.assertEqual(out.getvalue().strip(), "Cardmarket matching: would ask 2, matched 0, to check 0, none 1, spent $0.00")
        self.assertEqual(ClaudeJudge.current().market_run_at, state.market_run_at)
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], evidence["candidates"][0]["id"]))
        result = cardmarket_match.run(client=client, limit=1)
        state = ClaudeJudge.current()
        self.assertEqual(state.market_run_note, f"Asked 1, matched 1, to check 0, none 0, spent ${result.spent / 1_000_000:.2f}. "
                                                "Reached the products for one run.")
        self.assertGreater(state.market_run_at, timezone.now() - timedelta(minutes=1))
        self.assertEqual(result.summary(), state.market_run_note[0].lower() + state.market_run_note[1:-1])

    def test_an_answer_that_cannot_be_read_is_asked_again_once_then_waits_for_the_owner(self):
        self.switch_on()
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], 123456789))   # never a candidate
        first = cardmarket_match.run(client=client)
        self.assertEqual((first.asked, first.check), (2, 0))
        self.assertFalse(CardmarketMatch.objects.filter(product=self.sparks).exists())
        second = cardmarket_match.run(client=client)
        self.assertEqual((second.asked, second.check), (2, 2))
        match = CardmarketMatch.objects.get(product=self.sparks)
        self.assertEqual((match.status, match.cardmarket_id), (CardmarketMatch.Status.CHECK, None))
        self.assertEqual(match.reason, "Claude's answer could not be read 2 times: choose yourself")
        self.assertEqual(sorted(match.candidates), [784949, 784950, 784951])
        # Not asked a third time, until our product or the candidates change.
        self.assertEqual(cardmarket_match.run(client=client).asked, 0)
        self.assertEqual(len(client.sent), 4)
        self.sparks.name = "Surging Sparks Booster Box (36 packs)"
        self.sparks.save()
        self.assertEqual(cardmarket_match.run(client=client).asked, 1)
        self.assertEqual(len(client.sent), 5)


class AdminTests(TestCase):
    def test_the_owner_confirms_or_rejects_a_match(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        game = make_game()
        cardmarket.run(["pokemon"], read=files)
        product = make_product(make_set(game, name="Surging Sparks", slug="ssp", code="SSP"),
                               name="Surging Sparks Booster Box", slug="ssp-box", product_type="booster_box")
        row = CardmarketMatch.objects.create(product=product, cardmarket_id=784949, status=CardmarketMatch.Status.CHECK)
        User.objects.create_superuser("owner", "owner@example.com", "pw")
        self.client.login(username="owner", password="pw")
        url = reverse("admin:catalogue_cardmarketmatch_changelist")
        self.assertContains(self.client.get(url), "Surging Sparks Booster Box")
        self.client.post(url, {"action": "confirm", "_selected_action": [row.pk]})
        row.refresh_from_db()
        self.assertEqual(row.status, CardmarketMatch.Status.CONFIRMED)
        self.client.post(url, {"action": "reject", "_selected_action": [row.pk]})
        row.refresh_from_db()
        self.assertEqual((row.status, row.cardmarket_id), (CardmarketMatch.Status.REJECTED, None))
        self.assertContains(self.client.get(reverse("admin:catalogue_cardmarketproduct_changelist")), "689.17")


class MarketPriceCheckTests(TestCase):
    """The owner's case once Cardmarket is matched: £1,199.99 at one UK shop against a European market price of
    about £578 is called a high price, on the page and in Discord."""

    def setUp(self):
        from django.utils import timezone

        self.today = timezone.localdate()
        game = make_game()
        cardmarket.run(["pokemon"], read=files)
        CardmarketPrice.objects.update(date=self.today)
        self.box = make_product(make_set(game, name="Ultra Shiny GX", slug="sm8b", code="SM8b"),
                                name="Ultra Shiny GX sm8b Japanese Booster Box", slug="sm8b-box",
                                product_type="booster_box", language="ja")
        make_listing(self.box, make_retailer("Japan2UK"), price="1199.99", delivery="0.00")

    def test_one_shop_with_a_market_price_gets_a_verdict_against_it(self):
        from . import fair_price

        self.assertIsNone(fair_price.verdict(self.box, Decimal("1199.99"), shops=1))   # not matched yet
        CardmarketMatch.objects.create(product=self.box, cardmarket_id=550461, status=CardmarketMatch.Status.MATCHED)
        found = fair_price.verdict(self.box, Decimal("1199.99"), shops=1)
        self.assertEqual((found.key, found.market.gbp, found.days), ("high", Decimal("577.70"), 0))
        # In line with the market: fair; well under it: good, never "lowest yet".
        self.assertEqual(fair_price.verdict(self.box, Decimal("600.00"), shops=1).key, "fair")
        self.assertEqual(fair_price.verdict(self.box, Decimal("540.00"), shops=1).key, "good")
        # A match Claude was not sure of is not used, nor a Cardmarket price older than a week.
        CardmarketMatch.objects.filter(product=self.box).update(status=CardmarketMatch.Status.CHECK)
        self.assertIsNone(fair_price.verdict(self.box, Decimal("1199.99"), shops=1))
        CardmarketMatch.objects.filter(product=self.box).update(status=CardmarketMatch.Status.CONFIRMED)
        from datetime import timedelta

        CardmarketPrice.objects.update(date=self.today - timedelta(days=fair_price.MARKET_FRESH_DAYS + 1))
        self.assertIsNone(fair_price.verdict(self.box, Decimal("1199.99"), shops=1))

    def test_the_page_says_high_price_and_names_the_market(self):
        CardmarketMatch.objects.create(product=self.box, cardmarket_id=550461, status=CardmarketMatch.Status.MATCHED)
        response = self.client.get(self.box.get_absolute_url())
        self.assertEqual(response.context["fair"].key, "high")
        self.assertContains(response, 'class="fair fair--high"')
        self.assertContains(response, "European market")
        self.assertContains(response, "£577.70")
        self.assertContains(response, "before post to the UK and any import costs")
        self.assertNotContains(response, "Average of")   # one shop: our own record is not used

    def test_both_references_take_the_lower_band(self):
        from datetime import timedelta

        from . import fair_price
        from .models import DailyLowestPrice

        CardmarketMatch.objects.create(product=self.box, cardmarket_id=550461, status=CardmarketMatch.Status.MATCHED)
        DailyLowestPrice.objects.bulk_create([DailyLowestPrice(product=self.box, date=self.today - timedelta(days=n),
                                                               price=Decimal("1400.00")) for n in range(1, 21)])
        # Our own record alone would call £1,199.99 the lowest yet; the market says it is high.
        found = fair_price.verdict(self.box, Decimal("1199.99"), shops=2)
        self.assertEqual((band_key(fair_price.band_for(found.price, found.average, found.low)), found.key), ("lowest", "high"))

    def test_the_lowest_yet_stands_when_the_market_calls_it_good(self):
        from datetime import timedelta

        from . import fair_price
        from .models import DailyLowestPrice

        CardmarketMatch.objects.create(product=self.box, cardmarket_id=550461, status=CardmarketMatch.Status.MATCHED)
        DailyLowestPrice.objects.bulk_create([DailyLowestPrice(product=self.box, date=self.today - timedelta(days=n),
                                                               price=Decimal("700.00")) for n in range(1, 21)])
        # Under the market (£577.70) and the lowest we have recorded: the lowest yet, not just good.
        self.assertEqual(fair_price.verdict(self.box, Decimal("560.00"), shops=2).key, "lowest")
        # At the market price exactly: good by the market, so the record's lowest yet stands.
        self.assertEqual(fair_price.verdict(self.box, Decimal("577.70"), shops=2).key, "lowest")
        # A little over the market: fair by it, and the lower band wins.
        self.assertEqual(fair_price.verdict(self.box, Decimal("590.00"), shops=2).key, "fair")
        # Good by our record (5% under its average) but not its lowest, and good by the market: good.
        DailyLowestPrice.objects.filter(date=self.today - timedelta(days=3)).update(price=Decimal("500.00"))
        self.assertEqual(fair_price.verdict(self.box, Decimal("560.00"), shops=2).key, "good")


class SettleTests(TestCase):
    """How the bands combine (fair_price.settle), from the owner's mini tin: £19.09 today, the lowest we had
    recorded (£21.13) and 21% under its 26-day average (£24.19), against Cardmarket's £20.05."""

    def test_the_owners_mini_tin_is_the_lowest_yet(self):
        from . import fair_price

        price = Decimal("19.09")
        record = fair_price.band_for(price, Decimal("24.19"), Decimal("21.13"))
        market = fair_price.market_band(price, Decimal("20.05"))
        self.assertEqual((band_key(record), band_key(market)), ("lowest", "good"))
        self.assertEqual(band_key(fair_price.settle([record, market], price, None)), "lowest")

    def test_the_lower_band_still_wins_otherwise(self):
        from . import fair_price as fp

        self.assertEqual(fp.settle([fp.LOWEST, fp.FAIR], Decimal("10"), None), fp.FAIR)
        self.assertEqual(fp.settle([fp.LOWEST, fp.HIGH_PRICE], Decimal("10"), None), fp.HIGH_PRICE)
        self.assertEqual(fp.settle([fp.GOOD_PRICE, fp.GOOD_PRICE], Decimal("10"), None), fp.GOOD_PRICE)
        self.assertEqual(fp.settle([fp.LOWEST], Decimal("10"), None), fp.LOWEST)
        # The RRP still holds a dear price down, whatever the others say.
        self.assertEqual(fp.settle([fp.LOWEST, fp.GOOD_PRICE], Decimal("20"), Decimal("10")), fp.HIGH_PRICE)
        self.assertEqual(fp.settle([fp.LOWEST, fp.GOOD_PRICE], Decimal("12"), Decimal("10")), fp.ABOVE)
        self.assertEqual(fp.settle([], Decimal("10"), Decimal("10")), fp.FAIR)
        self.assertIsNone(fp.settle([], Decimal("10"), None))


def band_key(band):
    from . import fair_price

    return fair_price.KEYS[band]


class MarketDiscordTests(MarketPriceCheckTests):
    def test_a_restock_post_names_the_market_price_and_says_high(self):
        from . import discord

        self.assertEqual(discord.price_check_field(self.box, Decimal("1199.99"), True, None),
                         ("Price check", "Only one UK shop lists it, so nothing to compare with."))
        CardmarketMatch.objects.create(product=self.box, cardmarket_id=550461, status=CardmarketMatch.Status.MATCHED)
        self.assertEqual(discord.price_check_field(self.box, Decimal("1199.99"), True, None),
                         ("Price check", "**High price**\nEuropean market £577.70"))
