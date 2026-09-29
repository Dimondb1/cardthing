# CardScout

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

## Checking a shop before adding it

```sh
python manage.py check_shop https://www.example-cards.co.uk/
```

reports whether the shop publishes Shopify product data, how many products
carry barcodes, and how many look like sealed TCG. If it is not Shopify, ask
the retailer or your affiliate network for a CSV feed instead.

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
   - **Product feed (CSV)**: enter the feed address from your affiliate
     network or the retailer. Columns `ean`, `url`, `price` are needed;
     `title`, `availability` and `delivery` are used if present. Common
     column names from Awin and Google Merchant feeds are recognised.
   - **Entered by hand**: add listings yourself under the product.
   Shops that publish no barcodes (some Shopify shops leave them blank) can
   still be imported: open our product in admin, add a listing for that
   retailer with the shop's product page link and any price, and the import
   matches by link from then on. Each price import lists the shop products it
   could not match, with their links, so you can add them one by one.
3. **Import.** `python manage.py import_prices` fetches every retailer with a
   source, or `import_prices <retailer-slug>` for one, or `--feed file.csv`
   for a local file. Run it from cron, hourly is sensible. Each run is
   recorded in Admin > Price imports with the retailer products that could
   not be matched, so you can add the missing barcodes.
4. **Images.** Price imports keep the retailer's product image for any
   product without one (`CARDSCOUT_USE_FEED_IMAGES`, on by default). An
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
`web/templatetags/cardscout.py`, screen reader hints such as "(opens in a new
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
`web/static/img/favicon.svg`.

## Prices

- A **listing** is one retailer's offer: product link, item price, delivery
  charge for one item to a UK address, stock status and when it was last
  checked. The delivered price is worked out by the database.
- The **cheapest delivered price** only uses listings that are in stock or on
  pre-order and were checked within `CARDSCOUT_STALE_AFTER_HOURS` (72 by
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

The `deploy/` folder has everything for a small Linux server (a £4 to £6 a
month VPS is enough):

1. Clone the repository to `/srv/cardscout` and run `sudo deploy/setup.sh`.
   It creates a virtualenv, installs requirements, applies migrations,
   collects static files, loads the catalogue, installs a systemd service
   for the app and a crontab for the hourly price check.
2. Edit `/srv/cardscout/.env` (start from `.env.example`): a long random
   `DJANGO_SECRET_KEY`, your domain in `DJANGO_ALLOWED_HOSTS` and
   `DJANGO_CSRF_TRUSTED_ORIGINS`, then `sudo systemctl restart cardscout`.
3. Install Caddy, put `deploy/caddy.Caddyfile` at `/etc/caddy/Caddyfile`
   with your domain, and reload it. Caddy fetches the HTTPS certificate.
4. Create an admin user: `sudo -u cardscout .venv/bin/python manage.py createsuperuser`.

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
| `CARDSCOUT_CONTACT_EMAIL`      |         | Shows the "Report a problem" section on the how it works page. |
| `CARDSCOUT_STALE_AFTER_HOURS`  | 72      | |
| `CARDSCOUT_USE_FEED_IMAGES`    | on      | Keep retailer images for products without one. |
| `CARDSCOUT_HOME_CACHE_SECONDS` | 300     | How long trending and savings are kept. Cleared by imports and edits. |

## Tests

```sh
python manage.py test
```

Besides behaviour, the tests render every public page and fail on em dashes,
exclamation marks and stock marketing phrases.
