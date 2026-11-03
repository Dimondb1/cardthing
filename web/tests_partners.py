"""The partners row on the home page (catalogue.Partner, web/partners.py). The owner, 3 Nov 2026, with Zatu Games' logo
and brand rules: "add a partners section on the website so that we can ask the same back"."""

import re
import shutil
import tempfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Partner, validate_logo
from catalogue.testing import make_retailer
from web import partners

CSS = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "site.css").read_text(encoding="utf-8")
TOKENS = (Path(settings.BASE_DIR) / "web" / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
HOME = "/"


def logo_file(width=500, height=500, name="zatu.png"):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class MediaCase(TestCase):
    """Uploads go to a folder made for the test and removed after it."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.folder, True)
        cls.media = override_settings(MEDIA_ROOT=cls.folder)
        cls.media.enable()
        cls.addClassCleanup(cls.media.disable)
        super().setUpClass()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def add(self, name="Zatu Games", website="https://www.zatu.example/", **kwargs):
        kwargs.setdefault("logo", logo_file())
        return Partner.objects.create(name=name, website=website, **kwargs)

    def row(self):
        html = self.client.get(HOME).content.decode()
        if 'id="partners-title"' not in html:
            return ""
        start = html.index('<section class="home-section partners"')
        return html[start:html.index("</section>", start)]


class ModelTests(MediaCase):
    def test_the_name_and_website_come_from_the_shop_unless_given(self):
        shop = make_retailer("Zatu Games", website="https://www.zatu.example/")
        partner = Partner(retailer=shop)
        self.assertEqual((partner.display_name, partner.link), ("Zatu Games", "https://www.zatu.example/"))
        partner.name, partner.website = "Zatu", "https://zatu.example/trade/"
        self.assertEqual((partner.display_name, partner.link), ("Zatu", "https://zatu.example/trade/"))
        self.assertEqual(str(partner), "Zatu")

    def test_it_needs_a_name_and_somewhere_to_link(self):
        with self.assertRaises(ValidationError) as caught:
            Partner(website="https://zatu.example/").clean()
        self.assertIn("name", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            Partner(name="Zatu").clean()
        self.assertIn("website", caught.exception.message_dict)
        Partner(name="Zatu", website="https://zatu.example/").clean()

    def test_a_logo_must_be_sharp_enough_and_small_enough(self):
        validate_logo(logo_file(240, 100))
        with self.assertRaisesMessage(ValidationError, "The logo is 200 pixels wide. Use one at least 240 wide"):
            validate_logo(logo_file(200, 200))
        with mock.patch("catalogue.models.LOGO_MAX_BYTES", 100), \
                self.assertRaisesMessage(ValidationError, "The logo is over 2 MB."):
            validate_logo(logo_file())

    def test_the_picture_sizes_are_kept_so_it_never_has_to_be_stretched(self):
        partner = self.add(logo=logo_file(500, 300))
        partner.refresh_from_db()
        self.assertEqual((partner.logo_width, partner.logo_height), (500, 300))


class HomeRowTests(MediaCase):
    def test_no_partner_no_row_and_no_claim_on_the_page(self):
        self.assertEqual(self.row(), "")
        self.assertNotIn("Our partners", self.client.get(HOME).content.decode())

    def test_a_partner_shows_by_its_logo_whole_and_linked_as_any_shop_is(self):
        partner = self.add()
        row = self.row()
        self.assertIn('<h2 id="partners-title" class="section-head__title">Our partners</h2>', row)
        self.assertIn("Shops and brands we work with.", row)
        self.assertIn('<a class="partners__link" href="https://www.zatu.example/" target="_blank" '
                      'rel="sponsored nofollow noopener">', row)
        self.assertIn(f'<img class="partners__logo" src="{partner.logo.url}" alt="Zatu Games" width="500" height="500" '
                      'loading="lazy" decoding="async">', row)
        self.assertIn('<span class="visually-hidden"> (opens in a new tab)</span>', row)
        self.assertTrue(partner.logo.url.startswith("/media/partners/"))

    def test_only_shown_partners_in_the_order_set_and_a_shop_fills_the_blanks(self):
        shop = make_retailer("Quay Cards", website="https://www.quay.example/")
        self.add("Second", "https://second.example/", position=2)
        self.add("First", "https://first.example/", position=1)
        self.add("Hidden", "https://hidden.example/", position=0, is_shown=False)
        Partner.objects.create(retailer=shop, logo=logo_file(name="quay.png"), position=3)
        row = self.row()
        self.assertNotIn("Hidden", row)
        names = re.findall(r'alt="([^"]+)"', row)
        self.assertEqual(names, ["First", "Second", "Quay Cards"])
        self.assertIn('href="https://www.quay.example/"', row)

    def test_a_change_shows_at_once_and_a_delete_takes_the_row_away(self):
        partner = self.add()
        self.assertIn("Zatu Games", self.row())
        partner.is_shown = False
        partner.save()
        self.assertEqual(self.row(), "")
        partner.is_shown = True
        partner.save()
        self.assertIn("Zatu Games", self.row())
        partner.delete()
        self.assertEqual(self.row(), "")

    def test_an_emptied_cache_leaves_no_row_until_warm_pages_puts_it_back(self):
        self.add()
        cache.clear()
        self.assertEqual(self.row(), "")
        partners.remember()
        self.assertIn("Zatu Games", self.row())

    def test_the_home_page_gains_no_query(self):
        self.client.get(HOME)   # builds the cached lists
        with CaptureQueriesContext(connection) as without:
            self.client.get(HOME)
        self.add()
        self.client.get(HOME)
        with CaptureQueriesContext(connection) as shown:
            self.client.get(HOME)
        self.assertIn("Zatu Games", self.row())
        self.assertEqual(len(shown), len(without))


class StyleTests(TestCase):
    """The logo is drawn as Zatu Games' own rules ask: not under 17 mm, 5 mm clear all round, never distorted, faded
    or cut off. Another partner's rules are usually the same."""

    def mm(self, token):
        rem = float(re.search(rf"{token}:\s*([\d.]+)rem", TOKENS).group(1))
        return rem * 16 / 96 * 25.4   # a CSS pixel is 1/96 inch

    def block(self):
        """The partners' rules without the comment explaining them."""
        block = CSS[CSS.index("/* Partners (home page"):CSS.index("/* The drop spotlight")]
        return re.sub(r"/\*.*?\*/", "", block, flags=re.S)

    def test_never_smaller_than_17mm_and_with_5mm_clear_all_round(self):
        self.assertGreaterEqual(self.mm("--partner-logo-min"), 17)
        self.assertGreaterEqual(self.mm("--partner-logo"), 17)
        self.assertGreaterEqual(self.mm("--partner-clear"), 5)
        block = self.block()
        self.assertIn("padding: var(--partner-clear);", block)
        self.assertIn("min-width: var(--partner-logo-min);", block)

    def test_never_stretched_faded_tinted_or_cut_off(self):
        block = self.block()
        self.assertIn("height: auto; object-fit: contain;", block)
        for banned in ("opacity", "filter", "mix-blend", "overflow", "clip", "aspect-ratio", "transform", "grayscale"):
            self.assertNotIn(banned, block)
        # Every value is a token: no hard-coded size or colour.
        self.assertNotRegex(block, r"\b\d+(\.\d+)?(px|rem)\b|#[0-9a-fA-F]{3,6}\b")


class AdminTests(MediaCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))

    def post(self, **fields):
        data = {"retailer": "", "name": "Zatu Games", "website": "https://www.zatu.example/", "logo": logo_file(),
                "position": "0", "is_shown": "on", **fields}
        return self.client.post(reverse("admin:catalogue_partner_add"), data, follow=True)

    def test_a_partner_added_in_admin_shows_on_the_home_page(self):
        response = self.post()
        self.assertEqual(Partner.objects.count(), 1)
        self.assertContains(response, "Zatu Games")
        self.assertIn('alt="Zatu Games"', self.row())
        change = self.client.get(reverse("admin:catalogue_partner_change", args=[Partner.objects.get().pk]))
        self.assertContains(change, "How it looks")
        self.assertContains(change, "Do not edit it.")
        self.assertContains(change, "Only add a partner who has agreed to be shown.")

    def test_a_logo_too_small_or_a_missing_name_is_refused(self):
        self.assertContains(self.post(logo=logo_file(200, 200)), "The logo is 200 pixels wide.")
        self.assertContains(self.post(name="", website="https://zatu.example/"), "Give a name, or pick the shop.")
        self.assertContains(self.post(website=""), "Give the website the logo links to")
        self.assertEqual(Partner.objects.count(), 0)

    def test_admins_home_page_leads_to_it(self):
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn(f'href="{reverse("admin:catalogue_partner_changelist")}" class="section">Partners', html)

    def test_the_list_opens_for_the_owner(self):
        self.add()
        self.assertContains(self.client.get(reverse("admin:catalogue_partner_changelist")), "Zatu Games")


class MediaTests(MediaCase):
    def test_an_uploaded_picture_is_served_and_kept_by_the_browser_for_a_week(self):
        partner = self.add()
        response = self.client.get(partner.logo.url)
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/png"))
        self.assertEqual(response["Cache-Control"], "public, max-age=604800")
        self.assertEqual(b"".join(response.streaming_content)[:4], b"\x89PNG")

    def test_nothing_else_is_served(self):
        self.add()
        for path in ("/media/partners/missing.png", "/media/partners/", "/media/", "/media/..%2Fmanage.py",
                     "/media/%2e%2e/%2e%2e/manage.py"):
            with self.subTest(path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (400, 404))
                self.assertNotIn(b"DJANGO_SETTINGS_MODULE", b"".join(getattr(response, "streaming_content", [response.content])))
