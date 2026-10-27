"""
Guides written in Word, Google Docs or plain text, read into a Guide and its questions (Admin, Guides, Import a
guide), so marketing never fills in the Admin form.

The template (template.py builds it as a Word file and as text):

    # A parent's guide to buying trading cards        <- Heading 1: the title
    Web address: parents                              <- the guide's details, one per line
    One-line answer: What to buy, what it should cost and how to pay safely.
    ...
    ## What should I buy for my child?                 <- Heading 2: one per question
    Short answer: ...                                  <- the first paragraph if it has no label
    Any other paragraphs and lists: the detail.
    Good signs:          then a list
    Warning signs:       then a list
    Prices:              then product page addresses, one per line
    What is in the box:  a product type, such as Elite Trainer Box
    Price checker: yes
    Sources:             then one per line: the page's name, its full web address, the date read
    ## Checklist                                       <- the printable checklist, a list

A Word file is read paragraph by paragraph: its Heading 1 and Heading 2 styles (or Title) are the headings,
its bullets and numbered lists are lists, and its links keep their addresses. A text or Markdown file (Google
Docs' Download, Markdown) uses "#" and "##" and lines starting "-" or "1.". Labels are matched whatever the
capitals and with or without bold. What cannot be read is said, line by line, before anything is saved.
"""

import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

from django.utils.text import slugify

from .text import DATE_FORMATS

MAX_BYTES = 5 * 1024 * 1024          # an upload, and any one part of a Word file once unzipped
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
URL = re.compile(r"https?://[^\s,;<>\]\)]+")
MD_LINK = re.compile(r"\[([^\[\]\n]+)\]\((https?://[^\s()<>]+|/[^\s()<>]*)\)")
BULLET = re.compile(r"^\s*[-*••–]\s+")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+")

GUIDE_LABELS = {
    "web address": "slug", "address": "slug", "one-line answer": "one_line_answer", "one line answer": "one_line_answer",
    "summary": "one_line_answer", "title in google": "search_title", "search title": "search_title",
    "title in search results": "search_title", "for": "audience", "audience": "audience",
    "linked from": "door", "link words": "door_line", "last checked": "last_reviewed",
    "next review": "next_review", "owner": "owner", "checked by": "reviewed_by",
}
SECTION_LABELS = {
    "short answer": "short", "detail": "detail", "good signs": "green", "green flags": "green",
    "warning signs": "red", "red flags": "red", "prices": "prices", "price cards": "prices",
    "what is in the box": "box", "price checker": "checker", "fair price checker": "checker", "sources": "sources",
    "title": "title",
}
CHECKLIST_HEADINGS = ("checklist", "printable checklist")


@dataclass
class ReadSection:
    question: str
    short_answer: str = ""
    body: list = field(default_factory=list)       # blocks: a paragraph, or a list as lines
    green: list = field(default_factory=list)
    red: list = field(default_factory=list)
    products: list = field(default_factory=list)   # Product
    box_type: str = ""
    show_checker: bool = False
    sources: list = field(default_factory=list)    # "title | address | 27 Oct 2026"
    states_facts: bool = True

    @property
    def body_text(self):
        return "\n\n".join(self.body)


@dataclass
class ReadGuide:
    fields: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    checklist_title: str = ""
    checklist: list = field(default_factory=list)
    stops: list = field(default_factory=list)      # what stops it being saved at all
    notes: list = field(default_factory=list)      # worth a look, saved anyway


# Reading the file -----------------------------------------------------------------------------------------

def lines_from_upload(name, data):
    """The template's lines from an uploaded file: a Word file (.docx) or text (.txt, .md). Raises ValueError
    with what to do when it cannot be read."""
    if len(data) > MAX_BYTES:
        raise ValueError("The file is over 5 MB. A guide is words only: leave the pictures out.")
    if (name or "").lower().endswith(".docx") or data[:2] == b"PK":
        return docx_lines(data)
    if (name or "").lower().endswith((".doc", ".pages", ".odt", ".pdf")):
        raise ValueError("Save it as a Word file (.docx) or as plain text (.txt), then choose it again.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text_lines(text)


def text_lines(text):
    """Lines from text or Markdown: wrapped lines of one paragraph joined, blank lines dropped, Markdown's
    escapes and bold taken off."""
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = clean_markdown(raw.rstrip())
        if not line.strip():
            out.append("")
            continue
        joins = (out and out[-1] and not starts_block(line) and not starts_block(out[-1])
                 and not is_label_line(line) and not is_label_line(out[-1]))
        if joins:
            out[-1] = out[-1] + " " + line.strip()
        else:
            out.append(line.strip() if not BULLET.match(line) and not NUMBERED.match(line) else line.strip())
    return [line for line in out if line]


def clean_markdown(line):
    line = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", line)       # Google Docs escapes these
    line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)                   # bold
    return line


def starts_block(line):
    return line.lstrip().startswith("#") or bool(BULLET.match(line) or NUMBERED.match(line))


def is_label_line(line):
    head = line.split(":", 1)[0].strip().lower() if ":" in line else ""
    return head in GUIDE_LABELS or head in SECTION_LABELS


def docx_lines(data):
    """The template's lines from a Word file: Heading 1 or Title as "#", Heading 2 as "##", lists as "-" or
    "1.", links as [words](address). Deleted tracked changes are left out."""
    try:
        package = zipfile.ZipFile(_bytes_io(data))
        parts = {info.filename: info for info in package.infolist()}
        if "word/document.xml" not in parts:
            raise ValueError
        for info in parts.values():
            if info.file_size > MAX_BYTES * 4:
                raise ValueError("A part of the Word file is too large to read.")
        document = ElementTree.fromstring(package.read("word/document.xml"))
        links = _relationships(package, parts)
        styles = _styles(package, parts)
        numbering = _numbering(package, parts)
    except ValueError as exc:
        raise ValueError(str(exc) or "This is not a Word file the site can read. Save it as .docx again.") from exc
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise ValueError("This is not a Word file the site can read. Save it as .docx again.") from exc
    lines = []
    for paragraph in document.iter(W + "p"):
        text = _paragraph_text(paragraph, links).strip()
        if not text:
            continue
        properties = paragraph.find(W + "pPr")
        level = _heading_level(properties, styles)
        if level:
            lines.append("#" * level + " " + text)
            continue
        numbered = properties is not None and properties.find(W + "numPr") is not None
        if numbered:
            num = properties.find(W + "numPr/" + W + "numId")
            kind = numbering.get(num.get(W + "val") if num is not None else "", "bullet")
            lines.append(("1. " if kind == "decimal" else "- ") + text)
        else:
            lines.append(clean_markdown(text))
    return lines


def _bytes_io(data):
    import io

    return io.BytesIO(data)


def _relationships(package, parts):
    name = "word/_rels/document.xml.rels"
    if name not in parts:
        return {}
    root = ElementTree.fromstring(package.read(name))
    return {item.get("Id"): item.get("Target") for item in root if item.get("TargetMode") == "External"}


def _styles(package, parts):
    """{style id: heading level} from the Word file's styles: Title and Heading 1 are 1, Heading 2 is 2."""
    if "word/styles.xml" not in parts:
        return {}
    found = {}
    for style in ElementTree.fromstring(package.read("word/styles.xml")).iter(W + "style"):
        ident = style.get(W + "styleId", "")
        name_node = style.find(W + "name")
        name = (name_node.get(W + "val", "") if name_node is not None else ident).lower()
        outline = style.find(W + "pPr/" + W + "outlineLvl")
        if name == "title" or name == "heading 1":
            found[ident] = 1
        elif name == "heading 2":
            found[ident] = 2
        elif name.startswith("heading "):
            found[ident] = 3
        elif outline is not None and outline.get(W + "val", "").isdigit():
            found[ident] = min(int(outline.get(W + "val")) + 1, 3)
    return found


def _numbering(package, parts):
    """{list id: "decimal" or "bullet"} from the Word file's numbering, by its first level."""
    if "word/numbering.xml" not in parts:
        return {}
    root = ElementTree.fromstring(package.read("word/numbering.xml"))
    abstract = {}
    for item in root.iter(W + "abstractNum"):
        level = item.find(W + "lvl")
        fmt = level.find(W + "numFmt") if level is not None else None
        abstract[item.get(W + "abstractNumId")] = fmt.get(W + "val") if fmt is not None else "bullet"
    found = {}
    for num in root.iter(W + "num"):
        ref = num.find(W + "abstractNumId")
        found[num.get(W + "numId")] = abstract.get(ref.get(W + "val") if ref is not None else "", "bullet")
    return found


def _heading_level(properties, styles):
    if properties is None:
        return 0
    style = properties.find(W + "pStyle")
    if style is not None and style.get(W + "val") in styles:
        return styles[style.get(W + "val")]
    style_id = (style.get(W + "val", "") if style is not None else "").lower()
    if style_id in ("title", "heading1"):
        return 1
    if style_id == "heading2":
        return 2
    outline = properties.find(W + "outlineLvl")
    if outline is not None and outline.get(W + "val", "").isdigit():
        return min(int(outline.get(W + "val")) + 1, 3)
    return 0


def _paragraph_text(paragraph, links):
    out = []

    def walk(node, link=None):
        for child in node:
            tag = child.tag
            if tag in (W + "del", W + "pPr", W + "rPr", W + "delText", W + "instrText"):
                continue
            if tag == W + "hyperlink":
                target = links.get(child.get(R + "id", ""))
                start = len(out)
                walk(child)
                words = "".join(out[start:])
                if target and words.strip():
                    del out[start:]
                    out.append(f"[{words}]({target})")
                continue
            if tag == W + "t":
                out.append(child.text or "")
            elif tag in (W + "tab", W + "br", W + "cr"):
                out.append(" ")
            else:
                walk(child)

    walk(paragraph)
    return re.sub(r"[ \t]+", " ", "".join(out))


# Understanding the lines ----------------------------------------------------------------------------------

def split_label(line):
    """(field, value) when ``line`` starts with a known label, else (None, line)."""
    if ":" not in line or line.lstrip().startswith("#"):
        return None, line
    head, value = line.split(":", 1)
    key = head.strip().strip("*_ ").lower()
    return key, value.strip()


def read(lines, find_product, product_types, today=None):
    """ReadGuide from the template's lines. ``find_product(address)`` gives a Product or None;
    ``product_types`` is {words: type code}."""
    guide = ReadGuide()
    section = None
    in_checklist = False
    listing = None        # the label whose list the next list lines belong to
    title_seen = False
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            words = stripped.lstrip("#").strip()
            listing = None
            if level == 1 and not title_seen:
                guide.fields["title"] = words
                title_seen = True
                continue
            if level <= 2 and words.strip().rstrip(":").lower() in CHECKLIST_HEADINGS:
                in_checklist, section = True, None
                continue
            if level <= 2:
                in_checklist = False
                section = ReadSection(question=words)
                guide.sections.append(section)
                continue
            if section is not None:          # a smaller heading inside a question: a paragraph of its detail
                section.body.append(words)
            continue
        key, value = split_label(stripped)
        if in_checklist:
            if key == "title" and not guide.checklist_title:
                guide.checklist_title = value
            else:
                guide.checklist.append(_item(stripped))
            continue
        if section is None:
            if key in GUIDE_LABELS:
                guide.fields[GUIDE_LABELS[key]] = value
            elif not title_seen:
                guide.stops.append("Start with the guide's title, as Heading 1 (or a line starting #).")
                title_seen = True
            else:
                guide.notes.append(f"Line {number} is before the first question and is not one of the guide's "
                                   f"details, so it was left out: \"{_short(stripped)}\"")
            continue
        label = SECTION_LABELS.get(key) if key else None
        is_item = bool(BULLET.match(line) or NUMBERED.match(line))
        if label:
            listing = None
            if label == "short":
                if value:
                    section.short_answer = (section.short_answer + " " + value).strip()
                else:
                    listing = "short"
            elif label == "detail":
                if value:
                    section.body.append(value)
            elif label in ("green", "red"):
                listing = label
                if value:
                    getattr(section, label).append(value)
            elif label == "prices":
                listing = "prices"
                if value:
                    _add_products(section, value, find_product, guide)
            elif label == "box":
                code = product_types.get(_words(value))
                if code:
                    section.box_type = code
                elif value:
                    guide.notes.append(f"\"{section.question}\": What is in the box: \"{value}\" is not a product "
                                       f"type, so no box card was added. Use one of: "
                                       f"{', '.join(sorted({w.capitalize() for w in product_types}))}.")
            elif label == "checker":
                section.show_checker = _words(value) not in ("no", "none", "")
            elif label == "sources":
                listing = "sources"
                if _words(value).startswith(("none", "no ")) or _words(value) == "no":
                    section.states_facts = False
                    listing = None
                elif value:
                    _add_source(section, value, guide)
            elif label == "title":
                section.body.append(stripped)
            continue
        if listing == "short" and not is_item:
            section.short_answer = stripped
            listing = None
            continue
        if listing in ("green", "red") and is_item:
            getattr(section, listing).append(_item(stripped))
            continue
        if listing == "prices" and (is_item or URL.search(stripped) or "/products/" in stripped):
            _add_products(section, _item(stripped), find_product, guide)
            continue
        if listing == "sources" and (is_item or URL.search(stripped)):
            _add_source(section, _item(stripped), guide)
            continue
        listing = None
        if not section.short_answer and not is_item:
            section.short_answer = stripped          # the first paragraph, when it has no label
            continue
        _add_detail(section, line)
    if "title" not in guide.fields and not any("title" in stop for stop in guide.stops):
        guide.stops.append("Start with the guide's title, as Heading 1 (or a line starting #).")
    if not guide.sections:
        guide.stops.append("Add at least one question, as Heading 2 (or a line starting ##).")
    for section in guide.sections:
        if not section.short_answer:
            guide.stops.append(f"\"{section.question}\" has no short answer. Write it on the line after the question.")
    _settle_fields(guide, today)
    return guide


def _item(line):
    return NUMBERED.sub("", BULLET.sub("", line)).strip()


def _words(text):
    return " ".join((text or "").lower().replace("-", " ").split())


def _short(text, length=80):
    return text if len(text) <= length else text[:length - 1] + "…"


def _add_detail(section, line):
    """A paragraph, or a list line joined to the list before it, so a list stays one list."""
    if BULLET.match(line) or NUMBERED.match(line):
        marker = "1. " if NUMBERED.match(line) else "- "
        item = marker + _item(line)
        if section.body and section.body[-1].split("\n")[-1].startswith(marker.strip()):
            section.body[-1] += "\n" + item
        else:
            section.body.append(item)
    else:
        section.body.append(line.strip())


def _add_products(section, text, find_product, guide):
    for address in [part for part in re.split(r"[\s,;]+", MD_LINK.sub(r"\2", text)) if part]:
        product = find_product(address)
        if product is None:
            guide.notes.append(f"\"{section.question}\": no product at {address}, so it has no price card. Copy "
                               f"the address of the product's page on RipRaptor.")
        elif product not in section.products:
            section.products.append(product)


def _add_source(section, line, guide):
    linked = MD_LINK.search(line)
    if linked and linked.group(2).startswith("http"):
        url, rest = linked.group(2), line[linked.end():]
        title = " ".join((line[:linked.start()] + " " + linked.group(1)).split()).strip(" ,;-–")
    else:
        found = URL.search(line)
        if not found:
            guide.stops.append(f"\"{section.question}\": the source \"{_short(line)}\" has no web address. Paste the "
                               f"full address, starting https://.")
            return
        url = found.group(0).rstrip(".")
        title = line[:found.start()].strip(" ,;:-–(")
        rest = line[found.end():]
    rest = re.sub(r"^[\s,;:)\-–.]+", "", rest)
    rest = re.sub(r"^(?:(?:read|accessed|checked|on)\s+)+", "", rest.strip(), flags=re.I).strip(" .)")
    read_on = _date(rest)
    if url.startswith("http://"):
        guide.stops.append(f"\"{section.question}\": the source {url} starts http://. Use its https:// address.")
        return
    if read_on is None:
        guide.stops.append(f"\"{section.question}\": add the date you read \"{_short(title or url)}\", for example "
                           f"27 Oct 2026, after its address.")
        return
    title = title or (urlsplit(url).hostname or url)
    section.sources.append(f"{title} | {url} | {read_on:%-d %b %Y}")


def _date(text):
    text = (text or "").strip()
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


AUDIENCES = (("parent", "parents"), ("gift", "parents"), ("collector", "collectors"))
DOORS = (("sold", "sold_out"), ("scalper", "sold_out"), ("product", "buying"), ("price", "buying"))


def _settle_fields(guide, today):
    fields = guide.fields
    slug_text = fields.get("slug", "").strip()
    slug_text = re.sub(r"^https?://[^/]+", "", slug_text)
    slug_text = slug_text.strip("/").removeprefix("guides/").strip("/")
    fields["slug"] = slugify(slug_text or fields.get("title", ""))[:60]
    audience = _words(fields.get("audience", ""))
    fields["audience"] = next((code for word, code in AUDIENCES if word in audience), "everyone")
    door = _words(fields.get("door", ""))
    fields["door"] = "" if not door or door.startswith(("nowhere", "none", "no")) else next(
        (code for word, code in DOORS if word in door), "")
    for name, label in (("last_reviewed", "Last checked"), ("next_review", "Next review")):
        value = fields.get(name, "")
        when = _date(value) if value else None
        if value and when is None:
            guide.stops.append(f"{label}: write the date as 27 Oct 2026.")
        fields[name] = when
    for name in ("title", "one_line_answer", "search_title", "door_line", "owner", "reviewed_by"):
        fields[name] = fields.get(name, "").strip()
    if not fields["one_line_answer"]:
        guide.stops.append("Add a One-line answer: line under the title.")


# Checking and saving --------------------------------------------------------------------------------------

def find_product(address):
    """The Product a product page address (or its last part) names, or None."""
    from catalogue.models import Product, ProductAlias

    path = urlsplit(address.strip()).path if "://" in address else address.strip()
    host = (urlsplit(address).hostname or "").lower() if "://" in address else ""
    if host and not host.endswith(("ripraptor.com", "localhost", "127.0.0.1", "testserver")):
        return None
    slug = path.strip("/").split("/")[-1] if path.strip("/") else ""
    if not slug:
        return None
    product = Product.objects.active().filter(slug=slug).first()
    if product is None:
        alias = ProductAlias.objects.filter(slug=slug, product__is_active=True).select_related("product").first()
        product = alias.product if alias else None
    return product


def product_type_words():
    """{"elite trainer box": "elite_trainer_box", "elite trainer boxes": ..., ...}: what may follow What is in the box."""
    from catalogue.models import Product

    found = {}
    for code, label in Product.Type.choices:
        if code == "other":
            continue
        words = _words(label)
        for form in (words, words + "s", words + "es", _words(code.replace("_", " "))):
            found[form] = code
    return found


@dataclass
class Checked:
    read: ReadGuide
    guide: object            # an unsaved Guide with the file's details
    sections: list           # unsaved Sections
    style: list              # stops any save, with the reading's own stops
    publish: list            # stops publishing
    existing: object = None  # the Guide already at this address, if any

    @property
    def can_save(self):
        return not self.style

    @property
    def can_publish(self):
        return not self.style and not self.publish


def check(lines, today=None):
    """Read the lines and check them as Admin would (guides/text.py). Nothing is saved."""
    from django.utils import timezone

    from .admin import shop_hosts
    from .models import Guide, Section

    today = today or timezone.localdate()
    read_guide = read(lines, find_product, product_type_words(), today)
    fields = read_guide.fields
    guide = Guide(**{name: fields.get(name) for name in (
        "slug", "title", "search_title", "one_line_answer", "audience", "door", "door_line", "owner",
        "reviewed_by", "last_reviewed", "next_review")})
    guide.checklist_title = read_guide.checklist_title
    guide.checklist = "\n".join(read_guide.checklist)
    sections = [Section(question=item.question[:160], short_answer=item.short_answer, body=item.body_text,
                        green_flags="\n".join(item.green), red_flags="\n".join(item.red), box_type=item.box_type,
                        show_checker=item.show_checker, sources="\n".join(item.sources),
                        states_facts=item.states_facts, position=n)
                for n, item in enumerate(read_guide.sections)]
    from . import text

    found = text.problems(guide, sections, shop_hosts(), today)   # every problem at once, not one batch at a time
    style = list(read_guide.stops) + found["style"]
    from .models import RESERVED

    if guide.slug in RESERVED:
        style.append(f"Web address: /guides/{guide.slug}/ is kept for another page. Choose another.")
    if len(guide.title) > 120:
        style.append("The title is over 120 characters. Make it shorter.")
    if len(guide.one_line_answer) > 200:
        style.append("The one-line answer is over 200 characters. Make it shorter.")
    if len(guide.search_title) > 70:
        style.append("The title in Google is over 70 characters. Keep it under 50.")
    if len(guide.door_line) > 80:
        style.append("The link words are over 80 characters. Make them shorter.")
    return Checked(read=read_guide, guide=guide, sections=sections, style=style, publish=found["publish"],
                   existing=Guide.objects.filter(slug=guide.slug).first() if guide.slug else None)


def save(checked, publish=False):
    """Save a checked guide: a new one, or the one already at its address with all its questions replaced.
    Returns the Guide. Published only when asked and nothing stops it; otherwise saved as a draft."""
    from django.db import transaction
    from django.utils import timezone

    from .models import Guide

    if not checked.can_save:
        raise ValueError("This guide has problems to fix before it can be saved.")
    publish = publish and checked.can_publish
    new = checked.guide
    with transaction.atomic():
        guide = Guide.objects.filter(slug=new.slug).first() or Guide(slug=new.slug)
        for name in ("title", "search_title", "one_line_answer", "audience", "door", "door_line", "owner",
                     "reviewed_by", "last_reviewed", "next_review", "checklist_title", "checklist"):
            setattr(guide, name, getattr(new, name))
        guide.is_published = publish
        if publish and guide.published_at is None:
            guide.published_at = timezone.now()
        guide.save()
        guide.sections.all().delete()
        for section, item in zip(checked.sections, checked.read.sections):
            section.guide = guide
            section.save()
            if item.products:
                section.products.set(item.products)
    return guide
