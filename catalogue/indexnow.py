"""
IndexNow: tell Bing, and the search engines that share its submissions (Yandex, Seznam, Naver), which product pages
changed, within the hour, rather than waiting for their crawlers to come round. Bing's index is one of the main
sources ChatGPT's search and Copilot answer from, so a new product or a price or stock change can reach them the same
day (the owner, 31 Oct 2026: RipRaptor lists poorly in Google and in AI answers). Only public page addresses are
sent: the pages the sitemap lists (web/sitemaps.py indexable_products) whose content changed since the last run.

The other pages go too, at most once a day (PAGES_EVERY), since they change with every price: each game's page with
a price that changed, each set's page that changed (by the sitemap's own date), the home page, Deals and Drops, and
the Guides pages (the owner, 9 Nov 2026: Bing showed two of the site's pages and Brave two; "a throttled ping when
pages are published or change materially"). A guide published or edited in Admin goes with the next daily batch.

The key is a hash of the site's secret key, so it is the same on every run and never needs setting up. The site
serves it at /<key>.txt (web/views.py indexnow_key), which is how the search engines check the pages are ours. The
hourly cron line runs ``manage.py indexnow``; RIPRAPTOR_INDEXNOW=0 stops it. ``manage.py indexnow --check`` says
where the key file is, whether the site serves it and what the last run sent.
"""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

ENDPOINT = "https://api.indexnow.org/indexnow"
MOST_URLS = 10_000                # the most one submission may carry
LAST_KEY = "indexnow:last-run"    # when the last submission went, in the shared cache
FIRST_LOOK = timedelta(days=1)    # with no record of it (a first run, or an emptied cache), the day before
PAGES_KEY = "indexnow:pages-sent"  # when the game, set and guide pages last went
PAGES_EVERY = timedelta(hours=24)  # they go at most this often
STATUS_KEY = "indexnow:status"    # what the last accepted run sent, for --check
FIXED_PAGES = ("web:home", "web:deals", "web:drops")   # the pages whose lists change with the day's prices
TIMEOUT = 30


class IndexNowError(Exception):
    pass


def key():
    """The site's IndexNow key: 32 hex characters, the same for as long as the secret key is."""
    return hashlib.sha256(f"{settings.SECRET_KEY}:indexnow".encode()).hexdigest()[:32]


def changed_urls(since, now=None):
    """The full addresses of the product pages the sitemap lists whose own details, set or shops' prices, delivery
    or stock changed since ``since``, newest first, at most MOST_URLS."""
    from web.sitemaps import indexable_products

    changed = indexable_products(now).filter(
        Q(listing_changed__gte=since) | Q(updated_at__gte=since) | Q(set_changed__gte=since)
    ).order_by("-pk").values_list("slug", flat=True)[:MOST_URLS]
    return [settings.RIPRAPTOR_SITE_URL + reverse("web:product", args=[slug]) for slug in changed]


def page_urls(since, now=None):
    """The full addresses of the pages other than products worth sending, for the daily batch: the home page, Deals
    and Drops; every game's page with a shop's price, delivery or stock changed since ``since``; every set's page
    changed since then (the sitemap's own date: its edits, its products', their prices); and, while the Guides pages
    are open, the Guides page, the guide built from live prices and each published guide edited since."""
    from django.db.models import Exists, OuterRef

    from guides import state
    from guides.models import Guide
    from web.sitemaps import SetSitemap

    from .models import Game, Listing

    site = settings.RIPRAPTOR_SITE_URL
    paths = [reverse(name) for name in FIXED_PAGES]
    moved = Listing.objects.filter(product__game=OuterRef("pk"), product__is_active=True, changed_at__gte=since)
    paths += [game.get_absolute_url() for game in Game.objects.filter(is_active=True).filter(Exists(moved)).order_by("pk")]
    sets = SetSitemap()
    paths += [product_set.get_absolute_url() for product_set in sets.items()
              if (sets.lastmod(product_set) or since) > since]
    if state.remember()["live"]:
        paths += [reverse("web:guides"), reverse("web:guide_compare")]
        paths += [guide.get_absolute_url() for guide in Guide.objects.filter(is_published=True, updated_at__gte=since)]
    return [site + path for path in paths]


def submit(urls):
    """Send ``urls`` in one submission. Raises IndexNowError, with the status, when it is not accepted."""
    site = settings.RIPRAPTOR_SITE_URL
    body = {"host": urlsplit(site).hostname, "key": key(), "keyLocation": f"{site}/{key()}.txt", "urlList": urls}
    request = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "RipRaptor (https://ripraptor.com, 1.0)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raise IndexNowError(f"IndexNow answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IndexNowError(f"IndexNow could not be reached: {exc.__class__.__name__}") from exc


def run(now=None, dry_run=False):
    """Submit the product pages changed since the last run, and once a day the other pages that changed (page_urls).
    Returns how many addresses were (or would be) sent. Each time is kept only once the submission is accepted, so a
    failed one is sent again next hour."""
    now = now or timezone.now()
    since = cache.get(LAST_KEY) or now - FIRST_LOOK
    urls = changed_urls(since, now)
    pages_sent = cache.get(PAGES_KEY)
    pages_due = pages_sent is None or now - pages_sent >= PAGES_EVERY
    pages = page_urls(pages_sent or now - FIRST_LOOK, now) if pages_due else []
    urls = (pages + urls)[:MOST_URLS]
    if dry_run:
        return len(urls)
    if urls:
        submit(urls)
    cache.set(LAST_KEY, now, None)
    if pages_due:
        cache.set(PAGES_KEY, now, None)
    if urls:
        cache.set(STATUS_KEY, {"at": now, "sent": len(urls), "pages": len(pages)}, None)
    return len(urls)


def status():
    """What the last accepted run sent: {"at", "sent", "pages"} or None when none is on record."""
    return cache.get(STATUS_KEY)


def pages_sent():
    """When the game, set and guide pages last went, or None."""
    return cache.get(PAGES_KEY)


def check():
    """What --check prints: where the key file is, whether the site serves it there, and the last accepted run.
    {"key_url", "served" (True, False, or None when the site could not be reached), "last": STATUS_KEY's or None,
    "pages_sent": when the daily batch last went or None}."""
    key_url = f"{settings.RIPRAPTOR_SITE_URL}/{key()}.txt"
    served = None
    try:
        request = urllib.request.Request(key_url, headers={"User-Agent": "RipRaptor (https://ripraptor.com, 1.0)"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            served = response.read().decode("utf-8", "replace").strip() == key()
    except urllib.error.HTTPError:
        served = False
    except (urllib.error.URLError, TimeoutError, OSError):
        served = None
    return {"key_url": key_url, "served": served, "last": status(), "pages_sent": pages_sent()}
