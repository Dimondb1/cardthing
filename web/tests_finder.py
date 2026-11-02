"""The home page's finder (the owner, Nov 2026: "make the homepage a live product finder: choose a game, narrow the
details, and immediately see relevant products with their delivered prices"). Its choices are the search page's own
filters, so See all opens the same list; every choice is a plain link or form, and finder.js only speeds it up."""

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import DailyPageView, Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

HUMAN = {"HTTP_USER_AGENT": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36"}


def results(html):
    """The names of the products the finder shows, in order."""
    start = html.index("data-finder-results")
    block = html[start:html.index("</section>", start)]
    return [part.split("</span>", 1)[0] for part in block.split('<span class="finder-card__name">')[1:]]


class FinderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.pokemon = make_game()
        self.magic = make_game("Magic: The Gathering", "magic-the-gathering", short_name="Magic")
        self.ssp = make_set(self.pokemon, name="Surging Sparks", slug="ssp", code="SSP")
        self.pre = make_set(self.pokemon, name="Prismatic Evolutions", slug="pre", code="PRE")
        self.fdn = make_set(self.magic, name="Foundations", slug="fdn", code="FDN")
        self.shop = make_retailer("Harbour Games")
        # 36 packs at £108 delivered: £3 a pack. 9 packs at £45: £5 a pack. One pack at £4.
        self.box = self.product(self.ssp, "Surging Sparks Booster Box", "booster_box", "108.00", packs=36)
        self.etb = self.product(self.ssp, "Surging Sparks Elite Trainer Box", "elite_trainer_box", "45.00", packs=9)
        self.pack = self.product(self.pre, "Prismatic Evolutions Booster Pack", "booster_pack", "4.00")
        self.deck = self.product(self.fdn, "Foundations Starter Deck", "deck", "12.00")
        self.tin = self.product(self.ssp, "Surging Sparks Tin", "tin", "20.00")
        self.sold_out = self.product(self.ssp, "Surging Sparks Booster Bundle", "bundle", "30.00",
                                     stock=Listing.Availability.OUT_OF_STOCK)
        self.preorder = self.product(self.pre, "Prismatic Evolutions Super Premium Collection", "collection_box", "80.00",
                                     stock=Listing.Availability.PREORDER)

    def product(self, product_set, name, kind, price, packs=None, stock=Listing.Availability.IN_STOCK):
        product = make_product(product_set, name=name, slug=name.lower().replace(" ", "-"), product_type=kind)
        if packs:
            Product.objects.filter(pk=product.pk).update(packs_found=packs)
        make_listing(product, self.shop, price=price, url=f"https://shop.example/{product.slug}", availability=stock)
        return product

    def home(self, query=""):
        response = self.client.get(reverse("web:home") + query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_finder_sits_under_the_search_with_tabs_shortcuts_and_menus(self):
        html = self.home()
        search, games = html.index('class="hero__search"'), html.index('class="finder__games"')
        self.assertLess(search, games)
        self.assertIn('<a class="finder__tab" href="/" data-finder-link aria-current="page">All games</a>', html)
        self.assertIn('href="/?game=pokemon" data-finder-link>Pokémon</a>', html)
        for label in ("Open packs", "Collect a set", "Start playing", "Find a gift"):
            self.assertIn(f">{label}</a>", html)
        # A plain form with its own button, so it works without script; finder.js hides the button.
        self.assertIn('<form class="finder__form" method="get" action="/" data-finder-form>', html)
        self.assertIn(">Show products</button>", html)
        self.assertIn('<script src="/static/js/finder.js" defer></script>', html)
        self.assertIn('<option value="in" selected>In stock</option>', html)
        # Nothing chosen: the home page as it was, to be indexed.
        self.assertNotIn('content="noindex', html)

    def test_a_game_tab_narrows_to_that_game_and_offers_its_sets(self):
        html = self.home("?game=magic-the-gathering")
        self.assertEqual(results(html), ["Foundations Starter Deck"])
        self.assertIn('<h2 id="finder-title" class="finder__title">Magic products</h2>', html)
        self.assertIn('<option value="fdn">Foundations</option>', html)
        self.assertNotIn('<option value="ssp">', html)
        # Chosen, shown as a chip that takes it off, and not a page for search engines.
        self.assertIn('<a class="chip" href="/" data-finder-link aria-current="true">Magic<span class="chip__off"', html)
        self.assertIn('<meta name="robots" content="noindex, follow">', html)
        html = self.home("?game=pokemon&set=pre")
        self.assertEqual(results(html), ["Prismatic Evolutions Booster Pack"])

    def test_open_packs_ranks_by_what_a_pack_costs_and_leaves_out_uncounted_ones(self):
        html = self.home("?want=open")
        self.assertEqual(results(html), ["Surging Sparks Booster Box", "Prismatic Evolutions Booster Pack",
                                         "Surging Sparks Elite Trainer Box"])
        self.assertIn('<h2 id="finder-title" class="finder__title">Packs to open</h2>', html)
        self.assertIn('<option value="">All pack products</option>', html)
        self.assertIn('<span class="finder-card__each">£3.00 a pack</span>', html)
        self.assertIn('aria-current="true">Open packs</a>', html)

    def test_the_other_shortcuts_choose_their_kinds(self):
        self.assertEqual(results(self.home("?want=play")), ["Foundations Starter Deck"])
        self.assertEqual(set(results(self.home("?want=gift"))), {"Surging Sparks Elite Trainer Box", "Surging Sparks Tin"})

    def test_budget_and_availability(self):
        names = results(self.home("?max=20"))
        self.assertEqual(set(names), {"Prismatic Evolutions Booster Pack", "Foundations Starter Deck", "Surging Sparks Tin"})
        self.assertIn('<a class="chip" href="/" data-finder-link aria-current="true">Up to £20<span', self.home("?max=20"))
        # In stock unless asked otherwise.
        self.assertNotIn("Surging Sparks Booster Bundle", results(self.home("?game=pokemon")))
        self.assertIn("Surging Sparks Booster Bundle", results(self.home("?game=pokemon&stock=all")))
        self.assertEqual(results(self.home("?stock=pre")), ["Prismatic Evolutions Super Premium Collection"])

    def test_nothing_matches_says_so_with_a_way_back(self):
        html = self.home("?game=magic-the-gathering&want=open")
        self.assertEqual(results(html), [])
        self.assertIn("Nothing matches all of these yet.", html)

    def test_choices_that_do_not_exist_are_left_out(self):
        html = self.home("?game=nope&max=lots&want=fly&sort=random")
        # Nothing valid chosen: the home page as it is with no choices (no deals here, so no list).
        self.assertNotIn('id="finder-title"', html)
        self.assertNotIn('content="noindex', html)
        self.assertIn('<a class="finder__tab" href="/" data-finder-link aria-current="page">All games</a>', html)

    def test_see_all_opens_the_same_list_on_the_search_page(self):
        html = self.home("?game=pokemon&want=open&max=50")
        self.assertIn('href="/search/?game=pokemon&amp;want=open&amp;max=50&amp;stock=in"', html)
        page = self.client.get(reverse("web:search") + "?game=pokemon&want=open&max=50&stock=in").content.decode()
        self.assertIn("Prismatic Evolutions Booster Pack", page)
        self.assertIn("Surging Sparks Elite Trainer Box", page)
        self.assertNotIn("Surging Sparks Booster Box", page)   # £108, over the budget
        # The search page keeps these choices when its own filters change.
        self.assertIn('<input type="hidden" name="want" value="open">', page)
        self.assertIn('<input type="hidden" name="max" value="50">', page)

    def test_the_script_gets_the_finder_alone_and_it_is_never_a_page_view(self):
        response = self.client.get(reverse("web:home") + "?game=pokemon&pane=finder", **HUMAN)
        html = response.content.decode()
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertTrue(html.lstrip().startswith('<nav class="finder__games"'))
        self.assertNotIn("<html", html)
        self.assertIn("Surging Sparks Booster Box", html)
        self.assertFalse(DailyPageView.objects.filter(kind=DailyPageView.Kind.HOME).exists())
        self.client.get(reverse("web:home"), **HUMAN)
        self.assertTrue(DailyPageView.objects.filter(kind=DailyPageView.Kind.HOME).exists())

    def test_the_same_queries_however_many_products(self):
        query = reverse("web:home") + "?game=pokemon&want=open&pane=finder"
        with CaptureQueriesContext(connection) as few:
            self.client.get(query)
        for n in range(6):
            self.product(self.ssp, f"Surging Sparks Booster Box {n}", "booster_box", f"{100 + n}.00", packs=36)
        cache.clear()
        with CaptureQueriesContext(connection) as many:
            self.client.get(query)
        self.assertEqual(len(many), len(few))
