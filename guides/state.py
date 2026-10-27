"""
Whether any guide is live, and which guide each door in links to, kept in the shared cache so the nav link,
the footer link and the lines on product pages cost no page a query.

remember() reads it from the database (one query): after any guide is saved or deleted, when a Guides page
is opened, and from warm_pages every five minutes, so an emptied cache (a deploy) hides the links for five
minutes at most.
"""

from django.core.cache import cache
from django.urls import reverse

KEY = "guides-live"
EMPTY = {"live": False, "doors": {}}


def remember():
    from .models import Guide

    rows = (Guide.objects.filter(is_published=True).order_by("position", "pk")
            .values_list("slug", "door", "door_line", "title"))
    doors, live = {}, False
    for slug, door, line, title in rows:
        live = True
        if door and door not in doors:
            doors[door] = {"url": reverse("web:guide", args=[slug]), "line": line or title, "title": title}
    flag = {"live": live, "doors": doors}
    cache.set(KEY, flag, None)
    return flag


def current():
    return cache.get(KEY) or EMPTY


def door(name):
    """{"url", "line", "title"} of the guide linked from ``name`` ("buying" or "sold_out"), or None. The line is
    the guide's own words for that place; elsewhere (the checker, the High price check) its title is shown."""
    return current()["doors"].get(name)


def on_change(sender, **kwargs):
    remember()
