"""
Template tag for editable site wording.

    {% load sitecopy %}
    {% copy "home.hero.title" %}
    {% copy "product.buy.button" price=listing.delivered_price|gbp retailer=listing.retailer.name %}
    {% copy "home.hero.subtitle" as subtitle %}{% if subtitle %}<p>{{ subtitle }}</p>{% endif %}
"""

from django import template

from content import service

register = template.Library()


@register.simple_tag(takes_context=True, name="copy")
def copy_tag(context, key, **values):
    return service.render(key, store=context.get("site_content"), **values)
