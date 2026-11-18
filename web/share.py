"""The words a share fills in for the visitor: the text under a Reddit post's title (product page and cards)."""

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


def paste_logo(card, height, at, centred=False):
    """The logo, ``height`` tall, at ``at`` (across the middle of the card at ``at``'s height when ``centred``): its
    width, or 0 without the file."""
    from django.contrib.staticfiles import finders
    from PIL import Image

    logo_path = finders.find(LOGO)
    if not logo_path:
        return 0
    with Image.open(logo_path) as logo:
        logo = logo.convert("RGBA")
        logo = logo.resize((round(logo.width * height / logo.height), height))
        if centred:
            at = ((card.width - logo.width) // 2, at[1])
        card.paste(logo, at, logo)
        return logo.width


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
# state". Everything sits between STORY_TOP and STORY_FOOT: Instagram lays its name and progress bar over the top of
# a story and its reply box over the foot. The product is a white card on the site's soft orange, as on its pages.

STORY_WIDTH, STORY_HEIGHT = 1080, 1920
STORY_TOP, STORY_FOOT = 260, 1660
STORY_MARGIN = 80
STORY_LOGO = 76                    # the logo's height, centred at the top
STORY_PAD = 48                     # inside the card
STORY_GAP = 40                     # between the logo, the card and the code's row
STORY_PHOTO = 440                  # the photo's height at most
STORY_NAME_LINE, STORY_PRICE, STORY_LINE = 66, 144, 48
STORY_TILE = 264                   # the white square the code sits on, at the foot left
STORY_CODE = 216                   # the code in it; the tile's edge round it is the code's quiet zone
STORY_TILE_X, STORY_TILE_Y = STORY_MARGIN, STORY_FOOT - STORY_TILE
STORY_CODE_X = STORY_TILE_X + (STORY_TILE - STORY_CODE) // 2
STORY_CODE_Y = STORY_TILE_Y + (STORY_TILE - STORY_CODE) // 2
BRAND_SOFT = "#fff1ea"             # --color-brand-soft


def story_image(product, summary, store=None):
    """The story's picture as PNG bytes, kept POST_SECONDS for the same price, as the post's is."""
    from django.core.cache import cache

    key = post_image_key(product, summary, kind="story-image:v1")
    found = cache.get(key)
    if found is not None:
        return found
    png = draw_story(product, summary, photo_bytes(product), store)
    cache.set(key, png, POST_SECONDS)
    return png


def draw_story(product, summary, photo, store=None):
    """The picture: the logo at the top and, at the foot, the code that opens the page beside a line saying what it
    does and the site; between them the product's card, as tall as what it holds, centred: the photo when there is one
    (on the white card, so a shop's white background melts into it), the name (two lines at most), the price in the
    site's green and the lines about the shops and when it was checked."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    width, margin = STORY_WIDTH, STORY_MARGIN
    story = Image.new("RGB", (width, STORY_HEIGHT), BRAND_SOFT)
    draw = ImageDraw.Draw(story)
    paste_logo(story, STORY_LOGO, (0, STORY_TOP), centred=True)
    inner = width - 2 * margin - 2 * STORY_PAD
    picture = fitted_photo(photo, inner, STORY_PHOTO, grow=2)
    name_font = post_font(54, 700, 24)
    names = fit_lines(draw, product.name, name_font, inner)
    best = post_lead(product, summary)
    lines = picture_lines(product, best, store)
    height = (STORY_PAD + (STORY_PHOTO + 36 if picture is not None else 0) + len(names) * STORY_NAME_LINE + 20
              + (STORY_PRICE if best is not None else 0) + len(lines) * STORY_LINE + STORY_GAP)
    space_top, space_foot = STORY_TOP + STORY_LOGO + STORY_GAP, STORY_TILE_Y - STORY_GAP
    top = space_top + max(0, (space_foot - space_top - height) // 2)
    draw.rounded_rectangle((margin, top, width - margin, top + height), radius=48, fill=PAPER)
    x, y = margin + STORY_PAD, top + STORY_PAD
    if picture is not None:
        paste_centred(story, picture, (x, y, x + inner, y + STORY_PHOTO))
        y += STORY_PHOTO + 36
    for line in names:
        draw.text((x, y), line, font=name_font, fill=INK)
        y += STORY_NAME_LINE
    y += 20
    small = post_font(34, 500, 14)
    if best is not None:
        price_font = post_font(108, 700, 32)
        price = f"£{best.shown_price:.2f}"
        draw.text((x, y), price, font=price_font, fill=PRICE)
        after = post_price(best, store).replace(price, "").strip()
        taken = draw.textlength(price, font=price_font) + 24
        draw.text((x + taken, y + 60), fit_lines(draw, after, small, inner - taken, lines=1)[0], font=small,
                  fill=INK_MUTED)
        y += STORY_PRICE
    for line in lines:
        draw.text((x, y), fit_lines(draw, line, small, inner, lines=1)[0], font=small, fill=INK_MUTED)
        y += STORY_LINE
    tile = (STORY_TILE_X, STORY_TILE_Y, STORY_TILE_X + STORY_TILE - 1, STORY_TILE_Y + STORY_TILE - 1)   # ends inclusive
    draw.rounded_rectangle(tile, radius=32, fill=PAPER)
    draw_code(draw, post_url(product), STORY_CODE, STORY_CODE_X, STORY_CODE_Y)
    words_x = STORY_TILE_X + STORY_TILE + STORY_GAP
    words = width - margin - words_x
    scan = post_font(44, 700, 24)
    draw.text((words_x, STORY_TILE_Y + 72), fit_lines(draw, copy.get("product.share.story_scan", store=store), scan,
                                                     words, lines=1)[0], font=scan, fill=INK)
    draw.text((words_x, STORY_TILE_Y + 140), "ripraptor.com", font=post_font(36, 600, 14), fill=INK_MUTED)
    out = BytesIO()
    story.save(out, "PNG", optimize=True)
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


def rip_card(product, kept, store=None):
    """The card as PNG bytes: "Rip says", the verdict as a badge, the answer's first sentence large, the product's
    name, the logo and site at the foot and the code at the foot right."""
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
    line_font = post_font(56, 600, 28)
    for line in fit_lines(draw, kept.get("line") or kept.get("answer", ""), line_font, size - 2 * margin, lines=5):
        draw.text((margin, y), line, font=line_font, fill=INK)
        y += 72
    small = post_font(32, 500, 14)
    words = CODE_X - CODE_GAP - margin
    name_y = CODE_Y - 8
    for line in fit_lines(draw, product.name, small, words, lines=2):
        draw.text((margin, name_y), line, font=small, fill=INK_MUTED)
        name_y += 42
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
