"""Product pictures at the size they are shown.

Shops give us the address of their full-size original, often several megabytes. Two image CDNs can serve the
same picture smaller from a different address, so a card asks for a card-sized copy and the product page for a
hero-sized one. The picture itself never changes: only the width the CDN sends.

- Shopify (cdn.shopify.com, and shop domains serving /cdn/shop/ paths) takes a ``width`` query parameter. It
  keeps the proportions, never enlarges past the original and picks WebP or AVIF when the browser accepts them.
- BigCommerce (cdnNN.bigcommerce.com) names the size in the path: /images/stencil/500x659/... becomes
  /images/stencil/400w/... It never enlarges past the original either. Only addresses that already use
  /images/stencil/ are changed; its other address forms are left alone.

Every other address, including our own uploads, is returned unchanged.
"""

import re
from urllib.parse import urlsplit, urlunsplit

BIGCOMMERCE_HOST = re.compile(r"^cdn\d*\.bigcommerce\.com$", re.IGNORECASE)
BIGCOMMERCE_STENCIL = re.compile(r"^(/s-[^/]+/images/stencil/)([^/]+)(/.+)$")

# Where a product picture is shown: (the width of the copy in ``src``, ``sizes``, the widths offered in ``srcset``).
# ``sizes`` is the width of the picture itself, not of its box: every slot shows a square picture with
# object-fit: contain, so in a wide box (cover, swipe) the picture is as wide as the box is tall, and the
# padding round it is left out. The figures were measured in Chromium at 320 to 1920px wide and fitted to
# straight lines between the breakpoints site.css already uses; web/tests_images.py keeps them in step.
#   48rem, 60rem  the tablet and desktop breakpoints in site.css.
#   74rem         --page-width (70rem) plus two --gutter-wide (2rem): from here the page stops growing.
#   30rem         .deck-page's max-width.
#   thumb  .product-image--thumb is 3rem.
#   row    .product-image--row is 4rem, 5.5rem from 48rem.
#   card   .card__top gives the picture 38.2% of the card: one card across on a phone, two from 48rem.
#   trend  .trending__item is 1/1.8 of a phone's width, a fifth of the page (at most 10rem of picture) from 48rem.
#   cover  .product-image--cover is a 16:9 box, so a square picture is as wide as the box is tall: two tiles
#          across, three from 48rem, more from 60rem.
#   large  the product page's picture well: 17rem on a phone; from 60rem as tall as the name and price box
#          beside it, measured at 24 to 32rem.
#   swipe  .swipe__image: as tall as the screen allows on a phone, up to 26rem inside the 30rem deck, and about
#          19rem from 48rem where the card has a fixed height.
# Each slot offers enough widths that a 2x screen at its largest box has a sharp copy, and a 1x screen does not
# download one far bigger than it shows.
SLOTS = {
    "thumb": (100, "3rem", (100, 200)),
    "row": (100, "(min-width: 48rem) 5.5rem, 4rem", (100, 200)),
    "card": (200, "(min-width: 74rem) 9.75rem, (min-width: 48rem) calc(16.8vw - 2.8rem), calc(33.4vw - 2.6rem)",
             (200, 400, 600)),
    "trend": (200, "(min-width: 48rem) 10rem, calc(49vw - 3.2rem)", (200, 400, 600, 800)),
    "cover": (200, "(min-width: 74rem) 4.5rem, (min-width: 60rem) calc(7.8vw - 1.4rem), "
                   "(min-width: 48rem) calc(12.6vw - 1.65rem), calc(18.6vw - 1.6rem)", (100, 200, 400)),
    "large": (600, "(min-width: 60rem) 30rem, 17rem", (600, 1200)),
    "swipe": (400, "(min-width: 48rem) 19rem, (min-width: 30rem) 26rem, 80vw", (400, 600, 800)),
}

# Social previews and search engines want a large picture: about 1200 pixels wide.
SHARE_WIDTH = 1200
# Emails show the picture at 200 CSS pixels at most; twice that keeps it sharp on a phone.
EMAIL_WIDTH = 400


def _is_shopify(parts):
    return parts.netloc.lower() == "cdn.shopify.com" or parts.path.startswith("/cdn/shop/")


def sized_image_url(url, width):
    """The address of ``url`` at ``width`` pixels wide where its CDN can resize it, else ``url`` unchanged."""
    if not url or not width:
        return url or ""
    try:
        width = int(width)
    except (TypeError, ValueError):
        return url
    if width <= 0:
        return url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return url
    if _is_shopify(parts):
        # The rest of the query (v=, the shop's cache key) is kept byte for byte; any width already there goes.
        kept = [pair for pair in parts.query.split("&") if pair and pair.split("=", 1)[0] != "width"]
        return urlunsplit(parts._replace(query="&".join(kept + [f"width={width}"])))
    if BIGCOMMERCE_HOST.match(parts.netloc):
        match = BIGCOMMERCE_STENCIL.match(parts.path)
        if match:
            return urlunsplit(parts._replace(path=f"{match.group(1)}{width}w{match.group(3)}"))
    return url


def can_resize(url):
    """True when ``sized_image_url`` would give a smaller copy of ``url``."""
    return bool(url) and sized_image_url(url, 1) != url


def responsive(url, slot):
    """``src``, ``srcset`` and ``sizes`` for a picture shown in ``slot`` (a key of SLOTS), and ``full``, the
    address as we hold it, for the page to fall back to if the CDN cannot resize it (a shop that replaced
    the file can leave the old address cached and the resized ones missing).

    ``srcset`` and ``sizes`` are empty when the address cannot be resized, so the page shows it as it is.
    """
    width, sizes, widths = SLOTS[slot]
    url = url or ""
    if not can_resize(url):
        return {"src": url, "srcset": "", "sizes": "", "full": url}
    srcset = ", ".join(f"{sized_image_url(url, each)} {each}w" for each in widths)
    return {"src": sized_image_url(url, width), "srcset": srcset, "sizes": sizes, "full": url}
