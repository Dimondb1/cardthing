"""
How to compare sealed TCG prices in the UK: a guide whose examples are read from the catalogue as the page is built
(the owner, 9 Nov 2026: "Publish a UK guide to comparing sealed TCG prices, delivery and stock ... Use live examples
from the database"). The other guides are written in Admin; this one is built here, because each of its points is
shown with a real product, shop and price that must still be true when it is read.

Its words are in Site wording (content/registry.py, the guides.compare keys). This module only works out the examples:
a product whose lowest list price is not its cheapest delivered price, one sitting just under a shop's free delivery
line, the stock, pre-order and raffle counts, a price a pack, a price against its RRP and its last month, an English
and a Japanese box side by side, and what RipRaptor asks of an eBay seller. An example that cannot be made from
today's prices is left out, with its sentence: nothing is written in by hand.

The examples are kept for KEEP seconds, so the page costs a visitor one cache read. It is open while the Guides pages
are (a guide is published), listed on the Guides page, in the sitemap and in llms.txt.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta

from django.core.cache import cache
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from catalogue.models import DailyLowestPrice, Listing, Product, Retailer
from catalogue.pricing import MARKETPLACES

SLUG = "compare-sealed-tcg-prices-uk"
PUBLISHED = date(2026, 11, 9)     # the day the guide went on the site
KEY = "guide-compare:v1"
KEEP = 60 * 60
NEAR_LINE = 5                     # pounds under a shop's free delivery line that still count as just under it
HISTORY_DAYS = 30
GAMES_TRIED = 3                   # the biggest games looked through for a box and pack, and a Japanese box
SECTIONS = ("delivered", "threshold", "stock", "packs", "rrp", "language", "marketplaces")
# The kinds of product the examples are picked from: the ones people compare, and few enough to read at once.
KINDS = (Product.Type.BOOSTER_BOX, Product.Type.ELITE_TRAINER_BOX, Product.Type.BUNDLE)


def examples(fresh=False):
    """The guide's live examples (build), kept for KEEP seconds; built each time while pages are not kept (tests)."""
    from web import pagecache

    if pagecache.seconds() <= 0:
        return build()
    found = None if fresh else cache.get(KEY)
    if found is None:
        found = build()
        cache.set(KEY, found, KEEP)
    return found


def offer_rows():
    """Every offer the examples may name: English-language boxes and bundles in stock at a UK shop, at a known
    delivered price nothing doubts. One query."""
    from web.answers import shop_offers

    fields = ("product_id", "retailer__name", "price", "delivery_cost", "delivered_price",
              "retailer__free_delivery_over", "last_checked")
    return [dict(zip(("product", "shop", "price", "delivery", "delivered", "line", "checked"), row))
            for row in shop_offers().filter(product__language="", product__product_type__in=KINDS).order_by()
            .values_list(*fields)]


def delivered_example(by_product):
    """A product where the shop with the lowest list price is not the cheapest delivered: the product with the most
    shops, then the biggest difference. {"product": pk, "low": the lowest list price's offer, "best": the cheapest
    delivered, "saving", "shops"} or None."""
    found = []
    for product, offers in by_product.items():
        if len(offers) < 2:
            continue
        low = min(offers, key=lambda offer: (offer["price"], offer["delivered"], offer["shop"]))
        best = min(offers, key=lambda offer: (offer["delivered"], offer["price"], offer["shop"]))
        if low["shop"] != best["shop"] and low["price"] < best["price"] and best["delivered"] < low["delivered"]:
            found.append({"product": product, "low": low, "best": best, "saving": low["delivered"] - best["delivered"],
                          "shops": len(offers)})
    return max(found, key=lambda row: (row["shops"], row["saving"], -row["product"]), default=None)


def plain_example(by_product):
    """With no product to show the difference: the cheapest offer of the product with the most shops, so the guide
    still shows a price and its delivery added up. {"product": pk, "best": offer, "shops"} or None."""
    if not by_product:
        return None
    product, offers = max(by_product.items(), key=lambda item: (len(item[1]), -item[0]))
    return {"product": product, "best": min(offers, key=lambda offer: (offer["delivered"], offer["shop"])),
            "shops": len(offers)}


def threshold_example(rows):
    """An offer priced just under its shop's free delivery line, so delivery is charged: the nearest to the line.
    {"product": pk, "offer", "gap"} or None."""
    near = [row for row in rows if row["line"] is not None and row["delivery"]
            and 0 < row["line"] - row["price"] <= NEAR_LINE]
    if not near:
        return None
    offer = min(near, key=lambda row: (row["line"] - row["price"], row["product"], row["shop"]))
    return {"product": offer["product"], "offer": offer, "gap": offer["line"] - offer["price"]}


def delivery_numbers():
    """How the UK shops compared charge for delivery: {"shops", "free" (every order), "lines" (free over a value),
    "common" (the value most of them set), "low" and "high" (the standard charges of those that charge), "unknown"}.
    The shops are the ones pricing.shops_compared counts. One query."""
    current = (Listing.objects.current().exclude(sanity=Listing.Sanity.EXCLUDED).order_by()
               .values("retailer_id").distinct())
    shops = list(Retailer.objects.filter(pk__in=current).exclude(source_type__in=MARKETPLACES)
                 .values_list("delivery_cost", "free_delivery_over"))
    charges = sorted(cost for cost, _line in shops if cost)
    lines = Counter(line for cost, line in shops if line is not None and cost != 0)
    return {
        "shops": len(shops),
        "free": sum(1 for cost, _line in shops if cost == 0),
        "lines": sum(lines.values()),
        "common": max(lines.items(), key=lambda item: (item[1], -item[0]))[0] if lines else None,
        "low": charges[0] if charges else None,
        "high": charges[-1] if charges else None,
        "unknown": sum(1 for cost, _line in shops if cost is None),
    }


def preorder_example():
    """The pre-order whose set comes out soonest, at its cheapest: an offer with its product, set and shop, or None."""
    from web import hubs

    today = timezone.localdate()
    return (hubs.preorder_listings()
            .filter(delivery_known=True, sanity=Listing.Sanity.OK, product__is_active=True, product__language="",
                    product__product_set__release_date__gt=today)
            .exclude(retailer__source_type__in=MARKETPLACES)
            .select_related("product__product_set", "retailer")
            .order_by("product__product_set__release_date", "delivered_price", "retailer__name", "pk").first())


def rrp_example(by_product):
    """A product in stock whose RRP is known, the one most shops have: {"product": pk, "best": offer, "rrp", "low",
    "high" (its cheapest delivered price over HISTORY_DAYS, or None)} or None. Two queries."""
    known = (Product.objects.filter(pk__in=list(by_product))
             .filter(Q(rrp__gt=0) | Q(shop_rrp__gt=0) | Q(found_rrp__gt=0)))
    priced = [(product, product.rrp_now) for product in known]
    priced = [(product, rrp[0]) for product, rrp in priced if rrp]
    if not priced:
        return None
    product, rrp = max(priced, key=lambda item: (len(by_product[item[0].pk]), -item[0].pk))
    since = timezone.localdate() - timedelta(days=HISTORY_DAYS)
    history = DailyLowestPrice.objects.filter(product=product, date__gte=since).aggregate(low=Min("price"), high=Max("price"))
    return {"product": product.pk, "best": min(by_product[product.pk], key=lambda offer: (offer["delivered"], offer["shop"])),
            "rrp": rrp, "low": history["low"], "high": history["high"]}


def game_examples():
    """{"packs": a booster box's price a pack beside a single pack's (answers.pack_facts) with its game, "language": a
    game's cheapest English and Japanese booster boxes with how many of its products are Japanese}: each from the
    first of the biggest games that has one, or None. The games' own figures come from their pages' kept copies."""
    from web import answers, feed, views

    packs = language = None
    for game in feed.games_with_products()[:GAMES_TRIED]:
        facts = views.shared_for_game(f"answers/{game.pk}", lambda game=game: answers.game_facts(game))
        if packs is None:
            found = answers.pack_facts(facts)
            if found:
                packs = {**found, "game": game}
        if language is None:
            english = next((pick for pick in facts["picks"] if pick.kind == Product.Type.BOOSTER_BOX), None)
            japanese = answers.cheapest(answers.shop_offers().filter(
                product__game=game, product__language="ja", product__product_type=Product.Type.BOOSTER_BOX))
            if english is not None and japanese is not None:
                language = {
                    "game": game, "english": english.listing, "japanese": japanese,
                    "count": Product.objects.active().filter(game=game, language="ja").count(),
                    "listed": views.game_stats(game).products,
                }
    return {"packs": packs, "language": language}


def build():
    """Everything the guide quotes, from the catalogue now."""
    from catalogue import ebay
    from web import hubs

    rows = offer_rows()
    by_product = defaultdict(list)
    for row in rows:
        by_product[row["product"]].append(row)
    found = {
        "delivered": delivered_example(by_product),
        "plain": None,
        "threshold": threshold_example(rows),
        "delivery": delivery_numbers(),
        "rrp": rrp_example(by_product),
        "preorder": preorder_example(),
        **game_examples(),
    }
    if found["delivered"] is None:
        found["plain"] = plain_example(by_product)
    # The products the row examples name, with their addresses, in one query.
    wanted = {example["product"] for example in (found["delivered"], found["plain"], found["threshold"], found["rrp"])
              if example}
    products = Product.objects.select_related("game").in_bulk(wanted)
    for name in ("delivered", "plain", "threshold", "rrp"):
        if found[name]:
            found[name]["product"] = products[found[name]["product"]]
    everything = hubs.hub_stats()
    found["stock"] = {
        "in_stock": everything.in_stock, "preorder": everything.preorder, "listed": everything.products,
        "lottery": Listing.objects.current().filter(availability=Listing.Availability.LOTTERY).count(),
    }
    # The eBay and Amazon prices in the comparison now, counted in the database (one query).
    counted = {row["retailer__source_type"]: row["n"] for row in (
        Listing.objects.buyable().filter(retailer__source_type__in=MARKETPLACES).order_by()
        .values("retailer__source_type").annotate(n=Count("pk")))}
    found["marketplaces"] = {
        "ebay": counted.get(Retailer.Source.EBAY, 0),
        "amazon": counted.get(Retailer.Source.AMAZON, 0),
        "feedback": f"{ebay.MIN_FEEDBACK:g}", "ratings": ebay.MIN_RATINGS,
    }
    found["checked"] = latest_check(found, rows)
    return found


def latest_check(found, rows):
    """The latest check among the prices the examples quote, or of any offer read, or None."""
    moments = []
    for name in ("delivered", "plain"):
        if found[name]:
            moments += [found[name]["best"]["checked"]] + ([found[name]["low"]["checked"]] if "low" in found[name] else [])
    if found["threshold"]:
        moments.append(found["threshold"]["offer"]["checked"])
    if found["rrp"]:
        moments.append(found["rrp"]["best"]["checked"])
    if found["preorder"]:
        moments.append(found["preorder"].last_checked)
    return max(moments, default=None) or max((row["checked"] for row in rows), default=None)


def modified(found):
    """The day the guide last changed: the day its examples' prices were last checked, never before PUBLISHED."""
    checked = found.get("checked")
    return max(PUBLISHED, timezone.localtime(checked).date()) if checked else PUBLISHED
