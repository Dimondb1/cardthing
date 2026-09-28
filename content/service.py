"""
Read site wording.

Every active row is loaded in one query, at most once per request: the
``{% copy %}`` tag and views share a store attached to the request. There is
no cross-request cache, so an edit in Django Admin shows on the next page
load, whichever server process handles it.
"""

import logging

from django.conf import settings
from django.db import DatabaseError

from .formatting import fill, to_html
from .registry import ENTRIES, REGISTRY

logger = logging.getLogger(__name__)


def load_all():
    from .models import SiteContent

    try:
        return dict(SiteContent.objects.filter(active=True).values_list("key", "content"))
    except DatabaseError:
        # Table missing, for example before the first migrate. Defaults are used.
        return {}


def _raw(key, store):
    entry = REGISTRY.get(key)
    if entry is None:
        logger.warning("Unknown site text key %r", key)
        if settings.DEBUG:
            return f"[{key}]", None
        return "", None

    text = store.get(key) if store is not None else None
    if text is None or (not text.strip() and not entry.optional):
        text = entry.default
    return text, entry


def get(key, store=None, **values):
    """Return plain text for ``key`` with placeholders filled in."""
    text, _entry = _raw(key, load_all() if store is None else store)
    return fill(text, values).strip()


def render(key, store=None, **values):
    """Return text ready for a template: plain for one-liners, <p> for paragraphs."""
    text, entry = _raw(key, load_all() if store is None else store)
    text = fill(text, values).strip()
    if entry is None or not text:
        return text
    return to_html(text, entry.kind)


class RequestStore:
    """Loads wording on first use and keeps it for the rest of the request."""

    def __init__(self):
        self._data = None

    def get(self, key, default=None):
        if self._data is None:
            self._data = load_all()
        return self._data.get(key, default)


def for_request(request):
    """The store for this request, shared by views and templates."""
    store = getattr(request, "_site_content", None)
    if store is None:
        store = RequestStore()
        request._site_content = store
    return store


def sync_defaults(using="default", prune=False, stdout=None):
    """Create missing rows and refresh labels, descriptions and defaults.

    Edited wording is never overwritten. Wording that still matches the old
    default follows the new default.
    """
    from .models import SiteContent

    rows = {row.key: row for row in SiteContent.objects.using(using).all()}
    created = updated = 0

    for position, entry in enumerate(ENTRIES):
        fields = {
            "position": position,
            "label": entry.label,
            "section": entry.section,
            "kind": entry.kind,
            "description": entry.help,
            "placeholders": ",".join(entry.placeholders),
            "is_legal": entry.legal,
            "default_content": entry.default,
        }
        row = rows.get(entry.key)
        if row is None:
            SiteContent.objects.using(using).create(
                key=entry.key, content=entry.default, **fields
            )
            created += 1
            continue

        changed = []
        if not row.is_edited and row.content != entry.default:
            row.content = entry.default
            changed.append("content")
        for name, value in fields.items():
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed.append(name)
        if entry.legal and not row.active:
            row.active = True
            changed.append("active")
        if changed:
            row.save(using=using, update_fields=changed + ["updated_at"])
            updated += 1

    removed = 0
    orphans = [key for key in rows if key not in REGISTRY]
    if orphans and prune:
        removed, _ = SiteContent.objects.using(using).filter(key__in=orphans).delete()

    if stdout is not None:
        stdout.write(
            f"Site text: {created} created, {updated} updated, {removed} removed."
        )
        if orphans and not prune:
            stdout.write(
                "Not in the registry (run with --prune to delete): " + ", ".join(orphans)
            )
    return created, updated, removed
