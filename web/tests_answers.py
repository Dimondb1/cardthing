"""The written answers on a game's page and a set's page (web/answers.py), the guide built from live prices
(guides/compare.py) and the counts they share. The owner, 9 Nov 2026: "Make the existing Pokémon page answer price,
delivery and stock comparison queries ... Every figure comes from the database at render time. Nothing hard-coded."
"""

import html as html_text
import json
import re
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Listing, Product, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from guides.models import Guide, not_reserved
from web import answers

from .tests_compact import section, switch

BOX, ETB, BUNDLE, PACK, DECK, COLLECTOR = (
    Product.Type.BOOSTER_BOX, Product.Type.ELITE_TRAINER_BOX, Product.Type.BUNDLE, Product.Type.BOOSTER_PACK,
    Product.Type.DECK, Product.Type.COLLECTOR_BOOSTER_BOX)
GUIDE = "/guides/compare-sealed-tcg-prices-uk/"


def structured(html):
    """Every JSON-LD block on the page, parsed (a block that does not parse fails the test), flattened."""
    found = []
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        data = json.loads(block)
        found += data if isinstance(data, list) else [data]
    return found


def of_type(html, kind):
    return [block for block in structured(html) if block.get("@type") == kind]


def shown_text(markup):
    """What a reader sees of ``markup``: its tags gone, its entities read."""
    return " ".join(html_text.unescape(re.sub(r"<[^>]*>", "", markup)).split())


class AnswersCase(TestCase):
    """Pokémon with a box, an Elite Trainer Box, a bundle and a pack in stock at three shops whose delivery differs,
    and beside them the offers an answer must never name: a cheaper Japanese box, an eBay price and a doubtful one."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        switch()
        self.pokemon = make_game()
        self.sparks = make_set(self.pokemon, name="Surging Sparks", slug="surging-sparks", code="SSP",
                               release_date=date(2024, 11, 8))
        self.harbour = make_retailer("Harbour Games", delivery_cost=Decimal("0"))
        self.kestrel = make_retailer("Kestrel Cards", delivery_cost=Decimal("3.99"), free_delivery_over=Decimal("50"))
        self.moor = make_retailer("Moor Toys", delivery_cost=Decimal("4.50"))
        self.box = self.item("Surging Sparks Booster Box", BOX, packs=36)
        self.offer(self.box, self.harbour, "150.00")
        self.box_best = self.offer(self.box, self.kestrel, "144.00", "3.99")      # £147.99 delivered
        self.etb = self.item("Surging Sparks Elite Trainer Box", ETB, packs=9)
        self.offer(self.etb, self.moor, "44.00", "4.50")                          # the lowest list price: £48.50
        self.etb_best = self.offer(self.etb, self.harbour, "47.99")               # the cheapest delivered
        self.bundle = self.item("Surging Sparks Booster Bundle", BUNDLE, packs=6)
        self.bundle_best = self.offer(self.bundle, self.kestrel, "46.49", "3.99")   # £3.51 under Kestrel's line
        self.pack = self.item("Surging Sparks Booster Pack", PACK)
        self.offer(self.pack, self.harbour, "4.50")
        # Never named: another language, a marketplace, a price the checks doubt.
        self.japanese = self.item("Surging Sparks Booster Box (Japanese)", BOX, packs=30)
        self.offer(self.japanese, self.harbour, "60.00")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        self.offer(self.box, self.ebay, "100.00")
        self.offer(self.box, self.moor, "50.00", "4.50", sanity=Listing.Sanity.DOUBTFUL)
        self.sold_out = self.item("Surging Sparks Tin", Product.Type.TIN)
        self.offer(self.sold_out, self.harbour, "20.00", availability=Listing.Availability.OUT_OF_STOCK)

    def item(self, name, kind, product_set=None, **fields):
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return make_product(product_set or self.sparks, name=name, slug=slug, product_type=kind, **fields)

    def offer(self, product, shop, price, delivery="0.00", **fields):
        return make_listing(product, shop, price=price, delivery=delivery, **fields)

    def page(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def publish_a_guide(self):
        return Guide.objects.create(slug="parents", title="A parent's guide", one_line_answer="What to buy.",
                                    is_published=True, published_at=timezone.now())


class GameAnswerTests(AnswersCase):
    def test_the_heading_says_what_the_page_compares_and_the_line_counts_it(self):
        html = self.page(self.pokemon.get_absolute_url())
        self.assertIn('<h1 class="cx-hero__title">Pokémon sealed product prices in the UK</h1>', html)
        self.assertEqual(html.count("<h1"), 1)
        # Five products are in stock (the Japanese box too), at three UK shops (eBay is not one), six are listed, in
        # one set.
        self.assertIn('<p class="cx-hero__lead">5 sealed Pokémon products in stock at 3 UK shops, out of 6 listed across '
                      '1 set. Every price includes UK delivery.</p>', html)

    def test_the_cheapest_of_each_main_kind_is_named_with_its_shop_price_and_check(self):
        html = self.page(self.pokemon.get_absolute_url())
        block = section(html, '<section class="hub-answer"', "</section>")
        self.assertIn('<h2 id="hub-answer-title" class="hub-answer__title">Cheapest Pokémon prices in stock now</h2>', block)
        rows = re.findall(r'<li class="hub-answer__row">(.*?)</li>', block, re.S)
        self.assertEqual(len(rows), 3)
        expected = [("Booster box", self.box, "£147.99", "Kestrel Cards", self.box_best),
                    ("Elite Trainer Box", self.etb, "£47.99", "Harbour Games", self.etb_best),
                    ("Booster bundle", self.bundle, "£50.48", "Kestrel Cards", self.bundle_best)]
        for row, (kind, product, price, shop, listing) in zip(rows, expected):
            with self.subTest(kind=kind):
                self.assertIn(f'<span class="hub-answer__kind">{kind}</span>', row)
                self.assertIn(f'<a class="hub-answer__product" href="{product.get_absolute_url()}">{product.name}</a>', row)
                self.assertIn(f'<strong class="hub-answer__price">{price}</strong> delivered at {shop}', row)
                self.assertIn(f"Checked {answers.stamp(listing.last_checked)}", row)
        # A date and a time, so the sentence is still true when a search engine or an assistant repeats it.
        self.assertRegex(answers.stamp(self.box_best.last_checked), r"^\d{1,2} [A-Z][a-z]{2} \d{4} at \d\d:\d\d$")
        # A cheaper Japanese box, an eBay price and a doubted price are never the answer.
        self.assertNotIn("Japanese)", block)
        self.assertNotIn("£100.00", block)
        self.assertNotIn("£54.50", block)
        self.assertIn("English-language products in stock at UK shops. Prices include UK delivery.", block)

    def test_the_questions_are_answered_with_live_figures(self):
        html = self.page(self.pokemon.get_absolute_url())
        faq = section(html, '<section class="hub-faq"', "</section>")
        self.assertIn("Pokémon prices: questions and answers", faq)
        shown = dict(re.findall(r'<h3 class="hub-faq__q">(.*?)</h3><p class="hub-faq__a">(.*?)</p>', faq, re.S))
        shown = {shown_text(question): shown_text(answer) for question, answer in shown.items()}
        self.assertTrue(5 <= len(shown) <= 7, shown.keys())
        when = answers.stamp(self.box_best.last_checked)
        self.assertEqual(
            shown["Where is the cheapest place to buy Pokémon booster boxes in the UK?"],
            "Right now the cheapest English-language Pokémon booster box in stock is Surging Sparks Booster Box at "
            f"Kestrel Cards: £147.99 delivered, checked on {when}. English-language Pokémon booster boxes are in stock "
            "at 2 UK shops. The cheapest changes as shops sell out and change their prices, so check on the day you buy.")
        self.assertIn("For example, Surging Sparks Booster Box at Kestrel Cards is £144.00 plus £3.99 delivery: £147.99 "
                      "delivered.", shown["Do the Pokémon prices on RipRaptor include delivery?"])
        self.assertIn("Right now 5 of the 6 sealed Pokémon products listed are in stock.",
                      shown["How do you know a Pokémon product is really in stock?"])
        self.assertIn(f"The latest check of a Pokémon price was on {when}.", shown["How often are Pokémon prices checked?"])
        self.assertEqual(
            shown["How many shops and Pokémon products does RipRaptor compare?"],
            "6 sealed Pokémon products are listed across 1 set, and 5 of them are in stock now at 3 UK shops. Across "
            "every game, RipRaptor compares prices from 3 UK shops.")
        # 36 packs at £147.99 is £4.11 a pack, under the single pack's £4.50.
        self.assertEqual(
            shown["Is a Pokémon booster box cheaper per pack than single booster packs?"],
            "Yes, at today's prices. The cheapest booster box in stock, Surging Sparks Booster Box, holds 36 packs: "
            "£4.11 a pack at £147.99 delivered from Kestrel Cards. The cheapest single booster pack in stock is "
            "Surging Sparks Booster Pack at £4.50 delivered from Harbour Games.")
        self.assertEqual(shown["Can I buy Pokémon products from RipRaptor?"],
                         "No. RipRaptor sells nothing. It compares prices from 3 UK shops and links to the shop to buy. "
                         "It may earn a commission when you buy.")

    def test_the_faq_structured_data_matches_the_page_word_for_word(self):
        html = self.page(self.pokemon.get_absolute_url())
        faq = section(html, '<section class="hub-faq"', "</section>")
        shown = [(shown_text(question), shown_text(answer)) for question, answer in
                 re.findall(r'<h3 class="hub-faq__q">(.*?)</h3><p class="hub-faq__a">(.*?)</p>', faq, re.S)]
        (block,) = of_type(html, "FAQPage")
        marked = [(entry["name"], entry["acceptedAnswer"]["text"]) for entry in block["mainEntity"]]
        self.assertEqual(marked, shown)
        self.assertTrue(all(entry["@type"] == "Question" and entry["acceptedAnswer"]["@type"] == "Answer"
                            for entry in block["mainEntity"]))
        self.assertEqual(block["@id"], "http://testserver/games/pokemon/#faq")

    def test_the_products_named_are_an_item_list(self):
        html = self.page(self.pokemon.get_absolute_url())
        (page,) = of_type(html, "CollectionPage")
        listed = page["mainEntity"]
        self.assertEqual(listed["@type"], "ItemList")
        urls = [entry["url"] for entry in listed["itemListElement"]]
        for product in (self.box, self.etb, self.bundle):   # the answer's picks lead, then the deals shown
            self.assertIn(f"http://testserver{product.get_absolute_url()}", urls)
        self.assertEqual(urls[0], f"http://testserver{self.box.get_absolute_url()}")
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(listed["numberOfItems"], len(urls))
        self.assertEqual([entry["position"] for entry in listed["itemListElement"]], list(range(1, len(urls) + 1)))
        self.assertEqual(len(of_type(html, "BreadcrumbList")), 1)

    def test_the_description_the_page_and_llms_txt_quote_the_same_counts(self):
        # The owner, 9 Nov 2026: the description said 1,901 in stock at 101 shops, the page 535 at 130, llms.txt 5772.
        html = self.page(self.pokemon.get_absolute_url())
        description = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
        line = shown_text(section(html, '<p class="cx-hero__lead">', "</p>"))
        llms = self.client.get("/llms.txt").content.decode()
        self.assertTrue(description.startswith("5 sealed Pokémon products in stock at 3 UK shops, from £4.50 delivered."))
        self.assertTrue(line.startswith("5 sealed Pokémon products in stock at 3 UK shops, out of 6 listed"))
        self.assertIn("- [Pokémon](http://testserver/games/pokemon/): 6 sealed products listed, 5 in stock now at "
                      "3 UK shops", llms)
        self.assertIn("5 of the 6 sealed Pokémon products listed are in stock", html)
        self.assertIn("Listed counts every sealed product of the game on the site.", llms)

    def test_ebay_and_amazon_are_never_counted_as_uk_shops(self):
        # "UK shops" means one thing: the shops the home page and Shops we compare count. A game only an eBay seller
        # has in stock says so, and claims no shop.
        only = make_game(name="One Piece Card Game", slug="one-piece", short_name="One Piece", search_aliases="")
        op = make_set(only, name="Royal Blood", slug="royal-blood", code="OP-10")
        self.offer(self.item("Royal Blood Booster Box", BOX, product_set=op), self.ebay, "90.00")
        html = self.page(only.get_absolute_url())
        self.assertIn("1 sealed product, 1 in stock now with an eBay or Amazon seller and none at a UK shop we check.",
                      html)
        self.assertNotIn("0 UK shops", html)
        self.assertIn("1 of them are in stock now with an eBay or Amazon seller, none at a UK shop.", html)
        self.assertIn("1 sealed product listed, 1 in stock now with an eBay or Amazon seller, none at a UK shop",
                      self.client.get("/llms.txt").content.decode())
        description = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
        self.assertNotIn("in stock at", description)

    def test_the_title_carries_the_site_name(self):
        html = self.page(self.pokemon.get_absolute_url())
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        self.assertTrue(html_text.unescape(title).endswith(" | RipRaptor"), title)
        self.assertLessEqual(len(html_text.unescape(title)), 70)

    def test_magic_leads_with_its_own_kinds_in_its_own_words(self):
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic", search_aliases="mtg")
        fin = make_set(magic, name="Final Fantasy", slug="final-fantasy", code="FIN")
        for name, kind, price in (("Final Fantasy Play Booster Box", BOX, "120.00"),
                                  ("Final Fantasy Collector Booster Box", COLLECTOR, "300.00"),
                                  ("Final Fantasy Commander Deck", DECK, "45.00"),
                                  ("Final Fantasy Bundle", BUNDLE, "55.00")):
            self.offer(self.item(name, kind, product_set=fin), self.harbour, price)
        html = self.page(magic.get_absolute_url())
        self.assertIn('<h1 class="cx-hero__title">Magic: The Gathering sealed product prices in the UK</h1>', html)
        block = section(html, '<section class="hub-answer"', "</section>")
        self.assertEqual(re.findall(r'<span class="hub-answer__kind">(.*?)</span>', block),
                         ["Play booster box", "Collector booster box", "Commander deck", "Bundle"])
        self.assertIn("Where is the cheapest place to buy Magic play booster boxes in the UK?", html)

    def test_a_game_with_nothing_in_stock_says_only_what_is_true(self):
        quiet = make_game(name="Disney Lorcana", slug="lorcana", short_name="Lorcana", search_aliases="")
        fabled = make_set(quiet, name="Fabled", slug="fabled", code="FAB")
        self.offer(self.item("Fabled Booster Box", BOX, product_set=fabled), self.harbour, "110.00",
                   availability=Listing.Availability.OUT_OF_STOCK)
        html = self.page(quiet.get_absolute_url())
        self.assertNotIn("hub-answer", html)
        self.assertIn("1 sealed product compared across 1 UK shop, none in stock right now.", html)
        (block,) = of_type(html, "FAQPage")
        questions = [entry["name"] for entry in block["mainEntity"]]
        self.assertNotIn("Where is the cheapest place to buy Lorcana booster boxes in the UK?", questions)
        self.assertIn("How many shops and Lorcana products does RipRaptor compare?", questions)
        self.assertIn("None is in stock right now.", html)
        self.assertEqual(of_type(html, "CollectionPage"), [])   # no product is named, so none is listed

    def test_the_answer_costs_a_few_queries_however_big_the_game(self):
        kinds = answers.main_types(self.pokemon)
        with self.assertNumQueries(len(kinds) + 3):   # the counts, the cheapest of each kind, the pack, the last check
            facts = answers.game_facts(self.pokemon)
        self.assertEqual([pick.kind for pick in facts["picks"]], kinds)

    def test_the_link_to_the_guide_shows_once_the_guides_are_open(self):
        self.assertNotIn(GUIDE, self.page(self.pokemon.get_absolute_url()))
        self.publish_a_guide()
        html = self.page(self.pokemon.get_absolute_url())
        self.assertIn(f'<p class="hub-faq__more"><a href="{GUIDE}">How to compare sealed TCG prices in the UK</a></p>', html)


class SetAnswerTests(AnswersCase):
    def test_a_set_opens_with_a_sentence_or_two_of_live_figures(self):
        html = self.page(self.sparks.get_absolute_url())
        intro = shown_text(section(html, '<p class="cx-setintro">', "</p>"))
        self.assertEqual(intro, "6 sealed products compared across 3 UK shops, 5 in stock now. Cheapest in stock: "
                                "Surging Sparks Booster Pack, £4.50 delivered at Harbour Games.")
        self.assertIn(f'<a href="{self.pack.get_absolute_url()}">Surging Sparks Booster Pack</a>', html)
        self.assertIn('<h2 id="set-list-title" class="visually-hidden">Surging Sparks products and prices</h2>', html)
        self.assertEqual(html.count("<h1"), 1)

    def test_its_products_are_an_item_list_and_a_narrowed_list_is_not(self):
        html = self.page(self.sparks.get_absolute_url())
        (page,) = of_type(html, "CollectionPage")
        urls = [entry["url"] for entry in page["mainEntity"]["itemListElement"]]
        self.assertEqual(len(urls), 6)
        self.assertIn(f"http://testserver{self.box.get_absolute_url()}", urls)
        self.assertEqual(page["url"], f"http://testserver{self.sparks.get_absolute_url()}")
        narrowed = self.page(self.sparks.get_absolute_url(), type="booster_box")
        self.assertEqual(of_type(narrowed, "CollectionPage"), [])
        self.assertEqual(len(of_type(narrowed, "BreadcrumbList")), 1)
        self.assertNotIn("cx-setintro", narrowed)


class CompareGuideTests(AnswersCase):
    def setUp(self):
        super().setUp()
        self.box.rrp = Decimal("161.64")
        self.box.save()
        coming = make_set(self.pokemon, name="Delta Reign", slug="delta-reign", code="ME03",
                          release_date=timezone.localdate() + timedelta(days=20))
        self.preorder = self.item("Delta Reign Booster Box", BOX, product_set=coming)
        self.offer(self.preorder, self.harbour, "139.00", availability=Listing.Availability.PREORDER)

    def test_it_is_open_once_the_guides_are_and_reserved_for_itself(self):
        self.assertEqual(self.client.get(GUIDE).status_code, 404)
        self.publish_a_guide()
        self.assertEqual(self.client.get(GUIDE).status_code, 200)
        self.assertEqual(reverse("web:guide_compare"), GUIDE)
        with self.assertRaises(ValidationError):
            not_reserved("compare-sealed-tcg-prices-uk")

    def test_every_point_is_shown_with_a_real_price(self):
        self.publish_a_guide()
        html = self.page(GUIDE)
        self.assertIn('<h1 id="guide-title" class="page-title">How to compare sealed TCG prices in the UK</h1>', html)
        self.assertEqual(html.count("<h1"), 1)
        self.assertEqual(len(re.findall(r'<section class="guide-section"', html)), 7)
        text = shown_text(html)
        # The lowest list price is not the cheapest delivered: Moor Toys' £44.00 comes to £48.50.
        self.assertIn("Surging Sparks Elite Trainer Box: Moor Toys has the lowest price on its page, £44.00, but £4.50 "
                      "delivery makes it £48.50. Harbour Games lists it at £47.99 with free delivery, so it costs £47.99 "
                      "delivered: £0.51 less.", text)
        self.assertIn(f'<a href="{self.etb.get_absolute_url()}" data-compare="guide_example">', html)
        self.assertIn("1 of the 3 UK shops RipRaptor compares deliver every order free. Where a shop charges, its "
                      "standard delivery runs from £3.99 to £4.50.", text)
        self.assertIn("1 of the 3 UK shops RipRaptor compares deliver free once an order passes a set value, most often "
                      "£50.00.", text)
        self.assertIn("Surging Sparks Booster Bundle at Kestrel Cards is £46.49, £3.51 under the shop's £50.00 free "
                      "delivery line, so £3.99 delivery is added: £50.48 delivered.", text)
        self.assertIn("Right now 5 of the 7 sealed products RipRaptor lists are in stock, and 1 more can only be "
                      "pre-ordered.", text)
        due = f"{self.preorder.product_set.release_date:%-d %B %Y}"
        self.assertIn(f"Delta Reign Booster Box is due out on {due}. It can be pre-ordered now from £139.00 delivered "
                      "at Harbour Games.", text)
        self.assertIn("Surging Sparks Booster Box holds 36 packs and is £147.99 delivered at Kestrel Cards: £4.11 a "
                      "pack. A single pack, Surging Sparks Booster Pack, is £4.50 delivered at Harbour Games.", text)
        self.assertIn("Surging Sparks Booster Box has an RRP of £161.64. Its cheapest delivered price in stock today is "
                      "£147.99 at Kestrel Cards, 8% under the RRP.", text)
        self.assertIn("1 of the 7 sealed Pokémon products RipRaptor lists are Japanese-language.", text)
        self.assertIn("The cheapest English-language Pokémon booster box in stock is Surging Sparks Booster Box at "
                      "£147.99 delivered from Kestrel Cards. The cheapest Japanese-language one is Surging Sparks "
                      "Booster Box (Japanese) at £60.00 delivered from Harbour Games.", text)
        self.assertIn("from sellers with at least 95% positive feedback and 50 or more ratings.", text)
        self.assertIn("Right now 1 eBay and 0 Amazon prices are in the comparison", text)
        self.assertIn(f"Every example on this page is a real price, last checked on {answers.stamp(self.box_best.last_checked)[:11]}",
                      text)
        self.assertIn(f'<a href="{self.pokemon.get_absolute_url()}">Pokémon</a>', html)

    def test_a_raffle_is_counted_as_neither_stock_nor_pre_order(self):
        self.publish_a_guide()
        self.offer(self.sold_out, self.moor, "20.00", availability=Listing.Availability.LOTTERY)
        self.assertIn("1 listing is a raffle or ballot and counts as neither.", shown_text(self.page(GUIDE)))

    def test_its_structured_data_is_an_article_with_its_dates(self):
        self.publish_a_guide()
        html = self.page(GUIDE)
        (article,) = of_type(html, "Article")
        self.assertEqual(article["headline"], "How to compare sealed TCG prices in the UK")
        self.assertEqual(article["datePublished"], "2026-11-09")
        self.assertGreaterEqual(article["dateModified"], article["datePublished"])
        self.assertEqual(article["dateModified"], max(date(2026, 11, 9), timezone.localdate()).isoformat())
        self.assertEqual(article["mainEntityOfPage"], f"http://testserver{GUIDE}")
        self.assertEqual(article["publisher"]["name"], "RipRaptor")
        (crumbs,) = of_type(html, "BreadcrumbList")
        self.assertEqual(crumbs["itemListElement"][-1]["item"], f"http://testserver{GUIDE}")
        self.assertIn(f'<link rel="canonical" href="http://testserver{GUIDE}">', html)

    def test_it_is_on_the_guides_page_in_the_sitemap_and_in_llms_txt(self):
        self.assertNotIn(GUIDE, self.client.get("/sitemap.xml").content.decode())
        self.assertNotIn(GUIDE, self.client.get("/llms.txt").content.decode())
        self.publish_a_guide()
        hub = self.page(reverse("web:guides"))
        self.assertIn(f'<h2 class="guide-tile__title"><a href="{GUIDE}">How to compare sealed TCG prices in the UK</a></h2>',
                      hub)
        self.assertIn(f"<loc>http://testserver{GUIDE}</loc><lastmod>", self.client.get("/sitemap.xml").content.decode())
        self.assertIn(f"- [How to compare sealed TCG prices in the UK](http://testserver{GUIDE}): Compare the delivered "
                      "price", self.client.get("/llms.txt").content.decode())

    def test_with_no_prices_it_still_gives_its_advice_and_invents_nothing(self):
        Listing.objects.all().delete()
        self.publish_a_guide()
        html = self.page(GUIDE)
        self.assertEqual(len(re.findall(r'<section class="guide-section"', html)), 7)
        self.assertNotIn("guide-live", html)
        self.assertNotIn("£", shown_text(section(html, '<article class="guide"', "</article>")))
        (article,) = of_type(html, "Article")
        self.assertEqual(article["dateModified"], "2026-11-09")


class EveryBlockParsesTests(AnswersCase):
    def test_the_structured_data_of_every_changed_page_parses(self):
        self.publish_a_guide()
        for url in ("/", self.pokemon.get_absolute_url(), self.sparks.get_absolute_url(), GUIDE, reverse("web:guides")):
            with self.subTest(url=url):
                html = self.page(url)
                self.assertTrue(structured(html))
                self.assertEqual(html.count("<h1"), 1)


class ProofTests(AnswersCase):
    """The checks ``manage.py visibility_proof`` runs on the live site (web/proof.py), run here on the test site's
    pages: they pass on the pages as built and fail on the faults they are there to catch."""

    def setUp(self):
        super().setUp()
        self.publish_a_guide()
        magic = make_game(name="Magic: The Gathering", slug="magic-the-gathering", short_name="Magic", search_aliases="mtg")
        fin = make_set(magic, name="Final Fantasy", slug="final-fantasy", code="FIN")
        self.offer(self.item("Final Fantasy Play Booster Box", BOX, product_set=fin), self.harbour, "120.00")

    def failed(self, checks):
        return [name for name, passed, _detail in checks if not passed]

    def test_every_check_passes_on_the_pages_as_built(self):
        from web import proof

        site = "http://testserver"
        llms = self.client.get("/llms.txt").content.decode()
        path = self.pokemon.get_absolute_url()
        self.assertEqual(self.failed(proof.game(self.page(path), proof.llms_line(llms, path))), [])
        self.assertEqual(self.failed(proof.product_set(self.page(self.sparks.get_absolute_url()))), [])
        self.assertEqual(self.failed(proof.guide(self.page(GUIDE))), [])
        self.assertEqual(self.failed(proof.home(self.page("/"))), [])
        self.assertEqual(self.failed(proof.guides_index(self.page(reverse("web:guides")))), [])
        self.assertEqual(self.failed(proof.llms(llms, site)), [])
        self.assertEqual(self.failed(proof.sitemap(self.client.get("/sitemap.xml").content.decode(), site)), [])

    def test_the_checks_catch_the_faults_they_are_for(self):
        from web import proof

        path = self.pokemon.get_absolute_url()
        html = self.page(path)
        llms = proof.llms_line(self.client.get("/llms.txt").content.decode(), path)
        # The FAQ block says one thing and the page another (the block comes first, in the head).
        differs = html.replace("RipRaptor sells nothing.", "RipRaptor sells everything.", 1)
        self.assertEqual(self.failed(proof.game(differs, llms)), ["FAQPage matches the page word for word"])
        # The counts disagree, as they did on 9 Nov 2026.
        self.assertEqual(self.failed(proof.game(html, llms.replace("5 in stock now", "9 in stock now"))),
                         ["counts agree: description, page, llms.txt"])
        self.assertEqual(self.failed(proof.game(html.replace('<meta name="description" content="5 sealed',
                                                             '<meta name="description" content="1,901 sealed'), llms)),
                         ["counts agree: description, page, llms.txt"])
        # Two headings, and a block that does not parse.
        self.assertIn("one h1", self.failed(proof.home(html.replace("</h1>", "</h1><h1>Again</h1>", 1))))
        broken = html.replace('<script type="application/ld+json"', '<script type="application/ld+json">{oops</script>'
                              '<script type="application/ld+json"', 1)
        self.assertIn("every JSON-LD block parses", self.failed(proof.common(broken)))
        # The page as it was before: a bare name for a heading, no answer, no questions.
        before = self.page("/")
        self.assertEqual(
            set(self.failed(proof.game(before))),
            {"heading says what the page compares", "cheapest of each kind in stock", "5 to 7 questions on the page",
             "FAQPage matches the page word for word", "ItemList of the products named", "counts agree: description, page"})

    def test_the_visitor_check_is_told_from_a_page(self):
        from web import proof

        self.assertTrue(proof.is_check_page('<body class="botcheck-page"><main class="botcheck" data-check>'))
        self.assertFalse(proof.is_check_page(self.page(self.pokemon.get_absolute_url())))

    def test_the_command_checks_this_machines_own_copy_as_every_crawler(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("visibility_proof", inside=True, site="http://testserver", stdout=out)
        report = out.getvalue()
        self.assertIn("All checks passed.", report)
        self.assertNotIn("FAIL", report)
        for crawler in ("Googlebot", "Bingbot", "GPTBot", "ClaudeBot", "PerplexityBot"):
            self.assertIn(f"ok   /games/pokemon/ [{crawler}] FAQPage matches the page word for word", report)
            self.assertIn(f"ok   {GUIDE} [{crawler}] Article with datePublished and dateModified", report)
        self.assertIn("ok   /games/pokemon/surging-sparks/ [ClaudeBot] ItemList of its products (6 products)", report)
        self.assertIn("ok   /llms.txt llms.txt names the guide", report)
        self.assertIn("ok   /sitemap.xml sitemap.xml lists the guide", report)
        # A page missing fails the run.
        Guide.objects.all().delete()
        cache.clear()
        switch()
        with self.assertRaises(SystemExit):
            call_command("visibility_proof", inside=True, site="http://testserver", stdout=StringIO())


class KeptCountsTests(AnswersCase):
    def test_llms_txt_reads_the_counts_the_game_page_last_built(self):
        # On the live site working out every game's numbers for llms.txt took seconds a game, so it reads the copy
        # each game's page keeps, and says what the page says.
        from django.db import connection
        from django.test import override_settings
        from django.test.utils import CaptureQueriesContext

        with override_settings(RIPRAPTOR_PAGE_CACHE_SECONDS=300):
            page = self.page(self.pokemon.get_absolute_url())
            self.assertIn("5 sealed Pokémon products in stock at 3 UK shops", page)
            # A price comes into stock after the page was built: llms.txt still agrees with the page being served.
            self.offer(self.sold_out, self.kestrel, "19.00")
            with CaptureQueriesContext(connection) as asked:
                llms = self.client.get("/llms.txt").content.decode()
            self.assertIn("6 sealed products listed, 5 in stock now at 3 UK shops", llms)
            self.assertFalse([query for query in asked if "catalogue_listing" in query["sql"]
                              and "COUNT" in query["sql"] and "product_type" in query["sql"]])
