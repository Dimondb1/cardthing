"""
Merge products that are the same thing under slightly different names.

    python manage.py merge_duplicates --dry-run
    python manage.py merge_duplicates
    python manage.py merge_duplicates --loose --dry-run

--loose also ignores words shops add that do not change the product
("Exclusive", "English", "TCG", and for Pokémon the series name, such as
"Mega Evolution" or "Scarlet & Violet"). It never runs on its own: read the
dry run, then run it without --dry-run if every group is right.

Shops write the same product differently ("Commander Legends: Battle for
Baldur's Gate Bundle" and "Commander Legends Battle For Baldurs Gate
Bundle"), so one product can end up on two pages, each with one shop. This
groups products of one game and type whose names share the same matching
key, keeps the one with the most listings (or an image), moves the other
listings, price history, restocks, clicks and stock alerts across, and
deletes the rest. The old addresses redirect to the kept product, and
watchlists saved in browsers follow them. Run with --dry-run first.
"""

from collections import defaultdict
from contextlib import nullcontext

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.matching import match_key
from catalogue.models import DailyLowestPrice, Listing, OutboundClick, Product, ProductAlias, Restock, StockAlert


# Words that never change which product it is.
FILLER = {"exclusive", "english", "tcg", "edition"}
# Pokémon series names, which some shops put before the set name and others leave out.
POKEMON_SERIES = {"pokemon", "mega", "evolution", "base", "set", "scarlet", "violet", "sv", "sword", "shield", "swsh"}
# Words that say what kind of thing it is, not which one.
KIND_WORDS = {"elite", "trainer", "box", "center", "booster", "bundle", "display", "pack", "packs", "case", "tin", "collection"}


def identifying(words):
    return [w for w in words if w not in KIND_WORDS and not w.startswith("#")]


def merge_key(name):
    """The matching key, with a plural dropped when its singular is already there: "Booster Bundle
    Display (10 Bundles)" and "Booster Bundle Display (10)" name the same thing.

    "" when nothing but the kind of product is left: the matching key drops series names, so
    "Scarlet & Violet Elite Trainer Box" and "Sword & Shield Elite Trainer Box" would otherwise meet.
    """
    words = match_key(name).split()
    kept = [w for w in words if not (w.endswith("s") and w[:-1] in words)]
    return " ".join(kept) if identifying(kept) else ""


def loose_key(name, game_slug):
    """``merge_key`` without filler and series words, or "" when nothing identifying is left
    (a bare "Scarlet & Violet Elite Trainer Box" must never meet "Sword & Shield Elite Trainer Box")."""
    noise = FILLER | (POKEMON_SERIES if game_slug == "pokemon" else set())
    words = [w for w in merge_key(name).split() if w not in noise]
    if not identifying(words):
        return ""
    return " ".join(sorted(set(words)))


def carry_history(keep, other):
    """Move what belongs to ``other`` onto ``keep``, so a merge loses no history."""
    lows = dict(DailyLowestPrice.objects.filter(product=keep).values_list("date", "price"))
    for row in DailyLowestPrice.objects.filter(product=other):
        if row.date not in lows:
            row.product = keep
            row.save(update_fields=["product"])
        elif row.price < lows[row.date]:
            DailyLowestPrice.objects.filter(product=keep, date=row.date).update(price=row.price)
    Restock.objects.filter(product=other).update(product=keep)
    OutboundClick.objects.filter(product=other).update(product=keep)
    asked = set(StockAlert.objects.filter(product=keep).values_list("email", flat=True))
    StockAlert.objects.filter(product=other).exclude(email__in=asked).update(product=keep)


def merge(keep, others):
    """Move listings from ``others`` onto ``keep`` and delete them. Returns listings moved."""
    moved = 0
    for other in others:
        for listing in other.listings.all():
            if Listing.objects.filter(product=keep, retailer=listing.retailer).exists():
                listing.delete()
            else:
                listing.product = keep
                listing.save(update_fields=["product"])
                moved += 1
        carry_history(keep, other)
        if not keep.image_url and other.image_url:
            keep.image_url = other.image_url
            keep.save(update_fields=["image_url"])
        if not keep.ean and other.ean:
            keep.ean = other.ean
            keep.save(update_fields=["ean"])
        # The old address keeps working: it sends visitors and search engines to the kept page.
        ProductAlias.objects.filter(product=other).update(product=keep)
        ProductAlias.objects.filter(slug=other.slug).delete()
        other.delete()
        ProductAlias.objects.create(slug=other.slug, product=keep)
    return moved


def merge_undoable(keep, others):
    """``merge``, but each other product is switched off rather than deleted, its history is copied rather
    than moved, and everything moved is noted, so ``unmerge`` can put it all back. The other product's old
    address redirects to the kept one, as after a merge. Returns the note, plain JSON.

    A switched-off product must be invisible to shop reads, or they would put a live price on it: its
    barcode moves to the kept product (or is cleared), its shop pages point at the kept product, and its
    address is an old address of the kept one, which is how reads and tidy_catalogue know to pass it by.
    The kept product is judged again at once, so a price moved onto it never claims a saving it should
    not. A listing at a shop the kept product is already listed at stays on the switched-off product,
    where nobody sees it, rather than being deleted.
    """
    from django.utils import timezone

    from catalogue import sanity
    from catalogue.models import ShopPage

    note = {"keep": keep.pk, "others": [], "image_url": None, "ean": None, "at": timezone.localdate().isoformat()}
    for other in others:
        entry = {"pk": other.pk, "listings": [], "restocks": [], "clicks": [], "alerts": [], "aliases": [],
                 "pages": [], "lows_added": [], "lows_lowered": [], "alias": None, "alias_was": None, "ean": other.ean}
        shops = set(keep.listings.values_list("retailer_id", flat=True))
        for listing in other.listings.all():
            if listing.retailer_id not in shops:
                Listing.objects.filter(pk=listing.pk).update(product=keep)
                entry["listings"].append(listing.pk)
                shops.add(listing.retailer_id)
        lows = dict(DailyLowestPrice.objects.filter(product=keep).values_list("date", "price"))
        for day, price in DailyLowestPrice.objects.filter(product=other).values_list("date", "price"):
            if day not in lows:
                entry["lows_added"].append(DailyLowestPrice.objects.create(product=keep, date=day, price=price).pk)
            elif price < lows[day]:
                DailyLowestPrice.objects.filter(product=keep, date=day).update(price=price)
                entry["lows_lowered"].append([day.isoformat(), str(lows[day]), str(price)])
                lows[day] = price
        for model, key in ((Restock, "restocks"), (OutboundClick, "clicks"), (ShopPage, "pages")):
            rows = model.objects.filter(product=other)
            entry[key] = list(rows.values_list("pk", flat=True))
            rows.update(product=keep)
        asked = set(StockAlert.objects.filter(product=keep).values_list("email", flat=True))
        alerts = StockAlert.objects.filter(product=other).exclude(email__in=asked)
        entry["alerts"] = list(alerts.values_list("pk", flat=True))
        alerts.update(product=keep)
        if not keep.image_url and other.image_url:
            note["image_url"] = other.image_url
            keep.image_url = other.image_url
            keep.save(update_fields=["image_url"])
        if not keep.ean and other.ean:
            note["ean"] = other.ean
            keep.ean = other.ean
            keep.save(update_fields=["ean"])
        aliases = ProductAlias.objects.filter(product=other)
        entry["aliases"] = list(aliases.values_list("pk", flat=True))
        aliases.update(product=keep)
        Product.objects.filter(pk=other.pk).update(is_active=False, ean="")
        taken = ProductAlias.objects.filter(slug=other.slug).first()
        if taken is not None:
            # An old address of some product already has this slug: it now leads to the kept product.
            entry["alias"], entry["alias_was"] = taken.pk, taken.product_id
            ProductAlias.objects.filter(pk=taken.pk).update(product=keep)
        else:
            entry["alias"] = ProductAlias.objects.create(slug=other.slug, product=keep).pk
        note["others"].append(entry)
    sanity.judge_product(keep.pk)
    return note


def unmerge(note):
    """Put back what ``merge_undoable`` did. Rows gone since are skipped; a day's low the kept product has
    recorded lower since is left as it is. Both products' prices are judged afresh, and the days the kept
    product recorded while merged are worked out again, since they held the other product's prices.
    Returns the products switched back on."""
    from datetime import date

    from catalogue import pricing, sanity
    from catalogue.models import ShopPage

    keep = Product.objects.filter(pk=note["keep"]).first()
    restored = []
    for entry in note["others"]:
        other = Product.objects.filter(pk=entry["pk"]).first()
        if other is None:
            continue
        Listing.objects.filter(pk__in=entry["listings"]).exclude(retailer__listings__product=other).update(product=other)
        Restock.objects.filter(pk__in=entry["restocks"]).update(product=other)
        OutboundClick.objects.filter(pk__in=entry["clicks"]).update(product=other)
        ShopPage.objects.filter(pk__in=entry.get("pages", [])).update(product=other)
        StockAlert.objects.filter(pk__in=entry["alerts"]).update(product=other)
        DailyLowestPrice.objects.filter(pk__in=entry["lows_added"]).delete()
        if keep is not None:
            for day, before, after in entry["lows_lowered"]:
                DailyLowestPrice.objects.filter(product=keep, date=day, price=after).update(price=before)
        if entry.get("alias_was"):
            ProductAlias.objects.filter(pk=entry["alias"]).update(product_id=entry["alias_was"])
        else:
            ProductAlias.objects.filter(pk=entry["alias"]).delete()
        ProductAlias.objects.filter(pk__in=entry["aliases"]).update(product=other)
        Product.objects.filter(pk=other.pk).update(is_active=True, ean=entry.get("ean", "") or other.ean)
        restored.append(other)
    if keep is not None:
        if note.get("image_url") and keep.image_url == note["image_url"]:
            Product.objects.filter(pk=keep.pk).update(image_url="")
        if note.get("ean") and keep.ean == note["ean"]:
            Product.objects.filter(pk=keep.pk).update(ean="")
        if note.get("at"):
            DailyLowestPrice.objects.filter(product=keep, date__gte=date.fromisoformat(note["at"])).delete()
    # Verdicts given while merged compared one product's prices with the other's: start both afresh.
    products = [p for p in [keep, *restored] if p is not None]
    Listing.objects.filter(product__in=products, sanity__in=[Listing.Sanity.DOUBTFUL, Listing.Sanity.EXCLUDED]) \
        .exclude(sanity_reason=sanity.TRUSTED_REASON).update(sanity=Listing.Sanity.OK, sanity_reason="", sanity_ratio=None)
    for product in products:
        sanity.judge_product(product.pk)
        pricing.update_daily_lowest(product)
    return restored


def duplicate_groups(loose=False):
    """[(keep, [others])] for every group of products that name the same thing, the one to keep first:
    most listings, then one with an image, then the shortest name."""
    groups = defaultdict(list)
    for product in Product.objects.filter(is_active=True).select_related("game", "product_set").order_by("pk"):
        key = loose_key(product.name, product.game.slug) if loose else merge_key(product.name)
        if key:
            groups[(product.game_id, product.product_type, key)].append(product)
    found = []
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda p: (-p.listings.count(), not p.image, not p.image_url, len(p.name), p.pk))
        found.append((group[0], group[1:]))
    return sorted(found, key=lambda row: row[0].name)


class Command(BaseCommand):
    help = "Merge products whose names identify the same thing."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--loose", action="store_true", help="Also ignore filler and series words. Review a dry run first.")

    def handle(self, *args, dry_run=False, loose=False, **options):
        merged = moved = 0
        # Each group is its own short transaction, so a group that fails leaves the earlier ones merged
        # and nothing else waits for the whole walk. A dry run wraps them all and rolls back.
        with transaction.atomic() if dry_run else nullcontext():
            for keep, others in duplicate_groups(loose):
                self.stdout.write(f"{'would keep' if dry_run else 'kept'}: {keep.name}")
                for other in others:
                    self.stdout.write(f"    merged: {other.name}")
                merged += len(others)
                if not dry_run:
                    with transaction.atomic():
                        moved += merge(keep, others)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{merged} products merged, {moved} listings moved. {Product.objects.count()} products remain.")
