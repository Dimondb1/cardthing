from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from catalogue.models import Game, Product, ProductSet


class ProductSitemap(Sitemap):
    changefreq = "hourly"
    priority = 0.8

    def items(self):
        return Product.objects.active().order_by("pk")

    def lastmod(self, product):
        return product.updated_at


class GameSitemap(Sitemap):
    changefreq = "daily"

    def items(self):
        return Game.objects.filter(is_active=True)


class SetSitemap(Sitemap):
    changefreq = "daily"

    def items(self):
        return ProductSet.objects.filter(game__is_active=True).select_related("game")


class PageSitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        return ["web:home", "web:deals", "web:new", "web:games", "web:about", "web:terms"]

    def location(self, name):
        return reverse(name)


SITEMAPS = {
    "products": ProductSitemap,
    "games": GameSitemap,
    "sets": SetSitemap,
    "pages": PageSitemap,
}
