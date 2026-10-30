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
`image_url`. Loading the same file again updates the details. A game the
classifier knows is created if no shop has brought one of its products in
yet. A product given a release date in the file is kept while no shop lists
it, so eBay, Amazon and the stockist finder can look for it: `tidy_catalogue`
only removes products that shops brought in. `catalogue/data/union_arena.csv`
holds the Union Arena products a visitor asked for; the installer loads it
on every update, so a product added to that file reaches the site with the
next update.

`scripts/browser_sweep.py` drives every public page in a real browser and
reports script errors, horizontal overflow, unlabelled controls and tap
targets under 24px. It needs `pip install playwright` and a running dev
server.

A Shopify shop that sells far more than cards (Zatu: board games, puzzles,
books, about 660,000 product pages) can be read from some of its
collections instead of the whole store: set "Shopify collections" on the
retailer to their handles, separated by spaces, and the import reads
`/collections/<handle>/products.json` for each, a product in two of them
once. `setup_shops` sets Zatu's to `trading-card-games
top-5000-trading-card-games football`: a check of all of Zatu on 13 Oct 2026
found 49 buyable card products outside `trading-card-games` alone, among
them the Topps football tins, and none worth having outside these three.

A shop that sells one game and leaves it out of its product names (Clifford
TCG, a Pokémon shop: "Surging Sparks Booster Box", with no game in the
name, type, vendor or tags either) has "game when the name does not say"
set on the retailer. A product of that shop whose name, type and tags name
no game is read as that game's; one that names a game keeps it (Clifford's
few Lorcana boxes). Leave it blank for a shop that sells several games:
there a name with no game is left unmatched, as before. `setup_shops` sets
Clifford TCG's to Pokémon, and reads only its four sealed collections
(`booster-boxes booster-blister-packs etbs special-collection-boxes`): they
held all 188 of its sealed products on 27 Oct 2026, beside about 2,700
single and graded cards. A product type of "Single" is never sealed, as
"Single Card" and "Singles" were not, and a "Single Pack Blister" (one
booster pack in a blister) is.

A shop's feed is read page by page until an empty page. Some shops answer
a request past their last page with an error instead, so a later page
failing ends the read too; it is still not the whole shop, so the products
the read never reached keep their last state (a complete read would have
marked every one of them out of stock), and the run's log says which page
was lost and why.

So nothing new is missed, a shop read this way is also read from its
newest products (the first two pages of `/products.json`, about a week of
new listings at Zatu). Any of them that are our kind of product but sit
outside the collections are kept under "Products read one by one" on the
retailer and read from `/products/<handle>.js` on every read from then on.
One the shop deletes, or that later joins a collection, drops off by itself;
a busy answer keeps it for the next read. At most 300 are kept.

Insights shows how much this covers: "Shops read from part of their site"
lists the collections, how many products are listed and buyable, and how
many are read one by one. "Look sealed, left out" lists items any shop
sells that look like a sealed product of a game we list but were not
listed, from each shop's latest good read: a kind the rules do not know
yet, or one ours by name but turned away by the shop's category, its price
or the matching. What the rules leave out on purpose (cases, sleeves,
singles, figures) is not listed. It is counted by the hourly `tidy_all`,
one shop's list at a time, and the page shows the last count and when it
was made: counting while the page loaded took the one-processor server past
gunicorn's 60 seconds and out of memory. "Products where a paying shop is cheapest"
gives the share of buyable products whose cheapest shop pays a commission,
and the shops that are cheapest most often without paying. Both lead
"What to do next" when they matter.

The rules take a booster pack from 50p when its title plainly says booster
pack (Japanese and Korean packs sell under £2), a "[Pre Order]" or
"(Pre-Order)" tag before the name, and MTG Scene Boxes and Draft Nights,
holiday calendars, tuck boxes, Pokemon Center special boxes, Topps hanger
and full boxes, and "Starter Decks". A Pokemon promo number ("SVP 066") or a
plain "Holo" marks a single card. One Piece Illustration Boxes stay out: their
names are only a code, which names lose, so two would merge.

Since Impact League TCG joined (15 Oct 2026): a price of nothing but nines
(£999, £999.99, £9,999) means not for sale and the offer is left out;
second-hand stock ("trade in", a bracketed "(TRADE)" or "(TRADE/IMPORT)"
tag, "pre-owned", "second hand") is left out; and "NEW SHOP STOCK" before a
name and "(Margin)" after it are dropped. Impact League TCG sells about
10,000 products, mostly graded and single cards, so only its sealed
collections are read (`COLLECTIONS` in `setup_shops`), with its newest
products; delivery is £5.99 (basket check), with no free threshold published.

Shop titles are matched with the game prefix taken off and the separators
collapsed, so "Disney Lorcana: Shimmering Skies - Booster Box" is judged
on "Shimmering Skies Booster Box" and counts as a full match for our
product of that name. The game itself still comes from the whole title.

A feed retailer accepts a Google Shopping XML feed (RSS or Atom with the
`g:` namespace) as well as CSV: barcode (`g:gtin`) first, title second,
with `g:sale_price` over `g:price`, `g:availability` and `g:shipping`.

## Your data in Excel

The whole catalogue can be downloaded as one Excel workbook, checked and
tidied in Excel, and uploaded again to make the changes. It is done from
**Your data in Excel** in admin (`/admin/data/`, a box on the admin home),
which only a superuser can open, and which works with JavaScript off:

1. **Make a spreadsheet.** The file is made in the background and the page
   refreshes itself until **Download spreadsheet** appears, usually within
   a minute. Pick a shop first to add the items its latest read left out.
2. **Edit it in Excel.** Change only the orange columns and save it as
   Excel Workbook (.xlsx).
3. **Check this file.** Upload it. Nothing changes yet: the page lists every
   change it would make in plain words, with how many there are of each.
4. **Make the changes.** One tap makes them; a second button also makes the
   ones held for your say. The page shows how far it has got.
5. **Undo this file** takes them all back if something looks wrong.

### What is in the file

A Read me with the file's number and how to edit it, then:

| Sheet | Rows | Orange (editable) columns |
|---|---|---|
| Products | every product, except ones merged away | Name, Type, Set, Language, Barcode, Release date, Show on site, Same as product id, Not the same as |
| Listings | every shop's page, hidden ones too | Product id, Show on site, Not this product |
| Shop items | items waiting for a decision, ones marked not ours, and linked items not shown, at shops switched on | Link to product id, Decision |
| Left out | one shop's items its latest read left out, when a shop was picked | Link to product id, Decision |
| Sets | every set | Name, Code, Release date |

Grey columns (prices, stock, shop titles, the start of each shop's
description, links, counts, Look-alike group, Kept apart from) come from the
shops or the site and are ignored on upload.
Look-alike group numbers products that may be one product on two pages (the
loose duplicate key, a Chinese set code or a shared barcode, whatever their
types). Products merged away are left out, and their listings say "Merged
into" the product kept.

Nothing personal or secret goes in: the sheets are built from an allowlist
of catalogue columns, never from alerts, messages, visitors, searches,
clicks, users, the admin log, Claude's answers, the import logs, or a
retailer's affiliate format, source address or session address. eBay and
Amazon links lose their query string. Every text is written as text, so a
shop title starting with = is never a formula, and barcodes stay text, so
Excel cannot turn them into 5.05E+12.

### What each edit does

- **Name, Type, Set, Language, Barcode, Release date** change the product,
  through the same form as Django Admin, with an admin history line. A type
  set here is kept from the hourly retype. A language is added to the name
  ("(Japanese)"), as the site reads language from the name and set.
- **Name, Code, Release date** on Sets change the set. A new name or code
  can change the language of every product in the set at once, as the site
  reads language from the name and set: the preview says how many products
  change, and the change is not made when one of them would then be joined
  to its twin by the hourly merge.
- **Show on site** No hides a product, or one shop's price on Listings; Yes
  shows it again.
- **Same as product id**: put the id of the product to keep, and this row is
  merged into it. It is an undoable merge, listed under Sorted for you on
  Things to check with its own Undo, and its address redirects.
- **Not the same as**: one or more ids of look-alikes that differ, so they
  are never suggested as duplicates again.
- **new1, new2** and so on in Product id (or Set id) add a row, with a game,
  a name, a type and a release date. Other rows and sheets can use new1 as a
  product id.
- **Product id** on Listings moves a shop's page to another product. **Not
  this product** Yes takes it off and marks the item not ours, so the next
  read never matches it by name again.
- **Decision** on Shop items and Left out: Link (to the suggested product,
  or the id typed in Link to product id), Not ours, or (Shop items only)
  Look again. A link keeps the price the shop showed and when it was seen,
  so an old price stays hidden until the shop's next read checks it, and an
  item with no price waits for that read. No price is made up.

### Held, not made, already so

Each cell is compared three ways: with the download, with the file and with
the site now. Only a cell that was edited counts, and a cell the site
changed since the download is left as the site has it ("Changed on the site
since your download"). Sorting whole rows, deleting rows, moving columns and
adding columns change nothing, and nor does Find and Replace changing a grey
column; an Address or Link that is another row's means the ids were moved.

A change is **not made** when the hourly tidy-up would undo it, a shop's
next read would put it back, or Excel mixed the file up: a name that would
merge with another product, a barcode already on another product, a page
moved to a product that already has that shop, a set cleared when the name
names one, a formula, an id twice, an id not in the download, ids sorted on
their own, or a Name or Barcode column sorted on its own. A change is
**held for your say** when it is probably right but worth a look: a merge
where a shop sells both on separate pages, or the barcodes differ, or you
said before they differ; a page move, link, rename or merge that leaves a
shop's address contradicting its product's name, which the hourly tidy
would delete (an address saying Japanese on a name that no longer does);
hiding a product, clearing its date, or taking off or moving its last
shop page, when the hourly tidy would then remove it (a link to an item
with no price makes no listing until the shop's next read, so it does not
count); or a column with 50 or more
edits on more than a quarter of its rows, in case a sort mixed them up.
Every change is judged against the site as it will be once the whole file
is made, so the order of rows never changes a verdict. The first button
makes the ready changes without the held ones, so a change that only works
once a held one is made (a barcode a held merge frees, or a link that lands
on the product kept by a held merge) is held with it.

A list of changes is made only from the page it was read on: Check again
reads the file against the site as it is now and gives a new list, and a
list over a day old must be checked again, as must a list checked before
another upload of the same download made or undid changes (so a new1 both
add is added once). The same file uploaded twice
goes to where the first got to, so it is never made twice; once discarded
or undone it can be uploaded again, and so can a file made without its
held changes, to make them (what was made then reads as already so). Once
a file is made, each section's heading says what came of its changes
("Ready (12): 11 made, 1 left as it was"). An upload must be an .xlsx of 10 MB or
less with no macros. Each change is made in its own short transaction
through `catalogue/owner_edits.py`, the code paths the owner's own buttons
use, and only while the site still holds what it held at the check: one
changed since is left as it was and listed with the reason. A file waits
while a shop read holds the import lock (downloads and checks, which need
no lock, are made meanwhile), and a copy of the database is made
first when it merges, takes pages off or makes more than 200 changes,
unless the newest copy is under six hours old.

**Undo this file** takes the changes back newest first, each only while the
site still holds what the file left; one changed again since is left as it
is and listed. Once what stopped it is put right (a later merge undone
first, say), Undo this file can be tapped again to try those once more.

### How long files are kept

A file can be uploaded for 90 days after it was made (not downloaded: a
file made on 1 March and downloaded on 14 March is refused from 30 May);
after that the page asks for a fresh download. `tidy_all` clears old files every hour: a
download's workbook after 14 days, the values it was made with after 90
days, an upload's file once its changes are made or it is discarded (or
after a week), and its list of changes after 180 days, when the file can no
longer be undone (its merges can still be undone on Things to check).

### The command

```sh
python manage.py data_files                         # run what the page queued
python manage.py data_files --export data.xlsx      # write the workbook to a file
python manage.py data_files --export data.xlsx --left-out zatu-games
python manage.py data_files --check edited.xlsx     # print what it would change
```

With no arguments it runs whatever the page queued (downloads to make,
uploads to check, files to make or undo). The page starts it at once in its
own process and starts it again when a job waits 30 seconds with nobody
taking it, or its heartbeat stops, unless a runner is going already. A lock
file in the cache folder keeps a second copy from running. It runs at a lower priority than the site and is
first in line for the memory killer (score 1000, ahead of the shop reader's
500), so visitors and fresh prices come first on the 1 GB server. A runner that is stopped is taken up again where it stopped,
without making any change twice; after three stops the file is given up,
with what to tap next (for a file part made, upload it again to make the
rest, or undo it).
A database another process holds for longer than SQLite waits is waited
for, about two and a half minutes, and then left to the runner's next
start; the change is not counted as failed, nor the wait as a stop.
An update (`deploy/install.sh`) waits for a file being made before it
touches the database or restarts the site, which would stop the runner
with it. If a runner is stopped part way all the same (the memory killer,
a crash), the hourly `tidy_all` leaves the products alone for that hour,
since it could remove or join what the rest of the file counts on, and
starts the runner to finish the file, so it is finished within the hour
even with the page closed. Its log, with the details of any change that
could not be made, goes to the web app's log on the server
(`journalctl -u ripraptor`), or to `/var/log/ripraptor-import.log` when
the hourly tidy-up started it.
`--export` makes a file that can be uploaded on the page like one made
there. `--check` prints every change in plain words and changes nothing;
changes are made only from the page, after its preview. The code is
`catalogue/workbook.py` (the sheets, one spec for both directions),
`catalogue/workbook_plan.py` (the comparison and guards),
`catalogue/owner_edits.py`, `catalogue/data_jobs.py` and the views in
`web/views.py`.

`openpyxl` was added for this, the one new dependency: it writes and reads
real .xlsx files, which keep barcodes as text and dates as dates and hold
several sheets with dropdowns. A CSV cannot, and Excel damages barcodes in
one. It is loaded only by `data_files`, never by a web worker (a test checks
this). Making the file takes about 9 seconds and 140 MB on a copy of the
October catalogue (7,626 products, 13,220 listings), or 22 seconds and 210
MB with the left-out items of the shop with the longest list (capped at
5,000 rows), so it never runs in a web request. No new settings are needed.

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
python manage.py check_delivery --apply --unknown   # only shops whose standard charge is not known
```

reads each shop's UK delivery charges from the shop itself, two ways. Its
delivery page (found through the "Delivery" or "Shipping" link on the home
page, or the usual addresses) gives the free-delivery threshold and, when
written out, the standard charge. A page with one threshold for single
cards and another for sealed products gives the sealed one ("FREE Royal
Mail 48h Tracked delivery on single card orders over £20 and on sealed
product orders over £30": £30, Unicorn Cards); one only for single cards
never counts. For a Shopify shop the command also puts one cheap product
in a basket, priced in pounds for the UK (a shop that sells abroad
otherwise prices it in the server's currency), asks for its UK delivery
options, takes the cheapest posted one and empties the basket. The basket
stays under the page's threshold; one that comes back free is tried again
with a cheaper product, since it may be over a threshold no page gave, and
a product the basket refuses gives way to the next (up to five). A boxed
sealed product is priced before a booster pack: a lone pack often goes as
a letter (Hammerhead TCG, 24 Oct 2026: £2.00 for a pack, £3.75 for a
booster box), and the comparison is mostly of boxes. Nothing but sealed
products is tried, since a single card or an accessory goes cheaper still,
so a shop with no sealed product in stock keeps its charge unknown (Tier
Zero Games, 25 Oct 2026: £2.50 for a single card). A basket that comes
back free under the threshold the shop's own page gives is not believed
either: an option such as collection has slipped through (Pixel and Block,
free over £200, answered £0.00 for a £57.50 box). When the
page gave a threshold, a basket just over it is priced too, to confirm it
is free. Every figure saved carries its source and the date in the
retailer's delivery note, so it can be checked. A shop that gives nothing
readable is listed with its address for checking by hand. The server
checks every shop on Sundays at 3:30 and, at 4:30 every day, the shops
whose standard charge is still unknown. (For a few hours on 24 Oct 2026
every run would have stopped before it began: a method of the command
named `check` replaced Django's system checks, which the tests skip.
`tests_ops` now fails on any command method named like one of Django's.) Shops with an unknown charge go
first, one shop's fault is reported and never stops the rest, and no shop
is started after 40 minutes, so the cron's limit never cuts a run short.

Not every shop can be read this way. Magic Madhouse (BigCommerce) shows
its charge only at its own checkout, whose delivery options refuse
anything but its own pages, and Card Empire's basket refuses every
product; Monarch Cards (Wix) and Shiny Vault publish no charge. Apla
Cards (Wix) publishes only its free thresholds (free over £49), so its
smaller orders show "+ delivery". For those,
type the charge from the shop's checkout into the retailer's "standard
delivery" in admin: a charge you set is kept, and the daily check skips it.

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
until then set just the tag: every product page's price list then ends with
an Amazon row, "Check price and stock on Amazon", whose button opens a tagged
Amazon search, and the price box keeps its "Compare on Amazon" button. A
product with no prices at all still gets the list with that one row. Both go
once we have an Amazon price for the product, which then sits in the list
like any other shop. The hourly import reads Amazon once a day: every product already
found there is refreshed in batches of ten, then up to
`RIPRAPTOR_AMAZON_DAILY_LIMIT` products are looked up, by barcode where we
have one and otherwise by name. The products visitors want most go first
(clicks, views, watchlists, alerts and the owner's "Search other shops
now" tap; being new or on pre-order does not count here), then those never
tried, then those tried longest ago. A wanted product searched in the last
three days waits its normal turn, so the same one is not searched every
day ahead of products never tried. A result is kept only when it matches the
product asked about. Amazon's own tracked link is stored on each listing,
so the shop needs no affiliate link format. Amazon removes API
access from accounts with no sales in their first months; the import then
reports the error in admin until access is restored.

**Amazon Associates rules the site keeps.** While the tag is set, Amazon's
required statement ("As an Amazon Associate, RipRaptor earns from
qualifying purchases.") is on the Terms page, in the footer line of every
page and beside the Amazon buttons in the product price box. Amazon links go
straight to amazon.co.uk with the tag (no redirect), and the site sends its
address as the referrer. The footer links "Privacy" to the Your data part of
the Terms page, which says shops and their affiliate networks set their own
cookies. No Amazon picture is asked for or stored: Amazon forbids keeping
its images.

Before turning on Amazon prices (the API keys), three more rules apply.
Amazon's UK policies say a site with Amazon links "must not have price
tracking and/or price alerting functionality" unless Amazon agrees, and
RipRaptor has price history, lowest-in-90-days badges and stock alerts, so
get Amazon's agreement in writing first. Amazon prices may be shown only
when refreshed within 24 hours, with the time they were checked and
Amazon's disclaimer ("Product prices and availability are accurate as of
the date/time indicated and are subject to change...") beside them or a
Details link to it. The daily read and the 72 hour stale window do not
meet that yet.

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
then the rest, by barcode where we have one and otherwise by name. A run stops
starting lookups after 45 minutes (`RUN_SECONDS` in `catalogue/ebay.py`) and
finishes as a good read with what it found, inside the background reader's
60 minute limit for a marketplace and the hourly import's 55; the products
it did not reach go first the next day. Cut off by either limit, a run would
count as failed, be tried again and spend more of the day's searches. Of the
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

`start.bat` / `start.command` run `setup_shops`, which adds the UK shops
in its list (among them Total Cards, Gathering Games, Magic Madhouse, The Card Vault, Lvl Up
Gaming, Zatu Games, Goblin Gaming, Travelling Man, JET Cards, Titan Cards,
Buy Any Cards, The TCG Shop, Double Sleeved, The Gamers Lodge,
Kongs Cards, MaxOnCards, Japan2UK, Iconic Trading Cards, Card Empire,
Griffins Gaming, Tayler TCG, Shiny Vault, Monarch Cards, Castle Comics, 120HP,
Ancient Warrior, Unicorn Cards and Sports Cards Direct) with the delivery rules read from their delivery pages and baskets,
and `remove_demo`, which deletes the fictional demo retailers. Shops that
cannot be read automatically, and why, are listed under "Shops we cannot
read" below. A Shopify shop that prices in another currency is refused
(Poke-Collect turned out to be a US shop pricing in dollars, so it is
hidden). Then `update_prices` (double-click) or
`python manage.py import_prices` pulls every product from each shop.

To remove a shop, delete it in Admin > Retailers. Its prices, listings and
reading go with it, and Admin > Removed shops remembers it, so the
`setup_shops` that every update runs never adds it back. Adding the shop
again under Retailers (or with `discover_shops`), or deleting its row under
Removed shops, lets it back in. Packrat was put back by updates before
this existed, so `setup_shops` deletes it once (`REMOVED`).

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
A rename never changes a product's address. A tidied name that is already
another product's of the same game, type and language is merged into it
the way `merge_duplicates` merges, so the old address redirects there for
good and its price history, restocks, clicks and stock alerts move across.
A product of another type, or one switched off in admin, is never merged
into: the product is renamed in place instead. A removed product's address
answers 410 Gone, not 404, with the search box and links to its set and
game when it had them, so Google drops it quickly and a visitor is not
stranded (see "Sitemap and removed pages").

Only Total Cards publishes barcodes, so most offers are matched by name.
An offer links to an existing product automatically only when it is a
sealed product of the same game and its title and our name agree both
ways (every meaningful word of ours in the title, and most of the title's
own words in ours), or when both boil down to the same matching key (the
identifying words plus the kind of thing: box, pack, bundle, deck) in the
same language, with the same numbers, set codes and booster kind. Before a
read makes a new product it looks for one it already has under other
shops' words: the same key, or the same set code with the same box size and
kind when only one product carries that code ("Eeveelutions Terastal
Festival ex High Class sv8a Japanese Booster Box" joins "(JAPANESE) Pokemon
SV8a High Class Terastal Festival ex Booster Box", and Solent's "Chasing
Glory Together Slim Booster Box (csv10)" joins "Pursue Glory Together csv10
C Slim"). An unwrapped box, a case, another size or language never joins.
Anything less certain goes to Shop products to review. Shops mark
pre-orders in a pre-order collection, which the importer reads, so a
pre-order never shows as in stock.

Multi-buys look after themselves the same way. When a Shopify shop has a
collection named like "2 for £50" or "Any 3 for £100", every read marks
its products with that deal and clears it when the collection or the
product goes. The product page shows it under that shop's price ("2 for
£50.00 here, £25.00 each delivered"), and the cheapest-price box and the
product cards add "Or 2 for £50.00 at ..." when the deal costs less per
item than buying one at the cheapest shop. Delivery is worked out on the
whole multi-buy, so a deal that reaches free delivery counts as free. The
ranking stays on buying one. A deal that saves nothing, or costs under
half the single price per item, is ignored as a mistake. A read that
cannot see the shop's collections leaves the deal as it was. Nothing to
set up: the deal shows read-only on the listing in Django Admin.

Three cleanup commands keep the catalogue honest after the rules improve
(`tidy_all` runs them in order; `start.bat` and `update_prices` run it
after their import, and the server's cron runs it at five past every hour,
when it also forgets removed product addresses older than a year):
`tidy_catalogue` (products that no longer pass; their addresses answer 410
Gone, and a name tidied onto another product's merges with a redirect),
`merge_duplicates` (one product under two names) and `tidy_listings` (a shop item linked to the
wrong product: a shop's category page, a kept shop title that plainly names another product by the
rule every read uses (see "Wrong links" below), one shop link on several of our products where the
shop's title names exactly one of them in full, or the words in its shop address; a language word in
the address counts against the product only when it names another language
than the product's, so "...-simplified-chinese-gem-4-booster-box" stays on
"Chinese Gem Pack Vol 4 Booster Box"; it also hides an
eBay listing priced under 40% of the cheapest any shop last showed, in
stock or not, and removes one whose title is plainly another thing: a
multi-buy, a code, a sampling pack, another language, or single cards
(`classify.SINGLE_CARD`: a card number such as "133/165", a promo number,
a rarity, a grade or a card's condition; "2025 Holiday Calendar Eevee
133/165 Pikachu 025/165" was the calendar's cheapest price on 24 Oct 2026).
A removed listing leaves no row on the page, the next eBay search can find
the product itself, and the days its price set as the product's lowest go
from the price history. A shop's single card named after the product it
comes from ("My First Battle Deck: Pikachu Deck / Electabuzz" at £2.45,
Magic Madhouse, 24 Oct 2026) is caught from what the shop's own page says
(`importers.not_sealed_reason`): an offer marked used (schema.org
UsedCondition, DamagedCondition or RefurbishedCondition), a breadcrumb
under single cards ("Pokémon Singles", not "Sealed"), or a description
that opens with the item's own title and calls it "a ... card from" a
product. Such a page never becomes a listing; one linked before is removed
when its page is read again, and this command removes those whose stored
description says so. When no other shop lists the product, its eBay and
Amazon listings go too, and `tidy_catalogue` retires the page (410 Gone).
Listings keep the shop's own
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
anyway", the wrong matches with a Hide button under each price, the possible duplicates
(see "Possible duplicates" below) with a Merge these button for each group
(only the group exactly as shown is merged, the undoable kind, each pair
listed under Sorted for you with Undo) and "Not the same" beside each
product (the pair is never suggested again), the products the stockist finder may have
found at another shop with "Yes, link it" and "No, not this" (see
"Stockist finder" below), the ones visitors want most first, and the shops
whose delivery charge is not known with a link to fill it in.

Only rows that change what a visitor sees are listed. A doubtful price is
listed only while it is the product's cheapest buyable price: one that is
dearer than another shop, or out of stock, never shows as the cheapest and
is left as it is (the page says how many). A product whose second price is
doubtful is not a wrong match. Two products a shop shows side by side are
never suggested as duplicates. A set showing its publisher's date is not
listed because a community source says otherwise.

### Possible duplicates

Each pair is labelled with how sure the rules are, from the names alone:
- **Certain:** the same strict name, numbers and language. The hourly merge joins these itself.
- **Likely:** the same product words, with one detail (such as a word like "Exclusive") to check.
- **Possible:** shares only a code or set.
- **Different:** a number, language, edition or kind differs. Never merged.

The page lists them in those four sections, most certain first. Tick the pairs you have
checked and tap **Merge ticked**; each is merged on its own and listed under Sorted for you with Undo.
Claude's rating sits beside each pair. Labels come from the rules, not from Claude, so a
percentage is never taken as proof. `python manage.py certainty_sample` prints a random sample
of each level, for checking by hand before trusting a level.

Possible duplicates are pairs, each a product to keep and one to merge into
it, found by any key two names of one product share in one game and
language (`merge_duplicates.duplicate_pairs`):

- the same words once shop wording is set aside (the `--loose` key below),
  whatever type each is filed under, since shops file one product under two
  ("Booster Display" as an Elite Trainer Box, "Gift Set" and "Gift Box");
- one set code with the box size and kind of product ("Triple Beat sv1a
  Booster Box" and "Triplet Beat sv1a Japanese Booster Box");
- for Pokémon, one Simplified Chinese set code ("Pursue Glory Together" and
  "Chasing Glory Together csv10");
- for products filed under a set, the words that are not the set's ("SV10
  Booster Box" and "Destined Rivals Booster Box", both under Destined
  Rivals);
- the same barcode.

A pair found only by a set code or a set must also agree on numbers, set
codes, booster kinds, the words that mark another product ("Collector's
Edition", "Case"), size and wrapping ("No Shrink", "Half"). Each pair stands
alone: a product is never pulled in because it looks like a third. A pair
you said are not the same is left out, and so is one a shop shows both of.
eBay and Amazon search for every product and a hidden listing is not shown,
so neither counts. When a shop shows one product and has its listing on the
other hidden, the one it shows is kept, so its price stays on the site. Two
products one shop shows side by side are listed under their kept product
separately, so Merge these never joins them.

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
  shop to compare, waits for you. A page whose barcode is not ours, or not
  recorded yet while we hold one, is never linked on its name: the finder
  sent it to you for that reason.
- **Doubtful prices and wrong matches: Hide**, for the product's cheapest
  price or either price of a wrong match, when the shop's title plainly
  names another kind of product or another set's code. A dearer doubtful
  price is left alone: hiding it could leave a wrong price as the cheapest.
  **Checked**, for a cheapest doubtful price whose shop gives our barcode
  (never eBay or Amazon, whose barcode is our own copied in): the price stays
  exactly as shown, with no saving claimed, and leaves your list until its
  price or title changes.
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

For prices, found pages and duplicates, Claude only says whether two things
the site already holds are the same product: a found shop page and our
product, a shop's price and our product, or two of our products. It never
supplies a price, a barcode or a product, and never touches delivery
charges. The one place it supplies anything is an announced set, and only
what a trusted web page it cites states (see "Announced sets" below). The
site's own rules decide whether its answer may act:

Claude settles every row itself, so Things to check ends up nearly empty
without you. It never counts a price as right. Every act is listed under
Sorted for you with Claude's reason and an Undo. "Sure" and "fairly sure"
are Claude's own confidence; a "low" one counts as could not tell, and a
"different" that names only the price counts as a fairly sure "same".

The policy, for each kind of row (`judge.ruling`):

| Row | Sure or fairly sure "same" | Sure or fairly sure "different" | Still unsure after the careful second look |
| --- | --- | --- | --- |
| Doubtful price | **Checked**: stays as shown, no saving claimed | Hidden | **Checked** |
| Wrong match, cheaper price | Checked; the wrong match leaves the list | Hidden | Hidden (it is the price that would mislead) |
| Wrong match, dearer price | Checked | Hidden, once the cheaper one is settled | Checked |
| Found at another shop | Linked | Refused | Refused |
| Possible duplicates | Merged, when the rules allow it | Sure of a difference that makes another product: kept apart for good. Otherwise kept apart for now | Kept apart for now |

The site's rules come first, whatever Claude says:

- **A careful second look first.** Could not tell, a fairly sure
  "different" that names only a variant, a condition or the page, a fairly
  sure "same" for a found page whose price is far from the other shops'
  (or with no other shop), a fairly sure "same" for a pair whose names
  leave room for doubt, and a marketplace seller's price Claude is only
  fairly sure of: each is asked once more at high effort before it acts
  (at once, if your effort setting is high).
- **Found pages:** a title that plainly names another kind or set, or a
  shop barcode that is not ours, refuses the page whatever Claude says. A
  link that would turn another shop's good price doubtful, or put a saving
  over 25% on the product page, is rolled back and the page refused
  instead. A page whose barcode is not recorded yet while we hold one waits
  for the finder to record it.
- **Doubtful prices:** a title that says deposit is hidden: it is not the
  whole price. A shop barcode not recorded yet waits for the shop's next
  read. When Claude says a price is the product but its title names another
  kind or its barcode is not ours, that is a real conflict: the row is
  marked **Needs you** and stays with you. It is the only kind of row Claude
  leaves you. When Claude cannot tell and such a rule says it is another
  product, the price is hidden.
- **Wrong matches:** a checked cheapest price takes the wrong match off the
  list (the site claims no saving for it already) until its price or title
  changes.
- **Possible duplicates:** a pair is merged only on a sure "same", or a
  fairly sure one whose names agree on every point a rule can check (the
  same words once shop wording is set aside, one language, equal numbers,
  set codes and booster kinds, and no "Collector's Edition", "Case", size or
  wrapping on one side only) and whose certainty is certain or likely. Never
  across a **different** certainty (a rule such as `why_not_merge` or the
  languages says they differ), never when their barcodes differ, their shops
  charge far apart, a shop shows both or they are in different languages:
  each of those is kept apart for now instead. **Kept apart for now** takes
  the pair off the list while Claude is on; when anything Claude was sent
  about the pair changes (a shop's title, a barcode, a description), the
  hold is lifted at the next run and Claude asks again. It is never for
  good, and the hourly merge and the spreadsheet never treat it as your Not
  the same. With Claude switched off, held pairs are back on the list.
  **Merge the pairs Claude is sure are the same** still merges any sure
  pairs left on your tap. "Far apart" means outside 0.75 to 1.33 of each
  other when each product has two or more shop prices, and more than twice
  apart when either has only one, since one shop's price may be a sale's or
  a pre-order's. The merged product is switched off, not deleted; its
  barcode, shop pages and linked shop rows move to the kept product so shop
  reads never price it; and Undo puts back its listings, history and
  address. A price that has not changed since the merge gets back the
  verdict and last good price it had; one that changed is judged on its own
  evidence; one first seen while merged is judged afresh. The kept
  product's days after the merge held both products' prices, so Undo
  removes them rather than leave a price history that never happened.
  Merges undo newest first: one that a later merge touched waits until that
  later merge is undone, and the page says which.
- **Your answers always win.** A row you answered, an undone answer
  included, is never Claude's again. Undoing a Checked price or a pair kept
  apart for now hands it to you; undoing any other act also tells Claude it
  was wrong.

It starts in **trial**: it only suggests, and its answer shows under each
row ("Claude, 9 Oct: same product, sure. ..."). Once you agree with it, tap
**Let Claude act**. It then acts by the rules above when the answer came
from the model asked (not a fallback) and the row is still as Claude saw it.
The box counts how often Claude agreed with your own taps.

Answers Claude gave in trial are not wasted. Let Claude act, Ask Claude now
and every run first put Claude's existing answers through the same rules,
free (nothing is sent), and act on the ones that pass: only a row's latest
answer, only while the row is unchanged, never one from a fallback, never on
a row anyone answered before (an undone answer included). While Claude may
act, every answer that has not acted says why under its row: **Needs you**
for a conflict only you can settle ("Needs you: the shop's barcode is not
ours, so only you can say."), and **Next** for one waiting on Claude ("Next:
Claude takes a careful second look before it acts."). The Claude box counts
the rows that need you. The run line says how many rows were sorted from
earlier answers.

Every act needs the exact price Claude saw. A fallback model's answer is
shown but never acts, and the row is asked again at the next run. A row is
asked again when its titles, price or barcode change, at most five times.
Rows an answer can clear are asked first, taking turns: a doubtful price a
"same" would tick off (or a wrong match, its two prices together), a
duplicate pair, a found page a "same" would link, and round again, so a
long list of prices never keeps the pairs waiting. Pairs and found pages
visitors want most come first in their turn. Then the rest, then the
careful second looks. An hourly run
asks up to 50 rows; one you ask for with **Ask Claude now** up to 150,
within the same $3 and 15 minute limits. Claude looks at up to 3,000
doubtful prices and 3,000 found pages, far past the 50 the page lists, so
rows left waiting never hide the rest of a backlog. **Ask Claude now** starts the run
at once in its own process (`judge_checks` holds a lock file in the cache
directory, so two runs never overlap). At most 30 prices are hidden in one
run; the rest are hidden from their answers at the next. If you undo three
or more of Claude's acts in a week (counted from when you last tapped Let
Claude act) and they are at least one in twenty of what it did, it goes back
to suggesting and tells you. Undoing a Checked price or a pair kept apart for
now never counts: those were the safe choice.

**Sort everything** (a button in the Claude box, off by default) works
through the whole list now rather than 50 rows an hour. It turns on Let
Claude act and starts a run at once. With 20 or more rows waiting, the run
sends up to 300 of them to Anthropic as one **Message Batch**, at half
price: one batch at a time, each question's worst case reserved against the
monthly limit (and at most $25 of worst cases in one batch), its shop pages
read first (up to 200 pages in 7 minutes, at each shop's pace). Most
batches come back within the hour, all within a day. While a batch is out,
each five-minute tick only looks at it, which is free, and the rows in it
are not asked again; once it has ended, the run saves its answers at half
price and acts on them by the same rules, then sends the next batch. A
question the batch did not answer (expired, errored, cancelled) costs
nothing and is asked again. Fewer than 20 rows are asked one by one at once,
within the run's $3 and 15 minutes. Batches take no server-side fallback: a
refusal is recorded as declined, as on Haiku. The rules are the same either
way. The monthly limit still holds.

**Answering what is left.** Each list (doubtful prices, wrong matches,
duplicates, found pages) has a tick box on every row and buttons at its top
that stay in view as you scroll: tick several and tap **Hide ticked**,
**Ticked prices are right**, **Link ticked**, **Ticked are not this** or
**Ticked are not the same**. Each ticked row is answered as its own button
would, and only while it is as the page showed it; one that changed is
left and counted. With JavaScript on, every tap answers its row in place
with no reload, and Tick all ticks a whole list. Everything works with
JavaScript off too.

The push after a run you asked for, and the daily one, say "N sorted, N
need you". `python manage.py judge_checks --dry-run` says what the
answers already given would sort, at no cost, and what the next run would
send.

**Cost.** Each request is priced from Anthropic's usage figures at the
model's rates and saved (Claude's answers are listed read-only in admin), at
half for an answer from a batch. Before a request is sent, the most it
could cost is reserved, and nothing is sent that could take the month (a
UTC month, as Anthropic counts) past your limit, or one run past $3. A row
is asked again only when its titles, price band, barcode or the shop's
description change, at most five times. Roughly, per answer at medium
effort: Claude Opus 5.5 (the default) $0.025, Claude Sonnet 5.5 $0.013,
Claude Haiku 5.5 under $0.001, and half that in a batch. The shop page's
facts and the stored description add about 500 input tokens to a request
(about $0.002 on Opus), and are part of each request's reserved worst case,
so the limits hold. A careful second look, at high effort, costs about
twice.

An announced set costs more, because of the web search: Anthropic charges
$10 per 1,000 searches (a cent each, in a batch too) on top of the tokens,
and the pages found are read as input. Claude may run at most 3 searches a
set (`set_judge.SET_SEARCHES`), and one run, or one batch, holds at most 60
searches' worth of sets (`SEARCHES_PER_RUN`, so 20 sets); the other rows
carry on. Each reply's searches are counted from Anthropic's usage figures
and saved with the answer. Estimated, not yet measured on real traffic: about
$0.10 to $0.20 a set on Opus 5.5 one by one (two searches, about 20,000
input tokens), about $0.06 to $0.12 in a batch, and Sonnet 5.5 about half
the token part. A backlog of 50 sets is therefore about $5 to $10. Before a
set is asked, its worst case is reserved like any other: every search, each
search's results at 15,000 tokens carried through every round, and every
output token, which is $1.51 on Opus 5.5 one by one (a fallback could run it
twice) and $0.35 in a batch, so the monthly limit holds whatever a search
returns.

At **$100 a month on Opus 5.5** that is about 4,000 answers one by one, or
about 8,000 through batches: about 130 a day one by one, or 265 a day in
batches, spread over the month. A backlog goes faster: hourly runs can ask
1,200 rows a day and batches several thousand, so a backlog of a few
thousand rows is settled within a day or two and the monthly limit then
stops Claude until the next month or until you raise it. The ceiling,
`RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD`, is $100, and a new site's limit starts
there. To change yours, open **Claude settings and key**, set **Monthly
limit in US dollars** and tap **Save settings**: anything from $0 to the
ceiling. A site set up before this change keeps the limit it had (it was $10)
until you raise it. Low effort costs about half, high about twice.

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
run lets the free autopilot answer first, then asks Claude about up to 50
rows, one per request, in the turns above (Sort everything sends a long list
as a batch instead). A wrong key, no
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
needed you and spent) and Claude's last 20 answers with what came of each,
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
their prices); the site's own note on the price; the shop's own description
as its last read stored it (below); and what the shop's own page said when
it was read (below). Never anything about visitors, alerts or messages.

**The shop's description.** Every read already fetches each product's
description: Shopify's `body_html`, a website page's schema.org or Open Graph
description, a feed's description column, eBay's short description. The read
keeps it on the listing (and on a row waiting on Things to check) as plain
text, at most 1,500 characters, and writes it only when it changed, so reads
make no extra request and write no more than before; lists and the
background reader never load it. Claude is sent the first 1,200 characters
as `shop_description` (for a pair, one shop's description of each product, a
shop's before a marketplace's), with the pack counts, languages, edition
words (Pokemon Center, first edition, exclusive, limited edition, premium)
and condition words (damaged, opened, no shrink wrap) it names. The prompt
treats it as the shop's words, never as instructions. A row whose shop gives
no description keeps its earlier answer; a description that arrives or
changes is new evidence and the row is asked again. The Excel export shows
the first 300 characters on the Listings sheet, read-only.

**The shop's own page.** Titles and prices alone often leave Claude unsure,
so before each row is asked the site reads the shop page(s) it is about:
the found page, the listing's page, or for a duplicate pair one shop page
of each product (a Shopify shop first). From each it keeps a few plain
facts, sent as `shop_page` (`first_shop_page` and `second_shop_page` for a
pair) with the time they were read: the page's title and heading, the
product's name, the variant the address selects (`?variant=` is respected)
and the variants on offer, the price, the stock, how its barcode compares
with ours, the pack counts and languages the description names, a 300
character excerpt of it, and whether the page shows one product at all.
Each page's facts come to at most 1,200 characters: a long page keeps at
least 120 characters of the excerpt and loses its last item specifics or
variants first. A page that answers 404, a category page listing several products, or an
address that now opens another product's page is a fact too. Claude may
then answer "different" naming the page (a new answer, `page`), which
refuses a found page or hides a price like any other plain difference, but
never keeps a pair apart for good. The prompt is version 5, so every row is
asked once more with its page.

Only shops you added are read, the way their reads already read them: a
Shopify shop through `/products/<handle>.js` (as the stock probe does),
any other shop's page for its schema.org product data, Open Graph tags,
title and heading, and an eBay listing through eBay's own item lookup
(never its page). Amazon, feed shops (affiliate links the site never
follows) and hand-entered prices are not read, nor is a shop that is
paused, switched off, waiting after errors, being read right now (asked
again before every request) or asked by the background reader in the last
30 seconds (it notes each shop it asks in the shared cache, so the two
never add up to more than the shop's pace), nor a
page the shop's robots.txt keeps robots away from (a robots.txt that answers
401 or 403 keeps robots away from every page; one that cannot be read, a
5xx, a 429 or a timeout, stops that shop's pages for an hour). A redirect is followed
only within the shop's own site and never into a private network: a page
that redirects to another site is "not read", and one that redirects to
another page of the shop is reported as having moved. Each shop is asked no
faster than its reads, one try with a 10 second limit; a 429 or three
failures stop that shop for the run without touching its read back-off. A
run reads at most 60 pages in 3 minutes. What a page said is kept for a
day per address, so a second look or the next run does not read it again;
a page that could not be read is tried again after an hour and is sent as
"not read", with why. `RIPRAPTOR_CLAUDE_PAGES=0` stops the reading, and
Claude is then sent what it was sent before. A dry run reads no page.

**Announced sets** (`catalogue/set_judge.py`). The sets under Announced sets
to check that no release rule could add are Claude's too, in the same runs,
batches, monthly limit and Let Claude act. Before it answers, Claude checks
the set on the web with Anthropic's web search tool, limited to trusted
domains for the set's game: the publisher's own sites first (pokemon.com and
tcg.pokemon.com, the Japanese pokemon-card.com and pokemon.co.jp and the
Korean pokemoncard.co.kr, magic.wizards.com, onepiece-cardgame.com,
disneylorcana.com, starwarsunlimited.com, yugioh-card.com and the other
publishers in `set_judge.DOMAINS`), then long-standing references
(Bulbapedia, Serebii, PokeBeach, the MTG Wiki, Scryfall, Yugipedia and a few
more). A game with no domains there (Football) is not sent; those rows stay
yours. Claude is also sent the eight sets the site already has that are most
like the announced one (same code, shared name words, then a date within six
weeks), so it can say "the same as set N". What its answer may do:

- **A new set** is added under the name, code and date Claude found, but
  only what a page it cited states: the name must be on a cited page (or a
  trusted page's title), or nothing is added; a code or date only counts
  when the page naming the set states it too, and is left off otherwise. A
  set the site already has under that name or code, or under the source's
  name, is used instead: one set is never added twice. Claude's date is
  written as "claude", so it never replaces your date or a source's, and a
  publisher's date replaces it later.
- **The same as a set the site has**: the row is filed under that set, only
  when it is one of the sets Claude was sent and it cited a trusted page. A
  date it found is written only where the set has none.
- **Not a set** (a promo, a deck, an event, a misread title): dismissed as
  your Not a set does, only when Claude is sure.
- **Not sure**, after a careful second look at high effort, or nothing on
  the trusted pages backs the answer: the safe choice. The set is **left
  unadded for now**: off the list, not marked Needs you, and asked about
  again when anything Claude was sent changes (a date, a code, a new set
  like it) or after 14 days, as new pages may say more, at most five asks
  in all. With Claude switched off those rows are back on your list.

Each announced set on the page shows Claude's answer, the name, code and
date its sources state, and the pages it rests on (marked "publisher" for
the publisher's own), and the list shrinks as Claude settles rows. Every
act is under Sorted for you with its sources and Undo: undoing an added set
takes the set away again (while no other source has taken it and its date
is still Claude's), undoing a filing takes off only the date Claude wrote,
and either way the row is back for you and never Claude's again. Your own
Add set or Not a set always wins, even while Claude's question is out.

**Sold in shops?** With every announced set Claude also says how the set
is sold: in ordinary shops, only at events or direct from the publisher
(a convention exclusive, a Secret Lair drop), or not sure. When it adds or
files a set and is sure, and a cited quote on a trusted page that names
the set says it is sold only at events or direct (the quote itself must
use words such as "only at", "convention", "exclusive" or "direct from";
a quote giving only a release date does not count), the set is marked
**Not sold in UK shops** (see Drops below), with the page in "why not in
shops". Never on a not sure answer, and never on a set a UK shop (eBay
and Amazon aside) already lists. Undo clears the mark. (Prompt version 2: sets
left unadded for now are asked again once with the new question.)

**Sets the site already has** (`catalogue/set_retail.py`,
`python manage.py check_set_retail`). The set judge never looks at a set
again once it is added, so cron asks once a day, at 6:51 after waiting for
the judge's lock, about each set whose day is from 14 days ahead to 14 days
ago (while Drops shows it) that no UK shop lists at all: "is this sold in
shops?". Each set is asked once for its release date; a date that moves
asks once more. It uses the judge's switch, key, model, effort, monthly
limit and Let Claude act, at most 10 sets and 30 searches a run within
the $3 run limit, and the same rule for marking: sure, cited, naming the
set. Its answers are listed with Claude's others on Things to check. `--dry-run`
lists the sets and the most each could cost, and sends nothing.

Search results are web pages, so they are treated as data: the prompt says
so, and nothing a page says counts unless it passes the checks above
(trusted domain, cited, naming the set). An answer given before a search it
started has come back is not an answer.

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
holds; it never asks a shop, and its message says so ("Showing our latest
checks"). Anything that comes back is stamped and kept as a restock, which
the Deals page lists under "Back in stock". A pre-order a shop marks as available stays
a pre-order after a single-product check until the product's release date
(or its set's) is known and has come; the next whole-shop read decides
otherwise. A Shopify product whose own title or tags say pre-order is a
pre-order after a single-product check too, as in a shop read, so a shop
opening pre-orders is never shown as a restock.

Lotteries: some shops sell a scarce product by a draw (Obsidia TCG tags
these "Lottery"). Entering is not buying, so an available product whose
title or tags say lottery, raffle or ballot is stored as `lottery`, never
in stock or on pre-order, in every read, single-product check and source
(`importers.says_lottery`, applied when an `Offer` is made). It is never
the cheapest, never counted as stock, and never a deal, restock, alert or
search engine price. The product page lists it in All prices under the
label "Lottery", with the price, a short note and an "Enter lottery" button
through `/go/` like any other shop link. When the shop drops the tag and
sells it normally, it is stock again (and counts as a restock). A draw that
is not open (sold out) is out of stock as usual.

A listing for one variant of a Shopify product (an address
ending `?variant=...`, for example a case rather than a box) takes that
variant's price and stock, never the cheapest on the page, and is left as
it is when the shop no longer has that variant. In a whole-shop read, a
variant that is another product than its page ("1 Pack", "18 Packs", "Half
Box" or "Case" on a booster box page) is left out before any matching, so it
never prices the box; "English", "Japanese" or the page's own count ("36
Packs" on a "(36x Packs)" page) are not left out. A box title counts its packs
only in brackets straight after the box ("Booster Box (36x Packs)"); any other
"36x" is read as a multi-buy and refused. A shop's note of what a pack or
box holds ("| 5 Cards Per Pack", "(5 Cards)", "Booster Box | 30 Packs") is
not part of the name, so the shop's pack joins everyone else's product
instead of becoming a second one (Titan Cards, Oct 2026); the hourly tidy
takes the note off names already in the catalogue. So with a shop's word that
it picks the design ("(Random)", "(One at Random)", "(Assorted Designs)",
"(Random Design)") and an "English" after a comma, a dash or in brackets,
English being every name's default: "Ascended Heroes Mini Tin (Random),
English" was a second page beside "Ascended Heroes Mini Tin" (the owner, 28
Oct 2026), and the hourly tidy renames such a page and joins it to the one
of that name. "random" and "assorted" are also words matching ignores. A
design the shop names ("Mini Tin (Pikachu)") is still another product. eBay and Amazon
listings are never checked this way, and nor are feed shops: their prices
come from the feed agreed with them, and their links are affiliate clicks
a server must never follow.

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
- Probed on 25 Oct 2026, when twelve shops were added (`setup_shops`):
  **Element Games, Firestorm Games**: product pages carry no price data
  the reader can use. **Wayland Games**: mostly wargaming, and its sitemap
  reaches only part of its card range. **PokePiece TCG**: every page gives
  the same other product's data, and its boxes are "live rip and ship".
  **Collecterz TCG**: mostly articles, a dozen card products. **Monster
  Card Corner, Go Cards UK**: mostly single cards, and their sealed ranges
  were sold out (1 of 70 boxes, 1 of 51 sealed products). **The Awesome
  Card Shop**: Las Vegas, in dollars. **Manaleak, Spirit Games, Dark
  Sphere**: no sitemap to read.
- Probed the same day among the shops a UK stock tracker follows, when 27
  more were added: **Incom Gaming, Loaded Dice**: single cards, almost no
  sealed stock. **365 Games**: wargaming; its Star Wars Shatterpoint packs
  were read as Star Wars Unlimited boosters, which the classifier now
  refuses. **Mythic Cardboard, Endo Collects, NJ Cards UK**: sealed ranges
  sold out. **Just Play Games**: its sealed products are pre-orders for
  local events, for collection. **Game On Games**: a US shop in dollars.
  **Ravensburger UK**: the publisher's own store, left out as Asmodee UK
  is. No shop site found for The Game Collection, Endgame, Trainer's
  Haven, Deck Market, Gamers at Hart, The Chase, Alchemists Workshop or
  Pulse Collective.

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
of that wording is under Admin > Site wording > Legal and disclosure text. The footer also has "Follow us" with Instagram (`ripraptor_uk`), Facebook (`ripraptor`) and TikTok (`@ripraptoruk`) icons. They are drawn in `includes/social/` and take their size from `--icon-social`; they open in a new tab.

## Swipe page

`/swipe/` shows one product at a time with its cheapest price, retailer and
runner-up. Swipe right (or press the right arrow) to save it to a list kept
in the browser, left to skip, tap to open the product. Cards come from
`/api/deck/` twelve at a time and the next batch loads before it is needed.

## Search results

**Best match.** The words as typed first (a name that starts with them, then
one that holds them), then products with a price, then everyday products ahead
of bulk lots, then the products more shops list, then the newest. A bulk lot
is a display of bundles, tins or ETBs, a case, or a count such as "(10
Bundles)" or "6x Booster Box" (`catalogue/search.py`, `BULK_PHRASES` and
`BULK_COUNTS`); "Booster Display" (a booster box) and "Case of 36" (one box)
are not. A search that names one ("151 bundle display", "etb case") ranks
bulk lots as any other product. So "151" leads with the booster bundle, the
ETB and the pack, not a £2,150 display of ten bundles.

**Quick filters** above the results: English, In stock and the three
commonest product types in the game's own words (Booster bundles, ETBs),
each a plain link that turns its filter on or off, keeping the search and
the other choices, with Clear filters once one is on. Only choices that
change the list show: English while the results hold other languages too,
In stock while some are not in stock. The full Sort and filter choices stay
below. Every result card names its language, English too.

Not done yet: mini tin artworks and other cosmetic variants are still one
result each. Grouping them needs a "variant of" link between products.

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
| `web`       | Public views, templates, CSS, the font and the favicon, the owner's dashboard, and the admin pages Things to check, Crawl health, Insights and Your data in Excel. |
| `inbox`     | Message us, shop enquiries and shop outreach. |
| `guides`    | Guides and the Fair Price Checker: guides written in Admin, their checks, pages and counts. |
| `twostep`   | Admin sign-in security: two-step login with an authenticator app, and the block after 10 wrong tries. |

Checks before a commit: `python manage.py test` and
`python -m pyflakes ripraptor catalogue content web inbox guides twostep`.

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
| Change every gap at once      | `--space-1` to `--space-8` and the `--rhythm-*` tokens |

Spacing follows the golden ratio (`--phi`, 1.618). The scale steps from a
1rem base by phi in both directions (0.25, 0.375, 0.625, 1, 1.625, 2.625,
4.25 and 6.875rem). Every page uses the same four gaps: `--rhythm-section`
between sections and before the footer (2.625rem on phones, 4.25rem from
tablet width), `--rhythm-title` from a heading to its content (1rem),
`--rhythm-page-top` from the header to a page's first heading, and
`--rhythm-control` between the stacked controls on game and search pages.
Gaps are measured glyph to glyph: a section heading's box starts at its
capitals and a line of text that ends a section stops at its descenders
(CSS `text-box`), so the gap after a line of text looks the same as the gap
after a card. Browsers without `text-box` show a few pixels more.
Where two columns sit side by side (the product picture beside the price
box, the price history beside the details, a card's picture beside its
text column) they split 61.8 to 38.2 (`--split-major`, `--split-minor`).
On a card the Save button keeps its own width outside that split.
Every rounded card, the price box included, has the same inner edge
(`--card-pad`).

The header puts the logo at the content's left edge on every page and at
every width, with the menu on the right. There is no back arrow: browsers
have their own. Under 60rem the search box has its own row under them.

The typeface is DM Sans (the owner's choice, 30 Oct 2026; Archivo before),
self-hosted from `web/static/fonts/` (SIL Open Font License), so there are no
requests to Google Fonts. The file is Google Fonts' Latin one, with every
weight and optical size: small text is drawn a little wider and looser,
headings tighter. DM Sans has one width, so nothing on the site is condensed,
and no tabular figures, so the drop countdown keeps each two-digit part two of
the widest digit wide (`--countdown-digits`) to stop the line moving as it
ticks. Change it in `tokens.css` (`@font-face` and `--font-sans`), the font
preload in `web/templates/web/base.html` and the emails' fallback list in
`web/templates/web/email/base.html`, then redraw the badge ("The RipRaptor badge").

The logo is `web/static/img/logo.png`, served as `logo.webp` (the same
pixels, lossless, half the bytes) to browsers that take WebP, through
`web/templates/web/includes/wordmark.html`. Replace both files to change the
logo everywhere. The favicon is
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
- A product with several offers in one read (a page's variants, or two pages
  that share a barcode) has its listing written once, from the best offer:
  the cheapest in stock or on pre-order, else the cheapest. A page's offers
  are held until the read moves to the next page, and an offer from another
  page than the one the listing links to waits for the end of the read
  while that page is still to come. So a dearer first variant never prices
  the listing for a moment, never records a false restock, and a read that
  finds what the last one did changes nothing. eBay's offers name their
  product and are written as they come. The run counts each product once.
- Wrong links. A shop's link keeps the Shopify variant it selects
  (`?variant=...`), so each variant of one page is its own listing and one
  variant never takes over another's. A link to the bare page is taken by a
  variant only when the variant's title names that product in full. A shop's
  category page (`/collections/...` without `/products/`, or a section such
  as `/shop/pokemon/japanese/booster-boxes`) and a page whose data names
  several products are never a listing. And whichever way a page reaches a
  product (its link, a Yes waiting for a price, its name, eBay's search, the
  stockist finder), a title that plainly names another product never prices
  it: another game, another set code ("OP15" on our OP18 box), another
  language, a Pokémon Center edition against the shops' edition, a damaged,
  opened or unwrapped item ("Damaged", "Dent", "No Shrink", "Cut Seal")
  against the sealed one, another unit of sale (a case, a box, a pack, an
  Elite Trainer Box) or another number of packs ("3-Pack Blister" on a 1 Pack
  Blister). Only the shop's own barcode settles it the other way, when it is
  ours and a valid GTIN. A barcode two products shown on the site carry names
  neither of them: a read matches such a page by its link or name instead. The rule is `matching.contradiction`; such offers are
  in the run's unmatched list with the reason. One eBay listing is only ever
  on one product.
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
  stored) and redirects. Formats for programmes the owner has joined are also
  in `AFFILIATE` in `setup_shops`, set on a shop that has none so one changed
  in admin is kept. Zatu pays through Rakuten: its format is the deep link
  Rakuten's publisher dashboard makes (Links, Deep Linking) with the product
  address replaced by `{url}`, the same way for any other Rakuten shop.
- Clicks from people and direct hits (`catalogue/clicks.py`). A click through
  `/go/` is counted and carries our tracking (the affiliate format, eBay's
  campaign id, the Amazon tag) only when it is a GET with a Referer from our
  own site (ripraptor.com, www, the host in `RIPRAPTOR_SITE_URL`, the request's
  own host, and localhost when `DJANGO_DEBUG` is on), the browser does not say
  it is a robot or is prefetching (`Purpose`, `Sec-Purpose`, `X-Moz`), and the
  connection has made no more than 30 such clicks this hour (a one-way hash of
  the address, held in the cache for an hour, never stored). Anything else
  still gets a 302 to the shop, but at the plain address: eBay's `mkevt`,
  `mkcid`, `mkrid`, `campid`, `customid`, `toolid` and similar are removed
  (the item id is kept), Amazon's `tag` likewise, and no affiliate format is
  applied. These are saved with **suspect** ticked, left out of every click
  count (Insights, popularity, heat, Trending now, the traffic explainer,
  `shop_report`), and shown on Insights as "Direct hits sent on without
  tracking" beside "Clicks from people" for the last 7 days. Robots that say
  so and HEAD requests are not saved at all. The trade-off: a person whose
  browser sends no Referer (strict privacy settings, some extensions) still
  reaches the shop, but without our tracking, so such a sale earns nothing.
  That is the price of sending eBay only clicks that could become sales.
  Emails therefore never link to `/go/`: the drop reminders link each price
  to the product page, where the reader presses Buy.
- A feed link that is itself an affiliate network's click (Awin's
  `aw_deep_link`, a CJ or Rakuten deep link) is followed only for a person's
  click. A direct hit goes to the shop address inside it (Awin's `ued`, CJ's
  `url`, Rakuten's `murl`) or, for Awin's `pclick.php` which has none, to the
  shop's front page.
- The Amazon and eBay search buttons on a product page go through
  `/go/search/<product slug>/amazon/` and `/go/search/<product slug>/ebay/`
  with the same check: a person gets the search with our tag or campaign,
  anything else the same search without it. They are saved as clicks
  against the site's Amazon or eBay shop when there is one (no listing was
  clicked); with none there is nothing to count them against.
  No public page carries the tag or the campaign id itself.
- Every click says where it came from and what was pressed (the affiliate
  click). `OutboundClick` keeps the **page** (`source`: home, product, game,
  set, search, deals, new, drops, watchlist, swipe or games, read from the
  Referer the click already needs), the **button** (`button`, from the
  link's `?b=`: `buy_now`, `price_list`, `lottery`, `card`, `deal`, `drop`,
  `drop_offer`, `coming`, `watchlist`, `swipe`, `amazon`, `ebay`), the
  product, the shop (and so the game) and the day's one-way visitor token
  (`visitor`, the same as the visit's `DailyVisitor` row, which changes
  every day and cannot be traced back), so the dashboard can count the
  visits that ended at a shop. Every link to a shop is drawn by the
  `go_link` template tag, which also gives Analytics its affiliate_click
  details (`data-click`, `data-click-shop`, `data-click-product`,
  `data-click-game`). Admin lists clicks by page and button.
- Presses on Compare prices (the product page's button, a card's "Compare N
  shops", a row card's Compare prices: `data-compare`) are counted on our
  own server too, as `DailyPageView` kind "taps" keyed `where:page`
  (`/api/note/?what=compare`, nothing about the visitor), so Buy now,
  Compare prices and a shop's row in the price list can be set side by side.
- **Sales.** A shop's affiliate link format can carry `{click}` where the
  network takes our own reference (Awin: `&clickref={click}`), and an eBay
  link with our campaign gets the click's number as its `customid`; the
  network's report then names the click. Admin, Affiliate sales, Import a
  network report takes the network's CSV of transactions
  (`catalogue/sales.py`): columns are found by heading (date, shop or
  advertiser, commission, status, order value, transaction id, click ref or
  custom id), a report with no shop column takes the shop chosen on the
  form, a sale already imported (same shop and transaction id) is updated
  rather than added, so importing again after approvals is safe, and a sale
  with a click reference is matched to that click and so its product and
  game. Only clicks from people can be matched. Nothing about the customer
  is read or kept.

## Putting it on a server

Quickest: a fresh Ubuntu 24.04 server, the domain's A records pointing at
it, then as root:

```sh
curl -fsSL https://raw.githubusercontent.com/Dimondb1/cardthing/claude/compassionate-edison-aot3li/deploy/install.sh | sudo bash -s ripraptor.com
```

`deploy/install.sh` installs Python and Caddy, clones the code to
`/srv/ripraptor`, writes `.env` with a generated secret key, migrates,
collects static files, sets up the shops, starts the app and the
background reader as services, configures HTTPS for the domain (with
`www.` forwarding permanently to the address without it, so Google sees one
site rather than two copies; `http://www.` goes there in one hop, not two;
and request bodies over 25 MB refused before
they reach the site, which reads a whole upload before it can refuse one)
and schedules the cron jobs. The reader reads every shop on its first start. It also turns on Ubuntu's
automatic security updates (`unattended-upgrades`: security fixes only, installed daily, never a restart)
and a firewall (`ufw`) that lets in only the web (80, and 443 over TCP and UDP) and SSH (22 and any port
`sshd` is set to, allowed before the firewall goes on); a server that cannot run a firewall is told so and
the update carries on. Run the same command again to update. The repository must be
public (or the server needs a token) for the clone to work.

**Memory.** On a 1 GB server the site, the reader and the cron jobs share
very little room. On 14 October 2026 the server ran out, with no swap, and
the kernel killed the site's gunicorn: visitors could not load it for
about 30 seconds, and pages hung for a few minutes after. So the update:
adds a 1 GB swap file at `/swapfile` when the server has no swap (made
once, kept in `/etc/fstab`, with `vm.swappiness=10` so the site stays in
memory while there is room); has the kernel stop the reader before the
site when memory runs out (`OOMScoreAdjust` in both unit files; the reader
may use 100 MB of swap past its limit, then it is restarted); runs the
reader with two jobs at once on a server under 1.5 GB; and starts the
stock alerts at 3 past the tens, the search alerts at 4 past the
quarters and the Claude judge at 2 past the fives,
so no more than two cron commands start in the same minute. A 2 GB server
removes the squeeze altogether.

If the site was down, this prints a short read-only report: reboots, code
updates, restarts and crashes of the site, the reader and Caddy, when Caddy
could not reach the site, out-of-memory kills, locked-database errors,
memory, disk and what is running now, all in UK time. It changes nothing.

```sh
curl -fsSL https://raw.githubusercontent.com/Dimondb1/cardthing/claude/compassionate-edison-aot3li/deploy/why_down.sh | bash
```

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
  a row and its last error, with **See what happened**: the log of the
  shop's latest failed read.
- **Read now** makes the shop due at once and forgets its errors and its
  wait, so the background reader takes it within a few minutes (or the next
  hourly read does when the reader is not running). **Pause** and
  **Resume** set the shop's Reading paused box, which stops its stock
  checks too.

### What a failed read logged

Every read, by the background reader or the hourly import, collects what it
logs in its own thread (`catalogue/runlog.py`): pages read, retries, eBay's
progress, warnings and errors. A read that fails or warns keeps its last 200
lines on its run (`ImportRun.log`), shown under What happened on the run's
admin page (Price imports); a clean read keeps nothing. The lines are saved
every 30 seconds while the read runs and at once on an error, so a read the
background reader cuts off at its time limit still shows how far it got,
followed by the cut-off note; a read that crashes gets its traceback added.
Keys and tokens in addresses (`apikey=`, `/apikey/...`, `token=`) are blanked
first. Logs are cleared after a week by `tidy_all`. The run's list of
unmatched titles says why each was left out; a page an earlier No (the
owner's, the autopilot's or Claude's) keeps off a product it now names
word for word is listed as "an earlier No for <product>", so a No that
keeps a shop's price off the site for good can be found in admin (Shop
products to review, filtered by the shop and "Not one of ours") and undone.

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
were running. A due whole-shop read starts before anything else whenever
there is room for one, and the next read starts within seconds of the last
ending, since one read sees every listing a shop has while a single-listing
check sees one. Until 24 Oct 2026 reads queued by priority with the
single-listing checks, which outweighed them once listings were an hour old,
so on a two-thread server shops went two hours or more between reads.
It is polite: one job per shop at a time; at most one request
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
restarts it after three minutes without one. It is capped at 32% of the
server's memory (`MemoryMax`: about 300 MB on a "1 GB" server, 600 MB on a
2 GB one) and runs at a lower priority than the site.

To stop it, start it or read its log on the server:

```sh
sudo systemctl stop ripraptor-worker
sudo systemctl start ripraptor-worker
journalctl -u ripraptor-worker -f
```

`run_worker --once` plans once, runs every job due in the calling thread
and stops. It runs `RIPRAPTOR_WORKER_THREADS` jobs at once (3 unless set);
`install.sh` writes 2 in `.env` on a server with under 1.5 GB of memory (a
"1 GB" server reports about 960 MB), and takes that 2 out again at the
first update after the server is resized to 1.5 GB or more, so a resized
server runs three jobs with no edit (a 2 typed by hand on a big server
goes too; any other value stays). `install.sh` copies the unit file on
every update, so the setting belongs in `.env`, not in the unit. `--max-threads` overrides it for one run.
With 2 threads one whole-shop read runs at a time, so a long website read
(Magic Madhouse takes about half an hour) holds every other shop's read
until it ends; 3 threads allow a second read beside it.
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
| TCGdex | Pokémon | community | 6 hours | no published limit; the newest 40 sets, at most 40 set pages a read, 100 ms apart (`file_sets --backfill`: every set, once) |
| pokemon.com UK news | Pokémon | official | 6 hours | the news index and at most 3 expansion articles a read |
| YGOPRODeck card sets | Yu-Gi-Oh! | community | 6 hours | one request; filtered on our side; set images are never shown from their server |
| Lorcast | Lorcana | community | 6 hours | 100 ms between requests |
| SWU-DB | Star Wars Unlimited | community | 6 hours | one request |
| One Piece Card Game products | One Piece | official | 6 hours | pages 1 and 2 |
| Dragon Ball Super Fusion World products | Dragon Ball | official | 6 hours | page 1 |
| Cardfight!! Vanguard products | Vanguard | official | 6 hours | page 1 |
| Weiss Schwarz products | Weiss Schwarz | official | 6 hours | page 1 |
| Flesh and Blood coming soon | Flesh and Blood | official | 6 hours | page 1, asked with a browser User-Agent because the site refuses any other |
| Digimon Card Game products (world.digimoncard.com) | Digimon | official | 24 hours | one page, which lists every English product |
| Riftbound site (riftbound.leagueoflegends.com, now answering from playriftbound.com) | Riftbound | official | 24 hours | the card gallery, the news page and at most 2 set overview articles a read |

The Digimon page gives each product's kind, title, code and dates. Boosters
(BT, EX, RB, AD), starter and advanced decks (ST) and the Limited Card Packs
(LM) are sets; the other Premium Bandai products, sleeves, gift and adventure
boxes are not. The set takes the "Release date" (for regions outside the US),
never the pre-release date a week earlier or the US shipping date. A month
("March 2026") is kept on the release and never written to a set. Names drop
"Extra Booster", "Theme Booster", "Advanced Booster" and "Limited Card Pack",
as shops do ("EX14 Malevolent VII"); codes are kept as published ("BT-27",
"BT15", "ST-9"). The site writes titles in capitals, so names are title
cased, except two Digimon whose published names keep inner capitals
("UlforceVeedramon", "RagnaLoardmon"). EX14 and other Japanese-only sets are not on the English
site, so they wait under "Announced sets to check".

Riot's Riftbound pages carry their content as JSON inside the page. The card
gallery lists every set with its three-letter code (OGN Origins, OGS Proving
Grounds, SFD Spiritforged, UNL Unleashed, VEN Vendetta, RAD Radiance) but no
dates. A set's English release date comes from a line on the news page that
names that set alone ("Spiritforged ... releases on 2/13") or else from its
overview article ("The Vendetta Overview": "Release: July 31 for English and
Chinese"). A date written without a year takes the year of the article it is
in (the next year when that would put it more than 60 days before the
article), and a release in another language or country is never taken. A
set already dated is not looked up again. When a news line links off the site
(a video or the merch store), the release row points at the news page the
date was read from instead. Riot never numbers sets "RB 03";
that is a shop's product number, so it files nothing.

Web pages are fetched a second apart and no more than 25 a day across all
of them, dry runs included; each page is counted as it is fetched, so the
background reader and `scan_releases` run at the same time cannot go over
together. A source that would go over waits for the next day. Sets released
more than 180 days ago are recorded, off Things to check, and become sets only
through the back catalogue below.

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

### Filing products under sets

A product with no set is filed under one only when its name says which, and
only one set fits (`catalogue/releases.py`, Filing products under sets):

- **By code first.** A code in the name that is the code of exactly one set of
  the game. Codes compare in one spelling: "BT 15", "BT-15" and "BT15" are one
  code, and "ST9" is "ST09" (Pokémon keeps its zeros, since a bare SV8 is as
  often the Japanese set as the English one). A run of sets such as
  "Special Booster Ver.2.0 (BT18 19)" or "BT01-03" is never filed by its codes
  (a set whose own code is a run, such as Digimon's "Special Booster Ver.2.0"
  [BT18-19], takes only products with its whole name), and a name with two
  sets' codes, or a code two sets share, is left alone. Riftbound's three-letter
  codes (OGN, SFD) file products; no other game's letters-only codes do, since
  "BRO" or "WAR" is also a word.
- **Then by name.** The set's whole name as a phrase, filler words left out.
  A one-word set name ("Origins", "Fabled", "Morningtide") files a product only
  when the name starts with it (after the game's name and a "Set 3:") and goes
  on with the kind of product: "Origins Booster Box", never "Deoxys VMAX Battle
  Box" or "Mega Evolutions". A set whose name another set's holds ("Final
  Fantasy" and "Final Fantasy Commander", "Team Rocket" and "Team Rocket
  Returns") files a name only when a booster word follows it, and never when
  any other word of the longer name is in the product's ("Star Trek Collector
  Commander Deck", "D&D Battle for Baldurs Gate Commander Legends").
- **Never** when two sets fit (unless one's name holds the other's), when the
  product is in another language from the set, or when it already has a set: a
  filed product is never moved.
- **Never** a reprint ("Reprint", "2025 European Print") under a set that is
  not itself a reprint edition, a "Commander" or "25th Anniversary" product
  under a set without that word while the game has sets with it ("Ikoria
  Commander Deck" is not Ikoria), a name that says "Series" after the set's
  name, a manga or a pin badge, or a Pokémon "Radiant Collection" card (that
  name is printed in both Legendary Treasures and Generations).
- **Never** a Digimon product the publisher lists apart from its sets: one with
  a Premium Bandai, gift box, adventure box, double pack or premium deck code
  (PB-25 "Digimon Story Time Stranger Set" is not the Time Stranger booster) or
  a name that ends in "Set" ("Digimon Liberator Debuggers Set").
- A Riftbound shop number ("RB 03", "RB 02PC01") is taken out of the name before
  its words are matched, so "Unleashed RB 03 English Booster Pack" files under
  Unleashed by its name.

**Korean sets** (Pokémon, `releases.KOREAN_MIRRORS`). Korean Pokémon sets are
the Japanese sets, printed in Korean and released in Korea later, under the
same names and codes. No release source lists them, and a Korean product never
files under the Japanese set (its date is Japan's), so Korean products had no
set and a Korean list offered none. Now a Korean product that names a
Japanese set the site has, by code or by name ("25th Anniversary Collection
s8a Korean Booster Box"), files under that set's Korean twin, added with the
first one: "25th Anniversary Collection (Korean)", the Japanese code, and no
release date, since Korea's is not Japan's and nothing says what it is. You
can give one a date in Admin. English sources never take a twin as the set
they speak of. The hourly tidy files them.

**The back catalogue.** Every set a release source lists is recorded, the
old ones too. A set released before the radar's 180 days becomes a set only
when a product on the site names it by those rules, with its source's date
(as that source's, so a publisher or your own date stands over it); nothing
is added that would stand empty, and no set ever comes from product names
alone (sports cards aside, below). Sets the sources list but the site does not have also stop a product
being filed under a set whose name is only part of the product's ("Black &
White" in "Black & White Dragons Exalted"). TCGdex's set ids are not the
codes shops write, so sets from it carry no code; nor does a set whose code a
source gives to another set as well (YGOPRODeck's SRL is both "Spell Ruler"
and "Spell Ruler (25th Anniversary Edition)"), so each keeps its own date. A
back-catalogue set you delete in Admin stays deleted.

**Sports cards by product line** (`catalogue/sports.py`). No release source
lists sports card releases, so the sports game (Sports cards, at
`/games/sports-cards/`) takes its sets from its products' names: brand, line and
league, such as "Panini Prizm Premier League", "Topps Match Attax Extra" or
"Panini Donruss Optic NFL". Every season of a line is one set, and each
product's own name keeps its season; a set per season would leave most sets
holding one product. Basketball, baseball and racing lines carry NBA, MLB or
NASCAR, and a line made for several sports carries "Football" for its
football sets. Topps's Champions League lines, renamed UEFA Club Competitions
in 2022/23, are one line. A line whose name is an everyday word ("One",
"Select", "Limited") counts only beside its brand's name. A name that names
no line the file knows (a new brand, a club's own box) is left with no set
rather than guessed at: add the line to `LINES` (or a league to `LEAGUES`).
A set's name or a product moved in Admin stays; a renamed set keeps its
address, which is how new products still find it. The hourly tidy files new
products, and `python manage.py file_sports_cards`, which `install.sh` runs
once after an update, files them straight away.

The game was Football cards at `/games/football/` until 22 Oct 2026.
`file_sports_cards` moves it to its sports address and name (a name you give
it in Admin stays) and moves its page view counts with it. `install.sh` runs
it straight after `migrate`, with the background reader stopped, so neither
the old code nor the new ever meets the other's address. Every old address,
`/games/football/`, a line under it and `/feeds/football.xml`, sends visitors
and search engines to the same page at the new address for good (a 301,
filters kept), once the game has moved.

**Set codes no source lists.** For Digimon, Riftbound and Dragon Ball Super
Masters the hourly `tidy_all` puts each set code in the names of products
still without a set that no set or release source has (a Japanese-only
Digimon EX14, a Masters B24) under "Announced sets to check", from "product
names on the site". Claude checks
them on the publisher's site when it is on (digimoncard.com,
riftbound.leagueoflegends.com, dbs-cardgame.com); otherwise tap Add set with
the set's name, and its products file by the code. These rows are listed
after the sources' own announced sets, so they never push one off the list.
Football has no sets.

Filing runs when a set is added, when an import creates a product, after each
release source read, and in `tidy_all`. To file everything once and see what
happened:

```sh
python manage.py file_sets --dry-run      # what would be filed per game, with examples and reasons
python manage.py file_sets --backfill     # read each source's whole list once (the publishers' booster
                                          # lists and every TCGdex set too), then file
```

`--backfill` reads the One Piece booster list (3 pages), the Dragon Ball
Fusion World booster list (2 pages), the Digimon products page (1 page) and
the Riftbound gallery, news page and up to 2 overviews (4 pages), which count
against the 25 web pages a day, and every TCGdex set (about 200 requests,
100 ms apart). Run it once after updating, or one source at a time with
`--source digimon_products` or `--source riftbound_sets`.

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
the stock watcher, the stock alerts, the drop reminders and the search
alerts 9, `check_worker` 10, the nightly
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

The home lists, the deals page, the newest drops, the sets coming soon and
the feeds are kept in a cache because they read every priced product. The cache is a folder of files, `RIPRAPTOR_CACHE_DIR`,
shared by the web app's workers and every command, so when an import or the
stock watcher finds a restock or a new pre-order, the site shows it on the
next page load once the background jobs rebuild the lists, seconds later while the reader runs. On a server the folder
is `/var/lib/ripraptor/cache` (`install.sh` creates it, owned by
`ripraptor`); on your own computer it is `.cache` beside the code. Deleting
the cache files (`*.djcache`) is always safe. The folder also holds
`listcache/`, the times of the last clears and the build locks for the page
lists (safe to delete too), and
`crawl-paused` while Pause all is on: deleting it resumes reading. The folder outlives a restart, so `install.sh`
empties it once the site has restarted on the new code: lists saved by the
old code can lack a field a migration added.

If the folder cannot be created or written, the site still runs: it logs
"Cache folder ... cannot be used" and each process keeps its own cache, as
before, so changes from imports take up to ten minutes to show. Fix the
folder's owner and restart the site.

Every saved listing or product clears the cached lists. A clear no longer
deletes them: it notes the time in a small file (`listcache/cleared.json`),
and every list built before it is stale (`web/listcache.py`). Each kind of
clear keeps its own time, written under a file lock and only ever raised, so
two processes clearing at once never undo each other's. Visitors keep being served the
stale copy, with the time each of its prices was checked, while the
background jobs build it again and replace it in place, so after the first
fill the cache is never empty and a visitor never waits for a list because
prices changed:

- a list is built by a visitor only when there is none (an emptied cache) or
  its build started more than `RIPRAPTOR_LIST_MAX_STALE_SECONDS` (ten
  minutes) ago. That build holds a lock file (`flock`, which the system
  lets go of if the process dies): other visitors and workers who need the
  same list wait for it (up to 20 seconds, then build their own) instead of
  all building it at once. Django's own cache cannot be the lock: its file
  cache checks and writes in two steps, so two workers could both take it;
- the end of every import, a restock found by the stock watcher,
  `tidy_listings` hiding eBay prices, a delivery rule change and the fixes on
  Things to check force a clear: the jobs build the lists again on their next
  turn. An ordinary save lets a list stay until it is 30 seconds old
  (`RIPRAPTOR_CACHE_CLEAR_SECONDS`), so a shop read that changes a few dozen
  prices causes one build rather than one per price;
- the owner's own changes clear hard: any form the owner sends while
  signed in (a save in Django Admin, a hide or a merge on Things to check)
  stops the copies built before it being served at all
  (`web.middleware.OwnerEditsMiddleware`). The reader builds them on its
  next turn, and a visitor who comes first builds them, so a hidden product
  or a corrected price is gone at once rather than after up to a minute;
- a clear waits until the change is committed, so another process cannot
  rebuild the lists from the rows as they were. The home and Deals lists
  read the same offers once; each is dated from when those offers were read,
  so a change saved between the two builds leaves the second one stale;
- the cache keeps up to 20,000 files. Past Django's default of 300, which
  the Claude judge's entries (one per shop page it read) can pass, Django
  deletes a random third of the files, the page lists among them.

The feeds, which no job builds, are still deleted by a clear, at most once
every 30 seconds per process unless forced. The tests keep a cache of their
own, so they never read or clear your development site's cache, and as they
run no background jobs they build a cleared list on the next request
(`RIPRAPTOR_SERVE_STALE` is off in the tests only).

The background jobs that build the lists again (`web/warming.py`):

- the background reader looks at the start of its loop (every 5 seconds)
  for a forced clear or a missing list, and every 30 seconds for ordinary
  clears and lists nearly `RIPRAPTOR_HOME_CACHE_SECONDS` old, and builds
  only those;
- `tidy_all`, `import_prices` and `watch_stock` build what is due when they
  finish;
- cron runs `python manage.py warm_pages --due` every five minutes on its
  own lock, the floor for when the reader is not running;
- `install.sh` runs `python manage.py warm_pages` after emptying the cache.

Visitors' requests never build a list in the background. The site runs two
gunicorn sync workers on a small server: a build is seconds of Python, and a
thread in a web worker would slow every page that worker serves meanwhile
and die whenever the worker restarts. The reader is already a separate
process, so it does the building.

Run `python manage.py warm_pages` to build them all by hand,
`python manage.py warm_pages --due` for only those cleared, missing or
nearly expired, or `python manage.py warm_pages --missing` for only those
not in the cache. A list is built by the same code a page would run, so
prices age out of it under the usual stale rules at its next build, and no
list is served more than ten minutes after its build started.

The lists read only what they need: the products in stock are picked from
their offers first, and the full rows (game, set, prices) are loaded for the
few picked. Every list works out prices per product from the `listing_offer`
index on listings (product, stock, check time, shop, verdict, price and
delivery), so it never reads the listing rows themselves, which carry the
shop's description. That is why the lists add price and delivery rather than
reading `delivered_price`: SQLite cannot read a generated column from an
index.

## Settings

| Environment variable           | Default | Notes |
|--------------------------------|---------|-------|
| `DJANGO_DEBUG`                 | on      | Set to `0` in production. |
| `DJANGO_SECRET_KEY`            |         | Required when debug is off. |
| `DJANGO_ALLOWED_HOSTS`         | `localhost,127.0.0.1` | Comma separated. |
| `DJANGO_CSRF_TRUSTED_ORIGINS`  |         | Comma separated, with scheme. |
| `DJANGO_SQLITE_PATH`           | `db.sqlite3` | |
| `RIPRAPTOR_CONTACT_EMAIL`      |         | Shows the "Report a problem" section on the how it works page, and is emailed when a refer a friend referral completes (see "Refer a friend"). When set, List your shop enquiries are emailed here instead of to `RIPRAPTOR_INBOX_NOTIFY_EMAIL`. |
| `RIPRAPTOR_STALE_AFTER_HOURS`  | 72      | |
| `RIPRAPTOR_USE_FEED_IMAGES`    | on      | Keep retailer images for products without one. |
| `RIPRAPTOR_HOME_CACHE_SECONDS` | 300     | How old the cached page lists may grow before the background jobs build them again. Imports and edits mark them stale sooner. |
| `RIPRAPTOR_PAGE_CACHE_SECONDS` | 300     | How long a built game or set page (each tab and set of filters under its own address) is served whole from the shared cache. 0 turns it off. |
| `RIPRAPTOR_LIST_MAX_STALE_SECONDS` | 600 | A cached page list is never served once its build started longer ago than this: the visitor who finds it builds it first. |
| `RIPRAPTOR_CACHE_DIR`          | `/var/lib/ripraptor/cache` (`.cache` with `DJANGO_DEBUG` on) | The cache folder the site and every command share. Must be writable by the user the site runs as. |
| `RIPRAPTOR_WORKER_THREADS`     | 3       | Jobs the background reader runs at once. 2 suits a server with under 1.5 GB of memory, and `deploy/install.sh` writes 2 on such a server when the setting is missing, and removes that 2 once the server has 1.5 GB or more. Restart `ripraptor-worker` after changing it. |
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
| `RIPRAPTOR_ZEPTOMAIL_TOKEN`    |         | Send Mail token from a Zoho ZeptoMail Mail Agent. With `RIPRAPTOR_MAIL_FROM` set, turns on back-in-stock emails, drop reminders, search alerts and the Your alerts box. |
| `RIPRAPTOR_ZEPTOMAIL_URL`      | `https://cpaas.zoho.com/v1.1/email` | The API address shown on the Mail Agent's API page (Zoho CPaaS, formerly ZeptoMail). |
| `RIPRAPTOR_MAIL_FROM`          | `alerts@ripraptor.com` | The sending address, on a domain verified in ZeptoMail. |
| `RIPRAPTOR_SITE_URL`           | `https://ripraptor.com` | Used for links in emails. |
| `RIPRAPTOR_DISCORD_WEBHOOK`    |         | Optional. A Discord channel's webhook address that gets every update until the first channel is added in admin (Discord channels). Keep it secret. |
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
| `RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD` | `100` | The highest monthly limit the page accepts, in US dollars. |
| `RIPRAPTOR_CLAUDE_PAGES`       | on      | Before Claude is asked about a row, read the shop page(s) it is about for evidence (see "The shop's own page"). `0` sends only what the site holds. |
| `RIPRAPTOR_CRAWL_PUSHES`       | on      | Tell you about crawl problems by push and email: the background reader stopped, a shop has failed every read for a day, or more than 10 doubtful prices are waiting as the cheapest price of their product. Each at most once a day. `RIPRAPTOR_CRAWL_PUSHES=0` turns them off; new messages are still sent. |
| `RIPRAPTOR_ADSENSE_CLIENT`     |         | Google AdSense publisher id (`ca-pub-...`). Empty means no adverts and no Google script. Pages marked noindex never carry it. |
| `RIPRAPTOR_TWO_STEP`           | on on the server; off with `DJANGO_DEBUG` on | Admin and the dashboard take an authenticator app's code as well as the password (see "Admin sign-in security"). `0` switches it off. |
| `RIPRAPTOR_GA_ID`              | `G-TZW5ZVM9H8` on the server; empty with `DJANGO_DEBUG` on | Google Analytics 4 measurement id. Each visitor is asked first; see "Google Analytics". Empty means no card and no Google script. |

## Games

The games the classifier recognises are listed in `catalogue/classify.py`
(`GAMES`): Pokémon, Magic, One Piece, Lorcana, Yu-Gi-Oh, Star Wars
Unlimited, Flesh and Blood, Digimon, Dragon Ball Super, Riftbound,
Cardfight!! Vanguard, Weiss Schwarz, Union Arena and sports cards (football,
American football, basketball, baseball and racing, filed by product line:
see Filing products under sets). A game is created
in the database the first time a shop product of it is imported, so adding
one means adding its words there, its filter labels in
`catalogue/types.py`, its eBay search word in `catalogue/ebay.py` and its
prefix to `GAME_PREFIX` in `catalogue/matching.py`. Pick games several
shops stock: one shop's price is a listing, not a comparison.

## Languages

Every product's language is read from its name, and failing that its set's
name and code, by one set of rules (`catalogue/languages.py`), and stored on
the product. The language filter (English, Japanese, Chinese, Korean, Other
languages) uses it, the product page says it beside the product type and in
its details, and search finds it ("japanese 151"). It is set when a product
is saved, by `python manage.py set_languages` (the update runs it), and every
hour by `tidy_all`. To correct one, put the language in the product's name,
for example "(Japanese)".

What counts, in order:

- **Guards first:** "not Japanese", "rulebook in Dutch", "made in Japan",
  "Chinese New Year" and "Asian English" say nothing about the cards.
- **Stated:** a language word in English or the language itself
  (Japanese, Korean, Simplified Chinese, Deutsch, Japonais), a product
  named in another language (Top-Trainer-Box, Coffret Dresseur d'Elite,
  Bustine), a script (kana, Hangul, Thai, Cyrillic, Chinese characters,
  Simplified or Traditional where a character says which), or a seller's
  code where it cannot be a word: "(JP)", "[DE]", "- FR" at the end, "JP
  Version", and the tokens JPN, CHN and KR. eBay titles are also read
  loosely: an upper-case JP, KOR, CHS or CHT on its own counts there. A bare
  JP in a shop's title does not (it may be a player's country on an English
  deck), and neither do "SC", "It" in Wreck It Ralph, "De" in Cruella De
  Vil, Magic's "Kor" or "Germany". For Pokémon, the codes only a stated
  language has: Simplified Chinese (CSV8C, CBB3, Gem Pack, 151C), a letter
  after a Japanese code ("s7D F" Traditional Chinese, "SV8-T" Thai), and
  the Thai and Indonesian sets (sv9s, MA2); for One Piece, OPC and OPK.
- **English** ("English", "Eng", "(EN)") then outweighs what is implied.
- **Implied, Pokémon only:** a set code with a letter after the number,
  which only Asian editions have (sv2a, sv5M, s12a, SM12a, M2a); a
  Japanese-only kind of product (High Class Pack, Deck Build Box, Premium
  Trainer Box) or set name (Terastal Festival, VSTAR Universe, Eevee
  Heroes, Ninja Spinner). These mean Japanese, because Korean and
  Traditional Chinese reuse the codes but say so. English numbering (SV3.5,
  SV08, SWSH12.5), bare codes English shares (SV8, SM6, M2) and set names
  English shares (Black Bolt, White Flare, 151) say nothing.

"Chinese" with something that says which Chinese ("Chinese Gem Pack Vol 4",
"(csv9C) Slim Booster Box Chinese") is that one, here Simplified Chinese.

Union Arena's set codes say the edition: UA53BT is the Japanese release,
UE53BT (and UEX) the English one, so a title with only a UA code is
Japanese. Its names drop the game's name, "English Version", the set code
and "Vol 1" (`union_arena_name` in `catalogue/classify.py`), so "Jujutsu
Kaisen Union Arena English Version Booster Box" at Collectors Kingdom and
"UE03BT Jujutsu Kaisen Booster Box" at Transcendent Cards are one product.
A title that is only a code and a set gets its kind from the code (BT a
booster box, ST a starter deck). Collectors Kingdom, Transcendent Cards and
Attic Cards were added on 13 Oct 2026 for Union Arena.

Football boxes are read by language words and scripts only: JPN, KOR and
GER are team codes there. A title that states two languages, or joins a
Japanese-only set to another ("Destined Rivals / Glory of Team Rocket"), is
mixed: it never matches by name and the autopilot never acts on it.

A shop or eBay title in another language never matches a product by name
(a title naming no language counts as English, so it only suggests our
Japanese product, never links it),
two products in different languages are never duplicates (so never merged),
a product is filed only under a set of its own language, the autopilot says
no to a found page or hides a price whose title is plainly in another
language, and Claude is told both languages. New products from a shop title
that gives only a code ("151 (sv2a) Booster Box") are named with the
language in words ("... (Japanese)").

## Product types per game

`catalogue/types.py` lists, for each game, which product types it has and
what its players call them: Play booster box and Commander deck for
Magic, Structure deck for Yu-Gi-Oh, Illumineer's Trove for Lorcana, Hobby
or retail box for sports cards, and so on. The type filter on game, set,
search and swipe pages offers only the types that game's catalogue holds,
in the game's words, and product pages use the same words. Magic's
collector boosters are their own types, and `tidy_catalogue` retypes any
that were filed as play boosters. Browsing also filters by price band and
by products compared at two or more shops.

The type filter is exact, so a product's type must be right. A shop's
category can be wrong (a pack filed under "Booster Boxes"), so a name that
plainly says another type wins (`plain_type` in `catalogue/classify.py`):
booster pack, booster box, Elite Trainer Box, bundle or tin. Names that
say two types, or that no rule reads safely (checklane, double pack set,
starter pack, mystery box), keep their type. This is applied when a
product is made from a shop title, by `python manage.py set_types` (the
update runs it; `--dry-run` lists what would change), and every hour by
`tidy_all`. A type you set in Django Admin is never changed, and a product
is only given a type its game uses.

## Drops

`/drops/` is the one page for new sets (Drops in the header and every
footer). It has two sections, plain links that work without script, and
game chips (`?game=pokemon`) that apply to both:

- **Coming soon** (the default): every set still to come that has a date
  the site trusts, soonest first under This week, This month and one heading
  per later month, then "Date not announced yet" for undated sets a UK shop
  already takes pre-orders for. A set's own date is always a full day (the
  release radar writes one only from the publisher or two sources that
  agree, or you type it); an accepted month-only announcement shows its
  month, never a day. Announcements still waiting on Things to check are
  never read.
- **Out now** (`?show=out`): what the old Latest drops page listed (released
  in the last 45 days or first listed by a shop in the last 14) that a shop
  has in stock, as product cards with Buy now, with sort and filters folded
  under "Sort and filter". Below them, "Just out, not in stock yet" lists
  sets out in the last 14 days that no shop has in stock, saying so, with
  Pre-order when a shop still takes pre-orders and never a reminder.

Each Coming soon card is short: the date tile, the set, "Out in 5 days"
(with a live countdown only within the week), one line with the best offer
("Pre-orders from £x delivered at 3 shops", or "In stock from £x" when a
shop sells it early) and which product and shop that is with when it was
checked, and one main button: Pre-order or Buy now to that shop through
`/go/`, or Remind me when no UK shop has it, or See the set (its own page)
when neither can be offered (emails not set up, or out today after the 9pm
cut-off). Remind me is a second, quiet button otherwise. The best offer is
the cheapest UK pre-order across the set's products, counting a product's
pre-order even when another shop sells it from stock for less, the same
listing the Coming soon rows pick; the product list shows that pre-order
beside the product's cheapest offer. A product with its own later release
date is left out of both, as it is out of the reminder emails. Remind me and "See all N products" (each product's
cheapest shop, its checked time and its own button) are `details`
elements, so they open without script. Offers are UK shops only, never eBay
or Amazon. A set whose day has come with no shop listing it says "It is
out, but no UK shop we check lists it yet". Remind me is only offered while
a reminder could still be sent (`drop_alerts.can_send`: not after the day,
nor on the day after the 9pm email window), and the sign-up refuses it
otherwise; undated sets take no reminder. On a set out today the form says
only one email can still go. A Remind me link from another page opens that
form with the email box focused.

### Sets not sold in UK shops

Some real sets never reach a UK shop: they are sold only at events or
direct from the publisher (Magic's Mystery Booster Commander Edition is a
Festival in a Box and a Secret Lair presale). Such a set is marked **Not
sold in UK shops** on the set in Django Admin (Catalogue, Sets: a tick box
in the list, a filter, and "why not in shops" on the set's form for the page
that says so). Tick it yourself, or Claude ticks it (see "Sold in shops?"
under The Claude judge). A marked set is left off Drops (both tabs), the
Coming soon rows on the home and game pages, and the reminder sign-up
(it answers not found); reminders already waiting for it are deleted at
the next reminder run without an email. Its own page still shows, with
"Sold at events or direct by the publisher, not in UK shops." under the
heading where the date line is (`browse.set.not_in_shops` in Site wording).
Untick it to put the set back.

`/new/` (the old Latest drops page) answers a permanent redirect to
`/drops/`, or to `/drops/?show=out` with its filters when it had any, and
is out of the sitemap. Out now has its own title and description
(`meta.drops.out.*`). Views of `/drops/` count under the Latest drops kind
in Insights, so its history carries on.

Queries: Coming soon is six with an empty cache (the three drop queries in
`catalogue/drops.py`, Out now's count per game, the first product's date and
the wording), and Out now about fourteen, however many drops there are.

### Coming soon rows

The home page has one Drops section, the drops page in small: up to 3
Coming soon rows (only sets a shop takes pre-orders for, most popular
first, see "Your games first" below) under a "Coming
soon" heading that opens `/drops/`, then an "Out now" row of products just
out and in stock (the same products as `/drops/?show=out`, which its heading
opens), so everything it shows is behind its link. Out now holds only products
that are out by the UK date: one whose own or set's release date is still to
come stays under Coming soon, however a shop marks it (a shop taking orders
early is not the release), and a date that has passed is never taken as stock.
An undated product counts by its shops alone. Out now cards, and cards in the
In stock tab or with the In stock filter on, lead with the cheapest shop that
has it in stock, never a cheaper pre-order (`views.in_stock_offers`), and then
leave out the Price check, which was worked out on that other price. The home page and each
game page's Pre-orders tab show sets
still to come as compact rows: the set, game, date, best price with when it
was checked, and one button, Pre-order (or Buy now) to the cheapest shop
through `/go/`, or Remind me, which opens that set's reminder on the drops
page (`/drops/?remind=<id>#drop-<id>`), or See the set when no reminder
can be taken. "See all" goes to `/drops/`. A set page says when it is out
in the line under its heading ("due out on <date>", or "Release date not
announced yet."), and the small print under that names the release source
that gave the date.

A set marked Not sold in UK shops never counts. Otherwise a set counts when
its date is today or later, or when it has no date but a
shop (never eBay or Amazon) has one of its products on pre-order, checked
within the stale window, and no shop already sells one from stock. The list
is one query (`web/views.py` `coming_soon_sets`), cached under
`web:coming-soon:v2` and `web:coming-soon:v2:<game>`, and cleared with the
other lists, including whenever a set is saved. The wording is under "Coming
soon sets" and "Drop tracker and drop reminders" in Site wording.

Reminders (`catalogue/drop_alerts.py`, model `DropAlert`) work like back in
stock emails and need the same ZeptoMail settings; without them the form
does not appear. The form works without JavaScript (it posts, then shows a
"Check your inbox" page); with it the answer replaces the form. A confirm
email goes first and nothing else is sent until it is pressed. Then up to
three emails, each once and each listing the cheapest offers with their
prices, shops, checked times and a "See prices" link to the product page
(never `/go/`: an email sends no Referer, so a click from it would reach the
shop untracked):

1. Pre-orders open: a UK shop opens pre-orders on one of the drop's products
   after the reminder was confirmed and before the day before the drop, and
   still takes them. The email names the shops that opened and always lists
   their prices, then the cheapest others.
2. Out tomorrow: from 8am UK time on the day before a dated drop. If the
   set's date moves after it went, it is sent again the day before the new
   date (the reminder keeps the day it was for in `eve_for`).
3. Out today: from 8am on the day, after which the address is deleted.

A product with its own later release date (a collection that follows the
set) is not part of the drop: it is left off the card and out of the emails.
A month-only set a shop already sells from stock counts as out, as on the
Coming soon list: it is not listed as still to come and takes no reminder.

The day-before and drop-day emails go only between 8am and 9pm; a window
missed is skipped, never sent late. A month-only drop hears only about
pre-orders until its day is published. Reminders are deleted unconfirmed
after a week, once their drop has passed, and after a year in any case.
Every email has an unsubscribe link that deletes the reminder at once. One
address can wait on 30 confirmed drops; one internet connection can ask ten
times an hour, and one email address is sent at most three confirmation
emails a day across every set, however many connections ask (both counted
in the shared cache under a one-way hash); asking again within ten minutes
sends no second confirmation. The form answers "Check your inbox" for every
address, so it never tells a stranger whether an address already has a
reminder. The Terms page says all this.

`send_drop_alerts` sends what is due. It is safe to run at any time and from
two processes at once (each email is claimed before it is sent and handed
back if ZeptoMail fails). Cron runs it every 15 minutes at 9, 24, 39 and 54
past, minutes no other command starts on, on its own lock
(`/tmp/ripraptor-drops.lock`). `deploy/install.sh` installs the line; run
`deploy/install.sh` again after updating, or add it by hand from
`deploy/crontab`. Reminder events are recorded with the alert counts
(`drop_asked`, `drop_confirmed`, `drop_sent_<stage>`, `drop_stopped`) but are
not yet shown on Insights; drops page views count with Latest drops.

`seed_demo` adds five fictional "(demo)" sets dated from today so the page
has something to show locally.

## Deals page

`/deals/` is the page to link from social posts: the biggest savings
between the cheapest shop and the next, the biggest price drops this
week, "Trending now" (the most clicked products, with how far each price
moved this week) and what came back in stock over the last seven days,
server rendered and in the sitemap. These used to sit on the home page;
they moved here so the home page stays short. Its lists are cached like
the home page's and cleared by imports.

**Lowest in 90 days** sits under the savings: products whose
cheapest confirmed delivered price now is at or below the lowest we recorded
in the 90 days before today, with at least 14 of those days, and at least 5%
under their average over them (a price that never moved is not news). Each
row shows that average above today's price, biggest saving on it first. A
fall over 70% is left out as a wrong product link, and a doubtful cheapest
price waits until it is confirmed, as with price drops. It is built from the
same daily record as the Price check (`fair_price.at_lowest`, one query) and
covers the same games.

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

## Home page and product cards

The home page opens with a compact hero: a short line above the headline
(optional), the headline on one line with its last sentence in orange
(`accent_last` in `web/templatetags/ripraptor.py`), one line saying how
many UK shops the site compares and that every price includes delivery,
not just the basket price ("Sealed TCG prices from 87 UK shops, ..."), then
the large search
box and the latest shop price check, all close together so the games and
Picked for you start on the first screen. The number of shops
(`pricing.shops_compared`) counts the UK shops with a current price on the
site, so it moves as shops are added, stop being read or come back: eBay
and Amazon are searches and never count, nor a price kept out of the
comparison. It is counted at most every ten minutes, and the background
reader counts it with the cached page lists, so no visitor waits for it.
The wording is `home.hero.subtitle` in Site wording, with `{shops}` where
the number goes; wording you have edited there keeps your text until you
add `{shops}` to it. Then "Find your game": a short
tile per game, its box art small beside its name and product count (a big
count written short, "4.1k products", `compact_count`, never rounded up),
every game in one row that swipes sideways (two and a bit on a phone, so the
next tile peeks in; five and a half on a computer), then "More games". The
home and Deals pages' sections sit 20px apart on a phone and 24px wider
(`--rhythm-home`). Each tile shows the box art of one of the game's own products, the
newest booster box with a picture, or else an elite trainer box, bundle and
so on (`game_covers` in `web/views.py`), so no game logo is borrowed; a
game with no pictures yet shows the booster box outline. After that come
Picked for you and Pick up where you left off (for a browser with history),
Featured deals, Drops and Coming soon, a card leading to the
watchlist, and "Browse by product": a tile for every product type with
products, most first, linking to the search page filtered to it. Each is
pictured by a real product of its kind, the one with a picture that the
most UK shops list now (`type_covers` in `web/views.py`, chosen at most
hourly), so a tile looks like what it leads to; a type with no picture
anywhere keeps its outline drawing. Each tile has its ⓘ card in its top
corner, and so does each game tile. That is all:
savings, price drops, trending and restocks are on the Deals page and every
set is on the Games page. Every product card in a row ends with the same
"Compare prices" button.

### Your games first (Drops and Featured deals)

The home page's Coming soon rows and Featured deals are put in one order
for everyone on the server, cached like the other lists, and then each
browser puts its own games first. Nothing about the visitor is sent or kept.

On the server, Coming soon is ordered by popularity
(`catalogue/popularity.py`), from counts the site already keeps for the last
seven days, none of them about a person:

    interest = views + 4 x clicks + 8 x reminders + 3 x shops
    soonness = 0.5 ^ (days until the drop / 21)   (undated counts as 28 days)
    score    = (1 + interest) x soonness

Views are product page views of the set's products (robots are never
counted), clicks are clicks through to a shop for them, reminders are the
confirmed drop reminders waiting for the set, and shops are the UK shops
taking pre-orders. Soonness halves every three weeks, so a set six weeks
away needs about four times the interest of one out this week to rank above
it. Ties go to the sooner date. The home page renders the top 8
(`COMING_SOON_RENDERED`) and shows 3; the rest are `hidden`, so without
script the page shows the three most popular. The order is cached under
`web:home-drops:v1` and costs one query a cache fill. Game pages and the
drops page keep their soonest-first order.

Featured deals keep their rules exactly (`featured_pick` in
`web/views.py`): the same eight deals in the same order without script.
Up to two spares on each side (shops that pay a commission and shops that
do not) are the next qualifying deals, rendered hidden.

In the browser, `web/static/js/interest.js` (included inline straight after
those rows, so it runs before they are painted and nothing visibly moves)
reads this browser's own history: game and set pages visited, saved
products, recently viewed products and recent searches, matched to games
by name, short name and search aliases. It puts the visitor's games first,
keeping the server's order within each, shows the top 3 drops, and lets a
spare deal from the visitor's game take the place of the last shown deal
from the same side that is not, so three in four featured deals still go to
shops that pay a commission. It makes no network calls. Clear history on
the home page forgets the games too.

### The raptor

The head from the logo appears where it has something to say, as the owner
drew it, and never beside section headings or in the footer. On a product
page it appears only inside the Buy now button, holding a shopping bag (see
"The product page" below). The whole logo stays in the masthead on every page.

- "Prices. Dropped.": an orange panel on the home page after Featured
  deals, leading to the Deals page, with the head in paper on the right.
- "Keep an eye on it.": a dark card leading to the watchlist, with the head
  in orange coming in from the left.
- "No luck. Yet.": a search that finds nothing shows a card with the head in
  ink and an orange question mark, the search it tried, a Browse games button
  and a link to each game.
- In ink above the heading on a broken link (with the question mark), the
  empty watchlist and the end of the swipe deck, each with a short line of
  its own in a grey bubble beside it (the owner, 30 Oct 2026: the site lacked
  character): "Sniffed every shelf. This page is not here.", "Not a scrap on
  my shelves by that name.", "Save one and I'll watch its price." and "Back
  tomorrow with fresh prices." The lines are in Site wording
  (`*.raptor`); empty one to hide it. The head stays hidden from screen
  readers, the line is read.
- On every buy button (Buy now, the cards' Buy and Pre-order, the buy bar)
  the raptor lifts its head towards the shop on hover and snaps its jaws once
  when pressed (`.is-chomping`, added by `site.js`), turning about its neck
  with the bag: 340ms, transforms only, none with reduced motion. The ⓘ cards
  have no raptor: most are on product pages, which keep the head to Buy now.

The two panels use the owner's poster style: heavy capitals in lines of even
length (`--text-poster`, `--weight-poster`, `text-wrap: balance`, which also
keeps the line under the heading clear of the head) and the head
cropped by the panel's edge, hung from the top on wider screens so its eye
always shows. Their wording is under Home page in Site wording; the last
sentence of each heading goes on its own line. The orange panel is the one
place orange is a surface rather than a button or link, at the owner's request.

The head is `web/static/img/raptor.png` (the head from the 512px app icon
with the white removed) drawn through a CSS mask, like the search and bag
icons, so it takes any token colour at any size; the panels use
`raptor-lg.png`, the same shape enlarged three times with its edge
re-sharpened, so it stays crisp at panel size. `mark.png` stays the source
`make_icons` builds the home screen icons from. Sizes are the `--mark-*`
tokens; `--mark-sm` is the floor, below which the crest smudges.

### The product page

On a phone the first screen holds the product's name, its picture, the
cheapest delivered price and Buy now, in that order: the breadcrumb, name
and a short details line (type and language) above the picture, the price
straight under it, then the buttons. The header and search box are compact
on phones for the same reason. The picture's height is whatever the first
screen leaves (`--product-media-phone`, measured against the screen with the
browser's bars showing), with a smaller allowance for names over 60
characters and for an unconfirmed delivery charge, which adds a line. Wide
screens put the picture beside the name and the price box.

- **Buy now** is one wide orange button: the raptor head holding a shopping
  bag, the words "Buy now", the shop it goes to ("at Harbour Games") and an
  arrow. It links through `/go/` to the cheapest offer a visitor can buy now,
  the first row of the price list, so clicks are counted and the shop's
  affiliate link applies. The mark is `includes/buy_mark.svg`: the site's own
  raptor head (`img/raptor.png` through the same mask as the logo marks) with a
  small shopping bag hooked on its lower jaw. The bag is a separate drawing, so
  it can swing from the top of its handle; its place and size are the
  `--buy-bag-*` tokens.
- The same mark, smaller (`--buy-mark-card`), is on the Buy now and
  Pre-order buttons of product cards and watchlist rows, so every buy button
  looks the same. It does not swing there: a list has no reason for motion.
  On a card the price and the button share one row and one centre line.
- **Compare prices ↓** is an outlined button under it: a plain link to the
  price list's heading, so it works without JavaScript. With it
  (`js/product.js`) the page glides there, or jumps when the visitor asks for
  less motion, lands the heading clear of anything stuck to the top of the
  screen, and moves keyboard focus to it.
- On arrival, once per page view and only when motion is allowed, the
  picture fades in, a soft orange sweep runs from it to Buy now, the bag
  swings once and settles, then (the owner, 30 Oct 2026: subtle motion so a
  visitor knows what to press) a thin orange ring spreads once from Buy now
  and the Compare prices arrow nods once: about a second and a half in all,
  each step under 400ms, opacity and transforms only. The price and button
  are readable and pressable throughout; with reduced motion the page simply
  appears finished. Hovering Compare prices nods its arrow too, and every
  button on the site shrinks a touch (scale 0.97) while pressed.
- **The buy bar.** On a phone (under 60rem wide) the cheapest price, Buy now
  and Compare prices also sit in a bar fixed to the foot of the screen
  (`.buy-bar`, last in the page so Tab order is untouched), because in some
  apps' browsers the price box's own Compare prices button fell below the
  fold (the owner, 30 Oct 2026). `js/product.js` shows the bar while neither
  the price box's Compare prices button nor the price list is on screen (the
  part under the bar does not count), and hides it otherwise; without script
  it is always shown. It slides up (reduced motion: appears), its arrow nods
  once each time, and off screen it is out of the Tab order. The footer and
  the cards that come up from the foot of the screen (analytics, home
  screen) leave room for it. Its presses are counted as `buy_bar`, beside
  `buy_now` and `buy_box`, in Clicks to retailers and the dashboard's
  Compare taps. Reload prices updates the bar's price too. Beside the
  picture on a wide screen the price box is always in view, so there is no
  bar.

The wording ("Compare prices", "{count} shops, cheapest delivered first",
"{count} shops" on the bar, "at {retailer}") is under Product page in Site
wording.

### What is inside

How many packs a product holds, and how many cards each pack has, so a box,
a bundle and an Elite Trainer Box can be weighed against each other (the
owner, 30 Oct 2026). Under the cheapest price the product page says "24 packs
inside, £3.63 a pack" (the cheapest price shared over the packs, for a
product holding two or more), and Product details has an Inside row ("24
packs of 12 cards"), marked "(from the shops' descriptions)" unless the
figures are your own.

Nothing is guessed from the product type: counts differ by game, language
and set (an English Pokémon box has 36 packs, a Japanese one 30, a Magic Play
Booster box 30, One Piece 24). `catalogue/contents.py` reads each live UK
shop listing's own description (kept by every read) for a count it states
plainly: a number before a pack word in the same clause ("Contains 36
booster packs", "9 Pokémon TCG: Scarlet & Violet, 151 booster packs" is 9),
and "10 cards per pack" or "Each pack contains 10 cards". A count of cards,
a share ("1 of 36 packs"), a description giving two different counts, or one
describing several boxes (a case) gives nothing. The product's figure is the
one most of the shops that give one agree on, more than all the others
together. eBay and Amazon descriptions are sellers' words and are never
read. `tidy_all` keeps the figures each hour (`refresh_contents`, also run
once by each update); you can set your own under the product in admin
(What is inside), which always wins, beside what the shops say.

### Price check

Beside "delivered" on a product page, a small chip says whether
today's cheapest delivered price is a good one for that product
(`catalogue/fair_price.py`). It is judged against RipRaptor's own record of
the cheapest delivered price each day (the same rows as the price history),
up to a year back and not counting today:

| Band | Shown as | When |
| --- | --- | --- |
| 5 | Lowest yet | at least 5% under the average and at or below the lowest recorded |
| 4 | Good price | at least 5% under the average |
| 3 | Fair price | within 5% of the average |
| 2 | Above average | up to 15% over it |
| 1 | High price | more than 15% over it |

A High price chip carries Rip off Rex, the owner's own character
(`web/static/img/rex-small.svg` on the chips, `rex.svg` on the Price check
sheet), drawn through a mask in the chip's ink like every other icon on
the site, in place of the arrows. It is never shown beside a shop that
pays a commission (an affiliate link, eBay or Amazon), at the owner's
request: the Price check is left off the page rather than softened, on the
product page (`web.views.product_detail`) and on a card led by that shop
(`Product.card_check`), and the Terms page says so.

- No price check shows until a product has 14 days of prices before today,
  or when the cheapest shop's delivery cost is unknown. Nothing is guessed.
- No price check shows for a product only one UK shop lists (in stock or
  not, `MIN_SHOPS`): one shop's price measured against its own past says
  nothing about the market. A lone £1,199.99 Ultra Shiny GX box was being
  called "Lowest yet" because its price had never moved; it now gets no
  verdict, and no badge (see Featured deals). eBay and Amazon are searches,
  not shops, so they never count towards the two.
- No price check shows on a doubtful cheapest price: it may not be this
  product. A price that only eBay or Amazon sellers give, for a product no
  UK shop we read lists in stock or not, is doubtful ("found by a search,
  and no shop we read lists it to compare with"): shown, never called a
  low, a saving or a deal, and put to Claude, who hides it when the
  listing is not the product. The hourly `tidy_listings` judges such
  products again, so one left alone when its last shop listing went is
  checked within the hour. On 24 Oct 2026 two promo cards from the Holiday
  Calendar 2025 were its only price, as "Lowest yet".
- A price that has never moved is its own lowest every day. That is a fair
  price, never "Lowest yet": the lowest band also needs the price to be 5%
  under the average.
- The record is ours: it lives in the database whatever happens to any shop
  or source, so the price check keeps working if a source stops. The
  outside references are Cardmarket's European price (see The European
  market below) and the RRP (below).
- **RRP**: the recommended retail price is a ceiling, never a reason to
  praise a price. Up to 15% over it (delivery included) changes nothing,
  and is Fair price when nothing else measures the product; up to 35% over
  it is at best Above average; more is High price, however low the price
  is against our record or Cardmarket. The RRP is the one you type on the
  product in Admin (RRP box), which always wins; otherwise the one UK
  shops give beside their own price, when at least two give the same
  figure (to the pound) and more give it than any other. Each read keeps
  the shop's figure on its listing (`Listing.list_price`): a Shopify
  shop's compare-at price, or a page's schema.org ListPrice,
  StrikethroughPrice, MSRP or SRP. A shop's figure more than twice its own
  price, or under half of it, is not believed. The hourly
  `refresh_price_checks` keeps the product's (`Product.shop_rrp`, with how
  many shops give it), shown read-only under the RRP box in Admin. The
  Price check sheet shows the RRP and who gives it. Shops tend to show an
  RRP only when they sell under it, so a product every shop sells over
  RRP needs yours typed in. The dashboard's Price check card shows the
  share of products on sale with an RRP.
- Tapping it opens the Price check sheet (`js/product.js`): the verdict,
  today against the average and the lowest, the price chart, and the date
  the record starts. Without JavaScript it is a link to the price history,
  which shows the same three figures above the chart.
- The chip is the owner's design (`includes/fair_price.html`), beside the
  price: on a narrow box "delivered" drops under the price instead of the
  chip dropping under both and leaving a gap. Four signal
  bars, a divider, an icon and the verdict, the whole chip one colour.
  Green for Lowest yet and Good price, grey for Fair price, dark yellow for
  Above average and High price. Never red, which stays for a dearer shop's
  price. Lit bars: 4 for Lowest yet down to none for High price
  (`Verdict.signal`). Icons: a crown, a tick, an equals sign, one arrow up,
  two arrows up (`includes/fair/*.svg`).
- The colours are the `--color-fair-{good,even,bad}-{bg,ink,rule}` tokens,
  the one place outside prices and savings with colour, at the owner's
  request. Each ink reads at 4.5:1 or more on its tint.
- Every game has it: it began as a Pokémon trial and was switched on for
  all games at your request once Cardmarket covered them. A game Cardmarket
  does not cover (Football) has only our own record to go on. The
  thresholds are `GOOD`, `HIGH`, `MIN_DAYS` and `WINDOW_DAYS` in the same file.

**In lists.** Each product's band is also stored, with what it rests on
(`Product.price_band`, `price_basis`: record, market or both), by
`fair_price.refresh_bands`: six or seven queries for the whole catalogue, the
same rules as the product page. `tidy_all` runs it each hour
(`refresh_price_checks`), and `install.sh` once after each update. From it:

- **Fairest price** in Sort and filter (`sort=fair`): lowest yet first, then
  good, fair, above average and high, then products with no Price check, the
  cheaper first within each.
- **Product cards** show the same chip, smaller, beside the badge
  (`includes/fair_card.html`), at no query of their own. A card's chip can
  lag the product page by up to an hour; the page works its own out fresh.

The wording is under Product page in Site wording (`product.fair.*`).

**The European market.** Once a product is matched to Cardmarket (see
Cardmarket market prices), Cardmarket's trend price for it, in pounds at the
day's ECB rate, is a second reference: at most 95% of it is a good price, up
to 115% fair, up to 135% above average, more is high (Cardmarket's prices
are before post to the UK and import costs, so a fair UK price sits a little
over them). It works for a product only one UK shop lists, so the lone
£1,199.99 Ultra Shiny GX box against Cardmarket's £577.70 is called a high
price. With both references the verdict is the lower band of the two, and
the market never makes a price "Lowest yet". The sheet then shows "European
market £577.70, Cardmarket, 22 Oct" and says what that price is.

### When a price was checked

A product page's headline price says which shop it is from and when that
shop's price was checked ("Price checked at Harbour Games 2 hours ago"),
never the newest check of some other shop, and each row in the price list
carries its own time. Reload prices updates the line with the rest. Only a
sold-out product shows "Last checked", the newest check across its shops.

Search, game and set pages share one card (`includes/product_card.html`):
the picture, the language when it is not English, the name, the type, the
badge and a bookmark to save it; the cheapest delivered price with the shop
and Buy now; the two cheapest shops as rows, the cheapest flagged; then
"Compare N shops" and "Check prices", both opening the product's price list.
The filters above are one select each for sort, game, type, price and
language, and two switches, In stock and 2+ shops (an even grid of equal
widths: two to a row on a phone, as many as fit wider). A game page lists 8
sets as chips with no heading on screen (screen readers hear "Sets";
one row that scrolls sideways on a phone): the sets people want most this
week first (`popularity.set_demand`: product page views plus four for each
click to a shop, over seven days), newest first when equal, so a set
everyone is hunting leads the row even when it is sold out. Each chip shows
how many of the set's products are in stock in the language and type chosen,
after a dot in the in-stock colour (grey at none; screen readers hear "12
in stock"),
ending in "All sets" when it has more, which opens the game's whole list on
the Games page (`/games/#game-<slug>`: the Games page shows a game's 8 newest
sets and keeps the rest in the page, shown by CSS once the address names that
game, so it needs no script). The chips follow the language and type chosen:
with Korean picked, only sets with Korean products show (Korean Pokémon
products file under Korean twins of the Japanese sets, see Korean sets). Each chip keeps the
language, type and sort in its link, so the set page opens on what the
visitor was looking for; not the price band or 2+ shops, which could leave
that page empty. A set page lists no other sets; under its
heading it gives its code and dates in small print and one link, "All
<game> products".

Game and set pages are kept short. The line under the heading says what
the page holds (see the hubs below), so there is no second product count
unless a filter narrows the list, and none beside the stock tabs, which
count their own lists. While anything is in stock it is one short line
("4,179 sealed products compared across 53 UK shops.", `browse.hub.brief`):
the In stock tab gives that count. A set page then shows its cheapest
product in stock as a small card under the small print ("Cheapest in
stock", the picture, the name, the delivered price in green and the shop;
`includes/cheapest_card.html`), rather than a second sentence, at your
request. The whole card opens the product's page and counts as
`hub_cheapest` among the Compare taps. Sort and filters show only for a list of
12 or more products (`FILTERS_FROM`), folded under one "Sort and filter"
button (a `<details>`); a shorter list has none unless something is chosen.
The panel loads closed even while a filter is chosen, so a page reached
from a set chip opens on its list: the button names the choices ("Sort and
filter · Booster box · Korean") and Clear filters sits beside it. The form
carries the stock tab only when the address named one, so a filter that
empties the tab in view opens on the first tab with products.

A change in the filters, a quick filter chip or Clear filters changes the
list in place (`js/browse.js`, on game, set and search pages): the page the
change names is fetched and the part under `<div data-browse>` (everything
but the share sheet) takes the place of the one on screen, under the
browser's own short cross-fade where it has view transitions and motion is
not reduced. Nothing scrolls away, the folded panel stays open, the select
keeps its focus, the address follows (`pushState`) and Back and Forward go
through the lists seen, fetched again. While the new list is on its way the
old one dims a touch, only once the wait is long enough to notice. The
scripts that enhance a list hear `ripraptor:content` (images, Save links,
the filter form) and `ripraptor:page` (the stock tabs set up again) and
take the new markup on. A page of results is still a page load, so the
top of the new page is in view, and other pages' links are theirs. Without
the script, or when the fetch fails, the form or link loads its page in
full, and the panel the change was made in opens on it (remembered for that
browser tab only).

Moving between pages cross-fades too (`@view-transition { navigation:
auto; }` in `site.css`): a short fade in place of a flash of white, with
the masthead staying put (`view-transition-name: masthead`), in browsers
with cross-document view transitions (Chrome 126+, Safari 18.2+; the rest
load as before). Both fades last `--motion-normal` and are off under
reduced motion.

### Pages ready before the tap

Three things make a tap feel instant. Game and set pages are built once
and kept whole in the shared cache for `RIPRAPTOR_PAGE_CACHE_SECONDS`
(`web/pagecache.py`): every visitor sees the same page, so the next one is
served the copy, each tab's list and each set of filters under its own
address. Prices on a list can be that many seconds behind the shops; the
product page is never cached, and an edit of the owner's (a hard clear of
the lists, `web.listcache.mark_stale`) forgets every kept page at once.
`warm_pages` builds each game's own page before anyone asks (the tap from
the home page) and `--due` builds any whose copy has gone, so the first
visitor never pays either; a page served from the cache says
`X-RipRaptor-Page: cached`. The tests run with the cache off
(`RIPRAPTOR_PAGE_CACHE_SECONDS` is 0 under `manage.py test`) and its own
tests turn it on.

In Chrome, a link touched or hovered is rendered in the background before
it is tapped (speculation rules in `base.html`, `eagerness: moderate`), so
the page is there the moment it is. Never a shop link (`/go/` counts a
click), the admin, search (it records the search), the swipe page, the
watchlist or a private page. A page fetched or rendered this way is not
counted as a view when fetched (`web.middleware` skips a request marked
`Sec-Purpose: prefetch`, and a tab's list fetched by script, `?pane=1`);
`site.js` counts it through `/api/note/?what=view` once it is shown, and
`history.js` and `install.js` wait until then to remember anything, so the
dashboard counts visits, not the browser's guesses. Every tap answers at
once (`touch-action: manipulation`), and a list's cards no longer fade in
as they scroll into view: a list is there as soon as the page is. "Email me when products like these are in stock" sits under
the list, only while the list has products or the set is still to come.

Game and set pages split their products into three tabs instead of the In
stock switch. The page opens on the first tab with products (a tab named in
the address still wins), only tabs with products show (and the one in view),
and when that leaves one list there is no tab row at all: just the list,
under "On pre-order" or "Out of stock" when it is not the In stock one. The
tabs are **In stock**, **Pre-orders** (soonest out
first unless another sort is chosen, with the game's Coming soon sets at
the top) and **Out of stock** (everything nobody has in stock or takes
pre-orders for, including products no shop lists yet). Each product is in
exactly one, and each tab shows its count, all counted in one query.
2+ shops means two shops in stock, taking pre-orders or listing it,
for the tab in view. The tab row stays at the top of the screen as the list
scrolls. Without JavaScript each tab is a link to its own page
(`?tab=pre-orders`, `?tab=out-of-stock`); with it (`js/tabs.js`) the other
tabs' lists are fetched one at a time once the page is idle
(`?tab=...&pane=1` returns just the list, marked noindex) and sit beside the
first, so a swipe or a tap moves between them at once. When a swipe settles,
the tab, the address, the filters' hidden tab and the height follow, and a
page scrolled past the top of the list goes back to it. The swipe page uses
the same selects for game, type, sort and language, without the price select
or the switches.

## Featured deals and sold-out pages

"Featured deals" is the first deal row on the home page: up to eight in-stock products
with evidence that today's price is a good one (`offers.Summary.is_deal`),
strongest first, each card naming its evidence:

- **Lowest in 30 days**, verified from our own daily record: today's
  cheapest delivered price is at or below every day's cheapest over the
  last 30 days, with at least 14 days recorded, and at least 3% under the
  month's average (a price that never moved is always its own low, so it
  is not a deal).
- **Save £X vs Shop**, a saving of at least 5% on the next shop, and only
  when that shop's price is in line with the market: no more than half as
  much again as the cheapest, or matched within 10% by a third shop. One
  unusually dear listing (£250 against a lone £699.95) says that shop is
  dear, not that £250 is a bargain, so no saving is claimed against it
  anywhere on the site: cards, the Deals page's biggest savings, picks.

The same 30-day low shows as a "Lowest in 30 days" badge on product pages
and cards, ahead of the week's badges, and only when it is that 3% under the
month's average. "Best in 7 days" needs the week to have been dearer at some
point. A card or price box shows one price history label: the Price check
gauge when the product has one, else one of these two badges. Which shop is
cheapest today is the price list's own "Cheapest" mark; there is no "Lowest
today" badge (it sat beside "Above average" and read as a contradiction). A product only one UK shop lists gets no badge at all, and is never a
featured deal or on Lowest in 90 days: there is no other price to judge it
against. A pick on Picked for you is labelled with a saving only when it
is 15% on a shop in line with the market (see "Picked for you").
The rules and their numbers are at the top of `catalogue/offers.py`.

Six of the eight places go to deals whose cheapest shop pays a
commission (an affiliate link format, eBay or Amazon), the best of the
rest take the other two, and either side fills in when the other is
short. To feature only shops that pay, tick **Only shops that pay** in Admin under
Products and prices -> Featured deals settings (off by default). The page lists the
shops that count as paying now; a shop counts once its retailer has an
affiliate link format, and eBay and Amazon always do. Ticked, the row can
hold fewer than eight deals, or none, and then it is not shown; the line
under the heading and the Terms page switch to "Featured deals come only
from shops that pay us a commission" by themselves. Saving rebuilds the
home page's row at once. A line under the Featured deals heading says so too, beside
the row it is about, and that price comparisons are always cheapest
delivered first and no shop can pay to move up, with a How it works link
(`home.featured.disclosure`). The footer line on every page says featured deals favour shops
that pay, and the Terms page explains how they are chosen. Product
pages and their price order are unchanged.

When no shop has a product in stock, the price box leads with "Compare on
Amazon" and "Search eBay": a tagged Amazon search and an eBay UK search
for new, buy it now listings carrying the eBay Partner Network campaign,
both through `/go/search/` so only a person's click carries the tracking.
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

Track price: on a product a shop has in stock or on pre-order, a "Track
price" button sits next to Save. It opens a form (in place with
JavaScript, by the link's `#track` without it) for a price, delivered, and
how long to keep looking: 1 week, 1 month (the default), 3 months or 6
months. It is the same alert (`StockAlert` with `max_price` and
`watch_days`), confirmed by email the same way, and one address can wait
on 30 products across both kinds. A price that is not below the cheapest
in stock now (at a shop, delivery known) is turned back, since the email
would go at once. `send_stock_alerts` sends it once when a shop or eBay
has the product in stock for that price or less with its delivery known
("Under your price: <product>, £x delivered"). eBay counts at your request
(its listings are new, buy it now, UK sellers with good feedback); Amazon
never does, since its Associates rules forbid price alerts on its prices
(`alerts.NOT_PRICE_ALERTED`). Like the
search alerts, the email names the price and links to the product page,
never the shop. The alert is deleted after that email, or unsent when its
time is up (counted from the confirmation). Insights counts those as
"expired". While no shop has the product, the back-in-stock form shows;
Track price shows beside it only when eBay has it in stock with a known
delivery cost.

## The ⓘ cards

For people new to card games (parents buying a present, say): a small grey ⓘ
after a label opens two plain sentences about it, and nothing on the page
grows. It is the browser's own popover (the `info` tag in
`web/templatetags/ripraptor.py`), so it needs no script: up from the bottom
on a phone like the other sheets, a small card in the middle from tablets
up; a tap outside or Esc closes it. A browser too old for popovers shows no
ⓘ at all rather than one that does nothing.

Where they are, and only there:

- **Product type** on the product page (Booster box, Elite Trainer Box,
  Collector booster box and the rest): what it is and what is in it.
- **Language** on the product page and on a product card's language label
  (Japanese, Korean, Chinese, any other but English): who it is made for
  and that packs often hold fewer cards. Pokémon adds a line: a Japanese or
  Korean booster pack usually has 5 cards, an English one 10.
- **The badge** beside the cheapest price (Lowest in 30 days, Best in 7
  days, Dropping soon): what it means.
- **The price check sheet** says how to read the gauge.
- **Product types on the home page**, on each Browse by product tile, with
  the same words as on the product page.
- **Games**, on each game tile on the home page, beside each game on the
  Games page and beside the game's name on its own page: what the game is,
  then a tip worth remembering about it (`info.game.<game>` and
  `info.game.<game>_tip`, with the game's address in place of `<game>` and
  dashes as underscores). Every game the site knows has both; a test fails
  for a new game without them.

The words are under "Info cards (the ⓘ buttons)" in Site wording
(`info.*`, and `product.fair.how`). Blank one and its ⓘ goes. A game whose
products need their own line can have one: `info.lang.ko_<game>` with the
game's address in place of `<game>` and dashes as underscores, added to the
registry. Each card is written into the page once however many buttons
open it, under an id new for every request, so a list fetched by script
never repeats one. Exact pack and card counts for one product wait for
"What's inside" (not built yet), so no card states one as fact for a
product it has not checked.

## Share on product pages

Next to Save and Track price, "Share" (with the usual share icon) opens a
row of round logos, as a phone's share sheet lays them out (in place with
JavaScript, by the link's `#share` without it; opening one panel closes
the other):

- Reddit: Reddit's own post form (`reddit.com/submit`) with the product's
  link, a title ("<product>: UK prices compared",
  `product.share.reddit_title`) and the post's text filled in, so it reads
  as a post rather than a bare link: "Cheapest UK price right now: £85.00
  delivered, checked Tue 27 Oct, 07:05. The link compares every UK shop's
  price with delivery included." It says plus delivery when the charge is
  not known, pre-order when the cheapest is one, and that no shop has it
  when none does (`product.share.reddit_text*`, `reddit_price*`, in
  `web/share.py`). Like the Discord posts it never names the shop. The
  visitor can change any of it before posting. Product cards' share sheets
  fill in the same text for their own product. A plain link, so it works
  without JavaScript.
- Instagram and TikTok: neither app takes a link from a web page, so their
  logos copy the link and say where to paste it (a story's link sticker, a
  message or a bio).
- Copy link, and More (the phone's own share sheet, where the browser has
  one).

The logos are the official marks from Simple Icons (CC0), in
`web/templates/web/includes/share/`, in each brand's own colour (tokens
`--color-reddit`, `--color-instagram`, `--color-tiktok`), used only here.
Without JavaScript or a clipboard, the page's plain address sits in a box
to copy by hand instead of the copy buttons.

Product cards (game, set and search pages, and Out now on the drops page)
have a share icon under their bookmark. It opens one Share sheet for the
page (`share_dialog.html`, a `<dialog>`: up from the bottom on a phone, a
small box from tablets up) with the same logos, filled by `share.js` with
that card's product. Close, Escape or a tap on the page around it closes
it. Without JavaScript the icon is a link to the product page's Share
panel (`/products/<slug>/#share`).

Every link a logo shares carries `utm_source` (reddit, instagram, tiktok,
copy_link or share_sheet), `utm_medium=social` and
`utm_campaign=product_share`, so Google Analytics shows which network
sends visitors. The page's canonical address stays the plain one, so
search engines see one page. The previews Reddit and messaging apps draw
come from the page's Open Graph tags (name, price and picture).

## Cardmarket market prices

Cardmarket publishes its whole catalogue and a daily price guide as free
downloads (https://www.cardmarket.com/Data/Download). `import_cardmarket`,
daily at 6:44 from cron, reads the sealed products ("non-singles": boxes,
packs, elite trainer boxes, tins, decks) and today's prices for every game
Cardmarket covers that we do (`cardmarket.GAMES`: Pokémon, Magic, Yu-Gi-Oh,
One Piece, Lorcana, Flesh and Blood, Digimon, Dragon Ball Super, Star Wars
Unlimited, Riftbound), and keeps one row a day per product
(`CardmarketPrice`: trend, average and lowest, in euros), so the history is
ours whatever happens to the download. Singles are left out. It also keeps
the European Central Bank's daily pound rate (`ExchangeRate`). A run takes
seconds; a game that fails to download never stops the others.

**Matching.** `match_cardmarket`, every 15 minutes from 7 past on its own
lock (a run still going skips the next; it never waits behind the judge),
asks Claude which
Cardmarket product each of ours is: Pokémon first, then the other games,
products a shop sells now first, and of those the ones viewed most in the
last 7 days, so the pages people look at get their Price check soonest. The site shortlists up to eight
Cardmarket products of a kind that can be ours sharing the set's words
(the box itself, and the near misses: the 18-booster box, the case, the
Pokémon Center edition), and Claude picks one or none
(`catalogue/cardmarket_match.py`). A sure answer is used (Matched by
Claude); a less sure one, or a match whose Cardmarket price is under a
third or over four times ours (more often a pack against a box), waits for
you (Claude was not sure: check it). A product with no candidate is noted
without asking. Admin, Cardmarket matches lists them all with Confirm and
Reject; Claude never asks again about one you answered, and asks again
about any other only when our product or the candidates change.

Speed and cost: picking one of a few shortlisted products is a narrow
question, so it always asks Claude Sonnet 5.5 at low effort
(`MATCH_MODEL`, `MATCH_EFFORT`), whatever the judge uses for Things to
check, six questions at a time (`WORKERS`). It answers in seconds for
roughly half a cent a product. It began on the judge's own Opus at medium
effort, one product at a time, once an hour behind the judge's lock, and had
matched 8 products in its first three hours; now a run gets through up to 600 products, $3 or 13 minutes,
whichever comes first, so the whole catalogue takes hours, not weeks. A run
that Anthropic keeps answering "busy" (12 times) stops and the next carries
on. It uses the judge's key and monthly limit, and spends only while Claude
is switched on in Things to check. Each question is a ClaudeAsk of kind
"Cardmarket match", priced and counted with the judge's, so the monthly
limit covers both. An answer that could not be read (cut off, declined, or
not naming a candidate) is asked again once; after two (`RETRIES`) the
product waits for you in Admin, Cardmarket matches, rather than being asked
every run.

**Seeing how it is going.** The dashboard's Price check coverage part shows,
for each game with a price, how many products Claude has looked at and
matched, how many of Cardmarket's sealed products the morning read holds
for the game and when their prices were last read (or that the game's list
has not been read, or that Cardmarket does not list the game at all), and
under Claude matching Cardmarket what the last run did and what stopped
it ("Asked 312, matched 40, to check 120, none 150, spent $1.60. Reached
the limit for one run."), kept on the judge's row (`ClaudeJudge.market_run_at`,
`market_run_note`). A run stopped by the monthly limit says how much of it
went on Things to check and how much on matching, since the two share it.

What it is not: Cardmarket's prices are European, in euros, before post to
the UK and import costs. They are a reference for the Price check (see
Price check), never shown beside the UK shops' prices or as a price you
can buy at. Cardmarket states no licence for the files beyond publishing
them for everyone, so the site uses them to judge prices and does not
republish the price list.

## Guides and the Fair Price Checker

Plain advice for parents and new collectors, wired to the site's live prices
(marketing's build spec, 27 Oct 2026). Nothing is public until a guide is
published in Admin: before that every Guides page is a 404 for visitors, and
staff see each page as it will look, a draft saying what still stops it.

**Pages.** `/guides/` lists the published guides and holds the checker.
`/guides/<address>/` is a guide. `/guides/<address>/checklist/` is its
printable checklist, laid out for one A4 page (a dozen items fit) with the
site's header and footer left off when printed. `/guides/fair-price/` is the Fair Price Checker. The addresses
`fair-price`, `gift-finder`, `scalper-watch` and the game names are kept for
pages of their own. Once a guide is published, Guides joins the header after
Drops and the footer; the flag is read from the shared cache
(`guides/state.py`), refreshed on every guide save and by `warm_pages`, so no
page gains a query.

**Importing a guide** (Admin, Guides, Import a guide) is how marketing adds and
updates guides, without the Admin form (`guides/importer.py`). They download
the template, a whole example guide as a Word file or plain text
(`guides/template.py`), replace its words, and upload the file: Word
(.docx, which Google Docs downloads too), Markdown or text, or paste it. The
title is Heading 1, each question Heading 2, and labels such as
`Short answer:`, `Good signs:`, `Warning signs:`, `Prices:` (product page
addresses), `What is in the box:`, `Price checker: yes` and `Sources:` (the
page's name, its full https address and the date read, or `none` for advice)
say what each part is; a `Checklist` heading holds the printable checklist.
A guide pasted from a chat with its code block fences (` ``` `) reads the
same, the fences left out. Check shows, before anything is saved, each question and what it carries,
what stops it being saved (the house style below, a missing title, short
answer or source date), what stops it being published, and what is worth a
look (a product address not found, a type not known). Save as a draft or
Save and publish; importing the same web address again replaces that guide,
questions and all, and a published guide stays published only when the new
version passes. A Word file is read paragraph by paragraph from its XML (no
extra library): heading styles, lists and links are kept, deleted tracked
changes left out, and it is refused over 5 MB.

**Logins for marketing.** Every migrate keeps a `Guide editors` group with
just what guides need (add and change guides and their questions, see the
checker's uses, look up products for price cards). Give a login Staff status
and that group, and Admin shows it Guides and nothing else to change. The
owner's own pages (Dashboard, Insights, Things to check with its Claude key,
Crawl health) stay closed to it although it has Staff status, and its admin
home shows only Guides (`guides/access.py`); a superuser is never kept out.

**Writing a guide** (Admin, Guides). A guide is a list of questions in the
reader's words. Each has a short answer, shown first in a quiet panel, then
optional detail, green and red flags (one per line, each with a tick or a
cross and its words), live price cards for chosen products, a "What is in the
box" card (the product type's own ⓘ explainer), the checker, and its sources,
one per line as `title | address | date read`. Detail is plain text: a blank
line starts a paragraph, lines starting `- ` or `1. ` are lists, and
`[words](address)` is a link to a RipRaptor page or an https page that is not
a shop or a marketplace (a shop's price goes in as a price card, so every
price is live and dated and shop links stay marked as sponsored).

The Admin hints ask for the reason behind every piece of advice and every
green or red flag, and for a search title under 50 characters, since
" | RipRaptor" is added after it and Google cuts long titles.

**Checks** (`guides/text.py`). Any save is refused with an em dash, an
exclamation mark, HTML, a stock phrase the site never uses, "guaranteed",
"the best price" or "every shop in the UK", or ETB before Elite Trainer Box
has been written out. Publishing is refused while a question marked as
stating facts has no source, while a "linked from" place has no link words,
or while the next review is missing, not after the last check, or more than
92 days after it. A published guide whose review date has passed stays up:
staff see a note on the page, the Guides list filters it, the admin home
counts it, and the dashboard marks it.

**Live price cards** name no shop and have no buy button: the cheapest
delivered price now at a UK shop we track (in stock first, eBay, Amazon and
doubtful prices left out), how many have it in stock, when the prices were
checked, the stored Price check chip (never High price beside a shop that
pays a commission, as everywhere), Compare prices to the product page, and
Watch the price to its price alert (its back in stock alert when sold out),
the strongest reason for a parent reading in November to come back.
All the cards on a guide cost two queries, however many.

**The Fair Price Checker** (`guides/checker.py`) uses the product pages' own
Price check (`catalogue/fair_price.py`) on the price someone was offered, so
the two never disagree. It shows the cheapest delivered price today at a UK
shop we track and how many have it in stock, the usual price with where it
comes from (our record's average, Cardmarket's European price or the RRP),
and one next step with its reason: buy, paying by card or PayPal so the
money can be claimed back; compare, because stock and delivery times differ;
shop around; or, at twice the usual price or more (`SCALPER_TIMES`), "Scalper
price" with Rip off Rex, what a scalper is, and do not pay it. Every result
offers Watch the price. Sold out at every UK shop we track points to Watch the
price and Drops, never to a marketplace. Without script it is a GET form:
words list products to choose from, then the price is checked; with script
(`checker.js`) the products are offered as you type. Each check is counted by
product and verdict (Admin, Fair Price Checker uses), never the price or
anything about who checked; a robot's or the browser's prefetch is not
counted, and the speculation rules never render it ahead of a tap.

**Coming back.** Every guide ends with why to check again ("Shop prices change
often") before its sources, and its price cards and the checker offer Watch
the price on every result.

**Doors in.** On phones the header has room only for Swipe and Watchlist, so
the home page carries "New to trading cards? Read our guides." under the
search box there. llms.txt, the page AI assistants read, lists each published
guide with its one-line answer, and the checker. Under the price on every
product page, once a guide is published, "New to this? Want to learn more?
Read our guides." (`product.guides.learn`) leads to the Guides page and every
guide on it: the owner asked for it to be for anyone new, not parents only,
and to lead to all the guides as more are written. A guide's "linked from"
can still put its own link words on sold-out product pages (the scams
guide), which then show those instead, and the scams guide's title in the
High price check sheet and under the checker's verdict. One guide per place,
the first published. "Product pages with a price" is no longer a place a
guide is linked from: those pages link to the Guides page.

**Readers.** "Was this useful?" counts Yes and No by guide and day, once per
connection a day (a one-way hash of the address in the cache for 24 hours,
never stored), and files an optional note in Message us as "Guide feedback",
at most 20 an hour. No name or email is asked for. "Spotted a mistake?" links
to Message us. Views of the Guides pages are counted (kind Guides), and taps
from a price card or the checker to a product page are Compare taps
(`guide_card`, `checker`). Whether readers come back on a later day is
counted with the `rr_guide` cookie (see Insights).

**Search and sharing.** Each guide has its own title for search results, an
Article and BreadcrumbList block, a canonical address and a 1200 by 630 share
image (the site's own if none is uploaded). The Guides page, the checker and
each published guide are in the sitemap; checklists and checker results are
not (results carry noindex). Share links: WhatsApp, Copy link (with script)
and the checklist.

**Dashboard.** Once a guide is published, a Guides part shows the last 7
days' guide visits, taps to a product page, visits by earlier guide readers
and prices checked with their verdicts, Yes and No for the last 30 days, and each guide's visits, votes and
whether its review is due.

## Discord

The site's footer links to RipRaptor's Discord server
(`https://discord.gg/5YKc77s4d7`), with Discord's own logo beside
Instagram, Facebook and TikTok. `post_discord`, every ten minutes from
cron, posts news to the channels set up in admin (`catalogue/discord.py`):

- Back in stock: a UK shop has a product in stock again.
- Pre-orders open: a UK shop opens pre-orders.
- Price drop: a product's cheapest delivered price falls below yesterday's
  by at least the channel's smallest drop, and the Price check calls the
  new price Fair price or better. A drop that leaves a price over its
  RRP, the European market or its own usual is a discount on an inflated
  price, not a deal, and a drop with nothing to measure it by (no RRP, no
  Cardmarket match, and under two UK shops or 14 days of our record) is
  not posted either. The post carries the Price check box.
- Out today: a set's release day, from 8am UK time, with its cheapest
  offer.

Nothing the Price check calls High price is posted: a restock or a
pre-order more than 35% over its RRP or the European market, or 15% over
its own usual price, is not shared. The owner, 24 Oct 2026: "ensure that
deals we share on Discord aren't massively overpriced, not just a
pointless discount".

Every post has RipRaptor's look: the brand orange down its side, the logo
beside what happened ("Back in stock", "Pre-orders open", "Price drop",
"Out today"), the product as the title linking to its RipRaptor page, the
price in bold with "Compare every UK shop on RipRaptor", the picture, and
the commission note under the logo. Back in stock and Pre-orders open posts
add boxes side by side, each only when we know it:

- **Price check**: the verdict and what it was measured against ("Above
  average", "Average £45.00 over 20 days", "European market £577.70" or
  "RRP £49.99"), so a dearer restock says so. When only one UK shop lists the product and there is
  no market price, it says there is nothing to compare with.
- **UK shops with it**: how many of the UK shops that list it have it in
  stock now, as "1 of 6". The honest kind of urgency: no shop we read says
  how many units it has, so a post never claims "only 10 left".
- **Out of stock for** (restocks): how long that shop had sold out. Each
  listing now keeps when it last sold out (`Listing.sold_out_at`), copied
  onto the restock (`Restock.out_since`); a shop seen only coming back has
  no box.
- **Restocks in 30 days**, when there has been more than one.
- **Last stock lasted**: how long that shop's previous stock lasted before
  it sold out, when we saw both.

The words are under Discord posts in Site wording (`discord.*`).

Each channel has its own page in admin, under Discord channels: its name
(for you), its webhook address, which of the four updates it gets, which
games (none ticked means every game), which sets (typed by name; chosen,
only their products post, whatever the games, so a "#30th-restocks"
channel follows 30th Celebration alone until you swap the set for the next
one people want; after a swap, the new set's restocks of the last hour
still in stock post too), the smallest price drop (5% to
start), how often it posts (every 10 minutes to once a day: each update at
most that often, with what happened since in one post), how many products
a post holds (1 to 10, 8 to start), and the name and picture shown (empty
uses the name given to the webhook in Discord; the RipRaptor logo is on to
start). A channel can be switched off without losing its settings. Ticking
"Send a test post" and saving posts one hello to that channel. Discord
posts, beside it, lists what has gone out.

The webhook address is typed like a password: it is checked to be a
Discord webhook, never shown again (the page shows only its last four
characters) and kept when the box is left empty. Anyone holding it can
post to the channel; it is in the database, so in its backups. To get one
in Discord: Edit Channel, Integrations, Webhooks, New Webhook, Copy Webhook
URL. Until the first channel is added in admin, a webhook address in
`.env` (`RIPRAPTOR_DISCORD_WEBHOOK`) gets every update with the defaults.

Each post names the product, the delivered price and when it was checked,
and links to RipRaptor's page for it (tagged `utm_source=discord`), never
to the shop: as with the search alert emails, people compare every shop
on the page. eBay and Amazon never count, nor a price kept out of the
comparison, nor a restock that has sold out again by the time the post
would go. Each piece of news posts once to a channel (`DiscordPost`, keyed
by channel), a product at most once a day for each update; news beyond a
post's limit waits for the next. A channel's first run for an update posts
nothing and only notes what is already there, so adding a channel never
floods it with last week. Posts forbid mentions, so a product named
"@everyone" pings nobody. Wording is in Site wording under `discord.*`.
`python manage.py post_discord --test` posts one hello to every channel.

## Refer a friend

A keyring promotion: when someone shares their link and a friend joins through it, both get a free RipRaptor
3D keyring by post. UK only, whilst stocks last, and you can stop it at any time. All its wording is in admin
under Site wording, "Refer a friend keyring offer", with its rules and data use on the Terms page.

**Why the referrer never types in a friend's email address.** Under PECR, an email we send to an address
someone else gave us is our marketing to a person who never agreed to it. So each person gives us only their
own address, and the referrer shares their link themselves: WhatsApp, a text, their own email app (the
"Send by email" button opens it with the message filled in; we send nothing), Copy link, or the phone's share
sheet. Every one works as a plain link without JavaScript, except Copy link and the share sheet, which appear
only when the browser offers them.

This lowers the PECR risk but does not remove it. The ICO treats a business that rewards people for sending
its marketing to friends as the instigator of those messages, and the WhatsApp, text and email buttons fill in
a message we wrote (`refer.share.message`). If you want the lowest risk, shorten that wording to just the link,
or keep only Copy link and the share sheet so people write their own words. That is your call; it is not done
for you.

**The flow.**

1. `/refer/`: the referrer gives their email address and ticks the terms. We email a confirm link.
2. The confirm link (`/refer/confirm/<token>/`) shows their link, `/r/<code>/`, with the share buttons, and
   emails it to them. The same link shows the page again later.
3. The friend opens `/r/<code>/`, reads one line about RipRaptor and the offer, and gives their own address.
   We email them a link to `/refer/claim/<token>/`, a page with a Claim my keyring button (a button, so a
   mail scanner opening the link cannot claim).
4. Pressing it completes the referral: two keyrings are taken from stock and you are told by push (ntfy) and
   by email to `RIPRAPTOR_CONTACT_EMAIL`. Neither names a person or an email address. Unless it is held for
   review, both people are emailed a private link to an address form (`/refer/address/<token>/`, 30 days).
5. The form takes name, address lines, town and a UK postcode, and nothing else. You see sent addresses in
   admin under **Keyrings to post** (filter "To post", which leaves out cancelled referrals); tick them and
   choose **Mark as posted**. The admin home page has a **Refer a friend** card with the number to post and
   the number held for review, each linking to its list. A row marked **Check first** is a second address at
   a postcode that has already had a keyring (a flat number is easy to add): make sure it is another
   household before posting, and cancel the referral if not.

**Your controls** (admin, **Refer a friend settings**): the on/off switch, **Keyrings left** (each completed
referral takes two, a cancelled one gives them back), an optional **Last day** and **Keyrings per person**
(default 1; the Terms wording says one, so change it too if you raise this). Off ends the offer at once:
`/refer/` and every `/r/` link say "This offer has ended.", confirm and claim links stop, and so do address
links. Out of keyrings or past the last day stops new people only; anyone already promised a keyring can
still send their address. Under **Referrals**, **Approve** sends the address emails for a referral held for
review and **Cancel** stops it (no email to anyone, keyrings back to stock, its links stop). The footer and
the watchlist page show "Refer a friend, get a free keyring" only while new people can join.

**Win a keyring (home page).** While the offer is advertised, the home page has a section under the orange Prices
dropped panel: "Win a free RipRaptor keyring today", a line naming a winner ("Matt from Portsmouth has already won
one.", at the owner's request, 30 Oct 2026; name only people who agreed, and empty the line in Site wording to hide
it), "Another 9 keyrings left to win." and how to get one, with Get my link to `/refer/`. The count is **Keyrings
left**, kept in the cached offer flag with the rest (`referrals.keyrings_left`), so it follows every keyring given
or given back and the home page gains no query. Its wording is under Home page (`home.keyring.*`). The section and
the refer a friend pages show the owner's photo of the real keyring (`web/static/img/keyring-photo.webp` and
`.jpg`, 900 by 600, re-encoded without the phone's EXIF, which held its GPS position); the small card keeps the
drawn one.

**The refer a friend card.** While the offer is advertised, a small card ("Raptors save in packs.", the keyring
picture, Get my link and No thanks) now and then invites visitors to refer a friend
(`web/static/js/refer-card.js`, `web/templates/web/includes/refer_card.html`). It never shows on a browser's
first page, then on about one page in five, at most once a day, after 8 seconds or once the visitor has scrolled
halfway. It waits while the analytics or home screen card is up, and the home screen card waits for it. No
thanks hides it for good in that browser, and so does reaching the "Check your inbox" or share page; Escape
hides it for that page only. It never shows on `/refer/`, `/r/` links, the private links, product pages (on a phone it
would cover the buy box), the swipe page, the contact, list your shop and alert forms, admin or error pages (`NO_REFER_CARD` in
`web/context_processors.py`), and never without JavaScript. It reads the cached offer flag only, so no page
gains a query, and the script makes no network calls. Its browser keys are `ripraptor.refer` (done),
`ripraptor.refer.pages` and `ripraptor.refer.shown`. The keyring picture (`web/static/img/keyring.webp`, with
`keyring.png` as the fallback) is cut from the owner's render; the two heads (orange RipRaptor leading, a friend
in ink behind) are the `.mark` mask twice, sized by the `--pack-*` tokens. In the card the keyring sits on a
soft orange panel so its white base reads on the white card, and below 360px wide the card drops the picture to
stay small.

**Rules against abuse.**

- One keyring per email address ever, and one per household (postcode with the house and flat numbers from
  both address lines; a second address at the same postcode is marked Check first rather than refused). Email
  addresses are compared as people: anything after a `+` is ignored, and for Gmail the dots too. These are
  kept as one-way fingerprints (`PromoMark`) after the addresses themselves are deleted.
- The friend must not be the referrer and must be new to the offer (not a confirmed referrer, not someone
  who has had a keyring). A friend's referral stays with the first referrer whose link they used: typing their
  address into a second link sends nothing, so nobody can take another person's credit.
- Held for review instead of sent: the friend on the same internet address as the referrer (with or
  without the same browser), another completed claim from the friend's internet address in 30 days, or three
  or more confirmed referrers on one internet address, or more than 3 referrals completed across the site in an
  hour (so a script using fresh addresses cannot empty the stock unseen). IPv6 addresses count by their /64 block. Connections
  are stored only as one-way hashes, deleted with the referral.
- No form says whether an address is known: every address gets the same "Check your inbox", and an address
  that cannot take part is simply sent nothing.
- Limits, counted in the shared cache under one-way hashes: 10 form posts an hour and 30 a day per internet
  address, 3 confirmation emails a day per person, one per 10 minutes, and 300 confirmation emails an hour
  for the whole site. When that site cap trips, real sign-ups get no email either, so you are told by push and
  email, once an hour. 20 wrong codes or tokens an hour from one internet address and the next answer is a
  429 without touching the database.
- Every form has the CSRF token and the hidden robot field. Posts to `/refer/` and `/r/` over 4 KB are
  refused before Django reads them (`web.middleware.SmallFormMiddleware`). Codes are 11 random characters
  and tokens 32 (`secrets.token_urlsafe`), looked up by unique index. Emails hold no text anyone typed.
  `/refer/` and `/r/` are noindex and in robots.txt; the private links carry no analytics, Awin or AdSense
  script, and `/r/` pages count as private too so a code is never sent to Google.

**Floods.** The checks above make a flood cheap: a robot post costs a cache count and no database write or
email. Posts are checked against the per-address limit before the database is read. Real network-level protection needs a front proxy. Stock Caddy has no rate limit directive (it needs
the caddy-ratelimit plugin), so none is added to `deploy/`. Cloudflare's free plan in front of the site
(proxied DNS, with a rate limiting rule on POSTs to `/refer/*` and `/r/*`) is the simplest step if the offer
is attacked.

**Deleting details.** `tidy_referrals` runs hourly (cron at 55 past, its own lock). It sends address emails
that failed to go, cancels a held referral not approved in 30 days, and deletes unconfirmed sign-ups after 14
days, links that brought no completed referral 90 days after confirming, unused address links 14 days after
they expire, names and addresses 60 days after posting (or 90 days after they were sent if never marked
posted), and cancelled referrals after 14 days; a cancelled referral's postal address is cleared at once.
Every keyring promised but never posted goes back to **Keyrings left** when its claim is deleted, by tidy,
by Cancel, by a person deleting their details or by deleting in admin, so the count matches the drawer. Once
a day, while any address has waited over 14 days to be posted, you are reminded by push and email. It also
restores the footer link after the cache is emptied (a deploy, or the weekly delivery check). Every email carries a "Delete my details" link to
a page with a button that deletes that person's details at once.

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

**Listing reports.** Each shop's row in a product page's price list has a
quiet Report link. It opens a form under the list (`?report=<listing>`,
no script needed) with that shop named: a reason (a different product,
another language, wrong quantity or box size, wrong price, wrong stock,
not sealed or not new, something else) and an optional note. No name or
email is asked for. It posts to `/products/<slug>/report/` and lands here
as a conversation named "Listing report", with the product, the shop and
its address, the price shown, and the listing's admin link
(`inbox/service.py report`). The same listing and reason twice in an hour
is kept once, the hidden field keeps robots out, and over 20 reports an
hour are refused ("try again in an hour"), so Message us is never filled
by them. A report changes nothing on its own: you decide in admin.

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

## List your shop

`/list-your-shop/` (footer, How it works page and the sitemap) lets a UK
shop ask to be listed: their name, the shop's website, an email address
and an optional message of up to 1,000 characters. It works without
JavaScript; a sent form redirects to `/list-your-shop/thanks/` (noindex),
so a reload sends nothing twice. The website is tidied (https assumed when
no scheme is typed, only http and https, a real domain name, no password
or odd port, lower-cased host) and stored as text. It is never fetched:
a shop is read only once you add it.

Enquiries are in admin under Messages -> Shop enquiries, with a
Waiting/Handled filter, search and a "Mark handled" action; the admin home
shows how many wait. Each new one is emailed to you as plain text (to
`RIPRAPTOR_CONTACT_EMAIL` when set, else `RIPRAPTOR_INBOX_NOTIFY_EMAIL`)
with a link to admin, and the ntfy push says only that a shop asked.
Nothing is ever emailed to the address typed into the form, so the form
cannot be used to mail a stranger; answer from your own email.

Limits (`inbox/shops.py`): 5 enquiries an hour and 20 a day from one
connection (counted in the shared cache under a one-way hash, never the
address itself), 30 an hour site-wide, 8 KB a request, and a hidden
honeypot field. A shop domain (without www) with an open enquiry from the
last 30 days gets the same thanks and nothing is stored or sent, so the
page never says what it knows. `tidy_all` (hourly) deletes enquiries a
year after they are marked handled, or two years after they arrived if
they never are. The Terms page's data section says so.

## The RipRaptor badge

Listed shops can show a "Listed on RipRaptor" badge on their own sites, so
their sites link to RipRaptor: links from other sites are how search
engines judge a new site. The List your shop page has it at
`/list-your-shop/#badge`, in a light and a dark version, each with the
code to paste and a Copy code button (with JavaScript off the code sits in
a box to select). The code is one plain link to the home page around the
image, which is served at its own lasting address
(`/static/img/badge/listed-on-ripraptor.png` and `-dark.png`, not the
hashed copy), so a badge redrawn later reaches every shop that pasted it.
Taking it is up to the shop and never changes its listing: a link given in
exchange for a listing is one search engines discount, and the page and
the email both say so.

The Offer the RipRaptor badge draft in Shop outreach links to that part of
the page. It gives the number of shoppers sent in the last 3 months only
when there were some (`outreach.sent.*`), so a new shop is not told nought.
Badge visits show on the Insights page under Other sites, by the shop's
address. The wording is under List your shop (`listshop.badge.*`); the
words on the images themselves are drawn in. To redraw the images after
the logo, the font or the brand colours change, run `python scripts/make_badges.py`
(needs `pip install playwright`).

## Shop outreach

Admin -> Messages -> Shop outreach is where you write to the shops
RipRaptor lists: to ask for an affiliate link, to tell them how many
shoppers you sent them, to ask for a product feed, or to offer the
RipRaptor badge (below). Nothing is sent from
the site. Each draft opens in your own mail app (a `mailto:` link), so it
goes from your Zoho address and the replies come back there. ZeptoMail is
for the automatic alert emails only and is never used for this.

Every shop except eBay and Amazon has a row, made when the page opens
(`inbox/outreach.py ensure_contacts`). The list puts the shops that get
the most shoppers from you first, with where things stand (Not contacted,
Emailed, Replied, Affiliate link live, Not interested, Do not email), when
to follow up and when you last wrote. The admin home says how many shops
are due a follow-up: emailed, no reply marked, and the follow-up date
reached.

Open a shop to see its figures and four drafts filled in with them:
shoppers sent in the last 30 days, the last 3 months and in all (people's
clicks only, never robots or direct hits), the products they clicked most,
and how many of the shop's products show on the site now and how many are
in stock (listings checked recently and not kept out, as the home page
count has them). Type in the shop's business address (from their website)
and save, press Open in your mail app, send it, then press Mark as sent.
That saves the page too, logs the email under Emails you sent, moves a
shop not contacted yet to Emailed (Replied and Affiliate link live stay as
they are) and sets the follow-up to two weeks on unless you set a later
one. Change the status by hand when they reply.

The wording is in Site wording under Shop outreach emails
(`outreach.*`). Every draft ends with the sign-off and a line saying to
reply STOP for no more, which UK rules expect for sole traders. When a shop
asks, set it to Do not email: the page then shows no drafts and Mark as
sent records nothing. Rows cannot be deleted, so that answer is kept.
Only the address you type is stored, and only to write to them.

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
robots.txt. Its Buy clicks are saved with the page "watchlist" (see
Clicks to shops), and each product on a loaded list is counted as a
"watchlist row" page view by slug, so Insights shows clicks from
watchlists and the stock watcher can put watched products first.

### Alert me (search alerts)

Below the list, while email is set up, the watchlist page has an Alert me
form: a game (required), and optionally a product type, a language
(English is suggested) and the most the visitor will pay delivered
(£0.50 to £5,000, "£4.50" and "4.5" both read), plus an email address. It
reads the alert back in plain English, for example "English Pokémon
booster packs up to £5.00 delivered, in stock". It posts to
`/watchlist/alert/` (`web:search_alert_ask`). Without JavaScript a good
request goes back to the watchlist page (the saved list kept), which says
"Check your inbox" with the alert read back; a mistake shows the page
again at `/watchlist/alert/#alert` with the field marked and in view
(`watch.js` only rewrites the list's address on `/watchlist/` itself,
never on that POST-only address). With JavaScript (`web/static/js/search-alert.js`)
the form is sent in place and the answer replaces it. Search, game and set
pages with a game chosen link to it ("Email me when products like these
are in stock"), filled in with their game, type, language and price band
(the band's top as the price). The alert can be wider than the list (it
has no set or search words), so the link does not promise these exact
products; with no language filter it passes `lang=` so the form keeps Any
language.

It works like the drop reminders (`catalogue/search_alerts.py`, models
`SearchAlert` and `SearchAlertSent`): nothing is sent until the confirm
link (`/alerts/confirm/search/<token>/`) is pressed, every email has a
stop link ("Stop this alert", `/alerts/stop/search/<token>/`, also the
List-Unsubscribe header) that deletes that alert at once (the address goes
with it unless another alert uses it, and the emails and stop page say
so), unconfirmed alerts go after a
week and confirmed ones six months after they were confirmed. The answer
is the same "Check your inbox" whether the address is new, already has
the alert or is at its limit; an address can have 10 confirmed alerts
(checked again when a confirm link is pressed, so requests made over
several days cannot pass it) and
is sent at most 3 confirmations a day, and one connection can ask 10
times an hour, counted under one-way hashes in the cache. The honeypot
field and CSRF token are as on the other forms.

`send_search_alerts` runs from cron every 15 minutes (4 past, its own
lock). A product matches when it is active, of the alert's game, type and
language (the browse filter's language rules), and a shop on the site, not
eBay or Amazon, has it in stock now at a current, counted price (the same
listings as the site's main price, as the back-in-stock alerts count
them). With a price, the cheapest known delivered price at such a shop
must be at or under it: a shop whose delivery charge is unknown cannot
prove that. Alerts with the same game, type and language share one
query. Each alert gets one email per run with its new matches, up to six,
cheapest first, and "And N more" linking to the in-stock list on the site
cheapest first. A product is emailed again only once it stopped matching
(sold out or over the price) and came back, and not within 7 days of the
last time. Stopped matching needs a current shop listing that says so: a
product whose listings only went unchecked (a shop's reader failing) is
not sent again when they are read again. Each email is claimed before it is sent and handed back if the
send fails, so two runs at once never send it twice.

The emails never name a shop: no shop name, no shop or `/go/` link, in
the subject, preview line, text or HTML. Each product links to its page
on RipRaptor ("See prices") with its delivered price and when it was
checked. Its picture is shown only when it is stored on RipRaptor
(`Product.image`, uploaded in admin) and its file name names no shop. A
shop's picture is never used, even on a shared CDN: Shopify folders and
BigCommerce store ids belong to one shop each, so the address gives the
shop away. Feed pictures are not stored here yet, so for now emails carry
no product pictures. Search alerts are listed read only in admin with what
each was sent. On the 20 Oct 2026 snapshot (after `set_languages`, prices
moved forward to be current) English Pokémon booster packs up to £5
matched 1 product of 87 English packs in stock at a shop (87 with no
price), Magic booster boxes up to £120 matched 7 and One Piece booster
boxes up to £80 matched 4; 204 alerts in 110 groups sent 133 emails in
about a second, and a run with nothing new took 0.6s. Alerts send whatever
type a product has, so a wrong type in the catalogue (a binder page
display filed as a booster box) reaches inboxes: fix it in admin.

### Your alerts

One place to see and stop every email alert an address has, with no
account and no password (`catalogue/manage_alerts.py`). While email is set
up, the watchlist page links to it under its title ("See, change or stop
your email alerts") and has a Your alerts box above Alert me: an email
address and "Email me a link". It posts to `/watchlist/manage/`
(`web:manage_ask`); without JavaScript the visitor goes back to the
watchlist page (the saved list kept) at `#manage`, with JavaScript
(`search-alert.js`, the same script as Alert me) the answer replaces the
box. The answer is always "If that address has alerts, we have emailed a
link to manage them": an address with any alert (back in stock, Track
price, a drop reminder or a search alert, confirmed or not) is emailed the
link (`web/templates/web/email/manage_link.*`, subject "Your RipRaptor
alerts"), any other address is sent nothing, and a failed send is logged,
not shown. One connection can ask 10 times an hour ("Too many links were
asked for from here just now") and one address is sent at most 3 links a
day, counted for every address typed, under one-way hashes in the cache
like the other sign-ups. The honeypot field and CSRF token are as on the
other forms.

The link is `/alerts/manage/<token>/` (`web:alerts_manage`): a signed
token (`django.core.signing`) carrying the address, lower-cased. Nothing
new is stored, so a link cannot be cancelled; it expires. One from the box
works for an hour. Every alert email (confirmations, back in stock, under
your price, pre-orders open, drop reminders, search alert emails) carries
one more small link at its foot, "Manage all your alerts", that works for
a week. The two are signed under different salts, so an hour's link is
never read as a week's. The page lists the address's alerts in four
groups: Back in stock (the product, linked), Track price (the product,
"At or under £X" and, once confirmed, "Until <date>": the time chosen or
the six-month limit), Drop reminders (the set, its game and its date as
the drops page words it) and Search alerts (the alert read back as on the
watchlist page, linked to that list). Unconfirmed ones say "Waiting for
you to confirm". Each Track price alert has Change (a native `<details>`,
so it works without JavaScript): a new price, delivered, and how long to
keep looking (1 week, 1 month, 3 months, 6 months), from today. It is
refused, opening again with the reason, for a price the form never takes
or one at or over the cheapest in stock now (the alert would be sent at
once). A change confirms an alert still waiting, since the link was sent
to the address as the confirm link was, and its six months start again
(`manage_alerts.change`). Each row has a Stop button and the top has Stop all:
plain forms that POST to the same address with the CSRF token, delete an
alert only when its address is the link's (whatever id is sent), and
come back with "Alert stopped and deleted." or "All your alerts are
stopped and deleted." An expired or altered link shows "This link has
expired" with the box for a new one. The page is a private link like the
confirm and stop links: no analytics, Awin or AdSense script, the
referrer cut to the site's address, `noindex, nofollow` (meta tag and
`X-Robots-Tag`), `Cache-Control: no-store`, and `/alerts/` is disallowed
in robots.txt. Three queries load every alert, however many there are.

The token is signed, not encrypted: anyone holding a link can read the
address in it, as they can read the page. It travels in the email and in
the page's own address. Caddy and gunicorn as set up in `deploy/` keep no
access log; one turned on would record addresses, these included. The
alert counts (`DailyPageView`, kind "alerts") gain
`manage_asked`, `manage_sent`, `manage_stopped` and `manage_stopped_all`.
The Terms page's data section says the link exists.

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

## Picked for you

The first row under the home page's game tiles, for anyone who has viewed,
saved or searched for something. The same script sends the viewed and saved
slugs and up to three recent searches to `/api/picks/?p=slug,slug&s=a|b` as
the page loads, and shows the row it gets back; with nothing known, or with
JavaScript off, the row stays hidden. The server (`catalogue/picks.py`)
ranks up to 8 in-stock products from the same set, or the same game and
kind, never one already viewed or saved: deals first (a confirmed saving of
at least 15% on the next shop, or a verified 30-day low), then the closest match (same set, same language), then the most
widely stocked. It keeps nothing about the visitor, and the request is never
cached.

Each pick has one label saying why it is worth a look, the first of these
that is true (`picks.tag`, `web/templates/web/includes/pick_tag.html`): in
green, price evidence, **Lowest in 30 days** or **Best in 7 days** (our own
daily price record, as on product cards) or **23% under the next shop** (a
saving of 15% or more on a next shop whose price is in line with the
market); in grey, plain facts, **New release** (out in the last 30 days) or
**In stock at 6 shops**. The owner, 30 Oct 2026: "Too good to miss" on
every deal said nothing. The wording is under Home page in Site wording
(`home.picks.*`). The In stock instead rows use the same labels.

**In stock instead** (`picks.instead`). A product page nobody can sell now,
and a released set page with nothing in stock or on pre-order, show up to 4
of the closest products shops do have in stock, right under the buy box or
above the set's list: the same set and language first, then the same set,
then the same game and kind in the same language; among those, deals with
evidence first, then the newest. The same ranking as Picked for you with
closeness put first, so someone hunting a sold-out set has somewhere to go.
A tap in the row counts as `instead` among the Compare taps.

## Google Analytics

Google Analytics 4 (`RIPRAPTOR_GA_ID`) counts visits, pages, searches (the
`q` in search addresses), every press on a link to a shop (an
`affiliate_click` event with `button`, `retailer`, `product`, `game` and
`page_type`) and every press on a Compare prices link (`compare_prices`,
with `button` and `page_type`). Every link to a shop goes through our
`/go/` address, so Analytics cannot see it leave the site as an outbound
click by itself; the event says so instead. To report on its details,
register `button`, `retailer`, `product`, `game` and `page_type` as
event-scoped custom dimensions (Admin, Custom definitions). UK law needs a visitor's permission before analytics cookies are
set, so a small card asks: Allow analytics or No analytics, equally plain.
Until a visitor allows it no Google Analytics script loads and nothing is
stored but the answer, which is kept in the browser (`ripraptor.analytics`)
and asked again after six months. The swipe page fills a phone's screen,
so there the card waits for the next page. Analytics cookies at the foot
of the site's pages shows the card again. No analytics deletes Google's
cookies, and a page whose tag is already running reloads without it, as
do the visitor's other open tabs and a page kept for the Back button.
Google signals and advertising features are off.

Message threads and stock alert links (`/contact/c/`, `/alerts/confirm/`,
`/alerts/stop/`, `/alerts/manage/`, matched by address in `web/context_processors.py` and in
`analytics.js`, so a mistyped one is covered too) are private: no Google
Analytics, Awin or AdSense script loads there, and leaving one passes on
only the site's address as the referrer. Without JavaScript there is no
card and no analytics. The Terms page explains it in its data section
while it is set up.

In Google Analytics, keep data retention at 14 months or less (Admin,
Data retention): the Terms page says Google keeps the detailed records
for at most 14 months. To count clicks to shops as conversions, mark
`affiliate_click` as a key event (Admin, Events).

`python scripts/analytics_check.py` checks all of this in Chromium against
a dev server on port 8000 started with `RIPRAPTOR_GA_ID=G-TEST123` and
demo data. Google is never reached.

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

## Admin app

The admin is a home screen app of its own, separate from the public one.
`/admin/manifest.webmanifest` (a view, `admin_manifest`) names it "RipRaptor
admin", gives it the raptor on a dark icon (`admin-icon-*.png`), starts it on
the admin home and keeps it inside `/admin/`, so a link to a public page
opens in the browser. A long press on the icon offers four shortcuts: Things
to check, Messages (unread), Crawl health and Insights. Every admin page,
the login page included, links the manifest through
`templates/admin/base_site.html` (the project templates folder, searched before
the apps so it replaces Django's own). The admin home has an Admin app box
with an Add to home screen button where Chrome offers one, and the steps for
an iPhone; it hides once the app is opened from its icon.

The sign-in renews each time the admin is used (`SESSION_SAVE_EVERY_REQUEST`),
so the app stays signed in while it is opened at least once a fortnight.
Public pages never start a session, so this writes nothing for visitors.

## Admin sign-in security

**Two-step login.** Admin and the dashboard take the password and a 6-digit
code from an authenticator app (Google Authenticator, Microsoft
Authenticator or any app that takes a setup key), so a stolen or guessed
password is not enough on its own (`twostep/`). The codes are the standard
kind (RFC 6238, 30 seconds each), worked out with Python's own library: no
package and no paid service.

- *Setting it up.* The first sign-in after this update goes to
  Admin, Two-step login (`/admin/two-step/`). On the phone, tap **Add to
  authenticator app**, or type the key shown into the app; then type the
  app's code. Eight backup codes are shown once: write them down. Each gets
  you in once if the phone is lost.
- *Every staff login* sets it up the same way, a Guide editors login
  included. Until it has, it gets no further than the set-up page, and on
  public pages it is a visitor.
- *Signed in before the update?* That sign-in counts once two-step is set up
  only if it gave a code, so the admin app asks you to sign in again, with
  the code. It then stays signed in as before.
- *A new phone:* Two-step login, **Set up a new phone**. The old phone keeps
  working until the new one gives its first code. **Make new backup codes**
  replaces the old set.
- *Lost phone and no backup codes:* on the server console,
  ```sh
  cd /srv/ripraptor && sudo -u ripraptor bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py reset_two_step YOURNAME'
  ```
  The next sign-in takes the password alone and goes straight to setting up
  a new app. A superuser can do the same for another staff login by
  deleting its row under Admin, Two-step logins.
- Each code works once. The secret and the backup codes (kept hashed) are in
  the database, so a backup holds them; the password is still needed.
- `RIPRAPTOR_TWO_STEP=0` switches it off; it is off on a development machine
  (`DJANGO_DEBUG` on) unless set to 1.

**Wrong tries.** Ten wrong sign-ins (password or code) from one address in
15 minutes and that address is turned away for 15 minutes without its
password being checked (`twostep/lockout.py`). A right sign-in clears the
count. It counts addresses, not usernames, so nobody can lock you out by
typing your name; the address is the one Caddy adds last to
`X-Forwarded-For`.

**HTTPS only.** On the server (`DJANGO_DEBUG=0`) every page tells browsers
to use https for ripraptor.com for a year (HSTS, `SECURE_HSTS_SECONDS`),
so nobody between a visitor and the site can serve a plain http copy
first. Subdomains and the browsers' preload list are left out, so it is
easy to undo while the site is young.

**Hacking tools' searches.** Scanners search for markup and code
(`"><svg onload=...>`, `{{7*7}}` and the like) to see whether the site
prints it back as code. It never does: every page prints a search as
plain text. These searches are still recorded but left out of the search
lists on Insights and the dashboard (`PROBE_SEARCH` in
`catalogue/insights.py`), where they pushed out the real ones.

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
admin under "Old product addresses". Listings, price history, restocks,
pre-order openings, clicks, shop pages, linked shop rows and stock alerts
move to the kept product. Every merge it makes is the undoable kind: the
other product is switched off rather than deleted, and each pair is listed
under Sorted for you on Things to check ("The hourly tidy merged them")
with an Undo that brings it back with its listings, history and address. A
pair whose merge anyone undid is never merged by the hourly run again, and
nor is a pair a shop shows both of (two products, to that shop), or whose
merge would take a shop's shown price off the site; those are left for
Things to check. No shop's listing is ever deleted. `tidy_catalogue`
merges the old way when it tidies a name onto another product's. A count
with and without its noun ("Display (10 Bundles)" and "Display (10)")
counts as the same name; a name
with nothing but the kind of product left ("Scarlet & Violet Elite Trainer
Box") is never merged. A Simplified Chinese set code counts however a shop
spells it ("csv7C", "csv7 C", "CSV9.5 C", "CBB2 C"), and "Simplified" and
"Vol" never change which product it is ("Gem Pack Vol. 2" and "Gem 2"), so
one Chinese box at two shops is one page. The code itself still counts
("Storming Emergence csm1b" and "csm1c" are two boxes), and so does the
size (slim and jumbo boxes stay apart), except a Gem Pack's code, which
only says its volume again ("Gem Pack Vol. 4 CBB4 C", "Gem Series 4" and
"Gem 4" are one box).

Words shops add or drop at will count for nothing: "Booster Display" (or
"Booster Display Box") for a booster box, but not a display of tins,
bundles, decks or Elite Trainer Boxes; a plural ("Commander Decks", "House
of Horrors"); "II" and "Two" for 2; the game's name in words ("League of
Legends", "Star Wars Unlimited", "Dungeons & Dragons", "Fusion World", "CG");
and language words, since products are only ever merged within one
language. For Pokémon also "Booster Bundle" for the bundle, "High Class"
and a set's "ex" on booster products, "PC" for Pokémon Center, and the
"Mega Evolution" series. Set codes count in one spelling ("OP-09", "OP 09"
and "OP09"; "PRB-01" and "PRB01"; "ME02"; Pokémon's Japanese "M1L", "M2a",
"S6a"). A code drops out of the comparison when other words name the
product ("OP-09 Emperors in the New World" and "Emperors in the New World"),
and stays when nothing else does ("OP-09 Booster Box", "Premium Booster
PRB-01"). Magic's "M20" and "M21" stay codes.

Shop wording that once kept one product on several pages
(`matching.chosen_option`): a shop system's "(Default)" or "Default Title"
goes, and a page that sells several artworks and names the one chosen in
brackets is that one only ("30th Celebration Sylveon / Greninja Ex Tin
(Greninja)" is the Greninja tin; "Paldea Collection Sprigatito / Fuecoco /
Quaxly (Sprigatito)" the Sprigatito one). The hourly tidy applies both to
names already in the catalogue too. For Pokémon, "30th Anniversary" is the
30th Celebration set. A Pokémon name that differs from another in its set
only by "ex" ("30th Celebration Greninja Tin" and "Greninja ex Tin") is
offered as a possible duplicate (the `set_ex` key), for the owner or Claude,
never merged by the rules alone.

Shop notes never name a product (`matching.without_notes`, checked against
the live site on 24 Oct 2026): a purchase limit ("*1pp limit*", "WEBSTORE 1
PER CUSTOMER", "MAX 10 PER CUSTOMER"), a release or restock note ("(Releases
27.03.26)", "February Restock", "Final Drop", "Wave 2"), "(Normal)" beside a
damaged variant, "(1 Box)", and a booster pack's card count ("(12 Cards)";
only a plain booster pack's, so "Mega Pack (18 Cards)" stays apart from its
tin). The hourly tidy takes the notes out of names already shown too.
Lorcana's set number goes ("Fabled (Set 9)", "Fabled (Chapter 9)"), and a
set number is never read as a "box set", so "Fabled Booster Box (Set 9)" is
a booster box. For Pokémon, "Mega Evolution 2 Phantasmal Flames" is ME02
(not when a pack count follows: "Mega Evolution 2 Pack Blister"), a
"Checklane Blister" is a "Checklane", a "3 Pack Blister" a "Triple Blister",
a "Booster Pack Bundle" the Booster Bundle, whose six packs are what it
holds; "Full Booster Box" is the booster box, and "Centre" is "Center"
everywhere.

The hourly merge only joins names whose numbers agree. A volume or set
number is never read as a pack count ("Gem Pack Vol 4 Booster Pack" and
"Vol 6" are two products), and two names with different numbers ("Mystery
Bundle 5 Sealed Booster Packs" and "10"), different booster kinds ("Set
Booster Box" and "Booster Box"), different set codes ("M1L" and "M1S"),
series ("Scarlet & Violet Base Set" and "Mega Evolution Base Set") or a word
the hourly tidy reads as another product on one side only ("Collector's
Edition", "Case", "Damaged") are left apart, with a "left:" line in the
command's output, and so is a pair the
owner said are not the same (Not the same on Things to check, or Not the
same as in his spreadsheet). What a box holds ("Booster Box (36 Packs)",
"Booster Box (36)", but not a half box's "(18)") and series or set codes
("SV8.5", "OP-10") do not count as numbers. Of a group, the product kept is
the one with most shops, then the one whose name says its language in words
("Gem 4 Simplified Chinese Booster Box" rather than "Gem Pack Vol. 4
Booster Box"), then one with a picture, then the shortest name.

```sh
python manage.py merge_duplicates --loose --dry-run
```

also ignores filler words ("Exclusive", "English", "TCG") and, for
Pokémon, series names, so "Pitch Black Pokémon Center Elite Trainer Box
(Exclusive)" meets "Mega Evolution Pitch Black Pokemon Center Elite Trainer
Box". For Pokémon it also pairs a Chinese box with the same box named
without its code, a box called only "Chinese" with the Simplified one, and
two boxes of one code and size whose shops translate the set name
differently ("Pursue Glory Together" and "Chasing Glory Together csv10").
Things to check shows these pairs, and more (see "Possible duplicates"),
for a yes or no. It never runs on its own: read the dry run, and run it without
`--dry-run` only when every group is right.

A merge made from the owner's spreadsheet (Same as product id, see "Your
data in Excel") is the undoable kind: the other product is switched off
rather than deleted, its address redirects the same way, and it is listed
under Sorted for you on Things to check with an Undo that brings it back.

## Search engines and AI crawlers

Every page carries a canonical link, Open Graph and Twitter sharing tags,
and structured data: WebSite with site search and Organization on the
home page; on product pages Product with its offers (AggregateOffer:
lowest and highest price, number of offers, stock), a BreadcrumbList and
an ItemPage whose `mainEntity` is that Product (linked by `@id`, the
page's address plus `#product`, `#breadcrumb` and `#webpage`); on game
and set pages a BreadcrumbList and, on the canonical view only (first
page of the tab the page opens on, no filters), a CollectionPage whose
`mainEntity` is an ItemList of the products that view shows. Other tabs,
pages and filters share the canonical address, so they carry only the
breadcrumbs. Product titles lead with the lowest delivered price.
Descriptions say "cheapest today" only when that price is in stock; when
the cheapest is a pre-order they say so and name the cheapest shop with
it in stock, if any. Descriptions are cut to 160 characters at a whole
sentence or word (`web/hubs.py`).

Game and set pages are hubs. Their titles, descriptions and the line
under the heading are worked out from the catalogue on each visit
(`web/hubs.py`, three queries however big the game): how many products,
how many a UK shop has in stock now, how many shops have a current price
(a shop whose only price is stale is not counted) and the cheapest
delivered price in stock, with that product linked. Titles name the
product types in stock in the game's own words (`PLURALS` in
`catalogue/types.py`), as many as fit in about 60 characters; when even
one does not fit, a short form ("Cheapest {game} {types} in stock UK",
"{set} {types}: UK prices") is used. Nothing says "cheapest" or "in
stock" unless a shop has it in stock now: a hub with nothing in stock, a
set still to come (its release date, and pre-orders only when a shop
takes them) and a game no shop lists yet each get plainer wording. With
nothing in stock the line says how many are on pre-order and at how many
shops ("1 sealed product, on pre-order at 2 UK shops."), and for a set still
to come with no date it ends "Release date not announced yet.". All of it is in Site
wording under "Page titles and search engine descriptions" and "Search
results and browsing" (`browse.hub.*`). Set pages link back to their
game under the heading as well as in the breadcrumb.

Set codes are what searchers type ("eb05 booster box uk", "op18 booster
box uk"), so titles carry them. A set whose code has letters and digits
(OP-18, EB05, FB11, BT28, SV8.5; not FIN or PRE, which are not searched
and can be ordinary words) is named "{set} ({code})" in its page's title
and description (`meta.set.name_code`): "{set} ({code}) booster boxes UK:
cheapest prices" while something is in stock, "{set} ({code}) pre-orders
UK: release date and prices" while shops take pre-orders before the
release date, "{set} ({code}) release date" when no shop does. A title
keeps the code ahead of a longer wording or more product types, and drops
the code before the set's name; when even the shortest wording runs past
60 characters without the code, the code stays. The line under the
heading of a set still to come gives its release date and how many shops
take pre-orders, or how many list it when none takes pre-orders yet
(`browse.hub.upcoming_listed`); "prices appear once shops list it" is
said only when no shop lists anything. Product titles and descriptions use the product's name
with its set's code put in when the name does not give it in any spelling
(`OP18`, `op 18`, `OP-18`, `EB-5` for `EB05`): after the set's name, else
before the product type, else in front. A name giving some other set's
code is left alone. A range code such as BT19-20 matches "BT19 20" and
"BT19-20". An em dash in a product's name becomes a space in titles and
descriptions (`hubs.search_name`). The heading and breadcrumbs keep the
name as it is.
Product titles take the longest wording that fits in about 60
characters, from "{product} from {price} delivered: UK price comparison"
through "{product} UK from {price} delivered" and "{product} UK from
{price}" to "{product} from {price}"; the name is never cut. They say
"pre-order from" only when the price quoted is a pre-order, no shop has
it in stock and its release date is unknown or still to come, so a shop's
stale pre-order flag on a product already out does not tell searchers it
is not out yet. A pre-order's description gives the release date while it
is still to come, and a product still to come that shops list but cannot
price (sold out, or delivery unknown) gets
`meta.product.description_dated_listed` rather than the "once shops list
it" line (`hubs.name_with_code`, `hubs.product_title`).

`/deals/`, `/drops/` and Out now (`/drops/?show=out`) are plain server
rendered pages with their own addresses, titles and descriptions, and all
three are in the sitemap. `/robots.txt`
names the AI crawlers and allows them the same pages as everyone else,
and `/llms.txt` describes the site in plain Markdown with links to the
games, the how it works page and the sitemap. Search, swipe and the
click redirect stay out of the index.

## Sitemap and removed pages

`/sitemap.xml` (`web/sitemaps.py`) lists the product pages worth a search
engine's visit, not every product:

- on sale now: a shop has it in stock or on pre-order, checked within the
  last 72 hours and not kept out of the comparison;
- coming soon: its release date (or its set's) is still ahead;
- priced recently: a shop had it on sale in the last 30 days
  (`RECENT_DAYS`), so a page stays listed through a sell-out.

The rest, products no shop has priced for a month, stay on the site, are
linked from their set and game pages and are not `noindex`; they are only
left out of the list so Google spends its visits on pages with a price. On
the 3 October snapshot that is 2,785 of 7,626 products. Every game, set and
the fixed pages are listed too.

Each product's `lastmod` is when its page really changed: the latest of its
own edits, its set's edits and the last change of any of its shops' price,
delivery or stock.

- `Product.updated_at` moves when a field the page shows is saved,
  including by the hourly tidy (a retype, a corrected language, filing
  under a set or taking it away, a merge undone), and not when a lookup is
  recorded.
- `ProductSet.updated_at` moves when the set's name, address, code or
  release date is saved, since the product page shows them too. A release
  source repeating the date the set already has only notes where it came
  from and does not move it. Sets last edited before this field was added
  have none.
- `Listing.changed_at` moves for a shop's change. A check that finds the
  same price and stock leaves it alone, so `lastmod` does not move every
  hour; a new price, a sell-out, a restock, a shop's price back on sale
  after being out of date, a multi-buy that starts, changes or ends, a buy
  link moved to another of the shop's pages (not another variant of the
  same page), a price kept out of or let back into the comparison, a
  listing hidden or shown, a shop switched off or on in admin, or a shop
  moved onto the product does. A hidden listing (or one of a shop switched
  off) still counts, by when it was hidden, so taking a shop off a page
  moves `lastmod` forward; while hidden, its own changes are not stamped,
  as the page does not show them. Listings changed before `changed_at`
  existed fall back to their last restock, if they are shown.

A set's `lastmod` is the latest of its own edits and its products', so a
product filed under it moves it too.
`changefreq` and `priority` are not sent: Google ignores them. Each part
of the sitemap costs two queries, a count and the page, however many
products there are.

A product page `tidy_catalogue` removes (no shop lists it, no release date
and no image, or it fails the sealed-product rules) leaves its address, and
every old address that led to it, in admin under "Removed product
addresses". A visit there answers **410 Gone** with "... is no longer
listed", a line pointing to the search box, the search box and links to
its set and game when it had them (on the 3 October snapshot none of the
204 products the tidy would remove had a set); the wording is in Site
wording under Error pages and never mentions the set or game links, which
may not be there. Google drops a 410 faster than a 404. A
product created again at the same address simply shows, and an old address
of a merged product still redirects. `tidy_all` forgets these addresses
after a year, when they go back to a plain 404. Changing a product's
address in Django Admin keeps the old one as an old address, so it
redirects.

## Dashboard

`/dashboard/` is the owner's page for a phone: how the site is doing, in the
site's own look (header, cards with the soft orange border, green for good,
red for bad). The admin home and Insights link to it. It is for staff only:
anyone else is sent to the admin sign-in. Top to bottom:

- **Today at a glance:** people today so far against the same time last
  week (or yesterday), clicks to shops from people today, rows waiting on
  Things to check, and Claude's spend this month against its limit.
- **Price check coverage** (`prices_section`): of the products with a
  price now, the share showing the Price check, the share whose check uses
  Cardmarket's price and the share using our own record, then each game,
  and a link to the Cardmarket matches waiting for you. From the bands
  stored each hour. Under it, **Claude matching Cardmarket**: of our
  products in the games Cardmarket covers, how many Claude has looked at and
  how many are left, how many it matched (or you confirmed), how many wait
  for you and how many have no Cardmarket product; this month's spend on it
  and when it last asked; how many days of Cardmarket prices we keep; and,
  in red, that matching is paused while Claude is switched off. Five
  queries.
- **What clicks earn, last 30 days** (`money_section`), the first card,
  five rates for the whole site, each with the figures behind it:
  - *Retailer click-through rate*: visits with a click to a shop, out of
    visits (counted from the first day clicks carried their visit's token,
    22 Oct 2026, so earlier days do not count as visits that never clicked).
  - *Commission per 100 clicks*: approved commission / clicks x 100.
  - *Retailer conversion rate*: sales the networks report (pending or
    approved, not declined) / clicks.
  - *Revenue per 1,000 visits*: approved commission / visits x 1,000.
  - *Affiliate coverage*: in-stock offers at shops whose links earn (an
    affiliate link format, or eBay and Amazon) / all in-stock offers.
  Then commission still pending, **What people press** (Buy now, a shop in
  the price list, a product card, Compare prices and the rest, as shares of
  every press), and the same figures **by game** (most commission first,
  naming the game that earns most per 100 clicks; the most profitable game
  need not be the biggest) and **by shop** (the twelve with most commission
  or clicks, and any shop with stock whose links earn nothing). Visits
  cannot be split by game or shop, so the click-through rate and revenue
  per visit are whole-site only. Sales and commission are read from
  Affiliate sales, filled by importing each network's report (see Clicks
  to shops); until one is imported they read nothing and the card says so.
  Seven queries, however many rows.
- **Visitors, last 7 days:** people a day as a bar chart (today marked "so
  far", burst days grey and left out of the totals), returning share, page
  views, where people came from, and the traffic explainer's top finding.
- **Clicks to shops, last 7 days:** clicks from people beside direct hits a
  day, clicks per person, clicks by how the shop pays (eBay Partner Network,
  Amazon Associates, Awin, CJ, Rakuten, another affiliate link, or none,
  worked out from each shop's price source and affiliate link format), and
  the five most clicked products.
- **Things to correct:** the count in each Things to check list, possible
  duplicate pairs by how sure the rules are, and how many rows Claude
  settled in the last day.
- **Shops health:** shops read in the last hour, up to date, paused, and
  late or failing (the first five named, each linking to its admin page),
  the background reader and the last read to finish.
- **Customers:** confirmed drop reminders, search alerts and back in stock
  alerts, alert emails sent this week, unread messages, shops asking to be
  listed, and the refer a friend offer.
- **Searches that found little:** this week's searches that found two
  products or fewer, most made first.
- **Claude:** what it is doing, spend against the monthly limit, rows
  settled this month and its last run.

Every figure is read from the database by the functions the admin pages
use (`catalogue/traffic.py` `explain()` for people, bursts and the same-time
comparison, `OutboundClick.objects.people()` and `.suspects()`,
`catalogue/checks.py`, `catalogue/crawl.py`, `catalogue/judge.py`), so the
dashboard and the admin pages always agree. A part with nothing recorded
says "No data yet". Each chart is inline SVG drawn on the server, with a
caption and a Show the numbers table; `web/static/js/dashboard.js` only adds
a tooltip for the day under the pointer.

Each part is kept in the shared cache for 60 seconds (`web/dashboard.py`
`SECTION_SECONDS`), so a reload costs a few cache reads; "Read them again
now" reads every part afresh. Possible duplicates read the whole catalogue
(over two seconds on a copy of the live database), so their counts are kept
whenever something counts them anyway: each opening of Things to check and
the hourly `tidy_all`. The dashboard shows the time they were counted, and
counts them itself only when no count is kept (after the cache is emptied).

The page is private like the links in alert emails: `noindex, nofollow`,
`Cache-Control: private, no-store`, `Disallow: /dashboard/` in robots.txt, no
analytics, Awin or AdSense script, and it is not counted as a page view. Its
wording is in Site wording under "Dashboard page (owner only)"; its styles
are `web/static/css/dashboard.css`, from the dashboard tokens in
`tokens.css`.

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

A second cookie, `rr_guide`, is set when a browser reads a guide or its
checklist (owner's yes, 27 Oct 2026). It also holds only "1", for 180 days,
and is never set for a robot or a page fetched ahead of a tap (a guide
rendered ahead is marked once it is shown, through the view note). Each
day's visitor row records whether the browser carried it
(`DailyVisitor.guide_reader`), so a visit on a later day by someone who read
a guide is counted: the dashboard's Guides part shows them as "Visits by
earlier guide readers". The Terms page names it beside `rr_back`.

The page opens with "Areas to improve": a ranked list worked out from the
figures, such as shops whose imports are failing, the share of clicks
going to shops with no affiliate link, searches that found nothing,
popular products with only one shop, products viewed but never clicked,
low search engine traffic and products with no picture. Below it are
shop health (last good read, stock, whether clicks can earn, latest
error), clicks that earn nothing, and the catalogue's comparison
coverage.

### Why traffic changed

Above everything else, "Why traffic changed" (`catalogue/traffic.py`,
`explain()`) answers what the By day table cannot. It counts people only:
visitors at the 150 page cap are scripts and are left out.

- **Like with like.** Today so far is compared with the same hours of the
  same weekday last week, then with yesterday up to the same time. Yesterday
  is compared with the average of the previous four of the same weekday, or
  the seven days before when fewer than two of those are usable. The By day
  table marks today "so far, to HH:MM".
- **Bursts apart.** A day is a burst when its pages per visitor, or visitors
  per click, are far above normal (5 robust spreads above the median over
  the last five weeks and at least 3 times the median; never under 4 pages
  or 8 visitors per click; a visitors-per-click burst must also bring 1.5
  times the usual visitors), or when 3 or fewer visitors below the cap (or
  5% of the day) open half of the pages people opened. A day's page views
  are the By day figure (page counts), because each visitor's own count
  only exists from 7 Oct 2026. Burst days are labelled in By day and left
  out of every average. A day with more clicks to shops than people is not
  a burst (its people still count) but is listed and left out of the usual
  clicks per person, since clicks are saved for any browser that follows a
  Buy now link.
- **What drove it.** The change in people is broken down by source, landing
  page type, landing page, country, device and new or returning. A driver is
  named only when it moved at least 20 people, carries a quarter of the
  change, and moved out of proportion to the rest; otherwise the page says
  the change is spread out. A source is named before a page, device or
  country, and a change shown always keeps its driver in the top five. A change within day to day noise (5 people, 15%
  or two Poisson standard deviations) is called normal and not explained.
- **Health.** Two or more hours today with no visitors when about 6 usually
  arrive (counting may be broken); three or more hours with 15+ people and
  no clicks when 5+ were expected (check the Buy now links), at the click
  rate of the comparison shown for today; clicks per person under half the
  usual and 10+ short, counting only the usual number of people (a new
  crowd from one site may click less without anything being broken); the
  background reader stopped; shops overdue a read. When today's rows have
  no arrival times, the clicks check uses the day so far as a whole, and
  the page never says a check passed that could not run.

Each finding gives its numbers and, where the data cannot decide, what to
check next, for example Google Search Console for a search drop, since the
site cannot see rankings. The site keeps no record of when it was updated,
so a change after an update is not marked. All its wording is in Site
wording under "Insights page in admin" and can be edited there.

It needs two fields on each visitor-day row, set once when the visitor's
first counted page of the day is opened: `first_seen` (when) and `landing`
(the page kind and slug, such as `product:prismatic-evolutions-etb` or
`home`; never a query string or anything about the person). Rows from
before these fields existed have neither, so they are left out of
same-time comparisons and landing page breakdowns. The day the change goes
live is only partly timed, so the first whole timed day is the next one,
and the page says that same-time comparisons with last week begin a week
after it. `explain_traffic --at` counts only visitors who had arrived by
that moment.

On SQLite, migration `catalogue/0056` rebuilds the visitor-day table
(Django copies the table to add the non-null `landing` column). A copy of
89,000 rows took about a third of a second in a test; page counts that
arrive during the copy are dropped, as at any busy database moment.

The same findings print on the server with:

```sh
python manage.py explain_traffic
python manage.py explain_traffic --at 2026-10-18T13:13   # as at a past moment, local time
```

## Tests

```sh
python manage.py test
```

Besides behaviour, the tests render every public page and fail on em dashes,
exclamation marks and stock marketing phrases.
The spreadsheet tests (`catalogue/tests_workbook.py`, `tests_owner_import.py`,
`tests_owner_apply.py` and `web/tests_data.py`) edit real workbooks the way
Excel saves typing (`catalogue.testing.excel_resave`), check the Your data
pages' wording the same way, and check in a fresh process that a web worker
never loads openpyxl.
A test run blanks `RIPRAPTOR_NTFY_TOPIC`, `RIPRAPTOR_INBOX_NOTIFY_EMAIL`
and `RIPRAPTOR_ZEPTOMAIL_TOKEN`, so running the tests from a shell that
has loaded the server's `.env` never sends a push or an email.
