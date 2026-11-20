"""
What people ask: the owner's page of what visitors want to know, sorted into the problems they have, so the next thing
built solves one that exists. The owner, 20 Nov 2026, planning how RipRaptor becomes the UK price benchmark: "Shall we
plan it together based on what people are asking for. We need to solve a problem that exists".

Read from what is already kept; nothing new is stored:

- the questions asked of Rip on product pages (ClaudeAsk, kind ask, row key ask:<product>), split into the one-tap
  questions as offered (askrip.SUGGESTED, in the registry's current words) and those typed in people's own words;
- Rip's requests from the list builder, the microphone, the Ask popup and the search bars (rip_wants.ROW_KEYS);
- the site's searches (DailySearch, scanners' probes left out), each counted as often as it was made;
- visitors' messages (inbox.Message from a visitor).

Each is put under the first THEMES pattern its words match, else "Looking for a product or other". Words, not Claude:
the page costs nothing to open and reads the same each time. Examples are the people's own words (typed questions,
Rip requests, messages, then the most made searches), with email addresses, links and long numbers taken out as Rip's
questions are. A Log to copy at the bottom puts the whole page as text, to paste to Claude when planning.

Staff only, never for editors, never cached or indexed. Four queries, however many questions.
"""
import re
from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import insights
from catalogue.models import ClaudeAsk, DailySearch
from guides.access import not_for_editors
from inbox.models import Message
from web import askrip, rip_wants

PERIODS = (30, 90, 180)
DEFAULT_DAYS = 90
MOST = 20000      # questions and requests read at most, the newest
EXAMPLES = 8      # examples shown under each problem
TEXT_MOST = 160   # characters of an example shown

# The problems, in the order a question is tried against them: the first whose words it has takes it. Trust and
# delivery come first because a question about either usually mentions price too ("is this cheap one fake?").
THEMES = (
    ("trust", "Fakes, reseals and safe sellers",
     r"\b(?:fakes?|reseal\w*|legit|scams?|scammers?|genuine|authentic\w*|counterfeit\w*|trust\w*|safe|red flags?|"
     r"dodgy|proper seller)\b"),
    ("delivery", "Delivery and the total cost",
     r"\b(?:deliver\w*|shipping|postage|courier|dispatch\w*|p ?& ?p)\b"),
    ("when", "Buy now or wait",
     r"\bwait\b|\bbuy (?:it )?now\b|\bblack friday\b|\bprice drops?\b|\b(?:go|going|come|coming) (?:up|down)\b|"
     r"\bcheaper (?:later|soon|next)\b|\bwhen (?:to|should i|do i) buy\b"),
    ("rrp", "RRP, pre-orders and release dates",
     r"\b(?:rrp|msrp|retail price|pre-?orders?|pre orders?|releas\w*|launch\w*|coming out|out yet)\b"),
    ("stock", "Stock and restocks",
     r"\b(?:in stock|out of stock|restock\w*|back in|sold out|availab\w*|where (?:can i|to|do i) (?:buy|get|find))\b"),
    ("price", "Is it a fair price",
     r"\b(?:good value|value|fair|worth|over ?priced|expensive|cheap\w*|bargain|deals?|prices?|pricing|cost|"
     r"how much|under)\b|£"),
    ("inside", "What's inside and pull rates",
     r"\b(?:inside|contents?|how many (?:packs|cards)|packs? in|pull rates?|pulls?|hits?|chase|odds)\b"),
    ("fit", "Which to buy, gifts and beginners",
     r"\b(?:right for me|gifts?|presents?|christmas|birthday|kids?|child\w*|son|daughter|beginner\w*|"
     r"best (?:to|for|one)|which (?:one|should|is better)|recommend\w*|suit\w*)\b"),
    ("language", "Languages and imports",
     r"\b(?:japanese|chinese|korean|english|thai|indonesian|imports?|imported)\b"),
)
OTHER = ("other", "Looking for a product or other")
MATCHERS = [(key, re.compile(pattern)) for key, _label, pattern in THEMES]
LABELS = dict([(key, label) for key, label, _pattern in THEMES] + [OTHER])

# Where an ask came from, as it reads mid-sentence ("10 in all: typed to Rip on a product page 2, searched 4").
SOURCES = (("typed", "typed to Rip on a product page"), ("tapped", "one-tap question to Rip"),
           ("rip", "asked of Rip elsewhere"), ("search", "searched"), ("message", "message to us"))
SOURCE_NAMES = dict(SOURCES)
# Where an example comes from, in the people's own words first.
OWN_WORDS = ("typed", "rip", "message", "search")


def theme_of(text):
    """The key of the first problem ``text`` is about, else "other"."""
    low = " ".join((text or "").casefold().split())
    for key, pattern in MATCHERS:
        if pattern.search(low):
            return key
    return OTHER[0]


def tapped_words():
    """{the one-tap question as it reads now, casefolded: its words}."""
    from content import service as copy

    store = copy.load_all()
    found = {}
    for key in askrip.SUGGESTED:
        words = copy.get(key, store)
        if words:
            found[askrip.tidy_question(words).casefold()] = words
    return found


def gathered(since):
    """Every ask since ``since`` as {source, text, at, times, product}, newest first within a source. Four queries."""
    taps = tapped_words()
    asks = []
    rows = (ClaudeAsk.objects.filter(kind=ClaudeAsk.Kind.ASK, asked_at__gte=since)
            .filter(row_key__startswith="ask:").order_by("-asked_at")
            .values_list("asked_at", "evidence", "product__name")[:MOST])
    for asked_at, evidence, product in rows:
        evidence = evidence if isinstance(evidence, dict) else {}
        text = askrip.tidy_question(str(evidence.get("question") or ""))
        if not text:
            continue
        source = "tapped" if text.casefold() in taps else "typed"
        asks.append({"source": source, "text": text, "at": asked_at, "times": 1,
                     "product": product or evidence.get("product") or ""})
    rows = (ClaudeAsk.objects.filter(kind=ClaudeAsk.Kind.ASK, row_key__in=rip_wants.ROW_KEYS, asked_at__gte=since)
            .order_by("-asked_at").values_list("asked_at", "evidence")[:MOST])
    for asked_at, evidence in rows:
        evidence = evidence if isinstance(evidence, dict) else {}
        text = askrip.tidy_question(str(evidence.get("list") or ""))
        if text:
            asks.append({"source": "rip", "text": text, "at": asked_at, "times": 1, "product": ""})
    searches = (insights.people_searches(DailySearch.objects.filter(date__gte=timezone.localtime(since).date()))
                .values("query").annotate(hits=Sum("hits")).order_by("-hits", "query"))
    for row in searches:
        asks.append({"source": "search", "text": row["query"], "at": None, "times": row["hits"], "product": ""})
    rows = (Message.objects.filter(from_visitor=True, created_at__gte=since).order_by("-created_at")
            .values_list("created_at", "body")[:MOST])
    for created_at, body in rows:
        text = askrip.tidy_question(body)
        if text:
            asks.append({"source": "message", "text": text, "at": created_at, "times": 1, "product": ""})
    return asks


def sorted_out(asks):
    """[{key, label, total, own, counts: {source: n}, parts, examples: [ask]}]: the problem most raised in people's
    own words first (``own``: everything but the one-tap questions), "other" last."""
    found = {key: {"key": key, "label": label, "total": 0, "counts": dict.fromkeys(SOURCE_NAMES, 0), "examples": [],
                   "seen": set()}
             for key, label in LABELS.items()}
    for ask in asks:
        theme = found[theme_of(ask["text"])]
        theme["total"] += ask["times"]
        theme["counts"][ask["source"]] += ask["times"]
    # Examples: the people's own words, newest first, then the most made searches; each wording once.
    for source in OWN_WORDS:
        for ask in asks:
            if ask["source"] != source:
                continue
            theme = found[theme_of(ask["text"])]
            folded = ask["text"].casefold()
            if len(theme["examples"]) < EXAMPLES and folded not in theme["seen"]:
                theme["seen"].add(folded)
                theme["examples"].append({**ask, "text": shortened(ask["text"]), "from": SOURCE_NAMES[source]})
    themes = [theme for theme in found.values() if theme["total"]]
    for theme in themes:
        del theme["seen"]
        theme["own"] = theme["total"] - theme["counts"]["tapped"]
        theme["parts"] = [(SOURCE_NAMES[source], theme["counts"][source]) for source, _name in SOURCES
                          if theme["counts"][source]]
    # A one-tap question is one we offered: what people raise in their own words ranks the problems.
    return sorted(themes, key=lambda theme: (theme["key"] == OTHER[0], -theme["own"], -theme["total"], theme["label"]))


def shortened(text):
    return text if len(text) <= TEXT_MOST else text[:TEXT_MOST - 1].rstrip() + "…"


def tapped_counts(asks):
    """[(the one-tap question, times)], the most tapped first."""
    counts = {}
    for ask in asks:
        if ask["source"] == "tapped":
            counts[ask["text"]] = counts.get(ask["text"], 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def totals(asks):
    """{source: how many}, searches counted as often as they were made, and the different searches."""
    found = dict.fromkeys(SOURCE_NAMES, 0)
    for ask in asks:
        found[ask["source"]] += ask["times"]
    found["searches_different"] = sum(1 for ask in asks if ask["source"] == "search")
    return found


def as_text(days, end, counted, themes, taps):
    """The page as plain text, for the Log to copy."""
    lines = [f"What people ask, last {days} days to {timezone.localtime(end):%d %b %Y}",
             f"Typed to Rip on product pages: {counted['typed']}. One-tap questions: {counted['tapped']}. "
             f"Asked of Rip elsewhere: {counted['rip']}. Searches: {counted['search']} "
             f"({counted['searches_different']} different). Messages: {counted['message']}.", ""]
    for theme in themes:
        parts = ", ".join(f"{name} {count}" for name, count in theme["parts"])
        lines.append(f"{theme['label']}: {theme['own']} in their own words, {theme['total']} in all ({parts})")
        for example in theme["examples"]:
            where = f", {example['product']}" if example.get("product") else ""
            times = f", {example['times']} times" if example["source"] == "search" else ""
            lines.append(f'  - "{example["text"]}" ({example["from"]}{where}{times})')
        lines.append("")
    if taps:
        lines.append("One-tap questions: " + "; ".join(f"{words} {times}" for words, times in taps))
    return "\n".join(lines).strip() + "\n"


@staff_member_required
@not_for_editors
@require_GET
def people_ask_page(request):
    try:
        days = int(request.GET.get("days", DEFAULT_DAYS))
    except ValueError:
        days = DEFAULT_DAYS
    days = days if days in PERIODS else DEFAULT_DAYS
    end = timezone.now()
    asks = gathered(end - timedelta(days=days))
    themes = sorted_out(asks)
    taps = tapped_counts(asks)
    counted = totals(asks)
    response = render(request, "admin/people_ask.html", {
        **admin.site.each_context(request),
        "title": "What people ask",
        "days": days, "periods": PERIODS, "counted": counted, "themes": themes, "taps": taps,
        "log": as_text(days, end, counted, themes, taps),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
