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
    """Current query string with some values replaced, for pagination links."""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"
