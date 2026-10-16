"""The shop's own page as the Claude judge's evidence (catalogue/shop_pages.py). Every page here is a
recorded body in testdata/shop_pages: no test touches the network."""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from . import judge, shop_pages
from .importers import ImportError_
from .models import ClaudeAsk, ImportRun, Listing, Retailer, ShopProduct
from .testing import make_listing, make_product, make_retailer
from .tests_judge import BOX, Base, reply

DATA = Path(__file__).resolve().parent / "testdata" / "shop_pages"
EAN = "0820650857508"
GONE = ImportError_("Could not fetch https://x.example/: HTTP Error 404: Not Found")


def recorded(name):
    return (DATA / name).read_bytes()


class Pages:
    """A shop that answers from recorded bodies: {address: body or an exception}. robots.txt answers 404
    unless given. Every address asked is kept in ``asked``."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def fetch_for(self, retailer):
        return self.fetch

    def fetch(self, url, retries=2, deadline=None):
        self.asked.append(url)
        assert retries == 0 and deadline is not None, "one try with a deadline"
        answer = self.answers.get(url, GONE if url.endswith("/robots.txt") else None)
        if answer is None:
            raise AssertionError(f"unexpected request {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer

    def pages_asked(self):
        return [url for url in self.asked if not url.endswith("/robots.txt")]


class ReaderBase(Base):
    def setUp(self):
        super().setUp()
        self.product.ean = EAN
        self.product.save()
        self.sleeps = []

    def reader(self, shop, **kwargs):
        kwargs.setdefault("sleep", self.sleeps.append)
        kwargs.setdefault("clock", lambda: 100.0)   # a still clock: every wait is the shop's whole gap
        return shop_pages.Reader(fetch_for=shop.fetch_for, **kwargs)

    def shopify(self, name="Shopify Shop", **kwargs):
        return make_retailer(name, source_type=Retailer.Source.SHOPIFY, source_url=f"https://www.{name.lower().replace(' ', '-')}.example/",
                             delivery_cost=Decimal("0"), **kwargs)

    def website(self, name="Website Shop", **kwargs):
        return make_retailer(name, source_type=Retailer.Source.WEBSITE, source_url=f"https://www.{name.lower().replace(' ', '-')}.example/",
                             delivery_cost=Decimal("0"), **kwargs)


class ShopifyTests(ReaderBase):
    def setUp(self):
        super().setUp()
        self.store = self.shopify()
        self.page = f"{self.store.website}products/surging-sparks-booster-box"
        self.pages = Pages({self.page + ".js": recorded("shopify_product.js")})

    def test_the_selected_variant_is_the_one_the_address_names(self):
        facts = self.reader(self.pages).facts(self.page + "?variant=42222222222222", self.store)
        said = shop_pages.evidence(facts, self.product)
        self.assertTrue(said["read_from_shop"])
        self.assertEqual(said["page"], "one product's page")
        self.assertEqual(said["shop_product_name"], "Pokemon TCG Surging Sparks Booster Box")
        self.assertEqual(said["shop_selected_variant"], "Case (6 Boxes)")
        self.assertEqual(said["shop_variants_on_page"], ["Booster Box", "Case (6 Boxes)"])
        self.assertEqual((said["page_price"], said["page_stock"], said["page_barcode"]), ("£699.99", "out of stock", "different"))
        self.assertEqual(said["pack_counts_in_description"], [36])
        self.assertEqual(said["languages_in_description"], ["English"])
        self.assertIn("Contains 36 booster packs", said["shop_description_excerpt"])
        self.assertNotIn("tracking", said["shop_description_excerpt"])
        # The probe's own address, after the shop's robots.txt.
        self.assertEqual(self.pages.asked, [f"https://{self.store.website.split('/')[2]}/robots.txt", self.page + ".js"])

    def test_the_box_variant_has_our_barcode_and_no_variant_leaves_the_page_open(self):
        said = shop_pages.evidence(self.reader(self.pages).facts(self.page + "?variant=41111111111111", self.store), self.product)
        self.assertEqual((said["shop_selected_variant"], said["page_price"], said["page_barcode"]), ("Booster Box", "£119.99", "same"))
        cache.clear()
        said = shop_pages.evidence(self.reader(self.pages).facts(self.page, self.store), self.product)
        self.assertNotIn("shop_selected_variant", said)
        self.assertEqual((said["page_price"], said["page_stock"]), ("£119.99 to 699.99", "in stock"))
        self.assertEqual(said["page_barcode"], "one of the page's variants carries ours")

    def test_a_variant_no_longer_on_the_page_and_a_page_that_moved_say_so(self):
        said = shop_pages.evidence(self.reader(self.pages).facts(self.page + "?variant=1", self.store), self.product)
        self.assertEqual(said["selected_variant"], "the variant in the address is no longer on the page")
        old = f"{self.store.website}products/surging-sparks-box-old"
        pages = Pages({old + ".js": recorded("shopify_product.js")})
        said = shop_pages.evidence(self.reader(pages).facts(old, self.store), self.product)
        self.assertEqual((said["page"], said["shop_now_at"]),
                         ("the address now opens another product page", "/products/surging-sparks-booster-box"))

    def test_a_page_that_is_gone_says_so_and_is_kept(self):
        pages = Pages({self.page + ".js": GONE})
        said = shop_pages.evidence(self.reader(pages).facts(self.page, self.store), self.product)
        self.assertEqual(said["page"], "gone: the shop answers that the page does not exist (HTTP 404)")
        self.assertNotIn("page_price", said)
        self.reader(pages).facts(self.page, self.store)
        self.assertEqual(pages.pages_asked(), [self.page + ".js"])

    def test_robots_txt_that_says_no_is_honoured(self):
        root = f"https://{self.store.website.split('/')[2]}"
        pages = Pages({root + "/robots.txt": b"User-agent: *\nDisallow: /products/\n"})
        said = shop_pages.evidence(self.reader(pages).facts(self.page, self.store), self.product)
        self.assertEqual(said, {"read_from_shop": False, "why_not": "the shop's robots.txt asks robots not to read this page"})
        self.assertEqual(pages.pages_asked(), [])

    def test_the_shop_is_asked_at_its_own_pace_and_a_429_stops_it(self):
        other = f"{self.store.website}products/stellar-crown-booster-box"
        third = f"{self.store.website}products/prismatic-evolutions-etb"
        pages = Pages({self.page + ".js": recorded("shopify_product.js"),
                       other + ".js": ImportError_("Could not fetch x: HTTP Error 429: Too Many Requests")})
        reader = self.reader(pages)
        reader.facts(self.page, self.store)
        self.assertEqual(self.sleeps, [1.0])     # a Shopify shop: one request a second, as its reads
        said = shop_pages.evidence(reader.facts(other, self.store), self.product)
        self.assertEqual(said["why_not"], "the shop did not answer")
        said = shop_pages.evidence(reader.facts(third, self.store), self.product)
        self.assertEqual(said["why_not"], "the shop asked the site to slow down")
        self.assertNotIn(third + ".js", pages.asked)
        self.store.refresh_from_db()
        self.assertIsNone(self.store.backoff_until)

    def test_a_run_reads_a_bounded_number_of_pages(self):
        other = f"{self.store.website}products/stellar-crown-booster-box"
        pages = Pages({self.page + ".js": recorded("shopify_product.js"), other + ".js": recorded("shopify_product.js")})
        reader = self.reader(pages, pages=2)    # robots.txt and one page
        reader.facts(self.page, self.store)
        said = shop_pages.evidence(reader.facts(other, self.store), self.product)
        self.assertEqual(said["why_not"], "this run has read as many pages as it may")
        self.assertEqual(pages.pages_asked(), [self.page + ".js"])

    def test_a_paused_shop_one_being_read_and_another_site_are_not_asked(self):
        pages = Pages({})
        Retailer.objects.filter(pk=self.store.pk).update(reading_paused=True)
        self.store.refresh_from_db()
        self.assertEqual(self.reader(pages).facts(self.page, self.store)["why"], "reading this shop is paused")
        Retailer.objects.filter(pk=self.store.pk).update(reading_paused=False)
        self.store.refresh_from_db()
        self.assertEqual(self.reader(pages).facts("https://elsewhere.example/products/x", self.store)["why"],
                         "the address is not on this shop's site")
        ImportRun.objects.create(retailer=self.store)
        self.assertEqual(self.reader(pages).facts(self.page, self.store)["why"], "the shop is being read now")
        self.assertEqual(pages.asked, [])

    def test_amazon_feed_and_hand_entered_prices_are_never_read(self):
        pages = Pages({})
        for source in (Retailer.Source.AMAZON, Retailer.Source.FEED, Retailer.Source.MANUAL):
            shop = make_retailer(f"Shop {source}", source_type=source)
            self.assertEqual(self.reader(pages).facts(f"{shop.website}p/x", shop)["status"], "not read")
        self.assertEqual(pages.asked, [])

    def test_a_failure_is_not_read_and_tried_again_only_after_an_hour(self):
        pages = Pages({self.page + ".js": ImportError_("Could not fetch x: timed out")})
        said = shop_pages.evidence(self.reader(pages).facts(self.page, self.store), self.product)
        self.assertEqual(said, {"read_from_shop": False, "why_not": "the shop did not answer"})
        self.reader(pages).facts(self.page, self.store)
        self.assertEqual(pages.pages_asked(), [self.page + ".js"])


class WebsiteTests(ReaderBase):
    def setUp(self):
        super().setUp()
        self.store = self.website()
        self.page = f"{self.store.website}product/surging-sparks-booster-display"

    def test_json_ld_title_and_heading_are_read(self):
        pages = Pages({self.page: recorded("website_product.html")})
        said = shop_pages.evidence(self.reader(pages).facts(self.page, self.store), self.product)
        self.assertEqual(said["page"], "one product's page")
        self.assertEqual(said["shop_page_title"], "Surging Sparks Booster Display (36 Packs) | Harbour Games")
        self.assertEqual(said["shop_heading"], "Surging Sparks Booster Display")
        self.assertEqual(said["shop_product_name"], "Surging Sparks Booster Display (36 Packs)")
        self.assertEqual((said["page_price"], said["page_stock"], said["page_barcode"]), ("£124.50", "in stock", "same"))
        self.assertEqual((said["pack_counts_in_description"], said["languages_in_description"]), ([30], ["Japanese"]))
        self.assertNotIn("shop_now_at", said)
        # A website shop: one request every two seconds.
        self.assertEqual(self.sleeps, [2.0])

    def test_a_category_page_is_named_as_one(self):
        address = f"{self.store.website}pokemon-booster-boxes"
        pages = Pages({address: recorded("category.html")})
        said = shop_pages.evidence(self.reader(pages).facts(address, self.store), self.product)
        self.assertEqual(said["page"], "a category page that lists several products, not one product's page (3 listed)")
        self.assertNotIn("page_price", said)
        self.assertEqual(said["shop_heading"], "Booster boxes")

    def test_a_page_that_names_another_address_says_so(self):
        address = f"{self.store.website}product/surging-sparks-old"
        pages = Pages({address: recorded("website_product.html")})
        said = shop_pages.evidence(self.reader(pages).facts(address, self.store), self.product)
        self.assertEqual((said["page"], said["shop_now_at"]),
                         ("the address now opens another product page", "/product/surging-sparks-booster-display"))

    def test_a_404_is_gone(self):
        pages = Pages({self.page: GONE})
        said = shop_pages.evidence(self.reader(pages).facts(self.page, self.store), self.product)
        self.assertTrue(said["page"].startswith("gone"))


@override_settings(RIPRAPTOR_EBAY_APP_ID="app", RIPRAPTOR_EBAY_CERT_ID="cert", RIPRAPTOR_EBAY_CAMPAIGN_ID="1")
class EbayTests(ReaderBase):
    def setUp(self):
        super().setUp()
        self.store = make_retailer("eBay", source_type=Retailer.Source.EBAY, website="https://www.ebay.co.uk/")
        self.page = "https://www.ebay.co.uk/itm/123456789012?mkcid=1"
        self.asked = []

    def request(self, answer):
        def ask(url, headers, data=None):
            self.asked.append(url)
            if "oauth2/token" in url:
                return {"access_token": "t"}
            if isinstance(answer, Exception):
                raise answer
            return answer
        return ask

    def test_ebays_item_lookup_is_read_not_the_page(self):
        from . import ebay

        reader = self.reader(Pages({}), ebay_request=self.request(json.loads(recorded("ebay_item.json"))))
        said = shop_pages.evidence(reader.facts(self.page, self.store), self.product)
        self.assertEqual(said["read_through"], "eBay's item lookup")
        self.assertEqual(said["shop_product_name"], "Pokemon Surging Sparks Booster Box Sealed 36 Packs")
        self.assertEqual((said["page_price"], said["page_stock"], said["page_barcode"], said["shop_condition"]),
                         ("£112.00", "in stock", "same", "New"))
        self.assertIn("Language: English", said["shop_item_specifics"])
        self.assertEqual(self.asked[-1], ebay.ITEMS_URL + "?item_ids=v1|123456789012|0")

    def test_an_ended_listing_is_gone(self):
        from . import ebay

        reader = self.reader(Pages({}), ebay_request=self.request(ebay.EbayError("eBay API 404: 11001 not found")))
        said = shop_pages.evidence(reader.facts(self.page, self.store), self.product)
        self.assertTrue(said["page"].startswith("gone"))


class JudgeEvidenceTests(ReaderBase):
    """The judge sends the page facts, keeps them out of the fingerprint, counts them in the worst case,
    and never reads a page twice in a day."""

    def setUp(self):
        super().setUp()
        self.shop(100)
        self.shop(104)
        self.shopify_shop = self.shopify()
        self.page = f"{self.shopify_shop.website}products/surging-sparks-booster-box"
        self.row = ShopProduct.objects.create(
            retailer=self.shopify_shop, url=self.page + "?variant=42222222222222", title="Surging Sparks Booster Box",
            price=Decimal("101.00"), availability=Listing.Availability.IN_STOCK, suggested=self.product,
            product=self.product, confidence=80, status=ShopProduct.Status.REVIEW, source=ShopProduct.Source.FINDER,
            shop_ean=EAN,
        )
        self.pages = Pages({self.page + ".js": recorded("shopify_product.js")})

    def run_with_pages(self, answer=None, now=None):
        return self.ask(answer, pages=self.reader(self.pages), now=now)

    def test_the_request_carries_the_page_and_the_fingerprint_does_not(self):
        self.switch_on(may_act=False)
        client, _ = self.run_with_pages()
        [(_, params)] = client.sent
        content = params["messages"][0]["content"]
        sent = json.loads(content[len("<evidence>"):-len("</evidence>")])
        page = sent["shop_page"]
        self.assertTrue(page["read_from_shop"])
        self.assertEqual((page["shop_selected_variant"], page["page_price"]), ("Case (6 Boxes)", "£699.99"))
        ask = ClaudeAsk.objects.get()
        self.assertEqual(ask.evidence["shop_page"], page)
        built = judge.Evidence({self.product.pk}).found(self.row)
        self.assertEqual(ask.fingerprint, built.fingerprint)
        # The page's facts are part of what the request could cost.
        bare = judge.request_params(judge.ClaudeJudge.load(), built, cache=False)
        self.assertGreater(judge.worst_case_micros(params, "claude-opus-5-5"), judge.worst_case_micros(bare, "claude-opus-5-5"))
        self.assertLess(len(json.dumps(page, ensure_ascii=False)), shop_pages.MAX_FACT_CHARS)

    def test_the_second_look_reads_the_kept_page_not_the_shop(self):
        self.switch_on(may_act=False)
        now = timezone.now()
        self.run_with_pages(lambda key, params: reply(key, "unsure", "medium", reason="The variant may be a case."), now=now)
        client, _ = self.run_with_pages(now=now + timedelta(hours=2))
        self.assertEqual(client.sent[0][1]["output_config"]["effort"], "high")
        self.assertEqual(self.pages.pages_asked(), [self.page + ".js"])

    def test_a_page_difference_refuses_a_found_page(self):
        self.switch_on()
        self.run_with_pages(lambda key, params: reply(key, "different", "high", ["page"], reason="The address sells a case."))
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, ShopProduct.Status.IGNORED)

    def test_a_dry_run_reads_no_page(self):
        self.switch_on()
        result = judge.run(dry_run=True, force=True, pages=shop_pages.Reader(fetch_for=self.pages.fetch_for, cache_only=True))
        self.assertEqual(self.pages.asked, [])
        self.assertTrue(any('"read_from_shop": false' in line for line in result.lines))

    def test_a_pair_carries_a_page_of_each_product(self):
        other = make_product(self.set, name=f"Scarlet & Violet {BOX}", product_type="booster_box", ean=EAN)
        make_listing(other, self.shopify_shop, url=self.page + "?variant=41111111111111", price="101.00", title=other.name)
        ShopProduct.objects.all().delete()
        self.switch_on(may_act=False)
        client, _ = self.run_with_pages()
        sent = [json.loads(p["messages"][0]["content"][len("<evidence>"):-len("</evidence>")]) for _, p in client.sent]
        [pair] = [row for row in sent if row["kind"] == "pair"]
        first, second = pair["first_shop_page"], pair["second_shop_page"]
        self.assertFalse(first["read_from_shop"])     # the first product's shops are entered by hand
        self.assertEqual((second["read_from_shop"], second["page_barcode"], second["shop"]), (True, "same", "Shopify Shop"))

    def test_with_pages_switched_off_the_evidence_is_as_before(self):
        self.switch_on(may_act=False)
        client, _ = self.ask()
        self.assertNotIn("shop_page", client.sent[0][1]["messages"][0]["content"])
        self.assertEqual(self.pages.asked, [])


PRODUCT_PAGE = (b'<html><head><title>Elsewhere</title><script type="application/ld+json">{"@type":"Product",'
                b'"name":"Another Site Product","offers":{"@type":"Offer","price":"9.99","priceCurrency":"GBP",'
                b'"availability":"InStock"}}</script></head><body><h1>Elsewhere</h1></body></html>')


def serve(answer):
    """A local web server whose GET is answered by ``answer(path) -> (status, headers, body)``. Returns
    (base address, the paths asked)."""
    import http.server
    import socketserver
    import threading

    asked = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            status, headers, body = answer(self.path)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, asked


class RedirectTests(ReaderBase):
    """The judge's own fetch, against local servers: a redirect never leaves the shop's site."""

    def start(self, answer):
        server, asked = serve(answer)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}", asked

    def local_shop(self, base):
        return make_retailer("Local Shop", source_type=Retailer.Source.WEBSITE, source_url=base + "/",
                             delivery_cost=Decimal("0"))

    def local_reader(self):
        return shop_pages.Reader(fetch_for=lambda shop: shop_pages.page_fetch(shop, allow_private=True),
                                 sleep=lambda seconds: None)

    def test_a_redirect_to_another_site_is_refused_and_never_fetched(self):
        other, other_asked = self.start(lambda path: (200, {}, PRODUCT_PAGE))
        other = other.replace("127.0.0.1", "localhost")
        base, asked = self.start(lambda path: (404, {}, b"") if path == "/robots.txt"
                                 else (302, {"Location": other + "/internal"}, b""))
        store = self.local_shop(base)
        reader = self.local_reader()
        said = shop_pages.evidence(reader.facts(base + "/product/box", store), self.product)
        self.assertEqual(said, {"read_from_shop": False, "why_not": "the shop sent the page to another site"})
        self.assertEqual(other_asked, [])
        self.assertEqual(asked, ["/robots.txt", "/product/box"])
        self.assertNotIn(store.pk, reader.blocked)
        self.assertEqual(reader.errors.get(store.pk, 0), 0)

    def test_a_redirect_within_the_shop_is_followed_and_reported_as_moved(self):
        def answer(path):
            if path == "/robots.txt":
                return 404, {}, b""
            if path == "/product/old-box":
                return 301, {"Location": "/product/new-box"}, b""
            return 200, {}, PRODUCT_PAGE
        base, asked = self.start(answer)
        store = self.local_shop(base)
        said = shop_pages.evidence(self.local_reader().facts(base + "/product/old-box", store), self.product)
        self.assertEqual((said["page"], said["shop_now_at"], said["page_price"]),
                         ("the address now opens another product page", "/product/new-box", "£9.99"))
        self.assertEqual(asked, ["/robots.txt", "/product/old-box", "/product/new-box"])

    def test_private_addresses_and_other_schemes_are_never_asked(self):
        base, asked = self.start(lambda path: (200, {}, PRODUCT_PAGE))
        store = self.local_shop(base)
        reader = shop_pages.Reader(sleep=lambda seconds: None)   # the real fetch, private addresses refused
        facts = reader.facts(base + "/product/box", store)
        self.assertEqual(facts["status"], "not read")
        self.assertEqual(asked, [])
        self.assertEqual(shop_pages.refused("file:///etc/passwd", {""}), "the address is not a web page")
        self.assertEqual(shop_pages.refused("http://169.254.169.254/latest", {"169.254.169.254"}),
                         "the address is inside a private network")
        self.assertEqual(self.reader(Pages({})).facts("ftp://www.shopify-shop.example/x", self.shopify())["why"],
                         "the address is not a web page")
