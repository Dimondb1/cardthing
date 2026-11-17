"""
Links from a product page to the same product in another language, and to its nearest relatives when it is filed
under no set.

The audit of 17 Nov 2026 found the Japanese EB-05 booster box and pack with three to five shops each and almost no
links pointing at them: the site keeps languages apart on purpose, the Japanese pages were filed under no set, so
their only breadcrumb was the game, and they had no "More from this set" block. The owner approved both fixes
("Approve both").

A language version is the same kind of product (the same product type, size and wrapping) from the same set, in
another language. The set is known only by its code: two pages are versions of one product when their names, or the
sets they are filed under, give the same one code (matching.code_key). Pokémon's Japanese sets have codes of their
own, so its English and Japanese boxes are never linked: they are different sets with different cards. A page whose
name gives no code and whose set has none has no versions.
"""

import re

from django.db.models import Q

from catalogue import languages
from catalogue.matching import BOX_SIZES, GENERIC_WORDS, VARIANT_WORDS, code_key, naming_words, set_codes
from catalogue.models import Product

VERSIONS_SHOWN = 4
RELATIVES_SHOWN = 6
CANDIDATES = 300           # rows read to rank relatives, so one common word never reads a whole game
CODE_PARTS = re.compile(r"^([a-z]+)(\d+)(.*)$")


def name_and_code(product):
    """The product's name, with its set's code after it when the name gives no code of its own."""
    game = product.game.slug
    if not set_codes(product.name, game) and product.product_set_id and product.product_set.code:
        return f"{product.name} {product.product_set.code}"
    return product.name


def version_key(product):
    """(code and kind, sizes and wrappings) for a product that carries one set code, else None. Two products with
    the same key are one product, whatever their languages."""
    game = product.game.slug
    key = code_key(name_and_code(product), game)
    if not key:
        return None
    return key, frozenset(naming_words(product.name, game) & (VARIANT_WORDS | BOX_SIZES))


def spellings(code):
    """The ways shops write a code the matcher reads as ``code`` ("eb5": EB5, EB05, EB-05, EB 05), for finding
    names that may carry it. What a query finds is checked again with version_key."""
    parts = CODE_PARTS.match(code)
    if not parts:
        return [code]
    letters, number, rest = parts.groups()
    return [f"{letters}{gap}{zero}{number}{rest}" for gap in ("", "-", " ") for zero in ("", "0")]


def carrying(codes):
    """A filter for products whose name, or whose set's code, may give one of ``codes``."""
    found = Q()
    for code in codes:
        for spelling in spellings(code):
            found |= Q(name__icontains=spelling) | Q(product_set__code__iexact=spelling)
    return found


def language_versions(product):
    """[{"language", "name", "url"}] for the same product in other languages, one for each language, in the
    languages' names' order. One query, and none for a product that carries no set code."""
    key = version_key(product)
    if key is None:
        return []
    game = product.game.slug
    code = next(iter(set_codes(name_and_code(product), game)))
    others = (Product.objects.active().filter(game_id=product.game_id, product_type=product.product_type)
              .exclude(pk=product.pk).exclude(language=product.language).filter(carrying([code]))
              .select_related("game", "product_set").order_by("pk")[:CANDIDATES])
    found = {}
    for other in others:
        if other.language not in found and version_key(other) == key:
            found[other.language] = other
    versions = [{"language": languages.name(other.language), "name": other.name, "url": other.get_absolute_url()}
                for other in found.values()]
    return sorted(versions, key=lambda version: version["language"])[:VERSIONS_SHOWN]


def distinctive_words(product):
    """The words of a product's name that name it, longest first: not its kind, a number or a short word."""
    words = naming_words(product.name, product.game.slug) - GENERIC_WORDS
    return sorted((w for w in words if len(w) >= 4 and not w.isdigit()), key=lambda w: (-len(w), w))


def relatives(product, limit=RELATIVES_SHOWN):
    """The nearest relatives of a product filed under no set, best first: products of its game that share its set
    code or the words that name it, with its own language and kind ahead. Two queries, or none when its name has
    nothing to go by."""
    game = product.game.slug
    codes = set_codes(product.name, game)
    words = distinctive_words(product)[:3]
    if not codes and not words:
        return []
    near = carrying(codes)
    for word in words:
        near |= Q(name__icontains=word)
    rows = (Product.objects.active().filter(game_id=product.game_id).exclude(pk=product.pk).filter(near)
            .values_list("pk", "name", "product_type", "language").order_by("pk")[:CANDIDATES])
    mine = set(words)
    ranked = []
    for pk, name, kind, language in rows:
        shared = len(mine & naming_words(name, game))
        same_set = bool(codes & set_codes(name, game))
        if not shared and not same_set:
            continue   # the word was only part of another ("heroine" in "Heroines' Shelter" counts, "art" in "party" does not)
        ranked.append((-(3 * same_set + shared), language != product.language, kind != product.product_type, name, pk))
    ids = [pk for *_rank, pk in sorted(ranked)[:limit]]
    found = {other.pk: other for other in Product.objects.for_lists().filter(pk__in=ids)}
    return [found[pk] for pk in ids if pk in found]
