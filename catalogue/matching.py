"""
Guess which of our products a shop product is, from its name.

Used when a shop gives no barcode and nobody has linked the product by hand.
The score is the share of words in our product's name that appear in the
shop's title, with a few checks that stop "Booster Box" matching a "Booster
Pack" or a case of six boxes.
"""

import re
from functools import lru_cache
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


# Remembered: one eBay run scores each of up to 200,000 titles against hundreds of our product names, and
# cleaning the same name again each time was nearly all of its work. Both depend only on the text.
@lru_cache(maxsize=65536)
def expand(text):
    text = " " + normalise(text) + " "
    for short, full in ABBREVIATIONS.items():
        text = text.replace(short, full)
    return text.strip()


@lru_cache(maxsize=65536)
def _words(text):
    return tuple(w for w in expand(text).split() if w not in STOP)


def words(text):
    return list(_words(text))


# Words shops add or drop at will: two names that differ only by these are one product. "Vol" goes and
# its number stays ("Gem Pack Vol. 2" and "Gem 2"). "Simplified" goes too: whether a box is Simplified
# Chinese is the product's language, which every use of the key checks apart, and shops write "Chinese"
# for the same box.
LOOSE = STOP | {"edition", "reprint", "reprinted", "sealed", "new", "official", "version", "ver", "release",
                "structure", "starter", "theme", "preconstructed", "precon", "box", "boxes", "set", "sets",
                "pack", "packs", "yugioh", "yu", "gi", "oh", "digimon", "riftbound",
                "star", "wars", "flesh", "blood", "tm", "vol", "volume", "simplified",
                # The game's name as shops shorten it: "Dragon Ball Super CG", "DBS", "SWU".
                "cg", "dbs", "swu"}
# The game or franchise in words, which shops put in front of a name, inside it or nowhere: "Riftbound:
# League of Legends TCG Origins", "Star Wars: Unlimited Spark of Rebellion", "Dungeons & Dragons:
# Adventures in the Forgotten Realms", "Dragon Ball Super Fusion World FB05".
PHRASES = ("universes beyond", "trading card game", "card game", "dragon ball super", "dragon ball", "union arena",
           "sealed tcg collection", "league of legends", "star wars unlimited", "dungeons and dragons", "fusion world",
           "weiss schwarz", "cardfight vanguard")
# Words that name a language. Every use of a key checks the language apart (duplicate groups are made per
# language, the importer compares the title's language with the product's), and shops put the word anywhere
# or leave it out: "Japanese Pokemon Mega Dream Booster Box" and "Mega Dream ex Japanese Booster Box".
LANGUAGE_WORDS = re.compile(r"\b(?:japanese|chinese|korean|traditional|thai|indonesian|german|french|italian|spanish|"
                            r"portuguese|russian|english|eng|jpn|jp|chn|chs|cht)\b")
# Series prefixes shops add or drop, with any set number after them:
# "Scarlet & Violet 8 Surging Sparks", "SV8.5 Prismatic Evolutions", "SWSH Evolving Skies". Each becomes one
# code ("sv8", "sv8.5", "swsh"), which a name keeps only when nothing else in it names the product.
SERIES = re.compile(
    r"\b(scarlet and violet|sword and shield|sun and moon|black and white|s and v|sv|swsh|sm|xy|bw|dp|hgss)"
    r"\s*-?\s*(\d{0,2})(?:\.(\d)|\s(\d)(?=\s|$))?\b",
    re.I,
)
SERIES_CODE = {"scarlet and violet": "sv", "s and v": "sv", "sword and shield": "swsh", "sun and moon": "sm",
               "black and white": "bw"}
# Counts that describe the same product: "36 packs", "x 10". "set of 4" stays: a set of four decks is not one
# deck. Union Arena's codes too ("UE02BT", "UE18ST", Japanese "UA18BT"), but not an EX set's ("UEX01BT"),
# which shops sometimes write instead of "Vol. 2" and must never meet the first volume.
# A number after "vol", "volume", "series", "set" or "gem" names the product ("Gem Pack Vol 6 Booster Pack"),
# so it is never taken for a count.
COUNTS = re.compile(r"(?<!\bvol )(?<!\bvolume )(?<!\bseries )(?<!\bset )(?<!\bgem )"
                    r"\b\d+ (?:sealed )?(?:booster )?packs?\b|\bx\s?\d+\b|\bu[ae]\d{1,2} ?(?:bt|st)\b")
# Set codes, one spelling each: "OP-09", "OP 09" and "OP09" are "op9", "PRB-01" and "PRB01" are "prb1",
# "FS06" is "fs6". Like the series, a name keeps its code only when nothing else names the product
# ("OP-09 Booster Box"), so "OP-09 Emperors in the New World" meets "Emperors in the New World".
SET_CODES = re.compile(r"\b((?:op|eb|prb)[ck]?|st|bt|ex|lm|pb|fb|fs|sd)[- ]?(\d{1,3})\b")
# Pokémon's own codes: English Mega Evolution sets (ME01, ME02.5) and Japanese sets (M1L, M2a, S6a, S12a).
# Pokémon only: Magic's "M20" and "M21" are two core sets whose names differ by nothing else.
POKEMON_CODES = re.compile(r"\b(?:me ?(0\d)|(me|m|s)(\d{1,2}))(?: (5)(?= |$))?([a-z]{0,2})\b")
# Pokémon's Mega Evolution series, written in front of its sets' names or left out.
MEGA_EVOLUTION = re.compile(r"\bmega evolutions?\b")
# What a booster box is called at many shops: "Booster Display", "Booster Display Box", "Boosters Display Box",
# "Booster Box Display". Not "Tin Display", "Bundle Display" or "Elite Trainer Box Display", which hold tins,
# bundles or boxes.
BOOSTER_DISPLAY = re.compile(r"\bboosters? (?:box )?display(?: box)?\b")
# A bare count straight after the box is what a full box holds ("Booster Box (36)", "Booster Display (24)").
# Not 18: "Booster Box (18)" is a half box, or an Enhanced one.
BOX_COUNT = re.compile(r"\b(booster box)(?: of)? (\d{1,2})(?= |$)")
FULL_BOX = {24, 30, 36}
# Unwrapped boxes are their own product, however a shop says it.
NO_SHRINK = re.compile(r"\b(?:no (?:shrink ?wrap|shrink|plastic wrap|plastic|wrap)|unwrapped)\b")
# Numbers shops write in words or numerals: "Rarity Collection II", "Three Pack Blister".
NUMBER_WORDS = ((re.compile(r"\bii\b"), "2"), (re.compile(r"\biii\b"), "3"),
                (re.compile(r"\btwo(?= (?:pack|booster|player|deck))"), "2"),
                (re.compile(r"\bthree(?= (?:pack|booster|player|deck))"), "3"))
# Kinds of Pokémon product that a set's name says "ex" in and shops leave out: "Shiny Treasure ex Booster
# Box" and "Shiny Treasure Booster Box". A collection, tin, deck or gift is named after its Pokémon, and
# "Charizard ex" is not "Charizard".
POKEMON_BOOSTER_KIND = re.compile(r"\bbooster\b|\bbundle\b|\bdisplay\b")
POKEMON_NAMED_KIND = re.compile(r"\bcollection\b|\btrove\b|\btins?\b|\bdecks?\b|\bgift\b|\belite trainer\b|\bbattle\b")
# Simplified Chinese Pokémon codes, read on normalised text ("csv9.5C" is "csv9 5c"), with the language
# letter shops glue on, leave apart or leave out ("csv7C", "csv7 C", "CBB2 C"): one spelling each, so two
# shops' titles for one box share a key. The code stays, because one name can cover two boxes that only
# the code tells apart ("Storming Emergence csm1b" and "csm1c"); only the letter C goes.
CHINESE_CODE = re.compile(
    r"\b(csv(?:10|[1-9])(?: 5)?|csm[12](?: 5|[abc])|cs[1-6](?: 5|[ab])|cbb[1-6]|csvl[12]|csvm2a|csvh5|cs[eqp]1)"
    r"(?: ?c)?\b")
# "151C" and "30th C" are codes many products share, so only the number is kept: it is in the name already.
SHARED_CHINESE_CODE = re.compile(r"\b(30th|151) ?c\b")
CHINESE_CODE_WORD = re.compile(
    r"^(?:csv(?:10|[1-9])(?:\.5)?|csm[12](?:\.5|[abc])|cs[1-6](?:\.5|[ab])|cbb[1-6]|csvl[12]|csvm2a|csvh5|cs[eqp]1)$")
BOX_PACKS = re.compile(r"\b((?:booster )?display(?: box)?|booster box|elite trainer box) +(\d{1,2}) ?x (?:sealed )?(?:booster )?packs?\b")
# A word of a key that is a set or series code, as key_text spells them.
SET_CODE = re.compile(r"^(?:(?:sv|swsh|sm|xy|bw|dp|hgss)(?:\d{1,2}(?:\.5)?)?|(?:sv|swsh|sm|xy)\d{1,3}[a-z]{1,2}|"
                      r"(?:(?:op|eb|prb)[ck]?|st|bt|ex|lm|pb|b|fb|fs|sd)\d{1,3}[a-z]?)$")
POKEMON_SET_CODE = re.compile(r"^(?:me(?:\d{1,2}(?:\.5)?)?|m\d{1,2}[a-z]{0,2}|s\d{1,2}[a-z]{0,2})$")
POKEMON = "pokemon"
# Words that say what kind of thing a product is, or describe it, without naming which one. A name made of
# these and a code is named by its code: "PRB-01 Premium Booster Box" is not "PRB-02 Premium Booster Box".
KIND_WORDS = {"elite", "trainer", "box", "center", "booster", "bundle", "display", "pack", "packs", "case", "tin",
              "collection"}
GENERIC_WORDS = KIND_WORDS | {
    "deck", "blister", "sleeved", "checklane", "premium", "extra", "best", "special", "deluxe", "mini", "ultra",
    "super", "double", "triple", "start", "build", "battle", "enhanced", "expansion", "high", "class", "jumbo",
    "slim", "standard", "half", "story", "prerelease", "pre", "kit", "gift", "exclusive", "limited", "tournament",
    "noshrink", "japan", "korea", "china", "ex", "base"}
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


def _series_code(match):
    name = match.group(1).lower()
    code = SERIES_CODE.get(name, name)
    number = match.group(2)
    if not number:
        return f" {code} "
    point = match.group(3) or match.group(4)
    return f" {code}{int(number)}{'.' + point if point else ''} "


def _set_code(match):
    return f" {match.group(1)}{int(match.group(2))} "


def _pokemon_code(match):
    family, number = ("me", match.group(1)) if match.group(1) else (match.group(2), match.group(3))
    point = f".{match.group(4)}" if match.group(4) else ""
    return f" {family}{int(number)}{point}{match.group(5)} "


def _spaced(text):
    return " " + " ".join(text.split()) + " "


@lru_cache(maxsize=65536)
def key_text(text, game=""):
    """The name with every phrase shops add or drop at will taken out, and every code spelt one way. ``game``
    (the game's slug) adds the rules only one game's names need."""
    expanded = " " + expand(text) + " "
    expanded = re.sub(r"\b(\w+)( \1\b)+", r"\1", expanded)  # "booster booster box", a shop's slip
    for phrase in PHRASES:
        expanded = expanded.replace(f" {phrase} ", " ")
    expanded = LANGUAGE_WORDS.sub(" ", expanded)
    for pattern, number in NUMBER_WORDS:
        expanded = pattern.sub(number, expanded)
    expanded = BOOSTER_DISPLAY.sub("booster box", expanded)
    expanded = NO_SHRINK.sub("noshrink", expanded)
    if game == POKEMON:
        # A Japanese High Class box is the set's only box, and the Booster Bundle the set's only bundle:
        # shops say so or not ("Mega Dream ex High Class Booster Box", "Journey Together Bundle").
        expanded = re.sub(r"\bhigh class\b", " ", expanded)
        expanded = re.sub(r"\bbooster bundle\b", "bundle", expanded)
        expanded = re.sub(r"\bpc (?=elite trainer box)", "center ", expanded)
        expanded = MEGA_EVOLUTION.sub(" me ", expanded)
        # Shops' names for the Simplified Chinese Gem Pack boxes: "Gem Series 4", "Gem Pack Vol. 4", "Gem 4".
        expanded = re.sub(r"\bgem series (?=\d)", "gem ", expanded)
    expanded = SERIES.sub(_series_code, _spaced(expanded))
    # Codes before counts: "PRB 02 Booster Pack" is the second Premium Booster, and "(csv9.5) Booster Pack" a
    # pack of csv9.5, not five packs. Pokémon's "ex" is a kind of card ("Charizard ex"), never a code.
    expanded = SET_CODES.sub(lambda m: m.group(0) if game == POKEMON and m.group(1) == "ex" else _set_code(m), expanded)
    if game == POKEMON:
        expanded = POKEMON_CODES.sub(_pokemon_code, _spaced(expanded))
    expanded = CHINESE_CODE.sub(lambda m: m.group(1).replace(" ", "."), _spaced(expanded))
    expanded = SHARED_CHINESE_CODE.sub(r"\1", expanded)
    # "Booster Box (36x Packs)" says what is inside the box, the same rule classify uses: only the count
    # straight after the box, once. "3x Booster Packs" and "+ 6x Booster Packs" keep their counts and marks.
    expanded = BOX_PACKS.sub(lambda m: f"{m.group(1)} " if 6 <= int(m.group(2)) <= 36 else m.group(0), _spaced(expanded),
                             count=1)
    expanded = _spaced(COUNTS.sub(" ", expanded))
    expanded = BOX_COUNT.sub(lambda m: m.group(1) if int(m.group(2)) in FULL_BOX else m.group(0), expanded)
    if game == POKEMON:
        # "Gem Pack Vol. 4 CBB4 C": the code says the volume again, and other shops leave it out.
        gem = re.search(r"\bcbb([1-6])\b", expanded)
        if gem and re.search(r"\bgem\b", expanded) and re.search(rf"(?<![a-z.]){gem.group(1)}\b", expanded):
            expanded = expanded.replace(f" cbb{gem.group(1)} ", " ")
        if POKEMON_BOOSTER_KIND.search(expanded) and not POKEMON_NAMED_KIND.search(expanded):
            expanded = re.sub(r"\bex\b", " ", expanded)
    return _spaced(expanded)


# What a box holds, straight after the box ("Booster Box 36 Packs", "Display (24)", "Elite Trainer Box 9 x Packs"):
# the same box written two ways, not two products.
BOX_CONTENTS = re.compile(r"\b(?:booster box|display(?: box)?|elite trainer box|case)(?: of)? \d{1,3}(?: ?x)?"
                          r"(?: (?:sealed )?(?:booster )?packs?)?\b")
CODES = re.compile(r"\b(?:(?:op|eb|prb)[ck]?|st|bt|ex|lm|pb|fb|fs|sd|b|sv|swsh|sm|xy|me|m|s)[- ]?\d{1,3}(?: 5)?[a-z]{0,2}\b"
                   r"|\bu[ae]\d{1,2} ?(?:bt|st)\b")
# A box's packs in brackets, as a shop writes what the box holds: "Booster Box M2A High Class Packs (10 Booster
# Packs)". Only in a box's name: a bundle's "(5 Packs)" says which bundle it is.
BRACKETED_PACKS = re.compile(r"\(\s*\d{1,2}\s*[x×]?\s*(?:sealed\s+)?(?:booster\s+)?packs?\s*\)", re.I)
BOX_NAME = re.compile(r"\bbooster\s+box\b|\bdisplay\b|\belite\s+trainer\s+box\b|\betb\b", re.I)


@lru_cache(maxsize=65536)
def identity_numbers(name):
    """The numbers that say which product a name is: a volume, a set number, how many packs a multi-pack
    holds. Not a series or set code, nor what a box holds. Two names with different ones are two products,
    even when every word matches ("Gem Pack Vol 4" and "Vol 6", "5 Sealed Booster Packs" and "10")."""
    if BOX_NAME.search(name or ""):
        name = BRACKETED_PACKS.sub(" ", name)
    text = " " + expand(name) + " "
    for pattern, number in NUMBER_WORDS:
        text = pattern.sub(number, text)
    text = SERIES.sub(" ", text)
    text = CHINESE_CODE.sub(" ", text)
    text = SHARED_CHINESE_CODE.sub(r" \1 ", text)
    text = CODES.sub(" ", text)
    text = BOX_CONTENTS.sub(" ", text)
    return frozenset(re.findall(r"\d+", text))


def chinese_code(word):
    """Whether a word of a key is a Simplified Chinese set code, as ``key_text`` spells them ("csv9.5")."""
    return bool(CHINESE_CODE_WORD.match(word))


# Words whose last "s" is not a plural.
# "Evolutions" is a Pokémon set, "Mega Evolution" a series.
NOT_PLURAL = {"series", "species", "always", "news", "lens", "plus", "bonus", "versus", "chaos", "atlas", "evolutions"}


def singular(word):
    """One spelling for a word and its plural: "decks" and "deck", "horrors" and "horror", "rarities" and
    "rarity". Codes and numbers are left as they are ("m1s" is a set)."""
    if len(word) <= 3 or not word.isalpha() or not word.endswith("s") or word in NOT_PLURAL:
        return word
    if word.endswith(("ss", "us", "is", "os")):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    return word[:-1]


def is_code(word, game=""):
    """Whether a word of a key is a set or series code ("op9", "sv8.5", "prb1", Pokémon's "m2a")."""
    return bool(SET_CODE.match(word) or (game == POKEMON and POKEMON_SET_CODE.match(word)))


@lru_cache(maxsize=65536)
def _key_words(text, game=""):
    words = {singular(w) for w in key_text(text, game).split()}
    words = {w for w in words if w not in LOOSE}
    codes = {w for w in words if is_code(w, game)}
    # The code goes when something else names the product, and stays when nothing does: "OP-09 Booster Box"
    # and "OP09 Booster Box" share a key, which "OP-10 Booster Box" does not.
    if any(w not in GENERIC_WORDS for w in words - codes):
        return frozenset(words - codes)
    return frozenset(words)


def key_words(text, game=""):
    """The identifying words of a name: everything shops do not add or drop at will."""
    return set(_key_words(text, game))


def set_codes(text, game=""):
    """The set codes a name carries, one spelling each, without a bare series ("sv8", "op9", "m2a", "csv7"):
    two names carrying different ones are two products."""
    words = {singular(w) for w in key_text(text, game).split()}
    return frozenset(w for w in words if (is_code(w, game) and re.search(r"\d", w)) or chinese_code(w))


# Pokémon's Japanese codes belong to a series as the English ones do: "s12a" is Sword & Shield's, "m2a" Mega
# Evolution's.
SERIES_FAMILY = {"s": "swsh", "m": "me"}
SERIES_NAMES = {"sv", "swsh", "sm", "xy", "bw", "dp", "hgss", "me"}


def series_of(text, game=""):
    """The series a name's codes and series words name ("sv", "swsh", "me"), as a set."""
    found = set()
    for word in key_text(text, game).split():
        if not is_code(word, game):
            continue
        family = re.match(r"[a-z]+", word).group(0)
        if game == POKEMON:
            family = SERIES_FAMILY.get(family, family)
        if family in SERIES_NAMES:
            found.add(family)
    return found


def codes_differ(a, b, game=""):
    """True when both names carry a set code and the codes are not the same, or name two series ("Scarlet &
    Violet Base Set" is not "Mega Evolution Base Set")."""
    first, second = set_codes(a, game), set_codes(b, game)
    if first and second and first != second:
        return True
    first, second = series_of(a, game), series_of(b, game)
    return bool(first and second and not first & second)


def names_something(key):
    """Whether a key holds a word that names a product, beyond the kind of product it is."""
    return any(w not in KIND_WORDS and not w.startswith("#") for w in key.split())


def covers(product_name, title, game=""):
    """Does our name account for every identifying word in the shop title?

    "Ashes of the Empire Carbonite Booster Pack" is not covered by "Ashes of
    the Empire Booster Pack": "carbonite" names a different product.
    """
    return key_words(title, game) <= key_words(product_name, game)


def match_key(text, game=""):
    """A sorted bag of the words that identify a product, for exact cross-shop matching.

    'Commander Legends: Battle for Baldur's Gate Bundle' and
    'Commander Legends Battle For Baldurs Gate Bundle' share a key; a booster
    box and a booster pack of the same set do not, because the kind of thing
    (box, pack, bundle, deck) is kept as a marker. Editions ("1st",
    "Unlimited") are kept because they are different products. ``game`` (the
    game's slug) adds the rules only one game's names need, such as Pokémon's
    set codes.
    """
    expanded = key_text(text, game)
    kept = sorted(_key_words(text, game))
    kept += [mark for mark, pattern in KIND_MARKS if pattern.search(expanded)]
    return " ".join(kept)


# A Magic booster's kind: a set booster box is not a draft booster box, though "set" is a word shops drop.
# Read on the plain name, so "Collector's Booster" and "Collectors English Booster" are Collector Boosters.
BOOSTER_KIND = re.compile(r"\b(set|draft|play|collector|jumpstart|gift|prerelease)s? booster")


def booster_kinds(name):
    return set(BOOSTER_KIND.findall(" ".join(LANGUAGE_WORDS.sub(" ", expand(name)).split())))


def same_product(a, b, game=""):
    """Whether two names that share a key may be taken for one product without a person: their numbers
    agree ("Vol 4" is not "Vol 6"), and so do their set codes and booster kinds."""
    return (identity_numbers(a) == identity_numbers(b) and not codes_differ(a, b, game)
            and booster_kinds(a) == booster_kinds(b))


# Sizes of one set's box: a slim box and a jumbo box of one code are two products.
BOX_SIZES = {"slim", "jumbo", "standard", "mini", "half"}
# Words that make a product a variant of another with the same name: an unwrapped box, a case of boxes.
VARIANT_WORDS = {"noshrink", "case", "half", "damaged", "mini"}


def code_key(name, game=""):
    """A name carrying one set code: the code with the box size and the kind of product ("sv8a #box",
    "csv10 slim #box"), else "". A code names one set, so two shops' names for one box share it however
    else they word it ("Eeveelutions Terastal Festival ex sv8a", "Pursue Glory Together csv10" and "Chasing
    Glory Together (csv10)")."""
    codes = set_codes(name, game)
    if len(codes) != 1:
        return ""
    text = key_text(name, game)
    marks = [mark for mark, pattern in KIND_MARKS if pattern.search(text)]
    return " ".join([*codes, *sorted(BOX_SIZES & set(text.split())), *marks])


def naming_words(name, game=""):
    """The words of a key that name the product, without its codes."""
    words = {singular(w) for w in key_text(name, game).split()} - LOOSE
    return {w for w in words if not is_code(w, game) and not chinese_code(w)}


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
