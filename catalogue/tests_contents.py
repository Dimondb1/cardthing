"""What is inside a product, read from the shops' own descriptions (catalogue/contents.py)."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import contents
from .models import Product, Retailer
from .testing import make_game, make_listing, make_product, make_retailer, make_set

# (a shop's description, the packs it says, the cards a pack it says). Written the way UK shops write them.
DESCRIPTIONS = [
 ("Pokemon TCG Surging Sparks Booster Box - 36 Booster Packs. Each booster pack contains 10 cards.", 36, 10),
 ("Each Pokémon TCG: Scarlet & Violet—Prismatic Evolutions Elite Trainer Box includes: 9 Pokémon TCG: Scarlet & Violet—Prismatic Evolutions booster packs 1 full-art foil promo card featuring Eevee 65 card sleeves 45 Pokémon TCG Energy cards", 9, None),
 ("The Pokémon TCG: Scarlet & Violet—151 Elite Trainer Box includes: 9 Pokémon TCG: Scarlet & Violet—151 booster packs, 1 etched foil promo card, 65 card sleeves", 9, None),
 ("Scarlet & Violet 151 Booster Bundle includes 6 booster packs.", 6, None),
 ("Japanese Booster Box. 30 packs per box, 5 cards per pack.", 30, 5),
 ("Each box contains 30 packs with 5 cards in each pack", 30, 5),
 ("Contains 30 Play Boosters. Each Play Booster contains 14 Magic: The Gathering cards.", 30, 14),
 ("Collector Booster Box contains 12 Collector Boosters. Each Collector Booster contains 15 cards", 12, 15),
 ("One Piece Card Game OP-11 booster box. 24 packs per box, 12 cards per pack.", 24, 12),
 ("Lorcana booster display of 24 booster packs, each with 12 cards.", 24, None),
 ("Case of 6 booster boxes. Each booster box contains 36 packs.", None, None),
 ("Pokemon 151 Ultra Premium Collection - 16 booster packs, 3 foil promo cards", 16, None),
 ("Includes 1 of 4 promo cards and 2 booster packs", 2, None),
 ("Contains 6 Surging Sparks booster packs and 2 Stellar Crown booster packs", None, None),
 ("A tin with 4 booster packs and a coin. £24.99 RRP. 4 booster packs inside.", 4, None),
 ("Sealed booster pack. Each pack contains 10 cards.", None, 10),
 ("3 Pack Blister: 3 booster packs and 1 promo card", 3, None),
 ("36 x Booster Packs", 36, None),
 ("36x booster packs per display", 36, None),
 ("Released in 2024, this box has 36 packs", 36, None),
 ("Mystery packs. You could pull 1 of 36 booster packs", None, None),
 ("Pokemon 151 Booster Bundle (6 Packs)", 6, None),
 ("Pack of 3 booster packs", 3, None),
 ("Booster box with 24 boosters of 10 cards.", 24, 10),
 ("1 booster box containing 36 booster packs", 36, None),
 ("2 booster boxes in this bundle", None, None),
 ("Yu-Gi-Oh! Phantom Revenge Booster Box. 24 packs per box. 9 cards per pack.", 24, 9),
 ("SV10 Destined Rivals Elite Trainer Box with 9 booster packs", 9, None),
 ("Price: £2.50 per pack", None, None),
 ("EB-02 Anime 25th Collection booster box, 24 packs", 24, None),
 ("Contents: 10 booster packs, 1 player's guide", 10, None),
 ("Booster Bundle: 6 booster packs", 6, None),
 ("Contains 8 booster packs, 1 Pokemon coin, 1 code card for the Pokemon TCG Live", 8, None),
 ("", None, None),
]


class ReadingTests(TestCase):
    def test_each_description_gives_what_it_plainly_says_and_nothing_it_does_not(self):
        for text, packs, cards in DESCRIPTIONS:
            with self.subTest(text=text):
                self.assertEqual((contents.packs_in(text), contents.cards_in(text)), (packs, cards))

    def test_most_shops_must_agree(self):
        self.assertEqual(contents.agreed([36, 36, 30]), 36)
        self.assertEqual(contents.agreed([36, None, None]), 36)
        self.assertIsNone(contents.agreed([36, 30]))
        self.assertIsNone(contents.agreed([36, 30, 24]))
        self.assertIsNone(contents.agreed([None, None]))
        self.assertIsNone(contents.agreed([]))


class RefreshTests(TestCase):
    def setUp(self):
        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", product_type="booster_box")
        self.shops = [make_retailer(f"Shop {n}") for n in range(3)]

    def listing(self, shop, description, **kwargs):
        listing = make_listing(self.box, shop, **kwargs)
        type(listing).objects.filter(pk=listing.pk).update(description=description)
        return listing

    def refresh(self):
        changed = contents.refresh_contents()
        self.box.refresh_from_db()
        return changed

    def test_the_count_most_shops_give_is_kept_and_a_lone_dissent_is_outvoted(self):
        self.listing(self.shops[0], "36 booster packs. Each booster pack contains 10 cards.")
        self.listing(self.shops[1], "Contains 36 packs")
        self.listing(self.shops[2], "Booster box with 30 packs")
        self.assertEqual(self.refresh(), 1)
        self.assertEqual((self.box.packs_found, self.box.cards_found), (36, 10))
        self.assertEqual(self.refresh(), 0)   # written only when it changes

    def test_shops_that_disagree_evenly_give_nothing(self):
        self.listing(self.shops[0], "36 booster packs")
        self.listing(self.shops[1], "30 booster packs")
        self.refresh()
        self.assertIsNone(self.box.packs_found)

    def test_marketplaces_and_hidden_listings_are_never_read(self):
        ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.listing(ebay, "Contains 36 booster packs")
        self.listing(self.shops[0], "Contains 36 booster packs", is_active=False)
        self.refresh()
        self.assertIsNone(self.box.packs_found)

    def test_a_count_that_goes_is_taken_away(self):
        listing = self.listing(self.shops[0], "Contains 36 booster packs")
        self.refresh()
        self.assertEqual(self.box.packs_found, 36)
        type(listing).objects.filter(pk=listing.pk).update(description="A sealed box.")
        self.refresh()
        self.assertIsNone(self.box.packs_found)

    def test_the_owners_figures_win(self):
        self.listing(self.shops[0], "Contains 36 booster packs, 10 cards per pack")
        self.refresh()
        self.assertEqual((self.box.packs_inside, self.box.cards_inside), (36, 10))
        self.assertTrue(self.box.contents_from_shops)
        Product.objects.filter(pk=self.box.pk).update(packs=30, cards_per_pack=5)
        self.box.refresh_from_db()
        self.assertEqual((self.box.packs_inside, self.box.cards_inside), (30, 5))
        self.assertFalse(self.box.contents_from_shops)

    def test_one_read_of_the_descriptions_and_one_write(self):
        for shop in self.shops:
            self.listing(shop, "Contains 36 booster packs")
        with self.assertNumQueries(3):   # the descriptions, the products, the write
            contents.refresh_contents()

    def test_the_command_and_the_hourly_tidy_run_it(self):
        self.listing(self.shops[0], "Contains 36 booster packs")
        out = StringIO()
        call_command("refresh_contents", dry_run=True, stdout=out)
        self.assertIn("1 products' packs and cards would change.", out.getvalue())
        self.box.refresh_from_db()
        self.assertIsNone(self.box.packs_found)
        call_command("refresh_contents", stdout=StringIO())
        self.box.refresh_from_db()
        self.assertEqual(self.box.packs_found, 36)
        from pathlib import Path

        from django.conf import settings

        tidy = (Path(settings.BASE_DIR) / "catalogue/management/commands/tidy_all.py").read_text()
        self.assertIn("refresh_contents(dry_run=dry_run)", tidy)
        install = (Path(settings.BASE_DIR) / "deploy/install.sh").read_text()
        self.assertIn("manage.py refresh_contents", install)


class ProductPageTests(TestCase):
    def setUp(self):
        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box", product_type="booster_box")
        make_listing(self.box, make_retailer("Harbour Games"), price="87.00")

    def page(self):
        return self.client.get(self.box.get_absolute_url()).content.decode()

    def test_the_price_box_gives_the_price_a_pack_and_the_details_what_is_inside(self):
        Product.objects.filter(pk=self.box.pk).update(packs_found=36, cards_found=10)
        html = self.page()
        self.assertIn('<p class="buybox__packs">36 packs inside, £2.42 a pack</p>', html)
        self.assertIn("<dt>Inside</dt><dd>36 packs of 10 cards <span class=\"muted\">(from the shops&#x27; descriptions)</span></dd>", html)
        self.assertLess(html.index("buybox__packs"), html.index('class="buy-now"'))

    def test_the_owners_figures_need_no_source(self):
        Product.objects.filter(pk=self.box.pk).update(packs=24, packs_found=36)
        html = self.page()
        self.assertIn("24 packs inside, £3.63 a pack", html)
        self.assertIn("<dt>Inside</dt><dd>24 packs</dd>", html)

    def test_one_pack_has_no_price_a_pack_and_nothing_known_shows_nothing(self):
        Product.objects.filter(pk=self.box.pk).update(packs_found=1, cards_found=10)
        html = self.page()
        self.assertNotIn("buybox__packs", html)
        self.assertIn("1 pack of 10 cards", html)
        Product.objects.filter(pk=self.box.pk).update(packs_found=None, cards_found=None)
        html = self.page()
        self.assertNotIn("buybox__packs", html)
        self.assertNotIn("<dt>Inside</dt>", html)

    def test_admin_shows_what_the_shops_say(self):
        from django.contrib.auth import get_user_model

        Product.objects.filter(pk=self.box.pk).update(packs_found=36, cards_found=10)
        self.client.force_login(get_user_model().objects.create_superuser("owner", "o@example.com", "pw"))
        html = self.client.get(reverse("admin:catalogue_product_change", args=[self.box.pk])).content.decode()
        self.assertIn("What is inside", html)
        self.assertIn("36 packs, 10 cards a pack", html)
        self.assertIn('name="packs"', html)
        self.assertIn('name="cards_per_pack"', html)


class CardTests(TestCase):
    """Product cards in lists say how many packs and what one costs, so products can be weighed at a glance."""

    def setUp(self):
        self.game = make_game()
        self.set = make_set(self.game)
        self.box = make_product(self.set, name="Surging Sparks Booster Box", product_type="booster_box",
                                packs_found=36)
        make_listing(self.box, make_retailer("Harbour Games"), price="87.00")
        self.single = make_product(self.set, name="Surging Sparks Mini Tin", product_type="tin", packs_found=1)
        make_listing(self.single, make_retailer("Tollgate TCG"), price="4.50")

    def card(self, product):
        html = self.client.get(self.set.get_absolute_url()).content.decode()
        start = html.index(f'class="card__name"><a href="{product.get_absolute_url()}"')
        start = html.rindex('<li class="card', 0, start)
        return html[start:html.index("</li>", html.index('class="card__foot"', start))]

    def test_a_card_gives_the_packs_and_the_price_a_pack(self):
        card = self.card(self.box)
        self.assertIn('Booster box<span aria-hidden="true"> · </span><span class="visually-hidden">, </span>36 packs</p>', card)
        self.assertIn('<span class="card__each">£2.42 a pack</span>', card)

    def test_one_pack_or_an_unknown_count_says_nothing(self):
        card = self.card(self.single)
        self.assertNotIn("card__each", card)
        self.assertNotIn(" packs</p>", card)
        Product.objects.filter(pk=self.box.pk).update(packs_found=None)
        self.assertNotIn("card__each", self.card(self.box))

    def test_the_price_a_pack_rounds_to_the_penny(self):
        from decimal import Decimal

        self.assertEqual(contents.price_a_pack(Decimal("87.00"), 24), Decimal("3.63"))
        self.assertEqual(contents.price_a_pack(Decimal("49.48"), 9), Decimal("5.50"))
        self.assertIsNone(contents.price_a_pack(Decimal("4.50"), 1))
        self.assertIsNone(contents.price_a_pack(None, 36))
        self.assertIsNone(contents.price_a_pack(Decimal("87.00"), None))
