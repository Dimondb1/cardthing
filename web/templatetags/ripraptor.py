import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe

from catalogue import images
from content import service

register = template.Library()

PENNY = Decimal("0.01")

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@register.filter
def accent_last(text, css="hero__accent"):
    """The headline with its last sentence on a line of its own, in the brand colour.

    "Bad prices. Binned." becomes the first sentence, then the second in a span. ``css`` names the span's class
    ("poster__line" on the raptor panels). A headline of one sentence is left as it is, so the owner's wording
    decides.
    """
    parts = SENTENCE_END.split(str(text or "").strip())
    if len(parts) < 2:
        return text
    return format_html('{} <span class="{}">{}</span>', " ".join(parts[:-1]), css, parts[-1])


def _money(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(PENNY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


@register.filter
def gbp(value):
    """Decimal('1249') -> '£1,249.00'."""
    amount = _money(value)
    if amount is None:
        return ""
    return f"£{amount:,.2f}"


@register.filter
def abs_money(value):
    amount = _money(value)
    return None if amount is None else abs(amount)


@register.simple_tag
def price(value, tag=False, size=""):
    """A price for display.

    Tagged prices (the cheapest price, price drops) sit on the yellow tag with
    raised pence, like a shelf label. Screen readers get the plain price.
    """
    amount = _money(value)
    if amount is None:
        return ""
    text = f"£{amount:,.2f}"
    classes = ["price"]
    if tag:
        classes.append("price--tag")
    if size:
        classes.append(f"price--{size}")
    if not tag:
        return format_html('<span class="{}">{}</span>', " ".join(classes), text)
    whole, pence = text.split(".")
    return format_html(
        '<span class="{}"><span class="visually-hidden">{}</span>'
        '<span class="price__visual" aria-hidden="true">{}<span class="price__pence">{}</span></span></span>',
        " ".join(classes),
        text,
        whole,
        pence,
    )


@register.filter
def ago(value):
    """'just now', '18 minutes ago', '3 hours ago', 'yesterday', '4 days ago', 'on 3 Sep'."""
    if not value:
        return ""
    seconds = (timezone.now() - value).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    return "on " + date_format(timezone.localtime(value), "j M")


@register.filter
def short_date(value):
    if not value:
        return ""
    if hasattr(value, "hour"):
        value = timezone.localtime(value)
    return date_format(value, "j M Y")


@register.simple_tag(takes_context=True)
def copy_count(context, key, count, shown=None, **values):
    """Pick ``key.one`` or ``key.other`` and fill in {count}, written as ``shown`` when given (compact_count)."""
    suffix = "one" if count == 1 else "other"
    return service.render(
        f"{key}.{suffix}", store=context.get("site_content"), count=count if shown is None else shown, **values
    )


@register.simple_tag(takes_context=True)
def reddit_text(context, cheapest):
    """The text under a card's shared Reddit post title (web/share.py), for share.js to fill in."""
    from web.share import reddit_text as words

    return words(cheapest, store=context.get("site_content"))


@register.simple_tag(takes_context=True)
def page_key(context):
    """The word for this page that clicks and Compare taps are saved under (catalogue/clicks.PAGES), or ""."""
    from catalogue.clicks import PAGES

    match = getattr(context.get("request"), "resolver_match", None)
    return PAGES.get(getattr(match, "url_name", None), "")


@register.simple_tag
def go_link(listing, button, product=None, game=None):
    """The attributes of a link to a shop through /go/: its address naming the button pressed (?b=, saved with
    the click, catalogue/clicks.py) and what Analytics' affiliate_click event reports (analytics.js): the
    button, the shop, the product and its game. ``product`` is the listing's and ``game`` the product's, passed
    where the page has them loaded, so the link costs no query of its own."""
    product = product or listing.product
    game = game or product.game
    return format_html(
        'href="{}?b={}" data-click="{}" data-click-shop="{}" data-click-product="{}" data-click-game="{}"',
        listing.get_outbound_url(), button, button, listing.retailer.name, product.name, game.display_short,
    )


@register.filter
def per_pack(price, packs):
    """A price shared over the packs a product holds (catalogue/contents.py), or None for fewer than two."""
    from catalogue.contents import price_a_pack

    return price_a_pack(price, packs)


@register.filter
def compact_count(value):
    """A count short enough for a small tile: 950, 1.2k, 12k. Rounded down, so it never claims more than there is."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return value
    if n < 1000:
        return str(n)
    if n < 10000:
        tenths = n // 100
        return f"{tenths // 10}k" if tenths % 10 == 0 else f"{tenths // 10}.{tenths % 10}k"
    return f"{n // 1000}k"


# Stands in for the price while the sentence around it is filled in and escaped.
PRICE_MARK = "\x00price\x00"


@register.simple_tag(takes_context=True)
def price_sentence(context, key, price, known, href=None, **values):
    """A sentence from the registry with its {price} drawn as a price: green, saying whether delivery is
    included, as every price on the site does, and linked to ``href`` when one is given."""
    store = context.get("site_content")
    sentence = service.get(key, store=store, price=PRICE_MARK, **values)
    basis = service.get("browse.price.label" if known else "browse.price.plus", store=store)
    inner = format_html('<span class="price">{}</span> <span class="delivered">{}</span>', gbp(price), basis)
    shown = format_html('<a class="coming__price" href="{}">{}</a>', href, inner) if href else inner
    before, found, after = sentence.partition(PRICE_MARK)
    return format_html("{}{}{}", before, shown if found else "", after)


@register.simple_tag(takes_context=True)
def price_count(context, key, count, price, known, href=None, **values):
    """price_sentence for ``key.one`` or ``key.other`` by ``count``, with {count} filled in."""
    suffix = "one" if count == 1 else "other"
    return price_sentence(context, f"{key}.{suffix}", price, known, href, count=count, **values)


@register.simple_tag(takes_context=True)
def preorders_from(context, product_set):
    """'Pre-orders from £x at n shops' for a set still to come, the price linked to the product it is for.
    The row's own button goes to the shop through /go/."""
    from django.urls import reverse

    return price_count(context, "new.coming_soon.preorders_from", product_set.preorder_count, product_set.best_price,
                       product_set.best_known, reverse("web:product", args=[product_set.best_slug]))


@register.filter
def has_come(day):
    """True for a release date that is today or past, in UK time."""
    return day is not None and day <= timezone.localdate()


@register.simple_tag
def can_remind(day=None):
    """True when a drop reminder asked for now could be sent: reminder emails are set up and the drop's day
    (None for a month-only drop) has not passed, or is today with its email window still open."""
    from catalogue import drop_alerts, mail

    return mail.enabled() and drop_alerts.can_send(day)


@register.simple_tag
def product_image(product, size="thumb"):
    """The product's picture for the slot ``size``, asking the shop's CDN for a copy that size where it can
    (catalogue/images.py), with larger copies for wider boxes and sharp screens."""
    picture = images.responsive(product.image_src, size) if product.image_src else None
    return render_to_string(
        "web/includes/product_image.html", {"product": product, "size": size, "picture": picture}
    )


@register.filter
def link_email(text, email):
    """Escape ``text`` and turn ``email`` inside it into a mailto link."""
    safe_text = conditional_escape(text)
    if not email:
        return safe_text
    safe_email = conditional_escape(email)
    link = format_html('<a href="mailto:{}">{}</a>', email, email)
    return mark_safe(str(safe_text).replace(str(safe_email), str(link), 1))


@register.simple_tag(takes_context=True)
def query_string(context, **changes):
    """Current query string with some values replaced, for pagination links. A stock tab's list fetched
    on its own (pane=1) links to whole pages."""
    params = context["request"].GET.copy()
    params.pop("pane", None)
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.simple_tag
def refer_waiting():
    """For the admin home page only: keyrings to post and referrals held for review, in one query, or None when
    the keyring offer has never had anyone in it."""
    from django.db.models import Count, Q

    from catalogue.models import Referral

    counts = Referral.objects.aggregate(
        review=Count("pk", filter=Q(status=Referral.Status.REVIEW), distinct=True),
        to_post=Count("claims", filter=Q(claims__submitted_at__isnull=False, claims__posted_at__isnull=True)
                      & ~Q(status=Referral.Status.CANCELLED), distinct=True),
        check=Count("claims", filter=Q(claims__submitted_at__isnull=False, claims__posted_at__isnull=True,
                                       claims__same_postcode=True) & ~Q(status=Referral.Status.CANCELLED),
                    distinct=True),
        any=Count("pk", distinct=True),
    )
    return counts if counts["any"] else None


@register.simple_tag
def worker_stopped():
    """The time of the background reader's last heartbeat when it is older than ten minutes, else an empty string.

    Nothing is said before the reader has ever run: the hourly schedule is then the normal way of reading.
    """
    from catalogue.insights import clock
    from catalogue.models import WorkerState

    state = WorkerState.current()
    now = timezone.now()
    if state is None or state.heartbeat_at is None or state.alive(now):
        return ""
    return clock(state.heartbeat_at, now)


@register.filter
def earns(listing):
    """Whether this listing's shop pays a commission, as featured deals count it (web.views.earns)."""
    from web.views import earns as shop_earns

    return bool(listing) and shop_earns(listing)


_INLINE = {}


@register.simple_tag
def inline_static(path):
    """A small static script's text, for a <script> that must run where it stands, before the rows above it
    are painted. Read once per process (every time with DEBUG on, so edits show)."""
    from django.conf import settings
    from django.contrib.staticfiles import finders

    text = None if settings.DEBUG else _INLINE.get(path)
    if text is None:
        found = finders.find(path)
        with open(found, encoding="utf-8") as handle:
            text = handle.read()
        if "</" in text:
            raise ValueError(f"{path} cannot be inlined: it contains '</'")
        _INLINE[path] = text
    return mark_safe(text)


# The ⓘ cards: a small button after a label that opens two plain sentences about it, for people new to card
# games. The browser's own popover (no script: it works with JavaScript off, and a tap outside or Esc closes it).
# The words are in the registry under info.* and edited in Admin. A label with no words for it gets no button.
INFO_ICON = mark_safe(
    '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="6.5" fill="none" '
    'stroke="currentColor" stroke-width="1.5"/><path d="M8 7.2v4" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round"/><circle cx="8" cy="4.9" r="0.95" fill="currentColor"/></svg>'
)
INFO_KINDS = ("type", "lang", "badge", "game")


def info_keys(kind, value, game=""):
    """The registry keys of an ⓘ card's words, in order: the card's text, then a line for this game when the
    registry has one (a Japanese Pokémon pack's card count)."""
    from catalogue.languages import CHINESE_FAMILY

    if kind == "lang":
        value = "zh" if value in CHINESE_FAMILY else value if value in ("ja", "ko") else "other" if value else ""
    if not value or kind not in INFO_KINDS:
        return []
    if kind == "game":
        # What the game is, then a tip about it (a game's slug, as "yu-gi-oh", is "yu_gi_oh" in a key).
        value = value.replace("-", "_")
        return [f"info.game.{value}", f"info.game.{value}_tip"]
    return [f"info.{kind}.{value}", f"info.{kind}.{value}_{game.replace('-', '_')}" if game else ""]


@register.simple_tag(takes_context=True)
def info(context, kind, value, title="", game=""):
    """An ⓘ button after a label (``title``, as the page shows it) and its card. ``kind`` is "type" (a product
    type code), "lang" (a language code: Japanese, Korean, Chinese, or any other but English), "badge" (a
    badge's code) or "game" (a game's slug: what the game is and a tip about it). Each card is written once per page, however many buttons open it; its id carries a short
    sum of the page's address, the same on every visit, so a list fetched into the page by script (another
    address) never repeats one."""
    import zlib

    from content.registry import REGISTRY

    keys = [key for key in info_keys(kind, value, game) if key in REGISTRY]
    if not keys:
        return ""
    store = context.get("site_content")
    title = title or (service.get(f"product.badge.{value}", store=store) if kind == "badge" else "")
    words = [service.get(key, store=store, language=title) for key in keys]
    words = [text for text in words if text]
    if not words:
        return ""
    request = context.get("request")
    cards = getattr(request, "_info_cards", None) if request is not None else None
    if cards is None:
        address = request.get_full_path() if request is not None else ""
        cards = {"prefix": f"{zlib.crc32(address.encode()) & 0xFFFFF:x}", "done": set()}
        if request is not None:
            request._info_cards = cards
    ident = f"info-{cards['prefix']}-{kind}-{keys[0].split('.')[-1]}".replace("_", "-")
    button = format_html(
        '<button type="button" class="info" popovertarget="{}" aria-label="{}">{}</button>',
        ident, service.get("info.open", store=store, thing=title), INFO_ICON,
    )
    if ident in cards["done"]:
        return button
    cards["done"].add(ident)
    return format_html(
        '{}<span class="info-card" id="{}" popover role="dialog" aria-labelledby="{}-title">'
        '<span class="info-card__head"><span class="info-card__title" id="{}-title">{}</span>'
        '<button type="button" class="button button--quiet button--small" popovertarget="{}" '
        'popovertargetaction="hide">{}</button></span>{}</span>',
        button, ident, ident, ident, title, ident, service.get("product.share.close", store=store),
        format_html_join_lines(words),
    )


def format_html_join_lines(lines):
    return mark_safe("".join(format_html('<span class="info-card__text">{}</span>', line) for line in lines))
