"""
What is inside a product: how many packs, and how many cards in each, read from the shops' own descriptions.

The owner, 30 Oct 2026: "we need to know what comes in a pack, for example in a booster box how many booster
packs". The product page shows it with the price per pack, so a box, a bundle and an Elite Trainer Box can be
weighed against each other. Nothing is guessed from the product type: counts differ by game, language and set
(an English Pokémon box has 36 packs, a Japanese one 30, a Magic Play Booster box 30, One Piece 24), so a count is
shown only when shops say it.

Each live UK shop listing's description (kept by every read, importers.clean_description) gives at most one
count of packs and one of cards a pack, and only when it states it plainly and once: a description giving two
different counts, or describing a case of several boxes, gives none. The product's count is the one most of the
shops that give one agree on, more than all the others together. Marketplaces' descriptions are sellers' own words
and are never read. The owner's own figures (Product.packs, cards_per_pack) always win; refresh_contents keeps the
shops' (packs_found, cards_found) each hour from tidy_all.
"""

import re
from collections import Counter

# Pack counts a sealed product can hold: a single pack up to the largest boxes. A number outside is something else
# (a set's number, "151", or a count of cards).
MOST_PACKS = 60
MOST_CARDS = 30

# One or two digits standing alone: never part of a price (£2.50), a larger number (a set's 151), a set code (OP-11,
# SV10) or a percentage.
NUMBER = r"(?<![\w£$€/-])(?<!\d[.,])(\d{1,2})(?![\d%]|[.,]\d)"
NUMBER_ALONE = re.compile(NUMBER)
# What a pack is called: booster packs, packs, and Magic's and others' named boosters.
PACK_WORD = re.compile(
    r"\b(?:booster\s+packs?|packs?|(?:play|draft|set|collector|jumpstart|premium|sleeved)\s+boosters?|boosters)\b", re.I)
# Where a clause ends, so a count is only ever read from the clause its pack word is in. Not a colon: "Pokémon TCG:"
# is in the middle of most pack names. Nor the point in a price (£2.50).
BREAK = re.compile(r"[.,](?!\d)|[;!?\n•|()\[\]]|\s[-–]\s")
# Words, never a number, between a count and what it counts.
WORDS = r"(?:[^\W\d][\w'’:-]*\s+)"
# A number that counts something else: cards ("1 full-art foil promo card", "10 cards per pack"), or boxes and the like
# ("1 booster box containing 36 packs").
COUNTS_OTHER = re.compile(r"\s*(?:x|×)?\s*" + WORDS + r"{0,4}?(?:cards?|box|boxes|displays?|tins?|bundles?|blisters?|"
                          r"cases?|sleeves|dice|coins?)\b", re.I)
# A share ("1 of 36 packs", "1 from 4 promo cards"): neither number is the count.
SHARE = re.compile(r"\s+(?:of|from)\s+" + NUMBER, re.I)
LOOK_BACK = 120
CARDS = (
    # "10 cards per pack", "5 cards in each pack", "14 Magic cards per Play Booster".
    re.compile(NUMBER + r"\s*" + WORDS + r"{0,4}?cards?\s+(?:per|in\s+(?:each|every|a|the)|each|a)\s+" + WORDS +
               r"{0,2}?(?:pack|booster)s?\b", re.I),
    # "Each booster pack contains 10 cards", "Each Play Booster contains 14 Magic: The Gathering cards".
    re.compile(r"\b(?:each|every|per|one)\s+" + WORDS + r"{0,2}?(?:pack|booster)\s+(?:contains|includes|has|holds|"
               r"comes\s+with|features)\s+" + NUMBER + r"\s*" + WORDS + r"{0,4}?cards?\b", re.I),
    # "36 packs of 10 cards", "booster packs with 10 cards each".
    re.compile(r"\b(?:packs?|boosters?)\s+(?:of|with)\s+" + NUMBER + r"\s*" + WORDS + r"{0,2}?cards?\b", re.I),
)
# Several boxes in one product (a case, a display of displays): the pack count given is a box's, not the product's.
BOXES = re.compile(NUMBER + r"\s*(?:x|×)?\s*(?:sealed\s+)?(?:booster\s+)?(?:boxes|box|displays?)\b", re.I)
CASE = re.compile(r"\bcase\s+of\b|\bsealed\s+case\b|\bbooster\s+box\s+case\b", re.I)


def one(found):
    """The single value a description states, or None when it states none or more than one."""
    values = set(found)
    return values.pop() if len(values) == 1 else None


def several_boxes(text):
    return bool(CASE.search(text)) or any(int(match.group(1)) >= 2 for match in BOXES.finditer(text))


def count_before(clause):
    """The first number in ``clause`` that counts packs, or None: counts of other things and shares are passed over."""
    numbers = list(NUMBER_ALONE.finditer(clause))
    skip = set()
    for i, number in enumerate(numbers):
        after = clause[number.end():]
        if SHARE.match(after):
            skip.update({i, i + 1})
        if i in skip or COUNTS_OTHER.match(after):
            continue
        count = int(number.group(1))
        return count if 1 <= count <= MOST_PACKS else None
    return None


def packs_in(text):
    """The number of packs a shop's description says the product holds, or None.

    Each pack word ("booster packs", "Play Boosters") is read with the first count in its own clause, which starts
    after the pack word before it: "9 Pokémon TCG: Scarlet & Violet, 151 booster packs" is 9 (three digits are never
    a count), "Booster Box (36 packs)" is 36. A description that counts packs twice with different numbers, or
    describes several boxes, gives none."""
    if not text or several_boxes(text):
        return None
    found = []
    previous = 0
    for word in PACK_WORD.finditer(text):
        begin = max(previous, word.start() - LOOK_BACK)
        window = text[begin:word.start()]
        previous = word.end()
        clause = window[max((cut.end() for cut in BREAK.finditer(window)), default=0):]
        count = count_before(clause)
        if count is not None:
            found.append(count)
    return one(found)


def cards_in(text):
    """The number of cards in each pack a shop's description gives, or None."""
    if not text:
        return None
    found = [int(match.group(1)) for pattern in CARDS for match in pattern.finditer(text)]
    return one(count for count in found if 1 <= count <= MOST_CARDS)


def agreed(values):
    """The value most shops give, when more give it than all the others together; else None."""
    counted = Counter(value for value in values if value is not None)
    if not counted:
        return None
    (best, votes), *others = counted.most_common()
    return best if votes > sum(n for _value, n in others) else None


def found_for(descriptions):
    """(packs, cards a pack) the shops' descriptions agree on, each None when they do not."""
    descriptions = list(descriptions)
    return agreed(packs_in(text) for text in descriptions), agreed(cards_in(text) for text in descriptions)


def refresh_contents(dry_run=False):
    """Keep each active product's packs_found and cards_found from its live UK shop listings' descriptions. One read
    of the descriptions, streamed, and one write for the products that changed. Returns how many changed."""
    from .models import Listing, Product
    from .pricing import MARKETPLACES

    by_product = {}
    rows = (Listing.objects.live().exclude(retailer__source_type__in=MARKETPLACES).exclude(description="")
            .filter(product__is_active=True).order_by().values_list("product_id", "description"))
    for product_id, description in rows.iterator(chunk_size=2000):
        by_product.setdefault(product_id, []).append((packs_in(description), cards_in(description)))
    changed = []
    for product in Product.objects.active().only("pk", "packs_found", "cards_found"):
        given = by_product.get(product.pk, [])
        now = (agreed(packs for packs, _cards in given), agreed(cards for _packs, cards in given))
        if (product.packs_found, product.cards_found) != now:
            product.packs_found, product.cards_found = now
            changed.append(product)
    if not dry_run:
        Product.objects.bulk_update(changed, ["packs_found", "cards_found"], batch_size=500)
    return len(changed)
