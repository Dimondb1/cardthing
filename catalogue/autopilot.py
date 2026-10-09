"""
The autopilot: answers the rows on the Things to check page that the evidence settles, so the owner only
sees the ones that need a person. It runs every hour (tidy_all) and when the owner taps Sort what you can
now. Every answer is written down as a CheckAnswer with what was done and why, and listed at the top of
the page with an Undo button.

It answers only from what the site already holds: the shop's own title, what the other shops charge,
the product's own price history and barcodes. It never adds a shop, a product or a price no shop gave,
and never publishes a release date.

Found at another shop
    No, when the shop's title names another kind of product (a pack against a box) or another set code
    (OP-10 against OP-09), or its price is under 0.3 or over 3.33 times what the other shops charge, the
    gap at which two shops make each other doubtful.
    Yes, when the names agree word for word both ways and the price is within a quarter of what the other
    shops charge. A likely name waits for the owner, and so does a page with no other shop to compare.

Doubtful prices and wrong matches
    Hide, when the shop's title names another kind of product or another set code.
    This price is right, for the product's cheapest price only, when the names agree word for word both
    ways, the delivery charge is known and the price is within 15% of the product's usual lowest price:
    the median of its daily lows over the 90 days before the price became doubtful, at least 7 of them.

Possible duplicates
    Merge, when the products carry the same barcode.

Announced sets
    Add set, with no date, when a community source names a set that products on the site already name,
    and no product it names is filed under another set. Its date is published only by the usual rules:
    the publisher's own, or two sources agreeing.
"""

import logging
import re
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from . import checks, sanity
from .classify import box_contents, find_type
from .importers import ean_key
from .matching import AUTO_LINK
from .models import CheckAnswer, DailyLowestPrice, Listing, Product, ProductSet, Release
from .types import type_label

logger = logging.getLogger(__name__)

Kind = CheckAnswer.Kind
OK, DOUBTFUL = Listing.Sanity.OK, Listing.Sanity.DOUBTFUL

# Found at another shop: a sure name is linked when its price is within this band of the other shops'.
LINK_LOW, LINK_HIGH = Decimal("0.75"), Decimal("1.33")
# It is refused when its price is outside the band at which two shops make each other doubtful.
REFUSE_LOW, REFUSE_HIGH = sanity.ONE_PEER_LOW, sanity.ONE_PEER_HIGH
# A doubtful price is counted when it is within this share of the product's usual lowest price, worked
# out from at least HISTORY_MIN_DAYS daily lows over the HISTORY_DAYS before it became doubtful.
HISTORY_BAND = Decimal("0.15")
HISTORY_DAYS = sanity.HISTORY_DAYS
HISTORY_MIN_DAYS = sanity.HISTORY_MIN_ROWS

# Set codes that name one set and no other: One Piece (OP-09, EB-02, PRB-01, ST-21) and Dragon Ball
# (FB05, BT24). A title that names only codes other than the product's is another set's product.
SET_CODE = re.compile(r"(?<![a-z0-9])(op|eb|prb|st|fb|bt)-?0*(\d{1,2})(?![0-9])", re.I)
# A title the classifier reads as a pack must say pack: "Blazing Dominion Booster" is as often the box.
PACK_WORDS = re.compile(r"\bpacks?\b|\bpacket\b|\bblister\b|\bchecklane\b|\bsleeved\b", re.I)


def money(value):
    return sanity.money(value)


def codes(text):
    return {(family.lower(), int(number)) for family, number in SET_CODE.findall(text or "")}


def contradiction(product, title):
    """Why the shop's title cannot be this product, or "": it names another kind of product, or only
    another set's code. A title that merely lacks words says nothing."""
    if not title:
        return ""
    theirs = find_type(box_contents(title))
    # The rule holds only where the classifier reads our own name as the product's type.
    if theirs and theirs != product.product_type and find_type(box_contents(product.name)) == product.product_type:
        if theirs != "booster_pack" or PACK_WORDS.search(title):
            game = product.game.slug
            return (f"the shop's title says {type_label(game, theirs).lower()}, "
                    f"not {type_label(game, product.product_type).lower()}")
    ours = codes(product.name) | codes(product.product_set.code if product.product_set_id else "")
    named = codes(title)
    if ours and named and not ours & named:
        found = ", ".join(sorted(f"{family.upper()}-{number:02d}" for family, number in named))
        return f"the shop's title names {found}, another set"
    return ""


def names_agree(product, title):
    """The shop's title and our name agree word for word both ways, as a sure stockist finder match."""
    from .finder import judge

    return bool(title) and judge(product, title) >= AUTO_LINK


def going_rates(product_ids):
    """{product id: [(shop id, item price)]}: current, buyable, OK prices at shops, not marketplaces."""
    rates = {}
    rows = (
        Listing.objects.buyable().filter(product_id__in=product_ids, sanity=OK, price__gt=0)
        .exclude(retailer__source_type__in=sanity.MARKETPLACES).values_list("product_id", "retailer_id", "price")
    )
    for product_id, retailer_id, price in rows:
        rates.setdefault(product_id, []).append((retailer_id, price))
    return rates


def usual_lowest(listing, now):
    """The median of the product's daily lows over the HISTORY_DAYS before the price became doubtful, or
    None with fewer than HISTORY_MIN_DAYS. The days since cannot vouch: the doubtful price is in them."""
    day = timezone.localdate(min(listing.sanity_at or now, now))
    lows = list(
        DailyLowestPrice.objects.filter(
            product_id=listing.product_id, date__lt=day, date__gte=day - timedelta(days=HISTORY_DAYS),
        ).values_list("price", flat=True)
    )
    if len(lows) < HISTORY_MIN_DAYS:
        return None
    return median(lows)


def hide(listing):
    """Take a listing off the site, as the owner's Hide: the other shops are judged again without it,
    and today's history loses a low it may have made."""
    from .pricing import correct_daily_lowest

    Listing.objects.filter(pk=listing.pk).update(is_active=False)
    sanity.judge_product(listing.product_id)
    correct_daily_lowest(listing.product_id)


class Autopilot:
    def __init__(self, now=None, dry_run=False):
        self.now = now or timezone.now()
        self.dry_run = dry_run
        self.done = []

    def answer(self, kind, what, why, act=None, **refs):
        """Record one answer and, unless this is a dry run, act on it in its own short transaction. A busy
        database rolls that one answer back and the rest go on: the next run tries it again."""
        if self.dry_run:
            self.done.append((kind, what, why))
            return
        try:
            with transaction.atomic():
                extra = act() if act else None
                CheckAnswer.objects.create(kind=kind, what=what[:300], why=why[:300], created_at=self.now,
                                           **refs, **(extra or {}))
        except DatabaseError:
            logger.warning("Autopilot could not answer %r; it is tried again next run.", what, exc_info=True)
            return
        self.done.append((kind, what, why))

    def run(self):
        self.found_rows()
        self.doubtful_prices()
        self.wrong_matches()
        self.duplicates()
        self.announced_sets()
        if self.done and not self.dry_run:
            from .signals import clear_list_caches

            clear_list_caches(force=True)
        return self.done

    # Found at another shop

    def found_rows(self):
        from . import finder

        rows = list(checks.found_waiting().select_related("retailer", "suggested__game", "suggested__product_set"))
        rates = going_rates({row.suggested_id for row in rows})
        for row in rows:
            product, shop = row.suggested, row.retailer.name
            others = [price for shop_id, price in rates.get(product.pk, []) if shop_id != row.retailer_id]
            rate = median(others) if others else None
            ratio = row.price / rate if row.price and row.price > 0 and rate else None
            why = contradiction(product, row.title)
            if not why and ratio is not None and not REFUSE_LOW <= ratio <= REFUSE_HIGH:
                why = f"{money(row.price)} is far from the {money(rate)} other shops charge"
            if why:
                self.answer(Kind.REFUSE, f'"{row.title}" at {shop} is not {product.name}', why,
                            act=lambda row=row: finder.ignore(row), shop_product=row, product=product, price=row.price)
                continue
            if row.confidence >= AUTO_LINK and ratio is not None and LINK_LOW <= ratio <= LINK_HIGH:

                def link(row=row, product=product):
                    existed = Listing.objects.filter(product=product, retailer=row.retailer).exists()
                    listing = finder.link(row)
                    return {"listing": None if existed else listing}

                self.answer(Kind.LINK, f"Linked {product.name} at {shop}, {money(row.price)}",
                            f"the names agree word for word and the price is close to the {money(rate)} other "
                            "shops charge", act=link, shop_product=row, product=product, price=row.price)

    # Doubtful prices and wrong matches

    def doubtful_prices(self):
        """Every doubtful price whose title rules it out is hidden; the cheapest ones the product's history
        backs are counted. A dearer one that is neither is left as it is: it never shows as the cheapest."""
        waiting = set(checks.doubtful_waiting().values_list("pk", flat=True))
        listings = (
            checks.judged().filter(sanity=DOUBTFUL)
            .select_related("product__game", "product__product_set", "retailer").order_by("pk")
        )
        for listing in listings:
            product, shop = listing.product, listing.retailer.name
            why = contradiction(product, listing.title)
            if why:
                self.hide(listing, why)
                continue
            if listing.pk not in waiting or not listing.delivery_known or not names_agree(product, listing.title):
                continue
            usual = usual_lowest(listing, self.now)
            if usual and abs(listing.delivered_price - usual) <= HISTORY_BAND * usual:

                def trust(listing=listing):
                    sanity.trust(listing, self.now)

                self.answer(Kind.TRUST, f"Counted {money(listing.delivered_price)} for {product.name} at {shop}",
                            f"the names agree word for word and it is close to this product's usual lowest price "
                            f"of {money(usual)}", act=trust, listing=listing, product=product, price=listing.price)

    def hide(self, listing, why):
        self.answer(Kind.HIDE, f"Hid {money(listing.shown_price)} for {listing.product.name} at {listing.retailer.name}",
                    why, act=lambda: hide(listing), listing=listing, product=listing.product, price=listing.price)

    def wrong_matches(self):
        """The cheapest or the next price, when exactly one of the two has a title that rules it out."""
        for product, summary in checks.wrong_matches():
            ruled_out = [(offer, contradiction(product, offer.title)) for offer in (summary.best, summary.second)]
            ruled_out = [(offer, why) for offer, why in ruled_out if why]
            if len(ruled_out) == 1:
                offer, why = ruled_out[0]
                offer.product = product
                self.hide(offer, why)

    # Possible duplicates

    def duplicates(self):
        from .management.commands.merge_duplicates import merge

        for keep, others in checks.duplicates():
            barcode = ean_key(keep.ean)
            same = [other for other in others if barcode and ean_key(other.ean) == barcode]
            if not same:
                continue
            names = ", ".join(other.name for other in same)

            def merged(keep=keep, same=same):
                merge(keep, same)

            self.answer(Kind.MERGE, f"Merged {names} into {keep.name}", "they carry the same barcode",
                        act=merged, product=keep)

    # Announced sets

    def announced_sets(self):
        from . import releases

        products = {}
        for row in checks.release_candidates(self.now, limit=None):
            if row.product_set_id or row.source.startswith(releases.SHOP_PREFIX) or row.official:
                continue
            if row.game_id not in products:
                products[row.game_id] = list(
                    Product.objects.filter(game_id=row.game_id, is_active=True).values_list("name", "product_set_id")
                )
            rules = releases.set_rules(row.game.slug, [(0, row.name, row.code)])
            named = [set_id for name, set_id in products[row.game_id] if releases.choose_set(name, rules) == 0]
            if not named or any(set_id is not None for set_id in named):
                continue
            label = releases.BY_NAME[row.source].label if row.source in releases.BY_NAME else row.source

            def add(row=row):
                created = releases.find_set(row.game, row.name, row.code) is None
                product_set = releases.add_set(row, row.name)
                return {"product_set": product_set if created else None}

            count = len(named)
            self.answer(Kind.ADD_SET, f"Added the set {row.name} to {row.game.name}",
                        f"{label} announced it and {count} product{'s' if count != 1 else ''} on the site already "
                        "name it. Its date is shown once the publisher or a second source gives it",
                        act=add, release=row)
            # The products it names are filed under it now.
            products.pop(row.game_id, None)


def run(now=None, dry_run=False):
    """Answer every row the evidence settles. Returns [(kind, what, why)]."""
    return Autopilot(now=now, dry_run=dry_run).run()


def enabled():
    return settings.RIPRAPTOR_AUTOPILOT


# Undo --------------------------------------------------------------------------------------------

def undo(answer, now=None):
    """Put back what an answer did, the other way round where that is the owner's meaning. Returns a
    message for the owner, or "" when it can no longer be undone (a merge, or the rows are gone)."""
    from . import finder, releases
    from .pricing import correct_daily_lowest
    from .signals import clear_list_caches

    if answer.undone_at is not None:
        return ""
    message = ""
    with transaction.atomic():
        if answer.kind == Kind.LINK and answer.shop_product is not None:
            row = answer.shop_product
            if answer.listing is not None:
                product_id = answer.listing.product_id
                Listing.objects.filter(pk=answer.listing.pk).delete()
                answer.listing = None
                sanity.judge_product(product_id)
                correct_daily_lowest(product_id)
            finder.ignore(row)
            message = f"Undone: {row.retailer.name}'s \"{row.title}\" is not compared, and the shop is not asked about it again."
        elif answer.kind == Kind.REFUSE and answer.shop_product is not None:
            row = answer.shop_product
            if row.suggested is not None and row.suggested.is_active:
                finder.link(row)
                message = f"Undone: linked {row.suggested.name} at {row.retailer.name}. The next read checks its price and stock."
        elif answer.kind == Kind.HIDE and answer.listing is not None:
            Listing.objects.filter(pk=answer.listing.pk).update(is_active=True)
            sanity.judge_product(answer.listing.product_id)
            message = f"Undone: {answer.listing.retailer.name}'s price for {answer.listing.product.name} is back on the site."
        elif answer.kind == Kind.TRUST and answer.listing is not None:
            Listing.objects.filter(pk=answer.listing.pk).update(trusted_price=None, trusted_at=None)
            sanity.judge_product(answer.listing.product_id)
            message = f"Undone: {answer.listing.retailer.name}'s price for {answer.listing.product.name} is judged again."
        elif answer.kind == Kind.ADD_SET and answer.release is not None:
            row = answer.release
            product_set = answer.product_set
            if product_set is not None:
                Product.objects.filter(product_set=product_set).update(product_set=None)
                Release.objects.filter(product_set=product_set).update(product_set=None)
                ProductSet.objects.filter(pk=product_set.pk).delete()
            row.refresh_from_db()
            releases.dismiss(row)
            message = f"Undone: {row.name} is not a set, and {row.game.name} sets called that are not suggested again from this source."
        elif answer.kind == Kind.APART:
            message = "Undone: the two products may be suggested as duplicates again."
        if message:
            answer.undone_at = now or timezone.now()
            answer.save(update_fields=["undone_at"])
    if message:
        clear_list_caches(force=True)
    return message


def owner_apart(keep, other):
    """The owner's Not the same: the loose duplicate rule never pairs these two again."""
    return CheckAnswer.objects.create(
        kind=Kind.APART, by_owner=True, what=f"{other.name} is not {keep.name}", product=keep, other=other,
    )

