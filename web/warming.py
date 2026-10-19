"""
Keep the cached lists the busiest pages read built, so a visitor rarely builds them.

The home page's deals, the Deals page's savings and drops, Out now and the sets coming soon are cached
(web.views) because each reads every priced product. An import, a tidy or the background reader clears
them whenever prices change (catalogue.signals.clear_list_caches), and the next visitor used to wait
while they were built again. Now the background jobs build them straight after:

- the background reader (catalogue.worker) fills any that are missing within a turn of its loop, and
  builds them all again shortly before they expire, so they never lapse while it runs;
- tidy_all, import_prices and watch_stock fill them when they finish, for when the reader is not running;
- ``manage.py warm_pages`` builds them all by hand, and the installer runs it after emptying the cache.

Nothing here changes what a list holds or how long it is kept: a list is built by the same function a
visitor's request would call, and still expires after RIPRAPTOR_HOME_CACHE_SECONDS, so prices age out
of it under the same stale rules.
"""

import logging
import time

from django.core.cache import cache

logger = logging.getLogger("catalogue")


def cached_keys(games):
    """The cache keys warm_pages fills, for ``games`` (the active games)."""
    from catalogue.signals import (
        COMING_SOON_CACHE_KEY, DEALS_CACHE_KEY, HOME_CACHE_KEY, HOME_DROPS_CACHE_KEY, NEW_CACHE_KEY,
    )

    return [HOME_CACHE_KEY, DEALS_CACHE_KEY, NEW_CACHE_KEY, COMING_SOON_CACHE_KEY, HOME_DROPS_CACHE_KEY,
            *(f"{COMING_SOON_CACHE_KEY}:{game.slug}" for game in games)]


def active_games():
    from catalogue.models import Game

    return list(Game.objects.filter(is_active=True))


def missing(games=None):
    """The keys of cached_keys not in the cache now. Reads no list: has_key checks the entry alone."""
    games = active_games() if games is None else games
    return [key for key in cached_keys(games) if not cache.has_key(key)]


def warm_pages(fresh=False):
    """Build the cached lists the main pages read and return {list name: seconds taken}.

    Without ``fresh`` only lists missing from the cache are built (a list already there costs a cache
    read); with it every list is built again, for a refresh before they expire. The coming soon sets go
    first: the home page's Coming soon rows are made from them.
    """
    from web import views

    games = active_games()
    candidates = views.shared_candidates()   # the home and Deals lists pick from the same offers, read once
    steps = [("coming soon", lambda: views.coming_soon_sets(limit=None, fresh=fresh))]
    steps += [(f"coming soon: {game.slug}", lambda game=game: views.coming_soon_sets(game, limit=None, fresh=fresh))
              for game in games]
    steps += [
        ("home coming soon", lambda: views.home_coming_soon(fresh=fresh)),
        ("home deals", lambda: views.home_lists(fresh=fresh, candidates=candidates)),
        ("out now", lambda: views.home_drops_new(fresh=fresh)),
        ("deals", lambda: views.deals_lists(fresh=fresh, candidates=candidates)),
    ]
    took = {}
    for name, build in steps:
        started = time.monotonic()
        build()
        took[name] = time.monotonic() - started
    return took


def warm_quietly(fresh=False):
    """warm_pages for the end of a background job: a failure is logged and never fails the job, whose own
    work is done. Returns the seconds taken, or None when it failed."""
    started = time.monotonic()
    try:
        warm_pages(fresh=fresh)
    except Exception:  # noqa: BLE001 - the next job or visitor builds the lists instead
        logger.exception("Could not build the cached page lists")
        return None
    return time.monotonic() - started
