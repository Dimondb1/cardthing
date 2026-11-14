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
* Each product has a quantity, 1 to MAX_QUANTITY, kept in the cookie as slug~2. A shop's delivery is worked out on the
  whole order's value; an eBay or Amazon offer's delivery is counted once for each one bought, never less than the
  seller can ask. Shops do not say how many they have, so the page says to check the basket.
* Next to the cheapest plan, the one with the fewest sellers (fewest), all from one shop when one has everything:
  the owner, 14 Nov 2026: "provide the most convenient purchase based on availability, delivery as it could be
  cheaper overall to get it through one seller".
"""

import itertools
import re
from dataclasses import dataclass, field
from decimal import Decimal

from catalogue import pricing
from catalogue.models import Listing, Product

COOKIE = "rr_list"
# The number of products on the list alone, which the header's script reads (the list's cookie is the server's only).
COUNT_COOKIE = "rr_list_n"
COOKIE_DAYS = 90
MAX_ITEMS = 20
MAX_QUANTITY = 10
SLUG = re.compile(r"^[-a-z0-9_]{1,200}$")
# Candidates kept per product, cheapest first, and the most plans tried one by one.
CANDIDATES = 8
EXACT_LIMIT = 6000
# What an offer whose delivery we do not know weighs in the search: so much that it is used only when it must be.
UNKNOWN = Decimal("100000")


# The cookie ----------------------------------------------------------------------------------------------------------

def quantity(raw):
    """A quantity as the cookie or a form gives it, kept to 1 to MAX_QUANTITY; 1 when it is not a number."""
    try:
        return min(max(int(str(raw).strip()), 1), MAX_QUANTITY)
    except (TypeError, ValueError):
        return 1


def read_items(request):
    """The list as (slug, quantity) in the order added, at most MAX_ITEMS. A cookie from before quantities holds
    slugs alone, each one of."""
    seen, items = set(), []
    for entry in (request.COOKIES.get(COOKIE) or "").split("."):
        slug, _tilde, count = entry.partition("~")
        if SLUG.match(slug) and slug not in seen:
            seen.add(slug)
            items.append((slug, quantity(count) if count else 1))
    return items[:MAX_ITEMS]


def read(request):
    """The products on the visitor's list, as slugs in the order added, at most MAX_ITEMS."""
    return [slug for slug, _count in read_items(request)]


def quantities(request):
    return dict(read_items(request))


def write(response, slugs, secure=False, counts=None):
    """Keep ``slugs`` as the list, each with its quantity in ``counts`` (1 when not given), or forget it when empty.
    A second cookie holds only how many products, for the header's count."""
    counts = counts or {}
    slugs = slugs[:MAX_ITEMS]
    if slugs:
        value = ".".join(slug if quantity(counts.get(slug, 1)) == 1 else f"{slug}~{quantity(counts[slug])}"
                         for slug in slugs)
        response.set_cookie(COOKIE, value, max_age=COOKIE_DAYS * 86400, samesite="Lax", httponly=True, secure=secure)
        response.set_cookie(COUNT_COOKIE, str(len(slugs)), max_age=COOKIE_DAYS * 86400, samesite="Lax",
                            secure=secure)
    else:
        response.delete_cookie(COOKIE, samesite="Lax")
        response.delete_cookie(COUNT_COOKIE, samesite="Lax")
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


def count_of(listing):
    """How many of the listing's product the list wants (plan sets it on each offer it weighs)."""
    return getattr(listing, "qty", 1)


def line(listing):
    """The listing's price for the quantity wanted."""
    return listing.price * count_of(listing)


def order_delivery(listings):
    """The delivery charge on one order of ``listings`` from one seller, or None when we do not know it. An eBay or
    Amazon offer's charge is counted once for each one bought: never less than the seller can ask."""
    first = listings[0]
    if is_marketplace(first):
        return first.delivery_cost * count_of(first) if first.delivery_known else None
    subtotal = sum((line(listing) for listing in listings), Decimal("0"))
    charge = first.retailer.delivery_for(subtotal)
    if charge is not None:
        return charge
    if all(listing.delivery_known for listing in listings):
        return sum((listing.delivery_cost * count_of(listing) for listing in listings), Decimal("0"))
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
        total += sum((line(listing) for listing in listings), Decimal("0")) + (
            UNKNOWN * len(listings) if delivery is None else delivery)
    return total


def alone(listing):
    """The product bought on its own from this offer, the quantity wanted, delivered; None when delivery is unknown.
    One of it is its delivered price, as its page gives it."""
    if count_of(listing) == 1:
        return listing.delivered_price if listing.delivery_known else None
    delivery = order_delivery([listing])
    return None if delivery is None else line(listing) + delivery


def single_cost(listing):
    """One product bought on its own: what ``alone`` gives, or the price and UNKNOWN."""
    cost = alone(listing)
    return line(listing) + UNKNOWN if cost is None else cost


def usable_offers(listings):
    """Every offer a plan may use for one product, cheapest on its own first: in stock, or pre-orders when none is."""
    usable = [listing for listing in listings if listing.is_buyable and listing.sanity == Listing.Sanity.OK]
    in_stock = [listing for listing in usable if listing.availability == Listing.Availability.IN_STOCK]
    chosen = in_stock or [listing for listing in usable if listing.availability == Listing.Availability.PREORDER]
    return sorted(chosen, key=lambda listing: (single_cost(listing), listing.retailer.name))


def candidates(listings):
    """The cheapest CANDIDATES of usable_offers, which the cheapest plan chooses among."""
    return usable_offers(listings)[:CANDIDATES]


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


def fewest(options):
    """The choice that buys every product from the fewest sellers, the cheapest such; ``options`` are every usable
    offer for each product. A seller with everything wins outright; otherwise sellers are taken, most products first,
    until every product is covered, and the cheapest plan among them is found."""
    sellers = {}
    for i, listed in enumerate(options):
        for listing in listed:
            sellers.setdefault(seller_key(listing), {}).setdefault(i, []).append(listing)
    everything = [key for key, has in sellers.items() if len(has) == len(options)]
    if everything:
        return min(([min(sellers[key][i], key=line) for i in range(len(options))] for key in everything),
                   key=plan_cost)
    left, chosen = set(range(len(options))), set()
    while left:
        def weight(key):
            covers = left & sellers[key].keys()
            return (len(covers), -sum((min(single_cost(listing) for listing in sellers[key][i]) for i in covers),
                                      Decimal("0")))
        key = max(sellers, key=weight)
        chosen.add(key)
        left -= sellers[key].keys()
    return search([[listing for listing in listed if seller_key(listing) in chosen][:CANDIDATES]
                   for listed in options])


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
        self.subtotal = sum((line(listing) for listing in self.listings), Decimal("0"))
        self.total = None if self.delivery is None else self.subtotal + self.delivery


def orders_for(choice):
    """The orders a choice makes, the seller with most first."""
    groups = {}
    for listing in choice:
        groups.setdefault(seller_key(listing), []).append(listing)
    orders = [Order(listings, order_delivery(listings)) for listings in groups.values()]
    return sorted(orders, key=lambda order: (-len(order.listings), order.retailer.name))


@dataclass
class Way:
    """Another way to buy the list: its orders, its total and how many orders have delivery we do not know."""
    orders: list
    total: Decimal
    unknown: int
    extra: Decimal | None = None   # what it costs over the cheapest plan, when both totals are whole

    @property
    def shops(self):
        return len(self.orders)


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
    fewest: Way | None = None              # the fewest sellers, when that is fewer than the cheapest plan's
    counts: dict = field(default_factory=dict)   # product pk: quantity
    many: bool = False                     # more than one of something is wanted

    @property
    def shops(self):
        return len(self.orders)


def plan(slugs, counts=None):
    """The Plan for the products ``slugs`` name, ``counts`` of each (slug: quantity, 1 when not given). Two queries:
    the products, and every offer for them."""
    counts = counts or {}
    found = {product.slug: product for product in Product.objects.for_lists().filter(slug__in=slugs)}
    products = [found[slug] for slug in slugs if slug in found]
    result = Plan(products=products, counts={product.pk: quantity(counts.get(product.slug, 1)) for product in products})
    result.many = any(count > 1 for count in result.counts.values())
    if not products:
        return result
    by_product = {}
    for listing in (Listing.objects.filter(product__in=products).live().select_related("retailer")
                    .order_by("delivered_price", "retailer__name")):
        by_product.setdefault(listing.product_id, []).append(listing)
    options, every, buyable = [], [], []
    for product in products:
        for listing in by_product.get(product.pk, []):
            listing.product = product   # the row needs no query of its own
            listing.qty = result.counts[product.pk]
        listed = usable_offers(by_product.get(product.pk, []))
        if listed:
            options.append(listed[:CANDIDATES])
            every.append(listed)
            buyable.append(product)
        else:
            result.missing.append(product)
    if not options:
        return result
    choice = search(options)
    result.orders = orders_for(choice)
    result.unknown = sum(1 for order in result.orders if order.delivery is None)
    result.total = sum((order.subtotal + (order.delivery or 0) for order in result.orders), Decimal("0"))
    if not result.unknown:
        # The same products, each on its own at the cheapest delivered price its page shows, for the quantity wanted.
        cheapest = [min((cost for cost in map(alone, listed) if cost is not None), default=None) for listed in options]
        if None not in cheapest:
            result.separately = sum(cheapest, Decimal("0"))
            if result.separately > result.total:
                result.saving = result.separately - result.total
    checked = [listing.last_checked for listing in choice]
    result.oldest, result.newest = min(checked), max(checked)
    if result.shops > 1:
        other = orders_for(fewest(every))
        if len(other) < result.shops:
            unknown = sum(1 for order in other if order.delivery is None)
            total = sum((order.subtotal + (order.delivery or 0) for order in other), Decimal("0"))
            result.fewest = Way(other, total, unknown,
                                extra=total - result.total if not unknown and not result.unknown else None)
    return result
