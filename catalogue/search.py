"""
Accent- and punctuation-insensitive product search that works on any database.

Each product stores ``search_text``: its name, set, set code, game, game
aliases and product type, normalised to plain lowercase words with a space at
each end. Every word the visitor types must match the start of a word in that
text, so "pokemon etb" finds "Pokémon ... Elite Trainer Box".
"""

import re
import unicodedata

from django.db.models import Case, IntegerField, Q, Value, When

from . import languages

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

# Bulk lots: a display of bundles, tins or ETBs, a case, ten of something. A broad search ("151") puts them after
# the everyday products a visitor is most likely after; one that names them ("151 bundle display", "etb case")
# ranks them as any other. Whole phrases, so "Booster Display" (a booster box) is not one, nor "Case of 36" (a
# box of 36 packs); a count needs its number, so "Phoenix Bundle" is not "x bundle". Plain regular expressions,
# read alike by SQLite and PostgreSQL.
BULK_PHRASES = ("bundle display", "tin display", "etb display", "elite trainer box display", "blister display")
BULK_COUNTS = (r"[0-9] ?x ?(booster box|bundle|etb|elite trainer box|tin)",
               r"\( ?[0-9]+ ?x? ?(bundles|booster boxes|boxes|tins|etbs|elite trainer boxes|blisters) ?\)")
BULK_WORDS = {"display", "displays", "case", "cases", "bulk", "wholesale"}


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
    if getattr(product, "language", ""):
        # So "japanese 151" finds a box whose name only says sv2a.
        parts.append(languages.name(product.language))
    parts.append(product.ean)
    return " " + normalise(" ".join(part for part in parts if part)) + " "


def terms_for(query):
    return normalise(query).split()[:MAX_TERMS]


def bulk_name():
    """True (as a WHERE test) for a product whose name reads as a bulk lot (BULK_PHRASES, BULK_COUNTS, a case)."""
    test = Q(name__icontains=" case") & ~Q(name__icontains=" case of")
    for phrase in BULK_PHRASES:
        test |= Q(name__icontains=phrase)
    for pattern in BULK_COUNTS:
        test |= Q(name__iregex=pattern)
    return test


def apply_search(queryset, query):
    """Filter to products matching every term. Returns (queryset, terms).

    Annotates ``name_rank`` (the words as typed lead the product's text, are in it, or are only scattered) and
    ``bulk`` (1 for a bulk lot when the search does not ask for one), for the Best match order."""
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
            ),
            bulk=Value(0) if BULK_WORDS & set(terms) else Case(When(bulk_name(), then=Value(1)), default=Value(0),
                                                                output_field=IntegerField()),
        )
    return queryset, terms
