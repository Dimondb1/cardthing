from django.conf import settings


def site(request):
    return {
        "site_name": settings.CARDSCOUT_SITE_NAME,
        "trending_days": settings.CARDSCOUT_TRENDING_DAYS,
    }
