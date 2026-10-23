"""Best match for a broad search, and the quick filters above a search's results (catalogue/search.py bulk_name,
catalogue/ordering.py, web/views.py quick_filters). A site review on 23 Oct 2026: a search for "151" led with a
£2,150 display of ten bundles, then nine mini tin artworks one after another."""

from django.core.cache import cache
from django.test import TestCase

from catalogue.models import Product
from catalogue.search import bulk_name
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

OUT = "out_of_stock"


class Shops:
    def shops(self, count):
        return [make_retailer(f"Shop {n}") for n in range(count)]


class BulkTests(TestCase):
    def test_a_bulk_lot_by_its_name_and_never_a_single_box(self):
        game = make_game()
        names = {
            "151 Booster Bundle Display (10 Bundles)": True,
            "Lumiose City Mini Tin Display (10 Tins)": True,
            "Secrets of Power Carbonite Booster Box Bundle (4 Booster Boxes)": True,
            "Surging Sparks 6x Booster Box": True,
            "Prismatic Evolutions Elite Trainer Box Case": True,
            "Surging Sparks Booster Box (Case of 36)": False,      # one box of 36 packs
            "Phoenix Bundle": False,                              # "x bundle", but no count
            "Duskmourn Play Booster Display": False,              # Magic's name for a booster box
            "151 Booster Bundle": False,
            "Mega Charizard X Booster Box": False,
        }
        made = {name: make_product(make_set(game, name=name[:40], slug=f"s{n}", code=f"C{n}"), name=name,
                                   slug=f"p{n}") for n, name in enumerate(names)}
        bulk = set(Product.objects.filter(bulk_name()).values_list("name", flat=True))
        for name, expected in names.items():
            self.assertEqual(name in bulk, expected, name)
        self.assertEqual(len(made), len(names))


class BestMatchTests(Shops, TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.set = make_set(self.game, name="151", slug="151", code="MEW")
        shops = self.shops(4)
        self.display = self.item("151 Booster Bundle Display (10 Bundles)", "2150.00", shops[:1], Product.Type.BOOSTER_BOX)
        self.tins = [self.item(f"151 Mini Tin {name}", "45.00", shops[:1], Product.Type.TIN)
                     for name in ("Dragonite & Vileplume", "Gengar & Poliwag", "Machamp & Cubone")]
        self.bundle = self.item("151 Booster Bundle", "45.00", shops, Product.Type.BUNDLE)
        self.etb = self.item("151 Elite Trainer Box", "60.00", shops[:3], Product.Type.ELITE_TRAINER_BOX)

    def item(self, name, price, shops, kind):
        product = make_product(self.set, name=name, slug=name.lower().replace(" ", "-").replace("&", "and")
                               .replace("(", "").replace(")", ""), product_type=kind)
        for shop in shops:
            make_listing(product, shop, price=price)
        return product

    def names(self, query):
        html = self.client.get("/search/", {"q": query}).content.decode()
        return [name for name in (self.bundle.name, self.etb.name, self.display.name, *[t.name for t in self.tins])
                if f">{name}<" in html], html

    def order(self, query):
        _, html = self.names(query)
        found = [(html.index(f">{p.name}<"), p) for p in (self.bundle, self.etb, self.display, *self.tins)
                 if f">{p.name}<" in html]
        return [p for _, p in sorted(found, key=lambda row: row[0])]

    def test_a_broad_search_leads_with_what_most_shops_sell_and_puts_the_bulk_lot_last(self):
        order = self.order("151")
        self.assertEqual(order[:2], [self.bundle, self.etb])
        self.assertEqual(order[-1], self.display)

    def test_a_search_that_names_the_bulk_lot_still_finds_it_first(self):
        self.assertEqual(self.order("151 bundle display")[0], self.display)
        self.assertEqual(self.order("151 display")[0], self.display)


class QuickFilterTests(Shops, TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.set = make_set(self.game, name="151", slug="151", code="MEW")
        self.shop = make_retailer("Harbour Games")
        self.bundle = self.item("151 Booster Bundle", Product.Type.BUNDLE)
        self.etb = self.item("151 Elite Trainer Box", Product.Type.ELITE_TRAINER_BOX)
        self.japanese = self.item("151 sv2a Japanese Booster Box", Product.Type.BOOSTER_BOX, language="ja")
        self.gone = self.item("151 Booster Pack", Product.Type.BOOSTER_PACK, availability=OUT)

    def item(self, name, kind, language="", availability="in_stock"):
        product = make_product(self.set, name=name, slug=name.lower().replace(" ", "-"), product_type=kind)
        if language:
            Product.objects.filter(pk=product.pk).update(language=language)
        make_listing(product, self.shop, price="45.00", availability=availability)
        return product

    def quick(self, **params):
        html = self.client.get("/search/", {"q": "151", **params}).content.decode()
        if 'class="chips quick"' not in html:
            return "", html
        row = html[html.index('class="chips quick"'):]
        return row[:row.index("</nav>")], html

    def test_the_choices_a_visitor_makes_most_in_the_game_s_own_words(self):
        row, _ = self.quick()
        self.assertIn('aria-label="Quick filters"', row)
        self.assertIn('href="?q=151&amp;lang=en">English</a>', row)
        self.assertIn('href="?q=151&amp;in_stock=on">In stock</a>', row)
        self.assertIn('href="?q=151&amp;type=bundle">Booster bundles</a>', row)
        self.assertIn('href="?q=151&amp;type=elite_trainer_box">ETBs</a>', row)
        self.assertNotIn("Clear filters", row)   # nothing on yet

    def test_one_that_is_on_says_so_takes_itself_off_and_clear_filters_resets(self):
        row, html = self.quick(lang="en", in_stock="on")
        self.assertIn('href="?q=151&amp;in_stock=on" aria-current="true">English<span class="chip__off" aria-hidden="true">×</span>'
                      '<span class="visually-hidden">, on, select to remove</span></a>', row)
        self.assertIn('href="?q=151&amp;lang=en" aria-current="true">In stock', row)
        self.assertIn('href="?q=151">Clear filters</a>', row)
        self.assertNotIn(self.japanese.name, html)
        self.assertNotIn(self.gone.name, html)
        # The full filters stay below, set to the same choices.
        self.assertIn('<option value="en" selected>', html)

    def test_a_choice_that_would_change_nothing_is_not_offered(self):
        Product.objects.filter(pk=self.japanese.pk).update(language="")
        self.gone.listings.update(availability="in_stock")
        row, _ = self.quick()
        self.assertNotIn(">English<", row)
        self.assertNotIn(">In stock<", row)
        self.assertIn(">Booster bundles<", row)

    def test_only_on_search_pages(self):
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertNotIn('class="chips quick"', html)
