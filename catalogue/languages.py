"""
The language a product or a shop's title is in, read from what it says. One place for the rules, used
by the language filter, product names, matching, duplicates, sets, eBay results, the autopilot and
Claude.

A title is English unless something in it says otherwise. What it states comes first:

1. a language word, in English or in the language itself ("Japanese", "Deutsch", "Japonais"), or a
   kind of product named in another language ("Top-Trainer-Box", "Coffret Dresseur d'Elite");
2. a seller's short code where it cannot be an ordinary word: "[JP]", "(DE)", "- KR" at the end, "JP
   Version", and codes that are never words ("JPN", "CHS"). Read loosely (shop and eBay titles), an
   upper-case "JP" or "KR" standing alone counts too;
3. a script: kana is Japanese, Hangul Korean, Thai Thai, Cyrillic Russian, Chinese characters alone
   Chinese.

Then what it implies, for Pokémon: a set code with a letter after the number (sv2a, s12a, SM12a, M2a),
a Simplified Chinese code (CSV9C, CBB3C, Gem Pack), or a Japanese-only kind of product or set name
(High Class Pack, Terastal Festival). Korean and Traditional Chinese reuse the Japanese codes, so these
mean Japanese only when nothing states another language; a letter after the code names the language
("s7D F" Traditional Chinese, "SV8A-T" Thai). For One Piece, the Chinese and Korean codes (OPC-01,
OPK-01).

"English", "Asian English" or "(EN)" stated in the title outweighs what a code or set name implies.
A bare code English shares (SV3, SV8, SM6, OP-09) says nothing.
"""

import re
import unicodedata
from functools import lru_cache

ENGLISH = ""
JAPANESE, CHINESE, KOREAN = "ja", "zh", "ko"
SIMPLIFIED, TRADITIONAL = "zh-hans", "zh-hant"

# Every language the site tells apart, and its name.
NAMES = {
    "ja": "Japanese", "zh": "Chinese", "zh-hans": "Simplified Chinese", "zh-hant": "Traditional Chinese",
    "ko": "Korean", "th": "Thai", "id": "Indonesian", "de": "German", "fr": "French", "it": "Italian",
    "es": "Spanish", "pt": "Portuguese", "ru": "Russian", "pl": "Polish", "nl": "Dutch",
}
CHINESE_FAMILY = ("zh", "zh-hans", "zh-hant")

# The public filter's choices: Japanese, Chinese and Korean apart, every other language together.
FILTER = [("en", "English"), ("ja", "Japanese"), ("zh", "Chinese"), ("ko", "Korean"), ("other", "Other languages")]
OTHER = [code for code in NAMES if code not in ("ja", "ko", *CHINESE_FAMILY)]


def plain(text):
    """Lower case without accents, so "Français" and "Francais" read alike."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _phrases(table):
    return [(re.compile(rf"(?<![\w-]){pattern}(?![\w-])"), code) for pattern, code in table]


# Language words and foreign kinds of product, matched on plain text.
WORDS = _phrases([
    (r"simplified chinese", SIMPLIFIED), (r"traditional chinese", TRADITIONAL), (r"s-chinese", SIMPLIFIED),
    (r"t-chinese", TRADITIONAL), (r"japanese", "ja"), (r"japanisch", "ja"), (r"japonais", "ja"),
    (r"giapponese", "ja"), (r"japones", "ja"), (r"korean", "ko"), (r"chinese", "zh"), (r"thai", "th"),
    (r"indonesian", "id"), (r"german", "de"), (r"deutsch", "de"), (r"deutsche", "de"), (r"french", "fr"),
    (r"francais", "fr"), (r"italian", "it"), (r"italiano", "it"), (r"spanish", "es"), (r"espanol", "es"),
    (r"castellano", "es"), (r"portuguese", "pt"), (r"portugues", "pt"), (r"russian", "ru"), (r"dutch", "nl"),
    (r"top-trainer-box", "de"), (r"sammelkartenspiel", "de"), (r"coffret dresseur d'elite", "fr"),
    (r"set allenatore fuoriclasse", "it"), (r"caja de entrenador elite", "es"), (r"treinador avancado", "pt"),
])
# Stated English: outweighs what a code or set name implies, never a stated language.
ENGLISH_WORDS = re.compile(r"(?<![\w-])(english|englisch|anglais|inglese|ingles)(?![\w-])|[(\[]\s*(?:en|eng)\s*[)\]]")

# Sellers' codes that are never ordinary words or product codes, as whole tokens.
SAFE_CODES = {"jpn": "ja", "chs": SIMPLIFIED, "cht": TRADITIONAL, "chn": "zh"}
# Short codes that are also words or parts of names ("Wreck It Ralph", "Cruella De Vil", "Kor"), so
# they count only in brackets, as the last word after a dash, or before "version" or "edition".
SHORT_CODES = {"jp": "ja", "jap": "ja", "kr": "ko", "cn": "zh", "sc": SIMPLIFIED, "tc": TRADITIONAL, "de": "de", "ger": "de", "fr": "fr", "vf": "fr",
               "it": "it", "ita": "it", "es": "es", "esp": "es", "pt": "pt", "ru": "ru", "rus": "ru"}
_SHORT = "|".join(sorted(SHORT_CODES, key=len, reverse=True))
_SAFE = "|".join(sorted(SAFE_CODES, key=len, reverse=True))
SAFE_CODE = re.compile(rf"(?<![\w-])({_SAFE})(?![\w-])")
BRACKETED = re.compile(rf"[(\[]\s*({_SHORT}|{_SAFE})\s*[)\]]")
TRAILING = re.compile(rf"\s[-–|/]\s*({_SHORT}|{_SAFE})\s*$")
VERSION = re.compile(rf"(?<![\w-])({_SHORT})\.?\s+(?:ver(?:sion)?|edition)(?![\w])")
# Read loosely, an upper-case seller's code standing on its own: "151 JP Booster Box", "KR 151". KOR
# too, unless the whole title is in capitals, where it may be Magic's Kor.
LOOSE = re.compile(r"(?<![\w-])(JP|JPN|KR|KOR|CN|CHN)(?![\w-])")

SCRIPTS = [
    (re.compile(r"[぀-ヿｦ-ﾟ]"), "ja"),          # hiragana, katakana, half-width katakana
    (re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]"), "ko"),  # Hangul
    (re.compile(r"[฀-๿]"), "th"),                      # Thai
    (re.compile(r"[Ѐ-ӿ]"), "ru"),                      # Cyrillic
    (re.compile(r"[㐀-鿿]"), "zh"),                      # Chinese characters with no kana
]

# Pokémon set codes that exist only outside English: a letter (or a plus) after the number. Japanese
# writes them in lower case (sv2a), shops often in upper case. English numbers its sets SV01 to SV10,
# SV3.5 and so on, so a bare sv3 or sv8 says nothing, and neither do SM6 to SM12, which English uses too.
POKEMON_CODE = re.compile(
    r"(?<![\w.])("
    r"sv(?:1[sva]|2[pda]|[3-9]a|4[km]|5[km]|10a|11[bw])"
    r"|m(?:1[ls]|\d{1,2}a)"
    r"|s(?:1[wha]|[2-6]a|5[ir]|6[hk]|7[rd]|8[ab]|9a|10[pdab]|11a|12a)"
    r"|sm(?:[1-5]\+|1[sm]|2[kl]|3[hn]|4[sa]|5[sm]|(?:[6-9]|1[01])[ab]|12a)"
    r")(?:[\s-]?([fti]))?(?![\w+])",
    re.I,
)
# The letter some shops add after such a code for the edition: F Traditional Chinese, T Thai, I Indonesian.
CODE_LETTER = {"f": TRADITIONAL, "t": "th", "i": "id"}
# Simplified Chinese codes, which never reuse the Japanese ones: CSV9C, CS4aC, CSM2C, CBB3C, Gem Pack.
CHINESE_CODE = re.compile(r"(?<![\w.])(?:csv\d+(?:\.5)?|csm\d+(?:\.5)?[abc]?|cs\d+(?:\.5)?[ab]?)\s?c(?![\w])"
                          r"|(?<![\w.])cbb\d+(?:\s?c)?(?![\w])|\bgem pack\b")
# Kinds of product sold only in Japan.
JAPANESE_KINDS = re.compile(
    r"\b(high class pack|enhanced expansion pack|strength expansion pack|strengthening expansion pack|"
    r"deck build box|special deck set|starter deck (?:&|and) build set|start deck 100|jumbo pack set|"
    r"gym promo pack)\b")
# Japanese-only set names: no English set has the name. Black Bolt, White Flare, 151, Forbidden Light,
# Shining Legends, Pokémon GO, Scarlet ex and Violet ex are left out, because English titles use them.
JAPANESE_SETS = re.compile(
    r"\b(shiny treasure ex|terastal festival|vstar universe|vmax climax|clay burst|snow hazard|"
    r"ruler of the black flame|raging surf|ancient roar|future flash|wild force|cyber judge|mask of change|"
    r"crimson haze|night wanderer|stellar miracle|paradise dragona|super electric breaker|battle partners|"
    r"glory of team rocket|heat wave arena|hot air arena|mega brave|mega symphonia|inferno x|mega dream ex|"
    r"eevee heroes|shiny star v|triplet beat|lost abyss|paradigm trigger|incandescent arcana|dark phantasma|"
    r"star birth|time gazer|space juggler|battle region|blue sky stream|fusion arts|jet black spirit|"
    r"jet-black geist|silver lance|skyscraping perfection|matchless fighters|single strike master|"
    r"rapid strike master|legendary heartbeat|amazing volt tackle|explosive walker|rebellion crash|"
    r"infinity zone|nihil zero)\b")
# One Piece's Chinese and Korean set codes.
ONE_PIECE_CODE = re.compile(r"(?<![\w])(?:op|eb|prb)([ck])-?\d{1,2}(?![\w])")


@lru_cache(maxsize=65536)
def stated(text, loose=False):
    """The language ``text`` states in words, a seller's code or a script, or "". ``loose`` also counts
    an upper-case code standing alone, as sellers write it; product names are read strictly."""
    if not text:
        return ENGLISH
    low = plain(text)
    found = [(match.start(), code) for pattern, code in WORDS for match in pattern.finditer(low)]
    if found:
        return min(found)[1]
    for pattern in (SAFE_CODE, BRACKETED, TRAILING, VERSION):
        match = pattern.search(low)
        if match:
            token = match.group(1)
            return SAFE_CODES.get(token) or SHORT_CODES[token]
    if loose:
        for match in LOOSE.finditer(text):
            token = match.group(1).lower()
            if token == "kor" and text.upper() == text:
                continue
            return SAFE_CODES.get(token) or SHORT_CODES.get(token) or KOREAN
    for pattern, code in SCRIPTS:
        if pattern.search(text):
            return code
    return ENGLISH


@lru_cache(maxsize=65536)
def implied(text, game=""):
    """The language a Pokémon or One Piece code, kind or set name implies, or "". ``game`` is the game's
    slug when known: these rules hold only inside their game."""
    low = plain(text)
    if game in ("", "pokemon"):
        if CHINESE_CODE.search(low):
            return SIMPLIFIED
        match = POKEMON_CODE.search(text or "")
        if match:
            return CODE_LETTER.get((match.group(2) or "").lower(), JAPANESE)
        if JAPANESE_KINDS.search(low) or JAPANESE_SETS.search(low):
            return JAPANESE
    if game in ("", "one-piece"):
        match = ONE_PIECE_CODE.search(low)
        if match:
            return CHINESE if match.group(1) == "c" else KOREAN
    return ENGLISH


def language_of(text, game="", loose=False):
    """The language ``text`` is in, as a code from NAMES, or "" for English."""
    said = stated(text, loose)
    if said:
        return said
    if ENGLISH_WORDS.search(plain(text)):
        return ENGLISH
    return implied(text, game)


def same(a, b):
    """Whether two languages can be the same product's: equal, or Chinese with one not saying which."""
    if a == b:
        return True
    return a in CHINESE_FAMILY and b in CHINESE_FAMILY and CHINESE in (a, b)


def name(code):
    """The language's name for a code, "English" for ""."""
    return NAMES.get(code, "English")


def filter_code(code):
    """The filter choice a language falls under: en, ja, zh, ko or other."""
    if not code:
        return "en"
    if code in CHINESE_FAMILY:
        return "zh"
    return code if code in ("ja", "ko") else "other"


def product_language(product):
    """A product's language: what its name says, or else what its set's name and code say."""
    game = product.game.slug if product.game_id else ""
    found = language_of(product.name, game)
    if not found and product.product_set_id:
        found = language_of(f"{product.product_set.name} {product.product_set.code or ''}", game)
    return found


def refresh_languages(dry_run=False):
    """Set every product's language from its name and set, for products saved before these rules or changed
    without being saved one by one. Their search text follows, so "japanese 151" finds a box that only
    says sv2a. Returns how many changed."""
    from .models import Product
    from .search import build_search_text

    changed = []
    for product in Product.objects.select_related("game", "product_set"):
        language = product_language(product)
        if language != product.language:
            product.language = language
            product.search_text = build_search_text(product)
            changed.append(product)
    if not dry_run:
        Product.objects.bulk_update(changed, ["language", "search_text"], batch_size=500)
    return len(changed)
