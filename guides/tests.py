"""Guides and the Fair Price Checker (marketing's build spec, 27 Oct 2026)."""

import json
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import DailyPageView, Listing, Retailer
from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set
from catalogue.tests_fair_price import record
from inbox.models import Conversation

from . import checker, state, text
from .models import Guide, PriceCheck, Section, Vote

BROWSER = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"
SOURCE = "Citizens Advice: changing your mind | https://www.citizensadvice.org.uk/consumer/ | 27 Oct 2026"


def make_guide(slug="parents", published=True, **kwargs):
    today = timezone.localdate()
    kwargs.setdefault("title", "A parent's guide to buying trading cards")
    kwargs.setdefault("one_line_answer", "What to buy, what it should cost and how to pay safely.")
    kwargs.setdefault("last_reviewed", today)
    kwargs.setdefault("next_review", today + timedelta(days=60))
    return Guide.objects.create(slug=slug, is_published=published,
                                published_at=timezone.now() if published else None, **kwargs)


def add_section(guide, question="Is this shop real?", **kwargs):
    kwargs.setdefault("short_answer", "Look for a UK address and card payments. If in doubt, buy elsewhere.")
    kwargs.setdefault("sources", SOURCE)
    kwargs.setdefault("position", guide.sections.count())
    return Section.objects.create(guide=guide, question=question, **kwargs)


def visible(html):
    return re.sub(r"<[^>]*>", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html))


class GuideCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.staff = get_user_model().objects.create_user("marketing", password="pw", is_staff=True,
                                                          is_superuser=True)

    def as_staff(self):
        self.client.force_login(self.staff)


class GateTests(GuideCase):
    def test_nothing_is_public_until_a_guide_is_published(self):
        draft = make_guide(published=False)
        add_section(draft)
        for url in (reverse("web:guides"), reverse("web:fair_price"), draft.get_absolute_url()):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 404)
        home = self.client.get(reverse("web:home")).content.decode()
        self.assertNotIn('href="/guides/"', home)
        # Staff see every page as it will look, the draft marked and saying what still stops it.
        self.as_staff()
        self.assertEqual(self.client.get(reverse("web:guides")).status_code, 200)
        page = self.client.get(draft.get_absolute_url())
        self.assertContains(page, "Draft: only staff can see this page.")
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")

    def test_publishing_opens_the_pages_and_the_links(self):
        guide = make_guide()
        add_section(guide)
        self.assertTrue(state.current()["live"])   # kept by the save, so pages read it from the cache
        for url in (reverse("web:guides"), reverse("web:fair_price"), guide.get_absolute_url()):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 200)
        home = self.client.get(reverse("web:home")).content.decode()
        nav = home[home.index('aria-label="Main"'):home.index("</nav>", home.index('aria-label="Main"'))]
        self.assertLess(nav.index("/drops/"), nav.index('href="/guides/"'))   # after Drops, as asked
        self.assertLess(nav.index('href="/guides/"'), nav.index("/games/"))
        footer = home[home.index('aria-label="Footer"'):]
        self.assertIn('href="/guides/"', footer)
        guide.is_published = False
        guide.save()
        self.assertFalse(state.current()["live"])
        self.assertEqual(self.client.get(reverse("web:guides")).status_code, 404)

    def test_an_emptied_cache_hides_the_link_until_warm_pages_puts_it_back(self):
        from io import StringIO

        from django.core.management import call_command

        make_guide()
        cache.clear()
        self.assertNotIn('href="/guides/"', self.client.get(reverse("web:about")).content.decode())
        call_command("warm_pages", "--due", stdout=StringIO())
        self.assertIn('href="/guides/"', self.client.get(reverse("web:about")).content.decode())

    def test_the_addresses_kept_for_other_pages(self):
        from django.core.exceptions import ValidationError

        guide = Guide(slug="fair-price", title="x", one_line_answer="y")
        with self.assertRaises(ValidationError):
            guide.full_clean()


class TextTests(TestCase):
    def test_the_house_style_and_the_standards_claims(self):
        cases = {
            "Buy it now — quickly": "em dash", "Buy it now!": "exclamation", "<b>Buy</b>": "HTML",
            "Unlock savings": "phrases the site never uses", "Delivery is guaranteed": "normally true",
            "We find the best price": "cheapest at every UK shop we track",
            "Every shop in the UK sells it": "every UK shop we track", "Learn more about packs": "phrases",
        }
        for words, expected in cases.items():
            with self.subTest(words):
                found = text.style_problems(words, "Detail")
                self.assertTrue(found and all(problem.startswith("Detail: ") for problem in found))
                self.assertIn(expected.lower(), " ".join(found).lower())
        self.assertEqual(text.style_problems("A fair price at every UK shop we track.", "Detail"), [])

    def test_etb_is_never_used_on_its_own(self):
        self.assertIsNotNone(text.etb_problem([("Detail", "An ETB has nine packs.")]))
        self.assertIsNone(text.etb_problem([("Title", "What is an Elite Trainer Box?"), ("Detail", "An ETB has nine packs.")]))
        self.assertIsNone(text.etb_problem([("Detail", "Sets, betting and fetb words are fine.")]))

    def test_links_go_to_our_pages_or_https_pages_that_are_not_shops(self):
        body = ("[ours](/products/x/) [advice](https://www.citizensadvice.org.uk/) [plain](http://example.com/) "
                "[shop](https://www.harbour-games.example/p/x) [market](https://www.ebay.co.uk/itm/1)")
        found = text.link_problems(body, "Detail", {"harbour-games.example"})
        self.assertEqual(len(found), 3)
        self.assertIn("http://example.com/ is not an https address", found[0])
        self.assertIn("www.harbour-games.example is a shop or a marketplace", found[1])
        self.assertIn("www.ebay.co.uk is a shop or a marketplace", found[2])

    def test_sources_are_title_address_and_date(self):
        found, wrong = text.sources_in(SOURCE + "\nWhich? | https://www.which.co.uk/ | 2026-10-27\nno bars here\n"
                                       "Blog | http://blog.example/ | 27 Oct 2026\nDated | https://x.example/ | soon")
        self.assertEqual([(s.title, s.read.isoformat()) for s in found],
                         [("Citizens Advice: changing your mind", "2026-10-27"), ("Which?", "2026-10-27")])
        self.assertEqual(len(wrong), 3)

    def test_a_body_is_paragraphs_lists_and_links_and_nothing_else(self):
        html = text.body_html("First line\nsecond line\n\n- one\n- two\n\n1. first\n2. second\n\n"
                              "Read [Citizens Advice](https://www.citizensadvice.org.uk/) or [our page](/about/).\n\n"
                              "<script>alert(1)</script>")
        self.assertIn("<p>First line<br>second line</p>", html)
        self.assertIn("<ul><li>one</li><li>two</li></ul>", html)
        self.assertIn("<ol><li>first</li><li>second</li></ol>", html)
        self.assertIn('<a href="https://www.citizensadvice.org.uk/" rel="noopener">Citizens Advice</a>', html)
        self.assertIn('<a href="/about/">our page</a>', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_jump_link_ids_are_unique(self):
        self.assertEqual(text.anchors(["Is this shop real?", "Is this shop real?", "?"]),
                         ["is-this-shop-real", "is-this-shop-real-2", "question-3"])

    def test_what_stops_publishing(self):
        today = timezone.localdate()
        guide = Guide(title="Scams", one_line_answer="How to avoid them.", last_reviewed=today,
                      next_review=today + timedelta(days=text.REVIEW_DAYS + 1), door="sold_out")
        facts = Section(question="How do I pay safely?", short_answer="Pay by card.", states_facts=True)
        opinion = Section(question="Which is more fun?", short_answer="Both.", states_facts=False)
        found = text.problems(guide, [facts, opinion], today=today)
        self.assertEqual(found["style"], [])
        self.assertEqual(found["publish"], [
            "Question 1 (How do I pay safely?) states facts but has no source.",
            "Write the link words for where this guide is linked from.",
            f"The next review is at most {text.REVIEW_DAYS} days after the last check.",
        ])
        facts.sources, guide.door_line, guide.next_review = SOURCE, "Read how to avoid scams", today + timedelta(days=90)
        self.assertEqual(text.problems(guide, [facts, opinion], today=today), {"style": [], "publish": []})


class AdminTests(GuideCase):
    def form(self, **changes):
        today = timezone.localdate()
        data = {
            "title": "How to avoid scams and scalpers", "slug": "scams-and-scalpers",
            "one_line_answer": "Fakes, reseals and fake shops, and what to do if it goes wrong.",
            "audience": "everyone", "is_published": "on", "position": "0", "door": "sold_out",
            "door_line": "Sold out? Here is how to avoid scalpers.",
            "last_reviewed": today.isoformat(), "next_review": (today + timedelta(days=80)).isoformat(),
            "sections-TOTAL_FORMS": "1", "sections-INITIAL_FORMS": "0", "sections-MIN_NUM_FORMS": "0",
            "sections-MAX_NUM_FORMS": "1000",
            "sections-0-position": "0", "sections-0-question": "How do I pay safely?",
            "sections-0-short_answer": "Pay by card or PayPal Goods and Services. Never by bank transfer to a stranger.",
            "sections-0-body": "", "sections-0-states_facts": "on", "sections-0-sources": SOURCE,
            "sections-0-green_flags": "", "sections-0-red_flags": "Bank transfer only", "sections-0-box_type": "",
        }
        data.update(changes)
        return data

    def test_a_guide_is_published_only_when_every_fact_has_a_source(self):
        self.as_staff()
        url = reverse("admin:guides_guide_add")
        response = self.client.post(url, self.form(**{"sections-0-sources": ""}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "states facts but has no source")
        self.assertFalse(Guide.objects.exists())
        # As a draft it saves, and says what still stops it.
        response = self.client.post(url, self.form(**{"sections-0-sources": "", "is_published": ""}))
        self.assertEqual(response.status_code, 302)
        guide = Guide.objects.get()
        page = self.client.get(reverse("admin:guides_guide_change", args=[guide.pk]))
        self.assertContains(page, "How do I pay safely?) states facts but has no source.")
        self.assertIsNone(guide.published_at)

    def test_house_style_stops_any_save(self):
        self.as_staff()
        response = self.client.post(reverse("admin:guides_guide_add"),
                                    self.form(**{"sections-0-short_answer": "Never pay by bank transfer!",
                                                 "is_published": "", "title": "Scams — a guide"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Question 1, short answer: remove the exclamation mark.")
        self.assertContains(response, "Title: remove the em dash.")
        self.assertFalse(Guide.objects.exists())

    def test_a_good_guide_publishes_and_links_from_its_door(self):
        self.as_staff()
        response = self.client.post(reverse("admin:guides_guide_add"), self.form())
        self.assertEqual(response.status_code, 302)
        guide = Guide.objects.get()
        self.assertIsNotNone(guide.published_at)
        self.assertEqual(state.door("sold_out"), {"url": "/guides/scams-and-scalpers/",
                                                  "line": "Sold out? Here is how to avoid scalpers.",
                                                  "title": "How to avoid scams and scalpers"})
        self.assertContains(self.client.get(reverse("admin:guides_guide_change", args=[guide.pk])), "Nothing. It is live.")

    def test_the_admin_home_says_when_a_review_is_due(self):
        self.as_staff()
        make_guide(last_reviewed=timezone.localdate() - timedelta(days=100),
                   next_review=timezone.localdate() - timedelta(days=8))
        self.assertContains(self.client.get(reverse("admin:index")), "1 guide due for review")


class PageCase(GuideCase):
    def setUp(self):
        super().setUp()
        self.pokemon = make_game()
        self.etb = make_product(make_set(self.pokemon), name="Prismatic Evolutions Elite Trainer Box")
        self.harbour = make_retailer("Harbour Games")
        self.quay = make_retailer("Quay Cards")
        self.ebay = make_retailer("eBay", source_type=Retailer.Source.EBAY)
        make_listing(self.etb, self.harbour, price="52.00", delivery="2.99")
        make_listing(self.etb, self.quay, price="49.99", delivery="3.99")
        make_listing(self.etb, self.ebay, price="40.00")     # a marketplace: never the guides' cheapest
        self.guide = make_guide(door="buying", door_line="New to this? Read the parent's guide.",
                                checklist="Check the language\nPay by card")
        self.what = add_section(
            self.guide, "What is the difference between a pack and an Elite Trainer Box?",
            body="An Elite Trainer Box holds packs and extras.\n\n- Sleeves\n- Dice", box_type="elite_trainer_box",
            green_flags="A UK address\nCard payments", red_flags="Bank transfer only", show_checker=True)
        self.what.products.add(self.etb)
        add_section(self.guide, "Which is more fun?", short_answer="Both.", states_facts=False, sources="")


class GuidePageTests(PageCase):
    def page(self):
        response = self.client.get(self.guide.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_each_question_with_its_short_answer_first(self):
        html = self.page()
        anchor = text.anchors([self.what.question])[0]
        self.assertIn(f'<a href="#{anchor}">{self.what.question}</a>', html)
        section = html[html.index(f'id="{anchor}"'):html.index('id="which-is-more-fun"')]
        self.assertLess(section.index("Short answer"), section.index("An Elite Trainer Box holds packs"))
        self.assertIn("<ul><li>Sleeves</li><li>Dice</li></ul>", section)
        self.assertIn("Good signs", section)
        self.assertIn("Warning signs", section)
        self.assertIn("Bank transfer only", section)
        # The box card is the product type's own explainer, the one product pages show.
        self.assertIn("What is in the box: Elite Trainer Box", section)
        self.assertIn("A Pokémon box with booster packs", section)
        self.assertIn('action="/guides/fair-price/"', section)
        self.assertIn('Source: <a href="https://www.citizensadvice.org.uk/consumer/" rel="noopener">', section)
        self.assertIn("min read", html)
        self.assertIn(f"Checked by RipRaptor on {timezone.localdate():%-d %B %Y}.", html)

    def test_live_price_cards_name_no_shop_and_leave_out_marketplaces(self):
        html = self.page()
        card = html[html.index('class="card guide-card"'):html.index("</li>", html.index('class="card guide-card"'))]
        self.assertIn("Cheapest delivered today", card)
        self.assertIn("53.98", card)       # Quay Cards, delivered; not eBay's £40
        self.assertNotIn("40.00", card)
        self.assertIn("In stock at 2 UK shops we track", card)
        self.assertIn(f'href="{self.etb.get_absolute_url()}"', card)
        # Watch the price: the price alert, so a parent reading early hears when it drops.
        self.assertIn(f'href="{self.etb.get_absolute_url()}#track" data-compare="guide_card">Watch the price', card)
        for name in ("Harbour Games", "Quay Cards", "eBay"):
            self.assertNotIn(name, card)
        self.assertNotIn('href="/go/', html)

    def test_sources_commission_share_and_feedback_close_the_guide(self):
        html = self.page()
        foot = html[html.index('class="guide-foot"'):]
        self.assertIn("Citizens Advice: changing your mind</a>, read 27 Oct 2026", foot)
        self.assertIn("We may earn a commission if you buy through a shop link. The cheapest price is shown first, "
                      "whoever pays us.", foot)
        self.assertIn("Shop prices change often, so check again before you buy.", foot)
        self.assertIn("https://wa.me/?text=", foot)
        self.assertIn("Printable checklist", foot)
        self.assertIn("Was this useful?", foot)
        self.assertIn('name="useful" value="yes"', foot)
        self.assertIn("Spotted a mistake?", foot)

    def test_search_and_sharing(self):
        html = self.page()
        self.assertIn(f'<link rel="canonical" href="http://testserver{self.guide.get_absolute_url()}">', html)
        blocks = json.loads(html.split('<script type="application/ld+json">')[1].split("</script>")[0])
        self.assertEqual([block["@type"] for block in blocks], ["Article", "BreadcrumbList"])
        self.assertEqual(blocks[0]["headline"], self.guide.title)
        self.assertEqual(blocks[0]["dateModified"], timezone.localdate().isoformat())
        self.assertEqual(blocks[1]["itemListElement"][0]["name"], "Guides")
        self.assertIn("<title>A parent&#x27;s guide to buying trading cards | RipRaptor</title>", html)
        self.guide.search_title = "Buying Pokémon cards for Christmas: a UK parent's guide"
        self.guide.save()
        self.assertIn("<title>Buying Pokémon cards for Christmas: a UK parent&#x27;s guide | RipRaptor</title>", self.page())

    def test_house_style_on_every_guides_page(self):
        from web.tests import CopyStyleTests

        urls = [reverse("web:guides"), self.guide.get_absolute_url(), reverse("web:guide_checklist", args=["parents"]),
                reverse("web:fair_price"), reverse("web:fair_price") + "?q=prismatic&price=80",
                reverse("web:fair_price") + f"?product={self.etb.slug}&price=120"]
        for url in urls:
            html = self.client.get(url).content.decode()
            with self.subTest(url):
                self.assertNotIn("!", visible(html))
                for phrase in CopyStyleTests.BANNED:
                    self.assertNotIn(phrase, html.lower())

    def test_queries_stay_the_same_however_many_cards(self):
        self.page()
        with CaptureQueriesContext(connection) as few:
            self.page()
        for n in range(4):
            product = make_product(self.etb.product_set, name=f"Prismatic Evolutions Booster Bundle {n}",
                                   product_type="bundle")
            make_listing(product, self.harbour, price="30.00")
            self.what.products.add(product)
        with CaptureQueriesContext(connection) as many:
            html = self.page()
        self.assertEqual(html.count('class="card guide-card"'), 5)
        self.assertEqual(len(few), len(many))
        self.assertLessEqual(len(many), 16)

    def test_staff_are_told_when_the_review_is_due(self):
        self.guide.last_reviewed = timezone.localdate() - timedelta(days=100)
        self.guide.next_review = timezone.localdate() - timedelta(days=8)
        self.guide.save()
        self.assertNotIn("The fact check was due", self.page())
        self.as_staff()
        self.assertIn("The fact check was due on", self.page())

    def test_the_checklist_prints_on_its_own(self):
        url = reverse("web:guide_checklist", args=["parents"])
        html = self.client.get(url).content.decode()
        self.assertIn("Check the language", html)
        self.assertIn('class="checklist__box"', html)
        self.assertIn("data-print hidden", html)   # the Print button needs script; the browser prints without it
        self.guide.checklist = ""
        self.guide.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_views_are_counted_by_guide(self):
        self.client.get(self.guide.get_absolute_url(), HTTP_USER_AGENT=BROWSER)
        self.client.get(reverse("web:guide_checklist", args=["parents"]), HTTP_USER_AGENT=BROWSER)
        self.client.get(reverse("web:guides"), HTTP_USER_AGENT=BROWSER)
        self.client.get(reverse("web:fair_price"), HTTP_USER_AGENT=BROWSER)
        self.assertEqual(set(DailyPageView.objects.filter(kind="guide").values_list("key", flat=True)),
                         {"parents", "checklist/parents", "", "fair-price"})


class DoorTests(PageCase):
    def test_a_line_under_the_price_and_on_a_sold_out_page(self):
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        self.assertIn('<p class="buybox__guide"><a href="/guides/parents/">New to this? Read the parent&#x27;s guide.</a></p>', html)
        scams = make_guide("scams-and-scalpers", title="How to avoid scams and scalpers", door="sold_out",
                           door_line="Sold out? Here is how to avoid scalpers.")
        add_section(scams)
        Listing.objects.filter(product=self.etb).update(availability=Listing.Availability.OUT_OF_STOCK)
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        self.assertIn('href="/guides/scams-and-scalpers/">Sold out? Here is how to avoid scalpers.</a>', html)
        self.assertNotIn("Read the parent", html)

    def test_and_from_the_high_price_check(self):
        scams = make_guide("scams-and-scalpers", title="How to avoid scams and scalpers", door="sold_out",
                           door_line="Sold out? Here is how to avoid scalpers.")
        add_section(scams)
        Listing.objects.filter(retailer=self.ebay).delete()
        record(self.etb, ["40.00"] * 20, timezone.localdate())   # £53.98 today is more than 15% over: High price
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        sheet = html[html.index('id="fair-sheet"'):html.index("</dialog>", html.index('id="fair-sheet"'))]
        # The guide's title: the product is not sold out, so its "Sold out?" words would be wrong here.
        self.assertIn('href="/guides/scams-and-scalpers/">How to avoid scams and scalpers</a>', sheet)

    def test_none_before_a_guide_is_published(self):
        Guide.objects.update(is_published=False)
        state.remember()
        self.assertNotIn("buybox__guide", self.client.get(self.etb.get_absolute_url()).content.decode())

    def test_the_sitemap_lists_published_guides(self):
        xml = self.client.get("/sitemap.xml").content.decode()
        for path in ("/guides/", "/guides/fair-price/", "/guides/parents/"):
            self.assertIn(f"http://testserver{path}</loc>", xml)
        self.assertNotIn("/guides/parents/checklist/", xml)
        Guide.objects.update(is_published=False)
        self.assertNotIn("/guides/", self.client.get("/sitemap.xml").content.decode())

    def test_the_checker_is_never_rendered_ahead_of_a_tap(self):
        html = self.client.get(reverse("web:home")).content.decode()
        rules = json.loads(html.split('<script type="speculationrules">')[1].split("</script>")[0])
        self.assertIn("/guides/fair-price/*", rules["prerender"][0]["where"]["and"][1]["not"]["href_matches"])


class CheckerTests(PageCase):
    def check(self, price):
        return checker.check(self.etb, Decimal(price))

    def test_prices_as_people_type_them(self):
        for typed, expected in (("£89", "89.00"), ("89.99", "89.99"), ("1,299", "1299.00"), ("89,99", "89.99"),
                                (" £ 49.5 ", "49.50"), ("49.99 GBP", "49.99")):
            with self.subTest(typed):
                self.assertEqual(checker.read_price(typed), Decimal(expected))
        for typed in ("", "free", "0", "-5", "100001", "£"):
            with self.subTest(typed):
                self.assertIsNone(checker.read_price(typed))

    def test_today_at_every_uk_shop_we_track(self):
        found = self.check("60.00")
        self.assertEqual((found.cheapest, found.in_stock, found.shops), (Decimal("53.98"), 2, 2))
        self.assertEqual(found.more, Decimal("6.02"))
        self.assertFalse(found.at_or_under)
        self.assertTrue(self.check("53.98").at_or_under)
        # A doubtful price may not be this product: never the cheapest.
        Listing.objects.filter(retailer=self.quay).update(sanity=Listing.Sanity.DOUBTFUL)
        self.assertEqual(self.check("60.00").cheapest, Decimal("54.99"))

    def test_the_verdict_is_the_product_pages_own(self):
        from catalogue import fair_price

        self.assertIsNone(self.check("60.00").verdict)   # nothing to measure by yet
        self.assertEqual(self.check("60.00").key, "unknown")
        record(self.etb, ["50.00"] * 20, timezone.localdate())
        for price, key in (("45.00", "lowest"), ("50.00", "fair"), ("56.00", "above"), ("70.00", "high"),
                           ("100.00", "scalper"), ("130.00", "scalper")):
            with self.subTest(price):
                found = self.check(price)
                self.assertEqual(found.key, key)
                self.assertEqual(found.verdict.band, fair_price.verdict(self.etb, Decimal(price), shops=2).band)
        found = self.check("130.00")
        self.assertEqual((found.usual, found.usual_from, found.times), (Decimal("50.00"), "record", Decimal("2.6")))

    def test_sold_out_at_every_uk_shop_we_track(self):
        Listing.objects.filter(retailer__in=[self.harbour, self.quay]).update(availability=Listing.Availability.OUT_OF_STOCK)
        found = self.check("60.00")
        self.assertTrue(found.sold_out)
        self.assertEqual(found.in_stock, 0)

    def test_without_javascript_words_then_a_choice_then_the_verdict(self):
        record(self.etb, ["50.00"] * 20, timezone.localdate())
        url = reverse("web:fair_price")
        html = self.client.get(url + "?q=prismatic+elite&price=120").content.decode()
        self.assertIn("Which one is it?", html)
        choice = f'href="?product={self.etb.slug}&amp;price=120#verdict"'
        self.assertIn(choice, html)
        response = self.client.get(url + f"?product={self.etb.slug}&price=120", HTTP_USER_AGENT=BROWSER)
        html = response.content.decode()
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")
        self.assertIn("You were offered £120.00.", html)
        self.assertIn("Scalper price", html)
        self.assertIn("That is 2.4 times the usual price.", html)
        self.assertIn('<span class="fair__rex"></span>', html)
        self.assertIn("A UK shop we track sells it for £53.98 today, delivery included. That is £66.02 less.", html)
        self.assertIn("Usual price: £50.00, the average delivered price at UK shops we track over 20 days.", html)
        self.assertIn("Scalpers buy up new stock and resell it at prices like this.", html)
        self.assertIn("Do not pay this. Watch the price and we will email you if a UK shop sells it for less.", html)
        self.assertIn(f'href="{self.etb.get_absolute_url()}#track"', html)
        self.assertNotIn("ebay", html.lower().split("<main")[1].split("</main>")[0])   # never sends anyone to a marketplace
        self.assertEqual(PriceCheck.objects.get().verdict, "scalper")

    def test_a_good_price_and_a_wrong_one(self):
        record(self.etb, ["50.00"] * 20, timezone.localdate())
        url = reverse("web:fair_price") + f"?product={self.etb.slug}"
        html = self.client.get(url + "&price=45").content.decode()
        self.assertIn("Lowest yet", html)
        self.assertIn("That is at or under £53.98, the cheapest price today at any UK shop we track, delivery included.", html)
        self.assertIn("A good price. Pay by card or PayPal Goods and Services, so you can get your money back", html)
        self.assertIn(f'href="{self.etb.get_absolute_url()}#track" data-compare="checker">Watch the price', html)
        html = self.client.get(url + "&price=lots").content.decode()
        self.assertIn("Type the price in pounds, for example 49.99.", html)
        self.assertNotIn("You were offered", html)
        html = self.client.get(reverse("web:fair_price") + "?q=zzzz").content.decode()
        self.assertIn("No product matches those words.", html)

    def test_each_check_is_counted_once_by_product_and_verdict_only(self):
        url = reverse("web:fair_price") + f"?product={self.etb.slug}&price=60"
        self.client.get(url, HTTP_USER_AGENT=BROWSER)
        self.client.get(url, HTTP_USER_AGENT=BROWSER)
        self.client.get(url, HTTP_USER_AGENT=BROWSER, HTTP_SEC_PURPOSE="prefetch;prerender")
        self.client.get(url, HTTP_USER_AGENT="Googlebot/2.1")
        row = PriceCheck.objects.get()
        self.assertEqual((row.product, row.verdict, row.hits), (self.etb, "unknown", 2))


class FeedbackTests(PageCase):
    def post(self, **data):
        return self.client.post(reverse("web:guide_feedback", args=["parents"]), data, HTTP_USER_AGENT=BROWSER)

    def test_yes_and_no_are_counted_once_a_day_per_connection(self):
        response = self.post(useful="yes")
        self.assertRedirects(response, "/guides/parents/?feedback=thanks#feedback", fetch_redirect_response=False)
        self.post(useful="yes")
        self.post(useful="no", REMOTE_ADDR="10.0.0.2")
        self.client.post(reverse("web:guide_feedback", args=["parents"]), {"useful": "no"}, REMOTE_ADDR="10.0.0.3",
                         HTTP_USER_AGENT=BROWSER)
        self.assertEqual(dict(Vote.objects.values_list("useful", "hits")), {True: 1, False: 1})
        self.assertContains(self.client.get("/guides/parents/?feedback=thanks"), "Thank you. We read every note.")

    def test_a_note_is_filed_in_messages_with_no_name_or_email(self):
        self.post(useful="no", note="Nothing on Japanese cards")
        thread = Conversation.objects.get()
        self.assertEqual((thread.name, thread.email), ("Guide feedback", ""))
        body = thread.messages.get().body
        self.assertIn("Guide feedback: A parent's guide to buying trading cards (not useful)", body)
        self.assertIn("/guides/parents/", body)
        self.assertIn("Note: Nothing on Japanese cards", body)

    def test_robots_and_drafts_are_turned_away(self):
        self.post(useful="yes", website="spam.example")
        self.post(useful="maybe")
        self.assertFalse(Vote.objects.exists())
        draft = make_guide("draft", published=False)
        self.assertEqual(self.client.post(reverse("web:guide_feedback", args=[draft.slug]), {"useful": "yes"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("web:guide_feedback", args=["parents"])).status_code, 405)


class DashboardTests(PageCase):
    def test_the_owner_sees_how_the_guides_are_doing(self):
        self.as_staff()
        self.client.get(self.guide.get_absolute_url(), HTTP_USER_AGENT=BROWSER)
        note = reverse("web:note_api")
        self.client.get(note + "?what=compare&where=guide_card&page=guide", HTTP_USER_AGENT=BROWSER)
        self.client.get(note + "?what=compare&where=checker&page=checker", HTTP_USER_AGENT=BROWSER)
        self.client.get(reverse("web:fair_price") + f"?product={self.etb.slug}&price=60", HTTP_USER_AGENT=BROWSER)
        Vote.objects.create(guide=self.guide, date=timezone.localdate(), useful=True, hits=3)
        Vote.objects.create(guide=self.guide, date=timezone.localdate(), useful=False, hits=1)
        response = self.client.get(reverse("web:dashboard") + "?fresh=1")
        part = response.context["guides"]
        self.assertEqual((part["views"], part["taps"], part["checks"], part["yes"], part["no"]), (1, 2, 1, 3, 1))
        self.assertEqual(part["mix"][0]["label"], "not enough prices")
        self.assertContains(response, "Taps to a product page")
        self.assertContains(response, "Useful: 3 yes, 1 no.")

    def test_no_part_before_a_guide_is_published(self):
        self.as_staff()
        Guide.objects.update(is_published=False)
        response = self.client.get(reverse("web:dashboard") + "?fresh=1")
        self.assertIsNone(response.context["guides"])
        self.assertNotContains(response, 'id="guides"')


class FollowUpTests(PageCase):
    def test_a_sold_out_card_watches_for_the_restock(self):
        Listing.objects.filter(product=self.etb).update(availability=Listing.Availability.OUT_OF_STOCK)
        html = self.client.get(self.guide.get_absolute_url()).content.decode()
        self.assertIn("Sold out at every UK shop we track", html)
        self.assertIn(f'href="{self.etb.get_absolute_url()}#alert" data-compare="guide_card">Watch the price', html)

    def test_every_checker_result_offers_to_watch_the_price(self):
        record(self.etb, ["50.00"] * 20, timezone.localdate())
        url = reverse("web:fair_price") + f"?product={self.etb.slug}&price="
        for price in ("45", "50", "56", "70", "120"):
            with self.subTest(price):
                self.assertIn(">Watch the price<", self.client.get(url + price).content.decode())
        Listing.objects.filter(product=self.etb).update(availability=Listing.Availability.OUT_OF_STOCK)
        self.assertIn(">Watch the price<", self.client.get(url + "60").content.decode())   # sold out

    def test_phones_get_a_guides_link_on_the_home_page(self):
        html = self.client.get(reverse("web:home")).content.decode()
        self.assertIn('<p class="hero__guides"><a href="/guides/">New to trading cards? Read our guides.</a></p>', html)
        Guide.objects.all().delete()
        self.assertNotIn("hero__guides", self.client.get(reverse("web:home")).content.decode())

    def test_ai_assistants_are_told_about_the_guides(self):
        text_ = self.client.get("/llms.txt").content.decode()
        self.assertIn("## Guides", text_)
        self.assertIn("- [A parent's guide to buying trading cards](http://testserver/guides/parents/): What to buy", text_)
        self.assertIn("- [Fair Price Checker](http://testserver/guides/fair-price/): Been offered a price?", text_)
        Guide.objects.update(is_published=False)
        self.assertNotIn("## Guides", self.client.get("/llms.txt").content.decode())

    def test_admin_hints_ask_for_the_reason(self):
        self.assertIn("why", Section._meta.get_field("short_answer").help_text)
        self.assertIn("under 50 characters", Guide._meta.get_field("search_title").help_text)
