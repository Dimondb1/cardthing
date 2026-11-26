"""The desktop browsing layout (the owner's brief of 26 Nov 2026): a wider page on a wide screen, three steps at the top
of every list (game, set or collection, product type) built from the shared parts, and products as cards on a desktop
screen with a Grid or List switch. A phone sees none of it: every desktop part is hidden below 64rem (site.css)."""

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalogue import images
from catalogue.models import Product, SiteLook
from catalogue.testing import make_game, make_listing, make_product, make_set

from . import look
from .tests import PageTestCase

WEB = Path(__file__).resolve().parent


class DesktopCase(PageTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        SiteLook.objects.update_or_create(pk=1, defaults={"compact": True, "feed_home": False})
        look.remember()
        # A second kind in stock, so the types show as tiles (one kind alone has no choice to offer).
        box = make_product(self.pre, name="Prismatic Evolutions Booster Box", product_type=Product.Type.BOOSTER_BOX)
        make_listing(box, self.harbour, price="150.00")
        # The types' tiles are pictured by a product of each kind, so only kinds with a picture have one.
        Product.objects.filter(game=self.game).update(image_url="https://cdn.example/box.png")

    def html(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class StepTests(DesktopCase):
    def test_every_list_leads_with_the_three_steps(self):
        type_page = self.game.get_absolute_url() + "elite-trainer-boxes/"
        for url in (self.game.get_absolute_url(), self.pre.get_absolute_url(), type_page):
            html = self.html(url)
            with self.subTest(url=url):
                self.assertIn("1. Choose a game", html)
                self.assertIn("2. Pick a set or collection", html)
                self.assertIn("3. Choose a product type", html)
                self.assertLess(html.index("1. Choose a game"), html.index("2. Pick a set or collection"))
                self.assertLess(html.index("2. Pick a set or collection"), html.index("3. Choose a product type"))

    def test_the_set_row_marks_the_set_in_view_and_keeps_the_type(self):
        html = self.html(self.pre.get_absolute_url())
        row = html[html.index('class="cx-setrow"'):html.index("</nav>", html.index('class="cx-setrow"'))]
        self.assertIn(f'href="{self.pre.get_absolute_url()}" aria-current="page"', row)
        self.assertIn(f'href="{self.game.get_absolute_url()}">', row)          # All sets: the game's page
        html = self.html(self.game.get_absolute_url() + "elite-trainer-boxes/")
        row = html[html.index('class="cx-setrow"'):html.index("</nav>", html.index('class="cx-setrow"'))]
        self.assertIn(f'href="{self.pre.get_absolute_url()}?type=elite_trainer_box"', row)
        self.assertIn('aria-current="page"', row.split("</li>")[0])           # All sets is where you are

    def test_a_games_page_links_each_type_to_its_own_page(self):
        html = self.html(self.game.get_absolute_url())
        desk = html[html.index('class="cx-desk"'):]
        desk = desk[:desk.index("</div>\n</div>")]
        self.assertIn(f'href="{self.game.get_absolute_url()}elite-trainer-boxes/"', desk)
        self.assertIn(f'href="/search/?game={self.game.slug}"', desk)         # All: every product of the game

    def test_the_set_row_costs_the_same_however_many_sets(self):
        url = self.pre.get_absolute_url()
        self.html(url)
        with CaptureQueriesContext(connection) as few:
            self.html(url)
        for n in range(15):
            make_set(self.game, name=f"Another Set {n}", slug=f"another-set-{n}", code=f"AS{n}")
        cache.clear()
        look.remember()
        self.html(url)
        with CaptureQueriesContext(connection) as many:
            self.html(url)
        self.assertEqual(len(many), len(few))


class CardTests(DesktopCase):
    def test_a_row_carries_its_desktop_card(self):
        html = self.html(self.pre.get_absolute_url())
        row = html[html.index('<li class="crow'):]
        row = row[:row.index("</li>")]
        self.assertIn('class="crow__cta"', row)                                 # Compare prices at the foot
        self.assertIn('class="crow__save"', row)                                # Watch, outside the link
        self.assertIn(f'data-watch="{self.etb.slug}"', html)
        self.assertNotIn('<a class="button', row[:row.index('class="crow__save"')])   # never a link inside the link
        # Its picture is the phone's size on a phone and a card's from 64rem.
        self.assertIn("product-image--crow", row)
        self.assertEqual(images.SLOTS["crow"][1], "(min-width: 64rem) 12rem, 3.5rem")

    def test_grid_or_list_is_remembered_before_the_first_paint(self):
        html = self.html(self.pre.get_absolute_url())
        self.assertIn('data-view-switch', html)
        switch = re.search(r'<div class="cx-view"[^>]*>', html).group(0)
        self.assertIn("hidden", switch)                                         # shown by site.js
        self.assertIn('localStorage.getItem("ripraptor.view") === "list"', html)
        script = (WEB / "static/js/site.js").read_text()
        self.assertIn('localStorage.setItem("ripraptor.view"', script)

    def test_a_phone_sees_none_of_it(self):
        css = (WEB / "static/css/site.css").read_text()
        block = css[css.index("/* Desktop browsing ---"):]
        hidden = block[block.index(".cx-step, .cx-setrow"):block.index("@media")]
        for part in (".cx-step", ".cx-setrow", ".cx-desk", ".crow__why", ".crow__cta", ".crow__save",
                     ".crow__check--desk", ".cx-view"):
            self.assertIn(part, hidden)
        self.assertIn("display: none", hidden)


class WidthTests(DesktopCase):
    def test_the_frame_holds_the_page_and_only_lists_take_the_wider_page(self):
        html = self.html(self.etb.get_absolute_url())
        self.assertIn('<div class="frame frame--rip">', html)
        self.assertLess(html.index('<div class="frame frame--rip">'), html.index('<main id="main"'))
        css = (WEB / "static/css/site.css").read_text()
        self.assertIn(".frame > .page:not(.page--compact):not(.page--wide) { max-width: calc(var(--page-standard)", css)
        tokens = (WEB / "static/css/tokens.css").read_text()
        self.assertIn("@media (min-width: 80rem) {\n  :root { --page-width: var(--page-wide); }", tokens)


class AskRipBesideTests(DesktopCase):
    """Ask Rip beside the page on a wide screen: open unless closed in this browser, chosen before the first paint, with
    questions about the list in view and Quick insights from the caches alone."""

    def test_the_popup_sits_in_the_frame_beside_the_page(self):
        html = self.html(self.pre.get_absolute_url())
        self.assertIn('<div class="frame frame--rip">', html)
        self.assertEqual(html.count('id="ask-sheet"'), 1)
        self.assertLess(html.index("</main>"), html.index('id="ask-sheet"'))
        self.assertLess(html.index('id="ask-sheet"'), html.index('class="site-footer'))
        # Open before the first paint unless closed in this browser, and shown as the page is read.
        self.assertIn('window.matchMedia("(min-width: 80rem)").matches', html)
        self.assertIn('localStorage.getItem("ripraptor.rip") === "closed"', html)
        self.assertIn('if (document.documentElement.classList.contains("rip-side")) { var s = '
                      'document.getElementById("ask-sheet"); s.hidden = false;', html)

    def test_questions_about_the_list_in_view_are_links_to_answers(self):
        html = self.html(self.game.get_absolute_url() + "elite-trainer-boxes/")
        side = html[html.index('data-ask-side'):]
        self.assertIn("Which ETBs are the best value now?", side)
        self.assertIn(f'href="{self.game.get_absolute_url()}elite-trainer-boxes/?great=on"', side)
        self.assertIn(f'href="{self.game.get_absolute_url()}elite-trainer-boxes/?rip=on"', side)
        self.assertIn(f'href="{self.game.get_absolute_url()}elite-trainer-boxes/?sort=low"', side)
        self.assertIn('href="/deals/"', side)                   # every game's: the page reads no game
        self.assertNotIn("/deals/?", side)
        html = self.html(self.pre.get_absolute_url())
        side = html[html.index('data-ask-side'):]
        self.assertIn(f'href="{self.pre.get_absolute_url()}?great=on"', side)
        self.assertNotIn("sort=low", side)                     # a set's page has no sort
        html = self.html(self.etb.get_absolute_url())
        self.assertNotIn("Ask about this list", html)          # a product's page has its own questions

    def test_quick_insights_come_from_the_caches_alone(self):
        from django.template import Context

        from web import rip_side

        with self.assertNumQueries(0):
            found = rip_side.panel(Context({"game": self.game}))
        self.assertEqual((found["saving"], found["pack"], found["shops"], found["set"]), (None, None, [], None))

    def test_the_insights_are_the_rails_own_cards(self):
        from catalogue import offers

        from web import feed

        def card(name, shop, strength, price, packs):
            best = SimpleNamespace(retailer=SimpleNamespace(name=shop), shown_price=Decimal(price))
            product = SimpleNamespace(name=name, product_type=Product.Type.BOOSTER_BOX, packs_inside=packs)
            return feed.Card(product, offers.Summary(best, None, None, strength, None))

        cards = [card("first", "Harbour", 50, "150.00", 36), card("second", "Harbour", 10, "40.00", 9),
                 card("third", "Northgate", 30, "30.00", None)]
        found = feed.rail_insights({None: cards, 7: []})
        self.assertEqual(found[None]["saving"].product.name, "third")    # the strongest after Today's top deal
        self.assertEqual(found[None]["shops"], [("Harbour", 2), ("Northgate", 1)])
        each, cheapest = found[None]["pack"]                              # packs unknown: not in the running
        self.assertEqual((each, cheapest.product.name), (Decimal("4.17"), "first"))
        self.assertNotIn(7, found)

    def test_the_choice_and_the_conversation_are_kept_in_the_browser(self):
        script = (WEB / "static/js/ask-input.js").read_text()
        self.assertIn('localStorage.setItem("ripraptor.rip", state)', script)
        self.assertIn('sessionStorage.setItem("ripraptor.chat"', script)
        self.assertIn('if (beside()) closeBeside();', script)
        # Nothing is fetched ahead beside the page: startEarly is the popup's alone.
        beside = script[script.index("var openBeside"):script.index("var closeBeside")]
        self.assertNotIn("startEarly", beside)
        self.assertIn("window.ripNote({ q: asked, go: data.go })", (WEB / "static/js/ask-reply.js").read_text())


class DealsTests(DesktopCase):
    """Deals on a desktop screen: a page of products, so it takes the wider page as a list does, and its own parts follow
    the space; a phone sees it as before (every rule sits under 64rem's media query)."""

    def test_deals_takes_the_wider_page_and_its_parts_follow_the_space(self):
        html = self.html("/deals/")
        self.assertIn('<main id="main" class="page page--wide">', html)
        css = (WEB / "static/css/site.css").read_text()
        self.assertIn(".frame > .page:not(.page--compact):not(.page--wide) { max-width:", css)
        block = css[css.index("/* Deals on a desktop screen"):]
        block = block[:block.index("\n}\n") + 2]
        self.assertIn("@media (min-width: 64rem) {", block)
        self.assertIn(".page--wide .savings { display: grid; grid-template-columns: repeat(auto-fill, "
                      "minmax(var(--desk-row), 1fr));", block)
        self.assertIn(".page--wide .drops { display: grid; grid-template-columns: repeat(auto-fill, "
                      "minmax(var(--desk-list), 1fr));", block)
        # Every rule is the desktop screen's: before the media query there is only the note.
        self.assertNotIn("{", block[:block.index("@media")])

    def test_the_swipe_pages_saved_list_is_left_as_it_was(self):
        html = self.html("/swipe/")
        self.assertNotIn("page--wide", html[html.index('<main id="main"'):html.index(">", html.index('<main id="main"'))])


class RefinementTests(DesktopCase):
    """The owner's refinement pass of 26 Nov 2026, each point a desktop screen's alone: the games as buttons with More
    for the rest, the questions and the box in Ask Rip's column in that order, a column that is part of the page, a
    slim banner, a wider search box and shorter pictures on the cards."""

    def css(self):
        return (WEB / "static/css/site.css").read_text()

    def tokens(self):
        return (WEB / "static/css/tokens.css").read_text()

    def test_the_games_are_buttons_and_the_rest_wait_behind_more(self):
        from web import feed

        html = self.html(self.game.get_absolute_url())
        self.assertNotIn("feed-games__more", html)                     # few games: every one a button, no More
        for n in range(feed.DESK_BAR):
            other = make_game(name=f"Game {n}", slug=f"game-{n}")
            make_listing(make_product(make_set(other, name=f"Set {n}", slug=f"set-{n}", code=f"S{n}"),
                                      name=f"Game {n} Booster Box", product_type=Product.Type.BOOSTER_BOX),
                         self.harbour, price="90.00")
        cache.clear()
        look.remember()
        html = self.html(self.game.get_absolute_url())
        bar = html[html.index('<nav class="feed-games"'):html.index("</nav>", html.index('<nav class="feed-games"'))]
        self.assertEqual(bar.count('<a class="feed-game"'), feed.DESK_BAR + 2)   # All and every game, for a phone
        self.assertIn('<details class="feed-games__more"><summary class="feed-game feed-game--more">', bar)
        self.assertIn('<span class="feed-game__label">More</span></summary></details>', bar)
        css = self.css()
        # The row holds All and the first DESK_BAR games; the fold shows the rest, and never hides the game in view.
        self.assertIn(f'.compact-look .feed-games:has(.feed-games__more:not([open])) > a:nth-child(n + {feed.DESK_BAR + 2})'
                      ':not([aria-current="page"]) { display: none; }', css)
        self.assertIn("@supports selector(:has(*)) {", css)
        hidden = css[css.index(".cx-step, .cx-setrow"):]
        self.assertIn(".feed-games__more { display: none; }", hidden[:hidden.index("\n")])    # a phone has no More
        self.assertIn(".compact-look .feed-games { display: grid; grid-template-columns: repeat(auto-fit, "
                      "minmax(var(--desk-game), 1fr));", css)

    def test_beside_the_page_the_questions_come_first_then_the_box_then_the_insights(self):
        html = self.html(self.pre.get_absolute_url())
        sheet = html[html.index('id="ask-sheet"'):]
        # In the page the box is first, as a phone's popup shows it; site.css orders them beside the page.
        self.assertLess(sheet.index("data-ask-sheet-form"), sheet.index("data-ask-side"))
        self.assertLess(sheet.index("data-ask-side"), sheet.index('class="ask-sheet__lower"'))
        self.assertLess(sheet.index('class="ask-sheet__lower"'), sheet.index('class="ask-sheet__ways"'))
        # A list has its own questions, so the popup's suggestions step aside beside the page: nothing is said twice.
        self.assertIn('<ul class="ask-sheet__chips ask-sheet__chips--also"', sheet)
        product = self.html(self.etb.get_absolute_url())
        self.assertIn('<ul class="ask-sheet__chips" aria-label=', product)
        self.assertNotIn("data-ask-side", product)
        css = self.css()
        block = css[css.index("/* Ask Rip beside the page (html.rip-side"):]
        for rule in ("html.rip-side .ask-side--ask, html.rip-side .ask-sheet__more > .ask-sheet__chips { order: 1; }",
                     "html.rip-side .ask-sheet__log, html.rip-side .ask-sheet__reply { order: 2; }",
                     'html.rip-side [data-ask-view="all"] > .ask-sheet__form { order: 3; }',
                     "html.rip-side .ask-sheet__chips.ask-sheet__chips--also { display: none; }"):
            self.assertIn(rule, block)
        # Only the part under the box may scroll, and a phone's popup lays its parts out as it always did.
        self.assertIn("html.rip-side .ask-sheet__lower { order: 5; flex: 1 1 auto;", block)
        self.assertIn("overflow-y: auto;", block[block.index("html.rip-side .ask-sheet__lower {"):].split("\n")[0])
        self.assertIn(".ask-sheet__lower { display: contents; }", block[:block.index("@media")])
        self.assertNotIn("overflow-y", block[block.index("html.rip-side .ask-sheet__panel {"):].split("\n")[0])

    def test_the_column_is_part_of_the_page_and_its_questions_are_quiet(self):
        self.assertIn("--rip-side: clamp(22rem, 25vw, 23.75rem);", self.tokens())      # 352px to 380px
        css = self.css()
        block = css[css.index("/* Ask Rip beside the page (html.rip-side"):]
        column = block[block.index("html.rip-side .ask-sheet {"):].split("\n")[0]
        self.assertIn("border-left: var(--border-width) solid var(--color-line);", column)
        panel = block[block.index("html.rip-side .ask-sheet__panel {"):].split("\n")[0]
        for part in ("position: sticky;", "border: 0;", "background: none;", "box-shadow: none;"):
            self.assertIn(part, panel)
        chip = block[block.index("html.rip-side .ask-sheet__chip {"):].split("\n")[0]
        self.assertIn("border-color: var(--color-line);", chip)
        self.assertIn("background: var(--color-shelf); color: var(--color-ink);", chip)
        self.assertNotIn("brand", chip)                                 # orange is for what sends and what is chosen
        # The insights say their names in full: nothing is cut with dots beside the page.
        self.assertIn("html.rip-side .ask-pick__name { display: block; overflow: visible;", block)
        self.assertNotIn("text-overflow", block)

    def test_the_banner_is_a_slim_band_in_season_and_gone_out_of_it(self):
        from datetime import date
        from unittest import mock

        css = self.css()
        band = css[css.index("/* On a desktop screen the banner is one slim band"):]
        band = band[:band.index("\n}\n")]
        self.assertIn("@media (min-width: 64rem) {", band)
        self.assertIn(".bf-banner { grid-template-columns: minmax(0, 1fr) auto; align-items: center;", band)
        self.assertIn("margin: 0 0 var(--space-4); padding: var(--space-3) var(--space-4); }", band)
        with mock.patch("web.black_friday.local_today", return_value=date(2026, 11, 25)):
            self.assertIn('class="bf-banner"', self.html("/"))
        cache.clear()
        look.remember()
        with mock.patch("web.black_friday.local_today", return_value=date(2026, 6, 1)):
            self.assertNotIn("bf-banner", self.html("/"))

    def test_the_search_box_takes_the_room_and_the_cards_pictures_are_shorter(self):
        tokens = self.tokens()
        self.assertIn("--desk-search: 60rem;", tokens)
        self.assertIn("--desk-deal-well: 10rem;", tokens)
        css = self.css()
        self.assertIn(".masthead__search { justify-self: center; width: 100%; max-width: var(--desk-search); }", css)
        self.assertIn("html:not(.view-list) .compact-look .crow__pic { display: block; height: var(--desk-deal-well);", css)
        self.assertIn("html:not(.view-list) .compact-look .crow__body { gap: var(--space-2); padding: var(--space-4) "
                      "var(--space-4) 0; }", css)
