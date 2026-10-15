"""
What the owner should look at, for the Things to check page in admin: prices
the other shops make doubtful or impossible, wrong matches behind impossible
savings, products that look like duplicates, products the stockist finder
may have found at another shop, shops whose delivery charge is not known,
announced sets waiting for a tap, release dates the sources disagree on and
release sources that have stopped answering. Each comes with its fix.

Only rows that change what a visitor sees are listed: a doubtful price that is
dearer than another shop's, or out of stock, never shows as the cheapest, so it
is left as it is. The autopilot (catalogue/autopilot.py) answers the rows the
evidence settles before the owner sees them.
"""

from copy import copy
from datetime import timedelta

from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import offers
from .models import CheckAnswer, Listing, OutboundClick, Product, Release, ReleaseSourceState, Retailer, ShopProduct
from .pricing import MARKETPLACES

# How many doubtful or excluded prices the page lists at once.
SANITY_ROWS = 50
# Doubtful prices of the products people clicked through for in this many days come first.
CLICK_DAYS = 7
# How many products found at another shop the page lists at once.
FOUND_ROWS = 50
# How many announced sets, and release date disagreements, the page lists at once.
RELEASE_ROWS = 50
# Sources further apart than this on a set's date go to the owner.
DATE_GAP_DAYS = 1
# The autopilot's answers are listed at the top of the page, with Undo, for this long, this many at once.
ANSWERS_DAYS = 7
ANSWER_ROWS = 100


def judged():
    """Listings that are judged and shown: a hidden listing, a switched-off shop or product waits until it is back."""
    return Listing.objects.live().filter(product__is_active=True)


def sanity_counts():
    """{'doubtful': n, 'excluded': n} over every judged listing, in one query, for the section headings."""
    return judged().aggregate(
        doubtful=Count("pk", filter=Q(sanity=Listing.Sanity.DOUBTFUL)),
        excluded=Count("pk", filter=Q(sanity=Listing.Sanity.EXCLUDED)),
    )


def doubtful_waiting(checked=False):
    """Doubtful prices a visitor sees as the product's cheapest: buyable, and no buyable price comes before
    them in the order the product page shows (confirmed delivered prices first, cheapest first, then the
    prices whose delivery is not known).

    A doubtful price that comes after another shop's, or is out of stock, never shows as the cheapest and
    claims no saving, so it waits for nobody. It is listed again if it becomes the cheapest.

    A price checked as the right product (a standing Checked answer for this price and title) waits for
    nobody either; ``checked`` gives those instead.
    """
    buyable = Listing.objects.buyable().filter(product=OuterRef("product_id"))
    known_cheaper = buyable.filter(delivery_known=True, delivered_price__lt=OuterRef("delivered_price"))
    any_known = buyable.filter(delivery_known=True)
    unknown_cheaper = buyable.filter(delivery_known=False, delivered_price__lt=OuterRef("delivered_price"))
    is_checked = Exists(CheckAnswer.objects.filter(
        kind=CheckAnswer.Kind.CHECKED, undone_at__isnull=True, listing=OuterRef("pk"), price=OuterRef("price"),
        title=OuterRef("title"),
    ))
    rows = (
        Listing.objects.buyable().filter(product__is_active=True, sanity=Listing.Sanity.DOUBTFUL)
        .exclude(Q(delivery_known=True) & Exists(known_cheaper))
        .exclude(Q(delivery_known=False) & (Exists(any_known) | Exists(unknown_cheaper)))
    )
    return rows.filter(is_checked) if checked else rows.exclude(is_checked)


def doubtful_count():
    """How many doubtful prices wait for the owner, counted as the page counts them."""
    return doubtful_waiting().count()


def checked_count():
    """How many doubtful prices are checked as the right product and left as shown."""
    return doubtful_waiting(checked=True).count()


def doubtful_prices(now=None):
    """Doubtful prices that wait for the owner, most clicked products first, then oldest verdict."""
    since = (now or timezone.now()) - timedelta(days=CLICK_DAYS)
    clicks = (
        OutboundClick.objects.filter(product=OuterRef("product_id"), created_at__gte=since)
        .order_by().values("product").annotate(n=Count("id")).values("n")
    )
    return list(
        doubtful_waiting()
        .annotate(clicks=Coalesce(Subquery(clicks, output_field=IntegerField()), Value(0)))
        .select_related("product__game", "product__product_set", "retailer")
        .order_by("-clicks", "sanity_at", "pk")[:SANITY_ROWS]
    )


def excluded_prices():
    """Listings kept out of the comparison because they are far from every other shop, newest verdict first."""
    return list(
        judged().filter(sanity=Listing.Sanity.EXCLUDED)
        .select_related("product", "retailer")
        .order_by("-sanity_at", "pk")[:SANITY_ROWS]
    )


def wrong_matches(doubtful_too=False):
    """[(product, summary)] where the cheapest confirmed delivered price is more than MAX_REAL_PERCENT under
    the next one: usually a different product matched (a pack against a box). The site already claims no
    saving for these. A product whose cheapest price is doubtful is listed under doubtful prices instead.

    ``doubtful_too`` also lists every product whose cheapest or next price is doubtful, for the
    suspect_savings command, which lists every saving the site holds back.
    """
    products = Product.objects.for_lists().filter(lowest_price__isnull=False).prefetch_related(offers.buyable_prefetch())
    rows = []
    for product in products.order_by("name"):
        summary = offers.summarise(product)
        best, second = summary.best, summary.second
        if doubtful_too:
            if summary.suspect and best and second:
                rows.append((product, summary))
            continue
        if not (best and second and best.delivery_known and second.delivery_known) or second.delivered_price <= 0:
            continue
        if best.sanity == Listing.Sanity.DOUBTFUL:
            continue
        gap = (second.delivered_price - best.delivered_price) / second.delivered_price * 100
        if gap > offers.MAX_REAL_PERCENT:
            rows.append((product, summary))
    return rows


def kept_apart():
    """{frozenset of two product pks} the owner said are not the same product, still standing."""
    return {
        frozenset(pair) for pair in CheckAnswer.objects.filter(
            kind=CheckAnswer.Kind.APART, undone_at__isnull=True, product__isnull=False, other__isnull=False,
        ).values_list("product_id", "other_id")
    }


def shop_listings(products=None):
    """{product id: {shop id: shown}} for listings at shops, not marketplaces, in one query: ``shown`` is
    False for a listing hidden by the owner, the autopilot or Claude. ``products`` limits it to those
    product ids; by default every product still shown."""
    rows = Listing.objects.exclude(retailer__source_type__in=MARKETPLACES)
    rows = rows.filter(product_id__in=products) if products is not None else rows.filter(product__is_active=True)
    found = {}
    for product_id, retailer_id, shown in rows.values_list("product_id", "retailer_id", "is_active"):
        found.setdefault(product_id, {})[retailer_id] = shown
    return found


def shown_at(listings):
    """The shops of a product's shop_listings that show it."""
    return {shop for shop, shown in listings.items() if shown}


def shops_in_the_way(keep, other):
    """Why the shops say two products may not be merged, from their shop_listings, or "".

    A shop that shows both under their own names sells two products. eBay and Amazon search for every
    product, so they say nothing, and nor does a hidden listing (often another product the shop sells).
    A shop whose listing on the kept product is hidden and on the other is shown would lose its price
    from the site, since a merge leaves a shop's second listing on the switched-off product."""
    if shown_at(keep) & shown_at(other):
        return "a shop sells both"
    if {shop for shop, shown in keep.items() if not shown} & shown_at(other):
        return "a shop's price would leave the site"
    return ""


def duplicates():
    """[(keep, [others])] of products that may be one product, from the pairs merge_duplicates.duplicate_pairs
    finds: each other is paired with its keep on its own, never through a third product. Strict duplicates
    are merged every hour already; what is left here is for the owner or Claude. Three queries.

    A pair the owner said are not the same is left out, and so is one a shop shows both of: a shop that
    shows both under their own names sells two products (shops_in_the_way: never eBay or Amazon, nor a
    hidden listing). When a shop shows one and has hidden its listing on the other, the one it shows is
    kept, so its price stays on the site. Two others one shop shows side by side go under their keep
    separately, so Merge these never joins them.
    """
    from .management.commands.merge_duplicates import duplicate_pairs

    pairs = duplicate_pairs()
    if not pairs:
        return []
    apart = kept_apart()
    shops = shop_listings()
    by_keep = {}
    for keep, other in pairs:
        if frozenset((keep.pk, other.pk)) in apart:
            continue
        ours, theirs = shops.get(keep.pk, {}), shops.get(other.pk, {})
        if shops_in_the_way(ours, theirs):
            if shops_in_the_way(theirs, ours):
                continue
            keep, other = other, keep
        by_keep.setdefault(keep.pk, (keep, []))[1].append(other)
    found = []
    for keep, others in sorted(by_keep.values(), key=lambda row: (row[0].name, row[0].pk)):
        groups = []
        for other in others:
            theirs = shops.get(other.pk, {})
            group = next((g for g in groups if not shops_in_the_way(g[0], theirs)), None)
            if group is None:
                group = (dict(shops.get(keep.pk, {})), [])
                groups.append(group)
            taken, members = group
            taken.update({shop: shown or taken.get(shop, False) for shop, shown in theirs.items()})
            # Its own copy for each pair: the page hangs Claude's answer about this pair on it.
            members.append(copy(other))
        found += [(keep, members) for _, members in groups]
    return found


def still_offered(keep, other):
    """Whether Possible duplicates still offers this pair, checked for these two products alone: both shown,
    not kept apart, a key in common and no shop in the way."""
    from .management.commands.merge_duplicates import share_a_key

    products = {p.pk: p for p in Product.objects.filter(pk__in=[keep.pk, other.pk], is_active=True)
                .select_related("game", "product_set")}
    if len(products) != 2 or frozenset(products) in kept_apart():
        return False
    if not share_a_key(products[keep.pk], products[other.pk]):
        return False
    shops = shop_listings(list(products))
    ours, theirs = shops.get(keep.pk, {}), shops.get(other.pk, {})
    return not (shops_in_the_way(ours, theirs) and shops_in_the_way(theirs, ours))


def unknown_delivery_shops():
    """Shops, not marketplaces, with prices whose delivery charge is not known, most affected first."""
    return list(
        Retailer.objects.filter(is_active=True)
        .exclude(source_type__in=MARKETPLACES)
        .annotate(unknown=Count("listings", filter=Q(listings__delivery_known=False, listings__is_active=True)))
        .filter(unknown__gt=0)
        .order_by("-unknown", "name")
    )


def found_waiting():
    """Rows the stockist finder found that wait for the owner, at shops and of products still shown."""
    return ShopProduct.objects.filter(
        source=ShopProduct.Source.FINDER, status=ShopProduct.Status.REVIEW, retailer__is_active=True,
        suggested__isnull=False, suggested__is_active=True,
    )


def found_stockists(now=None):
    """Products the finder may have found at another shop, the ones visitors want most first, then newest.

    One query for the rows and the finder's interest queries for the order.
    """
    from .finder import interest_scores

    rows = list(found_waiting().select_related("retailer", "suggested__game", "suggested__product_set")
                .order_by("-last_seen", "-pk"))
    if len(rows) > 1:
        scores = interest_scores(now, visitors_only=True)
        rows.sort(key=lambda row: -scores.get(row.suggested_id, 0))
    return rows[:FOUND_ROWS]


def release_candidates(now=None, limit=RELEASE_ROWS):
    """Announced sets no rule could add, waiting for Add set or Not a set, newest first, in one query.

    A row about a set the site has is listed only when it gives a full date and the set has none (a lone
    community source's date for a set added by hand, say): otherwise it has nothing to add, and a date
    that differs from the set's is under release dates to confirm. Rows about sets released long ago are
    recorded but never listed.
    """
    from .releases import local_today, recent_cutoff

    cutoff = recent_cutoff(local_today(now or timezone.now()))
    undated_set = Q(product_set__release_date__isnull=True, precision=Release.Precision.DAY)
    return list(
        Release.objects.filter(status=Release.Status.PENDING, game__is_active=True)
        .filter(Q(product_set__isnull=True) | undated_set)
        .filter(Q(release_date__isnull=True) | Q(release_date__gte=cutoff))
        .select_related("game", "product_set").order_by("-first_seen_at", "-pk")[:limit]
    )


def release_disagreements(now=None):
    """[{'game', 'name', 'set', 'rows', 'hand_date'}] where sources give one set dates more than a day apart.

    Each row is a source's date with its own Use this date button. A date the set had before any source
    was read (typed in admin or imported, with no source recorded) counts as one of the dates, as
    'hand_date' with a Keep this date button, so a lone source that contradicts it is shown too. A set
    whose date the owner chose is left alone: the owner's answer stands. So is a set showing the
    publisher's date that only community sources contradict: the rules keep the publisher's date, and it
    would win anyway. One query.
    """
    from .releases import SHOP_PREFIX, is_official, local_today, name_key, recent_cutoff

    cutoff = recent_cutoff(local_today(now or timezone.now()))
    rows = (
        Release.objects.filter(precision=Release.Precision.DAY, release_date__gte=cutoff, game__is_active=True)
        .exclude(status=Release.Status.DISMISSED).exclude(source__startswith=SHOP_PREFIX)
        .select_related("game", "product_set__game").order_by("game__name", "release_date", "source")
    )
    groups = {}
    for row in rows:
        key = (row.game_id, ("set", row.product_set_id) if row.product_set_id else ("name", name_key(row.name)))
        groups.setdefault(key, []).append(row)
    found = []
    for members in groups.values():
        product_set = next((r.product_set for r in members if r.product_set_id), None)
        if product_set is not None and product_set.release_date_source == "owner":
            continue
        if product_set is not None and product_set.release_date and is_official(product_set.release_date_source):
            shown = product_set.release_date
            if not any(r.official for r in members if abs((r.release_date - shown).days) > DATE_GAP_DAYS):
                continue
        hand_date = None
        if product_set is not None and product_set.release_date and not product_set.release_date_source:
            hand_date = product_set.release_date
        dates = [r.release_date for r in members] + ([hand_date] if hand_date else [])
        if (max(dates) - min(dates)).days <= DATE_GAP_DAYS:
            continue
        found.append({"game": members[0].game, "name": product_set.name if product_set else members[0].name,
                      "set": product_set, "rows": members, "hand_date": hand_date})
    return found[:RELEASE_ROWS]


def name_sources(rows):
    """Give each Release row a source_label the owner can read: the source's own name ("TCGdex"), or "a
    Total Cards product title" for a shop's title. One query for the shops, however many rows."""
    from .releases import BY_NAME, SHOP_PREFIX

    rows = list(rows)
    slugs = {row.source[len(SHOP_PREFIX):] for row in rows if row.source.startswith(SHOP_PREFIX)}
    shops = dict(Retailer.objects.filter(slug__in=slugs).values_list("slug", "name")) if slugs else {}
    for row in rows:
        if row.source in BY_NAME:
            row.source_label = BY_NAME[row.source].label
        elif row.source.startswith(SHOP_PREFIX):
            slug = row.source[len(SHOP_PREFIX):]
            row.source_label = f"a {shops.get(slug, slug)} product title"
        else:
            row.source_label = row.source
    return rows


def stale_release_sources(now=None):
    """Release sources with no good read in STALE_SOURCE_DAYS days, counted from when first tried."""
    from .releases import BY_NAME, STALE_SOURCE_DAYS

    cutoff = (now or timezone.now()) - timedelta(days=STALE_SOURCE_DAYS)
    states = ReleaseSourceState.objects.filter(name__in=BY_NAME).filter(
        Q(last_ok_at__lt=cutoff) | Q(last_ok_at__isnull=True, created_at__lt=cutoff)
    ).order_by("name")
    return [{"state": state, "source": BY_NAME[state.name]} for state in states]


def undoable():
    """The answers Sorted for you lists with Undo: the autopilot's, Claude's and the hourly merge's, and the
    owner's own merges (Merge these), which can be undone like any other."""
    return CheckAnswer.objects.filter(Q(by_owner=False) | Q(kind=CheckAnswer.Kind.MERGE))


def recent_answers(now=None):
    """What the autopilot, Claude and the hourly merge answered, and what the owner merged, in the last
    ANSWERS_DAYS days, newest first, in one query."""
    since = (now or timezone.now()) - timedelta(days=ANSWERS_DAYS)
    return list(
        undoable().filter(created_at__gte=since)
        .select_related("listing", "shop_product", "product", "release")
        .order_by("-created_at", "-pk")
    )
