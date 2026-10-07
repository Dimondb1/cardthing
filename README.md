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
have one and otherwise by name, keeping a result only when it matches the
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
then the rest, by barcode where we have one and otherwise by name. For
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
(`tidy_all` runs them in order, and `start.bat`, `update_prices` and the
server's hourly import run `tidy_all` automatically):
`tidy_catalogue` (products that no longer pass), `merge_duplicates` (one
product under two names) and `tidy_listings` (a shop item linked to the
wrong product, judged by the words in its shop address; it also hides an
eBay listing the current rules refuse: a price under 40% of the cheapest
any shop last showed, in stock or not, or a title that is a multi-buy, a
code, a sampling pack or another language. Listings keep the shop's own
title for this, so a new rule applies at the next hourly import rather
than the next eBay run). Each takes
`--dry-run`. The home page never shows a "saving" above 70%, because a gap
that large is a wrong link, not a bargain.

`watch_stock` runs every ten minutes on the server. It asks each shop
about single products (a Shopify shop answers `/products/<handle>.js` in
milliseconds). Half its budget of 300 goes to the products people are
watching: the most viewed and most saved to a watchlist over the last two
days, in or out of stock. The rest starts with in-stock items people
click, then sold-out items people click, then everything else by age. The
"Reload prices" button on a product page only re-reads what the site
holds; it never asks a shop. Anything that comes back is
stamped and shown on the home page under "Back in stock" for
`RIPRAPTOR_RESTOCK_HOURS` (48). Checking whole shops more often than hourly
is not possible: the shops rate-limit scripted requests and a full read of
all of them takes over an hour.

Price history starts the day a product first gets a price and is kept for
ever; the product page charts the last 90 days and, after 30 days, says
what the cheapest price was a month ago. `snapshot_daily_prices` runs
nightly on the server to record each day's cheapest price.

The first import takes 20 to 40 minutes for a Shopify shop and about an
hour for a large website shop; later ones are similar, so run it hourly on
a server rather than on a laptop. The server cron takes a lock so a slow
import never overlaps the next one.

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
     as a sealed product are fetched first (up to 3,000 a run), pages whose
     address reads as a single card or accessory are skipped, and the game
     is taken from the address when the page title leaves it out.
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
   for a local file. Run it from cron, hourly is sensible. Each run is
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
collects static files, sets up the shops, starts the app as a service,
configures HTTPS for the domain, schedules hourly imports and starts the
first import. Run the same command again to update. The repository must be
public (or the server needs a token) for the clone to work.

The manual steps:

The `deploy/` folder has everything for a small Linux server (a £4 to £6 a
month VPS is enough):

1. Clone the repository to `/srv/ripraptor` and run `sudo deploy/setup.sh`.
   It creates a virtualenv, installs requirements, applies migrations,
   collects static files, loads the catalogue, installs a systemd service
   for the app and a crontab for the hourly price check.
2. Edit `/srv/ripraptor/.env` (start from `.env.example`): a long random
   `DJANGO_SECRET_KEY`, your domain in `DJANGO_ALLOWED_HOSTS` and
   `DJANGO_CSRF_TRUSTED_ORIGINS`, then `sudo systemctl restart ripraptor`.
3. Install Caddy, put `deploy/caddy.Caddyfile` at `/etc/caddy/Caddyfile`
   with your domain, and reload it. Caddy fetches the HTTPS certificate.
4. Create an admin user: `sudo -u ripraptor .venv/bin/python manage.py createsuperuser`.

Static files are served by the app itself (WhiteNoise) with hashed names
and long cache headers, so no separate static hosting is needed. Uploaded
product images live in `media/`; back that folder and the database up.

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
| `RIPRAPTOR_RESTOCK_HOURS`      | 48      | How long a restocked product stays under "Back in stock". |
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
| `RIPRAPTOR_ZEPTOMAIL_URL`      | `https://api.zeptomail.eu/v1.1/email` | The API address shown on the Mail Agent's API page; it differs by Zoho data centre. |
| `RIPRAPTOR_MAIL_FROM`          |         | The sending address, on a domain verified in ZeptoMail, for example `alerts@ripraptor.com`. |
| `RIPRAPTOR_SITE_URL`           | `https://ripraptor.com` | Used for links in emails. |
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
create a Mail Agent, copy its Send Mail token and API address into `.env`
as `RIPRAPTOR_ZEPTOMAIL_TOKEN` and `RIPRAPTOR_ZEPTOMAIL_URL`, set
`RIPRAPTOR_MAIL_FROM`, restart, and check with
`python manage.py send_stock_alerts --test you@example.com`. Without the
token the form does not appear.

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

`/feeds/deals.xml` is an RSS feed of restocks and price drops across the
site, and `/feeds/<game>.xml` (for example `/feeds/pokemon.xml`) the same
for one game. Every page announces them in its head, and the deals page
prints the address, so a feed reader or a Discord feed bot can follow
them. Restocks come from the Restock rows with the shop and the delivered
price. Price drops come from the daily lowest prices, which hold a date
and no time, so a drop is dated to its day and a product appears once.
Entries link to the product page. Feeds are built with Django's own
syndication module and cached like the home lists, so a restock recorded
by a check appears on the next request.

## Merged products keep their addresses

`merge_duplicates` (run hourly by `tidy_all`) records every merged
product's old address, and a visit to an old address is sent to the
kept product with a permanent redirect, so links and search results
keep working. Old addresses are listed in admin under "Old product
addresses".

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
