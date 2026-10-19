"""
The release radar: sets and release dates announced by publishers and community databases, and set
codes that turn up in shop titles before any set is known.

Each source in SOURCES is free, needs no key and is not a retailer. It is read on its own schedule
(scan, run by the background reader and by scan_releases) and turned into ReleaseSignal rows by one
pure parser per source. accept() records every signal as a Release row and decides what may be
published:

- a set is added, and its date written, when the source is the publisher's own with a full date, or
  when two different sources agree on the same day;
- a month-only date is kept on the Release and never written to a set;
- a lone community source and every shop title wait on the Things to check page for a tap;
- a date the owner set by hand is never changed, and a source may only change a date it set itself
  (or one a community source set, when it is the publisher);
- sources more than a day apart are shown on the Things to check page with a button per date.

No product is ever created from a release, and nothing is published that no source gave.

Politeness: every request names the site in its User-Agent (fabtcg.com answers only a browser one);
Scryfall is read once a day and the rest every 6 hours; JSON sources wait 100 ms between requests
and web pages a second; all web pages together stay under 25 a day.
"""

import html
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import NamedTuple

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.text import slugify

from .checks import DATE_GAP_DAYS
from . import languages
from .classify import find_game, language_of
from .models import Game, Listing, Product, ProductSet, Release, ReleaseSourceState

logger = logging.getLogger(__name__)

OWNER = "owner"
SHOP_PREFIX = "shop:"
JSON, HTML = "json", "html"
DAY, MONTH, NONE = Release.Precision.DAY, Release.Precision.MONTH, Release.Precision.NONE

# Web pages fetched from every page-reading source together, per day.
HTML_PAGES_PER_DAY = 25
# Seconds per request: generous where the source asks for nothing in particular.
TIMEOUT = 20
# TCGdex lists sets without dates, so the newest are opened one by one, at most this many per read.
TCGDEX_DETAILS = 40
TCGDEX_SET_URL = "https://api.tcgdex.net/v2/en/sets/{id}"
# pokemon.com news articles opened per read, after the index.
POKEMON_ARTICLES = 3
DIGIMON_URL = "https://world.digimoncard.com/products/"
# Riot's Riftbound site (it answers from playriftbound.com, where riftbound.leagueoflegends.com sends it).
RIFTBOUND_URL = "https://riftbound.leagueoflegends.com/en-us/card-gallery/"
RIFTBOUND_NEWS = "https://riftbound.leagueoflegends.com/en-us/news/"
# A news line's own link can leave the site (a video, the merch store); the date is still the site's, so the
# row then points at the news page where the line was read.
RIFTBOUND_HOSTS = ("riftbound.leagueoflegends.com", "playriftbound.com")
# Riftbound set overview articles opened per read, after the gallery and the news page.
RIFTBOUND_ARTICLES = 2
# The radar is about what is coming: a set released longer ago than this is recorded but changes nothing.
RECENT_DAYS = 180
# A source with no good read for this long is shown on the Things to check page.
STALE_SOURCE_DAYS = 14
# A source never read is planned as if it were this many minutes overdue.
NEVER_READ_MINUTES = 60
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


@dataclass(frozen=True)
class Source:
    name: str
    label: str
    game: str
    url: str
    kind: str
    official: bool
    every_hours: int
    pause: float
    accept: str = "application/json"
    browser: bool = False   # the site refuses anything but a browser's User-Agent
    pages: tuple = ()   # further pages read after url, while the daily page allowance lasts
    terms: str = ""
    shop_codes: bool = True   # its set codes are the ones shops write, so a set added from it carries one

    def headers(self):
        agent = BROWSER_USER_AGENT if self.browser else user_agent()
        return {"User-Agent": agent, "Accept": self.accept}


def user_agent():
    return f"RipRaptor release check (+{settings.RIPRAPTOR_SITE_URL})"


HTML_ACCEPT = "text/html,application/xhtml+xml"

SOURCES = [
    Source("scryfall_sets", "Scryfall", "magic-the-gathering", "https://api.scryfall.com/sets", JSON, True, 24, 0.1,
           terms="Accurate User-Agent and Accept headers, at most 10 requests a second, cache for 24 hours."),
    Source("tcgdex_sets", "TCGdex", "pokemon",
           "https://api.tcgdex.net/v2/en/sets?sort:field=releaseDate&sort:order=DESC"
           "&pagination:page=1&pagination:itemsPerPage=40", JSON, False, 6, 0.1,
           terms="No published limit; 100 ms between requests and at most 40 set pages a read.", shop_codes=False),
    Source("pokemon_uk_news", "pokemon.com UK news", "pokemon", "https://www.pokemon.com/uk/news/", HTML, True, 6, 1.0,
           accept=HTML_ACCEPT, terms="Ordinary website terms; the index and at most 3 articles a read."),
    Source("ygoprodeck_sets", "YGOPRODeck", "yu-gi-oh", "https://db.ygoprodeck.com/api/v7/cardsets.php", JSON, False,
           6, 0.1, terms="At most 20 requests a second; store locally; images are never shown from their server."),
    Source("lorcast_sets", "Lorcast", "lorcana", "https://api.lorcast.com/v0/sets", JSON, False, 6, 0.1,
           terms="50 to 100 ms between requests."),
    Source("swudb_sets", "SWU-DB", "star-wars-unlimited", "https://api.swu-db.com/sets", JSON, False, 6, 0.1,
           terms="No published limit."),
    Source("bandai_onepiece", "One Piece Card Game site", "one-piece", "https://en.onepiece-cardgame.com/products/",
           HTML, True, 6, 1.0, accept=HTML_ACCEPT, pages=("https://en.onepiece-cardgame.com/products/?page=2",),
           terms="No API or published limit; pages 1 and 2."),
    Source("dbs_fusion", "Dragon Ball Super Fusion World site", "dragon-ball",
           "https://www.dbs-cardgame.com/fw/en/products/", HTML, True, 6, 1.0, accept=HTML_ACCEPT,
           terms="No API or published limit; page 1."),
    Source("bushiroad_vanguard", "Cardfight!! Vanguard site", "cardfight-vanguard", "https://en.cf-vanguard.com/products/",
           HTML, True, 6, 1.0, accept=HTML_ACCEPT, terms="No API or published limit; page 1."),
    Source("bushiroad_ws", "Weiss Schwarz site", "weiss-schwarz", "https://en.ws-tcg.com/products/", HTML, True, 6, 1.0,
           accept=HTML_ACCEPT, terms="No API or published limit; page 1."),
    Source("digimon_products", "Digimon Card Game site", "digimon", DIGIMON_URL, HTML, True, 24, 1.0, accept=HTML_ACCEPT,
           terms="No API or published limit; one page lists every English product."),
    Source("riftbound_sets", "Riftbound site", "riftbound", RIFTBOUND_URL, HTML, True, 24, 1.0, accept=HTML_ACCEPT,
           pages=(RIFTBOUND_NEWS,),
           terms="No API or published limit; the card gallery, the news page and at most 2 set overviews a read."),
    Source("fabtcg_coming_soon", "Flesh and Blood site", "flesh-and-blood", "https://fabtcg.com/coming-soon/", HTML,
           True, 6, 1.0, accept=HTML_ACCEPT, browser=True,
           terms="No API; answers only a browser User-Agent; name and date only."),
]
BY_NAME = {source.name: source for source in SOURCES}


@dataclass(frozen=True)
class ReleaseSignal:
    game_slug: str
    name: str
    code: str = ""
    release_date: date | None = None
    precision: str = NONE
    url: str = ""


class SourceError(Exception):
    """A source could not be read, or its answer was not what its parser expects."""


class NothingFound(SourceError):
    """The answer parsed, but held nothing the parser recognises: the page or API has changed."""


class OutOfPages(Exception):
    """Today's web page allowance is spent."""


# Dates ------------------------------------------------------------------------------------------

MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
FULL_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
               "november", "december")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
MONTH_DAY_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
US_SHORT_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*$")


def month_number(word):
    """3 for 'Mar', 'March' or 'mar.'; 9 for 'Sept'. None for anything else."""
    word = word.lower().rstrip(".")
    number = MONTHS.get(word[:3])
    if number is None:
        return None
    if word == "sept" or FULL_MONTHS[number - 1].startswith(word):
        return number
    return None


def safe_date(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def parse_date(text):
    """(date, precision) from '2026-11-20', 'November 20, 2026', 'October 24th, 2025', '6 Nov 2026' or
    'November 2026' (a month only, given as its first day with precision month). (None, none) otherwise."""
    text = " ".join(html.unescape(text or "").split())
    match = ISO_DATE.search(text)
    if match:
        found = safe_date(*match.groups())
        if found:
            return found, DAY
    for pattern, order in ((MONTH_DAY_YEAR, (3, 1, 2)), (DAY_MONTH_YEAR, (3, 2, 1))):
        for match in pattern.finditer(text):
            year, month, day = (match.group(i) for i in order)
            number = month_number(month)
            found = safe_date(year, number, day) if number else None
            if found:
                return found, DAY
    for match in MONTH_YEAR.finditer(text):
        number = month_number(match.group(1))
        if number:
            return date(int(match.group(2)), number, 1), MONTH
    return None, NONE


def parse_us_short_date(text):
    """(date, day) from SWU-DB's '11/20/26' (month first, two-digit year), or (None, none)."""
    match = US_SHORT_DATE.match(text or "")
    if not match:
        return None, NONE
    month, day, year = match.groups()
    year = int(year) + (2000 if len(year) == 2 else 0)
    found = safe_date(year, month, day)
    return (found, DAY) if found else (None, NONE)


# Text helpers -----------------------------------------------------------------------------------

SMALL_WORDS = {"of", "the", "and", "a", "an", "in", "on", "to", "for", "at", "by", "from", "or"}


def clean(text):
    """Visible text: tags dropped, entities decoded, spaces collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def shouted(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) >= 0.7 * len(letters)


def title_case(text):
    """'THE DOMINANCE OF GOD' -> 'The Dominance of God'. Text that is not in capitals is left as it is."""
    if not shouted(text):
        return text
    words = []
    for index, word in enumerate(text.split()):
        lower = word.lower()
        words.append(lower if index and lower in SMALL_WORDS else lower[:1].upper() + lower[1:])
    return " ".join(words)


def compact_code(code):
    """'OP-18' -> 'op18', 'SV8.5' -> 'sv8.5': codes compare without dashes, spaces or case."""
    return re.sub(r"[^a-z0-9.]", "", (code or "").lower())


def name_key(name):
    return slugify(name or "")[:120]


# Promotional runs, energy and token sets and play formats are listed by some sources beside real sets.
NOT_A_SET = re.compile(r"\b(?:promos?|energy|mcdonald'?s|tokens?|challenge|format|black star)\b", re.I)


def looks_like_set(name, code=""):
    return bool(name) and not NOT_A_SET.search(name) and name != code


def since_ok(signal, since):
    return since is None or signal.release_date is None or signal.release_date >= since


def load_json(body):
    try:
        return json.loads(body)
    except (TypeError, ValueError) as exc:
        raise SourceError(f"The answer was not JSON: {exc}") from exc


def as_text(body):
    return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)


# Parsers: pure, one per source ------------------------------------------------------------------

SCRYFALL_TYPES = {"expansion", "commander", "masters", "draft_innovation"}


def parse_scryfall(body, since=None):
    sets = load_json(body).get("data")
    if not sets:
        raise NothingFound("Scryfall listed no sets.")
    signals = []
    for row in sets:
        if row.get("set_type") not in SCRYFALL_TYPES or row.get("digital"):
            continue
        released, precision = parse_date(row.get("released_at") or "")
        signals.append(ReleaseSignal("magic-the-gathering", row["name"], (row.get("code") or "").upper(), released,
                                     precision, row.get("scryfall_uri") or ""))
    return [s for s in signals if since_ok(s, since)]


TCG_POCKET_ID = re.compile(r"^[A-Z]\d|^P-[A-Z]$")


def parse_tcgdex_list(body):
    """Set ids from the TCGdex list, newest first as the request sorted them, without TCG Pocket's (A1, B2a)."""
    rows = load_json(body)
    if not isinstance(rows, list) or not rows:
        raise NothingFound("TCGdex listed no sets.")
    return [row["id"] for row in rows if row.get("id") and not TCG_POCKET_ID.match(row["id"])]


def parse_tcgdex_set(body):
    row = load_json(body)
    if not isinstance(row, dict) or not row.get("id") or not row.get("name"):
        raise NothingFound("A TCGdex set page had no set.")
    if (row.get("serie") or {}).get("id") == "tcgp" or not looks_like_set(row["name"]):
        return None
    released, precision = parse_date(row.get("releaseDate") or "")
    return ReleaseSignal("pokemon", row["name"], row["id"], released, precision,
                         TCGDEX_SET_URL.format(id=urllib.parse.quote(row["id"])))


POKEMON_NEWS_LINK = re.compile(r"<a\b[^>]*href=\"((?:https://www\.pokemon\.com)?/uk/news/([a-z0-9-]+))/?\"[^>]*>(.*?)</a>",
                               re.I | re.S)
# An article about one of the card game's series. TCG Pocket and TCG Live are video games, and their slugs
# never carry a series name straight after pokemon-tcg.
POKEMON_SERIES_SLUG = re.compile(
    r"pokemon-tcg-(?:mega-evolution|scarlet-(?:and-)?violet|sword-(?:and-)?shield|sun-(?:and-)?moon)-"
)
POKEMON_EXPANSION = re.compile(
    r"Pok[e\u00e9]mon TCG:\s*([^\u2014\u2013:]+?)\s*[\u2014\u2013]\s*(.+?)"
    r"(?:\s+(?:Prerelease Events?|Prerelease|Pre-Release Events?|Expansion|Arrives.*|Is Coming.*|Coming.*|"
    r"Now Available.*))?\s*$",
    re.I,
)
# An article about one product of an expansion ('Delta Reign Booster Bundle', '... Mini Tins') names the
# expansion too, but its Release Date is that product's, and the rest of its heading is not a set.
POKEMON_PRODUCT = re.compile(
    r"\b(?:boosters?|bundles?|box(?:es)?|tins?|collections?|elite trainer|build (?:&|and) battle|decks?|"
    r"blisters?|binders?|packs?|premium|surprise|posters?|figures?|pins?|cases?|sleeves?|kits?|stadiums?|"
    r"displays?|accessor(?:y|ies)|portfolios?|calendars?)\b",
    re.I,
)
POKEMON_RELEASE_DATE = re.compile(r"Release Date\s*:?\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?\s+\d{4})", re.I)


def parse_pokemon_index(body):
    """[(article url, title)] of news articles that may name a Pokémon TCG expansion, newest first."""
    text = as_text(body)
    links = POKEMON_NEWS_LINK.findall(text)
    if not links:
        raise NothingFound("The pokemon.com news index had no articles.")
    found, seen = [], set()
    for href, slug, inner in links:
        url = urllib.parse.urljoin("https://www.pokemon.com/", href)
        title = clean(inner)
        if url in seen:
            continue
        seen.add(url)
        if POKEMON_PRODUCT.search(slug.replace("-", " ")) or POKEMON_PRODUCT.search(title):
            continue
        if POKEMON_SERIES_SLUG.search(slug) or POKEMON_EXPANSION.search(title):
            found.append((url, title))
    return found


def pokemon_expansion(heading):
    """'Delta Reign' from 'Pokémon TCG: Mega Evolution, then a long dash, Delta Reign Prerelease Event'; '' for any heading
    that names no expansion, or names one of its products."""
    match = POKEMON_EXPANSION.search(clean(heading))
    if match is None:
        return ""
    name = match.group(2).strip(" -:")
    return "" if POKEMON_PRODUCT.search(name) else name


def parse_pokemon_article(body, url=""):
    """The expansion an article announces, with its 'Release Date', or [] for any other article."""
    text = as_text(body)
    heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", text, re.I | re.S)
    if heading is None:
        raise NothingFound("A pokemon.com article had no heading.")
    name = pokemon_expansion(heading.group(1))
    if not name:
        return []
    when = POKEMON_RELEASE_DATE.search(clean(text))
    if when is None:
        return []
    released, precision = parse_date(when.group(1))
    if precision != DAY:
        return []
    return [ReleaseSignal("pokemon", name, "", released, DAY, url)]


def parse_ygoprodeck(body, since=None):
    rows = load_json(body)
    if not isinstance(rows, list) or not rows:
        raise NothingFound("YGOPRODeck listed no sets.")
    signals = []
    for row in rows:
        released, precision = parse_date(row.get("tcg_date") or "")
        if not row.get("set_name"):
            continue
        # Set images are never taken from here: YGOPRODeck bans hotlinking.
        signals.append(ReleaseSignal("yu-gi-oh", row["set_name"], row.get("set_code") or "", released, precision))
    return [s for s in signals if s.release_date is not None and since_ok(s, since)]


def parse_lorcast(body, since=None):
    rows = load_json(body).get("results")
    if not rows:
        raise NothingFound("Lorcast listed no sets.")
    signals = []
    for row in rows:
        if not looks_like_set(row.get("name"), row.get("code") or ""):
            continue
        released, precision = parse_date(row.get("released_at") or row.get("prereleased_at") or "")
        signals.append(ReleaseSignal("lorcana", row["name"], row.get("code") or "", released, precision))
    return [s for s in signals if since_ok(s, since)]


def parse_swudb(body, since=None):
    rows = load_json(body)
    if not isinstance(rows, list) or not rows:
        raise NothingFound("SWU-DB listed no sets.")
    signals = []
    for row in rows:
        if not row.get("isBaseSet") or not row.get("fullName"):
            continue
        released, precision = parse_us_short_date(row.get("releaseDate") or "")
        signals.append(ReleaseSignal("star-wars-unlimited", row["fullName"], row.get("setId") or "", released, precision))
    return [s for s in signals if since_ok(s, since)]


# 'BOOSTER PACK -THE DOMINANCE OF GOD- [OP-18]', 'EXTRA BOOSTER -ONE PIECE HEROINES EDITION vol.2- [EB-05]'
BANDAI_TITLE = re.compile(r"^(?P<kind>[^\[]*?)\s*-(?P<name>.+)-\s*\[(?P<code>[^\]]+)\]\s*$")
BANDAI_PLAIN = re.compile(r"^(?P<name>[^\[]+?)\s*\[(?P<code>[^\]]+)\]\s*$")


def bandai_set(title):
    """(name, code) from a Bandai product title."""
    match = BANDAI_TITLE.match(title) or BANDAI_PLAIN.match(title)
    if match is None:
        return title_case(title), ""
    return title_case(match.group("name").strip()), match.group("code").strip()


def parse_bandai_onepiece(body, since=None):
    text = as_text(body)
    items = re.findall(r"<li class=\"linkListColBox\" data-cat=\"([^\"]*)\">(.*?)</li>", text, re.S)
    if not items:
        raise NothingFound("The One Piece products page listed nothing.")
    signals = []
    for category, block in items:
        if category != "boosters":
            continue
        title = re.search(r"class=\"linkListColTitle\">(.*?)</h4>", block, re.S)
        if title is None:
            continue
        link = re.search(r"<a href=\"([^\"]+)\"", block)
        when = re.search(r"class=\"linkListColDate\">(.*?)</p>", block, re.S)
        released, precision = parse_date(clean(when.group(1))) if when else (None, NONE)
        name, code = bandai_set(clean(title.group(1)))
        signals.append(ReleaseSignal("one-piece", name, code, released, precision, link.group(1) if link else ""))
    return [s for s in signals if since_ok(s, since)]


def parse_dbs_fusion(body, since=None):
    text = as_text(body)
    items = re.findall(r"<a href=\"([^\"]+)\" class=\"cardLink\">.*?<h3 class=\"cardText\">(.*?)</h3>(.*?)</li>", text, re.S)
    if not items:
        raise NothingFound("The Dragon Ball Super Fusion World products page listed nothing.")
    signals, seen = [], set()
    for url, raw_title, rest in items:
        title = clean(raw_title)
        if "BOOSTER" not in title.upper() or url in seen:
            continue
        seen.add(url)
        when = re.search(r"RELEASE</dt>\s*<dd class=\"cardInfoTxt\">(.*?)</dd>", rest, re.S)
        released, precision = parse_date(clean(when.group(1))) if when else (None, NONE)
        name, code = bandai_set(title)
        signals.append(ReleaseSignal("dragon-ball", name, code, released, precision, url))
    return [s for s in signals if since_ok(s, since)]


VANGUARD_PREFIX = re.compile(r"^Cardfight!*\s*Vanguard\s*", re.I)


def parse_vanguard(body, since=None):
    text = as_text(body)
    items = re.findall(
        r"<a href=\"([^\"]+)\">\s*<span class=\"img\">.*?<div class=\"category ([^\"]*)\">.*?</div>\s*"
        r"<div class=\"title\">(.*?)</div>\s*<div class=\"release\">(.*?)</div>", text, re.S,
    )
    if not items:
        raise NothingFound("The Cardfight!! Vanguard products page listed nothing.")
    signals, seen = [], set()
    for url, category, raw_title, raw_date in items:
        if category.strip() != "booster" or url in seen:
            continue
        seen.add(url)
        code = re.search(r"\u3010([^\u3011]+)\u3011", raw_title)
        title = clean(re.sub(r"\u3010[^\u3011]*\u3011", " ", raw_title))
        title = VANGUARD_PREFIX.sub("", title)
        # 'Booster Pack 16: Parallactic Dawn' -> 'Parallactic Dawn'
        name = title.split(":", 1)[1].strip() if ":" in title else title
        released, precision = parse_date(clean(raw_date))
        signals.append(ReleaseSignal("cardfight-vanguard", name, code.group(1).strip() if code else "", released,
                                     precision, url))
    return [s for s in signals if since_ok(s, since)]


WS_KINDS = ("Booster Pack", "Premium Booster")


def parse_weiss(body, since=None):
    text = as_text(body)
    items = re.findall(
        r"<a href=\"([^\"]+)\">\s*<div class=\"p-products__thumbnail\">.*?p-products__category\">(.*?)</p>.*?"
        r"p-products__ttl\">(.*?)</p>.*?p-products_release-date\">(.*?)</p>", text, re.S,
    )
    if not items:
        raise NothingFound("The Weiss Schwarz products page listed nothing.")
    signals, seen = [], set()
    for url, category, raw_title, raw_date in items:
        category = clean(category)
        if category not in WS_KINDS or url in seen:
            continue
        seen.add(url)
        title = clean(raw_title)
        name = title[len(category):].strip() if title.lower().startswith(category.lower()) else title
        released, precision = parse_date(clean(raw_date))
        signals.append(ReleaseSignal("weiss-schwarz", name, "", released, precision, url))
    return [s for s in signals if since_ok(s, since)]


def parse_fabtcg(body, since=None):
    text = as_text(body)
    items = re.findall(r"<a\s+href=\"([^\"]+)\"[^>]*class=\"fl-link-card-ssr.*?<h3>(.*?)</h3>\s*(?:<p>(.*?)</p>)?",
                       text, re.S)
    if not items:
        raise NothingFound("The Flesh and Blood coming soon page listed nothing.")
    signals = []
    for url, raw_name, raw_date in items:
        name = clean(raw_name)
        # Decks for one hero are products, not sets.
        if not name or re.search(r"\bdeck\b", name, re.I):
            continue
        released, precision = parse_date(clean(raw_date))
        signals.append(ReleaseSignal("flesh-and-blood", name, "", released, precision, url))
    return [s for s in signals if since_ok(s, since)]


# Digimon: every English product on one page, each '<article>' with its kind ('Booster Pack'), its title
# ('DIGIMON CARD GAME EXTRA BOOSTER CHIVALROUS XIII [EX-13]') and its release date, or a month for Premium Bandai.
# Boosters and starter decks are sets; of the Premium Bandai products only the Limited Card Packs (LM-) are, the
# rest are binders, sleeves and gift sets.
DIGIMON_KINDS = {"Booster Pack", "Starter Deck", "Advanced Deck"}
DIGIMON_LIMITED = "LM-"
DIGIMON_PREFIX = re.compile(r"^DIGIMON CARD GAME\s+", re.I)
# Words of the product's kind that shops leave out of the set's name: 'EX14 Malevolent VII Booster Pack'.
DIGIMON_KIND_WORDS = re.compile(r"^(?:EXTRA BOOSTER|THEME BOOSTER|ADVANCED BOOSTER|LIMITED CARD PACK)\s+(?=\S)", re.I)
# Digimon whose published names carry capitals inside the word, which the site's capitals hide.
DIGIMON_SPELLINGS = {"ulforceveedramon": "UlforceVeedramon", "ragnaloardmon": "RagnaLoardmon"}
ROMAN = re.compile(r"^(?=[IVXLC])(?:C[D]|D?C{0,3})(?:X[CL]|L?X{0,3})(?:I[XV]|V?I{0,3})$")


def digimon_name(text):
    """'CHIVALROUS XIII' -> 'Chivalrous XIII', 'DIGIMON ALYSION Yukinamon' -> 'Digimon Alysion Yukinamon': words in
    capitals are title cased, Roman numerals and words already in mixed case are left as they are, and the Digimon in
    DIGIMON_SPELLINGS keep their published capitals ('ULFORCEVEEDRAMON' -> 'UlforceVeedramon')."""
    words = []
    for index, word in enumerate(text.split()):
        if not shouted(word) or ROMAN.match(word):
            words.append(word)
            continue
        lower = word.lower()
        if lower in DIGIMON_SPELLINGS:
            words.append(DIGIMON_SPELLINGS[lower])
            continue
        words.append(lower if index and lower in SMALL_WORDS else lower[:1].upper() + lower[1:])
    return " ".join(words)


def digimon_release(block):
    """(date, precision) of the release in one product's block: its 'Release date', or 'Release date (For Other
    regions)', never the 'Pre-Release date' a week before nor the US shipping date."""
    for label, value in re.findall(r"<dl class=\"release\">\s*<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", block, re.S):
        if clean(label).lower().startswith("release date"):
            return parse_date(clean(value))
    return None, NONE


def parse_digimon(body, since=None):
    text = as_text(body)
    items = re.findall(r"<article class=\"genrecol-[^\"]*\"([^>]*)>(.*?)</article>", text, re.S)
    if not items:
        raise NothingFound("The Digimon Card Game products page listed nothing.")
    signals, seen = [], set()
    for attributes, block in items:
        kind = re.search(r"class=\"genre-[^\"]*genrename\">(.*?)</p>", block, re.S)
        title = re.search(r"class=\"prodname\">(.*?)</h4>", block, re.S)
        if kind is None or title is None:
            continue
        title = clean(title.group(1))
        code = re.search(r"\[([A-Z]{1,3}-?\d[\dA-Z-]*)\]\s*$", title)
        code = code.group(1) if code else ""
        if clean(kind.group(1)) not in DIGIMON_KINDS and not code.startswith(DIGIMON_LIMITED):
            continue
        name = DIGIMON_PREFIX.sub("", re.sub(r"\s*\[[^\]]*\]\s*$", "", title)).strip()
        name = digimon_name(DIGIMON_KIND_WORDS.sub("", name))
        if not name or (name, code) in seen:
            continue
        seen.add((name, code))
        released, precision = digimon_release(block)
        link = re.search(r"data-url=\"([^\"]+)\"", attributes)
        url = urllib.parse.urljoin(DIGIMON_URL, link.group(1)) if link else ""
        signals.append(ReleaseSignal("digimon", name, code, released, precision, url))
    return [s for s in signals if since_ok(s, since)]


# Riftbound: Riot's site is a Next.js app whose pages carry their content as JSON in a __NEXT_DATA__ script. The
# card gallery lists every set with its three-letter code (OGN Origins, SFD Spiritforged); the news page lists
# every article with its date and a line about it; a set's overview article ('The Vendetta Overview') gives its
# release date. Neither the site nor its articles use the 'RB 03' numbers some shops write, so those are not codes.
NEXT_DATA = re.compile(r"<script id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", re.S)
# Dates as the articles write them: 'October 23, 2026', 'July 31', 'Oct. 23', '5/8' (Riot's US pages put the
# month first).
RB_DATE = r"(?:[A-Z][a-z]{2,8}\.? \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
# The English release, in the words the articles use, most particular first: 'the English version will release on
# 5/8', 'launches in English on October 31, 2025', 'Release: October 23, 2026', 'releases on 2/13'.
RB_RELEASE = [re.compile(pattern, re.I) for pattern in (
    rf"\bEnglish (?:version|language release|release) will (?:release|launch|be released|be out) on ({RB_DATE})",
    rf"\b(?:release[sd]?|launch(?:es|ed)?|be released|be out) in English on ({RB_DATE})",
    rf"\bRelease\s*:\s*({RB_DATE})",
    rf"\b(?:release[sd]?|launch(?:es|ed)?|drops?|dropping)(?: worldwide)?(?: on)? ({RB_DATE})",
)]
# A release in another language or country is not the English one.
RB_OTHER_LANGUAGE = re.compile(r"\b(?:Chinese|China|French|France|Korean|Korea|Japanese|Japan)\b")
RB_ENGLISH = re.compile(r"\bEnglish\b")
# A date with no year is the next one on or after the article, give or take this many days.
RB_YEAR_SLACK = 60


def next_data(body):
    """The JSON a Next.js page carries, or SourceError."""
    match = NEXT_DATA.search(as_text(body))
    if match is None:
        raise NothingFound("The page carried no __NEXT_DATA__.")
    return load_json(match.group(1))


def blades(body):
    return ((next_data(body).get("props") or {}).get("pageProps") or {}).get("page") or {}


def parse_riftbound_sets(body):
    """[(code, name)] from the card gallery's set list, in its order."""
    page = blades(body)
    found = []
    for blade in page.get("blades") or []:
        for item in ((blade.get("sets") or {}).get("items") or []) if isinstance(blade, dict) else []:
            code, name = (item.get("id") or "").strip(), clean(item.get("name") or "")
            if code and name and (code, name) not in found:
                found.append((code, name))
    if not found:
        raise NothingFound("The Riftbound card gallery listed no sets.")
    return found


class RiftboundArticle(NamedTuple):
    title: str
    text: str
    url: str
    published: date | None


def published_on(value):
    """The day of a timestamp such as '2026-07-07T01:00:00.000Z', or None."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    return safe_date(*match.groups()) if match else None


def parse_riftbound_news(body):
    """[RiftboundArticle] from the news page, newest first, each with its line rather than its whole text."""
    articles = []
    for blade in blades(body).get("blades") or []:
        for item in (blade.get("items") or []) if isinstance(blade, dict) else []:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            url = (((item.get("action") or {}).get("payload") or {}).get("url")) or ""
            articles.append(RiftboundArticle(clean(item["title"]), clean((item.get("description") or {}).get("body")),
                                             urllib.parse.urljoin(RIFTBOUND_URL, url) if url else "",
                                             published_on(item.get("publishedAt"))))
    if not articles:
        raise NothingFound("The Riftbound news page listed no articles.")
    return sorted(articles, key=lambda a: a.published or date.min, reverse=True)


def parse_riftbound_article(body, url=""):
    """A RiftboundArticle with the article's whole text."""
    page = blades(body)
    parts = []
    for blade in page.get("blades") or []:
        rich = (blade.get("richText") or {}).get("body") if isinstance(blade, dict) else None
        if rich:
            parts.append(clean(rich))
    if not parts:
        raise NothingFound("The Riftbound article had no text.")
    return RiftboundArticle(clean(page.get("title") or ""), " ".join(parts), url,
                            published_on(page.get("displayedPublishDate")))


def article_date(text, published):
    """(date, precision) of the English release a text gives, or (None, none). A date with no year takes the
    article's, or the next year's when that would put it more than RB_YEAR_SLACK days before the article; with no
    article date, a date with no year is not used."""
    for pattern in RB_RELEASE:
        for match in pattern.finditer(text or ""):
            around = text[max(0, match.start() - 60):match.end() + 30]
            if RB_OTHER_LANGUAGE.search(around) and not RB_ENGLISH.search(around):
                continue
            found = article_day(match.group(1), published)
            if found:
                return found, DAY
    return None, NONE


def article_day(text, published):
    slash = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if slash:
        month, day, year = int(slash.group(1)), int(slash.group(2)), slash.group(3)
        year = (int(year) + (2000 if len(year) == 2 else 0)) if year else None
    else:
        words = re.match(r"([A-Za-z]+)\.? (\d{1,2})(?:st|nd|rd|th)?(?:, (\d{4}))?", text)
        month = month_number(words.group(1)) if words else None
        if not month:
            return None
        day, year = int(words.group(2)), int(words.group(3)) if words.group(3) else None
    if year is not None:
        return safe_date(year, month, day)
    if published is None:
        return None
    found = safe_date(published.year, month, day)
    if found and found < published - timedelta(days=RB_YEAR_SLACK):
        found = safe_date(published.year + 1, month, day)
    return found


def names_set(article, name, names):
    """Whether an article's title and line name this set and no other."""
    text = f"{article.title} {article.text}"

    def named(other):
        return re.search(rf"(?<![A-Za-z]){re.escape(other)}(?![A-Za-z])", text) is not None
    return named(name) and not any(named(other) for other in names if other != name and name not in other)


# Reading ----------------------------------------------------------------------------------------

def fetch_url(url, headers=None, timeout=TIMEOUT):
    """The body at ``url``, asked for once with ``headers``. Raises SourceError."""
    import http.client

    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise SourceError(f"{url} answered {exc.code}") from exc
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise SourceError(f"Could not reach {url}: {exc}") from exc


def take_page(name, now):
    """Count one web page from source ``name`` against today's allowance, or raise OutOfPages.

    The check and the count are one transaction, and the database takes its write lock when the
    transaction begins (transaction_mode IMMEDIATE), so the background reader and a scan_releases run by
    hand at the same moment cannot both spend the last page.
    """
    today = local_today(now)
    with transaction.atomic():
        state = ReleaseSourceState.objects.get_or_create(name=name)[0]
        if pages_left(now) <= 0:
            raise OutOfPages()
        if state.pages_day != today:
            state.pages_day, state.pages_today = today, 0
        state.pages_today += 1
        state.save(update_fields=["pages_day", "pages_today"])


class Reader:
    """Every request one scan makes: its headers, the pause between requests and the page allowance.

    Each web page is counted before it is fetched, by ``take_page`` (raising OutOfPages when the day's
    pages are spent), dry runs included: they fetch the same pages.
    """

    def __init__(self, source, fetch, sleep, take_page=None, stop=None, details=TCGDEX_DETAILS):
        self.source, self.fetch, self.sleep = source, fetch, sleep
        self.details = details   # TCGdex set pages opened at most
        self.take_page = take_page or (lambda: None)
        self.stop = stop or (lambda: False)
        self.requests = 0
        self.pages = 0

    def get(self, url):
        if self.source.kind == HTML:
            self.take_page()
            self.pages += 1
        if self.requests:
            self.sleep(self.source.pause)
        self.requests += 1
        return self.fetch(url, headers=self.source.headers())


@dataclass
class Known:
    """What this source has already told us: set ids with a date, and articles already read with one."""

    dated_codes: set = field(default_factory=set)
    dated_urls: set = field(default_factory=set)
    dated_names: set = field(default_factory=set)

    @classmethod
    def load(cls, source_name):
        known = cls()
        for code, url, name, released in Release.objects.filter(source=source_name).values_list(
                "code", "source_url", "name", "release_date"):
            if released is not None:
                known.dated_codes.add(code)
                known.dated_urls.add(url)
                known.dated_names.add(name_key(name))
        return known


def read_tcgdex(source, reader, since, known):
    signals = []
    ids = [i for i in parse_tcgdex_list(reader.get(source.url)) if i not in known.dated_codes][:reader.details]
    for set_id in ids:
        if reader.stop():
            break
        signal = parse_tcgdex_set(reader.get(TCGDEX_SET_URL.format(id=urllib.parse.quote(set_id))))
        if signal is not None:
            signals.append(signal)
    return signals


def read_pokemon(source, reader, since, known):
    """The expansions in the newest articles. An expansion already dated from one article is not read
    again from another (a prerelease article and an announcement), so its date cannot swap between them."""
    signals = []
    done = set(known.dated_names)
    articles = []
    for url, title in parse_pokemon_index(reader.get(source.url)):
        key = name_key(pokemon_expansion(title))
        if url not in known.dated_urls and not (key and key in done):
            articles.append(url)
    for url in articles[:POKEMON_ARTICLES]:
        if reader.stop():
            break
        try:
            found = parse_pokemon_article(reader.get(url), url)
        except OutOfPages:
            break
        for signal in found:
            if name_key(signal.name) not in done:
                done.add(name_key(signal.name))
                signals.append(signal)
    return signals


def on_riftbound_site(url):
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in RIFTBOUND_HOSTS)


def read_riftbound(source, reader, since, known):
    """Every set in the card gallery, with the English release date the news page's lines give it, or else its
    overview article ('The Unleashed Overview'), at most RIFTBOUND_ARTICLES a read. A set this source has already
    dated, and no line dates now, is left out, so a date it gave is never taken away."""
    sets = parse_riftbound_sets(reader.get(source.url))
    news_url = source.pages[0] if source.pages else RIFTBOUND_NEWS
    news = parse_riftbound_news(reader.get(news_url))
    names = [name for _code, name in sets]
    signals, opened = [], 0
    for code, name in sets:
        released, precision, url = None, NONE, ""
        for article in news:
            if names_set(article, name, names):
                released, precision = article_date(f"{article.title}. {article.text}", article.published)
                if released:
                    url = article.url if on_riftbound_site(article.url) else news_url
                    break
        if released is None and code in known.dated_codes:
            continue
        overview = next((a for a in news if a.title.lower() == f"the {name} overview".lower() and a.url), None)
        if released is None and overview is not None and opened < RIFTBOUND_ARTICLES and not reader.stop():
            opened += 1
            try:
                article = parse_riftbound_article(reader.get(overview.url), overview.url)
            except OutOfPages:
                # The day's pages are spent: the set is still listed, and its date waits for the next read.
                opened = RIFTBOUND_ARTICLES
            else:
                released, precision = article_date(article.text, article.published or overview.published)
                url = overview.url if released and on_riftbound_site(overview.url) else (news_url if released else "")
        signals.append(ReleaseSignal("riftbound", name, code, released, precision, url or source.url))
    return [s for s in signals if since_ok(s, since)]


def read_pages(parse):
    def read(source, reader, since, known):
        signals = parse(reader.get(source.url), since)
        for url in source.pages:
            if reader.stop():
                break
            try:
                signals += parse(reader.get(url), since)
            except OutOfPages:
                break
            except NothingFound:
                # A later page can be empty when the publisher lists fewer products.
                break
        return signals
    return read


READERS = {
    "scryfall_sets": read_pages(parse_scryfall),
    "tcgdex_sets": read_tcgdex,
    "pokemon_uk_news": read_pokemon,
    "ygoprodeck_sets": read_pages(parse_ygoprodeck),
    "lorcast_sets": read_pages(parse_lorcast),
    "swudb_sets": read_pages(parse_swudb),
    "bandai_onepiece": read_pages(parse_bandai_onepiece),
    "dbs_fusion": read_pages(parse_dbs_fusion),
    "bushiroad_vanguard": read_pages(parse_vanguard),
    "bushiroad_ws": read_pages(parse_weiss),
    "fabtcg_coming_soon": read_pages(parse_fabtcg),
    "digimon_products": read_pages(parse_digimon),
    "riftbound_sets": read_riftbound,
}


@dataclass
class ScanResult:
    source: str
    signals: list = field(default_factory=list)
    skipped: str = ""
    error: str = ""
    requests: int = 0
    stats: Counter = field(default_factory=Counter)

    @property
    def ok(self):
        return not self.error

    def __str__(self):
        if self.skipped:
            return f"{self.source}: skipped, {self.skipped}."
        if self.error:
            return f"{self.source}: {self.error}"
        parts = [f"{len(self.signals)} found in {self.requests} request{'s' if self.requests != 1 else ''}"]
        parts += [f"{n} {what}" for what, n in sorted(self.stats.items()) if n]
        return f"{self.source}: " + ", ".join(parts) + "."


def local_today(now):
    return timezone.localtime(now).date()


def recent_cutoff(today):
    return today - timedelta(days=RECENT_DAYS)


def pages_left(now):
    """Web pages the page-reading sources may still fetch today."""
    used = ReleaseSourceState.objects.filter(pages_day=local_today(now)).aggregate(n=Sum("pages_today"))["n"] or 0
    return max(0, HTML_PAGES_PER_DAY - used)


def next_day(now):
    local = timezone.localtime(now)
    return (local + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)


def due_sources(now):
    """[(source name, minutes overdue)] of the sources due a read, most overdue first, in one query."""
    states = {s.name: s.next_at for s in ReleaseSourceState.objects.filter(name__in=BY_NAME)}
    due = []
    for source in SOURCES:
        next_at = states.get(source.name)
        if next_at is None:
            due.append((source.name, float(NEVER_READ_MINUTES)))
        elif next_at <= now:
            due.append((source.name, (now - next_at).total_seconds() / 60))
    return sorted(due, key=lambda item: -item[1])


def scan(name, fetch=None, now=None, sleep=None, dry_run=False, stop=None):
    """Read one source if it is due, and record what it says. A dry run records nothing but the web
    pages it fetched, which count against the day's allowance like any others, and it does not move the
    source's next read.

    A parser that raises, or finds nothing it recognises, leaves its error on the source and writes no rows.
    """
    source = BY_NAME[name]
    fetch = fetch or fetch_url
    sleep = sleep or time.sleep
    now = now or timezone.now()
    today = local_today(now)
    result = ScanResult(name)
    if dry_run:
        state = ReleaseSourceState.objects.filter(name=name).first() or ReleaseSourceState(name=name)
    else:
        state = ReleaseSourceState.objects.get_or_create(name=name)[0]
    if state.next_at is not None and state.next_at > now:
        result.skipped = f"next read at {timezone.localtime(state.next_at):%d %b %H:%M}"
        return result
    allowance = pages_left(now) if source.kind == HTML else None
    if allowance is not None and allowance <= 0:
        result.skipped = f"the {HTML_PAGES_PER_DAY} web pages a day are used up"
        if not dry_run:
            state.next_at = next_day(now)
            state.save(update_fields=["next_at"])
        return result
    if not dry_run:
        # Stamped before the fetch, so a read that fails or hangs is not tried again at once.
        state.next_at = now + timedelta(hours=source.every_hours)
        state.save(update_fields=["next_at"])
    reader = Reader(source, fetch, sleep, take_page=lambda: take_page(name, now), stop=stop)
    try:
        # Every set the answer lists, the back catalogue included: the release rules settle only recent ones, and
        # an older one may only file products (attach_sets).
        signals = READERS[name](source, reader, None, Known.load(name))
    except OutOfPages:
        signals = []
        result.skipped = f"the {HTML_PAGES_PER_DAY} web pages a day are used up"
    except Exception as exc:  # noqa: BLE001 - a broken page or parser records its error and writes nothing
        signals = None
        result.error = (str(exc) or exc.__class__.__name__)[:200]
    result.requests = reader.requests
    if dry_run:
        # What the radar would act on: sets inside its window, or with no date.
        result.signals = [s for s in signals or [] if since_ok(s, recent_cutoff(today))]
        return result
    # Pages were counted as they were fetched (take_page), so they are not saved from here.
    fields = ["last_error"]
    if signals is None:
        state.last_error = result.error
        logger.warning("Release source %s: %s", name, result.error)
    else:
        result.signals = signals
        result.stats = accept(source, signals, now=now)
        state.last_error = ""
        if not result.skipped:
            state.last_ok_at = now
            state.signals_found = len(signals)
            fields += ["last_ok_at", "signals_found"]
    state.save(update_fields=fields)
    return result


# Acceptance -------------------------------------------------------------------------------------

def is_official(source_name):
    source = BY_NAME.get(source_name)
    return bool(source and source.official)


def usable_code(code):
    """A code that can file a product by itself: letters and digits together, three or more characters.
    'OP-18' and 'ME03' can; 'BRO' (also a word) and Lorcast's '14' cannot."""
    compact = compact_code(code)
    return len(compact) >= 3 and re.search(r"[a-z]", compact) and re.search(r"\d", compact)


def find_set(game, name, code=""):
    """The game's set with this code, or with this name's address, or None.

    Every source is an English one, so a set whose name carries another language ('... (Japanese)'),
    which shares the English set's code but not its date, is never the one a source speaks about.
    """
    sets = ProductSet.objects.filter(game=game)
    compact = compact_code(code)
    if compact:
        for product_set in sets.exclude(code=""):
            if compact_code(product_set.code) == compact and not language_of(f"{product_set.name} {product_set.code}", game.slug):
                return product_set
    return sets.filter(slug=name_key(name)).first()


def set_for(game, name, code=""):
    """The game's set with this code, or with this name's address, created when there is none."""
    product_set = find_set(game, name, code)
    if product_set is None:
        return ProductSet.objects.create(game=game, name=name[:120], slug=name_key(name), code=(code or "")[:20])
    if code and not product_set.code:
        product_set.code = code[:20]
        product_set.save(update_fields=["code"])
    return product_set


def may_write_date(product_set, source_name):
    """Whether ``source_name`` may set this set's date: never over the owner's, nor over a date that was
    there before sources were read; only over its own, or a community source's when it is the publisher."""
    current = product_set.release_date_source
    if product_set.release_date is None:
        return current != OWNER
    if current in ("", OWNER):
        return False
    if current == source_name:
        return True
    return is_official(source_name) and not is_official(current)


def write_date(product_set, released, source_name):
    if released is None or not may_write_date(product_set, source_name):
        return False
    if product_set.release_date == released and product_set.release_date_source == source_name:
        return False
    fields = ["release_date_source"] if product_set.release_date == released else ["release_date", "release_date_source"]
    product_set.release_date = released
    product_set.release_date_source = source_name[:20]
    product_set.save(update_fields=fields)
    return True


def link_rows(product_set, game, name):
    """Every other row naming this set points at it, so it leaves the list of sets to add."""
    key = name_key(name)
    for row in Release.objects.filter(game=game, product_set__isnull=True).exclude(status=Release.Status.DISMISSED):
        if name_key(row.name) == key:
            row.product_set = product_set
            row.save(update_fields=["product_set"])


def agreeing(row):
    """Rows from other non-shop sources that give this row's set the same day."""
    others = (
        Release.objects.filter(game_id=row.game_id, release_date=row.release_date, precision=DAY)
        .exclude(source=row.source).exclude(source__startswith=SHOP_PREFIX).exclude(status=Release.Status.DISMISSED)
    )
    key = name_key(row.name)
    return [o for o in others if name_key(o.name) == key or (row.product_set_id and o.product_set_id == row.product_set_id)]


def decide(row, today, stats):
    """Add the set and publish the date when the rules allow; otherwise the row waits for the owner."""
    if row.status == Release.Status.DISMISSED or row.source.startswith(SHOP_PREFIX):
        return None
    if row.release_date is not None and row.release_date < recent_cutoff(today):
        return None
    if row.product_set_id is None:
        # A set that already exists is this row's set, even when nothing more may be published from it.
        found = find_set(row.game, row.name, row.code)
        if found is not None:
            row.product_set = found
            row.save(update_fields=["product_set"])
    if row.official:
        product_set = row.product_set or set_for(row.game, row.name, row.code)
        released = row.release_date if row.precision == DAY else None
        others = agreeing(row) if released else []
    else:
        if row.precision != DAY:
            return None
        others = agreeing(row)
        if not others:
            return None
        product_set = row.product_set or next((o.product_set for o in others if o.product_set_id), None)
        product_set = product_set or set_for(row.game, row.name, row.code)
        released = row.release_date
    created = product_set.pk is not None and row.product_set_id is None
    if write_date(product_set, released, row.source):
        stats["dates written"] += 1
    if released is not None and product_set.release_date is not None and \
            abs((product_set.release_date - released).days) > DATE_GAP_DAYS:
        # The set keeps a date this source may not change (typed by hand, or another publisher's), and
        # this source says otherwise: the row waits, filed under the set, and the two dates go to the
        # owner under Release dates to confirm.
        if row.product_set_id != product_set.pk:
            row.product_set = product_set
            row.save(update_fields=["product_set"])
        stats["dates to confirm"] += 1
        return product_set
    for other in [row, *others]:
        if other.product_set_id != product_set.pk or other.status != Release.Status.ACCEPTED:
            other.product_set = product_set
            other.status = Release.Status.ACCEPTED
            other.save(update_fields=["product_set", "status"])
    link_rows(product_set, row.game, row.name)
    if created:
        stats["sets linked"] += 1
    return product_set


QUOTES = " \"'" + chr(0x201C) + chr(0x201D)


def accept(source, signals, now=None):
    """Record each signal as a Release row and add sets and dates the rules allow. Returns counts."""
    now = now or timezone.now()
    today = local_today(now)
    stats = Counter()
    games = {g.slug: g for g in Game.objects.filter(slug__in={s.game_slug for s in signals})}
    touched = set()
    with transaction.atomic():
        rows = {(r.game_id, r.name): r for r in Release.objects.filter(source=source.name).select_related("game")}
        unchanged = []
        for signal in signals:
            game = games.get(signal.game_slug)
            if game is None:
                # No Game row: the owner has not added this game, and a source never adds one.
                stats["skipped, game not added"] += 1
                continue
            name = clean_name(signal.name)
            if not name:
                continue
            values = {"code": (signal.code or "")[:20], "release_date": signal.release_date,
                      "precision": signal.precision, "source_url": (signal.url or "")[:500],
                      "official": source.official}
            row = rows.get((game.pk, name))
            if row is None:
                row = Release.objects.create(game=game, name=name, source=source.name, first_seen_at=now,
                                             last_seen_at=now, **values)
                rows[(game.pk, name)] = row
                stats["new"] += 1
                # A newly listed set may name products already on the site (the back catalogue).
                touched.add(game.pk)
            else:
                changed = [key for key, value in values.items() if getattr(row, key) != value]
                if changed:
                    for key in changed:
                        setattr(row, key, values[key])
                    row.last_seen_at = now
                    row.save(update_fields=changed + ["last_seen_at"])
                    stats["changed"] += 1
                else:
                    unchanged.append(row.pk)
            if decide(row, today, stats) is not None:
                touched.add(game.pk)
        Release.objects.filter(pk__in=unchanged).update(last_seen_at=now)
    for game in games.values():
        if game.pk in touched:
            filed = attach_sets(game, now=now)
            if filed:
                stats["products filed"] += filed
    return stats


# The back catalogue ------------------------------------------------------------------------------
#
# The radar's rules are about sets still to come, so every set a source lists is recorded as a Release row, and a
# set released before the radar's window (RECENT_DAYS) waits there, off the Things to check page. It is a matter
# of record, so the source listing it (a publisher, or one of the long-standing card databases above) is enough
# to add it, but only when a product on the site names it, by code or whole name, with no other set or listed
# set fitting as well (attach_sets): no set is added that would stand empty, and none from a product's name
# alone. Its date is written as that source's, so a publisher's date replaces it later and the owner's is never
# changed. A row the owner dismissed stays dismissed. The listed sets also stop a product being filed under a set
# whose name is only part of the product's ('Black & White' in 'Black & White Dragons Exalted').

# Read once by file_sets --backfill: the publisher's booster listings, linked from the pages the radar reads, and
# every TCGdex set rather than the newest 40. News and coming-soon pages hold no back catalogue.
BACKFILL = {
    "tcgdex_sets": {"url": "https://api.tcgdex.net/v2/en/sets?sort:field=releaseDate&sort:order=DESC"},
    "bandai_onepiece": {"url": "https://en.onepiece-cardgame.com/products/?subcategory=boosters",
                        "pages": ("https://en.onepiece-cardgame.com/products/?subcategory=boosters&page=2",
                                  "https://en.onepiece-cardgame.com/products/?subcategory=boosters&page=3")},
    "dbs_fusion": {"url": "https://www.dbs-cardgame.com/fw/en/products/?tags=BoosterPack&page=1",
                   "pages": ("https://www.dbs-cardgame.com/fw/en/products/?tags=BoosterPack&page=2",)},
}
BACKFILL_SKIP = {"pokemon_uk_news", "fabtcg_coming_soon"}
TCGDEX_BACKFILL = 300


def clean_name(name):
    return " ".join((name or "").split()).strip(QUOTES)[:120]


def listed_rows(game, today):
    """The game's Release rows from release sources (never a shop's title) naming a set the site does not have,
    out by ``today``, one per name, publishers' first. One query.

    A row marked accepted with no set had one, and the owner deleted it in Admin: no row of that name is offered
    again, so a deleted set stays deleted."""
    rows = list(Release.objects.filter(game=game, product_set__isnull=True, release_date__lte=today)
                .exclude(status=Release.Status.DISMISSED).exclude(source__startswith=SHOP_PREFIX)
                .order_by("-official", "pk"))
    removed = {name_key(row.name) for row in rows if row.status == Release.Status.ACCEPTED}
    found, seen = [], set(removed)
    for row in rows:
        key = name_key(row.name)
        if key in seen or not looks_like_set(row.name, "") or (row.precision == MONTH and row.release_date >= today.replace(day=1)):
            continue
        seen.add(key)
        found.append(row)
    return found


def filing_code(row, shared=frozenset(), game_slug=""):
    """The row's code, when its source's codes are the ones shops write (not TCGdex's ids) and no set of
    another name has it too (``shared``, code_key spellings): YGOPRODeck gives 'Spell Ruler' and 'Spell Ruler
    (25th Anniversary Edition)' the one code SRL."""
    source = BY_NAME.get(row.source)
    code = row.code if source is None or source.shop_codes else ""
    return "" if code and code_key(code, game_slug) in shared else code


def shared_codes(game, sets):
    """The codes (code_key spellings) that sets of more than one name carry, among the game's sets (``sets``,
    (pk, name, code) rows) and every set its release sources list. One query."""
    names = defaultdict(set)
    for _pk, name, code in sets:
        if code:
            names[code_key(code, game.slug)].add(name_key(name))
    rows = (Release.objects.filter(game=game).exclude(code="").exclude(source__startswith=SHOP_PREFIX)
            .exclude(status=Release.Status.DISMISSED).values_list("name", "code", "source"))
    for name, code, source in rows:
        if BY_NAME.get(source) is None or BY_NAME[source].shop_codes:
            names[code_key(code, game.slug)].add(name_key(name))
    return frozenset(code for code, keys in names.items() if len(keys) > 1)


def add_listed_set(row, now, code=None):
    """Add the set a back-catalogue Release row names, as its source gives it, with ``code`` (its filing_code
    by default). Returns the set.

    The row gives its date only to a set of its own name: a set found by code under another name keeps its
    date."""
    code = filing_code(row) if code is None else code
    product_set = set_for(row.game, row.name, code)
    if row.precision == DAY and name_key(product_set.name) == name_key(row.name):
        write_date(product_set, row.release_date, row.source)
    row.product_set, row.status = product_set, Release.Status.ACCEPTED
    row.save(update_fields=["product_set", "status"])
    link_rows(product_set, row.game, row.name)
    logger.info("Back catalogue: added %s set %s from %s", row.game.slug, row.name, row.source)
    return product_set


def backfill(name, fetch=None, now=None, sleep=None, dry_run=False):
    """Read one source's whole list once, for its back catalogue (file_sets --backfill), record it and file the
    game's products. The source's own schedule is left alone; its web pages count against the day's allowance
    like any others. A dry run records nothing."""
    source = BY_NAME[name]
    now = now or timezone.now()
    result = ScanResult(name)
    if name in BACKFILL_SKIP:
        result.skipped = "it lists only sets still to come"
        return result
    game = Game.objects.filter(slug=source.game).first()
    if game is None:
        result.skipped = "the game is not added"
        return result
    wide = replace(source, **BACKFILL.get(name, {}))
    if wide.kind == HTML and pages_left(now) < 1 + len(wide.pages):
        result.skipped = f"not enough of the {HTML_PAGES_PER_DAY} web pages a day are left"
        return result
    reader = Reader(wide, fetch or fetch_url, sleep or time.sleep, take_page=lambda: take_page(name, now),
                    details=TCGDEX_BACKFILL)
    try:
        signals = READERS[name](wide, reader, None, Known.load(name))
    except OutOfPages:
        result.skipped = f"the {HTML_PAGES_PER_DAY} web pages a day are used up"
        return result
    except Exception as exc:  # noqa: BLE001 - a broken page or parser writes nothing
        result.error = (str(exc) or exc.__class__.__name__)[:200]
        return result
    result.requests = reader.requests
    result.signals = signals
    if not dry_run:
        result.stats = accept(source, signals, now=now)
        sets_before = ProductSet.objects.filter(game=game).count()
        result.stats["products filed"] += attach_sets(game, now=now)
        result.stats["back catalogue sets"] += ProductSet.objects.filter(game=game).count() - sets_before
    return result


# Shop titles ------------------------------------------------------------------------------------

SHOP_CODE = re.compile(
    r"(?<![A-Za-z0-9-])(OP-\d\d|EB-\d\d|FB\d\d|VGE-[A-Z]{2}-BT\d\d|SV\d+(?:\.\d)?|ME\d\d|Set \d+)(?![A-Za-z0-9])"
)
PRERELEASE_EVENT = re.compile(r"\bpre-?release event", re.I)
# Each code pattern belongs to one game, so another game's title (a 'Set 2' of anything) never raises it.
CODE_GAMES = (("OP-", "one-piece"), ("EB-", "one-piece"), ("FB", "dragon-ball"), ("VGE-", "cardfight-vanguard"),
              ("SV", "pokemon"), ("ME", "pokemon"), ("Set ", "lorcana"))
# 'Gift Set 2' or 'Starter Set 3' is a product, not Lorcana's sixth set.
NOT_SET_NUMBER = re.compile(r"\b(?:gift|starter|collector'?s?|trove|deck|bundle|box)\s+$", re.I)


def shop_code(title, game_slug):
    """The first set code in a shop title that belongs to ``game_slug``, or ''."""
    for match in SHOP_CODE.finditer(title):
        code = match.group(1)
        game = next(slug for prefix, slug in CODE_GAMES if code.startswith(prefix))
        if game != game_slug:
            continue
        if code.startswith("Set ") and NOT_SET_NUMBER.search(title[:match.start()]):
            continue
        return code
    return ""


def event_set_name(title):
    """'Pokémon - Mega Evolution: Delta Reign Pre-Release Event - Wednesday 6pm' -> 'Delta Reign'."""
    from .matching import GAME_PREFIX

    text = html.unescape(title or "")
    match = PRERELEASE_EVENT.search(text)
    if match is None:
        return ""
    text = text[:match.start()]
    for _ in range(2):
        text = GAME_PREFIX.sub("", text)
    parts = re.split(r"\s*:\s*|\s+[-\u2013|]\s+", text.strip())
    name = parts[-1].strip(" -:|") if parts else ""
    return name if len(name) >= 3 else ""


class ShopSignals:
    """Set codes in pre-order titles, and pre-release event tickets, from one shop's read.

    Each writes one pending Release (source shop:<shop>) for a code or set no set of that game has yet.
    Nothing is read from the database until an offer looks like one, so an ordinary read costs nothing.
    """

    def __init__(self, retailer):
        self.source = f"{SHOP_PREFIX}{retailer.slug}"[:40]
        # Marketplace sellers' titles are anyone's words, not a shop's listing.
        self.off = retailer.source_type in (retailer.Source.EBAY, retailer.Source.AMAZON)
        self.loaded = False
        self.seen = set()
        self.again = []

    def load(self):
        self.games = dict(Game.objects.values_list("slug", "pk"))
        self.codes = {(game_id, compact_code(code))
                      for game_id, code in ProductSet.objects.exclude(code="").values_list("game_id", "code")}
        self.slugs = set(ProductSet.objects.values_list("game_id", "slug"))
        self.known = set(Release.objects.filter(source=self.source).values_list("game_id", "name"))
        self.loaded = True

    def see(self, offer, now=None):
        if self.off:
            return None
        title = offer.title or ""
        event = PRERELEASE_EVENT.search(title)
        if not event and offer.availability != Listing.Availability.PREORDER:
            return None
        if not event and SHOP_CODE.search(title) is None:
            return None
        if not self.loaded:
            self.load()
        slug = find_game(title) or find_game(offer.vendor, " ".join(offer.tags or ()), offer.shop_type)
        game_id = self.games.get(slug)
        if game_id is None:
            return None
        code = shop_code(title, slug)
        if code:
            name = code
            if (game_id, compact_code(code)) in self.codes:
                return None
        elif not event:
            return None
        else:
            code, name = "", event_set_name(title)
            if not name or (game_id, name_key(name)) in self.slugs:
                return None
        key = (game_id, name[:120])
        if key in self.seen:
            return None
        self.seen.add(key)
        if key in self.known:
            self.again.append(key)
            return None
        url = offer.url if (offer.url or "").lower().startswith(("http://", "https://")) else ""
        now = now or timezone.now()
        with transaction.atomic():
            # A savepoint, so a clash with another process's row cannot break the read around it.
            row = Release.objects.create(
                game_id=game_id, name=name[:120], code=code[:20], source=self.source, official=False,
                source_url=url[:500], note=title[:300], first_seen_at=now, last_seen_at=now,
            )
        self.known.add(key)
        return row

    def finish(self, now=None):
        if not self.again:
            return
        now = now or timezone.now()
        query = Q()
        for game_id, name in self.again:
            query |= Q(game_id=game_id, name=name)
        Release.objects.filter(query, source=self.source).update(last_seen_at=now)


# Set codes in product names that no set and no release source has yet: a Digimon set the English site does not
# list (a Japanese EX14), or a Dragon Ball Super Masters set, which no source lists. Each becomes one row under
# Announced sets to check: Claude settles it from the
# publisher's own pages (set_judge.DOMAINS), or the owner taps Add set or Not a set. A name never adds a set.
NAMES_SOURCE = f"{SHOP_PREFIX}names"
NAME_CODES = {
    "digimon": re.compile(r"(?<![a-z0-9])(bt|ex|st|lm)[ -]?(\d{1,2})(?![a-z0-9])"),
    "riftbound": re.compile(r"(?<![a-z0-9])(?:(ogn|sfd)|(rb)[ -]?(\d{2}))(?![a-z0-9])"),
    "dragon-ball": re.compile(r"(?<![a-z0-9])(b)[ -]?(\d{2})(?![a-z0-9])"),
}
NAME_EXAMPLES = 3


def name_code(match):
    """'BT27' from 'bt 27', 'ST09' from 'st9', 'OGN' from 'ogn'."""
    parts = [part for part in match.groups() if part]
    letters, number = parts[0], parts[1] if len(parts) > 1 else ""
    return letters.upper() + (f"{int(number):02d}" if number else "")


def raise_name_candidates(game, dry_run=False):
    """Write one pending Release row (source shop:names) per set code that products of ``game`` with no set
    carry and no set or announced set of the game has. Returns the codes. Three queries."""
    pattern = NAME_CODES.get(game.slug)
    if pattern is None:
        return []
    known = set()
    for code in ProductSet.objects.filter(game=game).exclude(code="").values_list("code", flat=True):
        known |= set_code_keys(code, game.slug) | {compact_code(code)}
    known |= {code_key(code, game.slug) for code in Release.objects.filter(game=game).exclude(code="")
              .values_list("code", flat=True)}
    known |= {code_key(name, game.slug) for name in Release.objects.filter(game=game, source=NAMES_SOURCE)
              .values_list("name", flat=True)}
    found = defaultdict(list)
    for name in Product.objects.filter(game=game, is_active=True, product_set__isnull=True).values_list("name", flat=True):
        lowered = name.lower()
        spans = [m.span() for m in CODE_SPAN.finditer(lowered)
                 if int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + SPAN_WIDTH]
        for match in pattern.finditer(lowered):
            if any(start <= match.start() < end for start, end in spans):
                continue
            code = name_code(match)
            if code_key(code, game.slug) not in known and name not in found[code]:
                found[code].append(name)
    if dry_run or not found:
        return sorted(found)
    now = timezone.now()
    with transaction.atomic():
        for code, names in sorted(found.items()):
            Release.objects.get_or_create(
                game=game, source=NAMES_SOURCE, name=code,
                defaults={"code": code, "official": False, "note": " | ".join(sorted(names)[:NAME_EXAMPLES])[:300],
                          "first_seen_at": now, "last_seen_at": now},
            )
    return sorted(found)


# Filing products under sets ---------------------------------------------------------------------
#
# A product with no set is filed under one only when its name says which, and only one set fits:
#
# - by code first: a code in the name that is the code of exactly one set of its game and language. Codes
#   compare in one spelling (code_key), so 'BT 15', 'BT-15' and 'BT15' are one code, and 'ST9' is 'ST09'.
#   A name with a run of sets ('Special Booster Ver.2.0 (BT18 19)', 'BT01-03') is never filed by its codes;
# - then by name: the set's whole name, as a phrase, with the game's filler words left out of both. A set whose
#   name is one word ('Origins', 'Fabled') files a product only when the name starts with that word, once the
#   game's name and a 'Set 3:' before it are taken off, and no other set of the game has that word for a name;
# - two sets that fit, unless one's name holds the other's ('Star Trek Commander' over 'Star Trek'), file nothing.
#
# A product already under a set is never moved, and a set only takes products of its own language.

def filler_words(game_slug):
    from .management.commands.merge_duplicates import FILLER, POKEMON_SERIES
    from .matching import STOP

    return FILLER | STOP | (POKEMON_SERIES if game_slug == "pokemon" else set())


def words_of(text):
    from .search import normalise

    return normalise(text).split()


CODE_TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
# A code written with a space: 'BT 15', 'LM 06', 'ST 1'.
SPACED_CODE = re.compile(r"(?<![a-z0-9.])([a-z]{1,4}) (\d{1,3})(?![a-z0-9.])")
# A run of sets: 'BT18 19', 'BT01-03', 'BT19/20'. The second number is a little above the first, so 'OP-15 24
# Packs' is not one.
CODE_SPAN = re.compile(r"(?<![a-z0-9])[a-z]{1,4}[ -]?(\d{1,3})(?:\s*[-/&,]\s*|\s+)(\d{1,3})(?![a-z0-9.])")
SPAN_WIDTH = 6
# Bandai's code for a booster that carries another set's cards ('OP15-EB04'): the first code is the set's.
JOINED_CODE = re.compile(r"^\s*([a-z]+-?\d+)-[a-z]+-?\d+\s*$", re.I)
# A code for a run of sets: 'BT18-19', 'BT01-03'.
RUN_CODE = re.compile(r"^\s*[a-z]+-?(\d+)\s*-\s*(\d+)\s*$", re.I)
# Games whose sets' codes are letters only, as the publisher prints them on every card ('OGN-001/298'), and so
# file products by themselves: Riftbound's OGN, SFD, UNL. No other game's are, since 'BRO' or 'WAR' is also a word.
LETTER_CODES = {"riftbound": re.compile(r"^[A-Z]{3}$")}
# Pokémon codes keep their zeros: a source's 'SV08' is not always the shops' 'SV8', which is as often the
# Japanese set of that number as the English one.
ZEROS_MATTER = {"pokemon"}
# Products the publisher lists apart from its sets, which are filed under none, by the codes it gives them or the
# way their names end: Digimon's Premium Bandai sets (PB-25 'Digimon Story Time Stranger Set' is not the Time
# Stranger booster, nor 'Digimon Liberator Debuggers Set' the Digimon Liberator one), gift boxes (GB), adventure
# boxes (AB), double pack sets (DP) and premium deck sets (PD). A Digimon booster or deck never ends in 'Set'.
NOT_SET_PRODUCTS = {"digimon": re.compile(r"(?<![a-z0-9])(?:pb|gb|ab|dp|pd)[ -]?\d{1,2}(?![0-9])|\bset\s*(?:\(.*\))?\s*$",
                                          re.I)}
# Numbers shops put on products that the publisher never uses for a set: Riftbound's 'RB 03' and 'RB 02PC01'. They
# are taken out of the name before its words are matched, so 'Unleashed RB 03 English Booster Box' is filed by
# 'Unleashed'; the number itself files nothing.
PRODUCT_CODES = {"riftbound": re.compile(r"(?<![a-z0-9])rb ?\d{2}(?:[a-z]{2}\d{2})?(?![a-z0-9])", re.I)}
# Words that never make a one-word set name: kinds of product and the words around them.
SET_NUMBER_WORDS = r"\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten"
LEADING_SET_NUMBER = re.compile(rf"^\s*set\s+(?:{SET_NUMBER_WORDS})\s*[:\-–]?\s*", re.I)
LEADING_GAME_WORDS = re.compile(r"^\s*(?:trading card game|card game|tcg)\s*[:\-–]?\s*", re.I)
ONE_WORD_MIN = 4
# Words that may follow a one-word set name in a product's: the kind of product ('Origins Booster Box', 'Fabled
# Illumineer's Trove'), never more name ('Deoxys VMAX', 'Arceus VSTAR').
KIND_FOLLOW = {"play", "draft", "collector", "collectors", "theme", "intro", "fat", "prerelease", "sealed", "booster",
               "boosters", "display", "bundle", "blister", "pack", "packs", "box", "jumbo", "sleeved", "starter", "deck",
               "decks", "set", "case", "tin", "etb", "elite", "trainer", "build", "first", "1st", "unlimited", "edition",
               "champion", "showdown", "vault", "illumineers", "illumineer", "trove", "gift", "collection", "pin",
               "checklane", "mini", "premium", "special", "advanced"}
# After a set's name that another set's name holds ('Final Fantasy' in 'Final Fantasy Commander', 'Team Rocket' in
# 'Team Rocket Returns'), only the words of a booster of that set: 'Final Fantasy Play Booster' is the set,
# 'Team Rocket Tin' and 'Legendary Duelists 9: Duels From the Deep' may be anything.
FAMILY_FOLLOW = {"play", "draft", "collector", "collectors", "set", "booster", "boosters", "display", "bundle", "blister",
                 "pack", "packs", "box", "jumbo", "sleeved", "prerelease", "elite", "trainer", "etb", "fat", "intro"}
# Short joining words, which never tell a longer set's name from a shorter one's ('Battle for Baldur's Gate').
JOINING_WORDS = {"for", "to", "in", "on", "at", "from", "with", "by", "vs", "its"}
# Words for a kind of set that some of a game's sets carry in their names and others never do: a product with
# the word is never filed under a set without it, while the game has a set with it. 'Ikoria Commander Deck' is
# not Ikoria (Scryfall keeps its decks in a Commander set), and 'Invasion of Chaos Booster Pack (25th
# Anniversary)' is not the 2004 Invasion of Chaos.
KIND_OF_SET = {"commander", "anniversary"}
# A product a shop calls a reprint, or a later print, is a later run of an older set, under its own date. It
# goes under a set only when that set is itself a reprint edition ('Dark Crisis (25th Anniversary Edition)').
REPRINT = re.compile(r"\breprint(?:s|ed)?\b|\b(?:19|20)\d\d\s+(?:[a-z]+\s+)?print\b", re.I)
REPRINT_SETS = {"reprint", "anniversary"}
# Not a sealed card product, whatever set it names: a manga box set, a pin badge. A set with the word in its
# own name ('Manga Booster 02') still takes it.
NOT_CARDS = re.compile(r"\bmanga\b|\bpin badges?\b", re.I)
# A set name that is printed inside more than one set, so it never says which by itself: Pokémon's Radiant
# Collection is a run of cards in both Legendary Treasures and Generations.
NAME_NOT_ENOUGH = {"pokemon": {("radiant", "collection")}}


def common_words():
    from .matching import GENERIC_WORDS

    return GENERIC_WORDS | {"set", "sets", "starter", "structure", "promo", "promos", "core", "card", "cards", "box",
                            "edition", "series", "vault", "decks", "tins", "boxes", "bundles"}


def code_key(code, game_slug=""):
    """One spelling for a set code: 'BT 15', 'BT-15' and 'bt15' -> 'bt15', and 'ST09' -> 'st9' (except for a game
    whose zeros matter)."""
    compact = compact_code(code)
    if game_slug in ZEROS_MATTER:
        return compact
    return re.sub(r"(?<=[a-z])0+(?=\d)", "", compact)


def set_code_keys(code, game_slug=""):
    """The code a set files products by, in code_key's spelling, or none: a code needs letters and digits (a
    Riftbound set's three letters are enough, LETTER_CODES), and a run of sets ('BT18-19', a Digimon special
    booster's) is no one set's code."""
    if RUN_CODE.match(code or ""):
        return frozenset()
    if not usable_code(code) and not (game_slug in LETTER_CODES and LETTER_CODES[game_slug].match(code or "")):
        return frozenset()
    joined = JOINED_CODE.match(code)
    return frozenset({code_key(joined.group(1) if joined else code, game_slug)})


def name_codes(name, game_slug=""):
    """The codes a product name gives, in code_key's spelling, leaving out every code in a run of sets."""
    lowered = (name or "").lower()
    spans = [m.span() for m in CODE_SPAN.finditer(lowered)
             if int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + SPAN_WIDTH]

    def free(match):
        return not any(start <= match.start() < end for start, end in spans)

    found = {code_key(m.group(), game_slug) for m in CODE_TOKEN.finditer(lowered) if free(m)}
    found |= {code_key(m.group(1) + m.group(2), game_slug) for m in SPACED_CODE.finditer(lowered) if free(m)}
    return found


def lead_words(name):
    """The name's words from where a set name may start: the game's name, 'Set 3:' and a 'The' taken off."""
    from .matching import GAME_PREFIX

    text = name or ""
    for _ in range(2):
        text = GAME_PREFIX.sub("", text)
        text = LEADING_GAME_WORDS.sub("", text)
    text = LEADING_SET_NUMBER.sub("", text)
    words = words_of(text)
    return words[1:] if words[:1] == ["the"] else words


class SetRule(NamedTuple):
    pk: object
    codes: frozenset     # code_key spellings
    words: tuple         # the name's words in order, filler left out
    length: int
    language: str
    one_word: bool       # a one-word name that may file a product whose name starts with it
    family: bool = False  # its name is part of another set's ('Legendary Duelists', 'Team Rocket')
    extras: frozenset = frozenset()   # the other words of the longer sets' names ('commander' for 'Star Trek')
    by_name: bool = True  # whether its name alone may file a product (NAME_NOT_ENOUGH)


def set_rules(game_slug, sets):
    """[SetRule] for the sets that can file a product, from (pk, name, code) rows."""
    filler = filler_words(game_slug)
    common = common_words()
    rules = []
    for pk, name, code in sets:
        # A set's language is what its name says, or what a code only another language has implies.
        language = languages.language_of(f"{name} {code or ''}", game_slug)
        said = languages.name(language).lower().split() if language else []
        words = tuple(w for w in words_of(name) if w not in filler and w not in said)
        rules.append(SetRule(pk, set_code_keys(code, game_slug), words, len(name or ""), language, False))
    singles = Counter((rule.language, rule.words[0]) for rule in rules if len(rule.words) == 1)
    holding = defaultdict(list)   # a word -> the rules whose name has it
    for rule in rules:
        for word in set(rule.words):
            holding[word].append(rule)
    not_enough = NAME_NOT_ENOUGH.get(game_slug, set())
    shaped = []
    for rule in rules:
        longer = [other for other in holding[rule.words[0]] if other.language == rule.language
                  and len(other.words) > len(rule.words) and holds(other.words, rule.words)] if rule.words else []
        shaped.append(rule._replace(
            one_word=len(rule.words) == 1 and singles[(rule.language, rule.words[0])] == 1
            and rule.words[0] not in common and len(rule.words[0]) >= ONE_WORD_MIN and bool(re.search(r"[a-z]", rule.words[0])),
            family=bool(longer),
            extras=frozenset(word for other in longer for word in other.words
                             if word not in rule.words and word not in common and word not in JOINING_WORDS),
            by_name=rule.words not in not_enough,
        ))
    return shaped


def holds(words, phrase):
    """Whether ``phrase`` (a tuple of words) is in ``words`` as a whole, in order."""
    size = len(phrase)
    return any(tuple(words[i:i + size]) == phrase for i in range(len(words) - size + 1))


def choose_set(product_name, rules, game=""):
    """The set a product name belongs to, or None (see the rules above this section).

    Only a set of the product's own language can take it: a Japanese box shares the English set's code and
    name but comes out months earlier, so it never takes the English set's date. One series word alone never
    files anything ('Scarlet & Violet' never takes 'Scarlet & Violet Surging Sparks ETB').
    """
    return choose_set_why(product_name, rules, game)[0]


def choose_set_why(product_name, rules, game=""):
    """(the set's pk or None, why), as choose_set decides it."""
    language = languages.language_of(product_name or "", game)
    rules = [rule for rule in rules if languages.same(rule.language, language)]
    if not rules:
        return None, f"no set in {languages.name(language) if language else 'English'}"
    other = NOT_SET_PRODUCTS.get(game)
    if other is not None and other.search(product_name or ""):
        return None, "a product the publisher lists apart from its sets"
    codes = name_codes(product_name, game)
    if game in PRODUCT_CODES:
        # Matched by name with the shop's product number taken out, never by the number.
        product_name = " ".join(PRODUCT_CODES[game].sub(" ", product_name or "").split())
    filler = filler_words(game)
    words = [w for w in words_of(product_name) if w not in filler]
    by_code = {rule.pk: rule for rule in rules if rule.codes & codes}
    if len(by_code) == 1:
        [rule] = by_code.values()
        return kept(rule, "code", words, product_name, rules)
    if by_code:
        # Two sets' codes: a product of several sets, or a code two sets share.
        return None, "codes of two sets"
    lead = None
    fits = []
    for rule in rules:
        if len(rule.words) >= 2:
            if holds(words, rule.words):
                fits.append(rule)
        elif rule.one_word and rule.words[0] in words:
            if lead is None:
                lead = lead_words(product_name)[:1]
            if lead == [rule.words[0]] and ends_name(words, rule.words, KIND_FOLLOW):
                fits.append(rule)
    if not fits:
        spans = any(int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + SPAN_WIDTH
                    for m in CODE_SPAN.finditer((product_name or "").lower()))
        return None, "a run of sets" if spans else "names no set the site has"
    best = max(fits, key=lambda r: (len(r.words), r.length))
    inside = set(best.words)
    if any(rule.pk != best.pk and not set(rule.words) < inside for rule in fits):
        return None, "two sets fit"
    if not best.by_name:
        return None, "the name is in more than one set"
    if best.family and (best.extras & set(words) or not ends_name(words, best.words, FAMILY_FOLLOW)):
        # 'Legendary Duelists 9: Duels From the Deep' is not 'Legendary Duelists', nor 'Star Trek Collector
        # Commander Deck' 'Star Trek': a set whose name starts another set's files only a name that goes on with
        # the kind of product, or ends, and has none of the longer set's other words anywhere.
        return None, "a longer set's name may fit"
    if followed_by(words, best.words, "series"):
        # 'Black & White Series Theme Deck' names the era, not the set.
        return None, "names a series"
    return kept(best, "one word" if len(best.words) == 1 else "name", words, product_name, rules)


def kept(rule, why, words, product_name, rules):
    """(rule's pk, why), unless the product is a later run or another kind of set than ``rule`` (REPRINT,
    KIND_OF_SET) or not a card product (NOT_CARDS): then (None, the reason)."""
    not_cards = NOT_CARDS.search(product_name or "")
    if not_cards and not set(words_of(not_cards.group())) <= set(rule.words):
        return None, "not a card product"
    if REPRINT.search(product_name or "") and not REPRINT_SETS & set(rule.words):
        return None, "a reprint"
    for word in KIND_OF_SET:
        if word in words and word not in rule.words and any(word in other.words for other in rules):
            return None, f"says {word}, and the set does not"
    return rule.pk, why


def followed_by(words, phrase, word):
    """Whether ``phrase`` stands right before ``word`` anywhere in ``words``."""
    size = len(phrase)
    return any(tuple(words[i:i + size]) == phrase and words[i + size] == word for i in range(len(words) - size))


def ends_name(words, phrase, follow):
    """Whether ``phrase`` in ``words`` is followed only by the kind of product: every place it stands is at the
    end, or before one of the ``follow`` words."""
    size = len(phrase)
    places = [i for i in range(len(words) - size + 1) if tuple(words[i:i + size]) == phrase]
    return bool(places) and all(i + size == len(words) or words[i + size] in follow for i in places)


def filing_rules(game, today):
    """(set_rules for the game's sets and the sets its release sources list that it does not have yet, those
    Release rows by their rule key). Each row carries ``filing_code``: its code, or '' when sets of other names
    share it (shared_codes). Three queries."""
    listed = {("listed", row.pk): row for row in listed_rows(game, today)}
    sets = list(ProductSet.objects.filter(game=game).values_list("pk", "name", "code"))
    shared = shared_codes(game, sets)
    slugs = {name_key(name) for _pk, name, _code in sets}
    candidates = []
    for key, row in listed.items():
        row.filing_code = filing_code(row, shared, game.slug)
        if name_key(row.name) not in slugs:
            candidates.append((key, row.name, row.filing_code))
    return set_rules(game.slug, sets + candidates), listed


def attach_sets(game, product_ids=None, now=None):
    """File the game's products that have no set under the set their name or code names. Returns how many.

    The sets the release sources list that the site does not have yet take part too (listed_rows): a product
    that names one released before the radar's window adds it (add_listed_set, the back catalogue); a product
    that names one still inside the window waits for the release rules; and either keeps a product from a set
    whose name is only part of the product's.
    """
    now = now or timezone.now()
    today = local_today(now)
    rules, listed = filing_rules(game, today)
    if not rules:
        return 0
    products = Product.objects.filter(game=game, product_set__isnull=True)
    if product_ids is not None:
        products = products.filter(pk__in=product_ids)
    moves = defaultdict(list)
    for pk, name in products.values_list("pk", "name"):
        chosen = choose_set(name, rules, game.slug)
        if chosen is not None:
            moves[chosen].append(pk)
    cutoff = recent_cutoff(today)
    moved = 0
    for set_pk, pks in moves.items():
        if isinstance(set_pk, tuple):
            row = listed[set_pk]
            if row.release_date >= cutoff:
                continue   # still inside the radar's window: the release rules decide whether it is a set
            with transaction.atomic():
                set_pk = add_listed_set(row, now, row.filing_code).pk
        # Filed under a set, the product's page gains the set and its date: the page changed.
        filed = Product.objects.filter(pk__in=pks, product_set__isnull=True).update(product_set_id=set_pk, updated_at=now)
        if filed:
            logger.info("Filed %s %s product%s under set %s: %s", filed, game.slug, "" if filed == 1 else "s", set_pk,
                        ", ".join(str(pk) for pk in pks[:20]))
        moved += filed
    if moved:
        from .signals import clear_list_caches

        clear_list_caches(force=True)
    return moved


def attach_all():
    """attach_sets for every game. Returns how many products were filed."""
    return sum(attach_sets(game) for game in Game.objects.all())


# The owner's taps -------------------------------------------------------------------------------

def add_set(row, name, released=None):
    """The owner's Add set: the set is created or updated, with the date if one was given, and products
    with its name or code are filed under it."""
    name = " ".join((name or "").split())[:120]
    if row.product_set_id is not None:
        # A set that is already there, with no date yet: the owner may correct its name, never its address.
        product_set = row.product_set
        if name and product_set.name != name:
            product_set.name = name
            product_set.save(update_fields=["name"])
    else:
        product_set = set_for(row.game, name, row.code if row.code and name != row.code else "")
    if released is not None:
        given = row.precision == DAY and row.release_date == released and not row.source.startswith(SHOP_PREFIX)
        product_set.release_date = released
        product_set.release_date_source = (row.source if given else OWNER)[:20]
        product_set.save(update_fields=["release_date", "release_date_source"])
    row.product_set = product_set
    row.status = Release.Status.ACCEPTED
    row.save(update_fields=["product_set", "status"])
    link_rows(product_set, row.game, name)
    attach_sets(row.game)
    return product_set


# Claude's answers (catalogue/judge.py): a date Claude found on a cited page is written with this source, so
# it never replaces the owner's date or one a source wrote, and a publisher's date replaces it later.
CLAUDE = "claude"


def claude_file(row, product_set, released=None):
    """File an announced set under a set the site has, as Claude's answer: the row is accepted under it, and
    the date Claude verified is written only where may_write_date lets Claude write one (never over the
    owner's or a source's). Returns {"date_written": the date written or None, "row_set": the row's set
    before}, what Undo needs."""
    before = row.product_set_id
    written = None
    if released is not None and write_date(product_set, released, CLAUDE):
        written = released.isoformat()
    row.product_set = product_set
    row.status = Release.Status.ACCEPTED
    row.save(update_fields=["product_set", "status"])
    link_rows(product_set, row.game, row.name)
    attach_sets(row.game)
    return {"date_written": written, "row_set": before}


def claude_add_set(row, name, code="", released=None):
    """Add an announced set under the name, code and date Claude verified. A set the site already has under
    that code or name, or under the name the source gave, is used instead: never a second set for one set.
    Returns (the set, whether it was created, what Undo needs)."""
    existing = (row.product_set or find_set(row.game, name, code) or find_set(row.game, row.name, row.code))
    if existing is not None:
        return existing, False, claude_file(row, existing, released)
    product_set = set_for(row.game, name, code if code and code != name else "")
    return product_set, True, claude_file(row, product_set, released)


def undo_claude_file(row, product_set, note):
    """Put back what claude_file did: the row waits again, filed where it was, and a date Claude wrote is
    taken off when it is still Claude's."""
    written = (note or {}).get("date_written")
    if product_set is not None and written and product_set.release_date_source == CLAUDE \
            and product_set.release_date and product_set.release_date.isoformat() == written:
        product_set.release_date = None
        product_set.release_date_source = ""
        product_set.save(update_fields=["release_date", "release_date_source"])
    row.product_set_id = (note or {}).get("row_set")
    row.status = Release.Status.PENDING
    row.save(update_fields=["product_set", "status"])


DATE_YMD = re.compile(r"\b(\d{4})[/.](\d{1,2})[/.](\d{1,2})\b")
DATE_JAPANESE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def dates_in(text):
    """Every full date a text gives, in any of the forms parse_date reads, and 2026/01/23 or 2026年1月23日 as
    Japanese and Chinese pages write them."""
    text = " ".join(html.unescape(text or "").split())
    found = set()
    for pattern in (ISO_DATE, DATE_YMD, DATE_JAPANESE):
        for match in pattern.finditer(text):
            day = safe_date(*match.groups())
            if day:
                found.add(day)
    for pattern, order in ((MONTH_DAY_YEAR, (3, 1, 2)), (DAY_MONTH_YEAR, (3, 2, 1))):
        for match in pattern.finditer(text):
            year, month, day = (match.group(i) for i in order)
            number = month_number(month)
            day = safe_date(year, number, day) if number else None
            if day:
                found.add(day)
    return found


def dismiss(row):
    """The owner's Not a set: the row is never shown again for its game, name and source."""
    row.status = Release.Status.DISMISSED
    row.save(update_fields=["status"])


def use_date(row):
    """The owner's Use this date: the set takes this row's date, as the owner's choice."""
    product_set = row.product_set or set_for(row.game, row.name, row.code)
    product_set.release_date = row.release_date
    product_set.release_date_source = OWNER
    product_set.save(update_fields=["release_date", "release_date_source"])
    row.product_set = product_set
    row.status = Release.Status.ACCEPTED
    row.save(update_fields=["product_set", "status"])
    link_rows(product_set, row.game, row.name)
    attach_sets(row.game)
    return product_set


def keep_date(product_set):
    """The owner's Keep this date: the date the set already shows becomes the owner's, so no source
    changes it and it is not asked about again."""
    product_set.release_date_source = OWNER
    product_set.save(update_fields=["release_date_source"])
    return product_set


# For the admin pages ----------------------------------------------------------------------------

def source_rows(now=None):
    """One row per source for the Insights page: when it was last read, what it found, its last error."""
    now = now or timezone.now()
    states = {s.name: s for s in ReleaseSourceState.objects.filter(name__in=BY_NAME)}
    rows = []
    for source in SOURCES:
        state = states.get(source.name)
        rows.append({
            "name": source.name, "label": source.label, "official": source.official,
            "every": source.every_hours, "last_ok": state.last_ok_at if state else None,
            "signals": state.signals_found if state else 0, "error": state.last_error if state else "",
            "next": state.next_at if state else None,
        })
    return rows


def parse_owner_date(text):
    """A date from the Add set form (YYYY-MM-DD), or None."""
    try:
        return datetime.strptime((text or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
