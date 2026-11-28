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

refuse() is the owner's own word on a picture that loads but is no good: a shop's photo of the shipping carton in
place of the product. The owner, 28 Nov 2026, of such a photo standing for a whole set on the home page: "I would
like images to be better and bigger and not crap". The address is kept with the product (Product.image_refused) and
never given back to it, by a read or by this check, and the product takes another shop's picture as above.
"""

import logging
import re
import time
import urllib.error
import urllib.request
from datetime import timedelta

from django.db.models import Exists, F, OuterRef
from django.utils import timezone

logger = logging.getLogger(__name__)

# A product sold by the case, the carton or the counter display is pictured, as often as not, by a photo of the brown
# carton it ships in, which says nothing of its set. The owner, 28 Nov 2026, of such a photo standing for 30th
# Celebration under Sets people want now: "I would like images to be better and bigger and not crap". Such a product
# pictures its set, or its type's tile, only when no other product there has a picture (web.compact.cover_rank,
# web.feed.set_tile_rows). "Booster Display" is a booster box.
CARTON_NAME = re.compile(r"\b(?:cases?|cartons?|pallets?)\b"
                         r"|\b(?:blister|checklane|sleeved|counter|gravity\s+feed)\b.*\bdisplay\b"
                         r"|\bdisplay\s+of\s+\d", re.I)


def carton_named(name):
    """Whether a product's name says it is sold by the case, the carton or the counter display (CARTON_NAME)."""
    return bool(CARTON_NAME.search(name or ""))


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
    from . import importers

    if not url.lower().startswith(("http://", "https://")):
        return UNKNOWN
    address = importers.safe_url(url)
    for method, headers in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
        request = urllib.request.Request(address, method=method,
                                         headers={"User-Agent": importers.USER_AGENT, **headers})
        try:
            # Only a public web address, as every shop read (importers.open_public).
            with importers.open_public(request, timeout=timeout) as response:
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
        except (urllib.error.URLError, OSError, ValueError, importers.ImportError_) as exc:
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
        .only("pk", "image", "image_url", "image_failed", "image_refused", "image_checked_at")[:limit]
    )


def another_picture(product, look=look):
    """Another shop's picture of ``product`` that loads now, from the shop pages linked to it, or ""."""
    from django.db.models import Q

    from .models import ShopProduct

    seen = {product.image_url, *product.pictures_not_to_use()}
    linked = ShopProduct.objects.filter(Q(product=product) | Q(suggested=product), status=ShopProduct.Status.LINKED)
    for url in (linked.exclude(image_url="")
                .order_by("-last_seen").values_list("image_url", flat=True)[:5]):
        if url in seen:
            continue
        seen.add(url)
        if look(url) == OK:
            return url
    return ""


# Refused addresses kept a product: far more than any product has shops, so the field never grows without end.
REFUSED_MOST = 40


def refuse(product, now=None, look=look):
    """The owner says the product's picture is no good. Its address is put aside for good and the product takes
    another shop's picture that loads (another_picture), else none: it shows its outline until the next read of a
    shop that lists it gives it that shop's picture. Returns "replaced", "outline", or "" when there was nothing to
    refuse (no shop picture, or the owner's own upload, which is his to change)."""
    if product.image or not product.image_url:
        return ""
    kept = [*product.image_refused.split(), product.image_url[:1000]]
    product.image_refused = "\n".join(dict.fromkeys(kept[-REFUSED_MOST:]))
    replacement = another_picture(product, look=look)
    product.image_url = replacement[:1000]
    product.image_checked_at = (now or timezone.now()) if replacement else None
    # Through save, so the product page's updated_at moves and the cached lists are cleared: its picture changed.
    product.save(update_fields=["image_refused", "image_url", "image_checked_at"])
    return "replaced" if replacement else "outline"


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
