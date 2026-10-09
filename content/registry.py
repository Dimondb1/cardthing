"""
Default wording for every editable piece of site copy.

Each entry has a stable key used by templates, for example::

    {% copy "home.hero.title" %}

The first part of the key is the section it belongs to in Django Admin.

Rows in the SiteContent table are created from this list whenever
``migrate`` runs (or ``manage.py sync_site_content``). Editing wording in
Django Admin never requires a change here. Change this file only to add a new
key, or to change the default that "Restore default wording" goes back to.

Style rules for defaults (checked by the test suite):

* short, direct British English
* no em dashes
* no marketing filler
"""

from dataclasses import dataclass


class Kind:
    HEADING = "heading"
    TEXT = "text"
    PARAGRAPHS = "paragraphs"
    BUTTON = "button"

    choices = [
        (HEADING, "Heading"),
        (TEXT, "Short text"),
        (PARAGRAPHS, "Paragraphs"),
        (BUTTON, "Button or link label"),
    ]


SECTIONS = [
    ("site", "Header and search box"),
    ("deck", "Swipe page"),
    ("home", "Home page"),
    ("deals", "Deals page"),
    ("feeds", "Feeds (RSS)"),
    ("alerts", "Back in stock emails"),
    ("watchlist", "Watchlist page"),
    ("browse", "Search results and browsing"),
    ("product", "Product page"),
    ("about", "How it works page"),
    ("contact", "Message us page"),
    ("footer", "Footer"),
    ("terms", "Terms page"),
    ("errors", "Error pages"),
    ("meta", "Page titles and search engine descriptions"),
]
SECTION_KEYS = {key for key, _ in SECTIONS}

# Shown in Django Admin next to any text that supports placeholders.
PLACEHOLDER_HELP = {
    "count": "a number",
    "date": "a date, for example 12 Aug 2026",
    "days": "a number of days",
    "delivery": "a delivery charge, for example £3.99",
    "email": "the contact email address",
    "game": "a game name, for example Pokémon",
    "high": "a price",
    "high_date": "a date",
    "hours": "a number of hours",
    "low": "a price",
    "low_date": "a date",
    "page": "the current page number",
    "pages": "the number of pages",
    "price": "a price, for example £44.99",
    "product": "the product name",
    "query": "what the visitor typed into search",
    "retailer": "a retailer name",
    "set": "a set name",
    "site_name": "the site name, RipRaptor",
    "time": "how long ago, for example 18 minutes ago",
    "was": "the previous price, for example £59.99",
    "start": "a time of day, for example 9am",
    "end": "a time of day, for example 11am",
}


@dataclass(frozen=True)
class Entry:
    key: str
    kind: str
    label: str
    default: str
    help: str
    placeholders: tuple = ()
    legal: bool = False
    optional: bool = False

    @property
    def section(self):
        return self.key.split(".", 1)[0]


H, T, P, B = Kind.HEADING, Kind.TEXT, Kind.PARAGRAPHS, Kind.BUTTON


ENTRIES = [
    # Header and search box ------------------------------------------------
    Entry(
        "site.search.placeholder", T, "Search box hint",
        "Search by product, set or game",
        "Grey hint text inside the search box, in the header and on the home page.",
    ),
    Entry(
        "site.search.button", B, "Header search button",
        "Search",
        "Button next to the search box in the header.",
    ),
    Entry(
        "site.search.recent", T, "Recent searches label",
        "Recent searches",
        "Above the searches this browser made before, shown under the search box before anything is typed.",
    ),
    Entry("site.install.label", T, "Home screen card name", "Add to home screen", "Name of the card for screen readers."),
    Entry(
        "site.install.android", T, "Home screen offer (Android)",
        "Keep RipRaptor on your home screen. It opens like an app, with no browser bar.",
        "The card shown once on an Android phone after a few pages.",
    ),
    Entry(
        "site.install.ios", T, "Home screen offer (iPhone)",
        "Keep RipRaptor on your home screen: tap Share, then Add to Home Screen.",
        "The card shown once on an iPhone after a few pages. Safari has no install button, so this explains the menu.",
    ),
    Entry("site.install.add", B, "Add to home screen button", "Add to home screen", "On the card, Android only."),
    Entry("site.install.dismiss", B, "Not now button", "Not now", "On the card. Pressing it means the card never shows again in that browser."),
    Entry(
        "site.nav.swipe", B, "Swipe link",
        "Swipe",
        "Header link to the swipe page on phones.",
    ),
    Entry(
        "site.see_all", B, "See all link",
        "See all",
        "Link at the end of each home page section.",
    ),
    Entry("site.nav.contact", B, "Message us link", "Message us", "Footer link to the Message us page."),
    Entry(
        "site.nav.terms", B, "Terms link",
        "Terms",
        "Footer link to the terms page.",
    ),
    Entry(
        "site.nav.games", B, "Games link",
        "Games",
        "Header link to the list of games and sets.",
    ),
    Entry(
        "site.nav.watchlist", B, "Watchlist link",
        "Watchlist",
        "Header and footer link to the products the visitor saved.",
    ),
    Entry(
        "site.nav.deals", B, "Deals link",
        "Deals",
        "Header and footer link to the deals page.",
    ),
    Entry(
        "site.nav.about", B, "How it works link",
        "How it works",
        "Header and footer link to the page that explains prices and commission.",
    ),

    # Home page ------------------------------------------------------------
    Entry(
        "home.hero.title", H, "Home page headline",
        "Better prices. More packs.",
        "The one strong line at the top of the home page, above the search box.",
    ),
    Entry(
        "home.hero.subtitle", T, "Line under the headline",
        "Compare sealed TCG products across UK retailers and buy at the best price.",
        "One sentence under the headline, above the search box. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "home.search.button", B, "Home search button",
        "Find best price",
        "Button next to the large search box on the home page.",
    ),
    Entry(
        "home.search.placeholder", T, "Home search box hint",
        "Search “Prismatic Evolutions ETB”",
        "Grey hint text inside the large search box on the home page.",
    ),
    Entry(
        "home.search.checked", T, "Prices last checked",
        "Prices checked {time}",
        "Under the home page search box. Uses the most recent check across every retailer. "
        "Hidden until a price has been checked.",
        placeholders=("time",),
    ),
    Entry(
        "home.search.checking", T, "Searching message",
        "Checking lowest prices",
        "Shown for a moment while search results load as you type.",
    ),
    Entry(
        "home.search.all_results", B, "All results link",
        "All results for ‘{query}’",
        "Link under the quick results as you type, leading to the full results page.",
        placeholders=("query",),
    ),
    Entry(
        "home.games.label", T, "Game shortcuts label",
        "Browse by game",
        "Small label before the list of game links under the home page search box.",
    ),
    Entry(
        "home.games.more", B, "More games link",
        "More games",
        "Link at the end of the game shortcuts, leading to the full list of games.",
    ),
    Entry(
        "home.focal.drop.title", H, "Biggest price drop heading",
        "Biggest price drop today",
        "Heading on the large highlight under the search box when a price has fallen this week.",
    ),
    Entry(
        "home.focal.saving.title", H, "Biggest saving heading",
        "Biggest saving right now",
        "Heading on the large highlight under the search box when no price has fallen this week. "
        "It shows the product where the cheapest shop beats the next one by the most.",
    ),
    Entry(
        "home.focal.was", T, "Highlight old price label",
        "was {price}",
        "Old price on the highlight, shown crossed out.",
        placeholders=("price",),
    ),
    Entry(
        "home.focal.next", T, "Highlight next best price",
        "Next best {price} at {retailer}",
        "Runner-up shop on the highlight.",
        placeholders=("price", "retailer"),
    ),
    Entry(
        "home.trending.down", T, "Price fell this week",
        "{amount} this week",
        "Under a trending product whose cheapest price is lower than a week ago. "
        "Shown in green with a down arrow.",
        placeholders=("amount",),
    ),
    Entry(
        "home.trending.up", T, "Price rose this week",
        "{amount} this week",
        "Under a trending product whose cheapest price is higher than a week ago. "
        "Shown in red with an up arrow.",
        placeholders=("amount",),
    ),
    Entry(
        "home.savings.next", T, "Runner-up price on savings rows",
        "{price} at {retailer}",
        "The price at the next cheapest shop on each savings row, shown crossed out. The saving is measured against it.",
        placeholders=("price", "retailer"),
    ),
    Entry(
        "home.new.title", H, "Latest drops heading",
        "Latest drops",
        "Home page heading above the newest products.",
    ),
    Entry(
        "home.new.intro", T, "Latest drops note",
        "New sealed products as UK shops list them, newest release first.",
        "One line under the latest drops heading. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "home.new.out", T, "Pre-order release date",
        "Out {date}",
        "On a latest-drops card that is on pre-order. {date} is the release date.",
        placeholders=("date",),
    ),
    Entry(
        "home.new.released", T, "Released date",
        "Released {date}",
        "On a latest-drops card that is out. {date} is the release date.",
        placeholders=("date",),
    ),
    Entry(
        "home.football.title", H, "Football row heading",
        "Football and sports cards",
        "Home page heading above the football row. Shown only while football products are in stock.",
    ),
    Entry(
        "home.football.intro", T, "Football row note",
        "Match Attax, Topps and Panini boxes, tins and packs, with the cheapest delivered price.",
        "One line under the football heading. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "home.restock.title", H, "Back in stock heading",
        "Back in stock",
        "Heading for the row of products a shop has just restocked.",
    ),
    Entry(
        "home.restock.intro", T, "Back in stock explanation",
        "Sold out, and now a shop has it again. Checked every few minutes.",
        "Short line under the back in stock heading. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "home.restock.when", T, "Restocked time",
        "Restocked {time}",
        "Under each product on the back in stock row.",
        placeholders=("time",),
    ),
    Entry(
        "home.recent.title", H, "Recently released heading",
        "Recently released",
        "Heading for the row of the newest sets.",
    ),
    Entry(
        "home.recent.count", T, "Products in a set",
        "{count} products",
        "Under each set on the recently released row.",
        placeholders=("count",),
    ),
    Entry(
        "home.trending.title", H, "Trending heading",
        "Trending now",
        "Heading for the row of most viewed products.",
    ),
    Entry(
        "home.savings.title", H, "Biggest savings heading",
        "Biggest savings",
        "Heading for products where the cheapest retailer beats the next one by the most.",
    ),
    Entry(
        "home.savings.save", T, "Saving amount",
        "Save {amount}",
        "Green label on savings rows and product cards.",
        placeholders=("amount",),
    ),
    Entry(
        "home.savings.save_vs", T, "Saving against a named shop",
        "Save {amount} vs {retailer}",
        "Green label on featured deals: the saving and the next cheapest shop it is measured against.",
        placeholders=("amount", "retailer"),
    ),
    Entry(
        "home.savings.percent", T, "Saving percentage",
        "{percent}% cheaper",
        "Under the saving amount.",
        placeholders=("percent",),
    ),
    Entry(
        "home.price_drops.title", H, "Price drops heading",
        "Price drops this week",
        "Heading for the list of products whose cheapest price has fallen.",
    ),
    Entry(
        "home.price_drops.intro", T, "Price drops explanation",
        "Cheapest delivered price now, compared with {days} days ago.",
        "Short line under the price drops heading explaining the comparison. "
        "Leave empty to hide it.",
        placeholders=("days",),
        optional=True,
    ),
    Entry(
        "home.price_drops.was", T, "Previous price label",
        "was {price}",
        "Shown next to each price drop with the old price crossed out.",
        placeholders=("price",),
    ),
    Entry(
        "home.popular.title", H, "Popular products heading",
        "Popular this week",
        "Heading for the most clicked products.",
    ),
    Entry(
        "home.popular.intro", T, "Popular products explanation",
        "Most clicks through to a retailer in the last {days} days.",
        "Short line under the popular heading explaining how it is worked out. "
        "Leave empty to hide it.",
        placeholders=("days",),
        optional=True,
    ),
    Entry(
        "home.resume.title", H, "Pick up heading",
        "Pick up where you left off",
        "Home page section with this browser's recent searches and viewed products. Hidden until there are some.",
    ),
    Entry("home.resume.searches", T, "Recent searches label", "Recent searches", "Label in the pick up section."),
    Entry("home.resume.viewed", T, "Recently viewed label", "Recently viewed", "Label in the pick up section."),
    Entry("home.resume.clear", B, "Clear history button", "Clear history", "Forgets the recent searches and viewed products in this browser."),
    Entry(
        "home.resume.note", T, "Pick up note",
        "Kept in this browser only.",
        "Small note under the pick up section.",
    ),
    Entry("home.featured.title", H, "Featured deals heading", "Featured deals", "Home page row near the top."),
    Entry(
        "home.featured.note", T, "Featured deals note",
        "Every featured deal is a real saving on the next shop or this week's lowest price. We favour shops that pay us a commission when choosing them, and each product page still shows every shop's price.",
        "On the terms page, under Independence. Says how the home page's featured deals row is chosen.",
        legal=True,
    ),
    Entry(
        "home.games.title", H, "All games heading",
        "Browse by game",
        "Heading for the list of games and their newest sets at the bottom of the home page.",
    ),

    # Search results and browsing -----------------------------------------
    Entry(
        "browse.results.title", H, "Search results heading",
        "Results for ‘{query}’",
        "Heading on the search results page.",
        placeholders=("query",),
    ),
    Entry(
        "browse.all.title", H, "All products heading",
        "All products",
        "Heading on the search page when nothing has been typed.",
    ),
    Entry(
        "browse.count.one", T, "Product count (one)",
        "1 product",
        "Number of results when there is exactly one.",
    ),
    Entry(
        "browse.count.other", T, "Product count (more than one)",
        "{count} products",
        "Number of results, and the number of products next to each game.",
        placeholders=("count",),
    ),
    Entry(
        "browse.no_results.title", H, "No results heading",
        "No products match ‘{query}’",
        "Heading when a search finds nothing.",
        placeholders=("query",),
    ),
    Entry(
        "browse.no_results.body", P, "No results help",
        "Check the spelling or try fewer words. Searching for the set name on its own "
        "usually works.",
        "Help text under the no results heading, above links to each game.",
    ),
    Entry(
        "browse.no_filtered_results", T, "No results with filters",
        "Nothing matches these filters.",
        "Shown when filters such as game or product type rule out every product.",
    ),
    Entry(
        "browse.new.title", H, "Latest drops page heading",
        "Latest drops",
        "Heading of the latest drops page.",
    ),
    Entry(
        "browse.new.intro", T, "Latest drops page introduction",
        "Sealed products released in the last few weeks, on pre-order, or newly listed by UK shops, "
        "with the cheapest delivered price for each.",
        "Under the latest drops page heading.",
    ),
    Entry(
        "browse.filters.apply", B, "Apply filters button",
        "Show results",
        "Button that applies the filters. Only visible if JavaScript is off.",
    ),
    Entry(
        "browse.filters.clear", B, "Clear filters link",
        "Clear filters",
        "Link that removes all filters.",
    ),
    Entry(
        "browse.price.label", T, "Price label in results",
        "delivered",
        "Small word under the price in search results and product lists.",
    ),
    Entry(
        "browse.price.plus", T, "Price label when delivery is unknown",
        "+ delivery",
        "After a price whose delivery charge we do not know. Never say delivered for these.",
    ),
    Entry(
        "browse.availability.in_stock.one", T, "In stock at one retailer",
        "In stock at 1 retailer",
        "Stock line in search results.",
    ),
    Entry(
        "browse.availability.in_stock.other", T, "In stock at several retailers",
        "In stock at {count} retailers",
        "Stock line in search results.",
        placeholders=("count",),
    ),
    Entry(
        "browse.availability.preorder.one", T, "Pre-order at one retailer",
        "Pre-order at 1 retailer",
        "Stock line in search results when nobody has stock but pre-orders are open.",
    ),
    Entry(
        "browse.availability.preorder.other", T, "Pre-order at several retailers",
        "Pre-order at {count} retailers",
        "Stock line in search results when nobody has stock but pre-orders are open.",
        placeholders=("count",),
    ),
    Entry(
        "browse.availability.none", T, "Out of stock everywhere",
        "Out of stock",
        "Stock line in search results when no retailer has the product.",
    ),
    Entry(
        "browse.no_prices", T, "No prices yet",
        "No prices yet",
        "Shown in place of a price when a product has no listings.",
    ),
    Entry(
        "browse.pagination.previous", B, "Previous page link",
        "Previous page",
        "Pagination link.",
    ),
    Entry(
        "browse.pagination.next", B, "Next page link",
        "Next page",
        "Pagination link.",
    ),
    Entry(
        "browse.pagination.status", T, "Page number",
        "Page {page} of {pages}",
        "Shown between the pagination links.",
        placeholders=("page", "pages"),
    ),
    Entry(
        "browse.games.title", H, "Games page heading",
        "Games and sets",
        "Heading on the page that lists every game and set.",
    ),
    Entry(
        "browse.game.sets_title", H, "Sets heading on a game page",
        "Sets",
        "Heading above the list of sets on each game page.",
    ),
    Entry(
        "browse.set.released", T, "Set release date",
        "Released {date}",
        "Under the heading on each set page.",
        placeholders=("date",),
    ),
    Entry(
        "browse.game.all_sets", B, "All sets link",
        "All {game} products",
        "Link on a set page back to every product for that game.",
        placeholders=("game",),
    ),

    # Swipe page --------------------------------------------------------------
    Entry(
        "deck.title", H, "Swipe page heading",
        "Swipe through today's prices",
        "Heading and page title for the swipe page.",
    ),
    Entry(
        "deck.hint", T, "Swipe hint",
        "Swipe right to save a product, left to skip. Tap a card for every price.",
        "Shown above the first card, then hidden.",
    ),
    Entry(
        "deck.save", B, "Save button", "Save", "Button under the deck, same as swiping right."),
    Entry(
        "deck.skip", B, "Skip button", "Skip", "Button under the deck, same as swiping left."),
    Entry(
        "deck.saved.title", H, "Saved heading", "Saved", "Heading above the products you swiped right on."),
    Entry(
        "deck.saved.empty", T, "Saved list empty",
        "Nothing saved yet.",
        "Shown under the saved heading before anything is saved.",
    ),
    Entry(
        "deck.saved.clear", B, "Clear saved", "Clear saved", "Removes every saved product."),
    Entry(
        "deck.finished", T, "End of deck",
        "That's every product with a price today.",
        "Shown when there are no more cards.",
    ),
    Entry(
        "deck.restart", B, "Start again", "Start again", "Button shown at the end of the deck."),
    Entry(
        "deck.loading", T, "Loading", "Checking lowest prices", "Shown while cards load."),
    Entry(
        "deck.all_games", B, "All games filter", "All games", "First option in the game filter on the swipe page."),

    # Product page ----------------------------------------------------------
    Entry(
        "product.cheapest.label", T, "Cheapest price label",
        "Cheapest price",
        "Small label above the main price on a product page.",
    ),
    Entry(
        "product.cheapest.label_unknown", T, "Main price label (delivery unknown)",
        "Lowest item price",
        "Small label above the main price when no shop's delivery charge is known.",
    ),
    Entry(
        "product.cheapest.retailer", T, "Cheapest retailer",
        "at {retailer}",
        "Shown next to the main price.",
        placeholders=("retailer",),
    ),
    Entry(
        "product.delivered.line", T, "Delivered price line",
        "{price} delivered",
        "Under the shop's own price on cards and the product page: what you pay with delivery.",
        placeholders=("price",),
    ),
    Entry(
        "product.breakdown.delivery", T, "Price breakdown",
        "{price} plus {delivery} delivery",
        "Explains how the delivered price is made up.",
        placeholders=("price", "delivery"),
    ),
    Entry(
        "product.breakdown.free", T, "Price breakdown (free delivery)",
        "{price} with free delivery",
        "Explains the delivered price when delivery is free.",
        placeholders=("price",),
    ),
    Entry(
        "product.delivery.unknown_note", T, "Delivery not known",
        "Delivery charge not confirmed",
        "Instead of the price breakdown when we do not know the shop's delivery charge.",
        placeholders=("price",),
    ),
    Entry(
        "product.delivery.unknown_check", T, "Delivery not known, check",
        "Check the total at the shop before you buy.",
        "Under the main price on a product page when its delivery charge is not known.",
    ),
    Entry(
        "product.badge.best_week", T, "Badge: best in 7 days",
        "Best in 7 days",
        "Green badge when the cheapest price is the lowest recorded in the last 7 days.",
    ),
    Entry(
        "product.badge.soon", T, "Badge: dropping soon",
        "Dropping soon",
        "Badge on a product that is on pre-order at its cheapest shop.",
    ),
    Entry(
        "product.badge.lowest_today", T, "Badge: lowest today",
        "Lowest today",
        "Green badge when the price is the lowest available today but was lower this week.",
    ),
    Entry(
        "product.rank.at", T, "Price at retailer",
        "at {retailer}",
        "Under the big price on product cards.",
        placeholders=("retailer",),
    ),
    Entry(
        "product.last_seen", T, "Last seen price",
        "Last seen at {price}",
        "On a product card when no shop has it in stock: the cheapest delivered price a shop last listed.",
        placeholders=("price",),
    ),
    Entry(
        "product.check_prices", B, "Check prices link",
        "Check prices",
        "Small link on search result cards, opening the full comparison.",
    ),
    Entry(
        "product.buy.button", B, "Main buy button",
        "Buy now",
        "Main button on a product page, linking to the cheapest retailer.",
        placeholders=("price", "retailer"),
    ),
    Entry(
        "product.preorder.button", B, "Main pre-order button",
        "Pre-order",
        "Main button when the cheapest option is a pre-order.",
        placeholders=("price", "retailer"),
    ),
    Entry(
        "product.availability.in_stock.one", T, "Stock summary (one retailer)",
        "1 retailer has this in stock.",
        "Under the main buy button.",
    ),
    Entry(
        "product.availability.in_stock.other", T, "Stock summary (several retailers)",
        "{count} retailers have this in stock.",
        "Under the main buy button.",
        placeholders=("count",),
    ),
    Entry(
        "product.availability.preorder.one", T, "Pre-order summary (one retailer)",
        "1 retailer is taking pre-orders.",
        "Under the main button when nobody has stock but pre-orders are open.",
    ),
    Entry(
        "product.availability.preorder.other", T, "Pre-order summary (several retailers)",
        "{count} retailers are taking pre-orders.",
        "Under the main button when nobody has stock but pre-orders are open.",
        placeholders=("count",),
    ),
    Entry(
        "product.last_checked", T, "Last checked",
        "Last checked {time}.",
        "Under the main buy button. Uses the most recent check across all retailers.",
        placeholders=("time",),
    ),
    Entry(
        "product.out_of_stock.message", T, "Out of stock everywhere",
        "Out of stock at every retailer we check",
        "Shown in place of the main price when nobody has the product.",
    ),
    Entry(
        "product.out_of_stock.last_price", T, "Last known price",
        "Cheapest price when last available: {price} on {date}.",
        "Shown under the out of stock message when we have price history.",
        placeholders=("price", "date"),
    ),
    Entry(
        "product.no_listings", T, "No prices yet",
        "We don't have prices for this product yet.",
        "Shown in place of the main price when no retailer is linked to the product.",
    ),
    Entry(
        "product.compare.title", H, "Price list heading",
        "All prices",
        "Heading above the list of every retailer's price.",
    ),
    Entry("product.compare.col.retailer", T, "Column: retailer", "Retailer",
          "Column heading in the price list (large screens)."),
    Entry("product.compare.col.stock", T, "Column: stock", "Stock",
          "Column heading in the price list (large screens)."),
    Entry("product.compare.col.price", T, "Column: price", "Price",
          "Column heading in the price list (large screens)."),
    Entry("product.compare.col.delivery", T, "Column: delivery", "Delivery",
          "Column heading in the price list (large screens)."),
    Entry("product.compare.col.total", T, "Column: delivered price", "Delivered",
          "Column heading in the price list (large screens)."),
    Entry("product.compare.col.checked", T, "Column: last checked", "Checked",
          "Column heading in the price list (large screens)."),
    Entry(
        "product.compare.cheapest", T, "Cheapest marker",
        "Cheapest",
        "Small marker on the cheapest row in the price list.",
    ),
    Entry(
        "product.compare.free_delivery", T, "Free delivery",
        "Free",
        "Shown in the delivery column when delivery costs nothing.",
    ),
    Entry(
        "product.compare.delivery_unknown", T, "Delivery not known",
        "Not confirmed",
        "Shown in the delivery column when we do not know the charge.",
    ),
    Entry(
        "product.compare.plus_delivery", T, "Total when delivery is not known",
        "{price} + delivery",
        "Shown in the total column when we do not know the delivery charge.",
        placeholders=("price",),
    ),
    Entry(
        "product.compare.shops", T, "Shop count link",
        "Compare {count} shops",
        "Next to the stock line on a product page; jumps to the price list.",
        placeholders=("count",),
    ),
    Entry(
        "product.compare.one_shop", T, "One shop",
        "1 shop found",
        "Next to the stock line when only one shop has a price.",
    ),
    Entry(
        "product.compare.buy", B, "Row button: buy",
        "Buy",
        "Button on each in-stock row in the price list.",
    ),
    Entry(
        "product.compare.preorder", B, "Row button: pre-order",
        "Pre-order",
        "Button on each pre-order row in the price list.",
    ),
    Entry(
        "product.compare.view", B, "Row button: out of stock",
        "View",
        "Link on rows that are out of stock or not checked recently.",
    ),
    Entry(
        "product.compare.checked_ago", T, "Checked time (small screens)",
        "Checked {time}",
        "Shown on each row of the price list on phones.",
        placeholders=("time",),
    ),
    Entry(
        "product.price.kept_out", T, "Price kept out of the comparison",
        "Not counted: far from the other shops' prices.",
        "Shown on a row whose price is so far from the other shops' prices that it is left out of the comparison.",
    ),
    Entry(
        "product.price.kept_out_band", T, "Price kept out: far below its kind",
        "Not counted: far below the usual price for this kind of product.",
        "Shown on a row whose price is left out of the comparison because it is far below every product "
        "of the same kind, when there are too few other shops to compare it with.",
    ),
    Entry(
        "product.compare.stale", T, "Not checked recently",
        "Not checked since {date}",
        "Shown on a row when we have not been able to check that retailer recently.",
        placeholders=("date",),
    ),
    Entry(
        "product.restock.recent.one", T, "Restock line (one)",
        "Back in stock once in the last {days} days, at {retailer} on {date}.",
        "Under the price list, from the restocks we have seen.",
        placeholders=("days", "retailer", "date"),
    ),
    Entry(
        "product.restock.recent.other", T, "Restock line (several)",
        "Back in stock {count} times in the last {days} days, most recently at {retailer} on {date}.",
        "Under the price list, from the restocks we have seen.",
        placeholders=("count", "days", "retailer", "date"),
    ),
    Entry(
        "product.restock.last", T, "Restock line (none recently)",
        "Last back in stock on {date} at {retailer}.",
        "Under the price list when the last restock we saw is older than the window.",
        placeholders=("date", "retailer"),
    ),
    Entry(
        "product.restock.hours", T, "Restock hours",
        "Most restocks landed between {start} and {end}.",
        "Added to the restock line once enough restocks have been seen to show a pattern.",
        placeholders=("start", "end"),
    ),
    Entry(
        "product.compare.unavailable.one", B, "Show unavailable (one)",
        "Show 1 retailer without a current price",
        "Expands the out of stock and out of date rows.",
    ),
    Entry(
        "product.compare.unavailable.other", B, "Show unavailable (several)",
        "Show {count} retailers without a current price",
        "Expands the out of stock and out of date rows.",
        placeholders=("count",),
    ),
    Entry(
        "product.amazon.note", T, "Amazon note",
        "Often in stock with Prime delivery. We do not have Amazon's price yet, so the link opens an Amazon search for it.",
        "Product page, under the Check price on Amazon button in the price box.",
        legal=True,
    ),
    Entry(
        "product.amazon.note_sold_out", T, "Amazon note when sold out",
        "Sold out at the shops we check. Amazon often has it, with Prime delivery. The link opens an Amazon search for it.",
        "Not used since eBay joined the sold-out buttons; kept so edited wording is not lost.",
        legal=True,
    ),
    Entry(
        "product.marketplaces.note_sold_out", T, "Marketplaces note when sold out",
        "Sold out at the shops we check. Amazon and eBay sellers often still have it. The links open a search on each, so check the seller and price before you buy.",
        "Product page price box when no shop has stock, under the Amazon and eBay buttons.",
        legal=True,
    ),
    Entry("product.ebay.button", B, "eBay button", "Search eBay", "Product page price box when no shop has stock. Opens an eBay UK search for new, buy it now listings."),
    Entry(
        "product.amazon.button", B, "Amazon button",
        "Compare on Amazon",
        "Product page price box, under the cheapest price. Shown only when an Amazon tracking "
        "tag is set and we have no Amazon price for the product. Opens Amazon in a new tab.",
    ),
    Entry(
        "product.small_print", T, "Small print under the price list",
        "Affiliate links. Prices can change before you pay.",
        "One line under the price list, followed by the Terms link.",
        legal=True,
    ),
    Entry(
        "product.reload_prices", B, "Reload prices button",
        "Reload prices",
        "Button above the price list on a product page. It re-reads the latest prices we hold; it does not ask the shops.",
    ),
    Entry(
        "product.refresh.done", T, "Prices reloaded message",
        "Prices are current.",
        "Shown for a moment after Reload prices finishes.",
    ),
    Entry(
        "product.refresh.failed", T, "Prices reload failed",
        "Couldn't reload prices. Try again in a moment.",
        "Shown if Reload prices fails.",
    ),
    Entry(
        "product.affiliate_note", T, "Commission note",
        "RipRaptor may earn a commission if you buy through these links. It does not "
        "change the price you pay or the order of this list.",
        "Terms page.",
        legal=True,
    ),
    Entry(
        "product.price_disclaimer", P, "Price disclaimer",
        "Prices and stock can change after you leave RipRaptor. Check the total on "
        "the retailer's site before you pay.",
        "Terms page.",
        legal=True,
    ),
    Entry(
        "product.history.title", H, "Price history heading",
        "Price history",
        "Heading above the price chart.",
    ),
    Entry(
        "product.history.caption", T, "Price history explanation",
        "Cheapest delivered price each day for the last {days} days.",
        "Line under the price history heading.",
        placeholders=("days",),
    ),
    Entry(
        "product.history.month_ago", T, "Price a month ago",
        "A month ago the cheapest price was {price}.",
        "Shown under the price history chart once a product has been tracked for 30 days.",
        placeholders=("price",),
    ),
    Entry(
        "product.history.summary", T, "Price history summary",
        "Lowest {low} on {low_date}. Highest {high} on {high_date}.",
        "Under the price chart. Also read out to screen reader users.",
        placeholders=("low", "low_date", "high", "high_date"),
    ),
    Entry(
        "product.history.empty", T, "No price history",
        "Not enough price history for a chart yet.",
        "Shown in place of the chart for new products.",
    ),
    Entry(
        "product.related.title", H, "Related products heading",
        "More from {set}",
        "Heading above other products from the same set.",
        placeholders=("set",),
    ),
    Entry(
        "product.details.title", H, "Product details heading",
        "Product details",
        "Heading above the game, set and product type.",
    ),
    Entry("product.details.game", T, "Detail: game", "Game", "Label in product details."),
    Entry("product.details.set", T, "Detail: set", "Set", "Label in product details."),
    Entry("product.details.type", T, "Detail: product type", "Product type",
          "Label in product details."),
    Entry("product.details.release_date", T, "Detail: release date", "Release date",
          "Label in product details."),
    Entry("product.details.barcode", T, "Detail: barcode", "Barcode",
          "Label in product details."),

    # How it works page -----------------------------------------------------
    Entry(
        "about.title", H, "Page heading",
        "How RipRaptor works",
        "Main heading on the how it works page. Also used as the page title.",
    ),
    Entry(
        "about.introduction", P, "Introduction",
        "RipRaptor compares UK prices for sealed trading card game products: booster "
        "boxes, Elite Trainer Boxes, bundles, collection boxes and decks.\n\n"
        "For each product we record the price, delivery charge and stock at each "
        "retailer we check, and when we last checked it.",
        "First paragraphs on the how it works page. Leave a blank line between "
        "paragraphs.",
    ),
    Entry(
        "about.prices.title", H, "Prices heading",
        "How prices are worked out",
        "Heading on the how it works page.",
    ),
    Entry(
        "about.prices.body", P, "Prices explanation",
        "The main price you see is the delivered price: the item price plus the "
        "retailer's standard charge to deliver one item to a UK address.\n\n"
        "The cheapest delivered price only includes retailers that have the product in "
        "stock or open for pre-order, and that we have checked in the last {hours} "
        "hours.\n\n"
        "When we do not know a retailer's delivery charge, we show the item price "
        "followed by + delivery. Those prices are listed after confirmed delivered prices, "
        "never count as the cheapest and never show a saving. Check the total at the shop.\n\n"
        "A saving compares the cheapest delivered price with the next cheapest shop. "
        "Very large gaps usually mean two different products were matched, so we do not "
        "show them.\n\n"
        "Discount codes, loyalty points and membership prices are not included.",
        "Explains the delivered price. Leave a blank line between paragraphs.",
        placeholders=("hours",),
    ),
    Entry(
        "about.money.title", H, "Commission heading",
        "How RipRaptor makes money",
        "Heading on the how it works page.",
    ),
    Entry(
        "about.money.body", P, "Commission explanation",
        "Some links to retailers are affiliate links. If you buy something after "
        "following one, the retailer may pay RipRaptor a commission. You pay the same "
        "price either way.\n\n"
        "Price comparisons are always sorted by delivered price, cheapest first. "
        "Retailers cannot pay to change that order.\n\n"
        "The featured deals row on the home page is different: it favours deals at "
        "retailers that pay us a commission. Every featured deal is still a genuine low "
        "price or saving.",
        "Affiliate disclosure on the how it works page.",
        legal=True,
    ),
    Entry(
        "about.retailers.title", H, "Retailers heading",
        "Retailers we check",
        "Heading above the list of retailers. The list itself comes from the Retailer "
        "table.",
    ),
    Entry(
        "about.retailers.intro", T, "Retailers note",
        "Being listed here does not mean a retailer endorses RipRaptor.",
        "Line under the retailers heading. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "about.contact.title", H, "Contact heading",
        "Report a problem",
        "Only shown when a contact email is set in RIPRAPTOR_CONTACT_EMAIL.",
    ),
    Entry(
        "about.contact.body", T, "Contact text",
        "Seen a wrong price or a missing product? Email {email}.",
        "Only shown when a contact email is set in RIPRAPTOR_CONTACT_EMAIL.",
        placeholders=("email",),
    ),

    # Message us -----------------------------------------------------------
    Entry("contact.title", H, "Page heading", "Message us", "Top of the Message us page."),
    Entry(
        "contact.intro", T, "Introduction",
        "Spotted a wrong price, a missing product or a shop we should add? Tell us here. We read every message.",
        "Under the heading on the Message us page.",
    ),
    Entry("contact.form.message", T, "Message label", "Your message", "Label above the message box."),
    Entry("contact.form.name", T, "Name label", "Your name (optional)", "Label above the name box."),
    Entry("contact.form.email", T, "Email label", "Email (optional)", "Label above the email box."),
    Entry(
        "contact.form.email_note", T, "Email note",
        "Only used to tell you we have replied. Leave it blank and your private link shows our reply instead.",
        "Under the email box.",
    ),
    Entry("contact.form.button", B, "Send button", "Send message", "Button that sends the first message."),
    Entry("contact.form.empty", T, "Empty message", "Write a message first.", "Shown when the message box is empty."),
    Entry("contact.form.invalid", T, "Bad email", "That email address does not look right.", "Shown when the email address is not valid."),
    Entry("contact.form.busy", T, "Too many messages", "We have had a lot of messages just now. Try again in an hour.", "Shown when the flood limit is reached."),
    Entry("contact.continue", T, "Open conversation note", "You already have a conversation with us.", "On the Message us page when this browser has one."),
    Entry("contact.continue.link", B, "Open conversation link", "Open your conversation", "Link next to the note above."),
    Entry("contact.thread.title", H, "Conversation heading", "Your conversation", "Top of a visitor's private conversation page."),
    Entry(
        "contact.thread.intro", T, "Conversation introduction",
        "This page's link is private to you. Our replies appear here, and this browser remembers the page.",
        "Under the heading on the conversation page.",
    ),
    Entry("contact.thread.you", T, "Visitor label", "You", "Above the visitor's messages."),
    Entry("contact.thread.us", T, "Our label", "RipRaptor", "Above replies."),
    Entry("contact.thread.waiting", T, "Waiting note", "Our reply will appear here.", "When the last message is the visitor's."),
    Entry("contact.thread.email_on", T, "Email note", "We will also email {email} when we reply.", "When the visitor left an email address.", placeholders=("email",)),
    Entry("contact.thread.sent", T, "Sent note", "Sent. Our reply will appear on this page.", "After a message is sent."),
    Entry("contact.thread.more", T, "Follow-up label", "Add a message", "Label above the box for a follow-up."),
    Entry("contact.thread.button", B, "Follow-up button", "Send", "Button that sends a follow-up."),
    Entry("contact.thread.busy", T, "Follow-up limit", "That is a lot of messages in an hour. Try again later.", "When a thread hits the hourly limit."),
    Entry("contact.thread.closed", T, "Closed note", "This conversation is closed. Start a new one if you need us.", "When the owner has closed the thread."),
    Entry("contact.thread.new", B, "New conversation link", "Start a new conversation", "On a closed conversation."),
    Entry("contact.thread.forget", T, "Delete email question", "Stop emails to {email} and delete the address? Replies still show on this page.", "After the Unsubscribe link in a reply email.", placeholders=("email",)),
    Entry("contact.thread.forget.button", B, "Delete email button", "Delete my email address", "Button under the question above."),
    Entry("contact.thread.forgot", T, "Email deleted note", "Your email address has been deleted. Replies still show on this page.", "After the email address is deleted."),

    # Footer ---------------------------------------------------------------
    Entry(
        "footer.line", T, "Footer line",
        "Prices include UK delivery. RipRaptor may earn a commission when you buy, and featured deals favour shops that pay one.",
        "The one line in the footer of every page, followed by the Terms link.",
        legal=True,
    ),
    Entry(
        "footer.affiliate_disclosure", T, "Affiliate disclosure",
        "RipRaptor may earn a commission if you buy through links on this site. "
        "This does not change the price you pay.",
        "Terms page.",
        legal=True,
    ),
    Entry(
        "footer.independence_disclaimer", T, "Independence statement",
        "RipRaptor is independent and is not endorsed by any retailer or game "
        "publisher. Product names and trademarks belong to their owners.",
        "Terms page.",
        legal=True,
    ),

    # Deals page ------------------------------------------------------------
    Entry(
        "deals.title", H, "Deals page heading",
        "Today's best sealed TCG deals in the UK",
        "Heading of the deals page.",
    ),
    Entry(
        "deals.intro", T, "Deals page introduction",
        "The biggest gaps between the cheapest shop and the next one, the biggest price drops this "
        "week and what just came back in stock. Delivered prices, checked {time}.",
        "Under the deals page heading.",
        placeholders=("time",),
    ),
    Entry("deals.savings.title", H, "Deals savings heading", "Biggest savings right now", "Deals page section heading."),
    Entry("deals.drops.title", H, "Deals drops heading", "Biggest price drops", "Deals page section heading."),
    Entry("deals.restock.title", H, "Deals restock heading", "Back in stock", "Deals page section heading."),
    Entry(
        "deals.restock.note", T, "Restock log explanation",
        "Every time a shop we check went from sold out to in stock in the last 7 days. Marketplaces are left out.",
        "Under the Back in stock heading on the deals page.",
    ),
    Entry(
        "deals.restock.again", T, "Sold out again marker",
        "sold out again",
        "After a restock in the log whose shop has since sold out.",
    ),
    Entry("watchlist.title", H, "Watchlist heading", "Your watchlist", "Heading of the watchlist page."),
    Entry(
        "watchlist.intro", T, "Watchlist introduction",
        "Live prices for the products you saved.",
        "Under the watchlist heading.",
    ),
    Entry("watchlist.save", B, "Save button", "Save", "On product pages and cards. Adds the product to the watchlist."),
    Entry("watchlist.saved", B, "Saved state", "Saved", "The Save button once the product is on the watchlist."),
    Entry("watchlist.remove", B, "Remove button", "Remove", "On the watchlist page. Takes the product off the list."),
    Entry("watchlist.open", B, "Open watchlist link", "Open watchlist", "On the swipe page, above the saved products."),
    Entry(
        "watchlist.saved_at", T, "Saved at price",
        "Saved at {price}",
        "On the watchlist page, the delivered price when the product was saved. Green when it is cheaper now, red when dearer.",
        placeholders=("price",),
    ),
    Entry(
        "watchlist.now", T, "Current price shop",
        "delivered at {retailer}",
        "After the current price on the watchlist page.",
        placeholders=("retailer",),
    ),
    Entry(
        "watchlist.device_note", T, "Saved on this device",
        "Saved on this device only.",
        "Small print on the watchlist page.",
    ),
    Entry(
        "watchlist.share", T, "Address holds the list",
        "This page's address holds your list, so bookmark it or paste it into a chat to keep it or share it.",
        "Small print on the watchlist page.",
    ),
    Entry("watchlist.empty.title", H, "Empty watchlist heading", "Nothing saved yet", "Heading when the watchlist is empty."),
    Entry(
        "watchlist.empty.body", T, "Empty watchlist text",
        "Press Save on any product and it will show here with its current price. The list stays in this browser only.",
        "Text when the watchlist is empty.",
    ),
    Entry("watchlist.empty.button", B, "Empty watchlist button", "See today's deals", "Button when the watchlist is empty."),
    Entry("alerts.form.title", T, "Alert form heading", "Email me when it is back in stock", "Above the email box on a product no shop has in stock."),
    Entry("alerts.form.label", T, "Alert email label", "Your email address", "Label for the email box, read out by screen readers."),
    Entry("alerts.form.placeholder", T, "Alert email hint", "you@example.com", "Grey hint in the email box."),
    Entry("alerts.form.button", B, "Alert button", "Email me", "Button that asks for the alert."),
    Entry(
        "alerts.form.note", T, "Alert privacy note",
        "One email when a shop has it, then your address is deleted. We use it for nothing else.",
        "Under the email box.",
        legal=True,
    ),
    Entry("alerts.form.sent", T, "Alert asked", "Check your inbox and confirm, then we will email you when it is back.", "Shown after the form is sent."),
    Entry("alerts.form.already", T, "Alert already set", "You already have an alert for this product.", "Shown when the address is already waiting for it."),
    Entry("alerts.form.limit", T, "Alert limit", "That address is waiting on 30 products already.", "Shown when one address asks for too many."),
    Entry("alerts.form.invalid", T, "Alert bad address", "That email address does not look right.", "Shown when the address is not valid."),
    Entry("alerts.form.failed", T, "Alert send failed", "We could not send the confirmation just now. Try again in a minute.", "Shown when the email service fails."),
    Entry("alerts.confirmed.title", H, "Alert confirmed heading", "Alert confirmed", "Page after the confirm link."),
    Entry(
        "alerts.confirmed.body", T, "Alert confirmed text",
        "We will email you once when {product} is back in stock at a UK shop we check, then delete your address.",
        "Page after the confirm link.",
        placeholders=("product",),
    ),
    Entry("alerts.stopped.title", H, "Alert stopped heading", "Alert stopped", "Page after the stop link."),
    Entry("alerts.stopped.body", T, "Alert stopped text", "Your alert and your email address have been deleted.", "Page after the stop link."),
    Entry("alerts.gone.title", H, "Alert gone heading", "This link has expired", "Page for a confirm link that no longer works."),
    Entry(
        "alerts.gone.body", T, "Alert gone text",
        "The alert was sent, stopped or expired. Ask again on the product page if you still want one.",
        "Page for a confirm link that no longer works.",
    ),
    Entry("alerts.done.back", B, "Back to product button", "Back to the product", "On the alert confirm and stop pages."),
    Entry(
        "feeds.deals.title", T, "Site feed title",
        "{site_name}: restocks and price drops",
        "Title of the site-wide RSS feed, shown in feed readers and Discord feed bots.",
        placeholders=("site_name",),
    ),
    Entry(
        "feeds.deals.description", T, "Site feed description",
        "Sealed trading card products back in stock or cheaper at UK shops, with the delivered price, as we see them.",
        "Description of the site-wide RSS feed.",
    ),
    Entry(
        "feeds.game.title", T, "Game feed title",
        "{site_name}: {game} restocks and price drops",
        "Title of one game's RSS feed.",
        placeholders=("site_name", "game"),
    ),
    Entry(
        "feeds.game.description", T, "Game feed description",
        "Sealed {game} products back in stock or cheaper at UK shops, with the delivered price, as we see them.",
        "Description of one game's RSS feed.",
        placeholders=("game",),
    ),
    Entry(
        "feeds.restock.title", T, "Feed restock entry title",
        "Back in stock: {product}, {price} at {retailer}",
        "Title of a restock in the feed.",
        placeholders=("product", "price", "retailer"),
    ),
    Entry(
        "feeds.restock.body", T, "Feed restock entry text",
        "Back in stock at {retailer} on {date} at {time}, {price} delivered.",
        "Text of a restock in the feed.",
        placeholders=("retailer", "date", "time", "price"),
    ),
    Entry(
        "feeds.restock.body_unknown", T, "Feed restock entry text (delivery unknown)",
        "Back in stock at {retailer} on {date} at {time}, {price} plus delivery.",
        "Text of a restock in the feed when the shop's delivery charge is not known.",
        placeholders=("retailer", "date", "time", "price"),
    ),
    Entry(
        "feeds.drop.title", T, "Feed price drop entry title",
        "Price drop: {product}, now {price} delivered, was {was}",
        "Title of a price drop in the feed.",
        placeholders=("product", "price", "was"),
    ),
    Entry(
        "feeds.drop.body", T, "Feed price drop entry text",
        "The cheapest delivered price fell from {was} to {price} on {date}.",
        "Text of a price drop in the feed.",
        placeholders=("was", "price", "date"),
    ),
    Entry(
        "feeds.deals.link", T, "Feed link on the deals page",
        "Follow restocks and price drops in a feed reader or a Discord feed bot:",
        "Small print on the deals page, followed by the feed address.",
    ),
    Entry(
        "deals.empty", T, "Deals page empty",
        "No deals to show yet. Prices are checked every hour, so try again soon.",
        "Shown when there is nothing on the deals page.",
    ),
    Entry(
        "meta.deals.title", T, "Deals page title",
        "Cheapest TCG booster box and ETB deals in the UK today",
        "Browser tab title for the deals page. The site name is added after it.",
    ),
    Entry(
        "meta.deals.description", T, "Deals page description",
        "Today's biggest savings on sealed Pokemon, Magic, One Piece and Yu-Gi-Oh products across UK shops, "
        "with delivered prices checked every hour.",
        "Search engine description for the deals page.",
    ),

    # Terms page ------------------------------------------------------------
    Entry(
        "terms.title", H, "Terms heading",
        "Terms",
        "Heading and page title of the terms page.",
    ),
    Entry(
        "terms.introduction", P, "Terms introduction",
        "RipRaptor is a price comparison service. We do not sell anything. When you "
        "buy, you buy from the retailer under their terms.",
        "First paragraph on the terms page.",
        legal=True,
    ),
    Entry(
        "terms.amazon", T, "Amazon disclosure",
        "As an Amazon Associate, RipRaptor earns from qualifying purchases.",
        "Terms page, shown while Amazon links carry our tag. Amazon requires this wording.",
        legal=True,
    ),
    Entry("terms.prices.title", H, "Prices heading", "Prices", "Heading on the terms page."),
    Entry("terms.independence.title", H, "Independence heading", "Independence", "Heading on the terms page."),
    Entry("terms.data.title", H, "Data heading", "Your data", "Heading on the terms page."),
    Entry(
        "terms.data.body", P, "Data explanation",
        "RipRaptor does not ask you to create an account. Products you save, "
        "products you view and searches you make are stored in your browser "
        "only, and you can clear them from the home page. We count clicks to retailers without "
        "recording who clicked. One small cookie remembers only that your browser "
        "has visited before, so we can count returning visitors. It holds nothing "
        "about you.\n\n"
        "If you ask to be emailed when a product is back in stock, we keep your email "
        "address for that alone. Nothing is sent until you confirm by the link we email "
        "you. You get one email when a shop has the product, and your address is then "
        "deleted. Unconfirmed requests are deleted after a week and any still waiting "
        "after six months are deleted too. Every email has a link that deletes your "
        "request at once. The emails are sent through Zoho ZeptoMail.\n\n"
        "If you message us, we keep your message, and your name and email address if you "
        "give them, to answer you and for nothing else. Your conversation is deleted six "
        "months after its last message. A cookie in your browser remembers the private "
        "link to your conversation.",
        "Data section of the terms page.",
        legal=True,
    ),

    # Error pages ----------------------------------------------------------
    Entry(
        "errors.404.title", H, "Page not found heading",
        "Page not found",
        "Heading on the page shown for a broken link.",
    ),
    Entry(
        "errors.404.body", P, "Page not found text",
        "The link may be wrong, or the product may no longer be listed. Try searching "
        "for it instead.",
        "Text on the page shown for a broken link, above a search box.",
    ),

    # Page titles and descriptions ----------------------------------------
    Entry(
        "meta.home.title", T, "Home page title",
        "{site_name}: compare UK prices for sealed TCG products",
        "Browser tab and search engine title for the home page.",
        placeholders=("site_name",),
    ),
    Entry(
        "meta.default.description", T, "Default description",
        "Compare delivered prices for booster boxes, Elite Trainer Boxes, bundles and "
        "decks from UK retailers.",
        "Search engine description for pages without their own.",
    ),
    Entry(
        "meta.product.title", T, "Product page title",
        "{product}: UK prices",
        "Browser tab title for product pages. The site name is added after it.",
        placeholders=("product",),
    ),
    Entry(
        "meta.product.description", T, "Product page description",
        "Compare delivered prices for {product} from UK retailers, with stock and the "
        "time each price was checked.",
        "Search engine description for product pages.",
        placeholders=("product",),
    ),
    Entry(
        "meta.product.title_priced", T, "Product page title with a price",
        "{product} from {price} delivered: UK price comparison",
        "Browser tab title for product pages that have a current price. The site name is added after it.",
        placeholders=("product", "price", "retailer", "count"),
    ),
    Entry(
        "meta.product.description_priced", T, "Product page description with a price",
        "Cheapest {product} today is {price} delivered at {retailer}. Compare {count} UK shops with "
        "stock, delivery and the time each price was checked.",
        "Search engine description for product pages that have a current price.",
        placeholders=("product", "price", "retailer", "count"),
    ),
    Entry(
        "meta.watchlist.title", T, "Watchlist page title",
        "Your watchlist",
        "Browser tab title for the watchlist page. The site name is added after it.",
    ),
    Entry(
        "meta.new.title", T, "Latest drops page title",
        "Latest TCG drops and new releases: UK prices",
        "Browser tab title for the latest drops page. The site name is added after it.",
    ),
    Entry(
        "meta.new.description", T, "Latest drops page description",
        "New and upcoming sealed Pokemon, Magic, One Piece, Yu-Gi-Oh and Lorcana products with the "
        "cheapest UK delivered price, updated every hour.",
        "Search engine description for the latest drops page.",
    ),
    Entry(
        "meta.game.title", T, "Game page title",
        "{game} sealed product prices",
        "Browser tab title for game pages. The site name is added after it.",
        placeholders=("game",),
    ),
    Entry(
        "meta.game.description", T, "Game page description",
        "Compare UK prices for {game} booster boxes, bundles and other sealed products.",
        "Search engine description for game pages.",
        placeholders=("game",),
    ),
    Entry(
        "meta.set.title", T, "Set page title",
        "{set} sealed product prices",
        "Browser tab title for set pages. The site name is added after it.",
        placeholders=("set", "game"),
    ),
    Entry(
        "meta.set.description", T, "Set page description",
        "Compare UK prices for {game} {set} booster boxes, bundles and other sealed "
        "products.",
        "Search engine description for set pages.",
        placeholders=("set", "game"),
    ),
]

REGISTRY = {entry.key: entry for entry in ENTRIES}
