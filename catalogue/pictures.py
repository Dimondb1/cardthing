"""Product pictures that still load.

A product's picture is a shop's own picture, linked, never copied: Product.image_url, set by the first read that
found one (importers.apply_offers). A shop that deletes or renames the file leaves the address dead, and every list
showed a broken picture with the product's name spilling out of it: the owner, 11 Nov 2026, with Pitch Black and
Ascended Heroes in the sets on the home page.

check() looks at products' pictures from tidy_all every hour, those on sale first, each again after CHECK_EVERY. A
picture the shop says is gone (404 or 410, or a page in place of a picture) is put aside (Product.image_failed) and
taken off, and the product gets another shop's picture of it when one is known and loads (a linked shop page's,
ShopProduct). Otherwise it shows its outline until the next read of any shop that lists it gives it that shop's
picture, which apply_offers does for a product without one and never with the address put aside. Any other answer (a
slow or busy shop, a refusal) is asked again a day later: only the shop saying the file is gone takes a picture off.

The browser does its part as well (site.js): a picture that will not load at all gives way to its product type's
outline, never a broken picture.
"""

import logging
import time
import urllib.error
import urllib.request
from datetime import timedelta

from django.db.models import Exists, F, OuterRef
from django.utils import timezone

logger = logging.getLogger(__name__)

CHECK_EVERY = timedelta(days=7)
# A picture whose shop did not answer plainly is asked again after this long.
RETRY_AFTER = timedelta(days=1)
# Each hour: at most this many pictures, in at most this many seconds, each answer waited for at most TIMEOUT.
PER_RUN = 400
BUDGET_SECONDS = 120
TIMEOUT = 8
OK, GONE, UNKNOWN = "ok", "gone", "unknown"


def look(url, timeout=TIMEOUT):
    """OK, GONE or UNKNOWN for the picture at ``url``: a HEAD request, or the first byte where a server refuses
    HEAD. GONE only for 404, 410, or a page sent in place of a picture."""
    from .importers import USER_AGENT, safe_url

    if not url.lower().startswith(("http://", "https://")):
        return UNKNOWN
    address = safe_url(url)
    for method, headers in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
        request = urllib.request.Request(address, method=method, headers={"User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if method == "GET":
                    response.read(1)
                return GONE if kind.startswith("text/html") else OK
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return GONE
            if exc.code in (405, 501) and method == "HEAD":
                continue   # a server that will not answer HEAD: ask for the first byte instead
            return UNKNOWN
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.info("Picture check: no answer for %s (%s)", url, exc)
            return UNKNOWN
    return UNKNOWN


def due(now, limit=PER_RUN):
    """The products whose picture is due a look, those on sale now first, then the longest unchecked."""
    from .models import Listing, Product

    on_sale = Listing.objects.filter(product=OuterRef("pk"), is_active=True, availability__in=Listing.BUYABLE)
    return list(
        Product.objects.filter(is_active=True, image="").exclude(image_url="")
        .exclude(image_checked_at__gt=now - CHECK_EVERY)
        .annotate(on_sale=Exists(on_sale))
        .order_by("-on_sale", F("image_checked_at").asc(nulls_first=True), "pk")
        .only("pk", "image", "image_url", "image_failed", "image_checked_at")[:limit]
    )


def another_picture(product, look=look):
    """Another shop's picture of ``product`` that loads now, from the shop pages linked to it, or ""."""
    from django.db.models import Q

    from .models import ShopProduct

    seen = {product.image_url, product.image_failed}
    linked = ShopProduct.objects.filter(Q(product=product) | Q(suggested=product), status=ShopProduct.Status.LINKED)
    for url in (linked.exclude(image_url="")
                .order_by("-last_seen").values_list("image_url", flat=True)[:5]):
        if url in seen:
            continue
        seen.add(url)
        if look(url) == OK:
            return url
    return ""


def check(now=None, limit=PER_RUN, budget=BUDGET_SECONDS, look=look, dry_run=False, log=None):
    """Look at the pictures due a look (due). Returns {"checked", "gone", "replaced"}."""
    from .models import Product

    now = now or timezone.now()
    stop = time.monotonic() + budget
    done = {"checked": 0, "gone": 0, "replaced": 0}
    for product in due(now, limit):
        if time.monotonic() > stop:
            break
        answer = look(product.image_url)
        done["checked"] += 1
        if answer == UNKNOWN:
            if not dry_run:
                Product.objects.filter(pk=product.pk).update(image_checked_at=now - CHECK_EVERY + RETRY_AFTER)
            continue
        if answer == OK:
            if not dry_run:
                Product.objects.filter(pk=product.pk).update(image_checked_at=now)
            continue
        done["gone"] += 1
        replacement = another_picture(product, look=look)
        if replacement:
            done["replaced"] += 1
        if log:
            log(f"{'would take off' if dry_run else 'took off'} a gone picture: {product.pk} {product.image_url}"
                + (f" -> {replacement}" if replacement else ""))
        if dry_run:
            continue
        product = Product.objects.get(pk=product.pk)
        product.image_failed = product.image_url[:1000]
        product.image_url = replacement[:1000]
        product.image_checked_at = now
        # Through save, so the product page's updated_at moves: its picture changed.
        product.save(update_fields=["image_failed", "image_url", "image_checked_at"])
    return done
