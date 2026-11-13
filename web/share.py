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


def post_image_key(product, summary):
    best = post_lead(product, summary)
    return f"web:post-image:v2:{product.pk}:{best.pk if best else 0}:{best.shown_price if best else ''}"


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


def draw_code(draw, text):
    matrix = code_matrix(text)
    cell, left, top = code_cells(matrix)
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

    from django.contrib.staticfiles import finders
    from PIL import Image, ImageDraw

    size, margin = POST_SIZE, POST_MARGIN
    words = CODE_X - CODE_GAP - margin   # the width the price and the lines beside the code may take
    card = Image.new("RGB", (size, size), PAPER)
    draw = ImageDraw.Draw(card)
    well = (margin, margin, size - margin, 540)
    draw.rounded_rectangle(well, radius=40, fill=SHELF)
    if photo:
        try:
            with Image.open(BytesIO(photo)) as picture:
                picture = picture.convert("RGBA")
                picture.thumbnail((well[2] - well[0] - 80, well[3] - well[1] - 80))
                x = (size - picture.width) // 2
                y = well[1] + (well[3] - well[1] - picture.height) // 2
                card.paste(picture, (x, y), picture)
        except (OSError, ValueError):
            pass
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
        shops = len({offer.retailer_id for offer in getattr(product, "offers", [])
                     if offer.availability == Listing.Availability.IN_STOCK})
        if best.availability == Listing.Availability.PREORDER:
            first = copy.get("product.share.post_image_preorder", store=store)
        elif shops > 1:
            first = copy.get("product.share.post_image_shops", store=store, shops=shops)
        else:
            first = copy.get("product.share.post_image_one", store=store)
        lines = [first, copy.get("product.share.post_checked", store=store, when=when_words(best.last_checked))]
    else:
        lines = [copy.get("product.share.post_image_none", store=store)]
    y = 856
    for line in lines:
        draw.text((margin, y), fit_lines(draw, line, small, words, lines=1)[0], font=small, fill=INK_MUTED)
        y += 42
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
    draw_code(draw, post_url(product))
    out = BytesIO()
    card.save(out, "PNG", optimize=True)
    return out.getvalue()


# Rip's answer as a card ----------------------------------------------------------------------------------------------
# A square picture of Rip's verdict and the sentence behind it, for the phone's share sheet (ask.js), with the product,
# the site and the code that opens the product page. A review, 13 Nov 2026: "a verdict card is something people send
# to a partner or a mate who's about to buy. Every share would carry ripraptor.com". Drawn from the kept answer
# (askrip.answer_for), so a card exists only while its answer does.

RIP_SOURCE = "rip_share"           # the tag on the code's and the shared link's address
# The badge's colours for each tone, as the Price check's chip (web/static/css/tokens.css).
TONE_COLOURS = {"good": ("#e7f7ec", PRICE), "usual": (SHELF, INK_MUTED), "warn": ("#fbf1d3", "#7d6000")}


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
    y = margin + 20
    draw.text((margin, y), copy.get("product.ask.card_says", store=store), font=post_font(40, 700, 18), fill=BRAND)
    y += 84
    verdict = (kept.get("verdict") or "").upper()
    if verdict:
        bg, ink = TONE_COLOURS.get(kept.get("tone"), TONE_COLOURS["usual"])
        font = post_font(46, 700, 20)
        width = draw.textlength(verdict, font=font)
        draw.rounded_rectangle((margin, y, margin + width + 64, y + 86), radius=43, fill=bg)
        draw.text((margin + 32, y + 16), verdict, font=font, fill=ink)
        y += 130
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
