"""
The site's layout and design rules, as data. web/tests_design_rules.py reads this file and fails the build when a
page breaks a rule, so a rule is kept because it cannot be broken quietly, not because someone remembered it. The
owner, 25 Nov 2026, of a page built with a design of its own: "why is it different than everything else. we need
this site to stick to strict layout and design rules and should be implemented and enforced".

The rules, each with its test:

1. Every page has a kind. A page that lists products to choose from is a LIST page; every other page is STANDARD. A
   new address cannot be added without saying which (PAGES, NOT_PAGES).
2. A LIST page wears the look the site is in (web/look.py). In the compact look that is: the compact body, the search
   box and games at the foot of a phone (the dock), the status line or the home page's numbers, and its products as
   compact rows. In the classic look: none of those, and its products as cards. It has no design of its own.
   On a desktop screen (from 64rem; the owner, 26 Nov 2026) the same parts are laid out for the width: the list leads
   with three steps, each a shared part with its label (the dock's games, compact_set_row.html, compact_tiles.html),
   and the compact rows are shown as cards in a grid whose columns follow the space, or as rows with List chosen. The
   markup is the same row on every screen: only site.css lays it out, so a phone is unchanged. The games are a row
   of bordered buttons the width of the page, the less popular behind More (web/feed.py DESK_BAR). From 80rem Ask
   Rip sits beside the page as a column that is part of it (questions, the box, Quick insights), never a card over
   it, and its questions are neutral rows: orange is for what sends and what is chosen.
3. A product is only ever shown through one of the shared parts in PRODUCT_PARTS (a row, a card, a table row...), or
   named in a sentence (PROSE). A new way to show a product is a new entry here, which the owner agrees first.
4. Every page has exactly one main heading.
5. The stylesheet holds no values of its own: every colour (web/tests_dark_mode.py), text size, weight, corner and
   font is a token from tokens.css, and it may hold no more bare lengths or timings than it does today (RAW_MOST:
   the numbers only go down). Only the breakpoints in BREAKPOINTS are used.
6. A template styles nothing itself: no style attributes or style blocks beyond the few listed in TEMPLATE_STYLES,
   each of which sets a figure only the page knows (a chart bar's height) or switches on a browser feature.

Still to bring over (LIST_LATER): pages that list products but keep the usual shell in every look, as they did before
these rules. They are named here so they are not forgotten, and move to LIST with the owner's say.
"""

LIST, STANDARD = "list", "standard"

# Every address in web/urls.py that answers with a page, by its name.
PAGES = {
    # Products to choose from: these wear the look.
    "home": LIST, "search": LIST, "game": LIST, "set": LIST, "per_card": LIST,
    "black_friday": LIST, "black_friday_game": LIST,
    # Everything else keeps the usual shell in every look.
    "product": STANDARD, "deals": STANDARD, "drops": STANDARD, "prices": STANDARD, "prices_game": STANDARD,
    "games": STANDARD, "deck": STANDARD, "find": STANDARD, "find_list": STANDARD, "search_photo": STANDARD,
    "watchlist": STANDARD, "shopping_list": STANDARD, "about": STANDARD, "ask_rip_about": STANDARD,
    "guides": STANDARD, "fair_price": STANDARD, "guide_compare": STANDARD, "guide": STANDARD,
    "guide_checklist": STANDARD, "contact": STANDARD, "contact_thread": STANDARD, "list_shop": STANDARD,
    "list_shop_thanks": STANDARD, "remove_shop": STANDARD, "remove_shop_thanks": STANDARD, "terms": STANDARD,
    "refer": STANDARD, "refer_confirm": STANDARD, "refer_claim": STANDARD, "refer_address": STANDARD,
    "refer_stop": STANDARD, "refer_friend": STANDARD, "alert_ask": STANDARD, "alert_confirm": STANDARD,
    "alert_stop": STANDARD, "alerts_manage": STANDARD, "manage_ask": STANDARD, "drop_ask": STANDARD,
    "drop_confirm": STANDARD, "drop_stop": STANDARD, "search_alert_ask": STANDARD,
    "search_alert_confirm": STANDARD, "search_alert_stop": STANDARD, "report_listing": STANDARD,
    "ask_rip": STANDARD, "product_post": STANDARD, "go": STANDARD, "go_continue": STANDARD,
    "dashboard": STANDARD,
}

# Addresses that are not pages: data for scripts, pictures, files, form posts and redirects.
NOT_PAGES = {
    "search_api", "deck_api", "recent_api", "picks_api", "note_api", "product_prices_api", "voice_search",
    "product_post_image", "product_story_image", "ask_rip_card", "robots", "sitemap", "llms", "indexnow_key",
    "manifest", "feed_deals", "feed_game", "dashboard_report", "dashboard_layout", "guide_feedback",
    "list_add", "list_remove", "list_clear", "list_quantity", "list_add_many", "go_search", "new", "shops",
}

# Pages that list products but keep the usual shell for now (see the notes above).
LIST_LATER = {"deals", "drops", "prices_game", "watchlist", "shopping_list"}

# The shared parts a product may be shown through, by the class on the part: the link to a product's page sits
# inside one of these on every page, in every look.
PRODUCT_PARTS = {
    "crow",             # a compact row (compact_row.html)
    "card",             # a product card (product_card.html)
    "feed-card",        # a card on the feed's rails (feed_card.html)
    "cx-deal",          # a deal in the compact strip (compact_strip.html)
    "trending__item",   # a tile in a row that scrolls sideways: featured deals, picks, recently viewed
    "saving",           # a Deals page saving
    "drop",             # a slim row with one figure (Deals: price drops, lows and restocks)
    "price-table",      # a row of a table of figures (UK prices, Black Friday's figures)
    "ask-pick",         # a pick in the Ask sheet and the empty search
    "hub-answer__row",  # a game page's cheapest of each kind
    "result",           # a related product on a product page (product_row.html)
    "cheapest-card",    # a set's cheapest product, under its heading
    "watch__item",      # a watchlist or shopping list row
    "drop-card",        # a drop's card on the Drops page, with its shops' offers
}
# A product named in a sentence: a plain link in running text.
PROSE = {"hub-about__text", "browse-head__intro", "cx-setintro", "hub-faq__a", "prose", "guide"}

# The breakpoints the stylesheet may use, in rem (a max-width one stops a hair under the next min-width).
BREAKPOINTS = {"14.99", "15.99", "18.99", "22.5", "39.99", "40", "47.99", "48", "59.99", "60", "64", "74", "75", "80"}

# The most bare lengths and timings the rules of site.css may hold: what they held on 25 Nov 2026. A breakpoint is not
# counted (a custom property cannot stand in a media query). New rules use tokens, so these only go down; lower them
# when they do.
RAW_MOST = {"px": 163, "rem": 114, "time": 12}
# Properties whose every value is a token (or one of PLAIN), with the few bare values there are.
TOKEN_ONLY = {"font-size": {"1.5rem"}, "font-weight": set(), "border-radius": set(), "font-family": set()}
PLAIN = {"inherit", "none", "0", "50%", "100%", "initial", "unset", "normal"}

# Templates that set a style themselves: {template: (style attributes, style blocks)}, the most each may hold.
TEMPLATE_STYLES = {
    "web/browse.html": (0, 1), "web/compact/_scripts.html": (0, 1), "web/home_feed.html": (0, 1),   # page transitions
    "web/product.html": (0, 1), "web/find.html": (1, 1),
    "web/dashboard.html": (1, 0), "web/includes/dash/claude.html": (1, 0), "web/includes/dash/clicks.html": (1, 0),
    "web/includes/dash/money.html": (1, 0), "web/includes/dash/shops.html": (1, 0),
    "web/includes/dash/visitors.html": (1, 0),                                                     # a bar's own width
    "web/includes/price_chart.html": (3, 0), "web/includes/mark_hunt.html": (1, 0),
    "web/includes/season/easter_scene.html": (1, 0), "web/includes/spotlight.html": (1, 0),
}
