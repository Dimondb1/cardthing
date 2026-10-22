"""What clicks earn: the click's number in the shop's link, and sales imported from a network's report
(catalogue/sales.py)."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import AffiliateSale, OutboundClick, Retailer
from .sales import import_report
from .testing import make_game, make_listing, make_product, make_retailer, make_set

BROWSER = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
AWIN = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2&clickref={click}&ued={url}"


class SalesTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.product = make_product(make_set(self.game), slug="pre-etb")
        self.harbour = make_retailer("Harbour Games", affiliate_url_template=AWIN)
        self.listing = make_listing(self.product, self.harbour)
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY, website="https://www.ebay.co.uk/")

    def test_the_clicks_number_goes_where_the_link_format_says(self):
        self.assertEqual(self.harbour.outbound_url("https://shop.example/p", click=42),
                         "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2&clickref=42&ued=https%3A%2F%2Fshop.example%2Fp")
        self.assertIn("clickref=&ued=", self.harbour.outbound_url("https://shop.example/p"))
        response = self.client.get(self.listing.get_outbound_url() + "?b=buy_now", HTTP_USER_AGENT=BROWSER,
                                   HTTP_REFERER="http://testserver/products/pre-etb/")
        click = OutboundClick.objects.get()
        self.assertIn(f"clickref={click.pk}&", response["Location"])

    def test_an_awin_report_is_matched_to_its_clicks_and_games_and_updated_not_doubled(self):
        click = OutboundClick.objects.create(product=self.product, retailer=self.harbour, listing=self.listing)
        yesterday = date.today() - timedelta(days=1)
        report = (
            "Transaction ID,Transaction Date,Advertiser Name,Sale Amount,Commission Amount,Commission Status,Click Ref\n"
            f"T1,{yesterday:%d/%m/%Y} 14:03,Harbour Games,£59.99,£2.40,pending,{click.pk}\n"
            f"T2,{yesterday.isoformat()},HARBOUR  games,30.00,1.20,approved,\n"
            f"T3,{yesterday.isoformat()},Unknown Shop,30.00,1.20,approved,\n"
            f"T4,{(date.today() + timedelta(days=3)).isoformat()},Harbour Games,30.00,1.20,approved,\n"
        )
        result = import_report(report.encode("utf-8-sig"))
        self.assertEqual((result.added, result.updated), (2, 0))
        self.assertEqual([line for line, _ in result.skipped], [4, 5])
        first = AffiliateSale.objects.get(reference="T1")
        self.assertEqual((first.click, first.game, first.status, first.commission, first.order_value, first.ordered_on),
                         (click, self.game, AffiliateSale.Status.PENDING, Decimal("2.40"), Decimal("59.99"), yesterday))
        self.assertIsNone(AffiliateSale.objects.get(reference="T2").game)
        # The network approves it later: the same report again updates the sale.
        again = import_report(report.replace("pending", "approved"))
        self.assertEqual((again.added, again.updated), (0, 2))
        self.assertEqual(AffiliateSale.objects.get(reference="T1").status, AffiliateSale.Status.APPROVED)
        self.assertEqual(AffiliateSale.objects.count(), 2)

    def test_a_report_with_no_shop_column_takes_the_shop_chosen_and_a_suspect_click_is_never_matched(self):
        suspect = OutboundClick.objects.create(product=self.product, retailer=self.ebay, suspect=True)
        report = f"Event Date,Earnings,Status,Custom ID\n{date.today().isoformat()},0.85,Paid,{suspect.pk}\n"
        self.assertEqual(import_report(report).skipped, [(1, "No column for: retailer.")])
        result = import_report(report, default_retailer=self.ebay)
        sale = AffiliateSale.objects.get()
        self.assertEqual((result.added, sale.retailer, sale.status, sale.click), (1, self.ebay, "approved", None))

    def test_the_owner_imports_a_report_in_admin(self):
        User.objects.create_superuser("owner", "owner@example.com", "pw")
        self.client.login(username="owner", password="pw")
        url = reverse("admin:catalogue_affiliatesale_import")
        self.assertContains(self.client.get(reverse("admin:catalogue_affiliatesale_changelist")), url)
        upload = SimpleUploadedFile("report.csv", f"Date,Shop,Commission\n{date.today().isoformat()},Harbour Games,3.10\n".encode())
        response = self.client.post(url, {"file": upload}, follow=True)
        self.assertContains(response, "1 sales added, 0 updated.")
        self.assertEqual(AffiliateSale.objects.get().commission, Decimal("3.10"))
