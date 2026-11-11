"""Shop performance (web/shop_report.py): how the shops that pay a commission are listed, shown, clicked and bought
from, on an Admin page and as a spreadsheet. The owner, 11 Nov 2026: "produce a listings and performance report to
show how much our affiliate stores products are being shown, clicked on, clicked buy ... a report to export"."""

from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import AffiliateSale, DailyPageView, Listing, OutboundClick, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from web import shop_report

BROWSER = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile Safari/604.1"
AWIN = "https://www.awin1.com/cread.php?awinmid=1&ued={url}&clickref={click}"


class ReportCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        product_set = make_set(make_game())
        self.box = make_product(product_set, name="Surging Sparks Booster Box", slug="surging-sparks-booster-box",
                                product_type="booster_box")
        self.etb = make_product(product_set, name="Surging Sparks Elite Trainer Box", slug="surging-sparks-etb")
        self.harbour = make_retailer("Harbour Games", affiliate_url_template=AWIN)      # pays
        self.kestrel = make_retailer("Kestrel Cards", affiliate_url_template=AWIN)      # pays
        self.moor = make_retailer("Moor Toys")                                           # pays nothing
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)              # pays through eBay
        # The box: Harbour cheapest in stock, Kestrel dearer, Moor cheaper still but only on pre-order.
        self.box_harbour = make_listing(self.box, self.harbour, price="140.00")
        make_listing(self.box, self.kestrel, price="150.00")
        make_listing(self.box, self.moor, price="120.00", availability=Listing.Availability.PREORDER)
        # The ETB: Moor (who pays nothing) cheapest, Kestrel out of stock.
        make_listing(self.etb, self.moor, price="45.00")
        make_listing(self.etb, self.kestrel, price="40.00", availability=Listing.Availability.OUT_OF_STOCK)

    def click(self, listing, button, days_ago=0, **fields):
        click = OutboundClick.objects.create(listing=listing, product=listing.product, retailer=listing.retailer,
                                             source="product", button=button, visitor="t", ran_page=True, **fields)
        if days_ago:
            OutboundClick.objects.filter(pk=click.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return click

    def row(self, report, shop):
        return next(row for row in report["shops"] if row["pk"] == shop.pk)


class ShownFirstTests(ReportCase):
    def test_a_product_page_view_counts_the_shop_it_leads_with(self):
        self.client.get(self.box.get_absolute_url(), HTTP_USER_AGENT=BROWSER)
        self.client.get(self.box.get_absolute_url(), HTTP_USER_AGENT=BROWSER)
        # In stock first: Harbour's £140 in stock leads, not Moor's cheaper pre-order.
        row = DailyPageView.objects.get(kind=DailyPageView.Kind.LEAD)
        self.assertEqual((row.key, row.hits), (f"{self.harbour.pk}:{self.box.slug}", 2))
        self.assertEqual(DailyPageView.objects.get(kind=DailyPageView.Kind.PRODUCT, key=self.box.slug).hits, 2)

    def test_only_a_counted_view_counts_its_lead(self):
        self.client.get(self.box.get_absolute_url(), HTTP_USER_AGENT="Mozilla/5.0 (compatible; Googlebot/2.1)")
        self.client.get(self.box.get_absolute_url(), HTTP_USER_AGENT=BROWSER, HTTP_SEC_PURPOSE="prefetch;prerender")
        self.assertFalse(DailyPageView.objects.filter(kind=DailyPageView.Kind.LEAD).exists())

    def test_a_page_rendered_ahead_counts_its_lead_once_shown(self):
        self.client.get(reverse("web:note_api"), {"what": "view", "path": self.etb.get_absolute_url()},
                        HTTP_USER_AGENT=BROWSER)
        row = DailyPageView.objects.get(kind=DailyPageView.Kind.LEAD)
        self.assertEqual(row.key, f"{self.moor.pk}:{self.etb.slug}")

    def test_a_page_with_nothing_to_buy_counts_no_lead(self):
        nothing = make_product(self.box.product_set, name="Surging Sparks Tin", slug="surging-sparks-tin",
                               product_type="tin")
        make_listing(nothing, self.harbour, price="20.00", availability=Listing.Availability.OUT_OF_STOCK)
        self.client.get(nothing.get_absolute_url(), HTTP_USER_AGENT=BROWSER)
        self.assertTrue(DailyPageView.objects.filter(kind=DailyPageView.Kind.PRODUCT, key=nothing.slug).exists())
        self.assertFalse(DailyPageView.objects.filter(kind=DailyPageView.Kind.LEAD).exists())


class BuildTests(ReportCase):
    def test_only_shops_that_pay_are_in_it(self):
        report = shop_report.build(30)
        names = [row["name"] for row in report["shops"]]
        self.assertIn("Harbour Games", names)
        self.assertIn("Kestrel Cards", names)
        self.assertNotIn("Moor Toys", names)
        self.assertNotIn("eBay", names)   # pays, but has nothing listed, clicked or sold

    def test_listings_stock_and_cheapest_now(self):
        report = shop_report.build(30)
        harbour, kestrel = self.row(report, self.harbour), self.row(report, self.kestrel)
        self.assertEqual((harbour["listed"], harbour["in_stock"], harbour["cheapest"]), (1, 1, 1))
        # Kestrel: the box in stock and the ETB out of stock (no current price), cheapest on neither.
        self.assertEqual((kestrel["listed"], kestrel["in_stock"], kestrel["cheapest"]), (1, 1, 0))
        self.assertEqual(report["total"]["cheapest"], 1)   # the ETB leads with Moor, who pays nothing

    def test_views_shown_first_clicks_and_sales(self):
        today = timezone.localdate()
        DailyPageView.objects.create(date=today, kind="product", key=self.box.slug, hits=40)
        DailyPageView.objects.create(date=today - timedelta(days=3), kind="product", key=self.etb.slug, hits=10)
        DailyPageView.objects.create(date=today - timedelta(days=60), kind="product", key=self.box.slug, hits=999)
        DailyPageView.objects.create(date=today, kind="lead", key=f"{self.harbour.pk}:{self.box.slug}", hits=30)
        DailyPageView.objects.create(date=today, kind="lead", key=f"{self.moor.pk}:{self.etb.slug}", hits=10)
        for button in ("buy_now", "buy_now", "buy_bar", "price_list", "card", "deal", "ebay"):
            self.click(self.box_harbour, button)
        self.click(self.box_harbour, "buy_now", days_ago=40)                 # before the period
        self.click(self.box_harbour, "buy_now", suspect=True)                # a direct hit
        self.click(self.box_harbour, "buy_now", automated=True)              # a script's
        sale = self.click(self.box_harbour, "buy_now")
        AffiliateSale.objects.create(retailer=self.harbour, click=sale, ordered_on=today, status="approved",
                                     commission=Decimal("4.50"), order_value=Decimal("140.00"), reference="a")
        AffiliateSale.objects.create(retailer=self.harbour, ordered_on=today, status="pending",
                                     commission=Decimal("2.00"), reference="b")
        AffiliateSale.objects.create(retailer=self.harbour, ordered_on=today, status="declined",
                                     commission=Decimal("9.00"), reference="c")
        report = shop_report.build(30)
        harbour, kestrel = self.row(report, self.harbour), self.row(report, self.kestrel)
        # Views of the product pages that list them now: Harbour the box, Kestrel the box and the ETB.
        self.assertEqual((harbour["views"], kestrel["views"]), (40, 50))
        self.assertEqual((harbour["shown"], kestrel["shown"]), (30, 0))
        self.assertEqual(harbour["clicks"], 8)
        self.assertEqual((harbour["buy"], harbour["price_list"], harbour["lists"], harbour["search"]), (4, 1, 2, 1))
        self.assertEqual((harbour["sales"], harbour["commission"], harbour["pending"]), (2, Decimal("4.50"), Decimal("2.00")))
        self.assertEqual(harbour["order_value"], Decimal("140.00"))
        # Product page click rate: Buy now and price list clicks per 100 views. Buy now rate: per 100 times shown first.
        self.assertEqual(harbour["click_rate"], Decimal("12.5"))
        self.assertAlmostEqual(float(harbour["buy_rate"]), 4 * 100 / 30, places=6)
        self.assertEqual(report["lead_from"], today)
        self.assertEqual(report["total"]["shown"], 30)   # Moor's lead is not a paying shop's

    def test_a_short_period_leaves_older_days_out(self):
        self.click(self.box_harbour, "buy_now", days_ago=10)
        self.click(self.box_harbour, "buy_now")
        self.assertEqual(self.row(shop_report.build(7), self.harbour)["clicks"], 1)
        self.assertEqual(self.row(shop_report.build(30), self.harbour)["clicks"], 2)

    def test_the_queries_do_not_grow_with_the_shops(self):
        with CaptureQueriesContext(connection) as few:
            shop_report.build(30)
        for n in range(6):
            shop = make_retailer(f"Shop {n}", affiliate_url_template=AWIN)
            listing = make_listing(self.box, shop, price=f"{160 + n}.00")
            self.click(listing, "buy_now")
        with CaptureQueriesContext(connection) as many:
            shop_report.build(30)
        self.assertEqual(len(many), len(few))


class PageTests(ReportCase):
    def staff(self, editor=False):
        user = get_user_model().objects.create_user("owner", password="pw", is_staff=True, is_superuser=not editor)
        if editor:
            from guides.apps import EDITORS

            user.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(user)

    def test_only_the_owner_can_open_it(self):
        url = reverse("shop_report")
        self.assertEqual(url, "/admin/shop-report/")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get(reverse("shop_report_download")).status_code, 302)
        self.staff(editor=True)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_it_shows_each_shop_and_links_the_download(self):
        self.staff()
        self.click(self.box_harbour, "buy_now")
        response = self.client.get(reverse("shop_report"), {"days": 7})
        html = response.content.decode()
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn('<a href="?days=7" aria-current="page">7 days</a>', html)
        self.assertIn(f'href="{reverse("shop_report_download")}?days=7">Download spreadsheet</a>', html)
        self.assertIn("Harbour Games", html)
        self.assertNotIn("Moor Toys", html)
        self.assertIn("What is not counted", html)
        self.assertIn(f'<a href="{reverse("shop_report")}" class="section">Shop performance</a>',
                      self.client.get(reverse("admin:index")).content.decode())

    def test_a_period_not_offered_falls_back_to_30_days(self):
        self.staff()
        for days in ("365", "x", ""):
            with self.subTest(days=days):
                html = self.client.get(reverse("shop_report"), {"days": days}).content.decode()
                self.assertIn('<a href="?days=30" aria-current="page">30 days</a>', html)

    def test_the_spreadsheet_holds_every_sheet(self):
        from openpyxl import load_workbook

        self.staff()
        DailyPageView.objects.create(date=timezone.localdate(), kind="product", key=self.box.slug, hits=12)
        DailyPageView.objects.create(date=timezone.localdate(), kind="lead", key=f"{self.harbour.pk}:{self.box.slug}",
                                     hits=9)
        self.click(self.box_harbour, "buy_now")
        response = self.client.get(reverse("shop_report_download"), {"days": 30})
        self.assertEqual(response["Content-Type"],
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertRegex(response["Content-Disposition"],
                         r'attachment; filename="ripraptor-shop-performance-\d{4}-\d\d-\d\d-30-days\.xlsx"')
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book.sheetnames, ["Summary", "Shops", "Products", "Daily", "Notes"])
        shops = [row for row in book["Shops"].iter_rows(values_only=True)]
        self.assertEqual(shops[0][:9], ("Shop", "Programme", "Switched on", "Listed now", "In stock now", "Cheapest now",
                                        "Page views", "Shown first", "Clicks"))
        harbour = next(row for row in shops if row[0] == "Harbour Games")
        self.assertEqual(harbour[1:9], ("Awin", "Yes", 1, 1, 1, 12, 9, 1))
        products = [row for row in book["Products"].iter_rows(values_only=True)]
        box = next(row for row in products if row[0] == "Harbour Games" and row[1] == self.box.name)
        self.assertEqual(box[4:12], ("Yes", "Yes", 140, "Yes", 12, 9, 1, 1))
        daily = [row for row in book["Daily"].iter_rows(values_only=True)]
        self.assertEqual(daily[1][1:5], ("Harbour Games", 9, 1, 1))
        notes = [row[0] for row in book["Notes"].iter_rows(values_only=True) if row and row[0]]
        self.assertIn("What is not counted", notes)


class NotPageViewsTests(ReportCase):
    def test_shown_first_is_not_counted_as_page_views_elsewhere(self):
        # Insights, the dashboard and the traffic explainer total the pages people opened: the shop a page led with is
        # a count of its own, beside the view, never a second view.
        from catalogue import insights, traffic

        self.client.get(self.box.get_absolute_url(), HTTP_USER_AGENT=BROWSER)
        self.assertEqual(DailyPageView.objects.filter(kind=DailyPageView.Kind.LEAD).count(), 1)
        self.assertIn(DailyPageView.Kind.LEAD, traffic.NOT_PAGES)
        data = insights.report(7)
        self.assertEqual(data["total_views"], 1)
        self.assertNotIn("Shown first", [label for label, _hits in data["kinds"]])
