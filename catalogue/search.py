"""
Accent- and punctuation-insensitive product search that works on any database.

Each product stores ``search_text``: its name, set, set code, game, game
aliases and product type, normalised to plain lowercase words with a space at
each end. Every word the visitor types must match the start of a word in that
text, so "pokemon etb" finds "Pokémon ... Elite Trainer Box".
"""

import bisect
import difflib
import re
import unicodedata

from django.core.cache import cache
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

    Annotates ``name_rank`` (the words as typed lead the product's text, are in it, or are only scattered, then
    how few of them are whole words) and ``bulk`` (1 for a bulk lot when the search does not ask for one), for the
    Best match order."""
    terms = terms_for(query)
    for term in terms:
        queryset = queryset.filter(search_text__contains=" " + term)
    if terms:
        phrase = " " + " ".join(terms)
        # Where the words sit (as typed and whole, as typed, or scattered), then how many are whole words, so
        # "pokemon set 3 etb" leads with Set 3 and not Set 30, whose words only start with a 3.
        place = Case(
            When(search_text__startswith=phrase + " ", then=Value(0)),
            When(search_text__contains=phrase + " ", then=Value(1)),
            When(search_text__startswith=phrase, then=Value(2)),
            When(search_text__contains=phrase, then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
        part_words = sum(
            (Case(When(search_text__contains=f" {term} ", then=Value(0)), default=Value(1), output_field=IntegerField())
             for term in terms),
            Value(0),
        )
        queryset = queryset.annotate(
            name_rank=place * (MAX_TERMS + 1) + part_words,
            bulk=Value(0) if BULK_WORDS & set(terms) else Case(When(bulk_name(), then=Value(1)), default=Value(0),
                                                                output_field=IntegerField()),
        )
    return queryset, terms


# Understanding a search as game, set and type (the owner, 9 Nov 2026: "We should search game (pokemon) - sets
# (delta reign) - the type"). The results page leads with where the products live, as links, and the box's
# suggestions offer a set as it is typed. Built from the words alone and a cached index of the sets, so neither
# adds a query.

SET_INDEX_KEY = "search:set-index:v1"
SET_INDEX_SECONDS = 600   # also dropped when a set or game is saved (catalogue/signals.py)
SUGGESTED_SETS = 2        # sets the box suggests above its products
# Words that say nothing about which set: left out when matching a set's name.
FILLER = {"tcg", "ccg", "trading", "card", "game", "the", "of", "and", "uk", "sealed", "english", "japanese",
          "korean", "chinese", "new"}
# Short names the type tiles use that are not the type's own name or aliases.
TYPE_SHORT = {"collector_booster_box": "collector box", "collector_booster_pack": "collector pack"}


def singular(word):
    """A search word in the singular, so "boxes", "ETBs" and "Sparks" match "box", "ETB" and "Spark"."""
    if len(word) > 3 and word.endswith("xes"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words_of(text):
    return frozenset(singular(word) for word in normalise(text).split())


def type_names():
    """{type code: [sets of words that name it]}: its name, the words people use for it and its tile's name."""
    from .models import Product

    names = {}
    for code, label in Product.Type.choices:
        if code == Product.Type.OTHER:
            continue
        names[code] = [words_of(label)] + [words_of(word) for word in TYPE_ALIASES.get(code, "").split()]
        if code in TYPE_SHORT:
            names[code].append(words_of(TYPE_SHORT[code]))
    return names


def named_type(words):
    """(type code, the words that named it) for the type ``words`` name most fully, or ("", empty) when none does or
    two do equally."""
    named = {}
    for code, names in type_names().items():
        fits = [name for name in names if name <= words]
        if fits:
            named[code] = max(fits, key=len)
    if not named:
        return "", frozenset()
    most = max(len(name) for name in named.values())
    codes = [code for code, name in named.items() if len(name) == most]
    return (codes[0], named[codes[0]]) if len(codes) == 1 else ("", frozenset())


def set_index():
    """Every active game and every set with products, with the words that name them. Cached."""
    from django.core.cache import cache
    from django.db.models import Count

    from .models import Game, ProductSet

    index = cache.get(SET_INDEX_KEY)
    if index is not None:
        return index
    games = {}
    for game in Game.objects.filter(is_active=True):
        names = [words_of(game.name), words_of(game.short_name)]
        names += [words_of(alias) for alias in game.search_aliases.split(",")]
        names = [name for name in names if name]
        games[game.pk] = {"pk": game.pk, "name": game.name, "short": game.display_short, "slug": game.slug,
                          "url": game.get_absolute_url(), "names": names, "words": frozenset().union(*names)}
    sets = []
    rows = (ProductSet.objects.filter(game_id__in=games)
            .annotate(n=Count("products", filter=Q(products__is_active=True))).filter(n__gt=0))
    for row in rows:
        sets.append({"pk": row.pk, "game": row.game_id, "game_short": games[row.game_id]["short"], "name": row.name,
                     "slug": row.slug,
                     "url": f"{games[row.game_id]['url']}{row.slug}/", "products": row.n,
                     "release": row.release_date.isoformat() if row.release_date else "",
                     "words": words_of(f"{row.name} {row.code} {compact(row.code)}")})
    index = {"games": games, "sets": sets}
    cache.set(SET_INDEX_KEY, index, SET_INDEX_SECONDS)
    return index


def understand(query):
    """What a search names: {"game": a game, "set": a set, "type": a type code}, each None or "" when it names none.

    A game when every word of its name, short name or an alias is in the search; a type as named_type; a set when
    the words left over (not the game's, the type's or FILLER) are all words of exactly one set's name or code, of
    the named game if there is one. "pokemon delta reign etb": Pokémon, Delta Reign, ETBs.
    "pokemon set 3": Set 3, never Set 30."""
    words = {singular(term) for term in terms_for(query)}
    found = {"game": None, "set": None, "type": ""}
    if not words:
        return found
    index = set_index()
    kind, kind_words = named_type(words)
    found["type"] = kind
    leftover = words - kind_words - FILLER
    for game in index["games"].values():
        named = [name for name in game["names"] if name <= words]
        if named:
            found["game"] = game
            leftover -= frozenset().union(*named)
            break
    if leftover:
        game = found["game"]
        sets = [entry for entry in index["sets"]
                if leftover <= entry["words"] and (game is None or entry["game"] == game["pk"])]
        if len(sets) == 1:
            found["set"] = sets[0]
            found["game"] = index["games"][sets[0]["game"]]
    return found


def suggest_sets(query, limit=SUGGESTED_SETS):
    """(sets, type) for the search box as it is typed: the sets whose name the words name, the last word perhaps half
    typed ("pokemon delta re"), the newest first, with the type the words name. A set only once a word of its own
    name is in, three letters at least when half typed, so "pokemon" alone is not every Pokémon set."""
    terms = terms_for(query)
    if not terms:
        return [], ""
    whole = {singular(term) for term in terms[:-1]}
    last = terms[-1]
    kind, kind_words = named_type(whole | {singular(last)})
    index = set_index()
    found = []
    for entry in index["sets"]:
        game_words = index["games"][entry["game"]]["words"]
        known = entry["words"] | game_words | kind_words | FILLER
        if not whole <= known:
            continue
        own = entry["words"] - game_words
        last_own = len(last) >= 3 and any(word.startswith(last) for word in own)
        if not (last_own or singular(last) in known or any(word.startswith(last) for word in known)):
            continue
        if whole & own or last_own:
            found.append((singular(last) in entry["words"], entry))
    # The set whose name has the last word whole first ("set 3" is Set 3 before Set 30), then the newest.
    found.sort(key=lambda pair: (pair[0], pair[1]["release"], pair[1]["name"]), reverse=True)
    return [entry for _whole, entry in found[:limit]], kind


# Words a search did not find. The owner, 14 Nov 2026: spoken "delta reign" came back from the phone as "delta rain"
# and found nothing, "people will give up after that". When a search finds nothing, each word that is not a word of
# any product on sale is swapped for the product word it most likely meant: one that sounds the same (rain, reign),
# else one spelt nearly the same (obsidan, obsidian; boosters, booster), and that finds something with the other
# words. The page says so, with a link to search exactly what was typed. Free and instant: no AI.

FIX_MIN_LETTERS = 4        # a shorter word is often still being typed
FIX_TRIES = 6              # words tried for each unknown word, best first, each a quick look for any product
FIX_CLOSE = 0.75           # how alike two spellings must be (difflib's ratio) to be offered
VOCABULARY_KEY = "search:vocabulary"
VOCABULARY_SECONDS = 600

SPELLINGS = (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("q", "k"), ("x", "ks"), ("z", "s"), ("dg", "j"))
VOWEL_SOUNDS = (("eigh", "A"), ("ai", "A"), ("ay", "A"), ("ei", "A"), ("ey", "A"), ("ee", "E"), ("ea", "E"),
                ("ie", "E"), ("oo", "U"), ("ou", "U"), ("ue", "U"), ("ew", "U"), ("oa", "O"), ("ow", "O"))


def sounds(word):
    """How a word sounds, roughly, as (its consonants after the first letter, the whole with its vowel sounds):
    "reign" and "rain" are ("rn", "rAn") alike, "lorkana" and "lorcana" ("lrkn", "lorkana"). ("", "") for a word
    with digits or other marks, which is a code or a number and never guessed at."""
    word = word.lower()
    if not word.isalpha() or not word.isascii():
        return "", ""
    for spelt, said in SPELLINGS:
        word = word.replace(spelt, said)
    if word[:2] in ("kn", "gn", "wr"):
        word = word[1:]
    word = re.sub(r"(?<=.)gh", "", word)                  # night, eight
    word = re.sub(r"(?<=[aeiou])g(?=n)", "", word)        # reign, sign, design
    word = re.sub(r"c(?=[eiy])", "s", word).replace("c", "k")
    for spelt, said in VOWEL_SOUNDS:
        word = word.replace(spelt, said)
    word = re.sub(r"(.)\1+", r"\1", word)
    if not word:
        return "", ""
    first = "a" if word[0] in "aeiouyAEUO" else word[0]
    return first + re.sub(r"[aeiouyAEUO]", "", word[1:]), word


def vocabulary():
    """(every word of a product on sale, sorted; {sounds()[0]: [those words]}), kept VOCABULARY_SECONDS in the
    shared cache. One query when it is built."""
    from .models import Product

    found = cache.get(VOCABULARY_KEY)
    if found is None:
        words = set()
        for text in Product.objects.active().values_list("search_text", flat=True).iterator():
            words.update(text.split())
        alike = {}
        for word in words:
            key = sounds(word)[0]
            if key:
                alike.setdefault(key, []).append(word)
        found = (tuple(sorted(words)), alike)
        cache.set(VOCABULARY_KEY, found, VOCABULARY_SECONDS)
    return found


def is_word(term, words):
    """Whether ``term`` is a whole word of some product (``words`` sorted)."""
    at = bisect.bisect_left(words, term)
    return at < len(words) and words[at] == term


def guesses(term, words, alike):
    """Product words ``term`` may have meant, best first: those that sound the same, then the closest spellings."""
    said, voiced = sounds(term)
    pool = set(alike.get(said, ())) | set(difflib.get_close_matches(term, words, n=FIX_TRIES, cutoff=FIX_CLOSE))
    pool.discard(term)

    def rank(word):
        return (sounds(word)[1] != voiced, -difflib.SequenceMatcher(None, term, word).ratio(), abs(len(word) - len(term)),
                word)

    return sorted(pool, key=rank)[:FIX_TRIES]


def corrected(query, queryset):
    """The search ``query`` with each word that is not a word of any product swapped for the one it most likely
    meant and that finds a product in ``queryset`` with the search's other words; "" when there is nothing to swap
    or no swap finds anything. Only for a search that found nothing: a few quick queries, one per word tried."""
    terms = terms_for(query)
    if not terms:
        return ""
    words, alike = vocabulary()
    unknown = [n for n, term in enumerate(terms)
               if len(term) >= FIX_MIN_LETTERS and sounds(term)[0] and not is_word(term, words)]
    if not unknown:
        return ""
    known = [term for n, term in enumerate(terms) if n not in unknown]
    fixed = list(terms)
    for n in unknown:
        for word in guesses(terms[n], words, alike):
            found, _ = apply_search(queryset, " ".join(known + [word]))
            if found.exists():
                fixed[n] = word
                break
    if fixed == terms:
        return ""
    found, _ = apply_search(queryset, " ".join(fixed))
    return " ".join(fixed) if found.exists() else ""
