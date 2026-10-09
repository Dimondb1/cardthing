"""
Price verdicts: every checked price is judged against what the other shops charge for the same product.

    ok        counts as normal
    doubtful  still shown and counted, but never claimed as a saving, a badge or a drop, and listed
              under Things to check with a one-tap fix
    excluded  so far from the other shops that it cannot be this product at this price: kept out of
              every comparison, shown on the product page as not counted, and listed in admin

Nothing is deleted. A verdict is worked out again on every change, and the owner can confirm a price
with one tap, which holds while it moves less than TRUST_BAND for TRUST_DAYS.

A listing with no other shop to compare against is OK, but that OK is never stored as the shop's last
good price. An excluded price stays excluded while fewer than two other shops are left to judge it, or
until the owner shows it: the shops that showed it was impossible selling out does not make it possible.
"""

from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.db.models import F
from django.utils import timezone

from .models import Listing, Retailer, stale_cutoff

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

# Marketplaces are many sellers, so they are judged against the shops but never judge anyone.
MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)

OK, DOUBTFUL, EXCLUDED = Listing.Sanity.OK, Listing.Sanity.DOUBTFUL, Listing.Sanity.EXCLUDED
SEVERITY = {OK: 0, DOUBTFUL: 1, EXCLUDED: 2}
TRUSTED_REASON = "confirmed by owner"
RATIO_MAX = Decimal("99999.99")
FIELDS = (
    "pk", "price", "delivered_price", "delivery_known", "availability", "last_checked",
    "retailer__source_type", "sanity", "sanity_reason", "sanity_ratio", "last_ok_price",
    "trusted_price", "trusted_at",
)


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


def worse(current, new):
    """The more severe of two (sanity, reason, ratio) verdicts; on a tie the first stands."""
    if current is None or SEVERITY[new[0]] > SEVERITY[current[0]]:
        return new
    return current


def verdicts(rows, now):
    """({pk: (sanity, reason, ratio)} for every row, {pks that had no other shop to compare against}).

    Two passes: the second leaves out of every comparison the prices the first kept out, so one wild
    price cannot drag the median the others are judged by.
    """
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
            if row["sanity"] == EXCLUDED and len(others) < 2:
                # Only two or more shops can keep a price out, so only two or more, or the owner, let it back in.
                result[row["pk"]] = (EXCLUDED, row["sanity_reason"], row["sanity_ratio"])
                continue
            if not others:
                lone.add(row["pk"])
                result[row["pk"]] = worse(result.get(row["pk"]), (OK, "", None))
                continue
            sanity, reason, ratio, both = peer_verdict(row, others)
            result[row["pk"]] = worse(result.get(row["pk"]), (sanity, reason, ratio))
            partner = others[0] if both else None
            # Only a peer still in the comparison, whose figure is above nothing, can make its one partner doubtful.
            if partner is not None and row["pk"] in peer_pks and row["pk"] not in left_out and partner["pk"] not in trusted:
                back = (DOUBTFUL, noted(disagree(row), partner, [row]), ratio_of(figure(partner) / figure(row)))
                result[partner["pk"]] = worse(result.get(partner["pk"]), back)
        return result, lone

    first, _ = judge(left_out=set())
    return judge(left_out={pk for pk, verdict in first.items() if verdict[0] == EXCLUDED})


def judge_product(product_id, now=None):
    """Judge every live listing of the product against the other shops and save the verdicts that changed.

    Returns {listing pk: (sanity, reason, ratio)} for every live listing.
    """
    now = now or timezone.now()
    rows = list(
        Listing.objects.filter(product_id=product_id, is_active=True, retailer__is_active=True)
        .order_by("pk").values(*FIELDS)
    )
    if not rows:
        return {}
    result, lone = verdicts(rows, now)
    changed = False
    stamp_ok, drop_trust = [], []
    for row in rows:
        sanity, reason, ratio = result[row["pk"]]
        lost_trust = row["trusted_price"] is not None and reason != TRUSTED_REASON
        if (sanity, reason, ratio) != (row["sanity"], row["sanity_reason"], row["sanity_ratio"]):
            update = {"sanity": sanity, "sanity_reason": reason, "sanity_ratio": ratio, "sanity_at": now}
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
