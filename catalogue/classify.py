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
import unicodedata
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
    ("cardfight-vanguard", "Cardfight!! Vanguard", "Vanguard", ("cardfight", "vanguard overdress", "vanguard: overdress")),
    ("weiss-schwarz", "Weiss Schwarz", "Weiss Schwarz", ("weiss schwarz", "weiß schwarz", "weiss-schwarz")),
    ("football", "Football cards", "Football", ("football", "soccer", "match attax", "adrenalyn", "premier league",
                                                 "champions league", "uefa", "fifa", "topps football", "panini football",
                                                 "world cup", "euros 20", "euro 20", "merlin heritage", "topps merlin",
                                                 "bundesliga", "la liga", "serie a", "topps now", "women's super league",
                                                 "topps tier one", "topps finest", "panini prizm", "panini select",
                                                 "panini donruss", "panini obsidian", "panini mosaic", "panini score")),
]

# Order matters: the first matching type wins.
TYPES = [
    ("collector_booster_box", ("collector booster box", "collector booster display", "collector display", "collector box")),
    ("collector_booster_pack", ("collector booster pack", "collector booster", "collector pack")),
    ("elite_trainer_box", ("elite trainer box", "etb", "trainer box")),
    ("collection_box", ("ultra premium collection", "super premium collection", "premium collection", "collection box",
                        "collector's box", "collectors box", "illumineer's trove", "illumineers trove", "trove", "box set",
                        "gift box", "collection", "figure box", "poster box", "pin box", "surprise box", "battle box", "starter kit",
                        "starter pack", "mega tin", "mega multipack", "multipack", "mega pack")),
    ("booster_box", ("booster box", "booster display", "display box", "display", "case of 36", "box of 36", "box of 24", "box of 30",
                     "hobby box", "retail box", "blaster box", "mega box", "jumbo box", "value box", "premium box", "fat pack box")),
    ("bundle", ("booster bundle", "bundle", "sleeved booster 3", "blister")),
    ("deck", ("commander deck", "starter deck", "theme deck", "battle deck", "league battle deck", "start deck", "trial deck",
              "deck", "precon", "two player starter", "2 player starter", "2-player starter")),
    ("tin", ("tin", "super tin", "booster tin")),
    ("gift_set", ("gift set", "gift pack")),
    ("booster_pack", ("booster pack", "booster", "pack", "packet", "checklane", "sleeved")),
]

# A title with any of these is not a sealed product we list.
NOT_SEALED = re.compile(
    r"\bdon!* card\b|\bcards only\b|\bno box\b|^\s*[A-Z]{1,4}\d{0,2}[- ]\d{2,3}\b|\b(?:super|ultra|secret|hyper|special|alternate|alt) ?(?:art )?rare\b|\brare foil\b|"
    r"\b(?:common|uncommon|rare)\s*:|\bpromo\s*:|\bfoil\s*:|"
    r"\b\d{1,3}\s*/\s*\d{1,3}\b|\bsingle\b|holofoil|holo rare|reverse holo|\bfoil\b(?!.*booster)|\bplaymat\b|\bsleeves?\b|\bbinder\b|"
    r"\bdeck box\b|\bportfolio\b|\btoploader|\bevent\b|\bpre[\s-]?release event\b|\bticket\b|\bcase\b(?! of)|\bcarton\b|"
    r"\bdamaged\b|\bopened\b|\bempty\b|\bdice\b|\bcounter\b|\bstorage\b|\bfigure\b(?! box)|\bplush\b|\bkeyring\b|\bposter\b(?! box)|"
    r"\bcode card\b|\bgraded\b|\bpsa\b|\bcgc\b|\bproxy\b|\bbulk\b|\blot of\b|\bjapanese single|\bpromo card\b|"
    r"\(near mint\)|\bnear mint\b|\blightly played\b|\bmoderately played\b|\(nm\)|\bpromo pack\b|\bpromotion pack\b|\bpower pack\b|"
    r"\bborderless\b|\bextended art\b|\(showcase\)|\bfoil etched\b|\bart card\b(?!.*tin)|"
    r"\b(?:x|×)\s?\d+\b|\b\d+\s?(?:x|×)\b|\bpack of \d+\b|\bbundle of \d+\b|"
    r"\bmystery booster(?: \d)?\s*$|\bmystery (?:box(?:es)?|bundles?|bags?|packs?)\b|\bdeck protectors?\b|\bprotectors?\b|\bholder\b|\bplay ?mat\b|\bgamegenic\b|\bultra[- ]pro\b|\bdragon shield\b|\bultimate guard\b|\bbastion\b|\bsidekick\b|\bsquire\b|\bwatchtower\b|\bsatin tower\b|\bzip-?up\b|\bart sleeves\b|\bcard holder\b|\bdeck holder\b|\bmatte sleeves\b|\bboxgods?\b|\bonline (?:deck )?code\b|\bprize pack\b|\bleague promo\b|\bnon-?holo\b|"
    r"\(planeswalker deck card\)|\bdeck card\b|\(borderless art\)|\bfull art\b(?!.*(?:box|tin|bundle|collection box))|"
    r"\btokens?\b|\bemblem\b|\bcode sheet\b|\bonline code\b|\bcard dividers?\b|\bdeck pods?\b|\(display commander\)|"
    r"\btheme booster card\b|\bbooster card\b|\bstickers?\b|\bmini album\b|\bcrates?\b|\bdeck box(?:es)?\b|\bcard case\b|"
    r"\bposters?\b|\bbeanie\b|\bscarf\b|\bfigures\b|\binch\b|\bmug\b|\bkeychain\b|\bwallet\b|\bhoodie\b|\bt-shirt\b|\bsocks\b|\bpuzzle\b|\bsofbits\b|\bshokugan\b",
    re.I,
)
# "Booster Box (36x Packs)" says what is inside the box, not that it is a multi-buy, but NOT_SEALED's
# "36x" rule would refuse it. Only a count of 6 to 36 packs in brackets straight after the box itself
# ("Booster Box (36x Packs)", "Display [24x Packs]") is rewritten, and only once. Any other count stays
# and is refused: "Booster Box (36x Packs) + 6x Booster Packs", "2 Booster Boxes (36x Packs)",
# "10x Booster Packs (Display Box)", "Mystery Box (10x Booster Packs)", and an unbracketed
# "Booster Box - 10x Booster Packs", which could as well be a box with packs added.
BOX_CONTENTS = re.compile(
    r"\b((?:booster\s+)?display(?:\s+box)?|booster\s+box|elite\s+trainer\s+box|etb)\b\s*[-–:]?\s*"
    r"([(\[]?)\s*(\d{1,2})\s?[x×]\s*((?:sealed\s+)?(?:booster\s*)?packs?)\b\s*([)\]]?)",
    re.I,
)


def box_contents(title, bracketed=True):
    """The title with a box's own pack count written as a count, not a multi-buy.

    ``bracketed=False`` is for shop addresses, which never keep brackets ("...-booster-box-36x-packs").
    """
    def rewrite(m):
        if not 6 <= int(m.group(3)) <= 36 or (bracketed and not (m.group(2) and m.group(5))):
            return m.group(0)
        return f"{m.group(1)} {m.group(2)}{m.group(3)} {m.group(4)}{m.group(5)}"
    return BOX_CONTENTS.sub(rewrite, title or "", count=1)


# Pack counts in a title or a variant label: "(36 Packs)", "36x Booster Packs", "18 Packs".
PACK_COUNT = re.compile(r"\b(\d{1,3})\s?[x×]?\s*(?:sealed\s+)?(?:booster\s*)?(?:packs?|boosters?)\b", re.I)
# Variant labels that are another amount of the same thing, never the product itself.
OTHER_AMOUNT = re.compile(r"\b(?:case|cases|half|quarter|carton|bundle of|set of|lot of)\b|\bx\s?\d|\d\s?x\b", re.I)


def variant_differs(page_title, label, shop_type=""):
    """Why a variant on a product page is not the page's product ("" when it is).

    A shop's page for a booster box may offer "1 Pack", "18 Packs", "Half Box" or "Case (6 Boxes)" as
    variants. Each is a different product at a different price, so none may price the box. A variant
    that names another kind, another amount or a pack count the page's own title does not state is
    left out; "English", "Japanese" or the page's own count ("36 Packs" on "...(36x Packs)") are not.
    """
    if not label:
        return ""
    if OTHER_AMOUNT.search(label):
        return "a variant of another amount"
    kind, page_kind = label_kind(label), find_type(page_title, shop_type)
    if kind is not None and page_kind is not None and kind != page_kind:
        return "a variant of another kind"
    counts = {int(n) for n in PACK_COUNT.findall(label)}
    if counts and not counts <= {int(n) for n in PACK_COUNT.findall(page_title)}:
        return "a variant of another amount"
    return ""


def label_kind(label):
    """The kind of product a variant label names ("1 Pack" is a booster pack), or None. A number of
    packs above one names no kind: it is a count, not a pack."""
    folded = unicodedata.normalize("NFKD", label or "").encode("ascii", "ignore").decode().lower()
    words = [word[:-1] if len(word) > 3 and word.endswith("s") else word for word in re.findall(r"[a-z0-9']+", folded)]
    text = f" {' '.join(words)} "
    counts = [int(word) for word in words if word.isdigit()]
    for kind, phrases in TYPES:
        if any(f" {phrase} " in text for phrase in phrases):
            if kind == "booster_pack" and any(n > 1 for n in counts):
                return None
            return kind
    return None


# Makers of merchandise and accessories, not cards. Nothing from them is a sealed TCG product.
NOT_SEALED_VENDORS = {"gb posters", "gb eye", "difuzed", "funko", "loungefly", "paladone", "ultra pro", "ultra-pro", "gamegenic",
                      "dragon shield", "ultimate guard", "bandai spirits", "jazwares", "mattel", "hasbro", "lego",
                      "wizkids", "games workshop", "vallejo", "the noble collection", "pyramid international", "cinereplicas"}
# One shop listing that covers several kinds at once ("Booster Pack / Booster
# Box", "Pack Box Case") carries only the cheapest kind's price, so it cannot
# be priced as any one product.
VARIANT_MENU = re.compile(
    r"(?<![\d(])(?<!\d-)\bpacks?\b\s*(?:[/&,+-]|or|and)?\s*(?:booster\s*)?\b(?:box|boxes|case)\b(?! of)|"
    r"\b(?:box|boxes)\b\s*(?:[/&,+-]|or|and)?\s*(?:booster\s*)?(?<![\d(])(?<!\d-)\bpacks?\b(?!\))",
    re.I,
)
NOT_SEALED_TYPES = {"single card", "singles", "pokemon single", "playmat", "deck box", "card sleeves", "sleeves", "binder",
                    "event", "events", "life counter", "zip binder", "oversized card", "accessory", "accessories", "dice",
                    "board games", "board game", "miniatures", "rpg", "books", "toys", "plush", "apparel", "supplies", "storage"}

# Words removed from the start or end of a title when building the clean name.
PREFIXES = re.compile(
    r"^(?:pok[eé]mon(?: tcg| trading card game)?|magic(?: the gathering|: the gathering)?|mtg|one piece(?: card game| tcg)?|"
    r"disney lorcana(?: tcg)?|lorcana(?: tcg)?|yu-gi-oh!?|yugioh|star wars(?::)? unlimited|flesh (?:and|&) blood(?: tcg)?|"
    r"digimon(?: card game| tcg)?|dragon ball super(?: card game| tcg)?|riftbound(?::)?(?: league of legends tcg)?|"
    r"cardfight!*(?: vanguard)?(?: overdress)?|weiss schwarz|weiß schwarz)\s*[:\-–|]*\s*",
    re.I,
)
KIND_FIRST = re.compile(r"^(Booster Box|Booster Pack|Trial Deck\+?|Start Deck|Starter Deck|Extra Booster(?: Box| Pack)?)\s*[:\-–]\s*(.+)$", re.I)
NOISE = re.compile(
    r"\s*\((?:en|eng|english|uk|new|sealed|in stock|pre-?order|\d+ (?:sealed )?(?:booster )?packs?|\d+ boosters?|\d+ ct|[a-z]{2,3}-?\d{2,3}[a-z]?)\)"
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


SHOP_TAILS = re.compile(
    r"(?<=\S)\s*:?\s*[:\-–]?\s*(?:brand new(?: and| &)? sealed(?: box| inc .*)?|new (?:and|&) sealed(?: box)?|pre[- ]?order\b.*|"
    r"in stock(?: now)?|sealed)\s*!*\s*$",
    re.I,
)
SHOP_HEADS = re.compile(r"^\s*pre[- ]?order\b(?:.*?delivery time)?\s*[:\-–]?\s*", re.I)


def tidy_name(title):
    """The light fixes safe to apply to a name that is already in the catalogue."""
    name = re.sub(r"\s+", " ", html.unescape(title)).strip()
    name = name.lstrip("!¡*#~ ").replace("!", "")          # "! Duelist Nexus", "Sealed Box!"
    name = re.sub(r"\s*:\s*:\s*", ": ", name)               # "Tin: : Brand New" from an empty shop field
    for _ in range(2):
        name = SHOP_TAILS.sub("", SHOP_HEADS.sub("", name)).strip(" :,-")
    name = re.sub(r"\b(\w+)( \1\b)+", r"\1", name, flags=re.I)  # "Booster Booster Pack"
    return drop_repeated_tail(drop_repeated_set(name).replace(" | ", " "))


LANGUAGE = re.compile(
    r"\b(traditional chinese|simplified chinese|japanese|korean|chinese|german|french|italian|spanish|portuguese|"
    r"thai|russian|indonesian)\b",
    re.I,
)


def language_of(title):
    """The language a title names ("[JAPANESE]", "Korean"), capitalised, or ""."""
    match = LANGUAGE.search(title)
    return match.group(1).title() if match else ""


def clean_name(title):
    language = language_of(title)
    name = tidy_name(title)
    name = re.sub(r"\s+", " ", name).strip()
    name = drop_repeated_tail(PREFIXES.sub("", name))
    # Some shops write the kind first: "Booster Box - Kaguya-sama" becomes "Kaguya-sama Booster Box".
    name = KIND_FIRST.sub(lambda m: f"{m.group(2).strip()} {m.group(1)}", name)
    for _ in range(3):
        name = NOISE.sub("", name).strip()
    name = re.sub(r"\s*[-–|]\s*", " ", name)  # shop-style " - " separators become spaces
    name = re.sub(r"\s*:\s*", ": ", name)
    name = re.sub(r"\s+", " ", name).strip(" :,-")
    name = name.replace("Elite Trainer Box", "Elite Trainer Box").replace(" Etb", " ETB").replace(" ETB", " Elite Trainer Box")
    # A language edition is a different product; keep it in the name even
    # when the shop wrote it in brackets, which are otherwise noise.
    if language and not LANGUAGE.search(name):
        name = f"{name} ({language})"
    return name[:200]


def classify(title, shop_type="", vendor="", tags=(), price=None):
    """Return a Sealed description, or None if this is not a sealed product we list."""
    if price is not None and price <= 0:
        return None
    if (shop_type or "").lower() in NOT_SEALED_TYPES:
        return None
    if (vendor or "").lower().strip() in NOT_SEALED_VENDORS:
        return None
    title = box_contents(title)
    if NOT_SEALED.search(title):
        return None
    if VARIANT_MENU.search(title):
        return None
    # The title first: shops mis-tag (Card Empire files Magic boxes under "Pokemon cards").
    game = find_game(title) or find_game(vendor, " ".join(tags), shop_type)
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
