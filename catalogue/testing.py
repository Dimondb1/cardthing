"""Small helpers for building test data."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Game, Listing, Product, ProductSet, Retailer


def make_game(name="Pokémon", slug="pokemon", **kwargs):
    kwargs.setdefault("search_aliases", "ptcg")
    return Game.objects.create(name=name, slug=slug, **kwargs)


def make_set(game, name="Prismatic Evolutions", slug="prismatic-evolutions", **kwargs):
    kwargs.setdefault("code", "PRE")
    return ProductSet.objects.create(game=game, name=name, slug=slug, **kwargs)


def make_product(product_set, name="Prismatic Evolutions Elite Trainer Box", **kwargs):
    kwargs.setdefault("product_type", Product.Type.ELITE_TRAINER_BOX)
    return Product.objects.create(
        game=product_set.game, product_set=product_set, name=name, **kwargs
    )


def make_retailer(name="Harbour Games", **kwargs):
    slug = kwargs.pop("slug", name.lower().replace(" ", "-").replace(".", ""))
    kwargs.setdefault("website", f"https://www.{slug}.example/")
    return Retailer.objects.create(name=name, slug=slug, **kwargs)


def make_listing(product, retailer, price="50.00", delivery="0.00", hours_ago=1, url=None, **kwargs):
    kwargs.setdefault("availability", Listing.Availability.IN_STOCK)
    return Listing.objects.create(
        product=product,
        retailer=retailer,
        url=url or f"{retailer.website}p/{product.slug}",
        price=Decimal(price),
        delivery_cost=Decimal(delivery),
        last_checked=timezone.now() - timedelta(hours=hours_ago),
        **kwargs,
    )


def inside_the_cache_window(test):
    """
    Act as if this process cleared the list caches a moment ago, for the rest of the test.

    An unforced clear (a save) is then skipped for 30 seconds, so a clear the test sees can only be
    a forced one.
    """
    import time
    from unittest import mock

    from django.test import override_settings

    overrides = override_settings(RIPRAPTOR_CACHE_CLEAR_SECONDS=30)
    overrides.enable()
    test.addCleanup(overrides.disable)
    patcher = mock.patch("catalogue.signals._last_clear", time.monotonic())
    patcher.start()
    test.addCleanup(patcher.stop)
