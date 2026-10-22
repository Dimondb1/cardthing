import re

from django.conf import settings

from catalogue import referrals

# Addresses that are private links: a message thread, the links in stock alert and refer a friend emails (and the
# Your alerts page they and the watchlist page link to), and the owner's dashboard. analytics.js holds the same pattern.
PRIVATE_LINK = re.compile(r"^/(?:contact/c|alerts/(?:confirm|stop|manage)|refer/(?:confirm|claim|address|stop)|r|dashboard)/")
# Pages the refer a friend card never interrupts: the offer's own pages, the swipe page, forms someone is filling in
# and admin. Error pages leave it off in their templates.
# Product pages too: on a phone the card would sit over the buy box, and the buy box comes first.
NO_REFER_CARD = re.compile(r"^/(?:refer|r|products|swipe|admin|alerts|drops/remind|contact|list-your-shop|dashboard)/")


def site(request):
    advertised = referrals.advertised()
    private = bool(PRIVATE_LINK.match(request.path))
    return {
        "site_name": settings.RIPRAPTOR_SITE_NAME,
        "adsense_client": getattr(settings, "RIPRAPTOR_ADSENSE_CLIENT", ""),
        "awin_publisher_id": getattr(settings, "RIPRAPTOR_AWIN_PUBLISHER_ID", ""),
        # Amazon links carry our tag: Amazon's required statement goes on every page and beside its buttons.
        "amazon_associate": bool(settings.RIPRAPTOR_AMAZON_PARTNER_TAG),
        "ga_id": getattr(settings, "RIPRAPTOR_GA_ID", ""),
        # No third-party script runs on these, even on a 404 for a mistyped one, and they pass on no address.
        "private_link": private,
        "trending_days": settings.RIPRAPTOR_TRENDING_DAYS,
        # The Refer a friend link, read from the cache alone so no page gains a query.
        "refer_advertised": advertised,
        # The card that now and then invites a visitor to refer a friend (web/static/js/refer-card.js decides when).
        "refer_card": advertised and not private and not NO_REFER_CARD.match(request.path),
    }
