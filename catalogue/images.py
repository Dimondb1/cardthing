"""Product pictures at the size they are shown.

Shops give us the address of their full-size original, often several megabytes. Two image CDNs can serve the
same picture smaller from a different address, so a card asks for a card-sized copy and the product page for a
hero-sized one. The picture itself never changes: only the width the CDN sends.

- Shopify (cdn.shopify.com, and shop domains serving /cdn/shop/ paths) takes a ``width`` query parameter. It
  keeps the proportions, never enlarges past the original and picks WebP or AVIF when the browser accepts them.
- BigCommerce (cdnNN.bigcommerce.com) names the size in the path: /images/stencil/500x659/... becomes
  /images/stencil/400w/... It never enlarges past the original either. Its older address for the same file,
  /products/<product>/images/<image>/<name>__<n>.<n>.<w>.<h>.jpg?c=1, is not resized by the CDN, so it is
  rewritten to the stencil form, /images/stencil/400w/products/<product>/<image>/<name>__<n>.<n>.jpg?c=1:
  checked on a picture ripraptor.com showed, both addresses give the same picture. Other forms are left alone.
- Unicorn Cards (nopCommerce) cannot resize to any width: it keeps a few fixed copies of each picture, named by
  their longest side, /images/thumbs/<picture>_960.jpeg and /images/thumbs/<picture>_415.jpeg. We hold the 960
  copy, so a slot that needs 415 pixels or fewer gets the 415 one (a quarter of the bytes). The shop makes a
  copy only for the sizes its own pages use: in October 2026 the 415 copy was there for all 14 pictures tried,
  the 550 copy for none and the 100 copy not for every one, so only 415 and 960 are offered.

Every other address, including our own uploads, is returned unchanged.
"""

import re
from urllib.parse import urlsplit, urlunsplit

BIGCOMMERCE_HOST = re.compile(r"^cdn\d*\.bigcommerce\.com$", re.IGNORECASE)
BIGCOMMERCE_STENCIL = re.compile(r"^(/s-[^/]+/images/stencil/)([^/]+)(/.+)$")
# The older product image address: the store, the product, the image, and the file name with the original's
# width and height before the extension.
BIGCOMMERCE_PRODUCT = re.compile(
    r"^(/s-[^/]+)/products/(\d+)/images/(\d+)/([^/]+__\d+\.\d+)\.\d+\.\d+(\.(?:jpe?g|png|gif|webp))$", re.IGNORECASE)
# Unicorn Cards' fixed copies: the host, then the picture's address with its size (the longest side) cut out.
NOPCOMMERCE_HOSTS = ("unicorncards.co.uk", "www.unicorncards.co.uk")
NOPCOMMERCE_THUMB = re.compile(r"^(/images/thumbs/[^/]+_)960(\.(?:jpe?g|png|gif|webp))$", re.IGNORECASE)
NOPCOMMERCE_SIZES = (415, 960)

# The widths a copy can be asked for: each about 1.25 to 1.3 times the one before, so whatever the box and the
# screen, the copy a browser picks (the smallest at least as wide as it draws, in Chromium) is never more than
# about 1.3 times the pixels it draws. Lighthouse flagged the old steps (200, 400, 600), which sent a 412px
# phone a copy up to 1.8 times too wide.
STEPS = (50, 65, 80, 100, 130, 160, 200, 260, 330, 420, 540, 700, 900, 1050, 1200)


def steps(low, high):
    """The STEPS a slot offers: from the first at least ``low`` (its smallest picture on a 1x screen) to the
    first at least ``high`` (its largest on a sharp screen)."""
    first = next(i for i, width in enumerate(STEPS) if width >= low)
    last = next((i for i, width in enumerate(STEPS) if width >= high), len(STEPS) - 1)
    return STEPS[first:last + 1]


# Where a product picture is shown: (the width of the copy in ``src``, ``sizes``, the widths offered in ``srcset``).
# ``sizes`` is the width of the picture itself, not of its box: every slot shows a square picture with
# object-fit: contain, so in a wide box (cover, swipe) the picture is as wide as the box is tall, and the
# padding round it is left out. The figures were measured in Chromium at 320 to 1920px wide (October 2026, after
# the home page redesign) and fitted to straight lines between the breakpoints site.css already uses;
# web/tests_images.py keeps them in step.
#   48rem, 60rem  the tablet and desktop breakpoints in site.css.
#   74rem         --page-width (70rem) plus two --gutter-wide (2rem): from here the page stops growing.
#   30rem         .deck-page's max-width.
#   thumb    .product-image--thumb is 3rem.
#   row      .product-image--row is 4rem, 5.5rem from 48rem.
#   compact  a row in .results--compact (the product page's related products) is 3.5rem at every width.
#   card     .card__top gives the picture 38.2% of the card: one card across on a phone, two from 48rem.
#   trend    .trending__item is 1/1.8 of a phone's width; from 48rem a fifth of the page, at least 8.8rem, and
#            11.4rem once the page stops growing.
#   cover    .product-image--cover is a 16:9 box, so a square picture is as wide as the box is tall: two tiles
#            across, three from 48rem, more from 60rem.
#   type     .product-image--type fills its tile's picture well: half a phone's width less the gaps, a fifth of
#            the page from 48rem, 11rem once the page stops growing.
#   large    the product page's picture well: 17rem on a phone; from 60rem as tall as the name and price box
#            beside it, 22 to 35rem by how many shops it lists. 33rem takes the 540 copy on a 1x screen, sharp for all of them.
#   spotlight the home page's drop spotlight: a fan of the set's boxes, 5.5rem a picture on a phone, 8rem from 48rem.
#   swipe    .swipe__image: as tall as the screen allows on a phone, up to 27.5rem inside the 30rem deck, and
#            about 20.5rem from 48rem where the card has a fixed height.
# Each slot offers the steps from its smallest picture on a 1x screen to its largest on a 3x phone, so a sharp
# screen always has a sharp copy and no screen downloads one far bigger than it shows.
SLOTS = {
    "thumb": (100, "3rem", steps(48, 144)),
    "row": (100, "(min-width: 48rem) 5.5rem, 4rem", steps(64, 264)),
    "compact": (100, "3.5rem", steps(56, 168)),
    "card": (200, "(min-width: 74rem) 10.95rem, (min-width: 48rem) calc(19.1vw - 3.2rem), calc(38.2vw - 3rem)",
             steps(74, 735)),
    "trend": (200, "(min-width: 74rem) 11.4rem, (min-width: 60rem) calc(20vw - 3.4rem), (min-width: 48rem) 8.8rem, "
                   "calc(55.6vw - 3.6rem)", steps(120, 1106)),
    "cover": (200, "(min-width: 74rem) 5.65rem, (min-width: 60rem) calc(10.23vw - 1.93rem), "
                   "(min-width: 48rem) calc(16.07vw - 2.14rem), calc(23.4vw - 1.95rem)", steps(44, 445)),
    "type": (200, "(min-width: 74rem) 11rem, (min-width: 48rem) calc(20vw - 3.5rem), calc(50vw - 3.5rem)",
             steps(96, 600)),
    "large": (540, "(min-width: 60rem) 33rem, 17rem", steps(272, 1440)),
    "swipe": (420, "(min-width: 48rem) 20.5rem, (min-width: 30rem) 27.5rem, 85vw", steps(245, 1362)),
    "spotlight": (200, "(min-width: 48rem) 8rem, 5.5rem", steps(88, 384)),
}

# Social previews and search engines want a large picture: about 1200 pixels wide.
SHARE_WIDTH = 1200
# Emails show the picture at 200 CSS pixels at most; twice that keeps it sharp on a phone.
EMAIL_WIDTH = 400


def _is_shopify(parts):
    return parts.netloc.lower() == "cdn.shopify.com" or parts.path.startswith("/cdn/shop/")


def _fixed_sizes(parts):
    """The widths a shop that keeps fixed copies has of this picture, or None when it can be resized freely
    (or not at all)."""
    if parts.netloc.lower() in NOPCOMMERCE_HOSTS and NOPCOMMERCE_THUMB.match(parts.path):
        return NOPCOMMERCE_SIZES
    return None


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
        match = BIGCOMMERCE_PRODUCT.match(parts.path)
        if match:
            store, product, image, name, extension = match.groups()
            path = f"{store}/images/stencil/{width}w/products/{product}/{image}/{name}{extension}"
            return urlunsplit(parts._replace(path=path))
    if _fixed_sizes(parts):
        # The smallest copy at least ``width`` wide; the 960 one (the address as we hold it) past that.
        size = next((each for each in NOPCOMMERCE_SIZES if each >= width), NOPCOMMERCE_SIZES[-1])
        match = NOPCOMMERCE_THUMB.match(parts.path)
        return urlunsplit(parts._replace(path=f"{match.group(1)}{size}{match.group(2)}"))
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
    fixed = _fixed_sizes(urlsplit(url))
    if fixed:
        # Only the copies the shop has: listing a width it lacks would name one copy at several sizes.
        widths = fixed
    srcset = ", ".join(f"{sized_image_url(url, each)} {each}w" for each in widths)
    return {"src": sized_image_url(url, width), "srcset": srcset, "sizes": sizes, "full": url}
