"""
Decide whether a shop product is a sealed TCG product, and if so which game
and type it is and what to call it.

Shops name things differently ("Pokemon - Surging Sparks - Booster Box (36
Packs)", "Pokémon TCG: Surging Sparks Booster Box", "Surging Sparks ETB").
This turns all of those into one clean name so prices from different shops
land on one product page.
"""

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
    r"\(borderless\)|\(extended art\)|\(showcase\)|\bfoil etched\b|\bart card\b(?!.*tin)|"
    r"\b(?:x|×)\s?\d+\b|\b\d+\s?(?:x|×)\b|\bpack of \d+\b|\bbundle of \d+\b|"
    r"\bmystery booster(?: \d)?\s*$|\bdeck protectors?\b|\bprize pack\b|\bleague promo\b|\bnon-?holo\b|"
    r"\(planeswalker deck card\)|\bdeck card\b|\(borderless art\)|\bfull art\b(?!.*(?:box|tin|bundle|collection box))",
    re.I,
)
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


def clean_name(title):
    name = re.sub(r"\s+", " ", title).strip()
    name = PREFIXES.sub("", name)
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
    if NOT_SEALED.search(title):
        return None
    game = find_game(title, vendor, " ".join(tags))
    if game is None:
        return None
    kind = find_type(title, shop_type)
    if kind is None:
        return None
    name = clean_name(title)
    if len(name) < 6:
        return None
    return Sealed(game=game, product_type=kind, name=name)
