from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Game, Product, ProductSet
from .search import build_search_text


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
