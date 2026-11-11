"""
Shop performance: how the shops that pay a commission (an affiliate link format, eBay and Amazon) are listed, shown,
clicked and bought from, over the last 7, 30 or 90 days, on one Admin page and as a spreadsheet to download. Staff only,
never for editors. The owner, 11 Nov 2026: "produce a listings and performance report to show how much our affiliate
stores products are being shown, clicked on, clicked buy ... a report to export".

For each shop:

* listed: its products with a current price; in stock: of those, in stock now; cheapest now: products whose page
  leads with it today (the Buy now), worked out as the product page does (views.buy_order);
* page views: people's views of the product pages that list it now (DailyPageView, each product page lists every
  shop that has it, so each shop on a page counts the view);
* shown first: views of a product page that led with it, its Buy now (DailyPageView kind LEAD, counted since 11 Nov
  2026);
* clicks to the shop by people (OutboundClick.people), split by button: Buy now (on the page and on the phone's bar),
  a shop in the price list, a product card or list elsewhere, and the eBay and Amazon search buttons;
* sales the networks report, not declined, with the commission approved and pending (AffiliateSale).

Nothing is guessed. What the site does not record is said in the spreadsheet's Notes sheet: how often a product card
in a list showed the shop's price is not counted, and page views are matched to today's listings.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue.models import AffiliateSale, DailyPageView, Listing, OutboundClick, Product, Retailer
from guides.access import not_for_editors

from . import dashboard

PERIODS = (7, 30, 90)
DEFAULT_DAYS = 30
# The rows the spreadsheet's Products sheet keeps, busiest first.
MOST_PRODUCT_ROWS = 20000
BUY = {OutboundClick.Button.BUY_NOW, OutboundClick.Button.BUY_BAR}
PRICE_LIST = {OutboundClick.Button.PRICE_LIST, OutboundClick.Button.LOTTERY}
SEARCH = {OutboundClick.Button.AMAZON, OutboundClick.Button.EBAY}
NETWORKS = {dashboard.EBAY: "eBay Partner Network", dashboard.AMAZON: "Amazon Associates", dashboard.AWIN: "Awin",
            dashboard.CJ: "CJ", dashboard.RAKUTEN: "Rakuten", dashboard.OTHER: "Other network"}


def days_asked(request):
    try:
        days = int(request.GET.get("days", DEFAULT_DAYS))
    except ValueError:
        return DEFAULT_DAYS
    return days if days in PERIODS else DEFAULT_DAYS


def paying_shops():
    """{retailer id: Retailer} for every shop whose clicks can earn: an affiliate link format, or eBay and Amazon."""
    shops = Retailer.objects.only("pk", "name", "slug", "is_active", "source_type", "affiliate_url_template")
    return {shop.pk: shop for shop in shops if shop.pays_commission}


def empty_counts():
    return {"views": 0, "shown": 0, "clicks": 0, "buy": 0, "buy_since": 0, "price_list": 0, "lists": 0,
            "search": 0, "sales": 0, "commission": Decimal("0"), "pending": Decimal("0"), "order_value": Decimal("0")}


def lead_now():
    """{product id: the retailer id its page leads with now}, in the product page's order (views.buy_order): in stock
    first, then the cheapest delivered. One query over every buyable listing, whatever the shop."""
    rows = (Listing.objects.buyable().filter(product__is_active=True).order_by()
            .values_list("product_id", "retailer_id", "availability", "delivery_known", "delivered_price",
                         "retailer__name"))
    from .views import IN_STOCK_FIRST

    best = {}
    for product, shop, availability, known, delivered, name in rows:
        key = (IN_STOCK_FIRST.get(availability, 2), not known, delivered, name)
        if product not in best or key < best[product][0]:
            best[product] = (key, shop)
    return {product: shop for product, (_key, shop) in best.items()}


def build(days=DEFAULT_DAYS, now=None):
    """Everything the page and the spreadsheet show for the last ``days`` days, today included. Eight queries."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    first = today - timedelta(days=days - 1)
    start = timezone.make_aware(datetime.combine(first, time.min))
    shops = paying_shops()
    totals = {pk: empty_counts() for pk in shops}
    pairs = defaultdict(empty_counts)        # (shop id, product id): the counts for one shop's product
    daily = defaultdict(empty_counts)        # (date, shop id)

    # What each shop lists now, and how: one query.
    listed = defaultdict(lambda: {"listed": 0, "in_stock": 0})
    state = {}                                # (shop, product): (in stock now, its delivered price now)
    slug_of, product_of_slug = {}, {}
    for shop, product, slug, availability, delivered, known in (
        Listing.objects.live().filter(retailer_id__in=list(shops), product__is_active=True).order_by()
        .values_list("retailer_id", "product_id", "product__slug", "availability", "delivered_price", "delivery_known")
    ):
        slug_of[product], product_of_slug[slug] = slug, product
        state[(shop, product)] = (availability == Listing.Availability.IN_STOCK, delivered if known else None)
    for shop, product, availability in (
        Listing.objects.buyable().filter(retailer_id__in=list(shops), product__is_active=True).order_by()
        .values_list("retailer_id", "product_id", "availability")
    ):
        listed[shop]["listed"] += 1
        listed[shop]["in_stock"] += availability == Listing.Availability.IN_STOCK
    leads = lead_now()
    cheapest = defaultdict(int)
    for product, shop in leads.items():
        if shop in shops:
            cheapest[shop] += 1

    # People's views of the product pages that list each shop now.
    views = dict(DailyPageView.objects.filter(kind=DailyPageView.Kind.PRODUCT, date__gte=first, date__lte=today)
                 .order_by().values_list("key").annotate(n=Sum("hits")))
    for (shop, product) in state:
        seen = views.get(slug_of[product], 0)
        if seen:
            totals[shop]["views"] += seen
            pairs[(shop, product)]["views"] += seen

    # Views of a product page that led with the shop, its Buy now, counted since 11 Nov 2026.
    lead_rows = (DailyPageView.objects.filter(kind=DailyPageView.Kind.LEAD, date__gte=first, date__lte=today)
                 .order_by().values_list("date", "key", "hits"))
    unknown_slugs = set()
    lead_counts = []
    for day, key, hits in lead_rows:
        shop_text, _, slug = key.partition(":")
        if not shop_text.isdigit() or int(shop_text) not in shops:
            continue
        lead_counts.append((day, int(shop_text), slug, hits))
        if slug not in product_of_slug:
            unknown_slugs.add(slug)
    if unknown_slugs:
        product_of_slug.update({slug: pk for pk, slug in Product.objects.filter(slug__in=unknown_slugs)
                                .values_list("pk", "slug")})
    for day, shop, slug, hits in lead_counts:
        totals[shop]["shown"] += hits
        daily[(day, shop)]["shown"] += hits
        if slug in product_of_slug:
            pairs[(shop, product_of_slug[slug])]["shown"] += hits
    first_lead = (DailyPageView.objects.filter(kind=DailyPageView.Kind.LEAD).order_by("date")
                  .values_list("date", flat=True).first())

    # People's clicks to the shops, by button.
    for shop, product, button, day, n in (
        OutboundClick.objects.people().filter(retailer_id__in=list(shops), created_at__gte=start, created_at__lte=now)
        .order_by().values_list("retailer_id", "product_id", "button", "created_at__date").annotate(n=Count("pk"))
    ):
        group = "buy" if button in BUY else "price_list" if button in PRICE_LIST else "search" if button in SEARCH \
            else "lists"
        for counts in (totals[shop], pairs[(shop, product)], daily[(day, shop)]):
            counts["clicks"] += n
            counts[group] += n
            if group == "buy" and first_lead is not None and day >= first_lead:
                counts["buy_since"] += n

    # The sales the networks report, not declined, by the day ordered.
    for shop, product, day, status, n, commission, value in (
        AffiliateSale.objects.filter(retailer_id__in=list(shops), ordered_on__gte=first, ordered_on__lte=today)
        .exclude(status=AffiliateSale.Status.DECLINED).order_by()
        .values_list("retailer_id", "click__product_id", "ordered_on", "status")
        .annotate(n=Count("pk"), commission=Sum("commission"), value=Sum("order_value"))
    ):
        places = [totals[shop], daily[(day, shop)]] + ([pairs[(shop, product)]] if product else [])
        for counts in places:
            counts["sales"] += n
            counts["order_value"] += value or 0
            counts["commission" if status == AffiliateSale.Status.APPROVED else "pending"] += commission or 0

    rows = []
    for pk, shop in shops.items():
        counts = totals[pk]
        row = {
            "pk": pk, "name": shop.name, "active": shop.is_active,
            "network": NETWORKS.get(dashboard.network_of(shop.source_type, shop.affiliate_url_template), "Other network"),
            "listed": listed[pk]["listed"], "in_stock": listed[pk]["in_stock"], "cheapest": cheapest[pk], **counts,
            "click_rate": dashboard.rate(counts["buy"] + counts["price_list"], counts["views"]),
            "buy_rate": dashboard.rate(counts["buy_since"], counts["shown"]),
        }
        if any(row[key] for key in ("listed", "views", "shown", "clicks", "sales")):
            rows.append(row)
    rows.sort(key=lambda row: (-row["clicks"], -row["shown"], -row["views"], -row["listed"], row["name"].lower()))
    total = empty_counts()
    for row in rows:
        for key in total:
            total[key] += row[key]
    for key in ("listed", "in_stock", "cheapest"):
        total[key] = sum(row[key] for row in rows)
    total["click_rate"] = dashboard.rate(total["buy"] + total["price_list"], total["views"])
    total["buy_rate"] = dashboard.rate(total["buy_since"], total["shown"])
    return {
        "days": days, "first": first, "last": today, "made": now, "shops": rows, "total": total,
        "lead_from": first_lead, "pairs": pairs, "daily": daily, "state": state, "leads": leads, "names": shops,
    }


def product_rows(report):
    """The Products sheet: one row for each shop's product that was viewed, shown first, clicked or sold in the
    period, busiest first, at most MOST_PRODUCT_ROWS. One query for the products' names."""
    pairs = [(key, counts) for key, counts in report["pairs"].items()
             if counts["views"] or counts["shown"] or counts["clicks"] or counts["sales"]]
    pairs.sort(key=lambda item: (-item[1]["clicks"], -item[1]["shown"], -item[1]["views"], item[0]))
    pairs = pairs[:MOST_PRODUCT_ROWS]
    products = Product.objects.select_related("game").in_bulk({product for (_shop, product), _counts in pairs})
    rows = []
    for (shop, product_id), counts in pairs:
        product = products.get(product_id)
        if product is None:
            continue
        in_stock, price = report["state"].get((shop, product_id), (False, None))
        rows.append({
            "shop": report["names"][shop].name, "product": product.name,
            "game": product.game.display_short if product.game_id else "", "type": product.type_name,
            "listed_now": (shop, product_id) in report["state"], "in_stock": in_stock, "price": price,
            "cheapest": report["leads"].get(product_id) == shop, **counts,
        })
    return rows


def daily_rows(report):
    rows = [{"date": day, "shop": report["names"][shop].name, **counts}
            for (day, shop), counts in report["daily"].items()]
    rows.sort(key=lambda row: (row["date"], row["shop"].lower()))
    return rows


def workbook(report):
    """The report as an Excel workbook (bytes): Summary, Shops, Products, Daily and Notes sheets."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    book = Workbook()
    bold = Font(bold=True)
    money, percent = '"£"#,##0.00', '0.0"%"'

    def sheet(title, headings, rows, formats=None, widths=None):
        found = book.create_sheet(title)
        found.append(headings)
        for cell in found[1]:
            cell.font = bold
        for row in rows:
            found.append(row)
        for index, heading in enumerate(headings, start=1):
            letter = get_column_letter(index)
            found.column_dimensions[letter].width = (widths or {}).get(heading, max(12, min(40, len(heading) + 2)))
            fmt = (formats or {}).get(heading)
            if fmt:
                for (cell,) in found.iter_rows(min_row=2, min_col=index, max_col=index):
                    cell.number_format = fmt
        found.freeze_panes = "A2"
        return found

    def pct(value):
        return float(round(value, 1)) if value is not None else None

    total = report["total"]
    summary = book.active
    summary.title = "Summary"
    period = f"{report['first']:%-d %b %Y} to {report['last']:%-d %b %Y} ({report['days']} days)"
    lines = [
        ("RipRaptor shop performance", ""), ("Period", period),
        ("Made", f"{timezone.localtime(report['made']):%-d %b %Y %H:%M}"),
        ("Shops that pay a commission, with activity", len(report["shops"])), ("", ""),
        ("Products listed now", total["listed"]), ("In stock now", total["in_stock"]),
        ("Products whose page leads with one of them now", total["cheapest"]),
        ("Views of product pages listing them", total["views"]), ("Shown first (Buy now)", total["shown"]),
        ("Clicks to them", total["clicks"]), ("Buy now clicks", total["buy"]),
        ("Price list clicks", total["price_list"]), ("Card and list clicks", total["lists"]),
        ("eBay and Amazon search clicks", total["search"]),
        ("Product page click rate (Buy now and price list clicks per 100 page views)", pct(total["click_rate"])),
        ("Buy now rate (Buy now clicks per 100 times shown first)", pct(total["buy_rate"])),
        ("Sales reported", total["sales"]), ("Commission approved", total["commission"]),
        ("Commission pending", total["pending"]), ("Order value", total["order_value"]),
    ]
    for line in lines:
        summary.append(line)
    summary["A1"].font = Font(bold=True, size=14)
    summary.column_dimensions["A"].width = 58
    summary.column_dimensions["B"].width = 34
    for cell in ("B19", "B20", "B21"):
        summary[cell].number_format = money

    headings = ["Shop", "Programme", "Switched on", "Listed now", "In stock now", "Cheapest now", "Page views",
                "Shown first", "Clicks", "Buy now clicks", "Price list clicks", "Card and list clicks",
                "Search clicks", "Product page click rate %", "Buy now rate %", "Sales", "Commission approved",
                "Commission pending", "Order value"]
    sheet("Shops", headings, [
        [row["name"], row["network"], "Yes" if row["active"] else "No", row["listed"], row["in_stock"], row["cheapest"],
         row["views"], row["shown"], row["clicks"], row["buy"], row["price_list"], row["lists"], row["search"],
         pct(row["click_rate"]), pct(row["buy_rate"]), row["sales"], row["commission"], row["pending"],
         row["order_value"]] for row in report["shops"]
    ], formats={"Product page click rate %": percent, "Buy now rate %": percent, "Commission approved": money,
                "Commission pending": money, "Order value": money}, widths={"Shop": 30, "Programme": 22})

    headings = ["Shop", "Product", "Game", "Type", "Listed now", "In stock now", "Its price now (delivered)",
                "Cheapest now", "Page views", "Shown first", "Clicks", "Buy now clicks", "Price list clicks",
                "Card and list clicks", "Search clicks", "Sales", "Commission approved"]
    sheet("Products", headings, [
        [row["shop"], row["product"], row["game"], row["type"], "Yes" if row["listed_now"] else "No",
         "Yes" if row["in_stock"] else "No", row["price"], "Yes" if row["cheapest"] else "No", row["views"],
         row["shown"], row["clicks"], row["buy"], row["price_list"], row["lists"], row["search"], row["sales"],
         row["commission"]] for row in product_rows(report)
    ], formats={"Its price now (delivered)": money, "Commission approved": money},
        widths={"Shop": 26, "Product": 60, "Game": 16, "Type": 20})

    headings = ["Date", "Shop", "Shown first", "Clicks", "Buy now clicks", "Price list clicks", "Card and list clicks",
                "Search clicks", "Sales", "Commission approved"]
    sheet("Daily", headings, [
        [row["date"], row["shop"], row["shown"], row["clicks"], row["buy"], row["price_list"], row["lists"],
         row["search"], row["sales"], row["commission"]] for row in daily_rows(report)
    ], formats={"Date": "dd/mm/yyyy", "Commission approved": money}, widths={"Date": 12, "Shop": 30})

    notes = book.create_sheet("Notes")
    for heading, lines in notes_lines(report["lead_from"]):
        notes.append([heading])
        notes.cell(row=notes.max_row, column=1).font = bold
        for line in lines:
            notes.append([line])
        notes.append([])
    notes.column_dimensions["A"].width = 120
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def notes_lines(lead_from):
    """[(heading, [lines])]: what each figure counts and what is not counted, for the page and the Notes sheet."""
    since = f"{lead_from:%-d %b %Y}" if lead_from else "the next update (none counted yet)"
    return [
        ("What each figure counts", [
            "Shops: those whose clicks can earn a commission (an affiliate link format, eBay and Amazon). Others are "
            "left out.",
            "Listed now: products the shop has a current price for (in stock or on pre-order). In stock now: of those, "
            "in stock.",
            "Cheapest now: products whose page leads with the shop today, the shop behind its Buy now button (in stock "
            "first, then the cheapest delivered).",
            "Page views: people's views of product pages that list the shop today. Every shop on a page counts the view.",
            f"Shown first: views of a product page that led with the shop, its Buy now. Counted since {since}.",
            "Clicks: people's clicks to the shop, from anywhere on the site. Scripts, prefetches and direct hits are "
            "left out, as on the dashboard.",
            "Buy now clicks: the Buy now button on a product page and on the bar at the foot of a phone's product page. "
            "Price list clicks: a shop in a product's list of prices, and Enter lottery. Card and list clicks: a product "
            "card, a Deals saving, a drop card, Coming soon, the watchlist and the swipe page. Search clicks: the eBay "
            "and Amazon search buttons.",
            "Product page click rate: Buy now and price list clicks per 100 page views (clicks made on a product page, "
            "against views of product pages). Buy now rate: Buy now clicks per 100 times shown first, from the day "
            "Shown first began.",
            "Sales: what the networks report, imported in Admin, not declined, by the day ordered. Commission approved "
            "is earned; pending may still be declined.",
        ]),
        ("What is not counted", [
            "How often a product card in a list (home, a game, Deals, search) showed a shop's price: lists are not "
            "counted card by card.",
            "Page views are matched to today's listings: a shop that stopped listing a product loses its earlier views "
            "of it, and one that started gains them.",
            "Sales a network has not reported, or that have not been imported yet.",
        ]),
    ]


@staff_member_required
@not_for_editors
@require_GET
def report_page(request):
    days = days_asked(request)
    report = build(days)
    response = render(request, "admin/shop_report.html", {
        **admin.site.each_context(request), "title": "Shop performance", "report": report, "periods": PERIODS,
        "notes": notes_lines(report["lead_from"]),
    })
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@staff_member_required
@not_for_editors
@require_GET
def report_download(request):
    days = days_asked(request)
    report = build(days)
    response = HttpResponse(workbook(report),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = (f'attachment; filename="ripraptor-shop-performance-'
                                       f'{report["last"]:%Y-%m-%d}-{days}-days.xlsx"')
    response["Cache-Control"] = "no-store, private"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
