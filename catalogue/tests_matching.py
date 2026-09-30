from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .importers import Offer, apply_offers
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

    def test_the_same_product_in_the_same_game_still_links(self):
        apply_offers(self.shop, [self.offer("Magic The Gathering Invasion Booster Box", "1200")])
        self.assertTrue(Listing.objects.filter(product=self.box).exists())

    def test_same_words_in_another_game_get_their_own_address(self):
        apply_offers(self.shop, [self.offer("Yu-Gi-Oh Chaos Origins Booster Pack", "4"), self.offer("Riftbound League of Legends Origins Booster Pack", "5")])
        slugs = set(Product.objects.filter(name__icontains="Origins").values_list("slug", flat=True))
        self.assertEqual(len(slugs), 2)


class TidyListingsTests(TestCase):
    def test_listings_on_the_wrong_product_are_removed(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic")
        box = make_product(make_set(magic, name="Invasion", slug="invasion"), name="Invasion Booster Box", product_type="booster_box")
        shop = make_retailer("Shop")
        wrong = make_listing(box, shop, url="https://shop.example/products/pokemon-tcg-sun-moon-4-crimson-invasion-booster-box")
        right = make_listing(box, make_retailer("Other"), url="https://other.example/magic-the-gathering-invasion-booster-box")
        call_command("tidy_listings", stdout=StringIO())
        self.assertFalse(Listing.objects.filter(pk=wrong.pk).exists())
        self.assertTrue(Listing.objects.filter(pk=right.pk).exists())
