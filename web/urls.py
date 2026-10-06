from django.contrib.sitemaps.views import sitemap
from django.urls import path

from . import views
from .feeds import DealsFeed
from .sitemaps import SITEMAPS

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("api/search/", views.search_api, name="search_api"),
    path("api/deck/", views.deck_api, name="deck_api"),
    path("api/recent/", views.recent_api, name="recent_api"),
    path("swipe/", views.deck, name="deck"),
    path("deals/", views.deals, name="deals"),
    path("new/", views.latest_drops, name="new"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("games/", views.games, name="games"),
    path("games/<slug:game_slug>/", views.game_detail, name="game"),
    path("games/<slug:game_slug>/<slug:set_slug>/", views.set_detail, name="set"),
    path("products/<slug:slug>/", views.product_detail, name="product"),
    path("go/<int:listing_id>/", views.go, name="go"),
    path("about/", views.about, name="about"),
    path("terms/", views.terms, name="terms"),
    path("api/products/<slug:slug>/prices/", views.product_prices_api, name="product_prices_api"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("llms.txt", views.llms_txt, name="llms"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("feeds/deals.xml", DealsFeed(), name="feed_deals"),
    path("feeds/<slug:game_slug>.xml", DealsFeed(), name="feed_game"),
]
