"""Out now and the In stock lists (web/views.py out_now_queryset, in_stock_offers). A site review on 23 Oct 2026
found products due out in November under Out now, some with Dropping soon on them: a shop marking a product in
stock before its release date is not the release, and a list about stock now leads with a shop that has it."""

from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from catalogue.models import Listing, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .views import home_drops_new, out_now_queryset

UK = ZoneInfo("Europe/London")
PRE = Listing.Availability.PREORDER


class OutNowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()
        self.game = make_game()
        self.harbour = make_retailer("Harbour Games")
        self.north = make_retailer("Northgate Cards")

    def product(self, name, release, **kwargs):
        product_set = make_set(self.game, name=name, slug=name.lower().replace(" ", "-"), code=name[:3].upper(),
                               release_date=release)
        return make_product(product_set, name=f"{name} Booster Box", slug=f"{name.lower().replace(' ', '-')}-box",
                            product_type=Product.Type.BOOSTER_BOX, **kwargs)

    def test_a_future_release_a_shop_marks_in_stock_stays_out_of_out_now(self):
        early = self.product("Next Month", self.today + timedelta(days=20))
        make_listing(early, self.harbour, price="90.00")   # in stock, by the shop's word
        today = self.product("Today Set", self.today)
        make_listing(today, self.harbour, price="90.00")
        # The catalogue began a month ago, so a product shops have only just listed counts as new.
        old = make_product(make_set(self.game, name="Old", slug="old", code="OLD"), name="Old Tin", slug="old-tin")
        Product.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=30))
        undated = make_product(make_set(self.game, name="Undated", slug="undated", code="UND"),
                               name="Undated Booster Box", slug="undated-box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(undated, self.harbour, price="90.00")
        out = set(out_now_queryset())
        self.assertNotIn(early, out)
        self.assertIn(today, out)        # out today counts as out
        self.assertIn(undated, out)      # no date: the shops alone say it is out
        # The product's own date counts before its set's.
        Product.objects.filter(pk=today.pk).update(release_date=self.today + timedelta(days=1))
        self.assertNotIn(today, set(out_now_queryset()))

    def test_out_by_the_uk_date_not_the_server_clock(self):
        # 23:30 on 30 June in UTC is already 1 July in London.
        moment = datetime(2026, 6, 30, 23, 30, tzinfo=ZoneInfo("UTC"))
        with mock.patch("django.utils.timezone.now", return_value=moment):
            july = self.product("July Set", date(2026, 7, 1))
            make_listing(july, self.harbour, price="90.00")
            later = self.product("Later Set", date(2026, 7, 2))
            make_listing(later, self.harbour, price="90.00")
            out = set(out_now_queryset())
        self.assertIn(july, out)
        self.assertNotIn(later, out)

    def test_a_release_date_passing_is_not_stock(self):
        gone = self.product("Sold Out Set", self.today - timedelta(days=3))
        make_listing(gone, self.harbour, price="90.00", availability=Listing.Availability.OUT_OF_STOCK)
        waiting = self.product("Pre Set", self.today - timedelta(days=2))
        make_listing(waiting, self.harbour, price="90.00", availability=PRE)
        out = set(out_now_queryset())
        self.assertNotIn(gone, out)
        self.assertNotIn(waiting, out)

    def test_the_home_row_leads_with_the_shop_that_has_it_never_a_cheaper_pre_order(self):
        box = self.product("Mixed Set", self.today - timedelta(days=2))
        make_listing(box, self.harbour, price="80.00", availability=PRE)
        make_listing(box, self.north, price="95.00")
        [row] = [p for p in home_drops_new(fresh=True) if p.pk == box.pk]
        self.assertEqual((row.best_offer.retailer, row.best_offer.availability), (self.north, Listing.Availability.IN_STOCK))
        html = self.client.get("/").content.decode()
        out = html[html.index('id="out-title"'):]
        out = out[:out.index("</ul>")]
        self.assertIn("£95.00", out)
        self.assertNotIn("£80.00", out)
        self.assertNotIn("Dropping soon", out)

    def test_out_now_on_the_drops_page_and_the_in_stock_tab_lead_with_stock_too(self):
        box = self.product("Mixed Set", self.today - timedelta(days=2))
        make_listing(box, self.harbour, price="80.00", availability=PRE)
        make_listing(box, self.north, price="95.00")
        Product.objects.filter(pk=box.pk).update(price_band=5)   # a Price check worked out on the £80 pre-order
        for url in ("/drops/?show=out", self.game.get_absolute_url(), "/search/?q=Mixed&in_stock=on"):
            html = self.client.get(url).content.decode()
            card = html[html.index('<li class="card'):]
            card = card[:card.index("</li>")]
            self.assertIn("£95.00", card, url)
            self.assertIn("Northgate Cards", card, url)
            self.assertNotIn("Dropping soon", card, url)
            self.assertNotIn('class="fair ', card, url)   # that Price check was for another price
        # The Pre-orders view of the same product still leads with its cheapest offer, the pre-order.
        html = self.client.get("/search/?q=Mixed").content.decode()
        card = html[html.index('<li class="card'):]
        self.assertIn("£80.00", card[:card.index("</li>")])
