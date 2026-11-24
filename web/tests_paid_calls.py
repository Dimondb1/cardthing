"""Every request sent to Anthropic under Ask Rip's job counts toward the owner's number a day, whatever it comes back
with: a question, a list, a photo of a list, a voice search or a search-bar reading. A request with nothing to show
for it gives the visitor's own question back, but the address's backstop and the site's day keep it, so blank photos
or voice searches cannot run Ask Rip's monthly limit down."""

import io
from types import SimpleNamespace

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalogue.models import AskRipSettings, ClaudeAsk, ClaudeJob
from web import askrip

from .tests_rip_list import URL, ListCase, items

VOICE_URL = reverse("web:voice_search")
ADDRESS = "203.0.113.7"


def small_photo(n):
    """A different small JPEG each time, so none is answered from the cache."""
    out = io.BytesIO()
    Image.new("RGB", (200 + n, 120), "white").save(out, "JPEG")
    return SimpleUploadedFile(f"list-{n}.jpg", out.getvalue(), content_type="image/jpeg")


class PaidCallCase(ListCase):
    def per_day(self, n, per_person=askrip.PER_PERSON):
        AskRipSettings.objects.create(pk=1, per_day=n, per_person=per_person)
        askrip.reopen()

    def say(self, words, **extra):
        return self.client.get(VOICE_URL, {"q": words, **extra}, REMOTE_ADDR=ADDRESS)

    def send_photo(self, n):
        return self.client.post(URL, {"wants": "", "photo": small_photo(n)}, REMOTE_ADDR=ADDRESS)

    def paid_today(self):
        return cache.get(askrip.paid_key(timezone.now())) or 0


class DayTests(PaidCallCase):
    def test_voice_searches_count_toward_the_day(self):
        self.per_day(2)
        for words in ("surging sparks booster box", "prismatic evolutions bundle", "obsidian flames booster box"):
            self.say(words)
        self.assertEqual(len(self.fake.sent), 2)

    def test_search_bar_readings_count_toward_the_day(self):
        self.per_day(2)
        for words in ("cheap korean booster boxes", "cheap japanese booster boxes", "cheap english booster boxes"):
            self.say(words, via="box")
        self.assertEqual(len(self.fake.sent), 2)

    def test_photos_with_nothing_to_buy_count_toward_the_day(self):
        self.per_day(2)
        self.fake.answer = items()
        states = [self.made(self.send_photo(n))[1] for n in range(3)]
        self.assertEqual(len(self.fake.sent), 2)
        self.assertEqual(states[-1], "closed")

    def test_once_the_day_is_used_ask_rip_says_it_is_closed_for_today(self):
        self.per_day(2)
        self.say("surging sparks booster box")
        self.say("prismatic evolutions bundle")
        askrip.reopen()
        self.assertEqual(askrip.work_out_closed(timezone.now()), "day")

    def test_an_answer_from_the_cache_is_free_and_not_counted(self):
        self.per_day(2)
        self.say("surging sparks booster box")
        self.say("surging sparks booster box")
        self.assertEqual(self.paid_today(), 1)

    def test_the_owners_requests_are_not_counted(self):
        from django.contrib.auth import get_user_model

        self.per_day(1)
        owner = get_user_model().objects.create_user("owner", "o@example.com", "pw", is_staff=True)
        self.client.force_login(owner)
        for words in ("surging sparks booster box", "prismatic evolutions bundle", "obsidian flames booster box"):
            self.say(words)
        self.assertEqual(len(self.fake.sent), 3)
        self.assertEqual(self.paid_today(), 0)


class BackstopTests(PaidCallCase):
    def test_photos_with_nothing_to_buy_stop_at_the_addresss_backstop(self):
        self.per_day(askrip.PER_DAY_MOST, per_person=1)
        self.fake.answer = items()
        backstop = askrip.chosen().per_address
        states = [self.made(self.send_photo(n))[1] for n in range(backstop + 2)]
        self.assertEqual(len(self.fake.sent), backstop)
        self.assertEqual(states[-1], "limit")

    def test_a_photo_with_nothing_to_buy_still_gives_the_visitor_their_question_back(self):
        self.fake.answer = items()
        self.send_photo(1)
        address = SimpleNamespace(META={"REMOTE_ADDR": ADDRESS})
        self.assertEqual(askrip.address_count(address, 0)[0], 1)
        self.assertContains(self.client.get(URL, REMOTE_ADDR="198.51.100.9"), "You can ask 5 more questions today.")

    def test_the_day_is_kept_for_a_request_that_found_nothing(self):
        self.fake.answer = items()
        self.send_photo(1)
        self.assertEqual(self.paid_today(), 1)
        self.assertEqual(cache.get(askrip.day_key(timezone.now())), 1)


class EmptyLimitTests(PaidCallCase):
    def test_with_its_own_limit_emptied_ask_rip_still_stops_at_the_servers_ceiling(self):
        from django.test import override_settings

        ClaudeJob.objects.create(name="ask", monthly_budget_usd=None)
        ClaudeAsk.objects.create(kind=ClaudeAsk.Kind.ASK, row_key="ask:1", fingerprint="f", model_asked=askrip.MODEL,
                                 effort="low", cost_micros=5_000_000, outcome=ClaudeAsk.Outcome.ANSWERED,
                                 asked_at=timezone.now())
        askrip.reopen()
        with override_settings(RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD=5):
            self.assertEqual(askrip.work_out_closed(timezone.now()), "job")
            self.say("surging sparks booster box")
        self.assertEqual(self.fake.sent, [])


class ClaudePageTests(PaidCallCase):
    def test_the_claude_page_says_every_request_counts(self):
        self.assertEqual(askrip.closed_words("day"), f"Hidden for today: its {askrip.chosen().per_day} requests a day were used. "
                                "It shows again tomorrow.")
