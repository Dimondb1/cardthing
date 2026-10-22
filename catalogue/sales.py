"""
Affiliate sales from a network's report: what clicks to shops earned (AffiliateSale).

The owner downloads a report of transactions from a network (Awin, CJ, eBay Partner Network, Amazon
Associates) as a CSV file and imports it in admin. Each row becomes a sale, or updates the sale already
imported with the same reference, since a network moves a sale from pending to approved or declined later.

Columns are found by their heading, whatever the network calls them (HEADINGS): the date, the shop, the
commission, the status, the order value, the network's reference and the click reference. A file without a
shop column (eBay's own report) takes the shop chosen on the import form. The click reference is the number
our /go/ link put in the shop's link ({click} in its affiliate link format, eBay's custom id): it matches the
sale to its click, and so to the product and game.

Nothing about the customer is read or kept.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .models import AffiliateSale, Game, OutboundClick, Retailer

HEADINGS = {
    "ordered_on": ("date", "order date", "transaction date", "event date", "sale date", "booking date"),
    "retailer": ("retailer", "shop", "advertiser", "advertiser name", "merchant", "merchant name", "programme",
                 "program", "advertiser_name"),
    "commission": ("commission", "commission amount", "earnings", "publisher commission", "commission (gbp)",
                   "commission_amount"),
    "status": ("status", "commission status", "transaction status", "state"),
    "order_value": ("order value", "sale amount", "sale value", "order amount", "sales", "sale_amount"),
    "reference": ("reference", "transaction id", "transaction", "order reference", "order id", "id",
                  "transaction_id"),
    "click": ("click ref", "clickref", "click reference", "custom id", "customid", "sub id", "subid",
              "click_ref", "epc ref"),
    "game": ("game",),
}
APPROVED = {"approved", "confirmed", "paid", "validated", "locked", "closed", "complete", "completed"}
DECLINED = {"declined", "rejected", "cancelled", "canceled", "void", "voided", "reversed", "returned"}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y")


@dataclass
class Result:
    added: int = 0
    updated: int = 0
    skipped: list = field(default_factory=list)   # (line number, reason)


def heading_map(headings):
    """{field: column index} for the headings found."""
    found = {}
    cleaned = [re.sub(r"\s+", " ", (heading or "").strip().lower().lstrip("﻿")) for heading in headings]
    for name, aliases in HEADINGS.items():
        for index, heading in enumerate(cleaned):
            if heading in aliases:
                found[name] = index
                break
    return found


def money(text):
    cleaned = re.sub(r"[£$€,\s]|GBP|EUR|USD", "", text or "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def day(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    first = text.split()[0] if re.match(r"\d", text) else text
    for candidate in (first, text):
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def status_of(text):
    word = (text or "").strip().lower()
    if word in APPROVED:
        return AffiliateSale.Status.APPROVED
    if word in DECLINED:
        return AffiliateSale.Status.DECLINED
    return AffiliateSale.Status.PENDING


def find_retailer(name, retailers):
    """The shop whose name matches, ignoring case, spaces and punctuation, or None."""
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if not key:
        return None
    return retailers.get(key)


def import_report(data, default_retailer=None, today=None):
    """Import a network report (CSV text or bytes). Returns a Result: sales added, sales updated (same shop and
    reference), and the rows skipped with why."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(data)))
    result = Result()
    if not rows:
        result.skipped.append((1, "The file is empty."))
        return result
    columns = heading_map(rows[0])
    missing = [name for name in ("ordered_on", "commission") if name not in columns]
    if "retailer" not in columns and default_retailer is None:
        missing.append("retailer")
    if missing:
        result.skipped.append((1, "No column for: " + ", ".join(missing) + "."))
        return result
    retailers = {re.sub(r"[^a-z0-9]", "", r.name.lower()): r for r in Retailer.objects.all()}
    games = {g.name.lower(): g for g in Game.objects.all()} | {g.slug: g for g in Game.objects.all()}
    today = today or date.today()

    def cell(row, name):
        index = columns.get(name)
        return row[index].strip() if index is not None and index < len(row) else ""

    for line, row in enumerate(rows[1:], start=2):
        if not any(value.strip() for value in row):
            continue
        # A shop named that is not one of ours is skipped, never put down to the shop chosen on the form.
        name = cell(row, "retailer")
        retailer = find_retailer(name, retailers) if name else default_retailer
        if retailer is None:
            result.skipped.append((line, f"No shop called {cell(row, 'retailer') or '(blank)'}."))
            continue
        ordered_on = day(cell(row, "ordered_on"))
        commission = money(cell(row, "commission"))
        if ordered_on is None or ordered_on > today:
            result.skipped.append((line, "No date, or one in the future."))
            continue
        if commission is None:
            result.skipped.append((line, "No commission."))
            continue
        click = None
        click_ref = re.sub(r"\D", "", cell(row, "click"))
        if click_ref:
            # Only a click from a person carried our tracking, so only one of those can have led to a sale.
            click = OutboundClick.objects.people().filter(pk=int(click_ref), retailer=retailer).first()
        values = {
            "ordered_on": ordered_on, "commission": commission, "status": status_of(cell(row, "status")),
            "order_value": money(cell(row, "order_value")), "click": click,
            "game": games.get(cell(row, "game").lower()) if cell(row, "game") else None,
        }
        reference = cell(row, "reference")[:80]
        existing = AffiliateSale.objects.filter(retailer=retailer, reference=reference).first() if reference else None
        if existing is not None:
            for name, value in values.items():
                if name in ("click", "game") and value is None:
                    continue
                setattr(existing, name, value)
            existing.save()
            result.updated += 1
        else:
            AffiliateSale.objects.create(retailer=retailer, reference=reference, **values)
            result.added += 1
    return result
