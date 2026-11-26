"""
Ask Rip beside the page on a wide screen (the owner's brief of 26 Nov 2026: "an integrated desktop shopping assistant
rather than requiring users to leave their browsing experience", with suggestions "relevant to what the user is
browsing" and Quick insights from "existing calculated information"). What the panel adds to Ask Rip's own popup
(ask_sheet.html, ask_side.html), worked out for each page from what the page already has:

- suggestions: short questions about the list in view (its game, set and product type), each a plain link to an
  existing answer: Great value, Best for ripping, Lowest in 90 days, the Deals page. A tap costs Rip nothing; no
  question is sent to him unless typed.
- insights: the biggest saving on today's best deals after the first (which Today's top deal shows), the lowest
  price a pack among them, the set people want most, and the shops with most of today's best deals, all from the
  caches the home page's lists keep (feed.insights, compact.peek). A page never builds them: before they are built,
  the panel leaves them out.

No query, no request: the panel is drawn with the page, which the page cache keeps for visitors as it is.
"""

from django.urls import reverse

from catalogue.models import Game, Product, ProductSet
from content import service as copy

from . import compact, feed, type_pages


def kind_in(context):
    for name in ("type_chosen", "desk_kind", "product_type"):
        kind = context.get(name)
        if isinstance(kind, str) and kind in Product.Type.values:
            return kind
    return ""


def suggestions(context, store):
    """[(question, address)] about the list in view, or [] on a page that is not a list (a product's page has its own
    questions)."""
    # A list in the compact layout carries the dock (its search box and games); any other page has no list to ask about.
    if context.get("product") is not None or not (context.get("compact") or context.get("dock")):
        return []
    game = context.get("game")
    game = game if isinstance(game, Game) else None
    product_set = context.get("product_set")
    product_set = product_set if isinstance(product_set, ProductSet) else None
    kind = kind_in(context)
    if product_set is not None:
        base, joiner = product_set.get_absolute_url() + (f"?type={kind}" if kind else ""), "&" if kind else "?"
    elif game is not None and kind:
        url = type_pages.url_for(game, kind)
        base, joiner = (url, "?") if url else (f"{reverse('web:search')}?game={game.slug}&type={kind}", "&")
    elif game is not None:
        base, joiner = f"{reverse('web:search')}?game={game.slug}", "&"
    else:
        base, joiner = reverse("web:search"), "?"
    if kind and game is not None:
        things = type_pages.plural(game.slug, kind)
    elif game is not None:
        things = copy.get("askrip.side.game_things", store=store, game=game.display_short)
    else:
        things = copy.get("askrip.side.things", store=store)
    found = [
        (copy.get("askrip.side.value", store=store, things=things), f"{base}{joiner}great=on"),
        (copy.get("askrip.side.card", store=store, things=things), f"{base}{joiner}rip=on"),
    ]
    if product_set is None:
        found.append((copy.get("askrip.side.low", store=store, things=things), f"{base}{joiner}sort=low"))
    # The Deals page is every game's (it reads no game), so it is linked as it is: one page, one cache entry.
    found.append((copy.get("askrip.side.drops", store=store), reverse("web:deals")))
    return found


def trending_set(game):
    """The set people want most now (compact.wanted_sets, from its cache alone), for ``game`` or any, or None."""
    wanted = compact.peek("wanted-sets")
    if not wanted:
        return None
    rows = wanted["games"].get(game.pk, []) if game is not None else wanted["all"]
    return rows[0] if rows else None


def panel(context):
    """What ask_side.html draws: {"suggestions", "saving", "pack", "set", "shops"}."""
    request = context.get("request")
    store = copy.for_request(request) if request is not None else None
    game = context.get("game")
    game = game if isinstance(game, Game) else None
    found = feed.insights(game)
    return {
        "suggestions": suggestions(context, store),
        "saving": found.get("saving"),
        "pack": found.get("pack"),
        "shops": found.get("shops") or [],
        "set": trending_set(game),
    }
