from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Game, Listing, Product, ProductSet
from .search import build_search_text

HOME_CACHE_KEY = "web:home-lists:v2"


@receiver([post_save, post_delete], sender=Product)
@receiver([post_save, post_delete], sender=Listing)
def clear_home_lists(sender, **kwargs):
    cache.delete(HOME_CACHE_KEY)


def _refresh(products):
    changed = []
    for product in products.select_related("game", "product_set"):
        text = build_search_text(product)
        if text != product.search_text:
            product.search_text = text
            changed.append(product)
    Product.objects.bulk_update(changed, ["search_text"], batch_size=500)


@receiver(post_save, sender=Game)
def game_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _refresh(Product.objects.filter(game=instance))


@receiver(post_save, sender=ProductSet)
def set_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _refresh(Product.objects.filter(product_set=instance))
