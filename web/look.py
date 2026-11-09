"""
The site's look: the owner's switch for the new home page (web/feed.py), read from the cache so no page gains a query.

The marketing team's redesign of 4 Nov 2026 (a feed of today's best prices, product type tiles, a rail of deals and the
next drop, with the search box and the games fixed to the foot of a phone) is built in beside the usual home page, not
in its place. The owner switches between them in Admin (catalogue.SiteLook, "Site look"), so the new look can be tried
and rolled back in a tap, without a deploy. The owner, 4 Nov 2026: "can we easily roll this back if I don't like it".

Like the seasonal themes (web/seasons.py), the switch is kept in the shared cache: remember() reads it after it is saved
and from warm_pages every five minutes, so an emptied cache (a deploy) shows the usual home page for five minutes at
most. Staff can see either look any time by adding ?look=feed or ?look=classic to the home page's or a game page's
address. The compact layout (web/compact.py, the owner's of 9 Nov 2026) is a second switch on the same row, winning
over the feed while on; ?look=compact previews it.
"""

from django.core.cache import cache

KEY = "site-look-feed"
COMPACT_KEY = "site-look-compact"
PREVIEW = "look"   # the query parameter staff preview a look with
FEED, CLASSIC, COMPACT = "feed", "classic", "compact"


def remember():
    """Read the owner's switches (one query) and keep them in the shared cache. Never opened in Admin: off. Returns
    the new home page's switch."""
    from catalogue.models import SiteLook

    found = SiteLook.objects.filter(pk=1).values_list("feed_home", "compact").first() or (False, False)
    switched, compact = bool(found[0]), bool(found[1])
    cache.set(KEY, switched, None)
    cache.set(COMPACT_KEY, compact, None)
    return switched


def feed_on():
    """The owner's switch, from the cache alone. Missing (an emptied cache): off until warm_pages puts it back."""
    return bool(cache.get(KEY, False))


def compact_on():
    """The compact layout's switch (web/compact.py), from the cache alone, as feed_on."""
    return bool(cache.get(COMPACT_KEY, False))


def mode(request):
    """The look this page is drawn in: COMPACT, FEED or CLASSIC. The owner's switches (the compact layout wins over
    the new home page), or for staff the look named in ?look= (the request's user is only looked at when the address
    asks)."""
    asked = request.GET.get(PREVIEW)
    if asked in (FEED, CLASSIC, COMPACT):
        user = getattr(request, "user", None)
        if user is not None and user.is_active and user.is_staff:
            return asked
    if compact_on():
        return COMPACT
    return FEED if feed_on() else CLASSIC


def for_request(request):
    """Whether this page is drawn as the feed (mode)."""
    return mode(request) == FEED


def compact_for_request(request):
    """Whether this page is drawn in the compact layout (mode)."""
    return mode(request) == COMPACT
