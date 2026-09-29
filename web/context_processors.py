from django.conf import settings


def site(request):
    return {
        "site_name": settings.RIPRAPTOR_SITE_NAME,
        "trending_days": settings.RIPRAPTOR_TRENDING_DAYS,
    }
