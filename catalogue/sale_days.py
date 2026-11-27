"""
The days of the year's big sale, worked out, never typed in: Black Friday is the day after the fourth Thursday of
November and Cyber Monday the Monday after it.

Two things follow them. The site's Black Friday pages and banner (web/black_friday.py), and the readers, which for
those days note which products a shop has put in its own Black Friday sale (importers.sale_handles, says_sale:
its sale collection, a product's tag or its name), so the Black Friday pages can list each with what our price record
says of it. The owner, 27 Nov 2026: "Can also the crawlers look for any black Friday deals in their searches".
"""

from datetime import date, timedelta

from django.utils import timezone

SEASON_FROM = 14                # the banner shows, and shops' sales are looked for, this many days before Black Friday
BLACK_FRIDAY = "black_friday"   # the sale a listing is in (Listing.Sale)


def black_friday(year):
    """Black Friday of ``year``: the day after the fourth Thursday of November."""
    first = date(year, 11, 1)
    thursday = first + timedelta(days=(3 - first.weekday()) % 7)
    return thursday + timedelta(weeks=3, days=1)


def cyber_monday(year):
    return black_friday(year) + timedelta(days=3)


def on(today=None):
    """Whether shops' Black Friday sales are looked for today: from SEASON_FROM days before Black Friday to the end of
    Cyber Monday, the days the site's own banner shows."""
    today = today or timezone.localdate()
    return black_friday(today.year) - timedelta(days=SEASON_FROM) <= today <= cyber_monday(today.year)
