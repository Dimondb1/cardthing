"""
Guess which of our products a shop product is, from its name.

Used when a shop gives no barcode and nobody has linked the product by hand.
The score is the share of words in our product's name that appear in the
shop's title, with a few checks that stop "Booster Box" matching a "Booster
Pack" or a case of six boxes.
"""

from .search import normalise

# Words in our product names that carry no meaning for matching.
STOP = {"the", "and", "of", "a", "tcg", "trading", "card", "game", "pokemon", "pokémon", "magic",
        "gathering", "mtg", "disney", "lorcana", "one", "piece", "en", "english"}

# If the shop title has one of these and our product name does not, it is a different thing.
DIFFERENT = {"case", "sleeves", "single", "singles", "playmat", "binder", "deck box", "x2", "x3", "x4",
             "x6", "japanese", "korean", "chinese", "pack of 2", "pack of 3", "pack of 6", "empty", "damaged"}

# Product types that must both appear or both not appear.
TYPE_WORDS = ("elite trainer box", "booster box", "booster bundle", "bundle", "booster pack", "collection",
              "tin", "deck", "gift set", "display")

AUTO_LINK = 100
SUGGEST = 60


ABBREVIATIONS = {" etb ": " elite trainer box ", " bb ": " booster box ", " upc ": " ultra premium collection "}


def expand(text):
    text = " " + normalise(text) + " "
    for short, full in ABBREVIATIONS.items():
        text = text.replace(short, full)
    return text.strip()


def words(text):
    return [w for w in expand(text).split() if w not in STOP]


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
