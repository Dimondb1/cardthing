"""
The games for the header's Games menu on a desktop screen (header.html; the owner, 9 Nov 2026, on the desktop site: "Do
not present 14 tiny game icons stretching across the screen"), read from the shared cache alone so no page gains a
query, like the refer link and the site's look. Pages that read the games anyway (the compact layout's dock, the
feed) put them there, and so does warm_pages every few minutes; with none kept the menu offers All games alone.
"""

from django.core.cache import cache

KEY = "nav:games:v1"
SECONDS = 60 * 60


def remember(games):
    """Keep ``games`` (feed.games_with_products: active games with products, in the bar's order) for the menu."""
    from . import feed

    found = [{"name": game.name, "label": game.display_short, "slug": game.slug, "url": game.get_absolute_url(),
              "icon": feed.icon_for(game.slug), "products": getattr(game, "product_count", None)} for game in games]
    cache.set(KEY, found, SECONDS)
    return found


def games():
    """The games kept for the menu, or [] when none are."""
    return cache.get(KEY) or []
