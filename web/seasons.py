"""
Seasonal themes: Halloween, Christmas, Easter and the summer holidays, each switching itself on during its dates.

The owner, 2 Nov 2026: "I want ripraptor to have seasonal themes, make sure that there is a seasonal theme created
with art work ... and make sure that the themes automatically enable via an on on the admin". While a theme is on,
the raptor in the logo wears its hat (every page), its decorations hang along the top of every page, and its
pictures stand either side of the home page's headline on wide screens. The artwork is drawn in
web/templates/web/includes/season/, every colour a token, and none of it is near a price.

Whether the owner's switch (catalogue.SeasonalThemes, in Admin) is on is kept in the shared cache, so no page gains
a query: remember() reads it after the switch is saved and from warm_pages every five minutes, so an emptied cache
(a deploy) leaves the usual look for five minutes at most. Which theme is on is worked out from today's date.

Staff can see any theme on any day by adding ?theme=christmas (or halloween, easter, summer) to a page's address.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.core.cache import cache
from django.utils import timezone

KEY = "seasonal-themes-on"
PREVIEW = "theme"   # the query parameter staff preview a theme with


def easter_sunday(year):
    """Easter Sunday in the Gregorian calendar (the anonymous Gregorian algorithm, as in Meeus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    starts: tuple = None    # (month, day), or None for a theme that moves with Easter
    ends: tuple = None

    def window(self, year):
        """The first and last day this theme is on in ``year``."""
        if self.key == "easter":
            sunday = easter_sunday(year)
            return sunday - timedelta(days=9), sunday + timedelta(days=1)   # the Friday before Palm Sunday to Easter Monday
        return date(year, *self.starts), date(year, *self.ends)

    def is_on(self, day):
        first, last = self.window(day.year)
        return first <= day <= last

    def next_window(self, day):
        """This theme's dates on or after ``day``: this year's when they have not ended, else next year's."""
        first, last = self.window(day.year)
        return (first, last) if last >= day else self.window(day.year + 1)

    @property
    def hat(self):
        return f"web/includes/season/{self.key}_hat.html"

    @property
    def garland(self):
        return f"web/includes/season/{self.key}_garland.html"

    @property
    def scene(self):
        return f"web/includes/season/{self.key}_scene.html"


# In the order of the year. No two overlap.
THEMES = [
    Theme("easter", "Easter"),
    Theme("summer", "Summer holidays", (7, 20), (8, 31)),
    Theme("halloween", "Halloween", (10, 17), (10, 31)),
    Theme("christmas", "Christmas", (12, 1), (12, 26)),
]
BY_KEY = {theme.key: theme for theme in THEMES}


def theme_for(day):
    """The theme whose dates hold ``day``, or None."""
    return next((theme for theme in THEMES if theme.is_on(day)), None)


def remember():
    """Read the owner's switch (one query) and keep it in the shared cache. A site that has never opened the
    switch in Admin has it on, as the owner asked."""
    from catalogue.models import SeasonalThemes

    found = SeasonalThemes.objects.filter(pk=1).values_list("on", flat=True).first()
    switched = True if found is None else found
    cache.set(KEY, switched, None)
    return switched


def switched_on():
    """The owner's switch, from the cache alone. Missing (an emptied cache): off until warm_pages puts it back."""
    return bool(cache.get(KEY, False))


def current(day=None):
    """Today's theme, or None when it is between seasons or the owner has switched themes off."""
    if not switched_on():
        return None
    return theme_for(day or timezone.localdate())


def for_request(request):
    """The theme a page is drawn with: today's, or for staff the one named in ?theme= (the request's user is only
    looked at when the address asks)."""
    asked = request.GET.get(PREVIEW)
    if asked and asked in BY_KEY:
        user = getattr(request, "user", None)
        if user is not None and user.is_active and user.is_staff:
            return BY_KEY[asked]
    return current()
