"""
The language a product or a shop's title is in, read from what it says. One place for the rules, used
by the language filter, product names, matching, duplicates, sets, eBay results, the autopilot and
Claude.

A title is English unless something in it says otherwise. Guards come first: "not Japanese", "rulebook
in Dutch", "made in Japan", "Chinese New Year" and "Asian English" say nothing about the cards. Then what
the title states:

1. a language word, in English or the language itself ("Japanese", "Deutsch", "Japonais"), or a product
   named in another language ("Top-Trainer-Box", "Coffret Dresseur d'Elite", "Bustine");
2. a seller's code where it cannot be an ordinary word: "(JP)", "[DE]", "- FR" at the end, "JP
   Version", and the tokens that are never words (JPN, CHN, KR). eBay titles are read loosely: an
   upper-case JP or KOR standing alone counts too;
3. a script: kana is Japanese, Hangul Korean, Thai Thai, Cyrillic Russian, Chinese characters alone
   Chinese (Simplified or Traditional where a character says which);
4. for Pokémon and One Piece, codes only a stated language has: Simplified Chinese (CSV8C, CBB3, Gem
   Pack, 151C), a language letter after a Japanese code ("s7D F" Traditional Chinese, "SV8-T" Thai),
   Indonesian and Thai combined sets (sv9s, MA2), One Piece OPC and OPK.

"English", "Eng" or "(EN)" then outweighs what the rest implies, for Pokémon only: a set code with a
letter after the number (sv2a, s12a, SM12a, M2a), a Japanese-only kind of product (High Class Pack,
Deck Build Box) or set name (Terastal Festival, Eevee Heroes). Korean and Traditional Chinese reuse the
Japanese codes, so these mean Japanese only when nothing states another language. A bare code English
shares (SV3, SV8, SM6, M2, OP-09) says nothing, and neither do set names English shares (Black Bolt,
White Flare, 151).

Football boxes are read by language words and scripts only: JPN, KOR and GER there are team codes.
A title that states two languages, or joins a Japanese-only set to another with "/", is mixed.
"""

import re
import unicodedata
from functools import lru_cache

ENGLISH = ""
JAPANESE, CHINESE, KOREAN = "ja", "zh", "ko"
SIMPLIFIED, TRADITIONAL = "zh-hans", "zh-hant"
ASIAN = "asia"   # an Asian edition whose language the title does not say (Thai or Indonesian sets)

# Every language the site tells apart, and its name.
NAMES = {
    "ja": "Japanese", "zh": "Chinese", "zh-hans": "Simplified Chinese", "zh-hant": "Traditional Chinese",
    "ko": "Korean", "th": "Thai", "id": "Indonesian", "asia": "Asian edition", "de": "German", "fr": "French",
    "it": "Italian", "es": "Spanish", "pt": "Portuguese", "ru": "Russian", "pl": "Polish", "nl": "Dutch",
}
CHINESE_FAMILY = ("zh", "zh-hans", "zh-hant")

# The public filter's choices: Japanese, Chinese and Korean apart, every other language together.
FILTER = [("en", "English"), ("ja", "Japanese"), ("zh", "Chinese"), ("ko", "Korean"), ("other", "Other languages")]
OTHER = [code for code in NAMES if code not in ("ja", "ko", *CHINESE_FAMILY)]

POKEMON, ONE_PIECE, FOOTBALL, MAGIC = "pokemon", "one-piece", "football", "magic-the-gathering"


def plain(text):
    """Lower case without accents, so "Français" and "Francais" read alike."""
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _table(rows):
    return [(re.compile(rf"(?<![\w-])(?:{pattern})(?![\w-])"), code) for pattern, code in rows]


# Language words, matched on plain text once the guards have taken out the ones that are not about cards.
LANGUAGE_WORDS = {
    "ja": r"japanese|japense|japanisch\w*|japonais(?:es?)?|japones(?:as?|es)?|giapponese",
    "ko": r"korean|koreanisch\w*|coreen(?:nes?|s)?|corean[oa]s?",
    "zh-hans": r"simplified chinese|s[\s-]chinese",
    "zh-hant": r"traditional chinese|t[\s-]chinese",
    "zh": r"chinese|chinesisch\w*|chinois(?:es?)?|cinese",
    "th": r"thai",
    "id": r"indonesian|bahasa indonesia|indonesia",
    "de": r"german|deutsch(?:e[nrs]?)?",
    "fr": r"french|francais(?:e)?",
    "it": r"italian|italiano",
    "es": r"spanish|espanol|castellano",
    "pt": r"portuguese|portugues|pt-br",
    "ru": r"russian",
}
_ANY_WORD = "|".join(LANGUAGE_WORDS.values())
# Products named in another language.
PRODUCT_WORDS = {
    "de": r"top-trainer-box|sammelkartenspiel|sammelkarten|karmesin (?:&|und) purpur|mega-entwicklung|"
          r"das erste kapitel|aufstieg der flutgestalten|der funke einer rebellion",
    "fr": r"coffret dresseur d'elite|coffret|display de 36 boosters|boite de 36 boosters|premier chapitre|"
          r"l'ascension des floodborn|les terres d'encres",
    "it": r"set allenatore fuoriclasse|scarlatto e violetto|megaevoluzione|bustine|nelle terre d'inchiostro",
    "es": r"caja de entrenador elite|escarlata y purpura|megaevolucion|sobres",
    "pt": r"treinador avancado|escarlate e violeta",
}
WORDS = _table([(LANGUAGE_WORDS[code], code) for code in LANGUAGE_WORDS])
PRODUCTS = _table([(PRODUCT_WORDS[code], code) for code in PRODUCT_WORDS])
# Dutch only as a label: "Flying Dutchman" and a Dutch rulebook are not Dutch cards.
DUTCH = re.compile(r"\(dutch\)|\bdutch language\b")
# French words for Scarlet & Violet and Mega Evolution, only with their accents: plain "Mega Evolution"
# is English.
ACCENTED = re.compile(r"écarlate et violet|méga-évolution", re.I)

# Words that name a language without saying the cards are in it.
GUARDS = re.compile(
    rf"\b(?:not|non|vs\.?|versus)[\s-]+(?:{_ANY_WORD})\b"
    rf"|\b(?:rulebook|rules|instructions)\s+in\s+(?:{_ANY_WORD}|dutch)\b"
    r"|\bchinese new year\b|\bchina box\b|\bmade in [a-z]+\b"
)
ENGLISH_WORDS = re.compile(r"(?<!asian )(?<!asia )(?<![\w-])(?:english|englisch|anglais|inglese|ingles|eng)(?![\w-])"
                           r"|[(\[]\s*en\s*[)\]]")
ASIAN_ENGLISH = re.compile(r"\basian? english\b|\basian?\s+edition\b")

# Sellers' codes, on plain text. Never in football, where they are team codes. Tokens that are never
# words count anywhere; short codes that are also words count in brackets, or as the last word after a
# separator; the shortest only in brackets.
CERTAIN_CODES = {"jpn": "ja", "chn": "zh", "kr": "ko"}
SEP_CODES = {"jp": "ja", "jap": "ja", "kor": "ko", "cn": "zh", "chs": SIMPLIFIED, "cht": TRADITIONAL,
             "taiwan": TRADITIONAL, "indo": "id", "de": "de", "ger": "de", "fr": "fr", "vf": "fr", "ita": "it",
             "esp": "es"}
BRACKET_CODES = {**SEP_CODES, "th": "th", "it": "it", "es": "es", "pt": "pt", "ru": "ru"}
CERTAIN = re.compile(r"(?<![\w-])(jpn|chn|kr)(?![\w-])")
BRACKETED = re.compile(rf"[(\[【]\s*({'|'.join(sorted(BRACKET_CODES, key=len, reverse=True))})\s*[)\]】]")
TRAILING = re.compile(rf"(?:\s[-–|/]|,)\s*({'|'.join(sorted(SEP_CODES, key=len, reverse=True))})\s*$")
VERSION = re.compile(r"(?<![\w-])(jp|kr)\.?\s*(?:ver(?:sion)?|edition|exclusive)(?![\w])")
# eBay titles, read loosely: an upper-case JP, KOR, CHS or CHT standing alone ("CHS Pokémon 30th
# Celebration Booster Pack"). Not KOR in a title all in capitals, where it may be Magic's Kor.
LOOSE = re.compile(r"(?<![\w-])(JP|KOR|CHS|CHT)(?![\w-])")
LOOSE_CODES = {"JP": "ja", "KOR": "ko", "CHS": SIMPLIFIED, "CHT": TRADITIONAL}

# Scripts, on the text as written. Kana leaves out the middle dot and long-vowel mark, which some English
# titles from Japan-based shops use as separators.
KANA = re.compile(r"[぀-ヺヽ-ヿｦ-ﾟ]")
HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
THAI = re.compile(r"[฀-๿]")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
HAN = re.compile(r"[㐀-鿿]")
HAN_JAPANESE = re.compile(r"拡|強化|未開封")
HAN_TRADITIONAL = re.compile(r"寶|擴|繁體")
HAN_SIMPLIFIED = re.compile(r"宝|补|简体")

_B, B_ = r"(?<![a-z0-9.])", r"(?![a-z0-9])"
# A language letter after a Japanese code: F Traditional Chinese, T Thai, I Indonesian (never glued:
# s5I is itself a code).
_LETTER = r"(?:[\s-]?(?P<l>[ft])|\s(?P<i>i))?"
CODE_LETTER = {"f": TRADITIONAL, "t": "th", "i": "id"}
# Pokémon set codes only Asian editions have: a letter (or a plus, or a p) after the number. Mega codes
# are listed one by one as they become official: a pattern would take "Airsoft M1A".
POKEMON_CODE = [re.compile(pattern) for pattern in (
    rf"{_B}sv(?:1[sva]|2[pda]|3a|4[kma]|5[kma]|[6-9]a|11[bw]){_LETTER}{B_}",
    rf"{_B}sm(?:[1-5](?:\+|p)|1[sm]|2[kl]|3[hn]|4[sa]|5[sm]|(?:[6-9]|1[01])[ab]|12a){_LETTER}(?![a-z0-9+])",
    rf"{_B}s(?:1[wha]|[2-6]a|5[ir]|6[hk]|7[rd]|8[ab]|9a|10[pdab]|11[abw]|12a){_LETTER}{B_}",
    rf"{_B}m(?:1[ls]|2a|6a){_LETTER}{B_}",
)]
# Other Japanese-only codes: SM0, SMP2, the Scarlet & Violet product codes, lettered XY sets, Concept Packs.
OTHER_JAPANESE_CODE = re.compile(
    rf"{_B}(?:sm0(?![0-9a-z.])|smp2{B_}|(?:sv(?:al|am|aw|el|em|hk|hm|jl|jp|ln|ls|od|om)|svp1){B_}"
    rf"|(?:xy(?:1[xy]|5[gt]|8[br]|11[br])|cp[1-6]){B_})")
# A language letter glued or hyphenated to a bare code (SV9F, SV8-T): the bare code alone says nothing.
BARE_CODE_LETTER = re.compile(rf"{_B}(?:sv(?:[3-9]|10)|m[2-6]|s(?:[2-4]|[89]|1[12]))(?:-(?P<l>[ft])|(?P<g>f)){B_}")
# Simplified Chinese, which never reuses the Japanese codes.
SIMPLIFIED_CODE = re.compile(
    rf"{_B}(?:csv(?:10|[1-9])(?:\.5)?|csm[12](?:\.5|[abc])|cs[1-6](?:\.5|[ab])|cbb[1-6]|csvl[12]|csvm2a|csvh5|"
    rf"cs[eqp]1)(?:\s?c)?{B_}|{_B}30th\s?c{B_}|{_B}151c{B_}|\bcollect 151\b|\bgem(?: pack)?\s*vol|\bgem pack\b")
# Indonesian and Thai combined sets, and the codes Asian editions share.
INDONESIAN_SET = re.compile(rf"{_B}sv(?:[7-9]|10)s(?:\s?(?P<l>[it]))?{B_}")
MA_SET = re.compile(rf"{_B}ma[1-6](?:\s?(?P<l>[ti]))?{B_}")
ASIAN_SET = re.compile(rf"{_B}(?:sc[1-3][ab]|ac[1-3][ab])(?:[\s-]?(?P<l>[ti]))?{B_}")
INDONESIAN_NAMES = re.compile(r"\bbonds of destiny\b|\bpresence of champions\b")
# One Piece's Simplified Chinese and Korean codes.
ONE_PIECE_CODE = re.compile(r"(?<![\w.])(?:op|eb|prb)([ck])[\s-]?\d{1,2}(?![0-9])")

# Kinds of product sold only in Japan.
JAPANESE_KINDS = re.compile(
    r"\b(?:high[- ]class(?: booster)? (?:pack|box|deck)|enhanced expansion pack|strength(?:ening)? expansion pack|"
    r"premium trainer box|(?:master )?deck build box|special deck set|starter deck (?:&|and) build set|"
    r"start(?:er)? decks? 100|battle master deck|(?:ex|vstar|vmax) special set|jumbo[- ]pack set|"
    r"gym promo(?: card)? pack|gym promo vol)\b")
# Japanese-only set names: no English set has the name.
JAPANESE_SETS = re.compile(
    r"\b(?:shiny treasures? ex|terastal fest(?:ival|a)?|vstar universe|vmax climax|shiny star v|triplet beat|"
    r"triple beat|snow hazard|clay ?burst|ruler of the black flame|black flame ruler|dark flame ruler|raging surf|"
    r"ancient roar|future flash|cyber judge|crimson haze|mask of change|transformation mask|night wanderer|"
    r"stellar? miracle|paradise dragona|super electric breaker|supercharged breaker|heat ?wave arena|"
    r"hot wind arena|glory of team rocket|"
    r"mega brave|mega symphonia|mega dream(?: ex)?|nihil zero|munikis zero|munix zero|ninja spinner|abyss eye|"
    r"storm emeralda|"
    r"vmax rising|eevee heroes|rebellion crash|rebellion clash|explosive (?:flame )?walker|legendary heartbeat|"
    r"(?:amazing|astonishing) volt tackle|electrifying tackle|single strike master|rapid strike master|"
    r"matchless fighters|peerless fighters|silver lance|jet[- ]black (?:spirit|poltergeist|geist)|"
    r"blue sky stream|skyscraping perfection|towering perfection|fusion arts|star birth|battle region|time gazer|"
    r"space juggler|dark (?:phantasma|fantasma)|lost abyss|incandescent arcana|paradigm trigger|"
    r"collection (?:sun|moon)|islands await you|alolan moonlight|facing a new trial|let'?s face new trials|"
    r"did you see the fighting rainbow|light consuming darkness|awakened heroes|beasts from the ultradimension|"
    r"gx battle boost|champions?'?s? road|charisma of the ripped sky|thunderclap spark|fairy rise|"
    r"super-?burst impact|gx ultra shiny|tag bolt|night unison|full metal (?:wall|force)|double blaze|gg end|"
    r"sky legend|alter genesis|tag all stars)\b")
# Names English uses for other things, which count only as a sealed product's name.
JAPANESE_SETS_WITH_KIND = re.compile(
    r"\b(?:wild forces?|battle partners|infinity zone|inferno x)\s+(?:\w+\s+){0,2}?(?:booster|pack|box|bundle)\b"
    r"|\binferno x\b.*\bm2\b")
# A Japanese-only set joined to another: a mixed bundle.
JOINED = re.compile(r"\s(?:/|&|\+|and)\s")


@lru_cache(maxsize=65536)
def _guarded(text):
    """Plain text with the words that name a language without being one taken out."""
    return GUARDS.sub(" ", plain(text))


@lru_cache(maxsize=65536)
def stated_all(text, game="", loose=False):
    """Every language ``text`` states, in the order they first appear, as codes."""
    if not text:
        return ()
    low = ASIAN_ENGLISH.sub(" ", _guarded(text))
    found = []
    for pattern, code in WORDS:
        found += [(match.start(), code) for match in pattern.finditer(low)]
    if game != FOOTBALL:
        for pattern, code in PRODUCTS:
            found += [(match.start(), code) for match in pattern.finditer(low)]
        found += [(match.start(), "nl") for match in DUTCH.finditer(low)]
        found += [(match.start(), "fr") for match in ACCENTED.finditer(text)]
        found += [(match.start(), CERTAIN_CODES[match.group(1)]) for match in CERTAIN.finditer(low)]
        for pattern, table in ((BRACKETED, BRACKET_CODES), (TRAILING, SEP_CODES)):
            found += [(match.start(), table[match.group(1)]) for match in pattern.finditer(low)
                      if not (match.group(1) == "kor" and game == MAGIC)]
        found += [(match.start(), "ja" if match.group(1) == "jp" else "ko") for match in VERSION.finditer(low)]
        if loose:
            found += [(match.start(), LOOSE_CODES[match.group(1)]) for match in LOOSE.finditer(text)
                      if not (match.group(1) == "KOR" and (text.upper() == text or game == MAGIC))]
    for pattern, code in ((KANA, "ja"), (HANGUL, "ko"), (THAI, "th"), (CYRILLIC, "ru")):
        match = pattern.search(text)
        if match:
            found.append((10_000 + match.start(), code))
    han = HAN.search(text)
    if han and not KANA.search(text) and not HANGUL.search(text):
        code = ("ja" if HAN_JAPANESE.search(text) else TRADITIONAL if HAN_TRADITIONAL.search(text)
                else SIMPLIFIED if HAN_SIMPLIFIED.search(text) else CHINESE)
        found.append((10_000 + han.start(), code))
    if game == POKEMON:
        found += [(m.start(), _letter(m)) for pattern in POKEMON_CODE for m in pattern.finditer(low) if _letter(m)]
        found += [(m.start(), CODE_LETTER[m.group("l") or m.group("g")]) for m in BARE_CODE_LETTER.finditer(low)]
        found += [(m.start(), SIMPLIFIED) for m in SIMPLIFIED_CODE.finditer(low)]
        found += [(m.start(), CODE_LETTER[m.group("l") or "i"]) for m in INDONESIAN_SET.finditer(low)]
        found += [(m.start(), CODE_LETTER.get(m.group("l") or "", ASIAN)) for m in MA_SET.finditer(low)]
        found += [(m.start(), CODE_LETTER.get(m.group("l") or "", ASIAN)) for m in ASIAN_SET.finditer(low)]
        found += [(m.start(), "id") for m in INDONESIAN_NAMES.finditer(low)]
    if game == ONE_PIECE:
        found += [(m.start(), SIMPLIFIED if m.group(1) == "c" else KOREAN) for m in ONE_PIECE_CODE.finditer(low)]
    seen = []
    for _, code in sorted(found):
        if code not in seen:
            seen.append(code)
    return tuple(seen)


def _letter(match):
    letter = match.group("l") or match.group("i")
    return CODE_LETTER[letter] if letter else ""


def stated(text, game="", loose=False):
    """The first language ``text`` states, or "" (see stated_all)."""
    found = stated_all(text or "", game, loose)
    return found[0] if found else ENGLISH


@lru_cache(maxsize=65536)
def implied(text, game=""):
    """The language a Pokémon code, kind or set name implies: Japanese, or "". Only for Pokémon, so only
    when the game is known."""
    if game != POKEMON:
        return ENGLISH
    low = _guarded(text)
    if (any(pattern.search(low) for pattern in POKEMON_CODE) or OTHER_JAPANESE_CODE.search(low)
            or JAPANESE_KINDS.search(low) or JAPANESE_SETS.search(low) or JAPANESE_SETS_WITH_KIND.search(low)):
        return JAPANESE
    return ENGLISH


def language_of(text, game="", loose=False):
    """The language ``text`` is in, as a code from NAMES, or "" for English. ``game`` is the game's slug:
    the code and set rules hold only inside their game, so pass it whenever it is known. ``loose`` reads
    sellers' upper-case codes as eBay sellers write them."""
    text = text or ""
    said = stated(text, game, loose)
    if said:
        return said
    if ENGLISH_WORDS.search(_guarded(text)) or ASIAN_ENGLISH.search(_guarded(text)):
        return ENGLISH
    return implied(text, game)


def mixed(text, game=""):
    """Whether ``text`` states two languages that cannot be one product's, or joins a Japanese-only set to
    another set: a mixed bundle, or a title too unclear to act on."""
    found = stated_all(text or "", game)
    if any(not same(a, b) for a in found for b in found):
        return True
    if game != POKEMON or found:
        return False
    # A Japanese-only set joined to a part that names none: "Destined Rivals / Glory of Team Rocket".
    parts = [part for part in JOINED.split(_guarded(text or "")) if part.strip()]
    japanese = [bool(JAPANESE_SETS.search(part)) for part in parts]
    return any(japanese) and not all(japanese)


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
