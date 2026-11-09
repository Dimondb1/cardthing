"""
Shop delivery: every shop whose prices lack a delivery charge on one page, with its standard charge and free-delivery
threshold to type in and one Save for them all. Staff only, never for editors. The owner, 9 Nov 2026: "I need an easy
way on dashboard to see how many shops don't have delivery and an easy way to update them at once".

The daily delivery check (check_delivery --unknown) reads these from each shop's own pages and leaves here the ones it
could not read. A charge saved here is applied to the shop's prices at once (pricing.apply_delivery_rules), as a save
in the shop's own Admin page is.
"""

import re
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from catalogue import checks, pricing
from catalogue.models import Listing, Retailer
from guides.access import not_for_editors

MAX_DELIVERY = Decimal("50")
MAX_FREE_OVER = Decimal("1000")
FREE_WORDS = {"free", "0", "0.00", "nil", "none"}


class NotAPrice(ValueError):
    pass


def read_price(raw, most):
    """A typed price ("3.99", "£3.99", "free") as a Decimal, None when nothing was typed. NotAPrice otherwise."""
    text = (raw or "").strip().lower().replace("£", "").replace(",", ".").strip()
    if not text:
        return None
    if text in FREE_WORDS:
        return Decimal("0.00")
    if not re.fullmatch(r"\d{1,4}(?:\.\d{1,2})?", text):
        raise NotAPrice(raw)
    try:
        value = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise NotAPrice(raw) from exc
    if value > most:
        raise NotAPrice(raw)
    return value


def save_typed(post, shops, today):
    """Save what was typed for each of ``shops`` (their pks are in the form) and apply it to their prices. Returns
    (shops saved, prices that now include delivery, prices still without, the problems as sentences)."""
    saved = fixed = still = 0
    problems = []
    for shop in shops:
        try:
            cost = read_price(post.get(f"cost-{shop.pk}"), MAX_DELIVERY)
        except NotAPrice as exc:
            problems.append(f"{shop.name}: “{exc}” is not a delivery charge. Type it like 3.99, or 0 for free.")
            continue
        try:
            free_over = read_price(post.get(f"free-{shop.pk}"), MAX_FREE_OVER)
        except NotAPrice as exc:
            problems.append(f"{shop.name}: “{exc}” is not an order value. Type it like 50.")
            continue
        changed = []
        if cost is not None and cost != shop.delivery_cost:
            shop.delivery_cost = cost
            changed.append("delivery_cost")
        if free_over is not None and free_over != shop.free_delivery_over:
            shop.free_delivery_over = free_over
            changed.append("free_delivery_over")
        if not changed:
            continue
        shop.delivery_note = f"Set by hand on Shop delivery, {today}"[:120]
        shop.save(update_fields=[*changed, "delivery_note"])
        unknown_before = shop.unknown
        pricing.apply_delivery_rules(shop)
        unknown_now = Listing.objects.filter(retailer=shop, is_active=True, delivery_known=False).count()
        saved += 1
        fixed += max(0, unknown_before - unknown_now)
        still += unknown_now
    return saved, fixed, still, problems


@staff_member_required
@not_for_editors
@require_http_methods(["GET", "POST"])
def delivery_page(request):
    shops = checks.unknown_delivery_shops()
    if request.method == "POST":
        asked = set(request.POST.getlist("shop"))
        chosen = [shop for shop in shops if str(shop.pk) in asked]
        saved, fixed, still, problems = save_typed(request.POST, chosen, timezone.localdate().strftime("%d %b %Y"))
        if saved:
            line = f"Saved delivery for {saved} shop{'s' if saved != 1 else ''}. {fixed} price{'s' if fixed != 1 else ''} now include{'s' if fixed == 1 else ''} delivery."
            if still:
                line += f" {still} still show{'s' if still == 1 else ''} plus delivery: those shops need a charge, or a free-delivery value under their prices."
            messages.success(request, line)
        for problem in problems:
            messages.warning(request, problem)
        if not saved and not problems:
            messages.info(request, "Nothing was typed in, so nothing changed.")
        return redirect(reverse("shop_delivery"))
    response = render(request, "admin/shop_delivery.html", {
        **admin.site.each_context(request),
        "title": "Shop delivery",
        "shops": shops,
        "prices": sum(shop.unknown for shop in shops),
        "marketplaces": Retailer.objects.filter(is_active=True, source_type__in=pricing.MARKETPLACES).exists(),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
