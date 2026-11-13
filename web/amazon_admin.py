"""
Amazon: the Creators API credential and the Associates tag, typed in on a page of their own in Admin and saved into a
file only the site can read (catalogue/amazon.py), so the owner never pastes a key into a message or a server's
settings. Each save asks Amazon for a token and one search, and says plainly whether the keys work. Staff only, never
for editors. The owner, 13 Nov 2026: "We have been approved for the amazon creators api ... I have this and a secret
password thing ... I now need you to build it in".

A save also adds Amazon as a shop (setup_shops' AMAZON) if it is not one yet, so the next hourly read starts reading
it, and shows how many Amazon prices the product pages show now.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from catalogue import amazon
from catalogue.models import ImportRun, Listing, Retailer
from guides.access import not_for_editors


def add_amazon_shop():
    """Amazon as a shop, as setup_shops adds it, when it is not one yet. Returns the shop."""
    from decimal import Decimal

    from catalogue.management.commands.setup_shops import AMAZON, CADENCE

    slug, name, website, source_type, delivery, free_over, note = AMAZON
    shop = Retailer.objects.filter(source_type=Retailer.Source.AMAZON).order_by("pk").first()
    if shop is None:
        shop = Retailer.objects.create(slug=slug, name=name, website=website, source_url=website,
                                       source_type=source_type, delivery_cost=Decimal(delivery),
                                       free_delivery_over=Decimal(free_over), delivery_note=note,
                                       read_every_minutes=CADENCE[source_type])
    return shop


def status():
    """What the page says of Amazon now: the shop, its last read, and the Amazon prices shown."""
    shop = Retailer.objects.filter(source_type=Retailer.Source.AMAZON).order_by("pk").first()
    if shop is None:
        return {"shop": None}
    since = timezone.now() - timedelta(hours=settings.RIPRAPTOR_AMAZON_SHOW_HOURS)
    return {
        "shop": shop,
        "last": ImportRun.objects.filter(retailer=shop, finished_at__isnull=False).order_by("-finished_at").first(),
        "shown": Listing.objects.filter(retailer=shop, is_active=True, availability__in=Listing.BUYABLE,
                                        last_checked__gte=since).count(),
        "known": shop.listings.count(),
    }


@staff_member_required
@not_for_editors
@require_http_methods(["GET", "POST"])
def amazon_page(request):
    credential_id, secret, tag = amazon.keys()
    from_settings = bool(settings.RIPRAPTOR_AMAZON_CREDENTIAL_ID and settings.RIPRAPTOR_AMAZON_CREDENTIAL_SECRET)
    if request.method == "POST":
        if from_settings:
            messages.warning(request, "The credential is set in the server's settings, which win, so nothing was saved.")
            return redirect(reverse("amazon_keys"))
        typed_id = request.POST.get("credential_id", "").strip()
        typed_secret = request.POST.get("secret", "").strip()
        typed_tag = request.POST.get("tag", "").strip() or tag
        if not typed_id or not (typed_secret or secret) or not typed_tag:
            messages.error(request, "Type the Credential ID, the Credential Secret and the tracking tag, then Save.")
            return redirect(reverse("amazon_keys"))
        amazon.save_keys(typed_id, typed_secret, typed_tag)
        ok, said = amazon.check()
        if ok:
            add_amazon_shop()
            messages.success(request, f"Saved. Amazon accepted the keys and answered a test search with {said} "
                                      "items. Amazon is read at the next hourly read, then every 6 hours.")
        else:
            messages.error(request, f"Saved, but Amazon did not accept them yet: {said}. Amazon says access can take up "
                                    "to 48 hours after a credential is made; save again later to check.")
        return redirect(reverse("amazon_keys"))
    response = render(request, "admin/amazon_keys.html", {
        **admin.site.each_context(request),
        "title": "Amazon",
        "credential_id": credential_id,
        "has_secret": bool(secret),
        "tag": tag or "ripraptor-21",
        "from_settings": from_settings,
        "show_hours": settings.RIPRAPTOR_AMAZON_SHOW_HOURS,
        **status(),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
