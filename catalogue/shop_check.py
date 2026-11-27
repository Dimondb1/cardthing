"""
The shop check: each shop's public record, looked up the way a shopper would, and the label the owner gives the shop
from it. The owner, 27 Nov 2026, told that a shop leading the deals was badly reviewed: "it just opens a question
about giving the user a bit more confidence in what they're buying and what shop they're buying from", and then:
"Look each shop up the way a person would (search, read what comes back) ... and use that to propose our own label
per shop: Checked, New or Caution. You confirm each one in Admin".

How it works. A search for each shop's reviews is run by hand or by an assistant (never a scraper: review sites'
pages are not read by a program, and their scores are not ours to publish). What it turned up is written as a file
of findings, one entry a shop, with the links it rests on and a label proposed by fixed rules:

- Caution: mostly negative public reviews (an average under 3 from at least 5), or two or more independent reports of
  orders not arriving, refunds not paid or fake goods, or an official warning;
- Checked: an average of 4 or more from at least 20 reviews on an independent review site, or a long-established
  retailer with no pattern of complaints;
- New: everything else, most often too little public information to say.

The file is loaded on the Shop check page in Admin (web/shop_check.py; load below), which shows the owner each
finding with its links. He confirms, changes or ignores each: only then is the shop's own label set (Retailer.trust).
A shop looked up again later takes the newer finding; when that proposes a different label from the one he gave, the
shop is put in front of him again (differs below), and his label stays until he changes it.
The findings are research about named businesses, so the file is never part of the code, and nothing in ShopCheck is
shown to visitors.
"""

import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.utils import timezone

from .models import RemovedShop, Retailer, ShopCheck

MOST = 2000              # entries a file may hold
SOURCES_MOST = 12        # links kept for an entry
LABELS = set(Retailer.Trust.values)
CONFIDENCE = {"high", "medium", "low"}


def web_link(value, most=300):
    """``value`` when it is a plain web address (http or https, with a host) no longer than ``most``, else "". A
    finding's links are shown as links in Admin, so nothing else may be one."""
    value = str(value or "").strip()
    if len(value) > most or any(ch.isspace() for ch in value):
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    return value if parts.scheme in ("http", "https") and parts.hostname else ""


def words(value, most):
    """Plain text of at most ``most`` characters: one line's worth of spaces, nothing unprintable."""
    text = " ".join("".join(ch for ch in str(value or "") if ch.isprintable() or ch.isspace()).split())
    return text[:most].strip()


def number(value, low, high):
    """A rating as a decimal between ``low`` and ``high``, or None."""
    try:
        found = Decimal(str(value)).quantize(Decimal("0.1"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return found if low <= found <= high else None


def count(value):
    try:
        found = int(value)
    except (TypeError, ValueError):
        return None
    return found if 0 <= found <= 10_000_000 else None


def entries(text):
    """The entries of a findings file: a JSON list of objects. Raises ValueError with a line for the owner when it
    is not one."""
    try:
        found = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("That is not a findings file: it could not be read as JSON.") from exc
    if isinstance(found, dict):
        found = found.get("shops")
    if not isinstance(found, list):
        raise ValueError("That is not a findings file: it should be a list of shops.")
    if len(found) > MOST:
        raise ValueError(f"That file has {len(found):,} entries. The most a file may hold is {MOST:,}.")
    return [entry for entry in found if isinstance(entry, dict)]


def shops_by_key():
    """({slug: shop}, {host: shop}) for every shop, to match an entry by its slug, else by its website."""
    by_slug, by_host = {}, {}
    for shop in Retailer.objects.all():
        by_slug[shop.slug] = shop
        host = RemovedShop.host_of(shop.website)
        if host:
            by_host.setdefault(host, shop)
    return by_slug, by_host


def load(text, now=None):
    """Take a findings file in: each entry that names a shop here (by its slug, else its website) and proposes one of
    the three labels becomes that shop's ShopCheck, replacing an earlier one. Returns {"saved": n, "unknown": [names
    of entries for no shop here], "bad": [names of entries without a label we know]}. Sets no shop's label: the owner
    does that. A finding he has answered stays answered when the newer one proposes the same label, and is not
    when it proposes another (differs below). Raises ValueError when the text is no findings file."""
    now = now or timezone.now()
    by_slug, by_host = shops_by_key()
    earlier = {found.retailer_id: found for found in ShopCheck.objects.all()}
    done = {"saved": 0, "unknown": [], "bad": []}
    for entry in entries(text):
        name = words(entry.get("name") or entry.get("slug") or entry.get("website"), 120) or "(no name)"
        shop = by_slug.get(str(entry.get("slug") or "")) or by_host.get(RemovedShop.host_of(str(entry.get("website") or "")))
        if shop is None:
            done["unknown"].append(name)
            continue
        label = str(entry.get("label") or "").strip().lower()
        if label not in LABELS:
            done["bad"].append(name)
            continue
        sources = entry.get("sources") if isinstance(entry.get("sources"), list) else []
        links = list(dict.fromkeys(link for link in (web_link(source, 500) for source in sources) if link))
        confidence = str(entry.get("confidence") or "").strip().lower()
        before = earlier.get(shop.pk)
        ShopCheck.objects.update_or_create(retailer=shop, defaults={
            "label": label,
            "reason": words(entry.get("reason"), 300),
            "review_site": "" if str(entry.get("review_site") or "").strip().lower() == "none"
                           else words(entry.get("review_site"), 40),
            "rating_seen": number(entry.get("rating_seen"), Decimal("0"), Decimal("5")),
            "review_count_seen": count(entry.get("review_count_seen")),
            "reviews_url": web_link(entry.get("reviews_url")),
            "company": words(entry.get("company"), 200),
            "complaints": words(entry.get("complaints"), 1000),
            "sources": links[:SOURCES_MOST],
            "confidence": confidence if confidence in CONFIDENCE else "",
            "notes": words(entry.get("notes"), 1000),
            "loaded_at": now,
            # A second look that proposes what the first did is nothing new to him: his answer to it stands.
            "answered": bool(before is not None and before.answered and before.label == label),
        })
        done["saved"] += 1
    return done


def confirm(shop, label, note="", reviews_url="", today=None):
    """The owner's label for ``shop``: one of Retailer.Trust, or "" to take it off. The reason and the link to its
    public reviews are his to word; the day is today's. The shop's finding is answered: only one loaded later can
    put the shop in front of him again (differs below)."""
    label = label if label in LABELS else ""
    shop.trust = label
    shop.trust_note = words(note, 200) if label else ""
    shop.trust_on = (today or timezone.localdate()) if label else None
    shop.reviews_url = web_link(reviews_url)
    shop.save(update_fields=["trust", "trust_note", "trust_on", "reviews_url"])
    ShopCheck.objects.filter(retailer=shop).update(answered=True)
    return shop


def differs(shop, finding):
    """True when ``finding`` proposes a label the owner has not answered, different from the one he gave ``shop``:
    a second look found something the first did not, so he should read it rather than have his earlier answer hide
    it. A proposal he already read and chose against is not raised again."""
    return bool(shop.trust and finding is not None and not finding.answered and finding.label != shop.trust)
