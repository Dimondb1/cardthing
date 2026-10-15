"""
The owner's own edits, one at a time, through the code paths his buttons use, so each leaves the same trail:
a merge listed under Sorted for you with Undo, a type the hourly retype leaves alone, a Not ours the next
shop read respects, an answer Claude is never asked again.

His uploaded spreadsheet (catalogue/data_jobs.py) makes every change through ``apply_change`` and takes it
back through ``undo``. None of these opens a transaction: the caller makes each change in its own, so one
that cannot be made rolls back alone. Each raises Stale when the site no longer holds what he saw when the
file was checked, and Refused when a rule says no.
"""

from typing import NamedTuple

from django import forms
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.admin.utils import construct_change_message
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from . import autopilot, finder, judge, languages, pricing, releases, sanity
from .autopilot import Stale
from .importers import ean_key, link_key
from .models import (
    CheckAnswer, DataChange, Game, Listing, OutboundClick, Product, ProductAlias, ProductSet, Restock, Retailer, ShopPage,
    ShopProduct, StockAlert, StockistSearch,
)

Kind = DataChange.Kind
Status = ShopProduct.Status

# A DataChange field and the Product field it changes.
PRODUCT_FIELDS = {"name": "name", "type": "product_type", "set": "product_set", "barcode": "ean",
                  "release_date": "release_date", "show": "is_active"}


class Refused(Exception):
    """A rule says no. The message is the sentence the owner reads."""


class ProductEditForm(forms.ModelForm):
    """The product fields the spreadsheet may change, validated as Django Admin validates them: Product.clean
    refuses a set of another game."""

    class Meta:
        model = Product
        fields = ["name", "product_type", "product_set", "ean", "release_date", "is_active"]


class ProductAddForm(ProductEditForm):
    class Meta(ProductEditForm.Meta):
        fields = ["game", *ProductEditForm.Meta.fields]


class ProductSetEditForm(forms.ModelForm):
    class Meta:
        model = ProductSet
        fields = ["name", "code", "release_date"]


class ProductSetAddForm(ProductSetEditForm):
    class Meta(ProductSetEditForm.Meta):
        fields = ["game", *ProductSetEditForm.Meta.fields]


def iso(day):
    return day.isoformat() if day else None


def form_problem(form):
    """The first thing the form refused, as the owner reads it."""
    for errors in form.errors.values():
        for error in errors:
            return str(error)
    return "The site would not save this."


def log(user_id, queryset, action, message):
    """The Django Admin history line his own save would leave. types.owner_typed reads a type change from it."""
    if user_id is not None:
        LogEntry.objects.log_actions(user_id, queryset, action, message, single_object=True)


# Products and sets ---------------------------------------------------------------------------------------

def product_now(product):
    """A product's fields as a DataChange records what the site held."""
    return {"name": product.name, "type": product.product_type, "set": product.product_set_id,
            "language": product.language, "barcode": product.ean or "", "release_date": iso(product.release_date),
            "show": product.is_active}


def product_form_data(product):
    return {"name": product.name, "product_type": product.product_type, "product_set": product.product_set_id or "",
            "ean": product.ean or "", "release_date": iso(product.release_date) or "", "is_active": product.is_active}


def set_pk(ref, sets):
    """A set id, with new1, new2 and so on named by the sets this file added."""
    if isinstance(ref, str):
        if ref not in sets:
            raise Refused(f"The new set {ref} on Sets was not added, so this was not made either.")
        return sets[ref]
    return ref


def save_product(pk, changes, user_id, sets=None):
    """Make one row's changes to a product, as the owner's save in Django Admin would. ``changes`` is
    {field: [as downloaded, in the file, on the site when checked, to save]}; a language is made through
    the name. Raises Stale when a field no longer holds what the site held when checked. Returns
    (before, after): the fields as they were and as they are now."""
    product = Product.objects.select_related("game", "product_set").filter(pk=pk).first()
    if product is None:
        raise Stale
    now = product_now(product)
    if any(now[field] != seen for field, (_base, _file, seen, _new) in changes.items()):
        raise Stale
    data = product_form_data(product)
    for field, (_base, _file, _seen, new) in changes.items():
        if field == "set":
            new = set_pk(new, sets or {})
        elif field == "release_date":
            new = new or ""
        if field in PRODUCT_FIELDS:
            data[PRODUCT_FIELDS[field]] = "" if new is None else new
    form = ProductEditForm(data, instance=product)
    if not form.is_valid():
        raise Refused(form_problem(form))
    if "product_type" in form.changed_data and user_id is None:
        raise Refused("Whoever uploaded this file has no account now, so a type set from it would not be kept "
                      "from the hourly retype.")
    form.save()
    if "language" in changes and not languages.same(product.language, changes["language"][3]):
        raise Refused(f"The name or set says {languages.name(product.language)}. Change the name or set.")
    # "Product type" in this line is what keeps the type from the hourly retype (types.owner_typed).
    log(user_id, Product.objects.filter(pk=pk), CHANGE, construct_change_message(form, [], False))
    pricing.update_daily_lowest(product)
    after = product_now(product)
    return {field: now[field] for field in changes}, {field: after[field] for field in changes}


def unique_slug(name):
    """The address a new product gets: its name, with -2, -3 and so on when a product or an old address
    already has it."""
    base = slugify(name)[:220] or "product"
    slug, n = base, 1
    while Product.objects.filter(slug=slug).exists() or ProductAlias.objects.filter(slug=slug).exists():
        n += 1
        suffix = f"-{n}"
        slug = f"{base[:220 - len(suffix)]}{suffix}"
    return slug


def create_product(values, user_id, sets=None):
    """Add a product, as Django Admin's Add product would."""
    data = {"game": values["game"], "name": values["name"], "product_type": values["type"],
            "product_set": set_pk(values.get("set"), sets or {}) or "", "ean": values.get("barcode") or "",
            "release_date": values.get("release_date") or "", "is_active": values.get("show", True) is not False}
    form = ProductAddForm(data, instance=Product(slug=unique_slug(values["name"])))
    if not form.is_valid():
        raise Refused(form_problem(form))
    product = form.save()
    wanted = values.get("language")
    if wanted is not None and not languages.same(product.language, wanted):
        raise Refused(f"The name or set says {languages.name(product.language)}. Change the name or set.")
    log(user_id, Product.objects.filter(pk=product.pk), ADDITION, construct_change_message(form, [], True))
    return product


def set_now(product_set):
    return {"name": product_set.name, "code": product_set.code or "", "release_date": iso(product_set.release_date),
            "source": product_set.release_date_source}


def save_set(pk, changes, user_id):
    """Change a set's name, code or release date. A date he types is his: no release source changes it
    (releases.may_write_date). A date he clears may be given again by a source. Returns (before, after)."""
    product_set = ProductSet.objects.filter(pk=pk).first()
    if product_set is None:
        raise Stale
    now = set_now(product_set)
    if any(now[field] != seen for field, (_base, _file, seen, _new) in changes.items()):
        raise Stale
    data = {"name": product_set.name, "code": product_set.code, "release_date": iso(product_set.release_date) or ""}
    for field, (_base, _file, _seen, new) in changes.items():
        data[field] = new or ""
    form = ProductSetEditForm(data, instance=product_set)
    if not form.is_valid():
        raise Refused(form_problem(form))
    product_set = form.save(commit=False)
    if "release_date" in changes:
        product_set.release_date_source = releases.OWNER if product_set.release_date else ""
    product_set.save()
    log(user_id, ProductSet.objects.filter(pk=pk), CHANGE, construct_change_message(form, [], False))
    after = set_now(product_set)
    return ({**{field: now[field] for field in changes}, "source": now["source"]},
            {**{field: after[field] for field in changes}, "source": after["source"]})


def create_set(values, user_id):
    """Add a set. Refused when the game has it already, by its code or its name."""
    game = Game.objects.filter(pk=values["game"]).first()
    if game is None:
        raise Stale
    found = releases.find_set(game, values["name"], values.get("code") or "")
    if found is not None:
        raise Refused(f"{game.name} already has this set: id {found.pk}.")
    base = releases.name_key(values["name"]) or "set"
    slug, n = base, 1
    while ProductSet.objects.filter(game=game, slug=slug).exists():
        n += 1
        slug = f"{base[:120 - len(str(n)) - 1]}-{n}"
    data = {"game": game.pk, "name": values["name"], "code": values.get("code") or "",
            "release_date": values.get("release_date") or ""}
    form = ProductSetAddForm(data, instance=ProductSet(slug=slug))
    if not form.is_valid():
        raise Refused(form_problem(form))
    product_set = form.save(commit=False)
    product_set.release_date_source = releases.OWNER if product_set.release_date else ""
    product_set.save()
    log(user_id, ProductSet.objects.filter(pk=product_set.pk), ADDITION, construct_change_message(form, [], True))
    return product_set


# Pairs of products -----------------------------------------------------------------------------------------

def pair(a, b):
    return Q(product_id=a, other_id=b) | Q(product_id=b, other_id=a)


def merge_into(keep, other, why):
    """Merge ``other`` into ``keep`` so it can be undone: listed under Sorted for you on Things to check with
    Undo, as Merge the pairs Claude is sure are the same lists it, and never counted as Claude's act."""
    from .management.commands.merge_duplicates import merge_undoable

    # The key first: it names both products as they are now.
    row_key = judge.pair_key(keep, other)
    note = merge_undoable(keep, [other])
    answer = CheckAnswer.objects.create(
        kind=CheckAnswer.Kind.MERGE, by_owner=False, product=keep, other=other, undo_note=note,
        what=f"Merged {other.name} into {keep.name}"[:300], why=why[:300],
    )
    judge.note_owner(row_key, "same")
    return answer


def keep_apart(a, b):
    """The owner's Not the same, as its button on Things to check."""
    answer = autopilot.owner_apart(a, b)
    judge.note_owner(judge.pair_key(a, b), "different")
    return answer


# Shop pages ------------------------------------------------------------------------------------------------

def move_listing(listing, target):
    """Move a shop's page to another product, with what keeps it there: the shop's linked row and its page
    in the sitemap name the new product, so the next read finds it there. Both products are judged again."""
    there = Listing.objects.filter(product=target, retailer_id=listing.retailer_id).exclude(pk=listing.pk).first()
    if there is not None:
        raise Refused(f"{listing.retailer.name} already has a page on {target.pk}: {there.url}. Either that page or "
                      "this one is a different product.")
    old = listing.product_id
    Listing.objects.filter(pk=listing.pk).update(product=target)
    ShopProduct.objects.filter(retailer_id=listing.retailer_id, url=listing.url, status=Status.LINKED).update(
        suggested=target)
    ShopPage.objects.filter(retailer_id=listing.retailer_id, url=listing.url).update(product=target)
    sanity.judge_product(old)
    sanity.judge_product(target.pk)
    pricing.correct_daily_lowest(old)
    pricing.update_daily_lowest(target)
    return old


def take_off_listing(listing):
    """Not this product: the page comes off the product, and its shop row says Not ours, so the next read
    never matches it by name again (importers.apply_offers). Returns what undoing it needs."""
    old = listing.product_id
    row = ShopProduct.objects.filter(retailer_id=listing.retailer_id, url=listing.url).first()
    was = {"status": row.status, "suggested": row.suggested_id, "source": row.source} if row else None
    pages = list(ShopPage.objects.filter(retailer_id=listing.retailer_id, url=listing.url, product_id=old)
                 .values_list("pk", flat=True))
    Listing.objects.filter(pk=listing.pk).delete()
    row, created = ShopProduct.objects.update_or_create(
        retailer_id=listing.retailer_id, url=listing.url,
        defaults={"status": Status.IGNORED, "source": ShopProduct.Source.IMPORT,
                  "title": (listing.title or listing.url)[:300], "price": listing.price,
                  "availability": listing.availability, "shop_ean": listing.shop_ean, "suggested_id": old,
                  "last_seen": listing.last_checked},
    )
    ShopPage.objects.filter(pk__in=pages).update(product=None)
    sanity.judge_product(old)
    pricing.correct_daily_lowest(old)
    return {"product": old, "retailer": listing.retailer_id, "url": listing.url, "row": row.pk, "created": created,
            "was": was, "pages": pages}


def hide_listing(listing):
    """Hide one shop's price, as the owner's Hide on Things to check: the autopilot and Claude leave it be."""
    autopilot.hide(listing)
    answer = autopilot.owner_hid(listing)
    judge.note_owner(f"offer:{listing.pk}", "hid")
    return answer


def show_listing(listing):
    """Show a hidden price again: a standing Hide is undone, so Things to check says so; otherwise the
    price is simply shown and judged. Returns the Hide undone, or None."""
    answer = (CheckAnswer.objects.select_related("listing__product", "listing__retailer")
              .filter(kind=CheckAnswer.Kind.HIDE, listing=listing, undone_at__isnull=True).order_by("-pk").first())
    if answer is None or not autopilot.undo(answer):
        Listing.objects.filter(pk=listing.pk).update(is_active=True)
        sanity.judge_product(listing.product_id)
        answer = None
    pricing.update_daily_lowest(listing.product)
    return answer


# Shop items ------------------------------------------------------------------------------------------------

def search_state(row):
    """The stockist finder's search behind a row it found, as (pk, outcome), so an undo can put it back."""
    if row.source != ShopProduct.Source.FINDER or not row.product_id:
        return None
    return StockistSearch.objects.filter(product_id=row.product_id, retailer_id=row.retailer_id).values_list(
        "pk", "outcome").first()


def page_state(retailer_id, url):
    return [[pk, product_id] for pk, product_id in
            ShopPage.objects.filter(retailer_id=retailer_id, url=url).values_list("pk", "product_id")]


def link_shop_item(row, target):
    """Yes, link it, as on Things to check: a priced row adds the listing at the price and stock it saw,
    when it saw them; an unpriced one waits for the shop's next read to price it."""
    undo = {"status": row.status, "suggested": row.suggested_id, "search": search_state(row),
            "pages": page_state(row.retailer_id, row.url)}
    row.suggested = target
    row.save(update_fields=["suggested"])
    listing = finder.link(row)
    judge.note_owner(f"found:{row.pk}", "same")
    return {**undo, "listing": listing.pk if listing is not None else None}


def ignore_shop_item(row):
    """No, not this, as on Things to check."""
    undo = {"status": row.status, "search": search_state(row)}
    finder.ignore(row)
    judge.note_owner(f"found:{row.pk}", "different")
    return undo


def review_shop_item(row):
    """Put a row back to be looked at again, as the admin's Put back for review."""
    undo = {"status": row.status}
    ShopProduct.objects.filter(pk=row.pk).update(status=Status.REVIEW)
    return undo


def link_left_out(retailer, url, title, barcode, target):
    """Link an item a shop's read left out. No price is invented: the row waits, and the shop's next read
    adds the listing at the price it reads (importers.apply_offers matches a waiting row before any name
    rule, so a title the rules turn away is still added)."""
    pages = page_state(retailer.pk, url)
    row, _created = ShopProduct.objects.update_or_create(
        retailer=retailer, url=url,
        defaults={"title": (title or url)[:300], "shop_ean": (barcode or "")[:20], "status": Status.LINKED,
                  "source": ShopProduct.Source.IMPORT, "suggested": target, "confidence": 100, "price": None,
                  "last_seen": timezone.now()},
    )
    ShopPage.objects.filter(retailer=retailer, url=url).update(product=target, last_fetched_at=None)
    return {"row": row.pk, "pages": pages}


def ignore_left_out(retailer, url, title, barcode):
    """Not ours, for an item a shop's read left out: its page is never matched by name again."""
    row, _created = ShopProduct.objects.update_or_create(
        retailer=retailer, url=url,
        defaults={"title": (title or url)[:300], "shop_ean": (barcode or "")[:20], "status": Status.IGNORED,
                  "source": ShopProduct.Source.IMPORT, "suggested": None, "price": None,
                  "last_seen": timezone.now()},
    )
    return {"row": row.pk}


# Making a file's changes ------------------------------------------------------------------------------------

class Applying:
    """What one file's changes share while they are made: who uploaded it, its number, and the products and
    sets its new rows became."""

    def __init__(self, user_id=None, reference="", products=None, sets=None, export_id=None, import_id=None):
        self.user_id = user_id
        self.reference = reference
        self.export_id = export_id   # the download the file came from
        self.import_id = import_id
        self.products = dict(products or {})   # new1: the product id it became
        self.sets = dict(sets or {})

    def product(self, change):
        """The product a move, link or merge lands on: the one kept once the file's merges are made."""
        ref = change.target_id if change.target_id is not None else change.data.get("target_ref")
        if isinstance(ref, str):
            if ref not in self.products:
                raise Refused(f"The new product {ref} was not added, so this was not made either.")
            ref = self.products[ref]
        product = Product.objects.select_related("game").filter(pk=ref).first() if ref is not None else None
        if product is None:
            raise Stale
        return product


class Made(NamedTuple):
    undo: dict
    answer: object = None      # the CheckAnswer the change made, for Undo on Things to check
    object_id: object = None   # the product or set a new row became


def apply_change(change, applying):
    """Make one checked change. Raises Stale or Refused, leaving the caller to roll it back."""
    return MAKERS[change.kind](change, applying)


def listing_of(pk):
    return Listing.objects.select_related("product__game", "retailer").filter(pk=pk).first()


def make_new_set(change, applying):
    product_set = create_set(change.data, applying.user_id)
    return Made({"set": product_set.pk}, object_id=product_set.pk)


def make_set_fields(change, applying):
    before, after = save_set(change.object_id, change.fields, applying.user_id)
    return Made({"before": before, "after": after})


def make_new_product(change, applying):
    # Two uploads of one download, both checked before either was made, would each add the row.
    if applying.export_id is not None:
        earlier = (DataChange.objects.filter(kind=Kind.NEW_PRODUCT, ref=change.ref, outcome=DataChange.Outcome.APPLIED,
                                             data_import__export_id=applying.export_id)
                   .exclude(data_import_id=applying.import_id).values_list("object_id", flat=True).first())
        if earlier is not None:
            raise Refused(f"Another upload of this download added {change.ref} already, as {earlier}.")
    product = create_product(change.data, applying.user_id, applying.sets)
    return Made({"product": product.pk}, object_id=product.pk)


def make_product_fields(change, applying):
    before, after = save_product(change.object_id, change.fields, applying.user_id, applying.sets)
    return Made({"before": before, "after": after})


def make_take_off(change, applying):
    listing = listing_of(change.object_id)
    if listing is None or listing.product_id != change.data.get("product"):
        raise Stale
    if autopilot.marketplace(listing):
        raise Refused("Its prices come from searching for the product. Use Show on site No.")
    return Made(take_off_listing(listing))


def make_move(change, applying):
    listing = listing_of(change.object_id)
    if listing is None or listing.product_id != change.fields["product"][2]:
        raise Stale
    target = applying.product(change)
    if target.pk == listing.product_id:
        raise Stale
    if not target.is_active:
        raise Refused(f"{target.pk} is hidden from the site.")
    old = move_listing(listing, target)
    return Made({"from": old, "to": target.pk})


def make_hide(change, applying):
    listing = listing_of(change.object_id)
    if listing is None or listing.is_active != change.fields["show"][2]:
        raise Stale
    return Made({}, answer=hide_listing(listing))


def make_show(change, applying):
    listing = listing_of(change.object_id)
    if listing is None or listing.is_active != change.fields["show"][2]:
        raise Stale
    if not listing.retailer.is_active:
        raise Refused(f"{listing.retailer.name} is switched off, so its prices are not shown.")
    if not listing.product.is_active:
        raise Refused("Its product is hidden from the site, so its prices are not shown.")
    undone = show_listing(listing)
    return Made({"hide_undone": undone.pk if undone is not None else None})


def make_merge(change, applying):
    other = Product.objects.select_related("game").filter(pk=change.object_id).first()
    keep = applying.product(change)
    if other is None or not keep.is_active or keep.pk == other.pk:
        raise Stale
    # A hidden product may be merged (and stays hidden if the merge is undone); one merged away already may not.
    if not other.is_active and ProductAlias.objects.filter(slug=other.slug).exclude(product=other).exists():
        raise Stale
    answers = CheckAnswer.objects.filter(pair(keep.pk, other.pk), undone_at__isnull=True)
    if answers.filter(kind=CheckAnswer.Kind.MERGE).exists():
        raise Stale
    apart = answers.filter(kind=CheckAnswer.Kind.APART)
    reopened = []
    if apart.exists():
        if change.state != DataChange.State.HELD:
            # Said to be different since the file was checked.
            raise Stale
        # Held for this and made with his second button: his say now is that they are one product.
        reopened = list(apart.values_list("pk", flat=True))
        apart.update(undone_at=timezone.now())
    answer = merge_into(keep, other, f"From your spreadsheet {applying.reference}, Products row {change.row}")
    return Made({"apart": reopened}, answer=answer)


def make_apart(change, applying):
    first = Product.objects.filter(pk=change.object_id).first()
    second = Product.objects.filter(pk=change.target_id).first()
    if first is None or second is None:
        raise Stale
    if CheckAnswer.objects.filter(pair(first.pk, second.pk), undone_at__isnull=True,
                                  kind__in=[CheckAnswer.Kind.APART, CheckAnswer.Kind.MERGE]).exists():
        raise Stale
    return Made({}, answer=keep_apart(second, first))


def shop_row(change):
    row = ShopProduct.objects.select_related("retailer", "suggested").filter(pk=change.object_id).first()
    if row is None:
        raise Stale
    return row


def make_link(change, applying):
    row = shop_row(change)
    target = applying.product(change)
    d = change.data
    # A merge in this file may have moved a linked row to the product kept, which is where it goes.
    if row.status != d["status"] or row.suggested_id not in (d["suggested"], target.pk):
        raise Stale
    if not target.is_active:
        raise Refused(f"{target.pk} is hidden from the site.")
    there = Listing.objects.filter(product=target, retailer=row.retailer).first()
    if there is not None:
        if link_key(there.url) == link_key(row.url):
            raise Stale
        raise Refused(f"{row.retailer.name} already shows {target.name} on {there.url}. One product has one page "
                      "per shop.")
    key = ean_key(row.shop_ean)
    if key:
        owners = [p for p in Product.objects.filter(is_active=True, ean__endswith=key).exclude(pk=target.pk)
                  if ean_key(p.ean) == key]
        if owners:
            raise Refused(f"The shop gives the barcode {row.shop_ean}, which is on {owners[0].pk}, so the shop's "
                          "next read links it there.")
    return Made({**link_shop_item(row, target), "product": target.pk})


def still_as_checked(row, change):
    d = change.data
    if (row.status, row.suggested_id) != (d["status"], d["suggested"]):
        raise Stale


def make_ignore(change, applying):
    row = shop_row(change)
    still_as_checked(row, change)
    return Made(ignore_shop_item(row))


def make_review(change, applying):
    row = shop_row(change)
    still_as_checked(row, change)
    return Made(review_shop_item(row))


def left_out_shop(change):
    d = change.data
    retailer = Retailer.objects.filter(pk=d["retailer"]).first()
    if retailer is None or ShopProduct.objects.filter(retailer=retailer, url=d["url"]).exists():
        # The shop is gone, or a read has found the item since.
        raise Stale
    return retailer


def make_left_link(change, applying):
    retailer = left_out_shop(change)
    target = applying.product(change)
    if not target.is_active:
        raise Refused(f"{target.pk} is hidden from the site.")
    there = Listing.objects.filter(product=target, retailer=retailer).first()
    if there is not None:
        # The read would pass the waiting row by, so the link would never show.
        raise Refused(f"{retailer.name} already has a page on {target.pk}: {there.url}. One product has one page "
                      "per shop.")
    d = change.data
    return Made({**link_left_out(retailer, d["url"], d["title"], d["barcode"], target), "product": target.pk})


def make_left_ignore(change, applying):
    retailer = left_out_shop(change)
    d = change.data
    return Made(ignore_left_out(retailer, d["url"], d["title"], d["barcode"]))


MAKERS = {
    Kind.NEW_SET: make_new_set, Kind.SET_FIELDS: make_set_fields, Kind.NEW_PRODUCT: make_new_product,
    Kind.PRODUCT_FIELDS: make_product_fields, Kind.TAKE_OFF: make_take_off, Kind.MOVE: make_move,
    Kind.HIDE: make_hide, Kind.SHOW: make_show, Kind.MERGE: make_merge, Kind.APART: make_apart,
    Kind.LINK: make_link, Kind.IGNORE: make_ignore, Kind.REVIEW: make_review, Kind.LEFT_LINK: make_left_link,
    Kind.LEFT_IGNORE: make_left_ignore,
}


# Taking a file's changes back -------------------------------------------------------------------------------

def undo(change, applying):
    """Take back one change the file made, while the site still holds what it left. Raises Stale when the
    site has changed it since, and Refused with the reason when it cannot be taken back yet."""
    UNDOERS[change.kind](change, applying)


def answer_of(change):
    answer = CheckAnswer.objects.select_related("listing__product", "listing__retailer", "product").filter(
        pk=change.answer_id).first() if change.answer_id else None
    if answer is None or answer.undone_at is not None:
        raise Stale
    return answer


def undo_product_fields(change, applying):
    before, after = change.undo["before"], change.undo["after"]
    product = Product.objects.filter(pk=change.object_id).first()
    if product is None:
        raise Stale
    now = product_now(product)
    if any(now[field] != value for field, value in after.items() if field != "language"):
        raise Stale
    data = product_form_data(product)
    for field, value in before.items():
        if field in PRODUCT_FIELDS:
            data[PRODUCT_FIELDS[field]] = "" if value is None else value
    form = ProductEditForm(data, instance=product)
    if not form.is_valid():
        raise Refused(form_problem(form))
    form.save()
    log(applying.user_id, Product.objects.filter(pk=product.pk), CHANGE, construct_change_message(form, [], False))
    pricing.correct_daily_lowest(product.pk)


def undo_set_fields(change, applying):
    before, after = change.undo["before"], change.undo["after"]
    product_set = ProductSet.objects.filter(pk=change.object_id).first()
    if product_set is None:
        raise Stale
    now = set_now(product_set)
    if any(now[field] != value for field, value in after.items()):
        raise Stale
    data = {"name": product_set.name, "code": product_set.code, "release_date": iso(product_set.release_date) or ""}
    for field in ("name", "code", "release_date"):
        if field in before:
            data[field] = before[field] or ""
    form = ProductSetEditForm(data, instance=product_set)
    if not form.is_valid():
        raise Refused(form_problem(form))
    product_set = form.save(commit=False)
    product_set.release_date_source = before["source"]
    product_set.save()
    log(applying.user_id, ProductSet.objects.filter(pk=product_set.pk), CHANGE,
        construct_change_message(form, [], False))


def undo_new_set(change, applying):
    product_set = ProductSet.objects.filter(pk=change.undo.get("set")).first()
    if product_set is None:
        raise Stale
    if Product.objects.filter(product_set=product_set).exists():
        raise Refused("Products are filed under this set now, so it stays.")
    log(applying.user_id, ProductSet.objects.filter(pk=product_set.pk), DELETION, "")
    product_set.delete()


def referred_to(pk):
    """Whether anything on the site names this product: a price, a shop's row or page, an old address, an
    alert, a click, a restock or a standing answer on Things to check."""
    return any(model.objects.filter(query).exists() for model, query in (
        (Listing, Q(product_id=pk)), (ShopProduct, Q(suggested_id=pk) | Q(product_id=pk)), (ShopPage, Q(product_id=pk)),
        (ProductAlias, Q(product_id=pk)), (StockAlert, Q(product_id=pk)), (OutboundClick, Q(product_id=pk)),
        (Restock, Q(product_id=pk)), (CheckAnswer, (Q(product_id=pk) | Q(other_id=pk)) & Q(undone_at__isnull=True)),
    ))


def undo_new_product(change, applying):
    product = Product.objects.filter(pk=change.undo.get("product")).first()
    if product is None:
        raise Stale
    if referred_to(product.pk):
        # Something names it now: it is switched off rather than lost.
        if product.is_active:
            Product.objects.filter(pk=product.pk).update(is_active=False)
            log(applying.user_id, Product.objects.filter(pk=product.pk), CHANGE,
                [{"changed": {"fields": ["Show on site"]}}])
        return
    log(applying.user_id, Product.objects.filter(pk=product.pk), DELETION, "")
    product.delete()


def undo_take_off(change, applying):
    u = change.undo
    row = ShopProduct.objects.filter(pk=u["row"], status=Status.IGNORED).first()
    product = Product.objects.filter(pk=u["product"]).first()
    if row is None or product is None or Listing.objects.filter(product=product, retailer_id=u["retailer"]).exists():
        raise Stale
    # Linked and waiting: the shop's next read adds the listing again at the price it reads. None is made up.
    ShopProduct.objects.filter(pk=row.pk).update(status=Status.LINKED, price=None, suggested=product)
    ShopPage.objects.filter(pk__in=u["pages"], product__isnull=True).update(product=product, last_fetched_at=None)


def undo_move(change, applying):
    u = change.undo
    listing = Listing.objects.select_related("retailer").filter(pk=change.object_id, product_id=u["to"]).first()
    back = Product.objects.filter(pk=u["from"]).first()
    if listing is None or back is None or Listing.objects.filter(product=back, retailer_id=listing.retailer_id).exists():
        raise Stale
    move_listing(listing, back)


def undo_by_answer(change, applying):
    """A hide, a merge or a pair kept apart, undone as its Undo on Things to check undoes it."""
    answer = answer_of(change)
    if answer.kind == CheckAnswer.Kind.HIDE and (answer.listing is None or answer.listing.is_active):
        raise Stale
    if answer.kind == CheckAnswer.Kind.MERGE:
        later = autopilot.later_merges(answer).order_by("-pk").first()
        if later is not None:
            raise Refused(f"A later merge built on this one: {later.what}. Undo that merge first, then this file.")
        if answer.product is None or not answer.product.is_active:
            raise Stale
    if not autopilot.undo(answer):
        raise Stale
    if change.kind == Kind.MERGE and change.undo.get("apart"):
        CheckAnswer.objects.filter(pk__in=change.undo["apart"]).update(undone_at=None)


def undo_show(change, applying):
    listing = listing_of(change.object_id)
    if listing is None or not listing.is_active:
        raise Stale
    hide_listing(listing)


def restore_search(state):
    if state:
        StockistSearch.objects.filter(pk=state[0]).update(outcome=state[1])


def restore_pages(pages):
    for pk, product_id in pages:
        ShopPage.objects.filter(pk=pk).update(product_id=product_id)


def drop_listing(listing):
    product_id = listing.product_id
    Listing.objects.filter(pk=listing.pk).delete()
    sanity.judge_product(product_id)
    pricing.correct_daily_lowest(product_id)


def undo_link(change, applying):
    u = change.undo
    row = ShopProduct.objects.filter(pk=change.object_id, status=Status.LINKED, suggested_id=u["product"]).first()
    if row is None:
        raise Stale
    if u.get("listing"):
        listing = Listing.objects.filter(pk=u["listing"], product_id=row.suggested_id).first()
        if listing is not None and link_key(listing.url) == link_key(row.url):
            drop_listing(listing)
    ShopProduct.objects.filter(pk=row.pk).update(status=u["status"], suggested_id=u["suggested"])
    restore_search(u.get("search"))
    restore_pages(u.get("pages", []))


def undo_status(change, applying):
    """Not ours or Look again: the row's status as it was."""
    u = change.undo
    now = Status.IGNORED if change.kind == Kind.IGNORE else Status.REVIEW
    if not ShopProduct.objects.filter(pk=change.object_id, status=now).update(status=u["status"]):
        raise Stale
    restore_search(u.get("search"))


def undo_left_link(change, applying):
    u = change.undo
    row = ShopProduct.objects.filter(pk=u["row"], status=Status.LINKED, suggested_id=u["product"]).first()
    if row is None:
        raise Stale
    # Any listing the shop's read added since, from this row: the product had no page at the shop before.
    for listing in Listing.objects.filter(product_id=u["product"], retailer_id=row.retailer_id):
        if link_key(listing.url) == link_key(row.url):
            drop_listing(listing)
    row.delete()
    restore_pages(u.get("pages", []))


def undo_left_ignore(change, applying):
    if not ShopProduct.objects.filter(pk=change.undo.get("row"), status=Status.IGNORED).delete()[0]:
        raise Stale


UNDOERS = {
    Kind.NEW_SET: undo_new_set, Kind.SET_FIELDS: undo_set_fields, Kind.NEW_PRODUCT: undo_new_product,
    Kind.PRODUCT_FIELDS: undo_product_fields, Kind.TAKE_OFF: undo_take_off, Kind.MOVE: undo_move,
    Kind.HIDE: undo_by_answer, Kind.SHOW: undo_show, Kind.MERGE: undo_by_answer, Kind.APART: undo_by_answer,
    Kind.LINK: undo_link, Kind.IGNORE: undo_status, Kind.REVIEW: undo_status, Kind.LEFT_LINK: undo_left_link,
    Kind.LEFT_IGNORE: undo_left_ignore,
}
