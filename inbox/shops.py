"""
List your shop: a shop asks to be listed, the shop checker looks at its website, and the owner approves it in admin,
which adds the shop, saves its discount code and sends it one welcome email. The owner, 14 Nov 2026: "they need to
provide the info it then goes to the shop checker function we already have and is checked, need to make sure this a
cannot be abused, I will then approve it and then it sends this message via email".

What the form takes is checked here, in the order that costs least: the shape of each field, then the
sender's internet address (a one-way hashed count in the shared cache, as the drop reminders keep), then
a cap on new enquiries an hour for the whole site, then whether the same shop already has an open
enquiry. Only then is anything written.

Abuse: the website is checked by the background reader, never while the form is sent, and only on the public
internet (catalogue/shop_adding.py public_get), at most PUBLIC_CHECKS_DAY new shops a day and once per shop. Nothing
is ever emailed to the address given until the owner approves the shop, and then once (welcome_sent_at), so the form
cannot be used to send mail to a stranger. The owner sees whether the address is on the shop's own domain before
approving (email_matches).

A shop that asks again within DUPLICATE_DAYS while its first enquiry is still open is answered with the
same thanks and nothing more is stored or sent, so the page never says what it already knows.

Enquiries are deleted HANDLED_KEEP_DAYS after they are marked handled, or OPEN_KEEP_DAYS after they
arrived if they never are (purge, run by tidy_all every hour).
"""

import re
import unicodedata
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
MAX_CODE = 40
MAX_TERMS = 120
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
PUBLIC_CHECKS_DAY = 40       # new shops from the form the shop checker looks at in a day; past it, the owner checks
MAX_BODY = 8 * 1024          # bytes a POST may carry; the form's fields at their longest fit in well under this
PER_ADDRESS_HOUR = 5         # enquiries one internet address may send in an hour
PER_ADDRESS_DAY = 20         # and in a day
PER_SITE_HOUR = 30           # new enquiries the whole site takes in an hour
DUPLICATE_DAYS = 30
HANDLED_KEEP_DAYS = 365
OPEN_KEEP_DAYS = 730

LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
TLD = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{1,59}")


KEEP_JOINERS = {"\u200c", "\u200d"}   # zero width (non) joiner: needed by emoji and some scripts


def hidden(c):
    """True for a control or format character (Unicode Cc or Cf): NUL, bidi overrides such as U+202E,
    zero width spaces. They let text shown to the owner read differently from what was sent."""
    return unicodedata.category(c) in ("Cc", "Cf")


def strip_hidden(text, keep="\n"):
    """Text without control and format characters, keeping the characters in ``keep`` and the joiners."""
    return "".join(c for c in text if c in keep or c in KEEP_JOINERS or not hidden(c))


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
    if not raw or len(raw) > MAX_URL or any(c.isspace() or hidden(c) for c in raw):
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


def clean(name, shop_url, email, message, discount_code="", discount_terms=""):
    """The form's values tidied, or Invalid naming the first field that is wrong."""
    name = " ".join(strip_hidden(name or "", keep="\t\n").split())
    if not name or len(name) > MAX_NAME:
        raise Invalid("name")
    url, domain = normalise_url(shop_url)
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise Invalid("email") from None
    message = strip_hidden((message or "").replace("\r\n", "\n").replace("\r", "\n"), keep="\t\n").strip()
    if len(message) > MAX_MESSAGE:
        raise Invalid("message")
    code = (discount_code or "").strip()
    if code and (len(code) > MAX_CODE or not CODE.fullmatch(code)):
        raise Invalid("discount_code")
    terms = " ".join(strip_hidden(discount_terms or "", keep="\t\n").split()) if code else ""
    if len(terms) > MAX_TERMS:
        raise Invalid("discount_terms")
    return {"name": name, "shop_url": url, "domain": domain, "email": email, "message": message,
            "discount_code": code, "discount_terms": terms}


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
    queue_check(enquiry, now)
    tell_owner(enquiry)
    return enquiry


def queue_check(enquiry, now=None):
    """Hand the shop's website to the shop checker, held for the owner's approval. A shop it has looked at before keeps
    that look. Past PUBLIC_CHECKS_DAY new shops in a day nothing is queued: the owner presses Check in admin."""
    from catalogue import shop_adding
    from catalogue.models import ShopToAdd

    now = now or timezone.now()
    row = ShopToAdd.objects.filter(host=enquiry.domain).first()
    if row is None:
        if ShopToAdd.objects.filter(hold=True, created_at__gte=now - timedelta(days=1)).count() >= PUBLIC_CHECKS_DAY:
            return None
        row = ShopToAdd.objects.create(host=enquiry.domain, address=shop_adding.base_of(enquiry.shop_url), hold=True,
                                       created_at=now)
    ShopEnquiry.objects.filter(pk=enquiry.pk).update(shop_check=row)
    enquiry.shop_check = row
    return row


def check_again(enquiry):
    """Look at the shop's website again (a shop that has not passed, or was never queued). Returns the row."""
    from catalogue.models import ShopToAdd

    row = enquiry.shop_check or queue_check(enquiry)
    if row is None:
        from catalogue import shop_adding

        row, _ = ShopToAdd.objects.get_or_create(host=enquiry.domain, defaults={
            "address": shop_adding.base_of(enquiry.shop_url), "hold": True})
        ShopEnquiry.objects.filter(pk=enquiry.pk).update(shop_check=row)
        enquiry.shop_check = row
    if row.status in (ShopToAdd.Status.NOT_ADDED, ShopToAdd.Status.WAITING):
        ShopToAdd.objects.filter(pk=row.pk).update(status=ShopToAdd.Status.WAITING, note="", checked_at=None)
    return row


def email_matches(enquiry):
    """Whether the address is on the shop's own domain (info@shop.co.uk for shop.co.uk): a sign it is theirs."""
    domain = enquiry.email.rpartition("@")[2].lower()
    return domain == enquiry.domain or domain.endswith("." + enquiry.domain)


# Approving -------------------------------------------------------------------------------------------------------

class NotReady(Exception):
    """Why a shop cannot be approved yet, worded for the owner."""


def shop_name(enquiry):
    """The shop's name as the welcome email gives it: the shop's own, else what the check found, else its domain."""
    if enquiry.retailer_id:
        return enquiry.retailer.name
    row = enquiry.shop_check
    return (row.found_name if row else "") or enquiry.domain


def welcome(enquiry, store=None):
    """(subject, body) of the welcome email (content/registry.py listshop.welcome.*)."""
    from content import service as copy

    shop = shop_name(enquiry)
    badge_url = settings.RIPRAPTOR_SITE_URL + reverse("web:list_shop") + "#badge"
    discount = copy.get("listshop.welcome.discount", store=store) if enquiry.discount_code else ""
    subject = copy.get("listshop.welcome.subject", store=store, shop=shop)
    body = copy.get("listshop.welcome.body", store=store, shop=shop, badge_url=badge_url, discount_line=discount)
    return subject, re.sub(r"[ \t]+\n", "\n", body)


def approve(enquiry, now=None):
    """Approve a shop that asked and passed its check: add it (or find it, if it is already on RipRaptor), save its
    discount code, keep its address for Shop outreach, mark the enquiry handled, then send the welcome email once.
    Returns (shop, why the email was not sent or ""). Raises NotReady, changing nothing, when it cannot be approved."""
    from django.db import transaction

    from catalogue import shop_adding
    from catalogue.models import ShopToAdd

    from .models import ShopContact

    now = now or timezone.now()
    shop = enquiry.retailer
    if shop is None:
        row = enquiry.shop_check
        if row is None:
            raise NotReady("Its website has not been checked yet. Press Check the website.")
        row.refresh_from_db()
        if row.status == ShopToAdd.Status.WAITING:
            raise NotReady("The check has not finished yet. Try again in a minute or two.")
        if row.status == ShopToAdd.Status.NOT_ADDED:
            raise NotReady(f"It did not pass the check: {row.note.rstrip('.')}. Add it by hand under Retailers if you "
                           "still want it, or press Check the website to try again.")
        if row.status == ShopToAdd.Status.PASSED:
            row = shop_adding.add_passed(row)
            if row.status == ShopToAdd.Status.NOT_ADDED:
                raise NotReady(row.note)
        shop = row.retailer or shop_adding.retailer_for(row.host)
        if shop is None:
            raise NotReady("The shop could not be found on RipRaptor. Add it by hand under Retailers.")
    with transaction.atomic():
        if enquiry.discount_code:
            shop.discount_code, shop.discount_terms, shop.discount_ends = (
                enquiry.discount_code, enquiry.discount_terms, None)
            shop.save(update_fields=["discount_code", "discount_terms", "discount_ends"])
        contact, _ = ShopContact.objects.get_or_create(retailer=shop)
        if not contact.email:
            contact.email = enquiry.email
            contact.save(update_fields=["email"])
        enquiry.retailer = shop
        enquiry.approved_at = enquiry.approved_at or now
        enquiry.handled_at = enquiry.handled_at or now
        enquiry.save(update_fields=["retailer", "approved_at", "handled_at"])
    return shop, send_welcome(enquiry, now)


def send_welcome(enquiry, now=None):
    """Send the welcome email from the owner, as the Sending page says (inbox/sending.py), once. Returns "" when sent,
    else why not, worded for the owner. It counts against the day's shop emails and shows in the shop's history."""
    from . import sending
    from .models import OutreachEmail, ShopContact

    now = now or timezone.now()
    if enquiry.welcome_sent_at:
        return "The welcome email was already sent."
    if enquiry.retailer_id is None:
        return "Approve the shop first."
    contact = ShopContact.objects.filter(retailer_id=enquiry.retailer_id).first()
    if contact is not None and contact.status == ShopContact.Status.DO_NOT_EMAIL:
        return "The shop asked not to be emailed, so no welcome email was sent."
    if sending.room_today(now=now) <= 0:
        return "Today's limit of shop emails is reached. Press Send the welcome email tomorrow."
    subject, body = welcome(enquiry)
    try:
        sending.send(enquiry.email, subject, body)
    except sending.SendError as exc:
        return str(exc)
    ShopEnquiry.objects.filter(pk=enquiry.pk).update(welcome_sent_at=now)
    enquiry.welcome_sent_at = now
    if contact is None:
        contact, _ = ShopContact.objects.get_or_create(retailer_id=enquiry.retailer_id)
    OutreachEmail.objects.create(contact=contact, kind="welcome", subject=subject[:200], sent_at=now)
    return ""


def owner_address():
    """Where the owner reads shop enquiries: RIPRAPTOR_CONTACT_EMAIL when set, else the inbox address."""
    return settings.RIPRAPTOR_CONTACT_EMAIL or settings.RIPRAPTOR_INBOX_NOTIFY_EMAIL


def tell_owner(enquiry, opener=None):
    """Best effort, through the same mailer as Message us. The email is plain text (escaped in its HTML
    part, the website never made a link); the push names nothing, since ntfy.sh topics are public."""
    code = f"{enquiry.discount_code} ({enquiry.discount_terms})" if enquiry.discount_terms else enquiry.discount_code
    detail = (
        f"Name: {enquiry.name}\n"
        f"Shop website (typed by them): {enquiry.shop_url}\n"
        f"Email: {enquiry.email}{'' if email_matches(enquiry) else ' (not on the shop website domain)'}\n"
        f"Discount code: {code or '(none)'}\n"
        f"Message: {enquiry.message or '(none)'}\n\n"
        "Nothing has been sent to them. The shop checker looks at the website within a few minutes. Open it in "
        "admin to see what it found, then Approve it: that adds the shop, saves the code and sends them the "
        "welcome email."
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
