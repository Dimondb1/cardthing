"""What people ask (web/people_ask.py): every question to Rip, search and visitor message, sorted into the problems
people have. The owner, 20 Nov 2026: "Shall we plan it together based on what people are asking for. We need to solve a
problem that exists"."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import ClaudeAsk, DailySearch
from catalogue.testing import make_game, make_product, make_set
from inbox.models import Conversation, Message

from . import people_ask


def question(product, words, days_ago=0):
    """A question asked of Rip on a product page, as askrip records it."""
    return ClaudeAsk.objects.create(
        kind=ClaudeAsk.Kind.ASK, row_key=f"ask:{product.pk}", fingerprint=words[:60], model_asked="claude-haiku-5-5",
        effort="low", asked_at=timezone.now() - timedelta(days=days_ago), product=product,
        evidence={"product": product.name, "question": words})


def request(words, row_key="voice"):
    """One of Rip's requests from the list builder, the microphone or a search bar."""
    return ClaudeAsk.objects.create(kind=ClaudeAsk.Kind.ASK, row_key=row_key, fingerprint=words[:60],
                                    model_asked="claude-haiku-5-5", effort="low", evidence={"list": words})


def searched(query, hits, day=None):
    return DailySearch.objects.create(date=day or timezone.localdate(), query=query, hits=hits, results=3)


def message(body, from_visitor=True):
    thread = Conversation.objects.create(name="Sam", email="sam@example.com")
    return Message.objects.create(conversation=thread, body=body, from_visitor=from_visitor)


class ThemeTests(TestCase):
    def test_each_problem_is_known_by_its_words_the_first_match_wins(self):
        for text, theme in [
            ("Is this a fake or resealed box?", "trust"),
            ("Is the cheap one on eBay legit", "trust"),
            ("how much is delivery to Belfast", "delivery"),
            ("Buy now or wait?", "when"),
            ("will it go down after christmas", "when"),
            ("what was the RRP", "rrp"),
            ("when do pre-orders open", "rrp"),
            ("when is it back in stock", "stock"),
            ("Is it good value?", "price"),
            ("is £55 too much", "price"),
            ("booster box under 20", "price"),
            ("What's inside?", "inside"),
            ("what are the pull rates", "inside"),
            ("Is it right for me?", "fit"),
            ("good gift for an 8 year old son", "fit"),
            ("japanese booster box", "language"),
            ("surging sparks etb", "other"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(people_ask.theme_of(text), theme)


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
class PageTests(TestCase):
    def setUp(self):
        game = make_game()
        self.box = make_product(make_set(game), name="Surging Sparks Booster Box", slug="box",
                                product_type="booster_box")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        self.url = reverse("people_ask")

    def page(self, **query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200)
        return response

    def test_staff_only_never_cached_or_indexed_and_linked_from_numbers(self):
        response = self.page()
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        home = self.client.get(reverse("admin:index")).content.decode()
        numbers = home[home.index('id="rr-numbers"'):home.index('id="rr-catalogue"')]
        self.assertIn(f'href="{self.url}" class="section">What people ask</a>', numbers)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_a_guides_editor_is_turned_away(self):
        from guides.apps import EDITORS

        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)

    def test_problems_ranked_by_peoples_own_words_with_their_examples(self):
        for _ in range(5):
            question(self.box, "Is it good value?")          # one-tap: counted, does not rank
        question(self.box, "is this a fake, the price looks too low")
        question(self.box, "Could it be resealed?")
        request("is the ebay one legit")
        message("How do I know a shop is safe to buy from? Email me at sam@example.com")
        searched("fake booster box", hits=4)
        question(self.box, "is £140 fair for this")
        found = self.page().context
        themes = {theme["key"]: theme for theme in found["themes"]}
        self.assertEqual(found["themes"][0]["key"], "trust")
        self.assertEqual((themes["trust"]["own"], themes["trust"]["total"]), (8, 8))
        self.assertEqual((themes["price"]["own"], themes["price"]["total"]), (1, 6))
        self.assertEqual(themes["trust"]["counts"],
                         {"typed": 2, "tapped": 0, "rip": 1, "search": 4, "message": 1})
        words = [example["text"] for example in themes["trust"]["examples"]]
        # Typed questions newest first, then Rip's requests, messages and the searches; emails taken out.
        self.assertEqual(words[:2], ["Could it be resealed?", "is this a fake, the price looks too low"])
        self.assertIn("How do I know a shop is safe to buy from? Email me at [email]", words)
        self.assertEqual(words[-1], "fake booster box")
        self.assertEqual(found["taps"], [("Is it good value?", 5)])
        html = self.page().content.decode()
        self.assertIn("Surging Sparks Booster Box", html)
        self.assertNotIn("sam@example.com", html)

    def test_only_the_period_counts_and_replies_are_not_asks(self):
        question(self.box, "is this a fake", days_ago=100)
        searched("fake booster box", hits=2, day=date.today() - timedelta(days=100))
        message("We replied: it is genuine", from_visitor=False)
        self.assertEqual(self.page().context["themes"], [])
        self.assertIn("Nothing asked in the last 90 days.", self.page().content.decode())
        trust = self.page(days="180").context["themes"][0]
        self.assertEqual((trust["key"], trust["total"]), ("trust", 3))
        self.assertEqual(self.page(days="nonsense").context["days"], 90)

    def test_scanners_probes_are_not_searches(self):
        searched("<script>alert(1)</script>", hits=9)
        self.assertEqual(self.page().context["counted"]["search"], 0)

    def test_the_log_to_copy_says_it_all_as_text(self):
        question(self.box, "is this a fake")
        question(self.box, "Any red flags?")
        searched("surging sparks", hits=3)
        log = self.page().context["log"]
        self.assertIn("Typed to Rip on product pages: 1. One-tap questions: 1.", log)
        self.assertIn("Fakes, reseals and safe sellers: 1 in their own words, 2 in all "
                      "(typed to Rip on a product page 1, one-tap question to Rip 1)", log)
        self.assertIn('  - "is this a fake" (typed to Rip on a product page, Surging Sparks Booster Box)', log)
        self.assertIn('  - "surging sparks" (searched, 3 times)', log)
        self.assertIn("One-tap questions: Any red flags? 1", log)
        self.assertIn('id="people-ask-log"', self.page().content.decode())

    def test_four_queries_however_many_asks(self):
        for number in range(12):
            question(self.box, f"question {number} is it fake")
            request(f"request {number}")
            searched(f"search {number}", hits=number + 1)
            message(f"message {number}")
        with CaptureQueriesContext(connection) as queries:
            asks = people_ask.gathered(timezone.now() - timedelta(days=90))
        self.assertEqual(len(asks), 48)
        reading = [query for query in queries if "SiteContent" not in query["sql"] and "content_" not in query["sql"]]
        self.assertEqual(len(reading), 4)
