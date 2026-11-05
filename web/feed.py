"""
The feed: the new home page, and the top of each game's page, while the owner's switch is on (web/look.py).

The marketing team's design of 4 Nov 2026 (site_redesign/README.md, "a Google Shopping style product feed with a game
bar at the bottom"), top to bottom: a title (Today's best prices, or the game's name), product type tiles each pictured
by a product of its kind in stock, a rail of product cards (Furthest under RRP, or Best prices today when fewer than
RAIL_MIN_RRP products are under their RRP), the Rip's Deals rail the usual home page has (the shops that pay a
commission first, as its disclosure says), the next drop with its pre-order price, and, fixed to the foot of a phone,
the search box (by voice too, where the browser can) and a bar of the games. The rails carry hidden spares so the
visitor's own games come first, worked out in the browser by interest.js as on the usual home page. Every price is a real delivered price from the site's own offers,
and a saving is only claimed where the data backs it: the product's RRP, or the next shop's confirmed delivered price,
with the same rules every list uses (catalogue/offers.summarise).

What reads every product is cached like the home page's other lists (web.listcache, built again by web.warming): the
rails in one entry for every game at once, the tile pictures hourly. A page then costs a handful of queries.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Count, Q
from django.urls import reverse

from catalogue import offers
from catalogue.models import FeaturedSettings, Game, Listing, Product
from content import service as copy

from . import listcache

RAILS_KEY = "web:feed-rails:v1"
TILES_KEY = "web:feed-tiles:v1"
TILES_SECONDS = 60 * 60
RAIL_SIZE = 12         # cards shown on a rail
RAIL_SPARES = 12       # more behind the home page's rails, hidden, for interest.js to bring the visitor's games forward
RAIL_MIN_RRP = 4       # under this many products under RRP, the rail is Best prices today instead
TILES_SHOWN = 8
# The game bar's order: All first, then these by name, then the rest by size. Each game's icon in the sprite
# (web/templates/web/includes/feed_sprite.svg) is matched on its slug, so a renamed game keeps its icon.
BAR_ORDER = ("pokemon", "magic", "one-piece", "lorcana", "yu-gi-oh", "sports", "star-wars", "riftbound", "digimon",
             "dragon-ball", "flesh", "union-arena", "vanguard", "weiss")
ICONS = {"pokemon": "pokemon", "magic": "magic", "one-piece": "one-piece", "lorcana": "lorcana", "yu-gi-oh": "yugioh",
         "sports": "sports", "star-wars": "star-wars", "riftbound": "riftbound", "digimon": "digimon",
         "dragon-ball": "dragon-ball", "flesh": "flesh-and-blood", "union-arena": "union-arena", "vanguard": "vanguard",
         "weiss": "weiss-schwarz"}
# The tiles' short labels, one a product type (a data label, so in code, as the stock states are).
TYPE_LABELS = {
    "booster_box": "Booster boxes", "booster_pack": "Booster packs", "elite_trainer_box": "ETBs", "bundle": "Bundles",
    "collection_box": "Collections", "deck": "Decks", "tin": "Tins", "gift_set": "Gift sets",
    "collector_booster_box": "Collector boxes", "collector_booster_pack": "Collector packs",
}


@dataclass
class Tile:
    code: str
    label: str
    product: object
    url: str
    current: bool = False   # the type the list under the tiles is filtered to; its link clears the filter


@dataclass
class Card:
    product: object
    summary: object
    amount: Decimal   # how far under RRP, or the saving on the next shop


# The tiles ------------------------------------------------------------------------------------------------------

def tile_picks(fresh=False):
    """{"all": [(type, product pk, in stock)], "games": {game id: [...]}}, most stocked types first: for each game and
    across them, the product in stock of each type with a picture that the most shops sell, the cheapest of those.
    A product many shops stock is a mainstream one of its kind; the cheapest alone was a mis-typed accessory (a card
    display frame as the Booster boxes tile, 4 Nov 2026). One query over every product in stock, at most hourly,
    like the home page's type covers."""
    picks = None if fresh else cache.get(TILES_KEY)
    if picks is None:
        rows = (Product.objects.active().with_prices().filter(in_stock_count__gte=1)
                .exclude(product_type=Product.Type.OTHER).exclude(image="", image_url="")
                .order_by().values_list("game_id", "product_type", "pk", "lowest_known", "in_stock_count"))
        counts, best = {}, {}
        for game_id, kind, pk, price, shops in rows:
            order = (-shops, price is None, price or 0, pk)   # most shops; then a confirmed delivered price, cheapest
            for scope in (game_id, None):
                counts[(scope, kind)] = counts.get((scope, kind), 0) + 1
                if (scope, kind) not in best or order < best[(scope, kind)][0]:
                    best[(scope, kind)] = (order, pk)
        games = {}
        for (scope, kind), (_order, pk) in best.items():
            games.setdefault(scope, []).append((kind, pk, counts[(scope, kind)]))
        for found in games.values():
            found.sort(key=lambda row: (-row[2], row[0]))
        picks = {"all": games.pop(None, []), "games": games}
        cache.set(TILES_KEY, picks, TILES_SECONDS)
    return picks


def tiles(game=None, current=""):
    """The product type tiles for ``game`` (or every game): TILES_SHOWN at most, each pictured by a real product and
    opening the list of that type. With ``current`` (the type the list is filtered to) that tile is marked and leads
    back to the unfiltered page, so a second tap clears the filter. One query for the products."""
    picks = tile_picks()
    rows = picks["games"].get(game.pk, []) if game is not None else picks["all"]
    rows = [row for row in rows if row[0] in TYPE_LABELS][:TILES_SHOWN]
    products = Product.objects.active().in_bulk([pk for _kind, pk, _n in rows]) if rows else {}
    base = game.get_absolute_url() if game is not None else reverse("web:search")
    clear = game.get_absolute_url() if game is not None else reverse("web:home")
    found = []
    for kind, pk, _n in rows:
        product = products.get(pk)
        if product is None:
            continue
        chosen = kind == current
        found.append(Tile(kind, TYPE_LABELS[kind], product, clear if chosen else f"{base}?type={kind}", chosen))
    return found


# The rails -------------------------------------------------------------------------------------------------------

def rrp_map(products):
    """{product pk: the RRP the Price check measures against (Product.rrp_now)}, in one query."""
    rows = Product.objects.filter(pk__in=[product.pk for product in products]).values_list(
        "pk", "rrp", "shop_rrp", "found_rrp")
    return {pk: rrp or shop_rrp or found_rrp for pk, rrp, shop_rrp, found_rrp in rows
            if rrp or shop_rrp or found_rrp}


def rails(fresh=False, candidates=None):
    """{game id or None: ("rrp" or "save", [Card])}: each game's rail and the home page's, cached together. ``fresh``
    and ``candidates`` as for the home page's lists (web.views.home_lists)."""
    from web import views

    return listcache.get_or_build(RAILS_KEY, lambda: build_rails(candidates), fresh=fresh,
                                  since=views.read_since(candidates))


def build_rails(candidates=None):
    from web import views

    products = (candidates or views.deal_candidates)()
    week_lows = views.lows_for(candidates, products)
    rrps = rrp_map(products)
    under, saves = [], []
    for product in products:
        summary = offers.summarise(product, week_lows)
        best = summary.best
        if (best is None or best.availability != Listing.Availability.IN_STOCK or not best.delivery_known
                or summary.suspect):
            continue
        rrp = rrps.get(product.pk)
        if rrp and best.delivered_price < rrp:
            under.append((rrp - best.delivered_price, product, summary))
        if summary.saving:
            saves.append((summary.saving, product, summary))
    scopes = [None, *sorted({product.game_id for product in products})]
    chosen = {}
    for scope in scopes:
        # The home page's rail carries spares behind the shown cards (interest.js); a game's page needs none.
        size = RAIL_SIZE + RAIL_SPARES if scope is None else RAIL_SIZE
        mine = [row for row in under if scope is None or row[1].game_id == scope]
        mine.sort(key=lambda row: (-row[0], row[1].pk))
        if len(mine) >= RAIL_MIN_RRP:
            chosen[scope] = ("rrp", mine[:size])
        else:
            mine = [row for row in saves if scope is None or row[1].game_id == scope]
            mine.sort(key=lambda row: (-row[0], row[1].pk))
            chosen[scope] = ("save", mine[:size])
    # The products as a list shows them (game, set, prices), in one query for every rail.
    groups = views.with_list_fields(*([(product, summary) for _amount, product, summary in rows]
                                      for _kind, rows in chosen.values()))
    result = {}
    for (scope, (kind, rows)), group in zip(chosen.items(), groups):
        amounts = {product.pk: amount for amount, product, _summary in rows}
        result[scope] = (kind, [Card(product, summary, amounts[product.pk]) for product, summary in group])
    return result


def rail(game=None):
    """(kind, [Card]) for ``game``'s rail or the home page's, from the cache: RAIL_SIZE to show, then the spares."""
    return rails().get(game.pk if game is not None else None, ("save", []))


def deals(game=None, shown=()):
    """(rows, spares) for the Rip's Deals rail: the home page's featured deals (web.views.featured_pick, which
    favours the shops that pay a commission, or takes only them with the owner's Featured deals setting) and the
    spares behind them, for ``game`` or any, less the products ``shown`` on the rail above. From the cache."""
    from web import views

    featured, _trending, spares = views.home_lists()
    keep = lambda product: product.pk not in shown and (game is None or product.game_id == game.pk)   # noqa: E731
    return ([row for row in featured if keep(row[0])], [row for row in spares if keep(row[0])])


# The drop and the bar ---------------------------------------------------------------------------------------------

def drop(game=None):
    """The next set a visitor can pre-order now, for ``game`` or any: the first of the cached Coming soon rows with a
    pre-order price, or None."""
    from web import views

    rows = views.coming_soon_sets(game, limit=None) if game is not None else views.home_coming_soon()
    return next((row for row in rows if row.preorder_count and row.best_id), None)


def icon_for(slug):
    """The sprite's icon for a game, matched on its slug; a game the sprite has no motif for shows the four tiles."""
    return next((icon for key, icon in ICONS.items() if key in slug), "all")


def games_with_products():
    """Every game with products, in the bar's order: BAR_ORDER by name, then the rest by size. One query."""
    found = list(
        Game.objects.filter(is_active=True)
        .annotate(product_count=Count("products", filter=Q(products__is_active=True)))
        .filter(product_count__gt=0)
    )

    def rank(game):
        place = next((i for i, key in enumerate(BAR_ORDER) if key in game.slug), len(BAR_ORDER))
        return (place, -game.product_count, game.name)

    return sorted(found, key=rank)


def games_bar(games=None):
    """The bar's items for ``games`` (games_with_products): All is the template's."""
    if games is None:
        games = games_with_products()
    return [{"slug": game.slug, "label": game.display_short, "icon": icon_for(game.slug),
             "url": game.get_absolute_url()} for game in games]


def context(request, game=None, kind=""):
    """Everything the feed's templates show for the home page (``game`` None), a game's page, or, with ``kind`` a
    product type, the list filtered to that type: then only the tiles show, the chosen one marked and the row stuck
    to the top, and the list sits right under them with the type as its heading, so the feed's title is left out."""
    from web import views

    store = copy.for_request(request)
    filtered = kind in TYPE_LABELS
    if filtered:
        rail_kind, shown, spares, deal_rows, deal_spares = "save", [], [], [], []
    else:
        rail_kind, cards = rail(game)
        shown, spares = cards[:RAIL_SIZE], cards[RAIL_SIZE:]
        deal_rows, deal_spares = deals(game, {card.product.pk for card in cards})
    games = games_with_products()
    if game is not None:
        title = game.name
        rail_url = f"{reverse('web:deals')}?game={game.slug}"
        search_hint = copy.get("feed.search.placeholder_game", store=store, game=game.display_short)
    else:
        title = copy.get("feed.title", store=store)
        rail_url = reverse("web:deals")
        search_hint = copy.get("feed.search.placeholder", store=store)
    return {
        "game": game, "show_game": game is None, "title": title, "show_title": not filtered,
        # Filtered to a type: the list's heading is the tile's label, and Clear leads back to the page unfiltered.
        "filtered": filtered, "kind": kind if filtered else "", "list_title": TYPE_LABELS.get(kind, ""),
        # The two quiet hints a first visit gets (feed.js): only the home page, unfiltered, has them in its markup.
        "hints": game is None and not filtered,
        "clear_url": game.get_absolute_url() if game is not None else reverse("web:home"),
        "tiles": tiles(game, current=kind), "rail_kind": rail_kind, "rail": shown, "rail_spares": spares,
        "rail_title": copy.get("feed.rail.rrp" if rail_kind == "rrp" else "feed.rail.best", store=store),
        "rail_url": rail_url, "deals": deal_rows, "deals_spares": deal_spares,
        "deals_paying_only": FeaturedSettings.current().paying_only if deal_rows else False,
        "drop": None if filtered else drop(game), "games": games_bar(games),
        "interest_games": views.interest_lexicon(games), "search_hint": search_hint, "today": date.today(),
    }
