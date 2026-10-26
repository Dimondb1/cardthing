"""Filters change the list in place (browse.js), and pages cross-fade into each other (view transitions), both
as enhancements: every form and link still works as a plain page load."""

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from catalogue.testing import make_game, make_listing, make_product, make_retailer, make_set


def static_text(path):
    with open(finders.find(path), encoding="utf-8") as handle:
        return handle.read()


class LiveRegionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = make_game()
        cls.product_set = make_set(cls.game)
        make_listing(make_product(cls.product_set), make_retailer())

    def test_game_set_and_search_pages_carry_the_region_and_the_script(self):
        for url in (self.game.get_absolute_url(), self.product_set.get_absolute_url(), reverse("web:search") + "?q=box"):
            with self.subTest(url):
                html = self.client.get(url).content.decode()
                self.assertIn("<div data-browse>", html)
                self.assertIn("js/browse.js", html)
                # The share sheet sits outside the region, so the swap leaves it (and share.js's hold on it) alone.
                self.assertLess(html.index("data-share-dialog"), html.index("<div data-browse>"))
                self.assertLess(html.index("<div data-browse>"), html.index("</main>"))

    def test_the_swipe_page_has_neither(self):
        html = self.client.get(reverse("web:deck")).content.decode()
        self.assertNotIn("data-browse", html)
        self.assertNotIn("js/browse.js", html)

    def test_game_pages_load_the_tabs_script_whatever_the_tab_row(self):
        # One list, no tab row: the script is there all the same, for the row a filter change can bring in.
        html = self.client.get(self.game.get_absolute_url()).content.decode()
        self.assertNotIn('class="stock-tabs', html)
        self.assertIn("js/tabs.js", html)
        self.assertNotIn("js/tabs.js", self.client.get(reverse("web:search") + "?q=box").content.decode())


class ScriptTests(TestCase):
    def test_browse_js_swaps_the_region_in_place_and_falls_back_to_a_page_load(self):
        script = static_text("js/browse.js")
        self.assertIn('document.querySelector("[data-browse]")', script)
        self.assertIn("history.pushState({ browse: true }", script)
        self.assertIn("if (event.state && event.state.browse) load(location.href, false);", script)
        # The fade is the browser's, only where it has one and motion is not reduced.
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', script)
        self.assertIn('typeof document.startViewTransition === "function" && !reduce.matches', script)
        # A fetch that fails loads the page the form or link named, with the panel open on it.
        self.assertIn("location.assign(url);", script)
        self.assertLess(script.index("remember();"), script.index("location.assign(url);"))
        # A page of results is a page load, so its top comes into view; other pages' links are theirs.
        self.assertIn('link.closest(".pagination")', script)
        self.assertIn("url.pathname !== location.pathname", script)
        # The scripts that enhance a list set the new one up.
        for event in ("ripraptor:content", "ripraptor:page"):
            self.assertIn(f'new CustomEvent("{event}", {{ detail: fresh }})', script)
        self.assertIn("again.focus({ preventScroll: true })", script)
        self.assertIn('root.setAttribute("aria-busy", "true")', script)

    def test_site_js_enhances_a_form_swapped_in_once(self):
        script = static_text("js/site.js")
        self.assertIn('form[data-autosubmit]:not([data-autosubmit-ready])', script)
        after = script[script.index('document.addEventListener("ripraptor:content"'):]
        for call in ("settleImages(event.detail);", "autosubmit(event.detail);", "fitPills();"):
            self.assertIn(call, after)
        self.assertNotIn("rr-filters-open", script)   # browse.js keeps the folded panel open now

    def test_tabs_js_sets_up_again_for_a_list_swapped_in(self):
        script = static_text("js/tabs.js")
        self.assertIn('document.addEventListener("ripraptor:page", start);', script)
        self.assertIn("if (stop) stop();", script)
        self.assertIn('window.addEventListener("resize", () => {', script)
        self.assertIn("}, { signal });", script)
        self.assertIn("gone.abort();", script)


class MotionTests(TestCase):
    def test_pages_cross_fade_with_the_masthead_still_and_nothing_under_reduced_motion(self):
        css = static_text("css/site.css")
        self.assertIn("@view-transition { navigation: auto; }", css)
        self.assertIn("::view-transition-old(root), ::view-transition-new(root), ::view-transition-group(masthead) "
                      "{ animation-duration: var(--motion-normal); }", css)
        self.assertIn(".masthead { view-transition-name: masthead; }", css)
        reduced = css[css.index("@media (prefers-reduced-motion: reduce) {\n  *, *::before"):]
        self.assertIn("::view-transition-old(*), ::view-transition-new(*) { animation: none !important; }",
                      reduced[:reduced.index("}\n\n")])

    def test_a_list_waiting_dims_only_opacity_and_only_after_a_moment(self):
        css = static_text("css/site.css")
        self.assertIn('[data-browse][aria-busy="true"] .stock-panes, [data-browse][aria-busy="true"] > .cards, '
                      '[data-browse][aria-busy="true"] > .empty { opacity: var(--opacity-waiting); '
                      "transition-delay: var(--motion-fast); }", css)
        self.assertIn("--opacity-waiting: 0.6;", static_text("css/tokens.css"))
