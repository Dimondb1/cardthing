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
from decimal import Decimal
import unicodedata
from dataclasses import dataclass

from . import languages
from .matching import SHOP_NOTES, chosen_option, expand

# slug, name, short name, words that identify the game in a title, vendor or tag
GAMES = [
    # First, because its sets are named after anime that other games' words could otherwise claim.
    ("union-arena", "Union Arena", "Union Arena", ("union arena", "union-arena")),
    ("pokemon", "Pokémon", "Pokémon", ("pokemon", "pokémon", "ptcg")),
    ("magic-the-gathering", "Magic: The Gathering", "Magic", ("magic the gathering", "magic: the gathering", "mtg", "magic:")),
    ("one-piece", "One Piece Card Game", "One Piece", ("one piece",)),
    ("lorcana", "Disney Lorcana", "Lorcana", ("lorcana",)),
    ("yu-gi-oh", "Yu-Gi-Oh!", "Yu-Gi-Oh", ("yu-gi-oh", "yugioh", "yu gi oh")),
    ("star-wars-unlimited", "Star Wars Unlimited", "Star Wars", ("star wars unlimited", "star wars: unlimited", "star wars")),
    ("flesh-and-blood", "Flesh and Blood", "Flesh and Blood", ("flesh and blood", "flesh & blood")),
    ("digimon", "Digimon Card Game", "Digimon", ("digimon",)),
    ("dragon-ball", "Dragon Ball Super Card Game", "Dragon Ball", ("dragon ball",)),
    ("riftbound", "Riftbound", "Riftbound", ("riftbound",)),
    ("cardfight-vanguard", "Cardfight!! Vanguard", "Vanguard", ("cardfight", "vanguard overdress", "vanguard: overdress")),
    ("weiss-schwarz", "Weiss Schwarz", "Weiss Schwarz", ("weiss schwarz", "weiß schwarz", "weiss-schwarz")),
    # Sports cards: football first, then American football, basketball, baseball and racing. Its address was
    # football while it held football cards only (catalogue/sports.py, which files them by product line).
    ("sports-cards", "Sports cards", "Sports", ("football", "soccer", "match attax", "adrenalyn", "premier league",
                                                 "champions league", "uefa", "fifa", "topps football", "panini football",
                                                 "world cup", "euros 20", "euro 20", "merlin heritage", "topps merlin",
                                                 "bundesliga", "la liga", "serie a", "topps now", "women's super league",
                                                 "topps tier one", "topps finest", "panini prizm", "panini select",
                                                 "panini donruss", "panini obsidian", "panini mosaic", "panini score",
                                                 "(nfl)", "(nba)", "(mlb)", "basketball", "baseball", "nascar")),
]

# Order matters: the first matching type wins.
TYPES = [
    ("collector_booster_box", ("collector booster box", "collector booster display", "collector display", "collector box")),
    ("collector_booster_pack", ("collector booster pack", "collector booster", "collector pack")),
    ("elite_trainer_box", ("elite trainer box", "etb", "trainer box")),
    ("collection_box", ("ultra premium collection", "super premium collection", "premium collection", "collection box",
                        "collector's box", "collectors box", "illumineer's trove", "illumineers trove", "trove", "box set",
                        "gift box", "collection", "figure box", "poster box", "pin box", "surprise box", "battle box", "starter kit",
                        "starter pack", "mega tin", "mega multipack", "multipack", "mega pack",
                        # Sealed boxes that are neither boosters nor decks: MTG's Scene Box and Draft Night, and the
                        # advent calendars shops call holiday calendars.
                        "scene box", "draft night", "holiday calendar", "advent calendar",
                        # Yu-Gi-Oh's tuck boxes, Pokemon Center special boxes, Lorcana's co-op Illumineer's Quest.
                        # Not One Piece's Illustration Box: its name is only its code, which names lose.
                        "tuck box", "special box", "illumineer's quest", "illumineers quest")),
    ("booster_box", ("booster box", "booster display", "display box", "display", "case of 36", "box of 36", "box of 24", "box of 30",
                     "hobby box", "retail box", "blaster box", "mega box", "jumbo box", "value box", "premium box", "fat pack box",
                     "hanger box", "full box")),
    ("bundle", ("booster bundle", "bundle", "sleeved booster 3", "blister")),
    ("deck", ("commander deck", "starter deck", "starter decks", "theme deck", "battle deck", "league battle deck", "start deck", "trial deck",
              "deck", "precon", "two player starter", "2 player starter", "2-player starter")),
    ("tin", ("tin", "super tin", "booster tin")),
    ("gift_set", ("gift set", "gift pack")),
    ("booster_pack", ("booster pack", "booster", "pack", "packet", "checklane", "sleeved")),
]

# A title with any of these is not a sealed product we list. A card number leads a single's title ("OP01-001",
# "BT22-045", "LOB-001"); "Set 01", "OP 18" and "SV 151" lead a set's name, and a title that goes on to name a
# box, pack or deck is the sealed product.
NOT_SEALED = re.compile(
    r"(?<!\bwith )\bdon!* card\b|\bcards only\b|\bno box\b|^\s*(?:[A-Z]{1,4}\d{1,2}[- ]\d{3}|[A-Z]{2,4}-\d{3})\b(?!.*\b(?:booster|box|pack|deck|bundle|display|tin|collection)\b)|\b(?:super|ultra|secret|hyper|special|alternate|alt) ?(?:art )?rare\b|\brare foil\b|"
    r"\b(?:common|uncommon|rare)\s*:|\bpromo\s*:|\bfoil\s*:|"
    r"\b\d{1,3}\s*/\s*\d{1,3}\b|\bsingle\b|holofoil|holo rare|reverse holo|\bfoil\b(?!.*booster)|"
    # Pokemon promo card numbers ("Iron Bundle SVP 066 - Holo"): set codes have one or two digits, promos three.
    r"\b(?:svp|swshp?|smp?|xyp?|bwp?)[ -]?\d{3}\b|\bholo\b(?!\s*(?:collection|box|tin|booster|pack))|\bplaymat\b|\bsleeves?\b|\bbinder\b|"
    r"\bdeck box\b|\bportfolio\b|\btoploader|\bevent\b|\bpre[\s-]?release event\b|\bticket\b|\bcase\b(?! of)|\bcarton\b|"
    # A plush packed in a tin with booster packs is the tin ("Electric Plush Blind Box Tin", Solent Cards), also
    # in an address that glues the tin to the next word ("...-blind-box-tinsimplified-chinese"). Not "tiny".
    r"\bdamaged\b|\bopened\b|\bripped seals?\b|\bcut seals?\b|\bbroken seals?\b|\bseals? (?:ripped|cut|broken)\b|\bempty\b|\bdice\b|\b(?:life|damage|spin ?down|loyalty|token)[ -]?counters?\b|\bstorage\b|\bfigure\b(?! box)|\bplush\b(?!.*\btins?(?![a-rt-z]))|\bkeyring\b|\bposter\b(?! box)|"
    # Toys and building sets that carry a card game's name, not cards.
    r"\bnanoblock\b|\bmega construx\b|\bmega bloks\b|\blego\b|\bfunko\b|\bbuilding (?:block|set)s?\b|"
    r"\bcode card\b|\bgraded\b|\bpsa\b|\bcgc\b|\bproxy\b|\bbulk\b|\blot of\b|\bjapanese single|\bpromo card\b|"
    r"\(near mint\)|\bnear mint\b|\blightly played\b|\bmoderately played\b|\(nm\)|\bpromo pack\b|\bpromotion pack\b|\bpower pack\b|"
    r"\bborderless\b|\bextended art\b|\(showcase\)|\bfoil etched\b|\bart card\b(?!.*tin)|"
    r"\b(?:x|×)\s?\d+\b|\b\d+\s?(?:x|×)\b|\bpack of \d+\b|\bbundle of \d+\b|"
    r"\bmystery booster(?: \d)?\s*$|\bmystery (?:box(?:es)?|bundles?|bags?|packs?)\b|\bdeck protectors?\b|\bprotectors?\b|\bholder\b|\bplay ?mat\b|\bgamegenic\b|\bultra[- ]pro\b|\bdragon shield\b|\bultimate guard\b|\bbastion\b|\bsidekick\b|\bsquire\b|\bwatchtower\b|\bsatin tower\b|\bzip-?up\b|\bart sleeves\b|\bcard holder\b|\bdeck holder\b|\bmatte sleeves\b|\bboxgods?\b|\bonline (?:deck )?code\b|\bprize pack\b|\bleague promo\b|\bnon-?holo\b|"
    r"\(planeswalker deck card\)|\bdeck card\b|\(borderless art\)|\bfull art\b(?!.*(?:box|tin|bundle|collection box))|"
    r"\btokens?\b|\bemblem\b|\bcode sheet\b|\bonline code\b|\bcard dividers?\b|\bdeck pods?\b|\(display commander\)|"
    # Second-hand stock ("Mega Charizard ex Tin (TRADE IN)"): the site lists new products. A bracketed
    # "(TRADE)" or "(TRADE/IMPORT)" tag is not explained by the shop, so it is treated the same.
    r"\btrade[- ]?ins?\b|\(trade\b[^)]*\)|\bpre[- ]?owned\b|\bsecond[- ]?hand\b|"
    r"\btheme booster card\b|\bbooster card\b|\bstickers?\b|\bmini album\b|\bcrates?\b|\bdeck box(?:es)?\b|\bcard case\b|"
    r"\bposters?\b|\bbeanie\b|\bscarf\b|\bfigures\b|\binch\b|\bmug\b|\bkeychain\b|\bwallet\b|\bhoodie\b|\bt-shirt\b|\bsocks\b|\bpuzzle\b|\bsofbits\b|\bshokugan\b",
    re.I,
)
# What only a single card's title says: a card number ("Eevee 133/165", not a date), a Pokémon promo number
# ("SVP 066"), a rarity, a grade or a card's condition. A marketplace title that says one sells cards from a
# product, not the product: "*SEALED* Pokémon TCG 2025 Holiday Calendar Eevee 133/165 Pikachu 025/165" on eBay
# was the Holiday Calendar's cheapest price on 24 Oct 2026. Narrower than NOT_SEALED, whose "1x" and "single"
# sellers also write of a sealed box or pack.
SINGLE_CARD = re.compile(
    r"(?<![/\d])\b\d{1,3}\s*/\s*\d{1,3}\b(?!\s*/\s*\d)|\b(?:svp|swshp?|smp?|xyp?|bwp?)[ -]?\d{3}\b|"
    r"\b(?:super|ultra|secret|hyper|special|illustration|alternate|alt) ?(?:art )?rare\b|\brare foil\b|holofoil|"
    r"\bholo rare\b|\breverse holo\b|\bgraded\b|\bpsa\b|\bcgc\b|\bbgs\b|\bpromo cards?\b|\bnear mint\b|\(nm\)|"
    r"\blightly played\b",
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
    r"cardfight!*(?: vanguard)?(?: overdress)?|weiss schwarz|weiß schwarz|union[- ]arena(?: tcg| card game)?)\s*[:\-–|]*\s*",
    re.I,
)
KIND_FIRST = re.compile(r"^(Booster Box|Booster Pack|Trial Deck\+?|Start Deck|Starter Deck|Extra Booster(?: Box| Pack)?)\s*[:\-–]\s*(.+)$", re.I)
# A bracketed code goes ("(OP-09)"), but not a Simplified Chinese one: "Chasing Glory Together Slim Booster Box
# (csv10)" is the box other shops call "Pursue Glory Together csv10 C", and only the code says so.
NOISE = re.compile(
    r"\s*\((?:en|eng|english|uk|new|sealed|in stock|pre-?order|\d+ (?:sealed )?(?:booster )?packs?|\d+ boosters?|\d+ ct|(?!c(?:sv|sm|bb))[a-z]{2,3}-?\d{2,3}[a-z]?)\)"
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


# A set's number is not a "box set": "Fabled Booster Box (Set 9)" is a booster box. Read on expand()ed text.
SET_NUMBER = re.compile(r"\b(?:set|chapter) \d{1,2}\b")


def find_type(title, shop_type=""):
    text = " " + SET_NUMBER.sub(" ", expand(title)) + " "
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


# What says a title is of a kind. A bare "booster" says nothing: nearly every title carries it.
KIND_PHRASES = {kind: tuple(p for p in phrases if p != "booster") for kind, phrases in TYPES}
# A title read as a pack must say pack, and one pack: "Blazing Dominion Booster" is as often the box,
# and "3-Pack", "3 x Booster Packs" or "Booster Pack x 3" is a bundle.
PACK_WORDS = re.compile(r"\bpacks?\b|\bpacket\b|\bchecklane\b|\bsleeved\b", re.I)
MULTI_PACK = re.compile(r"\d+\s*[-x×]?\s*(?:booster\s*)?packs?\b|\bpacks?\s+of\s+\d|\bpacks?\s*[x×]\s*\d", re.I)
# Kinds a name states so plainly that it outranks the shop's category. Collection boxes, gift sets and
# decks are left out: shops call one product a "Box Set", a "Gift Set" or a "Display".
PLAIN_KINDS = ("booster_box", "booster_pack", "elite_trainer_box", "bundle", "tin")
# Names no rule reads safely: a checklane blister or a double pack set is a few packs and a promo, a
# starter pack is a binder and cards, "Battle Decks ... Bundle" is two decks, a mystery box is anything.
UNSURE_KIND = re.compile(r"\bcheck\s?lane\b|\bdouble\s+pack\b|\bdisplay\s+set\b|\bstarter\b|\bdecks\b|\bmystery\b",
                         re.I)


def says_kind(title, kind):
    text = " " + SET_NUMBER.sub(" ", expand(box_contents(title))) + " "
    return any(f" {phrase} " in text for phrase in KIND_PHRASES.get(kind, ()))


def plain_type(name, current):
    """The type a product's name plainly states when it is not ``current``, or None.

    A shop files a pack under "Booster Boxes" and the product is made a booster box. The type filter
    then shows a pack among the boxes. A name that says one kind plainly, and not ``current``, wins.
    """
    kind = find_type(box_contents(name))
    if kind not in PLAIN_KINDS or kind == current or says_kind(name, current) or UNSURE_KIND.search(name):
        return None
    if kind == "booster_pack" and (not PACK_WORDS.search(name) or MULTI_PACK.search(name)):
        return None
    # A name naming two kinds ("Bundle: 2 Booster Boxes") is not plain. A box may list its packs.
    if any(says_kind(name, other) for other in PLAIN_KINDS if other not in (kind, "booster_pack")):
        return None
    return kind


# Nothing sealed sells for less than this. Cheaper things are single cards,
# tokens, code sheets and other loose parts.
MIN_PRICE = 2
# Japanese and Korean booster packs sell for under £2 (Japan2UK: 59p to £1.99). A title that plainly says
# booster pack, and nothing that marks a single card, is a pack at any price from this.
MIN_PACK_PRICE = Decimal("0.50")
# A price of nothing but nines (£999, £999.99, £9,999) is a shop's way of saying not for sale
# (Impact League TCG's in-stock "Paldea Evolved Booster Box" at £999): such an offer is left out.
PLACEHOLDER_PRICE = re.compile(r"^9{3,}(?:\.(?:00|99))?$")
PLAIN_PACK = re.compile(r"\bbooster pack\b", re.I)

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
    r"(?<=\S)\s*:?\s*[:\-–]?\s*(?:brand new(?: and| &)? sealed(?: box| inc .*)?|new (?:and|&) sealed(?: box)?|(?<![\[(])pre[- ]?order\b.*|"
    r"in stock(?: now)?|sealed|\(margin\))\s*!*\s*$",
    re.I,
)
SHOP_HEADS = re.compile(
    r"^\s*[\[(]?\s*(?:pre[- ]?orders?\b(?:.*?delivery time)?|new shop stock\b)\s*[\])]?\s*[:\-–]?\s*", re.I
)


def tidy_name(title):
    """The light fixes safe to apply to a name that is already in the catalogue."""
    name = re.sub(r"\s+", " ", html.unescape(title)).strip()
    # A long dash between words is a shop's separator ("Booster Box — Simplified Chinese"), and the site
    # never shows one: it becomes the plain " - " every other rule here already treats as a separator.
    name = re.sub(r"\s*[‒—―]\s*", " - ", name)
    name = name.lstrip("!¡*#~ ").replace("!", "")          # "! Duelist Nexus", "Sealed Box!"
    name = re.sub(r"\s*:\s*:\s*", ": ", name)               # "Tin: : Brand New" from an empty shop field
    for _ in range(2):
        name = SHOP_TAILS.sub("", SHOP_HEADS.sub("", name)).strip(" :,-")
    # A shop's note on how many each customer may buy or when it comes out ("*1pp limit*", "(Releases
    # 27.03.26)", "February Restock"), and the brackets or stars it leaves empty.
    name = re.sub(r"\(\s*\)|\[\s*\]|\*\s*\*", " ", SHOP_NOTES.sub(" ", name))
    name = re.sub(r"\s*[(\[]\s*$", "", re.sub(r"\s+", " ", name)).strip(" :,-")
    name = re.sub(r"\b(\w+)( \1\b)+", r"\1", name, flags=re.I)  # "Booster Booster Pack"
    # "(Default)" from a shop's system, and of "Sylveon / Greninja Ex Tin (Greninja)" only the one chosen.
    name = chosen_option(name)
    return drop_repeated_tail(drop_repeated_set(name).replace(" | ", " "))


def language_of(title, game="", loose=False):
    """The language a title is in ("Japanese", "Simplified Chinese"), or "" for English: in words, a
    seller's code, a script, or a set code only another language has (catalogue/languages.py)."""
    code = languages.language_of(title or "", game, loose=loose)
    return languages.name(code) if code else ""


# Union Arena titles carry the game's name, "English Version" and the set code anywhere in them
# ("Jujutsu Kaisen Union Arena English Version Booster Box", "UE03BT Jujutsu Kaisen Booster Box"). English is
# the default and the language is read from the title before this, so none of it belongs in the name.
# "Vol 1" goes too: the first volume of a set is its plain name ("Hunter x Hunter" and "Hunter x Hunter Vol 1").
UNION_ARENA_NOISE = re.compile(
    r"\bunion[\s-]*arena\b(?:\s+(?:tcg|trading card game|card game))?|\benglish\s+ver(?:sion|\.)?(?!\w)"
    r"|\bvol(?:ume)?\.?\s*1(?!\d)\b", re.I)
# UE02BT, UA53BT, UE24ST, and an EX set's UEX01BT (a shop's slip "UEX05T" too). An EX code goes only when the
# name says its volume, since the code is otherwise all that tells it from the first set.
UNION_ARENA_CODE = re.compile(r"\bu(?:a|e|ex)\d{1,2}\s?(st|b?t)\b\s*:?", re.I)
KIND_WORD = re.compile(r"\b(?:booster|deck|pack|box|bundle|display|case)\b", re.I)


def union_arena_name(name):
    """A Union Arena name without the game's name, "English Version" or its set code. A title that is only a
    code and a set ("UE04BT CODE GEASS: Lelouch of the Rebellion") gets its kind from the code."""
    codes = [m.group(1).lower() for m in UNION_ARENA_CODE.finditer(name)]
    name = UNION_ARENA_NOISE.sub(" ", name)
    has_volume = re.search(r"\bvol", name, re.I)
    name = UNION_ARENA_CODE.sub(lambda m: m.group(0) if m.group(0).lower().startswith("uex") and not has_volume else " ",
                                name)
    name = re.sub(r"\s+", " ", name).strip(" :,-")
    if codes and not KIND_WORD.search(name):
        name = f"{name} {'Starter Deck' if codes[0] == 'st' else 'Booster Box'}"
    return name


def clean_name(title, game=""):
    code = languages.language_of(title or "", game)
    name = tidy_name(title)
    name = re.sub(r"(?<=[a-z])\((?=[a-z])", " (", name, flags=re.I)   # "Blind Box Tin(Simplified Chinese)"
    name = re.sub(r"\s+", " ", name).strip()
    name = drop_repeated_tail(PREFIXES.sub("", name))
    # Some shops write the kind first: "Booster Box - Kaguya-sama" becomes "Kaguya-sama Booster Box".
    name = KIND_FIRST.sub(lambda m: f"{m.group(2).strip()} {m.group(1)}", name)
    for _ in range(3):
        name = NOISE.sub("", name).strip()
    name = re.sub(r"\s*[-–|]\s*", " ", name)  # shop-style " - " separators become spaces
    name = re.sub(r"\s*:\s*", ": ", name)
    name = re.sub(r"\s+", " ", name).strip(" :,-")
    if game == "union-arena":
        name = union_arena_name(name)
    name = name.replace("Elite Trainer Box", "Elite Trainer Box").replace(" Etb", " ETB").replace(" ETB", " Elite Trainer Box")
    # A language edition is a different product; keep it in the name in words even when the shop wrote
    # it in brackets, which are otherwise noise, or gave only a set code ("151 (sv2a)" is Japanese).
    if code and not languages.stated(name, game):
        name = f"{name} ({languages.name(code)})"
    return name[:200]


def classify(title, shop_type="", vendor="", tags=(), price=None):
    """Return a Sealed description, or None if this is not a sealed product we list."""
    if price is not None and (price <= 0 or PLACEHOLDER_PRICE.match(str(price))):
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
    # The shop's category goes first, but a title that plainly says another type is right.
    kind = plain_type(title, kind) or kind
    floor = MIN_PACK_PRICE if kind == "booster_pack" and PLAIN_PACK.search(title) else MIN_PRICE
    if price is not None and price < floor:
        return None
    name = clean_name(title, game)
    if len(name) < 6 or is_generic(name):
        return None
    return Sealed(game=game, product_type=kind, name=name)
