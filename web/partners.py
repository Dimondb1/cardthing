"""
The partners shown by their logos in a row on the home page (catalogue.Partner, added in Admin).

The owner, 3 Nov 2026, with Zatu Games' logo and brand rules: "add a partners section on the website so that we can
ask the same back". The row is read from the shared cache, so the home page gains no query: remember() builds it from
the database (one query) when a partner is saved or deleted, and from warm_pages every five minutes, so an emptied
cache (a deploy) hides the row for a few minutes at most. With no partner shown there is no row at all, so nothing is
claimed on the page until the owner adds one.

Each is a dict of what the template needs: ``name``, ``url`` (where the logo links), ``logo`` (the picture's address)
and its ``width`` and ``height``, so the picture keeps its own proportions.
"""

from django.core.cache import cache

KEY = "partners-shown"


def remember():
    from catalogue.models import Partner

    shown = []
    for partner in Partner.objects.filter(is_shown=True).select_related("retailer"):
        if partner.logo and partner.link:
            shown.append({"name": partner.display_name, "url": partner.link, "logo": partner.logo.url,
                          "width": partner.logo_width, "height": partner.logo_height})
    cache.set(KEY, shown, None)
    return shown


def current():
    return cache.get(KEY) or []
