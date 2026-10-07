"""
Back-in-stock email alerts: sign up, confirm, send once, forget.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import insights, mail
from .models import DailyPageView, Listing, Retailer, StockAlert

MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)
MAX_PER_EMAIL = 30
UNCONFIRMED_DAYS = 7
CONFIRMED_DAYS = 183


def note(event):
    insights.bump(DailyPageView, kind=DailyPageView.Kind.ALERTS, key=event)


def link(name, *args):
    return settings.RIPRAPTOR_SITE_URL + reverse(name, args=args)


def shop_stock(product):
    """The cheapest in-stock listing at a shop (not a marketplace), or None."""
    return (
        Listing.objects.filter(product=product).buyable()
        .filter(availability=Listing.Availability.IN_STOCK)
        .exclude(retailer__source_type__in=MARKETPLACES)
        .select_related("retailer").order_by("delivered_price").first()
    )


def render_mail(template, context):
    context = {**context, "site_name": settings.RIPRAPTOR_SITE_NAME}
    return (
        render_to_string(f"web/email/{template}.txt", context).strip(),
        render_to_string(f"web/email/{template}.html", context),
    )


def ask(product, email):
    """Record a request and send the confirmation. Returns "sent", "already" or "limit"."""
    email = email.strip().lower()
    existing = StockAlert.objects.filter(product=product, email=email).first()
    if existing and existing.confirmed_at:
        return "already"
    if existing is None:
        if StockAlert.objects.filter(email=email).count() >= MAX_PER_EMAIL:
            return "limit"
        existing = StockAlert.objects.create(product=product, email=email)
    text, html = render_mail("confirm", {
        "product": product,
        "confirm_url": link("web:alert_confirm", existing.token),
        "stop_url": link("web:alert_stop", existing.token),
    })
    mail.send(email, f"Confirm your alert for {product.name}", text, html)
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
    """Email every confirmed alert whose product a shop now has in stock, then forget the address."""
    now = now or timezone.now()
    StockAlert.objects.filter(
        Q(confirmed_at__isnull=True, created_at__lt=now - timedelta(days=UNCONFIRMED_DAYS))
        | Q(confirmed_at__lt=now - timedelta(days=CONFIRMED_DAYS))
    ).delete()
    sent = failed = 0
    waiting = StockAlert.objects.filter(confirmed_at__isnull=False).select_related("product")
    stock = {}
    for alert in waiting:
        if alert.product_id not in stock:
            stock[alert.product_id] = shop_stock(alert.product)
        listing = stock[alert.product_id]
        if listing is None:
            continue
        text, html = render_mail("back_in_stock", {
            "product": alert.product,
            "listing": listing,
            "product_url": settings.RIPRAPTOR_SITE_URL + alert.product.get_absolute_url(),
            "stop_url": link("web:alert_stop", alert.token),
        })
        try:
            mail.send(alert.email, f"Back in stock: {alert.product.name}", text, html)
        except mail.MailError as exc:
            failed += 1
            if stdout:
                stdout.write(f"not sent to alert {alert.pk}: {exc}")
            continue
        alert.delete()
        note("sent")
        sent += 1
    return sent, failed
