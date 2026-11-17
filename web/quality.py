"""
Catalogue quality: one Admin page that counts what is wrong with the catalogue, with a Log to copy.

The audit of 17 Nov 2026 found duplicate pages, pages that were no product, shop wording in names and products with
no set by reading the live site from outside, because nothing inside showed them in one place. The owner approved
this page ("Yes"): what a search engine is offered, how many products are a real comparison, the possible
duplicates and why each still waits, names that need tidying, products filed under no set or with no picture, the
pages that may be no product, and the old addresses. Each count comes with a few examples, and the whole page is
also plain text in a box to copy and paste.

It only reads: nothing here changes a product, a price or an address. It reads every product on the site once and
pairs the possible duplicates, as Things to check does, so it takes a few seconds; it is opened when wanted, never
on a visitor's request. Staff only, never for editors, never indexed or cached. A dozen queries, whatever the
number of products.
"""

from collections import Counter
from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import checks, languages, not_products
from catalogue.classify import tidy_name
from catalogue.management.commands.merge_duplicates import (
    CERTAINTY_LABELS, certainty, duplicate_pairs, shown_products, undone_merges, why_not_merge,
)
from catalogue.matching import MOJIBAKE, own_name_off, set_codes
from catalogue.models import CheckAnswer, ClaudeAsk, Game, Listing, Product, ProductAlias, ProductSet, RemovedProduct
from catalogue.pricing import MARKETPLACES

from guides.access import not_for_editors

from . import sitemaps

EXAMPLES = 5               # examples shown beside a count
PAIRS_LISTED = 40          # possible duplicate pairs the log lists
LONG_NAME = 80             # characters past which a name is called long
RECENT_DAYS = 30

GAME_WORDING = ("tcg", "trading card game")


def examples(rows, most=EXAMPLES):
    """The first ``most`` names of ``rows`` (products), for showing beside a count."""
    return [product.name for product in rows[:most]]


def comparison():
    """How many products are a real comparison. Two queries."""
    stats = Product.objects.for_lists().aggregate(
        total=Count("id"), in_stock=Count("id", filter=Q(in_stock_count__gte=1)),
        compared=Count("id", filter=Q(in_stock_count__gte=2)))
    buyable = Listing.objects.buyable().filter(product=OuterRef("pk"))
    priced = Product.objects.active().filter(Exists(buyable)).count()
    return [
        ("Products on the site", stats["total"]),
        ("In stock at two shops or more (a real comparison)", stats["compared"]),
        ("In stock at exactly one shop", stats["in_stock"] - stats["compared"]),
        ("With a price to quote now (in stock or on pre-order)", priced),
        ("With no price to quote now", stats["total"] - priced),
    ]


def offered(total):
    """What the sitemap offers search engines, counted as the sitemap counts it. Four queries."""
    from guides.models import Guide

    products = sitemaps.indexable_products().count()
    return [
        ("Product pages the sitemap offers", products),
        (f"Product pages on the site but left out (no shop has priced them for {sitemaps.RECENT_DAYS} days)", total - products),
        ("Set pages", ProductSet.objects.filter(game__is_active=True).count()),
        ("Game pages", Game.objects.filter(is_active=True).count()),
        ("Guides published", Guide.objects.filter(is_published=True).count()),
    ]


def why_it_waits(keep, other, apart, undone, shops):
    """Why a possible duplicate pair has not merged by itself, in the owner's words."""
    pair = frozenset((keep.pk, other.pk))
    if pair in undone:
        return "Its merge was undone"
    if pair in apart:
        return "You said they are not the same"
    different = why_not_merge(keep, other)
    if different:
        return f"The names differ: {different}"
    if languages.product_language(keep) != languages.product_language(other):
        return "Their languages may differ"
    if keep.product_type != other.product_type:
        return "They are filed under different kinds of product"
    blocked = checks.shops_in_the_way(shops.get(keep.pk, {}), shops.get(other.pk, {}))
    if blocked:
        return blocked[0].upper() + blocked[1:]
    return CERTAINTY_LABELS[certainty(keep, other)]


def duplicates(products, now):
    """The possible duplicate pairs among ``products`` and why each waits, and the merges of the last RECENT_DAYS
    days. Four queries."""
    apart, undone, shops = checks.kept_apart(), undone_merges(), checks.shop_listings()
    pairs = [(keep, other, why_it_waits(keep, other, apart, undone, shops)) for keep, other in duplicate_pairs(products)]
    reasons = Counter(why for _keep, _other, why in pairs)
    merges = CheckAnswer.objects.filter(kind=CheckAnswer.Kind.MERGE, created_at__gte=now - timedelta(days=RECENT_DAYS))
    made = merges.aggregate(
        hourly=Count("pk", filter=Q(by_owner=False, ask__isnull=True)), claude=Count("pk", filter=Q(ask__isnull=False)),
        owner=Count("pk", filter=Q(by_owner=True, ask__isnull=True)), undone=Count("pk", filter=Q(undone_at__isnull=False)))
    return {
        "pairs": len(pairs),
        "reasons": reasons.most_common(),
        "listed": [{"keep": keep, "other": other, "why": why} for keep, other, why in pairs[:PAIRS_LISTED]],
        "merges": [(f"Merged by the hourly tidy in the last {RECENT_DAYS} days", made["hourly"]),
                   ("Merged by Claude", made["claude"]), ("Merged by you", made["owner"]),
                   ("Of those, undone", made["undone"])],
    }


def names(products):
    """Names that need tidying, each a (label, count, examples). One query (the shops listing each product)."""
    shops = {}
    for product_id, shop in (Listing.objects.exclude(retailer__source_type__in=MARKETPLACES)
                             .filter(product__is_active=True).values_list("product_id", "retailer__name").order_by()):
        shops.setdefault(product_id, set()).add(shop)
    waiting, shop_named, game_worded, english, long_names, no_kind, damaged = [], [], [], [], [], [], []
    for product in products:
        name = product.name
        lowered = name.lower()
        if tidy_name(name) != name:
            waiting.append(product)
        if any(own_name_off(name, shop) != name for shop in shops.get(product.pk, ())):
            shop_named.append(product)
        if any(f" {wording} " in f" {lowered} " for wording in GAME_WORDING):
            game_worded.append(product)
        if " english " in f" {lowered} ":
            english.append(product)
        if len(name) > LONG_NAME:
            long_names.append(product)
        if not not_products.KIND_WORDS.search(name):
            no_kind.append(product)
        if MOJIBAKE.search(name):
            damaged.append(product)
    return [
        ("Names the hourly tidy will rewrite at its next run", waiting),
        ("Names carrying the name of a shop that lists them", shop_named),
        ('Names with "TCG" or "Trading Card Game" in them', game_worded),
        ('Names with the word "English"', english),
        (f"Names over {LONG_NAME} characters", long_names),
        ("Names with no word for a kind of product", no_kind),
        ("Names with damaged text from a shop's feed", damaged),
    ]


def filing(products):
    """Products with no set or no picture, each a (label, count, examples), and the products by language."""
    no_set = [product for product in products if not product.product_set_id]
    could_file = [product for product in no_set if len(set_codes(product.name, product.game.slug)) == 1]
    no_picture = [product for product in products if not product.image and not product.image_url]
    by_language = Counter(languages.name(product.language) for product in products)
    return {
        "rows": [("Filed under no set", no_set),
                 ("Of those, with one set code in the name (could be filed)", could_file),
                 ("With no picture", no_picture)],
        "languages": by_language.most_common(),
    }


def not_a_product(now):
    """Pages that may be no product: who they wait for, and what has been taken down. A few queries."""
    waiting = not_products.progress()
    since = now - timedelta(days=RECENT_DAYS)
    by_claude = CheckAnswer.objects.filter(kind=CheckAnswer.Kind.NOT_SEALED, ask__kind=ClaudeAsk.Kind.NOT_PRODUCT,
                                           created_at__gte=since, undone_at__isnull=True).count()
    return [
        ("May not be products, waiting for you", waiting["needs_you"]),
        ("May not be products, waiting for Claude", waiting["waiting"]),
        (f"Taken off the site by Claude in the last {RECENT_DAYS} days", by_claude),
        (f"Pages the hourly tidy removed in the last {RECENT_DAYS} days", RemovedProduct.objects.filter(removed_at__gte=since).count()),
    ]


def addresses():
    """Old addresses and pages switched off. Three queries."""
    return [
        ("Old addresses that redirect to a live page", ProductAlias.objects.filter(product__is_active=True).count()),
        ("Removed addresses that say the page has gone", RemovedProduct.objects.count()),
        ("Pages switched off (they say the page has gone, or wait for a first check)", Product.objects.filter(is_active=False).count()),
    ]


def gather(now=None):
    """Everything the page and its log show."""
    now = now or timezone.now()
    products = list(shown_products())
    total = len(products)
    name_rows = names(products)
    filed = filing(products)
    return {
        "at": now,
        "comparison": comparison(),
        "offered": offered(total),
        "duplicates": duplicates(products, now),
        "names": [(label, len(rows), examples(rows)) for label, rows in name_rows],
        "filing": [(label, len(rows), examples(rows)) for label, rows in filed["rows"]],
        "languages": filed["languages"],
        "not_products": not_a_product(now),
        "addresses": addresses(),
    }


def log_text(data):
    """The whole page as plain text, for the owner to copy and paste. No key, token or address of a person is in
    it: product names and counts only."""
    out = [f"RipRaptor catalogue quality, {timezone.localtime(data['at']):%a %d %b %Y %H:%M} UK time"]

    def section(title, rows):
        out.extend(["", title.upper()])
        out.extend(f"{label}: {count}" for label, count in rows)

    def listed(title, rows):
        out.extend(["", title.upper()])
        for label, count, some in rows:
            out.append(f"{label}: {count}")
            out.extend(f"    {name}" for name in some)

    section("A real comparison", data["comparison"])
    section("What search engines are offered", data["offered"])
    duplicate = data["duplicates"]
    section("Possible duplicates", [("Pairs waiting", duplicate["pairs"]), *duplicate["merges"]])
    out.append("Why each pair waits:")
    out.extend(f"    {count} x {why}" for why, count in duplicate["reasons"])
    if duplicate["listed"]:
        out.append(f"The first {len(duplicate['listed'])} pairs:")
        for pair in duplicate["listed"]:
            out.append(f"    {pair['keep'].name}  |  {pair['other'].name}  |  {pair['why']}")
    listed("Names", data["names"])
    listed("Filing", data["filing"])
    out.append("By language: " + ", ".join(f"{language} {count}" for language, count in data["languages"]))
    section("Not products", data["not_products"])
    section("Addresses", data["addresses"])
    return "\n".join(out)


@staff_member_required
@not_for_editors
@require_GET
def quality_page(request):
    data = gather()
    response = render(request, "admin/catalogue_quality.html", {
        **admin.site.each_context(request), "title": "Catalogue quality", "data": data, "log": log_text(data),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
