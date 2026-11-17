"""Pages that are not products (the audit of 17 Nov 2026): event entries, subscriptions, multi-buy offers, deposits,
merchandise and single cards stood among the product pages. The owner chose that the ones the rules are sure of
come down by themselves ("Approve, remove sure ones"), that traded-in boxes and mystery packs wait for him, and of
the rest: "Claude api should be able to remove these for me as long as it doesn't cost too much"."""

import json
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from . import autopilot, judge, new_pages, not_products
from .classify import NOT_SEALED, classify, tidy_name
from .models import CheckAnswer, ClaudeAsk, ClaudeJob, ClaudeJudge, NewPage, Product
from .testing import make_listing, make_product, make_retailer
from .tests_judge import Base, usage

# Names of pages that were live on 17 Nov 2026, as the shops wrote them.
NOT_PRODUCTS = (
    "Pack Club Monthly Subscription",
    "Anime Club Subscription Monthly Booster Packs",
    "Buy 12 Booster Packs Get 5 Off",
    "Buy 2 Marvel Super Heroes Play Booster Boxes Save 10",
    "Buy Both Marvel Super Heroes Scene Boxes for 45",
    "Hertfordshire Pokemon Club December 2026 Entry (No Pack)",
    "Star Trek Draft Night (Per Player)",
    "Locals August OP13 Booster Prizing Monday 3rd August",
    "Northumbrian Tin Soldier One Piece Store Treasure Cup December 2026",
    "30th Celebration Ultra Premium Collection Day Night Pair 20 Deposit 600 Invoiced Before Dispatch",
    "Star Wars The Vintage Collection Luminara Unduli",
    "Star Wars The Black Series Star Wars The Phantom Menace 3 Pack",
    "De Agostini Diecast Star Wars Figurine Collection Mace Windu",
    "Set of 3 Tin Signs",
    "Harry Potter Hufflepuff Baseball Cap",
    "Star Wars The Force Awakens BB-8 Droid Figural Crossbody Bag",
    "Star Wars BB-8 Cushion",
    "Chelsea FC Calendar Diary Musical Gift Box 2027",
    "Star Wars Pin Badges Series 1 Display 24",
    "Nintendo Switch 2 Console Pokemon Pokopia Bundle",
    "Early Days Collection Nintendo Switch Game Cartridge",
    "Bandai Namco PS5 One Piece Odyssey Limited Edition Video Game Bundle",
    "Squaroes MTG Edge of Eternities Tezzeret",
    "Pop Bobble Head Star Wars Valentines Day Exclusive Diamond Glitter 4 Pack",
    "Fog King OTS Tournament Pack 2 Ultimate Rare Spanish Excellent Normal",
    "Kabutops Legendary Collection Rare English Good Normal",
    "Dark Dust Spirit Structure Deck Zombie Madness Common English Good 1st Edition",
    "Blue-Eyes White Dragon V2 Starter Deck Kaiba Reloaded English Light Played Normal",
    "25th Anniversary Rarity Collection RA01-EN001 Lava Golem",
    "CBB1C Chinese Shiny 6 Card Lot Quaxly Fuecoco Sprigatito",
)
# Products whose names come close to a rule and must stay.
PRODUCTS = (
    "Topps Match Attax UCC 2026/27 Mega Tin New Entries",
    "Shadowverse Evolve Entry Deck",
    "Star Trek Draft Night",
    "Scarlet & Violet 151 Mini Tin Magneton (2 Booster Packs, 1 Coin)",
    "Holiday Calendar 2025",
    "Premium Tournament Collection Cyrus",
    "League Battle Deck Gardevoir ex",
    "Mega Evolution Pitch Black Build & Battle Box",
    "Topps Stadium Club Chrome UEFA Champions League 2025/26 Hobby Box",
    "25th Anniversary Rarity Collection II Booster Box",
)


def answer(page, verdict="not_product", confidence="high", reason="The shop sells a place at its monthly event."):
    body = json.dumps({"page": page, "verdict": verdict, "confidence": confidence, "reason": reason})
    return SimpleNamespace(stop_reason="end_turn", stop_details=None, model=new_pages.PAGE_MODEL, id="msg_1",
                           content=[SimpleNamespace(type="text", text=body)], usage=usage(900, 150))


class Client:
    """Answers each question through ``reply(page key, params)``."""

    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply or (lambda page, params: answer(page))
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params):
        self.sent.append(params)
        text = params["messages"][0]["content"][0]["text"]
        return self.reply(json.loads(text[len("<evidence>"):-len("</evidence>")])["page"], params)


class RuleTests(Base):
    def test_what_the_rules_are_sure_of_is_no_sealed_product(self):
        for name in NOT_PRODUCTS:
            with self.subTest(name):
                self.assertIsNotNone(NOT_SEALED.search(name))
                # A shop's read refuses it, whatever game the shop files it under.
                self.assertIsNone(classify(name, "Booster Box", "Pokemon", ("pokemon",), Decimal("20.00")))

    def test_products_whose_names_come_close_stay(self):
        for name in PRODUCTS:
            with self.subTest(name):
                self.assertIsNone(NOT_SEALED.search(name))

    def test_buy_in_front_of_one_piece_is_a_shops_wording_not_an_offer(self):
        self.assertEqual(tidy_name("Buy One Piece OP-13 Booster Box Carrying On His Will 2026"),
                         "One Piece OP-13 Booster Box Carrying On His Will")
        # An offer keeps its words, and is refused as no product.
        self.assertEqual(tidy_name("Buy One Get One Free Booster Pack"), "Buy One Get One Free Booster Pack")
        self.assertIsNone(NOT_SEALED.search("One Piece OP-13 Booster Box Carrying On His Will"))

    def test_the_hourly_tidy_takes_such_a_page_down_and_its_address_says_gone(self):
        shop = make_retailer("Tollgate TCG")
        gone = make_product(self.set, name="Pack Club Monthly Subscription", product_type="booster_pack")
        stays = make_product(self.set, name="Shadowverse Evolve Entry Deck", product_type="starter_deck")
        for product in (gone, stays):
            make_listing(product, shop, url=f"https://tollgate.example/{product.slug}")
        out = StringIO()
        call_command("tidy_catalogue", stdout=out)
        self.assertIn("removed (not sealed): Pack Club Monthly Subscription", out.getvalue())
        self.assertFalse(Product.objects.filter(pk=gone.pk).exists())
        self.assertTrue(Product.objects.filter(pk=stays.pk).exists())
        self.assertEqual(self.client.get(f"/products/{gone.slug}/").status_code, 410)


class SuspectTests(Base):
    def setUp(self):
        super().setUp()
        self.shop = make_retailer("Tollgate TCG")
        ClaudeJudge.objects.update_or_create(pk=1, defaults={"monthly_budget_usd": Decimal("20")})

    def page(self, name, kind="booster_pack", title=None, description=""):
        product = make_product(self.set, name=name, product_type=kind)
        make_listing(product, self.shop, url=f"https://tollgate.example/{product.slug}", title=title or name,
                     description=description)
        return product

    def run_job(self, reply=None, **kwargs):
        client = Client(reply)
        return client, not_products.run(client=client, **kwargs)

    def test_who_is_a_suspect(self):
        self.assertEqual(not_products.suspicion("Traded-In Pokemon Destined Rivals Booster Box"),
                         ("owner", "It is traded-in stock, and the site lists new products"))
        self.assertEqual(not_products.suspicion("Mystery Japanese/Chinese Booster Pack")[0], "owner")
        # Magic's Mystery Booster is a set.
        self.assertIsNone(not_products.suspicion("Mystery Booster Box"))
        self.assertEqual(not_products.suspicion("One Piece Heroines Battle Vol.2"),
                         ("claude", "Its name has no word for a kind of product"))
        self.assertEqual(not_products.suspicion("Chinese Gem Pack Badge Set"),
                         ("claude", "Its name has a word merchandise uses"))
        for name in ("Surging Sparks Booster Box", "Reign of Jafar Illumineer's Quest", "Star Trek Draft Night",
                     "Holiday Calendar 2025", "Prismatic Evolutions Elite Trainer Box", "Pokemon Trading Card Game Classic"):
            self.assertIsNone(not_products.suspicion(name), name)

    def test_claude_takes_down_what_it_is_sure_is_no_product_with_undo(self):
        self.switch_on()
        event = self.page("One Piece Heroines Battle Vol.2", description="Entry to our in-store event on 6 December.")
        client, result = self.run_job()
        self.assertEqual((result.asked, result.taken_off), (1, 1))
        event.refresh_from_db()
        self.assertFalse(event.is_active)
        self.assertEqual(self.client.get(event.get_absolute_url()).status_code, 410)
        # The shop's own words went, and no photo: the words say what an event is.
        params = client.sent[0]
        self.assertEqual((params["model"], params["output_config"]["effort"]), ("claude-sonnet-5-5", "low"))
        self.assertEqual([block["type"] for block in params["messages"][0]["content"]], ["text"])
        self.assertIn("Entry to our in-store event on 6 December.", params["messages"][0]["content"][0]["text"])
        done = CheckAnswer.objects.get(kind=CheckAnswer.Kind.NOT_SEALED)
        self.assertEqual(done.what, "Took One Piece Heroines Battle Vol.2 off the site: not a product")
        self.assertEqual(done.why, "Claude: The shop sells a place at its monthly event.")
        # A shop's later reads keep its listing on this page, so it never comes back as a new one.
        self.assertEqual(event.new_page.status, NewPage.Status.NOT_SEALED)
        # Undo is the owner saying it is a product: back on the site, and never asked about again.
        self.assertEqual(autopilot.undo(done), "Undone: One Piece Heroines Battle Vol.2 is on the site.")
        event.refresh_from_db()
        self.assertTrue(event.is_active)
        self.assertEqual(ClaudeAsk.objects.get().owner_answer, "product")
        self.assertNotIn(event.pk, not_products.suspects())
        client, result = self.run_job()
        self.assertEqual(client.sent, [])

    def test_a_page_claude_is_fairly_sure_is_a_product_is_cleared(self):
        self.switch_on()
        self.page("Black & White Noble Victories", title="Pokemon Black & White Noble Victories Booster Pack")
        _, result = self.run_job(lambda page, params: answer(page, "product", "medium", "The shop's title names a booster pack."))
        self.assertEqual((result.asked, result.cleared), (1, 1))
        self.assertEqual(not_products.for_owner(), {"count": 0, "rows": []})
        client, _ = self.run_job()
        self.assertEqual(client.sent, [])

    def test_only_what_nothing_could_change_takes_a_live_page_down(self):
        self.switch_on()
        fairly = self.page("Chinese Gem Pack Badge Set")
        unsure = self.page("Team Rocket Attache Briefcase", kind="collection_box")
        replies = {f"page-{fairly.pk}": answer(f"page-{fairly.pk}", "not_product", "medium", "It looks like badges with no cards."),
                   f"page-{unsure.pk}": answer(f"page-{unsure.pk}", "unsure", "low", "The words do not say what is inside.")}
        _, result = self.run_job(lambda page, params: replies[page])
        self.assertEqual((result.asked, result.taken_off, result.for_owner), (2, 0, 2))
        self.assertTrue(Product.objects.get(pk=fairly.pk).is_active)
        waiting = not_products.for_owner()
        self.assertEqual(waiting["count"], 2)
        self.assertEqual({row.name: row.claude.reason for row in waiting["rows"]},
                         {"Chinese Gem Pack Badge Set": "It looks like badges with no cards.",
                          "Team Rocket Attache Briefcase": "The words do not say what is inside."})
        # The same words are never asked about twice.
        client, _ = self.run_job()
        self.assertEqual(client.sent, [])

    def test_traded_in_and_mystery_items_are_the_owners_alone(self):
        self.switch_on()
        traded = self.page("Traded-In Pokemon Destined Rivals Booster Box", kind="booster_box")
        mystery = self.page("Mystery Japanese/Chinese Booster Pack")
        client, result = self.run_job()
        self.assertEqual((client.sent, result.asked), ([], 0))
        waiting = not_products.for_owner()
        self.assertEqual([row.pk for row in waiting["rows"]], [traded.pk, mystery.pk])
        self.assertTrue(all(row.claude is None for row in waiting["rows"]))

    def test_the_job_has_its_own_switch_and_a_limit_of_three_dollars(self):
        self.switch_on()
        self.assertEqual(judge.job_settings()["notprod"].monthly_budget_usd, Decimal("3.00"))
        suspect = self.page("One Piece Heroines Battle Vol.2")
        ClaudeAsk.objects.create(kind=ClaudeAsk.Kind.NOT_PRODUCT, row_key="notpro:0", fingerprint="f",
                                 model_asked="claude-sonnet-5-5", effort="low", cost_micros=2_999_000,
                                 outcome=ClaudeAsk.Outcome.ANSWERED)
        client, result = self.run_job()
        self.assertEqual(client.sent, [])
        self.assertEqual(result.note, "Not a product reached its own monthly limit of $3.00, set on the Claude page")
        # Switched off, or with Claude unable to act, the suspects wait for the owner, on the site.
        ClaudeJob.objects.create(name="notprod", on=False)
        client, result = self.run_job()
        self.assertEqual(client.sent, [])
        self.assertEqual(result.note, "Not a product is switched off on the Claude page, so suspects wait for you")
        self.assertTrue(Product.objects.get(pk=suspect.pk).is_active)
        self.assertEqual([row.pk for row in not_products.for_owner()["rows"]], [suspect.pk])

    def test_a_page_is_asked_about_at_most_twice_ever(self):
        self.switch_on()
        suspect = self.page("One Piece Heroines Battle Vol.2")
        unsure = lambda page, params: answer(page, "unsure", "low", "The words do not say.")   # noqa: E731
        self.run_job(unsure)
        Product.objects.filter(pk=suspect.pk).update(name="One Piece Heroines Battle Vol.2 December")
        client, _ = self.run_job(unsure)
        self.assertEqual(len(client.sent), 1)   # its words changed: asked once more
        Product.objects.filter(pk=suspect.pk).update(name="One Piece Heroines Battle Vol.2 January")
        client, _ = self.run_job(unsure)
        self.assertEqual(client.sent, [])

    def test_a_page_still_with_its_first_check_or_settled_by_the_owner_is_left(self):
        self.switch_on()
        checking = self.page("One Piece Heroines Battle Vol.2")
        NewPage.objects.create(product=checking, status=NewPage.Status.UNCHECKED)
        kept = self.page("Chinese Gem Pack Badge Set")
        NewPage.objects.create(product=kept, status=NewPage.Status.SEALED, by_owner=True)
        self.assertEqual(not_products.suspects(), {})
        client, _ = self.run_job()
        self.assertEqual(client.sent, [])

    def test_the_owner_answers_from_things_to_check(self):
        traded = self.page("Traded-In Pokemon Destined Rivals Booster Box", kind="booster_box")
        badges = self.page("Chinese Gem Pack Badge Set")
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        html = self.client.get(reverse("checks")).content.decode()
        self.assertIn("May not be products (2)", html)
        self.assertIn("It is traded-in stock, and the site lists new products.", html)
        self.assertIn('value="It is a product"', html)
        response = self.client.post(reverse("checks"), {"action": "notprod_keep", "product": badges.pk}, follow=True)
        self.assertContains(response, "Chinese Gem Pack Badge Set stays on the site, and is not asked about again.")
        self.assertContains(response, "May not be products (1)")
        response = self.client.post(reverse("checks"), {"action": "notprod_off", "product": traded.pk}, follow=True)
        self.assertContains(response, "Traded-In Pokemon Destined Rivals Booster Box is off the site.")
        self.assertContains(response, "May not be products (0)")
        traded.refresh_from_db()
        self.assertFalse(traded.is_active)
        self.assertEqual((traded.new_page.status, traded.new_page.by_owner), (NewPage.Status.NOT_SEALED, True))
        self.assertEqual(self.client.get(traded.get_absolute_url()).status_code, 410)
        # A page that is no longer on the list is not answered.
        response = self.client.post(reverse("checks"), {"action": "notprod_off", "product": self.product.pk}, follow=True)
        self.assertContains(response, "That page has changed since the page loaded.")
        self.assertTrue(Product.objects.get(pk=self.product.pk).is_active)

    def test_the_claude_page_lists_the_job_with_its_limit(self):
        self.client.force_login(get_user_model().objects.create_superuser("ben", "ben@example.com", "pw"))
        response = self.client.get(reverse("claude"))
        self.assertContains(response, "Not a product")
        job = next(job for job in response.context["jobs"] if job["name"] == "notprod")
        self.assertEqual((job["on"], job["cap"]), (True, Decimal("3.00")))

    def test_the_ten_minute_command_runs_it_after_the_new_pages(self):
        self.switch_on()
        out = StringIO()
        call_command("check_new_pages", stdout=out)
        self.assertIn("New pages: ", out.getvalue())
        self.assertIn("Not a product: asked 0, taken off 0, cleared 0, for you 0, spent $0.00", out.getvalue())

    def test_the_new_page_check_names_the_same_things(self):
        for words in ("a place at an event or tournament", "a subscription", "a deposit", "an offer on several items",
                      "merchandise with no cards in it"):
            self.assertIn(words, new_pages.SYSTEM_PROMPT)
