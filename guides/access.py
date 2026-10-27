"""A Guide editors login (guides/apps.py) is for writing guides: the owner's own pages (the dashboard, Insights,
Things to check with its Claude key, Crawl health) stay closed to it, even though it has Staff status."""

from functools import wraps

from django.core.exceptions import PermissionDenied


def editor_only(user):
    """True for a Guide editor who is not the owner."""
    from .apps import EDITORS

    return bool(user and user.is_authenticated and not user.is_superuser
                and user.groups.filter(name=EDITORS).exists())


def not_for_editors(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if editor_only(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return guarded
