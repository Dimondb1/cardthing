"""Ask Rip's settings on the Claude page (AskRipSettings, askrip.chosen): the questions a person and the whole site may
ask a day, and the model that answers. The owner, 13 Nov 2026: "Add a way to configure ask rips limit amount and chat
model. In admin. As I need to be able to change this"."""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from catalogue import judge
from catalogue.models import AskRipSettings, ClaudeAsk
from web import askrip

from .tests_askrip import AskRipCase


class SettingsCase(AskRipCase):
    def setUp(self):
        super().setUp()
        # Opus and Sonnet go through the beta client, with the server-side fallback; Haiku never does.
        self.fake.beta = SimpleNamespace(messages=SimpleNamespace(create=self.beta_create))
        self.beta_sent = []

    def beta_create(self, **params):
        self.beta_sent.append(params)
        betas, fallbacks = params.pop("betas"), params.pop("fallbacks")
        self.assertEqual((betas, fallbacks), ([judge.FALLBACK_BETA], "default"))
        return self.fake.create(**params)

    def owner(self):
        owner = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(owner)
        return owner

    def save(self, per_person="5", per_day="500", model="claude-haiku-5-5"):
        return self.client.post(reverse("claude"), {"action": "askrip_settings", "askrip_per_person": per_person,
                                                    "askrip_per_day": per_day, "askrip_model": model}, follow=True)


class DefaultTests(SettingsCase):
    def test_without_a_saved_row_it_is_five_a_person_five_hundred_a_day_on_haiku(self):
        chosen = askrip.chosen()
        self.assertEqual((chosen.per_person, chosen.per_day, chosen.model, chosen.per_address),
                         (5, 500, "claude-haiku-5-5", 15))
        self.assertFalse(AskRipSettings.objects.exists())

    def test_reading_it_is_one_query_an_hour_at_most(self):
        askrip.chosen()
        with self.assertNumQueries(0):
            askrip.chosen()

    def test_a_value_out_of_range_or_a_model_no_longer_offered_is_the_default(self):
        AskRipSettings.objects.create(pk=1, per_person=0, per_day=999999, model="claude-opus-4-1")
        askrip.reopen()
        chosen = askrip.chosen()
        self.assertEqual((chosen.per_person, chosen.per_day, chosen.model), (5, 500, "claude-haiku-5-5"))


class PageTests(SettingsCase):
    def test_the_claude_page_shows_the_settings_with_each_models_cost(self):
        self.owner()
        page = self.client.get(reverse("claude"))
        self.assertContains(page, 'id="askrip"')
        self.assertContains(page, 'name="askrip_per_person"')
        self.assertContains(page, 'value="5" required')
        self.assertContains(page, 'name="askrip_per_day"')
        self.assertContains(page, 'value="claude-haiku-5-5" checked')
        for label in ("Claude Haiku 5.5 (cheapest)", "Claude Sonnet 5.5", "Claude Opus 5.5"):
            self.assertContains(page, label)
        self.assertNotContains(page, "Claude Opus 5.5 (recommended)")
        # What its $30 a month covers on each, at the most a question can cost.
        choices = page.context["askrip"]
        self.assertEqual(choices["limit"], judge.JOB_DEFAULT_LIMITS["ask"])
        covers = {model["id"]: model["covers"] for model in choices["models"]}
        self.assertGreater(covers["claude-haiku-5-5"], covers["claude-sonnet-5-5"])
        self.assertGreater(covers["claude-sonnet-5-5"], covers["claude-opus-5-5"])
        self.assertGreater(covers["claude-opus-5-5"], 500)

    def test_saving_changes_the_numbers_and_the_model_at_once(self):
        self.owner()
        askrip.chosen()   # kept in the cache before the save
        page = self.save(per_person="12", per_day="200", model="claude-sonnet-5-5")
        self.assertContains(page, "Ask Rip is saved: 12 questions a person a day, 200 a day across the site, answered "
                                  "by Claude Sonnet 5.5.")
        row = AskRipSettings.objects.get()
        self.assertEqual((row.pk, row.per_person, row.per_day, row.model), (1, 12, 200, "claude-sonnet-5-5"))
        chosen = askrip.chosen()
        self.assertEqual((chosen.per_person, chosen.per_day, chosen.model, chosen.per_address),
                         (12, 200, "claude-sonnet-5-5", 36))
        self.assertContains(page, 'value="claude-sonnet-5-5" checked')

    def test_a_wrong_value_saves_nothing(self):
        self.owner()
        for values in ({"per_person": "0"}, {"per_person": "51"}, {"per_person": "two"}, {"per_day": "0"},
                       {"per_day": "5001"}, {"model": "gpt-5"}, {"model": "claude-opus-4-8"}):
            page = self.save(**values)
            self.assertContains(page, "Nothing was saved.")
        self.assertFalse(AskRipSettings.objects.exists())

    def test_only_staff_can_save(self):
        response = self.client.post(reverse("claude"), {"action": "askrip_settings", "askrip_per_person": "50",
                                                        "askrip_per_day": "5000", "askrip_model": "claude-opus-5-5"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AskRipSettings.objects.exists())


class PerPersonTests(SettingsCase):
    def test_a_person_gets_the_number_the_owner_chose(self):
        AskRipSettings.objects.create(pk=1, per_person=2)
        askrip.reopen()
        self.assertContains(self.client.get(self.box.get_absolute_url()), "You can ask 2 more questions today.")
        self.assertEqual(self.ask("First question?").json()["state"], "answered")
        self.assertEqual(self.ask("Second question?").json()["state"], "answered")
        limit = self.ask("Third question?").json()
        self.assertEqual(limit["state"], "limit")
        self.assertIn("your 2 questions for today.", limit["html"])

    def test_more_than_fifteen_a_person_raises_the_address_backstop_with_it(self):
        AskRipSettings.objects.create(pk=1, per_person=20)
        askrip.reopen()
        for n in range(16):
            self.assertEqual(self.ask(f"Question number {n}?").json()["state"], "answered")

    def test_the_ask_rip_page_says_the_number(self):
        AskRipSettings.objects.create(pk=1, per_person=8)
        askrip.reopen()
        page = self.client.get(reverse("web:ask_rip_about"))
        self.assertEqual(page.context["per_person"], 8)
        self.assertContains(page, "8 questions a day")


class PerDayTests(SettingsCase):
    def test_the_site_stops_at_the_owners_daily_number(self):
        AskRipSettings.objects.create(pk=1, per_day=3)
        askrip.reopen()
        cache.set(askrip.day_key(timezone.now()), 3, 60)
        self.assertEqual(self.ask().json()["state"], "closed")
        self.assertEqual(askrip.closed_words(askrip.work_out_closed(timezone.now())),
                         "Hidden for today: its 3 questions a day were asked. It shows again tomorrow.")

    def test_raising_it_opens_the_box_again_at_once(self):
        AskRipSettings.objects.create(pk=1, per_day=3)
        askrip.reopen()
        cache.set(askrip.day_key(timezone.now()), 3, 60)
        self.assertEqual(askrip.closed_reason(), "day")
        self.owner()
        self.save(per_day="10")
        self.assertEqual(askrip.closed_reason(), "")


class ModelTests(SettingsCase):
    def test_haiku_is_asked_without_a_fallback(self):
        self.ask()
        self.assertEqual(self.fake.sent[0]["model"], "claude-haiku-5-5")
        self.assertEqual(self.beta_sent, [])

    def test_the_chosen_model_answers_and_is_recorded_and_priced(self):
        AskRipSettings.objects.create(pk=1, model="claude-opus-5-5")
        askrip.reopen()
        self.fake.answer = lambda params: SimpleNamespace(
            stop_reason="end_turn", stop_details=None, model=params["model"], id="msg_1",
            content=[SimpleNamespace(type="text", text="Harbour Games has it at £140.00 delivered.")],
            usage=SimpleNamespace(input_tokens=1500, output_tokens=60, cache_creation_input_tokens=0,
                                  cache_read_input_tokens=0, iterations=None))
        self.assertEqual(self.ask().json()["state"], "answered")
        sent = self.beta_sent[0]
        self.assertEqual((sent["model"], sent["thinking"], sent["output_config"]),
                         ("claude-opus-5-5", {"type": "disabled"}, {"effort": "low"}))
        ask = ClaudeAsk.objects.get()
        self.assertEqual(ask.model_asked, "claude-opus-5-5")
        # 1,500 tokens in and 60 out at Opus 5.5's rates.
        self.assertEqual(ask.cost_micros, 1500 * 4 + 60 * 20)

    def test_a_new_model_answers_afresh(self):
        self.ask()
        self.assertEqual(len(self.fake.sent), 1)
        self.ask()   # the same question: from the cache, free
        self.assertEqual(len(self.fake.sent), 1)
        AskRipSettings.objects.create(pk=1, model="claude-sonnet-5-5")
        askrip.reopen()
        self.ask()
        self.assertEqual(len(self.fake.sent), 2)
        self.assertEqual(self.fake.sent[1]["model"], "claude-sonnet-5-5")

    def test_a_dearer_model_reserves_its_worst_case(self):
        haiku = askrip.typical_worst("claude-haiku-5-5")
        opus = askrip.typical_worst("claude-opus-5-5")
        self.assertGreater(opus, 30 * haiku)
        # Opus can hand a refusal to a fallback, so its worst case includes a second answer at the dearest rates.
        self.assertGreater(opus, askrip.usual_micros("claude-opus-5-5"))
        self.assertEqual(haiku, askrip.usual_micros("claude-haiku-5-5"))


class FreshTests(SettingsCase):
    def test_the_claude_page_shows_what_is_saved_even_with_an_older_copy_cached(self):
        self.owner()
        askrip.chosen()   # the defaults, cached
        AskRipSettings.objects.create(pk=1, per_person=9)   # saved some other way, without clearing the cache
        page = self.client.get(reverse("claude"))
        self.assertEqual(page.context["askrip"]["per_person"], 9)
        self.assertEqual(askrip.chosen().per_person, 9)
