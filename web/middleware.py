"""Count public page views by kind and day. Nothing about the visitor is kept."""

from catalogue.insights import record_view
from catalogue.models import DailyPageView

KINDS = {
    "home": DailyPageView.Kind.HOME,
    "product": DailyPageView.Kind.PRODUCT,
    "game": DailyPageView.Kind.GAME,
    "set": DailyPageView.Kind.SET,
    "search": DailyPageView.Kind.SEARCH,
    "deck": DailyPageView.Kind.SWIPE,
    "games": DailyPageView.Kind.OTHER,
    "about": DailyPageView.Kind.OTHER,
    "terms": DailyPageView.Kind.OTHER,
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
        return response
