"""What Rip couldn't find (web/rip_wants.py): the owner's page of what people asked Rip for that the site does not
have. The owner, 15 Nov 2026, picking it from the suggestions after small talk."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import ClaudeAsk
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


def browse(**chosen):
    """A general request as rip_list.read_browse keeps it."""
    return {"type": "", "game": "", "set": "", "sort": "", "price": "", "max_price": 0, "language": "", "rip": False,
            "in_stock": True, **chosen}


def asked(words, items=(), general=None, chat="", row_key="list", days_ago=0, photo=False,
          outcome=ClaudeAsk.Outcome.ANSWERED):
    """One of Rip's requests as rip_list.send records it."""
    return ClaudeAsk.objects.create(
        kind=ClaudeAsk.Kind.ASK, row_key=row_key, fingerprint=words[:60] or "photo", model_asked="claude-haiku-5-5",
        effort="low", outcome=outcome, asked_at=timezone.now() - timedelta(days=days_ago),
        evidence={"list": words, **({"photo": True} if photo else {})},
        set_answer={"items": [{"search": search, "game": game, "quantity": 1} for search, game in items],
                    "browse": general, "chat": chat})


@override_settings(RIPRAPTOR_TWO_STEP_REQUIRED=False)
class RipWantsTests(TestCase):
    def setUp(self):
        product_set = make_set(make_game(), name="Surging Sparks", slug="surging-sparks", code="SSP")
        self.box = make_product(product_set, name="Surging Sparks Booster Box", slug="surging-sparks-booster-box",
                                product_type="booster_box")
        make_listing(self.box, make_retailer("Harbour Games"), price="140.00")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="x"))
        self.url = reverse("rip_wants")

    def page(self, **query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        return response.content.decode()

    def part(self, html, heading):
        start = html.index(f"<h2>{heading}</h2>")
        return html[start:html.index("</section>", start)]

    def test_products_the_site_does_not_have_the_most_asked_first(self):
        asked("2 black bolt etbs", items=[("Black Bolt Elite Trainer Box", "pokemon")])
        asked("black bolt etb please", items=[("Black Bolt Elite Trainer Box", "pokemon")], row_key="search")
        asked("korean 151 box", items=[("Korean 151 Booster Box", "pokemon")], row_key="voice")
        asked("a surging sparks box", items=[("Surging Sparks Booster Box", "pokemon")])
        asked("", items=[("Mega Evolution Tin", "")], photo=True)
        part = self.part(self.page(), "Products the site does not have")
        self.assertIn(f'<a href="{reverse("web:search")}?q=Black%20Bolt%20Elite%20Trainer%20Box&amp;game=pokemon">'
                      "Black Bolt Elite Trainer Box</a>", part)
        self.assertIn('<td data-label="Times">2</td>', part)
        self.assertIn("black bolt etb please", part)   # the latest words for it
        self.assertLess(part.index("Black Bolt"), part.index("Korean 151 Booster Box"))
        self.assertIn('<td data-label="Game">Pokémon</td>', part)
        self.assertIn("A photo", part)
        self.assertNotIn("Surging Sparks Booster Box", part)   # the site has it

    def test_general_requests_with_nothing_in_stock(self):
        korean = browse(type="elite_trainer_box", game="pokemon", language="ko", sort="price")
        asked("cheap korean etbs", general=korean)
        asked("korean etbs", general=korean, row_key="search")
        asked("booster boxes", general=browse(type="booster_box"))   # in stock: not shown
        asked("etbs under 5 quid", general=browse(type="elite_trainer_box", max_price=5))
        part = self.part(self.page(), "General requests with nothing in stock")
        self.assertIn(">Elite Trainer Box, Pokémon, Korean</a>", part)
        self.assertIn('href="/search/?type=elite_trainer_box&amp;game=pokemon&amp;sort=price&amp;lang=ko&amp;in_stock=1"',
                      part)
        self.assertIn('<td data-label="Times">2</td>', part)
        self.assertIn(">Elite Trainer Box, under £5</a>", part)
        self.assertNotIn(">Booster Box<", part)

    def test_what_rip_could_not_follow_newest_first_and_never_small_talk(self):
        asked("asdf qwerty", days_ago=2)
        asked("the thing from the advert", row_key="voice")
        asked("hello rip", chat="Chirp. Hello to you too.")
        html = self.page()
        part = self.part(html, "What Rip could not follow")
        self.assertLess(part.index("the thing from the advert"), part.index("asdf qwerty"))
        self.assertIn('<td data-label="Where">Spoken or Ask popup</td>', part)
        self.assertNotIn("hello rip", part)
        self.assertIn("Rip read 3 requests in the last 30 days, 1 of them small talk.", html)

    def test_only_the_period_chosen_and_only_rips_answered_requests(self):
        asked("old black bolt etb", items=[("Black Bolt Elite Trainer Box", "pokemon")], days_ago=40)
        asked("is it good value", row_key="ask:1")   # a product page's question
        asked("failed one", items=[("Phantasmal Flames Booster Box", "pokemon")], outcome=ClaudeAsk.Outcome.ERROR)
        html = self.page()
        self.assertIn("Everything Rip named is on the site.", html)
        self.assertIn("Rip followed every request.", html)
        self.assertIn("Rip read 0 requests", html)
        self.assertIn("Black Bolt Elite Trainer Box", self.page(days=90))
        self.assertIn("<strong>Last 30 days</strong>", html)
        self.assertIn('<a href="?days=90">Last 90 days</a>', html)
        self.assertIn("<strong>Last 30 days</strong>", self.page(days=12))   # only the periods offered

    def test_a_few_queries_however_many_requests(self):
        for n in range(30):
            asked(f"black bolt etb {n}", items=[("Black Bolt Elite Trainer Box", "pokemon")],
                  general=browse(type="tin", language="ja"))
        with CaptureQueriesContext(connection) as queries:
            self.page()
        self.assertLess(len(queries), 20)

    def test_staff_only_and_on_the_admin_home(self):
        self.assertContains(self.client.get(reverse("admin:index")), f'<a href="{self.url}">')
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
