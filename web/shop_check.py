"""
Shop check (Admin): the owner reads what a search for each shop's public record turned up and gives the shop his own
label, Checked, New or Caution. The owner, 27 Nov 2026: "Look each shop up the way a person would (search, read what
comes back) ... and use that to propose our own label per shop: Checked, New or Caution. You confirm each one in
Admin".

The findings come as a file (catalogue/shop_check.py says how they are made, and why they are never part of the
code), loaded here by choosing it or pasting it. Each shop with a finding waiting is a card: the label proposed and
its one-sentence reason, what the search showed, any complaints, and the links it rests on, to open and read. Under
it the label to give, the reason in words a shopper could read, and the link to the shop's public reviews; Confirm
saves them on the shop (Retailer.trust). A shop he would rather not list can be switched off in the same tap. The
shops proposed Checked, and those proposed New, can each be confirmed together; a Caution never is: it is a
statement about a business, so each is read and confirmed by itself. Nothing is set by a program.

A shop is looked up more than once. When a finding loaded after he labelled a shop proposes a different label, the
shop goes back among those waiting, saying what he chose and when, so a second look is never hidden behind his
first answer. His label stays until he changes it, and such a shop is never swept up by a confirm-together.

Staff only, never for editors, never cached or indexed. Two queries for the page.
"""

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from catalogue import shop_check
from catalogue.models import RemovedShop, Retailer, ShopCheck
from catalogue.pricing import MARKETPLACES
from guides.access import not_for_editors

FILE_MOST = 2 * 1024 * 1024          # a findings file, at most
TOGETHER = (Retailer.Trust.CHECKED, Retailer.Trust.NEW)     # the labels that may be confirmed many at once
ORDER = {Retailer.Trust.CAUTION: 0, Retailer.Trust.NEW: 1, Retailer.Trust.CHECKED: 2}


def proposal_of(shop):
    """The shop's finding, or None."""
    try:
        return shop.shop_check
    except ShopCheck.DoesNotExist:
        return None


def shops():
    """Every shop that is not a marketplace (their sellers have their own standing), each with its finding, as
    ``shop.proposal`` and ``shop.host``. One query."""
    found = list(Retailer.objects.exclude(source_type__in=MARKETPLACES).select_related("shop_check").order_by("name"))
    for shop in found:
        shop.proposal = proposal_of(shop)
        shop.host = RemovedShop.host_of(shop.website)
        shop.differs = shop_check.differs(shop, shop.proposal)
    return found


def take_file(request):
    """The findings the owner sent: the file he chose, else what he pasted. ("", why not) when there is none to
    read."""
    sent = request.FILES.get("file")
    if sent is not None:
        if sent.size > FILE_MOST:
            return "", "That file is too large to be a findings file."
        try:
            return sent.read().decode("utf-8-sig"), ""
        except UnicodeDecodeError:
            return "", "That file could not be read as text."
    text = request.POST.get("findings", "").strip()
    return (text, "") if text else ("", "Choose the findings file, or paste what it holds.")


def load(request):
    text, why = take_file(request)
    if not text:
        messages.warning(request, why)
        return
    try:
        done = shop_check.load(text)
    except ValueError as exc:
        messages.error(request, str(exc))
        return
    messages.success(request, f"{done['saved']} shop{'s' if done['saved'] != 1 else ''} loaded. Read each one below "
                              "and confirm its label.")
    if done["unknown"]:
        messages.info(request, f"Not a shop here, so left out: {', '.join(done['unknown'][:20])}"
                               f"{' and more' if len(done['unknown']) > 20 else ''}.")
    if done["bad"]:
        messages.warning(request, f"No label we know, so left out: {', '.join(done['bad'][:20])}.")


def confirm_one(request):
    shop = Retailer.objects.exclude(source_type__in=MARKETPLACES).filter(pk=request.POST.get("shop") or 0).first()
    if shop is None:
        messages.error(request, "That shop is no longer here.")
        return ""
    label = request.POST.get("label", "")
    shop_check.confirm(shop, label, request.POST.get("note", ""), request.POST.get("reviews_url", ""))
    said = f"{shop.name}: {shop.get_trust_display()}." if shop.trust else f"{shop.name}: no label."
    if request.POST.get("hide") and shop.is_active:
        shop.is_active = False
        shop.save(update_fields=["is_active"])
        said += " Switched off: its prices leave the site within a few minutes."
    messages.success(request, said)
    return f"#shop-{shop.pk}"


def confirm_together(request):
    label = request.POST.get("label", "")
    if label not in TOGETHER:
        messages.error(request, "Only shops proposed Checked or New can be confirmed together.")
        return
    done = 0
    for shop in shops():
        if not shop.trust and shop.proposal is not None and shop.proposal.label == label:
            shop_check.confirm(shop, label, shop.proposal.reason, shop.proposal.reviews_url)
            done += 1
    messages.success(request, f"{done} shop{'s' if done != 1 else ''} confirmed as {Retailer.Trust(label).label}.")


@staff_member_required
@not_for_editors
@require_http_methods(["GET", "POST"])
def shop_check_page(request):
    if request.method == "POST":
        action, anchor = request.POST.get("action", ""), ""
        if action == "load":
            load(request)
        elif action == "confirm":
            anchor = confirm_one(request)
        elif action == "together":
            confirm_together(request)
        return redirect(reverse("shop_check") + anchor)
    every = shops()
    # Waiting: a finding he has not answered, the shops whose newer finding disagrees with his label first.
    waiting = sorted((shop for shop in every if shop.proposal is not None and (not shop.trust or shop.differs)),
                     key=lambda shop: (not shop.differs, ORDER.get(shop.proposal.label, 9), shop.name.lower()))
    confirmed = sorted((shop for shop in every if shop.trust and not shop.differs),
                       key=lambda shop: (ORDER.get(shop.trust, 9), shop.name.lower()))
    bare = [shop for shop in every if not shop.trust and shop.proposal is None]
    together = [(label, Retailer.Trust(label).label,
                 sum(1 for shop in waiting if not shop.trust and shop.proposal.label == label)) for label in TOGETHER]
    response = render(request, "admin/shop_check.html", {
        **admin.site.each_context(request),
        "title": "Shop check",
        "waiting": waiting, "confirmed": confirmed, "bare": bare, "labels": Retailer.Trust.choices,
        "together": [row for row in together if row[2]],
        "cautions": sum(1 for shop in waiting if shop.proposal.label == Retailer.Trust.CAUTION),
        "differing": sum(1 for shop in waiting if shop.differs),
        "counts": {label: sum(1 for shop in every if shop.trust == value) for value, label in Retailer.Trust.choices},
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
