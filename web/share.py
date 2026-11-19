"""The words a share fills in for the visitor: the text under a Reddit post's title (product page and cards)."""

import re
from decimal import Decimal

from catalogue.discord import when_words
from catalogue.models import Listing
from content import service as copy


def reddit_text(cheapest, store=None):
    """The text under a shared Reddit post's title, so it reads as a post and not a bare link: the cheapest
    price now and when it was checked, as the page shows them, or that no shop has it. Like the Discord posts
    it never names the shop: the page compares every one. The visitor can change it before posting."""
    if cheapest is None:
        return copy.get("product.share.reddit_text_none", store=store)
    price = copy.get("product.share.reddit_price" if cheapest.delivery_known else "product.share.reddit_price_plus",
                     store=store, price=f"£{cheapest.shown_price:.2f}")
    key = ("product.share.reddit_text_preorder" if cheapest.availability == Listing.Availability.PREORDER
           else "product.share.reddit_text")
    return copy.get(key, store=store, price=price, when=when_words(cheapest.last_checked))


# Share as post ---------------------------------------------------------------------------------------------------
# A square picture of the product and its price, and a caption, for an Instagram post: Instagram takes no post from
# a web page, so the phone's share sheet takes the picture and the caption goes on the clipboard (share.js). The
# owner, 5 Nov 2026: "a share as post button so it created an insta post with a realistic not spammy caption". A
# post carries no link either, so the picture carries a QR code to the product page: a phone's camera, or a long
# press on the picture (Google Lens, Live Text), opens it. The owner, 5 Nov 2026: "a QR code that some phones can
# long hold to take you to the site".

POST_SIZE = 1080
POST_SECONDS = 60 * 60            # the picture is kept this long, for the same price
POST_FETCH_SECONDS = 8            # the shop's picture, at most
POST_MARGIN = 60
CODE_BOX = 272                    # the QR code's square at the foot right; the margins around it are its quiet zone
CODE_X = CODE_Y = POST_SIZE - POST_MARGIN - CODE_BOX
CODE_GAP = 40                     # between the words and the code
CODE_SOURCE = "instagram.com"     # the tag on the code's address: the dashboard counts a scan as a visit from Instagram
FONT = "fonts/DMSans-variable.ttf"
LOGO = "img/logo.png"
# The game's own hashtags, few and the ones its collectors use; the registry's general ones follow.
POST_TAGS = {
    "pokemon": "#pokemontcg #pokemoncards",
    "magic-the-gathering": "#mtg #magicthegathering",
    "one-piece": "#onepiececardgame #onepiecetcg",
    "lorcana": "#disneylorcana #lorcana",
    "yu-gi-oh": "#yugioh #yugiohtcg",
    "sports-cards": "#sportscards #tradingcards",
    "star-wars-unlimited": "#starwarsunlimited",
    "riftbound": "#riftbound",
    "digimon": "#digimontcg",
    "dragon-ball": "#dragonballsupercardgame",
    "flesh-and-blood": "#fleshandbloodtcg",
}
# The site's colours (web/static/css/tokens.css), for the picture.
INK, INK_MUTED, PAPER, SHELF, PRICE, BRAND = "#1f2430", "#545b68", "#ffffff", "#f6f6f8", "#107a32", "#ff5a1f"


def post_price(best, store=None):
    return copy.get("product.share.reddit_price" if best.delivery_known else "product.share.reddit_price_plus",
                    store=store, price=f"£{best.shown_price:.2f}")


def post_lead(product, summary):
    """The offer a post leads with: the cheapest shop that has the product in stock, never a cheaper pre-order, as
    a list about stock leads its cards (web.views.in_stock_offers); a pre-order only when no shop has it. The
    owner, 6 Nov 2026, with a post saying pre-order for a pack two shops had in stock: "it's saying pre-order when
    the actual item isn't"."""
    offers = getattr(product, "offers", [])
    return next((offer for offer in offers if offer.availability == Listing.Availability.IN_STOCK), summary.best)


def post_caption(product, summary, store=None):
    """The caption for a post about ``product``: its name; the cheapest delivered price today and how many UK shops
    were compared (or that it is on pre-order, or that no shop has it); what the Price check makes of it, when it
    has a verdict; when it was checked; the site, since a caption takes no link; and a few hashtags. Plain sentences
    the owner would write, each one true to the page."""
    best = post_lead(product, summary)
    lines = [product.name]
    if best is None:
        lines.append(copy.get("product.share.post_none", store=store))
    else:
        price = post_price(best, store)
        if best.availability == Listing.Availability.PREORDER:
            middle = [copy.get("product.share.post_preorder", store=store, price=price)]
        else:
            shops = len({offer.retailer_id for offer in getattr(product, "offers", [])
                         if offer.availability == Listing.Availability.IN_STOCK})
            middle = [copy.get("product.share.post_price", store=store, price=price, shops=shops) if shops > 1
                      else copy.get("product.share.post_price_one", store=store, price=price)]
        # The Price check was worked out on the cheapest price of all: not this one, when a cheaper pre-order sits
        # behind the shop that has it.
        check = product.price_check if best is summary.best else None
        if check is not None and check.key in ("lowest", "good"):
            middle.append(copy.get(f"product.share.post_{check.key}", store=store))
        middle.append(copy.get("product.share.post_checked", store=store, when=when_words(best.last_checked)))
        lines.append(" ".join(middle))
    lines.append(f"{copy.get('product.share.post_site', store=store)} {copy.get('product.share.post_scan', store=store)}")
    tags = " ".join(part for part in (POST_TAGS.get(product.game.slug, ""), copy.get("product.share.post_tags", store=store)) if part)
    if tags:
        lines.append(tags)
    return "\n\n".join(lines)


def post_image_key(product, summary, kind="post-image:v2"):
    best = post_lead(product, summary)
    return f"web:{kind}:{product.pk}:{best.pk if best else 0}:{best.shown_price if best else ''}"


def post_url(product):
    """The address the code opens: the product page on the site itself (RIPRAPTOR_SITE_URL), tagged as a visit from
    Instagram, since a phone's camera passes no Referer (catalogue.insights.source_of reads the tag)."""
    from django.conf import settings

    return f"{settings.RIPRAPTOR_SITE_URL}{product.get_absolute_url()}?utm_source={CODE_SOURCE}"


def code_matrix(text):
    """The QR code for ``text`` as rows of booleans, dark modules True. Error correction M: a quarter of the code can
    be lost to Instagram's compression or a thumb and it still reads. No border: the picture's margins are the quiet
    zone."""
    import qrcode

    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    code.add_data(text)
    code.make(fit=True)
    return code.get_matrix()


def code_cells(matrix, box=CODE_BOX, x=CODE_X, y=CODE_Y):
    """(cell, left, top): whole pixels a module, the code centred in its box, so every edge is crisp."""
    cell = box // len(matrix)
    span = cell * len(matrix)
    return cell, x + (box - span) // 2, y + (box - span) // 2


def draw_code(draw, text, box=CODE_BOX, x=CODE_X, y=CODE_Y):
    matrix = code_matrix(text)
    cell, left, top = code_cells(matrix, box, x, y)
    for row, modules in enumerate(matrix):
        for column, dark in enumerate(modules):
            if dark:
                draw.rectangle((left + column * cell, top + row * cell, left + (column + 1) * cell - 1,
                                top + (row + 1) * cell - 1), fill=INK)


def post_image(product, summary, store=None):
    """The post's picture as PNG bytes, kept POST_SECONDS: the product's photo in a soft well, its name, its cheapest
    delivered price in the site's green, how many shops and when it was checked, the logo, and the code that opens
    the page. Without a photo, or with one that will not come in POST_FETCH_SECONDS, the well is empty and the words
    carry it."""
    from django.core.cache import cache

    key = post_image_key(product, summary)
    found = cache.get(key)
    if found is not None:
        return found
    png = draw_post(product, summary, photo_bytes(product), store)
    cache.set(key, png, POST_SECONDS)
    return png


def photo_bytes(product):
    """The product's picture: its own file, else the shop's over the web, else None."""
    import time

    from catalogue.importers import ImportError_, fetch

    if product.image:
        try:
            with product.image.open("rb") as handle:
                return handle.read()
        except (OSError, ValueError):
            return None
    if not product.image_url:
        return None
    try:
        return fetch(product.image_url, retries=0, deadline=time.monotonic() + POST_FETCH_SECONDS)
    except (ImportError_, OSError, ValueError, TimeoutError):
        return None


def post_font(size, weight=400, optical=None):
    from django.contrib.staticfiles import finders
    from PIL import ImageFont

    font = ImageFont.truetype(finders.find(FONT), size)
    try:
        font.set_variation_by_axes([optical if optical is not None else min(40, max(9, size // 2)), weight])
    except OSError:
        pass
    return font


def fit_lines(draw, text, font, width, lines=2):
    """``text`` wrapped to ``width`` over at most ``lines`` lines, the last cut with an ellipsis."""
    words = text.split()
    out, line = [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    if len(out) > lines:
        out = out[:lines]
        last = out[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1].rstrip()
        out[-1] = last + "…"
    return out


def draw_post(product, summary, photo, store=None):
    """The picture, top to bottom on a fixed grid so every post looks the same: the well with the photo, the name
    (two lines at most), the price, two short lines about the shops and when, the logo and site at the foot, and the
    code at the foot right, with the words kept CODE_GAP clear of it."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    size, margin = POST_SIZE, POST_MARGIN
    words = CODE_X - CODE_GAP - margin   # the width the price and the lines beside the code may take
    card = Image.new("RGB", (size, size), PAPER)
    draw = ImageDraw.Draw(card)
    well = (margin, margin, size - margin, 540)
    draw.rounded_rectangle(well, radius=40, fill=SHELF)
    paste_photo(card, photo, well)
    name_font = post_font(52, 700, 24)
    y = 584
    for line in fit_lines(draw, product.name, name_font, size - 2 * margin):
        draw.text((margin, y), line, font=name_font, fill=INK)
        y += 62
    best = post_lead(product, summary)
    small = post_font(32, 500, 14)
    y = 736
    if best is not None:
        price_font = post_font(88, 700, 32)
        price = f"£{best.shown_price:.2f}"
        draw.text((margin, y), price, font=price_font, fill=PRICE)
        after = post_price(best, store).replace(price, "").strip()
        taken = draw.textlength(price, font=price_font) + 20
        draw.text((margin + taken, y + 48), fit_lines(draw, after, small, words - taken, lines=1)[0], font=small,
                  fill=INK_MUTED)
    lines = picture_lines(product, best, store)
    y = 856
    for line in lines:
        draw.text((margin, y), fit_lines(draw, line, small, words, lines=1)[0], font=small, fill=INK_MUTED)
        y += 42
    foot, height = 1000, 48
    x = margin
    logo = paste_logo(card, height, (margin, foot))
    if logo:
        x += logo + 24
    draw.text((x, foot + 8), "ripraptor.com", font=post_font(30, 600, 14), fill=INK_MUTED)
    draw_code(draw, post_url(product))
    out = BytesIO()
    card.save(out, "PNG", optimize=True)
    return out.getvalue()


def paste_photo(card, photo, well, inset=40):
    """The product's photo, fitted ``inset`` inside the well and centred in it. A photo that will not open leaves the
    well empty."""
    picture = fitted_photo(photo, well[2] - well[0] - 2 * inset, well[3] - well[1] - 2 * inset)
    if picture is not None:
        paste_centred(card, picture, well)


def fitted_photo(photo, width, height, grow=1.0):
    """The photo as an RGBA image fitted inside ``width`` by ``height``, made up to ``grow`` times bigger when it is
    smaller (a shop's photo is often a few hundred pixels), or None when there is none or it will not open."""
    from io import BytesIO

    from PIL import Image

    if not photo:
        return None
    try:
        with Image.open(BytesIO(photo)) as opened:
            picture = opened.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    scale = min(width / picture.width, height / picture.height, grow)
    if scale < 1 or grow > 1:
        size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
        picture = picture.resize(size, Image.LANCZOS)
    return picture


def paste_centred(card, picture, box):
    x = box[0] + (box[2] - box[0] - picture.width) // 2
    y = box[1] + (box[3] - box[1] - picture.height) // 2
    card.paste(picture, (x, y), picture)


def paste_logo(card, height, at):
    """The logo, ``height`` tall, at ``at``: its width, or 0 without the file."""
    logo = logo_image(height)
    if logo is None:
        return 0
    card.paste(logo, at, logo)
    return logo.width


def logo_image(height, name=LOGO):
    """A picture from the site's static files (the logo by default), ``height`` tall, RGBA, or None without it."""
    from django.contrib.staticfiles import finders
    from PIL import Image

    path = finders.find(name)
    if not path:
        return None
    with Image.open(path) as found:
        found = found.convert("RGBA")
        return found.resize((round(found.width * height / found.height), height), Image.LANCZOS)


def picture_lines(product, best, store=None):
    """The short lines under the price on a post's or a story's picture: how many UK shops have it (or that it is on
    pre-order) and when it was checked, or that no shop has it."""
    if best is None:
        return [copy.get("product.share.post_image_none", store=store)]
    shops = len({offer.retailer_id for offer in getattr(product, "offers", [])
                 if offer.availability == Listing.Availability.IN_STOCK})
    if best.availability == Listing.Availability.PREORDER:
        first = copy.get("product.share.post_image_preorder", store=store)
    elif shops > 1:
        first = copy.get("product.share.post_image_shops", store=store, shops=shops)
    else:
        first = copy.get("product.share.post_image_one", store=store)
    return [first, copy.get("product.share.post_checked", store=store, when=when_words(best.last_checked))]


# Share to story ------------------------------------------------------------------------------------------------------
# A tall picture (9:16) of the product and its price for an Instagram story. A web page cannot open Instagram's story
# editor itself: that takes an app registered with Meta. So the phone's share sheet takes the picture, where Instagram
# offers Stories, and the product's link goes on the clipboard for the story's link sticker (share.js). The owner, 18
# Nov 2026: "someones website had a insta stories share button one Tap created the insta story in its drafting
# state", and then, with a template from the marketing team, "Could it be a bit more exciting like" it.
#
# The template's look, drawn with the site's tokens: a white card on the shelf grey, as a product card on the site.
# Its head row has the game's icon and name and the logo; then the photo in a big grey well with the Price check's
# chip, the name, the delivered price very large in green, what it is under the RRP when it is, the shops compared and
# when it was checked, and a copy of the site's search bar naming a word that finds the product there. No QR code:
# someone watching a story on their phone cannot scan that phone. Everything sits between STORY_TOP and STORY_FOOT,
# clear of Instagram's name bar over the top of a story and its reply box over the foot.

STORY_WIDTH, STORY_HEIGHT = 1080, 1920
STORY_TOP, STORY_FOOT = 250, 1670
STORY_CARD_WIDTH = 984
STORY_PAD_TOP, STORY_PAD = 44, 48               # inside the card: top, and sides and foot
STORY_HEAD = 64                                 # the game and the logo
STORY_WELL, STORY_WELL_LEAST = 600, 420         # the photo's well, and how far it shrinks for a long name
STORY_TITLE_SIZES = (64, 58, 52, 48)            # the name steps down to fit two lines
STORY_PRICE_SIZES = (164, 148, 132, 118)        # the price steps down to fit one line with "delivered"
STORY_ICON = 46
STORY_SEARCH = 108                              # the search bar's height
STORY_GO = 82                                   # its orange button
# The room for the bar's words: the bar less its left inset, the search icon and the gaps beside it, and the button
# with its inset.
STORY_SEARCH_WORDS = STORY_CARD_WIDTH - 2 * STORY_PAD - 1 - 34 - STORY_ICON - 20 - 20 - STORY_GO - 12
STORY_PHOTO_GROW = 2                            # a small photo is made at most this much bigger
# The site's colours (web/static/css/tokens.css).
LINE, LINE_STRONG, BRAND_TEXT, BRAND_SOFT, PRICE_SOFT = "#e6e8ec", "#cfd4dc", "#cc4410", "#fff1ea", "#e7f7ec"
# The Price check's chip on the photo: its words and colours, as the site's own chip.
STORY_CHIPS = {"lowest": ("product.fair.verdict_lowest", BRAND_SOFT, BRAND_TEXT),
               "good": ("product.fair.verdict_good", PRICE_SOFT, PRICE)}
SET_CODE = re.compile(r"\(([A-Za-z]{1,4}-?\d{1,3}[A-Za-z]?)\)")   # "(OP-11)" in a name


def story_image(product, summary, store=None):
    """The story's picture as PNG bytes, kept POST_SECONDS for the same price, as the post's is."""
    from django.core.cache import cache

    key = post_image_key(product, summary, kind="story-image:v2")
    found = cache.get(key)
    if found is not None:
        return found
    png = draw_story(product, summary, photo_bytes(product), store)
    cache.set(key, png, POST_SECONDS)
    return png


def search_finds(term, product):
    """Whether searching the site for ``term`` shows ``product`` on the first page, in the order the search box
    lists them (views.search_api). One query."""
    from django.conf import settings
    from django.db.models import F

    from catalogue.models import Product
    from catalogue.search import apply_search

    products, terms = apply_search(Product.objects.for_lists(), term)
    if not terms:
        return False
    first = products.order_by("name_rank", F("lowest_price").asc(nulls_last=True), "name")
    return product.pk in set(first.values_list("pk", flat=True)[:settings.RIPRAPTOR_PAGE_SIZE])


def story_search_terms(product):
    """Words that find ``product`` on the site, best first, as the story's search bar may name them: the set code in
    the name's brackets ("OP-11", what collectors type), the set's name ("Prismatic Evolutions", plainer than its
    code), the set's code; then the product's own name, unchecked, as the last word. Lazy: a search a word at a
    time, so the first that fits the bar costs one query."""
    named = SET_CODE.search(product.name)
    found_set = product.product_set
    candidates = [named.group(1) if named else "", found_set.name if found_set else "",
                  found_set.code if found_set else ""]
    for term in dict.fromkeys(term.strip() for term in candidates if term and term.strip()):
        if search_finds(term, product):
            yield term
    yield product.name


def fit_search(draw, before, behind, terms, room):
    """(term, plain font, bold font, [widths]) for the search bar's words: the first term that fits ``room`` at one
    of its sizes, largest first; when none does, the first term at the smallest size, cut."""
    first = None
    for term in terms:
        for size in (38, 34, 30):
            plain, bold = post_font(size, 600, 24), post_font(size, 800, 24)
            widths = [draw.textlength(before, font=plain), draw.textlength(term, font=bold),
                      draw.textlength(behind, font=plain)]
            if sum(widths) <= room:
                return term, plain, bold, widths
        first = first or (term, plain, bold, widths)
    term, plain, bold, widths = first
    term = fit_lines(draw, term, bold, room - widths[0] - widths[2], lines=1)[0]
    return term, plain, bold, [widths[0], draw.textlength(term, font=bold), widths[2]]


def story_saving(product, best):
    """What the delivered price is under the product's RRP, or None. Only under, and only from an RRP the site
    trusts (Product.rrp_now): never a saving the data cannot back. Measured from the delivered price shown beside it,
    so it can only ever say less than the product page's item price would."""
    if best is None or not best.delivery_known or product.rrp_now is None:
        return None
    gap = (product.rrp_now[0] - best.shown_price).quantize(Decimal("0.01"))
    return gap if gap > 0 else None


def knock_out_white(picture):
    """A shop's photo on a plain white background, with that background made clear, so the product stands on the
    grey well as a cut-out does. Only the white that reaches the photo's edges goes (found on a quarter-size copy,
    where filling is quick, then followed at full size), so white on the box itself stays. A photo that is already
    cut out, or whose corners are not white, is left as it is."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    picture = picture.convert("RGBA")
    if picture.getchannel("A").getextrema()[0] < 250:
        return picture
    rgb = picture.convert("RGB")
    width, height = rgb.size
    if not all(min(rgb.getpixel(corner)) >= 240 for corner in ((0, 0), (width - 1, 0), (0, height - 1),
                                                               (width - 1, height - 1))):
        return picture
    step = max(1, max(width, height) // 256)
    small = rgb.resize((max(1, width // step), max(1, height // step)), Image.BILINEAR)
    key = (255, 0, 255)
    for corner in ((0, 0), (small.width - 1, 0), (0, small.height - 1), (small.width - 1, small.height - 1)):
        if small.getpixel(corner) != key:
            ImageDraw.floodfill(small, corner, key, thresh=36)
    apart = ImageChops.difference(small, Image.new("RGB", small.size, key)).split()
    reached = ImageChops.lighter(ImageChops.lighter(apart[0], apart[1]), apart[2]).point(lambda v: 255 if v < 8 else 0)
    reached = reached.resize((width, height), Image.BILINEAR).point(lambda v: 255 if v > 64 else 0)
    reached = reached.filter(ImageFilter.MaxFilter(5))
    channels = rgb.split()
    white = ImageChops.darker(ImageChops.darker(channels[0], channels[1]), channels[2]).point(lambda v: 255 if v >= 232 else 0)
    keep = ImageChops.invert(ImageChops.multiply(reached, white)).filter(ImageFilter.GaussianBlur(0.8))
    picture.putalpha(ImageChops.darker(picture.getchannel("A"), keep))
    return picture


def tracked_text(draw, at, text, font, fill, tracking, anchor="ls"):
    """``text`` drawn letter by letter ``tracking`` pixels apart (negative is tighter), from ``at`` on its baseline.
    Returns its width."""
    x, y = at
    for i, letter in enumerate(text):
        draw.text((x, y), letter, font=font, fill=fill, anchor=anchor)
        x += draw.textlength(letter, font=font) + (tracking if i < len(text) - 1 else 0)
    return x - at[0]


def tracked_length(draw, text, font, tracking):
    return sum(draw.textlength(letter, font=font) for letter in text) + tracking * max(0, len(text) - 1)


def shadow(story, box, radius, offset, blur, alpha):
    """A soft shadow under the rounded ``box``, as the site's cards cast."""
    from PIL import Image, ImageDraw, ImageFilter

    layer = Image.new("RGBA", story.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((box[0], box[1] + offset, box[2], box[3] + offset), radius=radius,
                                            fill=(31, 36, 48, alpha))
    story.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def story_facts(product, best, store=None):
    """The lines beside the icons: [(icon, words, quiet)]. The shops (or pre-order, or none in stock) and when it was
    checked, never a shop's name."""
    lines = picture_lines(product, best, store)
    facts = [("rr-deals", lines[0], False)]
    if best is not None:
        facts.append(("rr-drops", copy.get("product.share.story_checked", store=store,
                                           when=when_words(best.last_checked)), True))
    return facts


def draw_story(product, summary, photo, store=None):
    """The picture, as the marketing team's template (story_mock.png): see the notes above."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from . import feed, glyphs

    story = Image.new("RGBA", (STORY_WIDTH, STORY_HEIGHT), SHELF)
    draw = ImageDraw.Draw(story)
    inner = STORY_CARD_WIDTH - 2 * STORY_PAD
    best = post_lead(product, summary)
    check = product.price_check if best is not None and best is summary.best else None
    chip = STORY_CHIPS.get(check.key) if check is not None else None
    saving = story_saving(product, best)
    facts = story_facts(product, best, store)

    # The name: the largest size that fits two lines, else the smallest, cut.
    for size in STORY_TITLE_SIZES:
        title_font = post_font(size, 800, 40)
        names = fit_lines(draw, product.name, title_font, inner, lines=99)
        if len(names) <= 2:
            break
    names = fit_lines(draw, product.name, title_font, inner, lines=2)
    title_line = round(title_font.size * 1.12)
    # The price: the largest size that fits one line with "delivered" beside it.
    price_words = after = ""
    if best is not None:
        price_words = f"£{best.shown_price:,.2f}"
        after = post_price(best, store).replace(f"£{best.shown_price:.2f}", "").strip()
        after_font = post_font(46, 600, 24)
        for size in STORY_PRICE_SIZES:
            price_font = post_font(size, 800, 40)
            tracking = -round(size / 55)
            if tracked_length(draw, price_words, price_font, tracking) + 22 + draw.textlength(after, font=after_font) <= inner:
                break

    # Heights, top to bottom, and the well takes up what a long name or a saving needs.
    rest = (STORY_PAD_TOP + STORY_HEAD + 28 + 38 + len(names) * title_line
            + (26 + price_font.size if best is not None else 0) + (14 + 50 if saving else 0)
            + 30 + len(facts) * STORY_ICON + (len(facts) - 1) * 18 + 40 + STORY_SEARCH + STORY_PAD)
    room = STORY_FOOT - STORY_TOP
    well_height = max(STORY_WELL_LEAST, min(STORY_WELL, room - rest))
    card_height = rest + well_height
    left = (STORY_WIDTH - STORY_CARD_WIDTH) // 2
    top = STORY_TOP + max(0, (room - card_height) // 2)
    card = (left, top, left + STORY_CARD_WIDTH - 1, top + card_height - 1)
    shadow(story, card, 44, 8, 18, 20)
    draw = ImageDraw.Draw(story)
    draw.rounded_rectangle(card, radius=44, fill=PAPER, outline=LINE, width=2)
    x, y = left + STORY_PAD, top + STORY_PAD_TOP

    # The head row: the game's icon and name, and the logo.
    icon = f"rr-game-{feed.icon_for(product.game.slug)}"
    glyphs.paste(story, icon if icon in glyphs.symbols() else "rr-game-all", STORY_ICON,
                 (x, y + (STORY_HEAD - STORY_ICON) // 2), INK)
    draw.text((x + STORY_ICON + 14, y + STORY_HEAD // 2), product.game.display_short, font=post_font(36, 700, 24),
              fill=INK_MUTED, anchor="lm")
    logo = logo_image(60)
    if logo is not None:
        story.alpha_composite(logo, (x + inner - logo.width, y + (STORY_HEAD - logo.height) // 2))
    y += STORY_HEAD + 28

    # The well, with the photo (or Rip's head in grey) and the Price check's chip.
    well = (x, y, x + inner - 1, y + well_height - 1)
    draw.rounded_rectangle(well, radius=61, fill=SHELF)
    picture = fitted_photo(photo, round(inner * 0.84), round(well_height * 0.88), grow=STORY_PHOTO_GROW)
    if picture is not None:
        paste_centred(story, knock_out_white(picture), well)
    else:
        head = logo_image(round(well_height * 0.5), "img/raptor-lg.png")
        if head is not None:
            grey = Image.new("RGBA", head.size, LINE)
            grey.putalpha(head.getchannel("A"))
            paste_centred(story, grey, well)
    if chip is not None:
        key, ground, ink = chip
        words = copy.get(key, store=store)
        chip_font = post_font(34, 800, 24)
        chip_width = draw.textlength(words, font=chip_font) + 52
        box = (x + 30, y + 30, x + 30 + chip_width, y + 30 + 58)
        draw.rounded_rectangle(box, radius=29, fill=ground)
        draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), words, font=chip_font, fill=ink, anchor="mm")
    y += well_height + 38

    # The name, the price and the saving.
    for line in names:
        draw.text((x, y), line, font=title_font, fill=INK)
        y += title_line
    if best is not None:
        y += 26
        baseline = y + round(price_font.size * 0.78)
        taken = tracked_text(draw, (x, baseline), price_words, price_font, PRICE, tracking)
        draw.text((x + taken + 22, baseline), after, font=after_font, fill=INK_MUTED, anchor="ls")
        y += price_font.size
    if saving:
        y += 14
        draw.text((x, y), copy.get("product.share.story_saving", store=store, saving=f"£{saving:,.2f}"),
                  font=post_font(42, 700, 24), fill=PRICE)
        y += 50

    # The facts, each beside its icon.
    y += 30
    for i, (name, words, quiet) in enumerate(facts):
        glyphs.paste(story, name, STORY_ICON, (x, y), BRAND_TEXT)
        font = post_font(36, 500, 24) if quiet else post_font(38, 600, 24)
        text_x = x + STORY_ICON + 18
        draw.text((text_x, y + STORY_ICON // 2), fit_lines(draw, words, font, inner - (text_x - x), lines=1)[0],
                  font=font, fill=INK_MUTED if quiet else INK, anchor="lm")
        y += STORY_ICON + (18 if i < len(facts) - 1 else 0)

    # The search bar, as the site's, naming a word that finds the product.
    y += 40
    bar = (x, y, x + inner - 1, y + STORY_SEARCH - 1)
    shadow(story, bar, STORY_SEARCH // 2, 6, 14, 20)
    draw = ImageDraw.Draw(story)
    draw.rounded_rectangle(bar, radius=STORY_SEARCH // 2, fill=PAPER, outline=LINE_STRONG, width=3)
    middle = y + STORY_SEARCH // 2
    glyphs.paste(story, "rr-search", STORY_ICON, (x + 34, middle - STORY_ICON // 2), INK_MUTED)
    go = (bar[2] - 12 - STORY_GO, middle - STORY_GO // 2, bar[2] - 12, middle + STORY_GO // 2)
    draw.ellipse(go, fill=BRAND)
    glyphs.paste(story, "rr-arrow-right", 44, (go[0] + (STORY_GO - 44) // 2, middle - 22), PAPER)
    words_x = x + 34 + STORY_ICON + 20
    before, _, behind = copy.get("product.share.story_search", store=store, term="\x00").partition("\x00")
    term, plain, bold, widths = fit_search(draw, before, behind, story_search_terms(product), STORY_SEARCH_WORDS)
    at = words_x
    for words, font, width in ((before, plain, widths[0]), (term, bold, widths[1]), (behind, plain, widths[2])):
        draw.text((at, middle), words, font=font, fill=INK, anchor="lm")
        at += width

    out = BytesIO()
    story.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


# Rip's answer as a card ----------------------------------------------------------------------------------------------
# A square picture of Rip's verdict and the sentence behind it, for the phone's share sheet (ask.js), with the product,
# the site and the code that opens the product page. A review, 13 Nov 2026: "a verdict card is something people send
# to a partner or a mate who's about to buy. Every share would carry ripraptor.com". Drawn from the kept answer
# (askrip.answer_for), so a card exists only while its answer does.

RIP_SOURCE = "rip_share"           # the tag on the code's and the shared link's address
# The badge's colours for each tone, as the Price check's chip (web/static/css/tokens.css).
TONE_COLOURS = {"good": ("#e7f7ec", PRICE), "usual": (SHELF, INK_MUTED), "warn": ("#fbf1d3", "#7d6000")}
# Rip himself at the top right, his face following the verdict as it does in the chat: glad at a good one, puzzled at
# a warning. Pictures drawn from the owner's rip_bot pack (web/static/img/rip/avatars/avatar_*.svg). The owner, 13 Nov
# 2026: "On the share card can it have rip on the design please".
RIP_FACES = {"good": "img/rip/avatars/card_happy.png", "warn": "img/rip/avatars/card_puzzled.png"}
RIP_FACE = "img/rip/avatars/card_normal.png"
RIP_WIDTH = 340
RIP_GAP = 32      # between the words on the left and Rip


def rip_url(product):
    """The product page on the site itself, tagged as a visit from a shared Rip answer."""
    from django.conf import settings

    return f"{settings.RIPRAPTOR_SITE_URL}{product.get_absolute_url()}?utm_source={RIP_SOURCE}"


RIP_LINE_LINES = 3      # the answer's first sentence, large, at most
RIP_LINE_SIZES = (56, 50, 44, 40)   # it steps down to fit whole; only past the last is it cut
RIP_MORE_FONT, RIP_MORE_LINE = 34, 46   # the sentences after it


def fit_headline(draw, text, width):
    """(size, font, lines) for the card's first sentence: whole, at the largest of RIP_LINE_SIZES it fits in
    RIP_LINE_LINES (the owner, 19 Nov 2026, of one cut short at "£7.4...": "It doesn't fit"); past the smallest, one
    line more, and only then cut."""
    for points in RIP_LINE_SIZES:
        font = post_font(points, 600, 28)
        lines = fit_lines(draw, text, font, width, lines=99)
        if len(lines) <= RIP_LINE_LINES:
            return points, font, lines
    return points, font, fit_lines(draw, text, font, width, lines=RIP_LINE_LINES + 1)


def rip_card(product, kept, store=None):
    """The card as PNG bytes: "Rip says", the verdict as a badge, the answer's first sentence large, then as many of
    the sentences after it as fit, smaller, so the card carries the numbers the verdict rests on (the owner, 19 Nov
    2026, of a card that said only "It looks good value." over empty space: "it's a bit different"). Whole
    sentences only, the first cut short when even it does not fit. At the foot, the product's name and when its
    prices were checked beside the code, the logo and the site."""
    from io import BytesIO

    from django.contrib.staticfiles import finders
    from PIL import Image, ImageDraw

    size, margin = POST_SIZE, POST_MARGIN
    card = Image.new("RGB", (size, size), PAPER)
    draw = ImageDraw.Draw(card)
    rip_bottom = margin
    face = finders.find(RIP_FACES.get(kept.get("tone"), RIP_FACE))
    if face:
        with Image.open(face) as rip:
            rip = rip.convert("RGBA")
            rip = rip.crop(rip.getbbox())
            rip = rip.resize((RIP_WIDTH, round(rip.height * RIP_WIDTH / rip.width)), Image.LANCZOS)
            card.paste(rip, (size - margin - RIP_WIDTH, margin), rip)
            rip_bottom = margin + rip.height
    beside = size - 2 * margin - RIP_WIDTH - RIP_GAP   # the width the words beside Rip may take
    y = margin + 20
    draw.text((margin, y), copy.get("product.ask.card_says", store=store), font=post_font(40, 700, 18), fill=BRAND)
    y += 84
    verdict = (kept.get("verdict") or "").upper()
    if verdict:
        bg, ink = TONE_COLOURS.get(kept.get("tone"), TONE_COLOURS["usual"])
        points = 46
        font = post_font(points, 700, 20)
        while points > 30 and draw.textlength(verdict, font=font) + 64 > beside:
            points -= 2
            font = post_font(points, 700, 20)
        width = draw.textlength(verdict, font=font)
        draw.rounded_rectangle((margin, y, margin + width + 64, y + 86), radius=43, fill=bg)
        draw.text((margin + 32, y + (86 - points) // 2 - 4), verdict, font=font, fill=ink)
        y += 130
    y = max(y, rip_bottom + 40)
    width = size - 2 * margin
    points, line_font, lines = fit_headline(draw, kept.get("line") or kept.get("answer", ""), width)
    for line in lines:
        draw.text((margin, y), line, font=line_font, fill=INK)
        y += round(points * 1.28)
    # The sentences after it, whole, while they fit above the code's row.
    from . import askrip

    more_font = post_font(RIP_MORE_FONT, 500, 16)
    room = (CODE_Y - 28 - (y + 18)) // RIP_MORE_LINE
    taken, shown = [], []
    for sentence in askrip.later_sentences(kept.get("answer", "")):
        lines = fit_lines(draw, " ".join(taken + [sentence]), more_font, width, lines=99)   # one paragraph
        if len(lines) > room:
            if not taken and room > 0:   # not even the first fits: as much of it as does, cut
                shown = fit_lines(draw, sentence, more_font, width, lines=room)
            break
        taken.append(sentence)
        shown = lines
    y += 18
    for line in shown:
        draw.text((margin, y), line, font=more_font, fill=INK)
        y += RIP_MORE_LINE
    small = post_font(32, 500, 14)
    words = CODE_X - CODE_GAP - margin
    name_y = CODE_Y - 8
    for line in fit_lines(draw, product.name, small, words, lines=2):
        draw.text((margin, name_y), line, font=small, fill=INK_MUTED)
        name_y += 42
    if kept.get("checked"):
        draw.text((margin, name_y + 6), copy.get("product.ask.card_checked", store=store,
                                                 when=when_words(kept["checked"])),
                  font=post_font(28, 500, 14), fill=INK_MUTED)
    foot, height = 1000, 48
    x = margin
    logo_path = finders.find(LOGO)
    if logo_path:
        with Image.open(logo_path) as logo:
            logo = logo.convert("RGBA")
            logo = logo.resize((round(logo.width * height / logo.height), height))
            card.paste(logo, (margin, foot), logo)
            x += logo.width + 24
    draw.text((x, foot + 8), "ripraptor.com", font=post_font(30, 600, 14), fill=INK_MUTED)
    draw_code(draw, rip_url(product))
    out = BytesIO()
    card.save(out, "PNG", optimize=True)
    return out.getvalue()
