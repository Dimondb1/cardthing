"""
What each game calls its sealed products.

The catalogue keeps one set of product types, but a Magic player looks for a
"Play booster box" and a Yu-Gi-Oh player for a "Structure deck". Each game
lists its types in the order a shop would, with the name its players use.
A game not listed gets the plain names.
"""

DEFAULT = [
    ("booster_box", "Booster box"),
    ("booster_pack", "Booster pack"),
    ("elite_trainer_box", "Elite Trainer Box"),
    ("bundle", "Bundle"),
    ("collection_box", "Collection box"),
    ("deck", "Deck"),
    ("tin", "Tin"),
    ("gift_set", "Gift set"),
    ("collector_booster_box", "Collector booster box"),
    ("collector_booster_pack", "Collector booster pack"),
    ("other", "Other"),
]

GAME_TYPES = {
    "pokemon": [
        ("booster_box", "Booster box"),
        ("elite_trainer_box", "Elite Trainer Box"),
        ("bundle", "Booster bundle"),
        ("booster_pack", "Booster pack"),
        ("collection_box", "Collection box"),
        ("tin", "Tin"),
        ("deck", "Battle deck"),
        ("gift_set", "Gift set"),
        ("other", "Other"),
    ],
    "magic-the-gathering": [
        ("booster_box", "Play booster box"),
        ("collector_booster_box", "Collector booster box"),
        ("bundle", "Bundle"),
        ("deck", "Commander deck"),
        ("booster_pack", "Play booster pack"),
        ("collector_booster_pack", "Collector booster pack"),
        ("collection_box", "Box set"),
        ("gift_set", "Gift bundle"),
        ("other", "Other"),
    ],
    "one-piece": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Starter deck"),
        ("collection_box", "Premium collection"),
        ("bundle", "Double pack set"),
        ("other", "Other"),
    ],
    "yu-gi-oh": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Structure deck"),
        ("tin", "Tin"),
        ("collection_box", "Collector's set"),
        ("bundle", "Blister"),
        ("other", "Other"),
    ],
    "lorcana": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Starter deck"),
        ("collection_box", "Illumineer's Trove"),
        ("gift_set", "Gift set"),
        ("bundle", "Bundle"),
        ("other", "Other"),
    ],
    "star-wars-unlimited": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Starter deck"),
        ("collection_box", "Prerelease box"),
        ("other", "Other"),
    ],
    "flesh-and-blood": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Blitz or Armory deck"),
        ("collection_box", "Collection"),
        ("other", "Other"),
    ],
    "digimon": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Starter deck"),
        ("other", "Other"),
    ],
    "dragon-ball": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Starter deck"),
        ("collection_box", "Premium pack set"),
        ("other", "Other"),
    ],
    "riftbound": [
        ("booster_box", "Booster box"),
        ("booster_pack", "Booster pack"),
        ("deck", "Champion deck"),
        ("collection_box", "Proving grounds"),
        ("other", "Other"),
    ],
    "football": [
        ("booster_box", "Hobby or retail box"),
        ("booster_pack", "Pack"),
        ("tin", "Tin"),
        ("collection_box", "Starter pack or multipack"),
        ("bundle", "Multipack"),
        ("other", "Other"),
    ],
}


def type_choices(game_slug=None, present=None):
    """(code, label) pairs for the filter, in the game's order.

    ``present`` limits the list to types the game's catalogue actually holds,
    so a filter never offers a type with nothing behind it.
    """
    choices = GAME_TYPES.get(game_slug, DEFAULT)
    if present is not None:
        choices = [(code, label) for code, label in choices if code in present]
    return choices


def type_label(game_slug, code):
    """The name a game's players use for a product type."""
    for known, label in GAME_TYPES.get(game_slug, DEFAULT):
        if known == code:
            return label
    for known, label in DEFAULT:
        if known == code:
            return label
    return code.replace("_", " ").capitalize()
