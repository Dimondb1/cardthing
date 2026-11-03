"""
The dashboard as one plain text file (/dashboard/report.txt), to attach to a message rather than screenshot.

The owner, 3 Nov 2026, looking at Search Console on a phone: "Can you produce a text report in admin download that
I can just attach to you". Every figure is the dashboard's own (web/dashboard.py, built fresh here, not from the
minute's cache) and the Insights page's for the last 28 days (catalogue/insights.py report), with the few counts
neither keeps as a list: where each day started (landing pages) and the pages the sitemap offers Google. Nothing is
estimated, nothing is interpreted, and a section with no rows says so: the report is the figures, for whoever reads
them to reason from.

It holds nothing personal: no email address, no visitor token, no message. Searches people made and shop names are
in it, as Insights shows them. Staff only, never cached, never indexed. Its headings are in the code: it is a file
for the owner and a reader, not a public page.
"""

import textwrap
from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Min
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import insights, traffic
from catalogue.models import DailyVisitor, Game, Listing, Product, ProductSet
from content import service as copy
from guides.access import not_for_editors
from web import dashboard

MONTH_DAYS = 28
TOP = 10
WIDTH = 78


# Writing it ------------------------------------------------------------------------------------------------------

class Text:
    """Lines of a plain text report: headings, key: value lines, and tables that line up in a fixed width font."""

    def __init__(self):
        self.lines = []

    def heading(self, title):
        self.lines += ["", "", title.upper(), "=" * len(title)]

    def sub(self, title):
        self.lines += ["", title, "-" * len(title)]

    def say(self, text=""):
        self.lines.append(text)

    def prose(self, text):
        """A sentence or two, wrapped to the width."""
        self.lines += textwrap.wrap(text, WIDTH)

    def fact(self, label, value):
        self.lines.append(f"{label}: {value}")

    def table(self, columns, rows, empty="Nothing recorded."):
        """``columns`` are (title, key, align) with align "<" or ">"; ``rows`` are dicts. Numbers right, words left."""
        if not rows:
            self.lines.append(empty)
            return
        cells = [[text(row.get(key)) for _title, key, _align in columns] for row in rows]
        widths = [max(len(title), *(len(line[i]) for line in cells)) for i, (title, _key, _align) in enumerate(columns)]
        aligns = [align for _title, _key, align in columns]

        def line(parts):
            return "  ".join(f"{part:{align}{width}}" for part, align, width in zip(parts, aligns, widths)).rstrip()

        self.lines.append(line([title for title, _key, _align in columns]))
        self.lines.append(line(["-" * width for width in widths]))
        self.lines += [line(parts) for parts in cells]

    def __str__(self):
        return "\n".join(self.lines).rstrip() + "\n"


def text(value):
    """A value as the report writes it: a percentage or money to two places, a count plain, nothing as a dash."""
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, float):
        return f"{value:.1f}"
    if hasattr(value, "strftime"):
        return value.strftime("%a %d %b")
    return str(value)


def percent(value):
    return "-" if value is None else f"{Decimal(value):.1f}%"


def pounds(value):
    return f"£{Decimal(value or 0):.2f}"


# The report ------------------------------------------------------------------------------------------------------

def build(now, store):
    """The whole report as text. The dashboard's sections are built fresh; Insights' report once."""
    local = timezone.localtime(now)
    today = local.date()
    data = {name: builder(now, store) for name, builder in dashboard.SECTIONS}
    month = insights.report(days=MONTH_DAYS)
    out = Text()
    out.say(f"RipRaptor report, {local:%A %d %B %Y, %H:%M} UK time")
    out.prose("Every figure is read from the site's own database, as the Dashboard and Insights pages show it. "
              "It holds nothing personal: no email address, visitor token or message.")
    out.prose("People are visitors whose browser ran the page; a script or a crawler is left out, and so is a burst "
              "day (a day one source flooded). Clicks are people's clicks through to a shop.")
    out.prose("Google's own figures (impressions, clicks from search, pages indexed) are not here: they live only in "
              "Search Console.")

    headline(out, data["traffic"], month, today)
    by_day(out, data["traffic"], month)
    sources(out, data["traffic"], month, now, today)
    products(out, data["products"], month)
    searches(out, data["searched"], data["gaps"], month)
    clicks(out, data["traffic"], data["shop_games"], month)
    money(out, data["money"])
    catalogue(out, month, data["prices"], data["checks"])
    shops(out, data["shops"])
    customers(out, data["customers"], month)
    guides(out, data["guides"])
    claude(out, data["claude"], now)
    return str(out)


def headline(out, week, month, today):
    out.heading("At a glance")
    out.fact("Period", f"last 7 days to {today:%a %d %b}, and last {MONTH_DAYS} days from {month['since']:%a %d %b}")
    rows = [
        {"what": "People", "week": week["people"], "month": month["total_visitors"]},
        {"what": "Of them returning", "week": week["returning"], "month": month["returning_visitors"]},
        {"what": "Page views", "week": week["views"], "month": month["total_views"]},
        {"what": "Clicks to shops", "week": week["clicks"], "month": month["total_clicks"]},
        {"what": "Direct hits (not people)", "week": week["direct"], "month": "-"},
        {"what": "Clicks per person", "week": week["per_person"], "month": "-"},
        {"what": "Pages per visitor", "week": "-", "month": month["pages_per_visitor"]},
        {"what": "Clicks per 100 product views", "week": "-", "month": percent(month["click_rate"])},
    ]
    out.table([("", "what", "<"), ("7 days", "week", ">"), (f"{MONTH_DAYS} days", "month", ">")], rows)
    out.say()
    out.prose(f"The {MONTH_DAYS} day column counts every visitor under the views cap, as Insights does; the 7 day "
              "column counts people only.")
    if week["bursts"]:
        out.fact("Burst days left out of the 7 day people", ", ".join(f"{day:%a %d %b}" for day in week["bursts"]))
    if week["automated"]:
        out.fact("Automated visits in the last 7 days", week["automated"])
    today_row = week["today"]
    if today_row["basis"]:
        out.fact(f"Today to {today_row['until']}", f"{today_row['people']} people and {today_row['clicks']} clicks, "
                 f"against {today_row['then']} and {today_row['then_clicks']} {today_row['basis']}")
    else:
        out.fact(f"Today to {today_row['until']}", f"{today_row['people']} people and {today_row['clicks']} clicks")
    if week["headline"]:
        out.fact("The traffic explainer says", week["headline"])
    home = week["home"]
    if home["landed"]:
        out.fact("Of people who landed on the home page, opened a product",
                 f"{home['on']} of {home['landed']} ({home['share']}%)")


def by_day(out, week, month):
    out.heading(f"By day, last {MONTH_DAYS} days")
    week_days = {day["date"]: day for day in week["days"]}
    rows = []
    for day in month["by_day"]:
        people = week_days.get(day["date"])
        rows.append({
            "date": day["date"], "visitors": day["visitors"], "returning": day["returning"], "views": day["views"],
            "clicks": day["clicks"],
            "people": (people["people"] if people and not people["burst"] else "burst" if people else "-"),
            "direct": people["direct"] if people else "-",
        })
    out.table([("Day", "date", "<"), ("Visitors", "visitors", ">"), ("Returning", "returning", ">"),
               ("Views", "views", ">"), ("Clicks", "clicks", ">"), ("People (7 days)", "people", ">"),
               ("Direct hits", "direct", ">")], rows)


def sources(out, week, month, now, today):
    out.heading("Where people came from")
    out.sub("Last 7 days, people, grouped")
    out.table([("Source", "name", "<"), ("People", "people", ">"), ("Share", "share", ">")],
              [{**row, "share": f"{row['share']}%"} for row in week["sources"]])
    out.sub(f"Last {MONTH_DAYS} days, visitors, grouped")
    out.table([("Source", "name", "<"), ("Visitors", "visitors", ">"), ("Share", "share", ">")],
              [{**row, "share": f"{row['share']}%"} for row in month["sources"]])
    out.sub(f"Last {MONTH_DAYS} days, the sites that sent most")
    out.table([("Site", "host", "<"), ("Visitors", "visitors", ">")],
              [{"host": host, "visitors": n} for host, n in month["top_hosts"]])
    out.sub("Devices and countries, visitors")
    out.table([("Device", "name", "<"), ("Visitors", "visitors", ">"), ("Share", "share", ">")],
              [{**row, "share": f"{row['share']}%"} for row in month["devices"]])
    out.say()
    out.table([("Country", "name", "<"), ("Visitors", "visitors", ">"), ("Share", "share", ">")],
              [{**row, "share": f"{row['share']}%"} for row in month["countries"]])
    if not month["geoip_ready"] and not month["countries"]:
        out.say("Countries are not recorded: the country database is not set up.")
    out.sub("Where people's visits began, last 7 days")
    out.table([("First page", "page", "<"), ("People", "people", ">")], landings(now, today))


def landings(now, today):
    """The pages people's days began on in the last 7 days, most first, as "kind:key" (a product by its slug). People
    are counted as the traffic explainer counts them: browsers, from the first day a page reported in."""
    first = today - timedelta(days=dashboard.WEEK_DAYS - 1)
    browser_since = (DailyVisitor.objects.filter(browser=True, views__lt=insights.VIEWS_PER_VISITOR_CAP,
                                                 date__gte=today - timedelta(days=traffic.WINDOW_DAYS))
                     .aggregate(first=Min("date"))["first"])
    rows = (DailyVisitor.objects.filter(traffic.people_q(browser_since), date__gte=first, date__lte=today,
                                        views__lt=insights.VIEWS_PER_VISITOR_CAP).exclude(landing="")
            .order_by().values_list("landing").annotate(n=Count("id")).order_by("-n", "landing")[:TOP])
    return [{"page": page, "people": n} for page, n in rows]


def products(out, week, month):
    out.heading("Products")
    out.sub("Clicked through to a shop most, last 7 days")
    out.table([("Product", "name", "<"), ("Clicks", "clicks", ">"), ("Views", "views", ">")], week["clicked"])
    out.sub("Opened most, last 7 days")
    out.table([("Product", "name", "<"), ("Views", "views", ">"), ("Clicks", "clicks", ">")], week["viewed"])
    out.sub(f"Opened most, last {MONTH_DAYS} days")
    out.table([("Product", "name", "<"), ("Views", "views", ">"), ("Clicks", "clicks", ">")],
              [{"name": row["product"].name, "views": row["views"], "clicks": row["clicks"]}
               for row in month["top_products"][:TOP]])
    out.sub(f"Looked at (3 views or more) but never clicked, last {MONTH_DAYS} days")
    out.table([("Product", "name", "<"), ("Views", "views", ">"), ("Shops", "shops", ">")],
              [{"name": row["product"].name, "views": row["views"], "shops": row["shops"]}
               for row in month["viewed_no_click"][:TOP]])
    out.sub(f"Opened, but only one shop sells it, last {MONTH_DAYS} days")
    out.table([("Product", "name", "<"), ("Views", "views", ">")],
              [{"name": row["product"].name, "views": row["views"]} for row in month["one_shop"][:TOP]])


def searches(out, week, gaps, month):
    out.heading("Searches on the site")
    out.sub("Made most, last 7 days")
    out.table([("Search", "query", "<"), ("Times", "hits", ">"), ("Found", "results", ">"),
               ("Week before", "before", ">")], week["searches"])
    out.sub("Found nothing, last 7 days")
    out.table([("Search", "query", "<"), ("Times", "hits", ">")], gaps["nothing"])
    out.sub(f"Found {dashboard.FEW_RESULTS} products or fewer, last 7 days")
    out.table([("Search", "query", "<"), ("Times", "hits", ">"), ("Found", "results", ">")], gaps["few"])
    out.sub(f"Made most, last {MONTH_DAYS} days")
    out.table([("Search", "query", "<"), ("Times", "hits", ">")], month["top_searches"][:TOP])


def clicks(out, week, shop_games, month):
    out.heading("Clicks to shops")
    out.sub("Last 7 days, by how the shop earns")
    rows = []
    for network in week["earning"]:
        rows.append({"name": network["name"], "clicks": network["clicks"], "share": f"{network['share']}%"})
        rows += [{"name": f"  {shop['name']}", "clicks": shop["clicks"], "share": ""} for shop in network["shops"]]
    out.table([("Network and shop", "name", "<"), ("Clicks", "clicks", ">"), ("Share", "share", ">")], rows)
    out.sub(f"Last {MONTH_DAYS} days, by shop")
    out.table([("Shop", "name", "<"), ("Clicks", "clicks", ">")], month["shops"][:TOP])
    out.sub(f"Last {MONTH_DAYS} days, by game")
    out.table([("Game", "name", "<"), ("Clicks", "clicks", ">")], month["games"])
    out.sub(f"Last {MONTH_DAYS} days, by kind of product")
    out.table([("Kind", "name", "<"), ("Clicks", "clicks", ">")], month["types"])
    out.sub(f"Each shop's most and least clicked game, last {shop_games['days']} days")
    out.table([("Shop", "name", "<"), ("Clicks", "clicks", ">"), ("Games", "games", ">"), ("Most", "most", "<"),
               ("Least", "least", "<")],
              [{"name": row["name"], "clicks": row["clicks"], "games": row["games"],
                "most": f"{row['most']['names']} ({row['most']['clicks']})",
                "least": f"{row['least']['names']} ({row['least']['clicks']})" if row["least"] else "-"}
               for row in shop_games["shops"]])
    out.sub(f"Clicks that can earn, last {MONTH_DAYS} days")
    out.fact("Clicks at shops whose links earn", f"{month['earning_clicks']} of {month['total_clicks']}")
    out.fact("Clicks from the watchlist", month["watchlist_clicks"])
    out.table([("Shop whose links earn nothing", "name", "<"), ("Clicks", "clicks", ">")], month["unpaid"],
              empty="Every clicked shop's links earn.")


def money(out, data):
    out.heading(f"What clicks are worth, last {data['days']} days")
    out.fact("Visits", data["visits"])
    out.fact("Clicks to shops", data["clicks"])
    out.fact("Sales the networks report", data["sales"])
    out.fact("Commission approved", pounds(data["commission"]))
    out.fact("Commission pending", pounds(data["pending"]))
    out.fact("Visits with a click to a shop", f"{data['clicked_visits']} of {data['tracked_visits']} "
             f"({percent(data['ctr'])})" if data["tracked_from"] else "not tracked yet")
    out.fact("Commission per 100 clicks", pounds(data["per_100"]) if data["per_100"] is not None else "-")
    out.fact("Conversion (sales per 100 clicks)", percent(data["conversion"]))
    out.fact("Revenue per 1,000 visits", pounds(data["per_1000"]) if data["per_1000"] is not None else "-")
    out.fact("In stock offers at shops whose links earn",
             f"{data['earning_offers']} of {data['offers']} ({percent(data['coverage'])})")
    out.sub("By game")
    out.table([("Game", "name", "<"), ("Clicks", "clicks", ">"), ("Sales", "sales", ">"),
               ("Commission", "commission", ">"), ("Offers", "offers", ">"), ("Earning", "coverage", ">")],
              [{**row, "commission": pounds(row["commission"]), "coverage": percent(row["coverage"])}
               for row in data["games"]])
    out.sub("By shop (clicked, selling, or earning nothing)")
    out.table([("Shop", "name", "<"), ("Clicks", "clicks", ">"), ("Sales", "sales", ">"),
               ("Commission", "commission", ">"), ("Offers", "offers", ">"), ("Earning", "coverage", ">")],
              [{**row, "commission": pounds(row["commission"]), "coverage": percent(row["coverage"])}
               for row in data["shops"]])
    out.sub("What people pressed")
    out.table([("Button", "name", "<"), ("Presses", "count", ">"), ("Share", "share", ">")],
              [{**row, "share": f"{row['share']}%"} for row in data["pressed"]])


def catalogue(out, month, prices, checks):
    out.heading("The catalogue")
    stats = month["catalogue"]
    out.fact("Products on the site", stats["total"])
    out.fact("In stock somewhere", stats["in_stock"])
    out.fact("In stock at two shops or more (a real comparison)", stats["compared"])
    out.fact("Without a picture", stats["no_image"])
    out.fact("Shops", month["shop_count"])
    out.fact("Offers on sale now", Listing.objects.buyable().filter(product__is_active=True).count())
    out.sub("Pages the sitemap offers Google")
    out.fact("Product pages", Product.objects.active().count())
    out.fact("Set pages", ProductSet.objects.filter(game__is_active=True).count())
    out.fact("Game pages", Game.objects.filter(is_active=True).count())
    out.fact("Guides published", published_guides())
    out.sub("The Price check")
    out.fact("Products with a current price", prices["priced"])
    out.fact("Of them showing the Price check", f"{prices['checked']} ({percent(prices['share'])})")
    out.fact("With an RRP", f"{prices['rrp']} ({percent(prices['rrp_share'])})")
    out.sub("Things to check, waiting")
    out.table([("List", "label", "<"), ("Rows", "count", ">")], checks["sections"])
    out.fact("Possible duplicates by certainty",
             ", ".join(f"{level['label']} {level['count']}" for level in checks["levels"]) or "none counted")


def published_guides():
    from guides.models import Guide

    return Guide.objects.filter(is_published=True).count()


def shops(out, data):
    out.heading("Shop health")
    out.table([("State", "label", "<"), ("Shops", "count", ">")], data["segments"])
    worker = data["worker"]
    out.fact("Background reader", "running" if worker.get("alive") else "stopped" if worker.get("known") else "unknown")
    if data["stopped_at"]:
        out.fact("Last heartbeat", data["stopped_at"])
    if data["last_finished"]:
        out.fact("Last read finished", data["last_finished"])
    if data["all_paused"]:
        out.say("Every shop's reading is paused.")
    out.sub("Shops late or failing")
    out.table([("Shop", "name", "<"), ("Failing", "failing", "<"), ("Last good read", "last_ok", "<"),
               ("Error", "error", "<")], data["problems"], empty="None.")
    if data["more_problems"]:
        out.say(f"And {data['more_problems']} more on the Crawl health page.")


def customers(out, data, month):
    out.heading("Alerts, messages and the offer")
    out.fact("Drop reminders confirmed", data["drops"])
    out.fact("Search alerts confirmed", data["searches"])
    out.fact("Back in stock alerts confirmed", data["stock"])
    out.fact("Alert emails sent, last 7 days", data["sent"])
    alerts = month["alerts"]
    out.fact(f"Alerts asked, confirmed, sent, stopped, last {MONTH_DAYS} days",
             f"{alerts['asked']}, {alerts['confirmed']}, {alerts['sent']}, {alerts['stopped']}")
    install = month["install"]
    out.fact(f"Home screen prompt shown, added, dismissed, opened, last {MONTH_DAYS} days",
             f"{install['shown']}, {install['added']}, {install['dismissed']}, {install['opened']}")
    out.fact("Unread messages", data["unread"])
    out.fact("Shops asking to be listed", data["shops"])
    refer = data["refer"]
    out.fact("Refer a friend", refer["line"])
    if refer["on"]:
        out.fact("Keyrings left, to post, to check, to review",
                 f"{refer['keyrings']}, {refer['to_post']}, {refer['check']}, {refer['review']}")


def guides(out, data):
    if data is None:
        return
    out.heading("Guides, last 7 days")
    out.fact("Guide views", data["views"])
    out.fact("Taps from a guide or the checker to a product", data["taps"])
    out.fact("Came back on a later day having read a guide", data["back"])
    out.fact("Fair Price Checker uses", data["checks"])
    out.fact("Useful: yes, no (30 days)", f"{data['yes']}, {data['no']}")
    out.table([("Guide", "title", "<"), ("Views", "views", ">"), ("Yes", "yes", ">"), ("No", "no", ">"),
               ("Review due", "due", "<")], data["rows"])


def claude(out, data, now):
    out.heading("Claude")
    out.fact("State", data["status"])
    out.fact("Spent this month", f"{data['spent']} of {data['limit']}")
    out.fact("Rows settled this month, last 24 hours", f"{data['settled_month']}, {data['settled_day']}")
    if data["last_run"]:
        out.fact("Last run", f"{data['last_run']} {data['last_note']}".strip())
    out.sub("By job, this month")
    out.table([("Job", "title", "<"), ("Asked", "asked", ">"), ("Spent", "spent", ">"), ("On", "on", "<"),
               ("Own limit", "cap", ">")], claude_jobs(now))


def claude_jobs(now):
    """What each of Claude's jobs (judge.JOBS) asked and spent this month, and how the Claude page has it set."""
    from django.db.models import Count

    from catalogue import judge
    from catalogue.models import ClaudeAsk

    asked = dict(ClaudeAsk.objects.filter(asked_at__gte=judge.month_start(now)).order_by().values_list("kind")
                 .annotate(n=Count("pk")).values_list("kind", "n"))
    spent, jobs = judge.spent_by_job(now), judge.job_settings()
    return [{"title": title, "asked": sum(asked.get(kind, 0) for kind in kinds), "spent": judge.dollars(spent[name]),
             "on": jobs[name].on, "cap": "-" if jobs[name].monthly_budget_usd is None else f"${jobs[name].monthly_budget_usd}"}
            for name, (title, kinds) in judge.JOBS.items()]


# The download ----------------------------------------------------------------------------------------------------

@staff_member_required
@not_for_editors
@require_GET
def download(request):
    """The report as a file to attach: ripraptor-report-<date>.txt. Staff only, never cached or indexed."""
    now = timezone.now()
    body = build(now, copy.for_request(request))
    response = HttpResponse(body, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="ripraptor-report-{timezone.localtime(now):%Y-%m-%d}.txt"'
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
