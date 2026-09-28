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
    ("home", "Home page"),
    ("browse", "Search results and browsing"),
    ("product", "Product page"),
    ("about", "How it works page"),
    ("footer", "Footer"),
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
    "site_name": "the site name, CardScout",
    "time": "how long ago, for example 18 minutes ago",
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
        "site.nav.games", B, "Games link",
        "Games",
        "Header link to the list of games and sets.",
    ),
    Entry(
        "site.nav.about", B, "How it works link",
        "How it works",
        "Header and footer link to the page that explains prices and commission.",
    ),

    # Home page ------------------------------------------------------------
    Entry(
        "home.hero.title", H, "Main heading",
        "Compare UK prices for sealed TCG products",
        "Largest heading at the top of the home page, above the search box.",
    ),
    Entry(
        "home.hero.subtitle", T, "Text under the main heading",
        "Booster boxes, Elite Trainer Boxes, bundles and decks from UK retailers. "
        "Prices include delivery, so you can compare like for like.",
        "One or two sentences under the main heading. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "home.search.button", B, "Home search button",
        "Search products",
        "Button next to the large search box on the home page.",
    ),
    Entry(
        "home.search.checked", T, "Prices last checked",
        "Prices last checked {time}.",
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
        "home.games.title", H, "Recent sets heading",
        "Recent sets",
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

    # Product page ----------------------------------------------------------
    Entry(
        "product.cheapest.label", T, "Cheapest price label",
        "Cheapest delivered price",
        "Small label above the main price on a product page.",
    ),
    Entry(
        "product.cheapest.retailer", T, "Cheapest retailer",
        "at {retailer}",
        "Shown next to the main price.",
        placeholders=("retailer",),
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
        "product.buy.button", B, "Main buy button",
        "Buy for {price}",
        "Main button on a product page, linking to the cheapest retailer.",
        placeholders=("price", "retailer"),
    ),
    Entry(
        "product.preorder.button", B, "Main pre-order button",
        "Pre-order for {price}",
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
        "product.compare.stale", T, "Not checked recently",
        "Not checked since {date}",
        "Shown on a row when we have not been able to check that retailer recently.",
        placeholders=("date",),
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
        "product.affiliate_note", T, "Commission note",
        "CardScout may earn a commission if you buy through these links. It does not "
        "change the price you pay or the order of this list.",
        "Under the price list on every product page.",
        legal=True,
    ),
    Entry(
        "product.price_disclaimer", P, "Price disclaimer",
        "Prices and stock can change after you leave CardScout. Check the total on "
        "the retailer's site before you pay.",
        "Under the price list on every product page.",
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
        "How CardScout works",
        "Main heading on the how it works page. Also used as the page title.",
    ),
    Entry(
        "about.introduction", P, "Introduction",
        "CardScout compares UK prices for sealed trading card game products: booster "
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
        "Discount codes, loyalty points and membership prices are not included.",
        "Explains the delivered price. Leave a blank line between paragraphs.",
        placeholders=("hours",),
    ),
    Entry(
        "about.money.title", H, "Commission heading",
        "How CardScout makes money",
        "Heading on the how it works page.",
    ),
    Entry(
        "about.money.body", P, "Commission explanation",
        "Some links to retailers are affiliate links. If you buy something after "
        "following one, the retailer may pay CardScout a commission. You pay the same "
        "price either way.\n\n"
        "Retailers cannot pay to be listed higher. Prices are always sorted by delivered "
        "price, cheapest first.",
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
        "Being listed here does not mean a retailer endorses CardScout.",
        "Line under the retailers heading. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "about.contact.title", H, "Contact heading",
        "Report a problem",
        "Only shown when a contact email is set in CARDSCOUT_CONTACT_EMAIL.",
    ),
    Entry(
        "about.contact.body", T, "Contact text",
        "Seen a wrong price or a missing product? Email {email}.",
        "Only shown when a contact email is set in CARDSCOUT_CONTACT_EMAIL.",
        placeholders=("email",),
    ),

    # Footer ---------------------------------------------------------------
    Entry(
        "footer.affiliate_disclosure", T, "Affiliate disclosure",
        "CardScout may earn a commission if you buy through links on this site. "
        "This does not change the price you pay.",
        "Footer of every page.",
        legal=True,
    ),
    Entry(
        "footer.independence_disclaimer", T, "Independence statement",
        "CardScout is independent and is not endorsed by any retailer or game "
        "publisher. Product names and trademarks belong to their owners.",
        "Footer of every page.",
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
