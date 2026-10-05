from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .importers import Offer, apply_offers
from .matching import match_key
from .models import Listing, Product
from .testing import make_game, make_listing, make_product, make_retailer, make_set


class WrongLinkTests(TestCase):
    """Shop items must not land on a product of another game, set or kind."""

    def setUp(self):
        self.pokemon = make_game()
        self.magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        self.shop = make_retailer("Shop")
        self.box = make_product(make_set(self.magic, name="Invasion"), name="Invasion Booster Box", product_type="booster_box")

    def offer(self, title, price="50"):
        return Offer(title=title, url="https://shop.example/" + title.lower().replace(" ", "-"), price=Decimal(price))

    def test_another_games_box_with_the_same_words_is_not_linked(self):
        apply_offers(self.shop, [self.offer("Pokemon TCG Sun and Moon Crimson Invasion Booster Box", "143")])
        self.assertFalse(Listing.objects.filter(product=self.box).exists())
        self.assertTrue(Product.objects.filter(game=self.pokemon, name__icontains="Crimson Invasion").exists())

    def test_a_single_card_is_never_matched_by_name(self):
        apply_offers(self.shop, [self.offer("Magic The Gathering Marvel Super Heroes Invasion Booster Box Extended Art", "3.95")])
        self.assertFalse(Listing.objects.filter(retailer=self.shop).exists())

    def test_a_title_with_many_extra_words_goes_to_review_not_the_product(self):
        apply_offers(self.shop, [self.offer("Magic Invasion of Chaos Reprint Unlimited Edition Booster Box", "62")])
        self.assertFalse(Listing.objects.filter(product=self.box).exists())

    def test_a_variant_with_an_extra_identifying_word_is_not_linked(self):
        pack = make_product(make_set(make_game(name="Star Wars Unlimited", slug="star-wars-unlimited", short_name="Star Wars"), name="Ashes of the Empire", slug="ashes"), name="Ashes of the Empire Booster Pack", product_type="booster_pack")
        apply_offers(self.shop, [self.offer("Star Wars Unlimited Ashes of the Empire Carbonite Booster Pack", "24.95")])
        self.assertFalse(Listing.objects.filter(product=pack).exists())
        apply_offers(make_retailer("Other"), [self.offer("Star Wars Unlimited Ashes of the Empire Booster Pack (Sealed)", "4.99")])
        self.assertTrue(Listing.objects.filter(product=pack).exists())

    def test_the_same_product_in_the_same_game_still_links(self):
        apply_offers(self.shop, [self.offer("Magic The Gathering Invasion Booster Box", "1200")])
        self.assertTrue(Listing.objects.filter(product=self.box).exists())

    def test_same_words_in_another_game_get_their_own_address(self):
        apply_offers(self.shop, [self.offer("Yu-Gi-Oh Chaos Origins Booster Pack", "4"), self.offer("Riftbound League of Legends Origins Booster Pack", "5")])
        slugs = set(Product.objects.filter(name__icontains="Origins").values_list("slug", flat=True))
        self.assertEqual(len(slugs), 2)


class TidyListingsTests(TestCase):
    def test_a_product_filed_under_the_wrong_game_is_moved_not_emptied(self):
        pokemon = make_game(name="Pokemon", slug="pokemon", short_name="Pokemon")
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        box = make_product(make_set(pokemon, name="Misc", slug="misc"), name="Zendikar Rising Set Booster Display (30 Count)",
                           product_type="booster_box")
        listing = make_listing(box, make_retailer("Card Empire"),
                               url="https://www.cardempire.co.uk/products/magic-the-gathering-zendikar-rising-set-booster-display-30-count")
        call_command("tidy_listings", stdout=StringIO())
        box.refresh_from_db()
        self.assertEqual(box.game, magic)
        self.assertIsNone(box.product_set)
        self.assertTrue(Listing.objects.filter(pk=listing.pk).exists())

    def test_the_title_beats_a_shop_tag_for_the_game(self):
        from .classify import classify

        sealed = classify("Magic: The Gathering - Zendikar Rising Set Booster Display (30 Count)",
                          vendor="cardempireuk", tags=["Booster Box", "Pokemon"], shop_type="Pokemon cards", price=120)
        self.assertEqual(sealed.game, "magic-the-gathering")

    def test_a_slug_ending_in_booster_is_not_taken_for_a_pack(self):
        yugioh = make_game(name="Yu-Gi-Oh", slug="yu-gi-oh", short_name="Yu-Gi-Oh")
        box = make_product(make_set(yugioh, name="Blazing Dominion", slug="bd"), name="TCG: Blazing Dominion Booster Box",
                           product_type="booster_box")
        pokemon = make_game(name="Pokemon", slug="pokemon", short_name="Pokemon")
        etb = make_product(make_set(pokemon, name="151", slug="151"), name="151 Elite Trainer Box", product_type="elite_trainer_box")
        rebel = make_product(make_set(pokemon, name="Rebel Clash", slug="rc"), name="Rebel Clash Booster Box (36 Sealed Booster Packs)",
                             product_type="booster_box")
        kept = [
            make_listing(box, make_retailer("A"), url="https://a.example/products/yu-gi-oh-tcg-blazing-dominion-booster"),
            make_listing(etb, make_retailer("B"), url="https://b.example/products/pokemon-scarlet-violet-151-booster-etb"),
            make_listing(rebel, make_retailer("C"), url="https://c.example/products/pokemon-rebel-clash-booster-box"),
        ]
        wrong = make_listing(box, make_retailer("D"), url="https://d.example/products/yu-gi-oh-blazing-dominion-booster-pack")
        call_command("tidy_listings", stdout=StringIO())
        for listing in kept:
            self.assertTrue(Listing.objects.filter(pk=listing.pk).exists(), listing.url)
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())

    def test_listings_on_the_wrong_product_are_removed(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        box = make_product(make_set(magic, name="Invasion", slug="invasion"), name="Invasion Booster Box", product_type="booster_box")
        shop = make_retailer("Shop")
        wrong = make_listing(box, shop, url="https://shop.example/products/pokemon-tcg-sun-moon-4-crimson-invasion-booster-box")
        right = make_listing(box, make_retailer("Other"), url="https://other.example/magic-the-gathering-invasion-booster-box")
        no_game = make_listing(box, make_retailer("Third"), url="https://third.example/products/invasion-booster-box")
        garbled = make_listing(box, make_retailer("Fourth"), url="https://fourth.example/products/mtg-invasion-bstr-box-p81362")
        japanese = make_listing(box, make_retailer("Fifth"), url="https://fifth.example/magic-the-gathering-invasion-booster-box-japanese")
        pack = make_listing(box, make_retailer("Sixth"), url="https://sixth.example/products/magic-invasion-booster-pack")
        single = make_listing(box, make_retailer("Seventh"), url="https://seventh.example/products/magic-invasion-booster-box-playmat")
        slip = make_listing(box, make_retailer("Eighth"), url="https://eighth.example/products/magic-invasion-booster-booster-box-36-packs")
        call_command("tidy_listings", stdout=StringIO())
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=right.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=no_game.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=garbled.pk).exists())
        self.assertFalse(Listing.objects.filter(pk=japanese.pk).exists())
        self.assertFalse(Listing.objects.filter(pk=pack.pk).exists())
        self.assertFalse(Listing.objects.filter(pk=single.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=slip.pk).exists())


class SeriesDecimalTests(TestCase):
    def test_a_point_release_series_number_is_part_of_the_series(self):
        self.assertEqual(
            match_key("Scarlet & Violet 8.5 Prismatic Evolutions Elite Trainer Box"),
            match_key("Prismatic Evolutions Elite Trainer Box"),
        )
        self.assertEqual(match_key("Scarlet & Violet 151 Booster Bundle"), match_key("151 Booster Bundle"))
        self.assertNotEqual(match_key("151 Booster Bundle"), match_key("Booster Bundle"))
