"""
Add shops: the owner pastes shop addresses, and the background reader checks each one and adds those that pass
(catalogue/shop_adding.py). Staff only, never for editors. The owner, 8 Nov 2026: "find how they are connecting 208
shops, we need to best this", and the bulk Add shops tool was the first of the two answers.
"""

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from catalogue import shop_adding
from catalogue.models import ShopToAdd
from guides.access import not_for_editors

SHOWN = 200


@staff_member_required
@not_for_editors
@require_http_methods(["GET", "POST"])
def add_shops_page(request):
    if request.method == "POST":
        if request.POST.get("again"):
            ShopToAdd.objects.filter(pk=request.POST["again"]).exclude(status=ShopToAdd.Status.ADDED).update(
                status=ShopToAdd.Status.WAITING, note="", checked_at=None)
            messages.success(request, "It will be checked again within a minute.")
        else:
            saved, known = shop_adding.take(request.POST.get("addresses", ""))
            if saved:
                messages.success(request, f"{saved} shop{'s' if saved != 1 else ''} to check. Each is checked within "
                                          "a few minutes and added if it passes.")
            if known:
                messages.info(request, f"{known} address{'es' if known != 1 else ''} already on this list.")
            if not saved and not known:
                messages.warning(request, "No shop addresses found. Paste one address per line.")
        return redirect(reverse("add_shops"))
    rows = list(ShopToAdd.objects.select_related("retailer")[:SHOWN])
    counts = {status: 0 for status in ShopToAdd.Status.values}
    for status in ShopToAdd.objects.values_list("status", flat=True):
        counts[status] += 1
    response = render(request, "admin/add_shops.html", {
        **admin.site.each_context(request),
        "title": "Add shops",
        "rows": rows, "counts": counts, "Status": ShopToAdd.Status,
        "more": max(0, sum(counts.values()) - SHOWN),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
