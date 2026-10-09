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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.text import slugify

from .checks import DATE_GAP_DAYS
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
           terms="No published limit; 100 ms between requests and at most 40 set pages a read."),
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

    def __init__(self, source, fetch, sleep, take_page=None, stop=None):
        self.source, self.fetch, self.sleep = source, fetch, sleep
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
    ids = [i for i in parse_tcgdex_list(reader.get(source.url)) if i not in known.dated_codes][:TCGDEX_DETAILS]
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
        signals = READERS[name](source, reader, recent_cutoff(today), Known.load(name))
    except OutOfPages:
        signals = []
        result.skipped = f"the {HTML_PAGES_PER_DAY} web pages a day are used up"
    except Exception as exc:  # noqa: BLE001 - a broken page or parser records its error and writes nothing
        signals = None
        result.error = (str(exc) or exc.__class__.__name__)[:200]
    result.requests = reader.requests
    if dry_run:
        result.signals = signals or []
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
            if compact_code(product_set.code) == compact and not language_of(product_set.name):
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
    product_set.release_date = released
    product_set.release_date_source = source_name[:20]
    product_set.save(update_fields=["release_date", "release_date_source"])
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
            name = " ".join((signal.name or "").split()).strip(QUOTES)[:120]
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
            attach_sets(game)
    return stats


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


# Filing products under sets ---------------------------------------------------------------------

def filler_words(game_slug):
    from .management.commands.merge_duplicates import FILLER, POKEMON_SERIES
    from .matching import STOP

    return FILLER | STOP | (POKEMON_SERIES if game_slug == "pokemon" else set())


def words_of(text):
    from .search import normalise

    return normalise(text).split()


CODE_TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


def set_rules(game_slug, sets):
    """[(set pk, code or '', name words, name length, language)] for the sets that can file a product."""
    filler = filler_words(game_slug)
    rules = []
    for pk, name, code in sets:
        code = compact_code(code) if usable_code(code) else ""
        language = language_of(name).lower()
        name_words = {w for w in words_of(name) if w not in filler and w not in language.split()}
        rules.append((pk, code, name_words, len(name), language))
    return rules


def choose_set(product_name, rules):
    """The set a product name belongs to, or None.

    Only a set of the product's own language can take it: a Japanese box shares the English set's code
    and name but comes out months earlier, so it never takes the English set's date. A set whose code is
    a whole token of the name wins. Otherwise the set with the most words, all of them in the name, wins;
    a set needs two such words, so one word or series words alone never file anything ('Scarlet & Violet'
    never takes 'Scarlet & Violet Surging Sparks ETB').
    """
    language = language_of(product_name or "").lower()
    rules = [rule for rule in rules if rule[4] == language]
    lowered = (product_name or "").lower()
    tokens = {compact_code(t) for t in CODE_TOKEN.findall(lowered)}
    by_code = [rule for rule in rules if rule[1] and rule[1] in tokens]
    if by_code:
        return max(by_code, key=lambda r: (len(r[2]), r[3]))[0]
    name_words = set(words_of(product_name))
    fits = [rule for rule in rules if len(rule[2]) >= 2 and rule[2] <= name_words]
    if not fits:
        return None
    return max(fits, key=lambda r: (len(r[2]), r[3]))[0]


def attach_sets(game, product_ids=None):
    """File the game's products that have no set under the set their name or code names. Returns how many."""
    rules = set_rules(game.slug, ProductSet.objects.filter(game=game).values_list("pk", "name", "code"))
    if not rules:
        return 0
    products = Product.objects.filter(game=game, product_set__isnull=True)
    if product_ids is not None:
        products = products.filter(pk__in=product_ids)
    moves = defaultdict(list)
    for pk, name in products.values_list("pk", "name"):
        chosen = choose_set(name, rules)
        if chosen is not None:
            moves[chosen].append(pk)
    moved = 0
    for set_pk, pks in moves.items():
        moved += Product.objects.filter(pk__in=pks, product_set__isnull=True).update(product_set_id=set_pk)
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
