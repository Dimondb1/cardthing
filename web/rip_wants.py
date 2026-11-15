"""
What Rip couldn't find: the owner's page of what people asked Rip for that the site does not have (the owner, 15 Nov
2026, picking it from the suggestions after small talk: "What Rip couldn't find"). Read from the requests already
recorded (catalogue.ClaudeAsk, kind ask, from the list builder, Search by photo, the microphone, the Ask popup and the
search bars); nothing new is kept:

- Products Rip named that the site's search finds nothing for now, the most asked first: the sets, products and
  languages to add shops or products for.
- General requests with nothing in stock now ("Korean ETBs", "booster boxes under £20"), each linked to its list.
- What Rip could not read as anything to buy, newest first: how people put things he did not follow.

Staff only, never for editors, never indexed or cached.
"""

from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import languages
from catalogue.models import ClaudeAsk, Game, Product
from catalogue.ordering import CARDS_KNOWN, apply_languages
from guides.access import not_for_editors
from web import rip_list

PERIODS = (7, 30, 90)
DEFAULT_DAYS = 30
# Rip's requests (web/rip_list.py send): the list builder and photos, the microphone and the Ask popup, the search bars.
# A product page's questions (row key ask:<product>) are about a product the site has.
ROW_KEYS = ("list", "voice", "search")
CHECKED = 150     # the most asked products checked against the catalogue now, at most (one search each)
SHOWN = 100       # rows in each table, at most
BANDS = {"0-10": "under £10", "10-25": "£10 to £25", "25-50": "£25 to £50", "50-100": "£50 to £100",
         "100-": "£100 and up"}


def requests_since(since):
    """Rip's answered requests since ``since``, newest first: (asked_at, row_key, the words, his answer). One query."""
    rows = (ClaudeAsk.objects.filter(kind=ClaudeAsk.Kind.ASK, row_key__in=ROW_KEYS, outcome=ClaudeAsk.Outcome.ANSWERED,
                                     asked_at__gte=since)
            .order_by("-asked_at").values_list("asked_at", "row_key", "evidence", "set_answer"))
    found = []
    for asked_at, row_key, evidence, answer in rows:
        evidence = evidence if isinstance(evidence, dict) else {}
        words = evidence.get("list") or ""
        found.append({"at": asked_at, "via": row_key, "words": words, "photo": bool(evidence.get("photo")),
                      "answer": answer if isinstance(answer, dict) else {}})
    return found


def not_found(rows):
    """[{search, game, times, last, said}]: the products Rip named that the site's search finds nothing for now (the
    list builder's own search, rip_list.matches), the most asked first. At most CHECKED are checked."""
    wanted = {}
    for row in rows:   # newest first, so the first of each is the latest
        for item in row["answer"].get("items") or []:
            search = " ".join(str(item.get("search") or "").split())
            if not search:
                continue
            game = item.get("game") or ""
            entry = wanted.setdefault((search.casefold(), game), {"search": search, "game": game, "times": 0,
                                                                  "last": row["at"], "said": row["words"],
                                                                  "photo": row["photo"]})
            entry["times"] += 1
    ranked = sorted(wanted.values(), key=lambda entry: (-entry["times"], -entry["last"].timestamp()))[:CHECKED]
    return [entry for entry in ranked if not rip_list.matches(entry["search"], entry["game"])]


def in_stock_for(browse):
    """How many products are in stock now for a general request, filtered as its list is (web.views._browse): the
    kind, game, set, language, price band or limit and Best for ripping. One query."""
    products = Product.objects.active().with_prices().in_stock()
    if browse.get("type"):
        products = products.filter(product_type=browse["type"])
    if browse.get("game"):
        products = products.filter(game__slug=browse["game"])
    if browse.get("set"):
        products = products.filter(product_set__slug=browse["set"])
    if browse.get("language"):
        products = apply_languages(products, [browse["language"]])
    band = browse.get("price") or ""
    if band:
        low, _, high = band.partition("-")
        products = products.filter(lowest_price__gte=low)
        if high:
            products = products.filter(lowest_price__lt=high)
    if browse.get("max_price"):
        products = products.filter(lowest_price__lte=browse["max_price"])
    if browse.get("rip"):
        products = products.filter(CARDS_KNOWN)
    return products.count()


def describe(browse, game_names):
    """A general request in a few words: "Booster boxes, Pokémon, Korean, under £20"."""
    parts = []
    if browse.get("type") in Product.Type.values:
        parts.append(str(Product.Type(browse["type"]).label))
    if browse.get("game"):
        parts.append(game_names.get(browse["game"], browse["game"]))
    if browse.get("set_name"):
        parts.append(browse["set_name"])
    if browse.get("language"):
        parts.append(dict(languages.FILTER).get(browse["language"], browse["language"]))
    if browse.get("price") in BANDS:
        parts.append(BANDS[browse["price"]])
    if browse.get("max_price"):
        parts.append(f"under £{browse['max_price']:g}")
    if browse.get("rip"):
        parts.append("for ripping")
    return ", ".join(parts) or "Anything"


def empty_requests(rows, game_names):
    """[{what, times, last, said, url}]: general requests with nothing in stock now, the most asked first. One count
    for each different request, at most CHECKED."""
    asked = {}
    for row in rows:
        browse = row["answer"].get("browse")
        if not isinstance(browse, dict):
            continue
        key = tuple(str(browse.get(name) or "") for name in ("type", "game", "set", "language", "price", "max_price",
                                                             "rip"))
        entry = asked.setdefault(key, {"browse": browse, "times": 0, "last": row["at"], "said": row["words"]})
        entry["times"] += 1
    ranked = sorted(asked.values(), key=lambda entry: (-entry["times"], -entry["last"].timestamp()))[:CHECKED]
    return [{**entry, "what": describe(entry["browse"], game_names), "url": rip_list.browse_url(entry["browse"])}
            for entry in ranked if in_stock_for(entry["browse"]) == 0]


def not_read(rows):
    """[{at, words, photo, via}]: what Rip could not read as anything to buy or as small talk, newest first."""
    return [row for row in rows
            if not row["answer"].get("items") and not row["answer"].get("browse") and not row["answer"].get("chat")]


VIA = {"list": "List builder", "voice": "Spoken or Ask popup", "search": "Search bar"}


@staff_member_required
@not_for_editors
@require_GET
def rip_wants_page(request):
    try:
        days = int(request.GET.get("days", DEFAULT_DAYS))
    except ValueError:
        days = DEFAULT_DAYS
    days = days if days in PERIODS else DEFAULT_DAYS
    rows = requests_since(timezone.now() - timedelta(days=days))
    game_names = dict(Game.objects.values_list("slug", "name"))
    missing = not_found(rows)
    for entry in missing:
        entry["game_name"] = game_names.get(entry["game"], "")
    unread = not_read(rows)
    for row in unread:
        row["via_name"] = VIA.get(row["via"], row["via"])
    response = render(request, "admin/rip_wants.html", {
        **admin.site.each_context(request),
        "title": "What Rip couldn't find",
        "days": days, "periods": PERIODS, "asked": len(rows),
        "chats": sum(1 for row in rows if row["answer"].get("chat")),
        "missing": missing[:SHOWN], "empty": empty_requests(rows, game_names)[:SHOWN], "unread": unread[:SHOWN],
        "unread_more": max(0, len(unread) - SHOWN),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
