"""
Price verdicts: every checked price is judged against what the other shops charge for the same product.

    ok        counts as normal
    doubtful  still shown and counted, but never claimed as a saving, a badge or a drop, and listed
              under Things to check with a one-tap fix
    excluded  so far from the other shops that it cannot be this product at this price: kept out of
              every comparison, shown on the product page as not counted, and listed in admin

Nothing is deleted. A verdict is worked out again on every change, and the owner can confirm a price
with one tap, which holds while it moves less than TRUST_BAND for TRUST_DAYS.

A listing with fewer than two other shops to compare against is also judged against its own evidence:
the shop's last good price, the product's lowest price over 90 days and the usual price range of
products of the same kind (a TypeBand). History alone only ever makes a price doubtful, because old
history may hold prices from listings since deleted; only the band can keep a price out, and not while
one other shop charges much the same, since two shops agreeing may mean the product is filed under the
wrong kind. A lone OK that nothing judged is never stored as the shop's last good price. A price that
is not OK is judged only by the history recorded before it stopped being OK, because doubtful prices
stay in the daily history and must not vouch for themselves the next day.

An excluded price stays excluded while fewer than two other shops are left to judge it, or until the
owner shows it: the shops that showed it was impossible selling out does not make it possible. It comes
back without them only when the shop changes the price and the new price's own evidence says it is
right. A price the band kept out is judged again on every change, since the band is still there to
judge it.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from functools import cached_property
from statistics import median

from django.db import transaction
from django.db.models import Count, F, Min, OuterRef, Q, Subquery
from django.utils import timezone

from .models import DailyLowestPrice, Listing, Product, Retailer, TypeBand, stale_cutoff
from .types import type_label

# With two or more other shops, the price against their median: under PEER_EXCLUDE_LOW or over
# PEER_EXCLUDE_HIGH is kept out; under PEER_DOUBT_LOW or over PEER_DOUBT_HIGH is doubtful. The floor
# equals the 70 percent saving cap, so nothing that cap already allows is hidden.
PEER_EXCLUDE_LOW = Decimal("0.30")
PEER_DOUBT_LOW = Decimal("0.55")
PEER_DOUBT_HIGH = Decimal("2.0")
PEER_EXCLUDE_HIGH = Decimal("4.0")
# With only one other shop we cannot tell which is wrong: a gap this wide makes both doubtful.
ONE_PEER_LOW = Decimal("0.30")
ONE_PEER_HIGH = Decimal("3.33")
# An owner-confirmed price holds while the shop moves it by no more than this, for this long.
TRUST_BAND = Decimal("0.10")
TRUST_DAYS = 30
# With fewer than two other shops, against the shop's own last good price: a real clearance can halve
# a price, so only under HISTORY_LOW or over HISTORY_HIGH is doubtful.
HISTORY_LOW = Decimal("0.35")
HISTORY_HIGH = Decimal("3.0")
# Against the product's lowest price over HISTORY_DAYS, once it has HISTORY_MIN_ROWS days of history.
HISTORY_DAYS = 90
HISTORY_MIN_ROWS = 7
HISTORY_FLOOR = Decimal("0.30")
# Against products of the same kind: a band needs BAND_MIN_PRODUCTS prices. The cheapest tenth is already
# left below p10, so a quarter of it is only reached by a different product; a collection box can
# honestly be dear, so a high price is only ever doubtful.
BAND_MIN_PRODUCTS = 8
BAND_EXCLUDE_LOW = Decimal("0.25")
BAND_DOUBT_LOW = Decimal("0.5")
BAND_DOUBT_HIGH = Decimal("4")

# Marketplaces are many sellers, so they are judged against the shops but never judge anyone.
MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)

OK, DOUBTFUL, EXCLUDED = Listing.Sanity.OK, Listing.Sanity.DOUBTFUL, Listing.Sanity.EXCLUDED
SEVERITY = {OK: 0, DOUBTFUL: 1, EXCLUDED: 2}
TRUSTED_REASON = "confirmed by owner"
RATIO_MAX = Decimal("99999.99")
FIELDS = (
    "pk", "product_id", "price", "delivered_price", "delivery_known", "availability", "last_checked",
    "retailer__source_type", "sanity", "sanity_reason", "sanity_ratio", "last_ok_price",
    "trusted_price", "trusted_at", "sanity_at",
)
# Every reason the band gives for keeping a price out starts with this. Such an exclusion is judged
# again on every change, unlike one the other shops made.
BAND_KEPT_OUT = Listing.BAND_KEPT_OUT


def figure(row):
    """What a listing is compared on: the delivered price when delivery is known, else the item price."""
    return row["delivered_price"] if row["delivery_known"] else row["price"]


def money(value):
    return f"£{value.quantize(Decimal('0.01'))}"


def ratio_of(value):
    return min(value.quantize(Decimal("0.01")), RATIO_MAX)


def is_peer(row, cutoff):
    """A shop (not a marketplace) whose price is current, buyable and more than nothing."""
    return (
        row["retailer__source_type"] not in MARKETPLACES
        and figure(row) > 0
        and row["last_checked"] >= cutoff
        and row["availability"] in Listing.BUYABLE
    )


def trust_expiry(now):
    """Owner trust given before this moment no longer applies."""
    return now - timedelta(days=TRUST_DAYS)


def is_trusted(row, now):
    trusted = row["trusted_price"]
    if not trusted or row["trusted_at"] is None or row["trusted_at"] < trust_expiry(now):
        return False
    return abs(row["price"] - trusted) / trusted <= TRUST_BAND


def peer_verdict(row, peers):
    """(sanity, reason, ratio, both) for ``row`` against ``peers``. ``both`` says the one peer is doubtful too."""
    if not peers:
        return OK, "", None, False
    ref = median([figure(p) for p in peers])
    if ref <= 0:
        return OK, "", None, False
    r = figure(row) / ref
    n = len(peers)
    who = f"what {n} other shops charge" if n > 1 else "what the other shop charges"
    around = f", around {money(ref)}"
    if n == 1:
        if ONE_PEER_LOW <= r <= ONE_PEER_HIGH:
            # Two shops are allowed the gap the saving cap allows: a real half-price offer stays a saving.
            return OK, "", None, False
        # Two prices this far apart: either could be the wrong one, so neither is kept out.
        return DOUBTFUL, noted(disagree(peers[0]), row, peers), ratio_of(r), True
    if r < PEER_EXCLUDE_LOW:
        sanity, reason = EXCLUDED, f"under a third of {who}{around}"
    elif r < PEER_DOUBT_LOW:
        sanity, reason = DOUBTFUL, f"well under {who}{around}"
    elif r <= PEER_DOUBT_HIGH:
        return OK, "", None, False
    elif r <= PEER_EXCLUDE_HIGH:
        sanity, reason = DOUBTFUL, f"over twice {who}{around}"
    else:
        sanity, reason = EXCLUDED, f"over four times {who}{around}"
    return sanity, noted(reason, row, peers), ratio_of(r), False


def disagree(other):
    return f"the two shops disagree, {money(figure(other))} at the other"


def noted(reason, row, peers):
    """The reason, saying so when an item price without its delivery was part of the comparison."""
    if not row["delivery_known"] or any(not p["delivery_known"] for p in peers):
        reason += " (item price compared)"
    return reason[:160]


class Evidence:
    """What a listing with fewer than two other shops is judged by besides them, loaded only when needed.

    One query, whatever the number of listings: the product's lowest price and days recorded over the
    HISTORY_DAYS before today (today is left out so a price recorded a moment ago cannot vouch for
    itself), and its band, the set's for its type when there is one, else the game's. A listing that
    stopped being OK on an earlier day is judged by the HISTORY_DAYS before that day, one more query
    for each such day.
    """

    def __init__(self, product_id, now):
        self.product_id = product_id
        self.today = timezone.localdate(now)
        self.earlier = {}

    @cached_property
    def facts(self):
        history = DailyLowestPrice.objects.filter(
            product=OuterRef("pk"), date__lt=self.today, date__gte=self.today - timedelta(days=HISTORY_DAYS),
        ).order_by().values("product")
        bands = TypeBand.objects.filter(
            Q(product_set=OuterRef("product_set")) | Q(product_set__isnull=True),
            game=OuterRef("game"), product_type=OuterRef("product_type"),
        ).order_by(F("product_set").asc(nulls_last=True))
        return Product.objects.filter(pk=self.product_id).values(
            "product_type", "game__slug", "game__name",
            low=Subquery(history.annotate(low=Min("price")).values("low")),
            days=Subquery(history.annotate(days=Count("pk")).values("days")),
            p10=Subquery(bands.values("p10")[:1]),
            p90=Subquery(bands.values("p90")[:1]),
        ).first() or {}

    def history(self, row):
        """(lowest price, days recorded) over the HISTORY_DAYS before today, or for a listing that is not
        OK, before the day it stopped being OK: what it recorded since then cannot vouch for it."""
        day = self.today
        if row["sanity"] != OK and row["sanity_at"] is not None:
            day = min(day, timezone.localdate(row["sanity_at"]))
        if day == self.today:
            return self.facts.get("low"), self.facts.get("days") or 0
        if day not in self.earlier:
            found = DailyLowestPrice.objects.filter(
                product_id=self.product_id, date__lt=day, date__gte=day - timedelta(days=HISTORY_DAYS),
            ).aggregate(low=Min("price"), days=Count("pk"))
            self.earlier[day] = found["low"], found["days"] or 0
        return self.earlier[day]

    @property
    def band(self):
        """(p10, p90) of the product's band, or None."""
        if self.facts.get("p10") is None:
            return None
        return self.facts["p10"], self.facts["p90"]

    def kind(self):
        """'booster box in Pokémon': the product type in the game's own words, lower case unless a name."""
        label = type_label(self.facts["game__slug"], self.facts["product_type"])
        if label == label.capitalize():
            label = label.lower()
        return f"{label} in {self.facts['game__name']}"


def own_verdict(row, evidence):
    """(verdict, judged) for a listing with fewer than two other shops, from its history and its band.

    ``judged`` says some evidence was there to judge it, so an OK may be kept as its last good price.
    """
    verdict, judged = None, False
    last = row["last_ok_price"]
    if last and last > 0:
        judged = True
        r = row["price"] / last
        if r < HISTORY_LOW or r > HISTORY_HIGH:
            verdict = worse(verdict, (DOUBTFUL, noted(f"was {money(last)} last time at this shop", row, []), ratio_of(r)))
    low, days = evidence.history(row)
    if days >= HISTORY_MIN_ROWS and low and low > 0:
        judged = True
        r = figure(row) / low
        if r < HISTORY_FLOOR:
            reason = f"under a third of the lowest in {HISTORY_DAYS} days, {money(low)}"
            verdict = worse(verdict, (DOUBTFUL, noted(reason, row, []), ratio_of(r)))
    band = evidence.band
    if band is not None and band[0] > 0:
        judged = True
        p10, p90 = band
        r = figure(row) / p10
        if r < BAND_EXCLUDE_LOW:
            reason = f"{BAND_KEPT_OUT}{evidence.kind()}, usually from {money(p10)}"
            verdict = worse(verdict, (EXCLUDED, noted(reason, row, []), ratio_of(r)))
        elif r < BAND_DOUBT_LOW:
            reason = f"under half the usual low of {money(p10)} for its type ({evidence.kind()})"
            verdict = worse(verdict, (DOUBTFUL, noted(reason, row, []), ratio_of(r)))
        elif p90 > 0 and figure(row) > BAND_DOUBT_HIGH * p90:
            reason = f"over four times the usual high of {money(p90)} for its type ({evidence.kind()})"
            verdict = worse(verdict, (DOUBTFUL, noted(reason, row, []), ratio_of(figure(row) / p90)))
    return verdict or (OK, "", None), judged


def worse(current, new):
    """The more severe of two (sanity, reason, ratio) verdicts; on a tie the first stands."""
    if current is None or SEVERITY[new[0]] > SEVERITY[current[0]]:
        return new
    return current


def comes_back(row, others, evidence):
    """Whether a price the other shops kept out, now with fewer than two of them, has evidence to come back:
    its own evidence (there must be some) and the one other shop, if any, all say OK."""
    own, judged = own_verdict(row, evidence)
    if not judged or own[0] != OK:
        return False
    return not others or peer_verdict(row, others)[0] == OK


def verdicts(rows, now, evidence=None, repriced=()):
    """({pk: (sanity, reason, ratio)} for every row, {pks with an OK that nothing was there to judge}).

    Two passes: the second leaves out of every comparison the prices the first kept out, so one wild
    price cannot drag the median the others are judged by. A row with two or more other shops is judged
    by them alone; a row with fewer is judged by them, if any, and by its own evidence, the worst winning.
    ``repriced`` holds the rows whose price or delivery changed in the check that asked for the verdicts.
    """
    if evidence is None and rows:
        evidence = Evidence(rows[0]["product_id"], now)
    cutoff = stale_cutoff(now)
    peers = [row for row in rows if is_peer(row, cutoff)]
    peer_pks = {row["pk"] for row in peers}
    trusted = {row["pk"] for row in rows if is_trusted(row, now)}

    def judge(left_out):
        result = {pk: (OK, TRUSTED_REASON, None) for pk in trusted}
        lone = set()
        for row in rows:
            if row["pk"] in trusted:
                continue
            others = [p for p in peers if p["pk"] != row["pk"] and p["pk"] not in left_out]
            if (row["sanity"] == EXCLUDED and len(others) < 2 and not row["sanity_reason"].startswith(BAND_KEPT_OUT)
                    and not (row["pk"] in repriced and comes_back(row, others, evidence))):
                # Only two or more shops can keep a price out, so only two or more, or the owner, let it back
                # in, unless the shop changed the price and the new one has its own evidence.
                result[row["pk"]] = (EXCLUDED, row["sanity_reason"], row["sanity_ratio"])
                continue
            if len(others) >= 2:
                sanity, reason, ratio, _ = peer_verdict(row, others)
                result[row["pk"]] = worse(result.get(row["pk"]), (sanity, reason, ratio))
                continue
            own, judged = own_verdict(row, evidence)
            if not others:
                if not judged:
                    lone.add(row["pk"])
                result[row["pk"]] = worse(result.get(row["pk"]), own)
                continue
            sanity, reason, ratio, both = peer_verdict(row, others)
            if sanity == OK and own[0] == EXCLUDED:
                # The one other shop charges much the same: two shops agreeing may mean the product is
                # filed under the wrong kind, so the band makes it doubtful for the owner, never hidden.
                own = (DOUBTFUL, *own[1:])
            result[row["pk"]] = worse(result.get(row["pk"]), worse((sanity, reason, ratio), own))
            partner = others[0] if both else None
            # Only a peer still in the comparison, whose figure is above nothing, can make its one partner doubtful.
            if partner is not None and row["pk"] in peer_pks and row["pk"] not in left_out and partner["pk"] not in trusted:
                back = (DOUBTFUL, noted(disagree(row), partner, [row]), ratio_of(figure(partner) / figure(row)))
                result[partner["pk"]] = worse(result.get(partner["pk"]), back)
        return result, lone

    first, _ = judge(left_out=set())
    return judge(left_out={pk for pk, verdict in first.items() if verdict[0] == EXCLUDED})


def judge_product(product_id, now=None, repriced=()):
    """Judge every live listing of the product against the other shops and save the verdicts that changed.

    ``repriced`` holds the listings whose price or delivery the calling check changed. ``sanity_at`` is
    when the listing last moved between OK and not OK, so it stays put while a price stays not OK.
    Returns {listing pk: (sanity, reason, ratio)} for every live listing.
    """
    now = now or timezone.now()
    rows = list(
        Listing.objects.filter(product_id=product_id, is_active=True, retailer__is_active=True)
        .order_by("pk").values(*FIELDS)
    )
    if not rows:
        return {}
    result, lone = verdicts(rows, now, repriced=repriced)
    changed = False
    stamp_ok, drop_trust = [], []
    for row in rows:
        sanity, reason, ratio = result[row["pk"]]
        lost_trust = row["trusted_price"] is not None and reason != TRUSTED_REASON
        if (sanity, reason, ratio) != (row["sanity"], row["sanity_reason"], row["sanity_ratio"]):
            update = {"sanity": sanity, "sanity_reason": reason, "sanity_ratio": ratio}
            if row["sanity_at"] is None or (sanity == OK) != (row["sanity"] == OK):
                update["sanity_at"] = now
            if sanity == OK and row["pk"] not in lone:
                update["last_ok_price"] = row["price"]
            if lost_trust:
                update.update(trusted_price=None, trusted_at=None)
            Listing.objects.filter(pk=row["pk"]).update(**update)
            changed = True
            continue
        if sanity == OK and row["pk"] not in lone and row["last_ok_price"] != row["price"]:
            stamp_ok.append(row["pk"])
        if lost_trust:
            drop_trust.append(row["pk"])
    if stamp_ok:
        # The price an OK listing last had, for judging a lone shop against itself later.
        Listing.objects.filter(pk__in=stamp_ok).update(last_ok_price=F("price"))
    if drop_trust:
        Listing.objects.filter(pk__in=drop_trust).update(trusted_price=None, trusted_at=None)
    if changed:
        from .signals import clear_list_caches

        clear_list_caches(force=True)
    return result


def trust(listing, now=None):
    """The owner says this listing's current price is right: it counts for TRUST_DAYS while it moves little."""
    now = now or timezone.now()
    Listing.objects.filter(pk=listing.pk).update(trusted_price=listing.price, trusted_at=now)
    return judge_product(listing.product_id, now=now)


def band_prices():
    """(game id, product type, set id, price) for every product on the site with a price a band can use.

    Each product's cheapest current, buyable, delivery-known shop price whose verdict is OK: a doubtful or
    excluded price, an item price without its delivery and a marketplace never shape what is usual.
    """
    shops = [source for source in Retailer.Source.values if source not in MARKETPLACES]
    usable = Q(
        listings__is_active=True,
        listings__retailer__is_active=True,
        listings__retailer__source_type__in=shops,
        listings__last_checked__gte=stale_cutoff(),
        listings__availability__in=Listing.BUYABLE,
        listings__sanity=OK,
        listings__delivery_known=True,
        listings__delivered_price__gt=0,
    )
    return (
        Product.objects.active()
        .annotate(band_price=Min("listings__delivered_price", filter=usable))
        .filter(band_price__isnull=False)
        .values_list("game_id", "product_type", "product_set_id", "band_price")
    )


def percentile(prices, percent):
    """The sorted price at index floor(percent / 100 x (n - 1)): never interpolated, always a real price."""
    return prices[percent * (len(prices) - 1) // 100]


def bands_from(prices_by_group):
    """{(game id, type, set id or None): (n, p10, median, p90)} for every group of BAND_MIN_PRODUCTS or more."""
    bands = {}
    for key, prices in prices_by_group.items():
        if len(prices) < BAND_MIN_PRODUCTS:
            continue
        prices = sorted(price.quantize(Decimal("0.01")) for price in prices)
        bands[key] = (len(prices), percentile(prices, 10), percentile(prices, 50), percentile(prices, 90))
    return bands


def rebuild_bands(dry_run=False, now=None):
    """Work out every band again from today's prices and save them, deleting groups now too small.

    A product counts in its set's band and in its game's band for its type. Returns the bands as a sorted
    list of (game id, type, set id, n, p10, median, p90). With ``dry_run`` nothing is written.
    """
    now = now or timezone.now()
    groups = defaultdict(list)
    for game_id, product_type, set_id, price in band_prices():
        groups[(game_id, product_type, None)].append(price)
        if set_id is not None:
            groups[(game_id, product_type, set_id)].append(price)
    bands = bands_from(groups)
    if not dry_run:
        with transaction.atomic():
            existing = {(b.game_id, b.product_type, b.product_set_id): b for b in TypeBand.objects.all()}
            gone = [band.pk for key, band in existing.items() if key not in bands]
            if gone:
                TypeBand.objects.filter(pk__in=gone).delete()
            changed, new = [], []
            for key, (n, p10, mid, p90) in bands.items():
                band = existing.get(key)
                if band is None:
                    new.append(TypeBand(game_id=key[0], product_type=key[1], product_set_id=key[2],
                                        n=n, p10=p10, median=mid, p90=p90, computed_at=now))
                    continue
                band.n, band.p10, band.median, band.p90, band.computed_at = n, p10, mid, p90, now
                changed.append(band)
            TypeBand.objects.bulk_create(new)
            TypeBand.objects.bulk_update(changed, ["n", "p10", "median", "p90", "computed_at"])
    return sorted(
        ((*key, *stats) for key, stats in bands.items()),
        key=lambda band: (band[0], band[1], band[2] is not None, band[2] or 0),
    )
