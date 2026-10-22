"""
Back-in-stock and price email alerts: sign up, confirm, send once, forget.

An alert asked for since settings.RIPRAPTOR_PREORDER_ALERTS_FROM is also sent, once, when a shop opens
pre-orders and no shop has the product in stock; that one email ends it like any other.

A price alert (Track price on the product page) has a price and a time to keep looking: it is sent once
when a shop or eBay (never Amazon) has the product in stock for that price or less delivered, and deleted
unsent when the time is up. Its email names the price and RipRaptor's page, never the shop.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from content import service as copy

from . import insights, mail
from .images import EMAIL_WIDTH, sized_image_url
from .manage_alerts import mail_link
from .models import DailyPageView, Listing, Retailer, StockAlert

MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)
# A price alert follows eBay's price too (new, buy it now, UK sellers with good feedback: catalogue/ebay.py), at the
# owner's request, but never Amazon's: Amazon's UK Associates policies forbid price alerting on its prices without
# its written agreement (README, Amazon Associates rules). Back in stock still means a shop, not a marketplace.
NOT_PRICE_ALERTED = (Retailer.Source.AMAZON,)
MAX_PER_EMAIL = 30
UNCONFIRMED_DAYS = 7
CONFIRMED_DAYS = 183


def note(event):
    insights.bump(DailyPageView, kind=DailyPageView.Kind.ALERTS, key=event)


def link(name, *args):
    return settings.RIPRAPTOR_SITE_URL + reverse(name, args=args)


IN_STOCK = "in_stock"
PREORDER = "preorder"
PRICE = "price"

# How long a price alert keeps looking, in days, as the form offers them. The longest is the six months any
# confirmed alert is kept.
TRACK_DAYS = [(7, "1 week"), (30, "1 month"), (91, "3 months"), (CONFIRMED_DAYS, "6 months")]
TRACK_DEFAULT_DAYS = 30


def preorder_from():
    """The moment pre-order alerts began (settings.RIPRAPTOR_PREORDER_ALERTS_FROM), or None when they are off."""
    return settings.RIPRAPTOR_PREORDER_ALERTS_FROM


def wants_preorder(alert):
    """True when this alert was asked for under the form that promised pre-orders too. Earlier alerts
    asked for back in stock only and hear only that."""
    start = preorder_from()
    return start is not None and alert.created_at >= start


def preorder_alerts_on(now=None):
    """True when an alert asked for now would also hear about pre-orders: the form says so only then."""
    start = preorder_from()
    return start is not None and (now or timezone.now()) >= start


def shop_stock(product, allow_preorder=False):
    """(listing, kind) for the cheapest in-stock listing at a shop (never a marketplace), kind "in_stock".
    With allow_preorder and no shop in stock, the cheapest shop pre-order instead, kind "preorder".
    (None, None) when there is neither. One query."""
    wanted = [Listing.Availability.IN_STOCK, Listing.Availability.PREORDER] if allow_preorder else [Listing.Availability.IN_STOCK]
    listing = (
        Listing.objects.filter(product=product).buyable()
        .filter(availability__in=wanted)
        .exclude(retailer__source_type__in=MARKETPLACES)
        .select_related("retailer")
        # "in_stock" sorts before "preorder": any shop in stock wins over every pre-order.
        .order_by("availability", "-delivery_known", "delivered_price").first()
    )
    if listing is None:
        return None, None
    return listing, IN_STOCK if listing.availability == Listing.Availability.IN_STOCK else PREORDER


def price_listing(product):
    """The listing a price alert compares with: the cheapest in stock at a shop or on eBay (never Amazon,
    NOT_PRICE_ALERTED) whose delivery is known, so its delivered price is the whole price. None when there is
    none. One query."""
    return (
        Listing.objects.filter(product=product).buyable()
        .filter(availability=Listing.Availability.IN_STOCK, delivery_known=True)
        .exclude(retailer__source_type__in=NOT_PRICE_ALERTED)
        .order_by("delivered_price").first()
    )


def watch_ends(alert):
    """When a confirmed price alert stops looking, or None for an alert kept the usual six months."""
    if alert.watch_days and alert.confirmed_at:
        return alert.confirmed_at + timedelta(days=alert.watch_days)
    return None


def price_words(listing):
    """"£34.99" delivered, or "£29.99 plus delivery" when the shop's delivery charge is not known."""
    return f"£{listing.delivered_price}" if listing.delivery_known else f"£{listing.price} plus delivery"


def absolute(url):
    if not url:
        return ""
    return url if url.startswith(("http://", "https://")) else settings.RIPRAPTOR_SITE_URL + url


def render_mail(template, context, store=None):
    """(text, html) for one email. ``store`` is the site wording, loaded once for a run of emails."""
    from django.templatetags.static import static

    product = context.get("product")
    context = {
        "site_name": settings.RIPRAPTOR_SITE_NAME,
        "site_url": settings.RIPRAPTOR_SITE_URL,
        "logo_url": absolute(static("img/logo.png")),
        "image_url": absolute(sized_image_url(product.image_src, EMAIL_WIDTH)) if product is not None else "",
        "stop_url": settings.RIPRAPTOR_SITE_URL + "/",
        "site_content": copy.load_all() if store is None else store,
        "preheader": "",
        **context,
    }
    return (
        render_to_string(f"web/email/{template}.txt", context).strip(),
        render_to_string(f"web/email/{template}.html", context),
    )


def ask(product, email, max_price=None, days=None):
    """Record a request and send the confirmation. Returns "sent", "already" or "limit". With ``max_price``
    it is a price alert that keeps looking for ``days`` once confirmed; asked again before it is confirmed,
    the newest price and time replace the earlier ones."""
    email = email.strip().lower()
    existing = StockAlert.objects.filter(product=product, email=email).first()
    if existing and existing.confirmed_at:
        return "already"
    terms = {"max_price": max_price, "watch_days": days if max_price is not None else None}
    if existing is None:
        if StockAlert.objects.filter(email=email).count() >= MAX_PER_EMAIL:
            return "limit"
        existing = StockAlert.objects.create(product=product, email=email, **terms)
    elif (existing.max_price, existing.watch_days) != (terms["max_price"], terms["watch_days"]):
        StockAlert.objects.filter(pk=existing.pk).update(**terms)
        existing.max_price, existing.watch_days = terms["max_price"], terms["watch_days"]
    preorder = wants_preorder(existing) and max_price is None
    if max_price is not None:
        preheader = f"One click and we will email you if it is £{max_price} or less delivered."
    elif preorder:
        preheader = "One click and we will email you when a shop has it."
    else:
        preheader = "One click and we will email you when it is back."
    text, html = render_mail("confirm", {
        "product": product,
        "subject": f"Confirm your alert for {product.name}",
        "preheader": preheader,
        "preorder": preorder,
        "max_price": max_price,
        "watch_label": dict(TRACK_DAYS).get(days, "") if max_price is not None else "",
        "confirm_url": link("web:alert_confirm", existing.token),
        "stop_url": link("web:alert_stop", existing.token),
        "manage_url": mail_link(email),
    })
    mail.send(email, f"Confirm your alert for {product.name}", text, html, unsubscribe=link("web:alert_stop", existing.token))
    note("asked")
    return "sent"


def confirm(token):
    alert = StockAlert.objects.select_related("product").filter(token=token).first()
    if alert is not None and alert.confirmed_at is None:
        alert.confirmed_at = timezone.now()
        alert.save(update_fields=["confirmed_at"])
        note("confirmed")
    return alert


def stop(token):
    alert = StockAlert.objects.select_related("product").filter(token=token).first()
    if alert is not None:
        product = alert.product
        alert.delete()
        note("stopped")
        return product
    return None


def send_due(now=None, stdout=None):
    """Email every confirmed alert whose product a shop now has in stock (for a price alert, for its price
    or less delivered), or, for a back-in-stock alert asked for since pre-order alerts began, has opened for
    pre-order, then forget the address. One email per alert. A price alert whose time is up is deleted."""
    now = now or timezone.now()
    StockAlert.objects.filter(
        Q(confirmed_at__isnull=True, created_at__lt=now - timedelta(days=UNCONFIRMED_DAYS))
        | Q(confirmed_at__lt=now - timedelta(days=CONFIRMED_DAYS))
    ).delete()
    sent = failed = 0
    waiting = StockAlert.objects.filter(confirmed_at__isnull=False).select_related("product")
    stock, priced, store = {}, {}, None
    for alert in waiting:
        ends = watch_ends(alert)
        if ends is not None and ends <= now:
            # Looked for as long as the visitor asked: the address goes, unsent.
            alert.delete()
            note("expired")
            continue
        if alert.max_price is not None:
            if alert.product_id not in priced:
                priced[alert.product_id] = price_listing(alert.product)
            listing, kind = priced[alert.product_id], PRICE
            if listing is None or listing.delivered_price > alert.max_price:
                continue
        else:
            allow_preorder = wants_preorder(alert)
            key = (alert.product_id, allow_preorder)
            if key not in stock:
                stock[key] = shop_stock(alert.product, allow_preorder=allow_preorder)
            listing, kind = stock[key]
            if listing is None:
                continue
        product_url = settings.RIPRAPTOR_SITE_URL + alert.product.get_absolute_url()
        stop_url = link("web:alert_stop", alert.token)
        if kind == PRICE:
            # The price and RipRaptor's page, not the shop: the page shows every shop's price now.
            subject = f"Under your price: {alert.product.name}, £{listing.delivered_price} delivered"
            template = "price_drop"
        elif kind == PREORDER:
            subject = f"Pre-orders open: {alert.product.name}, {price_words(listing)} at {listing.retailer.name}"
            template = "preorder_open"
        else:
            subject = f"Back in stock: {alert.product.name}"
            template = "back_in_stock"
        if kind == PRICE:
            preheader = f"In stock for £{listing.delivered_price} delivered, at or under your £{alert.max_price}. Checked just now."
        else:
            preheader = f"{price_words(listing)}{' delivered' if listing.delivery_known else ''} at {listing.retailer.name}, checked just now."
        store = copy.load_all() if store is None else store
        text, html = render_mail(template, {
            "max_price": alert.max_price,
            "product": alert.product,
            "subject": subject,
            "preheader": preheader,
            "listing": listing,
            "product_url": product_url,
            "stop_url": stop_url,
            "manage_url": mail_link(alert.email),
        }, store)
        try:
            mail.send(alert.email, subject, text, html, unsubscribe=stop_url)
        except mail.MailError as exc:
            failed += 1
            if stdout:
                stdout.write(f"not sent to alert {alert.pk}: {exc}")
            continue
        alert.delete()
        note("sent")
        sent += 1
    return sent, failed
