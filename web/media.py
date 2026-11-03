"""
Uploaded pictures (settings.MEDIA_ROOT: partner logos, product and guide images), served by Django.

Caddy sends every request to Django and static files come from WhiteNoise, but nothing served this folder outside
development, so an image uploaded in Admin was saved and then did not show. This serves the folder's files, and only
those: a missing file, a folder and any path out of the folder answer 404. Each name is new (Django adds a suffix
to a name already taken), so a picture is kept by the browser for a week.
"""

from django.conf import settings
from django.views.static import serve

CACHE_SECONDS = 7 * 24 * 3600


def serve_media(request, path):
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return response
