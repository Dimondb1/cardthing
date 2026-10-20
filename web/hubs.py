"""
What the game and set pages (the hubs) say about themselves, from the catalogue at the moment of the request.

Their titles, search engine descriptions and the short paragraph under the heading quote real numbers: how many
products the hub holds, how many a UK shop has in stock now, how many shops have them and the cheapest delivered
price in stock. The wording is in Site wording (content/registry.py, the "meta" and "browse.hub" keys); this
module only works out the numbers and picks the wording that is true for them. Nothing says "in stock" or
"cheapest" unless a shop has it in stock now, so a hub with no stock gets plainer wording.

Three queries per page, however big the hub: the products by type and stock, the shops with a current price,
and the cheapest in-stock offer.
"""

import re
from dataclasses import dataclass, field

from django.db.models import Count, Exists, OuterRef, Q
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from catalogue.models import Listing, Product, stale_cutoff
from catalogue.types import plural_words

# Search engines show about this many characters of a title before the site name, and of a description.
TITLE_LIMIT = 60
DESCRIPTION_LIMIT = 160
# Below this a description says too little, so a long one is cut at a word rather than losing a sentence.
DESCRIPTION_LEAST = 110


@dataclass
class HubStats:
    products: int = 0           # active products in the hub
    in_stock: int = 0           # of those, how many a UK shop has in stock now
    shops: int = 0              # shops with a current price for any of them (in stock, on pre-order or sold out)
    stock_shops: int = 0        # shops with one of them in stock now
    preorder_shops: int = 0     # shops taking pre-orders for one of them now
    listed: int = 0             # shops listing any of them at all, current or not
    stocked_types: list = field(default_factory=list)   # product type codes with something in stock
    all_types: list = field(default_factory=list)       # product type codes in the hub at all
    cheapest: Listing = None    # the cheapest in-stock offer with a known delivered price, with product and shop


def in_stock_listings():
    """Offers a visitor can buy now: current, in stock and not kept out of the comparison."""
    return Listing.objects.buyable().filter(availability=Listing.Availability.IN_STOCK)


def hub_stats(**scope):
    """HubStats for the active products matching ``scope`` (game=... or product_set=...)."""
    stats = HubStats()
    products = Product.objects.active().filter(**scope)
    stocked = Exists(in_stock_listings().order_by().filter(product=OuterRef("pk")))
    rows = products.order_by().annotate(stocked=stocked).values("product_type", "stocked").annotate(n=Count("pk"))
    types, stocked_types = set(), set()
    for row in rows:
        stats.products += row["n"]
        types.add(row["product_type"])
        if row["stocked"]:
            stats.in_stock += row["n"]
            stocked_types.add(row["product_type"])
    stats.all_types, stats.stocked_types = sorted(types), sorted(stocked_types)
    if not stats.products:
        return stats

    listing_scope = {f"product__{key}": value for key, value in scope.items()}
    # A shop counts only with a current price (checked recently and not kept out of the comparison): a shop
    # whose only listing is stale is not one the page compares. ``listed`` tells "no shop lists it" apart from
    # "no shop has a current price for it".
    current = Q(last_checked__gte=stale_cutoff()) & ~Q(sanity=Listing.Sanity.EXCLUDED)
    counted = Listing.objects.live().filter(product__is_active=True, **listing_scope).order_by().aggregate(
        listed=Count("retailer", distinct=True),
        shops=Count("retailer", distinct=True, filter=current),
        stock_shops=Count("retailer", distinct=True, filter=current & Q(availability=Listing.Availability.IN_STOCK)),
        preorder_shops=Count("retailer", distinct=True, filter=current & Q(availability=Listing.Availability.PREORDER)),
    )
    stats.listed = counted["listed"]
    stats.shops, stats.stock_shops = counted["shops"], counted["stock_shops"]
    stats.preorder_shops = counted["preorder_shops"]
    if stats.in_stock:
        stats.cheapest = (
            in_stock_listings()
            .filter(product__is_active=True, delivery_known=True, **listing_scope)
            .select_related("product", "retailer")
            .order_by("delivered_price", "retailer__name", "pk")
            .first()
        )
    return stats


def join_words(words, joiner):
    """["a", "b", "c"] -> "a, b and c", with the site's own word for "and"."""
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} {joiner} {words[-1]}"


def fit_title(build, words, limit=TITLE_LIMIT, short=None):
    """The title ``build(types)`` gives with as many of ``words`` named as fit in ``limit`` characters, at least
    one. ``build`` takes the chosen words. When even one does not fit, ``short`` (a shorter wording built the
    same way) gets the same chance, and failing that the shorter of the two with one word named is used."""
    for builder in (build, short) if short else (build,):
        for count in range(len(words), 0, -1):
            title = builder(words[:count])
            if len(title) <= limit:
                return title
    return min((builder(words[:1]) for builder in (build, short) if builder), key=len)


DOUBLED_STOP = re.compile(r"(?<!\.)\.\.(?!\.)")


def tidy_stops(text):
    """One full stop where a name ending in one ("Marlow Card Co.") meets the full stop after it."""
    return DOUBLED_STOP.sub(".", text)


SENTENCE_END = re.compile(r"(?<=[.?])\s+(?=[A-Z£])")


def fit_description(text, limit=DESCRIPTION_LIMIT, least=DESCRIPTION_LEAST):
    """``text`` cut to ``limit`` characters without cutting a word. Whole sentences go from the end while what
    is left is still ``least`` characters or more; otherwise the text is cut after its last whole word that fits
    and ends with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    sentences = SENTENCE_END.split(text)
    while len(sentences) > 1 and len(" ".join(sentences)) > limit and len(" ".join(sentences[:-1])) >= least:
        sentences.pop()
    text = " ".join(sentences)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return cut + "…"


class Words:
    """Site wording for one request, as views.text gives it."""

    def __init__(self, text, request):
        self.text, self.request = text, request

    def __call__(self, key, **values):
        return self.text(self.request, key, **values)

    def count(self, key, count, **values):
        """A wording with .one and .other forms."""
        return self(f"{key}.{'one' if count == 1 else 'other'}", count=f"{count:,}", **values)

    def types(self, game_slug, codes):
        return plural_words(game_slug, codes)

    def join(self, words):
        return join_words(words, self("meta.and"))


def money(value):
    from .templatetags.ripraptor import gbp

    return gbp(value)


def game_meta(words, game, stats):
    """(title, description) for a game page."""
    name = game.display_short
    if stats.in_stock and stats.cheapest is not None:
        types = words.types(game.slug, stats.stocked_types)
        title = fit_title(
            lambda chosen: words("meta.game.title", game=name, types=words.join(chosen)), types,
            short=lambda chosen: words("meta.game.title_short", game=name, types=words.join(chosen)),
        ) if types else words("meta.game.title_plain", game=name)
        description = words(
            "meta.game.description_stock", game=name, count=f"{stats.in_stock:,}",
            products=words.count("meta.products", stats.in_stock, game=name), shops=words.count("meta.shops", stats.stock_shops),
            price=money(stats.cheapest.delivered_price),
        )
    else:
        title = words("meta.game.title_plain", game=name)
        description = words("meta.game.description", game=name)
    return title, fit_description(tidy_stops(description))


def set_meta(words, product_set, stats, upcoming, date=""):
    """(title, description) for a set page. ``date`` is the release date as shown, for a set still to come."""
    game, name = product_set.game, product_set.name
    values = {"set": name, "game": game.display_short}
    if stats.in_stock and stats.cheapest is not None:
        types = words.types(game.slug, stats.stocked_types)
        title = fit_title(
            lambda chosen: words("meta.set.title", types=words.join(chosen), **values), types,
            short=lambda chosen: words("meta.set.title_short", types=words.join(chosen), **values),
        ) if types else words("meta.set.title_plain", **values)
        description = words(
            "meta.set.description_stock", count=f"{stats.in_stock:,}",
            products=words.count("meta.products", stats.in_stock, game=game.display_short), shops=words.count("meta.shops", stats.stock_shops), price=money(stats.cheapest.delivered_price), **values,
        )
    elif upcoming and date and stats.preorder_shops:
        title = words("meta.set.title_upcoming", **values)
        description = words("meta.set.description_upcoming", date=date, **values)
    elif upcoming and date:
        # Still to come and no shop takes pre-orders yet: the date is all the page can promise.
        title = words("meta.set.title_dated", **values)
        description = words("meta.set.description_dated", date=date, **values)
    else:
        title = words("meta.set.title_plain", **values)
        description = words("meta.set.description", **values)
    return title, fit_description(tidy_stops(description))


def intro(words, stats):
    """The paragraph under a hub's heading, as safe HTML, or "" for a hub with no products. The cheapest
    product in stock is a link to its page."""
    if not stats.products:
        return ""
    products = words.count("browse.hub.products", stats.products)
    if not stats.listed:
        return escape(words("browse.hub.unlisted", products=products))
    if not stats.shops:
        return escape(words("browse.hub.no_current", products=products))
    shops = words.count("browse.hub.shops", stats.shops)
    if not stats.in_stock:
        return escape(words("browse.hub.none_in_stock", products=products, shops=shops))
    first = words("browse.hub.intro", products=products, shops=shops, count=f"{stats.in_stock:,}")
    if stats.cheapest is None:
        return escape(first)
    # The product's name goes in as a marker, so the sentence is escaped whole and the name then becomes a link.
    marker = "\x00product\x00"
    second = escape(tidy_stops(words(
        "browse.hub.lowest", product=marker, price=money(stats.cheapest.delivered_price),
        retailer=stats.cheapest.retailer.name,
    )))
    link = format_html('<a href="{}">{}</a>', stats.cheapest.product.get_absolute_url(), stats.cheapest.product.name)
    return mark_safe(f"{escape(first)} {second.replace(escape(marker), link)}")


def item_list_json(request, page_url, title, description, crumbs_id, products):
    """schema.org CollectionPage for a hub whose main content is ``products`` (the first page of the list as
    the canonical address shows it), as an ItemList."""
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": page_url + "#webpage",
        "url": page_url,
        "name": title,
        "description": description,
        "breadcrumb": {"@id": crumbs_id},
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(products),
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "url": request.build_absolute_uri(product.get_absolute_url()),
                 "name": product.name}
                for i, product in enumerate(products)
            ],
        },
    }
