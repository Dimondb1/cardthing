"""
RSS feeds of restocks and price drops: one for the whole site, one per game.

    /feeds/deals.xml
    /feeds/pokemon.xml

A feed is the one way to push news to people that stores nothing about
anyone. Restocks come from the Restock rows (so marketplaces are left out),
with the shop and the delivered price. Price drops come from the daily
lowest prices, which hold a date and no time, so a drop is dated to its
day and each product appears once. Entries link to the product page.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.syndication.views import Feed
from django.core.cache import cache
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from catalogue import pricing
from catalogue.models import DailyLowestPrice, Game, Listing, Restock
from content import service as copy

FEED_DAYS = 7
FEED_LIMIT = 50
DROP_CANDIDATES = 40
CACHE_PREFIX = "web:feed:v1:"


@dataclass
class Entry:
    key: str
    title: str
    body: str
    url: str
    at: datetime


@dataclass
class FeedPage:
    slug: str
    title: str
    description: str
    url: str
    entries: list = field(default_factory=list)


def feed_cache_key(slug):
    return CACHE_PREFIX + slug


def restock_entries(game, store, now):
    rows = Restock.objects.filter(
        at__gte=now - timedelta(days=FEED_DAYS), product__is_active=True, retailer__is_active=True
    ).exclude(listing__sanity=Listing.Sanity.EXCLUDED)  # a price since kept out of the comparison is not news
    if game is not None:
        rows = rows.filter(product__game=game)
    entries = []
    for restock in rows.select_related("product", "retailer").order_by("-at")[:FEED_LIMIT]:
        when = timezone.localtime(restock.at)
        entries.append(Entry(
            key=f"restock-{restock.pk}",
            title=copy.get("feeds.restock.title", store=store, product=restock.product.name,
                           price=f"£{restock.price:.2f}", retailer=restock.retailer.name),
            body=copy.get("feeds.restock.body" if restock.delivery_known else "feeds.restock.body_unknown", store=store,
                          retailer=restock.retailer.name,
                          date=when.strftime("%-d %b"), time=when.strftime("%H:%M"), price=f"£{restock.price:.2f}"),
            url=restock.product.get_absolute_url(),
            at=restock.at,
        ))
    return entries


def drop_entries(game, store, now):
    """One entry per product whose daily lowest price fell within the window, dated to the day it fell."""
    today = timezone.localdate(now)
    products = list(pricing.price_drops(limit=DROP_CANDIDATES, days=FEED_DAYS, today=today, game=game))
    history = {}
    rows = DailyLowestPrice.objects.filter(
        product__in=products, date__gte=today - timedelta(days=FEED_DAYS + 1)
    ).order_by("product_id", "date").values_list("product_id", "date", "price")
    for pk, date, price in rows:
        history.setdefault(pk, []).append((date, price))
    entries = []
    for product in products:
        fell_on = None
        for (_, before), (date, price) in zip(history.get(product.pk, []), history.get(product.pk, [])[1:]):
            if price < before and date >= today - timedelta(days=FEED_DAYS):
                fell_on = (date, before, price)
        if fell_on is None:
            continue
        date, before, price = fell_on
        entries.append(Entry(
            key=f"drop-{product.pk}-{date.isoformat()}",
            title=copy.get("feeds.drop.title", store=store, product=product.name, price=f"£{price:.2f}", was=f"£{before:.2f}"),
            body=copy.get("feeds.drop.body", store=store, price=f"£{price:.2f}", was=f"£{before:.2f}",
                          date=date.strftime("%-d %b")),
            url=product.get_absolute_url(),
            at=timezone.make_aware(datetime.combine(date, time.min)),
        ))
    return entries


def build_feed(slug, store, now=None):
    """The FeedPage for ``deals`` or a game slug, or None for an unknown game."""
    now = now or timezone.now()
    game = None
    if slug != "deals":
        game = Game.objects.filter(slug=slug, is_active=True).first()
        if game is None:
            return None
    site = settings.RIPRAPTOR_SITE_NAME
    if game is None:
        title = copy.get("feeds.deals.title", store=store, site_name=site)
        description = copy.get("feeds.deals.description", store=store)
        url = reverse("web:deals")
    else:
        title = copy.get("feeds.game.title", store=store, site_name=site, game=game.name)
        description = copy.get("feeds.game.description", store=store, game=game.name)
        url = game.get_absolute_url()
    entries = restock_entries(game, store, now) + drop_entries(game, store, now)
    entries.sort(key=lambda entry: entry.at, reverse=True)
    return FeedPage(slug=slug, title=title, description=description, url=url, entries=entries[:FEED_LIMIT])


def feed_cache_keys():
    """The cache key of the site-wide feed and of each active game's feed."""
    slugs = ["deals", *Game.objects.filter(is_active=True).values_list("slug", flat=True)]
    return [feed_cache_key(slug) for slug in slugs]


class DealsFeed(Feed):
    """Restocks and price drops, site-wide or for one game. Built once every few minutes, like the home lists."""

    def get_object(self, request, game_slug="deals"):
        key = feed_cache_key(game_slug)
        page = cache.get(key)
        if page is None:
            page = build_feed(game_slug, copy.for_request(request))
            if page is None:
                raise Http404("No feed for that game.")
            cache.set(key, page, settings.RIPRAPTOR_HOME_CACHE_SECONDS)
        return page

    def title(self, page):
        return page.title

    def link(self, page):
        return page.url

    def description(self, page):
        return page.description

    def items(self, page):
        return page.entries

    def item_title(self, entry):
        return entry.title

    def item_description(self, entry):
        return entry.body

    def item_link(self, entry):
        return entry.url

    item_guid_is_permalink = False

    def item_guid(self, entry):
        return entry.key

    def item_pubdate(self, entry):
        return entry.at
