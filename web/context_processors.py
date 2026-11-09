import re

from django.conf import settings

from catalogue import referrals
from guides import state as guides

from . import look, navigation, refresh, seasons

# Addresses that are private links: a message thread, the links in stock alert and refer a friend emails (and the
# Your alerts page they and the watchlist page link to), and the owner's dashboard. analytics.js holds the same pattern.
PRIVATE_LINK = re.compile(r"^/(?:contact/c|alerts/(?:confirm|stop|manage)|refer/(?:confirm|claim|address|stop)|r|dashboard)/")
# Pages the refer a friend card never interrupts: the offer's own pages, the swipe page, forms someone is filling in
# and admin. Error pages leave it off in their templates.
# Product pages too: on a phone the card would sit over the buy box, and the buy box comes first.
NO_REFER_CARD = re.compile(r"^/(?:refer|r|products|swipe|admin|alerts|drops/remind|contact|list-your-shop|dashboard)/")

GUIDE_PAGES = ("guides", "guide", "guide_compare", "guide_checklist", "fair_price")


def site(request):
    advertised = referrals.advertised()
    private = bool(PRIVATE_LINK.match(request.path))
    return {
        "site_name": settings.RIPRAPTOR_SITE_NAME,
        # On every script tag: only scripts carrying it run (ripraptor/csp.py).
        "csp_nonce": getattr(request, "csp_nonce", ""),
        "adsense_client": getattr(settings, "RIPRAPTOR_ADSENSE_CLIENT", ""),
        "awin_publisher_id": getattr(settings, "RIPRAPTOR_AWIN_PUBLISHER_ID", ""),
        # Amazon links carry our tag: Amazon's required statement goes on every page and beside its buttons.
        "amazon_associate": bool(settings.RIPRAPTOR_AMAZON_PARTNER_TAG),
        "ga_id": getattr(settings, "RIPRAPTOR_GA_ID", ""),
        "google_verification": settings.RIPRAPTOR_GOOGLE_VERIFICATION,
        "bing_verification": settings.RIPRAPTOR_BING_VERIFICATION,
        # No third-party script runs on these, even on a 404 for a mistyped one, and they pass on no address.
        "private_link": private,
        "trending_days": settings.RIPRAPTOR_TRENDING_DAYS,
        # The Refer a friend link, read from the cache alone so no page gains a query.
        "refer_advertised": advertised,
        # The card that now and then invites a visitor to refer a friend (web/static/js/refer-card.js decides when).
        "refer_card": advertised and not private and not NO_REFER_CARD.match(request.path),
        # The Guides link, once a guide is published: read from the cache alone, like the refer link.
        "guides_live": guides.current()["live"],
        "guide_pages": GUIDE_PAGES,
        # Today's seasonal theme (web/seasons.py), from the cache alone; staff can preview one with ?theme=.
        "season": seasons.for_request(request),
        # Find with Rip in the header (web/find.py) while the new home page or the compact layout is on (web/look.py,
        # from the cache alone).
        "find_link": look.mode(request) in (look.FEED, look.COMPACT),
        # The refresh dots under the logo (web/refresh.py): the lists' next rebuild, from the clock alone; a product
        # page puts its own shops' next read in their place.
        "refresh": refresh.for_lists(),
        # The header's Games menu on a desktop screen (web/navigation.py), from the cache alone.
        "nav_games": navigation.games(),
    }
