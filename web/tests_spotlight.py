"""The home page's drop spotlight (views.drop_spotlight, DropSpotlight in admin): the drop chosen, its countdown and
boxes, and the steps to be ready for it, each working without script."""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import DropSpotlight, Listing
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set

from .tests import CopyStyleTests
from .tests_drops import MAIL_ON

HOME = "/"


def section(html):
    """The spotlight's own markup, or "" when the page has none."""
    found = re.search(r'(?s)<section class="home-section spotlight".*?</section>', html)
    return found.group(0) if found else ""


@override_settings(**MAIL_ON)
class SpotlightTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.today = timezone.localdate()
        self.game = make_game()
        self.set = make_set(self.game, name="Delta Reign", slug="delta-reign", code="DRI",
                            release_date=self.today + timedelta(days=6))
        self.etb = make_product(self.set, name="Delta Reign Elite Trainer Box", slug="dri-etb",
                                image_url="https://cdn.example/etb.png")
        self.box = make_product(self.set, name="Delta Reign Booster Box", slug="dri-box", product_type="booster_box",
                                image_url="https://cdn.example/box.png")
        self.blister = make_product(self.set, name="Delta Reign Blister", slug="dri-blister", product_type="booster_pack")
        self.shop = make_retailer("Harbour Games")
        self.other = make_retailer("Northgate Cards")
        make_listing(self.etb, self.shop, price="54.99", availability=Listing.Availability.PREORDER)
        make_listing(self.box, self.other, price="150.00", availability=Listing.Availability.PREORDER)

    def choose(self, discord=""):
        DropSpotlight.objects.update_or_create(pk=1, defaults={"product_set": self.set, "discord_invite": discord})

    def spotlight(self):
        return section(self.client.get(HOME).content.decode())

    def test_nothing_shows_until_a_drop_is_chosen(self):
        self.assertEqual(self.spotlight(), "")
        self.choose()
        self.assertIn("Delta Reign is coming.", self.spotlight())

    def test_the_drop_its_day_countdown_and_boxes(self):
        self.choose()
        html = self.spotlight()
        self.assertIn('id="drop-spotlight"', html)
        self.assertIn('<span class="spotlight__accent">Be ready.</span>', html)
        self.assertIn("The next big drop", html)
        self.assertIn("Out in 6 days", html)
        self.assertIn('data-countdown="', html)
        # The boxes with a picture, the Elite Trainer Box first; the blister has none.
        pictures = re.findall(r'alt="([^"]+)"', html)
        self.assertEqual(pictures, ["Delta Reign Elite Trainer Box", "Delta Reign Booster Box"])
        self.assertIn(f'href="{self.set.get_absolute_url()}" tabindex="-1" aria-hidden="true"', html)

    def test_the_steps(self):
        self.choose(discord="https://discord.gg/abc123")
        html = self.spotlight()
        steps = re.findall(r'data-step="([a-z]+)"', html)
        self.assertEqual(steps, ["alerts", "discord", "preorder", "prices"])
        # Every alert: one form, posting to the drop's reminder with every=1, back to the home page.
        self.assertIn(f'action="{reverse("web:drop_ask", args=[self.set.pk])}"', html)
        self.assertIn('name="every" value="1"', html)
        self.assertIn('name="from" value="home"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn("then every restock at a fair price for 30 days", html)
        # The Discord invite opens in a new tab and is not a shop link.
        self.assertIn('href="https://discord.gg/abc123" target="_blank" rel="noopener"', html)
        # Pre-orders from the cheapest, at both shops, in the site's green price.
        self.assertIn('Pre-orders from <span class="price">£54.99</span>', html)
        self.assertIn("at 2 shops", html)
        self.assertIn(f'href="{reverse("web:drops")}#drop-{self.set.pk}"', html)
        self.assertIn("See Delta Reign prices", html)

    def test_no_discord_step_without_an_invite_and_no_alerts_without_email(self):
        self.choose()
        self.assertNotIn('data-step="discord"', self.spotlight())
        with override_settings(RIPRAPTOR_ZEPTOMAIL_TOKEN=""):
            self.assertNotIn('data-step="alerts"', self.spotlight())

    def test_once_out_it_offers_restocks_and_the_shops_with_stock(self):
        self.set.release_date = self.today - timedelta(days=2)
        self.set.save()
        Listing.objects.filter(product=self.etb).update(availability=Listing.Availability.IN_STOCK)
        self.choose()
        html = self.spotlight()
        self.assertIn("Delta Reign is out.", html)
        self.assertIn('<span class="spotlight__accent">Beat the scalpers.</span>', html)
        self.assertIn("Out now", html)
        self.assertIn("Every restock at a UK shop at a fair price, for 30 days after release.", html)
        self.assertEqual(re.findall(r'data-step="([a-z]+)"', html), ["alerts", "stock", "prices"])
        self.assertIn('In stock at 1 UK shop from <span class="price">£54.99</span>', html)
        self.assertNotIn("data-countdown", html)

    def test_it_goes_fourteen_days_after_the_drop(self):
        self.set.release_date = self.today - timedelta(days=15)
        self.set.save()
        self.choose()
        self.assertEqual(self.spotlight(), "")

    def test_a_drop_with_only_a_month_has_no_restock_promise(self):
        from catalogue.models import Release

        self.set.release_date = None
        self.set.save()
        first = (self.today.replace(day=1) + timedelta(days=40)).replace(day=1)
        Release.objects.create(game=self.game, name="Delta Reign", release_date=first, precision=Release.Precision.MONTH,
                               source="pokemon", official=True, status=Release.Status.ACCEPTED, product_set=self.set)
        self.choose()
        html = self.spotlight()
        self.assertIn("then the day before and on the day once the day is known", html)
        self.assertNotIn('name="every"', html)

    def test_a_drop_with_no_date_at_all_takes_no_alerts(self):
        self.set.release_date = None
        self.set.save()
        self.choose()
        html = self.spotlight()
        self.assertIn("Delta Reign is coming.", html)   # still shown while shops take pre-orders
        self.assertNotIn('data-step="alerts"', html)

    def test_the_house_style(self):
        self.choose(discord="https://discord.gg/abc123")
        page = self.client.get(HOME).content.decode()
        html = section(page)
        self.assertNotIn("—", html)
        self.assertNotIn("!", re.sub(r"<[^>]*>", " ", html))
        for phrase in CopyStyleTests.BANNED:
            self.assertNotIn(phrase, html.lower())
        # The scripts come only with the spotlight.
        self.assertIn("js/spotlight.js", page)
        DropSpotlight.objects.update(product_set=None)
        self.assertNotIn("js/spotlight.js", self.client.get(HOME).content.decode())

    def test_it_costs_the_home_page_a_few_queries_however_many_products(self):
        self.choose()

        def count():
            cache.clear()
            with CaptureQueriesContext(connection) as queries:
                self.client.get(HOME)
            return len(queries)

        before = count()
        for n in range(6):
            product = make_product(self.set, name=f"Delta Reign Tin {n}", slug=f"dri-tin-{n}", product_type="tin",
                                   image_url=f"https://cdn.example/tin{n}.png")
            make_listing(product, self.shop, price="19.99", availability=Listing.Availability.PREORDER)
        self.assertEqual(count(), before)
        DropSpotlight.objects.update(product_set=None)
        self.assertLessEqual(before - count(), 4)


class SpotlightAdminTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "pw")
        self.client.force_login(self.owner)
        self.set = make_set(make_game(), name="Delta Reign", slug="delta-reign",
                            release_date=timezone.localdate() + timedelta(days=6))

    def test_the_list_opens_the_one_row(self):
        response = self.client.get(reverse("admin:catalogue_dropspotlight_changelist"))
        self.assertRedirects(response, reverse("admin:catalogue_dropspotlight_change", args=[1]))
        page = self.client.get(response.url)
        self.assertContains(page, "Nothing shows.")

    def test_the_admin_home_links_to_it(self):
        self.assertContains(self.client.get(reverse("admin:index")),
                            f'href="{reverse("admin:catalogue_dropspotlight_changelist")}" class="section">Drop spotlight')

    def test_only_a_discord_invite_is_taken(self):
        row = DropSpotlight(pk=1, product_set=self.set, discord_invite="https://example.com/discord")
        with self.assertRaises(ValidationError):
            row.full_clean()
        for good in ("https://discord.gg/abc123", "https://discord.com/invite/abc123"):
            DropSpotlight(pk=1, product_set=self.set, discord_invite=good).full_clean()

    def test_it_says_what_shows_and_who_signed_up(self):
        from catalogue.models import DropAlert

        DropAlert.objects.create(product_set=self.set, email="a@example.com", confirmed_at=timezone.now(),
                                 restocks_asked=True, restocks=True)
        DropAlert.objects.create(product_set=self.set, email="b@example.com", confirmed_at=timezone.now())
        DropSpotlight.objects.create(pk=1, product_set=self.set)
        page = self.client.get(reverse("admin:catalogue_dropspotlight_change", args=[1]))
        self.assertContains(page, "showing on the home page")
        self.assertContains(page, "Confirmed reminders: 2, of them with every alert: 1.")


class SpotlightPictureTests(TestCase):
    def test_the_pictures_slot(self):
        from catalogue.images import SLOTS

        width, sizes, offered = SLOTS["spotlight"]
        self.assertEqual(width, 200)
        self.assertEqual(sizes, "(min-width: 48rem) 8rem, 5.5rem")
        self.assertGreaterEqual(offered[0], 88)
        self.assertGreaterEqual(offered[-1], 384)
