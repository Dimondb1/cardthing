import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe

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
def copy_count(context, key, count, **values):
    """Pick ``key.one`` or ``key.other`` and fill in {count}."""
    suffix = "one" if count == 1 else "other"
    return service.render(
        f"{key}.{suffix}", store=context.get("site_content"), count=count, **values
    )


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
    return render_to_string(
        "web/includes/product_image.html", {"product": product, "size": size}
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
