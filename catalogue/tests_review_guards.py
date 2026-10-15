"""Guards added after the review of the wider matching: each case was a real way to join two products."""

from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from . import checks
from .importers import Catalogue, Offer, create_from_offer
from .matching import identity_numbers, match_key
from .models import CheckAnswer, Product
from .testing import make_game


class TwinGuardTests(TestCase):
    def setUp(self):
        self.game = make_game()

    def catalogue(self):
        return Catalogue(Product.objects.values_list("pk", "name", "game__slug"))

    def test_a_pokemon_center_box_is_not_the_plain_box_with_its_code(self):
        Product.objects.create(game=self.game, name="Shrouded Fable Elite Trainer Box sv6.5", product_type="elite_trainer_box")
        title = "Pokemon - Scarlet & Violet 6.5 Shrouded Fable - Pokemon Center Elite Trainer Box"
        self.assertIsNone(self.catalogue().twin("Shrouded Fable Pokemon Center Elite Trainer Box sv6.5", title, game="pokemon"))

    def test_a_twin_of_another_kind_or_a_refused_one_is_never_used(self):
        pack = Product.objects.create(game=self.game, name="Destined Rivals Booster Pack", product_type="booster_pack")
        catalogue = self.catalogue()
        catalogue.twin = lambda *args, **kwargs: pack.pk   # whatever the keys say
        box = Offer(title="Pokemon Destined Rivals Booster Box (36 Packs)", url="https://shop.example/p/box", price=Decimal("150"))
        self.assertNotEqual(create_from_offer(box, catalogue), pack.pk)
        same_kind = Offer(title="Pokemon Destined Rivals Booster Pack", url="https://shop.example/p/pack", price=Decimal("5"))
        self.assertNotEqual(create_from_offer(same_kind, catalogue, refused={pack.pk}), pack.pk)
        self.assertEqual(create_from_offer(same_kind, catalogue), pack.pk)


class KeyGuardTests(TestCase):
    def test_a_bare_jp_keeps_a_japanese_name_apart(self):
        self.assertNotEqual(match_key("OP-09 JP Emperors in the New World Booster Box", "one-piece"),
                            match_key("OP-09 Emperors in the New World Booster Box", "one-piece"))

    def test_a_half_box_keeps_its_count(self):
        self.assertNotEqual(identity_numbers("Destined Rivals Booster Box (18 Packs)"),
                            identity_numbers("Destined Rivals Booster Display Box"))
        self.assertEqual(identity_numbers("Destined Rivals Booster Box (36 Packs)"),
                         identity_numbers("Destined Rivals Booster Display Box"))


class ListedAnswersTests(TestCase):
    def test_the_hourly_merges_never_push_other_answers_off_the_page(self):
        def answer(kind, by_owner=False, ask_id=None):
            return SimpleNamespace(kind=kind, by_owner=by_owner, ask_id=ask_id)

        hourly = [answer(CheckAnswer.Kind.MERGE) for _ in range(570)]
        claude = [answer(CheckAnswer.Kind.HIDE, ask_id=1), answer(CheckAnswer.Kind.MERGE, ask_id=2)]
        listed = checks.listed_answers(hourly + claude)
        self.assertEqual(listed[:2], claude)
        self.assertEqual(len(listed), 572)
