"""
Wrong Admin sign-ins, counted per address. Ten wrong tries in 15 minutes and that address is turned away for
15 minutes, without its password being checked, so guessing passwords from one place stops being worth it. A
right sign-in clears only the wrong tries made with its own username, so a guide editor's sign-in from the same
address never lowers the count against the owner's login. Kept in the shared cache, so every web worker sees the
same count.

The block is per address only: a block per username would let anyone lock the owner out by typing their name.
The tries are kept by username inside the address's record only so that one account's right sign-in cannot
clear another's.
"""

import time

from django.core.cache import cache

from catalogue.insights import client_ip

LIMIT = 10
WINDOW = 15 * 60


def key(request):
    return f"twostep:fails:{client_ip(request)}"


def name(username):
    return str(username or "")[:150]


def record(request, now):
    """The address's (until, {username: tries}) while its window is open, else a fresh one."""
    stored = cache.get(key(request))
    if isinstance(stored, tuple) and len(stored) == 2 and isinstance(stored[1], dict) and now < stored[0]:
        return stored[0], dict(stored[1])
    return now + WINDOW, {}


def save(request, until, tries, now):
    # Set with what is left, not the cache's default: the file cache's incr would reset the expiry.
    cache.set(key(request), (until, tries), timeout=max(1, int(until - now) + 1))


def locked(request):
    _, tries = record(request, time.time())
    return sum(tries.values()) >= LIMIT


def failed(request, username=""):
    """One more wrong try. The count lasts WINDOW from the first; the tenth starts a WINDOW-long block."""
    now = time.time()
    until, tries = record(request, now)
    tries[name(username)] = tries.get(name(username), 0) + 1
    if sum(tries.values()) >= LIMIT:
        until = now + WINDOW
    save(request, until, tries, now)


def cleared(request, username=""):
    """A right sign-in: forget only the wrong tries made with this username from this address."""
    now = time.time()
    until, tries = record(request, now)
    if tries.pop(name(username), None) is not None:
        if tries:
            save(request, until, tries, now)
        else:
            cache.delete(key(request))
