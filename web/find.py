"""
Find with Rip: the guided search, one question a screen, for a visitor who does not know where to start. The owner,
5 Nov 2026: "a little bit of a hand-holding ... what are you looking for? Pokemon. What kind? Booster packs. Which
series? What matters most? ... and then it just takes them to that list".

Four steps, each a page of plain links (so it works without script, and Back works): the game, the product type in
stock for it, the newest sets with that type in stock (or any set), and what matters most, which is the list's sort.
The answers ride in the address (?game=&type=&set=&want=); with all four the view sends the visitor to the game's
or set's page, filtered and sorted, which opens on what is in stock, best first.
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.urls import reverse

from catalogue.models import Product, ProductSet
from catalogue.types import type_label

from . import feed

SETS_SHOWN = 8
# What matters most, and the sort the list opens with (catalogue/ordering.py SORTS).
WANTS = [("price", "price"), ("fair", "fair"), ("saving", "saving"), ("newest", "newest")]
WANT_ICONS = {"price": "deals", "fair": "shop", "saving": "trend-down", "newest": "drops"}
STEPS = ("game", "type", "set", "want")


@dataclass
class Option:
    label: str
    url: str
    icon: str = ""         # a symbol in the feed's sprite (rr-game-*, rr-*)
    product: object = None  # a product whose picture stands for the option
    note: str = ""
    quiet: bool = False    # the "any" option, drawn lighter


@dataclass
class Step:
    key: str
    question: str
    sub: str
    options: list = field(default_factory=list)
    chosen: list = field(default_factory=list)   # [(label, url to change it)]
    number: int = 1


def link(**chosen):
    params = {key: value for key, value in chosen.items() if value}
    return reverse("web:find") + ("?" + urlencode(params) if params else "")


def destination(game, kind, product_set, want):
    """The list the four answers lead to: the set's page or the game's, filtered to the type and sorted."""
    base = product_set.get_absolute_url() if product_set is not None else game.get_absolute_url()
    params = {}
    if kind:
        params["type"] = kind
    sort = dict(WANTS).get(want)
    if sort and sort != "newest":
        params["sort"] = sort
    return base + ("?" + urlencode(params) if params else "")


def answers(request):
    """(game, kind, product_set, set_asked, want) from the address: a game by its slug, a type the catalogue has, a set
    of that game ("any" for none in particular), a want the page offers. Anything else is unanswered."""
    games = {game.slug: game for game in feed.games_with_products()}
    game = games.get(request.GET.get("game", ""))
    kind = request.GET.get("type", "")
    if kind != "any" and kind not in Product.Type.values:
        kind = ""
    set_asked = request.GET.get("set", "") if game is not None else ""
    product_set = None
    if game is not None and set_asked and set_asked != "any":
        product_set = ProductSet.objects.filter(game=game, slug=set_asked).first()
        if product_set is None:
            set_asked = ""
    want = request.GET.get("want", "")
    if want not in dict(WANTS):
        want = ""
    return list(games.values()), game, kind, product_set, set_asked, want


def in_stock(game, kind=""):
    products = Product.objects.active().in_stock().filter(game=game)
    return products.filter(product_type=kind) if kind and kind != "any" else products


def step(request, text):
    """The step to show, or the address to send the visitor to once every question is answered. ``text`` is
    views.text, for the wording."""
    games, game, kind, product_set, set_asked, want = answers(request)
    if game is not None and kind and set_asked and want:
        return destination(game, "" if kind == "any" else kind, product_set, want)
    chosen = []
    if game is not None:
        chosen.append((game.display_short, link()))
    if game is not None and kind:
        chosen.append((text("find.type.any") if kind == "any" else type_label(game.slug, kind), link(game=game.slug)))
    if game is not None and kind and set_asked:
        chosen.append((product_set.name if product_set else text("find.set.any"), link(game=game.slug, type=kind)))
    if game is None:
        found = Step("game", text("find.game.question"), text("find.game.sub"), number=1)
        found.options = [Option(item.display_short, link(game=item.slug), icon=f"game-{feed.icon_for(item.slug)}")
                         for item in games]
    elif not kind:
        found = Step("type", text("find.type.question", game=game.display_short), "", number=2)
        picks = feed.tile_picks()["games"].get(game.pk, [])
        products = Product.objects.active().in_bulk([pk for _kind, pk, _n in picks]) if picks else {}
        for code, pk, count in picks:
            found.options.append(Option(feed.TYPE_LABELS.get(code) or type_label(game.slug, code),
                                        link(game=game.slug, type=code), product=products.get(pk),
                                        note=text("find.in_stock", count=count)))
        found.options.append(Option(text("find.type.any"), link(game=game.slug, type="any"), quiet=True))
    elif not set_asked:
        found = Step("set", text("find.set.question"), text("find.set.sub"), number=3)
        sets = (ProductSet.objects.filter(game=game, pk__in=in_stock(game, kind).values("product_set_id"))
                .order_by(*ProductSet._meta.ordering)[:SETS_SHOWN])
        for item in sets:
            note = f"{item.release_date:%b %Y}" if item.release_date else ""
            found.options.append(Option(item.name, link(game=game.slug, type=kind, set=item.slug), note=note))
        found.options.append(Option(text("find.set.any"), link(game=game.slug, type=kind, set="any"), quiet=True))
    else:
        found = Step("want", text("find.want.question"), text("find.want.sub"), number=4)
        for key, _sort in WANTS:
            found.options.append(Option(text(f"find.want.{key}"), link(game=game.slug, type=kind, set=set_asked, want=key),
                                        icon=WANT_ICONS[key]))
    found.chosen = chosen
    return found
