from django.conf import settings


def site(request):
    return {
        "site_name": settings.RIPRAPTOR_SITE_NAME,
        "adsense_client": getattr(settings, "RIPRAPTOR_ADSENSE_CLIENT", ""),
        "awin_publisher_id": getattr(settings, "RIPRAPTOR_AWIN_PUBLISHER_ID", ""),
        "trending_days": settings.RIPRAPTOR_TRENDING_DAYS,
    }
