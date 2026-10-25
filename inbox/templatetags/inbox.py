from django import template

from inbox.outreach import follow_ups_due
from inbox.service import unread_count
from inbox.shops import open_count

register = template.Library()


@register.simple_tag
def inbox_unread():
    return unread_count()


@register.simple_tag
def shops_waiting():
    return open_count()


@register.simple_tag
def outreach_due():
    return follow_ups_due()
