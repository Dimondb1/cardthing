"""
Who is online: the owner's page of the visitors on the site now (catalogue/online.py), with each one's internet
address, country, device, the site that sent them and the page they are on, whether they are a person or a script,
and how many were here at each point of the last day. Staff only, never for editors, never indexed or cached. The
addresses are kept only while visitors are here and for LIVE_KEEP minutes after their last page. The owner, 7 Nov
2026: "build into it a users, like current users, so I can see the IP address and the location and how many people
are online at any given point".
"""

import re

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import insights, online
from catalogue.traffic import DEVICES
from guides.access import not_for_editors

REFRESH_SECONDS = 30
CHART_HOURS = 24
SHOWN = 300

BROWSERS = [("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser/", "Samsung Internet"), ("Firefox/", "Firefox"),
            ("CriOS/", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari")]
SYSTEMS = [("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
           ("Mac OS X", "Mac"), ("CrOS", "ChromeOS"), ("Linux", "Linux")]
HEADLESS = re.compile(r"headless|phantom|electron|puppeteer|playwright", re.I)


def browser_of(agent):
    """"Chrome on Windows", from a User-Agent, or "" when it says neither."""
    name = next((label for marker, label in BROWSERS if marker in agent), "")
    system = next((label for marker, label in SYSTEMS if marker in agent), "")
    if HEADLESS.search(agent):
        name = f"{name or 'A'} without a screen"
    return " on ".join(part for part in (name, system) if part)


def country_name(code):
    return insights.COUNTRY_NAMES.get(code, code) if code else ""


def chart(rows, peak):
    """Bars for the people online at each count, as percentages of the chart's height and width."""
    top = max((row.people for row in rows), default=0) or 1
    width = 100 / max(len(rows), 1)
    bars = []
    for i, row in enumerate(rows):
        height = round(100 * row.people / top, 2)
        bars.append({"x": round(i * width, 3), "y": round(100 - height, 2), "width": round(width * 0.8, 3),
                     "height": height, "at": timezone.localtime(row.at), "people": row.people,
                     "automated": row.automated, "peak": peak is not None and row.pk == peak.pk})
    return bars


@staff_member_required
@not_for_editors
@require_GET
def online_page(request):
    now = timezone.now()
    rows = online.online(now)
    people = [row for row in rows if row.person]
    for row in rows:
        row.country_name = country_name(row.country)
        row.device_name = DEVICES.get(row.device, "")
        row.source_name = row.source or ""
        row.browser_name = browser_of(row.agent)
    counts, peak = online.day_of_counts(now, hours=CHART_HOURS)
    countries = {}
    for row in people:
        countries[row.country_name or "Unknown"] = countries.get(row.country_name or "Unknown", 0) + 1
    response = render(request, "admin/online.html", {
        **admin.site.each_context(request),
        "title": "Who is online",
        "rows": rows[:SHOWN], "more": max(0, len(rows) - SHOWN),
        "people": len(people), "automated": len(rows) - len(people),
        "countries": sorted(countries.items(), key=lambda item: (-item[1], item[0])),
        "bars": chart(counts, peak), "peak": peak, "hours": CHART_HOURS,
        "minutes": online.ONLINE_MINUTES, "keep": online.LIVE_KEEP, "refresh": REFRESH_SECONDS,
        "updated": timezone.localtime(now),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
