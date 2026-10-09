import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.templatetags.static import static
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, F, IntegerField, Max, OuterRef, Prefetch, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from catalogue import alerts, insights, mail, offers, pricing
from catalogue.ordering import LANGUAGES, SORTS, apply_languages, order_products, order_products_default
from catalogue.models import DailyPageView, Game, Listing, OutboundClick, Product, ProductAlias, ProductSet, Retailer, stale_cutoff
from catalogue.search import apply_search
from catalogue.types import type_choices
from content import service as copy
from inbox import service as inbox
from inbox.models import Conversation

from .charts import price_chart
from .templatetags.ripraptor import gbp

BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "preview", "monitor")


def text(request, key, **values):
    """Site wording for use in a view, sharing the request's single lookup."""
    return copy.get(key, store=copy.for_request(request), **values)


PRICE_BANDS = [
    ("", "Any price"),
    ("0-10", "Under £10"),
    ("10-25", "£10 to £25"),
    ("25-50", "£25 to £50"),
    ("50-100", "£50 to £100"),
    ("100-", "£100 and up"),
]


class FilterForm(forms.Form):
    """Search and filters. Labels here are short functional labels."""

    # "Best match" only means something when there is a search query.
    SEARCH_SORTS = [("best", "Best match")] + SORTS
    BROWSE_SORTS = SORTS

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
    compared = forms.BooleanField(label="2+ shops", required=False)
    price = forms.ChoiceField(label="Price", choices=PRICE_BANDS, required=False)
    lang = forms.MultipleChoiceField(
        label="Language", choices=LANGUAGES, required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, fixed_game=None, has_query=False, type_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        if fixed_game is not None:
            del self.fields["game"]
        if type_options is not None:
            self.fields["type"].choices = [("", "All types")] + list(type_options)
        if not has_query:
            self.fields["sort"].choices = self.BROWSE_SORTS
        self.default_sort = "best" if has_query else "newest"

    def value(self, name, default=None):
        # cleaned_data holds every valid field even when another field is not.
        if not self.is_bound or not hasattr(self, "cleaned_data"):
            return default
        return self.cleaned_data.get(name) or default


def types_for(game, queryset):
    """The type filter's choices: this game's words, only for types the list holds."""
    present = set(queryset.order_by().values_list("product_type", flat=True).distinct())
    return type_choices(game.slug if game else None, present=present)


def _browse(request, template_context, *, base_queryset, fixed_game=None, default_ordering=None):
    """Shared by search, game and set pages."""
    has_query = bool(request.GET.get("q", "").strip())
    chosen_game = fixed_game or Game.objects.filter(slug=request.GET.get("game", "")).first()
    scope = base_queryset.filter(game=chosen_game) if chosen_game and fixed_game is None else base_queryset
    form = FilterForm(
        request.GET or None, fixed_game=fixed_game, has_query=has_query, type_options=types_for(chosen_game, scope)
    )
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
    if form.value("compared"):
        products = products.filter(in_stock_count__gte=2)
    band = form.value("price")
    if band:
        low, _, high = band.partition("-")
        products = products.filter(lowest_price__gte=low)
        if high:
            products = products.filter(lowest_price__lt=high)
    languages = form.value("lang") or []
    products = apply_languages(products, languages)

    sort = form.value("sort", form.default_sort)
    if sort == "best" and not terms:
        sort = "newest"
    if default_ordering and sort == "newest":
        products = order_products_default(products, sort, default_ordering)
    else:
        products = order_products(products, sort, first="name_rank" if sort == "best" else None)

    paginator = Paginator(products.prefetch_related(offers.buyable_prefetch()), settings.RIPRAPTOR_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    if query and not request.GET.get("page"):
        insights.record_search(request, query, paginator.count)
    week_lows = offers.week_low_map([p.pk for p in page.object_list])
    cards = [(product, offers.summarise(product, week_lows)) for product in page.object_list]

    filters_active = bool(
        game or product_type or form.value("in_stock") or form.value("compared") or band or languages
        or sort != form.default_sort
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
    featured = featured_deals(priced)
    # Biggest savings leaves out what featured deals already show, so the two rows do not repeat.
    shown = {product.pk for product, _ in featured}
    savings = [row for row in offers.biggest_savings(priced, limit=9 + len(shown)) if row[0].pk not in shown][:9]
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
        if before is not None and product.lowest_known is not None and before != product.lowest_known:
            product.movement = product.lowest_known - before
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
    cache.set(key, (savings, trending, recent, retailer_count, featured), settings.RIPRAPTOR_HOME_CACHE_SECONDS)
    return savings, trending, recent, retailer_count, featured


FEATURED_SLOTS = 8
FEATURED_EARNING = 6   # three in four featured deals go to shops that pay a commission


def earns(listing):
    retailer = listing.retailer
    return bool(retailer.affiliate_url_template) or retailer.source_type in (Retailer.Source.AMAZON, Retailer.Source.EBAY)


def featured_deals(products):
    """Good deals for the top of the home page, as (product, summary), three in four from shops that earn.

    Only genuine deals qualify: the cheapest price is a saving on the next
    shop or the lowest in the trending window. Among those, deals whose
    cheapest shop pays a commission take up to six of the eight places and
    the best of the rest take the others; if either side is short, the other
    fills in. The page says that featured deals favour those shops.
    """
    week_lows = offers.week_low_map([p.pk for p in products])
    good = []
    for product in products:
        summary = offers.summarise(product, week_lows)
        if summary.best is None or summary.best.availability != Listing.Availability.IN_STOCK:
            continue
        # Promoted deals need a confirmed delivered price and no sign of a wrong product link.
        if not summary.best.delivery_known or summary.suspect:
            continue
        if not (summary.badge or summary.saving):
            continue
        good.append((product, summary))
    good.sort(key=lambda row: (-(row[1].percent or 0), row[0].name))
    paying = [row for row in good if earns(row[1].best)]
    other = [row for row in good if not earns(row[1].best)]
    picked_paying = paying[:FEATURED_EARNING]
    picked_other = other[:FEATURED_SLOTS - len(picked_paying)]
    room = FEATURED_SLOTS - len(picked_paying) - len(picked_other)
    picked_paying += paying[len(picked_paying):len(picked_paying) + room]
    # Interleave so the row reads as one list: three earning, one other, and so on.
    rows, pay, rest = [], list(picked_paying), list(picked_other)
    while pay or rest:
        rows += [pay.pop(0) for _ in range(min(3, len(pay)))]
        if rest:
            rows.append(rest.pop(0))
    return rows[:FEATURED_SLOTS]


# Games shown first on the home page, in this order; the rest follow by size.
PINNED_GAMES = ["pokemon", "magic-the-gathering", "football", "one-piece", "yu-gi-oh", "lorcana"]


def home_football():
    """In-stock football products for the home row, most widely stocked first."""
    from django.core.cache import cache

    from catalogue.signals import FOOTBALL_CACHE_KEY as key

    cached = cache.get(key)
    if cached is not None:
        return cached
    rows = list(
        Product.objects.for_lists()
        .filter(game__slug="football", in_stock_count__gte=1)
        .prefetch_related(offers.buyable_prefetch())
        .order_by("-in_stock_count", F("lowest_price").asc(nulls_last=True))[:10]
    )
    for product in rows:
        product.best_offer = product.offers[0] if product.offers else None
    cache.set(key, rows, settings.RIPRAPTOR_HOME_CACHE_SECONDS)
    return rows


NEW_RELEASE_DAYS = 45    # a product counts as a drop while its release is this recent or still to come
NEW_SEEN_DAYS = 14       # or while shops have only just started listing it


def latest_drops_queryset():
    """New sealed products with a price: on pre-order, just released, or just listed by shops.

    Few products carry a release date, so a current pre-order listing is the
    main sign of an upcoming drop. "Just listed" starts two days after the
    catalogue's first product, so the launch import does not make the whole
    catalogue a drop for a fortnight. ``tier`` orders them: upcoming first,
    then recently released, then newly listed.
    """
    today = timezone.localdate()
    seen_since = timezone.now() - timedelta(days=NEW_SEEN_DAYS)
    first = Product.objects.order_by("created_at").values_list("created_at", flat=True).first()
    if first is not None:
        seen_since = max(seen_since, first + timedelta(days=2))
    on_preorder = Exists(
        Listing.objects.filter(
            product=OuterRef("pk"), is_active=True, retailer__is_active=True,
            last_checked__gte=stale_cutoff(), availability=Listing.Availability.PREORDER,
            sanity__in=Listing.COUNTED,
        )
    )
    upcoming = Q(release__gt=today) | Q(on_preorder=True)
    recent = Q(release__gte=today - timedelta(days=NEW_RELEASE_DAYS))
    fresh = Q(created_at__gte=seen_since)
    return (
        Product.objects.for_lists()
        .filter(lowest_price__isnull=False)
        .annotate(on_preorder=on_preorder)
        .filter(upcoming | recent | fresh)
        .annotate(tier=Case(When(upcoming, then=Value(0)), When(recent, then=Value(1)), default=Value(2),
                            output_field=IntegerField()))
    )


DROPS_ORDER = ("tier", F("release").desc(nulls_last=True), "-created_at", "name")


def home_drops_new():
    """The home row of latest drops, newest release first, cached with the other lists."""
    from django.core.cache import cache

    from catalogue.signals import NEW_CACHE_KEY as key

    cached = cache.get(key)
    if cached is not None:
        return cached
    rows = list(latest_drops_queryset().prefetch_related(offers.buyable_prefetch()).order_by(*DROPS_ORDER)[:10])
    for product in rows:
        product.best_offer = product.offers[0] if product.offers else None
    cache.set(key, rows, settings.RIPRAPTOR_HOME_CACHE_SECONDS)
    return rows


COMING_SOON_ON_HOME = 3   # the home page is the money page: only sets a visitor can pre-order now, and few


def coming_soon_queryset(game=None):
    """Announced sets still to come, in one query: dated today or later, or undated with a shop taking
    pre-orders and none selling it from stock, since an undated set a shop already sells is out. Each
    carries how many shops take pre-orders, the cheapest of them, and whether a shop already has it in
    stock (a UK street date can come before the published one), so a row never says no pre-orders while a
    shop is selling it.

    Only a current pre-order at a shop counts: active, checked within the stale window, its price not
    kept out, and never eBay or Amazon, whose pre-orders are resellers' guesses. The cheapest is a
    confirmed delivered price when any shop has one, else an item price shown as plus delivery, the
    order every list uses.
    """
    from catalogue.pricing import MARKETPLACES

    current = Listing.objects.filter(
        product__product_set=OuterRef("pk"), product__is_active=True, is_active=True, retailer__is_active=True,
        last_checked__gte=stale_cutoff(), sanity__in=Listing.COUNTED,
    ).exclude(retailer__source_type__in=MARKETPLACES)
    preorders = current.filter(availability=Listing.Availability.PREORDER)
    cheapest = preorders.annotate(
        shown=Case(When(delivery_known=True, then=F("delivered_price")), default=F("price"))
    ).order_by("-delivery_known", "shown", "pk")
    shops = (
        preorders.order_by().values("product__product_set")
        .annotate(shops=Count("retailer", distinct=True)).values("shops")
    )
    sets = ProductSet.objects.filter(game__is_active=True)
    if game is not None:
        sets = sets.filter(game=game)
    today = timezone.localdate()
    return (
        sets.select_related("game")
        .annotate(
            has_preorder=Exists(preorders),
            in_stock=Exists(current.filter(availability=Listing.Availability.IN_STOCK)),
            preorder_count=Coalesce(Subquery(shops[:1]), Value(0)),
            preorder_price=Subquery(cheapest.values("shown")[:1]),
            preorder_known=Subquery(cheapest.values("delivery_known")[:1]),
            preorder_slug=Subquery(cheapest.values("product__slug")[:1]),
        )
        .filter(Q(release_date__gte=today) | Q(release_date__isnull=True, has_preorder=True, in_stock=False))
        .order_by(F("release_date").asc(nulls_last=True), "name")
    )


def coming_soon_sets(game=None, limit=8):
    """Sets still to come, soonest first, for the New page (limit None), a game page or the home page.

    Cached with the other lists under 'web:coming-soon:v1', or ':<game>' for one game's, so every
    clear_list_caches drops them.
    """
    from django.core.cache import cache

    from catalogue.signals import COMING_SOON_CACHE_KEY

    key = COMING_SOON_CACHE_KEY if game is None else f"{COMING_SOON_CACHE_KEY}:{game.slug}"
    rows = cache.get(key)
    if rows is None:
        rows = list(coming_soon_queryset(game))
        cache.set(key, rows, settings.RIPRAPTOR_HOME_CACHE_SECONDS)
    return rows if limit is None else rows[:limit]


def date_source_label(product_set):
    """Where a set's date came from, for 'Date from ...': a release source's name, or nothing when the
    owner set it, it predates the sources, or the source is a shop's title. Exclamation marks in a
    source's name are left out, as everywhere in the site's wording."""
    from catalogue.releases import BY_NAME

    source = BY_NAME.get(product_set.release_date_source)
    return source.label.replace("!", "") if source else ""


@require_GET
def latest_drops(request):
    heading = text(request, "browse.new.title")
    return _browse(
        request,
        {
            "heading": heading,
            "intro": text(request, "browse.new.intro"),
            "meta_title": text(request, "meta.new.title"),
            "meta_description": text(request, "meta.new.description"),
            "canonical_url": request.build_absolute_uri(reverse("web:new")),
            "structured_json": json.dumps(breadcrumbs_json(request, [(heading, reverse("web:new"))])),
            "coming_soon": coming_soon_sets(limit=None),
        },
        base_queryset=latest_drops_queryset(),
        default_ordering=DROPS_ORDER,
    )


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
    rank = {slug: i for i, slug in enumerate(PINNED_GAMES)}
    games.sort(key=lambda g: (rank.get(g.slug, len(PINNED_GAMES)), -g.product_count, g.name))
    last_checked = Listing.objects.live().aggregate(latest=Max("last_checked"))["latest"]
    savings, trending, recent, retailer_count, featured = home_lists()
    drops = list(pricing.price_drops(limit=6))
    restocked = pricing.back_in_stock(limit=8)
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
            "featured": featured,
            "trending": trending,
            "recent": recent,
            "drops": drops,
            "restocked": restocked,
            "football": home_football(),
            "latest": home_drops_new(),
            "coming_soon": [row for row in coming_soon_sets(limit=None) if row.preorder_price is not None][
                :COMING_SOON_ON_HOME
            ],
            "hide_header_search": True,
            "meta_full_title": text(
                request, "meta.home.title", site_name=settings.RIPRAPTOR_SITE_NAME
            ),
            "canonical_url": request.build_absolute_uri("/"),
            "structured_json": json.dumps(site_json(request)),
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
    sets = ProductSet.objects.filter(game=game, products__is_active=True).select_related("game").distinct()
    return _browse(
        request,
        {
            "heading": game.name,
            "game": game,
            "sets": sets,
            "meta_title": text(request, "meta.game.title", game=game.name),
            "meta_description": text(request, "meta.game.description", game=game.name),
            "canonical_url": request.build_absolute_uri(game.get_absolute_url()),
            "structured_json": json.dumps(breadcrumbs_json(request, [(game.name, game.get_absolute_url())])),
            "coming_soon": coming_soon_sets(game),
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
    # A set still to come says when it is out and who said so; an undated one says so only while shops take
    # pre-orders, since an old set entered without a date is not still to come.
    upcoming = any(row.pk == product_set.pk for row in coming_soon_sets(game, limit=None))
    return _browse(
        request,
        {
            "heading": product_set.name,
            "game": game,
            "product_set": product_set,
            "set_upcoming": upcoming,
            "date_source": date_source_label(product_set) if upcoming else "",
            "sets": ProductSet.objects.filter(game=game, products__is_active=True).select_related("game").distinct(),
            "meta_title": text(request, "meta.set.title", **values),
            "meta_description": text(request, "meta.set.description", **values),
            "canonical_url": request.build_absolute_uri(product_set.get_absolute_url()),
            "structured_json": json.dumps(breadcrumbs_json(
                request, [(game.name, game.get_absolute_url()), (product_set.name, product_set.get_absolute_url())]
            )),
        },
        base_queryset=Product.objects.for_lists().filter(product_set=product_set),
        fixed_game=game,
    )


def breadcrumbs_json(request, crumbs):
    """schema.org BreadcrumbList for [(name, path), ...]."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": request.build_absolute_uri(path)}
            for i, (name, path) in enumerate(crumbs)
        ],
    }


def site_json(request):
    """schema.org WebSite and Organization, with the site search for search engines."""
    home = request.build_absolute_uri("/")
    return [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": settings.RIPRAPTOR_SITE_NAME,
            "url": home,
            "potentialAction": {
                "@type": "SearchAction",
                "target": {"@type": "EntryPoint", "urlTemplate": home + "search/?q={query}"},
                "query-input": "required name=query",
            },
        },
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": settings.RIPRAPTOR_SITE_NAME,
            "url": home,
            "logo": request.build_absolute_uri(static("img/logo.png")),
        },
    ]


def search_words(product):
    """What a person would type to find the product: the game first unless the name already says it."""
    game = product.game.display_short
    first = game.lower().split()[0] if game else ""
    return product.name if not first or first in product.name.lower() else f"{game} {product.name}"


def amazon_search_url(product, listings):
    """A tagged Amazon search for the product, or None when we have no tag or a real Amazon price."""
    tag = settings.RIPRAPTOR_AMAZON_PARTNER_TAG
    if not tag or any(row.retailer.source_type == Retailer.Source.AMAZON for row in listings):
        return None
    query = urlencode({"k": search_words(product), "tag": tag})
    return f"https://www.amazon.co.uk/s?{query}"


# eBay Partner Network's UK rotation id, as in the site's own tracked eBay links.
EBAY_UK_ROTATION = "710-53481-19255-0"


def ebay_search_url(product, listings):
    """A tagged eBay UK search for new, buy-it-now listings of the product, or None.

    Only when eBay has no in-stock listing we already show, since then the
    real eBay price is in the comparison already.
    """
    campaign = settings.RIPRAPTOR_EBAY_CAMPAIGN_ID
    if not campaign or any(
        row.retailer.source_type == Retailer.Source.EBAY and row.is_buyable for row in listings
    ):
        return None
    query = urlencode({
        "_nkw": search_words(product), "LH_BIN": "1", "LH_ItemCondition": "1000", "LH_PrefLoc": "1",
        "mkevt": "1", "mkcid": "1", "mkrid": EBAY_UK_ROTATION, "campid": campaign, "toolid": "10001",
    })
    return f"https://www.ebay.co.uk/sch/i.html?{query}"


@require_GET
def product_detail(request, slug):
    product = Product.objects.active().select_related("game", "product_set").filter(slug=slug).first()
    if product is None:
        alias = ProductAlias.objects.filter(slug=slug, product__is_active=True).select_related("product").first()
        if alias is None:
            raise Http404("No product with that address.")
        return HttpResponsePermanentRedirect(alias.product.get_absolute_url())
    listings = list(
        Listing.objects.filter(product=product)
        .live()
        .select_related("retailer")
        .order_by("-delivery_known", "delivered_price", "retailer__name")
    )
    in_stock_first = {Listing.Availability.IN_STOCK: 0, Listing.Availability.PREORDER: 1}
    current = sorted(
        (listing for listing in listings if listing.is_buyable),
        key=lambda listing: (
            *offers.offer_order(listing),
            in_stock_first.get(listing.availability, 2),
            listing.retailer.name,
        ),
    )
    unavailable = [listing for listing in listings if not listing.is_buyable]
    cheapest = current[0] if current else None
    shop_count = len({listing.retailer_id for listing in current})
    product.offers = current
    summary = offers.summarise(product, offers.week_low_map([product.pk]))

    in_stock_count = sum(1 for l in current if l.availability == Listing.Availability.IN_STOCK)
    preorder_count = sum(1 for l in current if l.availability == Listing.Availability.PREORDER)
    last_checked = max((l.last_checked for l in listings), default=None)
    alerts_preorder = alerts.preorder_alerts_on()
    alert_hides = Listing.BUYABLE if alerts_preorder else [Listing.Availability.IN_STOCK]

    today = timezone.localdate()
    days = settings.RIPRAPTOR_HISTORY_DAYS
    chart = price_chart(pricing.history(product, days=days, today=today), days, today)
    month_ago = pricing.previous_price_map([product.pk], days=30, today=today).get(product.pk)
    last_known = None if cheapest else pricing.last_known_price(product)
    restocks = pricing.restock_summary(product) if listings else None

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
        "category": product.type_name,
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
            "lowPrice": str(min(listing.shown_price for listing in current)),
            "highPrice": str(max(listing.shown_price for listing in current)),
            "offerCount": len(current),
            "availability": "https://schema.org/InStock" if in_stock_count else "https://schema.org/PreOrder",
        }
    elif any(listing.sanity != Listing.Sanity.EXCLUDED for listing in unavailable):
        # Sold out everywhere: Google still needs offers, so give the shops' last prices, marked out of stock.
        # A price kept out of the comparison is not one of them.
        prices = sorted(listing.delivered_price for listing in unavailable if listing.sanity != Listing.Sanity.EXCLUDED)
        structured["offers"] = {
            "@type": "AggregateOffer",
            "priceCurrency": "GBP",
            "lowPrice": str(prices[0]),
            "highPrice": str(prices[-1]),
            "offerCount": len(prices),
            "availability": "https://schema.org/OutOfStock",
        }
    product_json = [structured] if "offers" in structured else []
    crumbs = [(product.game.name, product.game.get_absolute_url())]
    if product.product_set:
        crumbs.append((product.product_set.name, product.product_set.get_absolute_url()))
    crumbs.append((product.name, product.get_absolute_url()))
    if cheapest and cheapest.delivery_known:
        meta_values = {
            "product": product.name,
            "price": gbp(cheapest.delivered_price),
            "retailer": cheapest.retailer.name,
            "count": len(current),
        }
        meta_title = text(request, "meta.product.title_priced", **meta_values)
        meta_description = text(request, "meta.product.description_priced", **meta_values)
    else:
        meta_title = text(request, "meta.product.title", product=product.name)
        meta_description = text(request, "meta.product.description", product=product.name)
    return render(
        request,
        "web/product.html",
        {
            "product": product,
            # A product with no prices at all carries only its breadcrumbs: Product data without offers is invalid.
            "structured_json": json.dumps(product_json + [breadcrumbs_json(request, crumbs)]),
            "meta_type": "product",
            "meta_image": request.build_absolute_uri(product.image_src) if product.image_src else "",
            "current": current,
            "unavailable": unavailable,
            "cheapest": cheapest,
            "shop_count": shop_count,
            "summary": summary,
            "in_stock_count": in_stock_count,
            "preorder_count": preorder_count,
            "last_checked": last_checked,
            "has_listings": bool(listings),
            "chart": chart,
            "history_days": days,
            "month_ago": month_ago,
            "last_known": last_known,
            "restocks": restocks,
            # Once pre-order emails are on, a shop pre-order hides the form too: the alert would be sent at once.
            "alerts_on": mail.enabled() and not any(
                l.availability in alert_hides and l.retailer.source_type not in alerts.MARKETPLACES
                for l in current
            ),
            "alerts_preorder": alerts_preorder,
            "alert_state": request.GET.get("alert") if request.GET.get("alert") in ALERT_STATES else "",
            "amazon_search": amazon_search_url(product, listings),
            "ebay_search": ebay_search_url(product, listings),
            "related": related,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "canonical_url": request.build_absolute_uri(product.get_absolute_url()),
        },
    )


CLICK_SOURCES = ("watchlist",)


@require_GET
def go(request, listing_id):
    """Send the visitor to the retailer and count the click, noting a click from a watchlist."""
    listing = get_object_or_404(
        Listing.objects.live().select_related("retailer", "product"),
        pk=listing_id,
        product__is_active=True,
    )
    agent = request.headers.get("User-Agent", "").lower()
    if agent and not any(marker in agent for marker in BOT_MARKERS):
        source = request.GET.get("from", "")
        # The click is counted on a best effort basis: a database too busy to save it never stops
        # the visitor's trip to the shop.
        with pricing.drop_if_locked("Click"):
            pricing.retry_locked(lambda: OutboundClick.objects.create(
                listing=listing, product=listing.product, retailer=listing.retailer,
                source=source if source in CLICK_SOURCES else "",
            ))
    response = HttpResponseRedirect(listing.retailer.outbound_url(listing.url))
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_GET
def terms(request):
    return render(
        request,
        "web/terms.html",
        {
            "meta_title": text(request, "terms.title"),
            "canonical_url": request.build_absolute_uri(request.path),
            "amazon": bool(settings.RIPRAPTOR_AMAZON_PARTNER_TAG)
            or Retailer.objects.filter(is_active=True, source_type=Retailer.Source.AMAZON).exists(),
        },
    )


@require_GET
def product_prices_api(request, slug):
    """Current prices for the product page's refresh control."""
    from .templatetags.ripraptor import ago

    product = get_object_or_404(Product.objects.active(), slug=slug)
    rows = []
    for listing in Listing.objects.filter(product=product).live().select_related("retailer"):
        rows.append({
            "id": listing.pk,
            "total": gbp(listing.delivered_price) if listing.delivery_known
            else text(request, "product.compare.plus_delivery", price=gbp(listing.price)),
            "price": gbp(listing.shown_price),
            "note": price_note(request, listing),
            "checked": ago(listing.last_checked),
            "buyable": listing.is_buyable,
        })
    response = JsonResponse({"listings": rows})
    response["Cache-Control"] = "no-store"
    return response


def price_note(request, listing):
    """How a shown price is made up, as the price_note include words it."""
    if not listing.delivery_known:
        return text(request, "product.delivery.unknown_note", price=gbp(listing.price))
    if listing.delivery_cost:
        return text(request, "product.breakdown.delivery", price=gbp(listing.price), delivery=gbp(listing.delivery_cost))
    return text(request, "product.breakdown.free", price=gbp(listing.price))


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
                "meta": f"{product.game.display_short} · {product.type_name}",
                "price": gbp(product.lowest_price) if product.lowest_price is not None else "",
                "stock": stock,
                "state": state,
                "type": product.product_type,
            })
    response = JsonResponse({"query": query, "results": results})
    response["Cache-Control"] = "no-store"
    return response


DECK_PAGE = 12


def shown_text(request, listing):
    """A listing's shown price as text, marked "+ delivery" when the charge is not known."""
    if listing.delivery_known:
        return gbp(listing.delivered_price)
    return f"{gbp(listing.price)} {text(request, 'browse.price.plus')}"


def card_data(request, product, summary):
    """One product as the swipe deck and search cards need it."""
    from .templatetags.ripraptor import ago, gbp

    best = summary.best
    data = {
        "id": product.pk,
        "name": product.name,
        "url": product.get_absolute_url(),
        "meta": f"{product.game.display_short} · {product.type_name}",
        "type": product.product_type,
        "image": product.image_src,
        "price": shown_text(request, best) if best else "",
        "retailer": best.retailer.name if best else "",
        "buy": best.get_outbound_url() if best else "",
        "checked": ago(best.last_checked) if best else "",
        "badge": summary.badge or "",
        "second": {"retailer": summary.second.retailer.name, "price": shown_text(request, summary.second)} if summary.second else None,
        "saving": gbp(summary.saving) if summary.saving else "",
        "percent": summary.percent or 0,
    }
    return data


def deck_products(request):
    """The deck's products under the page's game, language and sort choices."""
    products = Product.objects.for_lists().filter(lowest_price__isnull=False)
    game = request.GET.get("game")
    if game:
        products = products.filter(game__slug=game)
    products = apply_languages(products, request.GET.getlist("lang"))
    kind = request.GET.get("type", "")
    if kind in dict(Product.Type.choices):
        products = products.filter(product_type=kind)
    sort = request.GET.get("sort") or "newest"
    if sort not in dict(SORTS):
        sort = "newest"
    return order_products(products, sort)


@require_GET
def deck_api(request):
    """Cards for the swipe deck, cheapest-first within newest sets."""
    try:
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        offset = 0
    products = deck_products(request)
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
    products = deck_products(request)
    chosen = request.GET.getlist("lang")
    game = Game.objects.filter(slug=request.GET.get("game", ""), is_active=True).first()
    scope = Product.objects.for_lists().filter(lowest_price__isnull=False)
    if game:
        scope = scope.filter(game=game)
    return render(
        request,
        "web/deck.html",
        {
            "games": Game.objects.filter(is_active=True),
            "product_types": Product.Type.choices,
            "sorts": SORTS,
            "sort": request.GET.get("sort") if request.GET.get("sort") in dict(SORTS) else "newest",
            "types": types_for(game, scope),
            "type": request.GET.get("type", ""),
            "languages": [(code, label, code in chosen) for code, label in LANGUAGES],
            "total": products.count(),
            "meta_title": text(request, "deck.title"),
            "noindex": True,
        },
    )


AI_CRAWLERS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "anthropic-ai",
               "PerplexityBot", "Google-Extended", "Applebot-Extended", "Bingbot", "CCBot", "Amazonbot")


def deals_lists():
    """Biggest savings, price drops and the restock log for the deals page, cached like the home lists."""
    from django.core.cache import cache

    from catalogue.signals import DEALS_CACHE_KEY

    cached = cache.get(DEALS_CACHE_KEY)
    if cached is not None:
        return cached
    priced = Product.objects.for_lists().filter(in_stock_count__gte=1).prefetch_related(offers.buyable_prefetch())
    lists = {
        "savings": offers.biggest_savings(priced, limit=30),
        "drops": list(pricing.price_drops(limit=12)),
        "restock_log": pricing.restock_log(days=7),
    }
    cache.set(DEALS_CACHE_KEY, lists, settings.RIPRAPTOR_HOME_CACHE_SECONDS)
    return lists


@require_GET
def deals(request):
    lists = deals_lists()
    last_checked = Listing.objects.live().aggregate(latest=Max("last_checked"))["latest"]
    return render(
        request,
        "web/deals.html",
        {
            **lists,
            "last_checked": last_checked,
            "meta_title": text(request, "meta.deals.title"),
            "meta_description": text(request, "meta.deals.description"),
            "canonical_url": request.build_absolute_uri(reverse("web:deals")),
            "structured_json": json.dumps(breadcrumbs_json(request, [(text(request, "deals.title"), reverse("web:deals"))])),
        },
    )


WATCHLIST_MAX = 50
RECENT_MAX = 12


def products_for_slugs(wanted):
    """Products for ``wanted`` slugs in that order, with buyable offers attached and old addresses resolved."""
    found = {p.slug: p for p in Product.objects.for_lists().filter(slug__in=wanted).prefetch_related(offers.buyable_prefetch())}
    missing = [slug for slug in wanted if slug not in found]
    if missing:
        moved = dict(ProductAlias.objects.filter(slug__in=missing, product__is_active=True).values_list("slug", "product__slug"))
        for product in Product.objects.for_lists().filter(slug__in=moved.values()).prefetch_related(offers.buyable_prefetch()):
            for old, new in moved.items():
                if new == product.slug:
                    found[old] = product
    products = []
    for slug in wanted:
        product = found.get(slug)
        if product is not None and product not in products:
            products.append(product)
    return products


def slugs_in(request, limit):
    return [slug for slug in request.GET.get("p", "").split(",") if slug][:limit]


@require_GET
def watchlist(request):
    """The products a visitor saved, named in the address, with live prices.

    The list lives in the browser and in this page's address, never on the
    server, so the page works bookmarked, pasted into a chat, or with
    JavaScript off. The script fills in what each was saved at.
    """
    wanted = slugs_in(request, WATCHLIST_MAX)
    rows = []
    if wanted:
        products = products_for_slugs(wanted)
        week_lows = offers.week_low_map([p.pk for p in products])
        rows = [(product, offers.summarise(product, week_lows)) for product in products]
        if not insights.is_bot(request):
            insights.bump_many(DailyPageView, DailyPageView.Kind.WATCHED, [p.slug for p in products])
    return render(
        request,
        "web/watchlist.html",
        {
            "rows": rows,
            "meta_title": text(request, "meta.watchlist.title"),
            "noindex": True,
        },
    )


@require_GET
def recent_api(request):
    """A row of the products named in ``p`` with live prices, for the home page's recently viewed list.

    The list of what was viewed lives in the browser; the server only prices it.
    """
    from django.template.loader import render_to_string

    products = products_for_slugs(slugs_in(request, RECENT_MAX))
    week_lows = offers.week_low_map([p.pk for p in products])
    rows = [(product, offers.summarise(product, week_lows)) for product in products]
    response = HttpResponse(render_to_string("web/includes/recent_row.html", {"rows": rows}, request=request))
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def note_api(request):
    """Count one home screen event (shown, added, dismissed, opened). Nothing about the visitor is kept."""
    event = request.GET.get("what", "")
    if event in insights.INSTALL_EVENTS and not insights.is_bot(request):
        insights.bump(DailyPageView, kind=DailyPageView.Kind.INSTALL, key=event)
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    return response


ALERT_STATES = ("sent", "already", "limit", "invalid", "failed")


@require_POST
def alert_ask(request, slug):
    """Take an email address for a back-in-stock alert and send the confirmation. Works without JavaScript."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    product = get_object_or_404(Product.objects.active(), slug=slug)
    back = product.get_absolute_url()
    if request.POST.get("website") or not mail.enabled():
        return HttpResponseRedirect(back)   # the hidden field is only ever filled by robots
    email = request.POST.get("email", "").strip()
    try:
        validate_email(email)
        state = alerts.ask(product, email)
    except ValidationError:
        state = "invalid"
    except mail.MailError:
        state = "failed"
    return HttpResponseRedirect(f"{back}?alert={state}#alert")


@require_GET
def alert_confirm(request, token):
    alert = alerts.confirm(token)
    if alert is None:
        heading, message, product = text(request, "alerts.gone.title"), text(request, "alerts.gone.body"), None
    else:
        heading = text(request, "alerts.confirmed.title")
        message = text(request, "alerts.confirmed.preorder" if alerts.wants_preorder(alert) else "alerts.confirmed.body",
                       product=alert.product.name)
        product = alert.product
    return render(request, "web/alert_done.html", {"heading": heading, "message": message, "product": product,
                                                    "meta_title": heading, "noindex": True})


@require_GET
def alert_stop(request, token):
    product = alerts.stop(token)
    heading = text(request, "alerts.stopped.title")
    message = text(request, "alerts.stopped.body")
    return render(request, "web/alert_done.html", {"heading": heading, "message": message, "product": product,
                                                    "meta_title": heading, "noindex": True})


INBOX_COOKIE = "rr_inbox"


def remember_thread(response, request, conversation):
    response.set_cookie(INBOX_COOKIE, conversation.token, max_age=inbox.KEEP_DAYS * 86400,
                        httponly=True, samesite="Lax", secure=request.is_secure())
    return response


def contact(request):
    """Message us. No account and no email needed: the visitor gets a private link to the thread."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    values = {"body": "", "name": "", "email": ""}
    error = ""
    if request.method == "POST":
        if request.POST.get("website"):
            return HttpResponseRedirect(reverse("web:contact"))   # the hidden field is only ever filled by robots
        values = {k: request.POST.get(k, "").strip() for k in values}
        try:
            if not values["body"]:
                raise ValueError("empty")
            if values["email"]:
                validate_email(values["email"])
            conversation = inbox.start(values["body"], name=values["name"], email=values["email"])
        except ValueError:
            error = "contact.form.empty"
        except ValidationError:
            error = "contact.form.invalid"
        except inbox.Refused:
            error = "contact.form.busy"
        else:
            response = HttpResponseRedirect(conversation.get_absolute_url() + "?sent=1#reply")
            return remember_thread(response, request, conversation)
    elif request.method != "GET":
        return HttpResponse(status=405)
    existing = Conversation.objects.filter(token=request.COOKIES.get(INBOX_COOKIE, "")).first()
    return render(request, "web/contact.html", {
        "values": values, "error": error, "existing": existing, "max_length": inbox.MAX_LENGTH,
        "meta_title": text(request, "contact.title"), "canonical_url": request.build_absolute_uri(request.path),
    })


def contact_thread(request, token):
    conversation = get_object_or_404(Conversation, token=token)
    state = ""
    if request.method == "POST" and request.POST.get("forget") == "1":
        # A button, not the link itself: mail scanners open links in emails and would delete the address unasked.
        if conversation.email:
            inbox.forget_email(conversation)
            conversation.email = ""
        state = "forgot"
    elif request.method == "POST":
        body = request.POST.get("body", "").strip()
        if request.POST.get("website") or not body:
            return HttpResponseRedirect(conversation.get_absolute_url())
        try:
            inbox.add(conversation, body)
        except inbox.Refused:
            state = "closed" if conversation.closed else "busy"
        else:
            return HttpResponseRedirect(conversation.get_absolute_url() + "?sent=1#reply")
    elif request.method != "GET":
        return HttpResponse(status=405)
    elif request.GET.get("forget") == "1" and conversation.email:
        state = "forget"
    elif request.GET.get("sent") == "1":
        state = "sent"
    messages_list = list(conversation.messages.all())
    response = render(request, "web/contact_thread.html", {
        "conversation": conversation, "thread": messages_list, "state": state, "max_length": inbox.MAX_LENGTH,
        "waiting": bool(messages_list) and messages_list[-1].from_visitor,
        "meta_title": text(request, "contact.thread.title"), "noindex": True,
    })
    response["Cache-Control"] = "no-store"
    if request.user.is_staff:
        return response   # the owner looking from admin should not take the visitor's thread as their own
    return remember_thread(response, request, conversation)


@require_GET
def manifest(request):
    """The web app manifest, so a phone can pin RipRaptor to its home screen. No service worker, no push."""
    data = {
        "id": "/",
        "name": settings.RIPRAPTOR_SITE_NAME,
        "short_name": settings.RIPRAPTOR_SITE_NAME,
        "description": text(request, "meta.default.description"),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
        "icons": [
            {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("img/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    response = JsonResponse(data, content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_GET
def robots_txt(request):
    private = ["Disallow: /admin/", "Disallow: /go/", "Disallow: /search/", "Disallow: /api/", "Disallow: /swipe/", "Disallow: /watchlist/",
               "Disallow: /alerts/", "Disallow: /contact/c/"]
    lines = ["User-agent: *", *private, ""]
    # Named so a crawler that only honours its own section still gets the same answer.
    for agent in AI_CRAWLERS:
        lines += [f"User-agent: {agent}", "Allow: /", *private, ""]
    lines += ["Sitemap: " + request.build_absolute_uri("/sitemap.xml")]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def llms_txt(request):
    """A plain description of the site for AI crawlers, with the pages worth reading."""
    games = (
        Game.objects.filter(is_active=True)
        .annotate(product_count=Count("products", filter=Q(products__is_active=True)))
        .filter(product_count__gt=0)
        .order_by("-product_count", "name")
    )
    shops = Retailer.objects.filter(is_active=True).count()
    lines = [
        f"# {settings.RIPRAPTOR_SITE_NAME}",
        "",
        f"> {text(request, 'meta.default.description')}",
        "",
        text(request, 'about.introduction'),
        "",
        f"Prices are in pounds, include UK delivery, and come from {shops} UK shops. "
        "Each product page lists every shop's price with stock and the time it was checked, "
        "and links to the shop to buy. Nothing is sold here.",
        "",
        "## Games",
        "",
    ]
    for game in games:
        lines.append(f"- [{game.name}]({request.build_absolute_uri(game.get_absolute_url())}): {game.product_count} sealed products")
    lines += [
        "",
        "## Pages",
        "",
        f"- [Every game and set]({request.build_absolute_uri(reverse('web:games'))})",
        f"- [How it works]({request.build_absolute_uri(reverse('web:about'))})",
        f"- [Terms and disclosures]({request.build_absolute_uri(reverse('web:terms'))})",
        f"- [Sitemap]({request.build_absolute_uri('/sitemap.xml')}): every product page",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/markdown; charset=utf-8")


@staff_member_required
def insights_page(request):
    """What visitors look at, search for and click. Staff only, under admin."""
    try:
        days = min(max(int(request.GET.get("days", 30)), 1), 365)
    except ValueError:
        days = 30
    context = {**admin.site.each_context(request), "title": "Insights", **insights.report(days)}
    return render(request, "admin/insights.html", context)


def posted_price_moved(request, listing):
    """True when the form posted a price and the listing's (or found row's) price is no longer that price."""
    if "price" not in request.POST:
        return False
    if listing.price is None:
        return request.POST["price"] != ""
    try:
        return Decimal(request.POST["price"]) != listing.price
    except (InvalidOperation, ValueError):
        return True


@staff_member_required
def checks_page(request):
    """Things to check: doubtful and excluded prices, wrong matches, likely duplicates, products found at
    another shop and unknown delivery, each with a one-tap fix."""
    from django.contrib import messages
    from django.db import transaction

    from catalogue import checks, sanity
    from catalogue.management.commands.merge_duplicates import merge
    from catalogue.signals import clear_list_caches

    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("hide", "trust", "show"):
            listing = get_object_or_404(Listing.objects.select_related("product", "retailer"), pk=request.POST.get("listing"))
            if posted_price_moved(request, listing):
                # Only the price the owner looked at is acted on, as with a merge.
                messages.warning(request, "That price has changed since the page loaded. Check it again below.")
            elif action == "hide":
                Listing.objects.filter(pk=listing.pk).update(is_active=False)
                # The other shops were judged against this price: without it they may be fine.
                sanity.judge_product(listing.product_id)
                clear_list_caches(force=True)
                messages.success(request, f"Hidden: {listing.product.name} at {listing.retailer.name}. "
                                          "Tick show on site on the listing to bring it back.")
            else:
                sanity.trust(listing)
                clear_list_caches(force=True)
                messages.success(request, f"Counted: £{listing.price} for {listing.product.name} at {listing.retailer.name}. "
                                          f"It stays counted while it moves less than {sanity.TRUST_BAND * 100:.0f}% "
                                          f"for {sanity.TRUST_DAYS} days.")
        elif action in ("link_found", "not_found"):
            from catalogue import finder

            row = get_object_or_404(checks.found_waiting().select_related("retailer", "suggested"), pk=request.POST.get("row"))
            if posted_price_moved(request, row):
                messages.warning(request, "That price has changed since the page loaded. Check it again below.")
            elif action == "link_found":
                finder.link(row)
                clear_list_caches(force=True)
                messages.success(request, f"Linked: {row.suggested.name} at {row.retailer.name}. "
                                          "The next read of the shop checks its price and stock.")
            else:
                finder.ignore(row)
                messages.success(request, f"Noted: {row.retailer.name} is not asked about {row.suggested.name} again.")
        elif action in ("add_set", "not_a_set", "use_release_date", "keep_release_date"):
            release_tap(request, action)
        elif action == "merge":
            # Only a group the page offered, exactly as it stands now, is merged.
            wanted = request.POST.get("keep", ""), sorted(request.POST.getlist("other"))
            for keep, others in checks.duplicates():
                if (str(keep.pk), sorted(str(o.pk) for o in others)) == wanted:
                    with transaction.atomic():
                        merge(keep, others)
                    clear_list_caches(force=True)
                    messages.success(request, f"Merged into {keep.name}. The old addresses redirect to it.")
                    break
            else:
                messages.warning(request, "That group has changed since the page loaded. Check it again below.")
        return HttpResponseRedirect(reverse("checks"))
    doubtful = checks.doubtful_prices()
    listed = {listing.product_id for listing in doubtful}
    context = {
        **admin.site.each_context(request), "title": "Things to check",
        "doubtful": doubtful, "excluded": checks.excluded_prices(), "counts": checks.sanity_counts(),
        # A product already listed under doubtful prices is not listed twice.
        "wrong": [row for row in checks.wrong_matches() if row[0].pk not in listed],
        "duplicates": checks.duplicates(), "shops": checks.unknown_delivery_shops(),
        "max_percent": offers.MAX_REAL_PERCENT,
        "found": checks.found_stockists(), "found_count": checks.found_waiting().count(),
        "releases": checks.release_candidates(), "disagreements": checks.release_disagreements(),
        "stale_sources": checks.stale_release_sources(),
    }
    return render(request, "admin/checks.html", context)


def release_tap(request, action):
    """The Things to check buttons for announced sets: Add set, Not a set, Use this date and Keep this date.

    Only a row the page offered, still in the state the page showed, is acted on: Use this date and Keep
    this date post the date the page showed, and nothing is saved when it has changed since.
    """
    from django.contrib import messages
    from django.db import transaction

    from catalogue import checks, releases
    from catalogue.signals import clear_list_caches

    changed = "That row has changed since the page loaded. Check it again below."
    shown = releases.parse_owner_date(request.POST.get("date", ""))
    if action == "keep_release_date":
        pk = request.POST.get("set", "")
        if not pk.isdigit():
            raise Http404("No such set.")
        group = next((g for g in checks.release_disagreements() if g["set"] is not None and g["set"].pk == int(pk)),
                     None)
        if group is None or group["hand_date"] is None or group["hand_date"] != shown:
            messages.warning(request, changed)
            return
        product_set = releases.keep_date(group["set"])
        messages.success(request, f"Kept: {product_set.name} comes out on {product_set.release_date:%-d %B %Y}. "
                                  "No source changes it.")
        return
    pk = request.POST.get("release", "")
    if not pk.isdigit():
        raise Http404("No such announced set.")
    if action == "use_release_date":
        row = next((r for group in checks.release_disagreements() for r in group["rows"] if r.pk == int(pk)), None)
        if row is not None and row.release_date != shown:
            row = None
    else:
        row = next((r for r in checks.release_candidates() if r.pk == int(pk)), None)
    if row is None:
        messages.warning(request, changed)
        return
    if action == "not_a_set":
        releases.dismiss(row)
        messages.success(request, f"Noted: {row.name} is not a set. {row.game.name} sets called that are not "
                                  "suggested again from this source.")
        return
    if action == "use_release_date":
        with transaction.atomic():
            product_set = releases.use_date(row)
        clear_list_caches(force=True)
        messages.success(request, f"{product_set.name} now comes out on {product_set.release_date:%-d %B %Y}. "
                                  "No source changes it.")
        return
    name = " ".join(request.POST.get("name", "").split())
    if not name or len(name) > 120:
        messages.warning(request, "Give the set a name of up to 120 characters, then tap Add set again.")
        return
    typed = request.POST.get("date", "").strip()
    released = releases.parse_owner_date(typed)
    if typed and released is None:
        messages.warning(request, "That date was not understood. Pick it from the calendar, or leave it empty.")
        return
    with transaction.atomic():
        product_set = releases.add_set(row, name, released)
    clear_list_caches(force=True)
    when = f" It comes out on {product_set.release_date:%-d %B %Y}." if product_set.release_date else ""
    messages.success(request, f"Added the set {product_set.name} to {row.game.name}.{when} Products named after it "
                              "are filed under it.")


@staff_member_required
def crawl_page(request):
    """Crawl health: whether shops are being read, with one-tap buttons to read a shop now or pause reading."""
    from django.contrib import messages
    from django.db import DatabaseError

    from catalogue import crawl, heat
    from catalogue.models import WorkerState

    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("pause_all", "resume_all"):
            try:
                if action == "pause_all":
                    crawl.pause_all()
                    messages.success(request, "Reading is paused for every shop. No shop is read or stock checked until you tap "
                                              "Resume all; a shop being read now finishes first.")
                else:
                    crawl.resume_all()
                    messages.success(request, "Reading has resumed. Each shop is read on its own schedule.")
            except (DatabaseError, OSError):
                messages.error(request, "The database was busy, so nothing changed. Tap the button again.")
        elif action == "find_now":
            from catalogue import finder

            pk = request.POST.get("product", "")
            if not pk.isdigit():
                raise Http404("No such product.")
            product = get_object_or_404(Product.objects.active(), pk=pk)
            finder.boost(product.pk)
            state = WorkerState.current()
            if state is not None and state.alive() and settings.RIPRAPTOR_FINDER:
                messages.success(request, f"{product.name} will be looked for at the other shops within a few minutes.")
            else:
                messages.warning(request, f"{product.name} is first in line, but the background reader is not running, "
                                          "so the search starts when it is back.")
            return HttpResponseRedirect(reverse("insights"))
        elif action in ("read_now", "pause", "resume"):
            pk = request.POST.get("shop", "")
            if not pk.isdigit():
                raise Http404("No such shop.")
            shop = get_object_or_404(
                Retailer.objects.filter(is_active=True).exclude(source_type=Retailer.Source.MANUAL), pk=pk
            )
            if action == "read_now":
                shop.next_read_at = timezone.now()
                shop.backoff_until = None
                shop.error_streak = 0
                shop.save(update_fields=["next_read_at", "backoff_until", "error_streak"])
                state = WorkerState.current()
                if state is not None and state.alive():
                    note = f"{shop.name} will be read within a few minutes."
                else:
                    note = f"{shop.name} will be read at the next hourly read."
                if shop.reading_paused:
                    note += " It is paused: tap Resume as well."
                elif crawl.all_paused(state):
                    note += " Reading is paused for every shop: tap Resume all as well."
                messages.success(request, note)
            else:
                # Set rather than toggled, so a second tap from a page loaded earlier cannot undo the first.
                shop.reading_paused = action == "pause"
                shop.save(update_fields=["reading_paused"])
                if shop.reading_paused:
                    messages.success(request, f"{shop.name} is paused. It is not read or stock checked until you tap Resume.")
                else:
                    messages.success(request, f"{shop.name} will be read on its schedule again.")
        return HttpResponseRedirect(reverse("crawl"))
    now = timezone.now()
    last = crawl.last_finished()
    state = WorkerState.current()
    hot, warm = heat.tier_counts(heat.heat_scores(now))
    context = {
        **admin.site.each_context(request), "title": "Crawl health",
        "all_paused": crawl.all_paused(state), "last_finished": insights.clock(last, now) if last else None,
        "shops": crawl.shops(now, state), "worker": crawl.worker_status(state, now), "hot": hot, "warm": warm,
    }
    return render(request, "admin/crawl.html", context)
