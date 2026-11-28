"""
Amazon: the Creators API credential and the Associates tag, typed in on a page of their own in Admin and saved into a
file only the site can read (catalogue/amazon.py), so the owner never pastes a key into a message or a server's
settings. Each save asks Amazon for a token and one search, and says plainly whether the keys work. Staff only, never
for editors. The owner, 13 Nov 2026: "We have been approved for the amazon creators api ... I have this and a secret
password thing ... I now need you to build it in".

Working keys also add Amazon as a shop and switch it on (amazon.ensure_shop), so the next hourly read starts reading
it; the page shows how many Amazon prices the product pages show now, and a Log to copy (log_text) of everything about
Amazon's reading, for the owner to paste to Claude (the owner, 24 Nov 2026: "Need to ensure Amazon health area has a
log").
"""

from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from catalogue import amazon, notify, worklog
from catalogue.importers import AMAZON_EVERY_HOURS
from catalogue.models import ImportRun, Listing, Product, Retailer
from guides.access import not_for_editors

LOG_RUNS, LOG_RUN_LINES, LOG_LINES = 6, 8, 60


def status():
    """What the page says of Amazon now: the shop, its last read, and the Amazon prices shown."""
    shop = Retailer.objects.filter(source_type=Retailer.Source.AMAZON).order_by("pk").first()
    if shop is None:
        return {"shop": None}
    since = timezone.now() - timedelta(hours=settings.RIPRAPTOR_AMAZON_SHOW_HOURS)
    return {
        "shop": shop,
        "waiting": shop.awaiting_access,
        "last": ImportRun.objects.filter(retailer=shop, finished_at__isnull=False).order_by("-finished_at").first(),
        "shown": Listing.objects.filter(retailer=shop, is_active=True, availability__in=Listing.BUYABLE,
                                        last_checked__gte=since).count(),
        "known": shop.listings.count(),
    }


def log_text(now, found, keys_set, from_settings):
    """Everything about Amazon's reading as plain text: the keys (set or not, never their values), the shop's row, the
    prices shown and the products found, the day's calls and a run's share, the last runs with the end of each one's
    log, and the reader's own lines about Amazon (catalogue/worklog.py). Five queries."""
    def at(moment):
        if moment is None:
            return "none"
        minutes = int((now - moment).total_seconds() // 60)
        ago = f"{minutes} min ago" if 0 <= minutes < 120 else f"{minutes // 60} h ago" if minutes >= 0 else \
            f"in {-minutes} min" if minutes > -120 else f"in {-minutes // 60} h"
        return f"{timezone.localtime(moment):%d %b %H:%M} ({ago})"

    shop = found.get("shop")
    out = [f"RipRaptor Amazon log, {timezone.localtime(now):%a %d %b %Y %H:%M} UK time",
           f"keys: {'set' if keys_set else 'NOT SET'}{' (in the server settings)' if from_settings else ''}"]
    out += ["", "AMAZON SHOP ROW"]
    if shop is None:
        out.append("no Amazon shop yet")
    else:
        out += [f"{shop.name} (id {shop.pk}): switched on {'yes' if shop.is_active else 'NO'}, paused "
                f"{'YES' if shop.reading_paused else 'no'}, reads every {shop.every_minutes} min",
                f"next read: {at(shop.next_read_at)}, last good read: {at(shop.last_ok_at)}, last read took "
                f"{shop.last_read_seconds or 0} s",
                f"failed tries in a row: {shop.error_streak}, waiting until: {at(shop.backoff_until)}",
                f"last error: {shop.last_error or 'none'}",
                f"prices shown on product pages now: {found['shown']}, Amazon listings found: {found['known']}"]
        invited = list(Listing.objects.filter(retailer=shop, is_active=True, closed_why=Listing.Closed.INVITE)
                       .order_by("product__name").values_list("product__name", flat=True)[:20])
        out.append(f"sold by invitation only now: {len(invited)}" + (f" ({', '.join(invited)})" if invited else ""))
    products = Product.objects.active()
    out += [f"products found on Amazon: {products.exclude(amazon_asin='').count()}, never looked up: "
            f"{products.filter(amazon_asin='', amazon_checked_at__isnull=True).count()}",
            f"calls: at most {settings.RIPRAPTOR_AMAZON_DAILY_LIMIT} a day, about "
            f"{max(1, settings.RIPRAPTOR_AMAZON_DAILY_LIMIT * AMAZON_EVERY_HOURS // 24)} a read, every "
            f"{AMAZON_EVERY_HOURS} h; a price shows for {settings.RIPRAPTOR_AMAZON_SHOW_HOURS} h after its check"]
    out += ["", f"AMAZON READS (the last {LOG_RUNS})"]
    runs = list(ImportRun.objects.filter(retailer__source_type=Retailer.Source.AMAZON).order_by("-started_at")[:LOG_RUNS])
    for run in runs:
        ended = f"{timezone.localtime(run.finished_at):%H:%M}" if run.finished_at else "STILL RUNNING"
        out.append(f"{timezone.localtime(run.started_at):%d %b %H:%M} to {ended}: {run.offers_found} offers found, "
                   f"{run.listings_updated} listings updated{', ERROR: ' + run.error[:300] if run.error else ''}")
        out += ["    " + line[:300] for line in (run.log or "").splitlines()[-LOG_RUN_LINES:]]
    if not runs:
        out.append("none on record")
    kept = [lines for _bad, lines in worklog.entries() if any("amazon" in line.lower() for line in lines)][-LOG_LINES:]
    out += ["", f"READER LOG: LINES ABOUT AMAZON (the newest {LOG_LINES}, oldest first)"]
    out += [line for lines in kept for line in lines] or ["none kept yet"]
    # A shop's last error and a run's own log come from outside: nothing secret leaves in them either.
    return worklog.scrub("\n".join(out))


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
            shop = amazon.ensure_shop(active=True)
            # A wait for Amazon to share its prices is over: read at the next turn, not hours later.
            if shop.awaiting_access:
                Retailer.objects.filter(pk=shop.pk).update(last_error="", backoff_until=None, next_read_at=timezone.now())
                notify.amazon_open()
            messages.success(request, f"Saved. Amazon accepted the keys and answered a test search with {said} "
                                      "items. Amazon is read at the next hourly read, then every hour.")
        elif Retailer.NOT_ELIGIBLE in str(said):
            # The keys work: Amazon will not share its prices with this account yet. The shop is switched on and asked
            # again every few hours, so prices show by themselves once Amazon says yes.
            amazon.ensure_shop(active=True).waiting_for_access(timezone.now(), str(said))
            messages.warning(request, f"Saved. The keys work, but Amazon will not share its prices with this account "
                                      f"yet: it needs 10 sales in the last 30 days. The site asks Amazon again every "
                                      f"{Retailer.ACCESS_WAIT.seconds // 3600} hours, and Amazon prices show by "
                                      "themselves once it says yes.")
        else:
            messages.error(request, f"Saved, but Amazon did not accept them yet: {said}. Amazon says access can take up "
                                    "to 48 hours after a credential is made; save again later to check.")
        return redirect(reverse("amazon_keys"))
    found = status()
    response = render(request, "admin/amazon_keys.html", {
        **admin.site.each_context(request),
        "title": "Amazon",
        "log": log_text(timezone.now(), found, bool(credential_id and secret and tag), from_settings),
        "credential_id": credential_id,
        "has_secret": bool(secret),
        "tag": tag or "ripraptor-21",
        "from_settings": from_settings,
        "show_hours": settings.RIPRAPTOR_AMAZON_SHOW_HOURS,
        "access_hours": Retailer.ACCESS_WAIT.seconds // 3600,
        "will_email": notify.can_email(),
        **found,
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
