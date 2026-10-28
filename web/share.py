"""The words a share fills in for the visitor: the text under a Reddit post's title (product page and cards)."""

from catalogue.discord import when_words
from catalogue.models import Listing
from content import service as copy


def reddit_text(cheapest, store=None):
    """The text under a shared Reddit post's title, so it reads as a post and not a bare link: the cheapest
    price now and when it was checked, as the page shows them, or that no shop has it. Like the Discord posts
    it never names the shop: the page compares every one. The visitor can change it before posting."""
    if cheapest is None:
        return copy.get("product.share.reddit_text_none", store=store)
    price = copy.get("product.share.reddit_price" if cheapest.delivery_known else "product.share.reddit_price_plus",
                     store=store, price=f"£{cheapest.shown_price:.2f}")
    key = ("product.share.reddit_text_preorder" if cheapest.availability == Listing.Availability.PREORDER
           else "product.share.reddit_text")
    return copy.get(key, store=store, price=price, when=when_words(cheapest.last_checked))
