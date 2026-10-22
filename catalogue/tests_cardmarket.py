"""Cardmarket's sealed catalogue and daily price guide, kept as our own copy (cardmarket.py), and Claude matching
its products to ours (cardmarket_match.py)."""

import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

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

    def test_the_monthly_limit_stops_a_run_before_it_spends(self):
        self.switch_on()
        ClaudeJudge.objects.filter(pk=1).update(monthly_budget_usd=Decimal("0.00"))
        client = FakeClient(lambda evidence: reply(evidence["ours"]["key"], 0))
        result = cardmarket_match.run(client=client)
        self.assertEqual((client.sent, result.note), ([], "Reached Claude's monthly limit"))
        self.assertEqual(ExchangeRate.objects.count(), 1)


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


def band_key(band):
    from . import fair_price

    return fair_price.KEYS[band]


class MarketDiscordTests(MarketPriceCheckTests):
    def test_a_restock_post_names_the_market_price_and_says_high(self):
        from . import discord

        self.assertEqual(discord.price_check_words(self.box, Decimal("1199.99"), True, None),
                         "Only one UK shop we check lists it, so there is no other price to compare with.")
        CardmarketMatch.objects.create(product=self.box, cardmarket_id=550461, status=CardmarketMatch.Status.MATCHED)
        self.assertEqual(discord.price_check_words(self.box, Decimal("1199.99"), True, None),
                         "Price check: High price, against a European market price of £577.70.")
