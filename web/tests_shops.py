"""The Shops we compare page was taken down (the owner, 14 Nov 2026: a public list of every shop "means people could
do the same"). Its address forwards for good to the home page; the count of shops compared stays, and every product
page still names the shop beside each price."""

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from catalogue import pricing
from catalogue.models import Listing, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


class ShopsCase(TestCase):
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

    def setUp(self):
        # The shop count is kept for a few minutes (pricing.shops_compared): start each test without another's.
        cache.clear()


class ShopsPageGoneTests(ShopsCase):
    def test_the_old_address_forwards_for_good_to_the_home_page(self):
        response = self.client.get("/shops/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], reverse("web:home"))

    def test_nothing_points_to_it_any_more_but_the_count_stays(self):
        self.assertEqual(pricing.shops_compared(), 5)
        for url in (reverse("web:home"), reverse("web:about"), "/sitemap.xml", "/llms.txt"):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn("/shops/", html)
                self.assertNotIn("Shops we compare</a>", html)
        about = self.client.get(reverse("web:about"))
        self.assertContains(about, "Today that means 5 UK shops, with delivery included in every price")
        self.assertContains(about, "Every price on RipRaptor names its shop, includes that shop&#x27;s standard UK "
                                   "delivery and says when we last checked it.")


class WhyRipRaptorTests(TestCase):
    """The Why RipRaptor section on the how it works page and in llms.txt (the owner, 9 Nov 2026: what makes
    RipRaptor better, so search engines and AI assistants can quote it)."""

    @classmethod
    def setUpTestData(cls):
        ShopsCase.setUpTestData.__func__(cls)

    def setUp(self):
        cache.clear()

    def test_how_it_works_says_why_with_the_live_numbers(self):
        html = self.client.get(reverse("web:about")).content.decode()
        self.assertIn('<h2 id="why-title">Why RipRaptor</h2>', html)
        for point in ("The price you will pay", "Only stock you can buy", "Prices you can trust", "Honest about deals",
                      "UK shops side by side", "Alerts that do the waiting", "Independent"):
            self.assertIn(f"<dt>{point}</dt>", html)
        self.assertIn("We compare 5 UK shops, each named beside its price", html)
        self.assertIn("one we have not been able to check for 72 hours stops counting", html)
        self.assertIn("A shop running a draw or raffle is not shown as in stock", html)
        # Before How prices are worked out: the first thing after the introduction.
        self.assertLess(html.index('id="why-title"'), html.index('id="prices-title"'))

    def test_ai_assistants_read_the_same_points(self):
        text = self.client.get("/llms.txt").content.decode()
        self.assertIn("## Why RipRaptor", text)
        self.assertIn("- The price you will pay: Every price includes the shop's standard UK delivery", text)
        self.assertIn("- UK shops side by side: We compare 5 UK shops", text)
