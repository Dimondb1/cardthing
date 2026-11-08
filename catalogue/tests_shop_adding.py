"""Adding shops from pasted addresses: each is checked politely and added only when it passes
(catalogue/shop_adding.py, web/add_shops.py). The owner, 8 Nov 2026: "find how they are connecting 208 shops, we need
to best this"."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from catalogue import shop_adding
from catalogue.importers import ImportError_
from catalogue.models import RemovedShop, Retailer, ShopToAdd
from catalogue.testing import make_retailer

SEALED = [{"title": "Pokémon TCG: Mega Evolution Delta Reign Elite Trainer Box", "product_type": "Pokemon"}]
SINGLES = [{"title": "Charizard ex 199/165 PSA 10", "product_type": "Single"}]
PRODUCT_PAGE = """<html><head><script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product",
"name": "One Piece OP-14 Booster Box", "offers": {"@type": "Offer", "price": "99.99", "priceCurrency": "GBP",
"availability": "https://schema.org/InStock"}}</script></head><body>One Piece OP-14 Booster Box</body></html>"""


def shop(pages):
    """A fake shop: its address to bytes, or a status that fails like the real reader's fetch."""
    def get(url):
        answer = pages.get(url, 404)
        if isinstance(answer, int):
            raise ImportError_(f"Could not fetch {url}: HTTP Error {answer}: Error")
        return answer if isinstance(answer, bytes) else answer.encode()
    return get


def shopify(base, currency="GBP", products=SEALED, robots="User-agent: *\nDisallow: /cart\n", name="Example Cards"):
    return {
        f"{base}/robots.txt": robots,
        f"{base}/meta.json": json.dumps({"name": name, "currency": currency}),
        f"{base}/products.json?limit=250&page=1": json.dumps({"products": products}),
    }


class TakeTests(TestCase):
    def test_pasted_addresses_become_shops_to_check_once_each(self):
        saved, known = shop_adding.take("examplecards.co.uk\nhttps://www.another.example/products/x, not an address\n"
                                        "EXAMPLECARDS.co.uk")
        self.assertEqual((saved, known), (2, 1))
        self.assertEqual(sorted(ShopToAdd.objects.values_list("host", "address")),
                         [("another.example", "https://www.another.example"),
                          ("examplecards.co.uk", "https://examplecards.co.uk")])
        self.assertEqual(shop_adding.take("examplecards.co.uk"), (0, 1))


class CheckTests(TestCase):
    def check(self, address, pages):
        shop_adding.take(address)
        return shop_adding.check(ShopToAdd.objects.get(host=shop_adding.host_of(address)), get=shop(pages))

    def test_a_shopify_shop_in_pounds_with_sealed_cards_is_added_and_switched_on(self):
        row = self.check("examplecards.co.uk", shopify("https://examplecards.co.uk"))
        self.assertEqual(row.status, ShopToAdd.Status.ADDED)
        added = row.retailer
        self.assertEqual((added.name, added.slug, added.website, added.source_type, added.source_url, added.is_active),
                         ("Example Cards", "example-cards", "https://examplecards.co.uk/", Retailer.Source.SHOPIFY,
                          "https://examplecards.co.uk/", True))
        # Never a guessed delivery charge: unknown until the daily check reads the shop's own.
        self.assertIsNone(added.delivery_cost)
        self.assertEqual(added.delivery_note, "Delivery charge not yet confirmed")

    def test_a_shop_already_on_the_site_is_left_as_it_is(self):
        mine = make_retailer("Example Cards", website="https://www.examplecards.co.uk/")
        row = self.check("examplecards.co.uk", {})
        self.assertEqual((row.status, row.retailer), (ShopToAdd.Status.ALREADY, mine))
        self.assertEqual(Retailer.objects.count(), 1)

    def test_shops_that_do_not_pass_are_never_added_and_say_why(self):
        cases = {
            "dollars.example": (shopify("https://dollars.example", currency="USD"), "Prices in USD, not pounds."),
            "singles.example": (shopify("https://singles.example", products=SINGLES), "No sealed trading card products"),
            "private.example": (shopify("https://private.example", robots="User-agent: *\nDisallow: /products.json\n"),
                                "robots.txt asks readers like ours not to read its products"),
            "blocked.example": ({"https://blocked.example/robots.txt": 403}, "turned our reader away"),
            "nothing.example": ({}, "No product feed or sitemap found"),
        }
        for host, (pages, why) in cases.items():
            with self.subTest(host=host):
                row = self.check(host, pages)
                self.assertEqual(row.status, ShopToAdd.Status.NOT_ADDED)
                self.assertIn(why, row.note)
        self.assertFalse(Retailer.objects.exists())

    def test_a_shop_the_owner_deleted_is_not_added_back(self):
        RemovedShop.objects.create(slug="example-cards", name="Example Cards", host="examplecards.co.uk")
        row = self.check("https://www.examplecards.co.uk/", shopify("https://www.examplecards.co.uk"))
        self.assertEqual(row.status, ShopToAdd.Status.NOT_ADDED)
        self.assertIn("You deleted this shop before", row.note)

    def test_another_shop_is_added_when_its_product_pages_carry_a_price_in_pounds(self):
        base = "https://pagesshop.example"
        pages = {
            f"{base}/robots.txt": f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n",
            f"{base}/sitemap.xml": f"<urlset><url><loc>{base}/products/one-piece-op-14-booster-box</loc></url></urlset>",
            f"{base}/products/one-piece-op-14-booster-box": PRODUCT_PAGE,
        }
        row = self.check("pagesshop.example", pages)
        self.assertEqual(row.status, ShopToAdd.Status.ADDED, row.note)
        self.assertEqual((row.retailer.name, row.retailer.source_type), ("Pagesshop", Retailer.Source.WEBSITE))
        euros = {key: value.replace("GBP", "EUR") for key, value in pages.items()}
        euros = {key.replace("pagesshop", "eurosshop"): value.replace("pagesshop", "eurosshop") for key, value in euros.items()}
        self.assertEqual(self.check("eurosshop.example", euros).status, ShopToAdd.Status.NOT_ADDED)

    def test_a_second_shop_with_the_same_name_gets_its_own(self):
        make_retailer("Example Cards", website="https://elsewhere.example/")
        row = self.check("examplecards.co.uk", shopify("https://examplecards.co.uk"))
        self.assertEqual((row.retailer.name, row.retailer.slug), ("Example Cards (2)", "example-cards-2"))

    def test_one_shops_fault_never_stops_the_rest(self):
        shop_adding.take("first.example\nsecond.example")
        with mock.patch("catalogue.shop_adding.check", side_effect=[RuntimeError("boom"), None]):
            self.assertEqual(shop_adding.check_waiting(), 2)
        first = ShopToAdd.objects.get(host="first.example")
        self.assertEqual((first.status, first.note), (ShopToAdd.Status.NOT_ADDED, "The check failed: try again."))


class WorkerTests(TestCase):
    def test_the_background_reader_checks_waiting_shops_in_a_thread_of_their_own(self):
        from django.utils import timezone

        from catalogue.worker import Worker

        worker = Worker(threads=1, warm=True)
        with mock.patch("catalogue.worker.threading.Thread") as thread:
            worker.keep_shops_checked(timezone.now())
            thread.assert_not_called()   # nothing waiting
            shop_adding.take("examplecards.co.uk")
            worker.last_shops = None
            worker.keep_shops_checked(timezone.now())
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()


class PageTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("ben", password="pw", is_staff=True, is_superuser=True)

    def test_the_owner_pastes_addresses_and_sees_what_happened(self):
        self.client.force_login(self.owner)
        url = reverse("add_shops")
        response = self.client.post(url, {"addresses": "examplecards.co.uk\nother.example"}, follow=True)
        self.assertContains(response, "2 shops to check.")
        self.assertContains(response, "Waiting: 2")
        self.assertContains(response, '<meta http-equiv="refresh" content="30">', html=False)
        row = ShopToAdd.objects.get(host="other.example")
        row.status, row.note = ShopToAdd.Status.NOT_ADDED, "Prices in USD, not pounds."
        row.save()
        page = self.client.get(url)
        self.assertContains(page, "Prices in USD, not pounds.")
        self.assertEqual(page["Cache-Control"], "private, no-store")
        self.client.post(url, {"again": row.pk})
        row.refresh_from_db()
        self.assertEqual((row.status, row.note), (ShopToAdd.Status.WAITING, ""))
        self.assertContains(self.client.get(reverse("admin:index")),
                            f'<a href="{url}" class="section">Add shops</a>', html=False)

    def test_only_the_owners_staff_can_use_it(self):
        url = reverse("add_shops")
        self.assertEqual(self.client.get(url).status_code, 302)
        editor = get_user_model().objects.create_user("editor", password="pw", is_staff=True)
        from guides.apps import EDITORS

        editor.groups.add(Group.objects.get_or_create(name=EDITORS)[0])
        self.client.force_login(editor)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"addresses": "x.example"}).status_code, 403)
        self.assertFalse(ShopToAdd.objects.exists())
