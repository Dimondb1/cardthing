"""
What an uploaded spreadsheet changes, worked out before anything is changed.

The owner downloads the catalogue (catalogue/workbook.py), changes some cells in Excel and uploads the file.
Each cell he may change is compared three ways: with what his download held (the base), with what the file
holds now, and with what the site holds now. Only a cell he changed counts, and only while the site has not
changed it too since his download; when it has, the site's value stays and he is told.

Every change is then judged against the site as it will be once the whole file is made (FinalState), so the
order the changes are made in never changes a verdict: a merge is judged with the barcodes and shop pages the
rest of the file gives, and a link to a product merged away in the same file lands on the product kept. It is
judged against the hourly tidy-up too, so nothing made from the file is joined to another product, put back
or removed within the hour.

plan() writes nothing. It returns unsaved DataChange rows, each with a plain sentence; the apply step makes
the ones he says yes to.
"""

from collections import Counter, defaultdict
from copy import copy
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache

from django.utils import timezone

from . import checks, languages, types
from . import workbook as wb
from .data_jobs import unpack_base
from .importers import ean_key, link_key
from .models import DataChange, DataExport, Game, Listing, Product, ProductAlias, ProductSet, Retailer, ShopProduct
from .sanity import MARKETPLACES

Kind = DataChange.Kind
State = DataChange.State
LIVE = (State.READY, State.HELD)

# The order changes are made in: sets, new products, product fields, shop pages, merges, pairs kept apart,
# then shop items. Within shop pages, take-offs come before moves, and moves before hiding and showing.
STEPS = {
    Kind.NEW_SET: 1, Kind.SET_FIELDS: 1, Kind.NEW_PRODUCT: 2, Kind.PRODUCT_FIELDS: 3, Kind.TAKE_OFF: 4,
    Kind.MOVE: 4, Kind.HIDE: 4, Kind.SHOW: 4, Kind.MERGE: 5, Kind.APART: 6, Kind.LINK: 7, Kind.IGNORE: 7,
    Kind.REVIEW: 7, Kind.LEFT_LINK: 7, Kind.LEFT_IGNORE: 7,
}
WITHIN_STEP = {Kind.TAKE_OFF: 0, Kind.MOVE: 1, Kind.HIDE: 2, Kind.SHOW: 2}
# The changes that land on a product named in the file, resolved through its merges.
TARGETED = (Kind.MOVE, Kind.LINK, Kind.LEFT_LINK, Kind.MERGE)
SHEET_ORDER = {sheet.name: n for n, sheet in enumerate((wb.SETS, wb.PRODUCTS, wb.LISTINGS, wb.SHOP_ITEMS, wb.LEFT_OUT))}
# A row that is not read is listed under the kind of change its sheet makes.
SHEET_KIND = {
    wb.PRODUCTS.name: Kind.PRODUCT_FIELDS, wb.LISTINGS.name: Kind.MOVE, wb.SHOP_ITEMS.name: Kind.LINK,
    wb.LEFT_OUT.name: Kind.LEFT_LINK, wb.SETS.name: Kind.SET_FIELDS,
}

MAX_CHANGES = 5000
# More rows than this whose id no longer matches their address, and the whole sheet is left.
MAX_MISMATCHES = 20
# A column changed on this many rows, and more than this share of them, is held: a sort may have mixed it up.
MASS_EDITS, MASS_SHARE = 50, 0.25
# A column with this many changes, half of them another row's value, was sorted on its own.
SHUFFLE_EDITS = 10
# A shop item last seen longer ago than this shows its price only after the shop's next read.
STALE_SEEN = timedelta(hours=72)

HEADINGS = {
    "name": "Name", "type": "Type", "set": "Set", "language": "Language", "barcode": "Barcode",
    "release_date": "Release date", "show": "Show on site", "product": "Product id", "code": "Code",
}

MISMATCH = "This row's id does not match its address. Was the id column sorted on its own or typed over?"
NOT_IN_FILE = "Not in the file you downloaded."
ADD_A_ROW = "Use new1, new2 and so on to add a row."
MERGED_AWAY = "Merged away, so its other changes are not made."
FORMULA = "Row {row}, {heading} is a formula. Copy the column, paste it back as values, save and upload again."
MASS = "Over a quarter of this column changed. If a sort mixed up the rows, discard this file."
TIDY_REMOVES = "The hourly tidy-up removes a product no shop lists that has no release date."
ALREADY = "Already so."
FITS = "The hourly tidy deletes a listing whose address contradicts the product."
DEPENDS = "Depends on a change held for your say, so it is made only with the held changes."


def three_way(base, file, current, equal=None):
    """'none' when he left the cell as downloaded, 'apply' when he changed it and the site has not, 'same'
    when the site already holds his value, 'conflict' when both changed it."""
    equal = equal or (lambda a, b: a == b)
    if equal(file, base):
        return "none"
    if equal(current, base):
        return "apply"
    if equal(current, file):
        return "same"
    return "conflict"


@lru_cache(maxsize=65536)
def strict_key(name):
    from .management.commands.merge_duplicates import merge_key

    return merge_key(name)


@lru_cache(maxsize=65536)
def fits_name(url, name, game_slug):
    from .management.commands.tidy_listings import fits

    return fits(Listing(url=url, product=Product(name=name, game=Game(slug=game_slug))))


def page_fits(url, product):
    """Whether the hourly tidy (tidy_listings.fits) keeps a shop page with this address on ``product``."""
    return fits_name(url, product.name, product.game_slug)


def day_text(value):
    if not value:
        return "none"
    day = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{day.day} {day:%b %Y}"


# A name the change gives is shown whole (a product name is at most 200 characters), and the name it replaces
# is cut short, so the sentence fits and he sees exactly what he is making.
WHOLE, SHORT = 200, 40


def quoted(text, limit=70, middle=False):
    """Text in quotes, cut to ``limit`` characters: at the end, or in the middle, so that two long names that
    differ only at the end still look different."""
    text = text or ""
    if len(text) <= limit:
        return f'"{text}"'
    if middle:
        half = (limit - 3) // 2
        return f'"{text[:half].rstrip()}...{text[-half:].lstrip()}"'
    return f'"{text[:limit - 3].rstrip()}..."'


def money(value):
    return f"£{value:,.2f}" if value is not None else "no price"


# What the site holds now ---------------------------------------------------------------------------------

@dataclass
class Prod:
    pk: object            # an id, or new1 for a product this file adds
    game_id: int
    game_slug: str
    name: str
    type: str
    set_id: object        # a set id, new2 for a set this file adds, or None
    language: str
    ean: str
    release_date: object  # ISO text or None
    active: bool
    slug: str = ""
    image: bool = False


@dataclass
class SetRow:
    pk: object
    game_id: int
    name: str
    code: str
    release_date: object


@dataclass
class Page:
    pk: int
    product_id: int
    retailer_id: int
    url: str
    title: str
    shop_ean: str
    active: bool


@dataclass
class Item:
    pk: int
    retailer_id: int
    url: str
    title: str
    shop_ean: str
    price: object
    last_seen: object
    status: str
    suggested_id: object


class Site:
    """What the site holds now: each table read once, outside any transaction."""

    def __init__(self):
        rows = list(Game.objects.values_list("pk", "name", "short_name", "slug"))
        self.games = {pk: (name, slug) for pk, name, _short, slug in rows}
        self.game_names = wb.GameNames(rows)
        self.sets = {
            pk: SetRow(pk, game_id, name, code or "", wb.iso(released))
            for pk, game_id, name, code, released in ProductSet.objects.values_list(
                "pk", "game_id", "name", "code", "release_date")
        }
        self.products = {}
        for pk, game_id, name, kind, set_id, language, ean, released, active, slug, image in (
            Product.objects.values_list("pk", "game_id", "name", "product_type", "product_set_id", "language",
                                        "ean", "release_date", "is_active", "slug", "image").iterator(chunk_size=2000)
        ):
            self.products[pk] = Prod(pk, game_id, self.games[game_id][1], name, kind, set_id, language, ean or "",
                                     wb.iso(released), active, slug, bool(image))
        self.aliases = dict(ProductAlias.objects.values_list("slug", "product_id"))
        self.shops = {pk: (name, source, active) for pk, name, source, active in
                      Retailer.objects.values_list("pk", "name", "source_type", "is_active")}
        self.pages = {}
        self.page_at = {}   # (product, retailer): the listing there now
        for pk, product_id, retailer_id, url, title, shop_ean, active in Listing.objects.values_list(
                "pk", "product_id", "retailer_id", "url", "title", "shop_ean", "is_active").iterator(chunk_size=2000):
            self.pages[pk] = Page(pk, product_id, retailer_id, url, title, shop_ean or "", active)
            self.page_at[(product_id, retailer_id)] = pk
        self.items = {}
        self.item_at = {}   # (retailer, link key): the shop item
        for row in ShopProduct.objects.values_list("pk", "retailer_id", "url", "title", "shop_ean", "price",
                                                   "last_seen", "status", "suggested_id").iterator(chunk_size=2000):
            item = Item(*row[:4], row[4] or "", *row[5:])
            self.items[item.pk] = item
            self.item_at[(item.retailer_id, link_key(item.url))] = item.pk
        self.apart = checks.kept_apart()

    def merged_into(self, pk):
        """The product kept when ``pk`` was merged away (switched off, its address an old address of
        another), else None."""
        product = self.products.get(pk)
        if product is None or product.active:
            return None
        kept = self.aliases.get(product.slug)
        return kept if kept and kept != pk else None

    def gone(self, pk, address):
        """(why, the product it was merged into) for a product of the download that is no longer on the site
        as it was, else ("", None)."""
        merged = self.merged_into(pk)
        if merged is None and pk not in self.products:
            slug = address.rstrip("/").rsplit("/", 1)[-1] if address else ""
            merged = self.aliases.get(slug)
        if merged:
            return f"Merged into {merged} since your download.", merged
        if pk not in self.products:
            return "Removed by the hourly tidy-up since your download.", None
        return "", None

    def is_marketplace(self, retailer_id):
        return self.shops[retailer_id][1] in MARKETPLACES

    def shop_name(self, retailer_id):
        return self.shops[retailer_id][0]


def language_of(product, set_row):
    """The language a product with this name and set would get when saved (languages.product_language, on
    an unsaved copy)."""
    copy = Product(name=product.name, game=Game(pk=product.game_id, slug=product.game_slug))
    if set_row is not None:
        # A set this file adds has no id yet; any id lets the copy read its name and code.
        copy.product_set = ProductSet(pk=set_row.pk if isinstance(set_row.pk, int) else -1, name=set_row.name,
                                      code=set_row.code or "")
    return languages.product_language(copy)


# The site once the file is made ----------------------------------------------------------------------------

class Index:
    """Active products by the keys the hourly tidy-up joins them on: the same game, type, language and strict
    duplicate key (merge_duplicates), or the same game, language and exact name."""

    def __init__(self, products):
        self.by_key = defaultdict(set)
        self.by_name = defaultdict(set)
        for pk, product in products.items():
            if not product.active:
                continue
            key = strict_key(product.name)
            if key:
                self.by_key[(product.game_id, product.type, product.language, key)].add(pk)
            self.by_name[(product.game_id, product.language, product.name)].add(pk)

    def collisions(self, pk, products):
        """The products the hourly tidy-up would join ``pk`` with, or treat as its twin."""
        from .management.commands.merge_duplicates import why_not_merge

        product = products[pk]
        if not product.active:
            return set()
        found = set(self.by_name[(product.game_id, product.language, product.name)])
        key = strict_key(product.name)
        if key:
            found |= {other for other in self.by_key[(product.game_id, product.type, product.language, key)]
                      if other != pk and not why_not_merge(product, products[other])}
        found.discard(pk)
        return found


class FinalState:
    """The site as it will be once every ready and held change is made: each product's fields, which shops
    have a page on it, who holds each barcode, and who merges into whom. Built from the site now and the
    changes, never from the database."""

    def __init__(self, site, live, set_rows):
        """``set_rows`` are the sets on the site and the sets the file adds: {set id or newN: SetRow}. The
        file's changes to a set's name or code are made here."""
        self.site = site
        products = dict(site.products)
        touched = set()
        sets = dict(set_rows)
        renamed = set()
        for change in live:
            if change.kind == Kind.SET_FIELDS and change.fields.keys() & {"name", "code"}:
                pk = change.object_id
                sets[pk] = replace(sets[pk], **{field: values[3] or "" for field, values in change.fields.items()
                                                if field in ("name", "code")})
                renamed.add(pk)
        # A set's name and code give its products their language, and the site works it out again for every
        # product in the set when the set is saved (signals.set_saved).
        self.in_sets = defaultdict(list)
        if renamed:
            for pk, product in site.products.items():
                if product.set_id in renamed:
                    self.in_sets[product.set_id].append(pk)
                    touched.add(pk)
                    products[pk] = replace(product)
        for change in live:
            if change.kind == Kind.NEW_PRODUCT:
                d = change.data
                products[change.ref] = Prod(change.ref, d["game"], site.games[d["game"]][1], d["name"], d["type"],
                                            d["set"], d["language"], d["barcode"], d["release_date"], d["show"])
            elif change.kind == Kind.PRODUCT_FIELDS:
                pk = change.object_id
                if pk not in touched:
                    products[pk] = replace(products[pk])
                    touched.add(pk)
                product = products[pk]
                for field, (_base, _file, _seen, new) in change.fields.items():
                    if field == "name":
                        product.name = new
                    elif field == "type":
                        product.type = new
                    elif field == "set":
                        product.set_id = new
                    elif field == "barcode":
                        product.ean = new
                    elif field == "release_date":
                        product.release_date = new
                    elif field == "show":
                        product.active = new
        for pk in touched:
            product = products[pk]
            product.language = language_of(product, sets.get(product.set_id))
        self.new_sets = {change.ref for change in live if change.kind == Kind.NEW_SET}
        # In the order they are made: each merge finds the product kept with the pages earlier ones brought.
        self.edges = {change.object_id: change.data["target"] for change in
                      sorted((c for c in live if c.kind == Kind.MERGE), key=lambda c: (c.row, c.seq))}

        # Shop pages before the merges: take-offs gone, moves made, links added, each on its final product.
        taken = {change.object_id for change in live if change.kind == Kind.TAKE_OFF}
        moved = self.moved = {change.object_id: self.keeper(change.data["target"])
                              for change in live if change.kind == Kind.MOVE}
        pages = defaultdict(lambda: defaultdict(list))   # product: {retailer: [(whose, link key, url)]}
        for pk, page in site.pages.items():
            if pk not in taken:
                entry = (("listing", pk), link_key(page.url), page.url)
                pages[moved.get(pk, page.product_id)][page.retailer_id].append(entry)
        self.priced = defaultdict(int)   # pages with a price each product gains from this file
        for product in moved.values():
            self.priced[product] += 1
        for change in live:
            if change.kind == Kind.LINK:
                item = site.items[change.object_id]
                product = self.keeper(change.data["target"])
                pages[product][item.retailer_id].append((("item", item.pk), link_key(item.url), item.url))
                self.priced[product] += item.price is not None
            elif change.kind == Kind.LEFT_LINK:
                product = self.keeper(change.data["target"])
                d = change.data
                pages[product][d["retailer"]].append((("left", change.ref), link_key(d["url"]), d["url"]))
        self.before_merge = {pk: replace(product) for pk, product in products.items() if pk in self.edges}

        # The merges: the product merged away is switched off, its barcode moves to the product kept when that
        # has none, and its pages move to it for the shops it lacks (merge_undoable).
        self.carried = defaultdict(list)   # a product merged away: the pages that move to the product kept
        self.stranded = {}                 # and the shops whose page stays on it, unseen
        for other in self.edges:
            keep = self.keeper(other)
            if keep is None or keep == other or other not in products or keep not in products:
                continue
            products[other] = replace(products[other], active=False, ean="")
            kept = products[keep] = replace(products[keep])
            if not kept.ean and self.before_merge[other].ean:
                kept.ean = self.before_merge[other].ean
            self.stranded[other] = set(pages[other]) & set(pages[keep])
            for retailer, entries in list(pages[other].items()):
                if retailer not in pages[keep]:
                    pages[keep][retailer] = entries
                    self.carried[other].extend((retailer, url) for _whose, _key, url in entries)
                    del pages[other][retailer]
        self.products = products
        self.pages = pages
        self.eans = defaultdict(set)
        for pk, product in products.items():
            if product.active and ean_key(product.ean):
                self.eans[ean_key(product.ean)].add(pk)
        self._index = None

    def keeper(self, ref):
        """The product ``ref`` ends as: itself, or the product it is merged into, through this file's merges
        and earlier ones. None for products that name each other in a circle."""
        seen = set()
        while True:
            if ref in seen:
                return None
            seen.add(ref)
            if ref in self.edges:
                ref = self.edges[ref]
                continue
            merged = self.site.merged_into(ref) if isinstance(ref, int) else None
            if merged:
                ref = merged
                continue
            return ref

    @property
    def index(self):
        if self._index is None:
            self._index = Index(self.products)
        return self._index

    def listed(self, pk):
        """Whether ``pk`` has a listing once the file is made: a shop's page, or a priced shop item linked to
        it. A link with no price, or a left-out link, makes none until the shop's next read."""
        return any(whose[0] == "listing" or (whose[0] == "item" and self.site.items[whose[1]].price is not None)
                   for entries in self.pages[pk].values() for whose, _key, _url in entries)

    def others_at(self, product, retailer, own):
        """The pages ``retailer`` has on ``product`` once the file is made, other than ``own``."""
        return [entry for entry in self.pages[product].get(retailer, []) if entry[0] != own]

    def barcode_owners(self, barcode, but=None):
        return {pk for pk in self.eans.get(ean_key(barcode), ()) if pk != but}


# Reading the rows ------------------------------------------------------------------------------------------

class Planner:
    def __init__(self, export, parsed, now=None):
        self.export = export
        self.base = unpack_base(export.base)["sheets"]
        self.parsed = parsed
        self.now = now or timezone.now()
        self.site = Site()
        self.changes = []
        self.refs = {}         # newN on Products that an earlier upload of this file added: the product's id
        self.set_refs = {}     # the same for Sets
        self.added = {}        # (sheet, newN): what the earlier upload's row said
        self.reused = {}       # newN on Products given to another product than the one added before: its id
        self.new_sets = {}     # newN on Sets that this file adds: SetRow
        self.new_rows = set()  # newN on Products that this file adds
        for sheet, ref, pk, data in DataChange.objects.filter(
            data_import__export=export, kind__in=[Kind.NEW_PRODUCT, Kind.NEW_SET], outcome=DataChange.Outcome.APPLIED,
            object_id__isnull=False,
        ).values_list("sheet", "ref", "object_id", "data"):
            (self.set_refs if sheet == wb.SETS.name else self.refs)[ref] = pk
            self.added[(sheet, ref)] = data or {}

    def run(self):
        self.read_sets()
        self.read_products()
        self.read_listings()
        self.read_shop_items()
        self.read_left_out()
        self.column_guards()
        self.judge()
        counted = [change for change in self.changes if change.state != State.SAME]
        if len(counted) > MAX_CHANGES:
            raise wb.FileRefused(f"This file makes more than {MAX_CHANGES:,} changes. {wb.TOO_BIG}")
        self.changes.sort(key=lambda c: (STEPS[c.kind], WITHIN_STEP.get(c.kind, 0), SHEET_ORDER[c.sheet], c.row, c.seq))
        for seq, change in enumerate(self.changes, start=1):
            change.step = STEPS[change.kind]
            change.seq = seq
            change.what = describe(change)[:300]
            change.why = change.why[:300]
            change.warning = change.warning[:300]
        return self.changes

    # Making changes -------------------------------------------------------------------------------------

    def add(self, kind, sheet, row, state, why="", **values):
        values.setdefault("data", {})
        values.setdefault("fields", {})
        change = DataChange(kind=kind, sheet=sheet.name, row=row, state=state, why=why, seq=len(self.changes),
                            **values)
        self.changes.append(change)
        return change

    def row_problem(self, sheet, row, key, why, what=""):
        """A row, cell or whole column that is not read, with why."""
        if not what:
            what = f"Row {row} on {sheet.name}" + (f" (id {key})" if key is not None else "") + " was not read."
        return self.add(SHEET_KIND[sheet.name], sheet, row, State.PROBLEM, why, data={"what": what},
                        object_id=key if isinstance(key, int) else None)

    def field_change(self, kind, sheet, row, field, verdict, base, value, seen, why="", **values):
        """A one-cell change from a compare() verdict."""
        state = {"apply": State.READY, "same": State.SAME, "conflict": State.CONFLICT, "problem": State.PROBLEM}[verdict]
        if verdict == "same":
            why = ALREADY
        return self.add(kind, sheet, row, state, why, fields={field: [base, value, seen, value]}, **values)

    def rows_of(self, sheet):
        """(row number, key, cells, base) for each row worth reading, once the rows that cannot be read are
        listed: ids that no longer match their address, one id on two rows that differ, ids not in the
        download, and rows with no id. A cell holding a formula is listed and left out of its row. ``base`` is
        None for a row the file adds (new1, new2 and so on)."""
        rows = self.parsed.sheets.get(sheet.name)
        if rows is None:
            return []
        base = self.base.get(sheet.name, {})
        changeable = [column for column in sheet.columns if column.mark != wb.READ_ONLY]
        # What two rows with one id are compared on: what is read from them.
        read = {column.key for column in changeable} | {sheet.key, sheet.integrity, "game"}
        keyed = defaultdict(list)
        mismatched = []
        # Which rows each address belongs to (two marketplace listings can share a shown link).
        owners = defaultdict(set)
        if sheet.integrity:
            for row_key, row in base.items():
                owners[wb.clean_text(row.get(sheet.integrity))].add(row_key)
        for number, cells in rows:
            cells = dict(cells)
            for column in changeable:
                value = cells.get(column.key)
                if isinstance(value, str) and value.strip().startswith("="):
                    self.row_problem(sheet, number, None, FORMULA.format(row=number, heading=column.heading),
                                     what=f"{column.heading} on {sheet.name} row {number} was not read.")
                    del cells[column.key]
            has_data = any(not wb.blank(cells.get(column.key)) for column in changeable)
            if sheet is wb.LEFT_OUT:
                link = wb.clean_text(cells.get("link"))
                key, problem = (wb.left_out_key(self.export.left_out_retailer_id, link) if link else None), ""
            else:
                key, problem = wb.as_ref(cells.get(sheet.key))
            if key is None:
                if has_data:
                    self.row_problem(sheet, number, None, problem or (
                        ADD_A_ROW if sheet in (wb.PRODUCTS, wb.SETS) else NOT_IN_FILE))
                continue
            if isinstance(key, str) and sheet is not wb.LEFT_OUT:
                if sheet not in (wb.PRODUCTS, wb.SETS):
                    if has_data:
                        self.row_problem(sheet, number, key, NOT_IN_FILE)
                    continue
                row_base = None
            else:
                row_base = base.get(str(key))
                if row_base is None:
                    if has_data:
                        self.row_problem(sheet, number, key, NOT_IN_FILE)
                    continue
                if sheet.integrity and sheet.integrity in cells:
                    # Another row's address means the ids were moved. One no row had is a grey cell changed,
                    # by Find and Replace say, and grey cells are ignored.
                    written = wb.clean_text(cells.get(sheet.integrity))
                    if written and written in owners and str(key) not in owners[written]:
                        mismatched.append((number, key))
                        continue
            keyed[key].append((number, cells, row_base))
        if len(mismatched) > MAX_MISMATCHES:
            self.row_problem(sheet, 0, None, (
                f"{len(mismatched)} rows' ids do not match their addresses. Was the id column sorted on its own "
                "or typed over?"), what=f"Nothing on {sheet.name} was changed.")
            return []
        for number, key in mismatched:
            self.row_problem(sheet, number, key, MISMATCH)
        found = []
        for key, entries in keyed.items():
            if len(entries) > 1:
                values = [{k: wb.clean_text(cells.get(k)) for k in read} for _n, cells, _b in entries]
                if any(v != values[0] for v in values[1:]):
                    numbers = " and ".join(str(n) for n, _c, _b in entries)
                    for number, _cells, _base in entries:
                        self.row_problem(sheet, number, key, f"Rows {numbers} have the same id and differ, so "
                                         "neither was read.")
                    continue
            number, cells, row_base = entries[0]
            found.append((number, key, cells, row_base))
        found.sort(key=lambda entry: entry[0])
        return found

    def compare(self, cells, field, normalise, base, current, equal=None, blank_unchanged=False):
        """(verdict, file value, problem) for one cell: 'none', 'apply', 'same', 'conflict' or 'problem'. A
        missing column, and a blank cell where blank means unchanged, are 'none'."""
        if field not in cells:
            return "none", None, ""
        value, problem = normalise(cells[field])
        if value is None and problem:
            return "problem", None, problem
        if value is None and blank_unchanged:
            return "none", None, ""
        verdict = three_way(base, value, current, equal)
        if verdict != "none" and problem:
            return "problem", value, problem
        return verdict, value, ""

    def conflict_why(self, field, base, value, current, game_slug=""):
        if field == "name":
            whole = (f"You changed Name from {quoted(base, WHOLE)} to {quoted(value, WHOLE)}, but the site has "
                     f"{quoted(current, WHOLE)} since your download. Left as {quoted(current, WHOLE)}.")
            if len(whole) <= 300:
                return whole
            # Long names do not all fit whole: each is cut in the middle, so where they differ still shows.
            return (f"You changed Name from {quoted(base, SHORT, middle=True)} to {quoted(value, 75, middle=True)}, "
                    f"but the site has {quoted(current, 75, middle=True)} since your download. Left as the site has "
                    "it.")

        def show(v):
            return self.shown(field, v, game_slug)
        return (f"You changed {HEADINGS[field]} from {show(base)} to {show(value)}, but the site has "
                f"{show(current)} since your download. Left as {show(current)}.")

    def shown(self, field, value, game_slug=""):
        """A value as the sentences show it."""
        if field == "name":
            return quoted(value)
        if field == "type":
            return types.type_label(game_slug, value) if value else "none"
        if field == "set":
            row = self.set_row(value)
            return row.name if row else "none"
        if field == "language":
            return languages.name(value)
        if field == "release_date":
            return day_text(value)
        if field == "show":
            return "Yes" if value else "No"
        return str(value) if value not in (None, "") else "none"

    def set_row(self, ref):
        if ref is None:
            return None
        if isinstance(ref, str):
            return self.new_sets.get(ref)
        return self.site.sets.get(ref)

    def as_product(self, value):
        """A typed product id or newN, with a newN an earlier upload of this file added as that product."""
        ref, problem = wb.as_ref(value)
        return self.refs.get(ref, ref), problem

    def resolve(self, ref):
        """(ref, problem) for a typed product id or newN: the product must be on the site or added in this file."""
        ref = self.refs.get(ref, ref)
        if isinstance(ref, str):
            return (ref, "") if ref in self.new_rows else (None, f"There is no {ref} row on Products.")
        if ref not in self.site.products:
            return None, f"No product has id {ref}."
        return ref, ""

    # Sets -----------------------------------------------------------------------------------------------

    def read_sets(self):
        sheet = wb.SETS
        names = self.set_names = wb.SetNames()
        for pk, row in self.site.sets.items():
            names.add(row.game_id, pk, row.name, row.code)
        for number, key, cells, base in self.rows_of(sheet):
            if base is None:
                self.new_set(number, key, cells)
                continue
            current = self.site.sets.get(key)
            label = {"name": base["name"], "column": None}
            for field, normalise in (("name", wb.as_text), ("code", wb.as_text), ("release_date", wb.as_date)):
                if current is None:
                    verdict, value, _why = self.compare(cells, field, normalise, base[field], base[field])
                    if verdict != "none":
                        self.add(Kind.SET_FIELDS, sheet, number, State.CONFLICT,
                                 "This set was removed since your download.", object_id=key,
                                 fields={field: [base[field], value, None, value]}, data={**label, "column": field})
                    continue
                now = {"name": wb.clean_text(current.name), "code": wb.clean_text(current.code),
                       "release_date": current.release_date}[field]
                verdict, value, why = self.compare(cells, field, normalise, base[field], now)
                if verdict == "none":
                    continue
                if verdict == "apply":
                    why = self.set_field_problem(field, value)
                    verdict = "problem" if why else verdict
                elif verdict == "conflict":
                    why = self.conflict_why(field, base[field], value, now)
                seen = {"name": current.name, "code": current.code, "release_date": current.release_date}[field]
                self.field_change(Kind.SET_FIELDS, sheet, number, field, verdict, base[field], value, seen, why,
                                  object_id=key, data={**label, "column": field})
                if field in ("name", "code") and verdict == "apply":
                    names.add(current.game_id, key, value if field == "name" else current.name,
                              value if field == "code" else current.code)
        # The names the download showed, so a product row still naming a set by its old name finds it.
        for key, base in self.base.get(sheet.name, {}).items():
            row = self.site.sets.get(int(key))
            if row is not None:
                names.add(row.game_id, row.pk, base["name"], base["code"])

    def set_field_problem(self, field, value):
        if field == "name" and not value:
            return "A set needs a name."
        if field in ("name", "code") and wb.MARKUP.search(value or ""):
            return "A set's name or code cannot hold < or >."
        if field == "name" and len(value) > 120:
            return "Set names are at most 120 characters."
        if field == "code" and len(value) > 20:
            return "Set codes are at most 20 characters."
        return ""

    def new_set(self, number, ref, cells):
        from .releases import find_set

        sheet = wb.SETS
        site = self.site
        if ref in self.set_refs:
            pk = self.set_refs[ref]
            row = site.sets.get(pk)
            data = {"name": wb.clean_text(cells.get("name")), "code": "", "release_date": None}
            if row is None:
                self.add(Kind.NEW_SET, sheet, number, State.CONFLICT,
                         f"Added from this file before as {pk}, and removed since.", ref=ref, data=data)
                return
            if not self.same_new_row(sheet, ref, cells, {"game": row.game_id, "name": row.name}):
                # Another set given the same newN: rows naming it are not filed under the earlier one.
                self.add(Kind.NEW_SET, sheet, number, State.PROBLEM,
                         self.reused_why(ref, f"the set {quoted(row.name)}", pk), ref=ref, data=data)
                return
            self.set_names.found[(row.game_id, "ref", ref)] = pk
            data = {"game": row.game_id, "name": row.name, "code": row.code, "release_date": row.release_date}
            self.add(Kind.NEW_SET, sheet, number, State.SAME, f"Already added (id {pk}).", ref=ref, object_id=pk,
                     data=data)
            return
        game_id, problem = site.game_names(cells.get("game"))
        name, _ = wb.as_text(cells.get("name"))
        code, _ = wb.as_text(cells.get("code"))
        released, date_problem = wb.as_date(cells.get("release_date"))
        problem = (problem or ("A new set needs a name." if not name else "") or date_problem
                   or self.set_field_problem("name", name) or self.set_field_problem("code", code))
        data = {"game": game_id, "name": name, "code": code, "release_date": released}
        if not problem:
            found = find_set(Game(pk=game_id, slug=site.games[game_id][1]), name, code)
            if found is not None:
                problem = f"{site.games[game_id][0]} already has this set: id {found.pk}."
        if problem:
            self.add(Kind.NEW_SET, sheet, number, State.PROBLEM, problem, ref=ref, data=data)
            return
        self.new_sets[ref] = SetRow(ref, game_id, name, code, released)
        self.set_names.add(game_id, ref, name, code)
        self.add(Kind.NEW_SET, sheet, number, State.READY, ref=ref, data=data)

    # Products -------------------------------------------------------------------------------------------

    def read_products(self):
        rows = self.rows_of(wb.PRODUCTS)
        # Other rows and sheets may name a new row, so they are all known before any row is read.
        self.new_rows = {key for _n, key, _c, base in rows if base is None}
        # A newN an earlier upload of this download added names that product, unless this row is another
        # product (the original download reopened and new1 used again): then nothing resolves to it.
        for _n, key, cells, base in rows:
            if base is None and key in self.refs and self.refs[key] in self.site.products:
                earlier = self.site.products[self.refs[key]]
                if not self.same_new_row(wb.PRODUCTS, key, cells, {"game": earlier.game_id, "name": earlier.name,
                                                                   "type": earlier.type}):
                    self.reused[key] = self.refs.pop(key)
        for number, key, cells, base in rows:
            if base is None:
                self.new_product(number, key, cells)
            else:
                self.product_row(number, key, cells, base)

    def product_row(self, number, pk, cells, base):
        sheet = wb.PRODUCTS
        site = self.site
        product = site.products.get(pk)
        gone, merged = site.gone(pk, base.get("address"))
        label = {"name": base["name"], "column": None}
        same_as, same_problem = wb.as_ref(cells.get("same_as"))
        apart, apart_problem = wb.as_refs(cells.get("not_same"))
        if same_problem:
            self.add(Kind.MERGE, sheet, number, State.PROBLEM, same_problem, object_id=pk, data=dict(label))
        elif same_as is not None:
            self.merge_change(number, pk, same_as, label, gone, merged)
        if apart_problem:
            self.add(Kind.APART, sheet, number, State.PROBLEM, apart_problem, object_id=pk, data=dict(label))
        for other in apart:
            self.apart_change(number, pk, other, label)

        game_slug = product.game_slug if product else ""
        game_id = product.game_id if product else None
        set_base = base["set"]
        set_name = (self.base.get(wb.SETS.name, {}).get(str(set_base)) or {}).get("name", "") if set_base else ""
        if not set_name and set_base in site.sets:
            set_name = site.sets[set_base].name

        def as_set(value):
            return self.set_names(value, game_id, set_base, set_name)

        base_name = wb.tidy(base["name"])

        def as_name(value):
            # The rules for a new name only matter for a name he changed.
            text = wb.as_text(value)[0]
            if text == base["name"] or wb.tidy(text) == base_name:
                return base_name, ""
            return wb.as_name(value, removable=not (product and product.image))

        specs = (
            ("name", as_name, None, False),
            ("type", lambda value: wb.as_type(value, game_slug), None, False),
            ("set", as_set, None, False),
            ("language", wb.as_language, None, False),
            ("barcode", wb.as_barcode, wb.same_barcode, False),
            ("release_date", wb.as_date, None, False),
            ("show", wb.as_yes_no, None, True),
        )
        bases = {**base, "name": base_name}
        if product is None:
            currents = bases
        else:
            currents = {"name": None, "type": product.type, "set": product.set_id, "language": product.language,
                        "barcode": wb.digits(product.ean), "release_date": product.release_date, "show": product.active}
        verdicts = {}
        for field, normalise, equal, blank_unchanged in specs:
            base_value, current = bases[field], currents[field]
            if field == "name" and product is not None:
                current = base_name if wb.clean_text(product.name) == base["name"] else wb.tidy(product.name)
            verdict, value, why = self.compare(cells, field, normalise, base_value, current, equal, blank_unchanged)
            if verdict != "none":
                verdicts[field] = (verdict, base_value, value, current, why)
        if not verdicts:
            return
        if gone or (same_as is not None and not same_problem):
            for field, (_verdict, base_value, value, _current, _why) in verdicts.items():
                self.add(Kind.PRODUCT_FIELDS, sheet, number, State.CONFLICT if gone else State.PROBLEM,
                         gone or MERGED_AWAY, object_id=pk, fields={field: [base_value, value, None, value]},
                         data={**label, "column": field})
            return

        seen = {"name": product.name, "type": product.type, "set": product.set_id, "language": product.language,
                "barcode": product.ean, "release_date": product.release_date, "show": product.active}
        made = {}
        for field, (verdict, base_value, value, current, why) in verdicts.items():
            if verdict == "conflict":
                why = self.conflict_why(field, base_value, value, current, game_slug)
            elif verdict == "apply" and field == "barcode" and value and not wb.gs1_ok(value):
                verdict, why = "problem", f"{value} is not a barcode: its last digit does not check out."
            data = {**label, "column": field}
            if verdict == "problem" and value is None:
                data["typed"] = wb.clean_text(cells.get(field))
            if field == "set":
                data["set_name"] = self.shown("set", value)
            made[field] = self.field_change(Kind.PRODUCT_FIELDS, sheet, number, field, verdict, base_value, value,
                                            seen[field], why, object_id=pk, data=data)
        # A language is set through the name: "(Japanese)" is added when the name does not already say it.
        language = made.get("language")
        if language is None or language.state != State.READY:
            return
        rename = made.get("name") if made.get("name") and made["name"].state == State.READY else None
        name = rename.fields["name"][3] if rename else product.name
        moved = made.get("set")
        set_id = moved.fields["set"][3] if moved and moved.state == State.READY else product.set_id
        named, problem = self.name_for_language(product, name, set_id, language.fields["language"][1])
        if problem:
            language.state, language.why = State.PROBLEM, problem
            return
        if rename is None and named == product.name:
            # "Chinese" for a name that says Simplified Chinese, or a language the new set gives.
            said = languages.name(language_of(replace(product, name=name), self.set_row(set_id)))
            language.state, language.why = State.SAME, f"Already so: the site reads it as {said}."
            return
        if rename is not None:
            self.changes.remove(rename)
        if named != product.name:
            language.fields["name"] = [base_name,
                                       rename.fields["name"][1] if rename else product.name, product.name, named]

    def name_for_language(self, product, name, set_id, target):
        """(the name that gives the product the ``target`` language, problem)."""
        trial = replace(product, name=name)
        found = language_of(trial, self.set_row(set_id))
        if languages.same(found, target):
            return name, ""
        if target != languages.ENGLISH and not languages.stated(name, product.game_slug):
            named = f"{name} ({languages.NAMES[target]})"
            if len(named) <= 200 and languages.same(language_of(replace(trial, name=named), self.set_row(set_id)),
                                                   target):
                return named, ""
        return name, f"The name or set says {languages.name(found)}. Change the name or set."

    def same_new_row(self, sheet, ref, cells, now):
        """Whether this newN row is the one an earlier upload of the download added, by what that upload's row
        said or by the product or set as it is now: the same game and name (a product's name may have gained
        its language, "(Japanese)"), and for a product the same type."""
        game_id, _ = self.site.game_names(cells.get("game"))
        name = wb.as_text(cells.get("name"))[0]
        if sheet is wb.PRODUCTS:
            name = wb.tidy(name)
            game_slug = self.site.games[game_id][1] if game_id else ""
            kind = wb.as_type(cells.get("type"), game_slug)[0] if game_id else None
        for was in (self.added.get((sheet.name, ref)) or {}, now):
            named = was.get("name") or ""
            if was.get("game") != game_id or (sheet is wb.PRODUCTS and was.get("type") != kind):
                continue
            if named.casefold() == name.casefold() or (named.startswith(f"{name} (") and named.endswith(")")):
                return True
        return False

    def reused_why(self, ref, what, pk):
        used = {int(r[3:]) for r in (*self.refs, *self.set_refs, *self.reused, *self.new_rows, *self.new_sets)
                if isinstance(r, str) and r[3:].isdigit()}
        return (f"{ref} was used for {what} (id {pk}) in an earlier upload of this download. Number this row "
                f"new{max(used, default=0) + 1} instead, and the rows that name it.")

    def new_product(self, number, ref, cells):
        sheet = wb.PRODUCTS
        site = self.site
        if ref in self.reused:
            pk = self.reused[ref]
            self.add(Kind.NEW_PRODUCT, sheet, number, State.PROBLEM,
                     self.reused_why(ref, quoted(site.products[pk].name), pk), ref=ref,
                     data={"name": wb.clean_text(cells.get("name"))})
            return
        if ref in self.refs:
            pk = self.refs[ref]
            product = site.products.get(pk)
            if product is None:
                self.add(Kind.NEW_PRODUCT, sheet, number, State.CONFLICT,
                         f"Added from this file before as {pk}, and removed since.", ref=ref,
                         data={"name": wb.clean_text(cells.get("name"))})
                return
            self.add(Kind.NEW_PRODUCT, sheet, number, State.SAME, f"Already added (id {pk}).", ref=ref, object_id=pk,
                     data={"game": product.game_id, "game_slug": product.game_slug, "name": product.name,
                           "type": product.type, "set": product.set_id, "barcode": product.ean,
                           "release_date": product.release_date, "show": product.active,
                           "language": product.language})
            return
        problems = []
        game_id, problem = site.game_names(cells.get("game"))
        problems.append(problem)
        game_slug = site.games[game_id][1] if game_id else ""
        name, problem = wb.as_name(cells.get("name"))
        problems.append(problem)
        kind, problem = wb.as_type(cells.get("type"), game_slug) if game_id else (None, "")
        problems.append(problem)
        set_id, problem = self.set_names(cells.get("set"), game_id) if game_id else (None, "")
        problems.append(problem)
        barcode, problem = wb.as_barcode(cells.get("barcode"))
        problems.append(problem)
        if barcode and not wb.gs1_ok(barcode):
            problems.append(f"{barcode} is not a barcode: its last digit does not check out.")
        released, problem = wb.as_date(cells.get("release_date"))
        problems.append(problem)
        show, problem = wb.as_yes_no(cells.get("show"))
        problems.append(problem)
        language = None
        if not wb.blank(cells.get("language")):
            language, problem = wb.as_language(cells.get("language"))
            problems.append(problem)
        if not wb.blank(cells.get("same_as")) or not wb.blank(cells.get("not_same")):
            problems.append("A new row cannot use Same as product id or Not the same as. Add the product first.")
        data = {"game": game_id, "game_slug": game_slug, "name": name, "type": kind, "set": set_id,
                "set_name": self.shown("set", set_id) if set_id is not None else "", "barcode": barcode or "",
                "release_date": released, "show": True if show is None else show, "language": ""}
        problem = next((p for p in problems if p), "")
        if not problem:
            product = Prod(ref, game_id, game_slug, name, kind, set_id, "", barcode or "", released, data["show"])
            if language is not None:
                name, problem = self.name_for_language(product, name, set_id, language)
                data["name"] = product.name = name
            data["language"] = language_of(product, self.set_row(set_id))
        self.add(Kind.NEW_PRODUCT, sheet, number, State.PROBLEM if problem else State.READY, problem, ref=ref,
                 data=data)

    def merge_change(self, number, pk, typed, label, gone, merged):
        target, problem = self.resolve(typed)
        if not problem and target == pk:
            problem = "A product cannot be the same as itself."
        data = {**label, "target": typed if problem else target}
        if problem:
            self.add(Kind.MERGE, wb.PRODUCTS, number, State.PROBLEM, problem, object_id=pk, data=data)
            return
        earlier = self.site.merged_into(target) if isinstance(target, int) else None
        warning = f"{target} was merged into {earlier} before, so this merges into {earlier}." if earlier else ""
        if gone:
            data.update(gone=gone, merged=merged)
        self.add(Kind.MERGE, wb.PRODUCTS, number, State.READY, object_id=pk, data=data, warning=warning)

    def apart_change(self, number, pk, typed, label):
        other, problem = self.resolve(typed)
        if not problem and isinstance(other, str):
            problem = "A new product has no look-alikes yet."
        if not problem and other == pk:
            problem = "A product cannot be kept apart from itself."
        data = {**label, "target": typed if problem else other}
        if problem:
            self.add(Kind.APART, wb.PRODUCTS, number, State.PROBLEM, problem, object_id=pk, data=data)
            return
        if any(c.kind == Kind.APART and c.state != State.PROBLEM and {c.object_id, c.target_id} == {pk, other}
               for c in self.changes):
            return   # both rows name each other: one change
        state = State.SAME if frozenset((pk, other)) in self.site.apart else State.READY
        self.add(Kind.APART, wb.PRODUCTS, number, state, ALREADY if state == State.SAME else "", object_id=pk,
                 target_id=other, data=data)

    # Listings -------------------------------------------------------------------------------------------

    def read_listings(self):
        sheet = wb.LISTINGS
        site = self.site
        ignored = {link_key(item.url) for item in site.items.values() if item.status == ShopProduct.Status.IGNORED}
        marketplace_why = "Its prices come from searching for the product. Use Show on site No."
        for number, pk, cells, base in self.rows_of(sheet):
            page = site.pages.get(pk)
            take_off, take_problem = wb.as_yes_no(cells.get("not_this"))
            if page is None:
                label = {"link": base["link"]}
                if take_off and link_key(base["link"]) in ignored:
                    self.add(Kind.TAKE_OFF, sheet, number, State.SAME, ALREADY, object_id=pk, data=label)
                    continue
                moving, *_ = self.compare(cells, "product", self.as_product, base["product"], base["product"])
                showing, *_ = self.compare(cells, "show", wb.as_yes_no, base["show"], base["show"], blank_unchanged=True)
                if take_off or moving != "none" or showing != "none":
                    kind = Kind.TAKE_OFF if take_off else Kind.MOVE if moving != "none" else Kind.HIDE
                    self.add(kind, sheet, number, State.CONFLICT, "This shop's page was removed since your download.",
                             object_id=pk, data=label)
                continue
            product = site.products.get(page.product_id)
            label = {"title": page.title or page.url, "shop": site.shop_name(page.retailer_id),
                     "product": page.product_id, "product_name": product.name if product else "", "link": base["link"]}
            marketplace = site.is_marketplace(page.retailer_id)
            verdict, value, why = self.compare(cells, "product", self.as_product, base["product"], page.product_id)
            if take_problem:
                self.add(Kind.TAKE_OFF, sheet, number, State.PROBLEM, take_problem, object_id=pk, data=dict(label))
            elif take_off and verdict == "apply":
                both = "Not this product and a new Product id on one row. Do one or the other."
                self.add(Kind.TAKE_OFF, sheet, number, State.PROBLEM, both, object_id=pk, data=dict(label))
                verdict, why = "problem", both
            elif take_off:
                self.add(Kind.TAKE_OFF, sheet, number, State.PROBLEM if marketplace else State.READY,
                         marketplace_why if marketplace else "", object_id=pk, data=dict(label))
            if verdict != "none":
                target = value
                if verdict == "apply":
                    target, problem = self.resolve(value)
                    if problem or marketplace:
                        verdict, why = "problem", problem or marketplace_why
                elif verdict == "conflict":
                    why = (f"You changed Product id from {base['product']} to {value}, but the site has "
                           f"{page.product_id} since your download. Left as {page.product_id}.")
                self.field_change(Kind.MOVE, sheet, number, "product", verdict, base["product"], value,
                                  page.product_id, why, object_id=pk,
                                  data={**label, "target": value if verdict == "problem" else target, "column": "product"})
            verdict, value, why = self.compare(cells, "show", wb.as_yes_no, base["show"], page.active,
                                               blank_unchanged=True)
            if verdict == "none":
                continue
            if take_off:
                verdict, why = "problem", "Not this product takes it off, so Show on site is left."
            elif verdict == "conflict":
                why = self.conflict_why("show", base["show"], value, page.active)
            self.field_change(Kind.HIDE if value is False else Kind.SHOW, sheet, number, "show", verdict, base["show"],
                              value, page.active, why, object_id=pk, data={**label, "column": "show"})

    # Shop items and left-out items ------------------------------------------------------------------------

    def decision(self, cells, choices):
        """(action, typed product, problem) from Decision and Link to product id: action is link, ignore,
        review, problem or None."""
        decision, problem = wb.as_choice(cells.get("decision"), choices)
        typed, ref_problem = wb.as_ref(cells.get("link_to"))
        problem = problem or ref_problem
        if problem:
            return "problem", typed, problem
        if typed is not None and decision in ("Not ours", "Look again"):
            return "problem", typed, f"Link to product id and {decision} on one row. Do one or the other."
        if decision == "Link" or (typed is not None and decision is None):
            return "link", typed, ""
        return {"Not ours": "ignore", "Look again": "review"}.get(decision), typed, ""

    def read_shop_items(self):
        sheet = wb.SHOP_ITEMS
        site = self.site
        for number, pk, cells, base in self.rows_of(sheet):
            action, typed, problem = self.decision(cells, wb.DECISIONS)
            if action is None:
                continue
            kind = {"ignore": Kind.IGNORE, "review": Kind.REVIEW}.get(action, Kind.LINK)
            item = site.items.get(pk)
            data = {"title": item.title if item else "", "shop": site.shop_name(item.retailer_id) if item else "",
                    "price": str(item.price) if item and item.price is not None else None,
                    "seen": item.last_seen.isoformat() if item else None, "typed": typed,
                    "status": base["status"], "suggested": base["suggested"]}
            if not problem and item is None:
                self.add(kind, sheet, number, State.CONFLICT, "This shop item was removed since your download.",
                         object_id=pk, data=data)
                continue
            target = None
            if not problem and action == "link":
                target, problem = self.resolve(typed) if typed is not None else (base["suggested"], "")
            data["target"] = target
            if problem:
                self.add(kind, sheet, number, State.PROBLEM, problem, object_id=pk, data=data)
                continue
            already = {"ignore": ShopProduct.Status.IGNORED, "review": ShopProduct.Status.REVIEW}.get(action)
            if already and item.status == already:
                self.add(kind, sheet, number, State.SAME, ALREADY, object_id=pk, data=data)
            elif already and (item.status, item.suggested_id) != (base["status"], base["suggested"]):
                self.add(kind, sheet, number, State.CONFLICT,
                         "Its status or suggested product changed since your download.", object_id=pk, data=data)
            else:
                # A link is judged with the merges in the file: see guard_link.
                self.add(kind, sheet, number, State.READY, object_id=pk, data=data)

    def read_left_out(self):
        sheet = wb.LEFT_OUT
        site = self.site
        retailer = self.export.left_out_retailer_id
        if sheet.name in self.parsed.sheets and retailer is None:
            self.row_problem(sheet, 0, None, "The shop this sheet came from has been removed.",
                             what="Nothing on Left out was read.")
            return
        for number, key, cells, base in self.rows_of(sheet):
            action, typed, problem = self.decision(cells, wb.LEFT_DECISIONS)
            if action is None:
                continue
            kind = Kind.LEFT_IGNORE if action == "ignore" else Kind.LEFT_LINK
            data = {"title": base["title"], "shop": site.shop_name(retailer), "url": base["link"],
                    "barcode": base["barcode"], "retailer": retailer, "typed": typed, "target": None}
            if not problem and action == "link" and typed is None:
                problem = "Type the product id in Link to product id."
            if not problem and action == "link":
                data["target"], problem = self.resolve(typed)
            if problem:
                self.add(kind, sheet, number, State.PROBLEM, problem, ref=key, data=data)
                continue
            item = site.items.get(site.item_at.get((retailer, link_key(base["link"]))))
            if item is None:
                self.add(kind, sheet, number, State.READY, ref=key, data=data)
            elif action == "ignore" and item.status == ShopProduct.Status.IGNORED:
                self.add(kind, sheet, number, State.SAME, ALREADY, ref=key, data=data)
            elif action == "link" and item.status == ShopProduct.Status.LINKED:
                # Linked to the product kept once the file's merges are made is already so: see guard_left_link.
                self.add(kind, sheet, number, State.READY, ref=key, data={**data, "linked": item.suggested_id})
            else:
                self.add(kind, sheet, number, State.CONFLICT, "The shop's read has found this item since your "
                         "download, so it is on Shop items in a fresh file.", ref=key, data=data)

    # Whole columns --------------------------------------------------------------------------------------

    def column_guards(self):
        """A Name or Barcode column sorted on its own is refused whole. A column changed on a quarter of its
        rows is held: a sort may have mixed it up."""
        by_column = defaultdict(list)
        for change in self.changes:
            column = change.data.get("column")
            if column and change.state != State.PROBLEM:
                by_column[(change.sheet, column)].append(change)
        for (sheet_name, column), changes in by_column.items():
            sheet = wb.SHEET_BY_NAME[wb.fold(sheet_name)]
            if column in ("name", "barcode") and len(changes) >= SHUFFLE_EDITS:
                if self.borrowed(sheet_name, column, changes) * 2 >= len(changes):
                    for change in changes:
                        self.changes.remove(change)
                    heading = HEADINGS[column]
                    self.row_problem(sheet, 0, None, f"{heading}s look sorted on their own, so nothing in this "
                                     "column was changed.", what=f"Nothing in {heading} on {sheet_name} was changed.")
                    continue
            rows = len(self.parsed.sheets.get(sheet_name, []))
            if len(changes) >= MASS_EDITS and len(changes) > MASS_SHARE * rows:
                for change in changes:
                    if change.state == State.READY:
                        change.state, change.why = State.HELD, MASS

    def borrowed(self, sheet_name, column, changes):
        """How many of a column's changes are another row's value as downloaded."""
        def comparable(value):
            if column == "barcode":
                return (value or "").lstrip("0")
            if sheet_name == wb.PRODUCTS.name:
                return wb.tidy(value)
            return value or ""

        rows = defaultdict(set)
        for key, row in self.base.get(sheet_name, {}).items():
            value = comparable(row.get(column))
            if value:
                rows[value].add(int(key))
        count = 0
        for change in changes:
            value = comparable(change.fields[column][1]) if column in change.fields else ""
            if value and rows.get(value, set()) - {change.object_id}:
                count += 1
        return count

    # Judging against the site once the file is made ------------------------------------------------------

    def judge(self):
        """Judge every ready and held change against the final state. A change refused, found already so or
        found changed since leaves the final state, which is then built again, until nothing more moves."""
        set_rows = {**self.site.sets, **self.new_sets}
        self.set_choices = {}
        settled_states = (State.PROBLEM, State.SAME, State.CONFLICT)
        for _round in range(10):
            live = [change for change in self.changes if change.state in LIVE]
            state = FinalState(self.site, live, set_rows)
            verdicts = [(change, self.guard(change, state)) for change in live]
            settled = [(change, verdict) for change, verdict in verdicts if verdict and verdict[0] in settled_states]
            for change, (verdict_state, why, *_rest) in settled:
                change.state, change.why = verdict_state, why
            if not settled:
                break
        for change, verdict in verdicts:
            if not verdict or change.state not in LIVE:
                continue
            verdict_state, why, *rest = verdict
            if rest and rest[0]:
                change.warning = rest[0]
            if verdict_state == State.HELD:
                change.why = " ".join(filter(None, [change.why if change.state == State.HELD else "", why]))
                change.state = State.HELD
        self.hold_dependents(state, set_rows)
        self.finish(state)

    def hold_dependents(self, state, set_rows):
        """Make N changes makes the ready ones without the held ones, so each ready change must stand on its
        own: one judged otherwise, or landing on another product, once the held changes are left out is held
        with them. Holding one can unsettle another, so this goes on until nothing more moves."""
        while any(change.state == State.HELD for change in self.changes):
            ready = [change for change in self.changes if change.state == State.READY]
            alone = FinalState(self.site, ready, set_rows)
            moved = False
            for change in ready:
                if self.stands(change, alone) != self.stands(change, state):
                    change.state, change.why = State.HELD, DEPENDS
                    moved = True
            if not moved:
                return

    def stands(self, change, state):
        """(verdict, the product it lands on) for a change in ``state``, leaving the change as it was."""
        trial = copy(change)
        trial.data = dict(change.data)
        verdict = self.guard(trial, state)
        target = change.data.get("target") if change.kind in TARGETED else None
        return (verdict[0] if verdict else State.READY), (state.keeper(target) if target is not None else None)

    @property
    def site_index(self):
        """The site's products by the hourly tidy-up's keys now, made once and only when a change needs it."""
        if getattr(self, "_site_index", None) is None:
            self._site_index = Index(self.site.products)
        return self._site_index

    def guard(self, change, state):
        """(state, why[, warning]) when the final state decides this change, else None."""
        method = getattr(self, f"guard_{change.kind}", None)
        return method(change, state) if method else None

    def product_name(self, state, pk):
        product = state.products.get(pk) or self.site.products.get(pk)
        return product.name if product else ""

    def named(self, state, pk):
        return f"{pk} {quoted(self.product_name(state, pk))}"

    def missing(self, ref):
        if ref is None:
            return "These products name each other in Same as product id, so none is merged."
        if isinstance(ref, str):
            return f"The new product {ref} is not added, so this is not made either."
        return f"No product has id {ref}."

    def guard_product_fields(self, change, state):
        pk = change.object_id
        product = state.products[pk]
        fields = change.fields
        # Shown again, a hidden product meets the hourly merge too, which deletes it rather than switching it off.
        if fields.keys() & {"name", "type", "set", "language"} or ("show" in fields and fields["show"][3] is True):
            new = state.index.collisions(pk, state.products) - self.site_index.collisions(pk, self.site.products)
            if new:
                other = min(new, key=str)
                return (State.PROBLEM, f"The hourly tidy would join this with {self.named(state, other)}. If they "
                        f"are one product, put {other} in Same as product id.")
        if "barcode" in fields and fields["barcode"][3]:
            owners = state.barcode_owners(fields["barcode"][3], but=pk)
            if owners:
                other = min(owners, key=str)
                return (State.PROBLEM, f"Already on {self.named(state, other)}. Two products with one barcode steer "
                        "shop prices to one of them. Merge them, or fix the other one's barcode in this file.")
        if "set" in fields:
            new_set = fields["set"][3]
            if isinstance(new_set, str) and new_set not in state.new_sets:
                return State.PROBLEM, f"The new set {new_set} on Sets is not added, so this is not made either."
            if new_set is None:
                chosen = self.set_chosen(product)
                if chosen is not None:
                    return (State.PROBLEM, f"The name names {self.set_row(chosen).name}, so the hourly filing puts "
                            "it back. Pick the right set or change the name.")
        if "name" in fields:
            before = self.site.products[pk]
            for retailer, entries in state.pages[pk].items():
                for whose, _key, url in entries:
                    # Its own pages; a page moved or linked here is judged by that change.
                    if (whose[0] == "listing" and self.site.pages[whose[1]].product_id == pk
                            and page_fits(url, before) and not page_fits(url, product)):
                        shop = possessive(self.site.shop_name(retailer))
                        return State.HELD, f"{FITS} The new name contradicts {shop} page {url}."
        unlisted = not state.listed(pk) and not product.image
        if "show" in fields and fields["show"][3] is False and unlisted and not product.release_date:
            return State.HELD, f"{TIDY_REMOVES} Hiding it removes it for good."
        if "release_date" in fields and fields["release_date"][3] is None and unlisted:
            return State.HELD, f"{TIDY_REMOVES} Clearing its date removes it."
        return None

    def guard_set_fields(self, change, state):
        """A set's new name or code gives its products another language at once, and with it maybe a twin the
        hourly merge joins (and deletes)."""
        for pk in self.relanguaged(change, state):
            new = state.index.collisions(pk, state.products) - self.site_index.collisions(pk, self.site.products)
            if new:
                other = min(new, key=str)
                return (State.PROBLEM, f"{self.named(state, pk)} becomes "
                        f"{languages.name(state.products[pk].language)}, and the hourly tidy would join it with "
                        f"{self.named(state, other)}. If they are one product, put {other} in its Same as product id.")
        return None

    def relanguaged(self, change, state):
        """The products of the set whose language this change of its name or code changes."""
        if change.kind != Kind.SET_FIELDS or not change.fields.keys() & {"name", "code"}:
            return []
        set_pk = change.object_id
        return [pk for pk in state.in_sets.get(set_pk, ()) if state.products[pk].set_id == set_pk
                and state.products[pk].language != self.site.products[pk].language]

    def set_chosen(self, product):
        """The set the hourly filing (releases.attach_sets) would put a set-less product under, or None."""
        from .releases import choose_set, set_rules

        if product.game_id not in self.set_choices:
            sets = [(row.pk, row.name, row.code) for row in (*self.site.sets.values(), *self.new_sets.values())
                    if row.game_id == product.game_id]
            self.set_choices[product.game_id] = set_rules(product.game_slug, sets)
        rules = self.set_choices[product.game_id]
        return choose_set(product.name, rules, product.game_slug) if rules else None

    def guard_new_product(self, change, state):
        ref = change.ref
        product = state.products[ref]
        d = change.data
        if isinstance(d["set"], str) and d["set"] not in state.new_sets:
            return State.PROBLEM, f"The new set {d['set']} on Sets is not added, so this is not added either."
        new = state.index.collisions(ref, state.products)
        if new:
            other = min(new, key=str)
            return (State.PROBLEM, f"The hourly tidy would join this with {self.named(state, other)}. If they are one "
                    "product, link its shops to that one instead.")
        if d["barcode"]:
            owners = state.barcode_owners(d["barcode"], but=ref)
            if owners:
                other = min(owners, key=str)
                return (State.PROBLEM, f"Its barcode is already on {self.named(state, other)}. Two products with one "
                        "barcode steer shop prices to one of them.")
        if not product.release_date and not state.priced.get(ref):
            return (State.PROBLEM, f"{TIDY_REMOVES} Give it a release date, or link a shop's priced item to it in "
                    "this file.")
        return None

    def guard_merge(self, change, state):
        pk = change.object_id
        keep = state.keeper(pk)
        d = change.data
        if d.get("gone"):
            if keep is not None and d.get("merged") == keep:
                return State.SAME, f"Already merged into {keep}."
            return State.CONFLICT, d["gone"]
        if keep is None or keep not in state.products:
            return State.PROBLEM, self.missing(keep)
        other, kept = state.before_merge[pk], state.products[keep]
        if kept.game_id != other.game_id:
            return State.PROBLEM, f"{keep} is a product of another game."
        if not kept.active:
            return State.PROBLEM, f"{self.named(state, keep)} is hidden from the site. Show it, or merge into another."
        if not languages.same(other.language, kept.language):
            return (State.PROBLEM, f"This is {languages.name(other.language)} and {keep} is "
                    f"{languages.name(kept.language)}, so they are not one product.")
        d["shops"] = len({retailer for retailer, _url in state.carried.get(pk, ())})
        warning = change.warning
        if other.type != kept.type:
            warning = " ".join(filter(None, [warning, f"Their types differ: {keep} keeps its type, "
                                             f"{types.type_label(kept.game_slug, kept.type)}."]))
        # Against the product kept as the file's earlier merges into it leave it.
        both = sorted(self.site.shop_name(r) for r in state.stranded.get(pk, ()) if not self.site.is_marketplace(r))
        if both:
            return (State.HELD, f"{both[0]} sells both on separate pages. After the merge its second page sits on the "
                    "switched-off product and is not shown. Fix it in Listings first, or make the merge anyway.",
                    warning)
        for retailer, url in state.carried.get(pk, ()):
            if page_fits(url, other) and not page_fits(url, kept):
                shop = possessive(self.site.shop_name(retailer))
                return (State.HELD, f"{FITS} {shop} page moves across, and its address contradicts "
                        f"{self.named(state, keep)}: {url}.", warning)
        if ean_key(other.ean) and ean_key(kept.ean) and ean_key(other.ean) != ean_key(kept.ean):
            return (State.HELD, f"They have different barcodes, {other.ean} and {kept.ean}. Check they are one "
                    "product.", warning)
        if frozenset((pk, keep)) in self.site.apart:
            return State.HELD, "You said before that these are not the same product.", warning
        return State.READY, "", warning

    def guard_apart(self, change, state):
        first, second = state.keeper(change.object_id), state.keeper(change.target_id)
        if first is not None and first == second:
            return State.PROBLEM, "The same pair merges in this file."
        return None

    def guard_take_off(self, change, state):
        page = self.site.pages[change.object_id]
        owners = state.barcode_owners(page.shop_ean)
        if owners:
            other = min(owners, key=str)
            return (State.PROBLEM, f"The shop gives the barcode {page.shop_ean}, which is on "
                    f"{self.named(state, other)}, so the shop's next read links it again. Fix that barcode first, "
                    "or use Show on site No.")
        if self.left_unlisted(page.product_id, state):
            return State.HELD, f"{TIDY_REMOVES} Taking its last page off removes it, with your other changes to it."
        return None

    def left_unlisted(self, pk, state):
        """Whether the hourly tidy-up removes this product once the file is made: no listing, no release date and
        no image. One merged away is kept for Undo."""
        product = state.products.get(pk)
        if product is None or pk in state.edges or self.site.merged_into(pk):
            return False
        return not product.release_date and not product.image and not state.listed(pk)

    def guard_move(self, change, state):
        page = self.site.pages[change.object_id]
        keep = state.keeper(change.data["target"])
        if keep is None or keep not in state.products:
            return State.PROBLEM, self.missing(keep if keep is not None else None)
        if keep == page.product_id:
            return State.SAME, ALREADY
        product = state.products[keep]
        if not product.active:
            return State.PROBLEM, f"{self.named(state, keep)} is hidden from the site."
        shop = self.site.shop_name(page.retailer_id)
        others = state.others_at(keep, page.retailer_id, ("listing", page.pk))
        if others:
            return (State.PROBLEM, f"{shop} already has a page on {keep}: {others[0][2]}. Either that page or this "
                    "one is a different product.")
        there = self.site.page_at.get((keep, page.retailer_id))
        if there is not None and there in state.moved:
            return (State.PROBLEM, f"{shop}'s page on {keep} moves too. Move one page at a time: upload, then move "
                    "the other.")
        owners = state.barcode_owners(page.shop_ean, but=keep)
        if owners:
            other = min(owners, key=str)
            return (State.PROBLEM, f"The shop gives the barcode {page.shop_ean}, which is on "
                    f"{self.named(state, other)}, so the shop's next read puts it back there.")
        if self.left_unlisted(page.product_id, state):
            return (State.HELD, f"{TIDY_REMOVES} Moving its last page removes {self.named(state, page.product_id)}, "
                    "with your other changes to it.")
        if not page_fits(page.url, product):
            return State.HELD, f"{FITS} Check its address names {self.named(state, keep)}."
        return None

    def guard_show(self, change, state):
        page = self.site.pages[change.object_id]
        if not self.site.shops[page.retailer_id][2]:
            shop = self.site.shop_name(page.retailer_id)
            return State.PROBLEM, f"{shop} is switched off, so its prices are not shown."
        product = state.products.get(state.moved.get(page.pk, page.product_id))
        if product is None or not product.active:
            return State.PROBLEM, "Its product is hidden from the site, so its prices are not shown."
        return None

    def guard_link(self, change, state):
        item = self.site.items[change.object_id]
        d = change.data
        if d["target"] is None:
            return State.PROBLEM, "No product to link to. Type one in Link to product id."
        keep = state.keeper(d["target"])
        if keep is None or keep not in state.products:
            return State.PROBLEM, self.missing(keep if keep is not None else None)
        there = self.site.page_at.get((keep, item.retailer_id))
        if there is not None and link_key(self.site.pages[there].url) == link_key(item.url):
            return State.SAME, ALREADY
        if item.status == ShopProduct.Status.LINKED and item.suggested_id == keep and d["status"] != item.status:
            return State.SAME, "Already linked, waiting for the shop's next read."
        if (item.status, item.suggested_id) != (d["status"], d["suggested"]):
            return State.CONFLICT, "Its status or suggested product changed since your download."
        product = state.products[keep]
        if not product.active:
            return State.PROBLEM, f"{self.named(state, keep)} is hidden from the site."
        shop = self.site.shop_name(item.retailer_id)
        others = state.others_at(keep, item.retailer_id, ("item", item.pk))
        if others:
            return (State.PROBLEM, f"{shop} already shows {quoted(product.name)} on {others[0][2]}. One product has "
                    "one page per shop. If this page is another product, add it as a new row on Products and link "
                    "it to that.")
        owners = state.barcode_owners(item.shop_ean, but=keep)
        if owners:
            other = min(owners, key=str)
            return (State.PROBLEM, f"The shop gives the barcode {item.shop_ean}, which is on "
                    f"{self.named(state, other)}, so the shop's next read links it there.")
        warning = ""
        if item.price is None or item.last_seen < self.now - STALE_SEEN:
            warning = "The price shows once the shop's next read checks it."
        if not page_fits(item.url, product):
            return State.HELD, f"{FITS} Check its address names {self.named(state, keep)}.", warning
        return (State.READY, "", warning) if warning else None

    def guard_left_link(self, change, state):
        d = change.data
        keep = state.keeper(d["target"])
        if keep is None or keep not in state.products:
            return State.PROBLEM, self.missing(keep if keep is not None else None)
        if d.get("linked") is not None or "linked" in d:
            if d["linked"] == keep:
                return State.SAME, ALREADY
            return (State.CONFLICT, "The shop's read has found this item since your download, so it is on Shop "
                    "items in a fresh file.")
        if not state.products[keep].active:
            return State.PROBLEM, f"{self.named(state, keep)} is hidden from the site."
        others = state.others_at(keep, d["retailer"], ("left", change.ref))
        if others:
            return (State.PROBLEM, f"{d['shop']} already has a page on {keep}: {others[0][2]}. One product has one "
                    "page per shop.")
        if not page_fits(d["url"], state.products[keep]):
            return State.HELD, f"{FITS} Check its address names {self.named(state, keep)}."
        return None

    def finish(self, state):
        """Name each change's final product, and note a language a rename or a set change brings."""
        noted = set()
        for change in self.changes:
            d = change.data
            if change.state in LIVE and change.object_id not in noted:
                after = Counter(state.products[pk].language for pk in self.relanguaged(change, state))
                if after:
                    noted.add(change.object_id)
                    d["languages_after"] = sorted(after.items(), key=lambda pair: (-pair[1], pair[0]))
            if (change.kind in (Kind.MOVE, Kind.LINK, Kind.LEFT_LINK, Kind.MERGE) and d.get("target") is not None
                    and change.state in (*LIVE, State.SAME)):
                keep = state.keeper(d["target"])
                if isinstance(keep, int) and not isinstance(keep, bool):
                    change.target_id = keep
                elif isinstance(keep, str):
                    d["target_ref"] = keep
                if keep is not None:
                    d["target_name"] = self.product_name(state, keep)
            elif change.kind == Kind.APART and change.target_id is not None:
                d["target_name"] = self.product_name(state, change.target_id)
            if change.kind == Kind.PRODUCT_FIELDS and change.state in LIVE and change.object_id in state.products:
                if change.fields.keys() & {"name", "set"} and "language" not in change.fields:
                    after = state.products[change.object_id].language
                    if after != self.site.products[change.object_id].language:
                        d["language_after"] = after
            if change.object_id in self.site.products and change.kind in (Kind.PRODUCT_FIELDS, Kind.MERGE, Kind.APART):
                d["game_slug"] = self.site.products[change.object_id].game_slug


def plan(export, parsed, now=None):
    """The changes an uploaded file makes, unsaved, in the order they are made. Writes nothing. Raises
    workbook.FileRefused when the file makes too many."""
    return Planner(export, parsed, now).run()


def export_for(reference):
    """The download a file came from, by the number in it. Raises workbook.FileRefused when the number is
    missing or wrong, or its download is too old to compare with."""
    if reference is None:
        raise wb.FileRefused(wb.LOST_NUMBER)
    pk, token = reference
    export = DataExport.objects.defer("workbook").filter(pk=pk).first()
    if export is None or export.token != token or export.base is None:
        raise wb.FileRefused(wb.LOST_NUMBER)
    return export


# Plain words -----------------------------------------------------------------------------------------------

def possessive(shop):
    return f"{shop}'" if shop.endswith("s") else f"{shop}'s"


def describe(change):
    """The change in one or two plain sentences, as the preview lists it."""
    d = change.data
    if d.get("what"):
        return d["what"]
    where = f"({change.sheet} row {change.row})"
    kind = change.kind
    if kind == Kind.PRODUCT_FIELDS:
        return describe_fields(change, where)
    if kind == Kind.NEW_PRODUCT:
        details = [types.type_label(d.get("game_slug", ""), d["type"]) if d.get("type") else ""]
        if d.get("set_name"):
            details.append(f"in {d['set_name']}")
        if d.get("release_date"):
            details.append(f"out {day_text(d['release_date'])}")
        if d.get("barcode"):
            details.append(f"barcode {d['barcode']}")
        details = ", ".join(part for part in details if part)
        text = f"Add {change.ref} {quoted(d.get('name'))}" + (f": {details}" if details else "") + f" {where}."
        return text + (" It stays hidden from the site." if d.get("show") is False else "")
    if kind == Kind.NEW_SET:
        code = f" ({d['code']})" if d.get("code") else ""
        out = f", out {day_text(d['release_date'])}" if d.get("release_date") else ""
        return f"Add the set {quoted(d.get('name'))}{code}{out} {where}."
    if kind == Kind.SET_FIELDS:
        field, (base, file, _seen, _new) = next(iter(change.fields.items()))
        name = f"the set {change.object_id} {quoted(d.get('name'))}"
        # The site works out the language of every product in the set again when it is saved.
        after = "".join(f" {n:,} product{'s become' if n != 1 else ' becomes'} {languages.name(code)}."
                        for code, n in d.get("languages_after") or [])
        if field == "name":
            name = f"the set {change.object_id} {quoted(d.get('name'), SHORT)}"
            return f"Rename {name} to {quoted(file, WHOLE)} {where}.{after}"
        if field == "code":
            return f"Change the code of {name} to {file or 'none'} (was {base or 'none'}) {where}.{after}"
        return (f"Change the release date of {name} to {day_text(file)} (was {day_text(base)}) {where}. Release "
                "sources never change a date you set.")
    if kind in (Kind.TAKE_OFF, Kind.MOVE, Kind.HIDE, Kind.SHOW):
        page = f"{possessive(d['shop'])} {quoted(d.get('title'))}" if d.get("shop") else f"the page {d.get('link')}"
        on = f"{d['product']} {quoted(d.get('product_name'))}" if d.get("product") else "its product"
        if kind == Kind.TAKE_OFF:
            return f"Take {page} off {on} {where}. Its page is never matched to a product by name again."
        if kind == Kind.MOVE:
            return f"Move {page} from {on} to {target_words(change)} {where}."
        if kind == Kind.HIDE:
            return f"Hide {page} on {on} {where}."
        return f"Show {page} on {on} again {where}."
    if kind == Kind.MERGE:
        text = f"Merge {change.object_id} {quoted(d.get('name'))} into {target_words(change)} {where}."
        shops = d.get("shops")
        if shops:
            text += f" Its {shops} shop{'s move' if shops != 1 else ' moves'} across and its address redirects."
        else:
            text += " Its address redirects."
        return text + " Can be undone."
    if kind == Kind.APART:
        name = f" {quoted(d['target_name'])}" if d.get("target_name") else ""
        return (f"Keep {change.object_id} {quoted(d.get('name'))} and {change.target_id or d.get('target')}{name} "
                f"apart {where}. They are not suggested as duplicates again.")
    left = "left-out " if kind in (Kind.LEFT_LINK, Kind.LEFT_IGNORE) else ""
    item = f"{possessive(d.get('shop') or 'The shop')} {left}{quoted(d.get('title'))}"
    if kind == Kind.LINK:
        seen = date.fromisoformat(d["seen"][:10]) if d.get("seen") else None
        price = money(float(d["price"])) if d.get("price") else "no price"
        when = f", seen {seen.day} {seen:%b}" if seen else ""
        text = f"Link {item} ({price}{when}) to {target_words(change)} {where}."
        return text + (" The shop's next read checks its price." if d.get("price") else
                       " Its price shows after the shop's next read.")
    if kind == Kind.LEFT_LINK:
        return f"Link {item} to {target_words(change)} {where}. The shop's next read adds its price."
    if kind in (Kind.IGNORE, Kind.LEFT_IGNORE):
        return f"Mark {item} as not ours {where}. Its page is never matched by name again."
    return f"Put {item} back to look at again {where}."


def target_words(change):
    d = change.data
    target = d.get("target_ref") or change.target_id or d.get("target") or d.get("typed") or "a product"
    name = f" {quoted(d['target_name'])}" if d.get("target_name") else ""
    return f"{target}{name}"


def describe_fields(change, where):
    d = change.data
    game = d.get("game_slug", "")
    who = f"{change.object_id} {quoted(d.get('name'))}"
    renamed = f"{change.object_id} {quoted(d.get('name'), SHORT)}"
    fields = change.fields
    if "language" in fields:
        target = languages.name(fields["language"][1])
        if "name" in fields:
            return f"Make {renamed} {target}: its name becomes {quoted(fields['name'][3], WHOLE)} {where}."
        return f"Make {who} {target} {where}."
    field, (base, _file, _seen, new) = next(iter(fields.items()))
    if new is None and d.get("typed") is not None:
        return f"Change {HEADINGS[field]} of {who} to {quoted(d['typed'])} {where}."
    if field == "name":
        text = f"Rename {renamed} to {quoted(new, WHOLE)} {where}."
    elif field == "type":
        text = (f"Change {who} from {types.type_label(game, base) if base else 'none'} to "
                f"{types.type_label(game, new) if new else 'none'} {where}. The hourly retype leaves a type you set.")
    elif field == "set":
        text = (f"File {who} under the set {d.get('set_name') or new} {where}." if new is not None else
                f"Take {who} out of its set {where}.")
    elif field == "barcode":
        text = (f"Give {who} the barcode {new} (was {base or 'none'}) {where}." if new else
                f"Clear the barcode of {who} (was {base}) {where}.")
    elif field == "release_date":
        text = (f"Set the release date of {who} to {day_text(new)} (was {day_text(base)}) {where}." if new else
                f"Clear the release date of {who} (was {day_text(base)}) {where}.")
    else:
        text = f"Show {who} on the site {where}." if new else f"Hide {who} from the site {where}."
    if d.get("language_after") is not None:
        text += f" Its language becomes {languages.name(d['language_after'])}."
    return text


SUMMARY_WORDS = {
    Kind.PRODUCT_FIELDS: ("product change", "product changes"), "name": ("name", "names"),
    "type": ("type", "types"), "set": ("set", "sets"),
    "language": ("language", "languages"), "barcode": ("barcode", "barcodes"),
    "release_date": ("release date", "release dates"),
    "show": ("product shown or hidden", "products shown or hidden"),
    Kind.NEW_SET: ("new set", "new sets"), Kind.SET_FIELDS: ("set change", "set changes"),
    Kind.NEW_PRODUCT: ("new product", "new products"), Kind.TAKE_OFF: ("page taken off", "pages taken off"),
    Kind.MOVE: ("move", "moves"), Kind.HIDE: ("price hidden", "prices hidden"),
    Kind.SHOW: ("price shown", "prices shown"), Kind.MERGE: ("merge", "merges"),
    Kind.APART: ("pair kept apart", "pairs kept apart"), Kind.LINK: ("link", "links"),
    Kind.IGNORE: ("item not ours", "items not ours"), Kind.REVIEW: ("item to look at again", "items to look at again"),
    Kind.LEFT_LINK: ("left-out link", "left-out links"),
    Kind.LEFT_IGNORE: ("left-out item not ours", "left-out items not ours"),
}


def counted_as(change):
    """What a change counts as in the summary: a product change by the column it changes."""
    if change.kind == Kind.PRODUCT_FIELDS and change.fields:
        return "language" if "language" in change.fields else next(iter(change.fields))
    return change.kind


def summary(changes):
    """'This file makes 142 changes: 40 names, 12 types, 18 merges, 9 links. 3 are held for your say, 5 could
    not be made, 2 were changed on the site since your download, 12 are already so.'"""
    states = defaultdict(int)
    kinds = defaultdict(int)
    for change in changes:
        states[change.state] += 1
        if change.state == State.READY:
            kinds[counted_as(change)] += 1
    ready = states[State.READY]
    if ready:
        parts = [f"{n:,} {SUMMARY_WORDS[kind][n != 1]}" for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1])]
        text = f"This file makes {ready:,} change{'s' if ready != 1 else ''}: {', '.join(parts)}."
    else:
        text = "This file makes no changes."
    rest = []
    for state, one, many in (
        (State.HELD, "is held for your say", "are held for your say"),
        (State.PROBLEM, "could not be made", "could not be made"),
        (State.CONFLICT, "was changed on the site since your download", "were changed on the site since your download"),
        (State.SAME, "is already so", "are already so"),
    ):
        if states[state]:
            rest.append(f"{states[state]:,} {one if states[state] == 1 else many}")
    return text + (" " + ", ".join(rest) + "." if rest else "")
