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
    ("new", "Coming soon sets"),
    ("drops", "Drop tracker and drop reminders"),
    ("deals", "Deals page"),
    ("feeds", "Feeds (RSS)"),
    ("discord", "Discord posts"),
    ("alerts", "Back in stock emails"),
    ("watchlist", "Watchlist page"),
    ("browse", "Search results and browsing"),
    ("product", "Product page"),
    ("info", "Info cards (the ⓘ buttons)"),
    ("about", "How it works page"),
    ("contact", "Message us page"),
    ("refer", "Refer a friend keyring offer"),
    ("listshop", "List your shop page"),
    ("footer", "Footer"),
    ("terms", "Terms page"),
    ("errors", "Error pages"),
    ("meta", "Page titles and search engine descriptions"),
    ("insights", "Insights page in admin (owner only)"),
    ("dashboard", "Dashboard page (owner only)"),
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
    "source": "where a date came from, for example Scryfall",
    "site_name": "the site name, RipRaptor",
    "time": "how long ago, for example 18 minutes ago",
    "was": "the previous price, for example £59.99",
    "start": "a time of day, for example 9am",
    "end": "a time of day, for example 11am",
    "now": "a number for the period being explained",
    "then": "the number it is compared with",
    "diff": "the difference between the two numbers",
    "percent": "a percentage",
    "share": "a percentage of the change",
    "name": "a source, page, device or country",
    "period": "the period being explained, for example Today to 13:13",
    "basis": "what it is compared with, for example by the same time last Sunday",
    "weekday": "a day of the week, for example Sunday",
    "visitors": "a number of visitors",
    "views": "a number of page views",
    "clicks": "a number of clicks",
    "expected": "the number usually seen",
    "reason": "why a day looks automated",
    "names": "a list of shop names or days",
    "total": "the whole change",
    "other": "the group that moved the other way",
    "other_diff": "how far that group moved",
    "first": "the first whole day with arrival times",
    "day": "a day, for example Sat 17 Oct",
    "when": "a time and day, for example 14:05, 18 Oct",
    "link": "the referrer's link to share",
    "types": "the product types in stock, for example booster boxes and ETBs",
    "products": "a number of products, for example 412 sealed products",
    "shops": "a number of shops, for example 18 UK shops",
    "code": "the set's code, for example OP-18 or EB05",
    "clock": "a time of day, for example 14:05, or a day and time, for example 18 Oct 14:05",
    "spent": "an amount in US dollars, for example $12.40",
    "limit": "an amount in US dollars, for example $100.00",
    "address": "the visitor's own email address",
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
    Entry("site.consent.label", T, "Analytics card name", "Analytics cookies", "Name of the analytics card for screen readers."),
    Entry(
        "site.consent.text", T, "Analytics question",
        "Can we use Google Analytics cookies to see which pages help? The site works the same either way.",
        "The card asking each visitor before Google Analytics loads. Shown until they answer, and again after six months.",
        legal=True,
    ),
    Entry("site.consent.more", B, "Analytics details link", "How we use it", "Link on the analytics card to the data section of the terms page."),
    Entry("site.consent.allow", B, "Allow analytics button", "Allow analytics", "On the analytics card. Loads Google Analytics."),
    Entry("site.consent.refuse", B, "Refuse analytics button", "No analytics", "On the analytics card. Google Analytics never loads."),
    Entry(
        "site.nav.consent", B, "Analytics choice link",
        "Analytics cookies",
        "Footer button that shows the analytics card again, so a visitor can change their answer.",
    ),
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
    Entry("site.nav.listshop", B, "List your shop link", "List your shop", "Footer link to the List your shop page, and its button on the how it works page."),
    Entry(
        "site.nav.terms", B, "Terms link",
        "Terms",
        "Footer link to the terms page.",
    ),
    Entry(
        "site.nav.privacy", B, "Privacy link",
        "Privacy",
        "Footer link to the Your data part of the terms page, the site's privacy policy.",
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
        "site.nav.drops", B, "Drops link",
        "Drops",
        "Header and footer link to the drop tracker.",
    ),
    Entry(
        "site.nav.about", B, "How it works link",
        "How it works",
        "Header and footer link to the page that explains prices and commission.",
    ),

    # Home page ------------------------------------------------------------
    Entry(
        "home.hero.eyebrow", T, "Line above the headline",
        "UK price comparison",
        "Small capitals above the home page headline. Leave empty to hide it.",
        optional=True,
    ),
    Entry(
        "home.hero.title", H, "Home page headline",
        "Bad prices. Binned.",
        "The headline at the top of the home page, above the search box, on one line with its last sentence in "
        "orange; one sentence is not split. Keep it to four or five short words so it fits a phone on one line.",
    ),
    Entry(
        "home.hero.subtitle", T, "Line under the headline",
        "Sealed TCG prices from UK shops, delivery included. Not just the basket price.",
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
        "Search sets, boxes and packs",
        "Grey hint text inside the large search box on the home page.",
    ),
    Entry(
        "home.search.checked", T, "Latest price check",
        "Latest shop price check {time}",
        "Under the home page search box. The most recent time any shop's price was checked; each product "
        "page says when its own prices were. Hidden until a price has been checked.",
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
        "home.games.label", T, "Game tiles heading",
        "Find your game",
        "Heading above the game tiles under the home page search box, and the label before the game links "
        "when a search finds nothing.",
    ),
    Entry(
        "home.card.compare", B, "Card button",
        "Compare prices",
        "Button at the foot of every product card in the home and deals page rows that has a price.",
    ),
    Entry(
        "home.card.view", B, "Card button with no price",
        "See product",
        "Button at the foot of a recently viewed card when no shop has a price for it.",
    ),
    Entry(
        "home.watch.title", H, "Watchlist card heading",
        "Keep an eye on it.",
        "Big capitals on the dark home page card that leads to the watchlist, beside the raptor. A second sentence "
        "goes on its own line. Keep it short: it wraps at a few words on a phone.",
    ),
    Entry(
        "home.watch.body", T, "Watchlist card text",
        "Save products. Watch the price.",
        "One line under the watchlist card heading.",
    ),
    Entry("home.watch.button", B, "Watchlist card button", "View watchlist", "The button on the watchlist card."),
    Entry(
        "home.dropped.title", H, "Deals panel heading",
        "Prices. Dropped.",
        "Big capitals on the orange home page panel that leads to the deals page, beside the raptor. The last "
        "sentence goes on its own line. Keep each sentence to a word or two.",
    ),
    Entry(
        "home.dropped.body", T, "Deals panel text",
        "Find your next rip for less.",
        "One line under the deals panel heading.",
    ),
    Entry("home.dropped.button", B, "Deals panel button", "Explore deals", "The button on the deals panel."),
    Entry(
        "home.types.title", H, "Browse by product heading",
        "Browse by product",
        "Heading for the product type tiles on the home page.",
    ),
    Entry(
        "home.games.more", B, "More games link",
        "More games",
        "Link beside the Find your game heading, leading to the full list of games.",
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
        "home.new.title", H, "Drops heading",
        "Drops",
        "Home page heading above the Coming soon rows and the Out now row. Its See all link opens the drops page.",
    ),
    Entry(
        "home.new.released", T, "Released date",
        "Released {date}",
        "On a latest-drops card that is out. {date} is the release date.",
        placeholders=("date",),
    ),
    Entry(
        "home.trending.title", H, "Trending heading",
        "Trending now",
        "Heading on the deals page for the in-stock products most clicked through to a shop this week, or the "
        "newest releases until there are clicks.",
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
        "Green label on featured deals: the saving and the next cheapest shop it is measured against. Only shown "
        "when that shop's price is in line with the market (catalogue/offers.py), never against one dear listing.",
        placeholders=("amount", "retailer"),
    ),
    Entry(
        "home.savings.percent", T, "Saving percentage",
        "{percent}% cheaper",
        "Under the saving amount.",
        placeholders=("percent",),
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
        "home.resume.title", H, "Pick up heading",
        "Pick up where you left off",
        "Home page section with this browser's recent searches and viewed products. Hidden until there are some.",
    ),
    Entry("home.resume.searches", T, "Recent searches label", "Recent searches", "Label in the pick up section."),
    Entry("home.resume.viewed", T, "Recently viewed label", "Recently viewed", "Label in the pick up section."),
    Entry("home.resume.clear", B, "Clear history button", "Clear history", "Forgets the recent searches, viewed products and games browsed in this browser."),
    Entry(
        "home.resume.note", T, "Pick up note",
        "Kept in this browser only.",
        "Small note under the pick up section.",
    ),
    Entry(
        "home.picks.title", H, "Picked for you heading", "Picked for you",
        "Top home page row of in-stock products like the ones this browser viewed, saved or searched for. Hidden "
        "until there are some.",
    ),
    Entry(
        "home.picks.note", T, "Picked for you note", "Like what you have looked at, best deals first.",
        "Small note under the Picked for you heading.",
    ),
    Entry(
        "home.picks.hot", T, "Too good to miss label", "Too good to miss",
        "Green label on a pick with a saving of 15% or more on a next shop whose price is in line with the "
        "market, or a verified 30-day low clearly under its average.",
    ),
    Entry("home.featured.title", H, "Featured deals heading", "Featured deals", "Home page row near the top."),
    Entry(
        "home.featured.disclosure", T, "Featured deals disclosure",
        "Featured deals favour shops that pay us a commission. Price comparisons are always cheapest delivered "
        "first, and no shop can pay to move up.",
        "Under the Featured deals heading on the home page, followed by a How it works link. Keep it true to how "
        "the row is chosen (web/views.py featured_pick).",
        legal=True,
    ),
    Entry(
        "home.featured.disclosure_paying", T, "Featured deals disclosure, paying shops only",
        "Featured deals come only from shops that pay us a commission. Price comparisons are always cheapest "
        "delivered first, and no shop can pay to move up.",
        "Replaces the Featured deals disclosure while Featured deals settings has Only shops that pay ticked.",
        legal=True,
    ),
    Entry(
        "home.featured.note", T, "Featured deals note",
        "Every featured deal has evidence behind it: its lowest delivered price in 30 days by our own daily record and clearly under its average, or a saving of at least 5% on the next shop when that shop's price is in line with the market. One unusually dear listing never makes a saving. We favour shops that pay us a commission when choosing them, and each product page still shows every shop's price. Deals for games you have looked at come first, worked out in your browser.",
        "On the terms page, under Independence. Says how the home page's featured deals row is chosen.",
        legal=True,
    ),
    Entry(
        "home.featured.note_paying", T, "Featured deals note, paying shops only",
        "Every featured deal has evidence behind it: its lowest delivered price in 30 days by our own daily record and clearly under its average, or a saving of at least 5% on the next shop when that shop's price is in line with the market. One unusually dear listing never makes a saving. Featured deals come only from shops that pay us a commission, and each product page still shows every shop's price. Deals for games you have looked at come first, worked out in your browser.",
        "Replaces the Featured deals note on the terms page while Featured deals settings has Only shops that "
        "pay ticked.",
        legal=True,
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
        "browse.no_results.title", H, "No results line",
        "No products match ‘{query}’",
        "Under ‘No luck. Yet.’ when a search finds nothing, and the message in the quick results as you type.",
        placeholders=("query",),
    ),
    Entry(
        "browse.no_results.body", P, "No results help",
        "Try the set name or a shorter search.",
        "Help text on the no results card, above the Browse games button and links to each game.",
    ),
    Entry(
        "browse.no_results.headline", H, "No results heading",
        "No luck. Yet.",
        "Big capitals beside the puzzled raptor when a search finds nothing.",
    ),
    Entry("browse.no_results.button", B, "No results button", "Browse games", "The button on the no results card, to the list of games."),
    Entry(
        "browse.no_filtered_results", T, "No results with filters",
        "Nothing matches these filters.",
        "Shown when filters such as game or product type rule out every product.",
    ),
    Entry(
        "new.coming_soon.title", H, "Coming soon heading",
        "Coming soon",
        "Heading of the list of announced sets still to come, on the drops page, the home page and game pages.",
    ),
    Entry(
        "new.coming_soon.out", T, "Coming soon release date",
        "Out {date}",
        "Next to a set still to come, and on its set page. {date} is the release date: the publisher's, two "
        "sources that agree, or yours.",
        placeholders=("date",),
    ),
    Entry(
        "new.coming_soon.tbc", T, "Coming soon with no date",
        "Date not announced yet",
        "On the page of a set shops take pre-orders for before any date is published.",
    ),
    Entry(
        "new.coming_soon.no_preorders", T, "No pre-orders yet",
        "No pre-orders yet",
        "Next to a set still to come that no shop takes pre-orders for. Its button is Remind me.",
    ),
    Entry(
        "new.coming_soon.in_stock", T, "Coming soon set already in stock",
        "In stock from {price} at {retailer}",
        "Next to a set still to come that a shop already sells from stock, before its published date, and no "
        "shop takes pre-orders for. {price} is the cheapest and its button is Buy now.",
        placeholders=("price", "retailer"),
    ),
    Entry(
        "new.coming_soon.preorders_from.one", T, "Pre-order price (one shop)",
        "Pre-orders from {price} at 1 shop",
        "Next to a set still to come that one shop takes pre-orders for, on the drops page too. {price} is "
        "its cheapest pre-order and links to that product. The Pre-order button beside it goes to that shop.",
        placeholders=("price",),
    ),
    Entry(
        "new.coming_soon.preorders_from.other", T, "Pre-order price (more than one shop)",
        "Pre-orders from {price} at {count} shops",
        "Next to a set still to come that several shops take pre-orders for, on the drops page too. {price} "
        "is the cheapest pre-order and links to that product. The Pre-order button beside it goes to that shop.",
        placeholders=("price", "count"),
    ),
    Entry(
        "new.coming_soon.source", T, "Where a set's date came from",
        "Date from {source}",
        "On the page of a set still to come, after its date. {source} is the release source that gave the "
        "date. Not shown for a date you set.",
        placeholders=("source",),
    ),
    Entry(
        "new.coming_soon.checked", T, "Coming soon price checked",
        "checked {time}",
        "After the price on each Coming soon row. {time} is how long ago the shop's price was read.",
        placeholders=("time",),
    ),

    # Drop tracker ---------------------------------------------------------
    Entry("drops.title", H, "Drops heading", "Drops", "Heading of the drops page, /drops/."),
    Entry(
        "drops.intro", T, "Drops introduction",
        "New sealed sets with the cheapest UK shop price. Pre-order what is coming, or ask us to remind you.",
        "Under the drops heading.",
    ),
    Entry("drops.tabs.label", T, "Drops sections name", "Coming soon or out now", "Read out by screen readers for the two section buttons on the drops page."),
    Entry("drops.tab.soon", B, "Coming soon button", "Coming soon", "First section button on the drops page: sets still to come. Shows its count."),
    Entry("drops.tab.out", B, "Out now button", "Out now", "Second section button on the drops page: products just out and in stock. Shows its count."),
    Entry(
        "drops.out.intro", T, "Out now introduction",
        "In stock now: products released in the last few weeks or just listed by UK shops.",
        "Above the products under Out now.",
    ),
    Entry("drops.out.empty", H, "Nothing out now heading", "Nothing new in stock right now", "Under Out now when no new product is in stock."),
    Entry("drops.filter.toggle", B, "Sort and filter button", "Sort and filter", "Opens the sort and filter choices under Out now."),
    Entry("drops.group.undated", H, "No date yet heading", "Date not announced yet", "Last group under Coming soon: sets UK shops take pre-orders for before any date is published."),
    Entry("drops.offer.stock", T, "In stock price on a drop", "In stock from {price}", "On a drop a UK shop already sells from stock and none takes pre-orders for. Its button is Buy now.", placeholders=("price",)),
    Entry("drops.offer.checked", T, "Cheapest product, shop and checked time", "{product} at {retailer}, checked {time}", "Under the price on each drop: what the main button buys and where.", placeholders=("product", "retailer", "time")),
    Entry("drops.remind.open", B, "Remind me button", "Remind me", "Opens the reminder email box on a drop, and links to it from the Coming soon rows."),
    Entry("drops.see_set", B, "See the set button", "See the set", "On a set with no shop price when a reminder cannot be taken (reminder emails are not set up, or it is out and the day's email has gone). Opens the set's page."),
    Entry("drops.none.out", T, "Out with no shop", "It is out, but no UK shop we check lists it yet.", "On a set whose day has come that no shop sells or takes pre-orders for."),
    Entry("drops.filter.label", T, "Game filter name", "Show drops for one game", "Read out by screen readers for the row of game buttons."),
    Entry("drops.filter.all", B, "All games button", "All games", "First of the game buttons on the drop tracker."),
    Entry("drops.group.week", H, "This week heading", "This week", "Drops in the next seven days."),
    Entry("drops.group.month", H, "This month heading", "This month", "The rest of this month's drops. Later months are headed by their name."),
    Entry("drops.when.today", T, "Out today", "Out today", "On a drop whose release date is today."),
    Entry("drops.when.tomorrow", T, "Out tomorrow", "Out tomorrow", "On a drop out tomorrow."),
    Entry("drops.when.days", T, "Out in some days", "Out in {days} days", "On a drop two or more days away. Within the week a live countdown is added beside it in browsers that run scripts.", placeholders=("days",)),
    Entry(
        "drops.when.month", T, "Month only",
        "Day not announced yet",
        "On a drop whose publisher has given only the month. No countdown is shown until the day is known.",
    ),
    Entry("drops.when.out_yesterday", T, "Came out yesterday", "Came out yesterday", "Under Not in stock yet on Out now."),
    Entry("drops.when.out", T, "Came out some days ago", "Came out {days} days ago", "Under Not in stock yet on Out now.", placeholders=("days",)),
    Entry("drops.checked", T, "When a drop price was checked", "checked {time}", "After each product's price in a drop's product list.", placeholders=("time",)),
    Entry("drops.more.one", B, "See the product", "See the product", "Opens the list of a drop's products, with a price and button for each.", placeholders=("count",)),
    Entry("drops.more.other", B, "See every product", "See all {count} products", "Opens the list of a drop's products, with a price and button for each.", placeholders=("count",)),
    Entry("drops.none", T, "No shop yet", "No UK shop takes pre-orders yet.", "On a drop still to come with no shop price, while reminder emails are not set up."),
    Entry(
        "drops.none.remind", T, "No shop yet, with reminders",
        "No UK shop takes pre-orders yet. We can email you when one does.",
        "On a drop still to come with no shop price. Its button is Remind me.",
    ),
    Entry("drops.form.title", T, "Reminder form heading", "Your email address", "Above the email box once Remind me is opened."),
    Entry("drops.form.button", B, "Reminder button", "Send reminder link", "Button that sends the email to confirm a drop reminder."),
    Entry(
        "drops.form.note", T, "Reminder privacy note",
        "Up to three emails: when a UK shop opens pre-orders, then the day before and on the day once the day is known. "
        "Then your address is deleted.",
        "Under the email box on each drop.",
        legal=True,
    ),
    Entry(
        "drops.form.note_out", T, "Reminder privacy note on the day",
        "One email today, with where to buy it. Then your address is deleted.",
        "Under the email box on a drop that is out today, when only the drop-day email can still go.",
        legal=True,
    ),
    Entry("drops.form.busy", T, "Too many requests", "Too many reminders were asked for from here just now. Try again in an hour.", "Shown when one internet connection asks for more than ten reminders in an hour."),
    Entry("drops.done.sent.title", H, "Check your inbox heading", "Check your inbox", "After asking for a reminder."),
    Entry(
        "drops.done.sent.body", T, "Check your inbox text",
        "We have emailed a link to confirm your reminder for {set}. Nothing else is sent until you press it. "
        "If that address already has this reminder, there is nothing more to do.",
        "After asking for a reminder, on its own page or in place of the form. The same for every address, so "
        "the page never says whether an address already has a reminder.",
        placeholders=("set",),
    ),
    Entry("drops.done.problem.title", H, "Reminder not set heading", "Reminder not set", "When a reminder could not be taken."),
    Entry("drops.done.back", B, "Back to the drops button", "Back to drops", "On the reminder pages."),
    Entry("drops.confirmed.title", H, "Reminder confirmed heading", "Reminder confirmed", "Page after the confirm link in a reminder email."),
    Entry(
        "drops.confirmed.body", T, "Reminder confirmed text",
        "We will email you about {set} when a UK shop opens pre-orders, then the day before it comes out and on "
        "the day once the day is known. Then your address is deleted.",
        "Page after the confirm link in a reminder email.",
        placeholders=("set",),
    ),
    Entry("drops.stopped.title", H, "Reminder stopped heading", "Reminder stopped", "Page after the unsubscribe link in a reminder email."),
    Entry(
        "drops.gone.body", T, "Reminder link expired text",
        "The reminder was sent, stopped or expired. Ask again on the drops page if you still want one.",
        "Page for a reminder confirm link that no longer works.",
    ),
    Entry("drops.empty.title", H, "No drops heading", "No release dates announced yet", "Under Coming soon when no set has a release date still to come."),
    Entry("drops.empty.game", H, "No drops for a game heading", "No {game} release dates announced yet", "When the chosen game has no drop still to come.", placeholders=("game",)),
    Entry(
        "drops.empty.body", T, "No drops text",
        "A new set shows here once its publisher, or two sources that agree, give its release date.",
        "Under the no drops heading.",
    ),
    Entry("drops.recent.title", H, "Not in stock yet heading", "Just out, not in stock yet", "Under Out now: sets released in the last 14 days that no UK shop has in stock."),
    Entry(
        "drops.small_print", P, "Drop tracker small print",
        "Release dates come from the publisher, from two sources that agree, or from us, and a date given as a "
        "month shows as that month. Prices are UK shops' own, checked through the day, and each says when. "
        "eBay and Amazon are left out here because their pre-orders are sellers' guesses.",
        "At the foot of the drops page.",
    ),
    Entry("drops.email.confirm.subject", T, "Reminder confirmation subject", "Confirm your reminder for {set}", "Subject of the email that confirms a reminder.", placeholders=("set",)),
    Entry("drops.email.confirm.preheader", T, "Reminder confirmation preview", "One click and we will email you before {set} drops.", "Preview line of the confirmation email.", placeholders=("set",)),
    Entry("drops.email.confirm.title", H, "Reminder confirmation heading", "Confirm your reminder", "In the confirmation email."),
    Entry(
        "drops.email.confirm.body", T, "Reminder confirmation text",
        "You asked {site_name} to email you about {set}: when a UK shop opens pre-orders, then the day before it "
        "comes out and on the day once the day is known.",
        "In the confirmation email.",
        placeholders=("set", "site_name"),
    ),
    Entry("drops.email.confirm.button", B, "Reminder confirmation button", "Confirm my reminder", "In the confirmation email."),
    Entry(
        "drops.email.confirm.note", T, "Reminder confirmation note",
        "Nothing is sent until you confirm. If this was not you, ignore this email and the request is deleted within a week.",
        "In the confirmation email.",
        legal=True,
    ),
    Entry("drops.email.preorder.subject", T, "Pre-orders open subject", "Pre-orders open: {set}", "Subject of the email sent when a shop opens pre-orders before a drop.", placeholders=("set",)),
    Entry("drops.email.preorder.preheader", T, "Pre-orders open preview", "{names} now take pre-orders.", "Preview line of the pre-orders email.", placeholders=("names",)),
    Entry(
        "drops.email.preorder.body", T, "Pre-orders open text",
        "{names} started taking pre-orders for {set}. Their prices are below, with the cheapest others we have now.",
        "In the pre-orders email.",
        placeholders=("set", "names"),
    ),
    Entry("drops.email.eve.subject", T, "Out tomorrow subject", "{set} is out tomorrow", "Subject of the email on the morning before a drop.", placeholders=("set",)),
    Entry("drops.email.eve.preheader", T, "Out tomorrow preview", "The cheapest UK prices the day before it drops.", "Preview line of the day-before email."),
    Entry("drops.email.eve.body", T, "Out tomorrow text", "{set} comes out tomorrow, {date}. These are the cheapest UK prices we have now.", "In the day-before email.", placeholders=("set", "date")),
    Entry("drops.email.day.subject", T, "Out today subject", "{set} is out today", "Subject of the email on a drop's morning.", placeholders=("set",)),
    Entry("drops.email.day.preheader", T, "Out today preview", "Where to buy it now, cheapest first.", "Preview line of the drop-day email."),
    Entry("drops.email.day.body", T, "Out today text", "{set} is out today. Here is where to buy it now, cheapest first.", "In the drop-day email.", placeholders=("set",)),
    Entry("drops.email.offers", T, "Email price list heading", "Cheapest now", "Above the prices in a reminder email."),
    Entry("drops.email.checked", T, "Email price checked", "Checked {when}", "After each price in a reminder email.", placeholders=("when",)),
    Entry("drops.email.none", T, "Email with no prices", "No UK shop we check lists it yet. The drops page shows the moment one does.", "In a reminder email when no shop has a price."),
    Entry("drops.email.offer_button", B, "Email price link", "See prices", "Link after each price in a reminder email, to the product's page."),
    Entry("drops.email.button", B, "Email drops page button", "See the drop", "Button in a reminder email, to the drop on the drops page."),
    Entry("drops.email.more", T, "Email more to come", "We only email you about this drop: when pre-orders open, the day before and on the day. Each email has a link that deletes your address.", "Footer of the pre-orders and day-before emails.", legal=True),
    Entry("drops.email.last", T, "Email last one", "This was your last reminder for {set}, so your email address has already been deleted.", "Footer of the drop-day email.", placeholders=("set",), legal=True),
    Entry(
        "browse.filters.apply", B, "Apply filters button",
        "Show results",
        "Button that applies the filters. Only visible if JavaScript is off.",
    ),
    Entry("browse.quick.label", T, "Quick filters name", "Quick filters",
          "Read out by screen readers for the row of quick filter buttons above search results."),
    Entry("browse.quick.remove", T, "Quick filter on", "on, select to remove",
          "Read out by screen readers after a quick filter that is on. Selecting it takes it off."),
    Entry(
        "browse.filters.clear", B, "Clear filters link",
        "Clear filters",
        "Link that removes all filters.",
    ),
    Entry(
        "browse.alert_link", B, "Email me about these link",
        "Email me when products like these are in stock",
        "Under the list on search, game and set pages once a game is chosen, while the list has products or the "
        "set is still to come. Opens the alert form on the watchlist "
        "page with the same game, type, language and price filled in. The alert can be wider than the list (it "
        "has no set or search words), so this must not promise these exact products. Shown only while emails "
        "are set up.",
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
        "browse.card.shops", T, "Card shop list name",
        "Cheapest shops",
        "Name of the two shop rows on a product card, read out by screen readers.",
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
        "browse.games.all_sets", B, "All sets link on the Games page",
        "All {count} sets",
        "Under a game's newest sets on the Games page, when it has more than are listed there. Shows the rest of "
        "the game's sets in place.",
        placeholders=("count",),
    ),
    Entry(
        "browse.games.all_products.one", B, "Products link on the Games page (one)",
        "See 1 product",
        "Under each game on the Games page, when the game has one product. Opens the game's page.",
    ),
    Entry(
        "browse.games.all_products.other", B, "Products link on the Games page",
        "See all {count} products",
        "Under each game on the Games page, with or without sets listed above it. Opens the game's page.",
        placeholders=("count",),
    ),
    Entry(
        "browse.game.sets_title", H, "Sets heading on a game page",
        "Sets",
        "Read out by screen readers for the row of the game's newest sets on each game page.",
    ),
    Entry("browse.hub.products.one", T, "Hub product count (one)", "1 sealed product",
          "Starts the line under the heading of a game or set page with one product."),
    Entry("browse.hub.products.other", T, "Hub product count (more than one)", "{count} sealed products",
          "Starts the line under the heading of a game or set page.", placeholders=("count",)),
    Entry("browse.hub.shops.one", T, "Hub shop count (one)", "1 UK shop",
          "In the line under the heading of a game or set page: shops listing any of its products."),
    Entry("browse.hub.shops.other", T, "Hub shop count (more than one)", "{count} UK shops",
          "In the line under the heading of a game or set page: shops listing any of its products.",
          placeholders=("count",)),
    Entry(
        "browse.hub.brief", T, "Game page line",
        "{products} compared across {shops}.",
        "Under the heading of a game page while a shop has something in stock. The In stock tab gives that count.",
        placeholders=("products", "shops"),
    ),
    Entry(
        "browse.hub.intro", T, "Hub line",
        "{products} compared across {shops}, {count} in stock now.",
        "Under the heading of a game or set page while a shop has something in stock. {count} is how many are "
        "in stock.",
        placeholders=("products", "shops", "count"),
    ),
    Entry(
        "browse.hub.cheapest_label", T, "Set page cheapest card label",
        "Cheapest in stock",
        "Small label on the card under a set page's heading that shows its cheapest product in stock now.",
    ),
    Entry(
        "browse.hub.lowest", T, "Hub cheapest in stock",
        "Cheapest in stock: {product}, {price} delivered at {retailer}.",
        "After the hub line: the cheapest delivered price in stock now. The product name links to its page.",
        placeholders=("product", "price", "retailer"),
    ),
    Entry(
        "browse.hub.none_in_stock", T, "Hub line with nothing in stock",
        "{products} compared across {shops}, none in stock right now.",
        "Under the heading of a game or set page when no shop has anything in stock or takes pre-orders.",
        placeholders=("products", "shops"),
    ),
    Entry(
        "browse.hub.preorder", T, "Hub line with everything on pre-order",
        "{products}, on pre-order at {shops}.",
        "Under the heading of a game or set page when nothing is in stock and every product is on pre-order. "
        "{shops} counts the shops taking pre-orders.",
        placeholders=("products", "shops"),
    ),
    Entry(
        "browse.hub.preorder_some", T, "Hub line with some on pre-order",
        "{products}, {count} on pre-order at {shops}.",
        "Under the heading of a game or set page when nothing is in stock and some products are on pre-order. "
        "{count} is how many are on pre-order and {shops} counts the shops taking them.",
        placeholders=("products", "count", "shops"),
    ),
    Entry(
        "browse.hub.no_date", T, "Hub line, no release date yet",
        "Release date not announced yet.",
        "After the line under the heading of a set page still to come that has no release date, while nothing "
        "is in stock.",
    ),
    Entry(
        "browse.hub.upcoming", T, "Hub line before release",
        "{products} due out on {date}, with {shops} taking pre-orders now.",
        "Under the heading of a set page still to come while nothing is in stock. {shops} counts the shops "
        "taking pre-orders.",
        placeholders=("products", "date", "shops"),
    ),
    Entry(
        "browse.hub.upcoming_none", T, "Hub line before release, no pre-orders",
        "{products} due out on {date}, not yet on pre-order at a UK shop we check.",
        "Under the heading of a set page still to come when no shop lists any of its products yet.",
        placeholders=("products", "date"),
    ),
    Entry(
        "browse.hub.upcoming_listed", T, "Hub line before release, listed, no pre-orders",
        "{products} due out on {date}, listed at {shops} but not on pre-order yet.",
        "Under the heading of a set page still to come when shops list its products but none takes "
        "pre-orders now. {shops} counts the shops listing them.",
        placeholders=("products", "date", "shops"),
    ),
    Entry(
        "browse.hub.unlisted", T, "Hub line with no shop",
        "{products}, not yet listed by a UK shop we check.",
        "Under the heading of a game or set page when no shop lists any of its products.",
        placeholders=("products",),
    ),
    Entry(
        "browse.hub.no_current", T, "Hub line with no current price",
        "{products}, with no current price from a UK shop we check.",
        "Under the heading of a game or set page when shops list its products but none has a price checked "
        "recently enough to show.",
        placeholders=("products",),
    ),
    Entry(
        "browse.set.game_link", B, "Set page link to its game",
        "All {game} products",
        "Under the heading of a set page: opens the game's page.",
        placeholders=("game",),
    ),
    Entry(
        "browse.set.not_in_shops", T, "Set not sold in UK shops",
        "Sold at events or direct by the publisher, not in UK shops.",
        "Under the heading of a set page, for a set marked as not sold in UK shops in Django Admin or by Claude.",
    ),
    Entry(
        "browse.set.released", T, "Set release date",
        "Released {date}",
        "Under the heading on each set page.",
        placeholders=("date",),
    ),
    Entry(
        "browse.game.set_stock.one", T, "Set chip: in stock (one)",
        "1 in stock",
        "Read out after a set's name in the row of sets on a game page; the chip shows the number alone.",
    ),
    Entry(
        "browse.game.set_stock.other", T, "Set chip: in stock (more than one)",
        "{count} in stock",
        "Read out after a set's name in the row of sets on a game page; the chip shows the number alone. "
        "Also used for none.",
        placeholders=("count",),
    ),
    Entry(
        "browse.game.all_sets", B, "All sets link",
        "All sets",
        "Last chip in the row of newest sets on a game page, when the game has more sets than the row shows. "
        "Opens the game's whole list of sets on the Games page.",
    ),
    Entry(
        "browse.tabs.label", T, "Stock tabs label",
        "Availability",
        "Read out by screen readers for the row of In stock, Pre-orders and Out of stock tabs on game and set pages.",
    ),
    Entry(
        "browse.tabs.loading", T, "Stock tab loading",
        "Loading",
        "Shown for a moment in a stock tab swiped to before its list arrives.",
    ),
    Entry(
        "browse.tabs.soonest", T, "Pre-orders order note",
        "Soonest out first.",
        "Above the Pre-orders tab's list when no other sort is chosen.",
    ),
    Entry(
        "browse.list.pre_orders", H, "Pre-orders list heading",
        "On pre-order",
        "Above the list on a game or set page whose products are all on pre-order, so it has no tabs.",
    ),
    Entry(
        "browse.list.out_of_stock", H, "Out of stock list heading",
        "Out of stock",
        "Above the list on a game or set page where nothing is in stock or on pre-order, so it has no tabs.",
    ),
    Entry(
        "browse.filters.toggle", B, "Sort and filter button",
        "Sort and filter",
        "Opens the sort and filter choices on a game or set page with 12 or more products in the list. Open "
        "already while a filter is chosen.",
    ),
    Entry(
        "browse.tabs.empty.in_stock", T, "Nothing in stock",
        "Nothing here is in stock right now.",
        "The In stock tab of a game or set page when no product is in stock.",
    ),
    Entry(
        "browse.tabs.empty.pre_orders", T, "No pre-orders",
        "No pre-orders here right now.",
        "The Pre-orders tab of a game or set page when no shop takes pre-orders.",
    ),
    Entry(
        "browse.tabs.empty.out_of_stock", T, "Nothing out of stock",
        "Everything here is in stock or on pre-order.",
        "The Out of stock tab of a game or set page when it is empty.",
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
        "Inside the Buy now button on a product page: the shop the button goes to.",
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
        "product.badge.low_30", T, "Badge: lowest in 30 days",
        "Lowest in 30 days",
        "Green badge when today's cheapest delivered price is at or below every day's cheapest over the last 30 "
        "days, checked against our own daily record with at least 14 days recorded. On featured deals only when "
        "it is also clearly under the month's average.",
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
        "On a sold-out product page, the most recent check across its shops. On the watchlist and recently viewed "
        "cards, when the price shown was checked.",
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
    Entry("discord.restock.title", T, "Discord: back in stock", "Back in stock",
          "On the line above a Discord post's title, beside the logo, when a UK shop has a product in stock again. "
          "The title is the product's name.", placeholders=("product",)),
    Entry("discord.preorder.title", T, "Discord: pre-orders open", "Pre-orders open",
          "On the line above a Discord post's title, beside the logo, when a UK shop opens pre-orders.",
          placeholders=("product",)),
    Entry("discord.price_drop.title", T, "Discord: price drop", "Price drop",
          "On the line above a Discord post's title, beside the logo, when a product's cheapest delivered price "
          "falls.", placeholders=("product",)),
    Entry("discord.drop_day.title", T, "Discord: out today", "Out today",
          "On the line above a Discord post's title, beside the logo, on a set's release day. The title is the set "
          "and its game.", placeholders=("set", "game")),
    Entry("discord.price", T, "Discord: price", "**{price}** delivered, checked {when}.",
          "The line under a Discord post's title. It never names the shop: the RipRaptor page shows every shop. "
          "Words between ** are bold in Discord. {when} is the day, date and time (Sat 24 Oct, 14:05); {time} "
          "is the time alone.", placeholders=("price", "time", "when")),
    Entry("discord.price_plus", T, "Discord: price, delivery unknown",
          "**{price}** plus delivery, checked {when}.",
          "As the price line, when the shop's delivery charge is not known.", placeholders=("price", "time", "when")),
    Entry("discord.compare", T, "Discord: compare link", "Compare every UK shop on RipRaptor",
          "A link under the price in every Discord post, to the product's page. Leave it blank for none.",
          optional=True),
    Entry("discord.price_drop.body", T, "Discord: price drop line",
          "Now **{price}** delivered, was {was} yesterday. Checked {when}.",
          "Under a price drop post's title. {when} is the day, date and time; {time} the time alone.",
          placeholders=("price", "was", "time", "when")),
    Entry("discord.drop_day.body", T, "Discord: out today line", "Cheapest from {price}",
          "Under an Out today post's title, when a UK shop has the set. {price} is the price line.",
          placeholders=("price",)),
    Entry("discord.drop_day.none", T, "Discord: out today, no shop yet",
          "No UK shop we check has it yet. We will post when one does.",
          "Under an Out today post's title, when no UK shop has the set yet."),
    Entry("discord.field.price_check", T, "Discord: Price check box", "Price check",
          "Name of the box in a back in stock or pre-order post that gives the product's Price check."),
    Entry("discord.fair.short_average", T, "Discord: price check average", "Average {average} over {days} days",
          "In the Price check box, under the verdict, when it is measured against our own record.",
          placeholders=("average", "days")),
    Entry("discord.fair.short_market", T, "Discord: price check market", "European market {market}",
          "In the Price check box, under the verdict, when it is measured against Cardmarket's price in pounds.",
          placeholders=("market",)),
    Entry("discord.fair.one_shop", T, "Discord: one shop only",
          "Only one UK shop lists it, so nothing to compare with.",
          "In the Price check box when only one UK shop lists the product."),
    Entry("discord.field.shops", T, "Discord: shops box", "UK shops with it",
          "Name of the box giving how many of the UK shops that list the product have it in stock now, as "
          "\"1 of 5\". Never a count of units: shops do not say how many they have."),
    Entry("discord.field.out_for", T, "Discord: out of stock box", "Out of stock for",
          "Name of the box in a back in stock post giving how long that shop had sold out, when we saw it go."),
    Entry("discord.field.restocks", T, "Discord: restocks box", "Restocks in {days} days",
          "Name of the box in a back in stock post counting its restocks lately, when there has been more than one.",
          placeholders=("days",)),
    Entry("discord.field.lasted", T, "Discord: last stock box", "Last stock lasted",
          "Name of the box in a back in stock post giving how long that shop's last stock lasted before it sold "
          "out, when we saw both."),
    Entry("discord.footer", T, "Discord: footer", "RipRaptor may earn a commission. Prices change, so check before you buy.",
          "Small print under every post to the RipRaptor Discord.", legal=True),
    Entry("discord.test", T, "Discord: test post",
          "RipRaptor is connected. Restocks, pre-orders, price drops and drop days will appear here. {site}",
          "Posted once by post_discord --test, to check a channel is set up.", placeholders=("site",)),
    Entry("product.share.button", B, "Share button", "Share",
          "Next to Save on a product page, with the share icon: opens the Share panel. Also read out for the share "
          "icon on each product card, and the small heading of the Share sheet it opens."),
    Entry("product.share.title", T, "Share panel name", "Share this product",
          "Read out by screen readers for the row of logos in the Share panel."),
    Entry("product.share.reddit", B, "Reddit logo label", "Post to Reddit",
          "Read out for the Reddit logo in the Share panel, which opens Reddit's post form with the link and title filled in."),
    Entry("product.share.reddit_title", T, "Reddit post title", "{product}: UK prices compared",
          "The title filled in on Reddit's post form, which the visitor can change.", placeholders=("product",)),
    Entry("product.share.instagram", B, "Instagram logo label", "Copy link for Instagram",
          "Read out for the Instagram logo in the Share panel. Instagram takes no link from a web page, so it copies one."),
    Entry("product.share.tiktok", B, "TikTok logo label", "Copy link for TikTok",
          "Read out for the TikTok logo in the Share panel. TikTok takes no link from a web page, so it copies one."),
    Entry("product.share.copy", B, "Copy link button", "Copy link", "Under the link icon in the Share panel."),
    Entry("product.share.more", B, "Share sheet label", "More ways to share",
          "Read out for the last icon in the Share panel, on phones and browsers that have a share sheet: opens it."),
    Entry("product.share.more_short", B, "Share sheet button", "More", "Under the last icon in the Share panel."),
    Entry("product.share.copied", T, "Link copied", "Link copied.", "Shown after Copy link in the Share panel."),
    Entry("product.share.copied_instagram", T, "Copied for Instagram",
          "Link copied. Paste it into a story's link sticker, a message or your bio.",
          "Shown after the Instagram logo is tapped."),
    Entry("product.share.copied_tiktok", T, "Copied for TikTok", "Link copied. Paste it into a message or your bio.",
          "Shown after the TikTok logo is tapped."),
    Entry("product.share.close", B, "Close share sheet", "Close",
          "Closes the Share sheet a product card's share icon opens."),
    Entry("product.share.link_label", T, "Share link label", "Link to this product",
          "Above the box holding the product page's address in the Share panel, shown only without JavaScript."),
    Entry("product.compare.lottery", B, "Enter lottery button", "Enter lottery",
          "On a price list row for a shop selling the product by a draw. Opens the shop's page."),
    Entry("product.compare.lottery_note", T, "Lottery row note",
          "This shop sells it by lottery: you enter, and only those drawn can buy. Not counted as in stock.",
          "Under a price list row for a shop selling the product by a draw."),
    Entry(
        "product.compare.cheapest", T, "Cheapest marker",
        "Cheapest",
        "Small marker on the cheapest row in the price list, and on the first shop row of a product card.",
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
        "product.deal.row", T, "Multi-buy on a shop's row",
        "{quantity} for {price} here, {each} each delivered",
        "Under a shop's price when the shop runs a multi-buy on the product. Read from the shop's own "
        "collection of that name on every read, so it comes and goes with the shop's offer.",
        placeholders=("quantity", "price", "each"),
    ),
    Entry(
        "product.deal.row_plus", T, "Multi-buy on a shop's row, delivery not known",
        "{quantity} for {price} here, {each} each plus delivery",
        "As the multi-buy line, when we do not know the shop's delivery charge on the multi-buy.",
        placeholders=("quantity", "price", "each"),
    ),
    Entry(
        "product.deal.better", T, "Cheaper per item in a multi-buy",
        "Or {quantity} for {price} at {retailer}, {each} each delivered",
        "Under the cheapest price on a product page and on product cards, when a shop's multi-buy costs "
        "less per item than buying one at the cheapest shop. The comparison itself stays on buying one.",
        placeholders=("quantity", "price", "retailer", "each"),
    ),
    Entry(
        "product.compare.shops", T, "Shop count link",
        "Compare {count} shops",
        "At the foot of a product card with two or more shops; jumps to the product page's price list.",
        placeholders=("count",),
    ),
    Entry(
        "product.compare.one_shop", T, "One shop",
        "1 shop found",
        "Under Compare prices on a product page when only one shop has a price.",
    ),
    Entry(
        "product.compare.jump", B, "Compare prices button",
        "Compare prices",
        "Outlined button under Buy now on a product page. Jumps down to the price list.",
    ),
    Entry(
        "product.compare.jump_note", T, "Compare prices button, shop count",
        "{count} shops, cheapest delivered first",
        "Small line inside the Compare prices button when two or more shops have a price.",
        placeholders=("count",),
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
        "product.compare.amazon", T, "Amazon row in the price list",
        "Check price and stock on Amazon",
        "The last row of the price list on every product page while an Amazon tracking tag is set and we "
        "have no Amazon price for the product. The row opens a tagged Amazon search in a new tab.",
    ),
    Entry(
        "product.compare.amazon_button", B, "Amazon row button",
        "Check on Amazon",
        "Button on the Amazon row at the foot of the price list.",
    ),
    # Report a problem with a listing (web/views.py report_listing, includes/report_form.html).
    Entry("product.report.link", B, "Report link", "Report",
          "Small link in each shop's row of the price list. Opens the report form under the list."),
    Entry("product.report.link_label", T, "Report link, read out", "a problem with the listing at {retailer}",
          "Read out by screen readers after the Report link.", placeholders=("retailer",)),
    Entry("product.report.title", H, "Report form heading", "Report a problem with this listing",
          "Heading of the report form under the price list."),
    Entry("product.report.shop", T, "Report form shop", "The offer at {retailer}.",
          "Under the report form's heading: which shop's offer is being reported.", placeholders=("retailer",)),
    Entry("product.report.reason_label", T, "Report reasons question", "What is wrong with it?",
          "Above the report form's reasons."),
    Entry("product.report.reason.product", T, "Report reason: product", "It is a different product", "A report reason."),
    Entry("product.report.reason.language", T, "Report reason: language", "It is in another language", "A report reason."),
    Entry("product.report.reason.quantity", T, "Report reason: quantity", "The quantity or box size is wrong",
          "A report reason."),
    Entry("product.report.reason.price", T, "Report reason: price", "The price is wrong", "A report reason."),
    Entry("product.report.reason.stock", T, "Report reason: stock", "The stock status is wrong", "A report reason."),
    Entry("product.report.reason.condition", T, "Report reason: condition", "It is not sealed or not new",
          "A report reason."),
    Entry("product.report.reason.other", T, "Report reason: other", "Something else", "A report reason."),
    Entry("product.report.note_label", T, "Report note label", "Anything else we should know (optional)",
          "Above the report form's note box."),
    Entry("product.report.privacy", T, "Report privacy note", "We do not ask for your name or email. We check every report.",
          "Small line above the Send report button."),
    Entry("product.report.send", B, "Send report button", "Send report", "Sends the report form."),
    Entry("product.report.cancel", B, "Report cancel link", "Cancel", "Closes the report form."),
    Entry("product.report.sent", T, "Report sent", "Thanks. We will check that listing.",
          "Under the price list after a report is sent."),
    Entry("product.report.busy", T, "Report refused", "We cannot take more reports right now. Please try again in an hour.",
          "Under the price list when too many reports came in the last hour."),
    Entry("product.report.reason_missing", T, "Report reason missing", "Choose what is wrong, then send it again.",
          "On the report form when it was sent without a reason."),
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
        "Showing our latest checks. Each shop's time is beside its price.",
        "Shown for a moment after Reload prices finishes. The button reloads the latest prices we hold; it "
        "does not ask the shops again, so this must not say the prices are current.",
    ),
    Entry(
        "product.cheapest.checked", T, "When the main price was checked",
        "Price checked at {retailer} {time}.",
        "Under the main price on a product page: when that shop's price was last checked, not any other shop's.",
        placeholders=("retailer", "time"),
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
    Entry("product.fair.title", T, "Price check name", "Price check",
          "Small heading of the sheet the price check opens, beside a product's cheapest price."),
    Entry("product.fair.verdict_lowest", T, "Price check: lowest", "Lowest yet",
          "Beside the cheapest price when it is at or below the lowest we have recorded for the product."),
    Entry("product.fair.verdict_good", T, "Price check: good", "Good price",
          "Beside the cheapest price when it is at least 5% under the product's average."),
    Entry("product.fair.verdict_fair", T, "Price check: fair", "Fair price",
          "Beside the cheapest price when it is within 5% of the product's average."),
    Entry("product.fair.verdict_above", T, "Price check: above", "Above average",
          "Beside the cheapest price when it is up to 15% over the product's average."),
    Entry("product.fair.verdict_high", T, "Price check: high", "High price",
          "Beside the cheapest price when it is more than 15% over the product's average."),
    Entry("product.fair.label", T, "Price check, read out", "Price check: {verdict}. Show price history",
          "Read out by screen readers for the price check beside the cheapest price.", placeholders=("verdict",)),
    Entry("product.fair.today", T, "Price check: today", "Today",
          "In the price check sheet, beside today's cheapest delivered price."),
    Entry("product.fair.average", T, "Price check: average", "Average of {days} days",
          "In the price check sheet, beside the average cheapest price of the days we have recorded.",
          placeholders=("days",)),
    Entry("product.fair.lowest", T, "Price check: lowest price", "Lowest",
          "In the price check sheet, beside the lowest cheapest price we have recorded and its date."),
    Entry("product.fair.market", T, "Price check: European market", "European market",
          "In the price check sheet, beside Cardmarket's price for the same product, in pounds."),
    Entry("product.fair.market_when", T, "Price check: Cardmarket date", "Cardmarket, {date}",
          "Under the European market price in the price check sheet.", placeholders=("date",)),
    Entry("product.fair.market_basis", T, "Price check: European market basis",
          "The European market price is Cardmarket's trend for the same product, in pounds at the day's European "
          "Central Bank rate, before post to the UK and any import costs.",
          "At the foot of the price check sheet when it uses Cardmarket's price."),
    Entry("product.fair.how", T, "Price check: how to read it",
          "More bars mean a better price. Green is a good price, grey is about usual, dark yellow is dearer than "
          "usual.",
          "Under the verdict in the price check sheet, for anyone new to it.", optional=True),
    Entry("product.fair.basis", T, "Price check: basis",
          "From the cheapest delivered price we have recorded each day since {since}.",
          "At the foot of the price check sheet.", placeholders=("since",)),
    Entry(
        "product.instead.title", H, "In stock instead heading",
        "In stock instead",
        "Above the closest products shops have in stock, on a product page nobody can sell now.",
    ),
    Entry(
        "product.instead.note", T, "In stock instead note",
        "Nobody has this in stock. These are the closest products that are.",
        "Under the In stock instead heading.",
    ),
    Entry(
        "browse.set.instead_note", T, "Sold-out set: In stock instead note",
        "Nothing from this set is in stock. These are the closest products that are.",
        "Under the In stock instead heading on a set page nobody has in stock or on pre-order.",
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
    Entry("product.details.language", T, "Detail: language", "Language",
          "Label in product details. The language is worked out from the product's name and set."),
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

    # Refer a friend -------------------------------------------------------
    Entry("refer.nav", B, "Refer a friend link", "Refer a friend, get a free keyring",
          "In the footer and on the watchlist page, only while the keyring offer is open."),
    Entry("refer.title", H, "Refer a friend heading", "Refer a friend, get a free keyring",
          "Heading and page title of the refer a friend page."),
    Entry("refer.kicker", T, "Pack line", "Raptors save in packs.",
          "Beside the two raptor heads, above the heading on the refer a friend pages and on the refer a friend card."),
    Entry("refer.keyring.alt", T, "Keyring picture description",
          "RipRaptor 3D keyring: the ripraptor logo in orange on a white base",
          "Read out by screen readers in place of the keyring picture."),
    Entry("refer.nudge.label", T, "Refer a friend card name", "Refer a friend",
          "Name of the refer a friend card for screen readers."),
    Entry("refer.nudge.text", T, "Refer a friend card text",
          "Refer a friend and you both get a free RipRaptor 3D keyring by post. UK only, whilst stocks last.",
          "On the card that now and then invites visitors to refer a friend, while the offer is open.", legal=True),
    Entry("refer.nudge.go", B, "Refer a friend card button", "Get my link",
          "On the refer a friend card. Opens the refer a friend page."),
    Entry("refer.nudge.dismiss", B, "Refer a friend card dismiss", "No thanks",
          "On the refer a friend card. Pressing it means the card never shows again in that browser."),
    Entry(
        "refer.intro", T, "Refer a friend introduction",
        "Ask for your own link and share it. When a friend joins through it and confirms their email address, "
        "you both get a free RipRaptor 3D keyring by post. UK only, whilst stocks last.",
        "Under the heading on the refer a friend page.",
        legal=True,
    ),
    Entry("refer.ended", T, "Offer ended", "This offer has ended.",
          "On the refer a friend pages and links when the offer is off, out of keyrings or past its last day."),
    Entry("refer.form.email", T, "Your email label", "Your email address", "Label of the email box on the refer a friend page."),
    Entry(
        "refer.form.email_note", T, "Your email note",
        "We email you a link to confirm, then your link to share. We only use your address for this offer.",
        "Under the email box on the refer a friend page.",
        legal=True,
    ),
    Entry("refer.form.agree", T, "Agree to the terms", "I live in the UK and agree to the promotion terms.",
          "Next to the tick box on the refer a friend forms.", legal=True),
    Entry("refer.form.terms_link", B, "Promotion terms link", "Read the promotion terms",
          "Link to the offer's terms, beside the tick box."),
    Entry("refer.form.button", B, "Ask for a link button", "Email me my link", "Button on the refer a friend page."),
    Entry("refer.form.invalid", T, "Email not valid", "Enter a full email address, like name@example.com.",
          "When the refer a friend forms get an address that is not valid."),
    Entry("refer.form.agree_needed", T, "Terms not ticked", "Tick the box to agree to the promotion terms.",
          "When the refer a friend forms are sent without the tick."),
    Entry("refer.form.busy", T, "Too many requests", "Too many requests from your connection. Try again in an hour.",
          "When one connection asks too often on the refer a friend pages."),
    Entry("refer.form.failed", T, "Email failed", "We could not send the email just now. Try again in a few minutes.",
          "When the email service is down."),
    Entry("refer.sent.title", H, "Check your inbox heading", "Check your inbox", "After asking for a link or a friend's keyring."),
    Entry(
        "refer.sent.body", T, "Check your inbox text",
        "If that address can take part, we have emailed it a link to confirm. It can take a few minutes, so check "
        "your spam folder too.",
        "After the refer a friend forms. The same for every address, so the page never says whether one is known.",
    ),
    Entry("refer.link.title", H, "Your link heading", "Your link to share", "Page after the referrer confirms their email address."),
    Entry(
        "refer.link.intro", T, "Your link text",
        "Send it to a friend. When they follow it and confirm their own email address, you both get a free keyring.",
        "Under the heading on the page with the referrer's link.",
    ),
    Entry("refer.link.label", T, "Your link label", "Your link", "Label of the box holding the referrer's link."),
    Entry("refer.link.note", T, "Who can join note",
          "Your friend must live in the UK, in a different household, and be new to RipRaptor offers.",
          "Under the share buttons on the referrer's link page.", legal=True),
    Entry("refer.link.used", T, "Already had a keyring",
          "This email address has already had a keyring from this offer. Thank you for sharing RipRaptor.",
          "On the referrer's link page once they have earned all the keyrings they can."),
    Entry("refer.share.whatsapp", B, "WhatsApp button", "Share on WhatsApp", "Share button on the referrer's link page."),
    Entry("refer.share.sms", B, "Text message button", "Send by text", "Share button on the referrer's link page."),
    Entry("refer.share.email", B, "Email button", "Send by email",
          "Share button on the referrer's link page. Opens the visitor's own email app; we send nothing to the friend."),
    Entry("refer.share.copy", B, "Copy button", "Copy link", "Share button on the referrer's link page, shown with JavaScript."),
    Entry("refer.share.copied", T, "Copied note", "Link copied", "After the Copy link button is pressed."),
    Entry("refer.share.native", B, "Share button", "Share link",
          "Share button on the referrer's link page, shown on phones that offer their own share sheet."),
    Entry("refer.share.subject", T, "Share subject", "A free RipRaptor keyring for us both",
          "Subject line filled in when the referrer sends their link by email."),
    Entry(
        "refer.share.message", T, "Share message",
        "I use RipRaptor to compare UK prices on sealed card products. Join through my link and we both get a "
        "free RipRaptor 3D keyring: {link}",
        "The message filled in when the referrer shares their link.",
        placeholders=("link",),
    ),
    Entry("refer.friend.title", H, "Friend's heading", "A free RipRaptor keyring for you", "Heading on a referrer's link."),
    Entry(
        "refer.friend.intro", T, "Friend's text",
        "A friend has invited you to RipRaptor, which compares UK prices for sealed trading card products. Confirm "
        "your email address and you both get a free 3D keyring by post. One per household, UK only, whilst "
        "stocks last.",
        "Under the heading on a referrer's link.",
        legal=True,
    ),
    Entry(
        "refer.friend.email_note", T, "Friend's email note",
        "We email you a link to confirm, then ask where to post your keyring. We only use your address for this offer.",
        "Under the email box on a referrer's link.",
        legal=True,
    ),
    Entry("refer.friend.button", B, "Friend's button", "Email me a link to confirm", "Button on a referrer's link."),
    Entry("refer.friend.used", T, "Link used", "This link has already been used.",
          "On a referrer's link once it has earned all the keyrings it can."),
    Entry("refer.friend.own", B, "Get your own link", "Get your own link", "Under the link used message."),
    Entry("refer.claim.title", H, "Claim heading", "Claim your keyring", "Page from the friend's confirmation email."),
    Entry("refer.claim.body", T, "Claim text",
          "Press the button to confirm your email address and finish the referral.",
          "Page from the friend's confirmation email."),
    Entry("refer.claim.button", B, "Claim button", "Claim my keyring", "Button on the friend's claim page."),
    Entry("refer.claimed.title", H, "Claimed heading", "Thank you", "After the friend claims their keyring."),
    Entry("refer.claimed.body", T, "Claimed text",
          "We will email you both when your keyrings are ready, to ask where to post them.",
          "After the friend claims their keyring."),
    Entry("refer.claim.not_new", T, "Not new",
          "This email address has already taken part in a RipRaptor offer, so it cannot claim another keyring.",
          "When a friend's address has taken part before."),
    Entry("refer.gone.title", H, "Link not working heading", "This link no longer works",
          "For a refer a friend link that is unknown, used, expired or cancelled."),
    Entry("refer.gone.body", T, "Link not working text", "It may have expired, been used or been cancelled.",
          "For a refer a friend link that is unknown, used, expired or cancelled."),
    Entry("refer.busy", T, "Too many tries", "Too many tries from your connection. Try again later.",
          "When one connection tries too many wrong refer a friend links."),
    Entry("refer.address.title", H, "Address heading", "Where should we send your keyring?", "Heading on the address form."),
    Entry(
        "refer.address.intro", T, "Address text",
        "UK addresses only. We use this address only to post your keyring, and delete it 60 days after posting.",
        "Under the heading on the address form.",
        legal=True,
    ),
    Entry("refer.address.name", T, "Name label", "Full name", "On the address form."),
    Entry("refer.address.line1", T, "Address line 1 label", "Address line 1", "On the address form."),
    Entry("refer.address.line2", T, "Address line 2 label", "Address line 2 (optional)", "On the address form."),
    Entry("refer.address.town", T, "Town label", "Town or city", "On the address form."),
    Entry("refer.address.postcode", T, "Postcode label", "Postcode", "On the address form."),
    Entry("refer.address.button", B, "Address button", "Send my address", "Button on the address form."),
    Entry("refer.address.missing", T, "Address incomplete", "Fill in your name, first address line, town and postcode.",
          "When the address form is incomplete."),
    Entry("refer.address.bad_postcode", T, "Postcode not valid",
          "Enter a UK postcode, like SW1A 1AA. We can only post to UK addresses.",
          "When the postcode is not a UK one."),
    Entry("refer.address.used", T, "Address used", "This address has already had a keyring from this offer.",
          "When the postal address has had a keyring before."),
    Entry("refer.address.done.title", H, "Address sent heading", "Thank you", "After the address form is sent."),
    Entry("refer.address.done.body", T, "Address sent text", "We have your address and will post your keyring.",
          "After the address form is sent, and on the link afterwards."),
    Entry("refer.address.expired", T, "Address link expired", "This link has expired. Address links last 30 days.",
          "When an address link is over 30 days old."),
    Entry("refer.stop.title", H, "Delete details heading", "Delete your details", "Page from the delete link in a refer a friend email."),
    Entry("refer.stop.body", T, "Delete details text",
          "This deletes your email address, name and postal address from the keyring offer. If your keyring has not been posted yet, you will not get one.",
          "Page from the delete link in a refer a friend email.", legal=True),
    Entry("refer.stop.button", B, "Delete details button", "Delete my details", "On the delete page and in refer a friend emails."),
    Entry("refer.stopped.title", H, "Details deleted heading", "Your details are deleted", "After deleting refer a friend details."),
    Entry("refer.stopped.body", T, "Details deleted text",
          "We have deleted your email address, name and postal address from the keyring offer.",
          "After deleting refer a friend details."),
    Entry("refer.email.stop_note", T, "Delete link note",
          "The button above opens a page where you can delete your details from this offer.",
          "At the foot of every refer a friend email.", legal=True),
    Entry("refer.email.confirm.subject", T, "Link confirmation subject", "Confirm your email to get your RipRaptor link",
          "Subject of the email that confirms a referrer's address."),
    Entry("refer.email.confirm.title", H, "Link confirmation heading", "Confirm your email address", "In the referrer's confirmation email."),
    Entry("refer.email.confirm.body", T, "Link confirmation text",
          "Press the button to confirm your email address and get your link to share.",
          "In the referrer's confirmation email."),
    Entry("refer.email.confirm.button", B, "Link confirmation button", "Confirm and get my link", "In the referrer's confirmation email."),
    Entry("refer.email.confirm.note", T, "Confirmation note",
          "If this was not you, ignore this email. Unconfirmed addresses are deleted after 14 days.",
          "In both refer a friend confirmation emails.", legal=True),
    Entry("refer.email.link.subject", T, "Your link subject", "Your RipRaptor link to share", "Subject of the email with the referrer's link."),
    Entry("refer.email.link.body", T, "Your link email text",
          "Here is your link. Send it to a friend, and when they confirm their email address you both get a free keyring.",
          "In the email with the referrer's link."),
    Entry("refer.email.link.button", B, "Your link email button", "Open my share page", "In the email with the referrer's link."),
    Entry("refer.email.friend.subject", T, "Friend confirmation subject", "Confirm your email to claim your RipRaptor keyring",
          "Subject of the friend's confirmation email."),
    Entry("refer.email.friend.body", T, "Friend confirmation text",
          "Press the button to confirm your email address. You and the friend who invited you then each get a free RipRaptor 3D keyring.",
          "In the friend's confirmation email."),
    Entry("refer.email.address.subject", T, "Address email subject", "Where should we send your RipRaptor keyring?",
          "Subject of the email asking for a postal address."),
    Entry("refer.email.address.title", H, "Address email heading", "Your keyring is ready to post", "In the email asking for a postal address."),
    Entry("refer.email.address.body", T, "Address email text",
          "Your referral is complete. Tell us where to send your free keyring. The link works for 30 days.",
          "In the email asking for a postal address."),
    # List your shop -------------------------------------------------------
    Entry("listshop.title", H, "Page heading", "List your shop", "Top of the List your shop page."),
    Entry(
        "listshop.intro", T, "Introduction",
        "RipRaptor compares UK prices for sealed trading card game products. If your UK shop sells them, ask "
        "to be listed here and we will reply by email.",
        "Under the heading on the List your shop page.",
    ),
    Entry("listshop.form.name", T, "Name label", "Your name", "Label above the name box."),
    Entry("listshop.form.shop_url", T, "Website label", "Shop website", "Label above the website box."),
    Entry("listshop.form.shop_url_note", T, "Website note", "For example yourshop.co.uk", "Under the website box."),
    Entry("listshop.form.email", T, "Email label", "Email address", "Label above the email box."),
    Entry(
        "listshop.form.email_note", T, "Email note",
        "Used only to reply about your shop. We delete it a year after we deal with it, or after two years at most.",
        "Under the email box, followed by a link to the data part of the terms page.",
    ),
    Entry("listshop.form.message", T, "Message label", "Anything we should know (optional)", "Label above the message box."),
    Entry("listshop.form.button", B, "Send button", "Ask to be listed", "Button that sends the form."),
    Entry("listshop.form.name_invalid", T, "Missing name", "Add your name.", "Shown when the name box is empty."),
    Entry(
        "listshop.form.shop_url_invalid", T, "Bad website",
        "That website address does not look right. Use one like yourshop.co.uk.",
        "Shown when the website is missing, is not a web address or has no real domain.",
    ),
    Entry("listshop.form.email_invalid", T, "Bad email", "That email address does not look right.", "Shown when the email address is not valid."),
    Entry("listshop.form.message_invalid", T, "Long message", "Keep the message under 1,000 characters.", "Shown when the message is too long."),
    Entry(
        "listshop.form.busy", T, "Too many requests",
        "We have had a lot of requests just now. Try again in an hour.",
        "Shown when this connection or the whole site has sent too many.",
    ),
    Entry("listshop.thanks.title", H, "Thanks heading", "Thanks for asking", "Top of the page after the form is sent."),
    Entry(
        "listshop.thanks.body", T, "Thanks text",
        "We read every request and will reply to the email address you gave.",
        "Under the heading after the form is sent. The same words show however many times a shop asks.",
    ),
    Entry("listshop.thanks.back", B, "Back button", "Go to the home page", "Button on the page after the form is sent."),
    Entry(
        "about.listshop", T, "List your shop line",
        "Run a UK shop that sells sealed products? Ask to be listed.",
        "On the how it works page, above the List your shop button.",
    ),

    # Footer ---------------------------------------------------------------
    Entry("footer.follow", T, "Footer follow heading", "Follow us", "Above the social media icons in the footer."),
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
        "The biggest gaps between the cheapest shop and the next one, this week's price drops, the most "
        "clicked products and what just came back in stock. Delivered prices, latest shop check {time}.",
        "Under the deals page heading.",
        placeholders=("time",),
    ),
    Entry("deals.savings.title", H, "Deals savings heading", "Biggest savings right now", "Deals page section heading."),
    Entry("deals.drops.title", H, "Deals drops heading", "Biggest price drops", "Deals page section heading."),
    Entry("deals.lowest.title", H, "Deals lowest heading", "Lowest in 90 days", "Deals page section heading."),
    Entry("deals.lowest.note", T, "Lowest in 90 days explanation",
          "Products at their cheapest delivered price in 90 days, and well under their average.",
          "Under the Lowest in 90 days heading on the Deals page."),
    Entry("deals.lowest.average", T, "Average price", "Average {price}",
          "Above the price on each Lowest in 90 days row: the product's average cheapest price over those days.",
          placeholders=("price",)),
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
    Entry("watchlist.alert.title", H, "Alert me heading", "Alert me", "Heading of the search alert form on the watchlist page."),
    Entry(
        "watchlist.alert.intro", T, "Alert me introduction",
        "Describe what you are after and we will email you when a UK shop has it in stock at your price or less.",
        "Under the Alert me heading.",
    ),
    Entry("watchlist.alert.game", T, "Alert game label", "Game", "Label for the game choice in the alert form."),
    Entry("watchlist.alert.type", T, "Alert type label", "Product type", "Label for the product type choice in the alert form."),
    Entry("watchlist.alert.any_type", T, "Any type choice", "Any type", "First choice in the product type list."),
    Entry("watchlist.alert.lang", T, "Alert language label", "Language", "Label for the language choice in the alert form."),
    Entry("watchlist.alert.any_lang", T, "Any language choice", "Any language", "First choice in the language list."),
    Entry("watchlist.alert.max", T, "Alert price label", "Most you will pay, delivered (£)", "Label for the price box in the alert form."),
    Entry("watchlist.alert.max_hint", T, "Alert price hint", "Leave empty for any price.", "Under the price box in the alert form."),
    Entry("watchlist.alert.email", T, "Alert email label", "Your email address", "Label for the email box in the alert form."),
    Entry("watchlist.alert.button", B, "Set up alert button", "Set up alert", "Button that sends the alert form."),
    Entry(
        "watchlist.alert.note", T, "Alert me privacy note",
        "We use your address for this alert alone. Each email has a link that deletes it at once, and we delete it "
        "six months after you confirm.",
        "Under the alert form.",
        legal=True,
    ),
    Entry(
        "watchlist.alert.what_price", T, "Alert with a price",
        "{things} up to {price} delivered",
        "How an alert with a price reads, in its summary and emails. {things} is built from the choices, for example "
        "English Pokémon booster packs.",
        placeholders=("things", "price"),
    ),
    Entry(
        "watchlist.alert.summary", T, "Alert summary",
        "{what}, in stock",
        "The alert read back in plain English: after asking, on the confirmed page and in the emails.",
        placeholders=("what",),
    ),
    Entry("watchlist.alert.done.title", H, "Alert check your inbox heading", "Check your inbox", "After asking for a search alert."),
    Entry(
        "watchlist.alert.done.body", T, "Alert check your inbox text",
        "We have emailed a link to confirm your alert for {summary}. Nothing else is sent until you press it. "
        "If that address already has this alert, there is nothing more to do.",
        "After asking for a search alert, in place of the form. The same for every address, so the page never says "
        "whether an address already has an alert.",
        placeholders=("summary",),
    ),
    Entry("watchlist.alert.again", B, "Another alert link", "Set up another alert", "After asking for a search alert."),
    Entry("watchlist.alert.problem", H, "Alert not set heading", "Alert not set", "When a search alert could not be taken."),
    Entry(
        "watchlist.alert.invalid", T, "Alert form problem",
        "Check the choices marked below.",
        "When a choice in the alert form is missing or out of range.",
    ),
    Entry(
        "watchlist.alert.price_range", T, "Alert price out of range",
        "Enter a price from {low} to {high}, or leave it empty.",
        "Under the price box when the price is out of range.",
        placeholders=("low", "high"),
    ),
    Entry(
        "watchlist.alert.busy", T, "Too many alert requests",
        "Too many alerts were asked for from here just now. Try again in an hour.",
        "Shown when one internet connection asks for more than ten alerts in an hour.",
    ),
    Entry("watchlist.alert.confirmed.title", H, "Search alert confirmed heading", "Alert confirmed", "Page after the confirm link in a search alert email."),
    Entry(
        "watchlist.alert.confirmed.body", T, "Search alert confirmed text",
        "We will email you when there is something new for {summary}. Your alert is deleted six months from now.",
        "Page after the confirm link in a search alert email.",
        placeholders=("summary",),
        legal=True,
    ),
    Entry(
        "watchlist.alert.gone.body", T, "Search alert link expired text",
        "The alert was stopped or expired. Set it up again on the watchlist page if you still want it.",
        "Page for a search alert confirm link that no longer works.",
    ),
    Entry(
        "watchlist.alert.full.body", T, "Search alert limit reached text",
        "This address already has {count} alerts, the most we keep. Stop one with the link in its email, then "
        "press this confirm link again within a week.",
        "Page for a search alert confirm link when the address already has the most confirmed alerts.",
        placeholders=("count",),
    ),
    Entry(
        "watchlist.alert.stopped.body", T, "Search alert stopped text",
        "This alert has been deleted. Your email address is deleted with it unless another of your alerts "
        "still uses it.",
        "Page after the stop link in a search alert email.",
        legal=True,
    ),
    Entry("watchlist.email.stop.button", B, "Search alert stop button", "Stop this alert",
          "At the foot of every search alert email. Deletes that one alert."),
    Entry(
        "watchlist.email.stop_note", T, "Search alert stop note",
        "Stopping deletes this alert at once. Your address goes with it unless another alert uses it.",
        "At the foot of every search alert email.",
        legal=True,
    ),
    Entry("watchlist.alert.back", B, "Back to the watchlist button", "Back to the watchlist", "On the search alert confirm and stop pages."),
    Entry(
        "watchlist.email.confirm.subject", T, "Search alert confirmation subject",
        "Confirm your alert: {summary}",
        "Subject of the email that confirms a search alert.",
        placeholders=("summary",),
    ),
    Entry(
        "watchlist.email.confirm.preheader", T, "Search alert confirmation preview",
        "One click and we will email you when they are in stock.",
        "Preview line of the confirmation email.",
    ),
    Entry("watchlist.email.confirm.title", H, "Search alert confirmation heading", "Confirm your alert", "In the confirmation email."),
    Entry(
        "watchlist.email.confirm.body", T, "Search alert confirmation text",
        "You asked {site_name} to email you about {summary}.",
        "In the confirmation email.",
        placeholders=("summary", "site_name"),
    ),
    Entry("watchlist.email.confirm.button", B, "Search alert confirmation button", "Confirm my alert", "In the confirmation email."),
    Entry(
        "watchlist.email.confirm.note", T, "Search alert confirmation note",
        "Nothing is sent until you confirm. If this was not you, ignore this email and the request is deleted within "
        "a week. Once confirmed, the alert is deleted after six months.",
        "In the confirmation email.",
        legal=True,
    ),
    Entry(
        "watchlist.email.match.subject", T, "Search alert email subject",
        "In stock: {what}",
        "Subject of the email sent when products match a search alert.",
        placeholders=("what",),
    ),
    Entry(
        "watchlist.email.match.preheader", T, "Search alert email preview",
        "{product}, {price}.",
        "Preview line of the search alert email: the cheapest product. Never names a shop.",
        placeholders=("product", "price"),
    ),
    Entry(
        "watchlist.email.match.body", T, "Search alert email text",
        "These match your alert for {summary}, cheapest first. See every price on {site_name}.",
        "In the search alert email.",
        placeholders=("summary", "site_name"),
    ),
    Entry("watchlist.email.match.delivered", T, "Search alert email price", "from {price} delivered", "Each product's price in a search alert email.", placeholders=("price",)),
    Entry("watchlist.email.match.plus", T, "Search alert email price, delivery unknown", "from {price} plus delivery", "A product's price when its delivery charge is not known.", placeholders=("price",)),
    Entry("watchlist.email.match.checked", T, "Search alert email checked", "Checked {when}", "After each price in a search alert email.", placeholders=("when",)),
    Entry("watchlist.email.match.button", B, "Search alert email button", "See prices", "Button under each product in a search alert email. Opens its page on RipRaptor."),
    Entry(
        "watchlist.email.match.more.one", T, "Search alert email one more",
        "And 1 more",
        "Link to the full list when one more product matches than the email shows.",
        placeholders=("count",),
    ),
    Entry(
        "watchlist.email.match.more.other", T, "Search alert email more",
        "And {count} more",
        "Link to the full list when more products match than the email shows.",
        placeholders=("count",),
    ),
    Entry(
        "watchlist.email.match.footer", T, "Search alert email footer",
        "We email you when something new matches. A product is sent again only if it sold out or went over your "
        "price and came back, and not within a week. Your alert is deleted six months after you confirmed it.",
        "Footer of the search alert email.",
        legal=True,
    ),
    # Your alerts: one place to see and stop every email alert (catalogue/manage_alerts.py) ----------------
    Entry("watchlist.manage.title", H, "Your alerts heading", "Your alerts",
          "On the watchlist page, above the box that emails a link to your alerts, and on the page that link opens."),
    Entry(
        "watchlist.manage.intro", T, "Your alerts introduction",
        "See, change and stop every email alert you have. We email you a link, so there is no account or password.",
        "Under the Your alerts heading on the watchlist page.",
    ),
    Entry("watchlist.manage.jump", T, "Your alerts link under the watchlist title", "See, change or stop your email alerts",
          "Under the watchlist page's title: goes to the Your alerts box further down the page."),
    Entry("watchlist.manage.email", T, "Your alerts email label", "Your email address",
          "Label for the email box in Your alerts."),
    Entry("watchlist.manage.button", B, "Email me a link button", "Email me a link",
          "Sends the Your alerts box. Emails a link to the address if it has alerts."),
    Entry("watchlist.manage.note", T, "Your alerts note", "The link works for an hour.",
          "Under the email box in Your alerts."),
    Entry("watchlist.manage.done.title", H, "Your alerts check your inbox heading", "Check your inbox",
          "After the Your alerts box is sent."),
    Entry(
        "watchlist.manage.done.body", T, "Your alerts check your inbox text",
        "If that address has alerts, we have emailed a link to manage them.",
        "After the Your alerts box is sent. The same for every address, so the page never says whether an "
        "address has alerts.",
    ),
    Entry("watchlist.manage.again", B, "Use another address link", "Use another address",
          "After the Your alerts box is sent. Shows the box again."),
    Entry(
        "watchlist.manage.busy", T, "Too many link requests",
        "Too many links were asked for from here just now. Try again in an hour.",
        "Shown when one internet connection asks for more than ten links in an hour.",
    ),
    Entry(
        "watchlist.manage.page.intro", T, "Your alerts page introduction",
        "Every alert for {address}. Change a price alert, or stop any alert: stopping deletes it at once.",
        "Under the heading of the page the Your alerts link opens.",
        placeholders=("address",),
    ),
    Entry("watchlist.manage.group.stock", H, "Back in stock group", "Back in stock",
          "On the Your alerts page, above the back in stock alerts."),
    Entry("watchlist.manage.group.price", H, "Track price group", "Track price",
          "On the Your alerts page, above the Track price alerts."),
    Entry("watchlist.manage.group.drops", H, "Drop reminders group", "Drop reminders",
          "On the Your alerts page, above the drop reminders."),
    Entry("watchlist.manage.group.search", H, "Search alerts group", "Search alerts",
          "On the Your alerts page, above the alerts set up on the watchlist page."),
    Entry("watchlist.manage.waiting", T, "Alert not confirmed yet", "Waiting for you to confirm",
          "On the Your alerts page, beside an alert whose confirm link has not been pressed."),
    Entry("watchlist.manage.price", T, "Track price limit", "At or under {price}",
          "On the Your alerts page, beside a Track price alert.", placeholders=("price",)),
    Entry("watchlist.manage.until", T, "Track price end", "Until {date}",
          "On the Your alerts page, when a confirmed Track price alert stops looking.", placeholders=("date",)),
    Entry("watchlist.manage.change", B, "Change a price alert", "Change",
          "On the Your alerts page, under each Track price alert: opens its price and time."),
    Entry("watchlist.manage.change.note", T, "Change a price alert note", "It looks from today for the time you choose.",
          "In a Track price alert's Change form on the Your alerts page, above Save changes."),
    Entry("watchlist.manage.change.save", B, "Save a changed price alert", "Save changes",
          "Sends a Track price alert's Change form on the Your alerts page."),
    Entry("watchlist.manage.change.under", T, "Changed price alert already under",
          "It is already at or under that price. Pick a lower price, or buy it now.",
          "On the Your alerts page, when a changed price is not below the cheapest in stock now: the alert would be "
          "sent at once."),
    Entry("watchlist.manage.changed", T, "Price alert changed", "Price alert saved.",
          "On the Your alerts page after a Track price alert's Change form is saved."),
    Entry("watchlist.manage.stop", B, "Stop one alert button", "Stop",
          "On the Your alerts page, beside each alert. Deletes that alert at once."),
    Entry("watchlist.manage.stop_all", B, "Stop all alerts button", "Stop all",
          "At the top of the Your alerts page. Deletes every alert for the address at once."),
    Entry("watchlist.manage.stopped.one", T, "One alert stopped", "Alert stopped and deleted.",
          "On the Your alerts page after a Stop button."),
    Entry("watchlist.manage.stopped.all", T, "All alerts stopped", "All your alerts are stopped and deleted.",
          "On the Your alerts page after Stop all."),
    Entry("watchlist.manage.none", T, "No alerts left", "There are no alerts for this address.",
          "On the Your alerts page when the address has none, or none are left."),
    Entry("watchlist.manage.expired.title", H, "Your alerts link expired heading", "This link has expired",
          "The page for a Your alerts link that is too old or not one of ours."),
    Entry(
        "watchlist.manage.expired.body", T, "Your alerts link expired text",
        "A link from this box works for an hour, and one from an alert email for a week. Enter your address "
        "for a new one.",
        "The page for a Your alerts link that is too old or not one of ours, above the email box.",
    ),
    Entry("watchlist.manage.email.subject", T, "Your alerts email subject", "Your {site_name} alerts",
          "Subject of the email with the link to your alerts.", placeholders=("site_name",)),
    Entry("watchlist.manage.email.preheader", T, "Your alerts email preview",
          "See, change and stop your email alerts. The link works for an hour.",
          "Preview line of the email with the link to your alerts."),
    Entry("watchlist.manage.email.title", H, "Your alerts email heading", "Your alerts",
          "In the email with the link to your alerts."),
    Entry(
        "watchlist.manage.email.body", T, "Your alerts email text",
        "Here is the link you asked for to see and stop your email alerts on {site_name}. It works for an hour.",
        "In the email with the link to your alerts.",
        placeholders=("site_name",),
    ),
    Entry("watchlist.manage.email.button", B, "Your alerts email button", "See my alerts",
          "In the email with the link to your alerts. Opens the Your alerts page."),
    Entry(
        "watchlist.manage.email.note", T, "Your alerts email note",
        "If this was not you, ignore this email and nothing changes.",
        "In the email with the link to your alerts.",
    ),
    Entry(
        "watchlist.manage.email.footer", T, "Your alerts email footer",
        "We send this link only to an address that has alerts.",
        "Footer of the email with the link to your alerts.",
    ),
    Entry("watchlist.manage.email.link", B, "Manage all your alerts link", "Manage all your alerts",
          "At the foot of every alert email. Opens the Your alerts page for that address; the link works for a week."),
    Entry("alerts.form.title", T, "Alert form heading", "Email me when it is back in stock", "Above the email box on a product no shop has in stock."),
    Entry(
        "alerts.form.preorder", T, "Alert form heading (pre-orders too)",
        "Tell me when a shop has it or opens pre-orders",
        "Above the email box on a product no shop has in stock or on pre-order, once pre-order emails are on.",
    ),
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
    Entry("alerts.track.button", B, "Track price button", "Track price",
          "Next to Save on a product page a shop has in stock or on pre-order: opens the price alert form, and sends it."),
    Entry("alerts.track.title", T, "Price alert heading", "Email me when it is in stock at or under my price",
          "Above the Track price form on a product page."),
    Entry("alerts.track.price_label", T, "Price alert price label", "My price, delivered",
          "Label for the price box in the Track price form."),
    Entry("alerts.track.now", T, "Price alert current price", "It is {price} delivered now.",
          "Under the price box, when a shop has it in stock with its delivery known.", placeholders=("price",)),
    Entry("alerts.track.for_label", T, "Price alert time label", "Keep looking for",
          "Label for the choice of how long the price alert looks (1 week, 1 month, 3 months, 6 months)."),
    Entry("alerts.track.email_label", T, "Price alert email label", "Email", "Label for the email box in the Track price form."),
    Entry(
        "alerts.track.note", T, "Price alert privacy note",
        "One email when it is at or under your price, then your address is deleted. It is also deleted when the "
        "time you chose is up. We use it for nothing else.",
        "Under the Track price form.",
        legal=True,
    ),
    Entry("alerts.track.sent", T, "Price alert asked", "Check your inbox and confirm, then we will start looking.",
          "Shown after the Track price form is sent."),
    Entry("alerts.track.under", T, "Price alert already under",
          "It is already at or under that price. Buy now above, or pick a lower price.",
          "Shown when the price asked for is not below the cheapest in stock now: the alert would be sent at once."),
    Entry("alerts.track.price_invalid", T, "Price alert bad price", "Enter a price in pounds, like 6.50.",
          "Shown when the price box does not hold a price from £0.50 to £5,000."),
    Entry(
        "alerts.confirmed.price", T, "Price alert confirmed text",
        "We will look until {date} and email you once if {product} is in stock at a UK shop we check for {price} "
        "or less, delivered. Then we delete your address.",
        "Page after the confirm link of a price alert.",
        placeholders=("date", "product", "price"),
    ),
    Entry("alerts.confirmed.title", H, "Alert confirmed heading", "Alert confirmed", "Page after the confirm link."),
    Entry(
        "alerts.confirmed.body", T, "Alert confirmed text",
        "We will email you once when {product} is back in stock at a UK shop we check, then delete your address.",
        "Page after the confirm link.",
        placeholders=("product",),
    ),
    Entry(
        "alerts.confirmed.preorder", T, "Alert confirmed text (pre-orders too)",
        "We will email you once when {product} is in stock or open for pre-order at a UK shop we check, then delete your address.",
        "Page after the confirm link, for an alert that also hears about pre-orders.",
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
        "feeds.preorder.title", T, "Feed pre-order entry title",
        "Pre-orders open: {product}, {price} at {retailer}",
        "Title of a pre-order opening in the feed.",
        placeholders=("product", "price", "retailer"),
    ),
    Entry(
        "feeds.preorder.body", T, "Feed pre-order entry text",
        "Pre-orders opened at {retailer} on {date} at {time}, {price} delivered.",
        "Text of a pre-order opening in the feed.",
        placeholders=("retailer", "date", "time", "price"),
    ),
    Entry(
        "feeds.preorder.body_unknown", T, "Feed pre-order entry text (delivery unknown)",
        "Pre-orders opened at {retailer} on {date} at {time}, {price} plus delivery.",
        "Text of a pre-order opening in the feed when the shop's delivery charge is not known.",
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
        "Shown while Amazon links carry our tag: on the terms page, in the footer of every page and beside "
        "the Amazon buttons on product pages. Amazon requires this wording.",
        legal=True,
    ),
    Entry("terms.prices.title", H, "Prices heading", "Prices", "Heading on the terms page."),
    Entry("terms.independence.title", H, "Independence heading", "Independence", "Heading on the terms page."),
    Entry("terms.data.title", H, "Data heading", "Your data", "Heading on the terms page."),
    Entry(
        "terms.data.body", P, "Data explanation",
        "RipRaptor does not ask you to create an account. Products you save, "
        "products you view, games you browse and searches you make are kept in your browser, not "
        "by us, and you can clear them from the home page. Your browser uses them to put your games "
        "first on the home page, without telling us. Our own count of clicks to retailers "
        "does not record who clicked. One small cookie of ours remembers only that your browser "
        "has visited before, so we can count returning visitors. It holds nothing "
        "about you.\n\n"
        "If you ask to be emailed when a product is back in stock, or with Track price when it is "
        "in stock at or under a price you choose, we keep your email address (and that price and "
        "how long to look) for that alone. Nothing is sent until you confirm by the link we email "
        "you. You get one email when a shop has the product (at or under your price, for Track "
        "price), and your address is then deleted. A Track price request is also deleted when the "
        "time you chose is up. Unconfirmed requests are deleted after a week and any still waiting "
        "after six months are deleted too. Every email has a link that deletes your "
        "request at once. The emails are sent through Zoho ZeptoMail.\n\n"
        "If you ask for a reminder about a drop on the drop tracker, we keep your email "
        "address with that set for that alone. Nothing is sent until you confirm by the link "
        "we email you. You then hear from us only about that set: when a UK shop opens "
        "pre-orders, the day before it comes out (again if its date moves) and on the day. "
        "Your address is deleted after the last one, or once the drop has passed. Unconfirmed requests are deleted after a week and "
        "any still waiting after a year are deleted too. Every email has a link that deletes "
        "your request at once. These emails go through Zoho ZeptoMail too.\n\n"
        "If you message us, we keep your message, and your name and email address if you "
        "give them, to answer you and for nothing else. Your conversation is deleted six "
        "months after its last message. A cookie in your browser remembers the private "
        "link to your conversation.",
        "Data section of the terms page.",
        legal=True,
    ),
    Entry("terms.refer.title", H, "Keyring offer heading", "Refer a friend keyring offer",
          "Heading on the terms page, shown while the keyring offer runs or has people in it."),
    Entry(
        "terms.refer.body", P, "Keyring offer terms",
        "The offer is run by RipRaptor and is open to UK residents only. Under 18s need a parent or guardian's "
        "permission to take part.\n\n"
        "Ask for your link on the refer a friend page and confirm your email address. When a friend follows your "
        "link and confirms their own email address, the referral is complete and you each get one RipRaptor 3D "
        "keyring. Your friend must be new to RipRaptor offers. Each person can earn one keyring, and each email "
        "address and each household can have one keyring. We check a second address at the same postcode "
        "before posting.\n\n"
        "Keyrings are free and given whilst stocks last. We post them to UK addresses only and cannot promise a "
        "delivery time. There is no cash alternative. We check referrals and may hold or cancel any that look "
        "automated, repeated or made up. We may change or cancel the offer at any time.",
        "Terms page: the keyring offer's rules.",
        legal=True,
    ),
    Entry(
        "terms.data.refer", P, "Keyring offer data",
        "If you take part in the refer a friend offer, we keep your email address to send your link, confirm the "
        "referral and ask where to post your keyring, and your name and postal address only to post it. We never "
        "email a friend on your behalf: each person gives us their own address, and an address typed into the "
        "offer is sent only emails asking its owner to confirm it, at most three a day, and nothing else until "
        "they do. We keep a "
        "one-way fingerprint of the connection each form was sent from, to tell people from automated sign-ups, "
        "and delete it with the rest.\n\n"
        "Unconfirmed sign-ups are deleted after 14 days. A link that brings no completed referral is deleted 90 "
        "days after it was confirmed. Address links last 30 days and an unused one is deleted 14 days after that. "
        "Your email address, name and postal address are deleted 60 days after your keyring is posted, 90 days "
        "after you sent your address if it has not been posted by then, or 14 days after a referral is "
        "cancelled; a cancelled referral's postal address is deleted at once. So nobody takes part twice, we keep a one-way fingerprint of each email "
        "address, postal address and postcode that has had a keyring; it cannot be turned back into the address. Every email has a "
        "link to delete your details.",
        "Data section of the terms page, shown while the keyring offer runs or has people in it.",
        legal=True,
    ),
    Entry(
        "terms.data.search_alerts", P, "Search alerts data",
        "If you set up an alert on the watchlist page, we keep your email address with what you described (the "
        "game, and the type, language and price if you chose them) for that alone. Nothing is sent until you "
        "confirm by the link we email you. We then email you when products that match are in stock at a UK shop, "
        "and keep a list of the products we have emailed you about so we do not send them again. Unconfirmed "
        "requests are deleted after a week, and alerts are deleted six months after you confirmed them, with that "
        "list. Every email has a link that deletes your alert at once. To stop too many requests we count them "
        "under a one-way fingerprint of your connection and of your address, kept for a day at most. These "
        "emails go through Zoho ZeptoMail too.",
        "Data section of the terms page, after the main explanation: what a search alert keeps.",
        legal=True,
    ),
    Entry(
        "terms.data.manage", P, "Your alerts data",
        "You can see and stop all your alerts from the watchlist page, using a link we email you, and we count "
        "requests for that link under the same one-way fingerprints.",
        "Data section of the terms page, after the search alerts: the Your alerts link.",
        legal=True,
    ),
    Entry(
        "terms.data.listshop", P, "Shop enquiries",
        "If you ask us to list your shop, we keep your name, your shop's website, your email address and "
        "your message to reply to you and for nothing else. We do not add you to a mailing list. Your "
        "enquiry is deleted a year after we have dealt with it, or two years after you sent it if we never do.",
        "Data section of the terms page, after the main explanation: what the List your shop form keeps.",
        legal=True,
    ),
    Entry(
        "terms.data.shops", P, "Shop and affiliate cookies",
        "When you follow a link to a shop, the shop or the affiliate network it uses (Amazon Associates, "
        "eBay Partner Network, Awin or Rakuten Advertising) may set its own cookies to record that you came "
        "from RipRaptor, so it can pay us a commission if you buy. Our pages also load a script from Awin that "
        "can turn shop links into Awin affiliate links. These companies do not tell us who you are, and their "
        "own privacy policies apply to what they collect.",
        "Data section of the terms page, after the main explanation: the cookies shops and affiliate networks set.",
        legal=True,
    ),
    Entry(
        "terms.analytics", P, "Analytics explanation",
        "We use Google Analytics only if you allow it, on the card that asks you. It tells us how many "
        "people visit, which pages and searches they use and how often they go to a shop. Google then "
        "receives the address of each page you open, including your searches and watchlist, and sets "
        "cookies that last up to two years to tell visits apart. Google says it does not store your IP "
        "address. It keeps the detailed records for at most 14 months and may process them in the "
        "United States. We have turned off Google signals and advertising features. Links to your "
        "messages and stock alerts are private and are never sent to Google. Without your permission "
        "Google Analytics does not load. To change your answer, use Analytics cookies at the foot of "
        "the site's pages.",
        "Data section of the terms page, shown while Google Analytics is set up.",
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
    Entry(
        "errors.410.title", H, "Product removed heading",
        "{product} is no longer listed",
        "Heading on the page shown at the address of a product we no longer list (it answers 410 Gone, so "
        "search engines drop it).",
        placeholders=("product",),
    ),
    Entry(
        "errors.410.body", P, "Product removed text",
        "We no longer compare prices for this product, so its page has been taken down. Search for what we "
        "compare now.",
        "Text under the heading on a removed product's page, above a search box and, when the product had them, "
        "links to its set and game. Most removed products have neither, so the text should not point to them.",
    ),
    Entry(
        "errors.410.set", B, "Product removed set link",
        "{set} products",
        "Link to the removed product's set.",
        placeholders=("set",),
    ),
    Entry(
        "errors.410.game", B, "Product removed game link",
        "All {game} products",
        "Link to the removed product's game.",
        placeholders=("game",),
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
        "Compare delivered UK prices for sealed Pokemon, Magic, One Piece and Yu-Gi-Oh booster boxes, "
        "Elite Trainer Boxes, bundles and decks, with stock at each shop.",
        "Search engine description for the home page and pages without their own. Search engines show "
        "120 to 160 characters.",
    ),
    Entry(
        "meta.product.title", T, "Product page title",
        "{product}: UK prices",
        "Browser tab title for product pages with no current price. {product} carries the set's code when the "
        "name does not. The site name is added after it.",
        placeholders=("product",),
    ),
    Entry(
        "meta.product.title_short", T, "Product page title, short form",
        "{product} UK",
        "Used instead of the product page title with no current price when that would run past about 60 "
        "characters.",
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
        "Browser tab title for product pages whose cheapest price is in stock. {product} carries the set's code "
        "when the name does not. When it runs past about 60 characters the next shorter form is used. The site "
        "name is added after it.",
        placeholders=("product", "price", "retailer", "count"),
    ),
    Entry(
        "meta.product.title_priced_uk", T, "Product page title with a price, shorter",
        "{product} UK from {price} delivered",
        "Used instead of the product page title with a price when that would run past about 60 characters.",
        placeholders=("product", "price"),
    ),
    Entry(
        "meta.product.title_priced_short", T, "Product page title with a price, shorter still",
        "{product} UK from {price}",
        "Used when the shorter product page title with a price would still run past about 60 characters.",
        placeholders=("product", "price"),
    ),
    Entry(
        "meta.product.title_priced_shortest", T, "Product page title with a price, shortest",
        "{product} from {price}",
        "Used for long product names, when every other form would run past about 60 characters.",
        placeholders=("product", "price"),
    ),
    Entry(
        "meta.product.title_preorder", T, "Product page title with a pre-order price",
        "{product} pre-order from {price} delivered: UK price comparison",
        "Browser tab title for product pages whose cheapest price is a pre-order. When it runs past about 60 "
        "characters the next shorter form is used. The site name is added after it.",
        placeholders=("product", "price"),
    ),
    Entry(
        "meta.product.title_preorder_uk", T, "Product page title with a pre-order price, shorter",
        "{product} UK pre-order from {price} delivered",
        "Used instead of the product page title with a pre-order price when that would run past about 60 "
        "characters.",
        placeholders=("product", "price"),
    ),
    Entry(
        "meta.product.title_preorder_short", T, "Product page title with a pre-order price, shorter still",
        "{product} UK pre-order from {price}",
        "Used when the shorter product page title with a pre-order price would still run past about 60 "
        "characters.",
        placeholders=("product", "price"),
    ),
    Entry(
        "meta.product.title_preorder_shortest", T, "Product page title with a pre-order price, shortest",
        "{product} pre-order from {price}",
        "Used for long product names, when every other pre-order form would run past about 60 characters.",
        placeholders=("product", "price"),
    ),
    Entry(
        "meta.product.description_priced", T, "Product page description with a price",
        "Cheapest {product} today is {price} delivered at {retailer}. Compare {shops} with stock, delivery "
        "and when each price was checked.",
        "Search engine description for product pages whose cheapest price is in stock. Cut to 160 characters "
        "at a whole sentence or word.",
        placeholders=("product", "price", "retailer", "count", "shops"),
    ),
    Entry(
        "meta.product.description_preorder", T, "Product page description with a pre-order price",
        "{product} is on pre-order from {price} delivered at {retailer}. Compare {shops} with delivery and "
        "when each price was checked.",
        "Search engine description for product pages that shops only take pre-orders for, so it never says "
        "in stock. Cut to 160 characters at a whole sentence or word.",
        placeholders=("product", "price", "retailer", "count", "shops"),
    ),
    Entry(
        "meta.product.description_preorder_dated", T, "Product page description with a pre-order price and date",
        "{product} is due out on {date}, on pre-order from {price} delivered at {retailer}. Compare {shops} "
        "with delivery and when each price was checked.",
        "Search engine description for product pages that shops only take pre-orders for, when its release "
        "date is still to come. Cut to 160 characters at a whole sentence or word.",
        placeholders=("product", "price", "retailer", "count", "shops", "date"),
    ),
    Entry(
        "meta.product.description_dated", T, "Product page description before release, no price",
        "{product} is due out on {date}. UK shop prices appear here once shops list it, with delivery and when "
        "each price was checked.",
        "Search engine description for a product with no current price whose release date is still to come.",
        placeholders=("product", "date"),
    ),
    Entry(
        "meta.product.description_dated_listed", T, "Product page description before release, listed, no price",
        "{product} is due out on {date}. Compare UK shop prices, delivery and when each price was checked.",
        "Search engine description for a product whose release date is still to come when shops list it but "
        "none has a price to quote (sold out, or delivery unknown).",
        placeholders=("product", "date"),
    ),
    Entry(
        "meta.product.description_preorder_stock", T, "Product page description with a cheaper pre-order",
        "{product} is on pre-order from {price} delivered at {retailer}, or in stock from {stock_price} at "
        "{stock_retailer}. Compare {shops} with stock, delivery and when each price was checked.",
        "Search engine description for product pages whose cheapest price is a pre-order while another shop "
        "has it in stock. Cut to 160 characters at a whole sentence or word.",
        placeholders=("product", "price", "retailer", "count", "shops", "stock_price", "stock_retailer"),
    ),
    Entry(
        "meta.watchlist.title", T, "Watchlist page title",
        "Your watchlist",
        "Browser tab title for the watchlist page. The site name is added after it.",
    ),
    Entry(
        "meta.drops.title", T, "Drops page title",
        "TCG drops: release dates, pre-orders and new releases in the UK",
        "Browser tab title for the drops page. The site name is added after it.",
    ),
    Entry(
        "meta.drops.description", T, "Drops page description",
        "Release dates for upcoming sealed Pokemon, Magic, One Piece, Yu-Gi-Oh and Lorcana sets, with the "
        "cheapest UK price and an email reminder before each drop.",
        "Search engine description for the drops page.",
    ),
    Entry(
        "meta.listshop.description", T, "List your shop page description",
        "Run a UK shop selling sealed trading card game products? Ask RipRaptor to list your prices.",
        "Search engine description for the List your shop page.",
    ),
    Entry(
        "meta.drops.out.title", T, "Out now page title",
        "New TCG releases in stock in the UK",
        "Browser tab title for Out now on the drops page. The site name is added after it.",
    ),
    Entry(
        "meta.drops.out.description", T, "Out now page description",
        "Sealed Pokemon, Magic, One Piece, Yu-Gi-Oh and Lorcana products just released or just listed by UK "
        "shops and in stock now, with the cheapest delivered price.",
        "Search engine description for Out now on the drops page.",
    ),
    Entry(
        "meta.and", T, "And between product types",
        "and",
        "Joins the last two product types named in a title, as in booster boxes, ETBs and packs.",
    ),
    Entry("meta.shops.one", T, "Shop count in descriptions (one)", "1 UK shop",
          "In search engine descriptions when one shop is counted."),
    Entry("meta.shops.other", T, "Shop count in descriptions (more than one)", "{count} UK shops",
          "In search engine descriptions when more than one shop is counted.", placeholders=("count",)),
    Entry("meta.products.one", T, "Product count in descriptions (one)", "1 sealed {game} product",
          "In search engine descriptions when one product is in stock.", placeholders=("count", "game")),
    Entry("meta.products.other", T, "Product count in descriptions (more than one)", "{count} sealed {game} products",
          "In search engine descriptions when more than one product is in stock.", placeholders=("count", "game")),
    Entry(
        "meta.game.title", T, "Game page title",
        "Cheapest {game} {types} in stock: UK price comparison",
        "Browser tab title for game pages while a shop has something in stock. {types} names the product types "
        "in stock now, as many as fit in about 60 characters. The site name is added after it.",
        placeholders=("game", "types"),
    ),
    Entry(
        "meta.game.title_short", T, "Game page title, short form",
        "Cheapest {game} {types} in stock UK",
        "Used instead of the game page title when that would run past about 60 characters with even one "
        "product type named. The site name is added after it.",
        placeholders=("game", "types"),
    ),
    Entry(
        "meta.game.title_plain", T, "Game page title with nothing in stock",
        "{game} sealed products: UK price comparison",
        "Browser tab title for game pages when no shop has anything in stock. The site name is added after it.",
        placeholders=("game",),
    ),
    Entry(
        "meta.game.description_stock", T, "Game page description",
        "{products} in stock at {shops}, from {price} delivered. Best price first, with most shops checked "
        "every hour.",
        "Search engine description for game pages while a shop has something in stock. {price} is the cheapest "
        "delivered price in stock now. Cut to 160 characters at a whole sentence or word.",
        placeholders=("game", "products", "shops", "price", "count"),
    ),
    Entry(
        "meta.game.description", T, "Game page description with nothing in stock",
        "Compare delivered UK prices and pre-orders for sealed {game} products, with stock at each shop and the "
        "time each price was checked.",
        "Search engine description for game pages when no shop has anything in stock.",
        placeholders=("game",),
    ),
    Entry(
        "meta.set.name_code", T, "Set name with its code",
        "{set} ({code})",
        "How set page titles and descriptions give a set with a code, such as OP-18 or EB05. When a title "
        "would run past about 60 characters with the code, it is given without.",
        placeholders=("set", "code"),
    ),
    Entry(
        "meta.set.title", T, "Set page title",
        "{set} {types} UK: cheapest prices",
        "Browser tab title for set pages while a shop has something in stock. {types} names the product types "
        "in stock now, as many as fit in about 60 characters. The site name is added after it.",
        placeholders=("set", "game", "types"),
    ),
    Entry(
        "meta.set.title_short", T, "Set page title, short form",
        "{set} {types}: UK prices",
        "Used instead of the set page title when that would run past about 60 characters with even one "
        "product type named. The site name is added after it.",
        placeholders=("set", "game", "types"),
    ),
    Entry(
        "meta.set.title_upcoming", T, "Set page title before release",
        "{set} pre-orders UK: release date and prices",
        "Browser tab title for a set still to come, with a release date, that a shop takes pre-orders for and "
        "none has in stock yet.",
        placeholders=("set", "game"),
    ),
    Entry(
        "meta.set.title_upcoming_short", T, "Set page title before release, short form",
        "{set} pre-orders UK and release date",
        "Used instead of the set page title before release when that would run past about 60 characters.",
        placeholders=("set", "game"),
    ),
    Entry(
        "meta.set.title_upcoming_shortest", T, "Set page title before release, shortest form",
        "{set} pre-orders UK",
        "Used when the short set page title before release would still run past about 60 characters.",
        placeholders=("set", "game"),
    ),
    Entry(
        "meta.set.title_plain", T, "Set page title with nothing in stock",
        "{set} sealed products: UK price comparison",
        "Browser tab title for set pages when no shop has anything in stock. The site name is added after it.",
        placeholders=("set", "game"),
    ),
    Entry(
        "meta.set.title_plain_short", T, "Set page title with nothing in stock, short form",
        "{set} sealed products UK",
        "Used instead of the set page title with nothing in stock when that would run past about 60 characters.",
        placeholders=("set", "game"),
    ),
    Entry(
        "meta.set.description_stock", T, "Set page description",
        "{set}: {products} in stock at {shops}, from {price} delivered. Best price first, with most shops "
        "checked every hour.",
        "Search engine description for set pages while a shop has something in stock. {price} is the cheapest "
        "delivered price in stock now. Cut to 160 characters at a whole sentence or word.",
        placeholders=("set", "game", "products", "shops", "price", "count"),
    ),
    Entry(
        "meta.set.description_upcoming", T, "Set page description before release",
        "{game} {set} is due out on {date}, on pre-order at {shops}. Compare pre-order prices and delivery for "
        "its sealed products, with most shops checked every hour.",
        "Search engine description for a set still to come, with a release date, that a shop takes pre-orders "
        "for and none has in stock yet. {shops} counts the shops taking pre-orders.",
        placeholders=("set", "game", "date", "shops"),
    ),
    Entry(
        "meta.set.title_dated", T, "Set page title before release, no pre-orders",
        "{set} release date",
        "Browser tab title for a set still to come, with a release date, that no shop takes pre-orders for yet. "
        "The site name is added after it.",
        placeholders=("set", "game"),
    ),
    Entry(
        "meta.set.description_dated", T, "Set page description before release, no pre-orders",
        "{game} {set} is due out on {date}. UK shop prices for its sealed products appear here once shops "
        "list them.",
        "Search engine description for a set still to come, with a release date, that no shop takes "
        "pre-orders for yet and no shop lists.",
        placeholders=("set", "game", "date"),
    ),
    Entry(
        "meta.set.description_dated_listed", T, "Set page description before release, listed, no pre-orders",
        "{game} {set} is due out on {date}. Compare UK shop prices for its sealed products, with delivery and "
        "when each price was checked.",
        "Search engine description for a set still to come, with a release date, whose products shops list "
        "but none takes pre-orders for yet.",
        placeholders=("set", "game", "date"),
    ),
    Entry(
        "meta.set.description", T, "Set page description with nothing in stock",
        "Compare delivered UK prices and pre-orders for sealed {game} {set} products, with stock at each shop "
        "and the time each price was checked.",
        "Search engine description for set pages when no shop has anything in stock.",
        placeholders=("set", "game"),
    ),
    # Insights page in admin: Why traffic changed ---------------------------
    Entry("insights.why.heading", H, "Why traffic changed heading", "Why traffic changed",
          "Heading of the explainer at the top of the Insights page."),
    Entry("insights.why.intro", T, "Why traffic changed intro",
          "People only: visitors who look automated are left out. Part of a day is compared with the same hours "
          "of another day.",
          "Under the heading of the explainer."),
    Entry("insights.why.period_today", T, "Today so far", "Today to {time}",
          "Names the part of today being compared.", placeholders=("time",)),
    Entry("insights.why.period_yesterday", T, "Yesterday", "Yesterday ({date})",
          "Names yesterday as a whole day.", placeholders=("date",)),
    Entry("insights.why.basis_last_week", T, "Same time last week", "by the same time last {weekday}",
          "What today so far is compared with.", placeholders=("weekday",)),
    Entry("insights.why.basis_yesterday", T, "Same time yesterday", "by the same time yesterday",
          "What today so far is compared with when last week cannot be used."),
    Entry("insights.why.basis_weekdays", T, "Average of the same weekday", "on an average {weekday} (last {count})",
          "What yesterday is compared with.", placeholders=("weekday", "count")),
    Entry("insights.why.basis_week", T, "Average of the week before", "on an average day of the {count} before",
          "What yesterday is compared with when there are too few of the same weekday.", placeholders=("count",)),
    Entry("insights.why.part_day", T, "Today is not over",
          "{period}: {now} people so far. The day is not over, so it can't be set beside whole days, and there "
          "is no earlier day with arrival times to compare the same hours with yet.",
          "Shown when today so far has nothing to compare with.", placeholders=("period", "now")),
    Entry("insights.why.up", T, "More people than usual",
          "{period}: {now} people, against {then} {basis}. That is {diff} more ({percent}%).",
          "A comparison that went up by more than day to day noise.",
          placeholders=("period", "now", "then", "basis", "diff", "percent")),
    Entry("insights.why.down", T, "Fewer people than usual",
          "{period}: {now} people, against {then} {basis}. That is {diff} fewer ({percent}%).",
          "A comparison that went down by more than day to day noise.",
          placeholders=("period", "now", "then", "basis", "diff", "percent")),
    Entry("insights.why.up_from_none", T, "More people than none",
          "{period}: {now} people, against none {basis}.",
          "A comparison with a time when nobody had arrived yet, so there is no percentage.",
          placeholders=("period", "now", "basis")),
    Entry("insights.why.same", T, "About the same as usual",
          "{period}: {now} people, against {then} {basis}. That is within normal day to day change.",
          "A comparison within day to day noise.", placeholders=("period", "now", "then", "basis")),
    Entry("insights.why.driver_up", T, "Where the rise came from",
          "Most of the rise came from {name}: {then} then, {now} now, {diff} more, {share}% of the change.",
          "Names the source, page, device or country behind a rise.",
          placeholders=("name", "then", "now", "diff", "share")),
    Entry("insights.why.driver_down", T, "Where the drop came from",
          "Most of the drop came from {name}: {then} then, {now} now, {diff} fewer, {share}% of the change.",
          "Names the source, page, device or country behind a drop.",
          placeholders=("name", "then", "now", "diff", "share")),
    Entry("insights.why.driver_up_over", T, "Where the rise came from, more than all of it",
          "Most of the rise came from {name}: {then} then, {now} now, {diff} more. That is more than the whole "
          "rise of {total}, because {other} fell by {other_diff}.",
          "Names the group behind a rise when it rose by more than the total, because another group fell.",
          placeholders=("name", "then", "now", "diff", "total", "other", "other_diff")),
    Entry("insights.why.driver_down_over", T, "Where the drop came from, more than all of it",
          "Most of the drop came from {name}: {then} then, {now} now, {diff} fewer. That is more than the whole "
          "drop of {total}, because {other} rose by {other_diff}.",
          "Names the group behind a drop when it fell by more than the total, because another group rose.",
          placeholders=("name", "then", "now", "diff", "total", "other", "other_diff")),
    Entry("insights.why.spread", T, "No single cause",
          "The change is spread out: no one source, landing page, device or country explains a quarter of it.",
          "Shown when nothing stands out as the cause."),
    Entry("insights.why.check_search", T, "Check search",
          "Check next: Google Search Console, Performance. This site cannot see search rankings, so the data "
          "here can't tell why search visitors changed.",
          "Under a change driven by search engines."),
    Entry("insights.why.check_social", T, "Check a social site",
          "Check next: look for a recent post or share on {name}. The data here shows where visitors came "
          "from, not why.",
          "Under a change driven by a social site.", placeholders=("name",)),
    Entry("insights.why.check_other", T, "Check other sites",
          "Check next: the Sites that sent visitors table below names the site. The data here can't tell why "
          "it sent them.",
          "Under a change driven by other sites."),
    Entry("insights.why.check_typed", T, "Check typed visits",
          "Typed or bookmarked visits carry no source, so the data here can't tell where they came from.",
          "Under a change driven by typed or bookmarked visits."),
    Entry("insights.why.check_other_driver", T, "Check a page, device or country",
          "The data here shows where the change landed, not why it happened.",
          "Under a change driven by a landing page, device or country."),
    Entry("insights.why.burst_days", T, "Bursts left out",
          "Left out of the averages as likely automated: {names}.",
          "Lists burst days.", placeholders=("names",)),
    Entry("insights.why.burst_check", T, "Bursts check",
          "The data here can't tell who sent them. They are left out so they do not hide real changes.",
          "Under the burst days."),
    Entry("insights.why.burst_now", T, "Burst in the period",
          "{period} looks automated: {reason}. Its totals are not all people.",
          "Shown when the day being explained is a burst.", placeholders=("period", "reason")),
    Entry("insights.why.reason_views", T, "Burst: many pages each", "{views} page views from {visitors} visitors",
          "Why a day looks automated.", placeholders=("views", "visitors")),
    Entry("insights.why.reason_clicks", T, "Burst: visitors without clicks", "{visitors} visitors but {clicks} clicks",
          "Why a day looks automated.", placeholders=("visitors", "clicks")),
    Entry("insights.why.reason_few", T, "Burst: a few visitors", "{count} visitors opened {share}% of the pages",
          "Why a day looks automated.", placeholders=("count", "share")),
    Entry("insights.why.reason_few_one", T, "Burst: one visitor", "1 visitor opened {share}% of the pages",
          "Why a day looks automated, when it is one visitor.", placeholders=("share",)),
    Entry("insights.why.gap", T, "No visitors recorded",
          "No visitors were recorded from {start} to {end}, when about {expected} usually arrive. Check the site "
          "loads and that counting works.",
          "Shown when hours pass with no visitors.", placeholders=("start", "end", "expected")),
    Entry("insights.why.no_clicks", T, "No clicks to shops",
          "No clicks to shops from {start} to {end} while {visitors} people arrived, when about {expected} clicks "
          "were expected. Check that the Buy now links work.",
          "Shown when visitors arrive but nobody clicks to a shop.",
          placeholders=("start", "end", "visitors", "expected")),
    Entry("insights.why.no_clicks_today", T, "No clicks to shops today",
          "No clicks to shops today up to {time} while {visitors} people arrived, when about {expected} clicks "
          "were expected. Check that the Buy now links work.",
          "Shown when today's visitors have made no clicks and their arrival times are not known.",
          placeholders=("time", "visitors", "expected")),
    Entry("insights.why.no_clicks_day", T, "No clicks to shops all day",
          "No clicks to shops on {day} while {visitors} people arrived, when about {expected} clicks were "
          "expected. Check that the Buy now links work.",
          "Shown when a whole day of visitors made no clicks.", placeholders=("day", "visitors", "expected")),
    Entry("insights.why.clicks_doubtful", T, "More clicks than people",
          "More clicks to shops than people on {names}. Some of those clicks were probably automated, so these "
          "days are left out of the usual clicks per person.",
          "Lists days with more clicks than people.", placeholders=("names",)),
    Entry("insights.why.clicks_doubtful_check", T, "More clicks than people check",
          "The data here can't tell who clicked. If clicks fall later while the Buy now links work, a script "
          "that was clicking them may have stopped.",
          "Under the days with more clicks than people."),
    Entry("insights.why.conversion", T, "Fewer clicks per visitor",
          "{period}: {now} clicks per 100 people, against {then} usually. Check the Buy now links and prices on "
          "the most visited products.",
          "Shown when clicks per visitor fall sharply.", placeholders=("period", "now", "then")),
    Entry("insights.why.worker", T, "Shop reader stopped",
          "The background shop reader last ran at {time}, so prices may be out of date.",
          "Shown when the worker has stopped.", placeholders=("time",)),
    Entry("insights.why.stale", T, "Shops not read",
          "{count} shops have not been read on time: {names}. Old prices can cost clicks.",
          "Shown when shops are overdue a read.", placeholders=("count", "names")),
    Entry("insights.why.history", T, "Arrival times start",
          "Arrival times are recorded from {date}. The first whole day with them is {first}, so same-time "
          "comparisons with last week start on {end}.",
          "Shown until a week of arrival times has been recorded.", placeholders=("date", "first", "end")),
    Entry("insights.why.healthy", T, "Counting looks healthy",
          "Counting, Buy now clicks and shop reads look normal.",
          "Shown when every health check ran and found no problem."),
    Entry("insights.why.healthy_no_hours", T, "Healthy, no hourly check",
          "Buy now clicks so far today and shop reads look normal. Visitors hour by hour can't be checked until "
          "arrival times have been recorded for a day.",
          "Shown when no problem was found but today's visitors have no arrival times to check hour by hour."),
    Entry("insights.why.healthy_no_clicks", T, "Healthy, clicks not checked",
          "Shop reads look normal. Buy now clicks can't be checked yet: there is no usual clicks per person to "
          "compare with.",
          "Shown when no problem was found but there was nothing to check today's clicks against."),
    Entry("insights.why.too_little", T, "Too little data",
          "Not enough visitors yet to compare. Come back after a few full days.",
          "Shown before there is enough history."),
    # Insights page in admin: clicks to shops ------------------------------
    Entry("insights.clicks.heading", H, "Clicks to shops heading", "Clicks to shops, last 7 days",
          "Heading of the table of clicks from people and direct hits on the Insights page."),
    Entry("insights.clicks.intro", T, "Clicks to shops intro",
          "A click from people came from a Buy button on our pages and carries our tracking. A direct hit came "
          "with no link from our pages, from a robot or a prefetch, or from a connection past {cap} clicks in an "
          "hour. It still reaches the shop, at the plain address with no tracking, and no other figure counts it.",
          "Under the heading of the clicks table on the Insights page.", placeholders=("cap",)),
    Entry("insights.clicks.people", T, "Clicks from people", "Clicks from people",
          "Column heading: clicks counted and sent with our tracking."),
    Entry("insights.clicks.direct", T, "Direct hits", "Direct hits sent on without tracking",
          "Column heading: clicks sent to the plain shop address and not counted."),
    # Dashboard (owner only) ----------------------------------------------
    Entry("dashboard.title", H, "Dashboard title", "Dashboard", "Title of the owner's dashboard at /dashboard/."),
    Entry("dashboard.intro", T, "Dashboard intro",
          "How the site is doing, read from the database. Tap a figure to see more or to act on it.",
          "Under the dashboard title."),
    Entry("dashboard.updated", T, "Dashboard updated time", "Updated {clock}.",
          "When the figures were read. Each part is read again after a minute.", placeholders=("clock",)),
    Entry("dashboard.refresh", B, "Dashboard refresh link", "Read them again now",
          "Link beside the updated time. It reads every figure again."),
    Entry("dashboard.admin", B, "Dashboard admin link", "Open admin", "Link at the top of the dashboard."),
    Entry("dashboard.no_data", T, "Dashboard nothing recorded", "No data yet.",
          "Shown where a part of the dashboard has nothing recorded."),
    Entry("dashboard.table", B, "Dashboard show the numbers", "Show the numbers",
          "Opens the table under a chart."),
    Entry("dashboard.day", T, "Dashboard day column", "Day", "Column heading in the tables under the charts."),
    Entry("dashboard.so_far", T, "Dashboard today so far", "so far", "Under today's bar in the charts."),
    Entry("dashboard.burst", T, "Dashboard burst day", "burst",
          "Under a day's bar when the day looks automated."),
    Entry("dashboard.kpi.title", H, "Dashboard today heading", "Today at a glance",
          "Read out by screen readers for the four figures at the top."),
    Entry("dashboard.kpi.people", T, "Dashboard people today", "People today",
          "Figure at the top: visitors today so far, people only."),
    Entry("dashboard.kpi.clicks", T, "Dashboard clicks today", "Clicks to shops today",
          "Figure at the top: clicks from people to shops today so far."),
    Entry("dashboard.kpi.checks", T, "Dashboard things to check", "Things to check",
          "Figure at the top: rows waiting on Things to check."),
    Entry("dashboard.kpi.claude", T, "Dashboard Claude spend", "Claude this month",
          "Figure at the top: what Claude has cost this month."),
    Entry("dashboard.kpi.against", T, "Dashboard compared with", "Against {then} {basis}.",
          "Under a figure for today: what it is compared with.", placeholders=("then", "basis")),
    Entry("dashboard.change.up", T, "Dashboard up", "up on before",
          "Read out by screen readers after the green up arrow beside a figure."),
    Entry("dashboard.change.down", T, "Dashboard down", "down on before",
          "Read out by screen readers after the red down arrow beside a figure."),
    Entry("dashboard.kpi.same", T, "Dashboard about the same", "About the same",
          "Beside a figure for today when it is within day to day change."),
    Entry("dashboard.kpi.so_far", T, "Dashboard nothing to compare", "So far, to {clock}. No earlier day to set it against yet.",
          "Under a figure for today when there is nothing to compare it with.", placeholders=("clock",)),
    Entry("dashboard.kpi.burst", T, "Dashboard automated today", "Today looks automated, so this is not all people.",
          "Under the people figure when today is a burst."),
    Entry("dashboard.kpi.checks_none", T, "Dashboard nothing to check", "Nothing waiting for you.",
          "Under the things to check figure when it is nought."),
    Entry("dashboard.kpi.checks_lists.one", T, "Dashboard waiting in one list", "Waiting in 1 list.",
          "Under the things to check figure.", placeholders=("count",)),
    Entry("dashboard.kpi.checks_lists.other", T, "Dashboard waiting in lists", "Waiting in {count} lists.",
          "Under the things to check figure.", placeholders=("count",)),
    Entry("dashboard.kpi.claude_of", T, "Dashboard Claude limit", "Of a {limit} limit.",
          "Under the Claude figure.", placeholders=("limit",)),
    Entry("dashboard.visitors.title", H, "Dashboard visitors heading", "Visitors, last 7 days",
          "Heading of the visitors part."),
    Entry("dashboard.visitors.intro", T, "Dashboard visitors intro",
          "People only: visitors who read the site like a script are left out, and so are burst days.",
          "Under the visitors heading."),
    Entry("dashboard.visitors.people", T, "Dashboard people", "People", "Figure and column: people in the week."),
    Entry("dashboard.visitors.returning", T, "Dashboard returning", "Returning",
          "Figure and column: people who had visited before."),
    Entry("dashboard.visitors.views", T, "Dashboard page views", "Page views", "Figure and column: pages opened."),
    Entry("dashboard.visitors.chart", T, "Dashboard people chart", "People each day, last 7 days",
          "Read out by screen readers for the visitors chart."),
    Entry("dashboard.visitors.best", T, "Dashboard busiest day", "Most people on {day}: {count}.",
          "Under the visitors chart.", placeholders=("day", "count")),
    Entry("dashboard.visitors.bursts", T, "Dashboard burst days", "Grey bars look automated and are left out of the totals: {names}.",
          "Under the visitors chart when the week has burst days.", placeholders=("names",)),
    Entry("dashboard.visitors.sources", H, "Dashboard where from heading", "Where people came from",
          "Heading of the list of sources in the visitors part."),
    Entry("dashboard.visitors.more", B, "Dashboard visitors link", "See more on Insights",
          "Link at the foot of the visitors part."),
    Entry("dashboard.clicks.title", H, "Dashboard clicks heading", "Clicks to shops, last 7 days",
          "Heading of the clicks part."),
    Entry("dashboard.clicks.intro", T, "Dashboard clicks intro",
          "Clicks from people carry our tracking. Direct hits still reach the shop, with no tracking, and count "
          "nowhere else.",
          "Under the clicks heading."),
    Entry("dashboard.clicks.people", T, "Dashboard clicks from people", "Clicks from people",
          "Figure, key and column: clicks counted and sent with our tracking."),
    Entry("dashboard.clicks.direct", T, "Dashboard direct hits", "Direct hits",
          "Figure, key and column: clicks sent to the plain shop address and not counted."),
    Entry("dashboard.clicks.per_person", T, "Dashboard clicks per person", "Clicks per person",
          "Figure: clicks from people divided by people in the week."),
    Entry("dashboard.clicks.chart", T, "Dashboard clicks chart", "Clicks from people and direct hits each day",
          "Read out by screen readers for the clicks chart."),
    Entry("dashboard.clicks.best", T, "Dashboard busiest clicks day", "Most clicks from people on {day}: {count}.",
          "Under the clicks chart.", placeholders=("day", "count")),
    Entry("dashboard.clicks.networks", H, "Dashboard clicks by network heading", "Clicks by how the shop pays us",
          "Heading of the clicks by affiliate programme list."),
    Entry("dashboard.clicks.network_other", T, "Dashboard other affiliate link", "Other affiliate link",
          "A shop whose affiliate link format is not one the dashboard names."),
    Entry("dashboard.clicks.network_direct", T, "Dashboard no affiliate link", "No affiliate link, earns nothing",
          "Shops with no affiliate link format."),
    Entry("dashboard.clicks.products", H, "Dashboard most clicked heading", "Most clicked products",
          "Heading of the most clicked products list."),
    Entry("dashboard.clicks.more", B, "Dashboard clicks link", "See the clicks table on Insights",
          "Link at the foot of the clicks part."),
    Entry("dashboard.clicks.admin", B, "Dashboard every click link", "See each click in admin",
          "Link at the foot of the clicks part."),
    Entry("dashboard.checks.title", H, "Dashboard things to correct heading", "Things to correct",
          "Heading of the things to correct part."),
    Entry("dashboard.checks.intro", T, "Dashboard things to correct intro",
          "Rows waiting on Things to check, each with a one tap fix there.",
          "Under the things to correct heading."),
    Entry("dashboard.checks.doubtful", T, "Dashboard doubtful prices", "Doubtful prices", "A Things to check list."),
    Entry("dashboard.checks.wrong", T, "Dashboard wrong matches", "Wrong matches", "A Things to check list."),
    Entry("dashboard.checks.duplicates", T, "Dashboard possible duplicates", "Possible duplicate pairs",
          "A Things to check list, by how sure the rules are."),
    Entry("dashboard.checks.found", T, "Dashboard found at another shop", "Found at another shop",
          "A Things to check list."),
    Entry("dashboard.checks.releases", T, "Dashboard announced sets", "Announced sets to check",
          "A Things to check list."),
    Entry("dashboard.checks.disagreements", T, "Dashboard release dates", "Release dates to confirm",
          "A Things to check list."),
    Entry("dashboard.checks.unknown_delivery", T, "Dashboard unknown delivery", "Shops with unknown delivery charges",
          "A Things to check list."),
    Entry("dashboard.checks.stale_sources", T, "Dashboard release sources", "Release sources not answering",
          "A Things to check list."),
    Entry("dashboard.checks.counted", T, "Dashboard duplicates counted", "Pairs counted at {clock}.",
          "Under the possible duplicate pairs: when they were counted.", placeholders=("clock",)),
    Entry("dashboard.checks.excluded", T, "Dashboard prices set aside",
          "{count} prices far from every other shop are set aside automatically. Nothing waits for you there.",
          "Under the things to correct list.", placeholders=("count",)),
    Entry("dashboard.checks.claude.one", T, "Dashboard Claude settled one", "Claude settled 1 row in the last 24 hours.",
          "Under the things to correct list.", placeholders=("count",)),
    Entry("dashboard.checks.claude.other", T, "Dashboard Claude settled", "Claude settled {count} rows in the last 24 hours.",
          "Under the things to correct list.", placeholders=("count",)),
    Entry("dashboard.checks.more", B, "Dashboard things to check link", "Open Things to check",
          "Link at the foot of the things to correct part."),
    Entry("dashboard.prices.title", H, "Dashboard Price check heading", "Price check coverage",
          "Heading of the part saying how many products show the Price check."),
    Entry("dashboard.prices.intro", T, "Dashboard Price check intro",
          "Of the products with a price now, how many show the Price check, and what it is measured against. "
          "Counted each hour.",
          "Under the Price check coverage heading."),
    Entry("dashboard.prices.checked", T, "Dashboard with a Price check", "With a Price check",
          "The share of products with a price now that show the Price check."),
    Entry("dashboard.prices.market", T, "Dashboard from Cardmarket", "From Cardmarket's price",
          "The share whose Price check uses Cardmarket's price (matched, and priced in the last week)."),
    Entry("dashboard.prices.record", T, "Dashboard from our record", "From our own price record",
          "The share whose Price check uses our own daily prices (two shops, 14 days)."),
    Entry("dashboard.prices.of", T, "Dashboard part of a whole", "{part} of {whole}",
          "Under each Price check share.", placeholders=("part", "whole")),
    Entry("dashboard.prices.game_row", T, "Dashboard Price check game row",
          "{checked} of {priced} priced, {market} from Cardmarket",
          "Under each game in the Price check coverage part.", placeholders=("checked", "priced", "market")),
    Entry("dashboard.prices.waiting.one", T, "Dashboard Cardmarket match waiting (one)",
          "1 Cardmarket match waits for you",
          "Link to the Cardmarket matches Claude was not sure of, when there is one."),
    Entry("dashboard.prices.waiting.other", T, "Dashboard Cardmarket matches waiting (more than one)",
          "{count} Cardmarket matches wait for you",
          "Link to the Cardmarket matches Claude was not sure of.", placeholders=("count",)),
    Entry("dashboard.matching.title", H, "Dashboard Cardmarket matching heading", "Claude matching Cardmarket",
          "Small heading in the Price check coverage part, over how Claude's matching to Cardmarket is going."),
    Entry("dashboard.matching.off", T, "Dashboard Cardmarket matching paused",
          "Claude is switched off, so matching is paused. Switch it on in Things to check.",
          "Shown when Claude is off: Cardmarket matching runs only while it is on."),
    Entry("dashboard.matching.asked", T, "Dashboard products looked at", "Looked at",
          "The share of our products in Cardmarket's games that Claude has matched or tried to."),
    Entry("dashboard.matching.asked_note", T, "Dashboard products looked at note",
          "{asked} of {products}, {left} to go",
          "Under the share looked at.", placeholders=("asked", "products", "left")),
    Entry("dashboard.matching.matched", T, "Dashboard matched", "Matched",
          "Products Claude matched surely, or you confirmed: these use Cardmarket's price."),
    Entry("dashboard.matching.waiting", T, "Dashboard matches waiting", "Waiting for you",
          "Matches Claude was not sure of, or whose prices were far apart, waiting for you to confirm or reject."),
    Entry("dashboard.matching.none", T, "Dashboard no Cardmarket product", "No Cardmarket product",
          "Products Claude found no Cardmarket product for, or whose match you rejected."),
    Entry("dashboard.matching.spent", T, "Dashboard matching spend",
          "Spent this month on matching: {spent}, products asked about: {count}.",
          "Under the matching figures. Part of Claude's monthly spend in the Claude part.",
          placeholders=("spent", "count")),
    Entry("dashboard.matching.last", T, "Dashboard matching last asked", "Last asked {clock}.",
          "After the matching spend.", placeholders=("clock",)),
    Entry("dashboard.matching.history.one", T, "Dashboard Cardmarket history (one day)",
          "Cardmarket price history: 1 day kept, from {since}.",
          "How long we have kept Cardmarket's daily prices, when it is one day.", placeholders=("since",)),
    Entry("dashboard.matching.history.other", T, "Dashboard Cardmarket history",
          "Cardmarket price history: {count} days kept, from {since}.",
          "How long we have kept Cardmarket's daily prices.", placeholders=("count", "since")),
    Entry("dashboard.matching.no_history", T, "Dashboard no Cardmarket history",
          "No Cardmarket prices read yet. They are read each morning.",
          "Shown before Cardmarket's prices have been read once."),
    Entry("dashboard.shops.title", H, "Dashboard shops heading", "Shops health", "Heading of the shops part."),
    Entry("dashboard.shops.intro", T, "Dashboard shops intro", "Whether each shop's prices are read on time.",
          "Under the shops heading."),
    Entry("dashboard.shops.summary", T, "Dashboard shops read lately", "{count} of {total} shops read in the last hour.",
          "Under the shops heading.", placeholders=("count", "total")),
    Entry("dashboard.shops.fresh", T, "Dashboard shops read lately key", "Read in the last hour",
          "Key of the shops bar."),
    Entry("dashboard.shops.ok", T, "Dashboard shops up to date key", "Up to date", "Key of the shops bar."),
    Entry("dashboard.shops.paused", T, "Dashboard shops paused key", "Paused", "Key of the shops bar."),
    Entry("dashboard.shops.late", T, "Dashboard shops late key", "Late or failing", "Key of the shops bar."),
    Entry("dashboard.shops.worker_on", T, "Dashboard reader running", "The background reader is running. Last beat {clock}.",
          "Shops part.", placeholders=("clock",)),
    Entry("dashboard.shops.worker_off", T, "Dashboard reader stopped",
          "The background reader stopped at {clock}. Shops are read on the hourly schedule meanwhile.",
          "Shops part.", placeholders=("clock",)),
    Entry("dashboard.shops.worker_never", T, "Dashboard reader never ran",
          "The background reader has not run here. Shops are read on the hourly schedule.", "Shops part."),
    Entry("dashboard.shops.all_paused", T, "Dashboard pause all",
          "Pause all is on: no shop is read until you tap Resume all on Crawl health.", "Shops part."),
    Entry("dashboard.shops.last_read", T, "Dashboard last read", "Last shop read finished {clock}.",
          "Shops part.", placeholders=("clock",)),
    Entry("dashboard.shops.problems", H, "Dashboard shops to look at", "Shops to look at",
          "Heading of the list of late or failing shops."),
    Entry("dashboard.shops.never", T, "Dashboard shop never read", "Never read successfully.",
          "Under a shop that has never been read."),
    Entry("dashboard.shops.late_since", T, "Dashboard shop late", "Last good read {clock}.",
          "Under a late shop.", placeholders=("clock",)),
    Entry("dashboard.shops.more_problems", T, "Dashboard more shops", "{count} more on Crawl health.",
          "Under the list of shops to look at.", placeholders=("count",)),
    Entry("dashboard.shops.more", B, "Dashboard crawl health link", "Open Crawl health",
          "Link at the foot of the shops part."),
    Entry("dashboard.shops.runs", B, "Dashboard import runs link", "See each shop read in admin",
          "Link at the foot of the shops part."),
    Entry("dashboard.customers.title", H, "Dashboard customers heading", "Customers",
          "Heading of the customers part."),
    Entry("dashboard.customers.intro", T, "Dashboard customers intro",
          "Confirmed alerts waiting to be sent, the emails sent, and people waiting for an answer.",
          "Under the customers heading."),
    Entry("dashboard.customers.drops", T, "Dashboard drop reminders", "Drop reminders", "A customers figure."),
    Entry("dashboard.customers.searches", T, "Dashboard search alerts", "Search alerts", "A customers figure."),
    Entry("dashboard.customers.stock", T, "Dashboard back in stock alerts", "Back in stock alerts", "A customers figure."),
    Entry("dashboard.customers.sent", T, "Dashboard alert emails", "Alert emails sent, last 7 days",
          "A customers figure."),
    Entry("dashboard.customers.unread", T, "Dashboard unread messages", "Unread messages", "A customers figure."),
    Entry("dashboard.customers.shops", T, "Dashboard shops waiting", "Shops asking to be listed", "A customers figure."),
    Entry("dashboard.customers.refer", H, "Dashboard refer a friend heading", "Refer a friend",
          "Heading of the refer a friend figures."),
    Entry("dashboard.customers.refer_open", T, "Dashboard offer open", "The offer is open to new people.",
          "Refer a friend figures."),
    Entry("dashboard.customers.refer_closed", T, "Dashboard offer closed",
          "The offer is on but closed to new people: email, keyrings or the last day stop it.",
          "Refer a friend figures."),
    Entry("dashboard.customers.refer_off", T, "Dashboard offer off", "The offer is switched off.",
          "Refer a friend figures."),
    Entry("dashboard.customers.keyrings", T, "Dashboard keyrings left", "Keyrings left", "Refer a friend figure."),
    Entry("dashboard.customers.to_post", T, "Dashboard keyrings to post", "Keyrings to post", "Refer a friend figure."),
    Entry("dashboard.customers.check", T, "Dashboard check first", "{count} to check first.",
          "Under keyrings to post.", placeholders=("count",)),
    Entry("dashboard.customers.review", T, "Dashboard held for review", "Held for review", "Refer a friend figure."),
    Entry("dashboard.gaps.title", H, "Dashboard searches heading", "Searches that found little",
          "Heading of the demand gaps part."),
    Entry("dashboard.gaps.intro", T, "Dashboard searches intro",
          "Searches this week that found {count} products or fewer, most made first. Each is a product to add or "
          "a name to fix.",
          "Under the searches heading.", placeholders=("count",)),
    Entry("dashboard.gaps.searched.one", T, "Dashboard searched once", "Searched once",
          "Beside a search.", placeholders=("count",)),
    Entry("dashboard.gaps.searched.other", T, "Dashboard searched", "Searched {count} times",
          "Beside a search.", placeholders=("count",)),
    Entry("dashboard.gaps.found_none", T, "Dashboard found nothing", "found nothing", "Beside a search."),
    Entry("dashboard.gaps.found.one", T, "Dashboard found one", "found 1 product", "Beside a search.",
          placeholders=("count",)),
    Entry("dashboard.gaps.found.other", T, "Dashboard found some", "found {count} products", "Beside a search.",
          placeholders=("count",)),
    Entry("dashboard.gaps.more", B, "Dashboard searches link", "See every search on Insights",
          "Link at the foot of the searches part."),
    Entry("dashboard.claude.title", H, "Dashboard Claude heading", "Claude", "Heading of the Claude part."),
    Entry("dashboard.claude.spent", T, "Dashboard Claude spend", "{spent} of {limit} this month.",
          "Beside the Claude spend bar.", placeholders=("spent", "limit")),
    Entry("dashboard.claude.server_off", T, "Dashboard Claude off on the server",
          "Claude is switched off in the server settings.", "What Claude is doing."),
    Entry("dashboard.claude.no_key", T, "Dashboard Claude needs a key",
          "Claude needs a key. Add it under Claude settings and key on Things to check.", "What Claude is doing."),
    Entry("dashboard.claude.stopped", T, "Dashboard Claude stopped", "Claude has stopped. {reason}",
          "What Claude is doing, with the reason from Things to check.", placeholders=("reason",)),
    Entry("dashboard.claude.off", T, "Dashboard Claude off", "Claude is off.", "What Claude is doing."),
    Entry("dashboard.claude.paused", T, "Dashboard Claude waits", "Pause all is on, so Claude waits.",
          "What Claude is doing."),
    Entry("dashboard.claude.limit", T, "Dashboard Claude at limit",
          "Claude reached this month's limit. It starts again next month, or raise the limit on Things to check.",
          "What Claude is doing."),
    Entry("dashboard.claude.sort_all", T, "Dashboard Claude sorting everything", "Claude is on and sorting everything.",
          "What Claude is doing."),
    Entry("dashboard.claude.acting", T, "Dashboard Claude acting", "Claude is on and settles each row itself.",
          "What Claude is doing."),
    Entry("dashboard.claude.trial", T, "Dashboard Claude in trial", "Claude is on, in trial: it only suggests.",
          "What Claude is doing."),
    Entry("dashboard.claude.settled", T, "Dashboard Claude settled", "Rows settled this month",
          "A Claude figure."),
    Entry("dashboard.claude.last_run", T, "Dashboard Claude last run", "Last run {clock}.",
          "Claude part.", placeholders=("clock",)),
    Entry("dashboard.claude.never", T, "Dashboard Claude never ran", "Claude has not run yet.", "Claude part."),
    Entry("dashboard.claude.more", B, "Dashboard Claude link", "Open Claude on Things to check",
          "Link at the foot of the Claude part."),
    Entry("dashboard.claude.answers", B, "Dashboard Claude answers link", "See each Claude answer and its cost",
          "Link at the foot of the Claude part."),
    Entry("dashboard.money.title", H, "Dashboard money heading", "What clicks earn, last {days} days",
          "Heading of the dashboard's first part: clicks to shops beside what they earn.", placeholders=("days",)),
    Entry("dashboard.money.intro", T, "Dashboard money intro",
          "Clicks to shops beside the sales the networks report. Sales and commission come from the reports you "
          "import in admin.", "Under the money heading."),
    Entry("dashboard.money.ctr", T, "Dashboard click-through rate", "Retailer click-through rate",
          "Visits with a click to a shop out of all visits."),
    Entry("dashboard.money.ctr_note", T, "Dashboard click-through note", "{clicked} of {visits} visits since {date}",
          "Under the click-through rate.", placeholders=("clicked", "visits", "date")),
    Entry("dashboard.money.per_100", T, "Dashboard commission per 100 clicks", "Commission per 100 clicks",
          "Approved commission per 100 clicks to shops."),
    Entry("dashboard.money.per_100_note", T, "Dashboard commission per 100 clicks note",
          "{commission} approved from {clicks} clicks", "Under commission per 100 clicks.",
          placeholders=("commission", "clicks")),
    Entry("dashboard.money.conversion", T, "Dashboard conversion rate", "Retailer conversion rate",
          "Sales the networks report out of clicks to shops."),
    Entry("dashboard.money.conversion_note", T, "Dashboard conversion note", "{sales} sales from {clicks} clicks",
          "Under the conversion rate.", placeholders=("sales", "clicks")),
    Entry("dashboard.money.per_1000", T, "Dashboard revenue per 1,000 visits", "Revenue per 1,000 visits",
          "Approved commission per 1,000 visits."),
    Entry("dashboard.money.per_1000_note", T, "Dashboard revenue per visit note", "{commission} over {visits} visits",
          "Under revenue per 1,000 visits.", placeholders=("commission", "visits")),
    Entry("dashboard.money.coverage", T, "Dashboard affiliate coverage", "Affiliate coverage",
          "In-stock offers at shops whose links earn, out of all in-stock offers."),
    Entry("dashboard.money.coverage_note", T, "Dashboard coverage note", "{earning} of {offers} in-stock offers earn",
          "Under affiliate coverage.", placeholders=("earning", "offers")),
    Entry("dashboard.money.pending", T, "Dashboard pending commission", "{amount} more is pending with the networks.",
          "Under the money figures when some sales are not yet approved.", placeholders=("amount",)),
    Entry("dashboard.money.no_sales", T, "Dashboard no sales yet",
          "No sales imported for these days, so commission and conversion read nothing. Import a network's report "
          "to fill them in.", "Under the money figures while no sale is imported for the days shown."),
    Entry("dashboard.money.not_tracked", T, "Dashboard click-through not counted yet",
          "Counted from the first click that carries its visit.", "Under the click-through rate before any click has."),
    Entry("dashboard.money.best_game", T, "Dashboard best earning game",
          "{game} earns the most per 100 clicks: {amount}.", "Under the games, naming the one that earns most per click.",
          placeholders=("game", "amount")),
    Entry("dashboard.money.presses", H, "Dashboard presses heading", "What people press",
          "Above the share of each button that leads to a shop, and Compare prices."),
    Entry("dashboard.money.compare", T, "Dashboard compare presses", "Compare prices",
          "A row in What people press: presses on Compare prices links, which stay on RipRaptor."),
    Entry("dashboard.money.button_unknown", T, "Dashboard button not named", "A link from before buttons were named",
          "A row in What people press for clicks from pages made before each button was named."),
    Entry("dashboard.money.games", H, "Dashboard money by game heading", "By game",
          "Above the games, most commission first."),
    Entry("dashboard.money.shops", H, "Dashboard money by shop heading", "By shop",
          "Above the shops, most commission first, then shops with stock whose links earn nothing."),
    Entry("dashboard.money.row", T, "Dashboard money row",
          "{clicks} clicks, {sales} sales, {per_hundred} per 100 clicks, {conversion} conversion, {coverage} of in-stock offers earn",
          "Under each game and shop.", placeholders=("clicks", "sales", "per_hundred", "conversion", "coverage")),
    Entry("dashboard.money.no_game", T, "Dashboard sales without a game", "Sales not matched to a game",
          "A row under By game for sales with no click reference."),
    Entry("dashboard.money.more_shops", T, "Dashboard more shops", "{count} more shops in the sales list.",
          "Under the shops when there are more than shown.", placeholders=("count",)),
    Entry("dashboard.money.sales", B, "Dashboard sales link", "See every sale", "Link at the foot of the money part."),
    Entry("dashboard.money.import", B, "Dashboard import link", "Import a network report",
          "Link at the foot of the money part."),
    Entry("info.open", T, "Info button, read out", "What is {thing}?",
          "What screen readers say for an ⓘ button. {thing} is the label beside it.", placeholders=("thing",)),
    Entry("info.type.booster_pack", T, "Info: booster pack",
          "One sealed pack of cards from a set. What is inside is a surprise, which is the fun of opening it.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.booster_box", T, "Info: booster box",
          "A sealed box of booster packs from one set, usually 24 or 36 depending on the game. Bought by players who open lots of packs and by collectors who keep it sealed.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.elite_trainer_box", T, "Info: Elite Trainer Box",
          "A Pokémon box with booster packs (9 in recent sets) plus card sleeves, dice and a box to keep cards in. Made for new players and a popular gift.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.bundle", T, "Info: bundle",
          "A few booster packs sold together, often 6, without the extras of a bigger box.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.collection_box", T, "Info: collection box",
          "Booster packs with extras such as promo cards, a figure or a pin. The packs are the same as those sold on their own.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.deck", T, "Info: deck",
          "A ready-made deck you can play with straight away. Every copy has the same cards, so there are no surprises. The easiest way to learn the game.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.tin", T, "Info: tin",
          "A collectable metal tin with a few booster packs and usually a promo card.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.gift_set", T, "Info: gift set",
          "A boxed set made as a present, with booster packs and extras.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.collector_booster_box", T, "Info: collector booster box",
          "A box of collector boosters: premium packs with more rare and shiny cards than normal packs. Dearer, and made for collectors rather than new players.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.type.collector_booster_pack", T, "Info: collector booster pack",
          "A premium pack with more rare and shiny cards than a normal pack. Dearer, and made for collectors rather than new players.",
          "The ⓘ card after this product type on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.lang.ja", T, "Info: Japanese",
          "Made for Japan, with the card text in Japanese. Packs often hold fewer cards than English ones. Good for collecting and opening, not for players who need English cards.",
          "The ⓘ card after the language on product pages and product cards. Leave it blank for no ⓘ.", optional=True),
    Entry("info.lang.ja_pokemon", T, "Info: Japanese Pokémon",
          "A Japanese Pokémon booster pack usually has 5 cards. An English one has 10.",
          "A second line on the Japanese ⓘ card for Pokémon products. Leave it blank to leave it off.", optional=True),
    Entry("info.lang.ko", T, "Info: Korean",
          "Made for Korea, with the card text in Korean. Packs often hold fewer cards than English ones. Good for collecting and opening, not for players who need English cards.",
          "The ⓘ card after the language on product pages and product cards. Leave it blank for no ⓘ.", optional=True),
    Entry("info.lang.ko_pokemon", T, "Info: Korean Pokémon",
          "A Korean Pokémon booster pack usually has 5 cards. An English one has 10.",
          "A second line on the Korean ⓘ card for Pokémon products. Leave it blank to leave it off.", optional=True),
    Entry("info.lang.zh", T, "Info: Chinese",
          "Made for China, Hong Kong or Taiwan, with the card text in Chinese. Pack sizes often differ from English ones. Good for collecting and opening, not for players who need English cards.",
          "The ⓘ card after the language on product pages and product cards. Leave it blank for no ⓘ.", optional=True),
    Entry("info.lang.other", T, "Info: other languages",
          "The card text is in {language}. The cards and pack sizes are usually the same as the English version.",
          "The ⓘ card after the language on product pages and product cards. Leave it blank for no ⓘ. {language} is the language's name.", optional=True, placeholders=('language',)),
    Entry("info.badge.low_30", T, "Info: Lowest in 30 days",
          "Today's cheapest price is the lowest we have recorded for it in the last 30 days, and under its usual price for the month.",
          "The ⓘ card after this badge on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.badge.best_week", T, "Info: Best in 7 days",
          "Today's cheapest price is the lowest we have recorded for it in the last 7 days.",
          "The ⓘ card after this badge on product pages. Leave it blank for no ⓘ.", optional=True),
    Entry("info.badge.soon", T, "Info: Dropping soon",
          "Shops are taking pre-orders. Order now and the shop sends it when it comes out.",
          "The ⓘ card after this badge on product pages. Leave it blank for no ⓘ.", optional=True),
]

REGISTRY = {entry.key: entry for entry in ENTRIES}
