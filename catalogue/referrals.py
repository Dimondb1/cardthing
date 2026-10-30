"""
Refer a friend: both people get a free RipRaptor 3D keyring, UK only, whilst stocks last.

The flow. RipRaptor never emails a friend on someone else's behalf: each person gives their own address, and an
address someone typed in is sent one confirmation email and nothing else until its owner confirms it.

1. The referrer gives their own email address on /refer/ and ticks the terms. We email a confirm link; once
   pressed, their page and a second email give them their own link, /r/<code>/, to share themselves.
2. A friend opens the link and gives their own email address. We email them a confirm link, which opens a page
   with a Claim my keyring button (a button, so a mail scanner opening the link cannot claim). Pressing it
   completes the referral: two keyrings are reserved, the owner is told, and, unless it is held for review,
   both people are emailed a private link to a postal address form (valid for 30 days).
3. The owner sees addresses under "Keyrings to post" in admin and marks them posted.

Rules against abuse, all checked on the server:

- one keyring per email address ever and per postal address (postcode and house and flat numbers), kept after
  the addresses are deleted as PromoMark fingerprints; plus addressing (any domain) and dots (Gmail) are ignored
  when comparing email addresses. A second address at a postcode that has had a keyring is marked for the owner
  to check before posting, since a flat number is easy to add;
- a friend's referral stays with the first referrer whose link they used, so typing someone's address into a
  second link cannot take the credit;
- a referrer earns a keyring for at most ReferralSettings.max_per_referrer completed referrals;
- the friend must differ from the referrer and be new to the offer;
- a friend on the same connection or internet address as the referrer, or a second claim from one internet
  address within REVIEW_DAYS, or many referrers from one address, holds the referral "for review": nothing is
  sent until the owner approves it, as is every referral beyond BURST_PER_HOUR completed across the site in
  an hour, so a scripted run of fresh addresses cannot empty the stock unseen;
- keyrings promised but never posted (an address link unused, a cancelled or deleted referral, an address left
  unposted for UNPOSTED_KEEP_DAYS) go back to the stock count;
- every form answers the same whether or not an address is known, so it cannot be used to find out who
  takes part. Cheap checks come first (body size in web.middleware, the hidden field, whether the offer is open,
  the per-connection count in the cache) so a flood costs little: no database write and no email.

Connection fingerprints are one-way hashes of the internet address (IPv6 by its /64, so rotating addresses in
one block count as one) and browser, never stored raw, and deleted with the rows they belong to.
"""

import hashlib
import hmac
import ipaddress
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from content import service as copy

from . import mail, notify
from .models import KeyringClaim, PromoMark, Referral, ReferralSettings, Referrer

logger = logging.getLogger(__name__)

UNCONFIRMED_DAYS = 14         # a sign-up or friend never confirmed is deleted after this
UNUSED_LINK_DAYS = 90         # a referrer's link that brought no completed referral is deleted this long after confirming
ADDRESS_LINK_DAYS = 30        # an address link works this long
UNCLAIMED_DAYS = 14           # an unused address link is deleted this long after it expired
POSTED_KEEP_DAYS = 60         # a name and address are deleted this long after posting
UNPOSTED_KEEP_DAYS = 90       # a name and address never marked posted are deleted this long after they were sent
REMIND_AFTER_DAYS = 14        # the owner is reminded, once a day, of addresses waiting this long to be posted
CANCELLED_KEEP_DAYS = 14      # a cancelled referral is deleted this long after cancelling
REVIEW_DAYS = 30              # a held referral the owner has not approved is cancelled after this
RESEND_MINUTES = 10           # a second ask for one address within this sends nothing more
CONFIRMS_PER_EMAIL_DAY = 3    # confirmation emails one address can be sent in a day
ASKS_PER_NETWORK_HOUR = 10    # form posts one internet address may make in an hour
ASKS_PER_NETWORK_DAY = 30
MAILS_PER_HOUR = 300          # confirmation emails the whole site may send in an hour, whoever asks
MISSES_PER_NETWORK_HOUR = 20  # wrong codes and tokens one internet address may try in an hour
SAME_NETWORK_REFERRERS = 3    # this many confirmed referrers from one internet address holds their referrals
BURST_PER_HOUR = 3            # completed referrals across the site in an hour before the next ones are held

CODE_SHAPE = re.compile(r"^[A-Za-z0-9_-]{6,16}$")
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{20,40}$")
# A UK postcode: outward code (area, district) and inward code. Jersey, Guernsey and the Isle of Man are
# Crown dependencies, not the UK, so they are refused, as is anything else.
POSTCODE = re.compile(r"^([A-PR-UWYZ][A-HK-Y]?[0-9][A-Z0-9]?) ?([0-9][ABD-HJLNP-UW-Z]{2})$")
NOT_UK_AREAS = ("JE", "GY", "IM")

FLAG_KEY = "refer:open:v1"
OWNER_EMAIL_SUBJECT = "Referral complete"
OWNER_REVIEW_SUBJECT = "Referral for review"
OWNER_CAP_SUBJECT = "Refer a friend sign-ups paused for the hour"
OWNER_REMIND_SUBJECT = "Keyrings waiting to be posted"


# Fingerprints ------------------------------------------------------------------------------------

def digest(kind, value):
    return hmac.new(settings.SECRET_KEY.encode(), f"refer|{kind}|{value}".encode(), hashlib.sha256).hexdigest()


def normalise_email(email):
    """The address as a person, not a spelling: lower case, anything after a + dropped, and for Gmail the dots
    too (Gmail delivers a.b@ and ab@ to the same inbox)."""
    local, _, domain = email.strip().lower().rpartition("@")
    plain = local.split("+", 1)[0] or local
    if domain in ("gmail.com", "googlemail.com"):
        plain, domain = plain.replace(".", "") or plain, "gmail.com"
    return f"{plain}@{domain}"


def email_key(email):
    return digest("email", normalise_email(email))


def network_of(ip):
    """The address an abuser cannot cheaply change: an IPv4 address, or an IPv6 /64."""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "unknown"
    if parsed.version == 6:
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return str(parsed)


def fingerprints(request):
    """(connection, network): one-way hashes of the internet address with the browser, and of the address alone."""
    from .insights import client_ip

    network = network_of(client_ip(request))
    agent = request.headers.get("User-Agent", "")[:300]
    return digest("connection", f"{network}|{agent}")[:32], digest("network", network)[:32]


def clean_postcode(value):
    """'SW1A 1AA' for a UK postcode in any spacing or case, or None."""
    compact = re.sub(r"\s+", "", value or "").upper()
    found = POSTCODE.match(compact)
    if not found or compact[:2] in NOT_UK_AREAS:
        return None
    return f"{found.group(1)} {found.group(2)}"


def address_key(line1, line2, postcode):
    """The postcode with the house and flat numbers (every number in both lines, in order of size, so moving the
    flat number to the second line changes nothing), or the first line's letters when there is no number.
    "12 High St" and "12, High Street" are one address; "Flat 1, 12 High St" is another, which is why
    postcode_key marks a second address at one postcode for the owner to check."""
    numbers = sorted(set(re.findall(r"\d+", f"{line1} {line2}")), key=lambda n: (int(n), n))
    house = "-".join(numbers) if numbers else re.sub(r"[^a-z]", "", line1.lower())
    return digest("address", f"{postcode.replace(' ', '')}|{house}")


def postcode_key(postcode):
    return digest("postcode", postcode.replace(" ", ""))


# Counts in the shared cache ----------------------------------------------------------------------

def counted(kind, value, seconds):
    """Add one to a count kept for ``seconds`` and return it. The key is a one-way hash of ``value``."""
    key = f"refer:{kind}:" + digest("count", value)[:24]
    cache.add(key, 0, seconds)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, seconds)
        return 1


def peek(kind, value):
    return cache.get(f"refer:{kind}:" + digest("count", value)[:24]) or 0


def network_allowed(network):
    """False once this internet address has posted too often. Checked before anything is read or written."""
    hour = counted("ask-hour", network, 3600)
    day = counted("ask-day", network, 86400)
    return hour <= ASKS_PER_NETWORK_HOUR and day <= ASKS_PER_NETWORK_DAY


def confirmation_allowed(key):
    """False once this address (as a person, see normalise_email) has had its confirmations for the day, or the
    site its confirmation emails for the hour. The site cap is a cost backstop: when it trips, real sign-ups get
    no email either, so the owner is told at once (once an hour) rather than finding out from complaints."""
    if counted("confirm", key, 86400) > CONFIRMS_PER_EMAIL_DAY:
        return False
    if counted("mail", "all", 3600) <= MAILS_PER_HOUR:
        return True
    if cache.add("refer:cap-told", 1, 3600):
        logger.warning("Refer a friend: the site sent %s confirmation emails this hour; the rest are dropped.",
                       MAILS_PER_HOUR)
        tell_owner_plainly(OWNER_CAP_SUBJECT,
                           f"More than {MAILS_PER_HOUR} confirmation emails were asked for in the last hour, which "
                           f"usually means automated sign-ups. Nobody else is sent one until the hour is up, real "
                           f"people included. If it keeps happening, switch the offer off in admin.",
                           reverse("admin:catalogue_referralsettings_changelist"))
    return False


def too_many_misses(network):
    """True once this internet address has tried MISSES_PER_NETWORK_HOUR wrong codes or tokens in the hour.
    Read before the database is, so guessing costs one cache read."""
    return peek("miss", network) >= MISSES_PER_NETWORK_HOUR


def missed(network):
    counted("miss", network, 3600)


# Whether the offer is open -----------------------------------------------------------------------

def taking_part(row=None, today=None):
    """True while new people can join: switched on, email set up, two keyrings left and not past the last day."""
    row = row or ReferralSettings.current()
    today = today or timezone.localdate()
    return bool(row.is_on and mail.enabled() and row.keyrings_left >= 2
                and (row.ends_on is None or today <= row.ends_on))


def links_work(row=None):
    """Address and claim links stop working when the owner switches the offer off. Running out of keyrings or
    passing the last day stops new people only: those already promised a keyring can still claim it."""
    row = row or ReferralSettings.current()
    return bool(row.is_on)


def refresh_flag(row=None):
    """Remember in the cache whether the offer is open, for the footer link on every page without a query."""
    row = row or ReferralSettings.current()
    cache.set(FLAG_KEY, {"on": bool(row.is_on and mail.enabled()), "stock": row.keyrings_left >= 2,
                         "left": row.keyrings_left, "ends": row.ends_on.isoformat() if row.ends_on else ""}, None)


def advertised(today=None):
    """Whether to show the Refer a friend link. Read from the cache only: when it has been emptied (a deploy),
    the link is hidden until the refer page is next opened or tidy_referrals next runs, within the hour."""
    flag = cache.get(FLAG_KEY)
    if not flag or not flag.get("on") or not flag.get("stock"):
        return False
    today = today or timezone.localdate()
    return not flag.get("ends") or today.isoformat() <= flag["ends"]


def keyrings_left(today=None):
    """Keyrings still to give, while the offer is advertised, for the home page's Win a keyring section; else None.
    From the cache, like advertised: a flag written before it held the count gives None until it is next written."""
    if not advertised(today):
        return None
    return cache.get(FLAG_KEY).get("left")


def rewards(key):
    return PromoMark.objects.filter(kind=PromoMark.Kind.EMAIL, digest=key).count()


def at_limit(referrer, row=None):
    row = row or ReferralSettings.current()
    return rewards(referrer.email_key) >= row.max_per_referrer


# Emails ------------------------------------------------------------------------------------------

def link(name, *args):
    return settings.RIPRAPTOR_SITE_URL + reverse(name, args=args)


def share_url(referrer):
    return link("web:refer_friend", referrer.code)


def send(to, template, subject_key, context, stop_token, store=None):
    from .drop_alerts import render_mail

    store = copy.load_all() if store is None else store
    subject = copy.get(subject_key, store=store)
    stop_url = link("web:refer_stop", stop_token)
    text, html = render_mail(template, {"subject": subject, "stop_url": stop_url, **context}, store)
    mail.send(to, subject, text, html, unsubscribe=stop_url)


def send_link(referrer):
    send(referrer.email, "refer_link", "refer.email.link.subject",
         {"page_url": link("web:refer_confirm", referrer.token), "share_url": share_url(referrer)}, referrer.token)


# The referrer ------------------------------------------------------------------------------------

def ask(email, connection, network, now=None):
    """Record a sign-up and send the confirmation, or the link again to a confirmed referrer. Says nothing
    about the address: an address that has had its keyring, or its emails for the day, is sent nothing."""
    now = now or timezone.now()
    email = email.strip()
    key = email_key(email)
    row = ReferralSettings.current()
    if rewards(key) >= row.max_per_referrer:
        return
    referrer = Referrer.objects.filter(email_key=key).first()
    if referrer is not None and referrer.confirm_sent_at and referrer.confirm_sent_at > now - timedelta(minutes=RESEND_MINUTES):
        return
    if not confirmation_allowed(key):
        return
    if referrer is None:
        try:
            with transaction.atomic():
                referrer = Referrer.objects.create(email=email, email_key=key, connection=connection,
                                                   network=network, created_at=now)
        except IntegrityError:
            return   # the same address asked twice at once: the first one's email stands
    elif referrer.confirmed_at is None:
        # Not yet confirmed, so the address typed now is the one to try.
        referrer.email, referrer.connection, referrer.network = email, connection, network
        referrer.save(update_fields=["email", "connection", "network"])
    if referrer.confirmed_at:
        send_link(referrer)
    else:
        send(referrer.email, "refer_confirm", "refer.email.confirm.subject",
             {"confirm_url": link("web:refer_confirm", referrer.token)}, referrer.token)
    Referrer.objects.filter(pk=referrer.pk).update(confirm_sent_at=now)


def confirm(token, now=None):
    """(referrer, state) for a confirm link. state is "ok", "used" (has had all their keyrings), "ended" or
    "gone" (no such link)."""
    referrer = Referrer.objects.filter(token=token).first()
    if referrer is None:
        return None, "gone"
    row = ReferralSettings.current()
    if not taking_part(row):
        return referrer, "ended"
    if at_limit(referrer, row):
        return referrer, "used"
    if referrer.confirmed_at is None:
        referrer.confirmed_at = now or timezone.now()
        referrer.save(update_fields=["confirmed_at"])
        try:
            send_link(referrer)
        except mail.MailError as exc:
            logger.warning("The referral link email was not sent: %s", exc)   # the page shows the link anyway
    return referrer, "ok"


def referrer_for_code(code):
    if not CODE_SHAPE.match(code or ""):
        return None
    return Referrer.objects.filter(code=code, confirmed_at__isnull=False).first()


# The friend --------------------------------------------------------------------------------------

def friend_is_new(key, referrer):
    return (key != referrer.email_key and not rewards(key)
            and not Referrer.objects.filter(email_key=key, confirmed_at__isnull=False).exists())


def ask_friend(referrer, email, connection, network, now=None):
    """Record the friend and send their confirmation. Says nothing about the address either way."""
    now = now or timezone.now()
    email = email.strip()
    key = email_key(email)
    if not friend_is_new(key, referrer):
        return
    referral = Referral.objects.filter(friend_key=key).first()
    if referral is not None and referral.status != Referral.Status.WAITING:
        return
    if referral is not None and referral.confirm_sent_at and referral.confirm_sent_at > now - timedelta(minutes=RESEND_MINUTES):
        return
    if not confirmation_allowed(key):
        return
    if referral is None:
        try:
            with transaction.atomic():
                referral = Referral.objects.create(referrer=referrer, friend_email=email, friend_key=key,
                                                   connection=connection, network=network, created_at=now)
        except IntegrityError:
            return
    elif referral.referrer_id != referrer.pk:
        # The referral stays with the first referrer whose link was used. Moving it would let anyone who knows a
        # friend's address take the credit by typing it into their own link, and the friend's first email
        # would then complete someone else's referral.
        return
    else:
        referral.friend_email, referral.connection, referral.network = email, connection, network
        referral.save(update_fields=["friend_email", "connection", "network"])
    send(email, "refer_friend", "refer.email.friend.subject",
         {"confirm_url": link("web:refer_claim", referral.token)}, referral.token)
    Referral.objects.filter(pk=referral.pk).update(confirm_sent_at=now)


def review_reasons(referral, referrer, now):
    reasons = []
    if referral.connection and referral.connection == referrer.connection:
        reasons.append("Same browser and internet address as the referrer")
    elif referral.network and referral.network == referrer.network:
        reasons.append("Same internet address as the referrer")
    if referral.network:
        others = (Referral.objects.filter(network=referral.network, completed_at__gte=now - timedelta(days=REVIEW_DAYS))
                  .exclude(pk=referral.pk).count())
        if others:
            reasons.append(f"{others} other claim{'s' if others > 1 else ''} from this internet address in {REVIEW_DAYS} days")
    if referrer.network and Referrer.objects.filter(
            network=referrer.network, confirmed_at__isnull=False).count() >= SAME_NETWORK_REFERRERS:
        reasons.append("Many referrers from the referrer's internet address")
    recent = Referral.objects.filter(completed_at__gte=now - timedelta(hours=1)).exclude(pk=referral.pk).count()
    if recent >= BURST_PER_HOUR:
        reasons.append(f"{recent} referrals completed across the site in the last hour")
    return reasons


def complete(referral, now=None):
    """The friend pressed Claim my keyring. Returns "done", "not_new", "used", "ended" or "gone"."""
    now = now or timezone.now()
    with transaction.atomic():
        referral = Referral.objects.select_for_update().select_related("referrer").filter(pk=referral.pk).first()
        if referral is None or referral.status == Referral.Status.CANCELLED:
            return "gone"
        if referral.status != Referral.Status.WAITING:
            return "done"
        referrer = referral.referrer
        if referrer is None or referrer.confirmed_at is None:
            return "gone"
        row = ReferralSettings.current()
        if not taking_part(row):
            return "ended"
        if not friend_is_new(referral.friend_key, referrer):
            return "not_new"
        if at_limit(referrer, row):
            return "used"
        if not ReferralSettings.objects.filter(pk=1, keyrings_left__gte=2).update(keyrings_left=F("keyrings_left") - 2):
            return "ended"
        reasons = review_reasons(referral, referrer, now)
        referral.status = Referral.Status.REVIEW if reasons else Referral.Status.COMPLETE
        referral.review_reason = "; ".join(reasons)[:200]
        referral.completed_at = now
        referral.save(update_fields=["status", "review_reason", "completed_at"])
        PromoMark.objects.bulk_create([
            PromoMark(kind=PromoMark.Kind.EMAIL, digest=referral.friend_key, created_at=now),
            PromoMark(kind=PromoMark.Kind.EMAIL, digest=referrer.email_key, created_at=now),
        ])
        KeyringClaim.objects.bulk_create([
            KeyringClaim(referral=referral, role=KeyringClaim.Role.REFERRER, created_at=now),
            KeyringClaim(referral=referral, role=KeyringClaim.Role.FRIEND, created_at=now),
        ])
    refresh_flag()
    tell_owner(referral)
    if referral.status == Referral.Status.COMPLETE:
        send_address_emails(referral, now)
    return "done"


def tell_owner_plainly(subject, detail, path):
    """A push and an email to the owner. Never names a person or an email address."""
    url = notify.link_for(path)
    notify.push(subject, url)
    to = settings.RIPRAPTOR_CONTACT_EMAIL
    if to and mail.enabled():
        try:
            mail.send(to, subject, f"{detail}\n\nOpen admin: {url}\n",
                      f"<p>{detail}</p><p><a href=\"{url}\">Open admin</a></p>")
        except mail.MailError as exc:
            logger.warning("The owner was not emailed (%s): %s", subject, exc)


def tell_owner(referral):
    review = referral.status == Referral.Status.REVIEW
    subject = OWNER_REVIEW_SUBJECT if review else OWNER_EMAIL_SUBJECT
    left = ReferralSettings.current().keyrings_left
    detail = (f"Referral {referral.pk} is complete and held for review: {referral.review_reason}. Approve or cancel it "
              f"in admin. Nothing has been sent to either person." if review else
              f"Referral {referral.pk} is complete. Both people have been asked for their postal address.")
    tell_owner_plainly(subject, f"{detail} Keyrings left: {left}.",
                       reverse("admin:catalogue_referral_change", args=[referral.pk]))


def send_address_emails(referral, now=None):
    """Email both people their address link. Safe to call again: a claim already emailed is skipped, and a
    failed send leaves emails_sent_at empty for tidy_referrals to try again within the hour."""
    now = now or timezone.now()
    claimed = Referral.objects.filter(pk=referral.pk, emails_sent_at__isnull=True,
                                      status=Referral.Status.COMPLETE).update(emails_sent_at=now)
    if not claimed:
        return False
    referral = Referral.objects.select_related("referrer").get(pk=referral.pk)
    store = copy.load_all()
    try:
        for claim in referral.claims.all():
            to = referral.referrer.email if claim.role == KeyringClaim.Role.REFERRER and referral.referrer else (
                referral.friend_email if claim.role == KeyringClaim.Role.FRIEND else "")
            if not to or claim.submitted_at:
                continue
            KeyringClaim.objects.filter(pk=claim.pk).update(created_at=now)   # the 30 days start when it is sent
            send(to, "refer_address", "refer.email.address.subject",
                 {"address_url": link("web:refer_address", claim.token)}, claim.token, store)
    except mail.MailError as exc:
        logger.warning("Address emails for referral %s were not sent: %s", referral.pk, exc)
        Referral.objects.filter(pk=referral.pk).update(emails_sent_at=None)
        return False
    return True


# Owner actions -----------------------------------------------------------------------------------

def approve(referral, now=None):
    """Send the address emails for a referral held for review. Returns whether it was approved."""
    if not Referral.objects.filter(pk=referral.pk, status=Referral.Status.REVIEW).update(status=Referral.Status.COMPLETE):
        return False
    send_address_emails(referral, now)
    return True


def owed(claims):
    """Of these claims, the keyrings still promised: not posted, and not of a cancelled referral (cancelling has
    already put those back)."""
    return claims.filter(posted_at__isnull=True).exclude(referral__status=Referral.Status.CANCELLED)


def give_back(count):
    if count:
        ReferralSettings.objects.filter(pk=1).update(keyrings_left=F("keyrings_left") + count)


def delete_claims(claims):
    """Delete keyring claims, putting any still promised back in stock. Every delete of a claim goes through
    here, so the Keyrings left count always matches the drawer."""
    with transaction.atomic():
        back = owed(claims).count()
        deleted = claims.delete()[0]
        give_back(back)
    refresh_flag()
    return deleted


def delete_referrals(referrals):
    """Delete referrals (their claims go with them), putting any keyrings still promised back in stock."""
    with transaction.atomic():
        back = owed(KeyringClaim.objects.filter(referral__in=referrals)).count()
        deleted = referrals.delete()[0]
        give_back(back)
    refresh_flag()
    return deleted


NO_ADDRESS = {"name": "", "line1": "", "line2": "", "town": "", "postcode": ""}


def cancel(referral, now=None):
    """Cancel a referral. Keyrings not yet posted go back to stock, any address sent for them is deleted at once
    (it will never be posted), and its links stop working. Nothing is emailed to anyone."""
    now = now or timezone.now()
    with transaction.atomic():
        updated = (Referral.objects.filter(pk=referral.pk)
                   .exclude(status=Referral.Status.CANCELLED)
                   .update(status=Referral.Status.CANCELLED, cancelled_at=now))
        if not updated:
            return False
        unposted = KeyringClaim.objects.filter(referral_id=referral.pk, posted_at__isnull=True)
        give_back(unposted.count())
        unposted.update(**NO_ADDRESS)
    refresh_flag()
    return True


def to_post(claims):
    """Addresses sent in and waiting for a keyring: never those of a cancelled referral."""
    return (claims.filter(submitted_at__isnull=False, posted_at__isnull=True)
            .exclude(referral__status=Referral.Status.CANCELLED))


def mark_posted(claims, now=None):
    return KeyringClaim.objects.filter(pk__in=to_post(claims).values("pk")).update(posted_at=now or timezone.now())


# Address form ------------------------------------------------------------------------------------

def claim_state(claim, now=None):
    """"open", "done", "expired", "ended" or "gone" for an address link."""
    now = now or timezone.now()
    if claim is None or claim.referral.status == Referral.Status.CANCELLED:
        return "gone"
    if not links_work():
        return "ended"
    if claim.referral.status != Referral.Status.COMPLETE or claim.referral.emails_sent_at is None:
        return "gone"
    if claim.submitted_at:
        return "done"
    if claim.created_at < now - timedelta(days=ADDRESS_LINK_DAYS):
        return "expired"
    return "open"


ADDRESS_FIELDS = {"name": 80, "line1": 100, "line2": 100, "town": 60, "postcode": 10}


def save_address(claim, values, now=None):
    """Check and save the address. Returns an error copy key, or "" when saved."""
    now = now or timezone.now()
    values = {name: " ".join(values.get(name, "").split())[:limit] for name, limit in ADDRESS_FIELDS.items()}
    if not all(values[name] for name in ("name", "line1", "town", "postcode")):
        return "refer.address.missing"
    postcode = clean_postcode(values["postcode"])
    if postcode is None:
        return "refer.address.bad_postcode"
    key = address_key(values["line1"], values["line2"], postcode)
    area = postcode_key(postcode)
    with transaction.atomic():
        marks = set(PromoMark.objects.filter(
            kind__in=[PromoMark.Kind.ADDRESS, PromoMark.Kind.POSTCODE], digest__in=[key, area],
        ).values_list("kind", flat=True))
        if PromoMark.Kind.ADDRESS in marks:
            return "refer.address.used"
        updated = KeyringClaim.objects.filter(pk=claim.pk, submitted_at__isnull=True).update(
            name=values["name"], line1=values["line1"], line2=values["line2"], town=values["town"],
            postcode=postcode, address_key=key, submitted_at=now,
            same_postcode=PromoMark.Kind.POSTCODE in marks,
        )
        if updated:
            PromoMark.objects.bulk_create([PromoMark(kind=PromoMark.Kind.ADDRESS, digest=key, created_at=now)]
                                          + ([] if PromoMark.Kind.POSTCODE in marks else
                                             [PromoMark(kind=PromoMark.Kind.POSTCODE, digest=area, created_at=now)]))
    return ""


# Deleting details on request ---------------------------------------------------------------------

def find_token(token):
    """The referrer, referral or keyring claim a token in an email belongs to, or None."""
    if not TOKEN_SHAPE.match(token or ""):
        return None
    for model in (Referrer, Referral, KeyringClaim):
        found = model.objects.filter(token=token).first()
        if found is not None:
            return found
    return None


def forget(found):
    """Delete what the person behind this token gave us. Keyrings not posted go back to stock. The PromoMark
    fingerprints stay, so a deleted address cannot take part again."""
    with transaction.atomic():
        if isinstance(found, KeyringClaim):
            claims = KeyringClaim.objects.filter(pk=found.pk)
            if found.role == KeyringClaim.Role.FRIEND:
                Referral.objects.filter(pk=found.referral_id).update(friend_email="")
        elif isinstance(found, Referral):
            claims = KeyringClaim.objects.filter(referral=found, role=KeyringClaim.Role.FRIEND)
        else:
            claims = KeyringClaim.objects.filter(referral__referrer=found, role=KeyringClaim.Role.REFERRER)
        back = owed(claims).count()
        claims.delete()
        if isinstance(found, Referral):
            if found.status == Referral.Status.WAITING:
                found.delete()
            else:
                Referral.objects.filter(pk=found.pk).update(friend_email="", connection="", network="")
        elif isinstance(found, Referrer):
            Referral.objects.filter(referrer=found, status=Referral.Status.WAITING).delete()
            found.delete()
        give_back(back)
    refresh_flag()


# Tidying -----------------------------------------------------------------------------------------

def tidy(now=None):
    """Send address emails that failed, then delete what is no longer needed. Returns counts."""
    now = now or timezone.now()
    days = lambda n: now - timedelta(days=n)   # noqa: E731
    counts = {}
    for referral in Referral.objects.filter(status=Referral.Status.COMPLETE, emails_sent_at__isnull=True):
        send_address_emails(referral, now)
    for referral in Referral.objects.filter(status=Referral.Status.REVIEW, completed_at__lt=days(REVIEW_DAYS)):
        cancel(referral, now)
    counts["unconfirmed"] = (
        Referrer.objects.filter(confirmed_at__isnull=True, created_at__lt=days(UNCONFIRMED_DAYS)).delete()[0]
        + Referral.objects.filter(status=Referral.Status.WAITING, created_at__lt=days(UNCONFIRMED_DAYS)).delete()[0]
    )
    counts["posted"] = KeyringClaim.objects.filter(posted_at__lt=days(POSTED_KEEP_DAYS)).delete()[0]
    # Keyrings nobody sent an address for, or the owner never posted, go back to stock with the claim.
    counts["unclaimed"] = delete_claims(KeyringClaim.objects.filter(
        submitted_at__isnull=True, created_at__lt=days(ADDRESS_LINK_DAYS + UNCLAIMED_DAYS)))
    counts["unposted"] = delete_claims(KeyringClaim.objects.filter(
        posted_at__isnull=True, submitted_at__lt=days(UNPOSTED_KEEP_DAYS)))
    counts["cancelled"] = Referral.objects.filter(status=Referral.Status.CANCELLED,
                                                  cancelled_at__lt=days(CANCELLED_KEEP_DAYS)).delete()[0]
    # A finished referral with no keyring left to post or claim: nothing about either person is needed.
    counts["finished"] = Referral.objects.filter(
        status=Referral.Status.COMPLETE, emails_sent_at__lt=days(ADDRESS_LINK_DAYS), claims__isnull=True).delete()[0]
    # A referrer with nothing left in progress: they have had their keyrings, or their link went unused.
    idle = Referrer.objects.filter(confirmed_at__isnull=False, referrals__isnull=True)
    gone = [r.pk for r in idle if r.confirmed_at < days(UNUSED_LINK_DAYS) or at_limit(r)]
    counts["referrers"] = Referrer.objects.filter(pk__in=gone).delete()[0]
    remind_owner(now)
    refresh_flag()
    return counts


def remind_owner(now):
    """Once a day, while any address has waited REMIND_AFTER_DAYS to be posted."""
    waiting = to_post(KeyringClaim.objects.filter(submitted_at__lt=now - timedelta(days=REMIND_AFTER_DAYS))).count()
    if waiting and cache.add("refer:remind", 1, 86400):
        tell_owner_plainly(
            OWNER_REMIND_SUBJECT,
            f"{waiting} keyring address{'es have' if waiting > 1 else ' has'} waited over {REMIND_AFTER_DAYS} days. "
            f"Post them and mark them posted in admin. An address not marked posted within {UNPOSTED_KEEP_DAYS} "
            f"days of being sent is deleted and its keyring goes back to stock.",
            reverse("admin:catalogue_keyringclaim_changelist") + "?state=to_post")
    return waiting
