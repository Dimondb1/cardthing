from django import template

from inbox.service import unread_count
from inbox.shops import open_count

register = template.Library()


@register.simple_tag
def inbox_unread():
    return unread_count()


@register.simple_tag
def shops_waiting():
    return open_count()
