import math
import time
from functools import partial

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .languages import product_language
from .models import Game, Listing, Product, ProductSet
from .search import build_search_text

HOME_CACHE_KEY = "web:home-lists:v6"
DEALS_CACHE_KEY = "web:deals:v2"
NEW_CACHE_KEY = "web:home-new:v3"
# Sets still to come; each game's own list is cached under this key plus ':<game>'.
COMING_SOON_CACHE_KEY = "web:coming-soon:v2"
# The home page's Coming soon rows in popularity order (catalogue.popularity).
HOME_DROPS_CACHE_KEY = "web:home-drops:v1"
LIST_CACHE_KEYS = (HOME_CACHE_KEY, DEALS_CACHE_KEY, NEW_CACHE_KEY, COMING_SOON_CACHE_KEY, HOME_DROPS_CACHE_KEY)


# When this process last cleared the feeds (time.monotonic), for the debounce.
_last_clear = None


def clear_list_caches(force=False):
    """
    Mark the cached home, deals and new lists stale and drop every feed once the current transaction commits.

    Another process shares the cache, so a clear before the commit would let it refill the lists from
    the rows as they were. Outside a transaction the clear runs at once. The tests, which never
    commit, clear at once (RIPRAPTOR_CLEAR_AFTER_COMMIT) and get clear_list_caches_now's answer.
    """
    from web.listcache import owner_editing

    hard = owner_editing.get()   # the owner's own edit shows at once (web.listcache.mark_stale)
    if not settings.RIPRAPTOR_CLEAR_AFTER_COMMIT:
        return clear_list_caches_now(force, hard=hard)
    transaction.on_commit(partial(clear_list_caches_now, force, hard=hard))
    return None


def clear_list_caches_now(force=False, hard=False):
    """
    Mark the cached lists stale and drop the feeds now, in the cache every process shares.

    The lists stay in the cache: visitors keep being served them while the background jobs build them
    again (web.listcache), so a price change never leaves a visitor waiting for a build. Marking them is
    one small cache write, so every clear does it; ``force`` (the end of an import, a restock found by the
    stock watcher) asks for them to be built again on the jobs' next turn. ``hard`` (the owner's own edits,
    in Django Admin or on the site, web.middleware.OwnerEditsMiddleware) stops the stale copies being served
    at all: the reader builds them on its next turn and a visitor who comes first builds them, so a hidden
    product or a corrected price is gone from the lists straight away.

    The feeds, which no job builds, are still dropped. A save fires this for each changed listing, so
    without force a feed clear within RIPRAPTOR_CACHE_CLEAR_SECONDS of this process's last one is skipped
    and the feeds expire when that window closes instead. Returns whether the feeds were cleared now.
    """
    global _last_clear
    from web import listcache
    from web.feeds import feed_cache_keys

    listcache.mark_stale(force=force, hard=hard)
    now = time.monotonic()
    wait = settings.RIPRAPTOR_CACHE_CLEAR_SECONDS
    if not (force or hard) and _last_clear is not None and now - _last_clear < wait:
        left = max(1, math.ceil(wait - (now - _last_clear)))
        for key in feed_cache_keys():
            cache.touch(key, left)
        return False
    _last_clear = now
    cache.delete_many(feed_cache_keys())
    return True


@receiver([post_save, post_delete], sender=Product)
@receiver([post_save, post_delete], sender=Listing)
@receiver([post_save, post_delete], sender=ProductSet)
def clear_home_lists(sender, **kwargs):
    clear_list_caches()


def _refresh(products):
    """Keep what a product's set and game decide up to date: its search text and its language."""
    changed = []
    for product in products.select_related("game", "product_set"):
        language = product_language(product)
        product.language, old = language, product.language
        text = build_search_text(product)
        if (text, language) != (product.search_text, old):
            product.search_text = text
            changed.append(product)
    Product.objects.bulk_update(changed, ["search_text", "language"], batch_size=500)


@receiver(post_save, sender=Game)
def game_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _refresh(Product.objects.filter(game=instance))


@receiver(post_save, sender=ProductSet)
def set_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _refresh(Product.objects.filter(product_set=instance))
