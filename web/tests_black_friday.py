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
from django.test import TestCase, override_settings
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
        self.assertIn("£10.00 under 30-day average", html)

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
        self.assertIn('<input type="checkbox" id="bf-sort" name="sort" value="pounds" checked>', html)
        self.assertIn('<input type="hidden" name="type" value="booster_box">', html)
        self.assertIn("Biggest £ saving first.", html)
        self.assertIn('href="/black-friday/?type=elite_trainer_box&amp;sort=pounds"', html)

    def test_the_lowest_recorded_switch(self):
        # The ETB has been cheaper before; the box and the bundle are at the lowest we have recorded.
        DailyLowestPrice.objects.create(product=self.etb, date=timezone.localdate() - timedelta(days=50), price="43.00")
        cache.clear()
        html = self.html("/black-friday/", lowest="on")
        self.assertIn('<input type="checkbox" id="bf-lowest" name="lowest" value="on" checked>', html)
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
        self.assertIn("Real Black Friday deals. Not inflated discounts.", html)
        self.assertIn('<p class="browse-head__intro">Pokémon booster boxes, Elite Trainer Boxes and booster bundles at '
                      "least 5% under their own 30-day average at a UK shop today.", html)

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
                self.assertIn('<span class="crow__badge">\u221210%</span>', html)
                self.assertIn('<span class="crow__note">£10.00 under 30-day average</span>', html)
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
        self.assertIn('<p class="card__deal card__deal--saving">£10.00 under 30-day average</p>', html)
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
        self.assertIn('<span class="crow__badge">Lowest yet</span>', html)
        self.assertIn('<span class="crow__note">Lowest recorded price</span>', html)

    def test_no_styles_of_its_own(self):
        from pathlib import Path

        css = (Path(__file__).resolve().parent / "static/css/site.css").read_text()
        self.assertNotIn(".bf-deal", css)
        self.assertNotIn(".bf__", css)
        # The one part named for Black Friday is the seasonal banner, which the home, Deals and game pages show too:
        # here it is the page's own head.
        template = (Path(__file__).resolve().parent / "templates/web/black_friday.html").read_text()
        self.assertEqual(re.findall(r'class="bf(?!-banner[_ "-])[-_ "]', template), [])


class SellingTests(PageCase):
    """The page sells (the owner, 27 Nov 2026, of a head that was a paragraph and a line of links over small rows:
    "doesnt make me feel like buying anything ... just a load of ... text and tiny products"): the banner as its head
    with its real figures, shortcuts as chips, the products as picture tiles with a badge, and the words after them."""

    wear = LookTests.wear

    def head(self, html):
        start = html.index('<div class="bf-banner bf-banner--lead">')
        return html[start:html.index("</div>", start)]

    def test_the_head_is_the_banner_with_the_pages_own_figures(self):
        self.wear(compact=True)
        head = self.head(self.html("/black-friday/"))
        self.assertIn('<p class="bf-banner__eyebrow">Starts Friday 27 November</p>', head)
        self.assertIn('<h1 class="browse-head__title">Black Friday 2026 TCG deals in the UK</h1>', head)
        # Three deals; the ETB is 12% under its average, the box £10.00 under its own: each the most on the page.
        self.assertIn('<strong class="bf-banner__figure">3</strong> genuine price drops', head)
        self.assertIn('<strong class="bf-banner__figure">12%</strong> biggest drop', head)
        self.assertIn('<strong class="bf-banner__figure">£10.00</strong> biggest saving', head)
        self.assertIn("Real Black Friday deals. Not inflated discounts. Every saving is against the product&#x27;s own "
                      "30-day average.", head)

    def test_the_line_over_the_heading_follows_the_weekend(self):
        self.wear(compact=True)
        for today, line in ((date(2026, 11, 28), "On now until Cyber Monday, 30 November"),
                            (date(2026, 12, 3), "Back on Friday 26 November 2027")):
            cache.clear()
            with self.subTest(today=today), mock.patch("web.black_friday.local_today", return_value=today):
                self.assertIn(f'<p class="bf-banner__eyebrow">{line}</p>', self.head(self.html("/black-friday/")))

    def test_the_figures_are_the_pages_before_its_filters_and_a_game_has_its_own(self):
        self.wear(compact=True)
        head = self.head(self.html("/black-friday/", type="bundle"))
        self.assertIn('<strong class="bf-banner__figure">3</strong> genuine price drops', head)
        self.assertIn('<strong class="bf-banner__figure">12%</strong>', head)
        magic = make_game(name="Magic: The Gathering", slug="magic")
        final = make_set(magic, name="Final Fantasy", slug="final-fantasy")
        for n, price in enumerate(("60.00", "61.00", "62.00")):
            self.product(f"Final Fantasy Bundle {n}", price, others=("99.00",), history=steady("80.00"),
                         product_set=final, kind="bundle")
        self.bands()
        self.wear(compact=True)
        self.assertIn('<strong class="bf-banner__figure">6</strong> genuine price drops',
                      self.head(self.html("/black-friday/")))
        head = self.head(self.html("/black-friday/pokemon/"))
        self.assertIn('<strong class="bf-banner__figure">3</strong> genuine price drops', head)
        self.assertIn('<strong class="bf-banner__figure">£10.00</strong>', head)

    def test_nothing_made_up_when_there_are_no_deals(self):
        DailyLowestPrice.objects.all().delete()
        Product.objects.update(price_band=None, price_basis="")
        self.wear(compact=True)
        html = self.html("/black-friday/")
        head = self.head(html)
        self.assertIn("Real Black Friday deals. Not inflated discounts.</p>", head)
        self.assertNotIn("bf-banner__figure", head)
        self.assertNotIn("Every saving is against", head)
        self.assertNotIn('class="chip"', html)
        self.assertNotIn("crow__badge", html)

    def test_each_game_is_a_chip_with_its_count(self):
        for compact in (True, False):
            self.wear(compact=compact)
            with self.subTest(compact=compact):
                self.assertIn('<a class="chip" href="/black-friday/pokemon/">Pokémon deals <span class="chip__count">3'
                              "</span></a>", self.html("/black-friday/"))
        # A game's own page links no games: the dock and the breadcrumbs do.
        self.assertNotIn("Black Friday deals by game", self.html("/black-friday/pokemon/"))

    def test_price_bands(self):
        self.wear(compact=True)
        html = self.html("/black-friday/")
        # The bundle (£27) and the ETB (£44), then the box (£90): only the bands that hold a deal.
        self.assertIn('<a class="chip" href="?price=25-50">£25 to £50 <span class="chip__count">2</span></a>', html)
        self.assertIn('<a class="chip" href="?price=50-100">£50 to £100 <span class="chip__count">1</span></a>', html)
        self.assertNotIn("Under £25", html)
        self.assertNotIn("£100 and over", html)
        html = self.html("/black-friday/", price="25-50")
        self.assertIn('<link rel="canonical" href="http://testserver/black-friday/">', html)
        self.assertEqual(html.count('<li class="crow">'), 2)
        self.assertNotIn(f'<a class="crow__link" href="{self.box.get_absolute_url()}"', html)
        self.assertIn("2 genuine price drops", html)
        # The chosen band is marked and leads back to every price; the types and the switches keep it.
        self.assertIn('<a class="chip" href="/black-friday/" aria-current="true">£25 to £50', html)
        self.assertIn('class="feed-tile__link" href="?price=25-50&amp;type=bundle"', html)
        self.assertEqual(html.count('<input type="hidden" name="price" value="25-50">'), 2)

    def test_a_band_with_the_other_choices_and_one_there_is_not(self):
        self.wear(compact=True)
        html = self.html("/black-friday/", price="25-50", type="bundle", sort="pounds")
        self.assertEqual(html.count('<li class="crow">'), 1)
        self.assertIn(f'<a class="crow__link" href="{self.bundle.get_absolute_url()}"', html)
        self.assertIn('href="?type=bundle&amp;sort=pounds"', html)        # the chosen band, back to every price
        html = self.html("/black-friday/", price="free")
        self.assertEqual(html.count('<li class="crow">'), 3)
        self.assertNotIn('name="price"', html)

    def test_the_bands_count_what_the_other_choices_leave(self):
        self.wear(compact=True)
        # Bundles: the one at £27. Boxes: the one at £90. Each sits in one band, so there is no choice to offer.
        self.assertNotIn("Deals by price", self.html("/black-friday/", type="bundle"))
        cheap = self.product("Surging Sparks Mini Bundle", "18.00", others=("24.00",), history=steady("20.00"),
                             kind="bundle")
        self.bands()
        self.wear(compact=True)
        html = self.html("/black-friday/", type="bundle")
        self.assertIn('<a class="chip" href="?type=bundle&amp;price=under-25">Under £25 <span class="chip__count">1'
                      "</span></a>", html)
        self.assertIn('<a class="chip" href="?type=bundle&amp;price=25-50">£25 to £50 <span class="chip__count">1'
                      "</span></a>", html)
        self.assertNotIn("£50 to £100", html)
        self.assertIn(f'<a class="crow__link" href="{cheap.get_absolute_url()}"',
                      self.html("/black-friday/", type="bundle", price="under-25"))
        # A band chosen that the type has nothing in still shows, marked, so it can be cleared.
        html = self.html("/black-friday/", type="booster_box", price="under-25")
        self.assertIn('<a class="chip" href="?type=booster_box" aria-current="true">Under £25 '
                      '<span class="chip__count">0</span></a>', html)
        self.assertEqual(html.count('<li class="crow">'), 0)

    def test_one_band_is_no_choice(self):
        Listing.objects.filter(product=self.box).delete()
        self.bands()
        cache.clear()
        self.wear(compact=True)
        self.assertNotIn("Deals by price", self.html("/black-friday/"))

    def test_the_products_are_picture_tiles_with_the_words_after_them(self):
        self.wear(compact=True)
        html = self.html("/black-friday/")
        self.assertIn('<ul class="crows crows--tiles" aria-labelledby="bf-list-title">', html)
        self.assertIn('<span class="crow__badge">\u221212%</span>', html)
        self.assertIn('<span class="crow__note">£6.00 under 30-day average</span>', html)
        tiles = html.index('<ul class="crows crows--tiles"')
        about = html.index("How these deals are chosen")
        self.assertLess(html.index("</h1>"), tiles)
        self.assertLess(tiles, about)
        self.assertLess(about, html.index("Booster boxes, Elite Trainer Boxes, booster bundles and other sealed"))
        self.assertLess(about, html.index("Black Friday 2026 is on Friday 27 November and Cyber Monday on Monday 30"))
        self.assertLess(about, html.index("Black Friday questions"))
        # Nothing but the banner, the shortcuts and the switches stands between the heading and the products.
        self.assertNotIn("browse-head__intro", html[:tiles])
        self.assertIn("3 genuine price drops", html[html.index("</h1>"):tiles])
        self.assertIn("Biggest % saving first.", html[html.index("</h1>"):tiles])

    def test_showing_the_top_is_said_under_the_list(self):
        self.wear(compact=True)
        with mock.patch.dict(black_friday.SHOWN, {None: 2}):
            html = self.html("/black-friday/")
        self.assertEqual(html.count('<li class="crow">'), 2)
        self.assertIn('<p class="section-head__note">Showing the top 2 of 3.</p>', html)
        self.assertLess(html.index("</ul>", html.index('<ul class="crows crows--tiles"')),
                        html.index("Showing the top 2 of 3."))

    def test_the_tiles_pictures_and_their_styles(self):
        from catalogue import images

        self.assertEqual(images.SLOTS["crow_tile"][1], "(min-width: 64rem) 12rem, 8.5rem")
        self.assertEqual(images.SLOTS["crow_lead"][1], "(min-width: 64rem) 12rem, (min-width: 40rem) 8.5rem, 12.5rem")
        row = (Path(__file__).resolve().parent / "templates/web/includes/compact_row.html").read_text()
        self.assertIn('{% if tiles and forloop.first %}{% product_image product "crow_lead" %}{% elif tiles %}'
                      '{% product_image product "crow_tile" %}{% else %}{% product_image product "crow" %}{% endif %}', row)
        css = (Path(__file__).resolve().parent / "static/css/site.css").read_text()
        tokens = (Path(__file__).resolve().parent / "static/css/tokens.css").read_text()
        self.assertIn("--tile-well: 8.5rem;", tokens)
        self.assertIn("--tile-lead-well: 12.5rem;", tokens)
        # Two across a phone, the first the width of the screen; three across a tablet; rows again with List chosen.
        self.assertIn("html:not(.view-list) .crows--tiles { grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertIn("html:not(.view-list) .crows--tiles > .crow:first-child { grid-column: 1 / -1; }", css)
        self.assertIn("@media (min-width: 40rem) { html:not(.view-list) .crows--tiles { grid-template-columns: "
                      "repeat(3, minmax(0, 1fr)); } }", css)
        # A saving, so the price green; on a tile it sits on the picture.
        self.assertIn(".crow__badge { justify-self: start;", css)
        badge = css[css.index(".crow__badge {"):css.index("}", css.index(".crow__badge {"))]
        self.assertIn("background: var(--color-price); color: var(--color-price-ink);", badge)
        self.assertIn("html:not(.view-list) .crows--tiles .crow__badge { position: absolute;", css)

    def test_a_choice_reads_no_more_than_the_page_itself(self):
        self.wear(compact=True)
        self.html("/black-friday/")                       # the look's own kept numbers and games

        def queries(**params):
            from web import look

            cache.clear()
            look.remember()
            with CaptureQueriesContext(connection) as found:
                self.html("/black-friday/", **params)
            return len(found)

        plain = queries()
        for params in ({"type": "bundle"}, {"price": "25-50"}, {"sort": "pounds"}, {"lowest": "on"},
                       {"type": "bundle", "price": "25-50", "sort": "pounds", "lowest": "on"}):
            with self.subTest(params=params):
                # The deals are read once whatever is chosen; one more only for the offers of the questions' product
                # when the choice leaves it off the list.
                self.assertLessEqual(queries(**params), plain + 1)


class FastChoiceTests(PageCase):
    """A switch, a type or a price band chooses from the deals the page already read, and loads in place. The owner,
    27 Nov 2026, of switches that built the whole page again from its top: "isn't a fast not smooth change in
    products. It moves the page up and takes ages to respond"."""

    wear = LookTests.wear

    def test_choosing_in_python_gives_what_the_query_gives(self):
        # More to choose from: one seen cheaper before, one at its lowest, kinds and prices apart.
        self.product("Surging Sparks Tin", "18.00", others=("24.00",), history=steady("20.00"), kind="tin")
        self.product("Surging Sparks Display", "180.00", others=("240.00",), history=steady("200.00"))
        DailyLowestPrice.objects.create(product=self.etb, date=timezone.localdate() - timedelta(days=50), price="43.00")
        self.bands()
        every = self.deals()
        self.assertEqual(len(every), 5)
        for kind in ("", "booster_box", "bundle", "tin"):
            for sort in ("", "pounds"):
                for lowest in (False, True):
                    with self.subTest(kind=kind, sort=sort, lowest=lowest):
                        self.assertEqual(black_friday.chosen_rows(every, kind=kind, sort=sort, lowest=lowest),
                                         self.deals(kind=kind, sort=sort, lowest=lowest))
        self.assertEqual([row.name for row in black_friday.chosen_rows(every, band=(None, 25))], ["Surging Sparks Tin"])
        self.assertEqual({row.name for row in black_friday.chosen_rows(every, band=(100, None))},
                         {"Surging Sparks Display"})
        # A price on a band's edge belongs to the band it opens.
        self.assertTrue(black_friday.in_band(Decimal("25.00"), 25, 50))
        self.assertFalse(black_friday.in_band(Decimal("25.00"), None, 25))

    @override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
    def test_while_pages_are_kept_a_choice_does_not_read_the_deals_again(self):
        self.wear(compact=True)
        self.html("/black-friday/")
        for params in ({"sort": "pounds"}, {"lowest": "on"}, {"type": "bundle"}, {"price": "25-50"}):
            with self.subTest(params=params), CaptureQueriesContext(connection) as found:
                html = self.html("/black-friday/", **params)
            self.assertIn('class="crows crows--tiles"', html)
            # Neither the deals nor the box figures: both set prices against the daily record.
            self.assertEqual([q["sql"] for q in found if "dailylowestprice" in q["sql"].lower()], [])
            # What is left is small: the offers of those shown, and of the questions' product when it is not among them.
            self.assertLessEqual(len(found), 4)
        # A game's page keeps its own.
        self.html("/black-friday/pokemon/")
        with CaptureQueriesContext(connection) as found:
            self.html("/black-friday/pokemon/", sort="pounds")
        self.assertEqual([q["sql"] for q in found if "dailylowestprice" in q["sql"].lower()], [])

    @override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
    def test_the_kept_deals_are_read_again_when_asked_and_forgotten_with_the_pages(self):
        from web import pagecache

        first = black_friday.shared()
        self.assertEqual(len(first["every"]), 3)
        self.assertEqual(first["shops"], [("Harbour Games", 3)])
        Listing.objects.filter(product=self.bundle).delete()
        self.bands()
        self.assertEqual(len(black_friday.shared()["every"]), 3)           # kept
        self.assertEqual(len(black_friday.shared(fresh=True)["every"]), 2)
        self.product("Surging Sparks Tin", "18.00", others=("24.00",), history=steady("20.00"), kind="tin")
        self.bands()
        pagecache.forget()
        self.assertEqual(len(black_friday.shared()["every"]), 3)

    def test_a_kept_deal_that_has_since_sold_out_is_left_off(self):
        self.wear(compact=True)
        held = black_friday.gathered()
        Listing.objects.filter(product=self.box).update(availability=Listing.Availability.OUT_OF_STOCK)
        with mock.patch.object(black_friday, "shared", return_value=held):
            html = self.html("/black-friday/")
        self.assertEqual(html.count('<li class="crow">'), 2)
        self.assertNotIn(f'<a class="crow__link" href="{self.box.get_absolute_url()}"', html)

    def test_the_page_marks_what_a_choice_changes_and_loads_it_in_place(self):
        self.wear(compact=True)
        html = self.html("/black-friday/")
        self.assertIn('src="/static/js/browse.js" defer></script>', html)
        start = html.index("<div data-browse>")
        region = html[start:html.index('<section class="cx-section" aria-labelledby="bf-about-title">')]
        # The head stays put; the shortcuts, the switches, the count, the list and its note are what changes.
        self.assertLess(html.index("</h1>"), start)
        for part in ('aria-label="Black Friday deals by game"', 'class="feed-tile__link" href="?type=booster_box" '
                     "data-browse-link", 'aria-label="Deals by price"', '<form method="get" action="/black-friday/" '
                     "data-autosubmit>", 'class="browse-head__count" role="status"', '<ul class="crows crows--tiles"'):
            with self.subTest(part=part):
                self.assertIn(part, region)
        self.assertTrue(region.rstrip().endswith("</div>"))
        self.assertNotIn("hub-faq", region)
        # The list is the region's own child, so it dims while its replacement comes.
        self.assertRegex(region, r'\n<ul class="crows crows--tiles"')
        # The figures follow the list from the foot of the page, and stand empty while there is nothing to show.
        self.assertIn('<section id="bf-figures" class="cx-section" aria-labelledby="bf-figures-title" data-browse-also>',
                      html)
        html = self.html("/black-friday/", type="booster_box", price="under-25")       # the box is £90: nothing
        self.assertIn('aria-labelledby="bf-figures-title" data-browse-also hidden>', html)
        self.assertNotIn('<table class="price-table', html)

    def test_the_loader_swaps_the_parts_that_follow_the_list_and_the_rows_dim(self):
        script = (Path(__file__).resolve().parent / "static/js/browse.js").read_text()
        self.assertIn('document.querySelectorAll("[data-browse-also][id]").forEach((part) => {', script)
        self.assertIn("const again = page && page.getElementById(part.id);", script)
        self.assertIn("show(fresh, page.title, page);", script)
        css = (Path(__file__).resolve().parent / "static/css/site.css").read_text()
        self.assertIn('[data-browse][aria-busy="true"] > .crows,', css)
        self.assertIn("[data-browse] > .crows,", css)

    def test_the_hub_is_kept_warm_in_its_season_only(self):
        from web import pagecache, warming

        with override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300):
            self.assertEqual(pagecache.event_paths(), ["/black-friday/"])
            self.assertIn("/black-friday/", pagecache.warm_paths([self.pokemon]))
            self.assertIn("page:/black-friday/", warming.missing([self.pokemon]))
            with mock.patch.object(black_friday, "shared", wraps=black_friday.shared) as shared, \
                    mock.patch.object(pagecache, "game_paths", return_value=[]):
                took = warming.warm_pages(only=["page:/black-friday/"])
            self.assertIn("page: /black-friday/", took)
            self.assertIn(mock.call(fresh=True), shared.call_args_list)
            self.assertTrue(pagecache.has("/black-friday/"))
            self.assertNotIn("page:/black-friday/", warming.missing([self.pokemon]))
            with mock.patch("web.black_friday.local_today", return_value=date(2026, 12, 3)):
                self.assertEqual(pagecache.event_paths(), [])
        self.assertEqual(pagecache.event_paths(), [])          # pages not kept: nothing to warm


class ShopSaleTests(PageCase):
    """What shops themselves call Black Friday deals, each checked. The owner, 27 Nov 2026: "Can also the crawlers look
    for any black Friday deals in their searches that could show up here in best offers from retailers", and of how to
    show them: "New section, each one checked". The readers mark a listing in its shop's own sale
    (catalogue/tests_shop_sale.py); here each is set against the price record, and a shop's label is never taken for a
    saving."""

    wear = LookTests.wear

    def mark(self, product, shop, at=None):
        Listing.objects.filter(product=product, retailer=shop).update(
            shop_sale=Listing.Sale.BLACK_FRIDAY, shop_sale_at=at or timezone.now())

    def items(self, game=None):
        return black_friday.sale_items(game, self.deals())[0]

    def test_a_real_drop_in_the_sale_of_the_shop_that_is_cheapest(self):
        self.mark(self.box, self.harbour)
        (item,) = self.items()
        self.assertEqual((item.product, item.shop, item.price), (self.box, "Harbour Games", Decimal("90.00")))
        self.assertEqual((item.verdict, item.percent, item.saving, item.lowest),
                         (black_friday.REAL, 10, Decimal("10.00"), False))

    def test_a_sale_price_another_shop_beats_is_cheaper_elsewhere_whatever_the_drop(self):
        self.mark(self.box, self.kestrel)                # Kestrel's £110, in its sale; Harbour has it for £90
        (item,) = self.items()
        self.assertEqual((item.shop, item.price), ("Kestrel Cards", Decimal("110.00")))
        self.assertEqual((item.verdict, item.gap), (black_friday.ELSEWHERE, Decimal("20.00")))

    def test_the_cheapest_price_that_is_no_drop_is_level_over_or_under_its_average(self):
        level = self.product("Journey Together Booster Box", "100.00", history=steady("100.00"))
        over = self.product("Paldean Fates Booster Bundle", "33.00", others=("40.00",), history=steady("30.00"),
                            kind="bundle")
        # Under its month's average, but the Price check does not call it a good price: raised, then cut.
        cut = self.product("Temporal Forces Booster Box", "92.00", history=steady("100.00"))
        self.bands()
        Product.objects.filter(pk=cut.pk).update(price_band=fair_price.FAIR)
        for product in (level, over, cut):
            self.mark(product, self.harbour)
        found = {item.product: item for item in self.items()}
        self.assertEqual({item.verdict for item in found.values()}, {black_friday.LEVEL})
        self.assertEqual((found[level].percent, found[over].percent, found[cut].percent), (0, -10, 8))
        self.assertNotIn(cut, self.deals())

    def test_too_few_days_of_prices_is_not_judged(self):
        new = self.product("Destined Rivals Booster Box", "120.00", others=("135.00",),
                           history=steady("130.00", days=5))
        self.bands()
        self.mark(new, self.harbour)
        (item,) = self.items()
        self.assertEqual((item.verdict, item.percent), (black_friday.UNKNOWN, 0))

    def test_only_a_mark_found_lately_on_stock_anyone_can_buy_counts(self):
        self.mark(self.box, self.harbour, timezone.now() - Listing.SALE_FRESH - timedelta(minutes=1))
        self.assertEqual(self.items(), [])                                   # stale: the shop took it out of its sale
        self.mark(self.box, self.harbour)
        self.assertEqual(len(self.items()), 1)
        Listing.objects.filter(product=self.box, retailer=self.harbour).update(sanity=Listing.Sanity.DOUBTFUL)
        self.assertEqual(self.items(), [])                                   # a doubtful price may not be this product
        Listing.objects.filter(product=self.box, retailer=self.harbour).update(
            sanity=Listing.Sanity.OK, availability=Listing.Availability.OUT_OF_STOCK)
        self.assertEqual(self.items(), [])                                   # sold out in that shop
        self.mark(self.etb, self.harbour)
        lorcana = make_game(name="Disney Lorcana", slug="lorcana")
        self.assertEqual([item.product for item in self.items(self.pokemon)], [self.etb])
        self.assertEqual(self.items(lorcana), [])

    def test_real_drops_come_first_biggest_first_then_cheaper_elsewhere_then_the_rest(self):
        level = self.product("Journey Together Booster Box", "100.00", history=steady("100.00"))
        new = self.product("Destined Rivals Booster Box", "120.00", others=("135.00",),
                           history=steady("130.00", days=5))
        self.bands()
        self.mark(self.bundle, self.kestrel)             # £33 against Harbour's £27
        for product in (new, level, self.box, self.etb):
            self.mark(product, self.harbour)
        self.assertEqual([(item.product, item.verdict) for item in self.items()], [
            (self.etb, black_friday.REAL), (self.box, black_friday.REAL), (self.bundle, black_friday.ELSEWHERE),
            (level, black_friday.LEVEL), (new, black_friday.UNKNOWN)])

    def test_the_section_lists_each_with_its_verdict_and_green_only_for_a_real_drop(self):
        level = self.product("Journey Together Booster Box", "100.00", history=steady("100.00"))
        self.bands()
        self.mark(self.box, self.harbour)
        self.mark(self.bundle, self.kestrel)
        self.mark(level, self.harbour)
        self.wear(compact=True)
        html = self.html("/black-friday/")
        start = html.index('<section id="bf-sales" class="cx-section" aria-labelledby="bf-sales-title">')
        section = html[start:html.index("</section>", start)]
        self.assertIn('<h2 id="bf-sales-title" class="cx-section__title">In shops&#x27; own Black Friday sales</h2>',
                      section)
        self.assertIn("Products a shop has put in its own Black Friday sale, each checked against our price record. A "
                      "sale label alone is not a saving. In a shop&#x27;s sale now: 3. Confirmed as a real drop by our "
                      "price record: 1.", section)
        self.assertIn('<ul class="crows crows--tiles crows--checked" aria-labelledby="bf-sales-title">', section)
        self.assertEqual(section.count('<li class="crow">'), 3)
        # A real drop: the page's own green badge, and whose sale it is in.
        self.assertIn('<span class="crow__badge">\u221210%</span>', section)
        self.assertIn('<span class="crow__note">A real drop, in the sale at Harbour Games</span>', section)
        # Every other verdict is plain: no green where there is no saving.
        self.assertIn('<span class="crow__badge crow__badge--plain">Cheaper elsewhere</span>', section)
        self.assertIn('<span class="crow__note crow__note--plain">The sale price at Kestrel Cards is £33.00 delivered</span>',
                      section)
        self.assertIn('<span class="crow__badge crow__badge--plain">No real drop</span>', section)
        self.assertIn('<span class="crow__note crow__note--plain">In the sale at Harbour Games, at about its 30-day '
                      "average</span>", section)
        self.assertNotIn("Showing", section)
        # The tile of the one that is cheaper elsewhere shows the cheaper shop's price, as every tile does.
        tile = section[section.index(self.bundle.get_absolute_url()):]
        tile = tile[:tile.index("</li>")]
        self.assertIn("£27.00", tile)
        self.assertIn("Harbour Games", tile)
        # It comes after the deals and before the words, and the head links to it.
        self.assertLess(html.index('<ul class="crows crows--tiles" aria-labelledby="bf-list-title">'), start)
        self.assertLess(start, html.index("How these deals are chosen"))
        self.assertIn('<a href="#bf-sales">See what is in shops&#x27; own sales (3)</a>', html[:start])
        # The page's own list is still its three deals: a shop's label adds none.
        self.assertIn("3 genuine price drops", html)
        self.assertNotIn("bf-sales", html[html.index("<div data-browse>"):start])

    def test_the_other_verdicts_words(self):
        over = self.product("Paldean Fates Booster Bundle", "33.00", others=("40.00",), history=steady("30.00"),
                            kind="bundle")
        cut = self.product("Temporal Forces Booster Box", "92.00", history=steady("100.00"))
        new = self.product("Destined Rivals Booster Box", "120.00", others=("135.00",),
                           history=steady("130.00", days=5))
        self.bands()
        Product.objects.filter(pk=cut.pk).update(price_band=fair_price.FAIR)
        tin = self.product("Surging Sparks Tin", "54.00", others=("58.00",), kind="tin",
                           history={**steady("55.00", days=30), **steady("62.00", days=60, start=31), 45: "54.00"})
        self.bands()
        Product.objects.filter(pk=cut.pk).update(price_band=fair_price.FAIR)
        for product in (over, cut, new, tin):
            self.mark(product, self.harbour)
        self.wear(compact=True)
        html = self.html("/black-friday/")
        for words in ('<span class="crow__note crow__note--plain">In the sale at Harbour Games, above its 30-day average</span>',
                      '<span class="crow__note crow__note--plain">In the sale at Harbour Games. Under its 30-day average, '
                      "but not a good price on our Price check</span>",
                      '<span class="crow__badge crow__badge--plain">Too new to check</span>',
                      '<span class="crow__note crow__note--plain">In the sale at Harbour Games. Too few days on our record '
                      "to check</span>",
                      # At its lowest recorded, with no clear fall on the month: a real one, said as the page says it.
                      '<span class="crow__badge">Lowest yet</span>'):
            with self.subTest(words=words):
                self.assertIn(words, html)

    def test_no_section_without_a_sale_or_out_of_season_and_a_game_has_its_own(self):
        self.wear(compact=True)
        self.assertNotIn("bf-sales", self.html("/black-friday/"))
        self.mark(self.box, self.harbour)
        cache.clear()
        self.wear(compact=True)
        self.assertIn('id="bf-sales"', self.html("/black-friday/"))
        self.assertIn('id="bf-sales"', self.html("/black-friday/pokemon/"))
        lorcana = make_game(name="Disney Lorcana", slug="lorcana")
        make_set(lorcana, name="Fabled", slug="fabled")
        self.assertNotIn("bf-sales", self.html("/black-friday/lorcana/"))
        cache.clear()
        self.wear(compact=True)
        with mock.patch("web.black_friday.local_today", return_value=date(2026, 12, 3)), \
                mock.patch.object(black_friday, "sale_items") as asked:
            self.assertNotIn("bf-sales", self.html("/black-friday/"))
        asked.assert_not_called()

    def test_every_real_drop_is_read_however_many_others_a_shop_puts_in_its_sale(self):
        level = self.product("Journey Together Booster Box", "100.00", history=steady("100.00"))
        other = self.product("Paldean Fates Booster Box", "100.00", history=steady("100.00"))
        self.bands()
        for product in (level, other, self.box, self.etb):
            self.mark(product, self.harbour)
        with mock.patch.object(black_friday, "SALE_MOST", 1):
            items, marked = black_friday.sale_items(None, self.deals())
        self.assertEqual(marked, 4)                                 # how many are in a sale, whatever is read
        self.assertEqual([item.product for item in items if item.verdict == black_friday.REAL], [self.etb, self.box])
        self.assertEqual(len(items), 3)                             # both real drops, and one of the others
        self.assertEqual(black_friday.sale_items(make_game(name="Disney Lorcana", slug="lorcana"), []), ([], 0))

    def test_more_than_the_page_shows_is_said(self):
        self.mark(self.box, self.harbour)
        self.mark(self.etb, self.harbour)
        self.mark(self.bundle, self.harbour)
        self.wear(compact=True)
        with mock.patch.object(black_friday, "SALE_SHOWN", 2):
            html = self.html("/black-friday/")
        section = html[html.index('<section id="bf-sales"'):]
        section = section[:section.index("</section>")]
        self.assertEqual(section.count('<li class="crow">'), 2)
        self.assertIn("In a shop&#x27;s sale now: 3. Confirmed as a real drop by our price record: 3.", section)
        self.assertIn('<p class="section-head__note">Showing 2 of 3.</p>', section)

    def test_the_classic_look_shows_them_as_cards(self):
        self.mark(self.box, self.kestrel)
        self.wear()
        html = self.html("/black-friday/")
        section = html[html.index('<section id="bf-sales" class="block"'):]
        section = section[:section.index("</section>")]
        self.assertEqual(section.count('<li class="card">'), 1)
        self.assertIn("The sale price at Kestrel Cards is £110.00 delivered", section)

    @override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
    def test_kept_with_the_pages_deals_so_a_choice_reads_none_of_it_again(self):
        self.mark(self.box, self.harbour)
        self.wear(compact=True)
        self.html("/black-friday/")
        with CaptureQueriesContext(connection) as found:
            html = self.html("/black-friday/", sort="pounds")
        self.assertIn('<span class="crow__note">A real drop, in the sale at Harbour Games</span>', html)
        self.assertEqual([q["sql"] for q in found if "shop_sale" in q["sql"] and "WHERE" in q["sql"]
                          and '"catalogue_product"' in q["sql"].split("FROM")[1].split("WHERE")[0]], [])
        self.assertLessEqual(len(found), 4)

    def test_the_queries_do_not_grow_with_the_sale(self):
        self.wear(compact=True)
        self.mark(self.box, self.harbour)

        def queries():
            from web import look

            cache.clear()
            look.remember()
            with CaptureQueriesContext(connection) as found:
                self.html("/black-friday/")
            return len(found)

        one = queries()
        self.mark(self.etb, self.harbour)
        self.mark(self.bundle, self.kestrel)
        self.assertEqual(queries(), one)

    def test_plain_badges_and_lines_and_the_price_check_on_a_checked_list(self):
        css = (Path(__file__).resolve().parent / "static/css/site.css").read_text()
        plain = css[css.index(".crow__badge--plain {"):css.index("}", css.index(".crow__badge--plain {"))]
        self.assertIn("background: var(--color-shelf); color: var(--color-ink-muted);", plain)
        self.assertNotIn("--color-price", plain)
        self.assertIn(".crow__note--plain { font-weight: var(--weight-body); color: var(--color-ink-muted); }", css)
        self.assertIn(".crows--checked .crow__check--desk { display: flex; }", css)
        row = (Path(__file__).resolve().parent / "templates/web/includes/compact_row.html").read_text()
        self.assertIn('class="crow__badge{% if product.row_plain %} crow__badge--plain{% endif %}"', row)
        self.assertIn('class="crow__note{% if product.row_plain %} crow__note--plain{% endif %}"', row)


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
