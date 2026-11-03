"""
Shop outreach: emails the owner sends to shops from their own mailbox, filled in with each shop's own figures.

The owner asked for "a CRM type of place to send emails" to shops: what RipRaptor has sent them, and asks such as
an affiliate link or a product feed, with replies handled in Zoho Mail. So nothing is sent from the site. The Shop
outreach page in admin keeps a ShopContact per shop (its business address, where things stand, notes and when to
follow up), fills in a draft from the wording in Site wording (``outreach.*``) and opens it in the owner's mail
app (a mailto: link), so the email goes from their own address and its replies come back there. The alert emails'
service (ZeptoMail) is for automatic emails only, and outreach through it could cost the alerts their sender.

Every draft ends with the line saying how to ask for no more (``outreach.optout``), and a shop marked "Do not
email" gets no draft at all. The figures are RipRaptor's own: shoppers sent are people's clicks to the shop
(OutboundClick.people(), never a robot or a direct hit), and the listings are the shop's on the site now.
"""

import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.db.models import Count, Min, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from content import service as copy
from content.registry import REGISTRY
from web import badge

from .models import OutreachEmail, ShopContact

MONTH, QUARTER = timedelta(days=30), timedelta(days=90)
TOP_PRODUCTS = 3
# A follow-up is due this long after an email is marked as sent, unless one is set.
FOLLOW_UP = timedelta(days=14)
# The drafts, in the order the page shows them: (kind, its name on the page).
DRAFTS = [("affiliate", "Ask for an affiliate link"), ("report", "Tell them what we sent them"),
          ("listing", "Ask for a product feed"), ("badge", "Offer the RipRaptor badge"),
          ("update", "Update and offer the badge")]
# The email sent to every shop in one go (the Update and badge emails page): what we sent them, then the badge.
UPDATE = "update"
KINDS = {kind for kind, _ in DRAFTS}


@dataclass
class Figures:
    visitors_month: int = 0
    visitors_quarter: int = 0
    visitors_total: int = 0
    since: object = None          # the date of the first shopper sent, or None
    listed: int = 0
    in_stock: int = 0
    top_products: tuple = ()


def ensure_contacts():
    """A ShopContact for every shop on the site that has none yet: not eBay or Amazon, which are searches. Two
    queries."""
    from catalogue.models import Retailer
    from catalogue.pricing import MARKETPLACES

    missing = (Retailer.objects.exclude(source_type__in=MARKETPLACES).filter(contact__isnull=True).order_by()
               .values_list("pk", flat=True))
    ShopContact.objects.bulk_create([ShopContact(retailer_id=pk) for pk in missing], ignore_conflicts=True)


def shown(retailer):
    """The shop's listings the comparison shows now: checked inside the stale cutoff and not kept out of it, as the
    home page's count of shops has them."""
    from catalogue.models import Listing

    return Listing.objects.current().filter(retailer=retailer).exclude(sanity=Listing.Sanity.EXCLUDED)


def with_counts(contacts, now=None):
    """``contacts`` (a ShopContact queryset) with ``visitors_quarter`` and ``listed`` on each, for the list page, in
    the same query."""
    from catalogue.models import OutboundClick

    now = now or timezone.now()
    clicks = (OutboundClick.objects.people().filter(retailer=OuterRef("retailer"), created_at__gte=now - QUARTER)
              .order_by().values("retailer").annotate(n=Count("pk")).values("n"))
    listed = shown(OuterRef("retailer")).order_by().values("retailer").annotate(n=Count("pk")).values("n")
    return contacts.annotate(visitors_quarter=Coalesce(Subquery(clicks), 0), listed=Coalesce(Subquery(listed), 0))


def figures(retailer, now=None):
    """The shop's Figures. Four queries."""
    from catalogue.models import Listing, OutboundClick

    now = now or timezone.now()
    clicks = OutboundClick.objects.people().filter(retailer=retailer)
    counts = clicks.aggregate(month=Count("pk", filter=Q(created_at__gte=now - MONTH)),
                              quarter=Count("pk", filter=Q(created_at__gte=now - QUARTER)), total=Count("pk"))
    first = clicks.order_by("created_at").values_list("created_at", flat=True).first()
    top = (clicks.filter(created_at__gte=now - QUARTER).order_by().values("product__name")
           .annotate(n=Count("pk")).order_by("-n", "product__name").values_list("product__name", flat=True)[:TOP_PRODUCTS])
    listings = shown(retailer).aggregate(
        listed=Count("pk"),
        in_stock=Count("pk", filter=Q(availability=Listing.Availability.IN_STOCK)),
    )
    return Figures(visitors_month=counts["month"], visitors_quarter=counts["quarter"], visitors_total=counts["total"],
                   since=timezone.localdate(first) if first else None, listed=listings["listed"],
                   in_stock=listings["in_stock"], top_products=tuple(top))


def bulk_figures(retailer_ids, now=None):
    """{retailer_id: Figures} for many shops in three queries: the same figures ``figures`` gives one shop at a time."""
    from catalogue.models import Listing, OutboundClick

    now = now or timezone.now()
    ids = list(retailer_ids)
    found = {pk: Figures() for pk in ids}
    clicks = OutboundClick.objects.people().filter(retailer_id__in=ids)
    for row in clicks.order_by().values("retailer_id").annotate(
            month=Count("pk", filter=Q(created_at__gte=now - MONTH)),
            quarter=Count("pk", filter=Q(created_at__gte=now - QUARTER)), total=Count("pk"),
            first=Min("created_at")):
        shop = found[row["retailer_id"]]
        shop.visitors_month, shop.visitors_quarter, shop.visitors_total = row["month"], row["quarter"], row["total"]
        shop.since = timezone.localdate(row["first"]) if row["first"] else None
    ranked = {}
    for row in (clicks.filter(created_at__gte=now - QUARTER).order_by().values("retailer_id", "product__name")
                .annotate(n=Count("pk"))):
        ranked.setdefault(row["retailer_id"], []).append((-row["n"], row["product__name"]))
    for pk, rows in ranked.items():
        found[pk].top_products = tuple(name for _n, name in sorted(rows)[:TOP_PRODUCTS])
    listings = (Listing.objects.current().filter(retailer_id__in=ids).exclude(sanity=Listing.Sanity.EXCLUDED)
                .order_by().values("retailer_id")
                .annotate(listed=Count("pk"), in_stock=Count("pk", filter=Q(availability=Listing.Availability.IN_STOCK))))
    for row in listings:
        found[row["retailer_id"]].listed, found[row["retailer_id"]].in_stock = row["listed"], row["in_stock"]
    return found


def listed_names(names):
    """"A, B and C"."""
    names = list(names)
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def values(retailer, found, store=None):
    """The placeholders the outreach wording takes, from the shop's Figures."""
    return {
        "shop": retailer.name,
        "visitors_month": f"{found.visitors_month:,}",
        "visitors_quarter": f"{found.visitors_quarter:,}",
        "visitors_total": f"{found.visitors_total:,}",
        "since": f"{found.since.day} {found.since:%B %Y}" if found.since else "",
        "top_products": listed_names(found.top_products) or copy.get("outreach.top_none", store=store),
        "listed": f"{found.listed:,}",
        "in_stock": f"{found.in_stock:,}",
        "site_url": settings.RIPRAPTOR_SITE_URL,
        "badge_url": badge.page_url(),
        "sent": sent_line(found.visitors_quarter, store),
        "shopper_update": shopper_update(found, store),
        "top_line": top_line(found, store),
    }


def sent_line(count, store=None):
    """"In the last 3 months we sent you 12 shoppers.", or "" when we sent none: a new shop is not told nought."""
    if not count:
        return ""
    return copy.get(f"outreach.sent.{'one' if count == 1 else 'other'}", store=store, count=f"{count:,}")


def shoppers_phrase(count, store=None):
    """"1 shopper" or "254 shoppers"."""
    return copy.get(f"outreach.shoppers.{'one' if count == 1 else 'other'}", store=store, count=f"{count:,}")


def shopper_update(found, store=None):
    """The update email's sentence on shoppers sent, "" when we sent none: a new shop is not told nought. With
    shoppers only before the last 30 days it gives the 3 months alone, and with all of them in the last 30 days
    it gives the 30 days alone, rather than the same number twice."""
    month, quarter = found.visitors_month, found.visitors_quarter
    if not quarter:
        return ""
    if not month:
        return sent_line(quarter, store)
    phrase = shoppers_phrase(month, store)
    if month == quarter:
        return copy.get("outreach.update.month", store=store, month=phrase)
    return copy.get("outreach.update.month_quarter", store=store, month=phrase, quarter=f"{quarter:,}")


def top_line(found, store=None):
    """The sentence naming the products clicked most, "" when nobody clicked through in 3 months."""
    if not found.top_products:
        return ""
    return copy.get("outreach.update.top", store=store, top_products=listed_names(found.top_products))


def draft(retailer, kind, found, store=None):
    """(subject, body) of the ``kind`` email to ``retailer``: the wording filled in, then the sign-off and the line
    about not emailing again. A draft may have a sign-off of its own (``outreach.<kind>.signoff``), as the update
    email has: it is signed with the owner's name."""
    filled = values(retailer, found, store)
    own = f"outreach.{kind}.signoff"
    subject = copy.get(f"outreach.{kind}.subject", store=store, **filled)
    body = "\n\n".join(part for part in (
        copy.get(f"outreach.{kind}.body", store=store, **filled),
        copy.get(own if own in REGISTRY else "outreach.signoff", store=store, **filled),
        copy.get("outreach.optout", store=store),
    ) if part)
    # A placeholder left empty (sent_line) leaves no space at the end of its line.
    return subject, re.sub(r"[ \t]+\n", "\n", body)


def mailto(email, subject, body):
    """A mailto: link that opens the email in the owner's mail app, ready to send. Line breaks as CRLF, as the
    standard asks."""
    query = f"subject={quote(subject)}&body={quote(body.replace(chr(13), '').replace(chr(10), chr(13) + chr(10)))}"
    return f"mailto:{quote(email, safe='@')}?{query}"


def update_queue(show_sent=False):
    """The shops to send the update email to, the ones sending most shoppers first: on the site and with something
    listed now, not marked Do not email, and (unless ``show_sent``) not already sent this email. The counts
    (``visitors_quarter``, ``listed``) are on each."""
    contacts = (with_counts(ShopContact.objects.select_related("retailer").filter(retailer__is_active=True))
                .filter(listed__gt=0).exclude(status=ShopContact.Status.DO_NOT_EMAIL))
    if not show_sent:
        contacts = contacts.exclude(emails__kind=UPDATE)
    return contacts.order_by("-visitors_quarter", "retailer__name")


def due(contacts, today=None):
    """The shops in ``contacts`` emailed and not yet replied whose follow-up date has come."""
    return contacts.filter(status=ShopContact.Status.EMAILED, follow_up_on__lte=today or timezone.localdate())


def follow_ups_due(today=None):
    """How many shops to follow up, for the admin home page. One query."""
    return due(ShopContact.objects.all(), today).count()


def can_email(contact):
    return contact.status != ShopContact.Status.DO_NOT_EMAIL


def mark_sent(contact, kind, subject, now=None):
    """Record that the owner sent the ``kind`` email. A shop not contacted yet, or one that said no before, is now
    Emailed; one that replied or whose affiliate link is live keeps its status. A follow-up is set two weeks on
    unless one is already set for later. Refused (ValueError) for a shop marked Do not email."""
    if not can_email(contact):
        raise ValueError(f"{contact} asked not to be emailed.")
    if kind not in KINDS:
        raise ValueError(f"No {kind} email.")
    now = now or timezone.now()
    OutreachEmail.objects.create(contact=contact, kind=kind, subject=subject[:200], sent_at=now)
    contact.last_emailed_at = now
    if contact.status in (ShopContact.Status.NEW, ShopContact.Status.DECLINED):
        contact.status = ShopContact.Status.EMAILED
    due = timezone.localdate(now) + FOLLOW_UP
    if contact.follow_up_on is None or contact.follow_up_on < due:
        contact.follow_up_on = due
    contact.save(update_fields=["last_emailed_at", "status", "follow_up_on"])
