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
from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import checks, languages, not_products
from catalogue.classify import tidy_name
from catalogue.management.commands.merge_duplicates import (
    CERTAIN, CERTAINTY_LABELS, LIKELY, certainty, duplicate_pairs, shown_products, undone_merges, why_not_merge,
)
from catalogue.matching import MOJIBAKE, own_name_off, set_codes
from catalogue.models import CheckAnswer, ClaudeAsk, Game, Listing, Product, ProductAlias, ProductSet, RemovedProduct
from catalogue.pricing import MARKETPLACES

from guides.access import not_for_editors

from . import sitemaps

EXAMPLES = 5               # examples shown beside a count
EXAMPLES_PER_REASON = 6    # possible duplicate pairs shown for each reason they wait
SHOP_PAIRS_LISTED = 40     # same-name pairs a shop lists both of, shown with that shop's two listings
KINDS_LISTED = 8           # pairs of product kinds shown for the pairs filed under different kinds
CHUNK = 400                # ids in one query, well inside SQLite's limit
CLOSE_PRICE = Decimal("0.10")   # two prices within a tenth of the dearer one are about the same price
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


SHOP_REASONS = ("a shop sells both", "a shop's price would leave the site")
# How alike the two names of a pair a shop stands in the way of are, as the reason says it.
ALIKE = {CERTAIN: "the names are the same product", LIKELY: "the names are nearly the same"}
SET_ONLY = "the names only share a set or code"


def kept_apart_by():
    """{frozenset of two product pks: whether the owner said so} for the pairs someone said are not the same and
    whose answer stands. Claude's answers are kept apart too, and the page says whose each is. One query."""
    rows = CheckAnswer.objects.filter(kind=CheckAnswer.Kind.APART, undone_at__isnull=True, product__isnull=False,
                                      other__isnull=False).values_list("product_id", "other_id", "by_owner")
    said = {}
    for product_id, other_id, by_owner in rows:
        pair = frozenset((product_id, other_id))
        said[pair] = said.get(pair, False) or by_owner
    return said


def why_it_waits(keep, other, apart, undone, shops):
    """(why a possible duplicate pair has not merged by itself, in the owner's words; whether the rules call the
    two names one product while a shop stands in the way)."""
    pair = frozenset((keep.pk, other.pk))
    if pair in undone:
        return "Its merge was undone", False
    if pair in apart:
        return ("You said they are not the same" if apart[pair] else "Claude said they are not the same"), False
    different = why_not_merge(keep, other)
    if different:
        return f"The names differ: {different}", False
    if languages.product_language(keep) != languages.product_language(other):
        return "Their languages may differ", False
    if keep.product_type != other.product_type:
        return "They are filed under different kinds of product", False
    blocked = checks.shops_in_the_way(shops.get(keep.pk, {}), shops.get(other.pk, {}))
    if blocked:
        level = certainty(keep, other)
        return f"{blocked[0].upper()}{blocked[1:]}, and {ALIKE.get(level, SET_ONLY)}", level in ALIKE
    return CERTAINTY_LABELS[certainty(keep, other)], False


def money(value):
    return f"£{value:,.2f}"


def said_of(listing, now):
    """One shop listing as the log gives it: its price, its stock and how long ago it was checked."""
    days = (now - listing["last_checked"]).days
    checked = "today" if days < 1 else f"{days} day{'s' if days != 1 else ''} ago"
    return f"{money(listing['price'])} {Listing.Availability(listing['availability']).label.lower()}, checked {checked}"


def shop_evidence(pairs, now):
    """For the pairs the rules call one product but a shop lists both of: what that shop's two listings say, so
    the owner can see whether it is one item listed twice or two items. {"kinds": [(label, pairs)], "listed":
    [{"keep", "other", "shop", "first", "second"}]}. One query for each CHUNK products."""
    ids = sorted({product.pk for pair in pairs for product in pair})
    listings = {}
    for start in range(0, len(ids), CHUNK):
        rows = (Listing.objects.filter(product_id__in=ids[start:start + CHUNK], is_active=True)
                .exclude(retailer__source_type__in=MARKETPLACES)
                .values("product_id", "retailer_id", "retailer__name", "price", "availability", "last_checked"))
        for row in rows:
            listings.setdefault(row["product_id"], {})[row["retailer_id"]] = row
    kinds, listed = Counter(), []
    for keep, other in pairs:
        ours, theirs = listings.get(keep.pk, {}), listings.get(other.pk, {})
        for shop in sorted(set(ours) & set(theirs)):
            first, second = ours[shop], theirs[shop]
            on_sale = [row["availability"] in Listing.BUYABLE for row in (first, second)]
            low, high = sorted((first["price"], second["price"]))
            close = high - low <= high * CLOSE_PRICE
            if all(on_sale):
                kind = "Both on sale at about the same price" if close else "Both on sale at different prices"
            elif any(on_sale):
                kind = "One on sale, the other sold out"
            else:
                kind = "Both sold out"
            kinds[kind] += 1
            if len(listed) < SHOP_PAIRS_LISTED:
                listed.append({"keep": keep, "other": other, "shop": first["retailer__name"], "kind": kind,
                               "first": said_of(first, now), "second": said_of(second, now)})
            break   # one shop is enough to say why the pair waits
    return {"kinds": kinds.most_common(), "listed": listed}


def duplicates(products, now):
    """The possible duplicate pairs among ``products`` and why each waits, with a few examples of each reason, what
    the shops say of the pairs the rules call one product, and the merges of the last RECENT_DAYS days. A handful
    of queries."""
    apart, undone, shops = kept_apart_by(), undone_merges(), checks.shop_listings()
    reasons, by_reason, kinds, same_name = Counter(), {}, Counter(), []
    pairs = duplicate_pairs(products)
    for keep, other in pairs:
        why, held_by_shop = why_it_waits(keep, other, apart, undone, shops)
        reasons[why] += 1
        some = by_reason.setdefault(why, [])
        if len(some) < EXAMPLES_PER_REASON:
            some.append({"keep": keep, "other": other})
        if held_by_shop:
            same_name.append((keep, other))
        if keep.product_type != other.product_type and why.startswith("They are filed"):
            kinds[" and ".join(sorted((keep.type_name, other.type_name)))] += 1
    merges = CheckAnswer.objects.filter(kind=CheckAnswer.Kind.MERGE, created_at__gte=now - timedelta(days=RECENT_DAYS))
    made = merges.aggregate(
        hourly=Count("pk", filter=Q(by_owner=False, ask__isnull=True)), claude=Count("pk", filter=Q(ask__isnull=False)),
        owner=Count("pk", filter=Q(by_owner=True, ask__isnull=True)), undone=Count("pk", filter=Q(undone_at__isnull=False)))
    return {
        "pairs": len(pairs),
        "reasons": [{"why": why, "count": count, "some": by_reason[why]} for why, count in reasons.most_common()],
        "kinds": kinds.most_common(KINDS_LISTED),
        "same_name": len(same_name),
        "shops": shop_evidence(same_name, now),
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
    out.append("Why each pair waits, with a few of each:")
    for reason in duplicate["reasons"]:
        out.append(f"    {reason['count']} x {reason['why']}")
        out.extend(f"        {pair['keep'].name}  |  {pair['other'].name}" for pair in reason["some"])
    if duplicate["kinds"]:
        out.append("Filed under different kinds, the commonest:")
        out.extend(f"    {count} x {kinds}" for kinds, count in duplicate["kinds"])
    out.extend(["", "SAME PRODUCT BY THE RULES, BUT A SHOP LISTS BOTH",
                f"Pairs: {duplicate['same_name']}"])
    out.extend(f"{kind}: {count}" for kind, count in duplicate["shops"]["kinds"])
    if duplicate["shops"]["listed"]:
        out.append(f"The first {len(duplicate['shops']['listed'])}, with what that shop says of each page:")
        for pair in duplicate["shops"]["listed"]:
            out.append(f"    {pair['keep'].name}  |  {pair['other'].name}")
            out.append(f"        {pair['shop']}: {pair['first']}  |  {pair['second']}  ({pair['kind']})")
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
