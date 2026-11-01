"""Claude checks each new page a shop read makes before it goes live (catalogue/new_pages.py). Winston's Emporium
listed 32 loose Chinese Pikachu cards as "Pikachu Collection 30th Anniversary", and the site made a Collection box
page in English (the owner, 1 Nov 2026: "How did this get through")."""

import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import autopilot, judge, new_pages
from .importers import Offer, apply_offers
from .models import CheckAnswer, ClaudeAsk, ClaudeJudge, Listing, NewPage, Product, Retailer
from .testing import make_game, make_retailer
from .tests_judge import Base, error, usage

PIKACHU = "Pikachu Collection 30th Anniversary"
PHOTO = "https://cdn.shopify.com/s/files/1/1021/0934/8222/files/04279F46.jpg?v=1791294333"


def answer(page, verdict="sealed", confidence="high", language="English", reason="The photo shows a sealed box."):
    body = json.dumps({"page": page, "verdict": verdict, "confidence": confidence, "language": language,
                       "reason": reason})
    return SimpleNamespace(stop_reason="end_turn", stop_details=None, model=new_pages.PAGE_MODEL, id="msg_1",
                           content=[SimpleNamespace(type="text", text=body)], usage=usage(1200, 120))


class PageClient:
    """Answers each question through ``reply(page key, params)``."""

    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply or (lambda page, params: answer(page))
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params):
        self.sent.append(params)
        text = next(block["text"] for block in params["messages"][0]["content"] if block["type"] == "text")
        return self.reply(json.loads(text[len("<evidence>"):-len("</evidence>")])["page"], params)


class NewPageTests(Base):
    def setUp(self):
        super().setUp()
        self.shop = make_retailer("Winston's Emporium", source_type=Retailer.Source.SHOPIFY)
        ClaudeJudge.objects.update_or_create(pk=1, defaults={"monthly_budget_usd": Decimal("20")})

    def read(self, title=PIKACHU, description="All chinese 30 Pikachus + 2 x Pikachus EX", url="https://winstons.example/products/pika"):
        offer = Offer(title=title, url=url, price=Decimal("40.00"), image=PHOTO, vendor="Pokémon",
                      tags=("game:pokemon", "set:30th"), description=description)
        apply_offers(self.shop, [offer], complete=False)
        return Product.objects.get(name=title)

    def run_pages(self, reply=None, **kwargs):
        client = PageClient(reply)
        return client, new_pages.run(client=client, **kwargs)

    def test_while_claude_can_act_a_new_page_waits_off_the_site(self):
        self.switch_on()
        product = self.read()
        self.assertFalse(product.is_active)
        self.assertEqual(product.new_page.status, NewPage.Status.WAITING)
        self.assertEqual(self.client.get(product.get_absolute_url()).status_code, 404)

    def test_while_claude_cannot_act_it_goes_up_at_once_and_is_checked_later(self):
        self.switch_on(may_act=False)
        product = self.read()
        self.assertTrue(product.is_active)
        self.assertEqual(product.new_page.status, NewPage.Status.UNCHECKED)
        # A machine with Claude switched off in its settings keeps no record at all.
        with override_settings(RIPRAPTOR_CLAUDE=False):
            other = self.read(title="Ascended Heroes Elite Trainer Box", url="https://winstons.example/products/etb")
        self.assertTrue(other.is_active)
        self.assertFalse(NewPage.objects.filter(product=other).exists())

    def test_a_sealed_answer_puts_the_page_up_with_undo(self):
        self.switch_on()
        product = self.read(title="Ascended Heroes Elite Trainer Box", description="Nine booster packs and sleeves.")
        client, result = self.run_pages()
        product.refresh_from_db()
        self.assertTrue(product.is_active)
        self.assertEqual(product.new_page.status, NewPage.Status.SEALED)
        self.assertEqual((result.asked, result.published), (1, 1))
        done = CheckAnswer.objects.get(kind=CheckAnswer.Kind.PUBLISH)
        self.assertEqual(done.what, "Put Ascended Heroes Elite Trainer Box on the site")
        self.assertTrue(done.why.startswith("Claude: "))
        # Claude was shown the shop's photo, resized, and the shop's own words.
        params = client.sent[0]
        self.assertEqual(params["model"], "claude-sonnet-5-5")
        picture, text = params["messages"][0]["content"]
        self.assertEqual(picture["source"]["url"], PHOTO + "&width=640")
        self.assertIn("Nine booster packs and sleeves.", text["text"])
        # Undo is the owner saying it is not sealed: off, and it stays off.
        self.assertIn("is off the site", autopilot.undo(done))
        product.refresh_from_db()
        self.assertFalse(product.is_active)
        self.assertEqual((product.new_page.status, product.new_page.by_owner), (NewPage.Status.NOT_SEALED, True))
        self.assertEqual(ClaudeAsk.objects.get().owner_answer, "not_sealed")

    def test_the_pikachu_lot_stays_off_and_never_comes_back(self):
        self.switch_on()
        product = self.read()
        _client, result = self.run_pages(lambda page, params: answer(
            page, "not_sealed", "high", "Simplified Chinese", "The description counts 32 Pikachu cards, a lot of singles."))
        product.refresh_from_db()
        self.assertFalse(product.is_active)
        self.assertEqual(product.new_page.status, NewPage.Status.NOT_SEALED)
        self.assertEqual(result.kept_off, 1)
        self.assertEqual(CheckAnswer.objects.get(kind=CheckAnswer.Kind.NOT_SEALED).what,
                         f"Kept {PIKACHU} off the site: not a sealed product")
        # The shop's next read keeps its listing on the page, off the site, and makes no new page.
        self.read()
        self.assertEqual(Product.objects.filter(name=PIKACHU).count(), 1)
        self.assertEqual(Listing.objects.get(retailer=self.shop).product_id, product.pk)
        self.assertFalse(Product.objects.get(pk=product.pk).is_active)

    def test_unsure_low_or_another_language_waits_for_the_owner(self):
        self.switch_on()
        cases = [("unsure", "medium", "English"), ("sealed", "low", "English"), ("sealed", "high", "Simplified Chinese")]
        for i, (verdict, confidence, language) in enumerate(cases):
            with self.subTest(verdict=verdict, confidence=confidence, language=language):
                product = self.read(title=f"Pikachu Collection {i}", url=f"https://winstons.example/products/p{i}")
                self.run_pages(lambda page, params: answer(page, verdict, confidence, language))
                product.refresh_from_db()
                self.assertFalse(product.is_active)
                self.assertEqual(product.new_page.status, NewPage.Status.UNSURE)
        page = NewPage.objects.get(product__name="Pikachu Collection 2")
        self.assertEqual(page.reason, "Claude reads the cards as Simplified Chinese, and the page says English")
        # On Things to check, with a button each way.
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        html = self.client.get(reverse("checks")).content.decode()
        self.assertIn("New pages to check (3)", html)
        self.assertIn('value="Put it on the site"', html)
        response = self.client.post(reverse("checks"), {"action": "page_up", "page": page.pk}, follow=True)
        self.assertContains(response, "Pikachu Collection 2 is on the site.")
        page.refresh_from_db()
        self.assertEqual((page.status, page.by_owner, page.product.is_active), (NewPage.Status.SEALED, True, True))

    def test_a_photo_anthropic_will_not_take_is_left_out_next_time(self):
        import anthropic

        self.switch_on()
        product = self.read()
        self.run_pages(lambda page, params: (_ for _ in ()).throw(error(anthropic.BadRequestError, 400, "invalid_request_error",
                                                                          "Could not fetch the image")))
        page = NewPage.objects.get(product=product)
        self.assertTrue(page.no_picture)
        self.assertEqual(page.status, NewPage.Status.WAITING)
        client, _ = self.run_pages()
        self.assertEqual([block["type"] for block in client.sent[0]["messages"][0]["content"]], ["text"])

    def test_pages_go_up_unchecked_when_claude_cannot_act_and_are_checked_later(self):
        self.switch_on()
        product = self.read()
        ClaudeJudge.objects.filter(pk=1).update(enabled=False)
        _client, result = self.run_pages()
        product.refresh_from_db()
        self.assertTrue(product.is_active)
        self.assertEqual((product.new_page.status, result.released, result.asked), (NewPage.Status.UNCHECKED, 1, 0))
        # Back on: Claude checks it and takes it off.
        ClaudeJudge.objects.filter(pk=1).update(enabled=True)
        self.run_pages(lambda page, params: answer(page, "not_sealed", "high"))
        product.refresh_from_db()
        self.assertFalse(product.is_active)
        self.assertEqual(CheckAnswer.objects.get().what, f"Took {PIKACHU} off the site: not a sealed product")

    def test_a_page_waiting_too_long_goes_up(self):
        self.switch_on()
        product = self.read()
        NewPage.objects.filter(product=product).update(created_at=timezone.now() - new_pages.HOLD_LIMIT - timedelta(minutes=1))
        self.assertEqual(new_pages.release(), 1)
        product.refresh_from_db()
        self.assertTrue(product.is_active)

    def test_an_answer_that_cannot_be_read_twice_waits_for_the_owner(self):
        self.switch_on()
        product = self.read()
        broken = lambda page, params: SimpleNamespace(stop_reason="end_turn", stop_details=None, model=new_pages.PAGE_MODEL,
                                                      content=[SimpleNamespace(type="text", text="no")], usage=usage())
        for _ in range(new_pages.RETRIES + 1):
            self.run_pages(broken)
        page = NewPage.objects.get(product=product)
        self.assertEqual(page.status, NewPage.Status.UNSURE)
        self.assertFalse(Product.objects.get(pk=product.pk).is_active)

    def test_the_monthly_limit_stops_the_run(self):
        self.switch_on()
        product = self.read()
        # Reached during the run: nothing more is sent, and the page keeps waiting.
        with mock.patch.object(judge, "spent_this_month", side_effect=[0, 19_999_000]):
            client, result = self.run_pages()
        self.assertEqual(client.sent, [])
        self.assertIn("monthly limit", result.note)
        self.assertEqual(NewPage.objects.get(product=product).status, NewPage.Status.WAITING)
        # Reached already: Claude cannot act, so the page goes up unchecked rather than waiting a month.
        with mock.patch.object(judge, "spent_this_month", return_value=19_999_000):
            client, result = self.run_pages()
        self.assertEqual((client.sent, result.released), ([], 1))
        self.assertTrue(Product.objects.get(pk=product.pk).is_active)

    def test_the_command_reports_the_run(self):
        from io import StringIO

        out = StringIO()
        with override_settings(RIPRAPTOR_CLAUDE=False):
            call_command("check_new_pages", stdout=out)
        self.assertIn("New pages: asked 0", out.getvalue())

    def test_what_claude_sorts_lists_new_pages(self):
        self.switch_on()
        self.read()
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        html = self.client.get(reverse("checks")).content.decode()
        job = html[html.index('data-job="pages"'):]
        job = job[:job.index("</li>")]
        self.assertIn("<b>New pages</b>", job)
        self.assertIn("Left 1: 1 for Claude.", job)


class WithoutClaudeTests(TestCase):
    def test_nothing_changes_for_a_shop_read_on_a_machine_without_claude(self):
        make_game()
        shop = make_retailer("Harbour Games")
        apply_offers(shop, [Offer(title="Pokemon Ascended Heroes Elite Trainer Box", url="https://h.example/etb",
                                  price=Decimal("49.99"))], complete=False)
        product = Product.objects.get()
        self.assertTrue(product.is_active)
        self.assertFalse(NewPage.objects.exists())
