"""The ⓘ cards: two plain sentences about a product type, a language or a badge, for people new to card games, in
the browser's own popover (web/templatetags/ripraptor.py, info). The owner: "parents ... to be able to understand
that they're buying the right thing, knowing that there's only 5 cards in a Korean booster pack", without making
the site look crowded."""

from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from catalogue.models import Product
from content.models import SiteContent
from content.registry import T
from web.templatetags.ripraptor import info_keys

from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


def render(source, request=None, **values):
    return Template("{% load ripraptor %}" + source).render(Context({"request": request, **values}))


class InfoTagTests(TestCase):
    def test_the_words_a_card_reads(self):
        self.assertEqual(info_keys("type", "booster_box"), ["info.type.booster_box", ""])
        self.assertEqual(info_keys("lang", "ko", "pokemon"), ["info.lang.ko", "info.lang.ko_pokemon"])
        self.assertEqual(info_keys("lang", "zh-hans")[0], "info.lang.zh")
        self.assertEqual(info_keys("lang", "de")[0], "info.lang.other")
        self.assertEqual(info_keys("lang", ""), [])   # English needs no card
        self.assertEqual(info_keys("badge", "low_30", "magic-the-gathering")[1], "info.badge.low_30_magic_the_gathering")

    def test_one_card_per_page_however_many_buttons_open_it(self):
        request = RequestFactory().get("/")
        html = render('{% info "lang" "ko" "Korean" game="pokemon" %}{% info "lang" "ko" "Korean" game="pokemon" %}',
                      request)
        self.assertEqual(html.count('class="info"'), 2)
        self.assertEqual(html.count('class="info-card"'), 1)
        self.assertIn('aria-label="What is Korean?"', html)
        self.assertIn('<span class="info-card__title" id="', html)
        self.assertIn("Made for Korea, with the card text in Korean.", html)
        self.assertIn("A Korean Pokémon booster pack usually has 5 cards. An English one has 10.", html)
        [target] = set(__import__("re").findall(r'popovertarget="([^"]+)"', html))
        self.assertIn(f'id="{target}" popover role="dialog"', html)
        self.assertIn('popovertargetaction="hide">Close</button>', html)
        # The same address gives the same page; another (a list fetched into the page by script) never repeats an id.
        again = render('{% info "lang" "ko" "Korean" %}', RequestFactory().get("/"))
        self.assertIn(target, again)
        other = render('{% info "lang" "ko" "Korean" %}', RequestFactory().get("/?tab=out-of-stock&pane=1"))
        self.assertNotIn(target, other)

    def test_the_pokemon_line_only_for_pokemon_and_the_language_named_for_others(self):
        html = render('{% info "lang" "ko" "Korean" game="lorcana" %}', RequestFactory().get("/"))
        self.assertNotIn("5 cards", html)
        html = render('{% info "lang" "de" "German" %}', RequestFactory().get("/"))
        self.assertIn("The card text is in German.", html)

    def test_no_button_without_words(self):
        self.assertEqual(render('{% info "type" "other" "Other" %}', RequestFactory().get("/")), "")
        # The owner blanks a card in Admin: its button goes too.
        SiteContent.objects.update_or_create(key="info.type.tin", defaults={
            "label": "Info: tin", "section": "info", "kind": T, "content": ""})
        from content.service import RequestStore

        html = render('{% info "type" "tin" "Tin" %}', RequestFactory().get("/"), site_content=RequestStore())
        self.assertEqual(html, "")

    def test_a_badge_card_takes_the_badges_own_words_as_its_title(self):
        html = render('{% info "badge" "low_30" %}', RequestFactory().get("/"))
        self.assertIn('aria-label="What is Lowest in 30 days?"', html)
        self.assertIn("lowest we have recorded for it in the last 30 days", html)

    def test_it_needs_no_script_and_shows_no_dead_button_where_popovers_are_missing(self):
        with open(finders.find("css/site.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertIn(".info { display: none; }\n@supports selector(:popover-open) {", css)
        self.assertIn(".info-card { display: none; }\n.info-card:popover-open {", css)
        self.assertIn(".card .info { position: relative; z-index: 1; }", css)


class InfoOnPagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.game = make_game()
        self.set = make_set(self.game)
        self.shop = make_retailer("Harbour Games")
        self.box = make_product(self.set, name="Prismatic Evolutions Korean Booster Box", slug="pre-box-ko",
                                product_type=Product.Type.BOOSTER_BOX)
        Product.objects.filter(pk=self.box.pk).update(language="ko")
        make_listing(self.box, self.shop, price="90.00")
        self.etb = make_product(self.set, slug="pre-etb")
        make_listing(self.etb, self.shop, price="50.00")

    def test_the_product_page_explains_the_type_and_the_language(self):
        html = self.client.get(self.box.get_absolute_url()).content.decode()
        facts = html[html.index('<p class="product-facts">'):html.index("</p>", html.index('<p class="product-facts">'))]
        self.assertIn('Booster box<button type="button" class="info"', facts)
        self.assertIn('aria-label="What is Korean?"', facts)
        self.assertIn("usually 24 or 36 depending on the game", facts)
        self.assertIn("A Korean Pokémon booster pack usually has 5 cards.", facts)
        # English: the type explained, nothing about the language.
        html = self.client.get(self.etb.get_absolute_url()).content.decode()
        facts = html[html.index('<p class="product-facts">'):html.index("</p>", html.index('<p class="product-facts">'))]
        self.assertIn("A Pokémon box with booster packs", facts)
        self.assertNotIn("What is English?", facts)

    def test_a_card_in_a_list_explains_its_language(self):
        html = self.client.get(self.set.get_absolute_url()).content.decode()
        self.assertIn('<span class="card__lang">Korean<button type="button" class="info"', html)
        import re

        self.assertEqual(len(re.findall(r'class="info-card" id="([^"]+)"', html)), 1)
