"""
Keep the cached lists the busiest pages read built, so a visitor rarely builds them.

The home page's deals, the Deals page's savings and drops, Out now and the sets coming soon are cached
(web.views, web.listcache) because each reads every priced product. An import, a tidy or the background
reader marks them stale whenever prices change (catalogue.signals.clear_list_caches); visitors keep being
served the stale copy, and these jobs build them again and replace them in place:

- the background reader (catalogue.worker) builds whatever is due within a turn of its loop (a few seconds);
- tidy_all, import_prices and watch_stock build what is due when they finish;
- cron runs ``manage.py warm_pages --due`` every five minutes, the floor for when the reader is not running;
- ``manage.py warm_pages`` builds them all by hand, and the installer runs it after emptying the cache.

A list is due when it is missing, was cleared (web.listcache.is_due), or is nearly
RIPRAPTOR_HOME_CACHE_SECONDS old. Nothing here changes what a list holds: a list is built by the same function
a visitor's request would call, so prices age out of it under the same stale rules.
"""

import logging
import time

from django.core.cache import cache

from web import listcache

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
    """The keys of cached_keys not in the cache now, and the game pages not kept (web.pagecache). Reads no
    list: has_key checks the entry alone."""
    from web import pagecache

    games = active_games() if games is None else games
    return ([key for key in cached_keys(games) if not cache.has_key(key)]
            + [f"page:{path}" for path in pagecache.game_paths(games) if not pagecache.has(path)])


def due(games=None):
    """The keys of cached_keys the jobs should build again now (web.listcache.is_due), and the game pages not
    kept (web.pagecache): a page is due once its copy has gone."""
    from web import pagecache

    games = active_games() if games is None else games
    now, marks = time.time(), listcache.cleared()
    return ([key for key in cached_keys(games) if listcache.is_due(key, now=now, marks=marks)]
            + [f"page:{path}" for path in pagecache.game_paths(games) if not pagecache.has(path)])


def warm_pages(fresh=False, only=None):
    """Build the cached lists the main pages read, in place, and return {list name: seconds taken}.

    Without ``fresh`` only the lists that are due are built (a list already fresh costs a cache read); with
    it every list is built again. ``only`` narrows that to the keys given (warming.missing(), say). The coming
    soon sets go first: the home page's Coming soon rows are made from them. A list another process is
    building just then is left to it.
    """
    from catalogue.signals import (
        COMING_SOON_CACHE_KEY, DEALS_CACHE_KEY, HOME_CACHE_KEY, HOME_DROPS_CACHE_KEY, NEW_CACHE_KEY,
    )
    from web import views

    games = active_games()
    wanted = set(cached_keys(games) if fresh else due(games))
    if only is not None:
        wanted &= set(only)
    candidates = views.shared_candidates()   # the home and Deals lists pick from the same offers, read once
    steps = [("coming soon", COMING_SOON_CACHE_KEY, lambda: views.coming_soon_sets(limit=None, fresh=True))]
    steps += [(f"coming soon: {game.slug}", f"{COMING_SOON_CACHE_KEY}:{game.slug}",
               lambda game=game: views.coming_soon_sets(game, limit=None, fresh=True)) for game in games]
    steps += [
        ("home coming soon", HOME_DROPS_CACHE_KEY, lambda: views.home_coming_soon(fresh=True)),
        ("home deals", HOME_CACHE_KEY, lambda: views.home_lists(fresh=True, candidates=candidates)),
        ("out now", NEW_CACHE_KEY, lambda: views.home_drops_new(fresh=True)),
        ("deals", DEALS_CACHE_KEY, lambda: views.deals_lists(fresh=True, candidates=candidates)),
    ]
    took = {}
    for name, key, build in steps:
        if key not in wanted:
            continue
        started = time.monotonic()
        build()
        took[name] = time.monotonic() - started
    # The count of shops under the home page's headline, so no visitor counts them either (one query when due).
    from catalogue.pricing import shops_compared

    shops_compared()
    # Each game's own page, built whole and kept (web.pagecache), so the tap from the home page never waits.
    from web import pagecache

    for path in pagecache.game_paths(games):
        if (only is not None and f"page:{path}" not in only) or (not fresh and pagecache.has(path)):
            continue
        started = time.monotonic()
        pagecache.warm(path)
        took[f"page: {path}"] = time.monotonic() - started
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
