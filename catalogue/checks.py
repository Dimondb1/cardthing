"""
What the owner should look at, for the Things to check page in admin: prices
the other shops make doubtful or impossible, wrong matches behind impossible
savings, products that look like duplicates, products the stockist finder
may have found at another shop, shops whose delivery charge is not known,
announced sets waiting for a tap, release dates the sources disagree on and
release sources that have stopped answering. Each comes with its fix.
"""

from datetime import timedelta

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import offers
from .models import Listing, OutboundClick, Product, Release, ReleaseSourceState, Retailer, ShopProduct
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


def judged():
    """Listings that are judged and shown: a hidden listing, a switched-off shop or product waits until it is back."""
    return Listing.objects.live().filter(product__is_active=True)


def sanity_counts():
    """{'doubtful': n, 'excluded': n} over every judged listing, in one query, for the section headings."""
    return judged().aggregate(
        doubtful=Count("pk", filter=Q(sanity=Listing.Sanity.DOUBTFUL)),
        excluded=Count("pk", filter=Q(sanity=Listing.Sanity.EXCLUDED)),
    )


def doubtful_count():
    """How many doubtful prices wait for the owner, counted as the page counts them."""
    return judged().filter(sanity=Listing.Sanity.DOUBTFUL).count()


def doubtful_prices(now=None):
    """Listings whose price the other shops make doubtful, most clicked products first, then oldest verdict."""
    since = (now or timezone.now()) - timedelta(days=CLICK_DAYS)
    clicks = (
        OutboundClick.objects.filter(product=OuterRef("product_id"), created_at__gte=since)
        .order_by().values("product").annotate(n=Count("id")).values("n")
    )
    return list(
        judged().filter(sanity=Listing.Sanity.DOUBTFUL)
        .annotate(clicks=Coalesce(Subquery(clicks, output_field=IntegerField()), Value(0)))
        .select_related("product", "retailer")
        .order_by("-clicks", "sanity_at", "pk")[:SANITY_ROWS]
    )


def excluded_prices():
    """Listings kept out of the comparison because they are far from every other shop, newest verdict first."""
    return list(
        judged().filter(sanity=Listing.Sanity.EXCLUDED)
        .select_related("product", "retailer")
        .order_by("-sanity_at", "pk")[:SANITY_ROWS]
    )


def wrong_matches():
    """[(product, summary)] where the cheapest price is doubtful or too far under the next to be the same thing.

    Products with a doubtful price come from doubtful_prices; the saving cap still catches a gap between
    prices that have not been judged yet.
    """
    doubtful = {listing.product_id for listing in doubtful_prices()}
    products = Product.objects.for_lists().filter(lowest_price__isnull=False).prefetch_related(offers.buyable_prefetch())
    rows = []
    for product in products.order_by("name"):
        summary = offers.summarise(product)
        if (summary.suspect or product.pk in doubtful) and summary.best and summary.second:
            rows.append((product, summary))
    return rows


def duplicates():
    """[(keep, [others])] that the loose rule would merge. Strict duplicates are merged every hour already."""
    from .management.commands.merge_duplicates import duplicate_groups

    return duplicate_groups(loose=True)


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


def found_stockists():
    """Products the finder may have found at another shop, newest first, in one query."""
    return list(found_waiting().select_related("retailer", "suggested").order_by("-last_seen", "-pk")[:FOUND_ROWS])


def release_candidates(now=None):
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
        .select_related("game", "product_set").order_by("-first_seen_at", "-pk")[:RELEASE_ROWS]
    )


def release_disagreements(now=None):
    """[{'game', 'name', 'set', 'rows', 'hand_date'}] where sources give one set dates more than a day apart.

    Each row is a source's date with its own Use this date button. A date the set had before any source
    was read (typed in admin or imported, with no source recorded) counts as one of the dates, as
    'hand_date' with a Keep this date button, so a lone source that contradicts it is shown too. A set
    whose date the owner chose is left alone: the owner's answer stands. One query.
    """
    from .releases import SHOP_PREFIX, local_today, name_key, recent_cutoff

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
