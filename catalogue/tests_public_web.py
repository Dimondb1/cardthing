"""Every address a shop gives is read only over http or https, on the usual ports, from an address on the public
internet, and the connection goes to the very address that was checked. Answers are read up to a limit, and a
robots.txt or sitemap cannot send the reader to another site."""

import socket
import urllib.request
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase

from . import importers, pictures, shop_adding
from .importers import NotPublic, Offer, TooBig, apply_offers
from .models import Product
from .testing import make_game, make_product, make_retailer, make_set

PUBLIC, LOOPBACK, PRIVATE = "93.184.216.34", "127.0.0.1", "10.0.0.5"


def answers(*addresses):
    """A stand-in for DNS that gives these addresses in turn, one each time it is asked."""
    queue = list(addresses)

    def resolve(host, port, *args, **kwargs):
        address = queue.pop(0) if len(queue) > 1 else queue[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


class Dripping:
    """An HTTP answer that never ends: a piece at a time, for ever."""

    length = None

    def read1(self, size):
        return b"x" * size


class AddressTests(SimpleTestCase):
    def test_only_http_and_https_on_the_usual_ports(self):
        for url in ("https://shop.example/", "http://shop.example/a", "https://shop.example:443/b"):
            self.assertEqual(importers.web_address(url), url)
        for url in ("file:///etc/hostname", "ftp://shop.example/", "data:text/plain,hi", "gopher://shop.example/",
                    "https://shop.example:8443/", "http://shop.example:22/", "https://", "/media/a.png", "",
                    "http://shop.example:notaport/"):
            with self.subTest(url=url), self.assertRaises(NotPublic):
                importers.web_address(url)

    def test_a_file_or_another_kind_of_address_is_never_opened(self):
        with mock.patch.object(socket, "create_connection") as connected, \
                mock.patch.object(urllib.request.FileHandler, "file_open") as opened:
            for url in ("file:///etc/hostname", "ftp://shop.example/x"):
                with self.subTest(url=url), self.assertRaises(NotPublic):
                    importers.fetch(url, retries=0)
        connected.assert_not_called()
        opened.assert_not_called()

    def test_a_name_that_resolves_inside_this_server_or_a_private_network_is_refused(self):
        for address in (LOOPBACK, PRIVATE, "169.254.169.254", "0.0.0.0", "224.0.0.1"):
            with self.subTest(address=address), mock.patch("socket.getaddrinfo", answers(address)), \
                    mock.patch.object(socket, "create_connection") as connected:
                with self.assertRaises(NotPublic):
                    importers.fetch("https://shop.example/robots.txt", retries=0)
                connected.assert_not_called()

    def test_the_connection_goes_to_the_address_that_was_checked(self):
        # A name that answers a public address and then this server's own (DNS rebinding) is connected to at the
        # public one, and never asked again for the connection.
        tried = []

        def connect(where, *args, **kwargs):
            tried.append(where)
            raise OSError("no route in a test")

        with mock.patch("socket.getaddrinfo", answers(PUBLIC, LOOPBACK)), \
                mock.patch.object(socket, "create_connection", side_effect=connect):
            with self.assertRaises(importers.ImportError_):
                importers.fetch("https://rebind.example/", retries=0)
        self.assertEqual(tried, [(PUBLIC, 443)])

    def test_a_redirect_into_this_server_or_to_a_file_is_refused(self):
        redirects = importers.PublicRedirects()
        with mock.patch("socket.getaddrinfo", answers(LOOPBACK)):
            with self.assertRaises(NotPublic):
                redirects.redirect_request(None, None, 302, "Found", {}, "http://localhost/admin/")
        for url in ("file:///etc/passwd", "https://shop.example:8443/"):
            with self.subTest(url=url), self.assertRaises(NotPublic):
                redirects.redirect_request(None, None, 302, "Found", {}, url)

    def test_the_opener_has_no_proxy_files_or_ftp(self):
        opener = importers.public_opener()
        kinds = {type(handler) for handler in opener.handlers}
        for missing in (urllib.request.FileHandler, urllib.request.FTPHandler, urllib.request.DataHandler):
            self.assertNotIn(missing, kinds)
        # No proxy from the environment: none would be pinned to the checked address.
        proxies = [handler for handler in opener.handlers if isinstance(handler, urllib.request.ProxyHandler)]
        self.assertEqual([handler.proxies for handler in proxies if handler.proxies], [])

    def test_list_your_shop_uses_the_same_rules(self):
        self.assertIs(shop_adding.NotPublic, NotPublic)
        self.assertIs(shop_adding.PublicRedirects, importers.PublicRedirects)


class BodyLimitTests(SimpleTestCase):
    def test_an_answer_that_never_ends_is_given_up_at_the_limit(self):
        with self.assertRaises(TooBig):
            importers.read_body(Dripping(), limit=1024 * 1024)
        self.assertTrue(issubclass(TooBig, importers.ImportError_))

    def test_list_your_shop_reads_less(self):
        self.assertLess(shop_adding.CHECK_BODY_BYTES, importers.MAX_BODY_BYTES)

    def test_a_huge_robots_txt_is_read_only_as_far_as_search_engines_read_one(self):
        rules = ("Disallow: /x\n" * 200_000).encode()
        asked = []

        def fetch(url, **kwargs):
            asked.append(url)
            if url.endswith("/robots.txt"):
                return rules + b"Sitemap: https://shop.example/late.xml\n"
            raise importers.ImportError_("none")

        importers.sitemap_pages("https://shop.example", fetch=fetch)
        self.assertNotIn("https://shop.example/late.xml", asked)


class SitemapTests(SimpleTestCase):
    def test_robots_and_sitemaps_cannot_send_the_reader_to_another_site(self):
        asked = []
        robots = (b"Sitemap: file:///etc/hostname\nSitemap: http://127.0.0.1/x.xml\n"
                  b"Sitemap: https://elsewhere.example/sitemap.xml\nSitemap: https://www.shop.example/products.xml\n")
        products = (b"<urlset><url><loc>https://shop.example/products/etb</loc></url>"
                    b"<url><loc>http://169.254.169.254/latest/meta-data/</loc></url>"
                    b"<url><loc>https://elsewhere.example/products/box</loc></url></urlset>")

        def fetch(url, **kwargs):
            asked.append(url)
            if url.endswith("/robots.txt"):
                return robots
            if url == "https://www.shop.example/products.xml":
                return products
            raise importers.ImportError_("none")

        pages = [url for url, _lastmod in importers.sitemap_pages("https://shop.example", fetch=fetch)]
        self.assertEqual(pages, ["https://shop.example/products/etb"])
        for never in ("file:///etc/hostname", "http://127.0.0.1/x.xml", "https://elsewhere.example/sitemap.xml"):
            self.assertNotIn(never, asked)

    def test_a_shop_and_its_own_subdomains_are_one_site(self):
        self.assertTrue(importers.same_site("https://www.shop.example/a", "https://shop.example"))
        self.assertTrue(importers.same_site("https://store.shop.example/a", "https://shop.example"))
        self.assertFalse(importers.same_site("https://shop.example.evil/a", "https://shop.example"))
        self.assertFalse(importers.same_site("file:///etc/hostname", "https://shop.example"))


class PictureTests(TestCase):
    def test_only_a_web_address_is_kept_as_a_picture(self):
        self.assertEqual(importers.web_image(" https://cdn.example/a.png "), "https://cdn.example/a.png")
        for address in ("file:///etc/passwd", "/media/a.png", "data:image/png;base64,AAAA", "", None):
            self.assertEqual(importers.web_image(address), "")

    def test_a_shop_picture_that_is_not_a_web_address_is_never_kept(self):
        retailer = make_retailer("Northgate Cards")
        product = make_product(make_set(make_game()), ean="0820650851230", image_url="")
        apply_offers(retailer, [Offer(title="ETB", url="https://n.example/a", price=Decimal("50"),
                                      ean="0820650851230", image="file:///etc/passwd")])
        self.assertEqual(Product.objects.get(pk=product.pk).image_url, "")

    def test_the_picture_check_never_asks_this_server(self):
        with mock.patch("socket.getaddrinfo", answers(LOOPBACK)), \
                mock.patch.object(socket, "create_connection") as connected:
            self.assertEqual(pictures.look("http://localhost/admin/"), pictures.UNKNOWN)
            self.assertEqual(pictures.look("https://cdn.example/a.png"), pictures.UNKNOWN)
        connected.assert_not_called()

    def test_a_share_picture_never_reads_a_file_or_this_server(self):
        from web import share

        product = make_product(make_set(make_game()), image_url="file:///etc/passwd")
        with mock.patch.object(importers, "fetch") as fetched:
            self.assertIsNone(share.photo_bytes(product))
        fetched.assert_not_called()
        product.image_url = "http://127.0.0.1:8000/admin/"
        with mock.patch.object(socket, "create_connection") as connected:
            self.assertIsNone(share.photo_bytes(product))
        connected.assert_not_called()
