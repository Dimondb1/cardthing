"""
Guess which of our products a shop product is, from its name.

Used when a shop gives no barcode and nobody has linked the product by hand.
The score is the share of words in our product's name that appear in the
shop's title, with a few checks that stop "Booster Box" matching a "Booster
Pack" or a case of six boxes.
"""

import re

from .search import normalise

# Words in our product names that carry no meaning for matching.
STOP = {"the", "and", "of", "a", "tcg", "trading", "card", "game", "pokemon", "pokémon", "magic",
        "gathering", "mtg", "disney", "lorcana", "one", "piece", "en", "english"}

# If the shop title has one of these and our product name does not, it is a different thing.
DIFFERENT = {"case", "sleeves", "single", "singles", "playmat", "binder", "deck box", "x2", "x3", "x4",
             "x6", "japanese", "korean", "chinese", "traditional", "simplified", "thai", "german", "french",
             "italian", "spanish", "portuguese", "pack of 2", "pack of 3", "pack of 6", "empty", "damaged",
             "collectors edition", "collector's edition", "extended art", "borderless", "foil", "promo"}

# Product types that must both appear or both not appear.
TYPE_WORDS = ("elite trainer box", "booster box", "booster bundle", "bundle", "booster pack", "collection",
              "tin", "deck", "gift set", "display")

AUTO_LINK = 100
REVERSE_LINK = 70   # share of the shop title's own words that must be in our name for an automatic link
SUGGEST = 60


ABBREVIATIONS = {" etb ": " elite trainer box ", " bb ": " booster box ", " upc ": " ultra premium collection "}


def expand(text):
    text = " " + normalise(text) + " "
    for short, full in ABBREVIATIONS.items():
        text = text.replace(short, full)
    return text.strip()


def words(text):
    return [w for w in expand(text).split() if w not in STOP]


# Words shops add or drop at will: two names that differ only by these are one product.
LOOSE = STOP | {"edition", "reprint", "reprinted", "sealed", "new", "official", "version", "ver", "release",
                "structure", "starter", "theme", "preconstructed", "precon", "box", "boxes", "set", "sets",
                "pack", "packs", "yugioh", "yu", "gi", "oh", "digimon", "riftbound",
                "star", "wars", "flesh", "blood", "tm"}
PHRASES = ("universes beyond", "trading card game", "card game", "dragon ball super", "dragon ball")
KIND_MARKS = (
    ("#box", re.compile(r"\bbooster box\b|\bdisplay\b|\bbox of \d+|\bcase of \d+")),
    ("#pack", re.compile(r"\bbooster\b(?! box| bundle| display)|\bblister\b|\bchecklane\b|\bsleeved\b")),
    ("#bundle", re.compile(r"\bbundle\b")),
    ("#etb", re.compile(r"\belite trainer\b")),
    ("#collection", re.compile(r"\bcollection\b|\btrove\b")),
    ("#tin", re.compile(r"\btins?\b")),
    ("#deck", re.compile(r"\bdecks?\b|\bprecon\b")),
    ("#gift", re.compile(r"\bgift\b")),
)


def match_key(text):
    """A sorted bag of the words that identify a product, for exact cross-shop matching.

    'Commander Legends: Battle for Baldur's Gate Bundle' and
    'Commander Legends Battle For Baldurs Gate Bundle' share a key; a booster
    box and a booster pack of the same set do not, because the kind of thing
    (box, pack, bundle, deck) is kept as a marker. Editions ("1st",
    "Unlimited") are kept because they are different products.
    """
    expanded = expand(text)
    for phrase in PHRASES:
        expanded = expanded.replace(phrase, " ")
    kept = sorted({w for w in expanded.split() if w not in LOOSE})
    kept += [mark for mark, pattern in KIND_MARKS if pattern.search(expanded)]
    return " ".join(kept)


def score(product_name, title):
    """0 to 100. 100 means every meaningful word of our name is in the title."""
    ours = words(product_name)
    if not ours:
        return 0
    theirs = expand(title)
    their_words = set(theirs.split())
    hit = sum(1 for w in ours if w in their_words)
    value = int(hit / len(ours) * 100)
    ours_text = expand(product_name)
    for word in DIFFERENT:
        if word in theirs and word not in ours_text:
            return min(value, SUGGEST - 1)
    for kind in TYPE_WORDS:
        if (kind in theirs) != (kind in ours_text):
            if kind == "display" and "booster box" in ours_text:
                continue
            value = min(value, SUGGEST + 10)
    return value


def best_match(title, products):
    """(product, score) for the best of ``products`` (iterable of (pk, name)) or (None, 0)."""
    best, best_score = None, 0
    for pk, name in products:
        value = score(name, title)
        if value > best_score or (value == best_score and best is not None and len(name) > len(best[1])):
            best, best_score = (pk, name), value
    return best, best_score
