"""
Sports cards: each product filed under its product line, named the way collectors and shops name one: brand,
line and league ("Panini Prizm Premier League", "Topps Match Attax Extra", "Panini Donruss Optic NFL"). Every
season of a line is one set, and each product's own name keeps its season: a line a season would leave most sets
holding one product. No release source lists sports card releases, so the sets come from the products' own names
(releases.attach_sets hands the game to attach). The owner: "can we not class these as sports cards and
then classify them by their proper product line as it feels they are just mashed up together."

A name that names no line this file knows is left with no set rather than guessed at. A line, league or brand
that shops start selling is added to LINES, LEAGUES or BRANDS below.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger(__name__)

GAME = "football"            # the game's address, kept from when it held football cards only
NAME, SHORT = "Sports cards", "Sports"
OLD_NAMES = ("Football cards",)

# The sport a name is for, when it is not football (soccer). Its league word stands in for a league the name
# does not give, so "Panini Prizm NFL 2024" is never mistaken for Panini Prizm's football sets.
SPORTS = [
    ("NFL", re.compile(r"\(nfl\)|\bnfl\b|american football", re.I)),
    ("WNBA", re.compile(r"\bwnba\b", re.I)),
    ("NBA", re.compile(r"\(nba\)|\bnba\b|basketball", re.I)),
    ("MLB", re.compile(r"\(mlb\)|\bmlb\b|baseball", re.I)),
    ("NASCAR", re.compile(r"nascar|\bracing\b", re.I)),
]

BRANDS = [
    ("Panini", r"\bpanini'?s?\b"),
    ("Topps", r"\btopps\b"),
    ("Futera", r"\bfutera\b"),
    ("Leaf", r"\bleaf\b"),
    ("Wild Card", r"\bwild ?card\b"),
    ("Onyx", r"\bonyx\b"),
    ("Club Legacyz", r"\bclub legacyz\b"),
]
BRANDS = [(brand, re.compile(pattern, re.I)) for brand, pattern in BRANDS]


@dataclass(frozen=True)
class Line:
    brand: str
    name: str
    pattern: re.Pattern
    alone: bool      # the line's own words name its brand: "Match Attax" is Topps without "Topps" in the name
    football: bool   # made only for football, so its set needs no "Football" to tell it from another sport's


def line(brand, name, pattern=None, alone=False, football=False):
    return Line(brand, name, re.compile(pattern or rf"\b{re.escape(name)}\b", re.I), alone, football)


# Within a brand, a longer line comes before the line its name starts with: Donruss Optic before Donruss.
# A line whose name is an everyday word ("One", "Black", "Select") counts only beside its brand's name.
LINES = [
    line("Panini", "Adrenalyn XL Plus", r"\badrenalyn xl plus\b", alone=True, football=True),
    line("Panini", "Adrenalyn XL", r"\badrenalyn\b", alone=True, football=True),
    line("Panini", "Top Class", alone=True),
    line("Panini", "Prizm Draft Picks", r"\bprizm draft picks?\b", alone=True),
    line("Panini", "Prizm No Huddle", alone=True),
    line("Panini", "Prizm", alone=True),
    line("Panini", "Clearly Donruss", alone=True),
    line("Panini", "Donruss Optic", alone=True),
    line("Panini", "Donruss Elite", alone=True),
    line("Panini", "Donruss", alone=True),
    line("Panini", "Contenders Optic"),
    line("Panini", "Contenders"),
    line("Panini", "Select Draft Picks", r"\bselect draft picks?\b"),
    line("Panini", "Select"),
    line("Panini", "Mosaic No Huddle", alone=True),
    line("Panini", "Mosaic", alone=True),
    line("Panini", "Chronicles"),
    line("Panini", "Obsidian", alone=True),
    line("Panini", "Immaculate", alone=True),
    line("Panini", "Impeccable", alone=True),
    line("Panini", "National Treasures", alone=True),
    line("Panini", "Noir"),
    line("Panini", "Absolute"),
    line("Panini", "Certified"),
    line("Panini", "Encore"),
    line("Panini", "Gold Standard"),
    line("Panini", "Illusions"),
    line("Panini", "Legacy"),
    line("Panini", "Limited"),
    line("Panini", "Luminance"),
    line("Panini", "Origins"),
    line("Panini", "Phoenix"),
    line("Panini", "Photogenic"),
    line("Panini", "Plates & Patches", r"\bplates (?:&|and) patches\b"),
    line("Panini", "Playbook"),
    line("Panini", "Prestige"),
    line("Panini", "Revolution"),
    line("Panini", "Rookies & Stars", r"\brookies (?:&|and) stars\b"),
    line("Panini", "Score"),
    line("Panini", "Spectra"),
    line("Panini", "XR"),
    line("Panini", "Zenith"),
    line("Panini", "Eternity", alone=True),
    line("Panini", "Pitch Kings"),
    line("Panini", "Black"),
    line("Panini", "One"),
    line("Topps", "Match Attax Extra", alone=True, football=True),
    line("Topps", "Match Attax 101", alone=True, football=True),
    line("Topps", "Match Attax", alone=True, football=True),
    line("Topps", "Merlin Chrome", r"\bmerlin chrome\b|\bchrome merlin\b", alone=True),
    line("Topps", "Merlin", alone=True),
    line("Topps", "Stadium Club Chrome", alone=True),
    line("Topps", "Stadium Club", alone=True),
    line("Topps", "Chrome Sapphire", r"\bchrome\b.*\bsapphire\b"),
    line("Topps", "Chrome Black"),
    line("Topps", "Chrome"),
    line("Topps", "Finest"),
    line("Topps", "Museum Collection", r"\bmuseum\b"),
    line("Topps", "Debut Edition"),
    line("Topps", "Flagship"),
    line("Topps", "Pro Debut"),
    line("Topps", "Gold"),
    line("Topps", "Inception"),
    line("Topps", "Jogaço", r"\bjoga[cç]o\b"),
    line("Topps", "Lineage"),
    line("Topps", "Pristine"),
    line("Topps", "Royalty"),
    line("Topps", "Resurgence"),
    line("Topps", "Signature Class"),
    line("Topps", "Vernissage"),
    line("Topps", "Més Que Un Club", r"\bm[eé]s que un club\b", football=True),
    line("Topps", "Collector Tins", r"\bcollectors? tins?\b", football=True),
    line("Futera", "Club Unique", football=True),
    line("Futera", "Unique XL", football=True),
    line("Futera", "Nostalgia Unique", r"\bnostalgia\b", football=True),
    line("Futera", "World Football FX", football=True),
    line("Futera", "Multi Sport FX", football=True),
    line("Futera", "World Football Unique", r"\bworld football\b.*\bunique\b|\bunique world football\b", football=True),
    line("Futera", "Club Collection", r"\bclub collection\b", football=True),
    line("Leaf", "Pro Set"),
    line("Leaf", "Signature Series"),
    line("Leaf", "Goal", r"\bgoal\b"),
    line("Leaf", "Vivid"),
    line("Leaf", "Metal"),
    line("Wild Card", "Alumination", alone=True),
    line("Wild Card", "WildChrome", r"\bwild ?chrome\b", alone=True),
    line("Wild Card", "Super Chrome"),
    line("Onyx", "Vintage"),
    line("Club Legacyz", "Icons", football=True),
]

# Leagues and competitions, in the words a set name uses. A name that holds two takes the first listed here, so
# a longer one comes before the one its words contain: Women's World Cup before World Cup. Topps's Champions
# League lines took the name UEFA Club Competitions in 2022/23, so both are one line.
LEAGUES = [
    ("Women's Champions League", r"\buwcl\b|\bwomen'?s champions league\b"),
    ("UEFA Club Competitions", r"\buefa club competitions?\b|\bucc\b|\buefa competitions\b"
                               r"|\b(?:uefa )?champion'?s league\b"),
    ("Road to Nations League Final", r"\broad to (?:uefa )?nations league(?: final)?\b"),
    ("Road to Euro", r"\broad to (?:uefa )?euros?\b"),
    ("Euro", r"\b(?:uefa )?euros?\b"),
    ("Club World Cup", r"\b(?:fifa )?club world cup\b"),
    ("Women's World Cup", r"\b(?:fifa )?women'?s world cup\b"),
    ("Road to World Cup", r"\broad to (?:the )?(?:fifa )?world cup\b"),
    ("World Cup", r"\b(?:fifa )?world cup\b"),
    ("FIFA 365", r"\bfifa 365\b"),
    ("FIFA", r"\bfifa\b"),
    ("Premier League", r"\b(?:english )?premier league\b|\bepl\b"),
    ("EFL", r"\befl\b"),
    ("SPFL", r"\bspfl\b"),
    ("WSL", r"\bwsl\b|\bwls\b|\bwomen'?s super league\b"),
    ("Bundesliga", r"\bbundesliga\b"),
    ("LaLiga", r"\bla ?liga\b"),
    ("Serie A", r"\bserie a\b"),
    ("MLS", r"\bmls\b|\bmajor league soccer\b"),
    ("J League", r"\bj[ .-]?league\b"),
    ("Copa", r"\bcopa\b"),
]
LEAGUES = [(league, re.compile(pattern, re.I)) for league, pattern in LEAGUES]

def tidy(name):
    return re.sub(r"\s+", " ", name.replace("’", "'").replace("‘", "'")).strip()


def brand_of(text):
    return next((brand for brand, pattern in BRANDS if pattern.search(text)), None)


def line_in(text, brand):
    """The first line the name holds: one of its brand's, or with no brand named, one that names its brand."""
    for row in LINES:
        if (row.brand == brand if brand else row.alone) and row.pattern.search(text):
            return row
    return None


def league_in(text):
    return next((league for league, pattern in LEAGUES if pattern.search(text)), "")


def set_name(name):
    """The product line a sports card product belongs to, as a set name, or None when its name does not say."""
    text = tidy(name)
    brand = brand_of(text)
    found = line_in(text, brand)
    sport = next((sport for sport, pattern in SPORTS if pattern.search(text)), None)
    league = sport or league_in(text)
    if found is None and not league:
        return None
    brand = brand or (found.brand if found else None)
    if brand is None:
        return None
    if not league and not found.football:
        league = "Football"
    return " ".join(part for part in (brand, found.name if found else "", league) if part)


def attach(game, product_ids=None, now=None):
    """File the game's products that have no set under the set their product line names, adding the set when the
    site does not have it yet. Returns how many were filed. A set the owner renamed keeps its address, so it is
    found by its address."""
    from .models import Product, ProductSet

    now = now or timezone.now()
    products = Product.objects.filter(game=game, product_set__isnull=True)
    if product_ids is not None:
        products = products.filter(pk__in=product_ids)
    moves = defaultdict(list)
    names = {}
    for pk, name in products.values_list("pk", "name"):
        found = set_name(name)
        if found:
            moves[slugify(found)].append(pk)
            names[slugify(found)] = found
    if not moves:
        return 0
    sets = dict(ProductSet.objects.filter(game=game, slug__in=list(moves)).values_list("slug", "pk"))
    moved, added = 0, []
    for slug, pks in moves.items():
        with transaction.atomic():
            if slug not in sets:
                sets[slug] = ProductSet.objects.create(game=game, name=names[slug][:120], slug=slug).pk
                added.append(names[slug])
            moved += Product.objects.filter(pk__in=pks, product_set__isnull=True).update(
                product_set_id=sets[slug], updated_at=now)
    if added:
        logger.info("Added %s sports card set%s: %s%s", len(added), "" if len(added) == 1 else "s",
                    ", ".join(added[:10]), ", ..." if len(added) > 10 else "")
    if moved:
        logger.info("Filed %s sports card product%s under their product lines", moved, "" if moved == 1 else "s")
        from .signals import clear_list_caches

        clear_list_caches(force=True)
    return moved


def rename_game():
    """Give the game its sports name where it still has the football one; a name the owner chose is kept."""
    from .models import Game

    return Game.objects.filter(slug=GAME, name__in=OLD_NAMES).update(name=NAME, short_name=SHORT)
