"""
Seasonal themes: Halloween, Christmas, Easter and the summer holidays, each switching itself on during its dates.

The owner, 2 Nov 2026: "I want ripraptor to have seasonal themes, make sure that there is a seasonal theme created
with art work ... and make sure that the themes automatically enable via an on on the admin". While a theme is on,
the raptor in the logo wears its hat (every page), its decorations hang along the top of every page, and its
pictures stand either side of the home page's headline on wide screens. The artwork is drawn in
web/templates/web/includes/season/, every colour a token, and none of it is near a price.

Christmas is its own look. The owner, 23 Nov 2026, of the fairy lights it first had: "not just adding some bullshit
lights like we've done originally". So at Christmas the site is wrapped as a foil booster pack: the header (with the
home page's headline) and the footer turn midnight blue with a faint gold sparkle, and each ends in a pack's crimped
edge, all drawn in site.css (the Christmas wrap). Orange stays the brand's colour and no red or green is added, as
those mean dearer and cheapest here. The raptor keeps its hat; the lights and the pictures are gone. Two things then
help a present buyer on the home page (includes/season/christmas_gifts.html): Gifts by budget, tiles that open the
price lists the site already has, and Royal Mail's next last posting day.

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
GIFTS_KEY = "seasonal-gift-bands"
PREVIEW = "theme"   # the query parameter staff preview a theme with
ART = ("hat", "garland", "scene")


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
    art: tuple = ART        # the drawn pieces it has, each a template in includes/season/
    bar: str = ""           # the colour of the phone browser's own bar while it is on, when the header is coloured

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

    def piece(self, name):
        """The template of one drawn piece, or "" when this theme has none (a template then draws nothing)."""
        return f"web/includes/season/{self.key}_{name}.html" if name in self.art else ""

    @property
    def hat(self):
        return self.piece("hat")

    @property
    def garland(self):
        return self.piece("garland")

    @property
    def scene(self):
        return self.piece("scene")


# In the order of the year. No two overlap.
THEMES = [
    Theme("easter", "Easter"),
    Theme("summer", "Summer holidays", (7, 20), (8, 31)),
    Theme("halloween", "Halloween", (10, 17), (10, 31)),
    # The wrap (site.css) takes the place of lights and pictures. ``bar`` is --season-wrap in tokens.css.
    Theme("christmas", "Christmas", (12, 1), (12, 26), art=("hat",), bar="#16224a"),
]
BY_KEY = {theme.key: theme for theme in THEMES}

# Gifts by budget (Christmas, the home page): the price lists a present buyer is sent to, each the list page's own
# price band (web.views.PRICE_BANDS) with what it suits, the wording's key under home.gifts in content/registry.py.
GIFT_BANDS = (("0-10", "fillers"), ("10-25", "small"), ("25-50", "main"), ("50-100", "big"))

# Royal Mail's last recommended posting days for UK delivery by Christmas, soonest first, from the Post Office's
# published list (postoffice.co.uk/last-posting-dates, read 23 Nov 2026). They are days for posting, not any shop's
# last day for orders, and the wording says so. A year that is not here shows no line: add each year's when the
# list is published, never a guess.
LAST_POST = {
    2026: (
        ("2nd Class", date(2026, 12, 18)),
        ("Tracked 48", date(2026, 12, 19)),
        ("1st Class and Tracked 24", date(2026, 12, 21)),
        ("Special Delivery Guaranteed", date(2026, 12, 23)),
    ),
}


def theme_for(day):
    """The theme whose dates hold ``day``, or None."""
    return next((theme for theme in THEMES if theme.is_on(day)), None)


def remember():
    """Read the owner's switch (one query) and keep it in the shared cache. A site that has never opened the
    switch in Admin has it on, as the owner asked. At Christmas the budgets with something in stock are kept too
    (one more query), so the home page's tiles need none."""
    from catalogue.models import SeasonalThemes

    found = SeasonalThemes.objects.filter(pk=1).values_list("on", flat=True).first()
    switched = True if found is None else found
    cache.set(KEY, switched, None)
    theme = theme_for(timezone.localdate())
    if switched and theme is not None and theme.key == "christmas":
        cache.set(GIFTS_KEY, stocked_bands(), None)
    return switched


def stocked_bands():
    """The GIFT_BANDS with at least one product in stock at a price inside them, as the list page counts a band
    (web.views.browse): one query."""
    from django.db.models import Count, Q

    from catalogue.models import Product

    wanted = {}
    for band, _ in GIFT_BANDS:
        low, _, high = band.partition("-")
        wanted[f"band_{low}"] = Count("pk", filter=Q(lowest_price__gte=low, lowest_price__lt=high))
    found = Product.objects.for_lists().in_stock().aggregate(**wanted)
    return [band for band, _ in GIFT_BANDS if found[f"band_{band.partition('-')[0]}"]]


def gift_bands():
    """[(band, wording key)] for the Gifts by budget tiles, from the cache alone: the budgets with something in
    stock, or every one when none has been kept yet (a staff preview out of season), so a tile is never missing
    for want of a count."""
    stocked = cache.get(GIFTS_KEY)
    return [row for row in GIFT_BANDS if stocked is None or row[0] in stocked]


def last_post(day=None):
    """(service, day) of Royal Mail's next last posting day for Christmas on or after ``day``, or None once the
    last has passed or in a year whose days are not in LAST_POST."""
    day = day or timezone.localdate()
    return next(((service, last) for service, last in LAST_POST.get(day.year, ()) if last >= day), None)


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
