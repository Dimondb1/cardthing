"""
A guide's words: how they are checked and how they are shown.

Checks, on every save (style) and before publishing (publish), from the content standards marketing set out:

- the site's house style: no em dashes, no exclamation marks, no HTML, none of the stock phrases the site's
  tests refuse, and none of the claims the standards rule out ("guaranteed", "the best price", "every shop in
  the UK": the site says "every UK shop we track");
- "ETB" never on its own: "Elite Trainer Box" comes first in the guide;
- links in a body go to RipRaptor's own pages or to an https address that is not a shop: a shop's price is
  shown as a price card, so every price is live and dated and shop links stay marked as sponsored;
- every section that states facts has a source, each written "title | address | date read";
- the next review is at most REVIEW_DAYS after the last check, and not before it.

A body is plain text: a blank line starts a paragraph, lines starting "- " are a list and lines starting
"1. " a numbered one, and [words](address) is a link. Everything else is shown exactly as typed.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.text import slugify

REVIEW_DAYS = 92
WORDS_PER_MINUTE = 200
EM_DASH = "—"
LINK = re.compile(r"\[([^\[\]\n]+)\]\(((?:https://|/)[^\s()<>]*)\)")
BULLET = re.compile(r"^\s*-\s+")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
HTML = re.compile(r"<\s*/?\s*[a-zA-Z]")
# The site's own (web.tests.CopyStyleTests.BANNED, without the dashes it checks for itself), then the standards'.
STOCK = ("lorem ipsum", "welcome to ripraptor", "learn more", "get started", "unlock", "supercharge", "seamless",
         "next-generation", "game-changing", "your ultimate", "all in one place", "elevate", "empowering",
         "revolutionise", "effortless", "powered by ai")
CLAIMS = {
    "guaranteed": "Say what is normally true instead of guaranteed.",
    "the best price": "Say the cheapest at every UK shop we track instead of the best price.",
    "every shop in the uk": "Say every UK shop we track.",
    "all uk shops": "Say every UK shop we track.",
}
# Marketplaces are not shops either: a listing there is shown on a product page beside the shops.
MARKETPLACE_HOSTS = ("ebay.", "amazon.", "vinted.", "facebook.com", "gumtree.")
DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y")


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    read: date


def lines(text):
    """The non-empty lines of a one-per-line field."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def style_problems(text, label):
    """What breaks the house style in one field, each said with the field's name."""
    found = []
    lowered = (text or "").lower()
    if not lowered:
        return found
    if EM_DASH in text or "&mdash;" in lowered:
        found.append(f"{label}: remove the em dash. Use a comma, a colon or a full stop.")
    if "!" in text:
        found.append(f"{label}: remove the exclamation mark.")
    if HTML.search(text):
        found.append(f"{label}: HTML is not used here. Write plain text.")
    for phrase in STOCK:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered):
            found.append(f"{label}: \"{phrase}\" is one of the phrases the site never uses.")
    for phrase, instead in CLAIMS.items():
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered):
            found.append(f"{label}: {instead}")
    return found


def link_problems(text, label, shop_hosts=()):
    """Links in a body that go to a shop, a marketplace or a plain http address."""
    found = []
    for match in re.finditer(r"\[[^\[\]\n]+\]\(([^)\s]*)\)", text or ""):
        address = match.group(1)
        if address.startswith("/") and not address.startswith("//"):
            continue
        if not address.startswith("https://"):
            found.append(f"{label}: {address} is not an https address or a RipRaptor page.")
            continue
        host = (urlsplit(address).hostname or "").lower()
        if any(mark in host for mark in MARKETPLACE_HOSTS) or any(
                host == shop or host.endswith("." + shop) for shop in shop_hosts):
            found.append(f"{label}: {host} is a shop or a marketplace. Add the product as a price card instead.")
    return found


def sources_in(text):
    """([Source], [problem]) from a sources field."""
    found, problems = [], []
    for line in lines(text):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or not all(parts):
            problems.append(f"Source \"{line}\": write it as title | address | date read.")
            continue
        title, url, when = parts
        if not url.startswith("https://") or not urlsplit(url).hostname:
            problems.append(f"Source \"{title}\": the address must start https://.")
            continue
        read = None
        for pattern in DATE_FORMATS:
            try:
                read = datetime.strptime(when, pattern).date()
                break
            except ValueError:
                continue
        if read is None:
            problems.append(f"Source \"{title}\": write the date read as 27 Oct 2026.")
            continue
        found.append(Source(title, url, read))
    return found, problems


def section_fields(section):
    return [("Question", section.question), ("Short answer", section.short_answer), ("Detail", section.body),
            ("Green flags", section.green_flags), ("Red flags", section.red_flags)]


def guide_fields(guide):
    return [("Title", guide.title), ("Title in search results", guide.search_title),
            ("One-line answer", guide.one_line_answer), ("Link words", guide.door_line),
            ("Checklist title", guide.checklist_title), ("Checklist", guide.checklist)]


def etb_problem(fields):
    """ETB is never used on its own: Elite Trainer Box comes first, in the guide's own order."""
    seen_full = False
    for label, text in fields:
        for match in re.finditer(r"elite trainer box|(?<![a-z])etbs?(?![a-z])", (text or "").lower()):
            if match.group(0) == "elite trainer box":
                seen_full = True
            elif not seen_full:
                return f"{label}: write Elite Trainer Box before using ETB, so a parent knows what it is."
    return None


def problems(guide, sections, shop_hosts=(), today=None):
    """{"style": [...], "publish": [...]}: style problems stop any save, publish problems stop publishing."""
    style, publish = [], []
    labelled = [(label, text) for label, text in guide_fields(guide)]
    for number, section in enumerate(sections, 1):
        name = f"Question {number}"
        labelled += [(f"{name}, {label.lower()}", text) for label, text in section_fields(section)]
    for label, text in labelled:
        style += style_problems(text, label)
    etb = etb_problem(labelled)
    if etb:
        style.append(etb)
    if not sections:
        publish.append("Add at least one question before publishing.")
    for number, section in enumerate(sections, 1):
        name = f"Question {number} ({section.question})" if section.question else f"Question {number}"
        style += link_problems(section.body, f"Question {number}, detail", shop_hosts)
        found, wrong = sources_in(section.sources)
        style += [f"Question {number}: {problem}" for problem in wrong]
        if section.states_facts and not found:
            publish.append(f"{name} states facts but has no source.")
    if guide.door and not guide.door_line:
        publish.append("Write the link words for where this guide is linked from.")
    if guide.last_reviewed is None or guide.next_review is None:
        publish.append("Fill in the last check and the next review.")
    else:
        if guide.next_review <= guide.last_reviewed:
            publish.append("The next review comes after the last check.")
        elif guide.next_review > guide.last_reviewed + timedelta(days=REVIEW_DAYS):
            publish.append(f"The next review is at most {REVIEW_DAYS} days after the last check.")
        if today and guide.last_reviewed > today:
            publish.append("The last check cannot be in the future.")
    return {"style": style, "publish": publish}


def inline(text):
    """One line or paragraph, escaped, with its [words](address) links."""
    out, last = [], 0
    for match in LINK.finditer(text):
        out.append(escape(text[last:match.start()]))
        words, address = match.group(1), match.group(2)
        if address.startswith("/") and not address.startswith("//"):
            out.append(format_html('<a href="{}">{}</a>', address, words))
        else:
            out.append(format_html('<a href="{}" rel="noopener">{}</a>', address, words))
        last = match.end()
    out.append(escape(text[last:]))
    return mark_safe("".join(str(part) for part in out))


def body_html(text):
    """A body as HTML: paragraphs, lists and links, nothing else."""
    blocks = [block for block in re.split(r"\n\s*\n", (text or "").strip()) if block.strip()]
    html = []
    for block in blocks:
        rows = [row for row in block.splitlines() if row.strip()]
        if all(BULLET.match(row) for row in rows):
            items = "".join(format_html("<li>{}</li>", inline(BULLET.sub("", row))) for row in rows)
            html.append(f"<ul>{items}</ul>")
        elif all(NUMBERED.match(row) for row in rows):
            items = "".join(format_html("<li>{}</li>", inline(NUMBERED.sub("", row))) for row in rows)
            html.append(f"<ol>{items}</ol>")
        else:
            html.append("<p>" + "<br>".join(str(inline(row.strip())) for row in rows) + "</p>")
    return mark_safe("".join(html))


def anchors(questions):
    """A short id for each question, unique in the guide, for its jump link."""
    taken, found = set(), []
    for number, question in enumerate(questions, 1):
        base = slugify(question)[:48].strip("-") or f"question-{number}"
        anchor, n = base, 2
        while anchor in taken:
            anchor, n = f"{base}-{n}", n + 1
        taken.add(anchor)
        found.append(anchor)
    return found


def reading_minutes(guide, sections):
    words = sum(len((text or "").split()) for _, text in guide_fields(guide) if _ != "Checklist")
    for section in sections:
        words += sum(len((text or "").split()) for _, text in section_fields(section))
    return max(1, round(words / WORDS_PER_MINUTE))
