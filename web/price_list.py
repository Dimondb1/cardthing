"""
UK prices: a public page for each game listing what each product usually costs delivered from UK shops, today's
cheapest delivered price, the Price check's verdict and the RRP, so the usual UK price can be quoted and linked to.
The owner, 20 Nov 2026, on making RipRaptor the UK price benchmark, choosing to make visible what the site already
knows rather than build anything new: "We kind of have all this stuff already".

The usual UK price is the Price check's own average (catalogue/fair_price.py): the average of the cheapest delivered
price at a UK shop each day over the last WINDOW_DAYS before today, with at least MIN_SHOPS UK shops listing the
product and MIN_DAYS days of prices. The page lists exactly the products whose Price check rests on that record
(Product.price_basis "record" or "both", kept each hour by refresh_bands), so its figure is the one the product page's
Price check names. Cardmarket and the RRP still inform the verdict, but neither is ever shown as the usual price.

/prices/ lists the games with a figure; /prices/<game>/ is the table, newest set first, filtered by kind of product
with ?type=, PAGE_SIZE rows a page. A query for the rows and one for the count, however many products. Kept like any
list page (web.pagecache).
"""
from datetime import timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Avg, Count, DecimalField, F, IntegerField, Max, OuterRef, Q, Subquery
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import fair_price
from catalogue.pricing import MARKETPLACES
from catalogue.models import DailyLowestPrice, Game, Listing, Product, fresh
from catalogue.types import DEFAULT as DEFAULT_TYPES, GAME_TYPES, type_label
from content import service as copy

from .pagecache import cached_page

PAGE_SIZE = 100
ON_RECORD = (fair_price.RECORD, fair_price.BOTH)
PENCE = Decimal("0.01")


def on_record():
    """Active products whose Price check rests on our own record of UK shops' prices: the ones with a usual UK
    price."""
    return Product.objects.active().filter(price_basis__in=ON_RECORD)


def with_usual(products, today=None):
    """``products`` with ``usual`` (the Price check's average, fair_price.verdict's) and ``usual_days``, keeping only
    those with MIN_DAYS of prices, and ``checked``: when a shop's price for it was last checked."""
    today = today or timezone.localdate()
    window = DailyLowestPrice.objects.order_by().filter(
        product=OuterRef("pk"), date__lt=today, date__gte=today - timedelta(days=fair_price.WINDOW_DAYS),
    ).values("product")
    buyable = (Q(listings__is_active=True, listings__retailer__is_active=True) & fresh("listings__")
               & Q(listings__availability__in=Listing.BUYABLE, listings__sanity__in=Listing.COUNTED))
    # The shop with today's cheapest delivered price: the Price check never says "High price" beside one that pays
    # a commission (Product.card_check, the owner's rule on product pages and cards).
    cheapest = (Listing.objects.buyable().filter(product=OuterRef("pk"), delivery_known=True)
                .order_by("delivered_price", "price"))
    return products.annotate(
        usual=Subquery(window.annotate(value=Avg("price")).values("value"),
                       output_field=DecimalField(max_digits=12, decimal_places=4)),
        usual_days=Subquery(window.annotate(value=Count("pk")).values("value"), output_field=IntegerField()),
        checked=Max("listings__last_checked", filter=buyable),
        cheapest_link=Subquery(cheapest.values("retailer__affiliate_url_template")[:1]),
        cheapest_source=Subquery(cheapest.values("retailer__source_type")[:1]),
    ).filter(usual_days__gte=fair_price.MIN_DAYS)


def rows_for(game, kind="", today=None):
    """The game's products with a usual UK price, newest set first, then by kind and name. One query."""
    products = on_record().filter(game=game).select_related("game", "product_set").with_prices().with_release()
    if kind:
        products = products.filter(product_type=kind)
    return with_usual(products, today).order_by(F("release").desc(nulls_last=True), "product_set__name",
                                                "product_type", "name")


def kinds_for(game):
    """[(type, the game's name for it)] of the kinds of product the game's list has, in the game's own order. One
    query."""
    found = set(on_record().filter(game=game).order_by().values_list("product_type", flat=True).distinct())
    ordered = [code for code, _label in GAME_TYPES.get(game.slug, DEFAULT_TYPES) if code in found]
    ordered += sorted(found - set(ordered))
    return [(code, type_label(game.slug, code)) for code in ordered]


def finished(row):
    """The row ready to show: its usual price to the penny, as the product page's Price check gives it, today's
    cheapest delivered price, the RRP, and the Price check, left off where it would call a commission-paying shop's
    price high."""
    row.usual = Decimal(row.usual).quantize(PENCE) if row.usual is not None else None
    row.today = row.lowest_known
    row.rrp_figure = row.rrp_now[0] if row.rrp_now else None
    check = row.card_check
    pays = bool((row.cheapest_link or "").strip()) or row.cheapest_source in MARKETPLACES
    row.check = None if check is not None and check.key == "high" and pays else check
    return row


@require_GET
@cached_page
def prices_index(request):
    """The games with a UK price list, each with how many products it has. One query."""
    store = copy.for_request(request)
    games = list(Game.objects.filter(is_active=True, products__is_active=True,
                                     products__price_basis__in=ON_RECORD)
                 .annotate(listed=Count("products", distinct=True)).order_by("-listed", "name"))
    heading = copy.get("browse.prices.index_title", store=store)
    return render(request, "web/prices_index.html", {
        "games": games, "least_days": fair_price.MIN_DAYS, "heading": heading, "meta_title": heading,
        "meta_description": copy.get("browse.prices.index_intro", store=store),
        "canonical_url": request.build_absolute_uri(reverse("web:prices")),
    })


@require_GET
@cached_page
def prices_for_game(request, game_slug):
    """A game's UK price list."""
    game = Game.objects.filter(slug=game_slug, is_active=True).first()
    if game is None:
        raise Http404("No game at that address.")
    store = copy.for_request(request)
    kinds = kinds_for(game)
    kind = request.GET.get("type", "")
    kind = kind if kind in dict(kinds) else ""
    page = Paginator(rows_for(game, kind), PAGE_SIZE).get_page(request.GET.get("page"))
    rows = [finished(row) for row in page.object_list]
    checked = max((row.checked for row in rows if row.checked), default=None)
    heading = copy.get("browse.prices.title", store=store, game=game.name)
    canonical = reverse("web:prices_game", args=[game.slug])
    from .views import breadcrumbs_json, ld_json

    return render(request, "web/prices.html", {
        "game": game, "rows": rows, "page": page, "kinds": kinds, "kind": kind, "checked": checked,
        "days": fair_price.WINDOW_DAYS, "least_days": fair_price.MIN_DAYS, "least_shops": fair_price.MIN_SHOPS,
        "heading": heading, "meta_title": heading,
        "meta_description": copy.get("browse.prices.description", store=store, game=game.name),
        "canonical_url": request.build_absolute_uri(canonical + (f"?type={kind}" if kind else "")),
        "structured_json": ld_json(breadcrumbs_json(request, [
            (copy.get("browse.prices.index_title", store=store), reverse("web:prices")), (heading, canonical)])),
    })
