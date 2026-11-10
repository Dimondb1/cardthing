"""
Search visibility: one Admin page that shows whether the pages built for search engines and AI assistants say what
they should, and whether IndexNow is telling Bing about them. Staff only, never for editors.

The owner, 10 Nov 2026, asked to run two commands on the server and paste what they print: "On mobile I can't copy
from console". So the page runs the same checks (web/proof.py, on this machine's own copy of the site, as each of five
crawlers) and reads the same IndexNow record (catalogue/indexnow.py), and a screenshot of it says what the console
would. Opening it builds each page once more, a few seconds on the live site; nothing is changed or sent.
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import indexnow
from guides.access import not_for_editors

from . import proof


def indexnow_status():
    """What the page says of IndexNow: whether it is on, whether the site serves the key file (asked of this
    machine's own copy: the search engines read it at the same address), and the last accepted run."""
    from django.test import Client
    from urllib.parse import urlsplit

    site = urlsplit(settings.RIPRAPTOR_SITE_URL)
    key = indexnow.key()
    response = Client(HTTP_HOST=site.netloc).get(f"/{key}.txt", secure=site.scheme == "https")
    return {
        "on": settings.RIPRAPTOR_INDEXNOW,
        "served": response.status_code == 200 and response.content.decode().strip() == key,
        "last": indexnow.status(),
        "pages_sent": indexnow.pages_sent(),
    }


@staff_member_required
@not_for_editors
@require_GET
def visibility_page(request):
    site = settings.RIPRAPTOR_SITE_URL.rstrip("/")
    rows = proof.run(proof.inside(site), site, proof.a_set())
    failed = sum(1 for row in rows if not row.passed)
    response = render(request, "admin/visibility.html", {
        **admin.site.each_context(request),
        "title": "Search visibility",
        "site": site,
        "checks": len(rows),
        "failed": failed,
        "pages": proof.by_page(rows),
        "crawlers": list(proof.CRAWLERS),
        "indexnow": indexnow_status(),
        "now": timezone.now(),
    })
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
