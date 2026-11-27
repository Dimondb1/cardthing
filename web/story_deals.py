"""
Story deals: the owner's page of great deals ready to post as Instagram stories, one after another. The owner, 22 Nov
2026: "I need a list of ebay products to add to my insta stories easily. Doesn't have to be on the public facing area,
just need a really quick way to advertise the products, a one tap carousel of great deals where I can customise the
advert slightly if I need to".

What it lists: products in stock whose cheapest delivered price now is an eBay seller's (or any seller's, with
?from=all) and whose Price check says Good price or Lowest yet (Product.price_band, kept each hour), the lowest yet
first, then the dearer products. A doubtful price is never offered: it may not be this product. eBay sellers are
already held to the owner's rating rules before their prices count at all. An eBay price is offered only when it was
checked in the last FRESH hours (the owner, 24 Nov 2026: "the products offered are. Sometimes really old"): an eBay
item sells in hours, and a story must not send people to one sold already. The hourly eBay search checks these deals
again first, every two hours (ebay.deal_ids), so the list stays full.

Each deal is a card in a row that swipes sideways: the story picture the product page's Share to story makes
(web/share.py), the price, the seller and when it was checked. One tap on Share to story hands the picture to the
phone's share sheet, where Instagram offers Stories, with the product's link copied for the story's link sticker
(story-deals.js); without script the button saves the picture and the link is there to copy. A web page cannot fill
in the link sticker itself. The advert can be changed a little: the words on the picture's badge, and a line of the
owner's own under the price. They are his words, drawn as typed; everything else on the picture is the site's data.

Staff only, never for editors, never cached or indexed. Three queries for the page, and the offers' and the week's
lows, however many deals.
"""
from urllib.parse import urlencode

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from datetime import timedelta

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalogue import fair_price, offers
from catalogue.models import Product, Retailer
from content import service as copy
from guides.access import not_for_editors

from . import share

SHOWN = 30
FRESH = timedelta(hours=3)   # an eBay price older than this is not offered for a story
EBAY, ALL = "ebay", "all"
SOURCES = ((EBAY, "eBay"), (ALL, "Every seller"))
PICTURE_SECONDS = 600     # a picture is kept by the phone this long: the share sheet gets the one the card shows
# The link's tags: a visit from a story counts as Instagram's on the dashboard, as the product page's Share to story.
LINK_TAGS = {"utm_source": "instagram_story", "utm_medium": "social", "utm_campaign": "story_deals"}


def stale_ebay(now=None):
    """The test for a product whose cheapest price is an eBay seller's checked longer ago than FRESH."""
    return Q(lead_source=Retailer.Source.EBAY, lead_checked__lt=(now or timezone.now()) - FRESH)


def candidates(source, now=None):
    """Products in stock with a Good price or Lowest yet, whose cheapest delivered price in stock is an eBay
    seller's (``source`` EBAY) or anyone's, lowest yet first, with their offers; never an eBay price checked longer ago
    than FRESH. Two queries."""
    products = offers.with_lead(Product.objects.for_lists().in_stock().filter(price_band__gte=fair_price.GOOD_PRICE))
    if source == EBAY:
        products = products.filter(lead_source=Retailer.Source.EBAY)
    else:
        products = products.filter(lead_source__isnull=False)
    products = products.exclude(stale_ebay(now))
    # Twice the number shown: a few are dropped below (a doubtful price, a pre-order leading).
    return list(products.order_by("-price_band", "-lowest_known", "name")
                .prefetch_related(offers.buyable_prefetch())[:SHOWN * 2])


def deals(source, store=None):
    """[{product, lead, check, saving}]: the deals to show, at most SHOWN. ``lead`` is the offer the story's picture
    leads with (share.post_lead), which must be the one that made it a deal."""
    products = candidates(source)
    lows = offers.week_low_map([product.pk for product in products])
    found = []
    for product in products:
        summary = offers.summarise(product, lows)
        lead = share.post_lead(product, summary)
        if lead is None or summary.suspect or lead is not summary.best or not lead.delivery_known:
            continue
        if source == EBAY and lead.retailer.source_type != Retailer.Source.EBAY:
            continue
        check = product.price_check
        if check is None:
            continue
        saving = share.story_saving(product, lead)
        found.append({
            "product": product, "lead": lead, "summary": summary,
            "check": copy.get(f"product.fair.verdict_{check.key}", store=store),
            "saving": copy.get("product.share.story_saving", store=store, saving=f"£{saving:,.2f}") if saving else "",
        })
        if len(found) == SHOWN:
            break
    return found


def waiting_count(now=None):
    """eBay deals left out for now because their price was checked longer ago than FRESH: they come back once the
    hourly search checks them again. One query."""
    deals = offers.with_lead(Product.objects.active().in_stock().filter(price_band__gte=fair_price.GOOD_PRICE))
    return deals.filter(stale_ebay(now)).count()


def picture_url(slug, badge="", line="", theme=""):
    """Where a deal's story picture is, with the owner's own words when he gave any and the look he chose."""
    words = {name: value for name, value in (("badge", badge), ("line", line), ("theme", theme)) if value}
    return reverse("story_deal_image", args=[slug]) + (f"?{urlencode(words)}" if words else "")


def looks(chosen, source):
    """The looks a picture can wear, for the row of links over the deals: today's own first (what the product page's
    Share to story gives a visitor), then plain, then each season, so the owner can look ahead or leave one off.
    [(link, name, chosen)]."""
    today = share.story_theme()
    own = f"Today's ({today.name})" if today is not None else "Today's (plain)"
    found = [("", own)] + [(share.PLAIN, "Plain")] + [(theme.key, theme.name) for theme in share.STORY_THEMES.values()]
    return [("?" + urlencode({name: value for name, value in (("from", source), ("theme", key)) if value}), name,
             key == chosen) for key, name in found]


@staff_member_required
@not_for_editors
@require_GET
def story_deals_page(request):
    source = request.GET.get("from", EBAY)
    source = source if source in dict(SOURCES) else EBAY
    store = copy.for_request(request)
    # The owner's own words, for the one card they were typed on (without script the form reloads the page).
    chosen = request.GET.get("p", "")
    badge = share.story_words(request.GET.get("badge", ""), share.STORY_BADGE_MOST)
    line = share.story_words(request.GET.get("line", ""), share.STORY_LINE_MOST)
    # The look the pictures wear: today's own unless he chose one (or plain).
    theme = request.GET.get("theme", "")
    theme = theme if theme in (*share.STORY_THEMES, share.PLAIN) else ""
    cards = deals(source, store)
    for card in cards:
        product = card["product"]
        own = product.slug == chosen
        card["badge"], card["line"] = (badge, line) if own else ("", "")
        card["picture"] = picture_url(product.slug)
        card["shown"] = picture_url(product.slug, card["badge"], card["line"], theme)
        card["file"] = f"{product.slug}-story.png"
        card["link"] = f"{request.build_absolute_uri(product.get_absolute_url())}?{urlencode(LINK_TAGS)}"
    response = render(request, "admin/story_deals.html", {
        **admin.site.each_context(request),
        "title": "Story deals",
        "cards": cards, "source": source, "sources": SOURCES, "waiting": waiting_count(),
        "fresh_hours": round(FRESH.total_seconds() / 3600),
        "badge_most": share.STORY_BADGE_MOST, "line_most": share.STORY_LINE_MOST,
        "theme": theme, "looks": looks(theme, source if source != EBAY else ""),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@staff_member_required
@not_for_editors
@require_GET
def story_deal_image(request, slug):
    """A deal's story picture, PNG, with the owner's own badge words and line when given (?badge=, ?line=), in the
    season that is on or the look he chose (?theme=, share.story_theme_for)."""
    product = get_object_or_404(
        Product.objects.active().for_lists().prefetch_related(offers.buyable_prefetch()), slug=slug)
    summary = offers.summarise(product, offers.week_low_map([product.pk]))
    png = share.story_image(product, summary, copy.for_request(request),
                            badge=request.GET.get("badge", ""), line=request.GET.get("line", ""),
                            theme=share.story_theme_for(request))
    response = HttpResponse(png, content_type="image/png")
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = f"private, max-age={PICTURE_SECONDS}"
    return response
