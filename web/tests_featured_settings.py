"""Featured deals settings in Admin (catalogue/models.py FeaturedSettings, web/views.py featured_pick). The owner:
"I have shops that are not affiliated in featured deals, where do I admin this". Ticked, the row holds only deals
at shops that pay, and the home page and the Terms page say so."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from catalogue.models import FeaturedSettings, Product
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

PAYS = "https://aff.example/click?u={url}"


class FeaturedSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        product_set = make_set(make_game())
        self.paying = make_retailer("Harbour Games", affiliate_url_template=PAYS)
        self.plain = make_retailer("Northgate Cards")
        self.dear = make_retailer("Tollgate TCG")
        # Two deals, each 29% under the next shop: one cheapest at a paying shop, one at a shop that does not pay.
        self.paid_deal = self.deal(product_set, "Surging Sparks Booster Box", "ssp-box", self.paying)
        self.plain_deal = self.deal(product_set, "Prismatic Evolutions Booster Box", "pre-box", self.plain)

    def deal(self, product_set, name, slug, cheapest):
        product = make_product(product_set, name=name, slug=slug, product_type=Product.Type.BOOSTER_BOX)
        make_listing(product, cheapest, price="50.00")
        make_listing(product, self.dear, price="70.00")
        return product

    def featured(self):
        cache.clear()
        html = self.client.get("/").content.decode()
        if 'id="featured-title"' not in html:
            return "", html
        section = html[html.index('id="featured-title"'):]
        return section[:section.index("</section>")], html

    def test_off_by_default_the_best_deals_from_other_shops_still_take_their_places(self):
        self.assertFalse(FeaturedSettings.current().paying_only)
        section, _ = self.featured()
        self.assertIn(self.paid_deal.name, section)
        self.assertIn(self.plain_deal.name, section)
        self.assertIn("Featured deals favour shops that pay us a commission.", section)

    def test_ticked_only_paying_shops_and_both_pages_say_so(self):
        FeaturedSettings.objects.create(pk=1, paying_only=True)
        section, _ = self.featured()
        self.assertIn(self.paid_deal.name, section)
        self.assertNotIn(self.plain_deal.name, section)
        self.assertIn("Featured deals come only from shops that pay us a commission. Price comparisons are always "
                      "cheapest delivered first, and no shop can pay to move up.", section)
        terms = self.client.get("/terms/").content.decode()
        self.assertIn("Featured deals come only from shops that pay us a commission, and each product page", terms)
        # The product page still lists every shop, cheapest delivered first.
        page = self.client.get(self.plain_deal.get_absolute_url()).content.decode()
        self.assertIn("Northgate Cards", page)

    def test_ticked_with_no_paying_deal_the_row_is_not_shown(self):
        FeaturedSettings.objects.create(pk=1, paying_only=True)
        self.paying.affiliate_url_template = ""
        self.paying.save()
        section, html = self.featured()
        self.assertEqual(section, "")
        self.assertNotIn("Featured deals favour", html)

    def test_admin_opens_the_one_row_names_the_paying_shops_and_the_home_page_follows_at_once(self):
        owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "pw-123456")
        self.client.force_login(owner)
        response = self.client.get("/admin/catalogue/featuredsettings/")
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"]).content.decode()
        self.assertIn("Only shops that pay", page)
        self.assertIn("Harbour Games. Add a shop&#x27;s affiliate link format under Retailers to add it.", page)
        self.assertNotIn("Northgate Cards", page)
        self.client.get("/")   # the home page's row is cached now
        self.client.post(response["Location"], {"paying_only": "on"})
        self.assertTrue(FeaturedSettings.current().paying_only)
        self.client.logout()
        html = self.client.get("/").content.decode()
        self.assertNotIn(self.plain_deal.name, html[html.index('id="featured-title"'):html.index("</section>", html.index('id="featured-title"'))])
