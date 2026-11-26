"""Black Friday (web/black_friday.py): genuine price drops on sealed TCG products at UK shops, at /black-friday/ and a
page for each game, from RipRaptor's own daily record of prices. The owner, 25 Nov 2026: "Real Black Friday deals. Not
inflated discounts."""

import json
import re
from datetime import date, timedelta
from pathlib import Path
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue import fair_price
from catalogue.models import DailyLowestPrice, Game, Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import black_friday

A = Listing.Availability
IN_SEASON = date(2026, 11, 25)
OUT_OF_SEASON = date(2026, 6, 1)


def record(product, prices):
    """Daily lows for ``product``: ``prices`` is {days ago: price}."""
    today = timezone.localdate()
    DailyLowestPrice.objects.bulk_create([
        DailyLowestPrice(product=product, date=today - timedelta(days=ago), price=Decimal(str(price)))
        for ago, price in prices.items()])


def steady(price, days=40, start=1):
    return {ago: price for ago in range(start, start + days)}


class DateTests(TestCase):
    def test_black_friday_is_the_day_after_the_fourth_thursday_of_november(self):
        self.assertEqual(black_friday.black_friday(2026), date(2026, 11, 27))
        self.assertEqual(black_friday.cyber_monday(2026), date(2026, 11, 30))
        self.assertEqual(black_friday.black_friday(2027), date(2027, 11, 26))
        self.assertEqual(black_friday.cyber_monday(2027), date(2027, 11, 29))
        self.assertEqual(black_friday.black_friday(2025), date(2025, 11, 28))
        # November starting on a Thursday.
        self.assertEqual(black_friday.black_friday(2029), date(2029, 11, 23))

    def test_the_season(self):
        state = lambda day: black_friday.season(day).state   # noqa: E731
        self.assertEqual(state(date(2026, 6, 1)), "before")
        self.assertEqual(state(date(2026, 11, 12)), "before")
        self.assertEqual(state(date(2026, 11, 13)), "soon")
        self.assertEqual(state(IN_SEASON), "soon")
        self.assertEqual(state(date(2026, 11, 27)), "on")
        self.assertEqual(state(date(2026, 11, 30)), "on")
        self.assertEqual(state(date(2026, 12, 1)), "after")
        self.assertTrue(black_friday.season(IN_SEASON).showing)
        self.assertFalse(black_friday.season(date(2026, 12, 1)).showing)
        # The new year is next year's Black Friday.
        self.assertEqual(black_friday.season(date(2027, 1, 2)).friday, date(2027, 11, 26))


class DealCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.pokemon = make_game(name="Pokémon", slug="pokemon")
        self.sparks = make_set(self.pokemon, name="Surging Sparks", slug="surging-sparks")
        self.harbour = make_retailer("Harbour Games")
        self.kestrel = make_retailer("Kestrel Cards")
        self.moor = make_retailer("Moor Toys")

    def product(self, name, today, others=("110.00",), history=None, kind="booster_box", product_set=None, **listing):
        """A product at ``today`` delivered at Harbour, the ``others`` at Kestrel and Moor, with ``history``."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        product = make_product(product_set or self.sparks, name=name, slug=slug, product_type=kind)
        make_listing(product, self.harbour, price=today, **listing)
        for shop, price in zip((self.kestrel, self.moor), others):
            make_listing(product, shop, price=price)
        record(product, history or {})
        return product

    def bands(self):
        fair_price.refresh_bands()

    def deals(self, **kwargs):
        return list(black_friday.deal_rows(**kwargs))


class DealRuleTests(DealCase):
    def test_a_price_well_under_its_30_day_average_is_a_deal(self):
        box = self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"))
        self.bands()
        (deal,) = self.deals()
        self.assertEqual(deal, box)
        black_friday.finished(deal)
        self.assertEqual(deal.average, Decimal("100.00"))
        self.assertEqual(deal.saving_money, Decimal("10.00"))
        self.assertEqual(deal.saving_percent, 10)

    def test_a_price_raised_and_then_cut_is_not_a_deal(self):
        # £80 for months, pushed up to £110 for the last 30 days, now "cut" to £100: under its 30-day average, but
        # over what it usually costs, so the Price check does not call it good.
        history = {**steady("80.00", days=60, start=31), **steady("110.00", days=30)}
        self.product("Surging Sparks Booster Box", "100.00", history=history)
        self.bands()
        self.assertEqual(self.deals(), [])

    def test_a_small_fall_or_a_price_that_never_moved_is_not_a_deal(self):
        self.product("Surging Sparks Booster Box", "98.00", history=steady("100.00"))
        self.product("Surging Sparks Elite Trainer Box", "50.00", history=steady("50.00"), kind="elite_trainer_box")
        self.bands()
        self.assertEqual(self.deals(), [])

    def test_only_in_stock_at_a_confirmed_delivered_price(self):
        self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"), availability=A.PREORDER)
        tin = self.product("Surging Sparks Tin", "18.00", others=("25.00",), history=steady("20.00"), kind="tin")
        Listing.objects.filter(product=tin, retailer=self.harbour).update(delivery_known=False)
        self.bands()
        names = [deal.name for deal in self.deals()]
        self.assertNotIn("Surging Sparks Booster Box", names)
        self.assertNotIn("Surging Sparks Tin", names)

    def test_a_doubtful_cheapest_price_is_not_a_deal(self):
        box = self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"))
        self.bands()
        Listing.objects.filter(product=box, retailer=self.harbour).update(sanity=Listing.Sanity.DOUBTFUL)
        self.assertEqual(self.deals(), [])

    def test_at_its_lowest_recorded_counts_only_as_the_lowest_yet(self):
        # £54 ties its lowest and is barely under the month's £54.69, but clearly under the year's £60: Lowest yet.
        etb = self.product("Surging Sparks Elite Trainer Box", "54.00", others=("58.00",), kind="elite_trainer_box",
                           history={**steady("55.00", days=30), **steady("62.00", days=60, start=31), 45: "54.00"})
        self.bands()
        etb.refresh_from_db()
        self.assertEqual(etb.price_band, fair_price.LOWEST)
        self.assertEqual(self.deals(), [etb])
        # The same tie on a price that has hardly moved all year is no deal.
        Product.objects.filter(pk=etb.pk).update(price_band=fair_price.GOOD_PRICE)
        self.assertEqual(self.deals(), [])

    def test_needs_two_weeks_of_prices_in_the_month(self):
        # Enough days in the year for the Price check, too few in the last month for an average worth quoting.
        history = {**steady("100.00", days=30, start=40), **steady("100.00", days=10)}
        self.product("Surging Sparks Booster Box", "90.00", history=history)
        self.bands()
        self.assertEqual(self.deals(), [])

    def test_needs_two_uk_shops(self):
        self.product("Surging Sparks Booster Box", "90.00", others=(), history=steady("100.00"))
        self.bands()
        self.assertEqual(self.deals(), [])

    def test_a_fall_too_large_to_be_real_is_left_out(self):
        self.product("Surging Sparks Booster Box", "20.00", history=steady("100.00"))
        self.bands()
        self.assertEqual(self.deals(), [])

    def test_sorted_by_percent_or_pounds_and_the_lowest_recorded_only(self):
        # The box: £10 (10%) under, at its lowest. The ETB: £6 (12%) under, but £45 was seen before.
        box = self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"))
        etb = self.product("Surging Sparks Elite Trainer Box", "44.00", others=("55.00",), kind="elite_trainer_box",
                           history={**steady("50.00"), 50: "43.00"})
        self.bands()
        self.assertEqual(self.deals(), [etb, box])
        self.assertEqual(self.deals(sort="pounds"), [box, etb])
        self.assertEqual(self.deals(lowest=True), [box])
        self.assertEqual(self.deals(kind="booster_box"), [box])


class FigureTests(DealCase):
    def test_the_figures_a_deal_shows(self):
        history = {**steady("100.00", days=30, start=8), **steady("95.00", days=7)}
        history[40] = "80.00"
        box = self.product("Surging Sparks Booster Box", "88.00", others=("92.00", "120.00"), history=history)
        Listing.objects.filter(product=box, retailer=self.moor).update(availability=A.OUT_OF_STOCK)
        self.bands()
        (deal,) = black_friday.ready(self.deals())
        # The 30-day average: 7 days at £95 and 23 at £100.
        self.assertEqual(deal.average, Decimal("98.83"))
        self.assertEqual(deal.week_change.direction, "down")
        self.assertEqual(deal.week_change.amount, Decimal("7.00"))    # the £95 recorded on the day a week ago
        self.assertEqual(deal.month_change.amount, Decimal("12.00"))
        self.assertFalse(deal.at_low)
        self.assertEqual((deal.low_price, deal.low_date), (Decimal("80.00"), timezone.localdate() - timedelta(days=40)))
        self.assertEqual(deal.in_stock_count, 2)
        self.assertEqual(deal.best.retailer, self.harbour)
        # Between shops: £4 less than Kestrel's £92, apart from the price history.
        self.assertEqual(deal.summary.saving, Decimal("4.00"))

    def test_a_rise_is_shown_as_one(self):
        # £100 for a month, £80 a week ago, £85 today: a drop on the month, a rise on the week.
        history = {**steady("100.00", days=30, start=8), **steady("80.00", days=7)}
        self.product("Surging Sparks Booster Box", "85.00", history={**history, **steady("100.00", days=60, start=38)})
        self.bands()
        (deal,) = black_friday.ready(self.deals())
        self.assertEqual((deal.week_change.direction, deal.week_change.amount), ("up", Decimal("5.00")))
        self.assertEqual((deal.month_change.direction, deal.month_change.amount), ("down", Decimal("15.00")))


class PageCase(DealCase):
    def setUp(self):
        super().setUp()
        self.box = self.product("Surging Sparks Booster Box", "90.00", history=steady("100.00"))
        self.etb = self.product("Surging Sparks Elite Trainer Box", "44.00", others=("55.00",),
                                history=steady("50.00"), kind="elite_trainer_box")
        self.bundle = self.product("Surging Sparks Booster Bundle", "27.00", others=("33.00",),
                                   history=steady("30.00"), kind="bundle")
        self.bands()
        patcher = mock.patch("web.black_friday.local_today", return_value=IN_SEASON)
        patcher.start()
        self.addCleanup(patcher.stop)

    def html(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def blocks(self, html):
        found = []
        for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            data = json.loads(raw)
            found.extend(data if isinstance(data, list) else [data])
        return found


class HubTests(PageCase):
    def test_addresses(self):
        self.assertEqual(reverse("web:black_friday"), "/black-friday/")
        self.assertEqual(reverse("web:black_friday_game", args=["pokemon"]), "/black-friday/pokemon/")

    def test_title_heading_description_and_canonical(self):
        html = self.html("/black-friday/")
        self.assertIn("<title>Black Friday 2026 TCG deals UK: real price drops | RipRaptor</title>", html)
        self.assertIn('<h1 class="browse-head__title">Black Friday 2026 TCG deals in the UK</h1>', html)
        self.assertIn("Real Black Friday deals. Not inflated discounts.", html)
        self.assertIn('<link rel="canonical" href="http://testserver/black-friday/">', html)
        self.assertNotIn('name="robots"', html)
        description = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
        self.assertIn("3 genuine price drops", description)
        self.assertIn("Black Friday 2026", description)
        self.assertIn("booster boxes", description)
        self.assertLessEqual(len(description), 160)

    def test_every_deal_links_its_product_page_and_buys_through_go(self):
        html = self.html("/black-friday/")
        for product in (self.box, self.etb, self.bundle):
            self.assertIn(f'href="{product.get_absolute_url()}"', html)
        for tag in re.findall(r'<a[^>]+href="/go/[^>]+>', html):
            self.assertIn('rel="sponsored nofollow noopener"', tag)
        self.assertIn("10% under 30-day average", html)

    def test_the_figures_are_a_table_and_the_two_kinds_of_saving_are_told_apart(self):
        html = self.html("/black-friday/")
        self.assertIn("The figures behind each price drop", html)
        self.assertIn("a difference, not a reduction", html)
        table = html[html.index('<table class="price-table price-table--wide">'):html.index("</table>")]
        row = table[table.index(self.box.get_absolute_url()):]
        row = row[:row.index("</tr>")]
        self.assertIn('<td class="price-table__today" data-label="Today, delivered">£90.00</td>', row)
        self.assertIn('<td class="price-table__usual" data-label="30-day average">£100.00</td>', row)
        self.assertIn('data-label="30 days"><span class="price-table__down">', row)
        self.assertIn("Down £10.00 (10%)", row)
        self.assertIn('data-label="Lowest recorded"><span class="price-table__down">Today', row)
        self.assertIn('<td data-label="Next shop">£20.00 dearer</td>', row)
        self.assertIn('<td data-label="In stock at">2 shops</td>', row)
        self.assertRegex(row, r'<td data-label="Checked"><time datetime="[^"]+">\d+ \w+, \d\d:\d\d</time></td>')

    def test_games_with_enough_deals_are_linked(self):
        html = self.html("/black-friday/")
        self.assertIn('href="/black-friday/pokemon/"', html)
        lorcana = make_game(name="Disney Lorcana", slug="lorcana")
        lore = make_set(lorcana, name="Fabled", slug="fabled")
        self.product("Fabled Booster Box", "90.00", history=steady("100.00"), product_set=lore)
        self.bands()
        cache.clear()
        self.assertNotIn('href="/black-friday/lorcana/"', self.html("/black-friday/"))

    def test_the_questions_are_answered_with_the_figures_and_given_again_as_faq(self):
        html = self.html("/black-friday/")
        self.assertIn("Where can I find the cheapest Pokémon cards this Black Friday?", html)
        self.assertIn("Are Pokémon booster boxes cheaper on Black Friday?", html)
        self.assertIn("Which UK TCG retailers have the best Black Friday 2026 deals?", html)
        self.assertIn("How does RipRaptor identify genuine price drops?", html)
        self.assertIn("When is Cyber Monday 2026?", html)
        self.assertIn("Cyber Monday 2026 is on Monday 30 November 2026", html)
        self.assertIn("Harbour Games (3)", html)
        (faq,) = [block for block in self.blocks(html) if block["@type"] == "FAQPage"]
        shown = re.findall(r'<h3 class="hub-faq__q">(.*?)</h3>', html)
        self.assertEqual([entry["name"] for entry in faq["mainEntity"]], [q.replace("&#x27;", "'") for q in shown])
        (crumbs,) = [block for block in self.blocks(html) if block["@type"] == "BreadcrumbList"]
        self.assertEqual(crumbs["itemListElement"][-1]["item"], "http://testserver/black-friday/")

    def test_filters_keep_the_canonical_on_the_page(self):
        html = self.html("/black-friday/", type="booster_box", sort="pounds")
        self.assertIn('<link rel="canonical" href="http://testserver/black-friday/">', html)
        self.assertIn(self.box.get_absolute_url(), html)
        self.assertNotIn(f'href="{self.etb.get_absolute_url()}"', html)
        # Each switch keeps the other choices, and the line under them says the order.
        self.assertIn('<input type="checkbox" name="sort" value="pounds" checked>', html)
        self.assertIn('<input type="hidden" name="type" value="booster_box">', html)
        self.assertIn("Biggest £ saving first.", html)
        self.assertIn('href="/black-friday/?type=elite_trainer_box&amp;sort=pounds"', html)

    def test_the_lowest_recorded_switch(self):
        # The ETB has been cheaper before; the box and the bundle are at the lowest we have recorded.
        DailyLowestPrice.objects.create(product=self.etb, date=timezone.localdate() - timedelta(days=50), price="43.00")
        cache.clear()
        html = self.html("/black-friday/", lowest="on")
        self.assertIn('<input type="checkbox" name="lowest" value="on" checked>', html)
        self.assertIn(f'href="{self.box.get_absolute_url()}"', html)
        self.assertNotIn(f'href="{self.etb.get_absolute_url()}"', html)
        self.assertIn("2 genuine price drops", html)

    def test_nothing_made_up_when_there_are_no_deals(self):
        DailyLowestPrice.objects.all().delete()
        Product.objects.update(price_band=None, price_basis="")
        html = self.html("/black-friday/")
        self.assertIn("No product is clearly under its 30-day average right now.", html)
        self.assertIn("No UK shop has a genuine price drop on this page today.", html)
        self.assertIn("0 genuine price drops", html)


class GamePageTests(PageCase):
    def test_a_game_with_enough_deals_is_published(self):
        html = self.html("/black-friday/pokemon/")
        self.assertIn("<title>Pokémon Black Friday 2026 deals UK: real price drops | RipRaptor</title>", html)
        self.assertIn('<h1 class="browse-head__title">Black Friday 2026 Pokémon deals in the UK</h1>', html)
        self.assertIn('<link rel="canonical" href="http://testserver/black-friday/pokemon/">', html)
        self.assertNotIn('name="robots"', html)
        self.assertIn("Real Black Friday deals. Not inflated discounts. Pokémon booster boxes, Elite Trainer Boxes and "
                      "booster bundles at least 5% under their own 30-day average at a UK shop today.", html)

    def test_a_game_with_too_few_is_not_indexed(self):
        Listing.objects.filter(product=self.bundle).delete()
        cache.clear()
        html = self.html("/black-friday/pokemon/")
        self.assertIn('<meta name="robots" content="noindex, follow">', html)
        self.assertNotIn("/black-friday/pokemon/", black_friday.sitemap_paths())

    def test_an_unknown_or_switched_off_game_is_not_found(self):
        self.assertEqual(self.client.get("/black-friday/no-such-game/").status_code, 404)
        Game.objects.filter(pk=self.pokemon.pk).update(is_active=False)
        cache.clear()
        self.assertEqual(self.client.get("/black-friday/pokemon/").status_code, 404)

    def test_the_queries_do_not_grow_with_the_deals(self):
        with CaptureQueriesContext(connection) as few:
            self.html("/black-friday/pokemon/")
        for n in range(5):
            self.product(f"Surging Sparks Tin {n}", f"{18 + n}.00", others=(f"{25 + n}.00",),
                         history=steady(f"{22 + n}.00"), kind="tin")
        self.bands()
        cache.clear()
        with CaptureQueriesContext(connection) as many:
            html = self.html("/black-friday/pokemon/")
        self.assertIn("8 genuine price drops", html)
        self.assertEqual(len(many), len(few))


class SitemapAndLinkTests(PageCase):
    def test_the_sitemap_has_the_hub_and_published_games(self):
        xml = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("http://testserver/black-friday/</loc>", xml)
        self.assertIn("http://testserver/black-friday/pokemon/</loc>", xml)

    def test_robots_still_keeps_search_out_and_lets_black_friday_in(self):
        robots = self.client.get("/robots.txt").content.decode()
        self.assertIn("Disallow: /search/", robots)
        self.assertNotIn("Disallow: /black-friday/", robots)

    def test_the_banner_shows_on_the_home_deals_and_game_pages_in_season(self):
        for url, link in (("/", "/black-friday/"), ("/deals/", "/black-friday/"),
                          (self.pokemon.get_absolute_url(), "/black-friday/pokemon/")):
            html = self.html(url)
            with self.subTest(url=url):
                self.assertIn('class="bf-banner"', html)
                self.assertIn(f'href="{link}"', html)
                self.assertIn("Real Black Friday deals. Not inflated discounts.", html)
                self.assertIn("until Cyber Monday on 30 November.", html)

    def test_no_banner_out_of_season_but_the_deals_page_keeps_its_link(self):
        with mock.patch("web.black_friday.local_today", return_value=OUT_OF_SEASON):
            cache.clear()
            self.assertNotIn('class="bf-banner"', self.html("/"))
            deals = self.html("/deals/")
            self.assertNotIn('class="bf-banner"', deals)
            self.assertIn('href="/black-friday/"', deals)
            # The page stays, for next year.
            html = self.html("/black-friday/")
            self.assertIn("Black Friday 2026 is on Friday 27 November and Cyber Monday on Monday 30 November.", html)

    def test_during_the_weekend_it_says_so(self):
        with mock.patch("web.black_friday.local_today", return_value=date(2026, 11, 28)):
            self.assertIn("Black Friday weekend is on now, until Cyber Monday on 30 November.",
                          self.html("/black-friday/"))

    def test_after_cyber_monday_it_names_next_year(self):
        with mock.patch("web.black_friday.local_today", return_value=date(2026, 12, 3)):
            html = self.html("/black-friday/")
            self.assertIn("Black Friday 2026 is over. The next is on Friday 26 November 2027", html)
            self.assertIn("Cyber Monday 2026 was on Monday 30 November 2026. Cyber Monday 2027 is on Monday 29 "
                          "November 2027.", html)


class LookTests(PageCase):
    """The page is a list page like a game's: the look the site is in, and its products as that look's own rows or
    cards, never a design of its own (web/design.py)."""

    def wear(self, compact=False, feed=False):
        from catalogue.models import SiteLook
        from web import look

        SiteLook.objects.update_or_create(pk=1, defaults={"compact": compact, "feed_home": feed})
        cache.clear()
        look.remember()

    def test_the_compact_look(self):
        self.wear(compact=True)
        for url in ("/black-friday/", "/black-friday/pokemon/"):
            html = self.html(url)
            with self.subTest(url=url):
                self.assertIn('<body class="feed-look compact-look"', html)
                self.assertIn('class="feed-dock"', html)           # the search box and games at the foot
                self.assertIn('class="cx-status"', html)           # the status line
                self.assertEqual(html.count('<li class="crow">'), 3)   # each product a compact row
                self.assertNotIn('class="card"', html)
                self.assertIn('<span class="crow__note">10% under 30-day average</span>', html)
                # The types as the look's tiles, each keeping the page.
                self.assertIn('class="feed-tile__link" href="?type=booster_box"', html)
                self.assertNotIn('class="chips quick"', html)

    def test_the_classic_look(self):
        self.wear()
        html = self.html("/black-friday/")
        self.assertNotIn("feed-look", html[html.index("<body"):html.index(">", html.index("<body"))])
        self.assertNotIn('class="feed-dock"', html)
        self.assertEqual(html.count('<li class="card">'), 3)       # each product the usual card
        self.assertNotIn('class="crow"', html)
        self.assertIn('<p class="card__deal card__deal--saving">10% under 30-day average</p>', html)
        self.assertIn('class="chips quick"', html)
        self.assertIn("Delivered prices checked ", html)            # the compact look's status line says it there

    def test_the_feed_look_has_the_dock_and_cards(self):
        self.wear(feed=True)
        html = self.html("/black-friday/")
        self.assertIn('<body class="feed-look"', html)
        self.assertIn('class="feed-dock"', html)
        self.assertEqual(html.count('<li class="card">'), 3)

    def test_a_product_here_for_its_lowest_recorded_price_says_that(self):
        # The same price for a year but for last month's rise, and now back at its lowest: no clear fall on the month.
        tin = self.product("Surging Sparks Tin", "54.00", others=("58.00",), kind="tin",
                           history={**steady("55.00", days=30), **steady("62.00", days=60, start=31), 45: "54.00"})
        self.bands()
        cache.clear()
        self.wear(compact=True)
        html = self.html("/black-friday/", type="tin")
        self.assertIn(f'href="{tin.get_absolute_url()}"', html)
        self.assertIn('<span class="crow__note">Lowest recorded price</span>', html)

    def test_no_styles_of_its_own(self):
        from pathlib import Path

        css = (Path(__file__).resolve().parent / "static/css/site.css").read_text()
        self.assertNotIn(".bf-deal", css)
        self.assertNotIn(".bf__", css)
        template = (Path(__file__).resolve().parent / "templates/web/black_friday.html").read_text()
        self.assertEqual(re.findall(r'class="bf[-_ "]', template), [])


class DesktopTests(PageCase):
    """On a desktop screen (the owner's brief of 26 Nov 2026): the same steps as a game's list, numbered for the steps it
    has, and the questions as folds, sent open so a phone, a search engine and a page without script read every answer."""

    wear = LookTests.wear

    def test_the_types_are_the_second_step_with_no_sets_above_them(self):
        self.wear(compact=True)
        html = self.html("/black-friday/")
        self.assertIn("1. Choose a game", html)
        self.assertIn("2. Choose a product type", html)
        self.assertNotIn("Pick a set or collection", html)
        self.assertNotIn("3. Choose a product type", html)

    def test_the_questions_fold_and_are_sent_open(self):
        for compact in (True, False):
            self.wear(compact=compact)
            html = self.html("/black-friday/")
            faq = html[html.index('<section class="hub-faq"'):]
            faq = faq[:faq.index("</section>")]
            with self.subTest(compact=compact):
                self.assertEqual(faq.count('<details class="hub-faq__item" open data-fold>'), 5)
                self.assertIn('<summary class="hub-faq__summary"><h3 class="hub-faq__q">When is Cyber Monday 2026?</h3>'
                              '</summary><p class="hub-faq__a">', faq)
        script = (Path(__file__).resolve().parent / "static/js/site.js").read_text()
        fold = script[script.index("details[data-fold]") - 300:script.index("details[data-fold]") + 80]
        self.assertIn('window.matchMedia("(min-width: 64rem)").matches) return;', fold)
        self.assertIn("item.open = false;", fold)
