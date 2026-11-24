"""A kept page carries the address it was asked at in its own links (canonical, og:url, the breadcrumbs), so whole
pages are kept per host and only for the site's own hosts as named. Django lets ripraptor.com:1 through as
ripraptor.com; such a page is built for that visitor alone and never becomes everyone else's copy."""

from django.core.cache import cache
from django.test import override_settings

from web import look, pagecache

from .tests import PageTestCase

SITE = {"ALLOWED_HOSTS": ["ripraptor.com", "www.ripraptor.com"], "RIPRAPTOR_SITE_URL": "https://ripraptor.com",
        "RIPRAPTOR_PAGE_CACHE_SECONDS": 300}


@override_settings(**SITE)
class PageCacheHostTests(PageTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        look.remember()

    def get(self, host):
        return self.client.get(self.game.get_absolute_url(), HTTP_HOST=host, secure=True)

    def test_a_page_asked_at_another_port_never_becomes_everyones_copy(self):
        odd = self.get("ripraptor.com:1")
        self.assertEqual(odd.status_code, 200)
        self.assertNotIn(pagecache.HEADER, odd)
        later = self.get("ripraptor.com")
        self.assertNotIn(":1", later.content.decode())
        self.assertIn('href="https://ripraptor.com/', later.content.decode())
        # Asked again at the site's address, the copy kept for it is served.
        self.assertEqual(self.get("ripraptor.com")[pagecache.HEADER], "cached")
        self.assertNotIn(":1", self.get("ripraptor.com").content.decode())

    def test_each_of_the_sites_hosts_has_its_own_copy(self):
        self.get("www.ripraptor.com")
        page = self.get("ripraptor.com")
        self.assertNotIn(pagecache.HEADER, page)
        self.assertNotIn("www.ripraptor.com", page.content.decode())

    def test_a_warmed_page_is_the_one_visitors_get(self):
        pagecache.warm(self.game.get_absolute_url())
        self.assertTrue(pagecache.has(self.game.get_absolute_url()))
        self.assertEqual(self.get("ripraptor.com")[pagecache.HEADER], "cached")
