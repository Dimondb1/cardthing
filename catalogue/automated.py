"""
Which visits and clicks were scripts, though they looked like people.

From 31 Oct 2026 a visitor counts as a person once their browser runs the page's own script (DailyVisitor.browser).
Headless browsers run it too: on 5 and 6 Nov 2026 about 13,000 of them in two days, most opening one page from
abroad with no link to us, and on 4 and 5 Nov thousands of clicks to shops from visitors whose browser never ran a
page. They filled the people and clicks every count reads: Today's best deals, which products the stock checks
treat as hot, the dashboard and the report. Whole days were never flagged as bursts, since browsers were trusted.
The owner, 7 Nov 2026: "fix the bot clicks and burst detection, but factor it was a massive Delta Reign Pokémon
drop yesterday".

``classify`` marks, on evidence, never on a guess about one visitor:

1. **Direct hits.** A visitor who sent one of our shop links (/go/) without coming from our page that day
   (OutboundClick.suspect_why referer or page; any direct hit from before reasons were kept) is a script. A person
   pressing Buy on our page always sends our page with it. Too many clicks in an hour or a prefetch are not
   evidence: a busy shared connection or a browser's own guess.
2. **Floods.** One-page visits with no link to us (typed, or an app that sends none) from outside the UK and Ireland,
   by browsers new to the site, far above their usual number that day (FLOOD_TIMES the median and FLOOD_SPREADS
   robust spreads above it, at least FLOOD_MIN; with too few days to know the usual, at least FLOOD_FALLBACK and
   half the day's people). A drop brings people from the UK, on phones, from Discord and Facebook, opening pages of
   one set: none of them is ever in a flood. Only the flood is left out; the day and its real people count.
3. **Clicks.** A tracked click whose visitor was marked, and one from before clicks were checked at the time
   (OutboundClick.ran_page empty) whose visitor's browser never ran a page that day, is automated: left out of
   every click count. Clicks since are checked when made (catalogue/clicks.py, reason "page").
4. **Page views.** A marked visitor who opened one page takes that view off the day's page counts
   (DailyPageView), so the products a flood opened are not the most opened, or hot for the stock checks.

Each visitor and click is marked once and never unmarked by a later run. The background reader runs it on today
every few minutes, tidy_all on the last WINDOW_DAYS every hour, and the installer once after an update.
"""

from datetime import timedelta
from statistics import median

from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.functions import Greatest
from django.utils import timezone

from .models import DailyPageView, DailyVisitor, OutboundClick

WINDOW_DAYS = 36            # as far back as the traffic explainer looks
HOME_COUNTRIES = ("GB", "IE")
DIRECT_REASONS = ("referer", "page", "")   # "" is a direct hit from before reasons were kept
FLOOD_MIN = 100
FLOOD_TIMES = 3
FLOOD_SPREADS = 5
FLOOD_FALLBACK = 300
FLOOD_SHARE = 0.5
BASELINE_DAYS = 4           # whole days needed to know a day's usual
CLICK_GRACE = timedelta(minutes=2)   # a click this new may be ahead of its page's report: judged next time


def drive_by_q():
    """A visitor in a flood: their browser ran one page, came with no link to us, is new to the site and is from a
    known country outside the UK and Ireland. An unknown country is never one: with no country database every
    visitor's is unknown."""
    return (Q(browser=True, returning=False, views__lte=1, source="") & ~Q(country="")
            & ~Q(country__in=HOME_COUNTRIES))


def robust(values):
    mid = median(values)
    spread = median(abs(v - mid) for v in values)
    return mid, max(1.4826 * spread, 0.25 * mid)


def flood_days(counts, browsers, already, today):
    """{date: (count, usual)} of the days in ``counts`` ({date: drive-bys}) that are floods, judged in date order
    against the whole days before each that were not floods. ``browsers`` is each day's browsers, ``already`` the
    days marked before."""
    found = {}
    calm = []
    for day in sorted(counts):
        n = counts[day]
        usual = None
        if len(calm) >= BASELINE_DAYS:
            usual, spread = robust(calm[-28:])
            flood = n >= max(FLOOD_MIN, FLOOD_TIMES * usual, usual + FLOOD_SPREADS * spread)
        else:
            flood = n >= FLOOD_FALLBACK and n >= FLOOD_SHARE * browsers.get(day, 0)
        if flood or day in already:
            found[day] = (n, usual)
        elif day < today:
            calm.append(n)
    return found


def classify(now=None, days=WINDOW_DAYS):
    """Mark the automated visitors and clicks of the last ``days`` days (today included). Returns
    {"direct", "flood", "clicks", "views"}: how many of each were marked by this run."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    since = today - timedelta(days=days)
    browser_since = (DailyVisitor.objects.filter(browser=True, date__gte=since).order_by("date")
                     .values_list("date", flat=True).first())
    done = {"direct": 0, "flood": 0, "clicks": 0, "views": 0}
    if browser_since is None:
        return done
    start = timezone.make_aware(timezone.datetime.combine(browser_since, timezone.datetime.min.time()))
    newly = []   # (date, landing, views) of the visitors this run marked, for their page views

    def mark(rows, why):
        rows = rows.filter(automated=False)
        newly.extend(rows.filter(views__lte=1).exclude(landing="").values_list("date", "landing"))
        n = rows.update(automated=True, automated_why=why)
        done[why] += n

    # 1. Direct hits: by day, the visitors who sent a shop link without our page.
    hits = {}
    for day, token in (
        OutboundClick.objects.suspects().filter(suspect_why__in=DIRECT_REASONS, created_at__gte=start)
        .exclude(visitor="").order_by().values_list("created_at__date", "visitor").distinct()
    ):
        hits.setdefault(day, set()).add(token)
    for day, tokens in hits.items():
        tokens = list(tokens)
        for at in range(0, len(tokens), 500):
            mark(DailyVisitor.objects.filter(date=day, token__in=tokens[at:at + 500], browser=True), "direct")

    # 2. Floods, judged on every day the window holds since browsers were known, and marked on the days asked for.
    window = today - timedelta(days=WINDOW_DAYS)
    rows = (DailyVisitor.objects.filter(date__gte=max(window, browser_since), date__lte=today).order_by()
            .values("date").annotate(drive_bys=Count("id", filter=drive_by_q()),
                                     browsers=Count("id", filter=Q(browser=True)),
                                     marked=Count("id", filter=Q(automated_why="flood"))))
    counts = {row["date"]: row["drive_bys"] for row in rows}
    browsers = {row["date"]: row["browsers"] for row in rows}
    already = {row["date"] for row in rows if row["marked"]}
    for day in flood_days(counts, browsers, already, today):
        if day >= since:
            mark(DailyVisitor.objects.filter(drive_by_q(), date=day), "flood")

    # 3. Clicks: those of marked visitors, and older ones whose visitor's browser never ran a page that day.
    marked_visitor = DailyVisitor.objects.filter(date=OuterRef("created_at__date"), token=OuterRef("visitor"),
                                                 automated=True)
    ran = DailyVisitor.objects.filter(date=OuterRef("created_at__date"), token=OuterRef("visitor"), browser=True)
    from_day = timezone.make_aware(timezone.datetime.combine(since, timezone.datetime.min.time()))
    tracked = OutboundClick.objects.people().filter(created_at__gte=max(start, from_day))
    done["clicks"] += tracked.filter(Exists(marked_visitor)).update(automated=True)
    # From the first whole day pages reported in: on the day it began, pages before the change never reported.
    first_whole = timezone.make_aware(timezone.datetime.combine(browser_since + timedelta(days=1),
                                                                timezone.datetime.min.time()))
    done["clicks"] += (tracked.filter(ran_page__isnull=True, created_at__gte=first_whole,
                                      created_at__lte=now - CLICK_GRACE)
                       .exclude(visitor="").exclude(Exists(ran)).update(automated=True))

    # 4. The page view each one-page visitor marked here opened, off the day's counts.
    taken = {}
    for day, landing in newly:
        kind, _, key = landing.partition(":")
        taken[(day, kind, key)] = taken.get((day, kind, key), 0) + 1
    with transaction.atomic():
        for (day, kind, key), n in taken.items():
            done["views"] += DailyPageView.objects.filter(date=day, kind=kind, key=key, hits__gt=0).update(
                hits=Greatest(F("hits") - n, 0))
    return done


def flood_counts(since, until):
    """{date: visitors left out as a flood} for the days from ``since`` to ``until``, for the explainer."""
    return dict(
        DailyVisitor.objects.filter(date__gte=since, date__lte=until, automated_why="flood").order_by()
        .values_list("date").annotate(n=Count("id")).values_list("date", "n")
    )
