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
    ("alerts", "Back in stock emails"),
    ("watchlist", "Watchlist page"),
    ("browse", "Search results and browsing"),
    ("product", "Product page"),
    ("about", "How it works page"),
    ("contact", "Message us page"),
    ("listshop", "List your shop page"),
    ("footer", "Footer"),
    ("terms", "Terms page"),
    ("errors", "Error pages"),
    ("meta", "Page titles and search engine descriptions"),
    ("insights", "Insights page in admin (owner only)"),
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
        "The strong lines at the top of the home page, beside the raptor and above the search box. The last "
        "sentence is shown in orange on a line of its own; one sentence is not split. On a phone the headline "
        "shares its width with the raptor, so keep each sentence to two or three short words or it wraps.",
    ),
    Entry(
        "home.hero.subtitle", T, "Line under the headline",
        "Compare sealed TCG prices from UK shops, cheapest delivered price first.",
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
    Entry("home.resume.clear", B, "Clear history button", "Clear history", "Forgets the recent searches and viewed products in this browser."),
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
        "Green label on a pick whose saving on the next shop is big, or whose price is the lowest of the week.",
    ),
    Entry("home.featured.title", H, "Featured deals heading", "Featured deals", "Home page row near the top."),
    Entry(
        "home.featured.note", T, "Featured deals note",
        "Every featured deal is a real saving on the next shop or this week's lowest price. We favour shops that pay us a commission when choosing them, and each product page still shows every shop's price.",
        "On the terms page, under Independence. Says how the home page's featured deals row is chosen.",
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
    Entry("drops.email.button", B, "Email drops page button", "See the drop", "Button in a reminder email, to the drop on the drops page."),
    Entry("drops.email.more", T, "Email more to come", "We only email you about this drop: when pre-orders open, the day before and on the day. Each email has a link that deletes your address.", "Footer of the pre-orders and day-before emails.", legal=True),
    Entry("drops.email.last", T, "Email last one", "This was your last reminder for {set}, so your email address has already been deleted.", "Footer of the drop-day email.", placeholders=("set",), legal=True),
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
        "browse.game.sets_title", H, "Sets heading on a game page",
        "Sets",
        "Heading above the list of sets on each game page.",
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
        "browse.game.all_sets", B, "All sets link",
        "All sets",
        "First chip in the row of sets on game and set pages: every product for the game.",
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
        "Used only to reply about your shop. We delete it a year after we reply.",
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
        "products you view and searches you make are kept in your browser, not "
        "by us, and you can clear them from the home page. Our own count of clicks to retailers "
        "does not record who clicked. One small cookie of ours remembers only that your browser "
        "has visited before, so we can count returning visitors. It holds nothing "
        "about you.\n\n"
        "If you ask to be emailed when a product is back in stock, we keep your email "
        "address for that alone. Nothing is sent until you confirm by the link we email "
        "you. You get one email when a shop has the product, and your address is then "
        "deleted. Unconfirmed requests are deleted after a week and any still waiting "
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
        "meta.drops.title", T, "Drops page title",
        "TCG drops: release dates, pre-orders and new releases in the UK",
        "Browser tab title for the drops page. The site name is added after it.",
    ),
    Entry(
        "meta.drops.description", T, "Drops page description",
        "Upcoming and just released sealed Pokemon, Magic, One Piece, Yu-Gi-Oh and Lorcana sets with their "
        "dates, the cheapest UK pre-order or delivered price and an email reminder before each drop.",
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
]

REGISTRY = {entry.key: entry for entry in ENTRIES}
