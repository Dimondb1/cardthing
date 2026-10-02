"""
Counting what visitors do, without recording who they are: page views,
searches and clicks to shops, totalled by day. The Insights page in admin
reads them back.
"""

import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from . import geo
from .models import DailyPageView, DailySearch, DailyVisitor, OutboundClick, Product, Retailer

COUNTRY_NAMES = {
    "GB": "United Kingdom", "IE": "Ireland", "US": "United States", "DE": "Germany", "FR": "France", "NL": "Netherlands",
    "ES": "Spain", "IT": "Italy", "AU": "Australia", "CA": "Canada", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "PL": "Poland", "BE": "Belgium", "PT": "Portugal", "JP": "Japan", "IN": "India", "NZ": "New Zealand", "CH": "Switzerland",
    "AT": "Austria", "FI": "Finland", "BR": "Brazil", "MX": "Mexico", "SG": "Singapore", "HK": "Hong Kong", "AE": "United Arab Emirates",
}

BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "preview", "monitor", "python-requests", "curl/")


def is_bot(request):
    agent = request.headers.get("User-Agent", "").lower()
    return not agent or any(marker in agent for marker in BOT_MARKERS)


def bump(model, **keys):
    """Add one to today's row for ``keys``, creating it the first time."""
    with transaction.atomic():
        updated = model.objects.filter(date=timezone.localdate(), **keys).update(hits=F("hits") + 1)
        if not updated:
            model.objects.create(date=timezone.localdate(), hits=1, **keys)


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


def record_visitor(request):
    day = timezone.localdate()
    token = visitor_token(request, day)
    if DailyVisitor.objects.filter(date=day, token=token).exists():
        return
    try:
        with transaction.atomic():
            DailyVisitor.objects.create(date=day, token=token, country=geo.country_of(client_ip(request)))
    except IntegrityError:
        pass   # two requests from the same visitor at once


def record_view(request, kind, key=""):
    if request.method == "GET" and not is_bot(request):
        bump(DailyPageView, kind=kind, key=key[:220])
        record_visitor(request)


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
    by_day = {row["date"]: row for row in views.values("date").annotate(hits=Sum("hits")).order_by("date")}
    clicks_by_day = dict(clicks.values_list("created_at__date").annotate(n=Count("id")).values_list("created_at__date", "n"))
    visitors_by_day = dict(visitors.values_list("date").annotate(n=Count("id")).values_list("date", "n"))
    days_out = []
    for offset in range(days):
        day = since + timedelta(days=offset)
        days_out.append({
            "date": day,
            "visitors": visitors_by_day.get(day, 0),
            "views": by_day.get(day, {}).get("hits", 0),
            "clicks": clicks_by_day.get(day, 0),
        })
    country_rows = list(visitors.values("country").annotate(n=Count("id")).order_by("-n")[:15])
    total_visitors = sum(d["visitors"] for d in days_out)
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
    total_views = sum(d["views"] for d in days_out)
    total_clicks = sum(d["clicks"] for d in days_out)
    product_page_views = sum(product_views.values())
    kinds = {row["kind"]: row["hits"] for row in views.values("kind").annotate(hits=Sum("hits"))}
    return {
        "days": days,
        "since": since,
        "today": today,
        "by_day": days_out,
        "total_views": total_views,
        "total_visitors": total_visitors,
        "countries": countries,
        "geoip_ready": geo.reader() is not None,
        "total_clicks": total_clicks,
        "click_rate": round(100 * total_clicks / product_page_views, 1) if product_page_views else 0,
        "kinds": [(label, kinds.get(code, 0)) for code, label in DailyPageView.Kind.choices],
        "top_products": top_products,
        "shops": shops,
        "games": games,
        "types": types,
        "hours": hours,
        "top_searches": top_searches,
        "empty_searches": empty_searches,
        "shop_count": Retailer.objects.filter(is_active=True).count(),
    }
