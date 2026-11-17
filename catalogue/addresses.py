"""
Product addresses the owner pastes: which product an address is now, and where a switched-off page's address
should lead.

Used by Merge two products (web/merge_box.py) and by the product form in Admin, where switching a page off can
send its address to the live page of the same product (the audit of 17 Nov 2026: an address Google had shown
answered not found while the same product was live at another).
"""

import re
from urllib.parse import urlsplit

from .models import Product, ProductAlias

SLUG = re.compile(r"^[-a-zA-Z0-9_]{1,220}$")


def slug_of(text):
    """The product address in what the owner pasted: a whole link, a path or the last part alone. "" when it is
    none of these."""
    text = (text or "").strip()
    if not text:
        return ""
    path = urlsplit(text).path if "/" in text else text
    parts = [part for part in path.split("/") if part]
    if "products" in parts:
        parts = parts[parts.index("products") + 1:]
    slug = parts[0] if parts else ""
    return slug.lower() if SLUG.match(slug) else ""


def find(text):
    """What an address is now: {"slug", "product", "old"}. ``product`` is the product at that address, on the site
    or switched off; or, for an old address that already leads somewhere, the product it leads to (``old`` True);
    or None when the address is no product at all."""
    slug = slug_of(text)
    found = {"slug": slug, "product": None, "old": False}
    if not slug:
        return found
    products = Product.objects.select_related("game", "product_set")
    found["product"] = products.filter(slug=slug).first()
    if found["product"] is None or not found["product"].is_active:
        alias = ProductAlias.objects.filter(slug=slug, product__is_active=True).values_list("product_id", flat=True).first()
        if alias is not None:
            found["product"], found["old"] = products.get(pk=alias), True
    return found


def send_to(slug, product):
    """Send the address ``slug`` to ``product`` from now on: an old product address, as a merge leaves."""
    ProductAlias.objects.update_or_create(slug=slug, defaults={"product": product})
