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


class ProductPageTests(TestCase):
    """The product page leads with the cheapest shop that has the product in stock, as the cards and a post do.
    The owner, 6 Nov 2026, after a card showing a shop's in-stock price opened a page leading with a cheaper
    pre-order: "I want to click the one I just clicked on, otherwise it's confusing"."""

    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.harbour = make_retailer("Harbour Games")
        self.north = make_retailer("Northgate Cards")
        product_set = make_set(self.game, name="Mixed Set", slug="mixed-set", code="MIX",
                               release_date=timezone.localdate() - timedelta(days=2))
        self.box = make_product(product_set, name="Mixed Set Booster Box", slug="mixed-set-box",
                                product_type=Product.Type.BOOSTER_BOX)

    def buy_box(self):
        html = self.client.get(self.box.get_absolute_url()).content.decode()
        box = html[html.index('class="buybox'):]
        return html, box[:box.index('id="offers-title"')]

    def test_the_page_leads_with_the_shop_that_has_it_never_a_cheaper_pre_order(self):
        make_listing(self.box, self.harbour, price="80.00", availability=PRE)
        make_listing(self.box, self.north, price="95.00")
        html, box = self.buy_box()
        self.assertIn("£95.00", box)
        self.assertIn("at Northgate Cards", box)
        self.assertIn("Buy now", box)
        self.assertNotIn("Pre-order", box)
        self.assertNotIn("Dropping soon", box)
        # The labels say the price shown is the cheapest with stock, and that the list puts stock first.
        self.assertIn("Cheapest in stock", box)
        self.assertIn("2 shops, in stock first", box)
        self.assertNotIn("rank__price--more", box)   # the #2 pre-order is not a dearer price, so not red
        # The pre-order is still in the list, after the shop with stock, and no row is called the cheapest.
        rows = html[html.index('id="offers-title"'):]
        self.assertLess(rows.index("Northgate Cards"), rows.index("Harbour Games"))
        self.assertIn("£80.00", rows)
        self.assertNotIn('class="offer__flag"', rows)
        self.assertIn('"lowPrice": "80.00"', html)

    def test_with_nothing_in_stock_the_cheapest_pre_order_leads_as_before(self):
        make_listing(self.box, self.harbour, price="80.00", availability=PRE)
        make_listing(self.box, self.north, price="95.00", availability=PRE)
        html, box = self.buy_box()
        self.assertIn("£80.00", box)
        self.assertIn("at Harbour Games", box)
        self.assertIn("Pre-order", box)
        self.assertIn("Dropping soon", box)
        self.assertIn("2 shops, cheapest delivered first", box)
        self.assertNotIn("Cheapest in stock", box)

    def test_a_dearer_pre_order_leaves_the_usual_labels(self):
        make_listing(self.box, self.north, price="95.00")
        make_listing(self.box, self.harbour, price="120.00", availability=PRE)
        html, box = self.buy_box()
        self.assertIn("Cheapest price", box)
        self.assertNotIn("Cheapest in stock", box)
        self.assertIn("2 shops, cheapest delivered first", box)
        self.assertIn("rank__price--more", box)
        self.assertIn('class="offer__flag"', html)
