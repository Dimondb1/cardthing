"""
Who is on the site now, for the owner's Who is online page (web/online.py).

Each counted page view and each page's report that it was shown (catalogue/insights.py) note the visitor in
LiveVisitor: their internet address, country, device, the site that sent them, the page they are on and when. The
address is personal data, so it is kept only while they are here: ``forget`` deletes a row LIVE_KEEP minutes after
the visitor's last page. The background reader runs it every few minutes, tidy_all every hour. Nothing here goes
into DailyVisitor, a report, an export or Insights.

``sample`` counts who is online every five minutes into OnlineCount (counts only), so the page can show how many
were here at any point of the last day. The owner, 7 Nov 2026: "so I can see the IP address and the location and
how many people are online at any given point".
"""

from datetime import timedelta
from urllib.parse import urlsplit

from django.utils import timezone

from . import geo
from .models import DailyVisitor, LiveVisitor, OnlineCount
from .pricing import drop_if_locked, retry_locked

LIVE_KEEP = 30          # minutes after a visitor's last page that their row, and address, are kept
ONLINE_MINUTES = 5      # on the site now: a page in the last this many minutes, as the dashboard's On the site now
SAMPLE_EVERY = 5        # minutes between counts
ONLINE_DAYS = 8         # days of counts kept


def seen(request, token, day, page="", path="", browser=False):
    """Note this visitor as on the site now, in one query: the row is made on their first page and its page and time
    kept current after. ``browser`` says their browser ran the page (its report); a later page view never undoes it.
    Their page count is DailyVisitor's. Best effort: a busy database drops the note, never the page."""
    from .insights import client_ip, device_of, source_of

    now = timezone.now()
    ip = client_ip(request)
    # The page's own address: the one its report names, else the request's, never the report's own (/api/note/).
    where = urlsplit(path).path if path else ("" if request.path.startswith("/api/") else request.path)
    row = LiveVisitor(
        date=day, token=token, ip=ip or None, country=geo.country_of(ip), device=device_of(request),
        source=source_of(request), agent=request.headers.get("User-Agent", "")[:200], page=page[:120],
        path=where[:200], browser=browser, first_seen=now, last_seen=now,
    )
    fields = (["last_seen"] + (["page"] if page else []) + (["path"] if where else [])
              + (["browser"] if browser else []))
    with drop_if_locked("Online"):
        retry_locked(lambda: LiveVisitor.objects.bulk_create(
            [row], update_conflicts=True, unique_fields=["token"], update_fields=fields))


def forget(now=None):
    """Delete the rows, and so the addresses, of visitors whose last page was more than LIVE_KEEP minutes ago, and
    counts older than ONLINE_DAYS. Returns how many visitors were forgotten."""
    now = now or timezone.now()
    gone, _ = LiveVisitor.objects.filter(last_seen__lt=now - timedelta(minutes=LIVE_KEEP)).delete()
    OnlineCount.objects.filter(at__lt=now - timedelta(days=ONLINE_DAYS)).delete()
    return gone


MARK_EVERY = 30   # seconds: this page and the dashboard judge today's newest visitors at most this often themselves


def marked_lately(now=None):
    """Judge today's newest visitors (catalogue/automated.py) before they are counted, at most every MARK_EVERY
    seconds, so a flood's latest scripts are not shown as people until the background reader's next turn (the
    owner, 8 Nov 2026, with 132 "people" on Who is online, most of them the flood's last few minutes)."""
    from django.core.cache import cache

    from . import automated

    if cache.add("online:marked", True, MARK_EVERY):
        automated.classify(now=now, days=1)


def online(now=None):
    """The LiveVisitor rows on the site now, newest page first, each with ``person`` (its browser ran the page and it
    was not found to be automated) and ``why`` (what the classifier found, from its DailyVisitor row). Two queries."""
    now = now or timezone.now()
    marked_lately(now)
    rows = list(LiveVisitor.objects.filter(last_seen__gte=now - timedelta(minutes=ONLINE_MINUTES)).order_by("-last_seen"))
    days = {
        token: (views, automated, why) for token, views, automated, why in
        DailyVisitor.objects.filter(date__in={row.date for row in rows}, token__in=[row.token for row in rows])
        .values_list("token", "views", "automated", "automated_why")
    } if rows else {}
    for row in rows:
        views, automated, why = days.get(row.token, (0, False, ""))
        row.views, row.why = views, why if automated else ""
        row.person = row.browser and not automated
    return rows


def counts(now=None):
    """(people, automated) on the site now, in one query."""
    now = now or timezone.now()
    marked_lately(now)
    since = now - timedelta(minutes=ONLINE_MINUTES)
    rows = list(LiveVisitor.objects.filter(last_seen__gte=since).values_list("date", "token", "browser"))
    if not rows:
        return 0, 0
    marked = set(
        DailyVisitor.objects.filter(date__in={row[0] for row in rows}, token__in=[row[1] for row in rows],
                                    automated=True).values_list("token", flat=True)
    )
    people = sum(1 for _date, token, browser in rows if browser and token not in marked)
    return people, len(rows) - people


def sample(now=None):
    """Count who is online now into OnlineCount, at most once every SAMPLE_EVERY minutes (the moment rounded down to
    it), and forget the visitors who have gone. Returns the count saved, or None when this slot already has one."""
    now = now or timezone.now()
    slot = now.replace(second=0, microsecond=0)
    slot -= timedelta(minutes=slot.minute % SAMPLE_EVERY)
    forget(now)
    if OnlineCount.objects.filter(at=slot).exists():
        return None
    people, automated = counts(now)
    found, _ = OnlineCount.objects.get_or_create(at=slot, defaults={"people": people, "automated": automated})
    return found


def day_of_counts(now=None, hours=24):
    """The counts of the last ``hours`` hours, oldest first, and the most people at once in them."""
    now = now or timezone.now()
    rows = list(OnlineCount.objects.filter(at__gte=now - timedelta(hours=hours)).order_by("at"))
    peak = max(rows, key=lambda row: (row.people, row.at), default=None)
    return rows, peak
