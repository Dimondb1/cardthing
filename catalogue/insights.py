"""
Counting what visitors do, without recording who they are: page views,
searches and clicks to shops, totalled by day. The Insights page in admin
reads them back.
"""

import hashlib
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone

from . import checks, geo
from .importers import STOPPED, section_handles
from .pricing import drop_if_locked, retry_locked
from .models import (
    DailyPageView, DailySearch, DailyVisitor, ImportRun, Listing, OutboundClick, Product, Retailer, StockAlert, WorkerState,
)

COUNTRY_NAMES = {
    "GB": "United Kingdom", "IE": "Ireland", "US": "United States", "DE": "Germany", "FR": "France", "NL": "Netherlands",
    "ES": "Spain", "IT": "Italy", "AU": "Australia", "CA": "Canada", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "PL": "Poland", "BE": "Belgium", "PT": "Portugal", "JP": "Japan", "IN": "India", "NZ": "New Zealand", "CH": "Switzerland",
    "AT": "Austria", "FI": "Finland", "BR": "Brazil", "MX": "Mexico", "SG": "Singapore", "HK": "Hong Kong", "AE": "United Arab Emirates",
}

BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "preview", "monitor", "python-requests", "curl/", "python-urllib",
               "python/", "aiohttp", "httpx", "wget", "go-http-client", "java/", "okhttp", "node-fetch", "axios",
               "headlesschrome", "phantomjs", "scrapy", "playwright", "puppeteer", "selenium", "libwww", "httpclient",
               "fetch/", "scraper", "facebookexternalhit", "whatsapp", "embedly", "lighthouse", "pagespeed")
# No person opens more pages than this in a day. Past it a visitor is a script, and its views stop counting.
VIEWS_PER_VISITOR_CAP = 150


def is_bot(request):
    agent = request.headers.get("User-Agent", "").lower()
    return not agent or any(marker in agent for marker in BOT_MARKERS)


def bump(model, **keys):
    """Add one to today's row for ``keys``, creating it the first time."""

    def write():
        with transaction.atomic():
            updated = model.objects.filter(date=timezone.localdate(), **keys).update(hits=F("hits") + 1)
            if not updated:
                model.objects.create(date=timezone.localdate(), hits=1, **keys)

    # A count is best effort: one the database is too busy to save is dropped rather than failing the page.
    with drop_if_locked("Page count"):
        retry_locked(write)


# What the home screen prompt reports: the prompt shown, the site added, the prompt dismissed,
# and the site opened from its icon (once per browser per day).
INSTALL_EVENTS = ("shown", "added", "dismissed", "opened")


def bump_many(model, kind, keys):
    """Add one to today's row for each of ``keys`` under ``kind``, in three queries however many there are."""
    keys = list(dict.fromkeys(key[:220] for key in keys if key))
    if not keys:
        return
    today = timezone.localdate()
    with transaction.atomic():
        existing = set(model.objects.filter(date=today, kind=kind, key__in=keys).values_list("key", flat=True))
        if existing:
            model.objects.filter(date=today, kind=kind, key__in=existing).update(hits=F("hits") + 1)
        model.objects.bulk_create(
            [model(date=today, kind=kind, key=key, hits=1) for key in keys if key not in existing],
            ignore_conflicts=True,
        )


def client_ip(request):
    """The visitor's address. Caddy puts the real one last in X-Forwarded-For."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


def visitor_token(request, day):
    """A one-way token for this visitor today. The day's secret is never stored, so it cannot be reversed."""
    secret = f"{settings.SECRET_KEY}:{day.isoformat()}"
    raw = f"{secret}|{client_ip(request)}|{request.headers.get('User-Agent', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


TABLET = re.compile(r"ipad|tablet|kindle|silk/|playbook|(android(?!.*mobile))", re.I)
MOBILE = re.compile(r"mobi|iphone|ipod|android|blackberry|opera mini|windows phone", re.I)


def device_of(request):
    agent = request.headers.get("User-Agent", "")
    if TABLET.search(agent):
        return "tablet"
    if MOBILE.search(agent):
        return "mobile"
    return "desktop"


def source_of(request):
    """The domain of the site that sent the visitor, or "" for typed, bookmarked or our own pages."""
    host = urlsplit(request.headers.get("Referer", "")).hostname or ""
    host = host.lower().removeprefix("www.").removeprefix("m.").removeprefix("l.").removeprefix("lm.")
    own = request.get_host().split(":")[0].lower().removeprefix("www.")
    if not host or host == own:
        return ""
    return host[:80]


# Sources grouped the way a site owner thinks about them.
SOURCE_GROUPS = [
    ("Search engines", ("google.", "bing.com", "duckduckgo.com", "yahoo.", "ecosia.org", "search.brave.com", "yandex.")),
    ("AI assistants", ("chatgpt.com", "chat.openai.com", "perplexity.ai", "claude.ai", "gemini.google.com", "copilot.microsoft.com")),
    ("Reddit", ("reddit.com",)),
    ("Facebook and Instagram", ("facebook.com", "instagram.com", "fb.com", "messenger.com")),
    ("Discord", ("discord.com", "discordapp.com")),
    ("X and Threads", ("t.co", "x.com", "twitter.com", "threads.net")),
    ("YouTube and TikTok", ("youtube.com", "youtu.be", "tiktok.com")),
    ("WhatsApp", ("whatsapp.com", "wa.me")),
]


def source_group(host):
    if not host:
        return "Typed or bookmarked"
    for name, markers in SOURCE_GROUPS:
        if any(host == m or host.endswith("." + m) or host.startswith(m) for m in markers):
            return name
    return "Other sites"


# Set once a browser has viewed a page, holding only "1": the next day's visit then counts as a return.
RETURN_COOKIE = "rr_back"
RETURN_COOKIE_DAYS = 365
# Set when a browser reads a guide (guides/), holding only "1": a visit on a later day then counts as a guide
# reader coming back (DailyVisitor.guide_reader), so the owner can see whether the guides bring parents back.
GUIDE_COOKIE = "rr_guide"
GUIDE_COOKIE_DAYS = 180
GUIDE_READ_PAGES = ("guide", "guide_checklist")


def mark_guide_reader(request, response):
    """Set the guide reader cookie on ``response`` once, for a person reading a guide."""
    if GUIDE_COOKIE not in request.COOKIES and not is_bot(request):
        response.set_cookie(GUIDE_COOKIE, "1", max_age=GUIDE_COOKIE_DAYS * 24 * 3600, httponly=True,
                            samesite="Lax", secure=request.is_secure())


def landing_of(kind, key=""):
    """The page a visitor's day started on, as "kind:key" or just the kind: no query string, nothing personal."""
    return (f"{kind}:{key}" if key else str(kind))[:120]


def record_visitor(request, kind="", key=""):
    """Note this visitor's page for today. False once they are past the daily cap, so the page is not counted.

    A new visitor's row also keeps when they arrived and the page they arrived on, for the traffic explainer."""
    day = timezone.localdate()
    token = visitor_token(request, day)
    seen = DailyVisitor.objects.filter(date=day, token=token, views__lt=VIEWS_PER_VISITOR_CAP)
    if retry_locked(lambda: seen.update(views=F("views") + 1)):
        return True

    def create():
        with transaction.atomic():
            DailyVisitor.objects.create(
                date=day, token=token, country=geo.country_of(client_ip(request)),
                device=device_of(request), source=source_of(request),
                returning=request.COOKIES.get(RETURN_COOKIE) == "1", views=1,
                guide_reader=request.COOKIES.get(GUIDE_COOKIE) == "1",
                first_seen=timezone.now(), landing=landing_of(kind, key) if kind else "",
            )

    try:
        retry_locked(create)
        return True
    except IntegrityError:
        # Already here today and past the cap (or two requests at once): not counted.
        return False


def mark_browser(request, kind="", key=""):
    """The page's own script reported in (site.js): this visitor's browser runs pages, so they count as a person, and
    the page they are on is kept for the dashboard's On the site now. Only today's row of a visitor already counted
    is changed: a report never makes a visitor, and a bot's never makes one a person."""
    if is_bot(request):
        return
    day = timezone.localdate()
    seen = DailyVisitor.objects.filter(date=day, token=visitor_token(request, day))
    changes = {"browser": True, "last_seen": timezone.now()}
    if kind:
        changes["last_page"] = landing_of(kind, key)
    with drop_if_locked("Browser"):
        retry_locked(lambda: seen.update(**changes))


def record_view(request, kind, key=""):
    if request.method == "GET" and not is_bot(request):
        # The first count that finds the database locked ends counting for this page, so a visitor
        # waits at most one busy timeout, never one per count.
        with drop_if_locked("Page view"):
            if record_visitor(request, kind, key):
                bump(DailyPageView, kind=kind, key=key[:220])


# Searches that are a scanner testing the site, not someone looking for cards: markup, script, template and
# database syntax, escaped characters, paths and long hex strings, to see whether the site echoes them back
# unescaped (it never does: every page prints them as plain text). They are still recorded, as typed, and left
# out of the search lists on Insights and the dashboard, where they pushed out the real ones.
PROBE_SEARCH = r"[<>{}\\`=;|]|\$\{|%[0-9a-f]{2}|&#|&[a-z]+;|javascript:|\.\./|/etc/|^[0-9a-f]{16,}$"


def people_searches(rows):
    """The DailySearch rows ``rows`` without the scanners' probes."""
    return rows.exclude(query__iregex=PROBE_SEARCH)


def record_search(request, query, results):
    query = " ".join(query.lower().split())[:100]
    if query and request.method == "GET" and not is_bot(request):
        with transaction.atomic():
            row, _created = DailySearch.objects.get_or_create(
                date=timezone.localdate(), query=query, defaults={"results": results}
            )
            DailySearch.objects.filter(pk=row.pk).update(hits=F("hits") + 1, results=results)


def report(days=30):
    """Everything the Insights page shows, for the last ``days`` days."""
    today = timezone.localdate()
    since = today - timedelta(days=days - 1)
    views = DailyPageView.objects.filter(date__gte=since)
    all_clicks = OutboundClick.objects.filter(created_at__date__gte=since)
    clicks = all_clicks.people()
    searches = DailySearch.objects.filter(date__gte=since)

    visitors = DailyVisitor.objects.filter(date__gte=since)
    # Watchlist rows and home screen events are counts of their own, not pages anyone opened.
    pages = views.exclude(kind__in=[DailyPageView.Kind.WATCHED, DailyPageView.Kind.INSTALL, DailyPageView.Kind.ALERTS])
    by_day = {row["date"]: row for row in pages.values("date").annotate(hits=Sum("hits")).order_by("date")}
    # Direct hits: sent on to the shop without tracking and left out of every other click count.
    week_since = today - timedelta(days=6)
    direct_by_day = dict(
        all_clicks.suspects().filter(created_at__date__gte=week_since).order_by()
        .values_list("created_at__date").annotate(n=Count("id")).values_list("created_at__date", "n")
    )
    clicks_by_day = dict(clicks.values_list("created_at__date").annotate(n=Count("id")).values_list("created_at__date", "n"))
    visitors_by_day = dict(visitors.values_list("date").annotate(n=Count("id")).values_list("date", "n"))
    returning_by_day = dict(
        visitors.filter(returning=True).values_list("date").annotate(n=Count("id")).values_list("date", "n")
    )
    days_out = []
    for offset in range(days):
        day = since + timedelta(days=offset)
        days_out.append({
            "date": day,
            "visitors": visitors_by_day.get(day, 0),
            "returning": returning_by_day.get(day, 0),
            "views": by_day.get(day, {}).get("hits", 0),
            "clicks": clicks_by_day.get(day, 0),
        })
    country_rows = list(visitors.values("country").annotate(n=Count("id")).order_by("-n")[:15])
    total_visitors = sum(d["visitors"] for d in days_out)
    returning_visitors = sum(d["returning"] for d in days_out)
    countries = [
        {"code": row["country"] or "", "name": COUNTRY_NAMES.get(row["country"], row["country"] or "Unknown"),
         "visitors": row["n"], "share": round(100 * row["n"] / total_visitors, 1) if total_visitors else 0}
        for row in country_rows
    ]

    product_views = {
        row["key"]: row["hits"]
        for row in views.filter(kind=DailyPageView.Kind.PRODUCT).values("key").annotate(hits=Sum("hits")).order_by("-hits")[:200]
    }
    products = {p.slug: p for p in Product.objects.filter(slug__in=list(product_views)).select_related("game")}
    product_clicks = dict(clicks.values_list("product__slug").annotate(n=Count("id")).values_list("product__slug", "n"))
    top_products = [
        {"product": products[slug], "views": hits, "clicks": product_clicks.get(slug, 0)}
        for slug, hits in list(product_views.items())[:20]
        if slug in products
    ]
    shop_rows = clicks.values("retailer__name").annotate(n=Count("id")).order_by("-n")
    shops = [{"name": row["retailer__name"], "clicks": row["n"]} for row in shop_rows]
    game_rows = clicks.values("product__game__name").annotate(n=Count("id")).order_by("-n")
    games = [{"name": row["product__game__name"], "clicks": row["n"]} for row in game_rows]
    type_rows = clicks.values("product__product_type").annotate(n=Count("id")).order_by("-n")
    labels = dict(Product.Type.choices)
    types = [{"name": labels.get(row["product__product_type"], row["product__product_type"]), "clicks": row["n"]} for row in type_rows]
    hour_rows = dict(clicks.values_list("created_at__hour").annotate(n=Count("id")).values_list("created_at__hour", "n"))
    hours = [{"hour": h, "clicks": hour_rows.get(h, 0)} for h in range(24)]

    people = people_searches(searches)
    top_searches = list(
        people.values("query").annotate(hits=Sum("hits"), results=Sum("results")).order_by("-hits")[:25]
    )
    empty_searches = list(
        people.filter(results=0).values("query").annotate(hits=Sum("hits")).order_by("-hits")[:25]
    )
    device_rows = dict(visitors.values_list("device").annotate(n=Count("id")).values_list("device", "n"))
    devices = [
        {"name": label, "visitors": device_rows.get(code, 0),
         "share": round(100 * device_rows.get(code, 0) / total_visitors, 1) if total_visitors else 0}
        for code, label in (("mobile", "Phone"), ("tablet", "Tablet"), ("desktop", "Computer"))
    ]
    grouped = {}
    hosts = {}
    for host, n in visitors.values_list("source").annotate(n=Count("id")).values_list("source", "n"):
        group = source_group(host)
        grouped[group] = grouped.get(group, 0) + n
        if host:
            hosts[host] = hosts.get(host, 0) + n
    sources = [
        {"name": name, "visitors": n, "share": round(100 * n / total_visitors, 1) if total_visitors else 0}
        for name, n in sorted(grouped.items(), key=lambda item: -item[1])
    ]
    top_hosts = sorted(hosts.items(), key=lambda item: -item[1])[:12]

    # Clicks that can earn: a shop with an affiliate link format, or Amazon and eBay links that carry our tag.
    earning = Q(retailer__affiliate_url_template__gt="") | Q(
        retailer__source_type__in=[Retailer.Source.AMAZON, Retailer.Source.EBAY]
    )
    earning_clicks = clicks.filter(earning).count()
    watchlist_clicks = clicks.filter(source="watchlist").count()
    unpaid = [
        {"name": row["retailer__name"], "slug": row["retailer__slug"], "clicks": row["n"]}
        for row in clicks.exclude(earning).values("retailer__name", "retailer__slug").annotate(n=Count("id")).order_by("-n")[:10]
    ]

    # Shop health: when each shop was last read successfully, its latest error and its reading schedule.
    now = timezone.now()
    shops_health = []
    # A pre-order pulse reads part of a shop, so it says nothing about whether the shop's reads work.
    recent_runs = ImportRun.objects.filter(finished_at__gte=timezone.now() - timedelta(days=7), note="")
    last_ok = dict(
        recent_runs.filter(error="")
        .values_list("retailer_id").annotate(t=Max("finished_at")).values_list("retailer_id", "t")
    )
    # The latest run is the one that started last: a run closed hours after it died (close_abandoned_runs)
    # carries the close time as finished_at and must not outrank a later read that worked.
    last_any = {}
    for run in recent_runs.order_by("retailer_id", "-started_at", "-finished_at").only(
        "retailer_id", "error", "finished_at"
    ):
        last_any.setdefault(run.retailer_id, run)
    stock, preorders = {}, {}
    for retailer_id, n, on_preorder in (
        Listing.objects.buyable().values_list("retailer_id")
        .annotate(n=Count("id"), p=Count("id", filter=Q(availability=Listing.Availability.PREORDER)))
        .values_list("retailer_id", "n", "p")
    ):
        stock[retailer_id], preorders[retailer_id] = n, on_preorder
    for retailer in Retailer.objects.filter(is_active=True).exclude(source_type=Retailer.Source.MANUAL).order_by("name"):
        latest = last_any.get(retailer.pk)
        ok = last_ok.get(retailer.pk)
        problem = ""
        latest_error = latest.error[:160] if latest is not None and latest.error else None
        if latest_error:
            problem = latest_error
        elif ok is None:
            problem = "Not read successfully in the last week."
        elif ok < now - stale_after(retailer):
            problem = f"Last read {timezone.localtime(ok):%d %b %H:%M}."
        shops_health.append({
            "name": retailer.name, "slug": retailer.slug, "last_ok": ok, "in_stock": stock.get(retailer.pk, 0),
            "preorders": preorders.get(retailer.pk, 0), "pulse": pulse_state(retailer, now),
            "problem": problem, "reading": reading_state(retailer, now, error_shown=problem == latest_error),
            "earns": bool(retailer.affiliate_url_template)
            or retailer.source_type in (Retailer.Source.AMAZON, Retailer.Source.EBAY),
        })

    # Products people look at but do not click, and popular products only one shop sells.
    viewed_no_click = [
        row for row in (
            {"product": products[slug], "views": hits, "clicks": product_clicks.get(slug, 0)}
            for slug, hits in product_views.items() if slug in products
        ) if row["views"] >= 3 and row["clicks"] == 0
    ][:15]
    shops_by_product = dict(
        Listing.objects.buyable().filter(product__slug__in=list(product_views))
        .values_list("product__slug").annotate(n=Count("retailer", distinct=True)).values_list("product__slug", "n")
    )
    for row in viewed_no_click:
        row["shops"] = shops_by_product.get(row["product"].slug, 0)
    one_shop = [
        {"product": products[slug], "views": hits}
        for slug, hits in product_views.items()
        if slug in products and shops_by_product.get(slug, 0) == 1
    ][:15]
    # How far the stockist finder has looked for each, in one query.
    from .finder import search_counts

    searched = search_counts(row["product"].pk for row in one_shop)
    for row in one_shop:
        row["searched"], row["searched_at"] = searched.get(row["product"].pk, (0, None))

    catalogue = Product.objects.for_lists()
    catalogue_stats = catalogue.aggregate(
        total=Count("id"),
        in_stock=Count("id", filter=Q(in_stock_count__gte=1)),
        compared=Count("id", filter=Q(in_stock_count__gte=2)),
        no_image=Count("id", filter=Q(image="", image_url="")),
    )

    total_views = sum(d["views"] for d in days_out)
    total_clicks = sum(d["clicks"] for d in days_out)
    product_page_views = sum(product_views.values())
    kinds = {row["kind"]: row["hits"] for row in views.exclude(kind__in=[DailyPageView.Kind.INSTALL, DailyPageView.Kind.ALERTS]).values("kind").annotate(hits=Sum("hits"))}
    capped = visitors.filter(views__gte=VIEWS_PER_VISITOR_CAP).count()
    install_rows = dict(
        views.filter(kind=DailyPageView.Kind.INSTALL).values_list("key").annotate(hits=Sum("hits")).values_list("key", "hits")
    )
    install = {event: install_rows.get(event, 0) for event in INSTALL_EVENTS}
    alert_rows = dict(
        views.filter(kind=DailyPageView.Kind.ALERTS).values_list("key").annotate(hits=Sum("hits")).values_list("key", "hits")
    )
    alert_counts = {event: alert_rows.get(event, 0) for event in ("asked", "confirmed", "sent", "stopped")}
    alert_counts["waiting"] = StockAlert.objects.filter(confirmed_at__isnull=False).count()
    click_week = [
        {"date": d["date"], "people": d["clicks"], "direct": direct_by_day.get(d["date"], 0)}
        for d in days_out if d["date"] >= week_since
    ]
    data = {
        "days": days,
        "since": since,
        "click_week": click_week,
        "today": today,
        "by_day": days_out,
        "total_views": total_views,
        "total_visitors": total_visitors,
        "returning_visitors": returning_visitors,
        "capped_visitors": capped,
        "views_cap": VIEWS_PER_VISITOR_CAP,
        "returning_share": round(100 * returning_visitors / total_visitors, 1) if total_visitors else 0,
        "countries": countries,
        "geoip_ready": geo.reader() is not None,
        "total_clicks": total_clicks,
        "click_rate": round(100 * total_clicks / product_page_views, 1) if product_page_views else 0,
        "kinds": [(label, kinds.get(code, 0)) for code, label in DailyPageView.Kind.choices
                  if code not in (DailyPageView.Kind.INSTALL, DailyPageView.Kind.ALERTS)],
        "alerts": alert_counts,
        "install": install,
        "top_products": top_products,
        "shops": shops,
        "games": games,
        "types": types,
        "hours": hours,
        "top_searches": top_searches,
        "empty_searches": empty_searches,
        "shop_count": Retailer.objects.filter(is_active=True).count(),
        "devices": devices,
        "sources": sources,
        "top_hosts": top_hosts,
        "earning_clicks": earning_clicks,
        "watchlist_clicks": watchlist_clicks,
        "unpaid": unpaid,
        "shops_health": shops_health,
        "worker_stopped": worker_stopped(now),
        "doubtful_waiting": checks.doubtful_count(),
        "preorder_pulse": preorder_pulse(now),
        "release_sources": release_sources(now),
        "viewed_no_click": viewed_no_click,
        "one_shop": one_shop,
        "catalogue": catalogue_stats,
        "pages_per_visitor": round(total_views / total_visitors, 1) if total_visitors else 0,
        "ebay": ebay_coverage(),
        "earning": earning_share(),
        "section_shops": section_shops(),
        "left_out": left_out(),
    }
    data["improvements"] = improvements(data)
    return data


def earning_share():
    """How many products a visitor can buy have a shop that pays us as their cheapest, the same order the
    product pages use, and the shops that are cheapest most often without paying. Clicks follow the
    cheapest price, so this is what the affiliate programmes are worth."""
    earning_ids = set(
        Retailer.objects.filter(
            Q(affiliate_url_template__gt="") | Q(source_type__in=[Retailer.Source.AMAZON, Retailer.Source.EBAY])
        ).values_list("pk", flat=True)
    )
    best = {}
    rows = (
        Listing.objects.buyable()
        .order_by("product_id", "-delivery_known", "delivered_price", "retailer__name")
        .values_list("product_id", "retailer_id", "retailer__name")
    )
    for product_id, retailer_id, name in rows:
        best.setdefault(product_id, (retailer_id, name))
    total = len(best)
    paying = sum(1 for retailer_id, _ in best.values() if retailer_id in earning_ids)
    unpaid = {}
    for retailer_id, name in best.values():
        if retailer_id not in earning_ids:
            unpaid[name] = unpaid.get(name, 0) + 1
    return {
        "total": total, "paying": paying, "share": round(100 * paying / total) if total else 0,
        "unpaid": sorted(unpaid.items(), key=lambda item: (-item[1], item[0]))[:10],
    }


def section_shops():
    """Shops read from some of their collections rather than the whole shop (Zatu): which collections,
    how many of their products we list, and how many found outside those collections are read one by one."""
    shops = list(
        Retailer.objects.filter(is_active=True, source_type=Retailer.Source.SHOPIFY).exclude(collection="").order_by("name")
    )
    ids = [retailer.pk for retailer in shops]
    listed = dict(
        Listing.objects.filter(retailer_id__in=ids, is_active=True)
        .values_list("retailer_id").annotate(n=Count("id")).values_list("retailer_id", "n")
    )
    buyable = dict(
        Listing.objects.buyable().filter(retailer_id__in=ids)
        .values_list("retailer_id").annotate(n=Count("id")).values_list("retailer_id", "n")
    )
    return [
        {"name": retailer.name, "slug": retailer.slug, "sections": section_handles(retailer),
         "listed": listed.get(retailer.pk, 0), "buyable": buyable.get(retailer.pk, 0),
         "extras": len((retailer.extra_products or "").split()), "last_ok": retailer.last_ok_at}
        for retailer in shops
    ]


# The line a read keeps for an item it left out: "title [barcode, or no barcode] link".
LEFT_OUT_LINE = re.compile(r"^(?P<title>.+) \[(?:no barcode|(?P<barcode>\d{8,14}))\] (?P<url>https?://\S+)$")


# The hourly tidy-up's count of items left out, kept long enough to outlast a missed hour or two.
LEFT_OUT_KEY = "insights:left-out:v2"
LEFT_OUT_SECONDS = 3 * 3600


def each_line(text):
    """The lines of a big text one at a time. splitlines() would hold a second copy of a big shop's list as a list
    of lines, and io.StringIO four copies: measured on a 41 MB list, 58 MB and 156 MB more, this nothing."""
    start, end_of_text = 0, len(text)
    while start < end_of_text:
        end = text.find("\n", start)
        if end < 0:
            end = end_of_text
        yield text[start:end].rstrip("\r")
        start = end + 1


def left_out_lines(text):
    """{title, barcode, url, why} for each item in one read's unmatched text that looks like a sealed product
    of a game we list: what count_left_out counts, and what the owner's spreadsheet lists for one shop."""
    from .classify import NOT_SEALED, VARIANT_MENU, classify, find_game, find_type

    for line in each_line(text or ""):
        found = LEFT_OUT_LINE.match(line)
        if not found:
            continue
        title = found.group("title")
        if not find_game(title) or NOT_SEALED.search(title) or VARIANT_MENU.search(title):
            continue
        if classify(title) is not None:
            why = "ours by name, turned away by the shop's category, price or the matching"
        elif find_type(title) is None:
            why = "a kind of product the rules do not know"
        else:
            why = "left out by another rule"
        yield {"title": title, "barcode": found.group("barcode") or "", "url": found.group("url"), "why": why}


def count_left_out(limit=40, now=None):
    """Items shops sell that look like a sealed product of a game we list but were not listed, from each
    shop's latest good read: a kind of product the rules do not know yet ("Illustration Box"), or one that
    is ours by its name but was turned away by the shop's category, its price or the matching. What the
    rules leave out on purpose (cases of boxes, sleeves, single cards, figures) is not counted.

    Counted by the hourly tidy_all and kept in the shared cache, never while a page waits: a big shop's
    list runs to thousands of lines, and on 14 Oct 2026 counting them for the Insights page took the
    one-processor server past gunicorn's 60 seconds and out of memory. One shop's list is read at a time."""
    from django.core.cache import cache

    latest = {}
    runs = (
        ImportRun.objects.filter(error="", note="", finished_at__isnull=False, retailer__is_active=True)
        .exclude(unmatched="").order_by("retailer_id", "-started_at").values_list("pk", "retailer_id")
    )
    for pk, retailer_id in runs:
        latest.setdefault(retailer_id, pk)
    items, by_shop = [], {}
    shops = ImportRun.objects.filter(pk__in=latest.values()).order_by("retailer__name").values_list("pk", "retailer__name")
    for pk, shop in list(shops):
        text = ImportRun.objects.filter(pk=pk).values_list("unmatched", flat=True).first() or ""
        for item in left_out_lines(text):
            by_shop[shop] = by_shop.get(shop, 0) + 1
            if len(items) < limit:
                items.append({"shop": shop, "title": item["title"], "url": item["url"], "why": item["why"]})
    result = {"count": sum(by_shop.values()), "by_shop": sorted(by_shop.items(), key=lambda item: -item[1]),
              "items": items, "counted_at": now or timezone.now()}
    cache.set(LEFT_OUT_KEY, result, LEFT_OUT_SECONDS)
    return result


def left_out():
    """The hourly count from count_left_out, or None before the first one."""
    from django.core.cache import cache

    return cache.get(LEFT_OUT_KEY)


def ebay_coverage():
    """How much of the catalogue eBay covers, or None when eBay is not a shop."""
    ebay = Retailer.objects.filter(source_type=Retailer.Source.EBAY, is_active=True).first()
    if ebay is None:
        return None
    products = Product.objects.filter(is_active=True)
    total = products.count()
    checked = products.filter(ebay_checked_at__isnull=False).count()
    listings = Listing.objects.filter(retailer=ebay, is_active=True)
    matched = listings.values("product").distinct().count()
    in_stock = listings.buyable().count()
    stale = listings.filter(availability=Listing.Availability.IN_STOCK).count() - in_stock
    last_run = (
        # A run closed by close_abandoned_runs carries the close time, not when it ran, so it is left out.
        ImportRun.objects.filter(retailer=ebay, finished_at__isnull=False, offers_found__gt=0)
        .exclude(error=STOPPED).order_by("-finished_at").values_list("finished_at", flat=True).first()
    )
    ebay_prices = dict(listings.buyable().values_list("product_id", "delivered_price"))
    lowest = dict(
        Product.objects.for_lists().filter(pk__in=list(ebay_prices)).order_by().values_list("pk", "lowest_price")
    )
    cheapest = sum(1 for pk, price in ebay_prices.items() if lowest.get(pk) is not None and price <= lowest[pk])
    from django.conf import settings as dj

    from .finder import MARKET_RETRY_AFTER, interest_scores

    per_day = getattr(dj, "RIPRAPTOR_EBAY_DAILY_LIMIT", 4000) or 4000
    waiting = total - checked
    # Wanted products already searched and not on eBay are searched again, each at most once every
    # three days, ahead of products not searched yet, so those searches are not counted as new ones.
    wanted = interest_scores(visitors_only=True)
    again = (
        products.filter(ebay_checked_at__isnull=False, pk__in=list(wanted))
        .exclude(pk__in=listings.values("product")).count()
        if wanted else 0
    )
    new_per_day = max(1, per_day - -(-again // MARKET_RETRY_AFTER.days))
    missed = list(
        Product.objects.for_lists()
        .filter(ebay_checked_at__isnull=False, in_stock_count__gte=2)
        .exclude(listings__retailer=ebay)
        .order_by("-in_stock_count", "name")
        .values("name", "in_stock_count")[:15]
    )
    return {
        "products": total,
        "checked": checked,
        "checked_share": round(100 * checked / total) if total else 0,
        "matched": matched,
        "match_rate": round(100 * matched / checked) if checked else 0,
        "in_stock": in_stock,
        "stale": stale,
        "last_run": last_run,
        "cheapest": cheapest,
        "waiting": waiting,
        "days_left": -(-waiting // new_per_day) if waiting else 0,
        "missed": [{"name": row["name"], "shops": row["in_stock_count"]} for row in missed],
    }


# The median is printed only once this many pre-orders can be timed: fewer says nothing.
PREORDER_TIMING_MIN = 5
PREORDER_WINDOW = timedelta(days=7)


def release_sources(now):
    """One row per release source: when it was last read, how many sets it gave, its last error."""
    from .releases import source_rows

    return source_rows(now)


def preorder_pulse(now):
    """How many pre-orders reached the site in the last 7 days and how fast.

    ``listed`` counts listings first seen on pre-order here in the window, at shops rather than
    marketplaces. ``median`` is the median minutes from the shop publishing the product
    (Listing.shop_published_at) to its listing first showing on pre-order here (first_preorder_at), over
    those also published in the window, so a product a shop has listed for months and only now opened is
    not timed from months ago. It is None under PREORDER_TIMING_MIN timed rows.
    """
    since = now - PREORDER_WINDOW
    rows = list(
        Listing.objects.filter(first_preorder_at__gte=since)
        .exclude(retailer__source_type__in=[Retailer.Source.AMAZON, Retailer.Source.EBAY])
        .order_by().values_list("shop_published_at", "first_preorder_at")
    )
    waits = sorted(
        (seen - published).total_seconds() / 60
        for published, seen in rows if published is not None and since <= published <= seen
    )
    median = None
    if len(waits) >= PREORDER_TIMING_MIN:
        middle = len(waits) // 2
        median = round(waits[middle] if len(waits) % 2 else (waits[middle - 1] + waits[middle]) / 2)
    return {"listed": len(rows), "timed": len(waits), "median": median, "needed": PREORDER_TIMING_MIN}


def pulse_state(retailer, now):
    """When the pre-order pulse last looked at a Shopify shop, or None for a shop it does not look at."""
    if retailer.source_type != Retailer.Source.SHOPIFY:
        return None
    if retailer.collections_polled_at is None:
        return "not yet"
    when = clock(retailer.collections_polled_at, now)
    return f"{when}, no collection list" if retailer.collections_ok is False else when


def worker_stopped(now):
    """When the background reader last beat, once that is more than ten minutes ago; None while it runs or
    before it has ever run (the hourly schedule is then the normal way shops are read)."""
    state = WorkerState.current()
    if state is None or state.heartbeat_at is None or state.alive(now):
        return None
    return state.heartbeat_at


def stale_after(retailer):
    """How long since its last good read before a shop counts as not updating: 6 hours, or two of its reads."""
    return max(timedelta(hours=6), timedelta(minutes=2 * retailer.read_every_minutes))


def clock(moment, now):
    """A time as 14:20 today, or 10 Oct 14:20 on another day."""
    local = timezone.localtime(moment)
    return f"{local:%H:%M}" if local.date() == timezone.localtime(now).date() else f"{local:%d %b %H:%M}"


def reading_state(retailer, now, error_shown=False):
    """One line on when a shop is read: paused, waiting after errors, or its interval and next read.

    The error that started a wait is left out when the latest run's error is already shown beside it.
    """
    if retailer.reading_paused:
        return "Paused"
    if retailer.backoff_until and retailer.backoff_until > now:
        line = f"Backing off until {clock(retailer.backoff_until, now)} after {plural(retailer.error_streak, 'error', 'errors')}"
        return f"{line}: {retailer.last_error[:160]}" if retailer.last_error and not error_shown else line
    line = f"Reads every {retailer.read_every_minutes} min"
    return f"{line}, next {clock(retailer.next_read_at, now)}" if retailer.next_read_at else line


def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def improvements(data):
    """What to do next, most valuable first, worked out from the report. Each has a reason and a place to act."""
    items = []

    def add(weight, title, detail, link="", link_label=""):
        items.append({"weight": weight, "title": title, "detail": detail, "link": link, "link_label": link_label})

    stopped = data.get("worker_stopped")
    if stopped:
        add(100, "Worker not running",
            f"The background reader last sent a heartbeat at {clock(stopped, timezone.now())}. Shops are read on the "
            "hourly schedule meanwhile, so prices update less often. The server restarts it on its own; if this stays, "
            "the server needs a look.", "/admin/crawl/", "Crawl health")

    broken = [s for s in data["shops_health"] if s["problem"]]
    if broken:
        names = ", ".join(s["name"] for s in broken[:5])
        add(100, f"{plural(len(broken), 'shop', 'shops')} not updating",
            f"Prices from {names} may be out of date. The shop health table below shows the error for each.",
            "/admin/catalogue/importrun/", "Import runs")

    total_clicks = data["total_clicks"]
    if total_clicks:
        unpaid_clicks = total_clicks - data["earning_clicks"]
        share = round(100 * unpaid_clicks / total_clicks)
        if share >= 25 and data["unpaid"]:
            top = ", ".join(f"{u['name']} ({u['clicks']})" for u in data["unpaid"][:4])
            add(90, f"{share}% of clicks earn nothing",
                f"These shops get your clicks but have no affiliate link: {top}. Add their link format once a "
                "programme approves you, or send them the Shopify Collabs email with their shop report.",
                "/admin/catalogue/retailer/", "Shops")

    if data["total_visitors"] >= 20:
        search_share = next((s["share"] for s in data["sources"] if s["name"] == "Search engines"), 0)
        if search_share < 20:
            add(80, f"Only {search_share}% of visitors come from search engines",
                "Search is the traffic that grows on its own. In Search Console, request indexing for the home, "
                "deals and top product pages, and check Pages for anything Google refuses.",
                "https://search.google.com/search-console", "Search Console")

    earning = data.get("earning")
    if earning and earning["total"] and earning["share"] < 60 and earning["unpaid"]:
        top = ", ".join(f"{name} ({n})" for name, n in earning["unpaid"][:4])
        add(85, f"{earning['share']}% of products in stock have a paying shop as cheapest",
            f"{earning['paying']} of {earning['total']}. Clicks follow the cheapest price, so these shops take the "
            f"clicks most often without paying you: {top}. Joining their programmes earns on them.",
            "/admin/catalogue/retailer/", "Shops")

    left = data.get("left_out")
    if left and left["count"]:
        shops = ", ".join(f"{name} ({n})" for name, n in left["by_shop"][:4])
        add(62, f"{plural(left['count'], 'item looks', 'items look')} sealed but {'was' if left['count'] == 1 else 'were'} left out",
            f"Shops sell them but the rules did not take them: {shops}. The list below shows each with its link; "
            "a kind the rules do not know can be added, and one turned away by mistake can be fixed.")

    if data["empty_searches"]:
        top = ", ".join(f"'{s['query']}'" for s in data["empty_searches"][:5])
        add(75, f"{plural(len(data['empty_searches']), 'search', 'searches')} found nothing",
            f"Visitors looked for {top}. Each is a product to add or a name to fix.")

    doubtful = data.get("doubtful_waiting")
    if doubtful:
        add(72, f"{plural(doubtful, 'doubtful price', 'doubtful prices')} waiting",
            "These prices look wrong next to other shops, the shop's own history or similar products, so they are "
            "never claimed as a saving. One tap on "
            "Things to check says a price is right or hides it.", "/admin/checks/", "Things to check")

    if data["one_shop"]:
        top = ", ".join(r["product"].name for r in data["one_shop"][:3])
        add(70, f"{plural(len(data['one_shop']), 'popular product has', 'popular products have')} only one shop",
            f"With one price there is nothing to compare, for example {top}. More shops or feeds fix this.")

    if data["viewed_no_click"]:
        sold_out = sum(1 for r in data["viewed_no_click"] if r["shops"] == 0)
        top = ", ".join(r["product"].name for r in data["viewed_no_click"][:3])
        add(65, f"{plural(len(data['viewed_no_click']), 'product is', 'products are')} viewed but never clicked",
            f"{sold_out} of them are out of stock everywhere. The rest may be dearer than people expect. "
            f"For example {top}.")

    views = sum(d["views"] for d in data["by_day"])
    if views >= 50 and data["click_rate"] < 5:
        add(60, f"Only {data['click_rate']}% of product views end in a click",
            "Check that the cheapest price and Buy button are what people see first on a phone, "
            "and that prices are fresh.")

    cat = data["catalogue"]
    if cat["total"]:
        compared_share = round(100 * cat["compared"] / cat["total"])
        if compared_share < 30:
            add(55, f"Only {compared_share}% of products can be compared",
                f"{cat['compared']} of {cat['total']} products are in stock at two or more shops. "
                "The Awin feeds for Chaos Cards, Big Orbit, Hills Cards, Zavvi and The Entertainer are the quickest way up.")
        if cat["no_image"]:
            add(30, f"{plural(cat['no_image'], 'product has', 'products have')} no picture",
                "Cards with a picture get more clicks. Upload one in admin, or it fills in when a shop with a picture lists it.",
                "/admin/catalogue/product/", "Products")

    phone = next((d["share"] for d in data["devices"] if d["name"] == "Phone"), 0)
    if data["total_visitors"] >= 20 and phone >= 60:
        add(40, f"{phone}% of visitors are on a phone",
            "Phone is the main screen. Check new pages at phone width first.")

    ebay = data.get("ebay")
    if ebay and ebay["checked"] and ebay["match_rate"] < 50:
        add(68, f"eBay matches only {ebay['match_rate']}% of the products it has looked up",
            f"{ebay['matched']} of {ebay['checked']} products have an eBay price. The eBay table below lists widely "
            "stocked products it misses; send any you can find on eBay so the matching can learn from them.")
    if ebay and ebay["waiting"]:
        add(25, f"{plural(ebay['waiting'], 'product is', 'products are')} still waiting for a first eBay look",
            f"At today's allowance that takes about {plural(ebay['days_left'], 'more day', 'more days')}.")

    if not data["geoip_ready"]:
        add(20, "Visitor countries are not being recorded",
            "The country database is missing. Run the update line on the server, which downloads it.")

    items.sort(key=lambda item: -item["weight"])
    return items
