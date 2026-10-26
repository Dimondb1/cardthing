"""
Whole pages kept ready, so a tap on a game shows its page at once.

A game or set page at the live site's size takes a good part of a second to build: the counts for its
tabs, its sets and their stock, the cards and their prices (web.views._browse). Every visitor sees the
same page (nothing on it is theirs), so once built it is kept in the shared cache for
RIPRAPTOR_PAGE_CACHE_SECONDS and served from there, each tab's list and each set of filters under its own
address. Prices on a list can then be that many seconds behind the shops; the product page, the money
page, is never cached. warm_pages (web.warming) builds each game's own page before a visitor asks, so the
first tap of the day never pays either.
"""

import hashlib
import time
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse
from django.http.request import validate_host
from django.test import RequestFactory
from django.urls import resolve

HEADER = "X-RipRaptor-Page"   # "cached" on a copy from the cache, so a check can tell
# Bumped by forget(): every page kept before it is then a miss, without a key to find and delete.
GENERATION_KEY = "page-cache-generation"


def seconds():
    return settings.RIPRAPTOR_PAGE_CACHE_SECONDS


def on():
    return seconds() > 0


def key_for(path):
    return "page:" + hashlib.sha256(f"{cache.get(GENERATION_KEY, 0)}|{path}".encode()).hexdigest()[:40]


def forget():
    """Every kept page is a miss from now: the owner changed something and no visitor should see the page
    built before it (web.listcache.mark_stale with ``hard``)."""
    cache.set(GENERATION_KEY, time.time(), None)


def has(path):
    return cache.has_key(key_for(path))


def cached_page(view):
    """Keep the view's answer to a GET in the shared cache for RIPRAPTOR_PAGE_CACHE_SECONDS, by its full
    address, and serve the copy to the next visitor. Only a 200 is kept, and never with cookies to set."""
    def wrapper(request, *args, **kwargs):
        if request.method != "GET" or not on():
            return view(request, *args, **kwargs)
        key = key_for(request.get_full_path())
        found = cache.get(key)
        if found is not None:
            response = HttpResponse(found["body"])
            for name, value in found["headers"]:
                response[name] = value
            response[HEADER] = "cached"
            return response
        response = view(request, *args, **kwargs)
        if response.status_code == 200 and not response.cookies and not getattr(response, "streaming", False):
            headers = [(name, value) for name, value in response.items() if name.lower() != "set-cookie"]
            cache.set(key, {"body": response.content, "headers": headers}, seconds())
        return response

    wrapper.__name__, wrapper.__doc__, wrapper.__wrapped__ = view.__name__, view.__doc__, view
    return wrapper


def warm_host():
    """The host a warmed page is built for, so its own links (canonical, share) carry the site's address: the
    one in RIPRAPTOR_SITE_URL when it is allowed, else the first allowed host."""
    site = urlsplit(settings.RIPRAPTOR_SITE_URL)
    host = site.hostname or ""
    if host and validate_host(host, settings.ALLOWED_HOSTS):
        return host, site.scheme or "https"
    named = [h for h in settings.ALLOWED_HOSTS if h and not h.startswith(("*", "."))]
    return (named[0] if named else "localhost"), "https"


def warm(path):
    """Build the page at ``path`` as a visitor would see it and keep it, whoever asks first. Returns True when
    a page was built and kept."""
    host, scheme = warm_host()
    request = RequestFactory(HTTP_HOST=host).get(path, secure=scheme == "https")
    request.user = AnonymousUser()
    request.resolver_match = match = resolve(urlsplit(path).path)
    response = match.func(request, *match.args, **match.kwargs)
    return response.status_code == 200 and response.get(HEADER) != "cached"


def game_paths(games):
    """The pages kept warm: each game's own page, the one a tap from the home page opens. None while off."""
    return [game.get_absolute_url() for game in games] if on() else []
