"""
The written answers on a game's page: the cheapest product of each main kind in stock now, with its delivered price,
shop and when it was checked, and the questions people ask a search engine or an AI assistant, answered with the same
live figures (the owner, 9 Nov 2026: "Make the existing Pokémon page answer price, delivery and stock comparison
queries"). Nothing here is written by hand: every number, product, shop and time is read from the catalogue when the
page is built, and the wording is in Site wording (content/registry.py, the browse.hub.answer and browse.hub.faq
keys), so a sentence is only shown when its figures exist.

One source for the counts. The page's own line, its search engine description, its questions and llms.txt all quote
the one HubStats the game's page keeps (web/hubs.py hub_stats, views.game_stats), so they cannot disagree: products
listed, products in stock, shops with one in stock. Each is named for what it counts.

The questions are shown on the page and given again as schema.org FAQPage, from the same list of (question, answer)
strings, so the two match word for word. The products named are an ItemList (hubs.item_list_json).
"""

from dataclasses import dataclass

from django.db.models import Count, Max
from django.utils import timezone
from django.utils.formats import date_format

from catalogue.models import Listing, Product
from catalogue.pricing import MARKETPLACES, shops_compared
from catalogue.types import DEFAULT, GAME_TYPES, PLURALS, type_label

from . import hubs

# The kinds a game's answer leads with, in this order (the owner, 9 Nov 2026). Another game gets the first OTHER_GAMES
# kinds it lists that are not single packs.
MAIN_TYPES = {
    "pokemon": ("booster_box", "elite_trainer_box", "bundle"),
    "magic-the-gathering": ("booster_box", "collector_booster_box", "deck", "bundle"),
}
OTHER_GAMES = 3
SINGLE_PACKS = {"booster_pack", "collector_booster_pack", "other"}
# A sentence spells these out; lists and titles keep the short word (catalogue/types.py PLURALS).
SPELLED = {"Elite Trainer Box": "Elite Trainer Boxes"}


def main_types(game):
    """The product type codes a game's answer names, in order."""
    named = MAIN_TYPES.get(game.slug)
    if named:
        return list(named)
    return [code for code, _label in GAME_TYPES.get(game.slug, DEFAULT) if code not in SINGLE_PACKS][:OTHER_GAMES]


def plural(label):
    """A product type's plural as a sentence gives it: "booster boxes", "Elite Trainer Boxes", "Commander decks"."""
    return SPELLED.get(label) or PLURALS.get(label, label.lower() + "s")


def singular(label):
    """A product type inside a sentence: "booster box", but "Elite Trainer Box" and "Commander deck" keep their
    capitals, as their plurals do."""
    return label if plural(label)[:1].isupper() else label[:1].lower() + label[1:]


def stamp(moment):
    """A moment as an answer gives it, in UK time: "9 Nov 2026 at 14:05". A date and a time, never "14 minutes ago":
    a search engine or an assistant quoting the sentence later still says when the price was checked."""
    local = timezone.localtime(moment)
    return f"{date_format(local, 'j M Y')} at {local:%H:%M}"


def shop_offers():
    """The offers an answer may name: in stock now at a UK shop (never eBay or Amazon), at a delivered price that is
    known and that nothing doubts."""
    return (hubs.in_stock_listings()
            .filter(delivery_known=True, sanity=Listing.Sanity.OK, product__is_active=True)
            .exclude(retailer__source_type__in=MARKETPLACES))


def cheapest(offers):
    return offers.select_related("product", "retailer").order_by("delivered_price", "retailer__name", "pk").first()


@dataclass
class Pick:
    kind: str           # the product type's code
    label: str          # as the game's players call it: "Booster box", "Play booster box"
    one: str            # inside a sentence: "booster box"
    many: str           # "booster boxes"
    listing: Listing    # the cheapest offer of the kind in stock, with its product and shop
    shops: int          # UK shops with one of the kind in stock
    products: int       # products of the kind in stock


def game_facts(game):
    """{"picks": [Pick] for the game's main kinds with an English-language product in stock, "pack": the cheapest
    single booster pack offer or None, "checked": the latest check of any of the game's current prices or None}.

    English-language products only: a UK buyer asking for the cheapest booster box means the English one, and the
    answers say so. One query for the counts, one for each kind in stock (at most four), one for the pack and one for
    the latest check. Kept with the game's other figures (views.shared_for_game)."""
    english = shop_offers().filter(product__game=game, product__language="")
    kinds = main_types(game)
    counted = {row["product__product_type"]: row for row in (
        english.filter(product__product_type__in=kinds).order_by().values("product__product_type")
        .annotate(shops=Count("retailer", distinct=True), products=Count("product", distinct=True)))}
    picks = []
    for kind in kinds:
        if kind not in counted:
            continue
        label = type_label(game.slug, kind)
        picks.append(Pick(kind=kind, label=label, one=singular(label), many=plural(label),
                          listing=cheapest(english.filter(product__product_type=kind)),
                          shops=counted[kind]["shops"], products=counted[kind]["products"]))
    checked = (Listing.objects.current().filter(product__game=game, product__is_active=True).order_by()
               .aggregate(at=Max("last_checked"))["at"])
    return {"picks": picks, "pack": cheapest(english.filter(product__product_type=Product.Type.BOOSTER_PACK)),
            "checked": checked}


def stock_line(words, game, stats, sets):
    """The line under a game's heading, as safe HTML: how many of its products are in stock, at how many shops, out
    of how many listed across how many sets, and that prices include delivery. With nothing in stock, the hub's usual
    line (hubs.intro), which says what is true then."""
    from django.utils.html import escape

    if not stats.in_stock or not stats.stock_shops:
        return hubs.intro(words, stats)
    return escape(words(
        "browse.hub.answer.stock",
        products=words.count("meta.products", stats.in_stock, game=game.display_short),
        shops=words.count("browse.hub.shops", stats.stock_shops), listed=f"{stats.products:,}",
        sets=words.count("browse.hub.answer.sets", sets),
    ))


def pack_facts(facts):
    """{"box": the booster box Pick, "packs", "each": its price a pack, "pack": the cheapest single pack offer,
    "single": its price} from game_facts, or None unless a booster box in stock says how many packs it holds and a
    single booster pack is in stock too."""
    from .compact import pack_price

    box = next((pick for pick in facts["picks"] if pick.kind == Product.Type.BOOSTER_BOX), None)
    pack = facts["pack"]
    if box is None or pack is None or (pack.product.packs_inside or 1) > 1:
        return None
    packs = box.listing.product.packs_inside
    each = pack_price(box.listing.delivered_price, box.kind, packs)
    if not packs or packs < 2 or each is None:
        return None
    return {"box": box, "packs": packs, "each": each, "pack": pack, "single": pack.delivered_price}


def pack_question(words, game, facts):
    """(question, answer) comparing a box's price a pack with a single pack's, or None without both (pack_facts)."""
    found = pack_facts(facts)
    if found is None:
        return None
    box, pack = found["box"], found["pack"]
    return (
        words("browse.hub.faq.packs.q", game=game.display_short, box=box.one),
        words("browse.hub.faq.packs.a_yes" if found["each"] < found["single"] else "browse.hub.faq.packs.a_no",
              box=box.one, product=box.listing.product.name, packs=found["packs"], each=hubs.money(found["each"]),
              price=hubs.money(box.listing.delivered_price), shop=box.listing.retailer.name,
              pack=pack.product.name, single=hubs.money(found["single"]), pack_shop=pack.retailer.name),
    )


def game_faq(words, game, stats, facts, sets):
    """[(question, answer)] for a game's page, five to seven of them, as plain text: the page shows them and its
    FAQPage block gives the same strings. A question whose figures are missing is left out."""
    name = game.display_short
    all_shops = words.count("browse.hub.shops", shops_compared())
    faq = []
    lead = facts["picks"][0] if facts["picks"] else None
    if lead is not None:
        offer = lead.listing
        faq.append((
            words("browse.hub.faq.cheapest.q", game=name, types=lead.many),
            words("browse.hub.faq.cheapest.a", game=name, type=lead.one, types=lead.many, product=offer.product.name,
                  shop=offer.retailer.name, price=hubs.money(offer.delivered_price), checked=stamp(offer.last_checked),
                  shops=words.count("browse.hub.shops", lead.shops)),
        ))
    delivery = [words("browse.hub.faq.delivery.a")]
    if lead is not None:
        offer = lead.listing
        key = "browse.hub.faq.delivery.a_example" if offer.delivery_cost else "browse.hub.faq.delivery.a_free"
        delivery.append(words(key, product=offer.product.name, shop=offer.retailer.name, item=hubs.money(offer.price),
                              delivery=hubs.money(offer.delivery_cost), price=hubs.money(offer.delivered_price)))
    faq.append((words("browse.hub.faq.delivery.q", game=name), " ".join(delivery)))
    stock = [words("browse.hub.faq.stock.a")]
    if stats.in_stock:
        stock.append(words("browse.hub.faq.stock.a_now", game=name, in_stock=f"{stats.in_stock:,}",
                           listed=f"{stats.products:,}"))
    if stats.preorder:
        stock.append(words.count("browse.hub.faq.stock.a_preorder", stats.preorder))
    faq.append((words("browse.hub.faq.stock.q", game=name), " ".join(stock)))
    checked = [words("browse.hub.faq.checked.a")]
    if facts["checked"]:
        checked.append(words("browse.hub.faq.checked.a_latest", game=name, checked=stamp(facts["checked"])))
    faq.append((words("browse.hub.faq.checked.q", game=name), " ".join(checked)))
    if stats.products:
        key = ("browse.hub.faq.counts.a_none" if not stats.in_stock
               else "browse.hub.faq.counts.a" if stats.stock_shops else "browse.hub.faq.counts.a_market")
        faq.append((
            words("browse.hub.faq.counts.q", game=name),
            words(key, game=name, listed=f"{stats.products:,}", sets=words.count("browse.hub.answer.sets", sets),
                  in_stock=f"{stats.in_stock:,}", shops=words.count("browse.hub.shops", stats.stock_shops),
                  all_shops=all_shops),
        ))
    packs = pack_question(words, game, facts)
    if packs:
        faq.append(packs)
    faq.append((words("browse.hub.faq.sells.q", game=name), words("browse.hub.faq.sells.a", all_shops=all_shops)))
    return [(question, hubs.tidy_stops(answer)) for question, answer in faq]


def faq_json(page_url, faq):
    """schema.org FAQPage for [(question, answer)], the strings the page shows."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "@id": page_url + "#faq",
        "mainEntity": [
            {"@type": "Question", "name": question, "acceptedAnswer": {"@type": "Answer", "text": answer}}
            for question, answer in faq
        ],
    }


def listed_products(picks, others):
    """The products a game's page names, once each: its answer's picks first, then ``others`` (the deals shown)."""
    found = {}
    for product in [pick.listing.product for pick in picks] + list(others):
        found.setdefault(product.pk, product)
    return list(found.values())
