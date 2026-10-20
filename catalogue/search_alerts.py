"""
Search alerts: a visitor describes what they want ("English Pokemon booster packs up to £5.00 delivered") on
the watchlist page and is emailed when products like that are in stock at a UK shop.

Kept like the back-in-stock alerts (catalogue/alerts.py) and drop reminders (catalogue/drop_alerts.py): the
address is stored for this alone, nothing is sent until the confirm link is pressed, every email carries a
link that deletes it at once, unconfirmed alerts go after a week and confirmed ones six months after they
were confirmed. The sign-up never says what it knows about an address: the answer is the same "check your
inbox" whether the address already has the alert, is at its limit or is new. An address gets at most
CONFIRMS_PER_EMAIL_DAY confirmation emails a day, and one connection makes at most ASKS_PER_ADDRESS_HOUR
sign-ups an hour, both counted under one-way hashes (drop_alerts.counted).

What matches: an active product of the alert's game, type and language (the browse filter's language rules,
ordering.apply_languages) that a shop on the site, not eBay or Amazon, has in stock now with a current,
counted price (Listing.buyable, as the site's main price). With a price, its cheapest delivered price at such
a shop must be known and at or under it; a shop whose delivery charge is not known cannot prove that.
eBay and Amazon are left out as they are from the back-in-stock alerts and drop reminders: a seller's
listing is not a shop having it in stock.

What is sent: one email per alert per run, for products it has not been emailed about (SearchAlertSent).
A product is emailed again only once it has stopped matching (sold out, or over the price) and come back,
and never within SEND_AGAIN_DAYS of the last time. The email shows up to PRODUCTS_PER_EMAIL, cheapest first,
and links to the rest on the site. It never names a shop or links to one: no shop name, no /go/ link, no
shop address, and a picture only when it is stored on RipRaptor itself (email_image). A shop's picture
address gives the shop away even on a shared CDN, whose folders belong to one shop each.

"Stopped matching" needs a current shop listing that says so (sold out, or over the price). A product
whose listings have only gone unchecked for a while (a shop's reader failing) has not stopped matching,
so it is not sent again when the reader comes back.

send_due runs every 15 minutes from cron. Alerts that describe the same game, type and language share one
query. Each email is claimed before it is sent (the alert's sent_at, and the products recorded as sent in
the same transaction), so a second run at the same moment cannot send it too; a failed send hands it back.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from content import service as copy

from . import insights, languages, mail
from .alerts import absolute
from .drop_alerts import counted, render_mail
from .images import EMAIL_WIDTH, sized_image_url
from .models import DailyPageView, Listing, Product, Retailer, SearchAlert, SearchAlertSent
from .ordering import apply_languages
from .types import type_label

MARKETPLACES = (Retailer.Source.AMAZON, Retailer.Source.EBAY)
MAX_PER_EMAIL = 10            # confirmed alerts one address can have
CONFIRMS_PER_EMAIL_DAY = 3    # confirmation emails one address can be sent in a day
ASKS_PER_ADDRESS_HOUR = 10    # sign-ups one internet address may make in an hour
RESEND_MINUTES = 10           # a second ask for the same alert within this sends no second confirmation
UNCONFIRMED_DAYS = 7
KEEP_DAYS = 183               # about six months after it was confirmed
SEND_AGAIN_DAYS = 7
PRODUCTS_PER_EMAIL = 6
MIN_PRICE = Decimal("0.50")
MAX_PRICE = Decimal("5000.00")

LANGUAGE_CODES = [code for code, _ in languages.FILTER]
# Words for the language before the game ("English Pokémon booster packs"); "other" goes after instead.
LANGUAGE_WORDS = {code: name for code, name in languages.FILTER if code != "other"}

# Parts of a shop's address that say nothing about which shop it is ("shop.example.co.uk").
HOST_WORDS = {"www", "shop", "store", "cdn", "images", "img", "static", "media", "co", "uk", "com", "net", "org",
              "example", "io", "eu", "store", "online"}


def note(event):
    insights.bump(DailyPageView, kind=DailyPageView.Kind.ALERTS, key=f"search_{event}")


def link(name, *args):
    return settings.RIPRAPTOR_SITE_URL + reverse(name, args=args)


def money(value):
    return f"£{Decimal(value).quantize(Decimal('0.01'))}"


# Describing an alert ------------------------------------------------------------------------------

def plural(label):
    """"Booster pack" to "booster packs", "Elite Trainer Box" to "Elite Trainer Boxes"."""
    words = label.split(" ")
    if not all(word[:1].isupper() for word in words if word[:1].isalpha()) or len(words) == 1:
        label = label[:1].lower() + label[1:]
    if label.lower() == "other":
        return "other products"
    if label.endswith("s"):
        return label
    if label.endswith(("x", "ch", "sh")):
        return label + "es"
    return label + "s"


def things(game, product_type="", language=""):
    """"English Pokémon booster packs", "Magic products in other languages"."""
    kind = plural(type_label(game.slug, product_type)) if product_type else "products"
    words = [LANGUAGE_WORDS.get(language, ""), game.display_short, kind]
    text = " ".join(word for word in words if word)
    return f"{text} in other languages" if language == "other" else text


def describe(game, product_type="", language="", max_price=None, store=None):
    """What the alert is for, without "in stock": "English Pokémon booster packs up to £5.00 delivered"."""
    what = things(game, product_type, language)
    if max_price is None:
        return what
    return copy.get("watchlist.alert.what_price", store=store, things=what, price=money(max_price))


def summary(game, product_type="", language="", max_price=None, store=None):
    """The plain-English line shown back to the visitor: "... up to £5.00 delivered, in stock"."""
    return copy.get("watchlist.alert.summary", store=store,
                    what=describe(game, product_type, language, max_price, store=store))


def alert_summary(alert, store=None):
    return summary(alert.game, alert.product_type, alert.language, alert.max_price, store=store)


def list_url(alert):
    """The site's list of what the alert asks for, in stock and cheapest first."""
    params = {"game": alert.game.slug}
    if alert.product_type:
        params["type"] = alert.product_type
    if alert.language:
        params["lang"] = alert.language
    params.update({"in_stock": "on", "sort": "price"})
    return settings.RIPRAPTOR_SITE_URL + reverse("web:search") + "?" + urlencode(params)


# Sign-up ------------------------------------------------------------------------------------------

def address_allowed(ip):
    """False once this internet address has asked ASKS_PER_ADDRESS_HOUR times in the hour."""
    return counted("search_ask", ip, 3600) <= ASKS_PER_ADDRESS_HOUR


def confirmation_allowed(email):
    return counted("search_confirm", email, 86400) <= CONFIRMS_PER_EMAIL_DAY


def ask(email, game, product_type="", language="", max_price=None, now=None):
    """Record a request and send the confirmation. Always returns "sent", whatever is known about the
    address, like drop_alerts.ask: one that already has this alert, is at MAX_PER_EMAIL or has had its
    confirmations for the day is sent nothing more, and the visitor is not told which."""
    now = now or timezone.now()
    email = email.strip().lower()
    wanted = {"email": email, "game": game, "product_type": product_type or "", "language": language or "",
              "max_price": max_price}
    alert = SearchAlert.objects.filter(**wanted).first()
    if alert is not None and alert.confirmed_at:
        return "sent"
    if alert is not None and alert.confirm_sent_at and alert.confirm_sent_at > now - timedelta(minutes=RESEND_MINUTES):
        return "sent"
    if alert is None and SearchAlert.objects.filter(email=email, confirmed_at__isnull=False).count() >= MAX_PER_EMAIL:
        return "sent"
    if not confirmation_allowed(email):
        return "sent"
    if alert is None:
        try:
            with transaction.atomic():
                alert = SearchAlert.objects.create(created_at=now, **wanted)
        except IntegrityError:
            return "sent"
    store = copy.load_all()
    what = alert_summary(alert, store)
    values = {"summary": what, "site_name": settings.RIPRAPTOR_SITE_NAME}
    subject = copy.get("watchlist.email.confirm.subject", store=store, **values)
    stop_url = link("web:search_alert_stop", alert.token)
    text, html = render_mail("search_confirm", {
        "subject": subject,
        "preheader": copy.get("watchlist.email.confirm.preheader", store=store, **values),
        "values": values,
        "confirm_url": link("web:search_alert_confirm", alert.token),
        "stop_url": stop_url,
    }, store)
    mail.send(email, subject, text, html, unsubscribe=stop_url)
    SearchAlert.objects.filter(pk=alert.pk).update(confirm_sent_at=now)
    note("asked")
    return "sent"


def confirm(token, now=None):
    """The alert, confirmed. One whose address already has MAX_PER_EMAIL confirmed alerts is left
    unconfirmed (confirmed_at stays None), so requests made over several days cannot pass the limit."""
    alert = SearchAlert.objects.select_related("game").filter(token=token).first()
    if alert is not None and alert.confirmed_at is None:
        if SearchAlert.objects.filter(email=alert.email, confirmed_at__isnull=False).count() >= MAX_PER_EMAIL:
            return alert
        alert.confirmed_at = now or timezone.now()
        alert.save(update_fields=["confirmed_at"])
        note("confirmed")
    return alert


def stop(token):
    """Delete the alert, and what it was sent, at once. Returns True when there was one."""
    deleted, _ = SearchAlert.objects.filter(token=token).delete()
    if deleted:
        note("stopped")
    return bool(deleted)


# Matching -----------------------------------------------------------------------------------------

@dataclass
class Offer:
    """The cheapest in-stock shop price for one product: ``known`` delivered where a shop's delivery charge
    is known, else ``price`` plus delivery. ``checked`` is when that listing was last read."""

    product_id: int
    known: Decimal = None
    known_checked: datetime = None
    price: Decimal = None
    price_checked: datetime = None

    def shown(self, max_price=None):
        """(price, delivered, checked) the email shows, or None when it does not match ``max_price``."""
        if self.known is not None and (max_price is None or self.known <= max_price):
            return self.known, True, self.known_checked
        if max_price is None and self.price is not None:
            return self.price, False, self.price_checked
        return None


def group_offers(game_id, product_type="", language=""):
    """({product id: Offer}, listed) for this game, type and language, in one query. The offers are every
    active product a shop (not a marketplace) has in stock now at a current, counted price, as
    Listing.buyable. ``listed`` is every product with any current, counted shop listing, in stock or not: a
    product listed but not matching has really sold out or gone over the price, while one missing from it
    has only gone unchecked."""
    products = Product.objects.active().filter(game_id=game_id)
    if product_type:
        products = products.filter(product_type=product_type)
    products = apply_languages(products, [language] if language else [])
    rows = (
        Listing.objects.current()
        .filter(product__in=products.values("pk"))
        .exclude(sanity=Listing.Sanity.EXCLUDED)
        .exclude(retailer__source_type__in=MARKETPLACES)
        .order_by()
        .values_list("product_id", "availability", "price", "delivery_cost", "delivery_known", "last_checked")
    )
    found, listed = {}, set()
    for product_id, availability, price, delivery, known, checked in rows:
        listed.add(product_id)
        if availability != Listing.Availability.IN_STOCK or price is None:
            continue
        offer = found.setdefault(product_id, Offer(product_id))
        if known:
            delivered = price + (delivery or 0)
            if offer.known is None or delivered < offer.known:
                offer.known, offer.known_checked = delivered, checked
        elif offer.price is None or price < offer.price:
            offer.price, offer.price_checked = price, checked
    return found, listed


def matches_for(alert, offers):
    """{product id: (price, delivered, checked)} for the products matching this alert now."""
    found = {}
    for product_id, offer in offers.items():
        shown = offer.shown(alert.max_price)
        if shown is not None:
            found[product_id] = shown
    return found


def due_products(records, matching, now):
    """The matching product ids this alert should hear about now: never sent, or sent, gone and back after
    SEND_AGAIN_DAYS."""
    again = now - timedelta(days=SEND_AGAIN_DAYS)
    due = []
    for product_id in matching:
        record = records.get(product_id)
        if record is None or (record.gone_at is not None and record.sent_at <= again):
            due.append(product_id)
    return due


# The email ----------------------------------------------------------------------------------------

def flat(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def shop_words():
    """Every way a shop's name or address could show in a picture's address, flattened to letters and
    digits, four characters or longer. One query, over every shop including hidden ones."""
    words = set()
    for name, slug, website, source in Retailer.objects.values_list("name", "slug", "website", "source_url"):
        words.update((flat(name), flat(slug)))
        for address in (website, source):
            host = urlsplit(address or "").netloc.lower().split(":")[0]
            labels = [label for label in host.split(".") if label not in HOST_WORDS]
            words.add(flat(".".join(labels)))
            words.update(flat(label) for label in labels)
    return {word for word in words if len(word) >= 4}


def email_image(product, words):
    """The product's picture for the email, or "": only a picture stored on RipRaptor (Product.image),
    never a shop's (image_url), and only when its file name names no shop. A shop's picture gives the shop
    away even on a shared CDN, whose folders and file prefixes belong to one shop each."""
    if not product.image:
        return ""
    url = absolute(sized_image_url(product.image.url, EMAIL_WIDTH))
    if urlsplit(url).netloc.lower() != urlsplit(settings.RIPRAPTOR_SITE_URL).netloc.lower():
        return ""
    plain = flat(url)
    if any(word in plain for word in words):
        return ""
    return url


def price_text(price, delivered, store):
    key = "watchlist.email.match.delivered" if delivered else "watchlist.email.match.plus"
    return copy.get(key, store=store, price=money(price))


def match_mail(alert, items, more, store):
    """(subject, text, html, stop_url) for one alert's email. ``items`` are dicts already worked out."""
    what = describe(alert.game, alert.product_type, alert.language, alert.max_price, store=store)
    values = {"what": what, "summary": alert_summary(alert, store), "site_name": settings.RIPRAPTOR_SITE_NAME}
    subject = copy.get("watchlist.email.match.subject", store=store, **values)
    first = items[0]
    stop_url = link("web:search_alert_stop", alert.token)
    text, html = render_mail("search_match", {
        "subject": subject,
        "preheader": copy.get("watchlist.email.match.preheader", store=store, product=first["name"],
                              price=first["price"]),
        "values": values,
        "items": items,
        "more": more,
        "more_url": list_url(alert),
        "stop_url": stop_url,
    }, store)
    return subject, text, html, stop_url


def item_rows(product_ids, matching, products, words, store):
    rows = []
    for product_id in product_ids:
        product = products[product_id]
        price, delivered, checked = matching[product_id]
        rows.append({
            "name": product.name,
            "url": settings.RIPRAPTOR_SITE_URL + product.get_absolute_url(),
            "image": email_image(product, words),
            "price": price_text(price, delivered, store),
            "checked": copy.get("watchlist.email.match.checked", store=store,
                                when=date_format(timezone.localtime(checked), "H:i, j M")),
        })
    return rows


# Sending ------------------------------------------------------------------------------------------

def tidy(now):
    """Delete alerts never confirmed within a week or confirmed over KEEP_DAYS ago, and the record of a
    product sent that has since stopped matching once SEND_AGAIN_DAYS have passed: it would be sent again
    if it came back, record or not."""
    SearchAlert.objects.filter(
        Q(confirmed_at__isnull=True, created_at__lt=now - timedelta(days=UNCONFIRMED_DAYS))
        | Q(confirmed_at__lt=now - timedelta(days=KEEP_DAYS))
    ).delete()
    SearchAlertSent.objects.filter(gone_at__isnull=False, sent_at__lt=now - timedelta(days=SEND_AGAIN_DAYS)).delete()


def claim(alert, due, records, now):
    """Mark this alert's email and products as sent, in one transaction, before sending. Returns the
    hand-back to undo it if the send fails, or None when another run claimed it first."""
    with transaction.atomic():
        if not SearchAlert.objects.filter(pk=alert.pk, sent_at=alert.sent_at).update(sent_at=now):
            return None
        again = [records[pid] for pid in due if pid in records]
        new = [pid for pid in due if pid not in records]
        if again:
            SearchAlertSent.objects.filter(pk__in=[r.pk for r in again]).update(sent_at=now, gone_at=None)
        SearchAlertSent.objects.bulk_create([SearchAlertSent(alert=alert, product_id=pid, sent_at=now) for pid in new])
    return {"sent_at": alert.sent_at, "again": [(r.pk, r.sent_at, r.gone_at) for r in again], "new": new}


def hand_back(alert, undo):
    with transaction.atomic():
        SearchAlert.objects.filter(pk=alert.pk).update(sent_at=undo["sent_at"])
        SearchAlertSent.objects.filter(alert=alert, product_id__in=undo["new"]).delete()
        for pk, sent_at, gone_at in undo["again"]:
            SearchAlertSent.objects.filter(pk=pk).update(sent_at=sent_at, gone_at=gone_at)


def send_due(now=None, stdout=None):
    """Email every confirmed alert with products newly matching it, one email each, and keep the record of
    what was sent up to date. Returns (sent, failed)."""
    now = now or timezone.now()
    tidy(now)
    alerts = list(SearchAlert.objects.filter(confirmed_at__isnull=False).select_related("game"))
    if not alerts:
        return 0, 0
    records = defaultdict(dict)
    for record in SearchAlertSent.objects.filter(alert__confirmed_at__isnull=False):
        records[record.alert_id][record.product_id] = record
    groups = defaultdict(list)
    for alert in alerts:
        groups[(alert.game_id, alert.product_type, alert.language)].append(alert)

    plans, gone = [], []
    for key, members in groups.items():
        offers, listed = group_offers(*key)
        for alert in members:
            matching = matches_for(alert, offers)
            mine = records.get(alert.pk, {})
            gone += [r.pk for pid, r in mine.items() if r.gone_at is None and pid in listed and pid not in matching]
            due = due_products(mine, matching, now)
            if due:
                # Cheapest first; a price plus delivery after a delivered one at the same figure.
                due.sort(key=lambda pid: (matching[pid][0], not matching[pid][1], pid))
                plans.append((alert, due, matching))
    if gone:
        SearchAlertSent.objects.filter(pk__in=gone).update(gone_at=now)
    if not plans:
        return 0, 0

    shown_ids = {pid for _, due, _ in plans for pid in due[:PRODUCTS_PER_EMAIL]}
    products = Product.objects.in_bulk(shown_ids)
    words = shop_words()
    store = copy.load_all()
    sent = failed = 0
    for alert, due, matching in plans:
        shown = [pid for pid in due if pid in products][:PRODUCTS_PER_EMAIL]
        if not shown:
            continue
        undo = claim(alert, due, records.get(alert.pk, {}), now)
        if undo is None:
            continue
        items = item_rows(shown, matching, products, words, store)
        subject, text, html, stop_url = match_mail(alert, items, len(due) - len(shown), store)
        try:
            mail.send(alert.email, subject, text, html, unsubscribe=stop_url)
        except mail.MailError as exc:
            hand_back(alert, undo)
            failed += 1
            if stdout:
                stdout.write(f"not sent to search alert {alert.pk}: {exc}")
            continue
        note("sent")
        sent += 1
    return sent, failed
