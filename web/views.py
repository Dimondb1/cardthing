import json
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlencode, urlsplit

from django import forms
from django.conf import settings
from django.templatetags.static import static
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, F, IntegerField, Max, OuterRef, Prefetch, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe

from catalogue import (alerts, clicks, fair_price, images, insights, languages, mail, offers, picks, popularity, pricing,
                       sports, traffic)
from catalogue.ordering import LANGUAGES, SORTS, apply_languages, order_products, order_products_default
from catalogue.models import (
    DailyPageView, FeaturedSettings, Game, Listing, OutboundClick, Product, ProductAlias, ProductSet, Referral, ReferralSettings, Referrer, RemovedProduct,
    Retailer, StockAlert, stale_cutoff,
)
from catalogue.search import apply_search
from catalogue.types import DEFAULT as DEFAULT_TYPES, GAME_TYPES, plural_words, type_choices, type_label
from content import service as copy
from inbox import service as inbox
from inbox.models import Conversation

from guides import state as guide_doors

from . import hubs, listcache
from .pagecache import cached_page
from .charts import price_chart
from .templatetags.ripraptor import gbp, short_date



def text(request, key, **values):
    """Site wording for use in a view, sharing the request's single lookup."""
    return copy.get(key, store=copy.for_request(request), **values)


PRICE_BANDS = [
    ("", "Price"),
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
        empty_label="Game",
    )
    type = forms.ChoiceField(
        label="Product type",
        choices=[("", "Type")] + Product.Type.choices,
        required=False,
    )
    sort = forms.ChoiceField(label="Sort by", choices=SEARCH_SORTS, required=False)
    in_stock = forms.BooleanField(label="In stock", required=False)
    compared = forms.BooleanField(label="2+ shops", required=False)
    price = forms.ChoiceField(label="Price", choices=PRICE_BANDS, required=False)
    lang = forms.ChoiceField(label="Language", choices=[("", "Language")] + LANGUAGES, required=False)

    def __init__(self, *args, fixed_game=None, has_query=False, type_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        if fixed_game is not None:
            del self.fields["game"]
        if type_options is not None:
            self.fields["type"].choices = [("", "Type")] + list(type_options)
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


# The stock tabs on game and set pages, in the order they sit side by side: what can be bought now
# first, then pre-orders, then everything nobody has in stock. Each product is in exactly one.
STOCK_TABS = [("in-stock", "In stock"), ("pre-orders", "Pre-orders"), ("out-of-stock", "Out of stock")]
# Below this many products in the list in view, a game or set page shows no sort or filter choices: a short
# list is read at a glance. From it up, they fold under one Sort and filter button.
FILTERS_FROM = 12
# Sets in the row on a game page, the ones people want most this week first (popularity.set_demand), newest first
# when equal, then the most in stock; All sets opens the game's whole list on the Games page.
GAME_PAGE_SETS = 8
# A sold-out set page's In stock instead row looks for products like this many of the set's own.
SET_INSTEAD_FROM = 12


def default_tab(counts):
    """The tab a game or set page opens on: the first with products, else In stock."""
    return next((key for key, _ in STOCK_TABS if counts.get(key)), STOCK_TABS[0][0])


def stock_tab(tab, compared=False):
    """The filter for one stock tab. With 2+ shops: two shops in stock, taking pre-orders, or listing it."""
    if tab == "pre-orders":
        return Q(in_stock_count=0, preorder_count__gte=2 if compared else 1)
    if tab == "out-of-stock":
        return Q(in_stock_count=0, preorder_count=0) & (Q(listing_count__gte=2) if compared else Q())
    return Q(in_stock_count__gte=2 if compared else 1)


def _browse(request, template_context, *, base_queryset, fixed_game=None, default_ordering=None, tabs=False,
            template="web/browse.html", structured=None, stock_now=False):
    """Shared by search, game and set pages, and Out now on the drops page (``template``).

    ``structured``, when given, is called with the page of products shown and whether this is the canonical
    view (hub_is_canonical), and returns the page's structured data (a list of schema.org blocks), so a hub's
    ItemList names exactly the products on the page.

    ``stock_now`` (Out now), the In stock tab and the In stock filter lead each card with its cheapest in-stock
    shop (in_stock_offers), so a list about stock never shows a pre-order's price or Dropping soon.

    With ``tabs`` (game and set pages) the products are split into STOCK_TABS: the page shows one tab's
    list, counted for every tab in one query, and ``?pane=1`` returns just that tab's list for the
    page's script to swipe to. The In stock toggle gives way to the tabs. The page opens on the first tab
    with products (a tab named in the address still wins), shows only the tabs with products (and the one
    in view), and no tab row at all when that leaves one list. Sort and filters show only for a list of
    FILTERS_FROM products or more, folded under one button, or while one is chosen.
    """
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
    pool = products   # a search's results before the filters below, for its quick filters
    product_type = form.value("type")
    if product_type:
        products = products.filter(product_type=product_type)
    if form.value("in_stock") and not tabs:
        products = products.in_stock()
    if form.value("compared") and not tabs:
        products = products.filter(in_stock_count__gte=2)
    band = form.value("price")
    if band:
        low, _, high = band.partition("-")
        products = products.filter(lowest_price__gte=low)
        if high:
            products = products.filter(lowest_price__lt=high)
    languages = [form.value("lang")] if form.value("lang") else []
    products = apply_languages(products, languages)

    tab, tab_counts, opening, asked = "", {}, "", ""
    if tabs:
        compared = bool(form.value("compared"))
        counted = products.aggregate(**{
            key.replace("-", "_"): Count("pk", filter=stock_tab(key, compared)) for key, _ in STOCK_TABS
        })
        tab_counts = {key: counted[key.replace("-", "_")] for key, _ in STOCK_TABS}
        # Never an empty list while another tab has products, unless the address names that tab.
        opening = default_tab(tab_counts)
        asked = request.GET.get("tab", "")
        asked = asked if asked in dict(STOCK_TABS) else ""
        tab = asked or opening
        products = products.filter(stock_tab(tab, compared))

    sort = form.value("sort", form.default_sort)
    if sort == "best" and not terms:
        sort = "newest"
    if tab == "pre-orders" and sort == "newest":
        # Pre-orders read best soonest out first: newest first would lead with next year's.
        sort = "soonest"
    if default_ordering and sort == "newest":
        products = order_products_default(products, sort, default_ordering)
    else:
        products = order_products(products, sort, first="name_rank" if sort == "best" else None)

    paginator = Paginator(products.prefetch_related(offers.buyable_prefetch()), settings.RIPRAPTOR_PAGE_SIZE)
    if tabs:
        paginator.count = tab_counts[tab]   # counted with the tabs already
    page = paginator.get_page(request.GET.get("page"))
    if query and not request.GET.get("page") and not clicks.prefetching(request):
        insights.record_search(request, query, paginator.count)
    # A list about stock now (Out now, the In stock tab or filter) leads each card with an in-stock shop.
    if stock_now or tab == "in-stock" or (form.value("in_stock") and not tabs):
        in_stock_offers(page.object_list)
    week_lows = offers.week_low_map([p.pk for p in page.object_list])
    cards = [(product, offers.summarise(product, week_lows)) for product in page.object_list]

    filters_active = bool(
        game or product_type or (form.value("in_stock") and not tabs) or form.value("compared") or band or languages
        or sort not in (form.default_sort, "soonest")
    )
    # The Alert me form on the watchlist page, filled in with this list's game, type, language and price band.
    alert_url = ""
    if (fixed_game or game) is not None and mail.enabled():
        params = {"game": (fixed_game or game).slug}
        if product_type:
            params["type"] = product_type
        # No language filter is any language: lang= keeps the form from suggesting English.
        params["lang"] = languages[0] if languages else ""
        if band in BAND_TOPS:
            params["max"] = BAND_TOPS[band]
        alert_url = reverse("web:watchlist") + "?" + urlencode(params) + "#alert"
    # The choices in force, named on the folded Sort and filter button so the list says what it holds.
    chosen = []
    if sort not in (form.default_sort, "soonest"):
        chosen.append(dict(form.fields["sort"].choices).get(sort))
    for name, value in (("type", product_type), ("price", band), ("lang", form.value("lang"))):
        if value:
            chosen.append(dict(form.fields[name].choices).get(value))
    if form.value("compared"):
        chosen.append(form.fields["compared"].label)
    # A set link on a game page keeps the language, type and sort: what the visitor was looking for. Not
    # the price or 2+ shops, which could leave the set's page empty (the sets shown follow language and type).
    kept = {"lang": form.value("lang"), "type": product_type,
            "sort": sort if sort not in (form.default_sort, "soonest", "best") else ""}
    kept = {name: value for name, value in kept.items() if value}
    # Clear filters keeps the search words, and the tab only when the address named it.
    clear = {key: value for key, value in (("q", query), ("tab", asked)) if value}
    stock_tabs = stock_tab_links(request, tab_counts, opening) if tabs else []
    quick = quick_filters(request, pool, form, game) if query and not tabs else []
    # Only the tabs with something in them, and the one in view; one list left needs no tab row.
    shown_tabs = [item for item in stock_tabs if item["count"] or item["key"] == tab]
    context = {
        "alert_url": alert_url,
        "form": form,
        "query": query,
        "page": page,
        "cards": cards,
        "total": sum(tab_counts.values()) if tabs else paginator.count,
        "filters_active": filters_active,
        "chosen_filters": [label for label in chosen if label],
        "set_query": "?" + urlencode(kept) if kept else "",
        "clear_url": "?" + urlencode(clear),
        # A short list on a game or set page needs no sort or filters; a longer one folds them away.
        "show_filters": not tabs or filters_active or paginator.count >= FILTERS_FROM,
        "fold_filters": tabs,
        "no_matches": bool(query) and not filters_active and paginator.count == 0,
        "quick": quick,
        "quick_active": any(chip["active"] for chip in quick),
        "games": Game.objects.filter(is_active=True),
        "tab": tab,
        "tab_opening": opening,
        "tab_asked": asked,
        "tab_label": dict(STOCK_TABS).get(tab, ""),
        "stock_tabs": stock_tabs,
        "tab_row": shown_tabs if len(shown_tabs) > 1 else [],
        # One list and no tab row: a list other than In stock says what it is in a heading.
        "single_list": tabs and len(shown_tabs) == 1,
        "soonest": sort == "soonest",
    }
    context.update(template_context)
    if not (page.object_list or context.get("set_upcoming")):
        # Nothing listed: an alert for products like these would read as a promise about an empty list.
        context["alert_url"] = ""
    if tabs and request.GET.get("pane"):
        # One tab's list for the page's script, which slots it in beside the others. Never indexed.
        response = render(request, "web/includes/stock_pane.html", {**context, "pane": tab})
        response["X-Robots-Tag"] = "noindex"
        return response
    if structured is not None:
        context["structured_json"] = ld_json(structured(page, canonical=hub_is_canonical(request, page, opening)))
    return render(request, template, context)


QUICK_TYPES = 3   # product types a search's quick filters offer: the commonest in its results


def quick_filters(request, pool, form, chosen_game=None):
    """The quick filters above a search's results: English, In stock and the commonest product types, each a
    plain link that turns its filter on or, when on, off, keeping the search and the rest of the choices. Only
    choices that change the list: English while the results hold other languages too, In stock while some are
    not in stock, types while they hold more than one; one that is on always shows, so it can be taken off.
    ``pool`` is the search's results before any filter but the game. Three queries."""
    everything = Product.objects.filter(pk__in=pool.values("pk"))
    counts = everything.aggregate(total=Count("pk"), english=Count("pk", filter=Q(language=languages.ENGLISH)))
    if not counts["total"]:
        return []
    stocked = everything.in_stock().count()
    kinds, games = {}, set()
    for row in everything.order_by().values("product_type", "game__slug").annotate(n=Count("pk")):
        kinds[row["product_type"]] = kinds.get(row["product_type"], 0) + row["n"]
        games.add(row["game__slug"])
    # The game's own words for its types ("Booster bundles" for Pokémon) when the results are one game's.
    game_slug = chosen_game.slug if chosen_game else (next(iter(games)) if len(games) == 1 else None)
    lang, in_stock, kind = form.value("lang"), bool(form.value("in_stock")), form.value("type")

    def link(name, value):
        params = request.GET.copy()
        for gone in ("page", "pane"):
            params.pop(gone, None)
        if value:
            params[name] = value
        else:
            params.pop(name, None)
        return "?" + params.urlencode()

    chips = []
    if lang == "en" or 0 < counts["english"] < counts["total"]:
        chips.append({"label": dict(LANGUAGES)["en"], "active": lang == "en", "url": link("lang", "" if lang == "en" else "en")})
    if in_stock or 0 < stocked < counts["total"]:
        chips.append({"label": form.fields["in_stock"].label, "active": in_stock,
                      "url": link("in_stock", "" if in_stock else "on")})
    # The commonest first, then in the game's own order of types (Booster box, Elite Trainer Box, ...).
    place = {code: n for n, (code, _label) in enumerate(GAME_TYPES.get(game_slug, DEFAULT_TYPES))}
    common = sorted((code for code in kinds if code != Product.Type.OTHER),
                    key=lambda code: (-kinds[code], place.get(code, len(place)), code))
    shown = common[:QUICK_TYPES] if len(kinds) > 1 else []
    if kind and kind not in shown:
        shown.append(kind)
    for code in shown:
        words = plural_words(game_slug, [code])
        label = words[0] if words else type_label(game_slug, code)
        chips.append({"label": label[:1].upper() + label[1:], "active": kind == code,
                      "url": link("type", "" if kind == code else code)})
    return chips


def stock_tab_links(request, counts, opening=STOCK_TABS[0][0]):
    """[{key, label, count, url, pane_url}] for the stock tabs: each keeps the filters and drops the page
    number. The tab the page opens on (``opening``) needs no tab in its address; its list on its own does."""
    links = []
    for key, label in STOCK_TABS:
        params = request.GET.copy()
        for name in ("page", "pane", "tab", "in_stock"):
            params.pop(name, None)
        if key != opening:
            params["tab"] = key
        encoded = params.urlencode()
        params["tab"] = key
        params["pane"] = "1"
        links.append({"key": key, "label": label, "count": counts.get(key, 0), "url": f"?{encoded}" if encoded else "?",
                      "pane_url": f"?{params.urlencode()}"})
    return links


def home_lists(fresh=False, candidates=None):
    """Featured deals and their spares (home page) and trending (deals page), as (featured, trending, spares),
    cached because they read every priced product (web.listcache). ``fresh`` builds them again in place
    (web.warming); ``candidates`` is a shared_candidates() to pick from, when the Deals lists are being built too."""
    from catalogue.signals import HOME_CACHE_KEY

    return listcache.get_or_build(HOME_CACHE_KEY, lambda: build_home_lists(candidates), fresh=fresh,
                                  since=read_since(candidates))


def build_home_lists(candidates=None):
    featured, spares = with_list_fields(*featured_pick((candidates or deal_candidates)()))
    ids = [product.pk for product in pricing.popular(limit=8)] or list(
        Product.objects.active().in_stock().with_release()
        .order_by(F("release").desc(nulls_last=True)).values_list("pk", flat=True)[:8]
    )
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
    return featured, trending, spares


def deal_candidates():
    """Every product a shop has in stock, with its buyable offers (``product.offers``) and its name, for
    picking deals from. Only what picking needs: the prices of the few picked are loaded afterwards by
    with_list_fields, not worked out for every product in stock."""
    return Product.objects.active().in_stock().only("pk", "name").prefetch_related(offers.buyable_prefetch())


class SharedCandidates:
    """deal_candidates, read once however many callers ask: the home lists and the Deals lists both pick from
    them, so building both reads the offers once. ``read_at`` is when that read began (time.time()), or None
    before it: a list built from them dates from then (web.listcache.store), not from when its own build
    began, so a price change saved between the two builds leaves the second list stale."""

    def __init__(self):
        self.rows = None
        self.read_at = None

    def __call__(self):
        if self.rows is None:
            self.read_at = time.time()
            self.rows = list(deal_candidates())
        return self.rows


def shared_candidates():
    return SharedCandidates()


def read_since(candidates):
    """For listcache.get_or_build's ``since``: when the shared candidates were read, if they were."""
    return None if candidates is None else (lambda: getattr(candidates, "read_at", None))


def with_list_fields(*groups):
    """Each list of (candidate, summary) from deal_candidates, with the candidate swapped for the product as
    a list shows it (for_lists: game, set, prices, release), in one query for all of them. The offers and
    summary stay the ones the pick was made from. A product gone from the site since is left out."""
    wanted = {product.pk for rows in groups for product, _ in rows}
    full = Product.objects.for_lists().in_bulk(wanted)
    swapped = []
    for rows in groups:
        kept = []
        for candidate, summary in rows:
            product = full.get(candidate.pk)
            if product is None:
                continue
            product.offers = candidate.offers
            for offer in product.offers:
                offer.product = product
            kept.append((product, summary))
        swapped.append(kept)
    return swapped


FEATURED_SLOTS = 8
FEATURED_EARNING = 6   # three in four featured deals go to shops that pay a commission
FEATURED_SPARES = 2    # per side: qualifying deals rendered hidden, for the visitor's own games (interest.js)


def earns(listing):
    return listing.retailer.pays_commission


def featured_deals(products):
    """Good deals for the top of the home page, as (product, summary), three in four from shops that earn.
    The rows of featured_pick, without its spares."""
    return featured_pick(products)[0]


def featured_pick(products):
    """(rows, spares): the featured deals, and the next qualifying deals on each side, for the home page.

    The rows are the featured deals:

    Only deals with evidence qualify (offers.Summary.is_deal): a verified
    30-day low clearly under the month's average, or a saving of at least
    offers.DEAL_PERCENT on a next shop whose price is in line with the
    market. The strongest come first. Among those, deals whose
    cheapest shop pays a commission take up to six of the eight places and
    the best of the rest take the others; if either side is short, the other
    fills in. The page says that featured deals favour those shops. With
    FeaturedSettings.paying_only only those shops' deals are featured.
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
        if not summary.is_deal:
            continue
        good.append((product, summary))
    good.sort(key=lambda row: (-row[1].strength, row[0].name))
    paying = [row for row in good if earns(row[1].best)]
    # The owner's choice (Admin, Featured deals settings): only shops that pay, however few deals that leaves.
    other = [] if FeaturedSettings.current().paying_only else [row for row in good if not earns(row[1].best)]
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
    rows = rows[:FEATURED_SLOTS]
    # Spares pass the same tests and come next on their side. The page renders them hidden; in the browser,
    # interest.js lets a spare from the visitor's own game take the place of a shown deal from the same side
    # (earning for earning, other for other), so the three in four never changes and nothing is sent anywhere.
    spares = paying[len(picked_paying):][:FEATURED_SPARES] + other[len(picked_other):][:FEATURED_SPARES]
    return rows, spares


# Games shown first on the home page, in this order; the rest follow by size.
PINNED_GAMES = ["pokemon", "magic-the-gathering", "sports-cards", "one-piece", "yu-gi-oh", "lorcana"]


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
        .priced()
        .annotate(on_preorder=on_preorder)
        .filter(upcoming | recent | fresh)
        .annotate(tier=Case(When(upcoming, then=Value(0)), When(recent, then=Value(1)), default=Value(2),
                            output_field=IntegerField()))
    )


DROPS_ORDER = ("tier", F("release").desc(nulls_last=True), "-created_at", "name")


def home_drops_new(fresh=False):
    """The home page's Out now row, newest release first: the same products as Out now on the drops page (in
    stock; pre-orders are the Coming soon rows above it), so each one is behind its See all. Cached with the
    other lists (web.listcache)."""
    from catalogue.signals import NEW_CACHE_KEY

    def build():
        rows = in_stock_offers(list(out_now_queryset().prefetch_related(offers.buyable_prefetch())
                                    .order_by(*DROPS_ORDER)[:10]))
        for product in rows:
            product.best_offer = product.offers[0] if product.offers else None
        return rows

    return listcache.get_or_build(NEW_CACHE_KEY, build, fresh=fresh)


COMING_SOON_ON_HOME = 3   # the home page is the money page: only sets a visitor can pre-order now, and few


def coming_soon_queryset(game=None):
    """Announced sets still to come, in one query: dated today or later, or undated with a shop taking
    pre-orders and none selling it from stock, since an undated set a shop already sells is out. Each
    carries how many shops take pre-orders and the offer its row leads with (``best_*``): the cheapest
    pre-order, or when no shop takes pre-orders the cheapest shop already selling it from stock (a UK street
    date can come before the published one), so a row never says no pre-orders while a shop is selling it.
    The row's button goes to that listing through /go/.

    Only a current offer at a shop counts: active, checked within the stale window, its price not kept out,
    and never eBay or Amazon, whose pre-orders are resellers' guesses. The cheapest is a confirmed delivered
    price when any shop has one, else an item price shown as plus delivery, the order every list uses.
    """
    from catalogue.pricing import MARKETPLACES

    # A product with its own later date is not part of the set's drop (catalogue.drops.in_drop), so its
    # pre-order neither makes the set's price nor keeps a released set listed here, as on the drops page.
    in_drop = (Q(product__release_date__isnull=True) | Q(product__product_set__release_date__isnull=True)
               | Q(product__release_date__lte=F("product__product_set__release_date")))
    current = Listing.objects.filter(
        in_drop, product__product_set=OuterRef("pk"), product__is_active=True, is_active=True,
        retailer__is_active=True, last_checked__gte=stale_cutoff(), sanity__in=Listing.COUNTED,
    ).exclude(retailer__source_type__in=MARKETPLACES)
    preorders = current.filter(availability=Listing.Availability.PREORDER)
    best = (
        current.filter(availability__in=Listing.BUYABLE)
        .annotate(
            shown=Case(When(delivery_known=True, then=F("delivered_price")), default=F("price")),
            later=Case(When(availability=Listing.Availability.PREORDER, then=Value(0)), default=Value(1)),
        )
        .order_by("later", "-delivery_known", "shown", "pk")
    )
    shops = (
        preorders.order_by().values("product__product_set")
        .annotate(shops=Count("retailer", distinct=True)).values("shops")
    )
    # A set sold only at events or direct by the publisher is never coming soon to a UK shop.
    sets = ProductSet.objects.filter(game__is_active=True, not_in_shops=False)
    if game is not None:
        sets = sets.filter(game=game)
    today = timezone.localdate()
    return (
        sets.select_related("game")
        .annotate(
            has_preorder=Exists(preorders),
            in_stock=Exists(current.filter(availability=Listing.Availability.IN_STOCK)),
            preorder_count=Coalesce(Subquery(shops[:1]), Value(0)),
            best_id=Subquery(best.values("pk")[:1]),
            best_price=Subquery(best.values("shown")[:1]),
            best_known=Subquery(best.values("delivery_known")[:1]),
            best_slug=Subquery(best.values("product__slug")[:1]),
            best_product=Subquery(best.values("product__name")[:1]),
            best_shop=Subquery(best.values("retailer__name")[:1]),
            best_checked=Subquery(best.values("last_checked")[:1]),
        )
        .filter(Q(release_date__gte=today) | Q(release_date__isnull=True, has_preorder=True, in_stock=False))
        .order_by(F("release_date").asc(nulls_last=True), "name")
    )


def coming_soon_sets(game=None, limit=8, fresh=False):
    """Sets still to come, soonest first, for the New page (limit None), a game page or the home page.

    Cached with the other lists under 'web:coming-soon:v2', or ':<game>' for one game's (web.listcache), so
    every clear_list_caches marks them stale.
    """
    from catalogue.signals import COMING_SOON_CACHE_KEY

    key = COMING_SOON_CACHE_KEY if game is None else f"{COMING_SOON_CACHE_KEY}:{game.slug}"
    rows = listcache.get_or_build(key, lambda: list(coming_soon_queryset(game)), fresh=fresh)
    return rows if limit is None else rows[:limit]


COMING_SOON_RENDERED = 8   # rendered on the home page; the first COMING_SOON_ON_HOME show, the rest wait hidden


def home_coming_soon(fresh=False):
    """The home page's Coming soon rows: sets a visitor can pre-order now, most popular first
    (catalogue.popularity), COMING_SOON_RENDERED of them. Without script the first COMING_SOON_ON_HOME show;
    interest.js may bring the visitor's own games forward from the rest, in the browser only. Cached with the
    other lists, so the order is the same for everyone and costs one query a cache fill."""
    from catalogue.popularity import rank_sets
    from catalogue.signals import HOME_DROPS_CACHE_KEY

    def build():
        return rank_sets([row for row in coming_soon_sets(limit=None) if row.preorder_count])[:COMING_SOON_RENDERED]

    return listcache.get_or_build(HOME_DROPS_CACHE_KEY, build, fresh=fresh)


def interest_lexicon(games):
    """[[slug, words...], ...] for interest.js: each game's name, short name and search aliases, so a recent
    search or a viewed product's name can be matched to its game in the browser."""
    lexicon = []
    for game in games:
        words = [game.name, game.short_name, *game.search_aliases.split(",")]
        lexicon.append([game.slug, *dict.fromkeys(w.strip().lower() for w in words if w.strip())])
    return lexicon


def date_source_label(product_set):
    """Where a set's date came from, for 'Date from ...': a release source's name, or nothing when the
    owner set it, it predates the sources, or the source is a shop's title. Exclamation marks in a
    source's name are left out, as everywhere in the site's wording."""
    from catalogue.releases import BY_NAME

    source = BY_NAME.get(product_set.release_date_source)
    return source.label.replace("!", "") if source else ""


# The old Latest drops page's filters, carried over when /new/ sends a visitor to Out now.
NEW_PAGE_PARAMS = ("game", "type", "price", "lang", "sort", "compared", "in_stock", "page")


@require_GET
def latest_drops(request):
    """/new/ was the Latest drops page. It is now Out now on the drops page: a permanent redirect, so search
    engines move the old address's standing over. A bare /new/ goes to /drops/ itself; one with filters to Out
    now with those filters kept."""
    params = request.GET.copy()
    for name in list(params):
        if name not in NEW_PAGE_PARAMS:
            del params[name]
    target = reverse("web:drops")
    if params:
        params["show"] = "out"
        target += "?" + params.urlencode()
    return HttpResponsePermanentRedirect(target)


def home_types(limit=6):
    """The product types with the most listed products, for the Browse by product tiles: (code, label, count)."""
    rows = (
        Product.objects.active().exclude(product_type=Product.Type.OTHER)
        .values("product_type").annotate(n=Count("id")).order_by("-n", "product_type")[:limit]
    )
    return [(row["product_type"], type_label(None, row["product_type"]), row["n"]) for row in rows]


# The kinds of product whose box art says most about a game, best first, for its tile on the home page.
COVER_TYPES = ["booster_box", "elite_trainer_box", "collector_booster_box", "bundle", "collection_box", "booster_pack"]


def game_covers(games):
    """Attach ``cover`` to each game: its newest boxed product with a picture, for the game's tile. Two queries."""
    rank = Case(*[When(product_type=kind, then=Value(i)) for i, kind in enumerate(COVER_TYPES)],
                default=Value(len(COVER_TYPES)), output_field=IntegerField())
    newest_with_art = (
        Product.objects.filter(game=OuterRef("pk"), is_active=True).exclude(image="", image_url="")
        .annotate(kind_rank=rank, out=Coalesce("release_date", "product_set__release_date"))
        .order_by("kind_rank", F("out").desc(nulls_last=True), "-created_at")
        .values("pk")[:1]
    )
    ids = dict(Game.objects.filter(pk__in=[g.pk for g in games]).annotate(cover=Subquery(newest_with_art))
               .values_list("pk", "cover"))
    covers = Product.objects.in_bulk([pk for pk in ids.values() if pk])
    for game in games:
        game.cover = covers.get(ids.get(game.pk))
    return games


@require_GET
def home(request):
    """The money page kept short: search, the games, the visitor's own picks, deals worth having and new
    releases. Savings, price drops, restocks and trending live on the Deals page; every set on Games."""
    games = list(
        Game.objects.filter(is_active=True)
        .annotate(product_count=Count("products", filter=Q(products__is_active=True)))
        .filter(product_count__gt=0)
        .order_by("-product_count", "name")
    )
    rank = {slug: i for i, slug in enumerate(PINNED_GAMES)}
    games.sort(key=lambda g: (rank.get(g.slug, len(PINNED_GAMES)), -g.product_count, g.name))
    game_covers(games)
    last_checked = Listing.objects.live().aggregate(latest=Max("last_checked"))["latest"]
    featured, _, featured_spares = home_lists()
    return render(
        request,
        "web/home.html",
        {
            "games": games,
            "product_types": home_types(),
            "last_checked": last_checked,
            "shops_compared": pricing.shops_compared(),
            "featured": featured,
            "featured_spares": featured_spares,
            "featured_paying_only": FeaturedSettings.current().paying_only if featured else False,
            "latest": home_drops_new(),
            "coming_soon": home_coming_soon(),
            "coming_soon_shown": COMING_SOON_ON_HOME,
            "interest_games": interest_lexicon(games),
            "hide_header_search": True,
            "meta_full_title": text(
                request, "meta.home.title", site_name=settings.RIPRAPTOR_SITE_NAME
            ),
            "canonical_url": request.build_absolute_uri("/"),
            "structured_json": ld_json(site_json(request)),
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


# Sets listed under each game on the Games page, newest first; the rest are a tap away on the game's own page.
GAMES_PAGE_SETS = 8


@require_GET
def games(request):
    game_list = (
        Game.objects.filter(is_active=True)
        .annotate(product_count=Count("products", filter=Q(products__is_active=True)))
        .prefetch_related(
            Prefetch(
                "sets",
                # Newest first. Spelled out: a query with a count drops the model's own order.
                queryset=ProductSet.objects.annotate(
                    product_count=Count("products", filter=Q(products__is_active=True))
                ).filter(product_count__gt=0).order_by(F("release_date").desc(nulls_last=True), "name"),
            )
        )
    )
    heading = text(request, "browse.games.title")
    return render(
        request,
        "web/games.html",
        {"games": game_list, "heading": heading, "meta_title": heading, "sets_shown": GAMES_PAGE_SETS},
    )


def moved(request, target):
    """A permanent redirect to ``target`` with the request's query kept: an old address's standing in search
    engines moves to the new one, and a filtered list stays filtered."""
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(f"{target}?{query}" if query else target)


@require_GET
@cached_page
def game_detail(request, game_slug):
    if sports.moved_from(game_slug):
        return moved(request, reverse("web:game", args=[sports.GAME]))   # /games/football/ is Sports cards now
    game = get_object_or_404(Game, slug=game_slug, is_active=True)
    # Only the page as people land on it needs the numbers and the sets: a tab's list fetched by script shows
    # none of them.
    pane = bool(request.GET.get("pane"))
    # The sets people want most this week, newest first when equal, each with how many of its products are in
    # stock: a set everyone is after shows even when it has none, so they can see it and look. Newest first,
    # spelled out: a distinct query keeps the model's order only by chance.
    # Only sets with products in the language and type in view: a Korean list offers only Korean sets, and
    # counts only Korean products in stock.
    in_view = apply_languages(Product.objects.active().filter(game=game), [request.GET.get("lang", "")])
    if request.GET.get("type") in Product.Type.values:
        in_view = in_view.filter(product_type=request.GET["type"])
    sets = []
    if not pane:
        stocked = (in_view.in_stock().filter(product_set=OuterRef("pk")).order_by().values("product_set")
                   .annotate(n=Count("pk")).values("n"))
        sets = list(
            ProductSet.objects.filter(game=game, pk__in=in_view.values("product_set_id")).select_related("game")
            .annotate(in_stock=Coalesce(Subquery(stocked[:1], output_field=IntegerField()), Value(0)))
            # Undated sets (sports card lines) with the most in stock first, not by name.
            .order_by(F("release_date").desc(nulls_last=True), "-in_stock", "name")
        )
        wanted = popularity.set_demand([item.pk for item in sets])
        sets.sort(key=lambda item: -wanted.get(item.pk, 0))   # stable: newest first among equals
    words = hubs.Words(text, request)
    stats = hubs.HubStats() if pane else hubs.hub_stats(game=game)
    meta_title, meta_description = hubs.game_meta(words, game, stats)
    page_url = request.build_absolute_uri(game.get_absolute_url())
    crumbs = breadcrumbs_json(request, [(game.name, game.get_absolute_url())], page_url=page_url)
    return _browse(
        request,
        {
            "heading": game.name,
            "game": game,
            "sets": sets[:GAME_PAGE_SETS],
            "more_sets": len(sets) > GAME_PAGE_SETS,
            "intro": hubs.intro(words, stats, brief=True),
            "meta_title": meta_title,
            "meta_description": meta_description,
            "canonical_url": page_url,
            "coming_soon": coming_soon_sets(game),
        },
        base_queryset=Product.objects.for_lists().filter(game=game),
        fixed_game=game,
        tabs=True,
        structured=lambda page, canonical: [crumbs] + ([hubs.item_list_json(
            request, page_url, meta_title, meta_description, crumbs["@id"], page.object_list,
        )] if canonical else []),
    )


@require_GET
@cached_page
def set_detail(request, game_slug, set_slug):
    if sports.moved_from(game_slug):
        return moved(request, reverse("web:set", args=[sports.GAME, set_slug]))
    product_set = get_object_or_404(
        ProductSet.objects.select_related("game"),
        game__slug=game_slug,
        game__is_active=True,
        slug=set_slug,
    )
    game = product_set.game
    # A set still to come says when it is out and who said so; an undated one says so only while shops take
    # pre-orders, since an old set entered without a date is not still to come.
    upcoming = any(row.pk == product_set.pk for row in coming_soon_sets(game, limit=None))
    words = hubs.Words(text, request)
    stats = hubs.HubStats() if request.GET.get("pane") else hubs.hub_stats(product_set=product_set)
    out_later = bool(product_set.release_date and product_set.release_date >= timezone.localdate())
    release_shown = short_date(product_set.release_date) if out_later else ""
    meta_title, meta_description = hubs.set_meta(words, product_set, stats, upcoming, date=release_shown)
    # The line under the heading gives the release date, or says there is none yet, for a set still to come.
    intro_date = release_shown if upcoming else ""
    undated = upcoming and product_set.release_date is None
    date_source = date_source_label(product_set) if upcoming else ""
    facts = set_facts(request, product_set, upcoming, out_later, date_source,
                      dated_above=hubs.intro_gives_date(stats, date=intro_date, undated=undated))
    page_url = request.build_absolute_uri(product_set.get_absolute_url())
    crumbs = breadcrumbs_json(
        request, [(game.name, game.get_absolute_url()), (product_set.name, product_set.get_absolute_url())],
        page_url=page_url,
    )
    # A released set nobody has in stock or on pre-order: the closest products that are in stock, so a visitor
    # hunting it has somewhere to go.
    instead = []
    if stats.products and not stats.in_stock and not stats.preorder and not upcoming:
        instead = picks.instead(list(Product.objects.active().filter(product_set=product_set)[:SET_INSTEAD_FROM]))
    return _browse(
        request,
        {
            "heading": product_set.name,
            "game": game,
            "product_set": product_set,
            "set_upcoming": upcoming,
            "set_facts": facts,
            "instead": instead,
            # One short line, and the cheapest in stock as a small card of its own rather than a second sentence.
            "intro": hubs.intro(words, stats, date=intro_date, undated=undated, brief=True),
            "cheapest_card": stats.cheapest if stats.in_stock else None,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "canonical_url": page_url,
        },
        base_queryset=Product.objects.for_lists().filter(product_set=product_set),
        fixed_game=game,
        tabs=True,
        structured=lambda page, canonical: [crumbs] + ([hubs.item_list_json(
            request, page_url, meta_title, meta_description, crumbs["@id"], page.object_list,
        )] if canonical else []),
    )


def set_facts(request, product_set, upcoming, out_later, date_source, dated_above=False):
    """The small print under a set page's heading, in order: its code; for a set sold only at events or by
    the publisher its date and that it is not in UK shops; for a set still to come its date (or that none is
    announced) unless the line above gives it (``dated_above``), and where the date came from; otherwise
    when it was released."""
    facts = [product_set.code] if product_set.code else []
    day = short_date(product_set.release_date) if product_set.release_date else ""
    if product_set.not_in_shops:
        if day:
            facts.append(text(request, "new.coming_soon.out" if out_later else "browse.set.released", date=day))
        facts.append(text(request, "browse.set.not_in_shops"))
    elif upcoming:
        if not dated_above:
            facts.append(text(request, "new.coming_soon.out", date=day) if day else text(request, "new.coming_soon.tbc"))
        if day and date_source:
            facts.append(text(request, "new.coming_soon.source", source=date_source))
    elif day:
        facts.append(text(request, "browse.set.released", date=day))
    return facts


# JSON inside <script>: "<", ">" and "&" as \u escapes, so a name holding "</script>" (from a shop's title or
# the owner's spreadsheet) cannot end the block and run as script. JSON-LD readers decode them as the characters.
SCRIPT_ESCAPES = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}


def hub_is_canonical(request, page, opening=STOCK_TABS[0][0]):
    """Whether a game or set page is shown as its canonical address names it: the first page of the tab it
    opens on (``opening``, the first with products) with no filters. Only that view describes the page in
    structured data, since every other view shares its canonical address and @id but lists other products.
    Tracking parameters do not count."""
    params = {key: value for key, value in request.GET.items()
              if value and key not in ("page", "tab") and not key.startswith("utm_") and key not in ("gclid", "fbclid")}
    return page.number == 1 and not params and request.GET.get("tab", "") in ("", opening)


def ld_json(data):
    """``data`` as JSON safe to put inside a <script type="application/ld+json"> block."""
    return json.dumps(data).translate(SCRIPT_ESCAPES)


def breadcrumbs_json(request, crumbs, page_url=None):
    """schema.org BreadcrumbList for [(name, path), ...]. With ``page_url`` it carries an @id (the page's address
    and #breadcrumb), so the page's own WebPage block can point at it."""
    data = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": request.build_absolute_uri(path)}
            for i, (name, path) in enumerate(crumbs)
        ],
    }
    if page_url:
        data = {"@context": data["@context"], "@type": data["@type"], "@id": page_url + "#breadcrumb",
                "itemListElement": data["itemListElement"]}
    return data


# The site's own social accounts, as linked in the footer.
SOCIAL_PROFILES = (
    "https://www.instagram.com/ripraptor_uk/",
    "https://www.facebook.com/ripraptor",
    "https://www.tiktok.com/@ripraptoruk",
)


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
            "sameAs": list(SOCIAL_PROFILES),
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


MARKET_SEARCHES = {"amazon": amazon_search_url, "ebay": ebay_search_url}


def search_link(product, market, tracked):
    """Our address for a marketplace search button, or None when the button is not shown."""
    return f'{reverse("web:go_search", args=[product.slug, market])}?b={market}' if tracked else None


def product_gone(request, gone):
    """410 Gone for a product page tidy_catalogue took down: search engines drop it quickly, and a visitor
    gets its set and game, while they are on the site, and the search box."""
    game = gone.game if gone.game.is_active else None
    product_set = gone.product_set if game is not None and gone.product_set_id else None
    heading = text(request, "errors.410.title", product=gone.name)
    return render(request, "web/product_gone.html", {
        "gone": gone, "gone_game": game, "gone_set": product_set, "heading": heading,
        "meta_title": heading, "noindex": True,
    }, status=410)


@require_GET
def product_detail(request, slug):
    product = Product.objects.active().select_related("game", "product_set").filter(slug=slug).first()
    if product is None:
        alias = ProductAlias.objects.filter(slug=slug, product__is_active=True).select_related("product").first()
        if alias is not None:
            return HttpResponsePermanentRedirect(alias.product.get_absolute_url())
        gone = RemovedProduct.objects.filter(slug=slug).select_related("game", "product_set__game").first()
        if gone is None:
            raise Http404("No product with that address.")
        return product_gone(request, gone)
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
    # Open draws at shops we read: listed with the prices under their own label, never as stock.
    lotteries = [
        listing for listing in unavailable
        if listing.availability == Listing.Availability.LOTTERY and listing.is_active and listing.retailer.is_active
        and not listing.is_stale and listing.sanity != Listing.Sanity.EXCLUDED
    ]
    cheapest = current[0] if current else None
    shop_count = len({listing.retailer_id for listing in current})
    # Every UK shop listing it, in stock or not: with one, its price has nothing to be compared with, so no
    # badge or Price check calls it a good one (offers.summarise, fair_price.verdict).
    shops_listing = len({listing.retailer_id for listing in listings if listing.sanity != Listing.Sanity.EXCLUDED})
    product.listing_count = shops_listing
    product.offers = current
    summary = offers.summarise(product, offers.week_low_map([product.pk]))

    in_stock_count = sum(1 for l in current if l.availability == Listing.Availability.IN_STOCK)
    preorder_count = sum(1 for l in current if l.availability == Listing.Availability.PREORDER)
    last_checked = max((l.last_checked for l in listings), default=None)
    alerts_preorder = alerts.preorder_alerts_on()
    alert_hides = Listing.BUYABLE if alerts_preorder else [Listing.Availability.IN_STOCK]
    # Once pre-order emails are on, a shop pre-order hides the form too: the alert would be sent at once.
    alerts_on = mail.enabled() and not any(
        l.availability in alert_hides and l.retailer.source_type not in alerts.MARKETPLACES for l in current
    )
    # What a price alert compares with (alerts.price_listing), from the listings already in hand.
    track_listing = min(
        (l for l in current if l.availability == Listing.Availability.IN_STOCK and l.delivery_known
         and l.retailer.source_type not in alerts.NOT_PRICE_ALERTED),
        key=lambda l: l.delivered_price, default=None,
    )

    today = timezone.localdate()
    days = settings.RIPRAPTOR_HISTORY_DAYS
    chart = price_chart(pricing.history(product, days=days, today=today), days, today)
    month_ago = pricing.previous_price_map([product.pk], days=30, today=today).get(product.pk)
    last_known = None if cheapest else pricing.last_known_price(product)
    restocks = pricing.restock_summary(product) if listings else None
    # Price check: today's cheapest delivered price against our own daily record, which counts only UK shops
    # (not eBay or Amazon searches). None without enough days, when the delivery cost is unknown, or when the
    # cheapest price is doubtful: it may not be this product.
    uk_shops = len({listing.retailer_id for listing in listings if listing.sanity != Listing.Sanity.EXCLUDED
                    and listing.retailer.source_type not in pricing.MARKETPLACES})
    fair = (fair_price.verdict(product, cheapest.delivered_price, today, shops=uk_shops)
            if cheapest and cheapest.delivery_known and cheapest.sanity != Listing.Sanity.DOUBTFUL else None)
    # The owner: never "High price" beside a shop that pays a commission, whatever the price. The check is left
    # off, not softened, so nothing on the page calls the price better than it is; the Terms page says so.
    if fair is not None and fair.band == fair_price.HIGH_PRICE and cheapest.retailer.pays_commission:
        fair = None

    # Nothing to buy here: the closest products shops have in stock, so a visitor hunting a sold-out product
    # has somewhere to go.
    instead = [] if cheapest else picks.instead([product])

    related = []
    if product.product_set_id:
        related = list(
            Product.objects.for_lists()
            .filter(product_set_id=product.product_set_id)
            .exclude(pk=product.pk)
            .order_by("name")[:6]
        )

    page_url = request.build_absolute_uri(product.get_absolute_url())
    structured = {
        "@context": "https://schema.org",
        "@type": "Product",
        "@id": page_url + "#product",
        "name": product.name,
        "url": page_url,
        "category": product.type_name,
        "brand": {"@type": "Brand", "name": product.game.name},
    }
    if product.image_src:
        structured["image"] = request.build_absolute_uri(images.sized_image_url(product.image_src, images.SHARE_WIDTH))
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
    words = hubs.Words(text, request)
    # Titles and descriptions name the set's code when the product's name does not, since searchers type it.
    search_name = hubs.name_with_code(product)
    release = product.effective_release_date
    release_shown = short_date(release) if release and release > today else ""
    if cheapest and cheapest.delivery_known:
        meta_values = {
            "product": search_name,
            "price": gbp(cheapest.delivered_price),
            "retailer": cheapest.retailer.name,
            "count": shop_count,
            "shops": words.count("meta.shops", shop_count),
        }
        # "Pre-order" in the title only while the product is not out: the quoted price is a pre-order, no shop
        # has it in stock and the release date is unknown or still to come. A shop's stale pre-order flag on a
        # product already out would otherwise tell searchers it is not out yet.
        preorder = (cheapest.availability == Listing.Availability.PREORDER and not in_stock_count
                    and (release is None or release > today))
        meta_title = hubs.product_title(words, search_name, meta_values["price"], preorder,
                                        retailer=meta_values["retailer"], count=shop_count)
        # "Cheapest today" only when the price quoted is in stock. When the cheapest is a pre-order, the
        # description says so, and names the cheapest shop with it in stock if there is one.
        stock_offer = next((listing for listing in current if listing.availability == Listing.Availability.IN_STOCK
                            and listing.delivery_known), None)
        if cheapest.availability == Listing.Availability.IN_STOCK:
            key = "meta.product.description_priced"
        elif stock_offer is not None:
            key = "meta.product.description_preorder_stock"
            meta_values.update(stock_price=gbp(stock_offer.delivered_price), stock_retailer=stock_offer.retailer.name)
        elif release_shown:
            key = "meta.product.description_preorder_dated"
            meta_values["date"] = release_shown
        else:
            key = "meta.product.description_preorder"
        meta_description = hubs.fit_description(hubs.tidy_stops(text(request, key, **meta_values)))
    else:
        meta_title = hubs.fitted(text(request, key, product=search_name)
                                 for key in ("meta.product.title", "meta.product.title_short"))
        meta_title = meta_title or text(request, "meta.product.title_short", product=search_name)
        if release_shown:
            # "Prices appear once shops list it" only when no shop lists it at all. A product listed but sold
            # out, or whose cheapest price has no known delivery cost, gets a dated line that does not deny it.
            key = "meta.product.description_dated_listed" if listings else "meta.product.description_dated"
            meta_description = hubs.fit_description(text(request, key, product=search_name, date=release_shown))
        else:
            meta_description = hubs.fit_description(text(request, "meta.product.description", product=search_name))
    breadcrumbs = breadcrumbs_json(request, crumbs, page_url=page_url)
    # The page itself: a page about one item, whose main entity is the Product above (when there is one).
    item_page = {
        "@context": "https://schema.org",
        "@type": "ItemPage",
        "@id": page_url + "#webpage",
        "url": page_url,
        "name": meta_title,
        "description": meta_description,
        "breadcrumb": {"@id": breadcrumbs["@id"]},
    }
    if product_json:
        item_page["mainEntity"] = {"@id": structured["@id"]}
    return render(
        request,
        "web/product.html",
        {
            "product": product,
            # A product with no prices at all carries no Product block: Product data without offers is invalid.
            "structured_json": ld_json(product_json + [breadcrumbs, item_page]),
            "meta_type": "product",
            "meta_image": (request.build_absolute_uri(images.sized_image_url(product.image_src, images.SHARE_WIDTH))
                           if product.image_src else ""),
            "current": current,
            "unavailable": [listing for listing in unavailable if listing not in lotteries],
            "lotteries": sorted(lotteries, key=lambda listing: listing.delivered_price),
            # ?report=<listing> opens the report form under the shops for that offer; ?report_state= says how
            # sending went (report_listing).
            "report_listing": next((listing for listing in listings if str(listing.pk) == request.GET.get("report")), None),
            "report_state": request.GET.get("report_state") if request.GET.get("report_state") in REPORT_STATES else "",
            "cheapest": cheapest,
            "shop_count": shop_count,
            "summary": summary,
            "in_stock_count": in_stock_count,
            "preorder_count": preorder_count,
            "last_checked": last_checked,
            "has_listings": bool(listings),
            # The guides linked from here (guides.state, from the cache): the parent's guide beside a price, the
            # scams guide on a sold-out page and in a High price check.
            "guide_door": guide_doors.door("buying" if cheapest else "sold_out") if listings else None,
            "scams_door": guide_doors.door("sold_out"),
            "chart": chart,
            "fair": fair,
            "fair_word": text(request, f"product.fair.verdict_{fair.key}") if fair else "",
            "history_days": days,
            "month_ago": month_ago,
            "last_known": last_known,
            "restocks": restocks,
            "alerts_on": alerts_on,
            "alerts_preorder": alerts_preorder,
            "alert_state": request.GET.get("alert") if request.GET.get("alert") in ALERT_STATES else "",
            # Track price: a price alert, next to Save wherever the back-in-stock form is not shown, and beside it
            # when only eBay has it in stock: a price alert follows eBay's price (alerts.NOT_PRICE_ALERTED).
            "track_on": mail.enabled() and bool(current) and (not alerts_on or track_listing is not None),
            "track_now": track_listing.delivered_price if track_listing else None,
            "track_days": alerts.TRACK_DAYS,
            "track_default": alerts.TRACK_DEFAULT_DAYS,
            "track_state": request.GET.get("track") if request.GET.get("track") in TRACK_STATES else "",
            "share": share_links(request, product, page_url),
            # Through /go/search/ so only a person's click carries our tag or campaign (go_search).
            "amazon_search": search_link(product, "amazon", amazon_search_url(product, listings)),
            "ebay_search": search_link(product, "ebay", ebay_search_url(product, listings)),
            "related": related,
            "instead": instead,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "canonical_url": request.build_absolute_uri(product.get_absolute_url()),
        },
    )


def save_click(request, reason, product, retailer, listing=None):
    """Save a click to a shop: the page it came from, the button pressed and the day's visitor token
    (catalogue/clicks.py), and whether it is suspect. A robot that says so is not worth a row; every other
    direct hit is kept so Insights can show them. Saved on a best effort basis: a database too busy to save
    it never stops the visitor's trip to the shop."""
    if reason in ("robot", "method"):
        return None
    saved = None
    with pricing.drop_if_locked("Click"):
        saved = pricing.retry_locked(lambda: OutboundClick.objects.create(
            listing=listing, product=product, retailer=retailer, suspect=bool(reason),
            source=clicks.page_of(request), button=clicks.button_of(request),
            visitor=insights.visitor_token(request, timezone.localdate()),
        ))
    return saved


@require_safe
def go(request, listing_id):
    """Send the visitor to the retailer. A click from a person on our pages is counted and carries our
    tracking; anything else (no link from our pages, a robot, a prefetch, too many from one connection)
    still reaches the shop, at its plain address, and is saved as suspect (catalogue/clicks.py)."""
    listing = get_object_or_404(
        Listing.objects.live().select_related("retailer", "product"),
        pk=listing_id,
        product__is_active=True,
    )
    reason = clicks.suspect_reason(request)
    saved = save_click(request, reason, listing.product, listing.retailer, listing)
    if reason:
        # An affiliate click address with no shop address inside goes to the shop's front page instead.
        target = clicks.plain_url(listing.url) or listing.retailer.website or listing.product.get_absolute_url()
    else:
        # The click's number goes where the network reports it back, so a sale can be matched to it.
        number = saved.pk if saved is not None else ""
        target = clicks.with_click_ref(listing.retailer.outbound_url(listing.url, click=number), number)
    response = HttpResponseRedirect(target)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_safe
def go_search(request, slug, market):
    """Send the visitor to an Amazon or eBay search for the product. Like go(): a person on our pages gets our
    tag or campaign, anything else the same search without it. Saved like go()'s clicks, against the site's
    Amazon or eBay shop when there is one (no listing was clicked); without one there is nothing to count it
    against."""
    builder = MARKET_SEARCHES.get(market)
    if builder is None:
        raise Http404("No such marketplace.")
    product = get_object_or_404(Product.objects.active().select_related("game"), slug=slug)
    # No listings: the page decided whether to show the button; a link from an older page still works.
    tracked = builder(product, ())
    if tracked is None:
        raise Http404("No marketplace search.")
    reason = clicks.suspect_reason(request)
    source_type = Retailer.Source.AMAZON if market == "amazon" else Retailer.Source.EBAY
    retailer = Retailer.objects.filter(source_type=source_type).order_by("-is_active", "pk").first()
    if retailer is not None:
        save_click(request, reason, product, retailer)
    target = clicks.plain_url(tracked) if reason else tracked
    response = HttpResponseRedirect(target)
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
            # The keyring offer's rules and data use, while it runs or anyone's details are still held.
            # A friend's referral and address can outlive the referrer's details, so either counts.
            "refer_terms": ReferralSettings.current().is_on or Referrer.objects.exists() or Referral.objects.exists(),
            "featured_paying_only": FeaturedSettings.current().paying_only,
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
            # The line under the main price, for the cheapest offer: whose price was checked, and when.
            "checked_line": text(request, "product.cheapest.checked", retailer=listing.retailer.name,
                                 time=ago(listing.last_checked)),
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
    picture = images.responsive(product.image_src, "swipe")
    data = {
        "id": product.pk,
        "name": product.name,
        "url": product.get_absolute_url(),
        "meta": f"{product.game.display_short} · {product.type_name}",
        "type": product.product_type,
        # The deck card's picture at the size it is shown; ``image`` alone is also the saved list's thumbnail.
        "image": picture["src"],
        "srcset": picture["srcset"],
        "sizes": picture["sizes"],
        "full": picture["full"],
        "price": shown_text(request, best) if best else "",
        "retailer": best.retailer.name if best else "",
        "buy": f"{best.get_outbound_url()}?b=swipe" if best else "",
        "game": product.game.display_short,
        "checked": ago(best.last_checked) if best else "",
        "badge": summary.badge or "",
        "second": {"retailer": summary.second.retailer.name, "price": shown_text(request, summary.second)} if summary.second else None,
        "saving": gbp(summary.saving) if summary.saving else "",
        "percent": summary.percent or 0,
    }
    return data


def deck_products(request):
    """The deck's products under the page's game, language and sort choices."""
    products = Product.objects.for_lists().priced()
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
    chosen = request.GET.getlist("lang")[:1]   # the page offers one language at a time
    game = Game.objects.filter(slug=request.GET.get("game", ""), is_active=True).first()
    scope = Product.objects.for_lists().priced()
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


def deals_lists(fresh=False, candidates=None):
    """Biggest savings, lowest in 90 days, price drops and the restock log for the deals page, cached like the
    home lists.
    ``fresh`` and ``candidates`` as for home_lists."""
    from catalogue.signals import DEALS_CACHE_KEY

    def build():
        savings, = with_list_fields(offers.biggest_savings((candidates or deal_candidates)(), limit=30))
        return {
            "savings": savings,
            "drops": list(pricing.price_drops(limit=12)),
            "lowest": fair_price.at_lowest(limit=12),
            "restock_log": pricing.restock_log(days=7),
        }

    return listcache.get_or_build(DEALS_CACHE_KEY, build, fresh=fresh, since=read_since(candidates))


@require_GET
def deals(request):
    candidates = shared_candidates()   # read only if a list has to be built, and then once for both
    lists = deals_lists(candidates=candidates)
    last_checked = Listing.objects.live().aggregate(latest=Max("last_checked"))["latest"]
    return render(
        request,
        "web/deals.html",
        {
            **lists,
            # Only what a shop has in stock: this page is for buying, and a click count alone is no deal.
            "trending": [product for product in home_lists(candidates=candidates)[1] if product.best_offer],
            "last_checked": last_checked,
            "meta_title": text(request, "meta.deals.title"),
            "meta_description": text(request, "meta.deals.description"),
            "canonical_url": request.build_absolute_uri(reverse("web:deals")),
            "structured_json": ld_json(breadcrumbs_json(request, [(text(request, "deals.title"), reverse("web:deals"))])),
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


class SearchAlertForm(forms.Form):
    """The watchlist page's Alert me form: what to be emailed about, and where. Labels come from the registry."""

    game = forms.ModelChoiceField(queryset=Game.objects.filter(is_active=True), to_field_name="slug", empty_label=None)
    type = forms.ChoiceField(required=False)
    lang = forms.ChoiceField(required=False)
    max = forms.CharField(required=False, max_length=12)
    email = forms.EmailField(max_length=254)

    def __init__(self, *args, request=None, **kwargs):
        from catalogue.types import DEFAULT

        kwargs.setdefault("auto_id", "alert-%s")
        super().__init__(*args, **kwargs)
        self.request = request
        words = {name: text(request, f"watchlist.alert.{name}") for name in ("game", "type", "lang", "max", "email")}
        for name, label in words.items():
            self.fields[name].label = label
        self.fields["type"].choices = [("", text(request, "watchlist.alert.any_type"))] + [
            (code, label) for code, label in DEFAULT if code != "other"
        ]
        self.fields["lang"].choices = [("", text(request, "watchlist.alert.any_lang"))] + LANGUAGES
        self.fields["game"].label_from_instance = lambda game: game.display_short

    def clean_max(self):
        from catalogue.search_alerts import MAX_PRICE, MIN_PRICE

        raw = self.cleaned_data.get("max", "").replace("£", "").replace(",", "").strip()
        if not raw:
            return None
        try:
            value = Decimal(raw).quantize(Decimal("0.01"))
        except InvalidOperation:
            value = None
        if value is None or not value.is_finite() or not MIN_PRICE <= value <= MAX_PRICE:
            raise forms.ValidationError(text(self.request, "watchlist.alert.price_range", low=f"£{MIN_PRICE}",
                                             high=f"£{MAX_PRICE:,.0f}"))
        return value


class ManageAlertsForm(forms.Form):
    """The Your alerts box: the address to email a link to its alerts. Its label is in the template."""

    email = forms.EmailField(max_length=254)


# What the Your alerts box says after it is sent. "sent" is the answer for every address, with alerts or not.
MANAGE_STATES = ("sent", "busy")
SEARCH_ALERT_STATES = ("sent", "busy", "failed")
# Prefill from a browse page's filters: its price band becomes the band's top as the most to pay.
BAND_TOPS = {"0-10": "10", "10-25": "25", "25-50": "50", "50-100": "100"}


def search_alert_prefill(request):
    """Initial values for the Alert me form from the address (a browse page's link, or after asking)."""
    initial = {"lang": "en"}
    for name in ("game", "type", "lang", "max"):
        if name in request.GET:
            initial[name] = request.GET.get(name, "")[:20]
    return initial


def search_alert_described(values, request):
    """The plain-English summary for cleaned or prefilled values, or "" when they do not make one."""
    from catalogue import search_alerts

    game = values.get("game")
    if isinstance(game, str):
        game = Game.objects.filter(is_active=True, slug=game).first()
    if game is None:
        return ""
    product_type = values.get("type") or ""
    language = values.get("lang") or ""
    if product_type not in [""] + list(Product.Type.values) or language not in [""] + [code for code, _ in LANGUAGES]:
        return ""
    max_price = values.get("max")
    if isinstance(max_price, str):
        try:
            max_price = Decimal(max_price) if max_price else None
        except InvalidOperation:
            return ""
        if max_price is not None and not (search_alerts.MIN_PRICE <= max_price <= search_alerts.MAX_PRICE):
            return ""
    return search_alerts.summary(game, product_type, language, max_price, store=copy.for_request(request))


def watchlist_page(request, alert_form=None, status=200, manage_form=None):
    wanted = slugs_in(request, WATCHLIST_MAX) or [
        slug for slug in request.POST.get("p", "").split(",") if slug
    ][:WATCHLIST_MAX]
    rows = []
    if wanted:
        products = products_for_slugs(wanted)
        week_lows = offers.week_low_map([p.pk for p in products])
        rows = [(product, offers.summarise(product, week_lows)) for product in products]
        if not insights.is_bot(request) and request.method == "GET":
            insights.bump_many(DailyPageView, DailyPageView.Kind.WATCHED, [p.slug for p in products])
    context = {
        "rows": rows,
        "meta_title": text(request, "meta.watchlist.title"),
        "noindex": True,
        "alerts_on": mail.enabled(),
        "list_slugs": ",".join(wanted),
    }
    if context["alerts_on"]:
        state = request.GET.get("alert", "")
        state = state if state in SEARCH_ALERT_STATES else ""
        prefill = search_alert_prefill(request)
        if alert_form is None:
            alert_form = SearchAlertForm(initial=prefill, request=request)
        described = search_alert_described(alert_form.cleaned_data if alert_form.is_bound and alert_form.is_valid()
                                           else prefill, request)
        manage_state = request.GET.get("manage", "")
        context.update({
            "alert_form": alert_form,
            "alert_state": state,
            "alert_summary": described,
            "alert_again_url": reverse("web:watchlist") + (f"?p={','.join(wanted)}" if wanted else "") + "#alert",
            "manage_form": manage_form or ManageAlertsForm(),
            "manage_state": manage_state if manage_state in MANAGE_STATES else "",
            "manage_again_url": reverse("web:watchlist") + (f"?p={','.join(wanted)}" if wanted else "") + "#manage",
        })
    return render(request, "web/watchlist.html", context, status=status)


@require_GET
def watchlist(request):
    """The products a visitor saved, named in the address, with live prices, and the Alert me form.

    The list lives in the browser and in this page's address, never on the
    server, so the page works bookmarked, pasted into a chat, or with
    JavaScript off. The script fills in what each was saved at.
    """
    return watchlist_page(request)


@require_POST
def search_alert_ask(request):
    """Take the Alert me form. Without JavaScript a good request goes back to the watchlist page, which says
    "Check your inbox" with the alert read back; one with a mistake shows the page again with it marked. With
    JavaScript the form asks for JSON and shows the answer in place. The answer is the same for every address."""
    from catalogue import search_alerts

    wants_json = "application/json" in request.headers.get("Accept", "")
    if request.POST.get("website") or not mail.enabled():
        return HttpResponseRedirect(reverse("web:watchlist"))   # the hidden field is only ever filled by robots
    form = SearchAlertForm(request.POST, request=request)
    if not form.is_valid():
        if wants_json:
            return JsonResponse({"state": "invalid"}, status=400)
        return watchlist_page(request, alert_form=form, status=400)
    data = form.cleaned_data
    if not search_alerts.address_allowed(insights.client_ip(request)):
        state = "busy"
    else:
        try:
            state = search_alerts.ask(data["email"], data["game"], data["type"], data["lang"], data["max"])
        except mail.MailError:
            state = "failed"
    summary = search_alert_described(data, request)
    if wants_json:
        if state == "sent":
            title, message = text(request, "watchlist.alert.done.title"), text(request, "watchlist.alert.done.body", summary=summary)
        else:
            title = text(request, "watchlist.alert.problem")
            message = text(request, "watchlist.alert.busy" if state == "busy" else "alerts.form.failed")
        return JsonResponse({"state": state, "title": title, "message": message, "summary": summary})
    # lang goes in even when empty: without it the page would suggest English again and read it back wrongly.
    params = {"alert": state, "game": data["game"].slug, "lang": data["lang"] or ""}
    if data["type"]:
        params["type"] = data["type"]
    if data["max"] is not None:
        params["max"] = str(data["max"])
    slugs = [slug for slug in request.POST.get("p", "").split(",") if slug][:WATCHLIST_MAX]
    if slugs:
        params = {"p": ",".join(slugs), **params}
    return HttpResponseRedirect(reverse("web:watchlist") + "?" + urlencode(params, safe=",") + "#alert")


@require_GET
def search_alert_confirm(request, token):
    from catalogue import search_alerts

    alert = search_alerts.confirm(token)
    if alert is None:
        heading, message = text(request, "alerts.gone.title"), text(request, "watchlist.alert.gone.body")
    elif alert.confirmed_at is None:   # the address already has the most confirmed alerts we keep
        heading = text(request, "watchlist.alert.problem")
        message = text(request, "watchlist.alert.full.body", count=search_alerts.MAX_PER_EMAIL)
    else:
        heading = text(request, "watchlist.alert.confirmed.title")
        message = text(request, "watchlist.alert.confirmed.body",
                       summary=search_alerts.alert_summary(alert, store=copy.for_request(request)))
    return render(request, "web/search_alert_done.html", {"heading": heading, "message": message,
                                                          "meta_title": heading, "noindex": True})


@require_GET
def search_alert_stop(request, token):
    from catalogue import search_alerts

    search_alerts.stop(token)
    heading = text(request, "alerts.stopped.title")
    return render(request, "web/search_alert_done.html", {"heading": heading, "message": text(request, "watchlist.alert.stopped.body"),
                                                          "meta_title": heading, "noindex": True})


# Your alerts: one place to see and stop every email alert (catalogue/manage_alerts.py) ---------------

def private_page(response):
    """The Your alerts page names an address: never kept by a cache or indexed, whatever robots.txt says."""
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_POST
def manage_ask(request):
    """Take the Your alerts box and email a link to the address if it has alerts. The answer is the same
    whether it has or not. Without JavaScript the visitor goes back to the watchlist page, which says so; an
    address that is not one shows the page again with it marked. With JavaScript (search-alert.js) the box asks
    for JSON and shows the answer in place."""
    from catalogue import manage_alerts

    wants_json = "application/json" in request.headers.get("Accept", "")
    if request.POST.get("website") or not mail.enabled():
        return HttpResponseRedirect(reverse("web:watchlist"))   # the hidden field is only ever filled by robots
    form = ManageAlertsForm(request.POST)
    if not form.is_valid():
        if wants_json:
            return JsonResponse({"state": "invalid"}, status=400)
        return watchlist_page(request, manage_form=form, status=400)
    if manage_alerts.address_allowed(insights.client_ip(request)):
        manage_alerts.ask(form.cleaned_data["email"])
        state = "sent"
    else:
        state = "busy"
    if wants_json:
        if state == "sent":
            return JsonResponse({"state": state, "title": text(request, "watchlist.manage.done.title"),
                                 "message": text(request, "watchlist.manage.done.body")})
        return JsonResponse({"state": state, "message": text(request, "watchlist.manage.busy")})
    params = {"manage": state}
    slugs = [slug for slug in request.POST.get("p", "").split(",") if slug][:WATCHLIST_MAX]
    if slugs:
        params = {"p": ",".join(slugs), **params}
    return HttpResponseRedirect(reverse("web:watchlist") + "?" + urlencode(params, safe=",") + "#manage")


def manage_groups(request, email):
    """The Your alerts page's groups, in plain words and in this order: back in stock, Track price, drop
    reminders, search alerts, each [{"key", "label", "rows"}] and left out when empty. A row is {"kind", "pk",
    "name", "url", "details", "waiting"}. Three queries however many alerts there are."""
    from catalogue import manage_alerts, search_alerts
    from catalogue.drops import Drop, month_start

    found = manage_alerts.find(email)
    store = copy.for_request(request)
    today = timezone.localdate()

    def row(kind, alert, name, url, details=()):
        return {"kind": kind, "pk": alert.pk, "name": name, "url": url, "details": [d for d in details if d],
                "waiting": alert.confirmed_at is None}

    offered = [days for days, _label in alerts.TRACK_DAYS]

    back, track = [], []
    for alert in found["stock"]:
        product = alert.product
        if alert.max_price is None:
            back.append(row("stock", alert, product.name, product.get_absolute_url()))
            continue
        details = [text(request, "watchlist.manage.price", price=gbp(alert.max_price))]
        if alert.confirmed_at:
            # It looks for the time chosen from the confirmation, and never past the six months any alert is kept.
            ends = alerts.watch_ends(alert) or alert.confirmed_at + timedelta(days=alerts.CONFIRMED_DAYS)
            details.append(text(request, "watchlist.manage.until", date=short_date(ends)))
        # Change opens with the alert's own price and the nearest of the times offered.
        track.append({**row("stock", alert, product.name, product.get_absolute_url(), details),
                      "change": {"price": alert.max_price, "days": min(offered, key=lambda days: abs(
                          days - (alert.watch_days or alerts.CONFIRMED_DAYS)))}})
    reminders = []
    for alert in found["drop"]:
        product_set = alert.product_set
        day = product_set.release_date
        month = month_start(alert.set_month) if day is None and alert.set_month else None
        drop = drop_words(request, Drop(product_set=product_set, day=day, month=month), today)
        reminders.append(row("drop", alert, product_set.name, reverse("web:drops") + f"#drop-{product_set.pk}",
                             [product_set.game.display_short, drop.date_text, drop.when_text]))
    searches = [row("search", alert, search_alerts.alert_summary(alert, store), search_alerts.list_path(alert))
                for alert in found["search"]]
    groups = [("stock", back), ("price", track), ("drops", reminders), ("search", searches)]
    return [{"key": key, "label": text(request, f"watchlist.manage.group.{key}"), "rows": rows}
            for key, rows in groups if rows]


@require_http_methods(["GET", "POST"])
def alerts_manage(request, token):
    """The page a Your alerts link opens: every alert for the link's address, each with a Stop button, and Stop
    all. A stop is a POST with the CSRF token to this same address, and deletes an alert only when it belongs
    to the link's address. It then comes back here with a short line saying what happened. An expired or
    altered link shows the box for a new one. A private link: no third-party script (context_processors), kept
    out of search engines (robots.txt, noindex) and never cached."""
    from catalogue import manage_alerts

    email = manage_alerts.email_for(token)
    if email is None:
        heading = text(request, "watchlist.manage.expired.title")
        return private_page(render(request, "web/alerts_manage.html", {
            "expired": True, "alerts_on": mail.enabled(), "manage_form": ManageAlertsForm(),
            "heading": heading, "meta_title": heading, "noindex": True, "robots": "noindex, nofollow",
        }))
    here = reverse("web:alerts_manage", args=[token])
    if request.method == "POST" and request.POST.get("change"):
        return HttpResponseRedirect(here + change_alert(email, request.POST))
    if request.method == "POST":
        choice = request.POST.get("stop", "")
        if choice == "all":
            done = "all" if manage_alerts.stop_all(email) else ""
        else:
            kind, _, pk = choice.partition("-")
            done = "one" if manage_alerts.stop(email, kind, pk) else ""
        return HttpResponseRedirect(here + (f"?done={done}" if done else ""))
    done = request.GET.get("done", "")
    heading = text(request, "watchlist.manage.title")
    done_keys = {"one": "watchlist.manage.stopped.one", "all": "watchlist.manage.stopped.all",
                 "changed": "watchlist.manage.changed"}
    # A change that was refused opens that alert's Change again, saying why.
    refused = {"price": "alerts.track.price_invalid", "under": "watchlist.manage.change.under"}
    error = request.GET.get("error", "")
    return private_page(render(request, "web/alerts_manage.html", {
        "email": email, "action": here, "groups": manage_groups(request, email),
        "track_days": alerts.TRACK_DAYS,
        "change_row": request.GET.get("row", "") if error in refused else "",
        "change_error": text(request, refused[error]) if error in refused else "",
        "done": text(request, done_keys[done]) if done in done_keys else "",
        "heading": heading, "meta_title": heading, "noindex": True, "robots": "noindex, nofollow",
    }))


def change_alert(email, data):
    """Change one Track price alert from the Your alerts page's form. Returns where to send the visitor back to on
    the page: what happened, and on a refusal which alert's Change to open again and why."""
    from catalogue import manage_alerts

    pk = data.get("change", "")
    if not str(pk).isdigit():
        return ""
    anchor = f"#alert-stock-{pk}"
    price = track_price(data.get("price", ""))
    if price is None:
        return f"?error=price&row={pk}{anchor}"
    alert = StockAlert.objects.filter(pk=pk, email=manage_alerts.normal(email), max_price__isnull=False).first()
    if alert is None:
        return ""
    listing = alerts.price_listing(alert.product)
    if listing is not None and listing.delivered_price <= price:
        # It is there now: the alert would be sent at once.
        return f"?error=under&row={pk}{anchor}"
    offered = {str(days): days for days, _label in alerts.TRACK_DAYS}
    days = offered.get(data.get("for", ""), alerts.TRACK_DEFAULT_DAYS)
    return f"?done=changed{anchor}" if manage_alerts.change(email, pk, price, days) else ""


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
def picks_api(request):
    """The home page's Picked for you row: in-stock products like those named in ``p`` (viewed and saved)
    and found by the searches in ``s``, deals too good to miss first.

    What a visitor looked at lives in their browser; the server only ranks and prices, and keeps nothing.
    """
    from django.template.loader import render_to_string

    seen = products_for_slugs(slugs_in(request, RECENT_MAX + WATCHLIST_MAX))
    searches = [term[:80] for term in request.GET.get("s", "").split("|")][:picks.SEARCHES_READ]
    rows = picks.picks(seen + picks.from_searches(searches))
    response = HttpResponse(render_to_string("web/includes/picks_row.html", {"rows": rows}, request=request))
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def note_api(request):
    """Count one home screen event (shown, added, dismissed, opened), or one press of a Compare prices link
    (what=compare, where it was and on which page), so the dashboard can set them beside Buy now and the
    shops' rows. Nothing about the visitor is kept."""
    event = request.GET.get("what", "")
    if insights.is_bot(request):
        pass
    elif event in insights.INSTALL_EVENTS:
        insights.bump(DailyPageView, kind=DailyPageView.Kind.INSTALL, key=event)
    elif event == "compare":
        where, page = request.GET.get("where", ""), request.GET.get("page", "")
        if where in clicks.COMPARE_PLACES and page in clicks.PAGES.values():
            insights.bump(DailyPageView, kind=DailyPageView.Kind.TAPS, key=f"{where}:{page}")
    elif event == "view" and not clicks.prefetching(request):
        # A page the browser rendered ahead of a tap (the speculation rules in base.html) was not counted when
        # fetched (web.middleware): the page's script counts it here once it is shown, as the view it is.
        from django.urls import Resolver404, resolve

        from .middleware import KINDS, view_key

        try:
            match = resolve(urlsplit(request.GET.get("path", ""))[2])
        except Resolver404:
            match = None
        if match is not None and match.namespace == "web" and match.url_name in KINDS:
            insights.record_view(request, KINDS[match.url_name], view_key(match))
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    return response


def share_links(request, product, page_url):
    """The product page's Share panel. Each network's link names where it was shared (utm_source), so analytics
    shows which one sends visitors; the link to copy by hand is the plain address. Reddit takes a link and a
    title to post; Instagram and TikTok take no link from a web page, so their buttons copy one to paste."""
    def tagged(source):
        return f"{page_url}?{urlencode({'utm_source': source, 'utm_medium': 'social', 'utm_campaign': 'product_share'})}"

    return {
        "link": page_url,
        "reddit": "https://www.reddit.com/submit?" + urlencode({
            "url": tagged("reddit"), "title": text(request, "product.share.reddit_title", product=product.name),
        }),
        "instagram": tagged("instagram"),
        "tiktok": tagged("tiktok"),
        "copy": tagged("copy_link"),
        "native": tagged("share_sheet"),
    }


ALERT_STATES = ("sent", "already", "limit", "invalid", "failed")
# A price alert can also have a price that is not one ("price") or is not below the cheapest now ("under").
TRACK_STATES = ALERT_STATES + ("price", "under")


def track_price(raw):
    """The price typed in the Track price form, in pounds, or None when it is not one the form takes."""
    from catalogue.search_alerts import MAX_PRICE, MIN_PRICE

    try:
        value = Decimal(raw.replace("£", "").replace(",", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return value if value.is_finite() and MIN_PRICE <= value <= MAX_PRICE else None


REPORT_STATES = ("sent", "busy", "reason")


@require_POST
def report_listing(request, slug):
    """Take a visitor's report that a shop's offer is wrong (Report beside each offer on the product page) into
    the Message us inbox (inbox.service.report), and send them back to the product with how it went. No name
    or email is asked for; the hidden field and the hourly limit keep robots out. Works without JavaScript."""
    product = get_object_or_404(Product.objects.active(), slug=slug)
    back = product.get_absolute_url()
    pk = request.POST.get("listing", "")
    listing = (Listing.objects.filter(pk=pk, product=product).select_related("retailer", "product").first()
               if pk.isdigit() else None)
    if request.POST.get("website") or listing is None:
        return HttpResponseRedirect(back)   # the hidden field is only ever filled by robots
    reason = request.POST.get("reason", "")
    if reason not in inbox.REPORT_REASONS:
        return HttpResponseRedirect(f"{back}?report={listing.pk}&report_state=reason#report")
    try:
        inbox.report(listing, reason, request.POST.get("note", ""))
        state = "sent"
    except inbox.Refused:
        state = "busy"
    return HttpResponseRedirect(f"{back}?report_state={state}#report")


@require_POST
def alert_ask(request, slug):
    """Take an email address for a back-in-stock alert, or with ``track`` a price alert (a price and how long
    to keep looking), and send the confirmation. Works without JavaScript."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    product = get_object_or_404(Product.objects.active(), slug=slug)
    back = product.get_absolute_url()
    if request.POST.get("website") or not mail.enabled():
        return HttpResponseRedirect(back)   # the hidden field is only ever filled by robots
    form = "track" if request.POST.get("track") else "alert"
    max_price = days = None
    if form == "track":
        max_price = track_price(request.POST.get("price", ""))
        if max_price is None:
            return HttpResponseRedirect(f"{back}?track=price#track")
        listing = alerts.price_listing(product)
        if listing is not None and listing.delivered_price <= max_price:
            # It is already there: the alert would be sent at once.
            return HttpResponseRedirect(f"{back}?track=under#track")
        offered = {str(n): n for n, _ in alerts.TRACK_DAYS}
        days = offered.get(request.POST.get("for", ""), alerts.TRACK_DEFAULT_DAYS)
    email = request.POST.get("email", "").strip()
    try:
        validate_email(email)
        state = alerts.ask(product, email, max_price=max_price, days=days)
    except ValidationError:
        state = "invalid"
    except mail.MailError:
        state = "failed"
    return HttpResponseRedirect(f"{back}?{form}={state}#{form}")


@require_GET
def alert_confirm(request, token):
    alert = alerts.confirm(token)
    if alert is None:
        heading, message, product = text(request, "alerts.gone.title"), text(request, "alerts.gone.body"), None
    else:
        heading = text(request, "alerts.confirmed.title")
        if alert.max_price is not None:
            ends = alerts.watch_ends(alert) or alert.confirmed_at + timedelta(days=alerts.CONFIRMED_DAYS)
            message = text(request, "alerts.confirmed.price", product=alert.product.name, price=f"£{alert.max_price}",
                           date=short_date(timezone.localtime(ends).date()))
        else:
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


# Drop tracker ------------------------------------------------------------------------------------

DROP_STATES = ("sent", "invalid", "failed", "busy")
# What a visitor reads after asking for a reminder: (heading, message) keys per state. "sent" is the answer
# for any address, new, already signed up or at its limit, so the page never tells a stranger about one.
DROP_STATE_COPY = {
    "sent": ("drops.done.sent.title", "drops.done.sent.body"),
    "invalid": ("drops.done.problem.title", "alerts.form.invalid"),
    "failed": ("drops.done.problem.title", "alerts.form.failed"),
    "busy": ("drops.done.problem.title", "drops.form.busy"),
}


COUNTDOWN_DAYS = 6   # a live countdown only for drops this week; later ones just say how many days


def drop_words(request, drop, today):
    """Attach the words a drop shows about its date: ``date_text`` ("Fri 23 Oct", "November 2026" for a
    month-only drop, nothing for an undated one) and ``when_text`` ("Out in 5 days", "Out tomorrow", "Out
    today", "Day not announced yet", "Came out 3 days ago"), and ``countdown`` (the drop's start, for a live
    countdown) when it is one to six days away."""
    from django.utils.formats import date_format

    drop.countdown = None
    if drop.undated:
        drop.date_text, drop.when_text = "", text(request, "new.coming_soon.tbc")
        return drop
    if drop.day is None:
        drop.date_text = date_format(drop.month, "F Y")
        drop.when_text = text(request, "drops.when.month")
        return drop
    drop.date_text = date_format(drop.day, "D j M")
    days = (drop.day - today).days
    if days < 0:
        drop.when_text = text(request, "drops.when.out_yesterday" if days == -1 else "drops.when.out", days=-days)
    elif days == 0:
        drop.when_text = text(request, "drops.when.today")
    else:
        drop.when_text = text(request, "drops.when.tomorrow" if days == 1 else "drops.when.days", days=days)
        if days <= COUNTDOWN_DAYS:
            drop.countdown = drop.starts_at.isoformat()
    return drop


def out_now_queryset():
    """Out now on the drops page and the home page: what the old Latest drops page listed (released in the last
    few weeks or just listed by shops) that a shop has in stock now and that is out. A product whose release date
    (its own or its set's) is still to come, by the UK date, stays under Coming soon however a shop marks it: a
    shop taking orders early is not the release. Undated products count by their shops alone."""
    return latest_drops_queryset().in_stock().filter(Q(release__isnull=True) | Q(release__lte=timezone.localdate()))


def in_stock_offers(products):
    """Narrow each product's ``offers`` (buyable_prefetch) to its in-stock ones, for a list that is about stock
    now (Out now, the In stock tab and filter): its card then leads with the cheapest shop that has it, never
    a cheaper pre-order. A product whose cheapest offer was a pre-order is marked ``narrowed``, so the card
    leaves out the Price check, which was worked out on that cheaper price."""
    for product in products:
        offers_now = [offer for offer in product.offers if offer.availability == Listing.Availability.IN_STOCK]
        product.narrowed = bool(product.offers) and (not offers_now or offers_now[0] is not product.offers[0])
        product.offers = offers_now
    return products


def drops_url(show="soon", game=None):
    params = {}
    if show == "out":
        params["show"] = "out"
    if game is not None:
        params["game"] = game.slug
    return reverse("web:drops") + (f"?{urlencode(params)}" if params else "")


@require_GET
def drops(request):
    """The drops page, one place for new sets: Coming soon (every set still to come, soonest first, each with
    its cheapest UK pre-order and one button, Pre-order or Remind me) and Out now (products just out and in
    stock, as product cards, with the sets just out that no shop has in stock yet). ?show=out picks Out now;
    a game chip (?game=) keeps one game's in both. Both are plain links, so the page works without script."""
    from django.utils.formats import date_format

    from catalogue import drop_alerts
    from catalogue import drops as tracker

    show = "out" if request.GET.get("show") == "out" else "soon"
    today = timezone.localdate()
    upcoming, recent = tracker.tracker(today)
    waiting = [drop for drop in recent if not drop.in_stock]   # just out, but no shop has it in stock
    # Out now's count per game, with the game's own fields, so the chips need no query of their own.
    game_fields = ("game", "game__name", "game__short_name", "game__slug", "game__sort_order")
    out_rows = (Product.objects.filter(pk__in=out_now_queryset().values("pk"))
                .order_by().values(*game_fields).annotate(n=Count("pk")))
    known = {}
    out_counts = {}
    for row in out_rows:
        known[row["game"]] = Game(pk=row["game"], name=row["game__name"], short_name=row["game__short_name"],
                                  slug=row["game__slug"], sort_order=row["game__sort_order"], is_active=True)
        out_counts[row["game"]] = row["n"]
    soon_counts, waiting_counts = {}, {}
    for drop in upcoming:
        soon_counts[drop.product_set.game_id] = soon_counts.get(drop.product_set.game_id, 0) + 1
        known[drop.product_set.game_id] = drop.product_set.game
    for drop in waiting:
        waiting_counts[drop.product_set.game_id] = waiting_counts.get(drop.product_set.game_id, 0) + 1
        known[drop.product_set.game_id] = drop.product_set.game
    games = sorted(known.values(), key=lambda game: (game.sort_order, game.name))
    chosen = next((game for game in games if game.slug == request.GET.get("game")), None)
    if chosen is not None:
        upcoming = [d for d in upcoming if d.product_set.game_id == chosen.pk]
        waiting = [d for d in waiting if d.product_set.game_id == chosen.pk]

    def total(counts):
        return counts.get(chosen.pk, 0) if chosen else sum(counts.values())

    soon_total = total(soon_counts)
    out_total = total(out_counts) + len(waiting)
    tab_counts = {"soon": soon_total, "out": out_total}
    chips = [
        {"game": game, "url": drops_url(show, game),
         "count": soon_counts.get(game.pk, 0) if show == "soon" else out_counts.get(game.pk, 0) + waiting_counts.get(game.pk, 0)}
        for game in games
    ]
    # A chip for a game with nothing in this section is a dead end; the chosen one stays so it can be cleared.
    chips = [chip for chip in chips if chip["count"] or chip["game"] == chosen]
    heading = text(request, "drops.title")
    context = {
        "heading": heading,
        "show": show,
        "tabs": [{"key": key, "label": text(request, f"drops.tab.{key}"), "count": tab_counts[key],
                  "url": drops_url(key, chosen)} for key in ("soon", "out")],
        "chips": chips,
        "chosen": chosen,
        "all_url": drops_url(show),
        # Out now has its own address, so its own title and description, not Coming soon's.
        "meta_title": text(request, "meta.drops.out.title" if show == "out" else "meta.drops.title"),
        "meta_description": text(request, "meta.drops.out.description" if show == "out" else "meta.drops.description"),
        "canonical_url": request.build_absolute_uri(drops_url(show, chosen)),
        "structured_json": ld_json(breadcrumbs_json(request, [(heading, reverse("web:drops"))])),
    }
    if show == "out":
        for drop in waiting:
            drop_words(request, drop, today)
        base = out_now_queryset()
        if chosen is not None:
            base = base.filter(game=chosen)
        context.update({"waiting": waiting, "game": chosen})
        return _browse(request, context, base_queryset=base, fixed_game=chosen, default_ordering=DROPS_ORDER,
                       template="web/drops.html", stock_now=True)

    reminders_on = mail.enabled()
    try:
        open_remind = int(request.GET.get("remind", ""))
    except ValueError:
        open_remind = None
    for drop in upcoming:
        drop_words(request, drop, today)
        drop.out = drop.day is not None and drop.day <= today
        drop.remind = reminders_on and not drop.undated and drop_alerts.can_send(drop.day)
        drop.remind_open = drop.remind and drop.pk == open_remind
    labels = {
        tracker.WEEK: text(request, "drops.group.week"),
        tracker.MONTH: text(request, "drops.group.month"),
        tracker.UNDATED: text(request, "drops.group.undated"),
    }
    context["groups"] = [
        {"key": key, "label": labels.get(key) or date_format(month, "F Y"), "drops": rows}
        for key, month, rows in tracker.group(upcoming, today)
    ]
    context["reminders_on"] = reminders_on
    return render(request, "web/drops.html", context)


def ask_for_drop(request, product_set):
    """Take the form's address for this set's reminder. Returns a state from DROP_STATES."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from catalogue import drop_alerts

    email = request.POST.get("email", "").strip()
    try:
        validate_email(email)
    except ValidationError:
        return "invalid"
    if not drop_alerts.address_allowed(insights.client_ip(request)):
        return "busy"
    try:
        return drop_alerts.ask(product_set, email)
    except mail.MailError:
        return "failed"


def drop_ask(request, set_id):
    """A reminder for one drop. POST takes the address and, without JavaScript, sends the visitor to this same
    address with ?state= (a page saying what happened, safe to reload); with JavaScript the drop card asks for
    JSON and shows the message in place. GET shows that page."""
    from catalogue import drop_alerts
    from catalogue.drops import upcoming_drop

    product_set = get_object_or_404(ProductSet.objects.select_related("game"), pk=set_id, game__is_active=True)
    back = reverse("web:drops") + f"#drop-{product_set.pk}"
    if request.method == "POST":
        if request.POST.get("website") or not mail.enabled():
            return HttpResponseRedirect(back)   # the hidden field is only ever filled by robots
        if upcoming_drop(product_set) is None or not drop_alerts.can_send(product_set.release_date):
            raise Http404("That drop has passed.")
        state = ask_for_drop(request, product_set)
        if "application/json" in request.headers.get("Accept", ""):
            title, body = DROP_STATE_COPY[state]
            return JsonResponse({"state": state, "message": text(request, body, set=product_set.name)})
        return HttpResponseRedirect(f"{reverse('web:drop_ask', args=[product_set.pk])}?state={state}")
    if request.method != "GET":
        return HttpResponse(status=405)
    state = request.GET.get("state")
    if state not in DROP_STATES:
        return HttpResponseRedirect(back)
    title, body = DROP_STATE_COPY[state]
    heading = text(request, title)
    return render(request, "web/drop_done.html", {
        "heading": heading, "message": text(request, body, set=product_set.name), "back_url": back,
        "meta_title": heading, "noindex": True,
    })


@require_GET
def drop_confirm(request, token):
    from catalogue import drop_alerts

    alert = drop_alerts.confirm(token)
    if alert is None:
        heading, message, back = text(request, "alerts.gone.title"), text(request, "drops.gone.body"), reverse("web:drops")
    else:
        heading = text(request, "drops.confirmed.title")
        message = text(request, "drops.confirmed.body", set=alert.product_set.name)
        back = reverse("web:drops") + f"#drop-{alert.product_set_id}"
    return render(request, "web/drop_done.html", {"heading": heading, "message": message, "back_url": back,
                                                  "meta_title": heading, "noindex": True})


@require_GET
def drop_stop(request, token):
    from catalogue import drop_alerts

    drop_alerts.stop(token)
    heading = text(request, "drops.stopped.title")
    return render(request, "web/drop_done.html", {"heading": heading, "message": text(request, "alerts.stopped.body"),
                                                  "back_url": reverse("web:drops"), "meta_title": heading,
                                                  "noindex": True})


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


LIST_SHOP_FIELDS = ("name", "shop_url", "email", "message")


@sensitive_post_parameters("email")
def list_shop(request):
    """A shop asks to be listed (inbox/shops.py). Works without JavaScript; a sent form goes on to the thanks
    page by redirect, so reloading it sends nothing twice. A robot's form, a shop that already asked and a
    new enquiry all reach the same thanks."""
    from inbox import shops

    values = dict.fromkeys(LIST_SHOP_FIELDS, "")
    error = ""
    if request.method == "POST":
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = shops.MAX_BODY + 1
        if length > shops.MAX_BODY:
            # Refused before the fields are looked at. Django's own DATA_UPLOAD_MAX_MEMORY_SIZE caps what the
            # CSRF check reads first.
            return HttpResponse(status=413)
        thanks = HttpResponseRedirect(reverse("web:list_shop_thanks"))
        if request.POST.get("website"):
            return thanks   # the hidden field is only ever filled by robots
        values = {name: request.POST.get(name, "")[:shops.MAX_MESSAGE * 2].strip() for name in LIST_SHOP_FIELDS}
        try:
            cleaned = shops.clean(**values)
            shops.ask(cleaned, insights.client_ip(request))
        except shops.Invalid as exc:
            error = f"listshop.form.{exc.args[0]}_invalid"
        except shops.Refused:
            error = "listshop.form.busy"
        else:
            return thanks
    elif request.method != "GET":
        return HttpResponse(status=405)
    return render(request, "web/list_shop.html", {
        "values": values, "error": error, "limits": {"name": shops.MAX_NAME, "url": shops.MAX_URL,
                                                     "message": shops.MAX_MESSAGE},
        "meta_title": text(request, "listshop.title"),
        "meta_description": text(request, "meta.listshop.description"),
        "canonical_url": request.build_absolute_uri(reverse("web:list_shop")),
    })


@require_GET
def list_shop_thanks(request):
    heading = text(request, "listshop.thanks.title")
    return render(request, "web/list_shop_thanks.html", {"heading": heading, "meta_title": heading, "noindex": True})


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
def admin_manifest(request):
    """The admin as its own home screen app: opens on the admin home, stays inside /admin/, and offers the
    owner's daily pages as shortcuts. Public, like any manifest: the browser fetches it without a login, and
    every page it names still asks for one."""
    shortcuts = [
        ("Things to check", "Checks", reverse("checks")),
        ("Messages", "Messages", reverse("admin:inbox_conversation_changelist") + "?unread__exact=1"),
        ("Crawl health", "Crawl", reverse("crawl")),
        ("Insights", "Insights", reverse("insights")),
    ]
    icon = [{"src": static("img/admin-icon-96.png"), "sizes": "96x96", "type": "image/png"}]
    data = {
        "id": "/admin/",
        "name": f"{settings.RIPRAPTOR_SITE_NAME} admin",
        "short_name": "RR admin",
        "description": f"Run {settings.RIPRAPTOR_SITE_NAME}: things to check, messages, shops and insights.",
        "start_url": reverse("admin:index"),
        "scope": reverse("admin:index"),
        "display": "standalone",
        "background_color": "#1f2430",
        "theme_color": "#417690",   # the admin header, so the status bar runs into it
        "icons": [
            {"src": static("img/admin-icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("img/admin-icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("img/admin-icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [{"name": name, "short_name": short, "url": url, "icons": icon} for name, short, url in shortcuts],
    }
    response = JsonResponse(data, content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_GET
def robots_txt(request):
    private = ["Disallow: /admin/", "Disallow: /go/", "Disallow: /search/", "Disallow: /api/", "Disallow: /swipe/", "Disallow: /watchlist/",
               "Disallow: /alerts/", "Disallow: /contact/c/", "Disallow: /refer/", "Disallow: /r/",
               "Disallow: /dashboard/"]
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
        f"- [Upcoming drops with dates and pre-order prices]({request.build_absolute_uri(reverse('web:drops'))})",
        f"- [How it works]({request.build_absolute_uri(reverse('web:about'))})",
        f"- [Terms and disclosures]({request.build_absolute_uri(reverse('web:terms'))})",
        f"- [Sitemap]({request.build_absolute_uri('/sitemap.xml')}): every product on sale, coming soon or priced "
        "in the last month, and every game and set",
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
    data = insights.report(days)
    why = traffic.explain(store=copy.for_request(request))
    for day in data["by_day"]:
        day["burst"] = day["date"] in why["burst_dates"]
    context = {**admin.site.each_context(request), "title": "Insights", **data, "why": why,
               "click_cap": clicks.CLICKS_PER_HOUR}
    return render(request, "admin/insights.html", context)


def posted_price_moved(request, listing):
    """True when the form posted a price and the listing's (or found row's) price is no longer that price."""
    if "price" not in request.POST:
        return False
    return price_moved(request.POST["price"], listing)


def price_moved(posted, listing):
    """True when the listing's (or found row's) price is no longer the posted one."""
    if listing.price is None:
        return posted != ""
    try:
        return Decimal(posted) != listing.price
    except (InvalidOperation, ValueError):
        return True


# Most rows one tap of Things to check's tick boxes answers.
TICKED_LIMIT = 100
CHANGED = "That {} has changed since the page loaded. Check it again below."


def offer_tap(action, listing):
    """Hide, This price is right or Show it anyway, for one listing. Returns what the page says."""
    from catalogue import autopilot, judge, sanity

    if action == "hide":
        # The other shops were judged against this price: without it they may be fine. The autopilot
        # never answers a price the owner hid.
        autopilot.hide(listing)
        autopilot.owner_hid(listing)
        judge.note_owner(f"offer:{listing.pk}", "hid")
        return (f"Hidden: {listing.product.name} at {listing.retailer.name}. "
                "Tick show on site on the listing to bring it back.")
    sanity.trust(listing)
    judge.note_owner(f"offer:{listing.pk}", "same")
    return (f"Counted: £{listing.price} for {listing.product.name} at {listing.retailer.name}. It stays counted "
            f"while it moves less than {sanity.TRUST_BAND * 100:.0f}% for {sanity.TRUST_DAYS} days.")


def found_tap(action, row):
    """Yes, link it or No, not this, for one page the stockist finder found. Returns what the page says."""
    from catalogue import finder, judge

    if action == "link_found":
        finder.link(row)
        judge.note_owner(f"found:{row.pk}", "same")
        return f"Linked: {row.suggested.name} at {row.retailer.name}. The next read of the shop checks its price and stock."
    finder.ignore(row)
    judge.note_owner(f"found:{row.pk}", "different")
    return f"Noted: {row.retailer.name} is not asked about {row.suggested.name} again."


def apart_tap(keep, other):
    """Not the same, for one pair of possible duplicates. Returns what the page says."""
    from catalogue import autopilot, judge

    autopilot.owner_apart(keep, other)
    judge.note_owner(judge.pair_key(keep, other), "different")
    return f"Noted: {other.name} is not {keep.name}. They are not suggested together again."


def merge_pair_tap(keep, other):
    """Merge one possible duplicate pair the owner ticked, as the Merge these button does for its group."""
    from django.db import transaction

    from catalogue import judge
    from catalogue.management.commands.merge_duplicates import merge_with_undo

    with transaction.atomic():
        merge_with_undo(keep, other, "You ticked Merge ticked", by_owner=True)
        judge.note_owner(judge.pair_key(keep, other), "same")


def ticked_tap(request):
    """One tap for every ticked row of a list: hide them, count them, link them, say no to them or keep
    pairs apart. Each row is answered as its own button would, and only while it is as the page showed
    it. Returns the ticks answered, for the page to mark without reloading."""
    from django.contrib import messages

    from catalogue import checks
    from catalogue.signals import clear_list_caches

    do, picks = request.POST.get("do", ""), request.POST.getlist("pick")[:TICKED_LIMIT]
    if do not in ("trust", "hide", "link_found", "not_found", "apart", "merge") or not picks:
        messages.warning(request, "Tick at least one row first, then tap what to do with them.")
        return []
    pairs = {f"{keep.pk}:{other.pk}": (keep, other) for keep, others in checks.duplicates() for other in others} \
        if do in ("apart", "merge") else {}
    done, changed = [], 0
    for pick in dict.fromkeys(picks):
        first, _, second = pick.partition(":")
        if do in ("apart", "merge"):
            pair = pairs.get(pick)
            if pair is None or not pair[0].is_active or not pair[1].is_active:
                changed += 1
                continue
            if do == "apart":
                apart_tap(*pair)
            else:
                merge_pair_tap(*pair)
        elif not first.isdigit():
            changed += 1
            continue
        elif do in ("trust", "hide"):
            listing = Listing.objects.select_related("product", "retailer").filter(pk=first, is_active=True).first()
            if listing is None or price_moved(second, listing):
                changed += 1
                continue
            offer_tap(do, listing)
        else:
            row = checks.found_waiting().select_related("retailer", "suggested").filter(pk=first).first()
            if row is None or price_moved(second, row):
                changed += 1
                continue
            found_tap(do, row)
        done.append(pick)
    if done and do != "not_found":
        clear_list_caches(force=True)
    n = len(done)
    rows = f"{n} row{'s' if n != 1 else ''}"
    said = {
        "trust": f"Counted {n} price{'s' if n != 1 else ''}.",
        "hide": f"Hidden {n} price{'s' if n != 1 else ''}. Tick show on site on a listing to bring it back.",
        "link_found": f"Linked {rows}. The next read of each shop checks its price and stock.",
        "not_found": f"Noted {rows}: the shops are not asked about them again.",
        "apart": f"Noted {n} pair{'s' if n != 1 else ''} as not the same.",
        "merge": f"Merged {n} pair{'s' if n != 1 else ''}. The old addresses redirect to the kept products, and each "
                 "merge is listed under Sorted for you, with Undo.",
    }[do]
    if n:
        messages.success(request, said)
    if changed:
        messages.warning(request, f"{changed} ticked row{'s' if changed != 1 else ''} had changed since the page "
                                  "loaded. Check again below.")
    return done


def duplicate_sections(groups):
    """The possible duplicate pairs in four sections, most certain first: (level label, count, [(keep, other)]).
    A level with no pairs is left out."""
    from catalogue.management.commands.merge_duplicates import CERTAINTY_LABELS

    rows = [(keep, other) for keep, others in groups for other in others]
    return [(CERTAINTY_LABELS[level], len(found), found) for level in CERTAINTY_LABELS
            if (found := [row for row in rows if row[1].certainty == level])]


@staff_member_required
@sensitive_post_parameters("claude_key")
def checks_page(request):
    """Things to check: what the autopilot answered, with Undo, then doubtful and excluded prices, wrong
    matches, likely duplicates, products found at another shop and unknown delivery, each with a one-tap fix."""
    from collections import Counter

    from django.contrib import messages
    from django.db import transaction

    from catalogue import autopilot, checks, judge
    from catalogue.management.commands.merge_duplicates import merge_with_undo
    from catalogue.models import CheckAnswer
    from catalogue.signals import clear_list_caches

    if request.method == "POST":
        action = request.POST.get("action", "")
        done = []
        if action in ("hide", "trust", "show"):
            listing = get_object_or_404(Listing.objects.select_related("product", "retailer"), pk=request.POST.get("listing"))
            if posted_price_moved(request, listing):
                # Only the price the owner looked at is acted on, as with a merge.
                messages.warning(request, CHANGED.format("price"))
            else:
                messages.success(request, offer_tap(action, listing))
                clear_list_caches(force=True)
        elif action in ("link_found", "not_found"):
            row = get_object_or_404(checks.found_waiting().select_related("retailer", "suggested"), pk=request.POST.get("row"))
            if posted_price_moved(request, row):
                messages.warning(request, CHANGED.format("price"))
            else:
                messages.success(request, found_tap(action, row))
                if action == "link_found":
                    clear_list_caches(force=True)
        elif action == "ticked":
            done = ticked_tap(request)
        elif action in ("add_set", "not_a_set", "use_release_date", "keep_release_date"):
            release_tap(request, action)
        elif action == "merge":
            # Only a group the page offered, exactly as it stands now, is merged.
            wanted = request.POST.get("keep", ""), sorted(request.POST.getlist("other"))
            for keep, others in checks.duplicates():
                if (str(keep.pk), sorted(str(o.pk) for o in others)) == wanted:
                    # Each pair can be undone on its own, under Sorted for you.
                    with transaction.atomic():
                        for other in others:
                            row_key = judge.pair_key(keep, other)
                            merge_with_undo(keep, other, "You tapped Merge these", by_owner=True)
                            judge.note_owner(row_key, "same")
                    clear_list_caches(force=True)
                    messages.success(request, f"Merged into {keep.name}. The old addresses redirect to it. Each "
                                              "merge is listed under Sorted for you, with Undo.")
                    break
            else:
                messages.warning(request, "That group has changed since the page loaded. Check it again below.")
        elif action == "apart":
            # Only a pair the page offered is kept apart.
            wanted = request.POST.get("keep", ""), request.POST.get("other", "")
            pair = next(((keep, other) for keep, others in checks.duplicates() for other in others
                         if (str(keep.pk), str(other.pk)) == wanted), None)
            if pair is None:
                messages.warning(request, CHANGED.format("group"))
            else:
                messages.success(request, apart_tap(*pair))
        elif action == "undo":
            pk = request.POST.get("answer", "")
            # The site's and Claude's answers, and the owner's merges: his other answers have their own buttons.
            answer = checks.undoable().filter(pk=pk).first() if pk.isdigit() else None
            message = autopilot.undo(answer) if answer is not None else ""
            if message:
                messages.success(request, message)
            elif answer is not None and answer.kind == CheckAnswer.Kind.MERGE and autopilot.later_merges(answer).exists():
                later = autopilot.later_merges(answer).order_by("-pk").first()
                messages.warning(request, f"A later merge built on this one: {later.what}. Undo that merge first, "
                                          "then this one.")
            elif answer is not None and answer.kind == CheckAnswer.Kind.MERGE and answer.product is not None \
                    and not answer.product.is_active:
                messages.warning(request, f"{answer.product.name} is switched off. Tick show on site on it first, "
                                          "then undo this merge.")
            else:
                messages.warning(request, "That one can no longer be undone. Check it on its own page.")
        elif action == "merge_sure":
            merge_sure(request)
        elif action.startswith("claude_"):
            claude_tap(request, action)
        elif action == "autopilot_now":
            done = autopilot.run()
            if done:
                messages.success(request, f"Sorted {len(done)} thing{'s' if len(done) != 1 else ''}. Each is listed under Sorted for you, with Undo.")
            else:
                messages.success(request, "Nothing more can be sorted without you. What is left below needs your eye.")
        if request.headers.get("X-Checks-Tap") == "1":
            # A tap sent by the page's script: it marks the row answered in place, with no reload.
            said = [(message.level_tag, message.message) for message in messages.get_messages(request)]
            return JsonResponse({"ok": bool(said) and all(tag == "success" for tag, _ in said),
                                 "said": " ".join(text for _, text in said), "done": done})
        return HttpResponseRedirect(reverse("checks"))
    now = timezone.now()
    counts = checks.sanity_counts()
    doubtful_count = checks.doubtful_count()
    checked_count = checks.checked_count()
    answers = checks.recent_answers(now)
    tally = Counter(answer.kind for answer in answers if answer.undone_at is None)
    doubtful, wrong, duplicates = checks.doubtful_prices(now), checks.wrong_matches(), checks.duplicates()
    # The dashboard reads these counts rather than reading the whole catalogue again.
    checks.note_duplicate_counts(duplicates, now)
    found = checks.found_stockists(now)
    # Claude's latest answer for each row on the page, while the row is still as Claude saw it.
    claude = judge.page_status(now)
    state = claude["state"]
    claude_counts = judge.attach_answers(doubtful, wrong, found, duplicates, may_act=bool(state and state.may_act))
    release_rows = checks.release_candidates(now)
    judge.attach_set_answers(release_rows, may_act=bool(state and state.may_act))
    context = {
        **admin.site.each_context(request), "title": "Things to check",
        "answers": checks.listed_answers(answers), "answers_count": len(answers), "answers_days": checks.ANSWERS_DAYS,
        "tally": [(CheckAnswer.Kind(kind).label, n) for kind, n in tally.most_common()],
        "doubtful": doubtful, "doubtful_count": doubtful_count,
        "doubtful_left": max(0, counts["doubtful"] - doubtful_count - checked_count), "checked_count": checked_count,
        "excluded": checks.excluded_prices(), "counts": counts,
        "wrong": wrong, "duplicates": duplicates, "sure_pairs": judge.sure_pairs(duplicates),
        "duplicate_sections": duplicate_sections(duplicates),
        "shops": checks.unknown_delivery_shops(),
        "max_percent": offers.MAX_REAL_PERCENT,
        "found": found, "found_count": checks.found_waiting().count(),
        "claude": claude, "claude_counts": claude_counts,
        "releases": release_rows, "disagreements": checks.release_disagreements(now),
        "stale_sources": checks.stale_release_sources(now),
    }
    # The owner reads a source's name, never its internal key (tcgdex_sets, shop:total-cards).
    checks.name_sources(context["releases"] + [row for group in context["disagreements"] for row in group["rows"]])
    return render(request, "admin/checks.html", context)


def merge_sure(request):
    """The owner's Merge the pairs Claude is sure are the same: each pair the page showed that Claude is
    still sure of is merged so it can be undone, and listed under Sorted for you with Undo."""
    from django.contrib import messages
    from django.db import transaction

    from catalogue import checks, judge
    from catalogue.management.commands.merge_duplicates import merge_undoable
    from catalogue.models import CheckAnswer
    from catalogue.signals import clear_list_caches

    wanted = set(request.POST.getlist("pair"))
    merged = 0
    for keep, other, ask in judge.sure_pairs(checks.duplicates()):
        if f"{keep.pk}:{other.pk}" not in wanted or CheckAnswer.objects.filter(ask=ask).exists():
            continue
        with transaction.atomic():
            note = merge_undoable(keep, [other])
            CheckAnswer.objects.create(
                kind=CheckAnswer.Kind.MERGE, what=f"Merged {other.name} into {keep.name}",
                why=f"Claude: {ask.reason} You tapped Merge the pairs Claude is sure are the same",
                product=keep, other=other, ask=ask, undo_note=note,
            )
            judge.note_owner(ask.row_key, "same")
        merged += 1
    if merged:
        clear_list_caches(force=True)
        messages.success(request, f"Merged {merged} pair{'s' if merged != 1 else ''}. Each is listed under Sorted for you, "
                                  "with Undo. The old addresses redirect.")
    else:
        messages.warning(request, "Those pairs have changed since the page loaded. Check them again below.")


def claude_tap(request, action):
    """The Claude box's buttons. Only Save key talks to Anthropic, with one free request."""
    from django.contrib import messages

    from catalogue import judge
    from catalogue.models import ClaudeJudge

    state = ClaudeJudge.load()
    update = {}
    if action == "claude_now":
        update["asked_at"] = timezone.now()
        messages.success(request, "Claude is looking now. Reload this page to see its answers.")
    elif action in ("claude_sort_on", "claude_sort_off"):
        update["sort_all"] = action == "claude_sort_on"
        if update["sort_all"]:
            # Sorting everything means acting: Let Claude act comes with it, and starts a fresh count of undos.
            update.update(may_act=True, acting_since=timezone.now(), asked_at=timezone.now())
            messages.success(request, "Claude now sorts everything, run after run, starting now. Each act has Undo.")
        else:
            messages.success(request, "Claude now looks at 25 rows an hour.")
    elif action in ("claude_on", "claude_off"):
        update["enabled"] = action == "claude_on"
        if not update["enabled"]:
            update["asked_at"] = None
        messages.success(request, "Claude is on. It starts in trial: it suggests and does not act." if update["enabled"] and not state.may_act
                         else "Claude is on." if update["enabled"] else "Claude is off. Nothing is sent and nothing is spent.")
    elif action in ("claude_act_on", "claude_act_off"):
        update["may_act"] = action == "claude_act_on"
        if update["may_act"]:
            # Undos are counted afresh from here: three in a week put Claude back to suggesting.
            update["acting_since"] = timezone.now()
        if not update["may_act"]:
            messages.success(request, "Claude now only suggests. Its answers show under each row.")
    elif action == "claude_settings":
        model, effort = request.POST.get("model", ""), request.POST.get("effort", "")
        try:
            budget = Decimal(request.POST.get("budget", "")).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            budget = None
        if model not in judge.MODELS or effort not in ClaudeJudge.Effort.values or budget is None \
                or not budget.is_finite() or not 0 <= budget <= settings.RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD:
            messages.warning(request, f"Choose a model and effort, and a monthly limit from 0 to "
                                      f"{settings.RIPRAPTOR_CLAUDE_MAX_MONTHLY_USD} dollars.")
        else:
            update.update(model=model, effort=effort, monthly_budget_usd=budget)
            if state.problem == "model" and model != state.model:
                update.update(problem="", problem_at=None)
            messages.success(request, "Claude's settings are saved.")
    elif action == "claude_key_save":
        saved, message = judge.save_key(request.POST.get("claude_key", ""))
        (messages.success if saved else messages.warning)(request, message)
    elif action == "claude_key_forget":
        if judge.forget_key():
            messages.success(request, "The key is forgotten. Claude sends nothing until a key is saved again.")
        else:
            messages.warning(request, "The server could not delete the key file. Switch Claude off, and delete the key in the Console.")
    if update:
        ClaudeJudge.objects.filter(pk=state.pk).update(**update)
    if action in ("claude_act_on", "claude_now", "claude_sort_on"):
        # What Claude has already answered, it may act on at once: nothing is sent and nothing is spent.
        earlier = judge.act_on_earlier()
        if earlier:
            messages.success(request, f"Claude sorted {earlier} row{'s' if earlier != 1 else ''} from answers it had "
                                      "already given. Each is under Sorted for you with Undo.")
        elif action == "claude_act_on":
            from catalogue import crawl

            messages.success(request, "Claude starts once Pause all is off." if crawl.all_paused() else
                             "Claude now sorts what it can.")
    if action in ("claude_now", "claude_sort_on"):
        judge.start_now()


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
    from catalogue import judge

    if action == "not_a_set":
        releases.dismiss(row)
        # The owner's tap is final: Claude's answer about the row, if any, is marked as his.
        judge.note_owner(f"set:{row.pk}", "not_set")
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
        judge.note_owner(f"set:{row.pk}", "added")
    clear_list_caches(force=True)
    when = f" It comes out on {product_set.release_date:%-d %B %Y}." if product_set.release_date else ""
    messages.success(request, f"Added the set {product_set.name} to {row.game.name}.{when} Products named after it "
                              "are filed under it.")


@staff_member_required
def crawl_page(request):
    """Crawl health: whether shops are being read, with one-tap buttons to read a shop now or pause reading."""
    from django.contrib import messages
    from django.db import DatabaseError

    from catalogue import crawl, heat, notify
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
        # Whether a problem reaches the owner's phone, so a page with no way to set it up says so.
        "alerts": {"on": settings.RIPRAPTOR_CRAWL_PUSHES, "push": notify.can_push(), "email": notify.can_email()},
    }
    return render(request, "admin/crawl.html", context)


# Your data in Excel ---------------------------------------------------------------------------------------
# The owner downloads the catalogue as a workbook, tidies it in Excel and uploads it (catalogue/workbook.py).
# Making, checking, making the changes and undoing them run in the data_files process (catalogue/data_jobs.py),
# never in a request: these pages queue the work, start the runner and show where it has got. A web worker never
# loads openpyxl: an upload is checked here only as a zip.

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# A download from here unpacks to about 30 MB in about 20 parts. A file far past that is not one of ours, and is
# refused before anything unpacks it.
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_PARTS = 200
# The part that carries the file number is a few hundred bytes.
MAX_NUMBER_PART = 64 * 1024
# A list of changes is judged against the site as it was when it was checked; after a day it is checked again.
PREVIEW_FRESH = timedelta(hours=24)
LINES_PER_PAGE = 200

NOT_XLSX = "This is not an .xlsx file. Save it as Excel Workbook (.xlsx) and upload that."
TOO_LARGE = "This file is over 10 MB, far bigger than a download from here. Upload the spreadsheet you downloaded."
TOO_MUCH_INSIDE = ("This file unpacks to far more than a download from here, so it was not opened. Upload the "
                   "spreadsheet you downloaded.")
MACROS = ("This file holds macros. Save it as Excel Workbook (.xlsx), not Excel Macro-Enabled Workbook, and upload "
          "that.")
BUSY = "The database was busy, so nothing changed. Tap the button again."
RECHECKED = ("This file was checked again since the page loaded, so nothing was made. Read the list below, then "
             "tap to make the changes.")
MADE_SINCE = ("Another upload of this spreadsheet has made or undone changes since this list was checked, so nothing "
              "was made. Tap Check again, read the list, then make the changes.")
OLD_PREVIEW = ("This list was checked over a day ago, so nothing was made. Tap Check again, read the list, then make "
               "the changes.")


def owner_only(view):
    """The owner alone: staff sign in first (anyone else is sent to the login page), then only a superuser gets
    in, since one upload changes thousands of rows. Never cached, so the back button shows where a file is now
    and a download stays out of shared caches."""

    @wraps(view)
    def owner_view(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return staff_member_required(never_cache(owner_view))


def megabytes(size):
    return f"{max(size, 100_000) / 1_000_000:.1f} MB"


def when(moment):
    from django.utils.dateformat import format as date_format

    local = timezone.localtime(moment)
    return f"{date_format(local, 'j M')} at {local:%H:%M}"


class ExportForm(forms.Form):
    left_out_shop = forms.ModelChoiceField(
        label="Add the items one shop's latest read left out", required=False, empty_label="No shop",
        queryset=Retailer.objects.filter(is_active=True).exclude(source_type=Retailer.Source.MANUAL).order_by("name"),
    )


class UploadForm(forms.Form):
    file = forms.FileField(
        label="Your edited spreadsheet (.xlsx, up to 10 MB)",
        widget=forms.FileInput(attrs={"accept": f".xlsx,{XLSX}"}),
        error_messages={"required": "Choose your spreadsheet first, then tap Check this file.",
                        "empty": "This file is empty. Upload the spreadsheet you saved in Excel."},
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        self.contents, self.reference = opened_upload(upload)
        return upload


def opened_upload(upload):
    """(bytes, file number or None) of an uploaded workbook that passes what can be checked without reading it as
    a workbook: its name and size, that it is a zip holding a workbook's parts and no macros, what it unpacks to,
    and its file number. Raises forms.ValidationError with the sentence he sees. The runner reads the sheets.

    A file that lost its number property is let through: the runner finds the number on its Read me sheet."""
    import zipfile
    import zlib
    from io import BytesIO

    from catalogue import workbook
    from catalogue.models import DataExport

    if not (upload.name or "").lower().endswith(".xlsx"):
        raise forms.ValidationError(NOT_XLSX)
    if upload.size > MAX_UPLOAD_BYTES:
        raise forms.ValidationError(TOO_LARGE)
    data = upload.read()
    if not data.startswith(b"PK\x03\x04"):
        raise forms.ValidationError(workbook.UNREADABLE)
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            parts = archive.infolist()
            # The sizes a zip states are the most its parts unpack to: Python stops there.
            if len(parts) > MAX_PARTS or sum(part.file_size for part in parts) > MAX_UNPACKED_BYTES:
                raise forms.ValidationError(TOO_MUCH_INSIDE)
            names = {part.filename.replace("\\", "/") for part in parts}
            # Excel never writes one part twice; a reader takes the last of the name, a check the first.
            if len(names) != len(parts):
                raise forms.ValidationError(workbook.UNREADABLE)
            if any(name.startswith("/") or ":" in name or ".." in name.split("/") for name in names):
                raise forms.ValidationError(workbook.UNREADABLE)
            if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                raise forms.ValidationError(workbook.UNREADABLE)
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise forms.ValidationError(MACROS)
            reference = workbook.quick_reference(archive, MAX_NUMBER_PART)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError, OSError,
            zlib.error) as exc:
        raise forms.ValidationError(workbook.UNREADABLE) from exc
    if reference is not None:
        pk, token = reference
        if not DataExport.objects.filter(pk=pk, token=token, base__isnull=False).exists():
            raise forms.ValidationError(workbook.LOST_NUMBER)
    return data, reference


def make_spreadsheet(request):
    from django.contrib import messages

    from catalogue import data_jobs
    from catalogue.models import DataExport

    form = ExportForm(request.POST)
    if not form.is_valid():
        messages.error(request, "That shop is no longer in the list. Pick another, or none.")
        return
    if DataExport.objects.filter(status__in=[DataExport.Status.QUEUED, DataExport.Status.BUILDING]).exists():
        messages.info(request, "A spreadsheet is being made already. Its Download button appears below when it is "
                               "ready.")
        return
    DataExport.objects.create(created_by=request.user, left_out_retailer=form.cleaned_data["left_out_shop"])
    data_jobs.start_now()
    messages.success(request, "Making the spreadsheet. It takes up to a minute, and its Download button appears "
                              "below when it is ready.")


def take_upload(request):
    """Keep an uploaded file and queue its check. Returns the page to go to: the file's own, or the Your data
    page when the file was refused."""
    import hashlib

    from django.contrib import messages

    from catalogue import data_jobs
    from catalogue.models import DataChange, DataImport

    Status = DataImport.Status
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("file", []) or ["Choose your spreadsheet first, then tap Check this file."]:
            messages.error(request, error)
        return reverse("data")
    data, reference = form.contents, form.reference
    sha256 = hashlib.sha256(data).hexdigest()
    # A list of changes whose file is no longer kept cannot be checked again, so this upload takes its place.
    DataImport.objects.filter(sha256=sha256, status=Status.CHECKED, upload__isnull=True).update(
        status=Status.DISCARDED, check_token="")
    # The same file twice is shown where it got to, so it is never made twice. One refused, discarded, failed or
    # undone may be uploaded again, and so may one made without its held changes, to make them: checked again,
    # what was made reads as already so.
    held_left = Exists(DataChange.objects.filter(data_import=OuterRef("pk"), state=DataChange.State.HELD,
                                                 outcome=DataChange.Outcome.NONE))
    earlier = (DataImport.objects.filter(sha256=sha256)
               .exclude(status__in=[Status.REFUSED, Status.DISCARDED, Status.FAILED, Status.UNDONE])
               .exclude(Q(status=Status.APPLIED), held_left)
               .only("pk", "created_at").order_by("-created_at", "-pk").first())
    if earlier is not None:
        messages.info(request, f"You uploaded this file already, on {when(earlier.created_at)}. This is where it "
                               "has got to.")
        return reverse("data_import", args=[earlier.pk])
    upload = DataImport.objects.create(
        created_by=request.user, export_id=reference[0] if reference else None, size_bytes=len(data),
        file_name=form.cleaned_data["file"].name[:200], sha256=sha256, upload=data,
    )
    data_jobs.start_now()
    messages.success(request, "Checking the file. Nothing changes until you have read the list of changes and "
                              "tapped to make them.")
    return reverse("data_import", args=[upload.pk])


@owner_only
def data_page(request):
    """Your data in Excel: make a spreadsheet of the catalogue, download it, and upload it once it is tidied. The
    page refreshes itself while something runs, and starts the runner again when a job waits with none."""
    from django.contrib import messages
    from django.db import DatabaseError
    from django.db.models import BooleanField, ExpressionWrapper

    from catalogue import data_jobs
    from catalogue.models import DataExport, DataImport

    if request.method == "POST":
        target = reverse("data")
        try:
            if request.POST.get("action") == "make":
                make_spreadsheet(request)
            elif request.POST.get("action") == "upload":
                target = take_upload(request)
        except DatabaseError:
            messages.error(request, BUSY)
        return HttpResponseRedirect(target)
    now = timezone.now()
    going = data_jobs.WAITING | data_jobs.RUNNING
    working = [*DataExport.objects.filter(status__in=going).values_list("status", "created_at", "heartbeat_at"),
               *DataImport.objects.filter(status__in=going).values_list("status", "created_at", "heartbeat_at")]
    data_jobs.wake_runner(working, now)
    exports = list(
        DataExport.objects.defer("workbook", "base").select_related("left_out_retailer")
        .annotate(kept=ExpressionWrapper(Q(workbook__isnull=False), output_field=BooleanField()))[:5]
    )
    for export in exports:
        export.size_label = megabytes(export.size_bytes)
        export.rows = ", ".join(f"{name} {count:,}" for name, count in (export.counts or {}).items())
    context = {
        **admin.site.each_context(request), "title": "Your data in Excel", "refresh": bool(working),
        "exports": exports, "imports": list(DataImport.objects.defer("upload")[:10]),
        "export_form": ExportForm(), "upload_form": UploadForm(),
    }
    return render(request, "admin/data.html", context)


@owner_only
@require_GET
def data_download(request, pk):
    from django.contrib import messages

    from catalogue.models import DataExport

    found = (DataExport.objects.filter(pk=pk, status=DataExport.Status.READY)
             .values_list("workbook", "created_at").first())
    if found is None or found[0] is None:
        messages.warning(request, "That spreadsheet is no longer kept. Make a fresh one.")
        return HttpResponseRedirect(reverse("data"))
    data, created_at = found
    response = HttpResponse(bytes(data), content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{DataExport(pk=pk, created_at=created_at).filename}"'
    with pricing.drop_if_locked("Spreadsheet download time"):
        DataExport.objects.filter(pk=pk).update(downloaded_at=timezone.now())
    return response


def import_tap(request, pk):
    """One of the buttons on an uploaded file's page. Each moves the file on only from the state the page showed,
    so a second tap, or a tap from an older page, does nothing."""
    from django.contrib import messages

    from catalogue import data_jobs
    from catalogue.models import DataChange, DataImport

    Status = DataImport.Status
    action = request.POST.get("action", "")
    now = timezone.now()
    jobs = DataImport.objects.filter(pk=pk)
    job = jobs.only("pk", "status", "check_token", "checked_at", "export_id").first()
    if job is None:
        raise Http404("No such upload.")
    made_some = Exists(DataChange.objects.filter(data_import=OuterRef("pk"), outcome=DataChange.Outcome.APPLIED))
    queued = {"heartbeat_at": now, "attempts": 0, "note": "", "error": ""}
    if action in ("apply", "apply_all"):
        token = request.POST.get("check_token", "")
        if job.status in (Status.APPLY_QUEUED, Status.APPLYING, Status.APPLIED):
            messages.info(request, "Its changes are being made already.")
        elif job.status != Status.CHECKED or not token or token != job.check_token:
            messages.warning(request, RECHECKED)
        elif job.checked_at is None or job.checked_at < now - PREVIEW_FRESH:
            messages.warning(request, OLD_PREVIEW)
        elif job.export_id and DataChange.objects.filter(
                Q(applied_at__gt=job.checked_at) | Q(undone_at__gt=job.checked_at),
                data_import__export_id=job.export_id).exclude(data_import_id=job.pk).exists():
            # Its list may count on what the other file has since made or undone, such as a new1 it added.
            messages.warning(request, MADE_SINCE)
        elif jobs.filter(status=Status.CHECKED, check_token=token).update(
                status=Status.APPLY_QUEUED, include_held=action == "apply_all", done=0, **queued):
            data_jobs.start_now()
            messages.success(request, "Making the changes. This page shows how far it has got.")
        else:
            messages.warning(request, RECHECKED)
    elif action == "check_again":
        if jobs.filter(status=Status.CHECKED).update(status=Status.QUEUED, check_token="", **queued):
            data_jobs.start_now()
            messages.success(request, "Checking the file again, against the site as it is now.")
        else:
            messages.warning(request, "Only a checked file whose changes have not been made can be checked again.")
    elif action == "discard":
        if jobs.filter(Q(status=Status.CHECKED) | Q(status=Status.FAILED) & ~made_some).update(
                status=Status.DISCARDED, upload=None, check_token=""):
            messages.success(request, "Discarded. Nothing in this file was made.")
        else:
            messages.warning(request, "Only a file whose changes have not been made can be discarded.")
    elif action == "undo":
        # An undone file with changes left as they were may be undone again, once what stopped them is put right.
        undo_left = Exists(DataChange.objects.filter(data_import=OuterRef("pk"),
                                                     outcome=DataChange.Outcome.UNDO_SKIPPED))
        if jobs.filter(Q(status__in=[Status.APPLIED, Status.FAILED]) & made_some
                       | Q(status=Status.UNDONE) & undo_left).update(status=Status.UNDO_QUEUED, **queued):
            data_jobs.start_now()
            messages.success(request, "Undoing the file, newest change first. Each is undone only while the site "
                                      "still holds what the file left.")
        else:
            messages.warning(request, "This file has no changes to undo.")


# What came of the ready and held changes, as their headings say once the file is made or undone.
CAME_OF = (("applied", "made", "made"), ("undone", "undone", "undone"),
           ("skipped", "left as it was", "left as they were"), ("failed", "could not be made", "could not be made"),
           ("undo_skipped", "not undone", "not undone"), ("", "not made", "not made"))


def came_of(outcomes, going):
    """'2 made, 1 left as it was' from {outcome: count}; "not made yet" while the file is still being made."""
    parts = []
    for outcome, one, many in CAME_OF:
        if outcomes.get(outcome):
            words = one if outcomes[outcome] == 1 else many
            parts.append(f"{outcomes[outcome]:,} {words}{' yet' if going and not outcome else ''}")
    return ", ".join(parts)


def change_sections(job, wanted, page):
    """The preview's sections, each with one page of its lines: one query a section, however long the list."""
    from collections import Counter, defaultdict

    from django.db.models import Count

    from catalogue.management.commands.data_files import SECTIONS
    from catalogue.models import DataChange, DataImport

    Status, State = DataImport.Status, DataChange.State
    states = (job.counts or {}).get("state", {})
    # Once the file is being made or undone, the ready and held headings say what came of them, in one query.
    done = job.status not in (Status.QUEUED, Status.CHECKING, Status.CHECKED, Status.REFUSED, Status.DISCARDED)
    outcomes = defaultdict(Counter)
    if done:
        for state, outcome, count in (DataChange.objects.filter(data_import_id=job.pk, state__in=[State.READY, State.HELD])
                                      .values_list("state", "outcome").annotate(count=Count("pk")).order_by()):
            outcomes[state][outcome] = count
    going = job.status in (Status.APPLY_QUEUED, Status.APPLYING, Status.UNDO_QUEUED, Status.UNDOING)
    sections = []
    for state, heading in SECTIONS:
        count = states.get(state, 0)
        if not count:
            continue
        pages = -(-count // LINES_PER_PAGE)
        number = min(page, pages) if wanted == state else 1
        start = (number - 1) * LINES_PER_PAGE
        # Not job.changes: through the relation each row would load its own upload id again.
        lines = DataChange.objects.filter(data_import_id=job.pk, state=state).order_by("step", "seq").only(
            "sheet", "row", "what", "why", "warning", "state", "kind", "outcome", "outcome_note")[
            start:start + LINES_PER_PAGE]
        result = came_of(outcomes[state], going) if done and state in (State.READY, State.HELD) else ""
        sections.append({
            "state": state, "heading": f"{heading} ({count:,})" + (f": {result}" if result else ""),
            "done": done and state in (State.READY, State.HELD),
            "left": done and state == State.HELD and not job.include_held and bool(outcomes[state].get("")),
            "lines": list(lines), "page": number, "pages": pages,
            "where": f"Lines {start + 1:,} to {min(start + LINES_PER_PAGE, count):,} of {count:,}",
            # What needs his eye is open; what is already so, or changed on the site, is a tap away.
            "open": wanted == state or (not wanted and state in ("ready", "held", "problem")),
        })
    return sections


def plural(count, one, many):
    return f"{count:,} {one if count == 1 else many}"


def result_line(job):
    """What making or undoing the file did, in a sentence or two."""
    from catalogue.models import DataChange, DataImport

    Outcome = DataChange.Outcome
    counts = job.counts or {}
    if job.status == DataImport.Status.APPLIED:
        made = counts.get("outcome", {})
        text = f"Made {plural(made.get(Outcome.APPLIED, 0), 'change', 'changes')}."
        left = [(made.get(Outcome.SKIPPED, 0), "was left as it was", "were left as they were"),
                (made.get(Outcome.FAILED, 0), "could not be made", "could not be made")]
    elif job.status == DataImport.Status.UNDONE:
        undone = counts.get("undo", {})
        text = f"Undid {plural(undone.get(Outcome.UNDONE, 0), 'change', 'changes')}."
        left = [(undone.get(Outcome.UNDO_SKIPPED, 0), "was left as it is", "were left as they are")]
    else:
        return ""
    parts = [plural(count, one, many) for count, one, many in left if count]
    if parts:
        listed = sum(count for count, _one, _many in left)
        text += (f" {' and '.join(parts)}. {'It is' if listed == 1 else 'Each is'} listed below with the reason.")
    return text


@owner_only
def data_import_page(request, pk):
    """One uploaded spreadsheet: being checked, then its changes in plain words with the buttons to make them,
    how far making or undoing them has got, and what was made or left as it was."""
    from django.contrib import messages
    from django.db import DatabaseError
    from django.db.models import BooleanField, ExpressionWrapper

    from catalogue import data_jobs
    from catalogue.models import DataChange, DataImport

    if request.method == "POST":
        try:
            import_tap(request, pk)
        except DatabaseError:
            messages.error(request, BUSY)
        return HttpResponseRedirect(reverse("data_import", args=[pk]))
    Status, Outcome, State = DataImport.Status, DataChange.Outcome, DataChange.State
    job = get_object_or_404(
        DataImport.objects.defer("upload")
        .annotate(kept=ExpressionWrapper(Q(upload__isnull=False), output_field=BooleanField())), pk=pk)
    now = timezone.now()
    data_jobs.wake_runner([(job.status, job.created_at, job.heartbeat_at)], now)
    counts = job.counts or {}
    states = counts.get("state", {})
    ready, held = states.get(State.READY, 0), states.get(State.HELD, 0)
    page = request.GET.get("page", "")
    page = int(page) if page.isdigit() and int(page) > 0 else 1
    listed = job.status not in (Status.QUEUED, Status.CHECKING, Status.REFUSED) and job.checked_at is not None
    made, undone = counts.get("outcome", {}), counts.get("undo", {})
    # A file stopped three times while being made may have made some changes: those can be undone.
    changes = DataChange.objects.filter(data_import_id=job.pk)
    failed_part_made = job.status == Status.FAILED and changes.filter(outcome=Outcome.APPLIED).exists()
    merged = (job.status == Status.APPLIED
              and changes.filter(kind=DataChange.Kind.MERGE, outcome=Outcome.APPLIED).exists())
    left = []
    if job.status == Status.APPLIED and (made.get(Outcome.SKIPPED) or made.get(Outcome.FAILED)):
        left = changes.filter(outcome__in=[Outcome.SKIPPED, Outcome.FAILED])
    elif job.status == Status.UNDONE and undone.get(Outcome.UNDO_SKIPPED):
        left = changes.filter(outcome=Outcome.UNDO_SKIPPED)
    if left:
        left = list(left.order_by("step", "seq").only("what", "outcome", "outcome_note")[:LINES_PER_PAGE])
    progress = ""
    if job.status in (Status.APPLYING, Status.UNDOING):
        doing = "Making" if job.status == Status.APPLYING else "Undoing"
        progress = f"{doing} changes: {job.done:,} of {job.total:,}."
    elif job.status in (Status.APPLY_QUEUED, Status.UNDO_QUEUED):
        progress = f"Waiting to {'make' if job.status == Status.APPLY_QUEUED else 'undo'} the changes."
    if progress and job.note:
        progress += f" {job.note}."
    context = {
        **admin.site.each_context(request), "title": job.file_name, "job": job, "progress": progress,
        "refresh": job.status in data_jobs.WAITING | data_jobs.RUNNING,
        "listed": listed, "summary": counts.get("summary", ""), "unread": counts.get("unread", []),
        "sections": change_sections(job, request.GET.get("section", ""), page) if listed else [],
        "fresh": job.checked_at is not None and job.checked_at >= now - PREVIEW_FRESH,
        "make_label": f"Make {plural(ready, 'change', 'changes')}" if ready else "",
        "make_all_label": (f"Make {plural(ready + held, 'change', 'changes')}, including the {held:,} held for your "
                           "say") if held else "",
        # Made without the held changes: they can still be made from the same file uploaded again.
        "held_left": held if job.status == Status.APPLIED and not job.include_held else 0,
        "can_undo": ((job.status == Status.APPLIED and made.get(Outcome.APPLIED)) or failed_part_made
                     or (job.status == Status.UNDONE and undone.get(Outcome.UNDO_SKIPPED))),
        "can_discard": job.status == Status.CHECKED or (job.status == Status.FAILED and not failed_part_made),
        "result": result_line(job), "left": left, "merges": merged,
    }
    return render(request, "admin/data_import.html", context)
