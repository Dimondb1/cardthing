"""
Posts to the owner's Discord server: products back in stock, pre-orders opening, price drops and sets out today.

    python manage.py post_discord           # what cron runs every ten minutes
    python manage.py post_discord --test    # one hello to each channel set up

Each kind goes to a channel's webhook: settings.RIPRAPTOR_DISCORD_WEBHOOK_RESTOCKS (restocks and pre-orders
opening), _PRICE_DROPS and _DROPS (sets out today), each falling back to RIPRAPTOR_DISCORD_WEBHOOK; a kind with
no address posts nothing. A post names the product, its delivered price and when it was checked, and links to
RipRaptor's page for it (utm_source=discord), never to the shop: as with the search alert emails, people compare
every shop on the page. Marketplaces never count, nor a price kept out of the comparison.

The owner chooses in admin (DiscordSettings) which kinds post, how often (each kind at most once in that
time, with what happened since in one post), how many products a post holds, the smallest price drop worth a
post, which games, and the name and picture shown. Each piece of news is posted once (DiscordPost keeps its
key), a product at most once a day for each kind. The first run for a kind posts nothing and only notes what
is there already, so turning a channel on never floods it with the week before. Product names never ping
anyone: every post forbids mentions. A webhook's address is never printed or logged.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Max
from django.templatetags.static import static
from django.utils import timezone

from content import service as copy

from . import drops, pricing
from .alerts import MARKETPLACES
from .images import sized_image_url
from .models import DiscordPost, DiscordSettings, Listing, PreorderOpen, ProductSet, Restock

logger = logging.getLogger(__name__)

RESTOCK, PREORDER, PRICE_DROP, DROP_DAY = "restock", "preorder", "price_drop", "drop_day"
KINDS = (RESTOCK, PREORDER, PRICE_DROP, DROP_DAY)
WEBHOOK_SETTING = {
    RESTOCK: "RIPRAPTOR_DISCORD_WEBHOOK_RESTOCKS",
    PREORDER: "RIPRAPTOR_DISCORD_WEBHOOK_RESTOCKS",
    PRICE_DROP: "RIPRAPTOR_DISCORD_WEBHOOK_PRICE_DROPS",
    DROP_DAY: "RIPRAPTOR_DISCORD_WEBHOOK_DROPS",
}
LOOK_BACK = timedelta(hours=6)     # a restock or opening older than this (or than the gap between posts) is not news
SLACK = timedelta(minutes=2)       # cron's runs drift a little; a post due in under this is due now
DROP_CANDIDATES = 40
SWITCHES = {RESTOCK: "restocks", PREORDER: "preorders", PRICE_DROP: "price_drops", DROP_DAY: "drop_days"}
DROP_DAY_FROM_HOUR = 8             # sets out today are posted from 8am UK time, like the drop-day emails
KEEP_DAYS = 30
COLOUR = 0xE5470E                  # the buy button's orange (--color-brand-action)
IMAGE_WIDTH = 200
TIMEOUT = 10
SEEDED = "seeded-"
NOTED = "noted"                    # a DiscordPost's kind for news a first run noted without posting


class DiscordError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass
class Post:
    key: str
    title: str
    body: str
    path: str                      # RipRaptor's page for it, before the tracking is added
    image: str = ""
    at: datetime | None = None


def webhook(kind):
    return getattr(settings, WEBHOOK_SETTING[kind], "") or settings.RIPRAPTOR_DISCORD_WEBHOOK


def webhooks():
    """Each address set up, once, for --test."""
    return list(dict.fromkeys(url for url in (webhook(kind) for kind in KINDS) if url))


def tracked(path):
    """The page's full address, marked as visited from Discord so analytics can count it."""
    query = urlencode({"utm_source": "discord", "utm_medium": "social", "utm_campaign": "alerts"})
    return f"{settings.RIPRAPTOR_SITE_URL}{path}?{query}"


def price_words(price, delivery_known, checked, store):
    return copy.get("discord.price" if delivery_known else "discord.price_plus", store=store, price=f"£{price:.2f}",
                    time=timezone.localtime(checked).strftime("%H:%M"))


def look_back(controls):
    return max(LOOK_BACK, timedelta(minutes=controls.every_minutes) + timedelta(hours=1))


def shop_news(model, kind, title_key, availability, now, store, controls, games):
    """Restocks or pre-order openings since look_back(), newest first, one a day per product, while the shop
    still has it that way."""
    rows = (
        model.objects.filter(at__gte=now - look_back(controls), product__is_active=True, retailer__is_active=True,
                             listing__is_active=True, listing__availability=availability)
        .exclude(retailer__source_type__in=MARKETPLACES).exclude(listing__sanity=Listing.Sanity.EXCLUDED)
        .select_related("product").order_by("-at")
    )
    if games:
        rows = rows.filter(product__game_id__in=games)
    seen = set()
    for row in rows:
        key = f"{kind}-{row.product_id}-{timezone.localdate(row.at).isoformat()}"
        if key in seen:
            continue
        seen.add(key)
        yield Post(key=key, title=copy.get(title_key, store=store, product=row.product.name),
                   body=price_words(row.price, row.delivery_known, row.at, store),
                   path=row.product.get_absolute_url(), image=row.product.image_src, at=row.at)


def price_drop_news(now, store, controls, games):
    """Products whose cheapest delivered price is at least the owner's smallest drop below yesterday's, once a
    day each."""
    today = timezone.localdate(now)
    for product in pricing.price_drops(limit=DROP_CANDIDATES, days=1, today=today):
        if product.drop_ratio < controls.min_drop_percent / 100 or (games and product.game_id not in games):
            continue
        checked = (Listing.objects.filter(product=product).buyable().filter(delivery_known=True)
                   .order_by("delivered_price").values_list("last_checked", flat=True).first()) or now
        yield Post(
            key=f"{PRICE_DROP}-{product.pk}-{today.isoformat()}",
            title=copy.get("discord.price_drop.title", store=store, product=product.name),
            body=copy.get("discord.price_drop.body", store=store, price=f"£{product.lowest_price:.2f}",
                          was=f"£{product.previous_price:.2f}", time=timezone.localtime(checked).strftime("%H:%M")),
            path=product.get_absolute_url(), image=product.image_src, at=checked,
        )


def drop_day_news(now, store, controls, games):
    """Sets out today, from DROP_DAY_FROM_HOUR, with the cheapest offer when a shop has one."""
    local = timezone.localtime(now)
    today = local.date()
    if local.hour < DROP_DAY_FROM_HOUR or not ProductSet.objects.filter(release_date=today).exists():
        return
    upcoming, _recent = drops.tracker(today)
    for drop in upcoming:
        if drop.day != today or (games and drop.product_set.game_id not in games):
            continue
        product_set = drop.product_set
        best = drop.best
        if best is not None:
            listing = best.listing
            body = copy.get("discord.drop_day.body", store=store, price=price_words(
                listing.delivered_price if listing.delivery_known else listing.price, listing.delivery_known,
                listing.last_checked, store))
        else:
            body = copy.get("discord.drop_day.none", store=store)
        yield Post(
            key=f"{DROP_DAY}-{product_set.pk}-{today.isoformat()}",
            title=copy.get("discord.drop_day.title", store=store, set=product_set.name, game=product_set.game.display_short),
            body=body, path=product_set.get_absolute_url(),
            image=best.product.image_src if best is not None else "", at=now,
        )


def news(kind, now, store, controls, games):
    if kind == RESTOCK:
        return list(shop_news(Restock, RESTOCK, "discord.restock.title", Listing.Availability.IN_STOCK, now, store,
                              controls, games))
    if kind == PREORDER:
        return list(shop_news(PreorderOpen, PREORDER, "discord.preorder.title", Listing.Availability.PREORDER, now,
                              store, controls, games))
    if kind == PRICE_DROP:
        return list(price_drop_news(now, store, controls, games))
    return list(drop_day_news(now, store, controls, games))


def absolute(url):
    return url if url.startswith(("http://", "https://")) else settings.RIPRAPTOR_SITE_URL + url


def embed(post, store):
    data = {
        "title": post.title[:256],
        "url": tracked(post.path),
        "description": post.body[:4000],
        "color": COLOUR,
        "footer": {"text": copy.get("discord.footer", store=store)[:2048]},
    }
    if post.image:
        data["thumbnail"] = {"url": absolute(sized_image_url(post.image, IMAGE_WIDTH))}
    if post.at:
        data["timestamp"] = post.at.isoformat()
    return data


def message(store, posts=(), content="", controls=None):
    """A webhook message that can never ping anyone, whatever a product's name says, under the owner's name and
    picture for it, or the webhook's own."""
    controls = controls or DiscordSettings.current()
    data = {"allowed_mentions": {"parse": []}}
    if controls.name:
        data["username"] = controls.name[:80]
    if controls.use_logo:
        data["avatar_url"] = absolute(static("img/apple-touch-icon.png"))
    if content:
        data["content"] = content
    if posts:
        data["embeds"] = [embed(post, store) for post in posts]
    return data


def send(url, data):
    """POST one message. Raises DiscordError, never naming the address."""
    request = urllib.request.Request(
        url + ("&" if "?" in url else "?") + "wait=true", data=json.dumps(data).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "RipRaptor (https://ripraptor.com, 1.0)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raise DiscordError(f"Discord answered {exc.code}", exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DiscordError(f"Discord could not be reached: {exc.__class__.__name__}") from exc


def due(kind, now, controls):
    """Whether the owner's gap since the kind's last post has passed."""
    last = DiscordPost.objects.filter(kind=kind).aggregate(last=Max("posted_at"))["last"]
    return last is None or now - last >= timedelta(minutes=controls.every_minutes) - SLACK


def run(now=None, post=None, stdout=None):
    """Post each kind's new news to its channel. Returns {kind: posts sent} for the kinds set up, switched on and
    due; a kind's first run counts 0 and only notes what is there. A 429 (too many posts) stops the run; the
    rest wait."""
    now = now or timezone.now()
    post = post or send
    store = copy.load_all()
    controls = DiscordSettings.current()
    # Only these games; none chosen (or the settings never saved) means every game.
    games = set() if controls._state.adding else set(controls.games.values_list("pk", flat=True))
    sent = {}
    for kind in KINDS:
        url = webhook(kind)
        if not url or not getattr(controls, SWITCHES[kind]):
            continue
        seeded = DiscordPost.objects.filter(key=SEEDED + kind).exists()
        if seeded and not due(kind, now, controls):
            continue
        posts = news(kind, now, store, controls, games)
        known = set(DiscordPost.objects.filter(key__in=[p.key for p in posts]).values_list("key", flat=True))
        fresh = [p for p in posts if p.key not in known]
        if not seeded:
            # Switched on just now: what is already there is old news.
            DiscordPost.objects.bulk_create([DiscordPost(key=p.key, kind=NOTED, posted_at=now) for p in fresh]
                                            + [DiscordPost(key=SEEDED + kind, kind=NOTED, posted_at=now)],
                                            ignore_conflicts=True)
            sent[kind] = 0
            continue
        batch = fresh[:controls.per_post]
        if not batch:
            sent[kind] = 0
            continue
        try:
            post(url, message(store, batch, controls=controls))
        except DiscordError as exc:
            logger.warning("Discord %s: %s", kind, exc)
            if stdout:
                stdout.write(f"{kind}: not posted, {exc}")
            if exc.status == 429:
                break
            continue
        DiscordPost.objects.bulk_create([DiscordPost(key=p.key, kind=kind, posted_at=now) for p in batch],
                                        ignore_conflicts=True)
        sent[kind] = len(batch)
    DiscordPost.objects.filter(posted_at__lt=now - timedelta(days=KEEP_DAYS)).exclude(key__startswith=SEEDED).delete()
    return sent


def test(post=None):
    """One hello to each channel set up. Returns how many were sent."""
    post = post or send
    store = copy.load_all()
    hello = copy.get("discord.test", store=store, site=settings.RIPRAPTOR_SITE_URL)
    for url in webhooks():
        post(url, message(store, content=hello))
    return len(webhooks())
