from django import template
from django.utils import timezone

register = template.Library()


@register.simple_tag
def guides_due():
    """Published guides whose review date has passed, for the admin home."""
    from guides.models import Guide

    return Guide.objects.filter(is_published=True, next_review__lt=timezone.localdate()).count()
