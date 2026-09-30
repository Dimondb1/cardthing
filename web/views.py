import json

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Case, Count, F, IntegerField, Max, Prefetch, Q, Value, When
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import offers, pricing
from catalogue.models import Game, Listing, OutboundClick, Product, ProductSet, Retailer
from catalogue.search import apply_search
from content import service as copy

from .charts import price_chart

BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "preview", "monitor")


def text(request, key, **values):
    """Site wording for use in a view, sharing the request's single lookup."""
    return copy.get(key, store=copy.for_request(request), **values)


class FilterForm(forms.Form):
    """Search and filters. Labels here are short functional labels."""

    # "Best match" only means something when there is a search query.
    SEARCH_SORTS = [
        ("best", "Best match"),
        ("price", "Lowest price"),
        ("newest", "Newest first"),
    ]
    BROWSE_SORTS = [
        ("newest", "Newest first"),
        ("price", "Lowest price"),
    ]

    q = forms.CharField(required=False, max_length=100)
    game = forms.ModelChoiceField(
        label="Game",
        queryset=Game.objects.filter(is_active=True),
        to_field_name="slug",
        required=False,
        empty_label="All games",
    )
    type = forms.ChoiceField(
        label="Product type",
        choices=[("", "All types")] + Product.Type.choices,
        required=False,
    )
    sort = forms.ChoiceField(label="Sort by", choices=SEARCH_SORTS, required=False)
    in_stock = forms.BooleanField(label="In stock only", required=False)

    def __init__(self, *args, fixed_game=None, has_query=False, **kwargs):
        super().__init__(*args, **kwargs)
        if fixed_game is not None:
            del self.fields["game"]
        if not has_query:
            self.fields["sort"].choices = self.BROWSE_SORTS
        self.default_sort = "best" if has_query else "newest"

    def value(self, name, default=None):
        # cleaned_data holds every valid field even when another field is not.
        if not self.is_bound or not hasattr(self, "cleaned_data"):
            return default
        return self.cleaned_data.get(name) or default


def _browse(request, template_context, *, base_queryset, fixed_game=None):
    """Shared by search, game and set pages."""
    has_query = bool(request.GET.get("q", "").strip())
    form = FilterForm(request.GET or None, fixed_game=fixed_game, has_query=has_query)
    form.is_valid()

    query = (form.value("q") or "").strip()
    products = base_queryset
    terms = []
    if query:
        products, terms = apply_search(products, query)

    game = form.value("game")
    if game is not None:
        products = products.filter(game=game)
    product_type = form.value("type")
    if product_type:
        products = products.filter(product_type=product_type)
    if form.value("in_stock"):
        products = products.filter(in_stock_count__gt=0)

    sort = form.value("sort", form.default_sort)
    if sort == "best" and not terms:
        sort = "newest"
    has_price = Case(
        When(lowest_price__isnull=True, then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )
    if sort == "price":
        ordering = [F("lowest_price").asc(nulls_last=True), "name"]
    elif sort == "newest":
        ordering = [has_price, F("release").desc(nulls_last=True), "name"]
    else:
        ordering = [has_price, F("release").desc(nulls_last=True), "name"]
        if terms:
            ordering.insert(0, "name_rank")
    products = products.annotate(has_price=has_price).order_by(*ordering)

    paginator = Paginator(products.prefetch_related(offers.buyable_prefetch()), settings.RIPRAPTOR_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    week_lows = offers.week_low_map([p.pk for p in page.object_list])
    cards = [(product, offers.summarise(product, week_lows)) for product in page.object_list]

    filters_active = bool(
        game or product_type or form.value("in_stock") or sort != form.default_sort
    )
    context = {
        "form": form,
        "query": query,
        "page": page,
        "cards": cards,
        "total": paginator.count,
        "filters_active": filters_active,
        "no_matches": bool(query) and not filters_active and paginator.count == 0,
        "games": Game.objects.filter(is_active=True),
    }
    context.update(template_context)
    return render(request, "web/browse.html", context)


def home_lists():
    """Biggest savings, trending and the newest sets, cached because they read every priced product."""
    from django.core.cache import cache

    from catalogue.signals import HOME_CACHE_KEY as key
    cached = cache.get(key)
    if cached is not None:
        return cached
    priced = Product.objects.for_lists().filter(in_stock_count__gte=1).prefetch_related(offers.buyable_prefetch())
    savings = offers.biggest_savings(priced, limit=9)
    popular = pricing.popular(limit=8) or list(priced.order_by(F("release").desc(nulls_last=True))[:8])
    ids = [product.pk for product in popular]
    with_offers = {
        product.pk: product
        for product in Product.objects.for_lists().filter(pk__in=ids).prefetch_related(offers.buyable_prefetch())
    }
    previous = pricing.previous_price_map(ids)
    trending = []
    for pk in ids:
        product = with_offers.get(pk)
        if product is None:
            continue
        product.best_offer = product.offers[0] if product.offers else None
        before = previous.get(pk)
        product.movement = None
        if before is not None and product.lowest_price is not None and before != product.lowest_price:
            product.movement = product.lowest_price - before
        trending.append(product)
    recent = list(
        ProductSet.objects.filter(products__is_active=True)
        .select_related("game")
        .annotate(
            product_count=Count("products", filter=Q(products__is_active=True), distinct=True),
            newest=Max("products__created_at"),
        )
        .order_by(F("release_date").desc(nulls_last=True), "-newest")[:8]
    )
    retailer_count = Listing.objects.live().values("retailer_id").distinct().count()
    cache.set(key, (savings, trending, recent, retailer_count), settings.RIPRAPTOR_HOME_CACHE_SECONDS)
    return savings, trending, recent, retailer_count


@require_GET
def home(request):
    games = list(
        Game.objects.filter(is_active=True)
        .annotate(product_count=Count("products", filter=Q(products__is_active=True)))
        .filter(product_count__gt=0)
        .order_by("-product_count", "name")
        .prefetch_related(
            Prefetch(
                "sets",
                queryset=ProductSet.objects.filter(products__is_active=True).distinct(),
                to_attr="listed_sets",
            )
        )
    )
    last_checked = Listing.objects.live().aggregate(latest=Max("last_checked"))["latest"]
    savings, trending, recent, retailer_count = home_lists()
    drops = list(pricing.price_drops(limit=6))
    focal = None
    if drops:
        focal = {"kind": "drop", "product": drops[0]}
        drops = drops[1:]
    elif savings:
        focal = {"kind": "saving", "product": savings[0][0], "summary": savings[0][1]}
        savings = savings[1:]
    return render(
        request,
        "web/home.html",
        {
            "games": games,
            "last_checked": last_checked,
            "retailer_count": retailer_count,
            "focal": focal,
            "savings": savings[:8],
            "trending": trending,
            "recent": recent,
            "drops": drops,
            "hide_header_search": True,
            "meta_full_title": text(
                request, "meta.home.title", site_name=settings.RIPRAPTOR_SITE_NAME
            ),
            "canonical_url": request.build_absolute_uri("/"),
        },
    )


@require_GET
def search(request):
    query = request.GET.get("q", "").strip()
    if query:
        heading = text(request, "browse.results.title", query=query)
    else:
        heading = text(request, "browse.all.title")
    return _browse(
        request,
        {"heading": heading, "meta_title": heading, "noindex": True, "is_search": True},
        base_queryset=Product.objects.for_lists(),
    )


@require_GET
def games(request):
    game_list = (
        Game.objects.filter(is_active=True)
        .annotate(product_count=Count("products", filter=Q(products__is_active=True)))
        .prefetch_related(
            Prefetch(
                "sets",
                queryset=ProductSet.objects.annotate(
                    product_count=Count("products", filter=Q(products__is_active=True))
                ).filter(product_count__gt=0),
            )
        )
    )
    heading = text(request, "browse.games.title")
    return render(
        request,
        "web/games.html",
        {"games": game_list, "heading": heading, "meta_title": heading},
    )


@require_GET
def game_detail(request, game_slug):
    game = get_object_or_404(Game, slug=game_slug, is_active=True)
    sets = ProductSet.objects.filter(game=game, products__is_active=True).distinct()
    return _browse(
        request,
        {
            "heading": game.name,
            "game": game,
            "sets": sets,
            "meta_title": text(request, "meta.game.title", game=game.name),
            "meta_description": text(request, "meta.game.description", game=game.name),
            "canonical_url": request.build_absolute_uri(game.get_absolute_url()),
        },
        base_queryset=Product.objects.for_lists().filter(game=game),
        fixed_game=game,
    )


@require_GET
def set_detail(request, game_slug, set_slug):
    product_set = get_object_or_404(
        ProductSet.objects.select_related("game"),
        game__slug=game_slug,
        game__is_active=True,
        slug=set_slug,
    )
    game = product_set.game
    values = {"set": product_set.name, "game": game.name}
    return _browse(
        request,
        {
            "heading": product_set.name,
            "game": game,
            "product_set": product_set,
            "sets": ProductSet.objects.filter(game=game, products__is_active=True).distinct(),
            "meta_title": text(request, "meta.set.title", **values),
            "meta_description": text(request, "meta.set.description", **values),
            "canonical_url": request.build_absolute_uri(product_set.get_absolute_url()),
        },
        base_queryset=Product.objects.for_lists().filter(product_set=product_set),
        fixed_game=game,
    )


@require_GET
def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.active().select_related("game", "product_set"), slug=slug
    )
    listings = list(
        Listing.objects.filter(product=product)
        .live()
        .select_related("retailer")
        .order_by("delivered_price", "retailer__name")
    )
    in_stock_first = {Listing.Availability.IN_STOCK: 0, Listing.Availability.PREORDER: 1}
    current = sorted(
        (listing for listing in listings if listing.is_buyable),
        key=lambda listing: (
            listing.delivered_price,
            in_stock_first.get(listing.availability, 2),
            listing.retailer.name,
        ),
    )
    unavailable = [listing for listing in listings if not listing.is_buyable]
    cheapest = current[0] if current else None
    product.offers = current
    summary = offers.summarise(product, offers.week_low_map([product.pk]))

    in_stock_count = sum(1 for l in current if l.availability == Listing.Availability.IN_STOCK)
    preorder_count = sum(1 for l in current if l.availability == Listing.Availability.PREORDER)
    last_checked = max((l.last_checked for l in listings), default=None)

    today = timezone.localdate()
    days = settings.RIPRAPTOR_HISTORY_DAYS
    chart = price_chart(pricing.history(product, days=days, today=today), days, today)
    month_ago = pricing.previous_price_map([product.pk], days=30, today=today).get(product.pk)
    last_known = None if cheapest else pricing.last_known_price(product)

    related = []
    if product.product_set_id:
        related = list(
            Product.objects.for_lists()
            .filter(product_set_id=product.product_set_id)
            .exclude(pk=product.pk)
            .order_by("name")[:6]
        )

    structured = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product.name,
        "url": request.build_absolute_uri(product.get_absolute_url()),
        "category": product.get_product_type_display(),
        "brand": {"@type": "Brand", "name": product.game.name},
    }
    if product.image_src:
        structured["image"] = request.build_absolute_uri(product.image_src)
    if product.ean:
        structured["gtin13"] = product.ean
    if current:
        structured["offers"] = {
            "@type": "AggregateOffer",
            "priceCurrency": "GBP",
            "lowPrice": str(current[0].delivered_price),
            "highPrice": str(current[-1].delivered_price),
            "offerCount": len(current),
            "availability": "https://schema.org/InStock" if in_stock_count else "https://schema.org/PreOrder",
        }
    return render(
        request,
        "web/product.html",
        {
            "product": product,
            "structured_json": json.dumps(structured),
            "current": current,
            "unavailable": unavailable,
            "cheapest": cheapest,
            "summary": summary,
            "in_stock_count": in_stock_count,
            "preorder_count": preorder_count,
            "last_checked": last_checked,
            "has_listings": bool(listings),
            "chart": chart,
            "history_days": days,
            "month_ago": month_ago,
            "last_known": last_known,
            "related": related,
            "meta_title": text(request, "meta.product.title", product=product.name),
            "meta_description": text(request, "meta.product.description", product=product.name),
            "canonical_url": request.build_absolute_uri(product.get_absolute_url()),
        },
    )


@require_GET
def go(request, listing_id):
    """Send the visitor to the retailer and count the click."""
    listing = get_object_or_404(
        Listing.objects.live().select_related("retailer", "product"),
        pk=listing_id,
        product__is_active=True,
    )
    agent = request.headers.get("User-Agent", "").lower()
    if agent and not any(marker in agent for marker in BOT_MARKERS):
        OutboundClick.objects.create(
            listing=listing, product=listing.product, retailer=listing.retailer
        )
    response = HttpResponseRedirect(listing.retailer.outbound_url(listing.url))
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_GET
def terms(request):
    return render(
        request,
        "web/terms.html",
        {"meta_title": text(request, "terms.title"), "canonical_url": request.build_absolute_uri(request.path)},
    )


@require_GET
def product_prices_api(request, slug):
    """Current prices for the product page's refresh control."""
    from .templatetags.ripraptor import ago, gbp

    product = get_object_or_404(Product.objects.active(), slug=slug)
    rows = []
    for listing in Listing.objects.filter(product=product).live().select_related("retailer"):
        rows.append({
            "id": listing.pk,
            "total": gbp(listing.delivered_price),
            "price": gbp(listing.price),
            "checked": ago(listing.last_checked),
            "buyable": listing.is_buyable,
        })
    response = JsonResponse({"listings": rows})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def about(request):
    return render(
        request,
        "web/about.html",
        {
            "retailers": Retailer.objects.filter(is_active=True),
            "contact_email": settings.RIPRAPTOR_CONTACT_EMAIL,
            "stale_hours": settings.RIPRAPTOR_STALE_AFTER_HOURS,
            "meta_title": text(request, "about.title"),
            "canonical_url": request.build_absolute_uri(request.path),
        },
    )


QUICK_RESULTS = 6


@require_GET
def search_api(request):
    """Results for the search box as you type. Plain data, no HTML."""
    from .templatetags.ripraptor import gbp

    query = request.GET.get("q", "").strip()[:100]
    results = []
    if query:
        products, terms = apply_search(Product.objects.for_lists(), query)
        if terms:
            products = products.order_by("name_rank", F("lowest_price").asc(nulls_last=True), "name")
        store = copy.for_request(request)
        for product in products[:QUICK_RESULTS]:
            if product.in_stock_count:
                stock = copy.get("browse.availability.in_stock." + ("one" if product.in_stock_count == 1 else "other"), store, count=product.in_stock_count)
                state = "in"
            elif product.preorder_count:
                stock = copy.get("browse.availability.preorder." + ("one" if product.preorder_count == 1 else "other"), store, count=product.preorder_count)
                state = "preorder"
            elif product.listing_count:
                stock, state = copy.get("browse.availability.none", store), "out"
            else:
                stock, state = copy.get("browse.no_prices", store), "none"
            results.append({
                "name": product.name,
                "url": product.get_absolute_url(),
                "meta": f"{product.game.display_short} · {product.get_product_type_display()}",
                "price": gbp(product.lowest_price) if product.lowest_price is not None else "",
                "stock": stock,
                "state": state,
                "type": product.product_type,
            })
    response = JsonResponse({"query": query, "results": results})
    response["Cache-Control"] = "no-store"
    return response


DECK_PAGE = 12


def card_data(request, product, summary):
    """One product as the swipe deck and search cards need it."""
    from .templatetags.ripraptor import ago, gbp

    best = summary.best
    data = {
        "id": product.pk,
        "name": product.name,
        "url": product.get_absolute_url(),
        "meta": f"{product.game.display_short} · {product.get_product_type_display()}",
        "type": product.product_type,
        "image": product.image_src,
        "price": gbp(best.delivered_price) if best else "",
        "retailer": best.retailer.name if best else "",
        "buy": best.get_outbound_url() if best else "",
        "checked": ago(best.last_checked) if best else "",
        "badge": summary.badge or "",
        "second": {"retailer": summary.second.retailer.name, "price": gbp(summary.second.delivered_price)} if summary.second else None,
        "saving": gbp(summary.saving) if summary.saving else "",
        "percent": summary.percent or 0,
    }
    return data


@require_GET
def deck_api(request):
    """Cards for the swipe deck, cheapest-first within newest sets."""
    try:
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        offset = 0
    products = Product.objects.for_lists().filter(lowest_price__isnull=False)
    game = request.GET.get("game")
    if game:
        products = products.filter(game__slug=game)
    products = products.order_by(F("release").desc(nulls_last=True), "name")
    batch = list(products.prefetch_related(offers.buyable_prefetch())[offset : offset + DECK_PAGE + 1])
    more = len(batch) > DECK_PAGE
    batch = batch[:DECK_PAGE]
    week_lows = offers.week_low_map([p.pk for p in batch])
    cards = [card_data(request, p, offers.summarise(p, week_lows)) for p in batch]
    response = JsonResponse({"cards": cards, "next": offset + DECK_PAGE if more else None})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def deck(request):
    products = Product.objects.for_lists().filter(lowest_price__isnull=False)
    return render(
        request,
        "web/deck.html",
        {
            "games": Game.objects.filter(is_active=True),
            "product_types": Product.Type.choices,
            "total": products.count(),
            "meta_title": text(request, "deck.title"),
            "noindex": True,
        },
    )


@require_GET
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /go/",
        "Disallow: /search/",
        "Disallow: /api/",
        "Disallow: /swipe/",
        "Sitemap: " + request.build_absolute_uri("/sitemap.xml"),
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
