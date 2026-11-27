"""Stock nobody can simply buy, said where people and machines look. The owner, 27 Nov 2026, of another site showing
invite-only boxes as deals: "doesn't know when it's invite only ripraptor does we should be telling everyone this".
The reader leaves out raffles and boxes kept for members, VIPs, invited customers or collection
(catalogue/tests_unavailable.py); here: a product page says so under its prices, Rip is told which shops are left out
and why, and the how it works page asks and answers it, in its FAQPage and in llms.txt, so search engines and AI
assistants read it in the site's own words."""

import json
import re
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue import judge
from catalogue.models import Listing
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from content.registry import REGISTRY

from . import askrip
from .tests_askrip import KEY, FakeClient

QUESTION = "Does RipRaptor show raffle, members-only or invite-only stock as a deal?"


def blocks(html):
    found = []
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        data = json.loads(raw)
        found.extend(data if isinstance(data, list) else [data])
    return found


class LeftOutCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.box = make_product(make_set(make_game()), name="Surging Sparks Booster Box",
                                slug="surging-sparks-booster-box", product_type="booster_box", packs=36)
        self.harbour = make_retailer("Harbour Games")
        make_listing(self.box, self.harbour, price="140.00")
        # A box kept for those a shop invites, one kept for its VIPs, and an open raffle: each the shop's own words.
        make_listing(self.box, make_retailer("Sleeved"), price="99.00", availability=Listing.Availability.OUT_OF_STOCK,
                     closed_why=Listing.Closed.INVITE)
        make_listing(self.box, make_retailer("Eclipse Cards"), price="105.00",
                     availability=Listing.Availability.OUT_OF_STOCK, closed_why=Listing.Closed.MEMBERS)
        make_listing(self.box, make_retailer("Obsidia"), price="95.00", availability=Listing.Availability.LOTTERY)
        make_listing(self.box, make_retailer("Moor Toys"), price="150.00",
                     availability=Listing.Availability.OUT_OF_STOCK)


class ProductPageTests(LeftOutCase):
    def test_the_price_list_says_what_it_leaves_out_and_each_row_says_why(self):
        html = self.client.get(self.box.get_absolute_url()).content.decode()
        line = REGISTRY["product.compare.only_buyable"].default
        self.assertIn(f'<p class="small-print">{line}</p>', html)
        self.assertIn("invite-only", line)
        self.assertLess(html.index(line), html.index("Affiliate links."))
        self.assertIn('<span class="stock-inline stock-inline--out_of_stock">Invite only</span>', html)
        self.assertIn('<span class="stock-inline stock-inline--out_of_stock">Shop members only</span>', html)
        # The cheapest it names is one anyone can buy, never the invite-only box's lower price.
        price_box = html[html.index("buybox"):html.index('class="block offers-section"')]
        self.assertIn("£140.00", price_box)
        self.assertNotIn("£99.00", price_box)


@override_settings(RIPRAPTOR_CLAUDE=True, RIPRAPTOR_CLAUDE_API_KEY=KEY)
class RipTests(LeftOutCase):
    def test_rip_is_told_which_shops_are_left_out_and_why_with_no_price(self):
        found, amounts, _checked = askrip.facts(self.box)
        self.assertEqual(found["left_out_of_the_comparison"], [
            {"shop": "Eclipse Cards", "why": "kept for the shop's members or VIPs"},
            {"shop": "Obsidia", "why": "sold by a prize draw: entering is not buying"},
            {"shop": "Sleeved", "why": "invite only"},
        ])
        # Out of stock for no stated reason is still just not for sale now; the left out are not named twice.
        self.assertEqual(found["not_for_sale_now_at"], ["Moor Toys"])
        self.assertNotIn(Decimal("99.00"), amounts)
        self.assertNotIn("Sleeved", [offer["shop"] for offer in found["offers"]])

    def test_rip_knows_the_rule_and_is_asked_with_it(self):
        self.assertIn("invite only, or for collection only is never counted as in stock or as a deal",
                      askrip.SYSTEM_PROMPT)
        self.assertIn('"left_out_of_the_comparison"', askrip.SYSTEM_PROMPT)
        fake = FakeClient()
        fake.answer = "VERDICT: Real deal\nIt is a real deal. Sleeved's box is invite only, so it is not counted."
        with mock.patch.object(judge, "make_client", return_value=fake):
            self.client.post(reverse("web:ask_rip", args=[self.box.slug]), {"question": "Is this deal real?"},
                             REMOTE_ADDR="203.0.113.7", HTTP_X_ASK_RIP="1")
        (sent,) = fake.sent
        self.assertIn("invite only", json.dumps(sent["messages"]))


class HowItWorksTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_the_question_is_on_the_page_in_its_faqpage_and_in_llms_txt(self):
        answer = REGISTRY["about.faq.stock.a"].default
        html = self.client.get(reverse("web:about")).content.decode()
        self.assertIn(QUESTION, html)
        self.assertIn(answer, html)
        (faq,) = [block for block in blocks(html) if block.get("@type") == "FAQPage"]
        entries = {entry["name"]: entry["acceptedAnswer"]["text"] for entry in faq["mainEntity"]}
        self.assertEqual(entries[QUESTION], answer)
        llms = self.client.get("/llms.txt").content.decode()
        self.assertIn(QUESTION, llms)
        self.assertIn(answer, llms)
        # The Why RipRaptor point says it too, on the page and in llms.txt.
        point = REGISTRY["about.why.stock.body"].default
        self.assertIn("invited customers", point)
        self.assertIn(point, llms)

    def test_no_other_site_is_named(self):
        for key in ("about.faq.stock.q", "about.faq.stock.a", "about.why.stock.body", "product.compare.only_buyable"):
            with self.subTest(key=key):
                self.assertNotRegex(REGISTRY[key].default.lower(), r"tcg ?compare|other sites?|competitor")
