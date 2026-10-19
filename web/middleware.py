"""Count public page views by kind and day. Nothing about the visitor is kept.

One cookie is set, holding only "1", so a browser that comes back on another
day counts as a returning visitor. It carries no identifier.
"""

from django.http import HttpResponse

from catalogue.insights import RETURN_COOKIE, RETURN_COOKIE_DAYS, is_bot, record_view
from catalogue.models import DailyPageView

KINDS = {
    "home": DailyPageView.Kind.HOME,
    "product": DailyPageView.Kind.PRODUCT,
    "game": DailyPageView.Kind.GAME,
    "set": DailyPageView.Kind.SET,
    "search": DailyPageView.Kind.SEARCH,
    "deck": DailyPageView.Kind.SWIPE,
    "deals": DailyPageView.Kind.DEALS,
    "drops": DailyPageView.Kind.NEW,   # /new/ is now a redirect here; the kind keeps its name for history
    "watchlist": DailyPageView.Kind.WATCHLIST,
    "games": DailyPageView.Kind.OTHER,
    "about": DailyPageView.Kind.OTHER,
    "terms": DailyPageView.Kind.OTHER,
    "list_shop": DailyPageView.Kind.OTHER,
}
KEYS = {"product": "slug", "game": "game_slug", "set": "set_slug"}


class PageViewMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        if response.status_code == 200 and match and match.namespace == "web" and match.url_name in KINDS:
            key = match.kwargs.get(KEYS.get(match.url_name, ""), "")
            record_view(request, KINDS[match.url_name], key)
            if request.method == "GET" and not is_bot(request) and RETURN_COOKIE not in request.COOKIES:
                response.set_cookie(
                    RETURN_COOKIE, "1", max_age=RETURN_COOKIE_DAYS * 24 * 3600,
                    httponly=True, samesite="Lax", secure=request.is_secure(),
                )
        return response


# The refer a friend forms take an email address or a postal address: a body over this is refused before
# Django reads it, and before the CSRF check reads it either.
SMALL_FORM_PATHS = ("/refer/", "/r/")
SMALL_FORM_BYTES = 4096


class SmallFormMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.startswith(SMALL_FORM_PATHS):
            try:
                length = int(request.META.get("CONTENT_LENGTH") or 0)
            except ValueError:
                length = SMALL_FORM_BYTES + 1
            if length > SMALL_FORM_BYTES or request.META.get("HTTP_TRANSFER_ENCODING"):
                return HttpResponse(status=413)
        return self.get_response(request)


class OwnerEditsMiddleware:
    """The owner's changes leave the cached lists straight away.

    Visitors are served a cached list while the background jobs build it again (web.listcache), which can
    take from a few seconds to a few minutes. That suits price imports, not the owner hiding a product or
    fixing a price. A change the owner makes (any POST by a staff user, in Django Admin or on the site's
    owner pages) clears the lists hard: no visitor is served a copy built before it. The reader builds them
    on its next turn (a few seconds); a visitor who comes first builds them, or waits for the build already
    running, as for a missing list. The owner's own request does not build them, so a run of taps on Things
    to check stays quick.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("GET", "HEAD", "OPTIONS") or not getattr(request, "user", None) \
                or not request.user.is_staff:
            return self.get_response(request)
        from web import listcache

        editing = listcache.owner_editing.set(True)
        try:
            return self.get_response(request)
        finally:
            listcache.owner_editing.reset(editing)
