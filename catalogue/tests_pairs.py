"""Possible duplicates, from the pairs offered on Things to check to what Claude and the hourly merge may do
with them. No test here touches the network: tests_judge's fake client stands in for Anthropic."""

from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import autopilot, checks
from .models import CheckAnswer, ClaudeAsk, Listing, Product, Retailer
from .testing import make_listing, make_product, make_retailer, make_set
from .tests_judge import Base, reply

Ask = ClaudeAsk


class PairBase(Base):
    def pair(self, keep_name="Surging Sparks Elite Trainer Box",
             other_name="Scarlet & Violet Surging Sparks Elite Trainer Box"):
        keep = make_product(self.set, name=keep_name)
        other = make_product(self.set, name=other_name)
        self.shop(50, product=keep, title=keep.name)
        self.shop(51, product=other, title=other.name)
        return keep, other

    def offered(self):
        return [(keep, other) for keep, others in checks.duplicates() for other in others]

    def pairs(self):
        return {frozenset((keep.pk, other.pk)) for keep, other in self.offered()}

    def named(self, name, product_set=None, product_type="booster_box"):
        """A product shown on the site with one shop, filed under ``product_set`` or no set."""
        product = Product.objects.create(game=self.game, product_set=product_set, name=name, product_type=product_type)
        self.shop(40, product=product, title=name)
        return product


class ShopsOnBothTests(PairBase):
    """A shop that shows two products under their own names sells two products, so the pair is never offered.
    eBay and Amazon search for every product, and a hidden listing is not shown, so neither says so."""

    def test_ebay_and_amazon_on_both_still_offer_the_pair_and_claude_merges_it_losing_no_listing(self):
        keep, other = self.pair()
        for source in (Retailer.Source.EBAY, Retailer.Source.AMAZON):
            market = make_retailer(f"Market {source}", source_type=source)
            make_listing(keep, market, url=f"{market.website}item/1")
            make_listing(other, market, url=f"{market.website}item/2")
        self.assertEqual(self.offered(), [(keep, other)])
        listings = Listing.objects.count()
        self.switch_on()
        self.ask()
        other.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertEqual(Listing.objects.count(), listings)
        # Each marketplace's listing for the switched-off product stays with it, where nobody sees it.
        self.assertEqual(Listing.objects.filter(product=keep).count(), 4)
        self.assertEqual(set(Listing.objects.filter(product=other).values_list("retailer__source_type", flat=True)),
                         {Retailer.Source.EBAY, Retailer.Source.AMAZON})

    def test_a_hidden_listing_on_the_other_product_says_nothing(self):
        keep, other = self.pair()
        both = make_retailer("Both")
        make_listing(keep, both)
        make_listing(other, both, url=f"{both.website}p/other", is_active=False)
        self.assertEqual(self.offered(), [(keep, other)])
        self.switch_on()
        self.ask()
        self.assertFalse(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Listing.objects.get(retailer=both, product=other).is_active, False)

    def test_a_shop_showing_both_keeps_the_pair_off_the_list(self):
        keep, other = self.pair()
        both = make_retailer("Both")
        make_listing(keep, both)
        make_listing(other, both, url=f"{both.website}p/other")
        self.assertEqual(self.offered(), [])

    def test_a_shop_listing_both_while_claude_was_asked_stops_the_merge(self):
        keep, other = self.pair()
        both = make_retailer("Both")

        def answer(key, params):
            make_listing(keep, both)
            make_listing(other, both, url=f"{both.website}p/other")
            return reply(key)

        self.switch_on()
        self.ask(answer)
        self.assertTrue(Product.objects.get(pk=other.pk).is_active)
        self.assertEqual(Ask.objects.get().action, Ask.Action.STALE)

    def test_the_shops_are_read_in_one_query(self):
        keep, other = self.pair()
        with CaptureQueriesContext(connection) as first:
            checks.duplicates()
        for n in range(5):
            extra = make_product(self.set, name=f"Surging Sparks Tin {n}", product_type="tin")
            twin = make_product(self.set, name=f"Scarlet & Violet Surging Sparks Tin {n}", product_type="tin")
            for product in (extra, twin):
                self.shop(20, product=product, title=product.name)
        with CaptureQueriesContext(connection) as more:
            self.assertEqual(len(self.offered()), 6)
        self.assertEqual(len(more), len(first))


class CandidatePairTests(PairBase):
    """Pairs reach Things to check and Claude through any key two names of one product share: the words (across
    product types), one set code, the set a product is filed under, or a barcode. Each pair stands alone."""

    def test_a_booster_display_filed_as_another_type_is_offered_beside_the_box(self):
        box = self.named("Chaos Rising Booster Box")
        display = self.named("Mega Evolution Chaos Rising Booster Display Box", product_type="elite_trainer_box")
        self.assertEqual(self.pairs(), {frozenset((box.pk, display.pk))})

    def test_one_set_code_pairs_two_translations_but_never_a_case_an_unwrapped_box_or_a_pack(self):
        box = self.named("(JAPANESE) Pokemon SV1a Triple Beat Booster Box")
        other = self.named("Triplet Beat sv1a Japanese Booster Box")
        self.named("Triplet Beat sv1a Japanese Booster Box No Shrink")
        self.named("Triple Beat sv1a Booster Box Case (Japanese)")
        self.named("(JAPANESE) Pokemon SV1a Triple Beat Booster Pack", product_type="booster_pack")
        self.named("Triple Beat sv1a Korean Booster Box")
        self.assertEqual(self.pairs(), {frozenset((box.pk, other.pk))})

    def test_a_code_only_name_meets_the_set_it_is_filed_under(self):
        rivals = make_set(self.game, name="Destined Rivals", slug="destined-rivals", code="SV10")
        coded = self.named("SV10 Booster Box", product_set=rivals)
        named = self.named("Destined Rivals Booster Box", product_set=rivals)
        self.named("Destined Rivals Booster Pack", product_set=rivals, product_type="booster_pack")
        self.named("Destined Rivals Pokemon Center Elite Trainer Box", product_set=rivals,
                   product_type="elite_trainer_box")
        self.assertEqual(self.pairs(), {frozenset((coded.pk, named.pk))})
        # Without the set, nothing in the two names says they are one product.
        Product.objects.filter(pk=coded.pk).update(product_set=None)
        self.assertEqual(self.pairs(), set())

    def test_two_products_with_one_barcode_are_offered(self):
        first = self.named("Surging Sparks Booster Bundle", product_type="bundle")
        second = self.named("Surging Sparks Bundle of Boosters", product_type="bundle")
        Product.objects.filter(pk__in=[first.pk, second.pk]).update(ean="0820650853494")
        self.assertEqual(self.pairs(), {frozenset((first.pk, second.pk))})

    def test_each_pair_stands_alone_so_a_lookalike_never_pulls_in_a_third(self):
        # The code pairs the first two and the words the last two; the first and last share nothing.
        pursue = self.named("Pursue Glory Together csv10 Slim Booster Box")
        chasing = self.named("Chasing Glory Together (csv10) Slim Booster Box Simplified Chinese")
        plain = self.named("Chasing Glory Together Slim Booster Box Simplified Chinese")
        self.assertEqual(self.pairs(), {frozenset((pursue.pk, chasing.pk)), frozenset((chasing.pk, plain.pk))})

    def test_a_pair_kept_apart_by_the_owner_or_claude_is_never_offered(self):
        keep, other = self.pair()
        self.assertTrue(checks.still_offered(keep, other))
        autopilot.owner_apart(keep, other)
        self.assertEqual(self.offered(), [])
        self.assertFalse(checks.still_offered(keep, other))
        CheckAnswer.objects.all().delete()
        self.switch_on()
        self.ask(lambda key, params: reply(key, "different", differences=["edition"]))
        self.assertEqual(CheckAnswer.objects.get().kind, CheckAnswer.Kind.APART)
        self.assertEqual(self.offered(), [])

    def test_claudes_apart_waits_when_the_pair_stopped_being_offered_while_it_was_asked(self):
        keep, other = self.pair()
        both = make_retailer("Both")

        def answer(key, params):
            make_listing(keep, both)
            make_listing(other, both, url=f"{both.website}p/other")
            return reply(key, "different", differences=["edition"])

        self.switch_on()
        self.ask(answer)
        self.assertEqual(CheckAnswer.objects.count(), 0)
        self.assertEqual(Ask.objects.get().action, Ask.Action.STALE)

    def test_a_product_paired_with_two_kept_ones_shows_claudes_answer_for_each_pair(self):
        from . import judge

        shared = self.named("Chasing Glory Together (csv10) Slim Booster Box Simplified Chinese")
        first = self.named("Pursue Glory Together csv10 Slim Booster Box Simplified Chinese")
        second = self.named("Chasing Glory Together Slim Booster Box Simplified Chinese")
        for product in (first, second):
            self.shop(41, product=product, title=product.name)
        verdicts = {judge.pair_key(first, shared): "same", judge.pair_key(second, shared): "different"}
        self.switch_on(may_act=False)
        self.ask(lambda key, params: reply(key, verdicts[key], differences=[] if verdicts[key] == "same" else ["set"]))
        duplicates = checks.duplicates()
        judge.attach_answers([], [], [], duplicates)
        said = {keep.pk: [other.claude.verdict for other in others] for keep, others in duplicates}
        self.assertEqual(said, {first.pk: ["same"], second.pk: ["different"]})
