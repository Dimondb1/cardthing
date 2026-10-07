from django import template

from inbox.service import unread_count

register = template.Library()


@register.simple_tag
def inbox_unread():
    return unread_count()
