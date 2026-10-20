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


# Set codes ----------------------------------------------------------------------------------------
#
# Searchers type a set's code with buying words ("eb05 booster box uk", "op18 booster box uk"), so set and product
# titles carry it. Only a code with letters and digits is shown: three-letter codes such as FIN, PRE or SSP are
# not what people search and some are ordinary words.

CODE_PART = re.compile(r"[a-z]+|\d+|\.")


def shown_code(product_set):
    """The set's code as titles show it ("OP-18", "EB05", "SV8.5"), or "" when it has none worth showing. A code
    stored all in lower case is shown in upper case; otherwise it is shown as the publisher writes it."""
    code = " ".join((getattr(product_set, "code", "") or "").split())
    if not (re.search(r"[A-Za-z]", code) and re.search(r"\d", code)):
        return ""
    return code.upper() if code == code.lower() else code


def code_pattern(code):
    """A pattern finding ``code`` in a name loosely: any case, with or without a space or hyphen where letters
    meet digits, and with or without zeros before a number ("OP-18" finds "OP18" and "op 18", "EB05" finds
    "EB-5")."""
    # Parts are found before separators go, so a range code keeps its numbers apart ("BT19-20": 19 then 20).
    parts = CODE_PART.findall(code.lower())
    if not parts:
        return None
    pattern = ""
    for index, part in enumerate(parts):
        if index and part != "." and parts[index - 1] != ".":
            pattern += r"[\s-]?"
        if part.isdigit():
            pattern += "0*" + (part.lstrip("0") or "0")
        else:
            pattern += re.escape(part)
    return re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", re.I)


def has_code(name, code):
    """Whether ``name`` already gives ``code``, compared loosely (code_pattern)."""
    pattern = code_pattern(code) if code else None
    return bool(pattern and pattern.search(name or ""))


def search_name(name):
    """``name`` as search titles and descriptions give it: an em dash from a shop's or publisher's name becomes a
    space, since the site's copy uses none and titles already end in a colon ("Ditto Premium Collection, em dash,
    30th Celebration" -> "Ditto Premium Collection 30th Celebration"). Page headings keep the name as stored."""
    name = name or ""
    if "\u2014" not in name:
        return name
    return " ".join(re.sub("\u2014", " ", name).split())


def name_with_code(product):
    """The product's name (search_name) with its set's code put in when the name does not give it: after the
    set's name when the product's name contains it, else before the product type ("Heroines Edition Vol.2 Booster
    Box" -> "Heroines Edition Vol.2 EB05 Booster Box"), else in front."""
    from catalogue.matching import set_codes

    name = search_name(product.name)
    code = shown_code(product.product_set) if product.product_set_id else ""
    if not code or has_code(name, code):
        return name
    # A name giving some other set's code is left alone rather than given two: the product may be filed wrongly.
    game = getattr(getattr(product, "game", None), "slug", "")
    if set_codes(name, game):
        return name
    lowered = name.lower()
    set_name = (product.product_set.name or "").strip().lower()
    at = re.search(rf"(?<![a-z0-9]){re.escape(set_name)}(?![a-z0-9])", lowered) if set_name else None
    if at:
        return f"{name[:at.end()]} {code}{name[at.end():]}"
    type_name = (product.type_name or "").lower()
    at = re.search(rf"(?<![a-z0-9]){re.escape(type_name)}", lowered) if type_name else None
    if at:
        return f"{name[:at.start()].rstrip()} {code} {name[at.start():]}".lstrip()
    return f"{code} {name}"


def fitted(candidates, limit=TITLE_LIMIT):
    """The first of ``candidates`` (titles, best first) within ``limit`` characters, or None."""
    return next((title for title in candidates if len(title) <= limit), None)


PRODUCT_TITLES = {
    False: ("meta.product.title_priced", "meta.product.title_priced_uk", "meta.product.title_priced_short",
            "meta.product.title_priced_shortest"),
    True: ("meta.product.title_preorder", "meta.product.title_preorder_uk", "meta.product.title_preorder_short",
           "meta.product.title_preorder_shortest"),
}


def product_title(words, name, price, preorder, **values):
    """A product page's title quoting ``price``: the longest wording that fits, keeping "UK" (what searchers type)
    and then "delivered" as long as they fit, and "pre-order" when the price quoted is a pre-order. ``name`` keeps its set code even
    when the title then runs long: the code is what searchers type, and the name is never cut."""
    titles = [words(key, product=name, price=price, **values) for key in PRODUCT_TITLES[bool(preorder)]]
    return fitted(titles) or titles[-1]


def set_labels(words, product_set):
    """The set's name as titles may give it, best first: with its code, then without."""
    code = shown_code(product_set)
    if code and not has_code(product_set.name, code):
        return [words("meta.set.name_code", set=product_set.name, code=code), product_set.name]
    return [product_set.name]


def set_title(words, labels, keys, types=(), **values):
    """The first title within the limit, trying each label (with the code first) in each wording ``keys`` (best
    first) with as many ``types`` as fit. The code goes before the wording and the types, so a title keeps the
    code ahead of a longer wording; the set's name is never dropped. When nothing fits, the shortest wording
    with the code is used, since dropping the code would not make it fit either."""
    def built(label, key, count):
        extra = {"types": words.join(list(types)[:count])} if types else {}
        return words(key, set=label, **extra, **values)

    counts = range(len(types), 0, -1) if types else (0,)
    for label in labels:
        title = fitted(built(label, key, count) for key in keys for count in counts)
        if title:
            return title
    return min((built(labels[0], key, counts[-1]) for key in keys), key=len)


def set_meta(words, product_set, stats, upcoming, date=""):
    """(title, description) for a set page. ``date`` is the release date as shown, for a set still to come."""
    game = product_set.game
    labels = set_labels(words, product_set)
    values = {"game": game.display_short}
    # Descriptions give the code too: they are not cut for it, so there is room.
    described = {"set": labels[0], **values}
    if stats.in_stock and stats.cheapest is not None:
        types = words.types(game.slug, stats.stocked_types)
        if types:
            title = set_title(words, labels, ("meta.set.title", "meta.set.title_short"), types, **values)
        else:
            title = set_title(words, labels, ("meta.set.title_plain", "meta.set.title_plain_short"), **values)
        description = words(
            "meta.set.description_stock", count=f"{stats.in_stock:,}",
            products=words.count("meta.products", stats.in_stock, game=game.display_short),
            shops=words.count("meta.shops", stats.stock_shops), price=money(stats.cheapest.delivered_price),
            **described,
        )
    elif upcoming and date and stats.preorder_shops:
        keys = ("meta.set.title_upcoming", "meta.set.title_upcoming_short", "meta.set.title_upcoming_shortest")
        title = set_title(words, labels, keys, **values)
        description = words("meta.set.description_upcoming", date=date,
                            shops=words.count("meta.shops", stats.preorder_shops), **described)
    elif upcoming and date:
        # Still to come and no shop takes pre-orders yet: the date is all the page can promise.
        title = set_title(words, labels, ("meta.set.title_dated",), **values)
        # "Prices appear once shops list them" only when no shop lists any of them.
        key = "meta.set.description_dated_listed" if stats.listed else "meta.set.description_dated"
        description = words(key, date=date, **described)
    else:
        title = set_title(words, labels, ("meta.set.title_plain", "meta.set.title_plain_short"), **values)
        description = words("meta.set.description", **described)
    return title, fit_description(tidy_stops(description))


def intro(words, stats, date=""):
    """The paragraph under a hub's heading, as safe HTML, or "" for a hub with no products. The cheapest
    product in stock is a link to its page. ``date`` is the release date as shown, for a set still to come:
    while nothing is in stock its line gives the date and how many shops take pre-orders."""
    if not stats.products:
        return ""
    products = words.count("browse.hub.products", stats.products)
    if date and not stats.in_stock:
        if stats.preorder_shops:
            return escape(words("browse.hub.upcoming", products=products, date=date,
                                shops=words.count("browse.hub.shops", stats.preorder_shops)))
        if stats.listed:
            return escape(words("browse.hub.upcoming_listed", products=products, date=date,
                                shops=words.count("browse.hub.shops", stats.listed)))
        return escape(words("browse.hub.upcoming_none", products=products, date=date))
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
