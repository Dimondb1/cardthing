"""Cheapest per card (the owner, 2 Nov 2026: "a section on the site that searches by price per card so that those who
just like ripping packs can get the best value for money"). A product's cards are its packs times the cards in each,
the owner's figures or the ones shops agree on (catalogue/contents.py); a booster pack no shop gives a count for is one
pack."""

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web.templatetags.ripraptor import pence


class PerCardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.set = make_set(self.game, name="Surging Sparks", slug="surging-sparks", code="SSP")
        self.shop = make_retailer("Harbour Games")
        # 36 packs of 10 at £108: 30p a card, a whole-pound price the database keeps as a whole number.
        self.box = self.product("Surging Sparks Booster Box", "booster_box", "108.00", packs=36, cards=10)
        # 9 packs of 10 at £45: 50p a card.
        self.etb = self.product("Surging Sparks Elite Trainer Box", "elite_trainer_box", "45.00", packs=9, cards=10)
        # One pack, no count from any shop, 10 cards at £4: 40p a card.
        self.pack = self.product("Surging Sparks Booster Pack", "booster_pack", "4.00", cards=10)
        # Cards not known: never on the page, last when a list is sorted by it.
        self.tin = self.product("Surging Sparks Tin", "tin", "20.00")

    def product(self, name, kind, price, packs=None, cards=None, stock=Listing.Availability.IN_STOCK):
        product = make_product(self.set, name=name, slug=name.lower().replace(" ", "-"), product_type=kind)
        Product.objects.filter(pk=product.pk).update(packs_found=packs, cards_found=cards)
        make_listing(product, self.shop, price=price, url=f"https://shop.example/{product.slug}", availability=stock)
        return product

    def page(self, url=None):
        html = self.client.get(url or reverse("web:per_card")).content.decode()
        return html, [name for name in (self.box.name, self.pack.name, self.etb.name, self.tin.name) if name in html]

    def test_the_page_ranks_what_each_card_costs(self):
        response = self.client.get(reverse("web:per_card"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 class="browse-head__title">Cheapest per card</h1>')
        self.assertContains(response, "It says nothing about the odds of a big pull.")
        html = response.content.decode()
        order = [html.index(name) for name in (self.box.name, self.pack.name, self.etb.name)]
        self.assertEqual(order, sorted(order))
        self.assertNotIn(self.tin.name, html)
        self.assertIn('<span class="card__each card__each--lead">30p a card</span>', html)
        self.assertIn('<span class="card__each card__each--lead">40p a card</span>', html)
        self.assertIn('<link rel="canonical" href="http://testserver/cheapest-per-card/">', html)
        # The sort menu says the list's own order, with or without other filters in the address.
        self.assertIn('<option value="card" selected>Cheapest per card</option>', html)
        html, _shown = self.page(reverse("web:per_card") + "?type=booster_box")
        self.assertIn('<option value="card" selected>Cheapest per card</option>', html)

    def test_only_what_can_be_bought_now_and_the_filters_still_work(self):
        self.product("Destined Rivals Booster Box", "booster_box", "72.00", packs=36, cards=10,
                     stock=Listing.Availability.OUT_OF_STOCK)
        html, _shown = self.page()
        self.assertNotIn("Destined Rivals", html)
        magic = make_game("Magic: The Gathering", "magic-the-gathering")
        other = make_product(make_set(magic, name="Foundations", slug="fdn", code="FDN"), name="Foundations Play Booster Box",
                             slug="fdn-box", product_type="booster_box")
        Product.objects.filter(pk=other.pk).update(packs_found=30, cards_found=14)
        make_listing(other, self.shop, price="99.00", url="https://shop.example/fdn")
        html, shown = self.page(reverse("web:per_card") + "?game=magic-the-gathering")
        self.assertIn("Foundations Play Booster Box", html)
        self.assertEqual(shown, [])

    def test_any_list_can_be_sorted_cheapest_per_card(self):
        html, shown = self.page(reverse("web:search") + "?sort=card")
        self.assertEqual(shown, [self.box.name, self.pack.name, self.etb.name, self.tin.name])
        self.assertIn('<option value="card" selected>Cheapest per card</option>', html)

    def test_the_product_page_says_what_each_card_costs(self):
        response = self.client.get(self.box.get_absolute_url())
        self.assertContains(response, '<a href="/cheapest-per-card/">360 cards inside, 30p a card</a>')
        self.assertNotContains(self.client.get(self.tin.get_absolute_url()), "a card</a>")

    def test_the_home_page_leads_with_the_cheapest_per_card(self):
        html = self.client.get(reverse("web:home")).content.decode()
        row = html[html.index('id="percard-title"'):]
        row = row[:row.index("</section>")]
        self.assertLess(row.index(self.box.name), row.index(self.pack.name))
        self.assertIn('<span class="price">30p a card</span>', row)
        self.assertIn("360 cards", row)
        self.assertNotIn(self.tin.name, row)
        self.assertIn('href="#percard-title">Cheapest per card</a>', html)

    def test_prices_are_said_as_people_say_them(self):
        self.assertEqual([pence(value) for value in ("0.123", "0.125", "0.004", "1.054", "12.5")],
                         ["12p", "13p", "1p", "£1.05", "£12.50"])

    def test_the_page_takes_the_same_queries_however_many_products(self):
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("web:per_card"))
        for i in range(6):
            self.product(f"Surging Sparks Booster Box {i}", "booster_box", f"{100 + i}.00", packs=36, cards=10)
        cache.clear()
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("web:per_card"))
        self.assertEqual(len(many), len(few))
