"""
List your shop: a shop asks to be listed, the owner reads it in admin and answers from their own email.

What the form takes is checked here, in the order that costs least: the shape of each field, then the
sender's internet address (a one-way hashed count in the shared cache, as the drop reminders keep), then
a cap on new enquiries an hour for the whole site, then whether the same shop already has an open
enquiry. Only then is anything written.

Nothing is ever emailed to the address given, so the form cannot be used to send mail to a stranger. The
shop's website is stored as text and never fetched: a shop is only read once the owner adds it.

A shop that asks again within DUPLICATE_DAYS while its first enquiry is still open is answered with the
same thanks and nothing more is stored or sent, so the page never says what it already knows.

Enquiries are deleted HANDLED_KEEP_DAYS after they are marked handled, or OPEN_KEEP_DAYS after they
arrived if they never are (purge, run by tidy_all every hour).
"""

import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.urls import reverse
from django.utils import timezone

from catalogue import notify

from .models import ShopEnquiry

MAX_NAME = 80
MAX_URL = 300
MAX_MESSAGE = 1000
MAX_BODY = 8 * 1024          # bytes a POST may carry; the form's fields at their longest fit in well under this
PER_ADDRESS_HOUR = 5         # enquiries one internet address may send in an hour
PER_ADDRESS_DAY = 20         # and in a day
PER_SITE_HOUR = 30           # new enquiries the whole site takes in an hour
DUPLICATE_DAYS = 30
HANDLED_KEEP_DAYS = 365
OPEN_KEEP_DAYS = 730

LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
TLD = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{1,59}")


class Invalid(Exception):
    """A field does not look right. ``args[0]`` is the field's name."""


class Refused(Exception):
    """Too many enquiries for now."""


def normalise_url(raw):
    """The shop's website as one tidy address, and its domain without www.

    A missing scheme is taken as https. Only http and https are allowed, with a real domain name (two or more
    labels and an alphabetic top level), no user name or password and no unusual port. The host is lower
    cased, a trailing dot, a default port and any #fragment are dropped. Raises Invalid("shop_url")."""
    raw = (raw or "").strip()
    if not raw or len(raw) > MAX_URL or any(c.isspace() for c in raw):
        raise Invalid("shop_url")
    if "://" not in raw:
        if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) and not re.match(r"^[^:/]+:\d", raw):
            raise Invalid("shop_url")   # mailto:, javascript: and the like, but not "shop.co.uk:443"
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise Invalid("shop_url") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or parts.username or parts.password:
        raise Invalid("shop_url")
    if port not in (None, 80, 443):
        raise Invalid("shop_url")
    host = (parts.hostname or "").rstrip(".")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise Invalid("shop_url") from None
    labels = host.split(".")
    if len(labels) < 2 or not all(LABEL.fullmatch(label) for label in labels) or not TLD.fullmatch(labels[-1]):
        raise Invalid("shop_url")
    url = f"{scheme}://{host}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    try:
        URLValidator(schemes=["http", "https"])(url)
    except ValidationError:
        raise Invalid("shop_url") from None
    if len(url) > MAX_URL:
        raise Invalid("shop_url")
    domain = host[4:] if host.startswith("www.") and len(labels) > 2 else host
    return url, domain


def clean(name, shop_url, email, message):
    """The form's values tidied, or Invalid naming the first field that is wrong."""
    name = " ".join((name or "").split())
    if not name or len(name) > MAX_NAME:
        raise Invalid("name")
    url, domain = normalise_url(shop_url)
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise Invalid("email") from None
    message = (message or "").strip().replace("\r\n", "\n")
    if len(message) > MAX_MESSAGE:
        raise Invalid("message")
    return {"name": name, "shop_url": url, "domain": domain, "email": email, "message": message}


def address_allowed(ip):
    """False once this internet address has sent PER_ADDRESS_HOUR enquiries in the hour or PER_ADDRESS_DAY
    in the day. Counted in the shared cache under a one-way hash, so the address itself is not kept."""
    from catalogue.drop_alerts import counted

    hour = counted("shop_hour", ip, 3600)
    day = counted("shop_day", ip, 86400)
    return hour <= PER_ADDRESS_HOUR and day <= PER_ADDRESS_DAY


def ask(values, ip, now=None):
    """Store the enquiry and tell the owner. Returns the new ShopEnquiry, or None when this shop already has
    an open one from the last DUPLICATE_DAYS (nothing stored, nothing sent). Raises Refused when the sender or
    the site has sent too many."""
    now = now or timezone.now()
    if not address_allowed(ip):
        raise Refused
    if ShopEnquiry.objects.filter(created_at__gte=now - timedelta(hours=1)).count() >= PER_SITE_HOUR:
        raise Refused
    if ShopEnquiry.objects.filter(domain=values["domain"], handled_at__isnull=True,
                                  created_at__gte=now - timedelta(days=DUPLICATE_DAYS)).exists():
        return None
    enquiry = ShopEnquiry.objects.create(created_at=now, **values)
    tell_owner(enquiry)
    return enquiry


def owner_address():
    """Where the owner reads shop enquiries: RIPRAPTOR_CONTACT_EMAIL when set, else the inbox address."""
    return settings.RIPRAPTOR_CONTACT_EMAIL or settings.RIPRAPTOR_INBOX_NOTIFY_EMAIL


def tell_owner(enquiry, opener=None):
    """Best effort, through the same mailer as Message us. The email is plain text (escaped in its HTML
    part, the website never made a link); the push names nothing, since ntfy.sh topics are public."""
    detail = (
        f"Name: {enquiry.name}\n"
        f"Shop website (typed by them, not checked): {enquiry.shop_url}\n"
        f"Email: {enquiry.email}\n"
        f"Message: {enquiry.message or '(none)'}\n\n"
        "Nothing has been sent to them. To answer, write to them from your own email. The shop is not read "
        "until you add it."
    )
    notify.owner(
        f"Shop asking to be listed: {enquiry.domain}", "A shop asks to be listed",
        reverse("admin:inbox_shopenquiry_change", args=[enquiry.pk]),
        detail=detail, once_a_day=False, link_label="Open in admin", tags="shopping", opener=opener,
        to=owner_address(),
    )


def purge(now=None):
    """Delete enquiries HANDLED_KEEP_DAYS after they were handled, and unhandled ones OPEN_KEEP_DAYS after
    they arrived. Returns how many went."""
    from django.db.models import Q

    now = now or timezone.now()
    gone, _ = ShopEnquiry.objects.filter(
        Q(handled_at__lt=now - timedelta(days=HANDLED_KEEP_DAYS))
        | Q(handled_at__isnull=True, created_at__lt=now - timedelta(days=OPEN_KEEP_DAYS))
    ).delete()
    return gone


def open_count():
    return ShopEnquiry.objects.filter(handled_at__isnull=True).count()
