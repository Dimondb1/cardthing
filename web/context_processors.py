import re

from django.conf import settings

# Addresses that are private links: a message thread and the links in stock alert emails. analytics.js
# holds the same pattern.
PRIVATE_LINK = re.compile(r"^/(?:contact/c|alerts/(?:confirm|stop))/")


def site(request):
    return {
        "site_name": settings.RIPRAPTOR_SITE_NAME,
        "adsense_client": getattr(settings, "RIPRAPTOR_ADSENSE_CLIENT", ""),
        "awin_publisher_id": getattr(settings, "RIPRAPTOR_AWIN_PUBLISHER_ID", ""),
        "ga_id": getattr(settings, "RIPRAPTOR_GA_ID", ""),
        # No third-party script runs on these, even on a 404 for a mistyped one, and they pass on no address.
        "private_link": bool(PRIVATE_LINK.match(request.path)),
        "trending_days": settings.RIPRAPTOR_TRENDING_DAYS,
    }
