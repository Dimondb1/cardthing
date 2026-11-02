"""The RRP under the price, and Claude finding one where nobody has (the owner, 2 Nov 2026: "I'm not going to have time
to add these rrps. Can we get Claude to do it"). Claude's figure is kept only when a quote it cited, from the
publisher's site or a UK shop the owner added, calls it the RRP of this product. No test touches the network."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

from django.core.management import call_command
from django.utils import timezone

from . import autopilot, fair_price, judge, rrp_finder
from .models import CheckAnswer, ClaudeAsk, DailyPageView, Listing, Product
from .testing import make_listing, make_product, make_retailer
from .tests_judge import Base, FakeClient
from .tests_set_judge import searched

Ask = ClaudeAsk
ETB = "Surging Sparks Elite Trainer Box"
SHOP_PAGE = "https://www.harbour-games.example/p/surging-sparks-etb"
SHOP_TITLE = "Pokemon TCG Scarlet & Violet Surging Sparks Elite Trainer Box | Harbour Games"
SHOP_SAYS = "RRP: £54.99 Our price: £49.99"
PUBLISHER_PAGE = "https://www.pokemon.com/uk/pokemon-tcg/surging-sparks"
PUBLISHER_SAYS = "The Surging Sparks Elite Trainer Box has a recommended retail price of £54.99."
CENTER_PAGE = "https://www.pokemoncenter.com/en-gb/product/10-10037-118/surging-sparks-elite-trainer-box"
CENTER_TITLE = "Pokémon TCG: Scarlet & Violet-Surging Sparks Elite Trainer Box | Pokémon Center Official Site"


def rrp_reply(row, found="found", rrp="54.99", confidence="high", cited=((SHOP_PAGE, SHOP_TITLE, SHOP_SAYS),),
              source=SHOP_PAGE, searches=1, model=rrp_finder.RRP_MODEL):
    """A reply as Anthropic sends it with web search on: the searches, Claude's text citing them, and settle_rrp."""
    content = []
    for n in range(searches):
        content.append(SimpleNamespace(type="server_tool_use", id=f"srv{n}", name="web_search", input={"query": "rrp"}))
        content.append(SimpleNamespace(type="web_search_tool_result", tool_use_id=f"srv{n}", content=[
            SimpleNamespace(type="web_search_result", url=url, title=title, encrypted_content="x", page_age="")
            for url, title, _ in cited]))
    content.append(SimpleNamespace(type="text", text="The page says:", citations=[
        SimpleNamespace(type="web_search_result_location", url=url, title=title, cited_text=text, encrypted_index="y")
        for url, title, text in cited]))
    content.append(SimpleNamespace(type="tool_use", id="toolu_1", name="settle_rrp", input={
        "row": row, "found": found, "confidence": confidence, "rrp_gbp": rrp, "source": source,
        "reason": "The shop's page gives the RRP of this box."}))
    return SimpleNamespace(stop_reason="tool_use", stop_details=None, model=model, id="msg_rrp", content=content,
                           usage=searched(searches))


class RRPBase(Base):
    def setUp(self):
        super().setUp()
        self.etb = make_product(self.set, name=ETB, slug="surging-sparks-etb", product_type="elite_trainer_box")
        self.harbour = make_retailer("Harbour Games")
        make_listing(self.etb, self.harbour, price="49.99", url=SHOP_PAGE)

    def finding(self, answer=None, **kwargs):
        client = FakeClient(answer or (lambda row_key, params: rrp_reply(row_key, **kwargs)))
        return client, rrp_finder.run(client=client)


class FindingTests(RRPBase):
    def test_a_cited_rrp_from_a_shop_is_kept_with_where_it_came_from(self):
        self.switch_on()
        client, result = self.finding()
        self.assertEqual((result.asked, result.found), (1, 1))
        [(_, params)] = client.sent
        search, settle = params["tools"]
        self.assertEqual(settle["name"], "settle_rrp")
        self.assertEqual(params["model"], "claude-haiku-5-5")
        self.assertEqual(search["type"], "web_search_20250305")
        self.assertEqual(search["max_uses"], 2)
        # The publisher's sites and the shops that sell it, nothing else.
        self.assertIn("pokemon.com", search["allowed_domains"])
        self.assertIn("harbour-games.example", search["allowed_domains"])
        self.assertIn("pokemoncenter.com", search["allowed_domains"])
        self.assertNotIn("bulbagarden.net", search["allowed_domains"])
        self.etb.refresh_from_db()
        self.assertEqual((self.etb.found_rrp, self.etb.found_rrp_source, self.etb.found_rrp_url),
                         (Decimal("54.99"), "Harbour Games", SHOP_PAGE))
        self.assertEqual(self.etb.rrp_now, (Decimal("54.99"), None))
        answer = CheckAnswer.objects.get(kind=CheckAnswer.Kind.FOUND_RRP)
        self.assertEqual(answer.what, f"Found the RRP of {ETB}: £54.99 from Harbour Games")
        ask = Ask.objects.get(kind=Ask.Kind.RRP)
        self.assertEqual((ask.action, ask.verdict, ask.web_searches), (Ask.Action.ACTED, "found", 1))
        self.assertEqual(ask.subject, f"the RRP of {ETB}")
        self.assertEqual(ask.result, "acted, the RRP is on the product page")
        self.assertEqual(ask.sources[0]["url"], SHOP_PAGE)

    def test_the_publisher_is_named_by_its_address(self):
        self.switch_on()
        _, result = self.finding(cited=((PUBLISHER_PAGE, "Surging Sparks | Pokemon.com", PUBLISHER_SAYS),),
                                 source=PUBLISHER_PAGE)
        self.assertEqual(result.found, 1)
        self.etb.refresh_from_db()
        self.assertEqual(self.etb.found_rrp_source, "pokemon.com")

    def test_the_pokemon_centers_own_price_is_the_rrp(self):
        """The owner, 2 Nov 2026: "Include Pokémon center". It sells at the publisher's price, without saying RRP."""
        self.switch_on()
        _, result = self.finding(cited=((CENTER_PAGE, CENTER_TITLE, "£54.99"),), source=CENTER_PAGE)
        self.assertEqual(result.found, 1)
        self.etb.refresh_from_db()
        self.assertEqual((self.etb.found_rrp, self.etb.found_rrp_source), (Decimal("54.99"), "Pokémon Center"))

    def test_the_pokemon_center_never_gives_a_sale_its_own_edition_or_a_page_not_about_the_product(self):
        self.switch_on()
        own_edition = "Pokémon TCG: Surging Sparks Pokémon Center Elite Trainer Box | Pokémon Center Official Site"
        for title, says, rrp in (
            (CENTER_TITLE, "£54.99 £44.99", "44.99"),
            (own_edition, "£59.99", "59.99"),
            ("Elite Trainer Boxes | Pokémon Center Official Site", "£54.99", "54.99"),
            (CENTER_TITLE, "$49.99", "49.99"),
        ):
            Ask.objects.all().delete()
            _, result = self.finding(rrp=rrp, cited=((CENTER_PAGE, title, says),), source=CENTER_PAGE)
            self.assertEqual(result.found, 0, (title, says))

    def test_only_pokemon_searches_the_pokemon_center(self):
        from .testing import make_game, make_set

        magic = make_game("Magic: The Gathering", "magic-the-gathering")
        box = make_product(make_set(magic, name="Foundations", slug="fdn", code="FDN"), name="Foundations Play Booster Box",
                           slug="fdn-box", product_type="booster_box")
        evidence = rrp_finder.evidence_for(box, [])
        self.assertEqual(evidence["search_domains"], ["wizards.com"])
        self.assertEqual(evidence["publisher_shops"], [])

    def test_a_figure_no_quote_calls_the_rrp_is_never_kept(self):
        self.switch_on()
        for says in ("Our price: £54.99, in stock now", "RRP: £59.99 Our price: £54.99"):
            Ask.objects.all().delete()
            _, result = self.finding(cited=((SHOP_PAGE, SHOP_TITLE, says),))
            self.assertEqual((result.asked, result.found), (1, 0), says)
        self.etb.refresh_from_db()
        self.assertIsNone(self.etb.found_rrp)

    def test_a_quote_about_another_product_is_never_kept(self):
        self.switch_on()
        for title, says in (
            ("Surging Sparks Booster Bundle | Harbour Games", "Booster Bundle RRP £54.99"),
            ("Surging Sparks Pokemon Center Elite Trainer Box", "RRP £54.99"),
            ("Prismatic Evolutions Elite Trainer Box", "RRP £54.99"),
        ):
            Ask.objects.all().delete()
            _, result = self.finding(cited=((SHOP_PAGE, title, says),))
            self.assertEqual(result.found, 0, title)

    def test_a_quote_from_a_site_outside_the_search_never_counts(self):
        self.switch_on()
        elsewhere = "https://www.someblog.example/surging-sparks"
        _, result = self.finding(cited=((elsewhere, SHOP_TITLE, SHOP_SAYS),), source=elsewhere)
        self.assertEqual(result.found, 0)

    def test_unsure_or_far_from_the_price_changes_nothing(self):
        self.switch_on()
        _, result = self.finding(confidence="low")
        self.assertEqual(result.found, 0)
        Ask.objects.all().delete()
        # £49.99 now against an RRP of £5.49 is a pack's RRP, not the box's.
        says = "Surging Sparks Elite Trainer Box RRP £5.49"
        _, result = self.finding(rrp="5.49", cited=((SHOP_PAGE, SHOP_TITLE, says),))
        self.assertEqual(result.found, 0)
        self.etb.refresh_from_db()
        self.assertIsNone(self.etb.found_rrp)

    def test_another_model_answering_never_acts(self):
        self.switch_on()
        _, result = self.finding(model="claude-opus-5-5")
        self.assertEqual((result.asked, result.found), (1, 0))

    def test_nothing_runs_unless_claude_may_act(self):
        self.switch_on(may_act=False)
        client, result = self.finding()
        self.assertEqual((result.asked, client.sent), (0, []))
        self.assertIn("cannot act", result.note)

    def test_the_monthly_limit_stops_the_run(self):
        self.switch_on(monthly_budget_usd=Decimal("0.01"))
        client, result = self.finding()
        self.assertEqual(client.sent, [])

    def test_undo_clears_it_and_claude_never_looks_again(self):
        self.switch_on()
        self.finding()
        answer = CheckAnswer.objects.get(kind=CheckAnswer.Kind.FOUND_RRP)
        message = autopilot.undo(answer)
        self.assertIn("does not look for one again", message)
        self.etb.refresh_from_db()
        self.assertIsNone(self.etb.found_rrp)
        self.assertEqual(Ask.objects.get(kind=Ask.Kind.RRP).owner_answer, "undone")
        Ask.objects.all().delete()
        client, result = self.finding()
        self.assertEqual(client.sent, [])

    def test_the_dry_run_sends_nothing(self):
        self.switch_on()
        out = StringIO()
        call_command("find_rrps", "--dry-run", stdout=out)
        self.assertIn(f"{ETB}: worst case $0.0", out.getvalue())
        self.assertFalse(Ask.objects.exists())


class WhichProductsTests(RRPBase):
    def test_only_products_with_no_rrp_that_a_uk_shop_sells_now_most_viewed_first(self):
        now = timezone.now()
        owner = make_product(self.set, name="Surging Sparks Booster Bundle", slug="ssp-bundle", rrp=Decimal("32.99"))
        shops = make_product(self.set, name="Surging Sparks Mini Tin", slug="ssp-tin")
        Product.objects.filter(pk=shops.pk).update(shop_rrp=Decimal("12.99"), shop_rrp_shops=2)
        japanese = make_product(self.set, name="Surging Sparks Booster Box (Japanese)", slug="ssp-jp")
        sold_out = make_product(self.set, name="Surging Sparks Build and Battle Box", slug="ssp-bnb")
        ebay_only = make_product(self.set, name="Surging Sparks Booster Pack", slug="ssp-pack",
                                 product_type="booster_pack")
        viewed = make_product(self.set, name="Surging Sparks Three Pack Blister", slug="ssp-blister",
                              product_type="blister")
        for product in (owner, shops, japanese, viewed):
            make_listing(product, self.harbour, price="20.00")
        make_listing(sold_out, self.harbour, price="20.00", availability=Listing.Availability.OUT_OF_STOCK)
        make_listing(ebay_only, make_retailer("eBay", source_type="ebay"), price="5.00")
        DailyPageView.objects.create(kind=DailyPageView.Kind.PRODUCT, key=viewed.slug, date=timezone.localdate(),
                                     hits=40)
        self.assertEqual([product.pk for product in rrp_finder.candidates(now)], [viewed.pk, self.etb.pk])
        # Asked lately: not again until RECHECK has passed.
        Ask.objects.create(kind=Ask.Kind.RRP, row_key=f"rrp:{viewed.pk}", fingerprint="x", model_asked="m",
                           effort="low", product=viewed, outcome=Ask.Outcome.ANSWERED, asked_at=now - timedelta(days=5))
        self.assertEqual([product.pk for product in rrp_finder.candidates(now)], [self.etb.pk])
        self.assertCountEqual([product.pk for product in rrp_finder.candidates(now + rrp_finder.RECHECK)],
                              [viewed.pk, self.etb.pk])


class LineTests(RRPBase):
    def page(self):
        return self.client.get(self.etb.get_absolute_url()).content.decode()

    def test_the_product_page_says_the_rrp_where_it_came_from_and_the_gap(self):
        self.assertNotIn("buybox__rrp", self.page())
        Product.objects.filter(pk=self.etb.pk).update(found_rrp=Decimal("54.99"), found_rrp_source="pokemon.com")
        html = self.page()
        self.assertIn('<span class="buybox__rrp-figure">RRP £54.99 from pokemon.com</span>', html)
        self.assertIn('<span class="buybox__rrp-gap buybox__rrp-gap--under">£5.00 under RRP</span>', html)
        # The UK shops' figure wins over Claude's, and the owner's over both.
        Product.objects.filter(pk=self.etb.pk).update(shop_rrp=Decimal("49.99"), shop_rrp_shops=3)
        html = self.page()
        self.assertIn('<span class="buybox__rrp-figure">RRP £49.99 from 3 UK shops</span>', html)
        self.assertIn('<span class="buybox__rrp-gap">At RRP</span>', html)
        Product.objects.filter(pk=self.etb.pk).update(rrp=Decimal("44.99"))
        html = self.page()
        self.assertIn('<span class="buybox__rrp-figure">RRP £44.99</span>', html)
        self.assertIn('<span aria-hidden="true">↑</span> £5.00 over RRP</span>', html)

    def test_over_the_rrp_is_never_said_beside_a_shop_that_pays_a_commission(self):
        Product.objects.filter(pk=self.etb.pk).update(rrp=Decimal("44.99"))
        line = fair_price.rrp_line(Product.objects.get(pk=self.etb.pk), Decimal("49.99"), pays_commission=True)
        self.assertEqual((line.rrp, line.side, line.gap), (Decimal("44.99"), "", None))
        line = fair_price.rrp_line(Product.objects.get(pk=self.etb.pk), Decimal("39.99"), pays_commission=True)
        self.assertEqual((line.side, line.gap), ("under", Decimal("5.00")))

    def test_the_price_check_measures_against_claudes_rrp(self):
        Product.objects.filter(pk=self.etb.pk).update(found_rrp=Decimal("30.00"), found_rrp_source="pokemon.com")
        found = fair_price.verdict(Product.objects.get(pk=self.etb.pk), Decimal("49.99"))
        self.assertEqual((found.rrp, found.rrp_shops, found.rrp_source), (Decimal("30.00"), None, "pokemon.com"))
        self.assertEqual(found.band, fair_price.HIGH_PRICE)
        fair_price.refresh_bands()
        self.etb.refresh_from_db()
        self.assertEqual(self.etb.price_band, fair_price.HIGH_PRICE)


class JobsTests(RRPBase):
    def test_what_claude_sorts_counts_the_rrps(self):
        self.switch_on()
        self.assertEqual(rrp_finder.progress(), {"waiting": 1, "needs_you": 0})
        self.finding()
        self.assertEqual(rrp_finder.progress(), {"waiting": 0, "needs_you": 0})
        self.assertEqual(judge.sorted_counts().get(Ask.Kind.RRP), 1)
