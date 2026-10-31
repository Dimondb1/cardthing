"""
IndexNow: tell Bing, and the search engines that share its submissions (Yandex, Seznam, Naver), which product pages
changed, within the hour, rather than waiting for their crawlers to come round. Bing's index is one of the main
sources ChatGPT's search and Copilot answer from, so a new product or a price or stock change can reach them the same
day (the owner, 31 Oct 2026: RipRaptor lists poorly in Google and in AI answers). Only public page addresses are
sent: the pages the sitemap lists (web/sitemaps.py indexable_products) whose content changed since the last run.

The key is a hash of the site's secret key, so it is the same on every run and never needs setting up. The site
serves it at /<key>.txt (web/views.py indexnow_key), which is how the search engines check the pages are ours. The
hourly cron line runs ``manage.py indexnow``; RIPRAPTOR_INDEXNOW=0 stops it.
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
    """Submit the pages changed since the last run. Returns how many were (or would be) sent. The run's time is
    kept only once the submission is accepted, so a failed one is sent again next hour."""
    now = now or timezone.now()
    since = cache.get(LAST_KEY) or now - FIRST_LOOK
    urls = changed_urls(since, now)
    if dry_run:
        return len(urls)
    if urls:
        submit(urls)
    cache.set(LAST_KEY, now, None)
    return len(urls)
