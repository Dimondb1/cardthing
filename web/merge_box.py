"""
Merge two products: the owner's page for a duplicate the site did not spot itself, and for an old address that
leads nowhere.

Things to check only offers the pairs the site's own rules find. An audit of the site (17 Nov 2026) found pages the
rules never meet: "One Piece Heroines Edition Vol.2 Booster Box Extra Booster" beside "EB05 Heroines Edition Vol.2
Booster Box", one saying from £204.99 and the other from £119.95 for the same box, and an address Google had shown
that answered not found while the same product was live at another. The owner: "Approve, add merge box".

The owner pastes two product addresses: the page to keep and the page to fold into it. The page shows the two side
by side (game, set, kind, language, shops and cheapest price) and says what would happen:

- Both on the site: merged as Things to check merges (merge_duplicates.merge_with_undo). The other page's shops,
  prices, price history, alerts and clicks move to the kept page, its address redirects there for good, and the
  merge is listed under Sorted for you with Undo.
- The other page is switched off, or its address is no product at all: only its address is sent to the kept page
  (an old product address), so visitors and search engines land on the live one. Its shops and prices are not
  moved: a page is switched off for a reason, often that its items are not sealed products.
- Never merged: two games, or two languages (an English box is not the Japanese one). When the kinds differ, the
  names say something different (a number, a set code, a Collector's Edition), nothing in the names points at one
  product, or a shop lists both, the owner must tick that they are the same product first.

Staff only, never for editors, never indexed or cached. Nothing happens on a GET: it only shows.
"""

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from catalogue import addresses, languages
from catalogue.addresses import find
from catalogue.models import Listing
from catalogue.pricing import MARKETPLACES
from catalogue.types import type_label
from guides.access import not_for_editors

WHY = "You merged them by address"


def side(product):
    """What the page shows of one product, read in two queries."""
    rows = list(Listing.objects.filter(product=product).live().select_related("retailer"))
    buyable = [row for row in rows if row.is_buyable]
    game = product.game.slug if product.game_id else ""
    return {
        "product": product,
        "kind": type_label(game, product.product_type) or product.product_type,
        "language": languages.name(languages.product_language(product)),
        "shops": len({row.retailer_id for row in rows}),
        "priced": len({row.retailer_id for row in buyable}),
        "cheapest": min((row.delivered_price for row in buyable if row.delivery_known), default=None),
    }


def both_sell(keep, other):
    """How many shops (never eBay or Amazon, which search for every product) show both products."""
    def shops(product):
        return set(Listing.objects.filter(product=product).live().exclude(retailer__source_type__in=MARKETPLACES)
                   .values_list("retailer_id", flat=True))
    return len(shops(keep) & shops(other))


def judge_pair(keep, other):
    """(what would happen, why not, cautions) for folding ``other`` into ``keep``.

    What: "merge" (both on the site), "redirect" (``other`` is switched off: only its address moves), or "" with
    the reason it cannot be done. Cautions are what the owner must look at and tick before a merge."""
    from catalogue.management.commands.merge_duplicates import share_a_key, why_not_merge

    if keep.pk == other.pk:
        return "", "Both addresses are the same product.", []
    if not keep.is_active:
        return "", f"{keep.name} is switched off. Keep the page that is on the site.", []
    if keep.game_id != other.game_id:
        return "", f"They are in different games: {keep.game.name} and {other.game.name}.", []
    ours, theirs = languages.product_language(keep), languages.product_language(other)
    if not languages.same(ours, theirs):
        return "", (f"They are in different languages: {languages.name(ours)} and {languages.name(theirs)}. "
                    "Each language keeps its own page."), []
    if not other.is_active:
        return "redirect", "", []
    cautions = []
    if keep.product_type != other.product_type:
        game = keep.game.slug
        cautions.append(f"They are filed as different kinds: {type_label(game, keep.product_type) or keep.product_type} "
                        f"and {type_label(game, other.product_type) or other.product_type}.")
    why = why_not_merge(keep, other)
    if why:
        cautions.append(f"The site's own rules would not merge them: {why}.")
    elif not share_a_key(keep, other):
        # Any two addresses can be pasted here: say so when nothing in the names, codes or set points at one product.
        cautions.append("Their names do not say the same product to the site. Open both pages and check they are "
                        "one product: the same set, kind, language and size.")
    shared = both_sell(keep, other)
    if shared:
        cautions.append(f"{shared} shop{'s' if shared != 1 else ''} list{'s' if shared == 1 else ''} both. A shop that "
                        "sells both may mean two products. Merged, the kept page keeps its own price from that shop.")
    return "merge", "", cautions


def address(slug):
    return reverse("web:product", args=[slug])


def do_merge(request, keep, other):
    """Merge ``other`` into ``keep`` as Things to check does, so it can be undone there."""
    from catalogue import judge
    from catalogue.management.commands.merge_duplicates import merge_with_undo
    from catalogue.signals import clear_list_caches

    with transaction.atomic():
        row_key = judge.pair_key(keep, other)
        merge_with_undo(keep, other, WHY, by_owner=True)
        judge.note_owner(row_key, "same")
    clear_list_caches(force=True)
    messages.success(request, f"Merged {other.name} into {keep.name}. {address(other.slug)} now leads to "
                              f"{address(keep.slug)}. It is listed under Sorted for you on Things to check, with Undo.")


def do_redirect(request, keep, slug):
    """Send an address that shows no page to ``keep``: an old product address, as a merge leaves."""
    from catalogue.signals import clear_list_caches

    addresses.send_to(slug, keep)
    clear_list_caches(force=True)
    messages.success(request, f"{address(slug)} now leads to {address(keep.slug)}. To take it back, delete it under "
                              "Old product addresses.")


@staff_member_required
@not_for_editors
@require_http_methods(["GET", "POST"])
def merge_page(request):
    asked = request.POST if request.method == "POST" else request.GET
    keep_text, other_text = asked.get("keep", ""), asked.get("other", "")
    keep, other = find(keep_text), find(other_text)
    problem, what, cautions = "", "", []
    if keep_text or other_text:
        if not keep["slug"] or not other["slug"]:
            problem = "Paste both addresses: the page to keep and the page to fold into it."
        elif keep["product"] is None:
            problem = f"No product is at {address(keep['slug'])}. The page to keep must be on the site."
        elif other["product"] is None:
            # No product at all: only the address can be sent on.
            what = "redirect" if keep["product"].is_active else ""
            problem = "" if what else f"{keep['product'].name} is switched off. Keep the page that is on the site."
        elif other["old"] and other["product"].pk == keep["product"].pk:
            problem = f"{address(other['slug'])} already leads to {keep['product'].name}."
        else:
            what, problem, cautions = judge_pair(keep["product"], other["product"])
    if request.method == "POST":
        if not what:
            messages.warning(request, problem or "Paste both addresses first.")
        elif what == "merge" and cautions and request.POST.get("same") != "yes":
            messages.warning(request, "Tick that they are the same product first, then merge.")
        elif what == "merge":
            do_merge(request, keep["product"], other["product"])
            return redirect("merge_products")
        else:
            # A switched-off page keeps its own address: that is the one sent on, never an old address of another.
            dead = other["product"] is None or (not other["old"] and not other["product"].is_active)
            if dead:
                do_redirect(request, keep["product"], other["slug"])
                return redirect("merge_products")
            messages.warning(request, f"{address(other['slug'])} already leads to another page. Merge that page.")
    response = render(request, "admin/merge_products.html", {
        **admin.site.each_context(request),
        "title": "Merge two products",
        "keep_text": keep_text, "other_text": other_text,
        "keep": side(keep["product"]) if keep["product"] is not None else None,
        "other": side(other["product"]) if other["product"] is not None else None,
        "other_slug": other["slug"], "other_old": other["old"],
        "what": what, "problem": problem, "cautions": cautions,
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
