"""
The compact layout: a short list at every step, quick to read and quick to build. The owner, 9 Nov 2026, after using
a simpler site: "the layout and speed of the site just makes sense ... Can we retain the bottom product search bar we
have, but can we emulate their layout as much as possible please do this a version I can turn on and off in admin".

* The home page: one headline with the numbers (prices in stock and the shops they are at, and when they were last
  checked), today's deals in a strip, then the sets people want now.
* A game's page: the game's numbers, its deals strip, then every set it has, newest first, each with the lowest price
  a pack.
* A set's page: the set's numbers, its types as chips, then each product as one row: picture, name, cheapest shop,
  delivered price, how many shops and the price a pack; in stock first, then pre-orders, then the rest.
* Search and the other lists draw their products as the same rows (web/templates/web/includes/compact_row.html).

The search box and the games stay fixed to the foot of a phone (feed_dock.html). On while the owner's switch is
(catalogue.SiteLook.compact, web/look.py); the other looks are untouched. RipRaptor's own colours, words and prices
throughout: only the layout is borrowed.
"""

import re

from django.core.cache import cache
from django.db.models import Count, Max
from django.shortcuts import render
from django.urls import reverse

from catalogue import fair_price, offers, pictures, pricing, popularity
from catalogue.contents import cards_total, price_a_card, price_a_pack
from catalogue.models import Listing, Product, ProductSet
from catalogue.types import type_choices
from content import service as copy

from . import feed, navigation, pagecache

STRIP = 10          # cards in the deals strip
POPULAR = 24        # sets on the home page
DESK_SETS = 12      # sets in each game's tab on a desktop screen
DESK_GAMES = 5      # games with a tab of their own beside All
SET_ROW = 12        # sets in a desktop screen's row of sets (compact_set_row.html), newest first
# The product that pictures a set: a booster box first, the way a set is known, then the boxes and bundles.
COVER_ORDER = ("booster_box", "elite_trainer_box", "bundle", "collection_box", "booster_pack", "tin", "deck")
# A product this many shops list is pictured by a shop people buy from; one or two may be a single shop's own photo.
COVER_SHOPS = 3


def cover_rank(pk, name, kind, listed, image, image_url):
    """Where a product stands as its set's picture, least first: one with a picture; the owner's own upload; not one
    named as a case, carton or counter display (pictures.carton_named); one COVER_SHOPS shops or more list; the kind of
    product (COVER_ORDER, a booster box first); the most shops; the oldest."""
    return (not (image or image_url), not image, pictures.carton_named(name), (listed or 0) < COVER_SHOPS,
            COVER_ORDER.index(kind) if kind in COVER_ORDER else len(COVER_ORDER), -(listed or 0), pk)


def kept(key, build, fresh=False):
    """``build()`` kept in the shared cache, so the compact pages read their numbers and set lists once every few
    minutes. warm_pages builds them again (``fresh``) every few minutes, inside the twice a page's time they are kept
    for, so a visitor never waits for one. Built every time while pages are not kept (the tests)."""
    seconds = pagecache.seconds()
    if seconds <= 0:
        return build()
    key = pagecache.key_for(f"compact/{key}")
    found = None if fresh else cache.get(key)
    if found is None:
        found = build()
        cache.set(key, found, seconds * 2)
    return found


def peek(key):
    """What ``kept`` holds under ``key``, or None, never building it: for a part every page carries (Ask Rip's Quick
    insights), which must not add a query to a page."""
    if pagecache.seconds() <= 0:
        return None
    return cache.get(pagecache.key_for(f"compact/{key}"))


def numbers(fresh=False):
    """{"offers": shop prices in stock now, "shops": shops compared (pricing.shops_compared), "checked": the latest
    check}. Marketplaces left out, as the shop count leaves them. One query when due."""
    def build():
        current = (Listing.objects.current().filter(availability=Listing.Availability.IN_STOCK)
                   .exclude(sanity=Listing.Sanity.EXCLUDED).exclude(retailer__source_type__in=pricing.MARKETPLACES))
        found = current.order_by().aggregate(n=Count("pk"), last=Max("last_checked"))
        offers_now = found["n"] or 0
        return {"offers": offers_now, "offers_text": f"{offers_now:,}", "checked": found["last"],
                "shops": pricing.shops_compared()}

    return kept("numbers", build, fresh)


def pack_price(price, product_type, packs):
    """What one pack costs at ``price``: a booster pack's own price, else the price shared over the packs it holds."""
    if price is None:
        return None
    if product_type == Product.Type.BOOSTER_PACK and (packs or 1) <= 1:
        return price
    return price_a_pack(price, packs)


def set_rows(game=None):
    """[dict] for every set with products in ``game`` (or every game): the set, its cover product's pk, how many
    products, in stock and on pre-order, the cheapest confirmed delivered price (in stock first, else a pre-order's)
    and the cheapest a pack. Two queries, however many sets: the products with their prices, then the sets."""
    products = Product.objects.active().exclude(product_set=None)
    products = products.filter(game=game) if game is not None else products.filter(game__is_active=True)
    rows = (products.with_prices().order_by()
            .values_list("pk", "product_set_id", "product_type", "lowest_known", "in_stock_count", "preorder_count",
                         "listing_count", "packs", "packs_found", "image", "image_url", "name"))
    found = {}
    for pk, set_id, kind, price, stocked, preordered, listed, owner_packs, shop_packs, image, image_url, name in rows:
        packs = owner_packs or shop_packs   # Product.packs_inside
        row = found.setdefault(set_id, {"products": 0, "in_stock": 0, "preorder": 0, "from": None, "from_preorder": None,
                                        "pack": None, "cover": None, "cover_rank": None})
        row["products"] += 1
        if stocked:
            row["in_stock"] += 1
            if price is not None and (row["from"] is None or price < row["from"]):
                row["from"] = price
        elif preordered:
            row["preorder"] += 1
            if price is not None and (row["from_preorder"] is None or price < row["from_preorder"]):
                row["from_preorder"] = price
        each = pack_price(price, kind, packs) if stocked else None
        if each is not None and (row["pack"] is None or each < row["pack"]):
            row["pack"] = each
        rank = cover_rank(pk, name, kind, listed, image, image_url)
        if row["cover_rank"] is None or rank < row["cover_rank"]:
            row["cover"], row["cover_rank"] = pk, rank
    sets = ProductSet.objects.filter(pk__in=found).select_related("game")
    out = []
    for product_set in sets:
        row = found[product_set.pk]
        row.pop("cover_rank")
        out.append({"set": product_set, **row})
    return out


def with_covers(rows):
    """``rows`` (set_rows) with each one's cover product, for its picture. One query."""
    covers = Product.objects.in_bulk([row["cover"] for row in rows if row["cover"]])
    return [{**row, "cover": covers.get(row["cover"])} for row in rows]


def newest_first(rows):
    rows = sorted(rows, key=lambda row: row["set"].name)
    rows.sort(key=lambda row: -(row["in_stock"] + row["preorder"]))
    return sorted(rows, key=lambda row: (row["set"].release_date is None, -(row["set"].release_date.toordinal()
                                                                           if row["set"].release_date else 0)))


def game_sets(game, fresh=False):
    """The game's sets for its page, newest first, cached (kept)."""
    return kept(f"sets/{game.pk}", lambda: newest_first(set_rows(game)), fresh)


def set_row(game, current=None, kind="", rows=None):
    """Step two of a desktop screen's three (game, set, type: compact_set_row.html): the game's newest sets, each with
    its picture and a link to its page that keeps the type in view, the one in view marked, after All sets, which goes
    back to the game's page (or the type's). The owner, 26 Nov 2026: "Game, Set or collection, Product type, Products
    ... consistent across relevant desktop browsing pages". ``rows``: the game's sets with their covers when the page
    has them already (its own list); else the cached list and one query for the pictures. None without a game."""
    from . import type_pages

    if game is None:
        return None
    if rows is None:
        found = game_sets(game)
        rows = found[:SET_ROW]
        if current is not None and all(row["set"].pk != current.pk for row in rows):
            rows = [row for row in found if row["set"].pk == current.pk] + rows[:SET_ROW - 1]
        rows = with_covers(rows)
    else:
        rows = rows[:SET_ROW]
    keep = f"?type={kind}" if kind else ""
    all_url = (type_pages.url_for(game, kind) or f"{game.get_absolute_url()}?type={kind}") if kind else game.get_absolute_url()
    items = [{"set": row["set"], "cover": row["cover"], "url": row["set"].get_absolute_url() + keep,
              "current": current is not None and row["set"].pk == current.pk} for row in rows]
    return {"items": items, "all_url": all_url, "all_current": current is None} if items else None


def game_type_tiles(request, game, all_url):
    """Step three on a game's own page on a desktop screen: its product types as the small tiles, each a link to that
    type's page (web/type_pages.py) and All to every product of the game. One query for the pictures."""
    from . import type_pages

    store = copy.for_request(request)
    rows = in_game_order(game, feed.tile_picks()["games"].get(game.pk, []))
    rows = [row for row in rows if row[0] in feed.TYPE_LABELS][:feed.TILES_SHOWN]
    products = Product.objects.active().in_bulk([pk for _kind, pk, _n in rows]) if rows else {}
    tiles = [feed.Tile(feed.ALL, copy.get("compact.types.all", store=store), None, all_url, False)]
    for kind, pk, _n in rows:
        product = products.get(pk)
        if product is not None:
            url = type_pages.url_for(game, kind) or f"{all_url}&type={kind}"
            tiles.append(feed.Tile(kind, feed.TYPE_LABELS[kind], product, url, False))
    return tiles


def popular_sets(limit=POPULAR, fresh=False):
    """The sets people want now across every game (popularity.set_demand, then the newest), only those with
    something to buy, for the home page. Cached (kept), with each game's own (sets_by_game)."""
    return wanted_sets(fresh)["all"][:limit]


def sets_by_game(fresh=False):
    """{game pk: the DESK_SETS sets of that game people want now}, in the same order as popular_sets: the home page's
    tabs on a desktop screen. Cached with them."""
    return wanted_sets(fresh)["games"]


def wanted_sets(fresh=False):
    """{"all": the POPULAR sets people want now, "games": {game pk: its DESK_SETS}}. Built together, kept together:
    two queries for the sets and one for their demand when due."""
    def build():
        rows = [row for row in set_rows() if row["in_stock"] or row["preorder"]]
        wanted = popularity.set_demand([row["set"].pk for row in rows])
        rows = newest_first(rows)
        rows.sort(key=lambda row: -wanted.get(row["set"].pk, 0))
        games = {}
        for row in rows:
            kept_rows = games.setdefault(row["set"].game_id, [])
            if len(kept_rows) < DESK_SETS:
                kept_rows.append(row)
        return {"all": rows[:POPULAR], "games": games}

    return kept("wanted-sets", build, fresh)


def with_covers_all(groups):
    """Each list of ``groups`` (set_rows lists) with its rows' cover products, for their pictures, in one query."""
    pks = {row["cover"] for rows in groups for row in rows if row["cover"]}
    covers = Product.objects.in_bulk(pks)
    return [[{**row, "cover": covers.get(row["cover"])} for row in rows] for rows in groups]


def dock(request, game=None, hints="", games=None):
    """What the dock at the foot (feed_dock.html) needs: the search box's words and the bar of games, and on the home
    page the hint by the games (feed.js), which lights them one by one on each landing (the owner, 9 Nov 2026: "just
    want a subtle way of showing someone what to do")."""
    store = copy.for_request(request)
    hint = (copy.get("feed.search.placeholder_game", store=store, game=game.display_short) if game is not None
            else copy.get("feed.search.placeholder", store=store))
    games = feed.games_with_products() if games is None else games
    if not navigation.games():
        navigation.remember(games)   # the header's Games menu, read from the cache on every page
    return {"game": game, "games": feed.games_bar(games), "more": len(games) > feed.DESK_BAR, "search_hint": hint,
            "hints": hints}


def strip(game=None):
    """Today's best deals for the strip: the feed's rail for ``game`` (or every game), from its cache."""
    return feed.rail(game)[:STRIP]


WORD = re.compile(r"[a-z0-9]+")


def words_with_spans(text):
    """[(word, start, end)]: the plain lowercase words of ``text`` and where each sits in it."""
    found = []
    for match in WORD.finditer(text.lower().replace("é", "e").replace("&", " and ")):
        found.append((match.group(0), match.start(), match.end()))
    return found


def short_name(product):
    """A product's name in its set's list, the set's own name (and anything before it) taken off when the name holds
    it, however it is punctuated: "Pokémon TCG: Mega Evolution Delta Reign Elite Trainer Box" is "Elite Trainer Box"
    under "Mega Evolution - Delta Reign", as under "Mega Evolution—Delta Reign". The set's last words alone count too
    ("Delta Reign"). The full name when that would leave too little."""
    name = product.name
    product_set = product.product_set
    if product_set is None:
        return name
    tokens = words_with_spans(name)
    plain = [word for word, _start, _end in tokens]
    tried = [product_set.name] + [part for sep in ("—", "-", ":") for part in product_set.name.split(sep)[1:]]
    for words in tried:
        wanted = [word for word, _start, _end in words_with_spans(words)]
        if not wanted or (len(wanted) == 1 and len(wanted[0]) < 3):
            continue
        for at in range(len(plain) - len(wanted) + 1):
            if plain[at:at + len(wanted)] == wanted:
                rest = name[tokens[at + len(wanted) - 1][2]:].strip(" -–—:,")
                if len(rest) >= 3:
                    return rest
                break
    return name


def type_tiles(request, rows, chosen, all_label, base=""):
    """The product types above a list as the feed's small tiles (feed.Tile): All first, then each type in ``rows``
    ([(type, product pk, count)], feed.tile_picks' or feed.set_tile_rows') pictured by that product, each a link to
    this page with the type chosen (the chosen one, marked, leads back to All), keeping the other choices. One query
    for the products."""
    rows = [row for row in rows if row[0] in feed.TYPE_LABELS][:feed.TILES_SHOWN]
    products = Product.objects.active().in_bulk([pk for _kind, pk, _n in rows]) if rows else {}

    def link(kind):
        params = request.GET.copy()
        for name in ("page", "pane", "tab", "type"):
            params.pop(name, None)
        if kind:
            params["type"] = kind
        return "?" + params.urlencode() if params else (base or request.path)

    tiles = [feed.Tile(feed.ALL, all_label, None, link(""), not chosen)]
    for kind, pk, _n in rows:
        product = products.get(pk)
        if product is None:
            continue
        current = kind == chosen
        tiles.append(feed.Tile(kind, feed.TYPE_LABELS[kind], product, link("" if current else kind), current))
    return tiles


def in_game_order(game, rows):
    """``rows`` ([(type, ...)]) in the game's own order of types (catalogue.types), as its players list them."""
    order = [code for code, _label in type_choices(game.slug)]
    return sorted(rows, key=lambda row: order.index(row[0]) if row[0] in order else len(order))


def list_tiles(request, game, chosen):
    """type_tiles for a list page (views._browse): the types of ``game``, or of every game."""
    picks = feed.tile_picks()
    rows = picks["games"].get(game.pk, []) if game is not None else picks["all"]
    return type_tiles(request, rows, chosen, copy.get("compact.types.all", store=copy.for_request(request)))


def in_stock_asked(request):
    """Whether the In stock switch is on in the address (``?in_stock=on``, as the filter form sends it)."""
    return bool(request.GET.get("in_stock"))


def rip_asked(request):
    """Whether the Best for ripping switch is on in the address (``?rip=on``)."""
    return bool(request.GET.get("rip"))


def value_asked(request):
    """Whether the Great value switch is on in the address (``?great=on``): only the products the Price check calls
    Good price or Lowest yet."""
    return bool(request.GET.get("great"))


def for_ripping(rows):
    """``rows`` with only the products you open (their cards can be counted, catalogue/contents.py), the ones a shop
    has first and among them the cheapest a card first: the Best for ripping switch."""
    def each(row):
        product, summary = row
        best = summary.best
        return price_a_card(best.shown_price, product) if best is not None else None

    rows = [row for row in rows if cards_total(row[0])]
    return sorted(rows, key=lambda row: (row[1].best is None, each(row) is None, each(row) or 0, row[0].name))


def product_rows(products):
    """[(product, summary)] in a set's order: in stock first, then pre-orders, then the rest; within each the cheapest
    a pack first where that is known, then the cheapest. ``products`` carry for_lists' prices."""
    products = list(products)
    week_lows = offers.week_low_map([product.pk for product in products])
    rows = [(product, offers.summarise(product, week_lows)) for product in products]

    def order(row):
        product, summary = row
        best = summary.best
        state = 2 if best is None else (1 if best.availability == Listing.Availability.PREORDER else 0)
        price = best.shown_price if best is not None else None
        each = pack_price(price, product.product_type, product.packs_inside) if price is not None else None
        return (state, each is None, each or 0, price is None, price or 0, product.name)

    return sorted(rows, key=order)


def home(request, extra):
    """The home page in the compact layout. ``extra``: the page's meta, partners and structured data. A desktop screen
    shows the same page laid out for it (desk), with a short intro, the deals as a grid and the sets in a tab for
    each game."""
    games = feed.games_with_products()
    by_game = sets_by_game()
    tab_games = [game for game in games if by_game.get(game.pk)][:DESK_GAMES]
    groups = with_covers_all([popular_sets(), *[by_game[game.pk] for game in tab_games]])
    return render(request, "web/compact/home.html", {
        **extra,
        "numbers": numbers(),
        "strip": strip(),
        "sets": groups[0],
        "dock": dock(request, hints="home", games=games),
        "desk": desk(tab_games, groups[1:]),
    })


def desk(tab_games, tab_sets):
    """What the home page shows only on a desktop screen: a tab of sets for each of ``tab_games``."""
    return {
        "tabs": [{"slug": game.slug, "label": game.display_short, "url": game.get_absolute_url(), "sets": rows}
                 for game, rows in zip(tab_games, tab_sets)],
    }


def game_page(request, game, extra, answer):
    """A game's page in the compact layout: what it compares in words (web/answers.py: the line of counts, the
    cheapest of each main kind in stock, the questions), its deals strip and every set. ``extra``: its meta.
    ``answer``: {"words", "stats", "facts", "crumbs", "page_url"} from the view, the figures the words quote. Its
    structured data names the products the page shows (ItemList) and gives the questions again (FAQPage)."""
    from . import answers, hubs
    from .views import ld_json

    sets = with_covers(game_sets(game))
    deals = strip(game)
    all_url = f"{reverse('web:search')}?game={game.slug}"
    words, stats, facts, page_url = answer["words"], answer["stats"], answer["facts"], answer["page_url"]
    faq = answers.game_faq(words, game, stats, facts, len(sets))
    listed = answers.listed_products(facts["picks"], [card.product for card in deals])
    structured = [answer["crumbs"], answers.faq_json(page_url, faq)]
    if listed:
        structured.insert(1, hubs.item_list_json(request, page_url, extra["meta_title"], extra["meta_description"],
                                                 answer["crumbs"]["@id"], listed))
    return render(request, "web/compact/game.html", {
        **extra,
        "game": game,
        "numbers": numbers(),
        "strip": deals,
        "sets": sets,
        "stock_line": answers.stock_line(words, game, stats, len(sets)),
        "picks": facts["picks"],
        "faq": faq,
        "structured_json": ld_json(structured),
        "all_url": all_url,
        # A desktop screen's steps two and three (compact_set_row.html, compact_tiles.html): the newest sets and the
        # game's types, from what the page has already, plus one query for the types' pictures.
        "set_row": set_row(game, rows=sets),
        "desk_tiles": game_type_tiles(request, game, all_url),
        # The light hints by the sets and the All products link on each landing (feed.js; the owner, 17 Nov 2026).
        "dock": dock(request, game, hints="game"),
    })


def set_page(request, product_set, extra, answer):
    """A set's page in the compact layout: the numbers, its types as small tiles pictured by its own products, an In
    stock switch, and its products as rows. ``extra``: its meta. ``answer``: {"intro", "crumbs", "page_url"} from the
    view. The whole set (no type, switch or other choice) opens with the intro, a sentence or two of live figures, and
    its structured data lists its products (ItemList); a narrowed list keeps the counts of what it shows."""
    from . import hubs
    from .views import ld_json

    products = (Product.objects.for_lists().filter(product_set=product_set)
                .prefetch_related(offers.buyable_prefetch()))
    chosen = request.GET.get("type", "")
    chosen = chosen if chosen in Product.Type.values else ""
    if chosen:
        products = products.filter(product_type=chosen)
    rows = product_rows(products)
    for product, _summary in rows:
        product.compact_name = short_name(product)
    totals = {"products": 0, "in_stock": 0, "pack": None}
    for product, row_summary in rows:
        totals["products"] += 1
        best = row_summary.best
        if best is not None and best.availability == Listing.Availability.IN_STOCK:
            totals["in_stock"] += 1
            each = pack_price(best.shown_price, product.product_type, product.packs_inside)
            if each is not None and (totals["pack"] is None or each < totals["pack"]):
                totals["pack"] = each
    only_in_stock = in_stock_asked(request)
    if only_in_stock:
        rows = [(product, summary) for product, summary in rows
                if summary.best is not None and summary.best.availability == Listing.Availability.IN_STOCK]
    rip = rip_asked(request)
    if rip:
        rows = for_ripping(rows)
    value = value_asked(request)
    if value:
        rows = [(product, summary) for product, summary in rows
                if product.price_band and product.price_band >= fair_price.GOOD_PRICE]
    cover = next((product for product, _ in rows if product.image_src), None)
    store = copy.for_request(request)
    whole = not (chosen or only_in_stock or rip or value)
    structured = [answer["crumbs"]]
    if whole and rows:
        structured.append(hubs.item_list_json(request, answer["page_url"], extra["meta_title"],
                                              extra["meta_description"], answer["crumbs"]["@id"],
                                              [product for product, _summary in rows]))
    return render(request, "web/compact/set.html", {
        **extra,
        "set_intro": answer["intro"] if whole else "",
        "structured_json": ld_json(structured),
        "game": product_set.game,
        "product_set": product_set,
        "numbers": numbers(),
        "rows": rows,
        "totals": totals,
        "cover": cover,
        "tiles": type_tiles(request, in_game_order(product_set.game, feed.set_tile_rows(product_set)), chosen,
                            copy.get("compact.types.all", store=store), base=product_set.get_absolute_url()),
        "type_chosen": chosen,
        "type_keep": [("type", chosen)],
        "in_stock_on": only_in_stock,
        "stock_switch": True,
        "rip_on": rip,
        "value_on": value,
        "set_row": set_row(product_set.game, current=product_set, kind=chosen),
        "dock": dock(request, product_set.game),
    })


def warm(games):
    """Build the compact layout's numbers and set lists again, for warm_pages: only while the layout is on and pages
    are kept. Returns whether it built them."""
    from . import look

    if not look.compact_on() or pagecache.seconds() <= 0:
        return False
    numbers(fresh=True)
    wanted_sets(fresh=True)
    for game in games:
        game_sets(game, fresh=True)
    return True
