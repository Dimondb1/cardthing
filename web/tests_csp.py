"""The Content Security Policy (ripraptor/csp.py) and the HSTS directives. The owner, 4 Nov 2026, from a Lighthouse
Trust and Safety report: no CSP in enforcement mode; HSTS without includeSubDomains and preload."""

import re

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import SiteLook
from ripraptor import csp
from web import look, pagecache

from .tests import PageTestCase

NONCE = re.compile(r"'nonce-([A-Za-z0-9_-]+)'")
SCRIPT = re.compile(r"<script\b([^>]*)>")
HANDLER = re.compile(r'\son[a-z]+="', re.I)


def directives(header):
    return {part.split(" ", 1)[0]: part.split(" ", 1)[1] if " " in part else "" for part in header.split("; ")}


def executable(attributes):
    """Whether a script tag with these attributes runs: a data block (JSON, structured data) does not."""
    kind = re.search(r'type="([^"]+)"', attributes)
    return kind is None or kind.group(1) not in ("application/json", "application/ld+json")


class PolicyTests(PageTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def public_pages(self):
        return self.public_urls() + [reverse("web:deals"), reverse("web:drops"), reverse("web:about"),
                                     reverse("web:watchlist"), "/no-such-page/"]

    def check(self, html, header, strict, form_action="'self'"):
        found = directives(header)
        nonce = NONCE.search(found["script-src"]).group(1)
        self.assertGreaterEqual(len(nonce), 22)
        self.assertEqual(found["object-src"], "'none'")
        self.assertEqual(found["base-uri"], "'none'")
        self.assertEqual(found["default-src"], "'self'")
        self.assertEqual(found["style-src"], "'self' 'unsafe-inline'")
        self.assertEqual(found["img-src"], "'self' data: https:")
        self.assertEqual(found["form-action"], form_action)
        self.assertEqual(found["frame-ancestors"], "'self'")
        if strict:
            self.assertEqual(found["script-src"], f"'nonce-{nonce}' 'strict-dynamic' 'self' https: 'unsafe-inline'")
        else:
            self.assertEqual(found["script-src"], f"'self' 'nonce-{nonce}'")
        # Every script that runs carries the response's nonce; nothing runs from an attribute.
        tags = [attrs for attrs in SCRIPT.findall(html) if executable(attrs)]
        for attrs in tags:
            if strict or "src=" not in attrs:
                self.assertIn(f'nonce="{nonce}"', attrs, attrs)
        self.assertIsNone(HANDLER.search(html), HANDLER.search(html) and HANDLER.search(html).group(0))
        return nonce

    def test_every_public_page_carries_a_strict_policy_its_scripts_satisfy(self):
        nonces = set()
        for url in self.public_pages():
            response = self.client.get(url)
            with self.subTest(url=url):
                self.assertIn(csp.HEADER, response)
                # The Continue page's one form answers with a redirect to the shop (web.views.gate).
                form_action = "'self' https:" if url.startswith("/go/") else "'self'"
                nonces.add(self.check(response.content.decode(), response[csp.HEADER], strict=True,
                                      form_action=form_action))
        self.assertEqual(len(nonces), len(self.public_pages()))   # a nonce is each response's own

    def test_the_new_home_page_and_its_filtered_lists_too(self):
        row = SiteLook.load()
        row.feed_home = True
        row.save()
        look.remember()
        for url in (reverse("web:home"), self.game.get_absolute_url(), reverse("web:search") + "?type=elite_trainer_box",
                    self.game.get_absolute_url() + "?type=elite_trainer_box"):
            response = self.client.get(url)
            with self.subTest(url=url):
                html = response.content.decode()
                self.assertIn('class="feed', html)
                nonce = self.check(html, response[csp.HEADER], strict=True)
                # The inline scripts the feed carries: the visitor's games, the page cross-fade's rules.
                self.assertIn(f'<script nonce="{nonce}" type="speculationrules">', html)

    def test_third_party_tags_get_their_connections_only_when_switched_on(self):
        with self.settings(RIPRAPTOR_AWIN_PUBLISHER_ID="", RIPRAPTOR_GA_ID="", RIPRAPTOR_ADSENSE_CLIENT=""):
            found = directives(self.client.get(reverse("web:home"))[csp.HEADER])
            self.assertEqual(found["connect-src"], "'self'")
            self.assertEqual(found["frame-src"], "'none'")
        with self.settings(RIPRAPTOR_AWIN_PUBLISHER_ID="3111686", RIPRAPTOR_GA_ID="G-1", RIPRAPTOR_ADSENSE_CLIENT="ca-pub-1"):
            response = self.client.get(reverse("web:home"))
            html = response.content.decode()
            found = directives(response[csp.HEADER])
            nonce = NONCE.search(found["script-src"]).group(1)
            self.assertIn(f'<script nonce="{nonce}" src="https://www.dwin2.com/pub.3111686.min.js">', html)
            self.assertIn(f'<script nonce="{nonce}" async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1"', html)
            for host in ("https://*.awin1.com", "https://*.googletagmanager.com", "https://*.googlesyndication.com"):
                self.assertIn(host, found["connect-src"])
            self.assertIn("https://*.awin1.com", found["frame-src"])
            self.assertIn("https://*.doubleclick.net", found["frame-src"])
            self.assertNotIn("googletagmanager", found["frame-src"])

    def test_the_admin_keeps_djangos_own_scripts_and_gets_the_nonce_for_its_inline_ones(self):
        self.client.force_login(get_user_model().objects.create_superuser("owner", "owner@example.com", "pw"))
        for url in (reverse("admin:index"), reverse("admin:catalogue_product_changelist"), reverse("admin:login")):
            response = self.client.get(url, follow=True)
            with self.subTest(url=url):
                self.assertIn(csp.HEADER, response)
                self.check(response.content.decode(), response[csp.HEADER], strict=False)
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn("<script nonce=", html)

    def test_a_view_may_set_its_own_policy(self):
        self.assertEqual(csp.policy("abc", admin=True), csp.policy("abc", admin=True))
        self.assertNotEqual(csp.nonce(), csp.nonce())

    @override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300)
    def test_a_page_served_from_the_cache_gets_a_nonce_of_its_own(self):
        look.remember()
        url = self.game.get_absolute_url()
        first = self.client.get(url)
        second = self.client.get(url)
        self.assertEqual(second[pagecache.HEADER], "cached")
        one = NONCE.search(first[csp.HEADER]).group(1)
        two = NONCE.search(second[csp.HEADER]).group(1)
        self.assertNotEqual(one, two)
        self.assertNotIn(one, second.content.decode())
        self.check(second.content.decode(), second[csp.HEADER], strict=True)
        # A page warmed before any visitor (web.warming) is the same.
        cache.clear()
        look.remember()
        self.assertTrue(pagecache.warm(url))
        served = self.client.get(url)
        self.assertEqual(served[pagecache.HEADER], "cached")
        self.check(served.content.decode(), served[csp.HEADER], strict=True)


class HstsTests(TestCase):
    @override_settings(SECURE_HSTS_SECONDS=31536000)
    def test_a_year_every_subdomain_and_the_preload_list(self):
        response = self.client.get(reverse("web:about"), secure=True)
        self.assertEqual(response["Strict-Transport-Security"], "max-age=31536000; includeSubDomains; preload")
