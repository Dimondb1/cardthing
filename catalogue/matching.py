"""
Guess which of our products a shop product is, from its name.

Used when a shop gives no barcode and nobody has linked the product by hand.
The score is the share of words in our product's name that appear in the
shop's title, with a few checks that stop "Booster Box" matching a "Booster
Pack" or a case of six boxes.
"""

import re
from html import unescape as html_unescape

from . import languages
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
PHRASES = ("universes beyond", "trading card game", "card game", "dragon ball super", "dragon ball",
           "sealed tcg collection")
# Series prefixes shops add or drop, with any set number after them:
# "Scarlet & Violet 8 Surging Sparks", "SV8.5 Prismatic Evolutions", "SWSH Evolving Skies".
SERIES = re.compile(
    r"\b(?:scarlet (?:&|and) violet|sword (?:&|and) shield|sun (?:&|and) moon|black (?:&|and) white|"
    r"s ?& ?v|sv|swsh|sm|xy|bw|dp|hgss)\s*-?\s*\d{0,2}(?:\.\d|\s\d(?=\s|$))?\b",
    re.I,
)
# Counts and codes that describe the same product: "36 packs", "(10)", "OP-10", "BT-22", "ST13".
# "set of 4" stays: a set of four decks is not one deck.
COUNTS = re.compile(r"\b\d+ (?:sealed )?(?:booster )?packs?\b|\(\d+\)|\bx\s?\d+\b|"
                    r"\b(?:op|st|bt|ex|eb|lm|pb|fb)[- ]?\d{1,3}\b")
BOX_PACKS = re.compile(r"\b((?:booster )?display(?: box)?|booster box|elite trainer box) +(\d{1,2}) ?x (?:sealed )?(?:booster )?packs?\b")
SET_CODE = re.compile(r"^(?:sv|swsh|sm|xy|op|st|bt|ex|eb|lm|pb|b|fb)-?\d{1,3}(?:\.\d)?[a-z]?$")
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


# The game a shop writes in front of a product name, with whatever separator follows. Taken off a
# shop title before matching, so "Star Wars: Unlimited - A Lawless Time - Booster Box" is judged on
# "A Lawless Time Booster Box"; our names never carry the game, the product's game field does.
GAME_PREFIX = re.compile(
    r"^\s*(?:pok[eé]mon(?: tcg| trading card game)?|magic(?: the gathering|: the gathering)?|mtg|"
    r"one piece(?: card game| tcg)?|disney lorcana(?: tcg| trading card game)?|lorcana(?: tcg)?|"
    r"yu-?gi-?oh!*(?: tcg| trading card game)?|star wars:? unlimited|swu|flesh (?:and|&) blood(?: tcg)?|"
    r"digimon(?: card game| tcg)?|dragon ball super(?: card game| cg| fusion world)?(?: fusion world)?|dbs(?: cg)?|"
    r"riftbound:?(?: league of legends(?: tcg)?)?|league of legends(?: tcg)?|union arena|gundam(?: card game| tcg)?|"
    r"weiss schwarz|cardfight!*(?: vanguard)?|final fantasy tcg|ff ?tcg|topps|panini)\s*[:\-–|]*\s+",
    re.I,
)
SEPARATORS = re.compile(r"\s*[-–|]\s*|\s*:\s+")


def shop_title(title):
    """A shop title with the game prefix taken off and its separators collapsed to spaces."""
    text = html_unescape(title or "").strip()
    for _ in range(2):   # "Pokemon TCG: Pokemon - Scarlet & Violet ..." carries it twice
        text = GAME_PREFIX.sub("", text)
    return re.sub(r"\s+", " ", SEPARATORS.sub(" ", text)).strip(" :,-")


def key_text(text):
    """The name with every phrase shops add or drop at will taken out."""
    expanded = " " + expand(text) + " "
    expanded = re.sub(r"\b(\w+)( \1\b)+", r"\1", expanded)  # "booster booster box", a shop's slip
    for phrase in PHRASES:
        expanded = expanded.replace(f" {phrase} ", " ")
    expanded = SERIES.sub(" ", expanded)
    # "Booster Box (36x Packs)" says what is inside the box, the same rule classify uses: only the count
    # straight after the box, once. "3x Booster Packs" and "+ 6x Booster Packs" keep their counts and marks.
    expanded = BOX_PACKS.sub(lambda m: f"{m.group(1)} " if 6 <= int(m.group(2)) <= 36 else m.group(0), expanded, count=1)
    expanded = COUNTS.sub(" ", expanded)
    return expanded


def key_words(text):
    """The identifying words of a name: everything shops do not add or drop at will."""
    return {w for w in key_text(text).split() if w not in LOOSE and not SET_CODE.match(w)}


def covers(product_name, title):
    """Does our name account for every identifying word in the shop title?

    "Ashes of the Empire Carbonite Booster Pack" is not covered by "Ashes of
    the Empire Booster Pack": "carbonite" names a different product.
    """
    return key_words(title) <= key_words(product_name)


def match_key(text):
    """A sorted bag of the words that identify a product, for exact cross-shop matching.

    'Commander Legends: Battle for Baldur's Gate Bundle' and
    'Commander Legends Battle For Baldurs Gate Bundle' share a key; a booster
    box and a booster pack of the same set do not, because the kind of thing
    (box, pack, bundle, deck) is kept as a marker. Editions ("1st",
    "Unlimited") are kept because they are different products.
    """
    expanded = key_text(text)
    kept = sorted(key_words(text))
    kept += [mark for mark, pattern in KIND_MARKS if pattern.search(expanded)]
    return " ".join(kept)


def score(product_name, title, game=""):
    """0 to 100. 100 means every meaningful word of our name is in the title. ``game`` lets the language
    rules read set codes (catalogue/languages.py)."""
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
    # A title in another language is another product, whether it says so in words, a seller's code or
    # a set code only that language has ("151 sv2a" is Japanese, our "151" English).
    if not same_language(product_name, title, game):
        return min(value, SUGGEST - 1)
    for kind in TYPE_WORDS:
        if (kind in theirs) != (kind in ours_text):
            if kind == "display" and "booster box" in ours_text:
                continue
            value = min(value, SUGGEST + 10)
    return value


def same_language(product_name, title, game=""):
    """False when the shop's title is in a language our product's name is not, or states two. A title that
    names no language says nothing here: shops selling only Japanese boxes often leave it out."""
    if languages.mixed(title or "", game):
        return False
    theirs = languages.language_of(title or "", game)
    return not theirs or languages.same(languages.language_of(product_name or "", game), theirs)


def best_match(title, products, game=""):
    """(product, score) for the best of ``products`` (iterable of (pk, name)) or (None, 0)."""
    best, best_score = None, 0
    for pk, name in products:
        value = score(name, title, game)
        if value > best_score or (value == best_score and best is not None and len(name) > len(best[1])):
            best, best_score = (pk, name), value
    return best, best_score
