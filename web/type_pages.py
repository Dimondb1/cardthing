"""
A page for each game and kind of product: /games/<game>/<kind>/, such as /games/digimon/booster-boxes/.

The owner's Search Console, 25 Nov 2026: of the searches Google named over a week, 89 of 147 impressions were "<game>
booster boxes" or "<game> booster packs" ("digimon booster boxes", "cardfight vanguard booster packs"), and the page
Google showed for them was the game's own, at about position 70. That page is about every kind of product, and its
list of one kind (?type=) named the game's page as its canonical address, so no page was about the thing searched for.
Now each kind with products has a page of its own: a heading, title and description in the words people search,
the line of live figures with the cheapest in stock, the list cheapest first, the game's sets that hold the kind, and
an About paragraph. It is the game's page with the kind fixed (views.game_page), in whichever look the site wears.

The address is the kind's plural in the game's own words (catalogue/types.py: "commander-decks" for Magic, "start-decks"
for Cardfight!! Vanguard), so a set with the same slug would win: set pages are looked up first (views.set_detail). The
type tiles on a game's page and its list of one kind link here; ?type= on the game's page still works and names this
page as its canonical address. The pages with PAGE_LEAST products or more are in the sitemap.
"""

from django.db.models import Count
from django.urls import reverse
from django.utils.text import slugify

from catalogue import types
from catalogue.models import Product, ProductSet

# A kind with fewer products than this has its page, but it is not offered to search engines in the sitemap.
PAGE_LEAST = 3
# Where a plural is a short form people also spell out, the address spells it out.
SLUGS = {"ETBs": "elite-trainer-boxes"}


def plural(game_slug, code):
    """The kind's plural in the game's own words, as people search it: "booster boxes", "Commander decks", "ETBs"."""
    words = types.plural_words(game_slug, [code])
    return words[0] if words else ""


def slug_for(game_slug, code):
    """The last part of the kind's address for this game ("booster-boxes"), or "" for a kind with no page."""
    if code == Product.Type.OTHER or code not in Product.Type.values:
        return ""
    words = plural(game_slug, code)
    return SLUGS.get(words) or slugify(words)


def code_for(game_slug, slug):
    """The kind whose address for this game ends ``slug``, or None."""
    for code, _label in types.type_choices(game_slug):
        if code != Product.Type.OTHER and slug_for(game_slug, code) == slug:
            return code
    for code in Product.Type.values:   # a kind the game does not list, still filed under it
        if code != Product.Type.OTHER and slug_for(game_slug, code) == slug:
            return code
    return None


def url_for(game, code):
    """The kind's page for ``game``, or "" for a kind with no page."""
    slug = slug_for(game.slug, code)
    return reverse("web:set", args=[game.slug, slug]) if slug else ""


def heading(game, code):
    """The page's heading as people search it: "Digimon booster boxes"."""
    return f"{game.display_short} {plural(game.slug, code)}"


def sitemap_pages():
    """[(game, code)] for every kind with PAGE_LEAST products or more on the site, its address not taken by a set, the
    game's name order. The games carry their id, slug and name only. Two queries."""
    from catalogue.models import Game

    rows = (Product.objects.active().filter(game__is_active=True).exclude(product_type=Product.Type.OTHER)
            .order_by().values("game_id", "game__slug", "game__name", "product_type").annotate(n=Count("pk"))
            .filter(n__gte=PAGE_LEAST))
    taken = set(ProductSet.objects.filter(game__is_active=True).values_list("game_id", "slug"))
    found = []
    for row in rows:
        slug = slug_for(row["game__slug"], row["product_type"])
        if slug and (row["game_id"], slug) not in taken:
            found.append((Game(pk=row["game_id"], slug=row["game__slug"], name=row["game__name"]), row["product_type"]))
    found.sort(key=lambda pair: (pair[0].name, pair[1]))
    return found
