import math
import time
from functools import partial

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Game, Listing, Product, ProductSet
from .search import build_search_text

HOME_CACHE_KEY = "web:home-lists:v4"
DEALS_CACHE_KEY = "web:deals:v2"
FOOTBALL_CACHE_KEY = "web:home-football:v2"
NEW_CACHE_KEY = "web:home-new:v2"
LIST_CACHE_KEYS = (HOME_CACHE_KEY, DEALS_CACHE_KEY, FOOTBALL_CACHE_KEY, NEW_CACHE_KEY)


# When this process last cleared the list caches (time.monotonic), for the debounce.
_last_clear = None


def cached_list_keys():
    """Every key clear_list_caches drops: the home, deals and new lists and each feed."""
    from web.feeds import feed_cache_keys

    return [*LIST_CACHE_KEYS, *feed_cache_keys()]


def clear_list_caches(force=False):
    """
    Drop the cached home, deals and new lists and every feed once the current transaction commits.

    Another process shares the cache, so a clear before the commit would let it refill the lists from
    the rows as they were. Outside a transaction the clear runs at once. The tests, which never
    commit, clear at once (RIPRAPTOR_CLEAR_AFTER_COMMIT) and get clear_list_caches_now's answer.
    """
    if not settings.RIPRAPTOR_CLEAR_AFTER_COMMIT:
        return clear_list_caches_now(force)
    transaction.on_commit(partial(clear_list_caches_now, force))
    return None


def clear_list_caches_now(force=False):
    """
    Drop the cached lists and feeds now, in the cache every process shares.

    A save fires this for each changed listing, so without force a clear within
    RIPRAPTOR_CACHE_CLEAR_SECONDS of this process's last one is skipped. A skipped
    clear makes whatever is cached expire when that window closes instead, so a
    change is never hidden for longer than the window. The end of an import, a
    restock found by the stock watcher and the owner's fixes pass force=True so
    what they wrote shows at once. Returns whether it cleared.
    """
    global _last_clear

    now = time.monotonic()
    wait = settings.RIPRAPTOR_CACHE_CLEAR_SECONDS
    if not force and _last_clear is not None and now - _last_clear < wait:
        left = max(1, math.ceil(wait - (now - _last_clear)))
        for key in cached_list_keys():
            cache.touch(key, left)
        return False
    _last_clear = now
    cache.delete_many(cached_list_keys())
    return True


@receiver([post_save, post_delete], sender=Product)
@receiver([post_save, post_delete], sender=Listing)
def clear_home_lists(sender, **kwargs):
    clear_list_caches()


def _refresh(products):
    changed = []
    for product in products.select_related("game", "product_set"):
        text = build_search_text(product)
        if text != product.search_text:
            product.search_text = text
            changed.append(product)
    Product.objects.bulk_update(changed, ["search_text"], batch_size=500)


@receiver(post_save, sender=Game)
def game_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _refresh(Product.objects.filter(game=instance))


@receiver(post_save, sender=ProductSet)
def set_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _refresh(Product.objects.filter(product_set=instance))
