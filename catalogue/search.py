"""
Accent- and punctuation-insensitive product search that works on any database.

Each product stores ``search_text``: its name, set, set code, game, game
aliases and product type, normalised to plain lowercase words with a space at
each end. Every word the visitor types must match the start of a word in that
text, so "pokemon etb" finds "Pokémon ... Elite Trainer Box".
"""

import re
import unicodedata

from django.db.models import Case, IntegerField, Value, When

# Extra words people use for each product type.
TYPE_ALIASES = {
    "booster_box": "display",
    "elite_trainer_box": "etb",
    "bundle": "",
    "collection_box": "collection",
    "deck": "starter precon",
    "tin": "",
    "booster_pack": "pack",
    "gift_set": "gift",
    "other": "",
}

MAX_TERMS = 8


def normalise(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['’‘`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def compact(text):
    """'OP-10' -> 'op10', so codes match with or without punctuation."""
    return normalise(text).replace(" ", "")


def build_search_text(product):
    parts = [product.name]
    product_set = product.product_set
    if product_set is not None:
        parts += [product_set.name, product_set.code, compact(product_set.code)]
    game = product.game
    parts += [game.name, game.short_name, game.search_aliases.replace(",", " ")]
    parts += [product.get_product_type_display(), TYPE_ALIASES.get(product.product_type, "")]
    parts.append(product.ean)
    return " " + normalise(" ".join(part for part in parts if part)) + " "


def terms_for(query):
    return normalise(query).split()[:MAX_TERMS]


def apply_search(queryset, query):
    """Filter to products matching every term. Returns (queryset, terms)."""
    terms = terms_for(query)
    for term in terms:
        queryset = queryset.filter(search_text__contains=" " + term)
    if terms:
        phrase = " " + " ".join(terms)
        queryset = queryset.annotate(
            name_rank=Case(
                When(search_text__startswith=phrase, then=Value(0)),
                When(search_text__contains=phrase, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
    return queryset, terms
