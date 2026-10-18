"""
Drop reminders: sign up on the drop tracker, confirm, hear up to three times, forget.

Kept like the back-in-stock alerts (catalogue/alerts.py): the address is stored for this alone, nothing is
sent until the confirm link is pressed, every email carries a link that deletes it at once, and it goes
after the last email. The stages, each sent at most once and only when its data exists:

- pre-orders: a shop opened pre-orders on one of the drop's products (PreorderOpen, written by the shop
  reads) after the reminder was confirmed, and still takes them at a shop on the site, while the drop is
  still to come and before the day-before email. The email names the shops that opened, with their prices;
- the day before: on the morning before a drop with a full date, from EMAIL_FROM_HOUR UK time. If the
  set's day moves after it went, it is sent again the day before the new day, so nobody is left with a
  wrong "out tomorrow";
- drop day: on the drop's own morning, from the same hour. The address is deleted once it has gone.

A month-only drop hears only about pre-orders until its date is published, and nothing once a shop sells
it from stock. A product with its own later release date is not part of the drop (drops.in_drop). A stage whose window has
passed is never sent late: a day-before email that missed its evening is skipped, not sent on the day.
Reminders are deleted unconfirmed after a week, once their drop has passed and after a year in any case.

The sign-up never says what it knows about an address: a stranger typing someone's address sees the same
"check your inbox" whether that address already has the reminder, is at its limit or is new. An address
gets at most CONFIRMS_PER_EMAIL_DAY confirmation emails a day across every set, however many connections
ask, and only confirmed reminders count towards MAX_PER_EMAIL, so a stranger cannot fill it.

send_due is safe to run every 15 minutes and from two processes at once: each stage is claimed with an
update that only one process can win before it is sent, and handed back if the send fails.
"""

import hashlib
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q, Subquery
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from content import service as copy

from . import insights, mail
from .drops import MARKETPLACES, in_drop, month_rows, month_start, next_month
from .models import DailyPageView, DropAlert, Listing, PreorderOpen

MAX_PER_EMAIL = 30            # confirmed reminders one address can wait on
CONFIRMS_PER_EMAIL_DAY = 3    # confirmation emails one address can be sent in a day, across every set
UNCONFIRMED_DAYS = 7
KEEP_DAYS = 365
RESEND_MINUTES = 10           # a second ask for the same address within this sends no second confirmation
ASKS_PER_ADDRESS_HOUR = 10    # sign-ups one internet address may make in an hour
OFFERS_PER_EMAIL = 5
EMAIL_FROM_HOUR = 8           # the day-before and drop-day emails go from 8am UK time
EMAIL_UNTIL_HOUR = 21         # and not after 9pm; a window missed is skipped
PREORDER_FROM_HOUR = 7        # pre-orders sell out fast, so that email may go a little earlier and later
PREORDER_UNTIL_HOUR = 22

PREORDER, EVE, DAY = "preorder", "eve", "day"
SENT_FIELD = {PREORDER: "preorder_sent_at", EVE: "eve_sent_at", DAY: "day_sent_at"}


def note(event):
    insights.bump(DailyPageView, kind=DailyPageView.Kind.ALERTS, key=f"drop_{event}")


def link(name, *args):
    return settings.RIPRAPTOR_SITE_URL + reverse(name, args=args)


def drop_url(product_set):
    return settings.RIPRAPTOR_SITE_URL + reverse("web:drops") + f"#drop-{product_set.pk}"


# Sign-up -----------------------------------------------------------------------------------------

def counted(kind, value, seconds):
    """Add one to a count kept in the shared cache for ``seconds`` and return it. The key is a one-way hash,
    so neither the internet address nor the email address is stored."""
    key = f"drops:{kind}:" + hashlib.sha256(f"{settings.SECRET_KEY}|{value}".encode()).hexdigest()[:24]
    cache.add(key, 0, seconds)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, seconds)
        return 1


def address_allowed(ip):
    """False once this internet address has asked ASKS_PER_ADDRESS_HOUR times in the hour."""
    return counted("ask", ip, 3600) <= ASKS_PER_ADDRESS_HOUR


def confirmation_allowed(email):
    """False once this email address has been sent CONFIRMS_PER_EMAIL_DAY confirmations in a day. Counted
    before sending, so a run of asks from many connections cannot fill one inbox."""
    return counted("confirm", email, 86400) <= CONFIRMS_PER_EMAIL_DAY


def render_mail(template, context, store=None):
    from .alerts import absolute

    context = {
        "site_name": settings.RIPRAPTOR_SITE_NAME,
        "site_url": settings.RIPRAPTOR_SITE_URL,
        "logo_url": absolute(static("img/logo.png")),
        "site_content": copy.load_all() if store is None else store,
        "preheader": "",
        **context,
    }
    return (
        render_to_string(f"web/email/{template}.txt", context).strip(),
        render_to_string(f"web/email/{template}.html", context),
    )


def ask(product_set, email, now=None):
    """Record a request and send the confirmation. Always returns "sent", whatever is known about the
    address: an address that already has this reminder, is at MAX_PER_EMAIL or has had its confirmations
    for the day is sent nothing more, and the visitor is not told which, since they may not own it."""
    now = now or timezone.now()
    email = email.strip().lower()
    alert = DropAlert.objects.filter(product_set=product_set, email=email).first()
    if alert is not None and alert.confirmed_at:
        return "sent"
    if alert is not None and alert.confirm_sent_at and alert.confirm_sent_at > now - timedelta(minutes=RESEND_MINUTES):
        # Asked again moments later: the confirmation already on its way stands, so a form pressed over and
        # over cannot fill someone's inbox.
        return "sent"
    if alert is None and DropAlert.objects.filter(email=email, confirmed_at__isnull=False).count() >= MAX_PER_EMAIL:
        return "sent"
    if not confirmation_allowed(email):
        return "sent"
    if alert is None:
        try:
            with transaction.atomic():
                alert = DropAlert.objects.create(product_set=product_set, email=email, created_at=now)
        except IntegrityError:
            # The same address asked twice at once: the first one's confirmation stands.
            return "sent"
    store = copy.load_all()
    values = {"set": product_set.name, "site_name": settings.RIPRAPTOR_SITE_NAME}
    subject = copy.get("drops.email.confirm.subject", store=store, **values)
    stop_url = link("web:drop_stop", alert.token)
    text, html = render_mail("drop_confirm", {
        "subject": subject,
        "preheader": copy.get("drops.email.confirm.preheader", store=store, **values),
        "product_set": product_set,
        "values": values,
        "confirm_url": link("web:drop_confirm", alert.token),
        "stop_url": stop_url,
    }, store)
    mail.send(email, subject, text, html, unsubscribe=stop_url)
    DropAlert.objects.filter(pk=alert.pk).update(confirm_sent_at=now)
    note("asked")
    return "sent"


def confirm(token, now=None):
    alert = DropAlert.objects.select_related("product_set__game").filter(token=token).first()
    if alert is not None and alert.confirmed_at is None:
        alert.confirmed_at = now or timezone.now()
        alert.save(update_fields=["confirmed_at"])
        note("confirmed")
    return alert


def stop(token):
    """Delete the reminder at once. Returns its set, or None when the link no longer works."""
    alert = DropAlert.objects.select_related("product_set").filter(token=token).first()
    if alert is None:
        return None
    product_set = alert.product_set
    alert.delete()
    note("stopped")
    return product_set


# Sending -----------------------------------------------------------------------------------------

def with_set_month(alerts):
    """Annotate ``set_month``: the month an accepted announcement gives the reminder's set, or None."""
    return alerts.annotate(set_month=Subquery(month_rows("product_set")))


def tidy(now):
    """Delete reminders never confirmed within a week, kept over a year, or whose drop has passed: a dated one
    after its day, a month-only one after its month."""
    today = timezone.localdate(now)
    DropAlert.objects.filter(
        Q(confirmed_at__isnull=True, created_at__lt=now - timedelta(days=UNCONFIRMED_DAYS))
        | Q(created_at__lt=now - timedelta(days=KEEP_DAYS))
        | Q(product_set__release_date__lt=today)
    ).delete()
    ended = with_set_month(DropAlert.objects.filter(product_set__release_date__isnull=True)).filter(
        set_month__lt=month_start(today)
    )
    DropAlert.objects.filter(pk__in=list(ended.values_list("pk", flat=True))).delete()


def in_window(local, start, end):
    return start <= local.hour < end


def eve_done(alert, day):
    """The day-before email has gone for this day. One sent for an earlier date the set has since moved from
    does not count."""
    return alert.eve_sent_at is not None and alert.eve_for == day


def due_stage(alert, local, opened):
    """The one stage this confirmed reminder should be sent now, or None. ``local`` is now in UK time and
    ``opened`` the pre-order openings since the reminder was confirmed that a shop still takes."""
    today = local.date()
    day = alert.product_set.release_date
    if day is not None:
        if today > day:
            return None
        if today == day:
            return DAY if alert.day_sent_at is None and in_window(local, EMAIL_FROM_HOUR, EMAIL_UNTIL_HOUR) else None
        if today == day - timedelta(days=1):
            # The day-before email lists the pre-orders too, so no separate pre-order email goes that day.
            return EVE if not eve_done(alert, day) and in_window(local, EMAIL_FROM_HOUR, EMAIL_UNTIL_HOUR) else None
    elif alert.set_month is not None and today >= next_month(alert.set_month):
        return None
    if alert.preorder_sent_at or alert.eve_sent_at or alert.day_sent_at:
        return None
    if not opened:
        return None
    return PREORDER if in_window(local, PREORDER_FROM_HOUR, PREORDER_UNTIL_HOUR) else None


def drop_month(alert):
    return None if alert.product_set.release_date else alert.set_month and month_start(alert.set_month)


def set_offers(product_set, month=None):
    """The cheapest current shop offer for each of the drop's products, cheapest first. One query. A product
    with its own later release date is left out (drops.in_drop); ``month`` is a month-only drop's month."""
    listings = (
        Listing.objects.buyable().filter(product__product_set=product_set, product__is_active=True)
        .exclude(retailer__source_type__in=MARKETPLACES)
        .select_related("retailer", "product")
        .order_by("-delivery_known", "delivered_price", "retailer__name")
    )
    best = {}
    for listing in listings:
        if in_drop(listing.product, product_set.release_date, month):
            best.setdefault(listing.product_id, listing)
    return list(best.values())


def preorder_offers(opened, listings):
    """The offers for the pre-orders email: every listing that just opened, then the cheapest others, up to
    OFFERS_PER_EMAIL, cheapest first."""
    fresh = list({o.listing_id: o.listing for o in opened}.values())
    others = [l for l in listings if l.pk not in {f.pk for f in fresh}]
    chosen = (fresh + others)[:max(OFFERS_PER_EMAIL, len(fresh))]
    return sorted(chosen, key=lambda l: (not l.delivery_known, l.shown_price, l.retailer.name))


def offer_rows(listings, store):
    """Rows for the email's price list, from listings already chosen and ordered."""
    from .alerts import price_words

    rows = []
    for listing in listings:
        rows.append({
            "product": listing.product.name,
            "price": price_words(listing) + (" delivered" if listing.delivery_known else ""),
            "retailer": listing.retailer.name,
            "preorder": listing.availability == Listing.Availability.PREORDER,
            "checked": copy.get("drops.email.checked", store=store,
                                when=date_format(timezone.localtime(listing.last_checked), "H:i, j M")),
            "url": link("web:go", listing.pk),
        })
    return rows


def stage_mail(alert, stage, listings, store, opened=()):
    """(subject, text, html) for one stage's email. ``opened`` names the shops a pre-orders email is about."""
    product_set = alert.product_set
    day = product_set.release_date
    values = {
        "set": product_set.name,
        "game": product_set.game.display_short,
        "date": date_format(day, "l j F") if day else "",
        "site_name": settings.RIPRAPTOR_SITE_NAME,
    }
    if stage == PREORDER:
        shops = sorted({o.listing.retailer.name for o in opened})
        values["names"] = ", ".join(shops[:3])
    subject = copy.get(f"drops.email.{stage}.subject", store=store, **values)
    stop_url = link("web:drop_stop", alert.token)
    text, html = render_mail("drop_reminder", {
        "subject": subject,
        "preheader": copy.get(f"drops.email.{stage}.preheader", store=store, **values),
        "stage": stage,
        "values": values,
        "product_set": product_set,
        "offers": offer_rows(listings, store),
        "drop_url": drop_url(product_set),
        "stop_url": stop_url,
        "last": stage == DAY,
    }, store)
    return subject, text, html, stop_url


def openings(alerts):
    """{set id: [PreorderOpen]}: openings at or after the earliest confirmation among ``alerts`` whose listing
    is still a current pre-order at a shop on the site (not eBay or Amazon), newest first, with the listing,
    its shop and product loaded. One query."""
    if not alerts:
        return {}
    taking = (
        Listing.objects.buyable().filter(availability=Listing.Availability.PREORDER, product__is_active=True)
        .exclude(retailer__source_type__in=MARKETPLACES)
    )
    rows = (
        PreorderOpen.objects.filter(
            product__product_set_id__in={a.product_set_id for a in alerts},
            at__gte=min(a.confirmed_at for a in alerts),
            listing__in=taking,
        )
        .select_related("listing__retailer", "listing__product")
        .order_by("-at")
    )
    found = {}
    for row in rows:
        found.setdefault(row.listing.product.product_set_id, []).append(row)
    return found


def opened_for(alert, rows):
    """The openings this reminder has not heard about: after it was confirmed, on a product in the drop."""
    month = drop_month(alert)
    return [
        row for row in rows
        if row.at > alert.confirmed_at and in_drop(row.listing.product, alert.product_set.release_date, month)
    ]


def send_due(now=None, stdout=None):
    """Send every stage that is due, at most one per reminder per run, and delete each reminder whose
    drop-day email has gone. Returns (sent, failed)."""
    now = now or timezone.now()
    tidy(now)
    local = timezone.localtime(now)
    waiting = list(with_set_month(DropAlert.objects.filter(confirmed_at__isnull=False)).select_related("product_set__game"))
    if not waiting:
        return 0, 0
    opens = openings(waiting)
    offers, store = {}, None
    sent = failed = 0
    for alert in waiting:
        opened = opened_for(alert, opens.get(alert.product_set_id, ()))
        stage = due_stage(alert, local, opened)
        if stage is None:
            continue
        if alert.product_set_id not in offers:
            offers[alert.product_set_id] = set_offers(alert.product_set, drop_month(alert))
        listings = offers[alert.product_set_id]
        if stage == PREORDER:
            if alert.product_set.release_date is None and any(
                l.availability == Listing.Availability.IN_STOCK for l in listings
            ):
                continue   # a month-only set a shop sells from stock is out: there is nothing to pre-order
            listings = preorder_offers(opened, listings)
        else:
            listings = listings[:OFFERS_PER_EMAIL]
        field = SENT_FIELD[stage]
        # Claimed first, so a second run at the same moment cannot send it too.
        claim = DropAlert.objects.filter(pk=alert.pk)
        if stage == EVE:
            day = alert.product_set.release_date
            claimed = claim.filter(Q(eve_sent_at__isnull=True) | ~Q(eve_for=day)).update(eve_sent_at=now, eve_for=day)
            handback = {"eve_sent_at": alert.eve_sent_at, "eve_for": alert.eve_for}
        else:
            claimed = claim.filter(**{f"{field}__isnull": True}).update(**{field: now})
            handback = {field: None}
        if not claimed:
            continue
        store = copy.load_all() if store is None else store
        subject, text, html, stop_url = stage_mail(alert, stage, listings, store, opened)
        try:
            mail.send(alert.email, subject, text, html, unsubscribe=stop_url)
        except mail.MailError as exc:
            DropAlert.objects.filter(pk=alert.pk).update(**handback)
            failed += 1
            if stdout:
                stdout.write(f"not sent to drop reminder {alert.pk}: {exc}")
            continue
        if stage == DAY:
            alert.delete()
        note(f"sent_{stage}")
        sent += 1
    return sent, failed
