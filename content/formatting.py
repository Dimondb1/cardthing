"""Placeholder handling and style checks for editable copy."""

import re
import string

from django.utils.html import escape, linebreaks
from django.utils.safestring import mark_safe

from .registry import Kind

_formatter = string.Formatter()
_name_re = re.compile(r"^[a-z_]+$")
_html_re = re.compile(r"<\s*/?\s*[a-zA-Z]")

EM_DASH = "—"


def placeholders_in(text):
    """Return the placeholder names used in ``text``.

    Raises ValueError for malformed braces or complex fields such as
    ``{price.amount}``.
    """
    names = []
    for _literal, field, spec, conversion in _formatter.parse(text):
        if field is None:
            continue
        if not _name_re.match(field) or spec or conversion:
            raise ValueError(field)
        names.append(field)
    return names


def fill(text, values):
    """Replace ``{name}`` placeholders. Unknown names become empty strings."""
    try:
        parts = list(_formatter.parse(text))
    except ValueError:
        return text
    out = []
    for literal, field, _spec, _conversion in parts:
        out.append(literal)
        if field is not None:
            value = values.get(field, "")
            out.append("" if value is None else str(value))
    return "".join(out)


def to_html(text, kind):
    """Escape and, for paragraph text, wrap in <p> tags."""
    if kind == Kind.PARAGRAPHS:
        return mark_safe(linebreaks(escape(text.strip())))
    return text


def check(text, *, kind, allowed=(), optional=False, legal=False):
    """Return a list of problems with ``text``. An empty list means it is fine."""
    problems = []
    stripped = text.strip()

    if not stripped:
        if legal or not optional:
            problems.append("This text can't be empty.")
        return problems

    if EM_DASH in text or "&mdash;" in text:
        problems.append(
            "Remove the em dash. Use a comma, colon or brackets instead."
        )

    if kind != Kind.PARAGRAPHS and "\n" in stripped:
        problems.append("Keep this text on one line.")

    if _html_re.search(text):
        problems.append("HTML isn't supported here. Write plain text.")

    try:
        used = placeholders_in(text)
    except ValueError:
        problems.append(
            "Curly brackets are only for placeholders such as {price}. "
            "To show a curly bracket, type it twice: {{ or }}."
        )
    else:
        unknown = sorted({name for name in used if name not in allowed})
        if unknown and allowed:
            problems.append(
                "Unknown placeholder {}. You can use: {}.".format(
                    ", ".join("{%s}" % name for name in unknown),
                    ", ".join("{%s}" % name for name in allowed),
                )
            )
        elif unknown:
            problems.append(
                "This text doesn't support placeholders. Remove {}.".format(
                    ", ".join("{%s}" % name for name in unknown)
                )
            )

    return problems
