"""The search hint on a phone (the owner, 23 Nov 2026, of "Search any pro..." in the bar at the foot of a phone: "make
the message scroll so you can read it. The fact it's cut off is ... annoying")."""

import re

from django.test import SimpleTestCase


def read(path):
    return open(path, encoding="utf-8").read()


class SearchGlideTests(SimpleTestCase):
    def setUp(self):
        self.css = read("web/static/css/site.css")
        self.script = read("web/static/js/feed.js")

    def test_the_hint_moves_only_by_transform_and_never_with_reduced_motion(self):
        frames = re.search(r"@keyframes feed-glide \{(.*?)\}\s*\}", self.css, re.S).group(1)
        self.assertEqual(set(re.findall(r"([a-z-]+):", frames)), {"transform"})
        self.assertIn(".feed-search__glide > span { flex: none; animation: feed-glide var(--motion-glide) var(--ease-standard) "
                      "infinite alternate; }", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce) { .feed-search__glide { display: none; }", self.css)
        self.assertIn('if (!field || !glide || reduced) return;', self.script)
        # Its length is a token, as every value is, longer than the usual 400ms at the owner's request.
        self.assertIn("--motion-glide: 6s;", read("web/static/css/tokens.css"))

    def test_it_shows_only_while_the_box_rests(self):
        # Never over what is typed, heard or being searched; the box's own hint is hidden under it only while it shows.
        self.assertIn(".feed-search input:not(:placeholder-shown) ~ .feed-search__glide,\n.feed-search.is-listening "
                      ".feed-search__glide, .feed-search.is-searching .feed-search__glide, .feed-search.is-voicing "
                      ".feed-search__glide { display: none; }", self.css)
        self.assertIn(".feed-search.has-glide:not(.is-listening):not(.is-searching) input::placeholder { color: transparent; }",
                      self.css)
        # It glides only when the words are wider than the box, measured as shown, again when the box changes size.
        self.assertIn("if (over > 1) {", self.script)
        self.assertIn("new ResizeObserver(fit).observe(field)", self.script)

    def test_an_empty_box_gives_the_clear_buttons_room_to_the_hint(self):
        # Chrome keeps that room though the button is invisible; at 384px "Search any product" then fits whole.
        self.assertIn(".feed-search input:placeholder-shown::-webkit-search-cancel-button { display: none; }\n", self.css)
