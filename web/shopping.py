"""
The shopping list: products a visitor means to buy, and the cheapest way to buy the lot, delivery included.

The owner, 11 Nov 2026, on a shopping list: "But shopping list does sound good as well."

* The list is a cookie holding only the products' addresses (COOKIE): no account, nothing about the visitor, and it
  works with script off, since Add to list is a form the server answers. The Terms page says so.
* The plan buys each product from one seller and pays each shop's delivery once, on the whole order, under the shop's
  own rules: free over its threshold, its standard charge below it (Retailer.delivery_for). A charge we do not know is
  never guessed: such an offer is used only when nothing else has the product, and the plan says delivery is extra.
  A shop whose standard charge we lack but whose every item's charge we hold is counted at the sum of those charges,
  as if each came in its own parcel: never less than the shop will ask.
* eBay and Amazon offers are each their own seller, with their own delivery, never combined.
* Only offers in stock count, or a pre-order for a product nobody has in stock. A doubtful price (it may not be this
  product) is left out.
* It is compared with buying each product on its own at the cheapest delivered price its page shows.
* Every possible plan is tried when there are few enough (EXACT_LIMIT); a longer list is searched from the cheapest
  start by moving products between sellers and emptying sellers, until nothing gets cheaper.
"""

import itertools
import re
from dataclasses import dataclass, field
from decimal import Decimal

from catalogue import pricing
from catalogue.models import Listing, Product

COOKIE = "rr_list"
COOKIE_DAYS = 90
MAX_ITEMS = 20
SLUG = re.compile(r"^[-a-z0-9_]{1,200}$")
# Candidates kept per product, cheapest first, and the most plans tried one by one.
CANDIDATES = 8
EXACT_LIMIT = 6000
# What an offer whose delivery we do not know weighs in the search: so much that it is used only when it must be.
UNKNOWN = Decimal("100000")


# The cookie ----------------------------------------------------------------------------------------------------------

def read(request):
    """The products on the visitor's list, as slugs in the order added, at most MAX_ITEMS."""
    seen = []
    for slug in (request.COOKIES.get(COOKIE) or "").split("."):
        if SLUG.match(slug) and slug not in seen:
            seen.append(slug)
    return seen[:MAX_ITEMS]


def write(response, slugs, secure=False):
    """Keep ``slugs`` as the list, or forget it when empty."""
    if slugs:
        response.set_cookie(COOKIE, ".".join(slugs[:MAX_ITEMS]), max_age=COOKIE_DAYS * 86400, samesite="Lax",
                            httponly=True, secure=secure)
    else:
        response.delete_cookie(COOKIE, samesite="Lax")
    return response


def added(slugs, slug):
    """The list with ``slug`` on it (last), or unchanged when it is there or the list is full."""
    if slug in slugs or len(slugs) >= MAX_ITEMS:
        return list(slugs)
    return [*slugs, slug]


def removed(slugs, slug):
    return [kept for kept in slugs if kept != slug]


# The plan ------------------------------------------------------------------------------------------------------------

def is_marketplace(listing):
    return listing.retailer.source_type in pricing.MARKETPLACES


def seller_key(listing):
    """Who an order goes to: the shop, or for eBay and Amazon each offer's own seller."""
    return ("offer", listing.pk) if is_marketplace(listing) else ("shop", listing.retailer_id)


def order_delivery(listings):
    """The delivery charge on one order of ``listings`` from one seller, or None when we do not know it."""
    first = listings[0]
    if is_marketplace(first):
        return first.delivery_cost if first.delivery_known else None
    subtotal = sum((listing.price for listing in listings), Decimal("0"))
    charge = first.retailer.delivery_for(subtotal)
    if charge is not None:
        return charge
    if all(listing.delivery_known for listing in listings):
        return sum((listing.delivery_cost for listing in listings), Decimal("0"))
    return None


def plan_cost(choice):
    """What buying ``choice`` (one listing for each product) costs, with UNKNOWN for each product in an order whose
    delivery we do not know: so no product joins such an order to save money we cannot show."""
    groups = {}
    for listing in choice:
        groups.setdefault(seller_key(listing), []).append(listing)
    total = Decimal("0")
    for listings in groups.values():
        delivery = order_delivery(listings)
        total += sum((listing.price for listing in listings), Decimal("0")) + (
            UNKNOWN * len(listings) if delivery is None else delivery)
    return total


def single_cost(listing):
    """One product bought on its own, as its page gives it: the delivered price, or the item price and UNKNOWN."""
    return listing.delivered_price if listing.delivery_known else listing.price + UNKNOWN


def candidates(listings):
    """The offers a plan may use for one product, cheapest on its own first: in stock, or pre-orders when none is."""
    usable = [listing for listing in listings if listing.is_buyable and listing.sanity == Listing.Sanity.OK]
    in_stock = [listing for listing in usable if listing.availability == Listing.Availability.IN_STOCK]
    chosen = in_stock or [listing for listing in usable if listing.availability == Listing.Availability.PREORDER]
    return sorted(chosen, key=lambda listing: (single_cost(listing), listing.retailer.name))[:CANDIDATES]


def search(options):
    """The cheapest choice of one listing from each list in ``options``."""
    size = 1
    for listed in options:
        size *= len(listed)
    if size <= EXACT_LIMIT:
        return list(min(itertools.product(*options), key=plan_cost))
    starts = [[listed[0] for listed in options]]
    # Also from the seller with the most of the list: everything it has comes from it.
    counts = {}
    for listed in options:
        for key in {seller_key(listing) for listing in listed}:
            counts[key] = counts.get(key, 0) + 1
    busiest = max(counts, key=counts.get)
    starts.append([next((listing for listing in listed if seller_key(listing) == busiest), listed[0])
                   for listed in options])
    return min((improve(start, options) for start in starts), key=plan_cost)


def improve(choice, options):
    """Move products between sellers, one at a time or a seller's whole order at once, while the plan gets cheaper."""
    choice, best = list(choice), plan_cost(choice)
    better = True
    while better:
        better = False
        for i, listed in enumerate(options):
            for listing in listed:
                if listing is choice[i]:
                    continue
                trial = choice[:i] + [listing] + choice[i + 1:]
                cost = plan_cost(trial)
                if cost < best:
                    choice, best, better = trial, cost, True
        for key in {seller_key(listing) for listing in choice}:
            trial = list(choice)
            for i, listing in enumerate(choice):
                if seller_key(listing) == key:
                    others = [other for other in options[i] if seller_key(other) != key]
                    if others:
                        trial[i] = min(others, key=lambda other: plan_cost(trial[:i] + [other] + trial[i + 1:]))
            cost = plan_cost(trial)
            if cost < best:
                choice, best, better = trial, cost, True
    return choice


@dataclass
class Order:
    """What the plan buys from one seller."""
    listings: list
    delivery: Decimal | None
    subtotal: Decimal = Decimal("0")
    total: Decimal | None = None
    retailer: object = None
    marketplace: bool = False

    def __post_init__(self):
        self.retailer = self.listings[0].retailer
        self.marketplace = is_marketplace(self.listings[0])
        self.subtotal = sum((listing.price for listing in self.listings), Decimal("0"))
        self.total = None if self.delivery is None else self.subtotal + self.delivery


@dataclass
class Plan:
    products: list                         # every product on the list, in the order added
    orders: list = field(default_factory=list)
    missing: list = field(default_factory=list)   # products nobody has to buy now
    total: Decimal | None = None           # the plan: every product, and every delivery charge we know
    separately: Decimal | None = None      # every product, each bought on its own at its cheapest delivered price
    saving: Decimal | None = None          # only when every charge is known, so it is never more than the truth
    unknown: int = 0                       # orders whose delivery we do not know, and so is extra
    oldest: object = None                  # when the plan's prices were checked: the oldest and newest
    newest: object = None

    @property
    def shops(self):
        return len(self.orders)


def plan(slugs):
    """The Plan for the products ``slugs`` name. Two queries: the products, and every offer for them."""
    found = {product.slug: product for product in Product.objects.for_lists().filter(slug__in=slugs)}
    products = [found[slug] for slug in slugs if slug in found]
    result = Plan(products=products)
    if not products:
        return result
    by_product = {}
    for listing in (Listing.objects.filter(product__in=products).live().select_related("retailer")
                    .order_by("delivered_price", "retailer__name")):
        by_product.setdefault(listing.product_id, []).append(listing)
    options, buyable = [], []
    for product in products:
        listed = candidates(by_product.get(product.pk, []))
        if listed:
            for listing in listed:
                listing.product = product   # the row needs no query of its own
            options.append(listed)
            buyable.append(product)
        else:
            result.missing.append(product)
    if not options:
        return result
    choice = search(options)
    groups = {}
    for listing in choice:
        groups.setdefault(seller_key(listing), []).append(listing)
    orders = [Order(listings, order_delivery(listings)) for listings in groups.values()]
    result.orders = sorted(orders, key=lambda order: (-len(order.listings), order.retailer.name))
    result.unknown = sum(1 for order in orders if order.delivery is None)
    result.total = sum((order.subtotal + (order.delivery or 0) for order in orders), Decimal("0"))
    if not result.unknown:
        # The same products, each on its own at the cheapest delivered price its page shows.
        result.separately = sum((min(listing.delivered_price for listing in listed if listing.delivery_known)
                                 for listed in options), Decimal("0"))
        if result.separately > result.total:
            result.saving = result.separately - result.total
    checked = [listing.last_checked for listing in choice]
    result.oldest, result.newest = min(checked), max(checked)
    return result
