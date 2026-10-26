"""
The owner's dashboard at /dashboard/: how the site is doing, on one page that reads well on a phone.

Every figure is read from the database by the functions the admin pages already use: the traffic explainer
(catalogue/traffic.py) for people, bursts and today against the same time last week, the clicks split into
people and direct hits (catalogue/clicks.py), the Things to check counts (catalogue/checks.py), Crawl health
(catalogue/crawl.py), the Claude judge's spend (catalogue/judge.py), the alerts, the refer a friend offer and
the inbox. Nothing is estimated: a figure with no rows behind it says so.

Each section is kept in the shared cache for SECTION_SECONDS, under a key of its own, so a reload within the
minute costs a few cache reads. Possible duplicates are counted when something reads the whole catalogue
anyway (catalogue/checks.py note_duplicate_counts) and shown with the time they were counted.
"""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Count, Exists, Max, Min, OuterRef, Q, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import cardmarket, checks, crawl, insights, judge, referrals, traffic
from catalogue.drop_alerts import SENT_FIELD
from catalogue.models import (
    AffiliateSale, CardmarketMatch, CardmarketPrice, CardmarketProduct, ClaudeAsk, ClaudeJudge, DailyPageView, DailySearch,
    DailyVisitor, DropAlert, Listing, OutboundClick, Product, ReferralSettings, Retailer, SearchAlert, StockAlert,
    WorkerState,
)
from content import service as copy
from inbox.service import unread_count
from inbox.shops import open_count

SECTION_SECONDS = 60
SECTION_KEY = "dashboard:{name}:v1"
WEEK_DAYS = 7
TOP_SOURCES = 5
TOP_PRODUCTS = 5
TOP_GAPS = 5
# A search that found this many products or fewer is a gap worth a look.
FEW_RESULTS = 2
FAILING_SHOWN = 5
# A shop counts as read lately when its last good read is this recent.
READ_LATELY = timedelta(hours=1)
# The tallest bar reaches this share of the chart, leaving room above it for its number.
BAR_REACH = 78
# A bar for anything above nought is at least this tall, so a small day still shows.
BAR_MIN = 4
# Each bar's share of its day's width: about 24px wide on a wide card and 13px on a phone.
BAR_SHARE = 30
PAIR_SHARE = 26

# How a shop's clicks can earn, from its price source and affiliate link format, in the order listed.
EBAY, AMAZON, AWIN, CJ, RAKUTEN, OTHER, DIRECT = "ebay", "amazon", "awin", "cj", "rakuten", "other", "direct"
NETWORK_NAMES = {EBAY: "eBay Partner Network", AMAZON: "Amazon Associates", AWIN: "Awin", CJ: "CJ",
                 RAKUTEN: "Rakuten Advertising"}
NETWORK_ORDER = [EBAY, AMAZON, AWIN, CJ, RAKUTEN, OTHER, DIRECT]
# The click addresses of each network, as catalogue/clicks.py REDIRECTORS knows them.
NETWORK_HOSTS = {
    "awin1.com": AWIN,
    "anrdoezrs.net": CJ, "dpbolvw.net": CJ, "jdoqocy.com": CJ, "kqzyfj.com": CJ, "tkqlhce.com": CJ, "emjcd.com": CJ,
    "linksynergy.com": RAKUTEN,
}
# The alert emails counted when sent (DailyPageView kind ALERTS): back in stock, the drop reminders' three and
# the search alerts'.
SENT_KEYS = ["sent", *(f"drop_sent_{stage}" for stage in SENT_FIELD), "search_sent"]


def network_of(source_type, template):
    """Which way a shop's clicks earn: eBay and Amazon through their own programmes (their links carry our
    tag), a shop with an affiliate link format through the network its address belongs to, any other format
    as "other", and a shop with none earns nothing ("direct")."""
    if source_type == Retailer.Source.EBAY:
        return EBAY
    if source_type == Retailer.Source.AMAZON:
        return AMAZON
    if not (template or "").strip():
        return DIRECT
    host = (urlsplit(template.replace("{url}", "")).hostname or "").lower()
    for domain, network in NETWORK_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return network
    return OTHER


def say(store, key, **values):
    return copy.get(key, store=store, **values)


def share(part, whole):
    return round(100 * part / whole) if whole else 0


def change(now, then):
    """{"diff", "percent", "tone"} for a count against the one it is compared with: "up" or "down" when the
    change is more than day to day noise (traffic.is_noise), "same" otherwise."""
    diff = now - then
    if traffic.is_noise(now, then):
        tone = "same"
    else:
        tone = "up" if diff > 0 else "down"
    return {"diff": abs(round(diff)), "percent": share(abs(diff), then) if round(then) else None, "tone": tone}


def bars(days, keys):
    """Heights and places for a column chart of ``days``, one bar per key in ``keys`` side by side, all on one
    scale. Places and sizes are shares of the chart, so it fits any width without stretching its text."""
    top = max([day[key] for day in days for key in keys] + [0])
    band = 100 / len(days)
    width = band * (BAR_SHARE if len(keys) == 1 else PAIR_SHARE) / 100
    for index, day in enumerate(days):
        centre = band * (index + 0.5)
        # The whole of the day's width answers a pointer, so a short bar is as easy to point at as a tall one.
        day["band_x"], day["band"] = round(band * index, 3), round(band, 3)
        day["bars"] = []
        for number, key in enumerate(keys):
            value = day[key]
            height = max(BAR_MIN, BAR_REACH * value / top) if value and top else 0
            # A pair of bars sits either side of the middle of its day, with a 2px gap between them.
            left = centre - width / 2 if len(keys) == 1 else centre - width if number == 0 else centre
            day["bars"].append({
                "key": key, "value": value, "x": round(left, 3), "width": round(width, 3),
                "y": round(100 - height, 3), "height": round(height, 3), "middle": round(left + width / 2, 3),
                "nudge": 0 if len(keys) == 1 else (-1 if number == 0 else 1),
            })
    return days


def rate(part, whole, scale=100):
    """``part`` per ``scale`` of ``whole`` (a percentage by default), or None when there is no whole."""
    return Decimal(part) * scale / whole if whole else None


# Sections ------------------------------------------------------------------------------------------

def traffic_section(now, store):
    """People, page views and clicks for the last seven days, today against the same time last week, where
    people came from and which shops and products they clicked through to."""
    why = traffic.explain(now, store=store)
    today = why["today"]
    first = today - timedelta(days=WEEK_DAYS - 1)
    rows = why["days"]
    start = traffic.start_of(first)
    direct = dict(
        OutboundClick.objects.suspects().filter(created_at__gte=start, created_at__lte=now).order_by()
        .values_list("created_at__date").annotate(n=Count("id")).values_list("created_at__date", "n")
    )
    days = []
    for offset in range(WEEK_DAYS):
        day = first + timedelta(days=offset)
        row = rows.get(day, {})
        days.append({
            "date": day, "today": day == today, "burst": bool(row.get("burst")),
            "people": row.get("people", 0), "returning": row.get("returning", 0), "views": row.get("views", 0),
            "clicks": row.get("clicks", 0), "direct": direct.get(day, 0),
        })
    # Burst days are left out of the people, and of the clicks set against them; every click from people counts.
    counted = [day for day in days if not day["burst"]]
    people = sum(day["people"] for day in counted)
    clicks = sum(day["clicks"] for day in days)
    counted_clicks = sum(day["clicks"] for day in counted)
    returning = sum(day["returning"] for day in counted)
    bursts = [day["date"] for day in days if day["burst"]]

    # Where people came from, on the days counted: one query, grouped as Insights groups them.
    grouped = {}
    for host, n in (
        DailyVisitor.objects.filter(date__gte=first, date__lte=today, views__lt=insights.VIEWS_PER_VISITOR_CAP)
        .exclude(date__in=bursts).order_by().values_list("source").annotate(n=Count("id")).values_list("source", "n")
    ):
        group = insights.source_group(host)
        grouped[group] = grouped.get(group, 0) + n
    sourced = sum(grouped.values())
    sources = [{"name": name, "people": n, "share": share(n, sourced)}
               for name, n in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))[:TOP_SOURCES]]

    # Clicks from people this week by shop, grouped by how the shop earns, and the products clicked most.
    week = OutboundClick.objects.people().filter(created_at__gte=start, created_at__lte=now).order_by()
    networks = {}
    for pk, name, source_type, template, n in (
        week.values_list("retailer_id", "retailer__name", "retailer__source_type", "retailer__affiliate_url_template")
        .annotate(n=Count("id")).order_by("-n", "retailer__name")
    ):
        network = networks.setdefault(network_of(source_type, template), {"clicks": 0, "shops": []})
        network["clicks"] += n
        network["shops"].append({"name": name, "pk": pk, "clicks": n})
    week_clicks = sum(network["clicks"] for network in networks.values())
    names = {**NETWORK_NAMES, OTHER: say(store, "dashboard.clicks.network_other"),
             DIRECT: say(store, "dashboard.clicks.network_direct")}
    earning = [
        {"key": key, "name": names[key], "clicks": networks[key]["clicks"],
         "share": share(networks[key]["clicks"], week_clicks), "shops": networks[key]["shops"], "earns": key != DIRECT}
        for key in NETWORK_ORDER if key in networks
    ]
    products = [
        {"name": name, "url": reverse("web:product", args=[slug]), "clicks": n}
        for name, slug, n in week.values_list("product__name", "product__slug").annotate(n=Count("id"))
        .order_by("-n", "product__name")[:TOP_PRODUCTS]
    ]

    # Today so far, against the same hours of the day the explainer compares it with.
    so_far = why.get("today_so_far") or {}
    references = so_far.get("references") or []
    reference = references[0] if references else None
    today_row = days[-1]
    today_people = so_far["now"] if reference else today_row["people"]
    today_clicks = so_far["now_clicks"] if reference else today_row["clicks"]
    local_now = timezone.localtime(now)
    best = max(counted, key=lambda day: (day["people"], day["date"])) if people else None
    most_clicks = max(days, key=lambda day: (day["clicks"], day["date"])) if any(d["clicks"] for d in days) else None
    return {
        "has_visitors": any(day["people"] or day["views"] for day in days),
        "has_clicks": any(day["clicks"] or day["direct"] for day in days),
        "days": bars([dict(day) for day in days], ["people"]),
        "click_days": bars([dict(day) for day in days], ["clicks", "direct"]),
        "people": people, "returning": returning, "returning_share": share(returning, people),
        "views": sum(day["views"] for day in counted), "clicks": clicks,
        "direct": sum(day["direct"] for day in days),
        "per_person": round(counted_clicks / people, 2) if people else None,
        "bursts": bursts, "best": best, "most_clicks": most_clicks,
        "sources": sources, "earning": earning, "products": products,
        "headline": why["headline"] if why["enough"] else "",
        "today": {
            "people": today_people, "clicks": today_clicks, "burst": today_row["burst"], "until": f"{local_now:%H:%M}",
            "basis": reference["basis"] if reference else "",
            "then": reference["then"] if reference else None,
            "then_clicks": reference["then_clicks"] if reference else None,
            "people_change": change(today_people, reference["then"]) if reference else None,
            "clicks_change": change(today_clicks, reference["then_clicks"]) if reference else None,
        },
    }


MONEY_DAYS = 30
TOP_SHOPS = 12


def money_section(now, store):
    """What clicks to shops are worth over the last MONEY_DAYS days, whole site and by game and shop:

    * retailer click-through rate: visits with a click to a shop / visits (from the first day clicks carried
      the visit's token),
    * commission per 100 clicks: approved commission / clicks to shops x 100,
    * retailer conversion rate: sales the networks report (not declined) / clicks to shops,
    * revenue per 1,000 visits: approved commission / visits x 1,000,
    * affiliate coverage: in-stock offers at shops whose links earn / all in-stock offers.

    Clicks are people's (clicks.py), visits are DailyVisitor rows under the views cap, sales are AffiliateSale
    rows by order date, imported from the networks' reports. Visits cannot be split by game or shop, so the
    click-through rate and revenue per visit are for the whole site. Seven queries, however many rows."""
    today = timezone.localtime(now).date()
    first = today - timedelta(days=MONEY_DAYS - 1)
    clicks = OutboundClick.objects.people().filter(created_at__gte=traffic.start_of(first), created_at__lte=now)
    clicks = clicks.order_by()
    by = {"shop": {}, "game": {}}

    def add(kind, pk, name, **values):
        entry = by[kind].setdefault(pk, {"name": name, "clicks": 0, "sales": 0, "approved": Decimal("0"),
                                         "pending": Decimal("0"), "offers": 0, "earning": 0})
        for key, value in values.items():
            entry[key] += value or 0

    # Clicks by shop and game, with the first one that carried its visit's token.
    tracked = None
    for shop, shop_name, game, game_name, n, first_tracked in (
        clicks.values_list("retailer_id", "retailer__name", "product__game_id", "product__game__name")
        .annotate(n=Count("pk"), first_tracked=Min("created_at", filter=~Q(visitor="")))
    ):
        add("shop", shop, shop_name, clicks=n)
        add("game", game, game_name, clicks=n)
        if first_tracked and (tracked is None or first_tracked < tracked):
            tracked = first_tracked
    # The click-through rate counts from the first day clicks carried the visit's token, so days before it are
    # not counted as visits that never clicked.
    tracked_from = timezone.localtime(tracked).date() if tracked else first
    clicked_visits = (clicks.exclude(visitor="").values("created_at__date", "visitor").distinct().count()
                      if tracked else 0)
    counts = DailyVisitor.objects.filter(date__gte=first, date__lte=today, views__lt=insights.VIEWS_PER_VISITOR_CAP)
    counts = counts.aggregate(visits=Count("pk"), tracked=Count("pk", filter=Q(date__gte=tracked_from)))
    visits, tracked_visits = counts["visits"], counts["tracked"]
    clicked_visits = min(clicked_visits, tracked_visits)

    # Sales the networks report, not declined, by shop and game; approved commission is what counts as earned.
    for shop, shop_name, game, game_name, n, approved, pending in (
        AffiliateSale.objects.filter(ordered_on__gte=first, ordered_on__lte=today)
        .exclude(status=AffiliateSale.Status.DECLINED).order_by()
        .values_list("retailer_id", "retailer__name", "game_id", "game__name")
        .annotate(n=Count("pk"), approved=Sum("commission", filter=Q(status=AffiliateSale.Status.APPROVED)),
                  pending=Sum("commission", filter=Q(status=AffiliateSale.Status.PENDING)))
    ):
        add("shop", shop, shop_name, sales=n, approved=approved, pending=pending)
        add("game", game, game_name or say(store, "dashboard.money.no_game"), sales=n, approved=approved,
            pending=pending)

    # In-stock offers, and those at shops whose links earn (an affiliate link format, or eBay and Amazon, whose
    # links carry our campaign and tag, as insights.earning_share counts them): what the programmes cover.
    paying = (Retailer.Source.AMAZON, Retailer.Source.EBAY)
    for shop, shop_name, template, source_type, game, game_name, n in (
        Listing.objects.buyable().filter(availability=Listing.Availability.IN_STOCK, product__is_active=True)
        .order_by().values_list("retailer_id", "retailer__name", "retailer__affiliate_url_template",
                                "retailer__source_type", "product__game_id", "product__game__name")
        .annotate(n=Count("pk"))
    ):
        earns = n if template.strip() or source_type in paying else 0
        add("shop", shop, shop_name, offers=n, earning=earns)
        add("game", game, game_name, offers=n, earning=earns)

    def row(pk, entry, url=""):
        return {
            "name": entry["name"], "url": url, "clicks": entry["clicks"], "sales": entry["sales"],
            "commission": entry["approved"], "per_100": rate(entry["approved"], entry["clicks"]),
            "conversion": rate(entry["sales"], entry["clicks"]), "offers": entry["offers"],
            "coverage": rate(entry["earning"], entry["offers"]),
        }

    def order(item):
        return -item["commission"], -item["clicks"], item["name"].lower()

    games = sorted((row(pk, entry) for pk, entry in by["game"].items()), key=order)
    # The shops that matter here: those clicked or selling, and those with stock whose links earn nothing.
    shops = [row(pk, entry, reverse("admin:catalogue_retailer_change", args=[pk])) for pk, entry in by["shop"].items()]
    shops = [shop for shop in sorted(shops, key=order) if shop["clicks"] or shop["sales"] or shop["coverage"] == 0]

    total = {key: sum(entry[key] for entry in by["shop"].values())
             for key in ("clicks", "sales", "approved", "pending", "offers", "earning")}
    labels = dict(OutboundClick.Button.choices)
    pressed = [{"name": labels.get(key) or say(store, "dashboard.money.button_unknown"), "count": n}
               for key, n in clicks.values_list("button").annotate(n=Count("pk"))]
    compare = (DailyPageView.objects.filter(kind=DailyPageView.Kind.TAPS, date__gte=first, date__lte=today)
               .aggregate(n=Sum("hits"))["n"] or 0)
    if compare:
        pressed.append({"name": say(store, "dashboard.money.compare"), "count": compare})
    pressed.sort(key=lambda item: (-item["count"], item["name"]))
    presses = sum(item["count"] for item in pressed)
    for item in pressed:
        item["share"] = share(item["count"], presses)
    return {
        "days": MONEY_DAYS, "visits": visits, "clicks": total["clicks"], "sales": total["sales"],
        "commission": total["approved"], "pending": total["pending"], "has_sales": bool(total["sales"]),
        "tracked_from": timezone.localtime(tracked).date() if tracked else None, "tracked_visits": tracked_visits,
        "clicked_visits": clicked_visits, "ctr": rate(clicked_visits, tracked_visits) if tracked else None,
        "per_100": rate(total["approved"], total["clicks"]), "conversion": rate(total["sales"], total["clicks"]),
        "per_1000": rate(total["approved"], visits, 1000),
        "offers": total["offers"], "earning_offers": total["earning"], "coverage": rate(total["earning"], total["offers"]),
        "games": games, "shops": shops[:TOP_SHOPS], "more_shops": max(0, len(shops) - TOP_SHOPS),
        "pressed": pressed, "best_game": max((game for game in games if game["per_100"]), default=None,
                                            key=lambda game: game["per_100"]),
    }


def checks_section(now, store):
    """How many rows wait in each Things to check section, possible duplicates by certainty."""
    waiting = checks.waiting_counts(now)
    duplicates = checks.duplicate_counts(now)
    rows = [
        ("doubtful", "dashboard.checks.doubtful", waiting["doubtful"]),
        ("wrong", "dashboard.checks.wrong", waiting["wrong"]),
        ("duplicates", "dashboard.checks.duplicates", duplicates["pairs"]),
        ("found", "dashboard.checks.found", waiting["found"]),
        ("releases", "dashboard.checks.releases", waiting["releases"]),
        ("disagreements", "dashboard.checks.disagreements", waiting["disagreements"]),
        ("unknown_delivery", "dashboard.checks.unknown_delivery", waiting["unknown_delivery"]),
        ("stale_sources", "dashboard.checks.stale_sources", waiting["stale_sources"]),
    ]
    sections = [{"key": key, "label": say(store, label), "count": count} for key, label, count in rows]
    return {
        "sections": sections,
        "total": sum(row["count"] for row in sections),
        "lists": sum(1 for row in sections if row["count"]),
        # "Certain: same name, numbers and language" is shown as "Certain".
        "levels": [{"key": level, "label": label.split(":")[0], "count": n} for level, label, n in duplicates["levels"]],
        "duplicates_at": duplicates["counted_at"],
        "excluded": waiting["excluded"],
    }


def prices_section(now, store):
    """Of the products with a current price, how many show the Price check, and what it rests on: Cardmarket's
    price, our own daily record, or both (Product.price_band, kept each hour), and how many have an RRP (the
    owner's or UK shops'), for the whole site and each game, and Claude's matching of our products to Cardmarket's
    (matching): for each game Cardmarket lists, how many products Claude has looked at and matched, how many
    Cardmarket products were read and when their prices last were, so a game stuck at nought says why. Seven
    queries."""
    from catalogue.fair_price import BOTH, MARKET, RECORD

    on_sale = Exists(Listing.objects.buyable().filter(product=OuterRef("pk")))
    rows = (
        Product.objects.active().annotate(on_sale=on_sale).order_by().values("game__name", "game__slug")
        .annotate(
            products=Count("pk"),
            priced=Count("pk", filter=Q(on_sale=True)),
            checked=Count("pk", filter=Q(price_band__isnull=False)),
            market=Count("pk", filter=Q(price_basis__in=(MARKET, BOTH))),
            record=Count("pk", filter=Q(price_basis__in=(RECORD, BOTH))),
            rrp=Count("pk", filter=Q(on_sale=True) & (Q(rrp__isnull=False) | Q(shop_rrp__isnull=False))),
            looked=Count("cardmarket_match"),
            matched=Count("cardmarket_match", filter=Q(cardmarket_match__status__in=CardmarketMatch.USED)),
        )
    )
    rows = list(rows)
    listed = dict(CardmarketProduct.objects.order_by().values_list("game__slug").annotate(n=Count("pk")))
    priced_on = dict(CardmarketPrice.objects.order_by().values_list("product__game__slug").annotate(day=Max("date")))
    games = [{"name": row["game__name"], "covered": row["game__slug"] in cardmarket.GAMES,
              "listed": listed.get(row["game__slug"], 0), "priced_on": priced_on.get(row["game__slug"]),
              **{key: row[key] for key in ("products", "priced", "checked", "market", "record", "rrp", "looked", "matched")}}
             for row in rows if row["priced"]]
    for game in games:
        game["share"] = rate(game["checked"], game["priced"])
    total = {key: sum(game[key] for game in games) for key in ("priced", "checked", "market", "record", "rrp")}
    return {
        **total,
        "share": rate(total["checked"], total["priced"]),
        "market_share": rate(total["market"], total["priced"]),
        "record_share": rate(total["record"], total["priced"]),
        "rrp_share": rate(total["rrp"], total["priced"]),
        "games": sorted(games, key=lambda g: (-g["priced"], g["name"])),
        "matching": matching(now, sum(row["products"] for row in rows if row["game__slug"] in cardmarket.GAMES)),
    }


def matching(now, products):
    """Claude matching our products to Cardmarket's (catalogue/cardmarket_match.py): of the ``products`` in the
    games Cardmarket covers, how many it has looked at and what came of them, this month's spend and its last
    ask, how many days of Cardmarket prices we keep, and whether Claude is switched on to carry on. Four
    queries."""
    statuses = dict(CardmarketMatch.objects.order_by().values("status").annotate(n=Count("pk"))
                    .values_list("status", "n"))
    status = CardmarketMatch.Status
    asks = ClaudeAsk.objects.filter(kind=ClaudeAsk.Kind.MARKET).aggregate(
        cost=Sum("cost_micros", filter=Q(asked_at__gte=judge.month_start(now))),
        month=Count("pk", filter=Q(asked_at__gte=judge.month_start(now))), last=Max("asked_at"),
    )
    history = CardmarketPrice.objects.aggregate(since=Min("date"), days=Count("date", distinct=True))
    asked = sum(statuses.values())
    state = ClaudeJudge.current()
    return {
        "products": products, "asked": asked, "left": max(0, products - asked), "share": rate(asked, products),
        "matched": statuses.get(status.MATCHED, 0) + statuses.get(status.CONFIRMED, 0),
        "waiting": statuses.get(status.CHECK, 0),
        "none": statuses.get(status.NONE, 0) + statuses.get(status.REJECTED, 0),
        "asks_month": asks["month"], "spent": judge.dollars(asks["cost"] or 0),
        "last": insights.clock(asks["last"], now) if asks["last"] else "",
        "history_days": history["days"], "history_since": history["since"],
        "claude_on": state.enabled,
        "last_run": insights.clock(state.market_run_at, now) if state.market_run_at else "", "last_note": state.market_run_note,
    }


def shops_section(now, store):
    """Whether each shop is read on time: read in the last hour, up to date, paused, or late or failing (the
    shops to look at, failing first), the background reader and the last read to finish."""
    state = WorkerState.current()
    everything_paused = crawl.all_paused(state)
    counts = {"fresh": 0, "ok": 0, "paused": 0, "late": 0}
    problems = []
    rows = crawl.shops(now, state)
    for row in rows:
        retailer = row["retailer"]
        ok_at = retailer.last_ok_at
        failing = row["problem"] or retailer.error_streak > 0
        late = ok_at is None or ok_at < now - insights.stale_after(retailer)
        if everything_paused or retailer.reading_paused:
            counts["paused"] += 1
        elif failing or late:
            counts["late"] += 1
            problems.append({
                "name": retailer.name, "pk": retailer.pk, "failing": failing,
                "error": retailer.last_error[:160] if failing else "",
                "last_ok": insights.clock(ok_at, now) if ok_at else "",
            })
        elif now - ok_at <= READ_LATELY:
            counts["fresh"] += 1
        else:
            counts["ok"] += 1
    problems.sort(key=lambda row: (not row["failing"], row["name"].lower()))
    total = len(rows)
    segments = [{"key": key, "label": say(store, f"dashboard.shops.{key}"), "count": counts[key],
                 "share": round(100 * counts[key] / total, 3) if total else 0}
                for key in ("fresh", "ok", "paused", "late")]
    worker = crawl.worker_status(state, now)
    last = crawl.last_finished()
    return {
        "total": total, "fresh": counts["fresh"], "segments": segments, "problems": problems[:FAILING_SHOWN],
        "more_problems": max(0, len(problems) - FAILING_SHOWN), "all_paused": everything_paused, "worker": worker,
        "stopped_at": insights.clock(state.heartbeat_at, now) if worker["known"] and not worker["alive"] else "",
        "last_finished": insights.clock(last, now) if last else "",
    }


def customers_section(now, store):
    """Confirmed alerts waiting, alert emails sent this week, the refer a friend offer and the inbox."""
    from web.templatetags.ripraptor import refer_waiting

    first = timezone.localtime(now).date() - timedelta(days=WEEK_DAYS - 1)
    sent = (DailyPageView.objects.filter(kind=DailyPageView.Kind.ALERTS, key__in=SENT_KEYS, date__gte=first)
            .aggregate(n=Sum("hits"))["n"] or 0)
    offer = ReferralSettings.current()
    waiting = refer_waiting() or {"to_post": 0, "check": 0, "review": 0, "any": 0}
    if not offer.is_on:
        offer_line = "dashboard.customers.refer_off"
    elif referrals.taking_part(offer):
        offer_line = "dashboard.customers.refer_open"
    else:
        offer_line = "dashboard.customers.refer_closed"
    return {
        "drops": DropAlert.objects.filter(confirmed_at__isnull=False).count(),
        "searches": SearchAlert.objects.filter(confirmed_at__isnull=False).count(),
        "stock": StockAlert.objects.filter(confirmed_at__isnull=False).count(),
        "sent": sent,
        "unread": unread_count(),
        "shops": open_count(),
        "refer": {"on": offer.is_on, "line": say(store, offer_line), "keyrings": offer.keyrings_left,
                  "to_post": waiting["to_post"], "check": waiting["check"], "review": waiting["review"]},
    }


def gaps_section(now, store):
    """The searches this week that found FEW_RESULTS products or fewer, most made first: what to add."""
    first = timezone.localtime(now).date() - timedelta(days=WEEK_DAYS - 1)
    rows = (
        DailySearch.objects.filter(date__gte=first).values("query")
        .annotate(hits=Sum("hits"), most=Max("results")).filter(most__lte=FEW_RESULTS)
        .order_by("-hits", "query")[:TOP_GAPS]
    )
    return {"searches": [{"query": row["query"], "hits": row["hits"], "results": row["most"]} for row in rows]}


def claude_section(now, store):
    """What Claude costs this month against its limit, what it settled this month and today, its last run."""
    state = ClaudeJudge.current()
    spent = judge.spent_this_month(now)
    limit = judge.monthly_limit_micros(state)
    kind = judge.status_kind(state, now, bool(judge.key()), spent, limit)
    since = judge.month_start(now)
    day_ago = now - timedelta(hours=24)
    acts = judge.claudes_acts().filter(created_at__gte=min(since, day_ago)).aggregate(
        month=Count("pk", filter=Q(created_at__gte=since)), day=Count("pk", filter=Q(created_at__gte=day_ago)),
    )
    reason = judge.problem_text(state.problem) if kind == "stopped" else ""
    return {
        "kind": kind, "status": say(store, f"dashboard.claude.{kind}", reason=reason),
        "spent": judge.dollars(spent), "limit": judge.dollars(limit),
        "share": min(100, round(100 * spent / limit, 1)) if limit else 0, "at_limit": kind == "limit" or bool(limit and spent >= limit),
        "settled_month": acts["month"], "settled_day": acts["day"],
        "last_run": insights.clock(state.last_run_at, now) if state.last_run_at else "",
        "last_note": state.last_run_note,
    }


SECTIONS = [
    ("money", money_section),
    ("traffic", traffic_section),
    ("checks", checks_section),
    ("prices", prices_section),
    ("shops", shops_section),
    ("customers", customers_section),
    ("gaps", gaps_section),
    ("claude", claude_section),
]


def section(name, build, now, store, fresh=False):
    """{"built_at", "data"} for one section, from the shared cache unless ``fresh`` or older than a minute."""
    key = SECTION_KEY.format(name=name)
    found = None if fresh else cache.get(key)
    if found is None:
        found = {"built_at": now, "data": build(now, store)}
        cache.set(key, found, SECTION_SECONDS)
    return found


def admin_links():
    """The admin pages each section opens, so the template and the tests share one list."""
    changelist = "admin:catalogue_{}_changelist".format
    return {
        "insights": reverse("insights") + "?days=7",
        "insights_clicks": reverse("insights") + "?days=7#clicks",
        "insights_searches": reverse("insights") + "?days=7#searches",
        "clicks": reverse(changelist("outboundclick")) + "?suspect__exact=0",
        "checks": reverse("checks"),
        "crawl": reverse("crawl"),
        "runs": reverse(changelist("importrun")),
        "drops": reverse(changelist("dropalert")) + "?confirmed_at__isnull=False",
        "searches": reverse(changelist("searchalert")) + "?confirmed_at__isnull=False",
        "stock": reverse(changelist("stockalert")) + "?confirmed_at__isnull=False",
        "refer": reverse(changelist("referralsettings")),
        "to_post": reverse(changelist("keyringclaim")) + "?state=to_post",
        "review": reverse(changelist("referral")) + "?status__exact=review",
        "unread": reverse("admin:inbox_conversation_changelist") + "?unread__exact=1",
        "shops": reverse("admin:inbox_shopenquiry_changelist") + "?handled=no",
        "claude": reverse(changelist("claudeask")),
        "sales": reverse(changelist("affiliatesale")),
        "sales_import": reverse("admin:catalogue_affiliatesale_import"),
        "matches": reverse(changelist("cardmarketmatch")) + "?status__exact=check",
        "admin": reverse("admin:index"),
    }


@staff_member_required
@require_GET
def dashboard(request):
    """The owner's dashboard. Staff only, never indexed or cached by a browser or proxy, no third-party script."""
    now = timezone.now()
    store = copy.for_request(request)
    fresh = request.GET.get("fresh") == "1"
    found = {name: section(name, build, now, store, fresh) for name, build in SECTIONS}
    data = {name: entry["data"] for name, entry in found.items()}
    context = {
        **data,
        "updated": timezone.localtime(min(entry["built_at"] for entry in found.values())),
        "links": admin_links(),
        "few_results": FEW_RESULTS,
        "meta_title": say(store, "dashboard.title"),
        "noindex": True,
        "robots": "noindex, nofollow",
    }
    response = render(request, "web/dashboard.html", context)
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
