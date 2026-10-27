from django import template
from django.utils import timezone

register = template.Library()


@register.simple_tag(takes_context=True)
def guide_editor_only(context):
    """True on the admin home for a Guide editors login: it is shown Guides alone."""
    from guides.access import editor_only

    request = context.get("request")
    return editor_only(getattr(request, "user", None))


@register.simple_tag
def guides_due():
    """Published guides whose review date has passed, for the admin home."""
    from guides.models import Guide

    return Guide.objects.filter(is_published=True, next_review__lt=timezone.localdate()).count()
