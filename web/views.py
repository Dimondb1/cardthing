from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Case, Count, F, IntegerField, Prefetch, Q, Value, When
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import pricing
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
        ordering = [F("release").desc(nulls_last=True), has_price, "name"]
    else:
        ordering = [has_price, F("release").desc(nulls_last=True), "name"]
        if terms:
            ordering.insert(0, "name_rank")
    products = products.annotate(has_price=has_price).order_by(*ordering)

    paginator = Paginator(products, settings.CARDSCOUT_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    filters_active = bool(
        game or product_type or form.value("in_stock") or sort != form.default_sort
    )
    context = {
        "form": form,
        "query": query,
        "page": page,
        "total": paginator.count,
        "filters_active": filters_active,
        "no_matches": bool(query) and not filters_active and paginator.count == 0,
        "games": Game.objects.filter(is_active=True),
    }
    context.update(template_context)
    return render(request, "web/browse.html", context)


@require_GET
def home(request):
    games = list(
        Game.objects.filter(is_active=True)
        .annotate(product_count=Count("products", filter=Q(products__is_active=True)))
        .filter(product_count__gt=0)
        .prefetch_related(
            Prefetch(
                "sets",
                queryset=ProductSet.objects.filter(products__is_active=True).distinct(),
                to_attr="listed_sets",
            )
        )
    )
    return render(
        request,
        "web/home.html",
        {
            "games": games,
            "drops": list(pricing.price_drops(limit=6)),
            "popular": pricing.popular(limit=8),
            "hide_header_search": True,
            "meta_full_title": text(
                request, "meta.home.title", site_name=settings.CARDSCOUT_SITE_NAME
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

    in_stock_count = sum(1 for l in current if l.availability == Listing.Availability.IN_STOCK)
    preorder_count = sum(1 for l in current if l.availability == Listing.Availability.PREORDER)
    last_checked = max((l.last_checked for l in listings), default=None)

    today = timezone.localdate()
    days = settings.CARDSCOUT_HISTORY_DAYS
    chart = price_chart(pricing.history(product, days=days, today=today), days, today)
    last_known = None if cheapest else pricing.last_known_price(product)

    related = []
    if product.product_set_id:
        related = list(
            Product.objects.for_lists()
            .filter(product_set_id=product.product_set_id)
            .exclude(pk=product.pk)
            .order_by("name")[:6]
        )

    return render(
        request,
        "web/product.html",
        {
            "product": product,
            "current": current,
            "unavailable": unavailable,
            "cheapest": cheapest,
            "in_stock_count": in_stock_count,
            "preorder_count": preorder_count,
            "last_checked": last_checked,
            "has_listings": bool(listings),
            "chart": chart,
            "history_days": days,
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
def about(request):
    return render(
        request,
        "web/about.html",
        {
            "retailers": Retailer.objects.filter(is_active=True),
            "contact_email": settings.CARDSCOUT_CONTACT_EMAIL,
            "stale_hours": settings.CARDSCOUT_STALE_AFTER_HOURS,
            "meta_title": text(request, "about.title"),
            "canonical_url": request.build_absolute_uri(request.path),
        },
    )


@require_GET
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /go/",
        "Disallow: /search/",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
