"""The dashboard as a text file to attach to a message (/dashboard/report.txt, web/report.py). The owner, 3 Nov
2026: "Can you produce a text report in admin download that I can just attach to you"."""

import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue import checks
from catalogue.models import DailyPageView, DailySearch, OutboundClick, Retailer, StockAlert
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from catalogue.tests_traffic import add_visitors
from web.tests_dashboard import NOW, TODAY, DashboardCase, build_fixtures

URL = "/dashboard/report.txt"


class ReportCase(DashboardCase):
    def report(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        return response


class AccessTests(ReportCase):
    def test_the_address_and_the_links_to_it(self):
        self.assertEqual(reverse("web:dashboard_report"), URL)
        self.assertContains(self.client.get("/dashboard/"), f'href="{URL}">Download a report</a>')
        self.assertContains(self.client.get(reverse("admin:index")), f'href="{URL}">Download a report')

    def test_anonymous_visitors_go_to_the_admin_sign_in(self):
        self.client.logout()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('admin:login')}?next={URL}")

    def test_a_signed_in_visitor_who_is_not_staff_is_refused(self):
        self.client.force_login(get_user_model().objects.create_user("visitor", password="pw"))
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("admin:login")))

    def test_it_downloads_as_a_dated_text_file_never_kept_or_indexed(self):
        response = self.report()
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="ripraptor-report-2026-10-18.txt"')
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_robots_txt_keeps_it_out(self):
        self.assertIn("Disallow: /dashboard/", self.client.get("/robots.txt").content.decode())


class ContentTests(ReportCase):
    def test_every_part_is_there_with_nothing_recorded(self):
        text = self.report().content.decode()
        self.assertTrue(text.startswith("RipRaptor report, Sunday 18 October 2026, 13:13 UK time"))
        for heading in ("AT A GLANCE", "BY DAY, LAST 28 DAYS", "WHERE PEOPLE CAME FROM", "PRODUCTS",
                        "SEARCHES ON THE SITE", "CLICKS TO SHOPS", "WHAT CLICKS ARE WORTH, LAST 30 DAYS",
                        "THE CATALOGUE", "SHOP HEALTH", "ALERTS, MESSAGES AND THE OFFER", "CLAUDE"):
            self.assertIn(f"\n{heading}\n{'=' * len(heading)}\n", text)
        self.assertNotIn("GUIDES", text)   # none published
        self.assertIn("Nothing recorded.", text)
        self.assertIn("Products on the site: 0", text)
        self.assertIn("they live only in Search Console", text)

    def test_the_figures_are_the_dashboards_and_insights(self):
        game = make_game()
        product = make_product(make_set(game), name="Prismatic Evolutions Elite Trainer Box", slug="pev-etb")
        shop = make_retailer("Harbour Games", last_ok_at=NOW)
        make_listing(product, shop, price="40.00")
        make_listing(product, make_retailer("Quay Cards", last_ok_at=NOW), price="42.00")
        for back in range(7):
            add_visitors(TODAY - timedelta(days=back), 4, source="google.com", hours=[9, 10], views=2)
            add_visitors(TODAY - timedelta(days=back), 2, source="instagram.com", hours=[9, 10], landing="product:pev-etb")
        add_visitors(TODAY - timedelta(days=20), 10, source="l.instagram.com", hours=[9], views=3)
        OutboundClick.objects.bulk_create([
            OutboundClick(product=product, retailer=shop, created_at=NOW - timedelta(hours=n + 1)) for n in range(5)])
        DailyPageView.objects.create(date=TODAY, kind=DailyPageView.Kind.PRODUCT, key="pev-etb", hits=9)
        DailySearch.objects.create(date=TODAY, query="prismatic", hits=3, results=1)
        DailySearch.objects.create(date=TODAY, query="unicorn box", hits=2, results=0)
        StockAlert.objects.create(product=product, email="fan@example.com", confirmed_at=NOW)
        text = self.report().content.decode()
        # At a glance: people (7 days) and visitors (28 days), clicks, the first page people arrived on.
        self.assertRegex(text, r"\nPeople\s+42\s+52\n")
        self.assertRegex(text, r"\nClicks to shops\s+5\s+5\n")
        self.assertRegex(text, r"\nPages per visitor\s+-\s+2\.1\n")
        self.assertRegex(text, r"Today to 13:13: \d+ people and 5 clicks")
        came_from = text.split("WHERE PEOPLE CAME FROM")[1].split("PRODUCTS")[0]
        self.assertRegex(came_from, r"Search engines\s+28\s+67%")
        self.assertRegex(came_from, r"google\.com\s+28\n")
        self.assertIn("l.instagram.com", came_from)
        self.assertIn("product:pev-etb", text)
        self.assertIn("Phone", text)
        self.assertIn("United Kingdom", text)
        # Products, searches, shops.
        self.assertIn("Prismatic Evolutions Elite Trainer Box", text)
        self.assertRegex(text, r"Prismatic Evolutions Elite Trainer Box\s+5\s+9\n")
        self.assertIn("prismatic", text)
        self.assertRegex(text, r"unicorn box\s+2\n")
        self.assertRegex(text, r"Harbour Games\s+5")
        self.assertIn("Products on the site: 1", text)
        self.assertIn("In stock at two shops or more (a real comparison): 1", text)
        self.assertIn("Offers on sale now: 2", text)
        self.assertIn("Product pages: 1", text)
        self.assertIn("Back in stock alerts confirmed: 1", text)
        # The 28 day day table has a row per day, with the week's people beside it.
        self.assertEqual(len(re.findall(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d \w\w\w ", text, re.M)), 28)

    def test_nothing_personal_is_in_it(self):
        build_fixtures(self)
        product = make_product(make_set(make_game("Lorcana", "lorcana")), name="Fabled Booster Box")
        StockAlert.objects.create(product=product, email="someone@example.com", confirmed_at=NOW)
        add_visitors(TODAY, 3, source="google.com", hours=[9])
        text = self.report().content.decode()
        self.assertNotIn("@", text)
        self.assertNotIn("someone", text)
        self.assertNotRegex(text, r"\bt\d+\b")   # no visitor token
        self.assertNotIn("Dear ", text)

    def test_house_style(self):
        from web.tests import CopyStyleTests

        build_fixtures(self)
        text = self.report().content.decode()
        self.assertNotIn("!", text)
        self.assertNotIn("—", text)
        for phrase in CopyStyleTests.BANNED:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text.lower())
        self.assertTrue(all(len(line) <= 160 for line in text.splitlines()), max(text.splitlines(), key=len))

    def test_money_and_claude_figures_read_as_pounds_and_dollars(self):
        build_fixtures(self)
        text = self.report().content.decode()
        self.assertRegex(text, r"Commission approved: £\d+\.\d\d")
        self.assertRegex(text, r"Spent this month: \$\d+\.\d\d of \$\d+\.\d\d")


class QueryTests(ReportCase):
    """Three times the shops, products, listings, clicks, visitors, searches and page views over the same days
    cost no more queries: nothing in the report is read a row at a time."""

    def fill(self, size, mark):
        shops = [make_retailer(f"Shop {mark}{n}", source_type=Retailer.Source.SHOPIFY, last_ok_at=NOW)
                 for n in range(6 * size)]
        products = [make_product(make_set(make_game(f"Game {mark}{n}", f"game-{mark}{n}"), slug=f"set-{mark}{n}"),
                                 name=f"Box {mark}{n}", slug=f"box-{mark}{n}") for n in range(6 * size)]
        for product, shop in zip(products, shops):
            make_listing(product, shop, price=Decimal("30.00"))
            make_listing(product, shops[(shops.index(shop) + 1) % len(shops)], price=Decimal("31.00"))
        OutboundClick.objects.bulk_create([
            OutboundClick(product=product, retailer=shop, created_at=NOW - timedelta(hours=n % 20))
            for n, (product, shop) in enumerate(zip(products * 6, shops * 6))
        ])
        for back in range(28):
            day = TODAY - timedelta(days=back)
            for source in ("google.com", "instagram.com", ""):
                add_visitors(day, 5 * size, views=3, source=source, hours=[8, 9, 10, 11], landing=f"product:{products[back % len(products)].slug}")
            for n in range(size):
                DailySearch.objects.create(date=day, query=f"missing {mark}{n} {back}", hits=2, results=0)
                DailySearch.objects.create(date=day, query=f"found {mark}{n}", hits=3, results=4)
                DailyPageView.objects.create(date=day, kind=DailyPageView.Kind.PRODUCT, key=products[(back + n) % len(products)].slug, hits=4)

    def test_the_report_adds_a_fixed_number_of_queries_to_the_dashboards_whatever_the_rows(self):
        """The dashboard's own sections are bounded (web/tests_dashboard.py); the report builds them fresh, reads
        Insights' 28 days and a few counts of its own, and adds the same number of queries however many rows."""
        checks.note_duplicate_counts([], NOW)
        counts = []
        for size, mark in ((1, "a"), (2, "b")):
            self.fill(size, mark)
            cache.clear()
            with CaptureQueriesContext(connection) as page:
                self.page()
            cache.clear()
            with CaptureQueriesContext(connection) as report:
                self.report()
            counts.append((len(page), len(report), self.tables(page), self.tables(report)))
        (page_few, report_few, page_t1, report_t1), (page_many, report_many, page_t2, report_t2) = counts
        self.assertEqual(report_few - page_few, report_many - page_many,
                         f"page: {page_t1}\n   -> {page_t2}\nreport: {report_t1}\n   -> {report_t2}")
        self.assertLessEqual(report_many - page_many, 60)

    @staticmethod
    def tables(captured):
        from collections import Counter

        counts = Counter(q["sql"].split("FROM ")[1].split()[0] if "FROM " in q["sql"] else q["sql"][:40]
                         for q in captured.captured_queries)
        return ", ".join(f"{name} {n}" for name, n in sorted(counts.items()))
