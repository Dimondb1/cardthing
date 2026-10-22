"""
Your alerts: one page to see and stop every email alert an address has, with no account and no password.

On the watchlist page a visitor types their address and presses "Email me a link". If the address has any
alert (back in stock, Track price, a drop reminder or a search alert, confirmed or not) it is emailed a link
to a page listing them all, each with a Stop button and "Stop all" at the top. An address with no alerts is
sent nothing. The answer on the page is the same either way, and a failed send is not told apart either, so
the form never says whether an address is known.

The link is signed (django.core.signing) and carries the address, lower-cased. Nothing new is stored, so a
link cannot be revoked; it simply expires. A link asked for on the watchlist page works for ASKED_MAX_AGE
(an hour). Every alert email carries one too ("Manage all your alerts") that works for EMAILED_MAX_AGE (a
week). The two are signed under different salts, so an hour's link is never read as a week's. Stopping
deletes an alert only when its address is the link's, whatever id the form sends.

Requests are counted like the other sign-ups (drop_alerts.counted: one-way hashes in the shared cache, so
neither address is stored): one connection can ask ASKS_PER_ADDRESS_HOUR times an hour, and one address is
sent at most LINKS_PER_EMAIL_DAY links a day, counted for every address typed, known or not.
"""

import logging

from django.conf import settings
from django.core import signing
from django.urls import reverse

from content import service as copy

from . import insights, mail
from .models import DailyPageView, DropAlert, SearchAlert, StockAlert

logger = logging.getLogger(__name__)

ASKED_SALT = "ripraptor.alerts.manage.asked"
EMAILED_SALT = "ripraptor.alerts.manage.emailed"
ASKED_MAX_AGE = 60 * 60              # a link asked for on the watchlist page: an hour
EMAILED_MAX_AGE = 7 * 24 * 60 * 60   # the link at the foot of every alert email: a week
ASKS_PER_ADDRESS_HOUR = 10           # links one internet address may ask for in an hour
LINKS_PER_EMAIL_DAY = 3              # links one email address may be sent in a day

# What a Stop button names: the alert's kind and its id ("stock-12"). Back in stock and Track price are both
# StockAlerts.
MODELS = {"stock": StockAlert, "drop": DropAlert, "search": SearchAlert}


def note(event):
    insights.bump(DailyPageView, kind=DailyPageView.Kind.ALERTS, key=f"manage_{event}")


def normal(email):
    return (email or "").strip().lower()


# The link -------------------------------------------------------------------------------------------

def token_for(email, emailed=False):
    """A signed token carrying the address: an hour's (asked for on the page) or, with ``emailed``, a week's."""
    return signing.dumps({"e": normal(email)}, salt=EMAILED_SALT if emailed else ASKED_SALT, compress=True)


def email_for(token):
    """The address a link was made for, or None when the token is not one of ours or has expired."""
    for salt, age in ((ASKED_SALT, ASKED_MAX_AGE), (EMAILED_SALT, EMAILED_MAX_AGE)):
        try:
            data = signing.loads(token, salt=salt, max_age=age)
        except signing.BadSignature:   # an expired one is a BadSignature too
            continue
        email = data.get("e") if isinstance(data, dict) else None
        if isinstance(email, str) and email:
            return email
    return None


def page_url(email, emailed=False):
    return settings.RIPRAPTOR_SITE_URL + reverse("web:alerts_manage", args=[token_for(email, emailed)])


def mail_link(email):
    """The "Manage all your alerts" link for the foot of an alert email: works for a week."""
    return page_url(email, emailed=True)


# What an address has --------------------------------------------------------------------------------

def find(email):
    """{"stock": [...], "drop": [...], "search": [...]}: every alert for this address, confirmed or not,
    newest first, with what the page shows loaded. One query each."""
    from .drop_alerts import with_set_month

    email = normal(email)
    return {
        "stock": list(StockAlert.objects.filter(email=email).select_related("product")),
        "drop": list(with_set_month(DropAlert.objects.filter(email=email)).select_related("product_set__game")),
        "search": list(SearchAlert.objects.filter(email=email).select_related("game")),
    }


def has_any(email):
    email = normal(email)
    return any(model.objects.filter(email=email).exists() for model in MODELS.values())


def stop(email, kind, pk):
    """Delete one alert, only when it belongs to this address. Returns True when one was deleted."""
    model = MODELS.get(kind)
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return False
    if model is None:
        return False
    deleted, _ = model.objects.filter(pk=pk, email=normal(email)).delete()
    if deleted:
        note("stopped")
    return bool(deleted)


def stop_all(email):
    """Delete every alert for this address. Returns how many alerts went (not what was deleted with them)."""
    email = normal(email)
    count = 0
    for model in MODELS.values():
        _, per_model = model.objects.filter(email=email).delete()
        count += per_model.get(model._meta.label, 0)
    if count:
        note("stopped_all")
    return count


# Asking for a link ----------------------------------------------------------------------------------

def address_allowed(ip):
    """False once this internet address has asked ASKS_PER_ADDRESS_HOUR times in the hour."""
    from .drop_alerts import counted

    return counted("manage_ask", ip, 3600) <= ASKS_PER_ADDRESS_HOUR


def link_allowed(email):
    """False once this email address has been sent LINKS_PER_EMAIL_DAY links in a day. Counted for every
    address asked about, so the count says nothing about which addresses have alerts."""
    from .drop_alerts import counted

    return counted("manage_link", normal(email), 86400) <= LINKS_PER_EMAIL_DAY


def ask(email):
    """Email a link to this address's alerts when it has any. Returns True when one was sent; the visitor is
    never told, and a send that fails is logged rather than raised, so the answer cannot differ."""
    from .drop_alerts import render_mail

    email = normal(email)
    note("asked")
    if not link_allowed(email) or not has_any(email):
        return False
    store = copy.load_all()
    values = {"site_name": settings.RIPRAPTOR_SITE_NAME}
    subject = copy.get("watchlist.manage.email.subject", store=store, **values)
    text, html = render_mail("manage_link", {
        "subject": subject,
        "preheader": copy.get("watchlist.manage.email.preheader", store=store, **values),
        "values": values,
        "page_url": page_url(email),
    }, store)
    try:
        mail.send(email, subject, text, html)
    except mail.MailError as exc:
        logger.warning("Alerts link not sent: %s", exc)
        return False
    note("sent")
    return True
