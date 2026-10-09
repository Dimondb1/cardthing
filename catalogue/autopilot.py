"""
The autopilot: answers the rows on the Things to check page that the evidence settles, so the owner only
sees the ones that need a person. It runs every hour (tidy_all) and when the owner taps Sort what you can
now. Every answer is written down as a CheckAnswer with what was done and why, and listed at the top of
the page with an Undo button.

It answers only from what the site already holds: the shop's own title and what the other shops charge.
It never adds a shop, a product or a price no shop gave, never publishes a release date, and never does
what cannot be undone (a merge). It answers each listing and each found page once: after an answer,
undone or not, and after the owner hid a price himself, that row is the owner's.

Found at another shop
    No, when the shop's title plainly names another kind of product (a pack against a box, an ETB against
    a bundle) or only another set's code (OP-10 against OP-09), or its price is under 0.3 or over 3.33
    times what two or more other shops charge. One other shop is not enough: it may be the wrong one.
    Yes, when the names agree word for word both ways, the shop does not list the product already, and
    the price is within a quarter of what the other shops charge. A likely name waits for the owner, and
    so does a page with no other shop to compare. So does a page whose barcode is not ours, however well
    its name agrees: the finder held it back for that, and only the owner can link it.

Doubtful prices and wrong matches
    Hide, for the product's cheapest price or either price of a wrong match, when the shop's title plainly
    names another kind of product or only another set's code. A doubtful price is never counted on the
    site's own say-so: the product's history may hold that same price.

Announced sets
    Add set, with no date, when a community source names a set that products on the site already name,
    no product it names is filed under another set, and the game has a publisher source that can give
    the date later. Its date is published only by the usual rules: the publisher's own, or two sources
    agreeing.
"""

import logging
import re
from decimal import Decimal
from statistics import median

from django.conf import settings
from django.db import DatabaseError, models, transaction
from django.utils import timezone

from . import checks, sanity
from .classify import TYPES, box_contents, find_type
from .matching import AUTO_LINK, expand
from .models import CheckAnswer, Listing, Product, ProductSet, Release, ShopProduct
from .types import type_label

logger = logging.getLogger(__name__)

Kind = CheckAnswer.Kind
OK, DOUBTFUL = Listing.Sanity.OK, Listing.Sanity.DOUBTFUL

# Found at another shop: a sure name is linked when its price is within this band of the other shops'.
LINK_LOW, LINK_HIGH = Decimal("0.75"), Decimal("1.33")
# It is refused when its price is outside the band at which two shops make each other doubtful, and only
# against two or more other shops: with one, either price may be the wrong one.
REFUSE_LOW, REFUSE_HIGH = sanity.ONE_PEER_LOW, sanity.ONE_PEER_HIGH
REFUSE_PEERS = 2

# Kinds a shop title names plainly, none of which is ever another of them. Collection boxes, gift sets,
# tins and decks are left out: shops call one product a "Box Set", a "Gift Set" or a "Display".
CLEAR_KINDS = {"booster_box", "booster_pack", "elite_trainer_box", "bundle", "collector_booster_box",
               "collector_booster_pack"}
# What says a title is of a kind. A bare "booster" says nothing: nearly every title carries it.
KIND_PHRASES = {kind: tuple(p for p in phrases if p != "booster") for kind, phrases in TYPES}
# A title read as a pack must say pack, and one pack: "Blazing Dominion Booster" is as often the box,
# and "3-Pack" or "3 x Booster Packs" is a bundle.
PACK_WORDS = re.compile(r"\bpacks?\b|\bpacket\b|\bchecklane\b|\bsleeved\b", re.I)
MULTI_PACK = re.compile(r"\d+\s*[-x×]?\s*(?:booster\s*)?packs?\b|\bpacks?\s+of\s+\d", re.I)

# Set codes that name one set and no other: One Piece (OP-09, EB-02, PRB-01, ST-21) and Dragon Ball
# (FB05, BT24). A title that names only codes other than the product's is another set's product.
SET_CODE = re.compile(r"(?<![a-z0-9])(op|eb|prb|st|fb|bt)-?0*(\d{1,2})(?![0-9])", re.I)


class Stale(Exception):
    """The row changed between the autopilot reading it and acting on it: it is left for the next run."""


def money(value):
    return sanity.money(value)


def codes(text):
    return {(family.lower(), int(number)) for family, number in SET_CODE.findall(text or "")}


def says_kind(title, kind):
    text = " " + expand(box_contents(title)) + " "
    return any(f" {phrase} " in text for phrase in KIND_PHRASES.get(kind, ()))


def contradiction(product, title):
    """Why the shop's title cannot be this product, or "": it plainly names another kind of product and
    not ours, or only another set's code. A title that merely lacks words, or names a kind loosely, says
    nothing."""
    if not title:
        return ""
    theirs, ours = find_type(box_contents(title)), product.product_type
    plain = (
        theirs in CLEAR_KINDS and ours in CLEAR_KINDS and theirs != ours
        # The classifier must read our own name as the product's type, and the title must not say it too.
        and find_type(box_contents(product.name)) == ours and not says_kind(title, ours)
        and (theirs != "booster_pack" or (PACK_WORDS.search(title) and not MULTI_PACK.search(title)))
    )
    if plain:
        game = product.game.slug
        return (f"the shop's title says {type_label(game, theirs).lower()}, "
                f"not {type_label(game, ours).lower()}")
    mine = codes(product.name) | codes(product.product_set.code if product.product_set_id else "")
    named = codes(title)
    if mine and named and not mine & named:
        found = ", ".join(sorted(f"{family.upper()}-{number:02d}" for family, number in named))
        return f"the shop's title names {found}, another set"
    return ""


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


def hide(listing):
    """Take a listing off the site, as the owner's Hide: the other shops are judged again without it,
    and today's history loses a low it may have made."""
    from .pricing import correct_daily_lowest

    Listing.objects.filter(pk=listing.pk).update(is_active=False)
    sanity.judge_product(listing.product_id)
    correct_daily_lowest(listing.product_id)


def owner_hid(listing):
    """Note the owner's own Hide, so the autopilot never answers that listing again."""
    return CheckAnswer.objects.create(
        kind=Kind.HIDE, by_owner=True, what=f"Hid {money(listing.shown_price)} for {listing.product.name} at "
        f"{listing.retailer.name}", listing=listing, product=listing.product, price=listing.price,
    )


def still_waiting(row):
    if not ShopProduct.objects.filter(pk=row.pk, status=ShopProduct.Status.REVIEW).exists():
        raise Stale


def refuse_found(row):
    """No: the stockist finder's row is not its product. Raises Stale when the row no longer waits."""
    from . import finder

    still_waiting(row)
    finder.ignore(row)


def link_found(row, keep_ok=False):
    """Yes: link the stockist finder's row to its product. Raises Stale, so the caller's transaction rolls
    back, when the row no longer waits, the shop's barcode is not ours (only the owner links that), or the
    shop lists the product already (linking would move that listing). With ``keep_ok`` it also rolls back
    when the new price is not judged OK against the other shops, or makes a price that was OK doubtful.
    Returns the fields for its CheckAnswer."""
    from . import finder

    still_waiting(row)
    product = row.suggested
    if finder.barcodes_differ(row) or Listing.objects.filter(product=product, retailer=row.retailer).exists():
        raise Stale
    before = dict(Listing.objects.filter(product=product).values_list("pk", "sanity"))
    listing = finder.link(row)
    if keep_ok:
        after = dict(Listing.objects.filter(product=product, is_active=True).values_list("pk", "sanity"))
        if listing is None or after.get(listing.pk) != OK:
            raise Stale
        if any(verdict == OK and after.get(pk, OK) != OK for pk, verdict in before.items()):
            raise Stale
    return {"listing": listing}


def answered_listings():
    """Listings with any answer, the owner's or the autopilot's, undone or not: they are the owner's now."""
    return set(CheckAnswer.objects.filter(listing__isnull=False).values_list("listing_id", flat=True))


class Autopilot:
    def __init__(self, now=None, dry_run=False):
        self.now = now or timezone.now()
        self.dry_run = dry_run
        self.done = []
        self.answered = answered_listings()

    def answer(self, kind, what, why, act=None, **refs):
        """Record one answer and, unless this is a dry run, act on it in its own short transaction. A row
        that changed since it was read, or a busy database, rolls that one answer back and the rest go on:
        the next run looks at it again."""
        if self.dry_run:
            self.done.append((kind, what, why))
            return
        try:
            with transaction.atomic():
                extra = act() if act else None
                CheckAnswer.objects.create(kind=kind, what=what[:300], why=why[:300], created_at=self.now,
                                           **refs, **(extra or {}))
        except Stale:
            return
        except DatabaseError:
            logger.warning("Autopilot could not answer %r; it is tried again next run.", what, exc_info=True)
            return
        self.done.append((kind, what, why))
        if refs.get("listing") is not None:
            self.answered.add(refs["listing"].pk)

    def run(self):
        self.found_rows()
        self.doubtful_prices()
        self.wrong_matches()
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
        listed = set(
            Listing.objects.filter(product_id__in={row.suggested_id for row in rows}).values_list("product_id", "retailer_id")
        )

        for row in rows:
            product, shop = row.suggested, row.retailer.name
            others = [price for shop_id, price in rates.get(product.pk, []) if shop_id != row.retailer_id]
            rate = median(others) if others else None
            ratio = row.price / rate if row.price and row.price > 0 and rate else None
            why = contradiction(product, row.title)
            if not why and ratio is not None and len(others) >= REFUSE_PEERS and not REFUSE_LOW <= ratio <= REFUSE_HIGH:
                why = f"{money(row.price)} is far from the {money(rate)} that {len(others)} other shops charge"
            if why:
                self.answer(Kind.REFUSE, f'"{row.title}" at {shop} is not {product.name}', why,
                            act=lambda row=row: refuse_found(row), shop_product=row, product=product, price=row.price)
                continue
            # A shop that lists the product already is the owner's to sort: linking would move its listing.
            if (product.pk, row.retailer_id) in listed:
                continue
            # A different barcode is the owner's to judge, however well the name agrees.
            if finder.barcodes_differ(row):
                continue
            if row.confidence >= AUTO_LINK and ratio is not None and LINK_LOW <= ratio <= LINK_HIGH:
                who = "the other shop charges" if len(others) == 1 else f"that {len(others)} other shops charge"
                self.answer(Kind.LINK, f"Linked {product.name} at {shop}, {money(row.price)}",
                            f"the names agree word for word and the price is close to the {money(rate)} {who}",
                            act=lambda row=row: link_found(row), shop_product=row, product=product, price=row.price)

    # Doubtful prices and wrong matches

    def doubtful_prices(self):
        """The doubtful prices shown as a product's cheapest whose title rules them out are hidden. A dearer
        one never shows as the cheapest, and hiding it could leave a wrong price as the product's."""
        listings = (
            checks.doubtful_waiting()
            .select_related("product__game", "product__product_set", "retailer").order_by("pk")
        )
        for listing in listings:
            if listing.pk in self.answered:
                continue
            why = contradiction(listing.product, listing.title)
            if why:
                self.hide(listing, why, sanity=DOUBTFUL)

    def hide(self, listing, why, **unchanged):
        def act():
            # Only the price the autopilot looked at is hidden, as with the owner's Hide.
            if not Listing.objects.filter(pk=listing.pk, is_active=True, price=listing.price, trusted_price__isnull=True,
                                          **unchanged).exists():
                raise Stale
            hide(listing)

        self.answer(Kind.HIDE, f"Hid {money(listing.shown_price)} for {listing.product.name} at {listing.retailer.name}",
                    why, act=act, listing=listing, product=listing.product, price=listing.price)

    def wrong_matches(self):
        """The cheapest or the next price, when exactly one of the two has a title that rules it out."""
        for product, summary in checks.wrong_matches():
            pair = (summary.best, summary.second)
            if any(offer.pk in self.answered for offer in pair):
                continue
            ruled_out = [(offer, contradiction(product, offer.title)) for offer in pair]
            ruled_out = [(offer, why) for offer, why in ruled_out if why]
            if len(ruled_out) == 1:
                offer, why = ruled_out[0]
                offer.product = product
                self.hide(offer, why)

    # Announced sets

    def announced_sets(self):
        from . import releases

        # A game whose publisher is read can date a set later; for the rest the owner's Add set gives it.
        dated = {source.game for source in releases.SOURCES if source.official}
        products = {}
        for row in checks.release_candidates(self.now, limit=None):
            if row.product_set_id or row.source.startswith(releases.SHOP_PREFIX) or row.official:
                continue
            if row.game.slug not in dated:
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
                if not Release.objects.filter(pk=row.pk, status=Release.Status.PENDING, product_set__isnull=True).exists():
                    raise Stale
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
    message for the owner, or "" when it can no longer be undone (the rows are gone, or a set has
    gained more since)."""
    from . import finder
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
            message = undo_set(answer)
        elif answer.kind == Kind.APART:
            message = "Undone: the two products may be suggested as duplicates again."
        elif answer.kind == Kind.MERGE and answer.undo_note and answer.product is not None and answer.product.is_active \
                and not later_merges(answer).exists():
            # Merges undo newest first: one that a later merge built on waits until that merge is undone.
            from .management.commands.merge_duplicates import unmerge

            restored = unmerge(answer.undo_note)
            if restored:
                names = ", ".join(product.name for product in restored)
                message = f"Undone: {names} is its own product again, with its prices and its address."
        if not message:
            # Whatever was tried is put back: an Undo that cannot finish changes nothing.
            transaction.set_rollback(True)
        else:
            answer.undone_at = now or timezone.now()
            answer.save(update_fields=["undone_at"])
            if answer.ask_id:
                # Undoing Claude's answer is the owner saying the opposite.
                from .models import ClaudeAsk

                opposite = "different" if answer.ask.verdict == "same" else "same"
                ClaudeAsk.objects.filter(pk=answer.ask_id).update(owner_answer=opposite)
    if message:
        clear_list_caches(force=True)
    return message


def undo_set(answer):
    """Take away a set the autopilot added, unless it has gained a date or another source since: then it
    is more than the autopilot's guess, and the owner changes it in admin."""
    from . import releases

    row = answer.release
    product_set = answer.product_set
    if product_set is not None:
        others = Release.objects.filter(product_set=product_set, status=Release.Status.ACCEPTED).exclude(pk=row.pk)
        if product_set.release_date is not None or others.exists():
            return ""
        Product.objects.filter(product_set=product_set).update(product_set=None)
        Release.objects.filter(product_set=product_set).update(product_set=None)
        ProductSet.objects.filter(pk=product_set.pk).delete()
    row.refresh_from_db()
    releases.dismiss(row)
    return f"Undone: {row.name} is not a set, and {row.game.name} sets called that are not suggested again from this source."


def later_merges(answer):
    """Merges made after this one that touch either of its products and are not undone: they must be undone
    first, or this Undo would put back prices and history from the middle of them."""
    involved = [pk for pk in (answer.product_id, answer.other_id) if pk]
    return CheckAnswer.objects.filter(kind=Kind.MERGE, pk__gt=answer.pk, undone_at__isnull=True).filter(
        models.Q(product_id__in=involved) | models.Q(other_id__in=involved))


def owner_apart(keep, other):
    """The owner's Not the same: the loose duplicate rule never pairs these two again."""
    return CheckAnswer.objects.create(
        kind=Kind.APART, by_owner=True, what=f"{other.name} is not {keep.name}", product=keep, other=other,
    )
