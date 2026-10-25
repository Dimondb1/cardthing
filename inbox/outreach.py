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

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from content import service as copy

from .models import OutreachEmail, ShopContact

MONTH, QUARTER = timedelta(days=30), timedelta(days=90)
TOP_PRODUCTS = 3
# A follow-up is due this long after an email is marked as sent, unless one is set.
FOLLOW_UP = timedelta(days=14)
# The drafts, in the order the page shows them: (kind, its name on the page).
DRAFTS = [("affiliate", "Ask for an affiliate link"), ("report", "Tell them what we sent them"),
          ("listing", "Ask for a product feed")]
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
    }


def draft(retailer, kind, found, store=None):
    """(subject, body) of the ``kind`` email to ``retailer``: the wording filled in, then the sign-off and the line
    about not emailing again."""
    filled = values(retailer, found, store)
    subject = copy.get(f"outreach.{kind}.subject", store=store, **filled)
    body = "\n\n".join(part for part in (
        copy.get(f"outreach.{kind}.body", store=store, **filled),
        copy.get("outreach.signoff", store=store, **filled),
        copy.get("outreach.optout", store=store),
    ) if part)
    return subject, body


def mailto(email, subject, body):
    """A mailto: link that opens the email in the owner's mail app, ready to send. Line breaks as CRLF, as the
    standard asks."""
    query = f"subject={quote(subject)}&body={quote(body.replace(chr(13), '').replace(chr(10), chr(13) + chr(10)))}"
    return f"mailto:{quote(email, safe='@')}?{query}"


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
