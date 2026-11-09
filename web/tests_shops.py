"""The Shops we compare page (web.views.shops): every UK shop with a current price, with its delivery, products priced
and last check, so one public page states what the home page claims. The owner, 9 Nov 2026, on ChatGPT naming other
comparison sites first."""

from django.test import TestCase
from django.urls import reverse

from catalogue import pricing
from catalogue.models import Listing, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class ShopsPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        product_set = make_set(make_game())
        cls.box = make_product(product_set, name="Prismatic Evolutions Booster Box", slug="pe-box")
        cls.etb = make_product(product_set, name="Prismatic Evolutions Elite Trainer Box", slug="pe-etb")
        cls.harbour = make_retailer("Harbour Games", delivery_cost="3.99", free_delivery_over="50")
        make_listing(cls.box, cls.harbour)
        make_listing(cls.etb, cls.harbour)
        cls.free = make_retailer("Free Post Cards", delivery_cost="0.00")
        make_listing(cls.box, cls.free)
        cls.threshold = make_retailer("Zatu Games", free_delivery_over="100")
        make_listing(cls.box, cls.threshold)
        cls.unknown = make_retailer("Apex TCG")
        make_listing(cls.box, cls.unknown)
        cls.up_to = make_retailer("Card Empire", delivery_cost="1.95", delivery_cost_up_to="20")
        make_listing(cls.box, cls.up_to)
        # Not compared: a shop whose only price is stale, one switched off, a marketplace, and one whose only
        # listing is kept out as impossible.
        make_listing(cls.box, make_retailer("Stale Shop"), hours_ago=900)
        make_listing(cls.box, make_retailer("Hidden Shop", is_active=False))
        make_listing(cls.box, make_retailer("eBay", source_type=Retailer.Source.EBAY))
        make_listing(cls.box, make_retailer("Odd Prices"), sanity=Listing.Sanity.EXCLUDED)

    def test_every_compared_shop_is_listed_a_to_z_with_delivery_count_and_check(self):
        response = self.client.get(reverse("web:shops"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([shop.name for shop in response.context["shops"]],
                         ["Apex TCG", "Card Empire", "Free Post Cards", "Harbour Games", "Zatu Games"])
        self.assertEqual(response.context["shops_compared"], 5)
        self.assertEqual(pricing.shops_compared(), 5)   # the home page's count is the same five
        lines = {shop.name: (shop.delivery_line, shop.priced) for shop in response.context["shops"]}
        self.assertEqual(lines, {
            "Apex TCG": ("Not yet known: prices show as plus delivery", 1),
            "Card Empire": ("£1.95 on orders under £20.00", 1),
            "Free Post Cards": ("Free", 1),
            "Harbour Games": ("£3.99, free over £50.00", 2),
            "Zatu Games": ("Free over £100.00", 1),
        })
        html = response.content.decode()
        self.assertIn('<a href="https://www.harbour-games.example/" rel="sponsored nofollow noopener" target="_blank">Harbour Games</a>', html)
        self.assertIn("1 hour ago", html)
        self.assertIn("<title>Shops we compare: 5 UK shops, delivery included | RipRaptor</title>", html)
        self.assertIn("RipRaptor compares prices from 5 UK shops.", html)
        for name in ("Stale Shop", "Hidden Shop", "eBay", "Odd Prices"):
            self.assertNotIn(name, html)

    def test_one_query_for_the_shops(self):
        from django.core.cache import cache

        cache.clear()
        with self.assertNumQueries(2):   # the shops, and the site's wording
            self.client.get(reverse("web:shops"))

    def test_the_site_points_to_it(self):
        url = reverse("web:shops")
        self.assertContains(self.client.get(reverse("web:home")), f'<a href="{url}">Shops we compare</a>')
        about = self.client.get(reverse("web:about"))
        self.assertContains(about, f'<a class="button button--quiet" href="{url}">Shops we compare</a>')
        self.assertContains(about, "Today that means 5 UK shops, with delivery included in every price")
        self.assertNotContains(about, "retailer-list")
        self.assertContains(self.client.get("/sitemap.xml"), f"<loc>http://testserver{url}</loc>")
        self.assertContains(self.client.get("/llms.txt"), f"[Shops we compare](http://testserver{url})")


class WhyRipRaptorTests(TestCase):
    """The Why RipRaptor section on the how it works page and in llms.txt (the owner, 9 Nov 2026: what makes
    RipRaptor better, so search engines and AI assistants can quote it)."""

    @classmethod
    def setUpTestData(cls):
        ShopsPageTests.setUpTestData.__func__(cls)

    def test_how_it_works_says_why_with_the_live_numbers(self):
        html = self.client.get(reverse("web:about")).content.decode()
        self.assertIn('<h2 id="why-title">Why RipRaptor</h2>', html)
        for point in ("The price you will pay", "Only stock you can buy", "Prices you can trust", "Honest about deals",
                      "UK shops side by side", "Alerts that do the waiting", "Independent"):
            self.assertIn(f"<dt>{point}</dt>", html)
        self.assertIn("We compare 5 UK shops, each listed on our Shops page", html)
        self.assertIn("one we have not been able to check for 72 hours stops counting", html)
        self.assertIn("A shop running a draw or raffle is not shown as in stock", html)
        # Before How prices are worked out: the first thing after the introduction.
        self.assertLess(html.index('id="why-title"'), html.index('id="prices-title"'))

    def test_ai_assistants_read_the_same_points(self):
        text = self.client.get("/llms.txt").content.decode()
        self.assertIn("## Why RipRaptor", text)
        self.assertIn("- The price you will pay: Every price includes the shop's standard UK delivery", text)
        self.assertIn("- UK shops side by side: We compare 5 UK shops", text)
