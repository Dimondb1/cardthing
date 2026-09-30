"""
Decide whether a shop product is a sealed TCG product, and if so which game
and type it is and what to call it.

Shops name things differently ("Pokemon - Surging Sparks - Booster Box (36
Packs)", "Pokémon TCG: Surging Sparks Booster Box", "Surging Sparks ETB").
This turns all of those into one clean name so prices from different shops
land on one product page.
"""

import html
import re
from dataclasses import dataclass

from .matching import expand

# slug, name, short name, words that identify the game in a title, vendor or tag
GAMES = [
    ("pokemon", "Pokémon", "Pokémon", ("pokemon", "pokémon", "ptcg")),
    ("magic-the-gathering", "Magic: The Gathering", "Magic", ("magic the gathering", "magic: the gathering", "mtg", "magic:")),
    ("one-piece", "One Piece Card Game", "One Piece", ("one piece",)),
    ("lorcana", "Disney Lorcana", "Lorcana", ("lorcana",)),
    ("yu-gi-oh", "Yu-Gi-Oh!", "Yu-Gi-Oh", ("yu-gi-oh", "yugioh", "yu gi oh")),
    ("star-wars-unlimited", "Star Wars Unlimited", "Star Wars", ("star wars unlimited", "star wars: unlimited")),
    ("flesh-and-blood", "Flesh and Blood", "Flesh and Blood", ("flesh and blood", "flesh & blood")),
    ("digimon", "Digimon Card Game", "Digimon", ("digimon",)),
    ("dragon-ball", "Dragon Ball Super Card Game", "Dragon Ball", ("dragon ball",)),
    ("riftbound", "Riftbound", "Riftbound", ("riftbound",)),
]

# Order matters: the first matching type wins.
TYPES = [
    ("elite_trainer_box", ("elite trainer box", "etb", "trainer box")),
    ("collection_box", ("ultra premium collection", "super premium collection", "premium collection", "collection box",
                        "collector's box", "collectors box", "illumineer's trove", "illumineers trove", "trove", "box set",
                        "gift box", "collection", "figure box", "poster box", "pin box", "surprise box", "battle box", "starter kit")),
    ("booster_box", ("booster box", "booster display", "display box", "display", "case of 36", "box of 36", "box of 24", "box of 30")),
    ("bundle", ("booster bundle", "bundle", "sleeved booster 3", "blister")),
    ("deck", ("commander deck", "starter deck", "theme deck", "battle deck", "league battle deck", "deck", "precon",
              "two player starter", "2 player starter", "2-player starter")),
    ("tin", ("tin",)),
    ("gift_set", ("gift set", "gift pack")),
    ("booster_pack", ("booster pack", "booster", "pack", "checklane", "sleeved")),
]

# A title with any of these is not a sealed product we list.
NOT_SEALED = re.compile(
    r"\b\d{1,3}\s*/\s*\d{1,3}\b|\bsingle\b|holofoil|holo rare|reverse holo|\bfoil\b(?!.*booster)|\bplaymat\b|\bsleeves?\b|\bbinder\b|"
    r"\bdeck box\b|\bportfolio\b|\btoploader|\bevent\b|\bpre-?release event\b|\bticket\b|\bcase\b(?! of)|\bcarton\b|"
    r"\bdamaged\b|\bopened\b|\bempty\b|\bdice\b|\bcounter\b|\bstorage\b|\bfigure\b(?! box)|\bplush\b|\bkeyring\b|\bposter\b(?! box)|"
    r"\bcode card\b|\bgraded\b|\bpsa\b|\bcgc\b|\bproxy\b|\bbulk\b|\blot of\b|\bjapanese single|\bpromo card\b|"
    r"\(near mint\)|\bnear mint\b|\blightly played\b|\bmoderately played\b|\(nm\)|\bpromo pack\b|\bpromotion pack\b|\bpower pack\b|"
    r"\bborderless\b|\bextended art\b|\(showcase\)|\bfoil etched\b|\bart card\b(?!.*tin)|"
    r"\b(?:x|×)\s?\d+\b|\b\d+\s?(?:x|×)\b|\bpack of \d+\b|\bbundle of \d+\b|"
    r"\bmystery booster(?: \d)?\s*$|\bdeck protectors?\b|\bprize pack\b|\bleague promo\b|\bnon-?holo\b|"
    r"\(planeswalker deck card\)|\bdeck card\b|\(borderless art\)|\bfull art\b(?!.*(?:box|tin|bundle|collection box))|"
    r"\btokens?\b|\bemblem\b|\bcode sheet\b|\bonline code\b|\bcard dividers?\b|\bdeck pods?\b|\(display commander\)|"
    r"\btheme booster card\b|\bbooster card\b|\bstickers?\b|\bmini album\b|\bcrates?\b|\bdeck box(?:es)?\b|\bcard case\b|"
    r"\bposters?\b|\bbeanie\b|\bscarf\b|\bfigures\b|\binch\b|\bmug\b|\bkeychain\b|\bwallet\b|\bhoodie\b|\bt-shirt\b|\bsocks\b|\bpuzzle\b|\bsofbits\b|\bshokugan\b",
    re.I,
)
# Makers of merchandise and accessories, not cards. Nothing from them is a sealed TCG product.
NOT_SEALED_VENDORS = {"gb posters", "difuzed", "funko", "loungefly", "paladone", "ultra pro", "ultra-pro", "gamegenic",
                      "dragon shield", "ultimate guard", "bandai spirits", "jazwares", "mattel", "hasbro", "lego",
                      "wizkids", "games workshop", "vallejo", "the noble collection", "pyramid international", "cinereplicas"}
NOT_SEALED_TYPES = {"single card", "singles", "pokemon single", "playmat", "deck box", "card sleeves", "sleeves", "binder",
                    "event", "events", "life counter", "zip binder", "oversized card", "accessory", "accessories", "dice",
                    "board games", "board game", "miniatures", "rpg", "books", "toys", "plush", "apparel", "supplies", "storage"}

# Words removed from the start or end of a title when building the clean name.
PREFIXES = re.compile(
    r"^(?:pok[eé]mon(?: tcg| trading card game)?|magic(?: the gathering|: the gathering)?|mtg|one piece(?: card game| tcg)?|"
    r"disney lorcana(?: tcg)?|lorcana(?: tcg)?|yu-gi-oh!?|yugioh|star wars(?::)? unlimited|flesh (?:and|&) blood(?: tcg)?|"
    r"digimon(?: card game| tcg)?|dragon ball super(?: card game| tcg)?|riftbound(?::)?(?: league of legends tcg)?)\s*[:\-–|]*\s*",
    re.I,
)
NOISE = re.compile(
    r"\s*\((?:en|eng|english|uk|new|sealed|in stock|pre-?order|\d+ packs?|\d+ boosters?|\d+ ct|[a-z]{2,3}-?\d{2,3}[a-z]?)\)"
    r"|\s*\[[^\]]*\]|\s*[-–:|]\s*(?:english|en|sealed|new|pre-?order|in stock)\s*$|\s*[-–]\s*$|^\s*[-–:]\s*",
    re.I,
)


@dataclass(frozen=True)
class Sealed:
    game: str        # game slug
    product_type: str
    name: str


def find_game(*texts):
    blob = " ".join(t for t in texts if t).lower()
    for slug, _name, _short, words in GAMES:
        if any(w in blob for w in words):
            return slug
    return None


def find_type(title, shop_type=""):
    text = " " + expand(title) + " "
    shop_type = (shop_type or "").lower()
    for kind, words in TYPES:
        if any(w.lower() in shop_type for w in words if len(w) > 3) and kind != "booster_pack":
            return kind
    for kind, words in TYPES:
        if any(f" {w} " in text or text.strip().endswith(" " + w) for w in words):
            return kind
    if "booster pack" in shop_type or shop_type == "pack":
        return "booster_pack"
    return None


# Nothing sealed sells for less than this. Cheaper things are single cards,
# tokens, code sheets and other loose parts.
MIN_PRICE = 2

# A name made only of these words says what kind of thing it is, not which.
GENERIC_WORDS = {"booster", "boosters", "pack", "packs", "box", "boxes", "deck", "decks", "starter", "bundle", "tin",
                 "tins", "collection", "elite", "trainer", "display", "gift", "set", "the", "edition", "of", "and", "&",
                 "card", "cards", "game", "tcg", "sealed", "new", "a", "an"}


def is_generic(name):
    return not {w for w in re.findall(r"[a-z0-9&']+", name.lower())} - GENERIC_WORDS


def drop_repeated_set(title):
    """'Innistrad Booster Box | Innistrad' -> 'Innistrad Booster Box'.

    Some shops append the set name after a bar. When every word of it is
    already in the product name it adds nothing.
    """
    parts = [p.strip() for p in title.split("|")]
    if len(parts) == 2 and parts[0] and parts[1]:
        left, right = (set(re.findall(r"[a-z0-9']+", p.lower())) for p in parts)
        if right and right <= left:
            return parts[0]
    return title


def drop_repeated_tail(name):
    """'Innistrad Booster Box Innistrad' -> 'Innistrad Booster Box'.

    The same set name repeated at the end, once the bar between them has gone.
    """
    words = name.split()
    for k in range(len(words) // 2, 0, -1):
        tail = words[-k:]
        meaningful = {w.lower().strip(":,") for w in tail} - GENERIC_WORDS
        # One short repeated word ("V Heroes Tin Umbreon V") is not a set name.
        if tail == words[:k] and meaningful and (k > 1 or len(tail[0]) >= 4):
            return " ".join(words[:-k])
    return name


def tidy_name(title):
    """The light fixes safe to apply to a name that is already in the catalogue."""
    name = re.sub(r"\s+", " ", html.unescape(title)).strip()
    return drop_repeated_tail(drop_repeated_set(name).replace(" | ", " "))


def clean_name(title):
    name = tidy_name(title)
    name = re.sub(r"\s+", " ", name).strip()
    name = drop_repeated_tail(PREFIXES.sub("", name))
    for _ in range(3):
        name = NOISE.sub("", name).strip()
    name = re.sub(r"\s*[-–|]\s*", " ", name)  # shop-style " - " separators become spaces
    name = re.sub(r"\s*:\s*", ": ", name)
    name = re.sub(r"\s+", " ", name).strip(" :,-")
    name = name.replace("Elite Trainer Box", "Elite Trainer Box").replace(" Etb", " ETB").replace(" ETB", " Elite Trainer Box")
    return name[:200]


def classify(title, shop_type="", vendor="", tags=(), price=None):
    """Return a Sealed description, or None if this is not a sealed product we list."""
    if price is not None and price <= 0:
        return None
    if (shop_type or "").lower() in NOT_SEALED_TYPES:
        return None
    if (vendor or "").lower().strip() in NOT_SEALED_VENDORS:
        return None
    if NOT_SEALED.search(title):
        return None
    game = find_game(title, vendor, " ".join(tags))
    if game is None:
        return None
    kind = find_type(title, shop_type)
    if kind is None:
        return None
    if price is not None and price < MIN_PRICE:
        return None
    name = clean_name(title)
    if len(name) < 6 or is_generic(name):
        return None
    return Sealed(game=game, product_type=kind, name=name)
