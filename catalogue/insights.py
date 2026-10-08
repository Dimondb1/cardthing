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

from . import geo
from .importers import STOPPED
from .pricing import drop_if_locked, retry_locked
from .models import DailyPageView, DailySearch, DailyVisitor, ImportRun, Listing, OutboundClick, Product, Retailer, StockAlert

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


def record_visitor(request):
    """Note this visitor's page for today. False once they are past the daily cap, so the page is not counted."""
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
            )

    try:
        retry_locked(create)
        return True
    except IntegrityError:
        # Already here today and past the cap (or two requests at once): not counted.
        return False


def record_view(request, kind, key=""):
    if request.method == "GET" and not is_bot(request):
        # The first count that finds the database locked ends counting for this page, so a visitor
        # waits at most one busy timeout, never one per count.
        with drop_if_locked("Page view"):
            if record_visitor(request):
                bump(DailyPageView, kind=kind, key=key[:220])


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
    clicks = OutboundClick.objects.filter(created_at__date__gte=since)
    searches = DailySearch.objects.filter(date__gte=since)

    visitors = DailyVisitor.objects.filter(date__gte=since)
    # Watchlist rows and home screen events are counts of their own, not pages anyone opened.
    pages = views.exclude(kind__in=[DailyPageView.Kind.WATCHED, DailyPageView.Kind.INSTALL, DailyPageView.Kind.ALERTS])
    by_day = {row["date"]: row for row in pages.values("date").annotate(hits=Sum("hits")).order_by("date")}
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

    top_searches = list(
        searches.values("query").annotate(hits=Sum("hits"), results=Sum("results")).order_by("-hits")[:25]
    )
    empty_searches = list(
        searches.filter(results=0).values("query").annotate(hits=Sum("hits")).order_by("-hits")[:25]
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

    # Shop health: when each shop was last read successfully, and its latest error.
    stale_after = timezone.now() - timedelta(hours=6)
    shops_health = []
    recent_runs = ImportRun.objects.filter(finished_at__gte=timezone.now() - timedelta(days=7))
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
    stock = dict(
        Listing.objects.buyable().values_list("retailer_id").annotate(n=Count("id")).values_list("retailer_id", "n")
    )
    for retailer in Retailer.objects.filter(is_active=True).exclude(source_type=Retailer.Source.MANUAL).order_by("name"):
        latest = last_any.get(retailer.pk)
        ok = last_ok.get(retailer.pk)
        problem = ""
        if latest is not None and latest.error:
            problem = latest.error[:160]
        elif ok is None:
            problem = "Not read successfully in the last week."
        elif ok < stale_after:
            problem = f"Last read {timezone.localtime(ok):%d %b %H:%M}."
        shops_health.append({
            "name": retailer.name, "slug": retailer.slug, "last_ok": ok, "in_stock": stock.get(retailer.pk, 0),
            "problem": problem, "earns": bool(retailer.affiliate_url_template)
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
    data = {
        "days": days,
        "since": since,
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
        "viewed_no_click": viewed_no_click,
        "one_shop": one_shop,
        "catalogue": catalogue_stats,
        "pages_per_visitor": round(total_views / total_visitors, 1) if total_visitors else 0,
        "ebay": ebay_coverage(),
    }
    data["improvements"] = improvements(data)
    return data


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

    per_day = getattr(dj, "RIPRAPTOR_EBAY_DAILY_LIMIT", 4000) or 4000
    waiting = total - checked
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
        "days_left": -(-waiting // per_day) if waiting else 0,
        "missed": [{"name": row["name"], "shops": row["in_stock_count"]} for row in missed],
    }


def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def improvements(data):
    """What to do next, most valuable first, worked out from the report. Each has a reason and a place to act."""
    items = []

    def add(weight, title, detail, link="", link_label=""):
        items.append({"weight": weight, "title": title, "detail": detail, "link": link, "link_label": link_label})

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

    if data["empty_searches"]:
        top = ", ".join(f"'{s['query']}'" for s in data["empty_searches"][:5])
        add(75, f"{plural(len(data['empty_searches']), 'search', 'searches')} found nothing",
            f"Visitors looked for {top}. Each is a product to add or a name to fix.")

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
