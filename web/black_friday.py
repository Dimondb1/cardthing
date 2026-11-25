"""
Black Friday: genuine price drops on sealed TCG products at UK shops, at /black-friday/ and a page for each game,
/black-friday/<game>/. The owner, 25 Nov 2026 (a brief for Black Friday and Cyber Monday 2026): "Real Black Friday
deals. Not inflated discounts." The addresses stay all year and serve every year's Black Friday: the year in the
titles is the current one, and the dates are worked out, never typed in.

What counts as a deal. Every figure comes from RipRaptor's own record of each product's cheapest delivered price each
day (DailyLowestPrice, as the product page's Price check and the Deals page use). A product is listed when:

- a UK shop has it in stock now, at a confirmed delivered price, and that price is not doubtful (catalogue/sanity.py);
- its Price check rests on our own record (Product.price_basis): at least fair_price.MIN_SHOPS UK shops list it and
  it has fair_price.MIN_DAYS days of prices;
- the Price check calls today's price a good price or the lowest yet (Product.price_band, kept each hour by
  fair_price.refresh_bands), which sets it against the whole year as well as Cardmarket and the RRP, so a price that
  was pushed up and then cut back is not called good;
- and today's price is at least DROP under its average over the last MONTH days (with MONTH_LEAST days recorded in
  them, and at least LEAST_SAVING), or at or below the lowest we have recorded with the Price check's "Lowest yet".

A fall larger than offers.MAX_REAL_PERCENT is a wrong product link appearing, not a deal, as on the Deals page.

Two kinds of saving are shown apart, as the brief asks: the price history (today against the product's own past
prices: the 30-day average, the 7 and 30-day changes, the lowest recorded) and the difference between shops (today's
cheapest against the next shop's, offers.summarise), which is never called a reduction.

A game's page is published (indexed, in the sitemap, linked from the hub) when it has PUBLISH_LEAST deals now; below
that it still answers, for a visitor who follows a link, but asks search engines not to index it.

Queries: the deals (one, plus one for the shops' offers of those shown, one for the cheapest shop of each, and one for
the questions' booster box figures). Pages are kept like any list page (web.pagecache).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import (Avg, Count, DecimalField, ExpressionWrapper, F, FloatField, IntegerField, Min,
                              OuterRef, Q, Subquery)
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_GET

from catalogue import fair_price, offers
from catalogue.models import DailyLowestPrice, Game, Product, Retailer
from catalogue.pricing import MARKETPLACES, doubtful_low
from catalogue.types import DEFAULT as DEFAULT_TYPES, GAME_TYPES, plural_words, type_label

from . import hubs
from .pagecache import cached_page

WEEK, MONTH = 7, 30
MONTH_LEAST = offers.LOW_MIN_DAYS          # days with a price in the last MONTH, to have an average worth quoting
DROP = fair_price.GOOD                     # 5%: under the 30-day average by at least this
LEAST_SAVING = Decimal("0.50")             # and by at least this much money
PUBLISH_LEAST = 3                          # deals a game's page needs to be indexed
SHOWN = {None: 60, "game": 120}            # rows the hub and a game's page show
SEASON_FROM = 14                           # the banner and the prominent links show this many days before
BOX = "booster_box"
QUESTION_GAME = "pokemon"                  # the hub's questions name this game, as the brief does
TOP_SHOPS = 3
PENCE = Decimal("0.01")
GOOD_BANDS = (fair_price.GOOD_PRICE, fair_price.LOWEST)
ON_RECORD = (fair_price.RECORD, fair_price.BOTH)

SORTS = ("percent", "pounds", "lowest")    # biggest % saving, biggest £ saving, lowest recorded prices only


def black_friday(year):
    """Black Friday of ``year``: the day after the fourth Thursday of November."""
    first = date(year, 11, 1)
    thursday = first + timedelta(days=(3 - first.weekday()) % 7)
    return thursday + timedelta(weeks=3, days=1)


def cyber_monday(year):
    return black_friday(year) + timedelta(days=3)


@dataclass
class Season:
    year: int
    friday: date
    monday: date
    today: date

    @property
    def state(self):
        """"before" the banner's days, "soon" in them, "on" from Black Friday to Cyber Monday, "after" once it is
        over (until the new year, when the next one is "before")."""
        if self.today > self.monday:
            return "after"
        if self.today >= self.friday:
            return "on"
        if self.today >= self.friday - timedelta(days=SEASON_FROM):
            return "soon"
        return "before"

    @property
    def showing(self):
        """Whether the home page's banner and the prominent links show."""
        return self.state in ("soon", "on")

    @property
    def next_friday(self):
        return black_friday(self.year + 1)

    @property
    def next_monday(self):
        return cyber_monday(self.year + 1)


def local_today():
    """Today in UK time: the one place the pages read the date, so a test can choose it."""
    return timezone.localdate()


def season(today=None):
    today = today or local_today()
    return Season(today.year, black_friday(today.year), cyber_monday(today.year), today)


def day(when):
    """A date as the pages give it: "Friday 27 November 2026"."""
    return date_format(when, "l j F Y")


def short_day(when):
    """A date after the name of its day: "30 November"."""
    return date_format(when, "j F")


def deal_rows(game=None, kind="", sort="percent", today=None):
    """The genuine price drops now (see the module's notes), each with ``month_average``, ``month_days``,
    ``week_price`` and ``month_price`` (the cheapest price recorded about a week and a month ago), ``low`` and
    ``low_date`` (the lowest recorded before today, and the latest day at it), ``saving`` and ``saving_ratio``
    (today's price under the 30-day average). Narrowed to ``game`` and ``kind``; ``sort`` as SORTS. One query."""
    today = today or timezone.localdate()
    money = DecimalField(max_digits=12, decimal_places=4)

    def window(days):
        return DailyLowestPrice.objects.order_by().filter(
            product=OuterRef("pk"), date__lt=today, date__gte=today - timedelta(days=days)).values("product")

    def summary(rows, aggregate, field=money):
        return Subquery(rows.annotate(value=aggregate).values("value"), output_field=field)

    def back(days):
        """The cheapest price recorded on the latest day at least ``days`` ago, within ``days`` before that."""
        target = today - timedelta(days=days)
        return Subquery(DailyLowestPrice.objects.filter(product=OuterRef("pk"), date__lte=target,
                                                        date__gt=target - timedelta(days=days))
                        .order_by("-date").values("price")[:1], output_field=money)

    month, year = window(MONTH), window(fair_price.WINDOW_DAYS)
    low_day = (DailyLowestPrice.objects.filter(product=OuterRef("pk"), date__lt=today,
                                               date__gte=today - timedelta(days=fair_price.WINDOW_DAYS))
               .order_by("price", "-date").values("date")[:1])
    products = (Product.objects.for_lists().in_stock()
                .filter(price_basis__in=ON_RECORD, price_band__in=GOOD_BANDS))
    if game is not None:
        products = products.filter(game=game)
    if kind:
        products = products.filter(product_type=kind)
    products = (
        products.annotate(
            month_average=summary(month, Avg("price")),
            month_days=summary(month, Count("pk"), IntegerField()),
            low=summary(year, Min("price")),
            low_date=Subquery(low_day),
            week_price=back(WEEK),
            month_price=back(MONTH),
            doubtful_low=doubtful_low(),
        )
        # Today's price is the cheapest in stock at a confirmed delivered price, with a month of prices to set it
        # against, and not a doubtful one: it may not be this product.
        .filter(lowest_known__isnull=False, lowest_known=F("lowest_price"), in_stock_count__gt=0,
                month_days__gte=MONTH_LEAST)
        .filter(Q(doubtful_low__isnull=True) | Q(doubtful_low__gt=F("lowest_known")))
        .annotate(
            saving=ExpressionWrapper(F("month_average") - F("lowest_known"), output_field=money),
            saving_ratio=ExpressionWrapper((F("month_average") - F("lowest_known")) * 1.0 / F("month_average"),
                                           output_field=FloatField()),
        )
        # Clearly under the month's average, or at the lowest recorded with the Price check's "Lowest yet" (which
        # also needs it GOOD under the whole year's average): a price that never moved ties its own low every day.
        .filter(Q(saving_ratio__gte=float(DROP), saving__gte=LEAST_SAVING)
                | Q(lowest_known__lte=F("low"), price_band=fair_price.LOWEST))
        .filter(saving_ratio__lte=offers.MAX_REAL_PERCENT / 100)
    )
    if sort == "lowest":
        products = products.filter(lowest_known__lte=F("low"))
    order = ("-saving", "-saving_ratio") if sort == "pounds" else ("-saving_ratio", "-saving")
    return products.order_by(*order, "name")


def pounds(value):
    return Decimal(value).quantize(PENCE, rounding=ROUND_HALF_UP) if value is not None else None


def percent(part, whole):
    if not whole:
        return 0
    return int((Decimal(part) / Decimal(whole) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class Change:
    """Today's price against one recorded ``days`` ago: ``amount`` up or down, ``direction`` "down", "up" or
    "same"."""
    days: int
    amount: Decimal
    percent: int
    direction: str


def change(today_price, then, days):
    if then is None:
        return None
    then = pounds(then)
    gap = today_price - then
    direction = "down" if gap < 0 else "up" if gap > 0 else "same"
    return Change(days, abs(gap), percent(abs(gap), then), direction)


def finished(product, lead_shops=None):
    """A deal ready to show: its figures to the penny and in whole percent, its changes, its lowest recorded, and
    the between-shops saving (offers.summarise, from ``product.offers``)."""
    today_price = product.lowest_known
    product.average = pounds(product.month_average)
    product.saving_money = max(pounds(product.saving), Decimal("0.00"))
    product.saving_percent = percent(product.saving_money, product.average)
    product.week_change = change(today_price, product.week_price, WEEK)
    product.month_change = change(today_price, product.month_price, MONTH)
    product.changes = [moved for moved in (product.week_change, product.month_change) if moved is not None]
    product.at_low = product.low is None or today_price <= pounds(product.low)
    product.low_price = today_price if product.at_low else pounds(product.low)
    product.summary = offers.summarise(product)
    product.best = product.summary.best
    return product


def kinds_in(rows, game_slug):
    """[(type, its name)] of the kinds the deals hold, in the game's own order (the default order across games)."""
    found = {row.product_type for row in rows}
    ordered = [code for code, _label in GAME_TYPES.get(game_slug, DEFAULT_TYPES) if code in found]
    ordered += sorted(found - set(ordered))
    return [(code, type_label(game_slug, code)) for code in ordered]


def by_game(rows):
    """{game: deals} of the games with PUBLISH_LEAST deals or more among ``rows``, most first. No query: each row
    carries its game."""
    counts = {}
    for row in rows:
        counts[row.game] = counts.get(row.game, 0) + 1
    return dict(sorted(((game, n) for game, n in counts.items() if n >= PUBLISH_LEAST),
                       key=lambda item: (-item[1], item[0].name)))


def published_games(today=None):
    """{game slug: genuine deals} for every active game whose page is published. One query."""
    counts = {}
    for slug in deal_rows(today=today).values_list("game__slug", flat=True):
        counts[slug] = counts.get(slug, 0) + 1
    return {slug: n for slug, n in counts.items() if n >= PUBLISH_LEAST}


def top_shops(rows):
    """[(shop name, deals)] of the UK shops (never eBay or Amazon) with the cheapest price on the most ``rows``, the
    first TOP_SHOPS. Two queries."""
    from .shop_report import lead_rows

    if not rows:
        return []
    markets = set(Retailer.objects.filter(source_type__in=MARKETPLACES).values_list("pk", flat=True))
    counts = {}
    for shop, name, _price, _known in lead_rows([row.pk for row in rows]).values():
        if shop not in markets:
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_SHOPS]


@dataclass
class BoxFigures:
    total: int          # booster boxes in stock with a month of prices
    drops: int          # of them, at least DROP under their 30-day average
    percent: int        # how far today's prices are from their 30-day averages, on average (negative: cheaper)


def box_figures(game, today=None):
    """How the game's booster boxes compare with their own last month now, for the question about them. One query."""
    today = today or timezone.localdate()
    month = DailyLowestPrice.objects.order_by().filter(
        product=OuterRef("pk"), date__lt=today, date__gte=today - timedelta(days=MONTH)).values("product")
    money = DecimalField(max_digits=12, decimal_places=4)
    rows = list(
        Product.objects.for_lists().in_stock().filter(game=game, product_type=BOX, price_basis__in=ON_RECORD)
        .annotate(month_average=Subquery(month.annotate(value=Avg("price")).values("value"), output_field=money),
                  month_days=Subquery(month.annotate(value=Count("pk")).values("value"), output_field=IntegerField()))
        .filter(lowest_known__isnull=False, month_days__gte=MONTH_LEAST)
        .values_list("lowest_known", "month_average")
    )
    if len(rows) < PUBLISH_LEAST:
        return None
    ratios = [(Decimal(now) - Decimal(average)) / Decimal(average) for now, average in rows if average]
    drops = sum(1 for ratio in ratios if ratio <= -DROP)
    mean = sum(ratios) / len(ratios) if ratios else Decimal("0")
    return BoxFigures(len(rows), drops, int((mean * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def faq(words, when, game, lead, shops, boxes):
    """[(question, answer)]: the questions the brief names, answered with the page's own figures. ``lead`` is the
    game's biggest drop now (finished), or None. The hub's name Pokémon where a game is needed; a game's page names
    its own game."""
    from .answers import stamp

    name = game.display_short
    found = []
    if lead is not None and lead.best is not None:
        answer = words("blackfriday.faq.cheapest.a", game=name, product=lead.name, price=hubs.money(lead.lowest_known),
                       shop=lead.best.retailer.name, percent=lead.saving_percent, checked=stamp(lead.best.last_checked))
    else:
        answer = words("blackfriday.faq.cheapest.a_none", game=name)
    found.append((words("blackfriday.faq.cheapest.q", game=name, year=when.year), answer))
    boxes_word = plural_words(game.slug, [BOX])
    boxes_word = boxes_word[0] if boxes_word else "booster boxes"
    if boxes is None:
        answer = words("blackfriday.faq.boxes.a_none", game=name, boxes=boxes_word)
    else:
        key = ("blackfriday.faq.boxes.a_less" if boxes.percent < 0 else "blackfriday.faq.boxes.a_more"
               if boxes.percent > 0 else "blackfriday.faq.boxes.a_same")
        answer = words(key, game=name, boxes=boxes_word, drops=f"{boxes.drops:,}", total=f"{boxes.total:,}",
                       percent=abs(boxes.percent), drop=int(DROP * 100))
    found.append((words("blackfriday.faq.boxes.q", game=name, boxes=boxes_word), answer))
    if shops:
        listed = words.join([words("blackfriday.faq.shops.one", shop=shop, count=f"{count:,}") for shop, count in shops])
        answer = words("blackfriday.faq.shops.a", shops=listed)
    else:
        answer = words("blackfriday.faq.shops.a_none")
    found.append((words("blackfriday.faq.shops.q", year=when.year), answer))
    found.append((words("blackfriday.faq.how.q"),
                  words("blackfriday.faq.how.a", drop=int(DROP * 100), days=MONTH_LEAST, month=MONTH,
                        shops=fair_price.MIN_SHOPS)))
    if when.state == "after":
        answer = words("blackfriday.faq.cyber.a_after", year=when.year, monday=day(when.monday),
                       next_year=when.year + 1, next_monday=day(when.next_monday))
    else:
        answer = words("blackfriday.faq.cyber.a", year=when.year, monday=day(when.monday), friday=day(when.friday))
    found.append((words("blackfriday.faq.cyber.q", year=when.year), answer))
    return [(question, hubs.tidy_stops(answer)) for question, answer in found]


def spelled(names):
    """Plural type names for a sentence: "ETBs" written out, as a game's own answers do (answers.SPELLED)."""
    return ["Elite Trainer Boxes" if name == "ETBs" else name for name in names]


def season_line(words, when):
    if when.state == "after":
        return words("blackfriday.season.after", year=when.year, next_year=when.year + 1,
                     next_friday=day(when.next_friday))
    if when.state == "on":
        return words("blackfriday.season.on", year=when.year, monday=short_day(when.monday))
    return words("blackfriday.season.before", year=when.year, friday=day(when.friday), monday=day(when.monday))


def ready(products):
    """``products`` with their shops' offers attached and finished. One query, none when all are already."""
    from django.db.models import prefetch_related_objects

    waiting = [product for product in products if not hasattr(product, "summary")]
    if waiting:
        prefetch_related_objects(waiting, offers.buyable_prefetch())
        for product in waiting:
            finished(product)
    return products


def page(request, game=None):
    """The hub (``game`` None) or a game's page."""
    from .answers import faq_json
    from .views import breadcrumbs_json, ld_json, text

    words = hubs.Words(text, request)
    when = season()
    kind = request.GET.get("type", "")
    sort = request.GET.get("sort", "")
    sort = sort if sort in SORTS else "percent"
    every = list(deal_rows(game=game))              # the page's deals before its own filters, biggest % first
    kinds = kinds_in(every, game.slug if game else "")
    kind = kind if kind in dict(kinds) else ""
    chosen = every if not kind and sort == "percent" else list(deal_rows(game=game, kind=kind, sort=sort))
    limit = SHOWN["game" if game else None]
    rows = ready(chosen[:limit])
    checked = max((row.best.last_checked for row in rows if row.best is not None), default=None)
    # The questions: about the page's game, or on the hub about Pokémon, with its biggest drop now.
    asked = game or next((row.game for row in every if row.game.slug == QUESTION_GAME), None) \
        or Game.objects.filter(slug=QUESTION_GAME, is_active=True).first()
    questions = []
    if asked is not None:
        lead = next((row for row in every if row.game_id == asked.pk), None)
        if lead is not None:
            ready([lead])
        questions = faq(words, when, asked, lead, top_shops(every), box_figures(asked))
    least = min((row.lowest_known for row in every), default=None)
    if game is None:
        path = reverse("web:black_friday")
        heading = words("blackfriday.title", year=when.year)
        short = words("meta.blackfriday.title_short", year=when.year)
        title = hubs.fitted([words("meta.blackfriday.title", year=when.year), short]) or short
        types = words.join(spelled(plural_words("", [code for code, _label in kinds][:3])))
        description = (words("meta.blackfriday.description", count=f"{len(every):,}", year=when.year, types=types,
                             price=hubs.money(least))
                       if every else words("meta.blackfriday.description_none", year=when.year))
        crumbs = [(words("blackfriday.crumb_deals"), reverse("web:deals")), (heading, path)]
        indexable = True
    else:
        path = reverse("web:black_friday_game", args=[game.slug])
        name = game.display_short
        heading = words("blackfriday.title_game", year=when.year, game=name)
        short = words("meta.blackfriday.title_game_short", year=when.year, game=name)
        title = hubs.fitted([words("meta.blackfriday.title_game", year=when.year, game=name), short]) or short
        types = words.join(spelled(plural_words(game.slug, [code for code, _label in kinds][:3])))
        description = (words("meta.blackfriday.description_game", count=f"{len(every):,}", year=when.year,
                             game=name, types=types, price=hubs.money(least))
                       if every else words("meta.blackfriday.description_game_none", year=when.year, game=name))
        crumbs = [(words("blackfriday.crumb_deals"), reverse("web:deals")),
                  (words("blackfriday.title", year=when.year), reverse("web:black_friday")), (heading, path)]
        indexable = len(every) >= PUBLISH_LEAST
    if game is None:
        intro = words("blackfriday.intro", drop=int(DROP * 100), month=MONTH)
    else:
        named = spelled(plural_words(game.slug, [code for code, _label in kinds][:3]))
        intro = words("blackfriday.intro_game", game=game.display_short, drop=int(DROP * 100), month=MONTH,
                      types=words.join(named) if named else words("blackfriday.sealed"))
    url = request.build_absolute_uri(path)
    structured = [breadcrumbs_json(request, crumbs, page_url=url)]
    if questions:
        structured.append(faq_json(url, questions))
    return render(request, "web/black_friday.html", {
        "game": game, "when": when, "rows": rows, "count": len(chosen), "every": len(every), "shown_limit": limit,
        "kinds": kinds, "kind": kind, "sort": sort, "sorts": SORTS, "checked": checked,
        "games": by_game(every) if game is None else {},
        "questions": questions, "season_line": season_line(words, when), "heading": heading, "intro": intro,
        "drop": int(DROP * 100), "month": MONTH, "path": path, "crumbs": crumbs,
        "meta_title": title, "meta_description": hubs.fit_description(description),
        "canonical_url": url, "robots": "" if indexable else "noindex, follow",
        "structured_json": ld_json(structured),
    })


@require_GET
@cached_page
def hub(request):
    return page(request)


@require_GET
@cached_page
def game_page(request, game_slug):
    game = Game.objects.filter(slug=game_slug, is_active=True).first()
    if game is None:
        raise Http404("No game at that address.")
    return page(request, game)


def banner(game=None, today=None):
    """What the seasonal banner links to while it shows (Season.showing), or None: the hub, or ``game``'s page. No
    query."""
    when = season(today)
    if not when.showing:
        return None
    url = reverse("web:black_friday_game", args=[game.slug]) if game is not None else reverse("web:black_friday")
    return {"when": when, "url": url, "game": game, "monday": short_day(when.monday)}


def sitemap_paths(today=None):
    """The addresses for the sitemap: the hub, and each published game's page. One query."""
    return [reverse("web:black_friday")] + [reverse("web:black_friday_game", args=[slug])
                                             for slug in sorted(published_games(today))]
