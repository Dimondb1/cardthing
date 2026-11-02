"""The home page's first screen (the owner, 2 Nov 2026: "when you land on the Rip Raptor site you don't even see any
deals, it's just a load of information. Is that maybe a reason why people leave the site?"): Best prices right now
first, real deals at UK shops whether they pay us or not; on a phone only the headline and the search above it; the
analytics question as a slim bar; and the dashboard counting how many who land on the home page open a product."""

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue.models import DailyPageView, DailyVisitor, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web.tests_dashboard import TODAY, DashboardCase, visible

CSS = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "site.css").read_text(encoding="utf-8")
HUMAN = {"HTTP_USER_AGENT": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36"}


def section(html, title_id):
    start = html.index(f'id="{title_id}"')
    return html[start:html.index("</section>", start)]


class BestPricesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.pokemon = make_game()
        self.magic = make_game("Magic: The Gathering", "magic-the-gathering")
        self.ssp = make_set(self.pokemon, name="Surging Sparks", slug="ssp", code="SSP")
        self.fdn = make_set(self.magic, name="Foundations", slug="fdn", code="FDN")
        self.harbour = make_retailer("Harbour Games")
        self.affiliate = make_retailer("Northgate Cards", affiliate_url_template="https://aff.example/?u={url}")
        self.dear = make_retailer("Tollgate TCG")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)

    def deal(self, product_set, name, slug, shop, price="50.00", next_price="70.00"):
        product = make_product(product_set, name=name, slug=slug, product_type="booster_box")
        make_listing(product, shop, price=price)
        make_listing(product, self.dear, price=next_price)
        return product

    def home(self):
        cache.clear()
        return self.client.get(reverse("web:home")).content.decode()

    def test_the_first_row_is_real_deals_at_uk_shops_and_ebay_is_left_to_rips_deals(self):
        box = self.deal(self.ssp, "Surging Sparks Booster Box", "ssp-box", self.harbour)
        ebay = self.deal(self.ssp, "Surging Sparks Elite Trainer Box", "ssp-etb", self.ebay)
        html = self.home()
        best = section(html, "finder-title")
        self.assertIn("Best prices right now</h2>", best)
        self.assertIn(box.name, best)
        self.assertNotIn(ebay.name, best)
        self.assertIn('data-compare="best"', best)
        self.assertIn("Cheapest delivered prices at UK shops that beat the next shop or sit at a 30-day low. Any shop, "
                      "whether it pays us or not.", best)
        self.assertIn(ebay.name, section(html, "featured-title"))
        # They are the finder's products until anything is chosen, the first on the page.
        self.assertLess(html.index('id="finder-title"'), html.index('id="featured-title"'))
        # The visitor's own games first, sorted straight after the finder, its game list on the page once.
        end = html.index("data-finder-said></p>\n</section>\n") + len("data-finder-said></p>\n</section>\n")
        self.assertTrue(html[end:].startswith('<script id="interest-games" type="application/json">'))
        self.assertEqual(html.count('id="interest-games"'), 1)

    def test_a_deal_at_a_shop_that_pays_shows_once_in_the_first_row(self):
        paid = self.deal(self.ssp, "Surging Sparks Booster Box", "ssp-box", self.affiliate)
        html = self.home()
        self.assertIn(paid.name, section(html, "finder-title"))
        self.assertNotIn('id="featured-title"', html)

    def test_no_deal_without_evidence_or_a_confirmed_delivered_price(self):
        small = self.deal(self.ssp, "Surging Sparks Booster Box", "ssp-box", self.harbour, next_price="51.00")
        html = self.home()
        self.assertNotIn(small.name, html[:html.index('id="games-title"')] if 'id="games-title"' in html else html)
        self.assertNotIn('id="finder-title" class="finder__title">Best prices right now', html)

    def test_the_games_people_view_most_come_first(self):
        # The Magic box is the bigger saving, but people open Pokémon pages more.
        pokemon = self.deal(self.ssp, "Surging Sparks Booster Box", "ssp-box", self.harbour, next_price="53.00")
        magic = self.deal(self.fdn, "Foundations Play Booster Box", "fdn-box", self.harbour, next_price="60.00")
        best = section(self.home(), "finder-title")
        self.assertLess(best.index(magic.name), best.index(pokemon.name))
        DailyPageView.objects.create(kind=DailyPageView.Kind.PRODUCT, key=pokemon.slug, date=timezone.localdate(),
                                     hits=30)
        best = section(self.home(), "finder-title")
        self.assertLess(best.index(pokemon.name), best.index(magic.name))
        # Views from before the window do not count.
        DailyPageView.objects.all().update(date=timezone.localdate() - timedelta(days=40))
        best = section(self.home(), "finder-title")
        self.assertLess(best.index(magic.name), best.index(pokemon.name))


class FirstScreenStyleTests(TestCase):
    def test_on_a_phone_only_the_headline_and_the_search_come_before_the_prices(self):
        self.assertIn("@media (max-width: 47.99rem) { .hero__eyebrow, .hero__subtitle { display: none; } }", CSS)

    def test_the_analytics_question_is_a_slim_bar_along_the_foot(self):
        rule = CSS[CSS.index(".consent {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("left: 0; right: 0;", rule)
        self.assertIn("border-radius: 0;", rule)
        self.assertIn(".consent .install__text { flex: 1 1 14rem; font-size: var(--text-xs);", CSS)
        # One short question, so the bar stays a line or two on a phone; both answers stay equally plain.
        from content.registry import REGISTRY

        self.assertEqual(REGISTRY["site.consent.text"].default, "Can we use Google Analytics cookies to see which pages help?")
        template = (Path(settings.BASE_DIR) / "web" / "templates" / "web" / "includes" / "consent.html").read_text()
        self.assertEqual(template.count('class="button button--quiet button--small"'), 2)


class HomeToProductTests(TestCase):
    def setUp(self):
        cache.clear()
        self.product = make_product(make_set(make_game()), name="Surging Sparks Booster Box", slug="ssp-box",
                                    product_type="booster_box")
        make_listing(self.product, make_retailer("Harbour Games"), price="50.00")

    def test_a_visitor_who_lands_on_home_and_opens_a_product_is_noted(self):
        self.client.get(reverse("web:home"), REMOTE_ADDR="203.0.113.5", **HUMAN)
        self.client.get(self.product.get_absolute_url(), REMOTE_ADDR="203.0.113.5", **HUMAN)
        self.client.get(reverse("web:home"), REMOTE_ADDR="203.0.113.9", **HUMAN)
        rows = {row.views: row for row in DailyVisitor.objects.all()}
        self.assertEqual((rows[2].landing, rows[2].opened_product), ("home", True))
        self.assertEqual((rows[1].landing, rows[1].opened_product), ("home", False))


class DashboardHomeToProductTests(DashboardCase):
    def visitor(self, n, landing, opened, browser=True):
        return DailyVisitor.objects.create(date=TODAY, token=f"t{n}", landing=landing, opened_product=opened, views=2,
                                           browser=browser, first_seen=timezone.now())

    def test_the_share_who_land_on_home_and_open_a_product(self):
        self.visitor(1, "home", True)
        self.visitor(2, "home", False)
        self.visitor(3, "home", False)
        self.visitor(4, "product:ssp-box", True)   # landed elsewhere
        self.visitor(5, "home", None)              # from before it was kept
        words = " ".join(visible(self.page().content.decode()).split())
        self.assertIn("Home page to a product 33% 1 of 3 who landed there", words)

    def test_nothing_shown_before_anyone_landed_there(self):
        self.visitor(1, "product:ssp-box", True)
        self.assertNotIn("Home page to a product", self.page().content.decode())
