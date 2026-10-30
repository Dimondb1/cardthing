"""
Wrong Admin sign-ins, counted per address. Ten wrong tries in 15 minutes and that address is turned away for
15 minutes, without its password being checked, so guessing passwords from one place stops being worth it. A
right sign-in clears the count. Kept in the shared cache, so every web worker sees the same count.

Counted per address only: a count per username would let anyone lock the owner out by typing their name.
"""

import time

from django.core.cache import cache

from catalogue.insights import client_ip

LIMIT = 10
WINDOW = 15 * 60


def key(request):
    return f"twostep:fails:{client_ip(request)}"


def locked(request):
    count, until = cache.get(key(request), (0, 0))
    return count >= LIMIT and time.time() < until


def failed(request):
    """One more wrong try. The count lasts WINDOW from the first; the tenth starts a WINDOW-long block."""
    now = time.time()
    count, until = cache.get(key(request), (0, 0))
    if now >= until:
        count, until = 0, now + WINDOW
    count += 1
    if count >= LIMIT:
        until = now + WINDOW
    # Set with what is left, not the cache's default: the file cache's incr would reset the expiry.
    cache.set(key(request), (count, until), timeout=max(1, int(until - now) + 1))


def cleared(request):
    cache.delete(key(request))
