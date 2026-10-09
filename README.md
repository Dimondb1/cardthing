# RipRaptor

Compare UK prices for sealed trading card game products. Django 5.2, SQLite by
default, no JavaScript framework.

## Run it locally

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate          # also creates all default site wording
python manage.py seed_catalogue   # real games, sets and products, no prices yet
python manage.py createsuperuser
python manage.py runserver
```

Site: http://127.0.0.1:8000/ Admin: http://127.0.0.1:8000/admin/

Project rules for anyone (or any AI) working on the code are in `CLAUDE.md`.

For a design review without real retailers, `python manage.py seed_demo`
loads fictional retailers on `.example` domains with generated prices. It only
runs with `DJANGO_DEBUG` on, and `--flush` replaces what is there.

## Running it on your own computer

The quick way: double-click `start.command` (Mac) or `start.bat` (Windows).
It installs what it needs, loads the catalogue, asks you to create an admin
login the first time, and opens the site. `check_shop.command` /
`check_shop.bat` asks for a shop address and reports whether it can be
imported.

The manual way:

You need Python 3.11 or newer (python.org, or `brew install python` on a
Mac). Then, in a terminal, inside the project folder:

```sh
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_catalogue
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/ in a browser, and http://127.0.0.1:8000/admin/
for admin. To see it on your phone, run
`python manage.py runserver 0.0.0.0:8000`, add your computer's local IP
address (for example `192.168.1.20`) to `DJANGO_ALLOWED_HOSTS`, and open
`http://192.168.1.20:8000/` on the phone while both are on the same wifi.

Prices only update while the importer runs, so on a local machine run
`python manage.py import_prices` by hand, or leave a terminal running:

```sh
while true; do python manage.py import_prices; sleep 3600; done
```

## Adding products in bulk

```sh
python manage.py import_products products.csv
```

takes columns `name`, `game`, `type` (booster_box, elite_trainer_box, bundle,
collection_box, deck, tin, booster_pack, gift_set, other, or ETB) and
optionally `set`, `set_code`, `ean`, `release_date` (YYYY-MM-DD) and
`image_url`. Loading the same file again updates the details.

`scripts/browser_sweep.py` drives every public page in a real browser and
reports script errors, horizontal overflow, unlabelled controls and tap
targets under 24px. It needs `pip install playwright` and a running dev
server.

A Shopify shop that sells far more than cards (Zatu: board games, puzzles,
books) can be read from one collection instead of the whole store: set
"Shopify collection" on the retailer to the collection's handle, for
example `trading-card-games`, and the import reads
`/collections/<handle>/products.json`. Products outside the collection are
treated as not stocked there. `setup_shops` sets this for Zatu.

Shop titles are matched with the game prefix taken off and the separators
collapsed, so "Disney Lorcana: Shimmering Skies - Booster Box" is judged
on "Shimmering Skies Booster Box" and counts as a full match for our
product of that name. The game itself still comes from the whole title.

A feed retailer accepts a Google Shopping XML feed (RSS or Atom with the
`g:` namespace) as well as CSV: barcode (`g:gtin`) first, title second,
with `g:sale_price` over `g:price`, `g:availability` and `g:shipping`.

## Adding several shops at once

```sh
python manage.py discover_shops https://shop-one.co.uk https://shop-two.com
```

works out how each shop can be read, adds it as a retailer, imports its
prices and matches products by barcode or name. Then run `check_delivery`
(below) and sort the uncertain matches under Shop products to review.

## Delivery charges

```sh
python manage.py check_delivery            # report what each shop says
python manage.py check_delivery --apply    # and save it
```

reads each shop's UK delivery charges from the shop itself, two ways. Its
delivery page (found through the "Delivery" or "Shipping" link on the home
page, or the usual addresses) gives the free-delivery threshold and, when
written out, the standard charge. For a Shopify shop the command also puts
one cheap product in a basket, asks the shop for its UK delivery options,
takes the cheapest posted one and empties the basket; when the page gave a
threshold, a basket just over it is priced too, to confirm it is free.
Every figure saved carries its source and the date in the retailer's
delivery note, so it can be checked. A shop that gives nothing readable is
listed with its address for checking by hand. The server runs this weekly.

A charge nobody has confirmed is unknown, never free. Leave "standard
delivery" empty in admin when you do not know it (and set "standard charge
only up to" when a shop publishes its charge only for small orders). Those
prices show as "£21.50 + delivery" with "Delivery charge not confirmed",
are listed after confirmed delivered prices, never count as the cheapest,
never make a saving, a badge, a featured deal or a price drop, and stay out
of the price history. Changing a shop's delivery in admin re-prices its
listings at once. `setup_shops` turns a £0 charge the shop list marks as
unknown into an empty one; a charge you set yourself is kept.

Everywhere a price is shown, the big number is the delivered total when
delivery is known ("£24.44 delivered", then "£21.50 plus £2.94 delivery
at eBay"), and the item price with "+ delivery" when it is not. Lists sort
on the number they show.

## Checking a shop before adding it

```sh
python manage.py check_shop https://www.example-cards.co.uk/
```

reports whether the shop publishes Shopify product data, how many products
carry barcodes, and how many look like sealed TCG. If it is not Shopify, ask
the retailer or your affiliate network for a CSV feed instead.

## What we do for a shop

```sh
python manage.py shop_report              # one line per shop
python manage.py shop_report jet-cards    # the full picture for one shop
```

prints how many of a shop's products we list, how many are in stock, on
how many it is the cheapest UK shop, which shops it beats, and the clicks we
sent it in the last 30 days. Use the figures when asking a shop to start an
affiliate programme. Shopify shops can turn on Shopify Collabs for free.

## Amazon

Amazon publishes no feed, so prices come from its Product Advertising API,
which needs an Amazon Associates account. In Associates Central open Tools,
Product Advertising API, and add credentials. Put the three values in the
server's `.env`:

```sh
RIPRAPTOR_AMAZON_ACCESS_KEY=...
RIPRAPTOR_AMAZON_SECRET_KEY=...
RIPRAPTOR_AMAZON_PARTNER_TAG=ripraptor-21
```

then run `setup_shops`, which adds Amazon as a shop only when the keys are
set. Amazon grants the API only to approved accounts with recent sales, so
until then set just the tag: every product page then carries a "Check price
on Amazon" link, a tagged Amazon search shown below the real prices and
hidden once we have an Amazon price for the product. The hourly import reads Amazon once a day: every product already
found there is refreshed in batches of ten, then up to
`RIPRAPTOR_AMAZON_DAILY_LIMIT` products are looked up, by barcode where we
have one and otherwise by name. The products visitors want most go first
(clicks, views, watchlists, alerts and the owner's "Search other shops
now" tap; being new or on pre-order does not count here), then those never
tried, then those tried longest ago. A wanted product searched in the last
three days waits its normal turn, so the same one is not searched every
day ahead of products never tried. A result is kept only when it matches the
product asked about. Amazon's own tracked link is stored on each listing,
so the shop needs no affiliate link format. The Terms page carries the
Amazon Associates line while Amazon is an active shop. Amazon removes API
access from accounts with no sales in their first months; the import then
reports the error in admin until access is restored.

## eBay

eBay gives affiliates a free price API straight away. Join eBay Partner
Network, note the Campaign ID under Campaigns, then at developer.ebay.com
create an application and copy its production App ID and Cert ID. Put the
three values in the server's `.env`:

```sh
RIPRAPTOR_EBAY_APP_ID=...
RIPRAPTOR_EBAY_CERT_ID=...
RIPRAPTOR_EBAY_CAMPAIGN_ID=...
```

then run `setup_shops`, which adds eBay as a shop only when the keys are
set. The hourly import reads eBay once a day, up to
`RIPRAPTOR_EBAY_DAILY_LIMIT` products a run: those already listed first,
then the rest, by barcode where we have one and otherwise by name. Of the
rest, the products visitors want most go first (counted as for Amazon
above, and waiting their normal turn for three days after a search), then
those never tried or tried longest ago, then those most shops stock. The
"Not looked up yet" estimate on Insights leaves out the searches that go
back to wanted products. For
each product it asks for new, buy-it-now items in Britain delivered to a
London postcode, cheapest first including postage, from sellers with at
least 95 percent feedback, and keeps the cheapest whose title matches the
product. Postage is stored as the delivery charge.

eBay sellers pad titles with the game's name and selling words, so a
result counts as our product when every word of our name is in its title,
it is the same kind of product in the same language, it is one of it (not
a lot, a case, sleeves, a play mat, tokens or "deck only"), and no more
specific product of ours fits it better. A booster display counts as a
booster box. A listing priced under 40% of the cheapest shop's price for
the same product is refused, because it is always something else (stickers,
a single pack, a part), and names with only one identifying word, such as
"151 Booster Pack", only match the strict way. Products with a barcode are searched by barcode first and by
name when that finds nothing; a name search that finds nothing is retried
without shop words such as "Official" or "(Soccer)". The daily limit
counts searches, not products. Each eBay price is saved the moment it is
found and progress is recorded every 100 products, so a run that is
stopped part-way (an update, a restart) keeps what it found and the next
run carries on from where it stopped.

    python manage.py ebay_report

prints how much of the catalogue eBay covers: products looked up, matched,
in stock, cheapest on eBay, still waiting, and the widely stocked products
it has no match for. The same figures are on the Insights page.

    python manage.py ebay_check https://ripraptor.com/products/<slug>/

shows one product's saved eBay listing and eBay's results right now, with
the price delivered, which one the import would pick, and why each other
is refused (too cheap, a different product, no UK postage). It uses one or
two searches and saves nothing. Links are eBay's own
affiliate links for the campaign, so the shop needs no affiliate link
format. Listings we already have are refreshed twenty at a time through eBay's
bulk item lookup, which has its own daily allowance, so searches go on
new products. The run asks eBay how many searches are left today and
stays inside that; when the allowance is used up it stops and keeps what
it found, and the hourly import tries again after the reset at 08:00 UK
time. A product whose listing has gone is searched again and marked out
of stock if nothing matches; products not reached in a run keep their
last state.

## Real prices from UK shops

`start.bat` / `start.command` run `setup_shops`, which adds 29 UK shops
(Total Cards, Gathering Games, Magic Madhouse, The Card Vault, Lvl Up
Gaming, Zatu Games, Goblin Gaming, Travelling Man, JET Cards, Titan Cards,
Buy Any Cards, The TCG Shop, Double Sleeved, Packrat, The Gamers Lodge,
Kongs Cards, MaxOnCards, Japan2UK, Iconic Trading Cards, Card Empire,
Griffins Gaming, Tayler TCG, Shiny Vault, Monarch Cards, Castle Comics, 120HP,
Ancient Warrior, Unicorn Cards and Sports Cards Direct) with the delivery rules read from their delivery pages and baskets,
and `remove_demo`, which deletes the fictional demo retailers. Shops that
cannot be read automatically, and why, are listed under "Shops we cannot
read" below. A Shopify shop that prices in another currency is refused
(Poke-Collect turned out to be a US shop pricing in dollars, so it is
hidden). Then `update_prices` (double-click) or
`python manage.py import_prices` pulls every product from each shop.

Shops publish tens of thousands of items, almost all single cards. The
importer classifies each one (`catalogue/classify.py`): sealed products
(booster boxes, Elite Trainer Boxes, bundles, collections, decks, tins,
packs, gift sets) for the ten games it knows become RipRaptor products
automatically, with the shop's photo; singles, accessories, events, cases
and multipacks are skipped. The same product from two shops lands on one
page by name. Nothing under two pounds is treated as sealed. `tidy_catalogue`
re-checks products the importer created: it removes any that a later,
stricter version of the rules would not have created, tidies names (HTML
entities, a set name repeated after the product) and merges the duplicates
that shows up. Run it with `--dry-run` first to see what it would do.

Only Total Cards publishes barcodes, so most offers are matched by name.
An offer links to an existing product automatically only when it is a
sealed product of the same game and its title and our name agree both
ways (every meaningful word of ours in the title, and most of the title's
own words in ours), or when both boil down to the same matching key (the
identifying words plus the kind of thing: box, pack, bundle, deck).
Anything less certain goes to Shop products to review. Shops mark
pre-orders in a pre-order collection, which the importer reads, so a
pre-order never shows as in stock.

Three cleanup commands keep the catalogue honest after the rules improve
(`tidy_all` runs them in order; `start.bat` and `update_prices` run it
after their import, and the server's cron runs it at five past every hour):
`tidy_catalogue` (products that no longer pass), `merge_duplicates` (one
product under two names) and `tidy_listings` (a shop item linked to the
wrong product, judged by the words in its shop address; it also hides an
eBay listing the current rules refuse: a price under 40% of the cheapest
any shop last showed, in stock or not, or a title that is a multi-buy, a
code, a sampling pack or another language. Listings keep the shop's own
title for this, so a new rule applies at the next hourly import rather
than the next eBay run; an eBay listing saved before titles were kept has
its title fetched through eBay's bulk lookup first, which does not touch the
search allowance). Each takes
`--dry-run`. No page ever shows a saving or a price drop above 70%, because
a gap that large is a wrong link, not a bargain, and such a product gets no
badge and is never featured. Savings name the shop they are measured
against ("Save £3 vs Zatu"), and only compare confirmed delivered prices.

```sh
python manage.py suspect_savings
```

lists those products with both links, so you can untick "show on site" on
whichever listing is the wrong product.

Every price check that changes a price or its stock is also judged against
the other shops' prices for the same product (`catalogue/sanity.py`). A
price under a third of, or over four times, what two or more other shops
charge is kept out of every comparison and shown on the product page as not
counted. A price well under or over twice the others, or two shops more than
70% apart, is doubtful: still shown, but never claimed as a saving, a badge
or a price drop, and listed for you to look at. Marketplaces are judged but
never judge anyone. An excluded price stays out while fewer than two other
shops are left to judge it, so it never becomes the cheapest just because the
others sold out. It comes back without them only when the shop changes the
price and the new price's own evidence (below) says it is right. Nothing is
deleted, and the verdict is worked out again on
the next change, on every read while it is not OK, when a shop comes back
from being out of date, when your confirmation runs out, when you hide a
price, and when you save a listing in admin.

A price with fewer than two other shops to compare against is also judged
against its own evidence. Under 35% or over three times the shop's last good
price, or under 30% of the product's lowest price in the 90 days before today
(once there are 7 days of history), is doubtful, never excluded: old history
can hold prices from listings since deleted. A price that is not OK is judged
only by the history from before it stopped being OK, so a doubtful price that
reached the daily history cannot clear itself the next day. Each kind of product in each
game, and in each set, has a usual price range once 8 or more products have
an OK delivered shop price (Admin > Price bands). Under a quarter of the
cheapest tenth is excluded, under half of it doubtful, and over four times
the dearest tenth doubtful. A set's range is used before the game's. When one
other shop charges much the same, the range can only make both prices
doubtful, since two shops agreeing may mean the product is filed under the
wrong kind. The product page says a price the range kept out is far below the
usual price for its kind, not far from other shops.
`snapshot_daily_prices` rebuilds the ranges every night. To look at them or
rebuild them at once:

```sh
python manage.py price_bands            # rebuild now and print the table
python manage.py price_bands --dry-run  # print without saving
```

All of this can be done from a phone, without the server console: admin
has a **Things to check** page (`/admin/checks/`) that lists doubtful prices
with "This price is right" (it then counts while it moves less than 10% for
30 days) and "Hide this one", the prices excluded automatically with "Show it
anyway", the wrong matches with a Hide button under each price, the possible duplicates the
`--loose` rule finds with a Merge button for each group (only the group
exactly as shown is merged) and "Not the same" beside each product (the
pair is never suggested again), the products the stockist finder may have
found at another shop with "Yes, link it" and "No, not this" (see
"Stockist finder" below), the ones visitors want most first, and the shops
whose delivery charge is not known with a link to fill it in.

Only rows that change what a visitor sees are listed. A doubtful price is
listed only while it is the product's cheapest buyable price: one that is
dearer than another shop, or out of stock, never shows as the cheapest and
is left as it is (the page says how many). A product whose second price is
doubtful is not a wrong match. Two products a shop sells side by side are
never suggested as duplicates. A set showing its publisher's date is not
listed because a community source says otherwise.

### The autopilot

Every hour (`tidy_all`), and when you tap **Sort what you can now**, the
autopilot (`catalogue/autopilot.py`) answers the rows the evidence settles.
It uses only what the site already holds: the shop's own title and the
prices the other shops charge. It answers each row once, never merges and
never counts a doubtful price on its own say-so.

- **Found at another shop: No** when the shop's title plainly names another
  kind of product (a single pack against a box, an ETB against a bundle; a
  bare "Booster", "Box Set", "Gift Set", "Display" or "3-Pack" is never
  taken as another kind) or only another set's code (OP-10 against OP-09,
  also EB, PRB, ST, FB and BT codes), or its price is under 0.3 or over 3.33
  times the median of two or more other shops' prices (one other shop may be
  the wrong one). **Yes** when the names agree word for word both ways, the
  shop does not list the product already, and the price is within 0.75 to
  1.33 of the other shops' median. A likely name, or a page with no other
  shop to compare, waits for you.
- **Doubtful prices and wrong matches: Hide**, for the product's cheapest
  price or either price of a wrong match, when the shop's title plainly
  names another kind of product or another set's code. A dearer doubtful
  price is left alone: hiding it could leave a wrong price as the cheapest.
- **Announced sets: Add set**, with no date, when a community source names a
  set that products on the site already name, none of them is filed under
  another set, and the game has a publisher source that can give the date
  later (Pokémon, Magic, One Piece, Dragon Ball, Vanguard, Weiss Schwarz,
  Flesh and Blood). For Yu-Gi-Oh, Lorcana and Star Wars Unlimited the set
  waits for you, so you can give its date.

Each answer is listed at the top of the page under **Sorted for you** for 7
days, with what was done, why, and an **Undo** button. Undo puts it back the
other way: a linked page is unlinked and that shop is not asked about it
again; a refused page is linked; a hidden price is shown; an added set is
taken away (unless it has since gained a date or another source) and the
source is not asked about it again. After an answer, undone or not, the
autopilot leaves that row to you, and it never touches a price you hid
yourself. A row that changed between the autopilot reading it and acting
on it is left for the next run. `RIPRAPTOR_AUTOPILOT=0` stops the hourly
run; the button still works. `python manage.py tidy_all --dry-run` lists
what it would answer.

### The Claude judge (paid)

What the free checks leave, Claude can answer (`catalogue/judge.py`). It is
a paid Anthropic service, so it does nothing until you save a key and
switch it on in the **Claude** box at the top of Things to check.

Claude only says whether two things the site already holds are the same
product: a found shop page and our product, a shop's price and our product,
or two of our products. It never supplies a price, a date, a barcode or a
product, and never touches sets, release dates or delivery charges. The
site's own rules decide whether its answer may act:

- **Found at another shop:** Claude's "different" refuses the page; its
  "same" links it only when the price is within 0.75 to 1.33 of the other
  shops', the title does not plainly name another kind or set, the shop
  does not list the product already, and the new price is judged OK
  without making any other price doubtful.
- **A shop's price** (the cheapest doubtful one, or a wrong match): "different"
  hides it; for a wrong match only when Claude is sure the other price is
  the product. "Same" never counts a price as right.
- **Possible duplicates:** "different" keeps the pair apart. "Same" never
  merges by itself: **Merge the pairs Claude is sure are the same** merges
  them on your tap, each so it can be undone. The merged product is switched
  off, not deleted; its barcode, shop pages and linked shop rows move to the
  kept product so shop reads never price it; and Undo puts back its listings,
  history and address. A price that has not changed since the merge gets
  back the verdict and last good price it had; one that changed is judged on
  its own evidence; one first seen while merged is judged afresh. The kept
  product's days after the merge held both products' prices, so Undo removes
  them rather than leave a price history that never happened. Merges undo
  newest first: one that a later merge touched (into the same product, or
  merging the kept product on) waits until that later merge is undone, and
  the page says which.

It starts in **trial**: it only suggests, and its answer shows under each
row ("Claude, 9 Oct: same product, sure. ..."). Once you agree with it, tap
**Let Claude act**. It then acts only when it is sure, a "different" names
something other than the price, the answer came from the model asked (not a
fallback), and the row is still as Claude saw it. Each act is listed under
Sorted for you with Claude's reason and an Undo. The box counts how often
Claude agreed with your own taps.

**Cost.** Each request is priced from Anthropic's usage figures at the
model's rates and saved (Claude's answers are listed read-only in admin).
Before a request is sent, the most it could cost is reserved, and nothing is
sent that could take the month (a UTC month, as Anthropic counts) past your
limit, or one run past $1. A row is asked again only when its titles or
price band change, at most three times. Roughly, per answer at medium
effort: Claude Opus 5.5 (the default) $0.023, Claude Sonnet 5.5 $0.012,
Claude Haiku 5.5 under $0.001. The limit defaults to $10 a month, about 430
answers on Opus. Low effort costs about half, high about twice.

**Setting it up by phone.** Sign up at console.anthropic.com; under Billing
buy credit; under Limits set a monthly spend limit (Anthropic then stops the
account at that amount, whatever the site does); under API keys create a
key. In Things to check open **Claude settings and key**, paste it and tap
**Save key**: it is saved in a file beside the database that only the site
can read (never in the database or its backups) and never shown again, and
Claude's first run checks it with Anthropic, which is free. Then tap **Switch Claude on**. To stop: **Switch Claude
off**, **Forget the key**, or delete the key in the Console.

**When it runs.** Cron runs `judge_checks` every five minutes on its own lock.
It does something only when you tapped **Ask Claude now**, or an hour after
its last run when rows are waiting, and never while Pause all is on. Each
run lets the free autopilot answer first, then asks Claude about up to 25
rows, one per request: the cheapest doubtful prices, the wrong matches, the
found pages (most wanted first), then the duplicates. A wrong key, no
credit or a model the key cannot use stops it and tells you once a day by
push and email; it starts again when you save a key, change the model or tap
Ask Claude now, or after a day. A busy Anthropic only ends that run.
`python manage.py judge_checks --dry-run` lists what would be sent and the
most each request could cost, and sends nothing.

**Seeing what it does.** The Claude box says what Claude is doing now ("Claude
is looking now: 7 of 25 asked so far", "Claude starts within 5 minutes", "Next
look about 15:05") and warns when a run you asked for has not started after 15
minutes, which means the server's timer has stopped. It says when Anthropic
last accepted the key, lists the last few runs (what was looked at, sorted,
left for you and spent) and Claude's last 20 answers with what came of each,
and links to every answer in admin with its cost. Each section Claude reads
says how far it has got ("Claude so far: 12 the same, 3 different, 1 could not
tell, 30 still to look at"). When a run you asked for finishes, you get a push
and an email with the counts; otherwise at most one a day sums up the last 24
hours. Like every owner notice, they carry counts only, never a shop or a
product. A run that fails outright says so in the box rather than leaving you
waiting.

**What is sent.** Our product's name, game, kind and set; the shop's name,
title, price, stock and page address; up to five other shops' titles for the
product; whether its price is close to or far from the other shops' (never
their prices); the site's own note on the price. Never anything about
visitors, alerts or messages.

On the server the background reader (see "Background reader" below) asks
shops about single products all day: the products people look at, save,
click or wait for every ten minutes, the rest of the single-shop, pre-order
and new products every hour. `watch_stock` is the same check from cron,
run every ten minutes only while the background reader is not running.
It asks each shop
about single products (a Shopify shop answers `/products/<handle>.js` in
milliseconds). Half its budget of 300 goes to the products people are
watching: the most viewed and most saved to a watchlist over the last two
days, in or out of stock. The rest starts with in-stock items people
click, then sold-out items people click, then everything else by age. The
"Reload prices" button on a product page only re-reads what the site
holds; it never asks a shop. Anything that comes back is
stamped and shown on the home page under "Back in stock" for
`RIPRAPTOR_RESTOCK_HOURS` (48). A pre-order a shop marks as available stays
a pre-order after a single-product check until the product's release date
(or its set's) is known and has come; the next whole-shop read decides
otherwise. A Shopify product whose own title or tags say pre-order is a
pre-order after a single-product check too, as in a shop read, so a shop
opening pre-orders is never shown as a restock. A listing for one variant of a Shopify product (an address
ending `?variant=...`, for example a case rather than a box) takes that
variant's price and stock, never the cheapest on the page, and is left as
it is when the shop no longer has that variant. In a whole-shop read, a
variant that is another product than its page ("1 Pack", "18 Packs", "Half
Box" or "Case" on a booster box page) is left out before any matching, so it
never prices the box; "English", "Japanese" or the page's own count ("36
Packs" on a "(36x Packs)" page) are not left out. A box title counts its packs
only in brackets straight after the box ("Booster Box (36x Packs)"); any other
"36x" is read as a multi-buy and refused. eBay and Amazon listings are
never checked this way, and nor are feed shops: their prices come from the
feed agreed with them, and their links are affiliate clicks a server must
never follow.

Price history starts the day a product first gets a price and is kept for
ever; the product page charts the last 90 days and, after 30 days, says
what the cheapest price was a month ago. `snapshot_daily_prices` runs
nightly on the server to record each day's cheapest price.

A whole read takes a few minutes for a Shopify shop and up to about 25
minutes for a website shop, so leave it to the server rather than a
laptop. On the server the background reader and the cron fallback share a
lock, so a read by hand never overlaps one of theirs.

### Shops we cannot read

Probed and not added, so nobody repeats the work. A feed from an affiliate
network is the way in for all of these.

- **Chaos Cards, Big Orbit Cards, Plus Cards, Legacy TCG, TCG Shop UK**:
  a bot challenge (Cloudflare or similar) answers every scripted request.
- **The Brotherhood Games, Bath TCG, Card Catcher Shop** (Square Online),
  **Azgard Collectibles** (GoHighLevel), **Hills Cards**: product pages are
  JavaScript shells with no price in the HTML.
- **TCG Globe**: readable, but a challenge page appears after a few
  requests and every product seen was sold out (new shop).
- **Trading Card Games (Newmarket)**: closed until it reopens; every product
  sold out. **Hobby Quarter**: every product sold out, prices are RRP.
  **AJ-TCG**: dormant, every product unavailable.
- **Troll Trader, Cob and Pip, Evolution Trading Cards, Mage Cards,
  Findablez**: singles shops. **Sports Trading Cards
  UK, P Commando Cards, Fanter, 3rd Down**: sports cards (Sports Cards
  Direct is connected for its football ranges). **Battleground
  Gaming, 7th City Collectables, Get Decked Games, Hidden Chest, Full Moon
  Gaming, Quirky and the Geek**: no sealed TCG stock online.
- **Cardarium**: looked like a fabricated storefront with below-market
  prices; not added on purpose.
- **PokeUK**: blocks its sitemaps. **Poke-Collect**: a US shop in dollars.
  **Zardo Cards**: a Canadian shop in dollars. **Shop Rurob**: registered in
  the US, three sealed products. **Cisul**: a Birmingham gift shop whose
  card items are hidden from its product feed and are collection-only.
  **Ancient Warrior**: connected, but sells no sealed card products.
  **Asmodee UK**: the distributor's own store; the owner chose not to compare it.

## Getting real prices in

1. **Products.** `seed_catalogue` loads the current sets and products. Check
   the release dates and add each product's **barcode** (EAN) in
   Admin > Products. Barcodes are how imported prices are matched. Upload a
   product image on the same page.
2. **Retailers.** Add each retailer in Admin > Retailers with its standard
   delivery charge and free delivery threshold, then choose a price source:
   - **Shopify store**: enter the shop address. Most UK card shops run on
     Shopify and publish their products at `/products.json`. Check the shop's
     terms allow automated price checks before using this.
   - **Website**: for shops that are not on Shopify. Enter the shop address;
     the importer reads the shop's sitemap and each product page's schema.org
     data (price, stock, barcode, image), which most Magento, WooCommerce,
     BigCommerce and custom shops publish. It pauses half a second between
     pages. A big shop can list 100,000 pages, so pages whose address reads
     as a sealed product come first, pages whose address reads as a single
     card or accessory are skipped, and the game is taken from the address
     when the page title leaves it out. Up to 3,000 pages a shop are kept in
     a page index (see "Website shop pages" below) and each read fetches 600
     of them.
   - A shop that shows each visitor their own currency (Unicorn Cards shows
     dollars to an American address) gets a "visit first" address on the
     retailer: the importer opens it before reading pages and keeps the
     cookie it sets, so every page is read in pounds. Pick a server in the
     UK for the same reason; a German or Finnish server is shown euros by
     such shops unless the switch address is set.
   - **Product feed (CSV)**: enter the feed address from your affiliate
     network or the retailer. Columns `ean`, `url`, `price` are needed;
     `title`, `availability` and `delivery` are used if present. Common
     column names from Awin and Google Merchant feeds are recognised.
   - **Entered by hand**: add listings yourself under the product.
   Shops that publish no barcodes are matched by name. The importer links
   a shop product automatically when every word of our product's name is in
   the shop's title and nothing suggests a different item (a case, a pack, a
   playmat). Anything less certain goes to Admin > Shop products to review
   with a suggested match: correct it if needed, tick the rows, and choose
   "Link to our product". Mark anything that is not a sealed product as "Not
   one of ours" and it stays hidden. Linked products are matched by link on
   every later import.
3. **Import.** `python manage.py import_prices` fetches every retailer with a
   source, or `import_prices <retailer-slug>` for one, or `--feed file.csv`
   for a local file. Run it from cron, hourly is sensible. With `--due` it
   reads only the shops whose turn has come (see "Reading schedule" below);
   the server's cron uses that. Each run is
   recorded in Admin > Price imports with the retailer products that could
   not be matched, so you can add the missing barcodes.
4. **Images.** Price imports keep the retailer's product image for any
   product without one (`RIPRAPTOR_USE_FEED_IMAGES`, on by default). An
   uploaded image always wins. Admin > Products can be filtered by image and
   by barcode, and barcodes can be edited in the list or loaded in bulk with
   `python manage.py import_barcodes barcodes.csv` (columns `ean` and `slug`
   or `name`).
5. **History.** `python manage.py snapshot_daily_prices` once a day keeps the
   price history complete.

Retailer products the site no longer lists are marked out of stock at that
retailer after an import. Nothing on the public site is invented: a product
with no listings says so.

## Terms and disclosures

The full commission, independence, price and data statements live on
`/terms/`. Every page's footer carries one short line and a link to it. All
of that wording is under Admin > Site wording > Legal and disclosure text.

## Swipe page

`/swipe/` shows one product at a time with its cheapest price, retailer and
runner-up. Swipe right (or press the right arrow) to save it to a list kept
in the browser, left to skip, tap to open the product. Cards come from
`/api/deck/` twelve at a time and the next batch loads before it is needed.

## Search as you type

The search box shows results while the visitor types, with the cheapest
delivered price and stock for each, from `/api/search/`. The form still
submits to the full results page, so search works with JavaScript off. The
"Checking lowest prices" message and the "All results" link are editable in
admin.

## Project layout

| App         | What it holds |
|-------------|---------------|
| `catalogue` | Games, sets, products, retailers, listings (one retailer's offer for one product), daily lowest prices and click counts. Price queries live in `catalogue/pricing.py`, search in `catalogue/search.py`. |
| `content`   | Editable site wording (`SiteContent`), its defaults in `content/registry.py`, and the `{% copy %}` template tag. |
| `web`       | Public views, templates, CSS, the font and the favicon. |

## Editing site wording

Almost every sentence, heading and button label on the public site is stored in
the database and edited in **Admin > Site wording**.

- **Site text** lists everything except legal wording, in the order it appears
  on each page. Filter by page, by type (heading, short text, paragraphs,
  button label) or by whether it has been edited. Search by key or wording.
- **Legal and disclosure text** holds the commission and independence
  statements and the price disclaimer. They are kept apart so they are not
  changed by accident, can't be left empty and are always shown.

Each item shows where it appears, the default wording, and any placeholders it
supports. Placeholders such as `{price}` or `{retailer}` are filled in when the
page is shown, for example `Buy for {price}` becomes "Buy for £44.99".

To undo an edit, tick **Restore default wording** and save, or select several
rows and use the **Restore default wording** action. Unticking **Use this
wording** shows the default but keeps your edit for later.

Saving checks the wording and explains any problem: em dashes, unknown
placeholders, HTML, or line breaks in one-line text are rejected.

Edits show on the next page load. There is no cache to clear.

### What is not editable in admin

Short functional labels tied to data stay in code: stock statuses (In stock,
Pre-order, Out of stock) and product types in `catalogue/models.py`, filter and
sort labels in `web/views.py`, relative times ("18 minutes ago") in
`web/templatetags/ripraptor.py`, screen reader hints such as "(opens in a new
tab)", and the static `500.html` page, which must work when the database does
not.

### Adding a new piece of wording

1. Add an `Entry` to `content/registry.py` with a key starting with its page,
   for example `product.delivery_note`.
2. Use it in a template: `{% load sitecopy %}{% copy "product.delivery_note" %}`.
   For numbers, `{% copy_count "browse.count" total %}` picks the `.one` or
   `.other` key.
3. Run `python manage.py sync_site_content` (it also runs after every
   `migrate`). Existing edits are never overwritten.

The test suite fails if a template uses a key that is missing from the
registry, or if any default contains an em dash or an exclamation mark.

## Changing the look

Every colour, size, radius and spacing value is a CSS custom property in
`web/static/css/tokens.css`. `site.css` uses nothing else.

| To                           | Change |
|------------------------------|--------|
| Reduce the corner radius     | `--radius`, `--radius-tag` |
| Make pages denser or roomier | `--density` (1 is the default, 0.875 is denser) |
| Change the accent colour     | `--color-accent`, `--color-accent-soft` |
| Make product titles bigger   | `--text-product-title` |
| Widen the page               | `--page-width` |
| Change heading or price width | `--stretch-display`, `--stretch-price` |

The typeface is Archivo, self-hosted from `web/static/fonts/` (SIL Open Font
License), so there are no requests to Google Fonts.

The wordmark is plain text in `web/templates/web/includes/wordmark.html`.
Replace that file to change the logo everywhere. The favicon is
`web/static/img/favicon.png`. The home screen icons (`icon-192.png`,
`icon-512.png`, `icon-maskable-512.png` and `apple-touch-icon.png`) are
made from `web/static/img/mark.png` by `python manage.py make_icons`; run
it again after changing the mark and commit the files.

## Prices

- A **listing** is one retailer's offer: product link, item price, delivery
  charge for one item to a UK address, stock status and when it was last
  checked. The delivered price is worked out by the database.
- The **cheapest delivered price** only uses listings that are in stock or on
  pre-order and were checked within `RIPRAPTOR_STALE_AFTER_HOURS` (72 by
  default). Older listings still appear on the product page, marked as not
  checked recently.
- Custom importers should call `catalogue.pricing.record_check(listing,
  price=..., delivery_cost=..., availability=...)` after checking a retailer.
  It updates the listing and today's lowest price. The built-in Shopify and
  CSV importers are in `catalogue/importers.py`.
- Unchanged offers are stamped, not rewritten. When a shop read finds the
  same price, delivery, stock state and link a listing already holds, only
  its last checked time moves, 500 listings to one short write, before each
  progress note on the run (every 250 offers), at the end, and when the read
  fails part way. Only offers that change something go through
  `record_check`, so a read of a large shop writes a few dozen rows instead
  of thousands.
- A price of £0.00 or less is not a price, whatever the stock state says (a
  shop opening a pre-order before pricing it, a deposit variant, or a page
  that lost its price). Such an offer can only take an existing listing out
  of stock. It never makes a listing buyable, never moves its last checked
  time otherwise (so an old price ages out as usual), never changes its link
  or title, and writes no restock or price history. When a product has
  priced and unpriced variants in the same read, only the priced ones count,
  whatever order the shop lists them in. No new listing is made from such an
  offer; it appears in the run's unmatched list ending "(no price)".
- Run `python manage.py snapshot_daily_prices` once a day (for example from
  cron) so price history and "Price drops this week" have no gaps.
- Retailer affiliate links: set **Affiliate link format** on the retailer, for
  example `https://network.example/click?id=123&url={url}`. All outbound links
  go through `/go/<listing id>/`, which counts the click (no personal data is
  stored) and redirects.

## Putting it on a server

Quickest: a fresh Ubuntu 24.04 server, the domain's A records pointing at
it, then as root:

```sh
curl -fsSL https://raw.githubusercontent.com/Dimondb1/cardthing/claude/compassionate-edison-aot3li/deploy/install.sh | sudo bash -s ripraptor.com
```

`deploy/install.sh` installs Python and Caddy, clones the code to
`/srv/ripraptor`, writes `.env` with a generated secret key, migrates,
collects static files, sets up the shops, starts the app and the
background reader as services, configures HTTPS for the domain and
schedules the cron jobs. The reader reads every shop on its first start. Run the same command again to update. The repository must be
public (or the server needs a token) for the clone to work.

Each update first copies the database to `/var/lib/ripraptor/backups/`
with `backup_db` (the newest five are kept; see "Backups, timeouts and runs
cut short" below). To roll back, stop the site and the cron jobs, copy the
backup over the live database and check out the previous code. The `-wal`
and `-shm` files belong to the database being replaced, so they go too;
left in place they would be applied to the restored copy.

```sh
sudo systemctl stop ripraptor ripraptor-worker cron
sudo rm -f /var/lib/ripraptor/db.sqlite3-wal /var/lib/ripraptor/db.sqlite3-shm
sudo cp /var/lib/ripraptor/backups/db.sqlite3.YYYYMMDD-HHMM.bak /var/lib/ripraptor/db.sqlite3
sudo chown ripraptor:ripraptor /var/lib/ripraptor/db.sqlite3
cd /srv/ripraptor && sudo -u ripraptor git checkout <previous commit>
sudo systemctl start ripraptor ripraptor-worker cron
```

The manual steps:

The `deploy/` folder has everything for a small Linux server (a £4 to £6 a
month VPS is enough):

1. Clone the repository to `/srv/ripraptor` and run `sudo deploy/setup.sh`.
   It creates a virtualenv, installs requirements, applies migrations,
   collects static files, loads the catalogue, installs systemd services
   for the app and the background reader, and the crontab.
2. Edit `/srv/ripraptor/.env` (start from `.env.example`): a long random
   `DJANGO_SECRET_KEY`, your domain in `DJANGO_ALLOWED_HOSTS` and
   `DJANGO_CSRF_TRUSTED_ORIGINS`, then `sudo systemctl restart ripraptor`.
3. Install Caddy, put `deploy/caddy.Caddyfile` at `/etc/caddy/Caddyfile`
   with your domain, and reload it. Caddy fetches the HTTPS certificate.
4. Create an admin user: `sudo -u ripraptor .venv/bin/python manage.py createsuperuser`.

Static files are served by the app itself (WhiteNoise) with hashed names
and long cache headers, so no separate static hosting is needed. Uploaded
product images live in `media/`; back that folder and the database up.

## SQLite

The database is one SQLite file shared by the web app, the cron jobs and any
command you run by hand. It runs in WAL mode (write-ahead log), so readers
never wait for a writer and a writer never waits for readers; only two
writers queue, and the busy timeout is 20 seconds, so a short write waits
rather than failing with "database is locked". The three tidy commands
commit after each product or group rather than holding one transaction
across the whole table. The few writes a visitor's request makes (page
counts, clicks) are best effort: if the file is still locked after one busy
wait, the count is dropped and logged, and the page or the trip to the shop
goes ahead.

Two extra files sit beside the database: `db.sqlite3-wal` holds changes
not yet folded into the main file and `db.sqlite3-shm` is its index. Never
copy the database with `cp` while anything is running: the copy misses
whatever is still in the `-wal` file. Stop the site and the cron jobs
first, or use SQLite's own backup (`sqlite3 db.sqlite3 ".backup copy.sqlite3"`),
which reads a consistent snapshot. Never delete the `-wal` file by hand.

## Reading schedule

Each shop carries its own reading schedule, shown under Reading on the
shop's page in Admin > Retailers:

- **Read every (minutes)** is how often the shop is read. It can be changed
  straight from the Retailers list. `setup_shops` sets Shopify shops to 45
  minutes and everything else to 60, once: only shops never read on a
  schedule and still on the default are changed, so a value you set stays.
  The hourly run rounds it to whole hours: up to 75 minutes is every hour,
  76 to 135 every two hours, and so on. A shop that takes a long time to
  read is read less often, never more than a third of the time: a read that
  took 30 minutes is next due 90 minutes after it ended.
- **Next read** is when its turn comes. The background reader and
  `import_prices --due` stamp it before reading, so a read that crashes is
  not retried in a loop.
- **Failed reads in a row** and **waiting after errors until**: after a
  failed read the shop waits 5 minutes, then 10, 20 and so on up to 6
  hours, and is read again once both that wait and its usual interval have
  passed. A shop that says it is being asked too often (HTTP 429) waits at
  least 30 minutes, and longer when the doubling has gone past that. A read
  that stops on an error nobody foresaw counts as a failed read too, and the
  run carries on with the next shop. One read that works clears both.
- **Reading paused** keeps the shop out of every scheduled read and out of
  the ten-minute stock checks. Reading it by name (`import_prices <slug>`)
  still works.

The background reader reads each shop when its next read comes. When it is
not running, the hourly cron runs `import_prices --due`. A shop's next read is counted
from the start of the run that read it, and each run also reads a shop
whose next read falls within 15 minutes of its start, so a run that starts
a few minutes late (waiting for the stock watch, say) does not push a shop
read every 45 or 60 minutes to every other hour. A shop still waiting after
errors is never read early. The run checks again after each shop so one
that comes due during the run is read too. eBay and Amazon keep their own
once-a-day limit inside the import: their hourly turn only retries a read
that failed. `import_prices` with no slug and no `--due` reads every shop,
as before. Insights shows each shop's schedule under Shop health, and a
shop counts as not updating when its last good read is older than 6 hours
or two of its intervals, whichever is longer.

## Website shop pages

A website shop has no product list to read in one go, so each read takes a
slice of its sitemap. Every page the sitemap lists that is worth fetching
(up to 3,000 a shop, sealed products first) is kept as a shop page with the
words of its address, when the sitemap says it last changed, when it was
last fetched and, once a price from it is linked, the product it sells.
Each read fetches at most 600 pages, in this order:

1. pages never fetched;
2. pages not fetched for a day, longest unread first, so no price nears the
   72 hour stale cutoff even when a shop dates hundreds of pages as changed
   on every read;
3. pages the sitemap dates after their last fetch, longest unread first;
4. pages the sitemap does not date, longest unread first.

A page the sitemap dates before its last fetch waits until it has not been
fetched for a day, so a stock change the shop does not date is still caught.
A date in the future, or one that cannot be read, counts as no date.

A shop whose sitemap carries no dates is read round the whole index in
turn: 600 pages a read, so 3,000 pages are all refreshed within five reads.
A page that fails to load counts as fetched, so it cannot hold the front of
every read. Pages a shop's sitemap has not listed for 30 days are removed
by `tidy_all`. A website read never marks unseen products out of stock.

## Crawl health

Admin, Crawl health (`/admin/crawl/`, linked from the admin home page and
from each shop under Shop health on Insights) shows whether shops are being
read and lets you change it with one tap, from a phone:

- The status line says whether the background reader is running: green
  while its heartbeat is under ten minutes old, red when it has stopped or
  never started (the hourly schedule then reads the shops). Below it: when
  the last read finished, how many products are checked every ten minutes
  and every hour, the requests to shops and the errors in the last hour,
  the jobs done since it started, and why it last restarted itself, if it
  did. The admin home page shows a red line under Crawl health when the
  reader has stopped. One line says how crawl problems reach you: by push,
  email or both, turned off (`RIPRAPTOR_CRAWL_PUSHES=0`), or in red when
  neither `RIPRAPTOR_NTFY_TOPIC` nor `RIPRAPTOR_INBOX_NOTIFY_EMAIL` with
  ZeptoMail is set, so nothing is sent.
- **Pause all** stops every scheduled read and stock check until you tap
  **Resume all**: the background reader starts no new job (the ones
  running finish, and its heartbeat goes on), `import_prices --due` and
  `watch_stock` read nothing, and a run already reading stops before its
  next shop. Runs left open by a crash or a timeout are still closed.
  Reading a shop by name still works. Shops you paused one by one stay
  paused when you resume. The switch is a row in the database (the
  background reader's), so the site, the reader and the cron all see the
  same thing and a deploy that empties the cache leaves it alone. A
  `crawl-paused` file left in the cache folder by the earlier version still
  counts as paused until Resume all removes it. If the database is busy the
  page says so and nothing changes.
- One row per shop read on a schedule: Reading (its latest run has not
  finished), Paused, Backing off until a time, or Idle, then its last read
  that worked, how long that took, its next read (paused while it or every
  shop is paused, and never before a wait after errors ends), its errors in
  a row and its last error.
- **Read now** makes the shop due at once and forgets its errors and its
  wait, so the background reader takes it within a few minutes (or the next
  hourly read does when the reader is not running). **Pause** and
  **Resume** set the shop's Reading paused box, which stops its stock
  checks too.

## Background reader

`python manage.py run_worker` is one long-running process (systemd unit
`deploy/ripraptor-worker.service`, installed and started by `install.sh`)
that keeps prices fresh all day:

- **Whole shops** when their next read comes, by the same rules as the
  hourly cron (Reading schedule above): Shopify shops every 45 minutes,
  website shops and feeds every hour, eBay and Amazon tried hourly under
  their once-a-day limit.
- **Single listings** of the products that matter (`catalogue/heat.py`):
  points for page views today (4 each, at most 20), a watchlist in the
  last two days (6), clicks in the last week (3 each, at most 15), a
  confirmed back-in-stock alert waiting (8), one shop or none selling it
  (5), any pre-order (6) and being new in the last fortnight (5). With 10
  points a product's listings are checked every ten minutes (at most 600
  listings; the rest wait their turn hourly), with 3 every hour, otherwise
  only by its shop's whole read. A listing checked in the last 8 minutes,
  or at a shop being read, is skipped. Feed shops, eBay and Amazon are
  never asked about single listings.
- **The pre-order pulse** for each Shopify shop every 15 minutes (see
  Pre-order pulse below).
- **Release sources**, one at a time, every 6 hours (Scryfall once a day),
  for announced sets and release dates (see Release radar below). A source
  read has two minutes and leaves one thread free, like the stockist finder.

There is no job list: every minute it works out what is due from the shops
and listings themselves, so a crash or a deploy loses only the jobs that
were running. It is polite: one job per shop at a time; at most one request
a second to a Shopify shop (three at once), one every two seconds to a
website shop and one every ten seconds to a feed; two a second in all;
half a second between Shopify product pages; at most two whole-shop reads
at once and only one of a website shop, so one of its three threads is
always free for single listings. A shop waiting after errors or paused is
not asked about single listings either. A shop that answers a single-listing
check with 429 (asked too often), or fails three checks in a row (a listing
it no longer has does not count), waits exactly as after a failed read: 30
minutes after a 429, otherwise 5, 10, 20 and so on, with no single-listing
checks and no whole read until the wait is over. Its Crawl health row says
"Checking single listings: ..." with the error.

Safety: only one reader runs (`/tmp/ripraptor-worker.lock`). It holds the
import lock (`/tmp/ripraptor-import.lock`) only while a whole-shop read is
running, so `import_prices` run by hand, the nightly snapshot and the weekly
delivery check wait only for reads in flight; after 20 minutes of holding it
without a break it starts no new read until it has let go, so they always
get a turn. Each job has a time limit: 20 minutes for a shop read, 45 for a
website shop (600 pages at one every two seconds), 60 for eBay and Amazon,
or twice the shop's last healthy read when that is longer (at most two
hours), and a minute for a single-listing check, where each request is
given up after 10 seconds however slowly the shop sends it. A job past its
limit marks its shop "Stuck, restarting" (which counts as a failed read, so
the shop waits before it is asked again), closes its run with "Stopped
before it finished.", closes the other reads in flight and makes those
shops due again at once, and ends the process; systemd starts a new one
ten seconds later, and on start the reader closes the runs it had in flight
and any older than three hours. It writes a heartbeat every 30 seconds to
the database (Crawl health reads it) and to systemd's watchdog, which
restarts it after three minutes without one. It is capped at 300 MB of
memory and runs at a lower priority than the site.

To stop it, start it or read its log on the server:

```sh
sudo systemctl stop ripraptor-worker
sudo systemctl start ripraptor-worker
journalctl -u ripraptor-worker -f
```

`run_worker --once` plans once, runs every job due in the calling thread
and stops. It runs `RIPRAPTOR_WORKER_THREADS` jobs at once (3 unless set);
set it to 2 in `.env` on a server with under 1 GB of memory and restart the
reader. `install.sh` copies the unit file on every update, so the setting
belongs in `.env`, not in the unit. `--max-threads` overrides it for one run.
`check_worker` prints how old the heartbeat is and exits with code 1 when
it is over ten minutes old or there has never been one; cron runs it hourly
so the log shows when the reader stopped. Insights lists "Worker not
running" first among the things to improve while the heartbeat is stale.

Crawl problems reach your phone through the same ntfy topic and email
address as new messages: "RipRaptor crawl stopped" when `check_worker`
finds a heartbeat over ten minutes old, or finds none at all on two
checks ten minutes or more apart (a reader that fails every time it
starts; the first check only notes the time, so a reader still starting
is not reported), "A shop keeps failing" when a shop that is not paused has failed
every read for more than a day, and "Prices to check" when more than 10
doubtful prices are waiting on Things to check. The reader looks for the
last two on each planning pass, except while Pause all is on. Each is sent
at most once a day, noted on the background reader row. The push is only a
title and a link to Crawl health or Things to check; the email names the
shops and their errors. `RIPRAPTOR_CRAWL_PUSHES=0` in `.env` turns them
off. Insights also lists "N doubtful prices waiting" while any wait.

What cron still does: if the reader stops or never starts, the hourly
`import_prices --due --if-worker-dead 30` and the ten-minute
`watch_stock --if-worker-dead 30` read the shops as before once its
heartbeat is 30 minutes old; while it runs they exit at once. Between 10
and 30 minutes after the heartbeat stops, Crawl health and `check_worker`
already say the reader has stopped but cron has not taken over yet, so
nothing reads the shops; systemd normally restarts the reader within that
time. `tidy_all` runs at five past every hour on its own lock and then waits
up to 30 minutes for the import lock, so it never merges or moves products
while a shop read is saving offers (its timeout starts once it holds the
lock; no new read starts while it runs, single-listing checks go on). The back-in-stock emails
every ten minutes whatever the reader is doing, the price history and a
backup (`backup_db --keep 5`) nightly, and the delivery check weekly.

## Pre-order pulse

A pre-order shows on the site within about 15 minutes of a Shopify shop
opening it, without reading the whole shop. Every 15 minutes the background
reader asks each Shopify shop for its collection list (`/collections.json`,
one small request) and compares the size and change time of the
collections it watches: any named for pre-orders or coming soon, and new
releases or new arrivals (but not one named for leaving pre-orders out).
Only a watched collection that changed, or is new, has its products read
(up to 20 pages), and they are saved like a shop read that covers part of
the shop: nothing it does not list is marked out of stock. A product in a
pre-order or coming soon collection is a pre-order while the shop has it
available; one in a new releases or arrivals collection is taken only when
its own title or tags say pre-order, because new says nothing about stock.
Tags that count: pre-order, pre-orders, Pre-Orders-Live and Coming Soon,
in any case. Pre-release event tickets are never products.

Each pulse that reads something leaves a price import noted "Pre-order
pulse: <collections>". It is not a read of the shop, so it never counts as
the shop's last read on Insights or Crawl health. A shop without a
collection list (404 or not JSON) is asked once a week; a paused shop or
one waiting after errors is not asked; a shop that answers 429 waits 30
minutes like a failed read. Pulses start at least 0.3 seconds apart. A
shop being read is not pulsed; a shop having single listings checked that
minute is pulsed at the next plan, unless its pulse is already 15 minutes
late, when the pulse goes first and the checks wait a minute. A pulse has a
minute: it starts no new request after 40 seconds. Pre-order and coming
soon collections are read before new arrivals, and one it had no time for
is read at the next pulse; a new arrivals collection read part way counts
as read, so a large one cannot take every pulse. A collection that cannot
be read is noted on the import and the others are still read. A shop that
prices in another currency has nothing applied.

Bookkeeping: a listing records when the shop published the product (from
Shopify, kept from the first read that gives it) and when it was first
seen arriving on pre-order here (a listing that was already a pre-order
before this was recorded is left blank, so it is never timed). A listing already known as out of stock that goes
on pre-order is kept as a pre-order opening (like a restock: once per two
hours, never for eBay or Amazon, never for a listing seen for the first
time, never for a price kept out of the comparison). Insights shows
"Pre-orders: N listed, median M minutes from a shop publishing a pre-order
to it appearing here (last 7 days)", the median only once 5 pre-orders
published and listed in the week can be timed, and each Shopify shop's
pre-orders and last pulse in Shop health.

`python manage.py poll_preorders` runs the pulse by hand for every shop due
one, and `--shop <slug>` for one shop now.

## Stockist finder

Products that one shop sells, or none, are looked for at every other
Shopify or website shop you have added. Nothing else is ever asked: no shop you have
not added, no search engine. Every 5 minutes the background reader takes
the 30 such products people want most (5 points per click to a shop in 7
days, 3 per product page view and 3 per watchlist row in 2 days, 10 per
confirmed stock alert, 2 for a pre-order, 2 for a product added in the
last 14 days; then pre-orders and new products, then the one looked for
longest ago) and, for each, every active Shopify or website shop that has
no listing for it, is not paused and is not waiting after errors. Shops that earn
from clicks are asked first.

At a Shopify shop it first looks again at the shop's last whole read: a line
it could not match that names the same game and kind of product, with a
name that agrees with ours word for word both ways, is the shop's page.
Otherwise it asks the shop's own search (`/search/suggest.json`), first
with the game and our name, then, when that finds nothing, with the set
code and kind, and judges the titles as a shop read judges them. The best
page found is read once (`/products/<handle>.js`) for its barcode, price
and stock. The same barcode as ours, or a name that agrees both ways when
neither side has a barcode, adds the listing like a shop read would: the
price is judged against the other shops and its history starts. A
different barcode never links. A likely match (60 to 99, or a sure name
with a barcode on one side only) is listed on Things to check as
"<our product> might be at <shop> as "<the shop's title>" for £<price>"
with "Yes, link it" and "No, not this". Yes adds the listing with the
price and stock the finder saw, dated when it saw them, and the shop's
next read checks both; a row that did not record its stock is added as
out of stock until that read. No means that shop is never asked about that
product again; the No is about that product only, so the same page can
still be matched to another of our products by a shop read or the finder.

A website shop has no search to ask, so its page index (the pages its
reads have listed, see "Website shop pages") is searched instead, with no
request: the words in the address of each page no product holds yet are
judged as a title would be. The best page at 60 or more is read once, and
only when reading it can make a sure match: its address agrees with our
name both ways, or we have a barcode to compare. The same rules then
decide, and a sure match without barcodes also needs the page's own title
to agree. A likely address that reading could not make sure is listed on
Things to check with the address words as its title and no price (or with
what an earlier read of the page saw, and when), so the shop is not asked.
Yes adds it with what was seen, and when. When no price was seen, Yes adds
nothing to the site yet: the page goes to the front of the shop's next read,
and that read adds the listing once it has priced the page. On equal scores
a page whose address names our kind of product is read first. A page whose
row you have already answered (Yes, or No for another product) is never
offered again, so the answer stands. A website shop not read yet has no
index: it is noted as not found and not asked.

A title must carry at least half of the words that name our product beyond
its kind, so another set's Elite Trainer Box is never offered for ours. A
variant of another kind on the page ("1 Pack" on a booster box page) is
never offered, and one with a count our name does not carry ("3 Packs")
only ever waits for a tap.

A shop is not asked about the same product again for 14 days (7 when it
has 10 or more interest points), the next day after a request failed, and
never after a No. The finder makes at most 20 requests to one shop and 200
in all each hour, one second apart at one shop, and never more than a
fifth of the background reader's requests, so whole-shop reads come
first. The caps hold across processes: the background reader and a
hand-run `find_stockists` count every request under one lock
(`/tmp/ripraptor-finder.lock`) in the shared cache. A shop whose search
answers 404, 429 (a bot check) or not with JSON is not searched for a
week (its last read is still looked at), and a shop that answers 429 to
any finder request is not asked again in that batch. The finder never
touches a shop's read back-off, so its errors cannot hold back the
shop's whole reads. Every product looked for is stamped, found or not. A
batch has two minutes and starts no new request after 90 seconds, and it
leaves one of the reader's threads free for single-product checks.

On Insights, each "Popular with only one shop" row says how many shops
were searched and when, with a "Search other shops now" button: the
product goes first for the next hour, in the finder and in the eBay and
Amazon lookups, and shops searched before the tap are asked again. `RIPRAPTOR_FINDER=0` stops the background reader looking.

```sh
python manage.py find_stockists                      # one batch of 30 products now
python manage.py find_stockists --requests 20        # ask the shops at most 20 times
python manage.py find_stockists --budget-seconds 60  # start nothing new after a minute
```

It prints "N products searched, M listings added, K to check".

## Release radar

Announced sets and release dates come from free publisher and community
sources, never from a shop you have not added and never from a paid
service (`catalogue/releases.py`). The background reader reads one source at
a time when its turn comes; `scan_releases` does the same by hand.

| Source | Game | Kind | Read every | Terms we keep to |
|---|---|---|---|---|
| Scryfall sets API | Magic | official data | 24 hours | an accurate User-Agent and Accept header, at most 10 requests a second, cached a day |
| TCGdex | Pokémon | community | 6 hours | no published limit; the newest 40 sets, at most 40 set pages a read, 100 ms apart |
| pokemon.com UK news | Pokémon | official | 6 hours | the news index and at most 3 expansion articles a read |
| YGOPRODeck card sets | Yu-Gi-Oh! | community | 6 hours | one request; filtered on our side; set images are never shown from their server |
| Lorcast | Lorcana | community | 6 hours | 100 ms between requests |
| SWU-DB | Star Wars Unlimited | community | 6 hours | one request |
| One Piece Card Game products | One Piece | official | 6 hours | pages 1 and 2 |
| Dragon Ball Super Fusion World products | Dragon Ball | official | 6 hours | page 1 |
| Cardfight!! Vanguard products | Vanguard | official | 6 hours | page 1 |
| Weiss Schwarz products | Weiss Schwarz | official | 6 hours | page 1 |
| Flesh and Blood coming soon | Flesh and Blood | official | 6 hours | page 1, asked with a browser User-Agent because the site refuses any other |

Web pages are fetched a second apart and no more than 25 a day across all
of them, dry runs included; each page is counted as it is fetched, so the
background reader and `scan_releases` run at the same time cannot go over
together. A source that would go over waits for the next day. Sets released
more than 180 days ago are recorded but change nothing.

pokemon.com articles are read only when their heading names an expansion
and nothing else: an article about one product of it (a Booster Bundle,
Mini Tins, a Premium Collection) gives that product's date, so it is never
opened. Once an expansion has a date from one article, other articles about
it are not opened either.

What is published, and when:

- A set is added, with its date, when the source is the game's publisher
  (marked official above) and gives a full date, or when two different
  sources give the same day.
- A month-only date ("Delivery Month November 2026") is kept on the
  announced set and never shown as a set's date.
- A lone community source waits on Things to check under "Announced sets
  to check", with Add set (a small form with the name and date filled in
  where known) and Not a set. Not a set is final for that game, name and
  source. A full date for a set the site already has without a date waits
  there too, and Add set gives that set the date.
- Shops add evidence too: a pre-order whose title carries a set code no set
  of that game has (OP-18, EB-05, FB11, VGE-DZ-BT16, SV9, ME03, Set 6, each
  counted only for its own game, and never "Gift Set 2" or "Starter Set 3"), and
  any "Pre-Release Event" ticket, waits under "Announced sets to check"
  with the shop's title. A ticket is never a product.
- A date you type on a set in admin is yours: no source changes it. A
  source may move a date it set itself, and a publisher may replace a
  community source's date.
- When sources are more than a day apart on a set, "Release dates to
  confirm" lists each source's date with Use this date. A date the set had
  before any source was read (typed in admin or imported) is one of the
  dates too, with Keep this date, so a publisher that disagrees with it is
  shown rather than ignored. Your choice is final. A button acts only if the
  date it showed is still the date, so a source moving it in the meantime
  saves nothing.
- A source with no good read for 14 days is listed under "Release sources
  not answering". Insights has a "Release sources" table with each
  source's last read, sets found and last error.
- No product is ever created from an announced set, and a game you have
  not added is skipped.

Products are filed under their set by the set's code (a whole word of the
product name, with letters and digits, such as OP-18) or, failing that, by
the longest set name whose words all appear in the product name. A name
needs two words that are not filler: series names such as Scarlet &
Violet or Mega Evolution never file anything alone. A product whose name
names a language (Japanese, Korean, Chinese and so on) is filed only under a
set whose name names the same language: those editions share the English
set's code but come out on other dates, and every release source is an
English one. This runs when a set is added, when an import creates a
product, and in `tidy_all`.

`RIPRAPTOR_RELEASES=0` stops the background reader reading these sources.

```sh
python manage.py scan_releases                         # every source that is due
python manage.py scan_releases --source scryfall_sets  # one source, when it is due
python manage.py scan_releases --dry-run               # what the due sources say; writes nothing but the pages it used
```

The parsers are tested against trimmed copies of each source's real
answer in `catalogue/fixtures/releases/`. pokemon.com refused requests
from the machine that saved them, so its three files are rebuilt from what
was read there by hand and say so at the top.

## Backups, timeouts and runs cut short

`python manage.py backup_db` copies the database with SQLite's own backup,
which reads a consistent snapshot while the site and the cron jobs keep
writing. The copy is named after the database and the time, for example
`db.sqlite3.20261008-0040.bak`, and goes in `backups/` beside the database
unless you pass `--dir`. Only the newest `--keep` copies (5 by default) are
kept; other files in the folder are left alone, so the older
`db-YYYYMMDD-HHMMSS.sqlite3` copies from earlier installs stay until you
delete them. `install.sh` runs `backup_db --keep 5` before every update.

`snapshot_daily_prices` runs a checkpoint after the nightly snapshot: it
folds the `-wal` file back into the database and empties it, so the file
cannot keep growing. If another process is busy at that moment it says so
in the log and tries again the next night.

Every cron line runs its command under `timeout`, set to the command's
budget plus five minutes: the hourly import 55 minutes, `tidy_all` 25,
the stock watcher and the stock alerts 9, `check_worker` 10, the nightly
snapshot and backup 30 each, the weekly delivery check 50 and the monthly
GeoIP download 30. Without it a
command that hangs while holding a lock would make every later run behind
the lock give up without a word. A command still running after its limit is
stopped, and killed a minute later (30 seconds for the ten-minute jobs) if
it has not stopped. `deploy/install.sh` and `deploy/crontab` carry the same
lines and both load `.env` first; a test fails when they differ.

The nightly snapshot and the weekly delivery check wait for the import
lock for as long as it takes (`tidy_all` for up to 30 minutes), and their timeout starts only once they hold
it (`flock ... timeout ...`). A full import can still be running at 00:15,
and a wait counted against the snapshot's 30 minutes would stop it before
it started, so that night's price history and checkpoint would be lost.
The hourly import keeps `timeout` outside its 30 minute lock wait, so
the whole line, wait included, ends before the next hour's import starts.

A price import that is stopped part way (a timeout, a deploy, a restart)
cannot record that it ended, so admin would show it as running for ever.
Each `import_prices` run first closes every run that started more than
three hours ago and never finished, with the error "Stopped before it
finished." The prices it saved before it stopped are kept. Because it has
an error, such a run never counts as the day's eBay or Amazon read, so
the next hourly import reads them again. Three hours is longer than the
longest real read (Amazon, about 37 minutes).

## Shared cache

The home lists, the deals page, the newest drops and the feeds are kept in
a cache for `RIPRAPTOR_HOME_CACHE_SECONDS` (five minutes) because they read
every priced product. The cache is a folder of files, `RIPRAPTOR_CACHE_DIR`,
shared by the web app's workers and every command, so when an import or the
stock watcher finds a restock or a new pre-order, the site shows it on the
next page load rather than up to five minutes later. On a server the folder
is `/var/lib/ripraptor/cache` (`install.sh` creates it, owned by
`ripraptor`); on your own computer it is `.cache` beside the code. Deleting
the cache files (`*.djcache`) is always safe. The folder also holds
`crawl-paused` while Pause all is on: deleting it resumes reading. The folder outlives a restart, so `install.sh`
empties it once the site has restarted on the new code: lists saved by the
old code can lack a field a migration added.

If the folder cannot be created or written, the site still runs: it logs
"Cache folder ... cannot be used" and each process keeps its own cache, as
before, so changes from imports take up to five minutes to show. Fix the
folder's owner and restart the site.

Every saved listing or product clears the cached lists, but a process that
cleared them less than 30 seconds ago skips the next clear, so a shop read
that changes a few dozen prices clears a handful of times rather than once
per price. A skipped clear makes the cached lists expire when those 30
seconds are up instead, so no change stays hidden for longer. The end of
every import, a restock found by the stock watcher, `tidy_listings` hiding
eBay prices, a delivery rule change and the fixes on Things to check always
clear at once. A clear waits until the change is committed, so the other
worker cannot refill the lists from the rows as they were. The tests keep a
memory cache of their own, so they never read or clear your development
site's cache.

## Settings

| Environment variable           | Default | Notes |
|--------------------------------|---------|-------|
| `DJANGO_DEBUG`                 | on      | Set to `0` in production. |
| `DJANGO_SECRET_KEY`            |         | Required when debug is off. |
| `DJANGO_ALLOWED_HOSTS`         | `localhost,127.0.0.1` | Comma separated. |
| `DJANGO_CSRF_TRUSTED_ORIGINS`  |         | Comma separated, with scheme. |
| `DJANGO_SQLITE_PATH`           | `db.sqlite3` | |
| `RIPRAPTOR_CONTACT_EMAIL`      |         | Shows the "Report a problem" section on the how it works page. |
| `RIPRAPTOR_STALE_AFTER_HOURS`  | 72      | |
| `RIPRAPTOR_USE_FEED_IMAGES`    | on      | Keep retailer images for products without one. |
| `RIPRAPTOR_HOME_CACHE_SECONDS` | 300     | How long trending and savings are kept. Cleared by imports and edits. |
| `RIPRAPTOR_CACHE_DIR`          | `/var/lib/ripraptor/cache` (`.cache` with `DJANGO_DEBUG` on) | The cache folder the site and every command share. Must be writable by the user the site runs as. |
| `RIPRAPTOR_RESTOCK_HOURS`      | 48      | How long a restocked product stays under "Back in stock". |
| `RIPRAPTOR_WORKER_THREADS`     | 3       | Jobs the background reader runs at once. 2 suits a server with under 1 GB of memory, and `deploy/install.sh` writes 2 on such a server when the setting is missing. Restart `ripraptor-worker` after changing it. |
| `RIPRAPTOR_AMAZON_ACCESS_KEY`  |         | Product Advertising API key from Amazon Associates. |
| `RIPRAPTOR_AMAZON_SECRET_KEY`  |         | Its secret. |
| `RIPRAPTOR_AMAZON_PARTNER_TAG` |         | The Associates tracking tag, for example `ripraptor-21`. All three set means Amazon is read once a day. |
| `RIPRAPTOR_AMAZON_DAILY_LIMIT` | 2000    | New products looked up on Amazon per day. |
| `RIPRAPTOR_EBAY_APP_ID`        |         | Production App ID (Client ID) from developer.ebay.com. |
| `RIPRAPTOR_EBAY_CERT_ID`       |         | Its Cert ID (Client Secret). |
| `RIPRAPTOR_EBAY_CAMPAIGN_ID`   |         | eBay Partner Network campaign id. All three set means eBay is read once a day. |
| `RIPRAPTOR_EBAY_DAILY_LIMIT`   | 4000    | Products checked on eBay per day. |
| `RIPRAPTOR_GEOIP_DB`           |         | Path of the DB-IP country database for visitor countries. Empty means countries are not recorded. |
| `RIPRAPTOR_AWIN_PUBLISHER_ID`  | 3111686 | Awin publisher id; loads Awin's MasterTag on every public page. Empty means no Awin script. |
| `RIPRAPTOR_ZEPTOMAIL_TOKEN`    |         | Send Mail token from a Zoho ZeptoMail Mail Agent. With `RIPRAPTOR_MAIL_FROM` set, turns on back-in-stock emails. |
| `RIPRAPTOR_ZEPTOMAIL_URL`      | `https://cpaas.zoho.com/v1.1/email` | The API address shown on the Mail Agent's API page (Zoho CPaaS, formerly ZeptoMail). |
| `RIPRAPTOR_MAIL_FROM`          | `alerts@ripraptor.com` | The sending address, on a domain verified in ZeptoMail. |
| `RIPRAPTOR_SITE_URL`           | `https://ripraptor.com` | Used for links in emails. |
| `RIPRAPTOR_PREORDER_ALERTS_FROM` | written by install.sh | ISO date or date and time (UK time without a zone). Alerts asked for from then on are also emailed when a shop opens pre-orders. `deploy/install.sh` adds it with the install moment when it is missing and never moves it. Empty means no pre-order emails. A value that does not parse stops the site starting. |
| `RIPRAPTOR_INBOX_NOTIFY_EMAIL` |         | Your own address. Each new Message us message is emailed to it through ZeptoMail with a link to reply in admin, and so are crawl problems (see below). Never shown on the site. |
| `RIPRAPTOR_NTFY_TOPIC`         |         | A long, unguessable ntfy topic name. Each new message, and each crawl problem, sends a push to the free ntfy phone app subscribed to it. The push holds only a title and a link to admin, never a name, the words, a shop, a product or a price. |
| `RIPRAPTOR_NTFY_URL`           | `https://ntfy.sh` | The ntfy server, if you run your own. |
| `RIPRAPTOR_AUTO_CATALOGUE`     | on      | Shop reads add sealed products they find that the catalogue does not have yet. `RIPRAPTOR_AUTO_CATALOGUE=0` adds none: a close match waits under Shop products to review and the rest are listed as unmatched on the run. |
| `RIPRAPTOR_FINDER`             | on      | The background reader looks for other shops selling products that one shop sells, or none (see "Stockist finder"). `RIPRAPTOR_FINDER=0` turns it off; `find_stockists` still runs by hand. |
| `RIPRAPTOR_RELEASES`           | on      | The background reader reads free publisher and community sources for announced sets and release dates (see "Release radar"). `RIPRAPTOR_RELEASES=0` turns it off; `scan_releases` still runs by hand. |
| `RIPRAPTOR_AUTOPILOT`          | on      | Every hour the site answers the Things to check rows its evidence settles, each listed with Undo (see "The autopilot"). `RIPRAPTOR_AUTOPILOT=0` stops the hourly run; Sort what you can now still runs it. |
| `RIPRAPTOR_CLAUDE`             | on      | The Claude judge may run (see "The Claude judge"). It still sends nothing until a key is saved and it is switched on in Things to check. `RIPRAPTOR_CLAUDE=0` stops it whatever the page says. |
| `RIPRAPTOR_CLAUDE_API_KEY`     |         | An Anthropic API key. Leave empty and save the key from Things to check instead; set here, it wins over the saved one. |
| `RIPRAPTOR_CLAUDE_KEY_FILE`    | `claude-key` beside the database | Where a key saved from the page is kept, readable only by the site's user. Never in the database or its backups: save the key again after restoring onto a new server. |
| `RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD` | `25` | The highest monthly limit the page accepts, in US dollars. |
| `RIPRAPTOR_CRAWL_PUSHES`       | on      | Tell you about crawl problems by push and email: the background reader stopped, a shop has failed every read for a day, or more than 10 doubtful prices are waiting as the cheapest price of their product. Each at most once a day. `RIPRAPTOR_CRAWL_PUSHES=0` turns them off; new messages are still sent. |
| `RIPRAPTOR_ADSENSE_CLIENT`     |         | Google AdSense publisher id (`ca-pub-...`). Empty means no adverts and no Google script. Pages marked noindex never carry it. |

## Games

The games the classifier recognises are listed in `catalogue/classify.py`
(`GAMES`): Pokémon, Magic, One Piece, Lorcana, Yu-Gi-Oh, Star Wars
Unlimited, Flesh and Blood, Digimon, Dragon Ball Super, Riftbound,
Cardfight!! Vanguard, Weiss Schwarz and football cards. A game is created
in the database the first time a shop product of it is imported, so adding
one means adding its words there, its filter labels in
`catalogue/types.py`, its eBay search word in `catalogue/ebay.py` and its
prefix to `GAME_PREFIX` in `catalogue/matching.py`. Pick games several
shops stock: one shop's price is a listing, not a comparison.

## Product types per game

`catalogue/types.py` lists, for each game, which product types it has and
what its players call them: Play booster box and Commander deck for
Magic, Structure deck for Yu-Gi-Oh, Illumineer's Trove for Lorcana, Hobby
or retail box for football, and so on. The type filter on game, set,
search and swipe pages offers only the types that game's catalogue holds,
in the game's words, and product pages use the same words. Magic's
collector boosters are their own types, and `tidy_catalogue` retypes any
that were filed as play boosters. Browsing also filters by price band and
by products compared at two or more shops.

## Latest drops

`/new/` lists sealed products released in the last 45 days, on pre-order,
or first listed by a shop in the last 14 days, newest release first, with
the usual filters. The home page carries a Latest drops row of the same
list, cached with the other home lists.

### Coming soon

Announced sets still to come (from the release radar, a CSV import or
admin) show as plain rows, soonest first, with no new address:

- `/new/` lists every one above the products, each with its date when one
  is published and the cheapest pre-order and how many shops take them,
  or "In stock at a shop already" when a shop sells it before its date
  and none takes pre-orders, or else "No pre-orders yet".
- The home page shows up to 3 under Latest drops, and only sets a shop
  already takes pre-orders for.
- A game page lists its own; a set page prints "Out <date>" and which
  release source gave the date (nothing for a date you set), or "Date not
  announced yet" for an undated set shops already take pre-orders for.

A set counts when its date is today or later, or when it has no date but a
shop (never eBay or Amazon) has one of its products on pre-order, checked
within the stale window, and no shop already sells one from stock (an
undated set a shop sells is out, not coming). The price links to the product page, so a buy
click still goes through `/go/` and is counted. The list is one query
(`web/views.py` `coming_soon_sets`), cached under `web:coming-soon:v1` and
`web:coming-soon:v1:<game>`, and cleared with the other lists, including
whenever a set is saved. The wording is under "Coming soon sets" in
Site wording.

## Deals page

`/deals/` is the page to link from social posts: the biggest savings
between the cheapest shop and the next, the biggest price drops this
week and what just came back in stock, server rendered and in the
sitemap. Its lists are cached like the home page's and cleared by
imports.

## Restock record

Every time a shop goes from not having a product to having it in stock,
`pricing.record_check` keeps a `Restock` row (product, shop, time,
delivered price). A listing that flips out and back within two hours
counts once, and eBay and Amazon are left out because their stock is many
sellers rather than one shop restocking. The deals page shows the last
seven days as a log by day, marking restocks whose shop has since sold out
again, and each product page says how often it has come back in the last
30 days and where, or when it last did. Once a product has six or more
restocks in 90 days the line adds the two-hour window most of them landed
in. `backfill_restocks` turns the stamps listings already carried into
rows; the installer runs it, and it is safe to run again.

## Featured deals and sold-out pages

The home page opens with "Featured deals": up to eight in-stock products
whose cheapest price is a real saving on the next shop or the week's
lowest. Six of the eight places go to deals whose cheapest shop pays a
commission (an affiliate link format, eBay or Amazon), the best of the
rest take the other two, and either side fills in when the other is
short. The footer line on every page says featured deals favour shops
that pay, and the Terms page explains how they are chosen. Product
pages and their price order are unchanged.

When no shop has a product in stock, the price box leads with "Compare on
Amazon" and "Search eBay": a tagged Amazon search and an eBay UK search
for new, buy it now listings carrying the eBay Partner Network campaign.
The eBay search is left out when an eBay price is already in the list.

## Back in stock emails

On a product no shop has in stock (eBay and Amazon do not count), the
price box offers "Email me when it is back in stock". The form works
without JavaScript. The address is stored with the product and a
confirmation email goes out through Zoho ZeptoMail; nothing else is sent
until the visitor presses the link in it. `send_stock_alerts` runs every
ten minutes after `watch_stock`: for each confirmed alert whose product a
shop now has in stock, it sends one email naming the cheapest shop and
linking to the product page, then deletes the address. Unconfirmed
requests are deleted after a week, confirmed ones after six months, and
every email has a link that deletes the request at once. One address can
wait on 30 products. The Terms page says all of this. Insights counts
requests, confirmations, emails sent and stops.

Setting up ZeptoMail: add and verify the domain (the DNS records it gives),
create a Mail Agent, copy its Send Mail token into `.env` as
`RIPRAPTOR_ZEPTOMAIL_TOKEN` (the address and sender have working defaults), restart, and check with
`python manage.py send_stock_alerts --test you@example.com`. Without the
token the form does not appear.

Pre-orders too: an alert asked for on or after `RIPRAPTOR_PREORDER_ALERTS_FROM`
is also sent, once, when a shop opens pre-orders and no shop has the product
in stock ("Pre-orders open: <product>, £x at <shop>"), and is then deleted
like any other. Alerts asked for before then were promised back in stock
only and hear only that. While the setting is on, the form reads "Tell me
when a shop has it or opens pre-orders" and hides once a shop (not eBay or
Amazon) has the product in stock or on pre-order, since the email would go
at once. `deploy/install.sh` writes the moment it first runs this code into
`.env`, so nothing needs doing by hand; without the setting no pre-order
email is sent and the form and emails read as before.

## Message us

`/contact/` (footer and How it works page) takes a message, an optional
name and an optional email address. No account and no inbox: the visitor
lands on a private page at `/contact/c/<token>/` that shows the thread,
takes follow-ups and is remembered by a cookie, so the Message us page
links back to it. You read and reply in admin under Messages ->
Conversations; the admin home shows how many are new. Opening a thread
marks it read, and "Open what they see" does not tie the thread to your
browser. Type in Your reply and save: the reply shows on their page and,
if they gave an address, is emailed to them through ZeptoMail with a link
back. The email's Unsubscribe opens their page with a button that deletes
the address (a button, because mail scanners open links). Tick Closed to
stop follow-ups.

You hear about new messages, and about crawl problems (see the background
reader section), by email to `RIPRAPTOR_INBOX_NOTIFY_EMAIL`, by phone push
through ntfy (install the ntfy app, subscribe to the topic in
`RIPRAPTOR_NTFY_TOPIC`), or both. Every new message is sent; crawl
problems at most once a day each. The push says only that a message
came in, with a link to admin, because anyone who guesses an ntfy.sh topic
can read it. A failed notification never loses the message. Limits: 3,000
characters a message, six follow-ups an hour per thread, 40 new threads an
hour site-wide. Threads are deleted six months after their last message.
Thread pages are noindex and kept out of robots.txt.

## Watchlist

Every product page and card has a Save link. With JavaScript it adds the
product to a list kept in the browser's localStorage (the same store the
swipe page uses, so swipe saves land there too), and the header link
becomes "Watchlist (3)" pointing at `/watchlist/?p=slug,slug`. That page
is rendered by the server from the slugs in the address, with each
product's live cheapest price, shop and check time, a Buy button and a
Remove link; the script adds "Saved at £X" in green when the price is
lower now and red when higher. Nothing is stored on the server, which is
why the address carries the list: it can be bookmarked or pasted into a
chat, and without JavaScript a Save link simply opens the watchlist page
for that one product. Old product addresses resolve through
`ProductAlias`. The page is `noindex` and `/watchlist/` is disallowed in
robots.txt. Buy clicks from it carry `?from=watchlist`, stored in
`OutboundClick.source`, and each product on a loaded list is counted as a
"watchlist row" page view by slug, so Insights shows clicks from
watchlists and the stock watcher can put watched products first.

## Recent searches and recently viewed

The browser remembers the last 8 searches and the last 12 product pages
it opened (`ripraptor.searches` and `ripraptor.viewed` in localStorage;
`web/static/js/history.js`). Tapping into an empty search box lists the
recent searches. The home page shows "Pick up where you left off" with
the searches as chips and the viewed products as a row with live prices,
fetched from `/api/recent/?p=slug,slug`, which renders the row for the
slugs it is given. The section stays hidden until there is something to
show, and "Clear history" forgets both. Nothing is sent to or stored on
the server, and nobody is sent back to where they were: the site opens
the same for everyone and the recent things are simply there.

## Add to home screen

`/manifest.webmanifest` is a web app manifest, built by a view so it
carries the site name and the hashed icon addresses, and every page links
it along with an Apple touch icon. A phone can then pin RipRaptor to its
home screen with its own icon and open it without browser chrome. There
is no service worker and no push: nothing runs in the background and
nothing is stored.

On a phone, after the third page in that browser, a small card offers to
add the site to the home screen (`web/static/js/install.js`). Android
Chrome supplies its install prompt; iPhone Safari has none, so there the
card explains Share, then Add to Home Screen. Answered either way, or
once the site is installed, it never shows again in that browser. Four
events are counted with no record of who, through `/api/note/`: shown,
added, dismissed, and opened from the icon (once per browser per day).
Insights shows them under "Home screen".

## Feeds

`/feeds/deals.xml` is an RSS feed of restocks, pre-order openings and price drops across the
site, and `/feeds/<game>.xml` (for example `/feeds/pokemon.xml`) the same
for one game. Every page announces them in its head, and the deals page
prints the address, so a feed reader or a Discord feed bot can follow
them. Restocks come from the Restock rows with the shop and the delivered
price. Pre-order openings come from the PreorderOpen rows of the last
seven days ("Pre-orders open: <product>, £x at <shop>", GUID
`preorder-<id>`), marketplaces left out. Price drops come from the daily
lowest prices, which hold a date and no time, so a drop is dated to its
day and a product appears once.
Entries link to the product page. Feeds are built with Django's own
syndication module and cached like the home lists, so a restock recorded
by a check appears on the next request.

## Merged products keep their addresses

`merge_duplicates` (run hourly by `tidy_all`) records every merged
product's old address, and a visit to an old address is sent to the
kept product with a permanent redirect, so links, search results and
watchlists saved in browsers keep working. Old addresses are listed in
admin under "Old product addresses". Price history, restocks, clicks and
stock alerts move to the kept product. A count with and without its noun
("Display (10 Bundles)" and "Display (10)") counts as the same name; a name
with nothing but the kind of product left ("Scarlet & Violet Elite Trainer
Box") is never merged.

```sh
python manage.py merge_duplicates --loose --dry-run
```

also ignores filler words ("Exclusive", "English", "TCG") and, for
Pokémon, series names, so "Pitch Black Pokémon Center Elite Trainer Box
(Exclusive)" meets "Mega Evolution Pitch Black Pokemon Center Elite Trainer
Box". It never runs on its own: read the dry run, and run it without
`--dry-run` only when every group is right.

## Search engines and AI crawlers

Every page carries a canonical link, Open Graph and Twitter sharing tags,
and structured data: WebSite with site search and Organization on the
home page, Product with its offers plus a BreadcrumbList on product
pages, and BreadcrumbList on game and set pages. Product titles and
descriptions lead with the cheapest delivered price. `/robots.txt`
names the AI crawlers and allows them the same pages as everyone else,
and `/llms.txt` describes the site in plain Markdown with links to the
games, the how it works page and the sitemap. Search, swipe and the
click redirect stay out of the index.

## Insights

Admin, Insights (`/admin/insights/`) shows what visitors do: page views
and clicks to shops by day, the most viewed products with their clicks,
clicks by shop, game, product type and hour, top searches, and searches
that found nothing. Views and searches are counted by day with no record
of who made them; clicks were already counted the same way. Visitors are
counted once a day each through a one-way token made from their address
and browser with a secret that changes daily, so nobody can be traced
and yesterday's visitor is a stranger today. Only the visitor's country
is kept, looked up in the free DB-IP country database that `fetch_geoip`
downloads (the installer fetches it and cron refreshes it monthly). Each visitor also
records their device type and the domain of the site that sent them, so
the page shows where visitors come from (search engines, AI assistants,
Reddit, Facebook, Discord and so on) and what they browse on. Bots
are left out, including scripts that say what they are (Python, curl,
headless Chrome, Scrapy and the like). A visitor who opens more than 150
pages in a day is taken for a script dressed as a browser: their pages
past 150 are not counted, and Insights says how many such visitors there
were. Watchlist rows and home screen events are not page views. The
window is 7, 30, 90 or 365 days.

Returning visitors are counted with one cookie, `rr_back`, set the first
time a browser views a page. It holds only "1", for a year, so a visit on
a later day is marked as a return; the daily token cannot do this because
it changes every day. Nothing identifies the person, and the Terms page
says so. The page shows the returning share and a Returning column by day.

The page opens with "Areas to improve": a ranked list worked out from the
figures, such as shops whose imports are failing, the share of clicks
going to shops with no affiliate link, searches that found nothing,
popular products with only one shop, products viewed but never clicked,
low search engine traffic and products with no picture. Below it are
shop health (last good read, stock, whether clicks can earn, latest
error), clicks that earn nothing, and the catalogue's comparison
coverage.

## Tests

```sh
python manage.py test
```

Besides behaviour, the tests render every public page and fail on em dashes,
exclamation marks and stock marketing phrases.
A test run blanks `RIPRAPTOR_NTFY_TOPIC`, `RIPRAPTOR_INBOX_NOTIFY_EMAIL`
and `RIPRAPTOR_ZEPTOMAIL_TOKEN`, so running the tests from a shell that
has loaded the server's `.env` never sends a push or an email.
