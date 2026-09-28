from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("api/search/", views.search_api, name="search_api"),
    path("api/deck/", views.deck_api, name="deck_api"),
    path("swipe/", views.deck, name="deck"),
    path("games/", views.games, name="games"),
    path("games/<slug:game_slug>/", views.game_detail, name="game"),
    path("games/<slug:game_slug>/<slug:set_slug>/", views.set_detail, name="set"),
    path("products/<slug:slug>/", views.product_detail, name="product"),
    path("go/<int:listing_id>/", views.go, name="go"),
    path("about/", views.about, name="about"),
    path("terms/", views.terms, name="terms"),
    path("api/products/<slug:slug>/prices/", views.product_prices_api, name="product_prices_api"),
    path("robots.txt", views.robots_txt, name="robots"),
]
