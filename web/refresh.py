"""
The refresh dots under the logo (web/templates/web/includes/refresh_dots.html): five dots that fill as the next
price refresh nears, and a line, on a tap, counting down to it in minutes and seconds. On a product page that is the next read of one
of its shops by the background reader (Retailer.next_read_at, or the end of its wait after errors); on every other
page it is the next five-minute rebuild of the page's lists (warm_pages --due, from cron at 1, 6, 11... minutes
past the hour, deploy/install.sh). Nothing here costs a query: a product page has its shops loaded with its
listings, the lists' tick is the clock, and the wording is the template's, from the request's one lookup. The
owner, 6 Nov 2026: "5 dots to accumulate under the RipRaptor logo to show when the prices are next going to be
refreshed, so users who are trying to get the latest deal before it sells out know it's looking for them. Subtle
dots that when tapped simply say the time to refreshing the stock list."
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

DOTS = 5
PRODUCT, LISTS = "product", "lists"
SHOP_CYCLE = 60 * 60       # a shop is read at least hourly: the dots fill over the hour before its next read
LISTS_CYCLE = 5 * 60       # the page lists are rebuilt when due every five minutes
LISTS_MINUTE = 1           # at this minute of each five (the warm_pages line in deploy/install.sh)


@dataclass
class Refresh:
    kind: str
    next: datetime
    cycle: int
    now: datetime

    @property
    def remaining(self):
        """Seconds until the refresh, never under zero."""
        return max(0, int((self.next - self.now).total_seconds()))

    @property
    def due(self):
        return self.remaining <= 0

    @property
    def minutes_part(self):
        return self.remaining // 60

    @property
    def seconds_part(self):
        return self.remaining % 60

    @property
    def dots(self):
        """Which of the DOTS are lit: all once the refresh is due, else more the nearer it is, never all."""
        if self.due:
            return [True] * DOTS
        lit = min(DOTS - 1, max(0, round(DOTS * (1 - self.remaining / self.cycle))))
        return [i < lit for i in range(DOTS)]


def for_lists(now=None):
    """The next five-minute rebuild of the page lists."""
    now = now or timezone.now()
    tick = now.replace(second=0, microsecond=0)
    while tick <= now or tick.minute % 5 != LISTS_MINUTE:
        tick += timedelta(minutes=1)
    return Refresh(LISTS, tick, LISTS_CYCLE, now)


def shop_next(retailer, now):
    """When the background reader next reads ``retailer``, or None for a shop it will not: paused, never read, or
    gone. A shop waiting after errors is read when its wait ends, at the earliest."""
    if not retailer.is_active or retailer.reading_paused or retailer.next_read_at is None:
        return None
    when = retailer.next_read_at
    if retailer.backoff_until is not None and retailer.backoff_until > when:
        when = retailer.backoff_until
    return when


def for_product(listings, now=None):
    """The next read of any shop among ``listings`` (their retailers loaded), else the lists' refresh."""
    now = now or timezone.now()
    whens = [when for when in (shop_next(listing.retailer, now) for listing in listings) if when is not None]
    if not whens:
        return for_lists(now)
    return Refresh(PRODUCT, min(whens), SHOP_CYCLE, now)
