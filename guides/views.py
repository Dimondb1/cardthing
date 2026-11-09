"""
The Guides pages: the Guides page (/guides/), each guide (/guides/<address>/), its printable checklist and the
Fair Price Checker (/guides/fair-price/). All of them work with JavaScript off.

Nothing here is public until a guide is published in Admin: before that every page is a 404 for visitors,
and staff see each page as it will look, with a note saying what still stops a draft being published.
"""

import hashlib
from collections import defaultdict

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from catalogue import insights
from catalogue.models import Listing, Product, ProductAlias
from catalogue.offers import buyable_prefetch
from catalogue.pricing import MARKETPLACES
from catalogue.search import apply_search
from catalogue.types import type_label
from content import service as copy
from inbox import service as inbox

from . import checker, state, text
from . import compare as compare_data
from .models import Guide, Section, Vote

CHOICES = 8          # products offered to choose from on the checker
VOTE_HOURS = 24      # one Yes or No per connection per guide in this time


def words(request, key, **values):
    return copy.get(key, store=copy.for_request(request), **values)


def is_staff(request):
    return getattr(request, "user", None) is not None and request.user.is_staff


def open_or_404(request):
    """The Guides pages are open once a guide is published, and to staff before that. One query, which also
    keeps the cached nav link right (guides.state)."""
    if not state.remember()["live"] and not is_staff(request):
        raise Http404("No guides yet.")


def guide_or_404(request, slug):
    guide = Guide.objects.filter(slug=slug).first()
    if guide is None or (not guide.is_published and not is_staff(request)):
        raise Http404("No guide at that address.")
    return guide


def price_cards(product_ids):
    """{product id: product} with what a guide's price card shows: the cheapest delivered price now at a UK shop
    we track (guide_cheapest, in stock first), how many have it in stock (guide_in_stock), when the prices were
    checked (guide_checked) and the stored Price check (guide_check). Two queries, however many cards."""
    if not product_ids:
        return {}
    found = {}
    for product in (Product.objects.for_lists().filter(pk__in=list(product_ids))
                    .prefetch_related(buyable_prefetch())):
        uk = [listing for listing in product.offers if listing.retailer.source_type not in MARKETPLACES
              and listing.delivery_known and listing.sanity != Listing.Sanity.DOUBTFUL]
        in_stock = [listing for listing in uk if listing.availability == Listing.Availability.IN_STOCK]
        cheapest = min(in_stock or uk, key=lambda listing: listing.delivered_price, default=None)
        product.guide_cheapest = cheapest.delivered_price if cheapest else None
        product.guide_preorder = bool(cheapest) and not in_stock
        product.guide_in_stock = len({listing.retailer_id for listing in in_stock})
        product.guide_checked = max((listing.last_checked for listing in uk), default=None)
        check = product.price_check if cheapest else None
        # As everywhere: never "High price" beside a shop that pays a commission.
        if check is not None and check.key == "high" and cheapest.retailer.pays_commission:
            check = None
        product.guide_check = check
        found[product.pk] = product
    return found


def box_card(request, section):
    """(title, [sentences]) for a section's "What is in the box" card, from the product type's own explainer
    (the one product pages show), or None."""
    from content.registry import REGISTRY
    from web.templatetags.ripraptor import info_keys

    if not section.box_type:
        return None
    game = section.box_game.slug if section.box_game_id else ""
    store = copy.for_request(request)
    lines = [copy.get(key, store=store) for key in info_keys("type", section.box_type, game) if key in REGISTRY]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return type_label(game or "pokemon", section.box_type), lines


def shown_sections(request, guide):
    """The guide's sections as the page shows them, with their price cards. Four queries."""
    sections = list(guide.sections.select_related("box_game"))
    through = Section.products.through.objects.filter(section__guide=guide).values_list("section_id", "product_id")
    wanted = defaultdict(list)
    for section_id, product_id in through:
        wanted[section_id].append(product_id)
    cards = price_cards({product_id for ids in wanted.values() for product_id in ids})
    for section, anchor in zip(sections, text.anchors([section.question for section in sections])):
        section.anchor = anchor
        section.body_html = text.body_html(section.body)
        section.short_html = text.body_html(section.short_answer)
        section.green = text.lines(section.green_flags)
        section.red = text.lines(section.red_flags)
        section.cards = [cards[pk] for pk in wanted.get(section.pk, []) if pk in cards]
        section.cards.sort(key=lambda product: product.name)
        section.box = box_card(request, section)
        section.source_list, _ = text.sources_in(section.sources)
    return sections


def guide_json(request, guide, page_url):
    from web.views import breadcrumbs_json

    image = (request.build_absolute_uri(guide.share_image.url) if guide.share_image
             else request.build_absolute_uri(static("img/share.png")))
    article = {
        "@context": "https://schema.org", "@type": "Article", "@id": page_url + "#article",
        "headline": guide.title[:110], "description": guide.one_line_answer, "url": page_url,
        "mainEntityOfPage": page_url, "inLanguage": "en-GB", "image": image,
        "author": organisation(request), "publisher": organisation(request),
    }
    if guide.published_at:
        article["datePublished"] = timezone.localtime(guide.published_at).date().isoformat()
    if guide.last_reviewed:
        article["dateModified"] = guide.last_reviewed.isoformat()
    crumbs = breadcrumbs_json(request, [(words(request, "guides.title"), reverse("web:guides")),
                                        (guide.title, guide.get_absolute_url())])
    return [article, crumbs]


def staff_notes(request, guide, sections):
    """What staff are told on a guide's page: a draft and what still stops it, or a review that is due."""
    if not is_staff(request):
        return []
    from .admin import shop_hosts

    today = timezone.localdate()
    notes = []
    if not guide.is_published:
        found = text.problems(guide, sections, shop_hosts(), today)
        notes.append(words(request, "guides.staff.draft"))
        notes += found["style"] + found["publish"]
    if guide.next_review and guide.next_review < today:
        notes.append(words(request, "guides.staff.review_due", day=f"{guide.next_review:%-d %B %Y}"))
    return notes


def organisation(request):
    return {"@type": "Organization", "name": settings.RIPRAPTOR_SITE_NAME, "url": request.build_absolute_uri("/"),
            "logo": {"@type": "ImageObject", "url": request.build_absolute_uri(static("img/logo.png"))}}


def linked(sentence, marker, product):
    """``sentence`` as safe HTML with ``marker`` (standing in for the product's name) made a link to its page."""
    from django.utils.html import escape, format_html
    from django.utils.safestring import mark_safe

    link = format_html('<a href="{}" data-compare="guide_example">{}</a>', product.get_absolute_url(), product.name)
    return mark_safe(escape(sentence).replace(escape(marker), link))


def compare_sections(request, found):
    """The comparing guide's sections as the page shows them: each question with its short answer and detail from
    Site wording, then what today's prices say (``numbers``, plain text) and one real example (``live``, safe HTML
    with the product linked), each only when the catalogue has it (guides/compare.py)."""
    from web.answers import stamp
    from web.hubs import tidy_stops
    from web.templatetags.ripraptor import gbp

    marker = "\x00product\x00"

    def say(key, **values):
        return tidy_stops(words(request, f"guides.compare.{key}", **values))

    def delivery_words(cost):
        return say("delivery.charge", charge=gbp(cost)) if cost else say("delivery.free")

    numbers, live = {}, {}
    delivery = found["delivery"]
    charges = []
    if delivery["free"]:
        charges.append(say("delivered.free", free=delivery["free"], shops=delivery["shops"]))
    if delivery["low"] is not None:
        charges.append(say("delivered.one" if delivery["low"] == delivery["high"] else "delivered.range",
                           low=gbp(delivery["low"]), high=gbp(delivery["high"])))
    if charges:
        numbers["delivered"] = " ".join(charges)
    if found["delivered"]:
        example = found["delivered"]
        low, best = example["low"], example["best"]
        live["delivered"] = linked(say(
            "delivered.live", product=marker, low_shop=low["shop"], low_item=gbp(low["price"]),
            low_delivery=delivery_words(low["delivery"]), low_delivered=gbp(low["delivered"]), best_shop=best["shop"],
            best_item=gbp(best["price"]), best_delivery=delivery_words(best["delivery"]),
            best_delivered=gbp(best["delivered"]), saving=gbp(example["saving"])), marker, example["product"])
    elif found["plain"]:
        best = found["plain"]["best"]
        live["delivered"] = linked(say(
            "delivered.plain", product=marker, shop=best["shop"], item=gbp(best["price"]),
            delivery=delivery_words(best["delivery"]), delivered=gbp(best["delivered"])), marker, found["plain"]["product"])
    if delivery["lines"] and delivery["common"] is not None:
        numbers["threshold"] = say("threshold.numbers", lines=delivery["lines"], shops=delivery["shops"],
                                   common=gbp(delivery["common"]))
    if found["threshold"]:
        offer = found["threshold"]["offer"]
        live["threshold"] = linked(say(
            "threshold.live", product=marker, shop=offer["shop"], item=gbp(offer["price"]),
            gap=gbp(found["threshold"]["gap"]), line=gbp(offer["line"]), delivery=delivery_words(offer["delivery"]),
            delivered=gbp(offer["delivered"])), marker, found["threshold"]["product"])
    stock = found["stock"]
    if stock["listed"]:
        lines = [say("stock.numbers", in_stock=f"{stock['in_stock']:,}", listed=f"{stock['listed']:,}",
                     preorder=f"{stock['preorder']:,}")]
        if stock["lottery"]:
            lines.append(say(f"stock.lottery.{'one' if stock['lottery'] == 1 else 'other'}", count=f"{stock['lottery']:,}"))
        numbers["stock"] = " ".join(lines)
    if found["preorder"]:
        offer = found["preorder"]
        live["stock"] = linked(say(
            "stock.live", product=marker, date=f"{offer.product.product_set.release_date:%-d %B %Y}",
            price=gbp(offer.delivered_price), shop=offer.retailer.name), marker, offer.product)
    if found["packs"]:
        packs = found["packs"]
        box, pack = packs["box"].listing, packs["pack"]
        live["packs"] = linked(say(
            "packs.live", product=marker, packs=packs["packs"], price=gbp(box.delivered_price),
            shop=box.retailer.name, each=gbp(packs["each"]), pack=pack.product.name, single=gbp(packs["single"]),
            pack_shop=pack.retailer.name), marker, box.product)
    if found["rrp"]:
        example = found["rrp"]
        best, rrp = example["best"], example["rrp"]
        percent = round(abs(best["delivered"] - rrp) / rrp * 100)
        difference = (say("rrp.same") if not percent
                      else say("rrp.under" if best["delivered"] < rrp else "rrp.over", percent=percent))
        sentence = say("rrp.live", product=marker, rrp=gbp(rrp), price=gbp(best["delivered"]), shop=best["shop"],
                       difference=difference)
        if example["low"] is not None and example["high"] is not None:
            sentence += " " + say("rrp.history", days=compare_data.HISTORY_DAYS, low=gbp(example["low"]),
                                  high=gbp(example["high"]))
        live["rrp"] = linked(sentence, marker, example["product"])
    if found["language"]:
        example = found["language"]
        english, japanese = example["english"], example["japanese"]
        name = example["game"].display_short
        numbers["language"] = say("language.numbers", count=f"{example['count']:,}", listed=f"{example['listed']:,}",
                                  game=name)
        live["language"] = linked(say(
            "language.live", game=name, product=marker, price=gbp(english.delivered_price),
            shop=english.retailer.name, japanese=japanese.product.name, japanese_price=gbp(japanese.delivered_price),
            japanese_shop=japanese.retailer.name), marker, english.product)
    markets = found["marketplaces"]
    numbers["marketplaces"] = say("marketplaces.numbers", feedback=markets["feedback"], ratings=markets["ratings"])
    if markets["ebay"] or markets["amazon"]:
        numbers["marketplaces"] += " " + say("marketplaces.now", ebay=f"{markets['ebay']:,}",
                                             amazon=f"{markets['amazon']:,}")
    return {
        "sections": [{"anchor": name, "question": say(f"{name}.q"), "short": say(f"{name}.short"),
                      "body": say(f"{name}.body"), "numbers": numbers.get(name, ""), "live": live.get(name, "")}
                     for name in compare_data.SECTIONS],
        "checked": stamp(found["checked"]) if found["checked"] else "",
    }


@require_GET
def compare_guide(request):
    """How to compare sealed TCG prices in the UK (guides/compare.py): open while the Guides pages are."""
    from web import feed
    from web.views import breadcrumbs_json, ld_json

    open_or_404(request)
    found = compare_data.examples()
    shown = compare_sections(request, found)
    title, answer = words(request, "guides.compare.title"), words(request, "guides.compare.answer")
    page_url = request.build_absolute_uri(reverse("web:guide_compare"))
    article = {
        "@context": "https://schema.org", "@type": "Article", "@id": page_url + "#article",
        "headline": title[:110], "description": answer, "url": page_url, "mainEntityOfPage": page_url,
        "inLanguage": "en-GB", "image": request.build_absolute_uri(static("img/share.png")),
        "author": organisation(request), "publisher": organisation(request),
        "datePublished": compare_data.PUBLISHED.isoformat(),
        "dateModified": compare_data.modified(found).isoformat(),
    }
    crumbs = breadcrumbs_json(request, [(words(request, "guides.title"), reverse("web:guides")),
                                        (title, reverse("web:guide_compare"))])
    return render(request, "guides/compare.html", {
        **shown,
        "title": title, "answer": answer,
        "games": feed.games_bar(feed.games_with_products()),
        "meta_title": title, "meta_description": answer, "canonical_url": page_url,
        "structured_json": ld_json([article, crumbs]),
        "share_url": page_url,
    })


@require_GET
def hub(request):
    from web.views import breadcrumbs_json, ld_json

    open_or_404(request)
    shown = Guide.objects.all() if is_staff(request) else Guide.objects.filter(is_published=True)
    guides = list(shown.prefetch_related("sections"))
    for item in guides:
        item.minutes = text.reading_minutes(item, list(item.sections.all()))
    return render(request, "guides/hub.html", {
        "guides": guides,
        "meta_title": words(request, "guides.title"),
        "meta_description": words(request, "guides.intro"),
        "canonical_url": request.build_absolute_uri(reverse("web:guides")),
        "structured_json": ld_json(breadcrumbs_json(request, [(words(request, "guides.title"), reverse("web:guides"))])),
    })


@require_GET
def guide(request, slug):
    from web.views import ld_json

    found = guide_or_404(request, slug)
    sections = shown_sections(request, found)
    page_url = request.build_absolute_uri(found.get_absolute_url())
    response = render(request, "guides/guide.html", {
        "guide": found,
        "sections": sections,
        "sources": list(dict.fromkeys(source for section in sections for source in section.source_list)),
        "minutes": text.reading_minutes(found, sections),
        "staff_notes": staff_notes(request, found, sections),
        "feedback": request.GET.get("feedback", "") if request.GET.get("feedback") in ("thanks", "busy") else "",
        "meta_title": found.search_title or found.title,
        "meta_description": found.one_line_answer,
        "meta_image": request.build_absolute_uri(found.share_image.url) if found.share_image else "",
        "canonical_url": page_url,
        "structured_json": ld_json(guide_json(request, found, page_url)),
        "share_url": page_url,
    })
    if not found.is_published:
        response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_GET
def checklist(request, slug):
    found = guide_or_404(request, slug)
    if not found.checklist_items:
        raise Http404("This guide has no checklist.")
    return render(request, "guides/checklist.html", {
        "guide": found,
        "items": found.checklist_items,
        "meta_title": found.checklist_title or found.title,
        "meta_description": found.one_line_answer,
        "canonical_url": request.build_absolute_uri(reverse("web:guide_checklist", args=[found.slug])),
    })


def product_for(slug):
    if not slug:
        return None
    product = Product.objects.active().select_related("game", "product_set").filter(slug=slug).first()
    if product is None:
        alias = ProductAlias.objects.filter(slug=slug, product__is_active=True).select_related(
            "product__game", "product__product_set").first()
        product = alias.product if alias else None
    return product


@require_GET
def fair_price(request):
    """The Fair Price Checker. With a product and a price it gives the verdict; with words it offers products
    to choose from; with neither, the form."""
    open_or_404(request)
    query = request.GET.get("q", "").strip()[:100]
    price_text = request.GET.get("price", "").strip()[:20]
    product = product_for(request.GET.get("product", "").strip()[:200])
    offered = checker.read_price(price_text) if price_text else None
    choices = []
    if product is None and query:
        found, terms = apply_search(Product.objects.for_lists(), query)
        if terms:
            from django.db.models import F

            found = found.order_by("name_rank", F("lowest_price").asc(nulls_last=True), "name")
        choices = list(found[:CHOICES])
    result = None
    if product is not None and offered is not None:
        result = checker.check(product, offered)
        checker.count(request, result)
    response = render(request, "guides/fair_price.html", {
        "query": query, "price_text": price_text, "product": product, "offered": offered,
        "price_wrong": bool(price_text) and offered is None, "choices": choices, "searched": bool(query),
        "result": result, "scams_door": state.door("sold_out"),
        "meta_title": words(request, "guides.checker.title"),
        "meta_description": words(request, "guides.checker.intro"),
        "canonical_url": request.build_absolute_uri(reverse("web:fair_price")),
    })
    if request.GET:
        response["X-Robots-Tag"] = "noindex, follow"
    return response


def voted_before(request, guide):
    """Whether this connection already said Yes or No to this guide in the last VOTE_HOURS. Only a one-way
    hash of the address is held, in the cache, for that long."""
    secret = f"{settings.SECRET_KEY}:guide-vote:{guide.pk}"
    key = "guide-vote:" + hashlib.sha256(f"{secret}|{insights.client_ip(request)}".encode()).hexdigest()[:24]
    return not cache.add(key, 1, VOTE_HOURS * 3600)


@require_POST
def feedback(request, slug):
    """Was this useful? Yes or No, counted by day (Vote), and what the reader wrote, filed as a message in
    Admin (inbox.service.guide_note). No name or email is asked for; the hidden field keeps robots out."""
    found = Guide.objects.filter(slug=slug, is_published=True).first()
    if found is None:
        raise Http404("No guide at that address.")
    back = found.get_absolute_url()
    useful = request.POST.get("useful")
    if request.POST.get("website") or useful not in ("yes", "no") or insights.is_bot(request):
        return HttpResponseRedirect(back + "#feedback")
    useful = useful == "yes"
    state_word = "thanks"
    if not voted_before(request, found):
        insights.bump(Vote, guide=found, useful=useful)
    note = request.POST.get("note", "").strip()
    if note:
        try:
            inbox.guide_note(found, useful, note)
        except inbox.Refused:
            state_word = "busy"
    return HttpResponseRedirect(f"{back}?feedback={state_word}#feedback")
