from django.contrib.sitemaps.views import sitemap
from django.urls import path

from . import dashboard, refer_views, views
from .feeds import DealsFeed
from .sitemaps import SITEMAPS

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("api/search/", views.search_api, name="search_api"),
    path("api/deck/", views.deck_api, name="deck_api"),
    path("api/recent/", views.recent_api, name="recent_api"),
    path("api/picks/", views.picks_api, name="picks_api"),
    path("api/note/", views.note_api, name="note_api"),
    path("swipe/", views.deck, name="deck"),
    path("deals/", views.deals, name="deals"),
    path("new/", views.latest_drops, name="new"),
    path("drops/", views.drops, name="drops"),
    path("drops/remind/<int:set_id>/", views.drop_ask, name="drop_ask"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("watchlist/alert/", views.search_alert_ask, name="search_alert_ask"),
    path("watchlist/manage/", views.manage_ask, name="manage_ask"),
    # Under /alerts/confirm/, /alerts/stop/ and /alerts/manage/, so they are private links like the stock
    # alerts' (no third-party script, kept out of search engines by robots.txt).
    path("alerts/manage/<str:token>/", views.alerts_manage, name="alerts_manage"),
    path("alerts/confirm/drop/<str:token>/", views.drop_confirm, name="drop_confirm"),
    path("alerts/stop/drop/<str:token>/", views.drop_stop, name="drop_stop"),
    path("alerts/confirm/search/<str:token>/", views.search_alert_confirm, name="search_alert_confirm"),
    path("alerts/stop/search/<str:token>/", views.search_alert_stop, name="search_alert_stop"),
    path("alerts/<slug:slug>/", views.alert_ask, name="alert_ask"),
    path("alerts/confirm/<str:token>/", views.alert_confirm, name="alert_confirm"),
    path("alerts/stop/<str:token>/", views.alert_stop, name="alert_stop"),
    path("refer/", refer_views.refer, name="refer"),
    # Private links from the refer a friend emails, like the alerts' (no third-party script, not indexed).
    path("refer/confirm/<str:token>/", refer_views.refer_confirm, name="refer_confirm"),
    path("refer/claim/<str:token>/", refer_views.refer_claim, name="refer_claim"),
    path("refer/address/<str:token>/", refer_views.refer_address, name="refer_address"),
    path("refer/stop/<str:token>/", refer_views.refer_stop, name="refer_stop"),
    path("r/<str:code>/", refer_views.refer_friend, name="refer_friend"),
    path("contact/", views.contact, name="contact"),
    path("contact/c/<str:token>/", views.contact_thread, name="contact_thread"),
    path("list-your-shop/", views.list_shop, name="list_shop"),
    path("list-your-shop/thanks/", views.list_shop_thanks, name="list_shop_thanks"),
    path("games/", views.games, name="games"),
    path("games/<slug:game_slug>/", views.game_detail, name="game"),
    path("games/<slug:game_slug>/<slug:set_slug>/", views.set_detail, name="set"),
    path("products/<slug:slug>/", views.product_detail, name="product"),
    # The Report link's form on a product page posts here; it sends the visitor back to the product.
    path("products/<slug:slug>/report/", views.report_listing, name="report_listing"),
    path("go/<int:listing_id>/", views.go, name="go"),
    path("go/search/<slug:slug>/<str:market>/", views.go_search, name="go_search"),
    path("about/", views.about, name="about"),
    # The owner's dashboard: staff only, a private page like the alert links (no third-party script, not indexed).
    path("dashboard/", dashboard.dashboard, name="dashboard"),
    path("terms/", views.terms, name="terms"),
    path("api/products/<slug:slug>/prices/", views.product_prices_api, name="product_prices_api"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("llms.txt", views.llms_txt, name="llms"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("feeds/deals.xml", DealsFeed(), name="feed_deals"),
    path("feeds/<slug:game_slug>.xml", DealsFeed(), name="feed_game"),
]
