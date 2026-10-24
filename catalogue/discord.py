"""
Posts to the owner's Discord server: products back in stock, pre-orders opening, price drops and sets out today.

    python manage.py post_discord           # what cron runs every ten minutes
    python manage.py post_discord --test    # one hello to each channel set up

Each channel is set up in admin (DiscordChannel): its webhook address, which updates and games (or sets) it gets, the
smallest price drop worth a post, how often it posts (each update at most once in that time, with what
happened since in one post), how many products a post holds, and the name and picture shown. Until the first
channel is added there, a webhook address in .env (RIPRAPTOR_DISCORD_WEBHOOK) gets every update with the
defaults.

A post names the product, its delivered price and when it was checked, and links to RipRaptor's page for it
(utm_source=discord), never to the shop: as with the search alert emails, people compare every shop on the
page. Marketplaces never count, nor a price kept out of the comparison. Each piece of news is posted once to a
channel (DiscordPost keeps its key), a product at most once a day for each update. A channel's first run for an
update posts nothing and only notes what is there already, so setting one up never floods it with the week
before. Product names never ping anyone: every post forbids mentions. A webhook's address is never printed or
logged.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Max
from django.templatetags.static import static
from django.utils import timezone

from content import service as copy

from . import drops, fair_price, pricing
from .alerts import MARKETPLACES
from .images import sized_image_url
from .models import DiscordChannel, DiscordPost, Listing, PreorderOpen, ProductSet, Restock

logger = logging.getLogger(__name__)

RESTOCK, PREORDER, PRICE_DROP, DROP_DAY = "restock", "preorder", "price_drop", "drop_day"
KINDS = (RESTOCK, PREORDER, PRICE_DROP, DROP_DAY)
LOOK_BACK = timedelta(hours=6)     # a restock or opening older than this (or than the gap between posts) is not news
SLACK = timedelta(minutes=2)       # cron's runs drift a little; a post due in under this is due now
DROP_CANDIDATES = 40
SWITCHES = {RESTOCK: "restocks", PREORDER: "preorders", PRICE_DROP: "price_drops", DROP_DAY: "drop_days"}
DROP_DAY_FROM_HOUR = 8             # sets out today are posted from 8am UK time, like the drop-day emails
KEEP_DAYS = 30
RESTOCK_DAYS = 30                  # a restock post counts the restocks in this many days
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
    label: str = ""                # what happened ("Back in stock"), on the line above the title beside the logo
    fields: list = field(default_factory=list)   # (name, value) facts, side by side under the price


def channels():
    """The channels set up in admin and posting; with none there yet, the address in .env as one channel (not
    saved) that gets every update with the defaults."""
    rows = list(DiscordChannel.objects.filter(is_active=True).exclude(webhook_url="").prefetch_related("games"))
    if rows or DiscordChannel.objects.exists() or not settings.RIPRAPTOR_DISCORD_WEBHOOK:
        return rows
    return [DiscordChannel(label=".env", webhook_url=settings.RIPRAPTOR_DISCORD_WEBHOOK,
                           restocks=True, preorders=True, price_drops=True, drop_days=True)]


def prefix(channel):
    """What a channel's DiscordPost keys start with."""
    return f"{channel.pk or 'env'}:"


def tracked(path):
    """The page's full address, marked as visited from Discord so analytics can count it."""
    query = urlencode({"utm_source": "discord", "utm_medium": "social", "utm_campaign": "alerts"})
    return f"{settings.RIPRAPTOR_SITE_URL}{path}?{query}"


def when_words(at):
    """When, as the post says it, in UK time: "Sat 24 Oct, 14:05". The owner: "include the date, day of week and
    time in the post, so I can see when it was"."""
    local = timezone.localtime(at)
    return f"{local:%a} {local.day} {local:%b, %H:%M}"


def price_words(price, delivery_known, checked, store):
    return copy.get("discord.price" if delivery_known else "discord.price_plus", store=store, price=f"£{price:.2f}",
                    time=timezone.localtime(checked).strftime("%H:%M"), when=when_words(checked))


def span_words(delta):
    """How long, in the plainest unit: "45 minutes", "5 hours", "3 days"."""
    minutes = max(1, int(delta.total_seconds() // 60))
    if minutes < 60:
        number, unit = minutes, "minute"
    elif minutes < 48 * 60:
        number, unit = minutes // 60, "hour"
    else:
        number, unit = minutes // (24 * 60), "day"
    return f"{number} {unit}{'' if number == 1 else 's'}"


def price_check_field(product, price, delivery_known, store):
    """("Price check", the verdict in bold and what it was measured against), the caveat when only one UK shop
    lists the product, whose price then has nothing to be compared with, or None while there is no verdict yet
    (catalogue/fair_price.py). One query, two more with a verdict."""
    shops = (Listing.objects.filter(product=product, is_active=True, retailer__is_active=True)
             .exclude(sanity=Listing.Sanity.EXCLUDED).values("retailer_id").distinct().count())
    found = fair_price.verdict(product, price, shops=shops) if delivery_known else None
    name = copy.get("discord.field.price_check", store=store)
    if found is None:
        return (name, copy.get("discord.fair.one_shop", store=store)) if shops < fair_price.MIN_SHOPS else None
    lines = [f"**{copy.get(f'product.fair.verdict_{found.key}', store=store)}**"]
    if found.days:
        lines.append(copy.get("discord.fair.short_average", store=store, average=f"£{found.average:.2f}", days=found.days))
    if found.market:
        lines.append(copy.get("discord.fair.short_market", store=store, market=f"£{found.market.gbp:.2f}"))
    return name, "\n".join(lines)


def stock_fields(product, store, restock=None, now=None):
    """The stock facts under a restock or pre-order post, each only when known: how many of the UK shops that
    list it have it in stock now; for a restock, how long the shop had been sold out, how many restocks it has
    had in RESTOCK_DAYS and how long the shop's last stock lasted. Never a count of units: no shop we read
    says how many it has. Marketplaces never count. Two queries, two more for a restock."""
    now = now or timezone.now()
    listed = (Listing.objects.filter(product=product, is_active=True, retailer__is_active=True)
              .exclude(retailer__source_type__in=MARKETPLACES).exclude(sanity=Listing.Sanity.EXCLUDED))
    total = listed.values("retailer_id").distinct().count()
    stocked = (Listing.objects.filter(product=product).buyable().filter(availability=Listing.Availability.IN_STOCK)
               .exclude(retailer__source_type__in=MARKETPLACES).values("retailer_id").distinct().count())
    fields = []
    if total:
        fields.append((copy.get("discord.field.shops", store=store), f"{stocked} of {total}"))
    if restock is None:
        return fields
    if restock.out_since:
        fields.append((copy.get("discord.field.out_for", store=store), span_words(restock.at - restock.out_since)))
    recent = Restock.objects.filter(product=product, at__gte=now - timedelta(days=RESTOCK_DAYS), at__lte=restock.at).count()
    if recent >= 2:
        fields.append((copy.get("discord.field.restocks", store=store, days=RESTOCK_DAYS), str(recent)))
    if restock.out_since and restock.listing_id:
        before = (Restock.objects.filter(listing_id=restock.listing_id, at__lt=restock.at)
                  .order_by("-at").values_list("at", flat=True).first())
        if before is not None and restock.out_since > before:
            fields.append((copy.get("discord.field.lasted", store=store), span_words(restock.out_since - before)))
    return fields


def look_back(channel):
    return max(LOOK_BACK, timedelta(minutes=channel.every_minutes) + timedelta(hours=1))


def shop_news(model, kind, title_key, availability, now, store, channel, games, sets=frozenset()):
    """Restocks or pre-order openings since look_back(), newest first, one a day per product, while the shop
    still has it that way."""
    rows = (
        model.objects.filter(at__gte=now - look_back(channel), product__is_active=True, retailer__is_active=True,
                             listing__is_active=True, listing__availability=availability)
        .exclude(retailer__source_type__in=MARKETPLACES).exclude(listing__sanity=Listing.Sanity.EXCLUDED)
        .select_related("product__game").order_by("-at")
    )
    if sets:
        rows = rows.filter(product__product_set_id__in=sets)
    elif games:
        rows = rows.filter(product__game_id__in=games)
    seen = set()
    for row in rows:
        key = f"{kind}-{row.product_id}-{timezone.localdate(row.at).isoformat()}"
        if key in seen:
            continue
        seen.add(key)
        check = price_check_field(row.product, row.price, row.delivery_known, store)
        facts = stock_fields(row.product, store, restock=row if kind == RESTOCK else None, now=now)
        yield Post(key=key, title=row.product.name, label=copy.get(title_key, store=store, product=row.product.name),
                   body=price_words(row.price, row.delivery_known, row.at, store),
                   fields=([check] if check else []) + facts,
                   path=row.product.get_absolute_url(), image=row.product.image_src, at=row.at)


def price_drop_news(now, store, channel, games, sets=frozenset()):
    """Products whose cheapest delivered price is at least the channel's smallest drop below yesterday's, once
    a day each."""
    today = timezone.localdate(now)
    for product in pricing.price_drops(limit=DROP_CANDIDATES, days=1, today=today):
        if product.drop_ratio < channel.min_drop_percent / 100 or not in_scope(product.game_id, product.product_set_id,
                                                                                  games, sets):
            continue
        checked = (Listing.objects.filter(product=product).buyable().filter(delivery_known=True)
                   .order_by("delivered_price").values_list("last_checked", flat=True).first()) or now
        yield Post(
            key=f"{PRICE_DROP}-{product.pk}-{today.isoformat()}",
            title=product.name, label=copy.get("discord.price_drop.title", store=store, product=product.name),
            body=copy.get("discord.price_drop.body", store=store, price=f"£{product.lowest_price:.2f}",
                          was=f"£{product.previous_price:.2f}", time=timezone.localtime(checked).strftime("%H:%M"),
                          when=when_words(checked)),
            path=product.get_absolute_url(), image=product.image_src, at=checked,
        )


def drop_day_news(now, store, channel, games, sets=frozenset()):
    """Sets out today, from DROP_DAY_FROM_HOUR, with the cheapest offer when a shop has one."""
    local = timezone.localtime(now)
    today = local.date()
    if local.hour < DROP_DAY_FROM_HOUR or not ProductSet.objects.filter(release_date=today).exists():
        return
    upcoming, _recent = drops.tracker(today)
    for drop in upcoming:
        if drop.day != today or not in_scope(drop.product_set.game_id, drop.product_set.pk, games, sets):
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
            title=f"{product_set.name} ({product_set.game.display_short})",
            label=copy.get("discord.drop_day.title", store=store, set=product_set.name, game=product_set.game.display_short),
            body=body, path=product_set.get_absolute_url(),
            image=best.product.image_src if best is not None else "", at=now,
        )


def in_scope(game_id, set_id, games, sets):
    """Whether a product or set is one a channel posts: its sets when it has some (whatever its games), else its
    games, else everything."""
    if sets:
        return set_id in sets
    return not games or game_id in games


def news(kind, now, store, channel, games, sets=frozenset()):
    if kind == RESTOCK:
        return list(shop_news(Restock, RESTOCK, "discord.restock.title", Listing.Availability.IN_STOCK, now, store,
                              channel, games, sets))
    if kind == PREORDER:
        return list(shop_news(PreorderOpen, PREORDER, "discord.preorder.title", Listing.Availability.PREORDER, now,
                              store, channel, games, sets))
    if kind == PRICE_DROP:
        return list(price_drop_news(now, store, channel, games, sets))
    return list(drop_day_news(now, store, channel, games, sets))


def absolute(url):
    return url if url.startswith(("http://", "https://")) else settings.RIPRAPTOR_SITE_URL + url


def logo():
    return absolute(static("img/apple-touch-icon.png"))


def embed(post, store):
    """One post in RipRaptor's look: the brand orange down its side, the logo beside what happened ("Back in
    stock"), the product as the title linking to its RipRaptor page, the price in bold with a link to compare
    every shop, the facts side by side, the picture, and the commission note."""
    url = tracked(post.path)
    compare = copy.get("discord.compare", store=store)
    data = {
        "title": post.title[:256],
        "url": url,
        "description": (f"{post.body}\n[{compare} →]({url})" if compare else post.body)[:4000],
        "color": COLOUR,
        "footer": {"text": copy.get("discord.footer", store=store)[:2048], "icon_url": logo()},
    }
    if post.label:
        data["author"] = {"name": post.label[:256], "url": url, "icon_url": logo()}
    if post.fields:
        data["fields"] = [{"name": name[:256], "value": value[:1024], "inline": True} for name, value in post.fields[:6]]
    if post.image:
        data["thumbnail"] = {"url": absolute(sized_image_url(post.image, IMAGE_WIDTH))}
    if post.at:
        data["timestamp"] = post.at.isoformat()
    return data


def message(store, channel, posts=(), content=""):
    """A webhook message that can never ping anyone, whatever a product's name says, under the channel's name
    and picture, or the webhook's own."""
    data = {"allowed_mentions": {"parse": []}}
    if channel.username:
        data["username"] = channel.username[:80]
    if channel.use_logo:
        data["avatar_url"] = logo()
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


def due(channel, kind, now):
    """Whether the channel's gap since its last post of this update has passed."""
    last = (DiscordPost.objects.filter(key__startswith=prefix(channel), kind=kind)
            .aggregate(last=Max("posted_at"))["last"])
    return last is None or now - last >= timedelta(minutes=channel.every_minutes) - SLACK


def post_channel(channel, now, store, post, stdout=None):
    """Post one channel's due updates. Returns {kind: posts sent} for the updates it gets that were due; a first
    run counts 0 and only notes what is there. A 429 (too many posts) leaves the channel's other updates for the
    next run."""
    sent = {}
    head = prefix(channel)
    saved = channel if channel.pk else None
    # Only these sets, else these games; none chosen means everything.
    games = {game.pk for game in channel.games.all()} if channel.pk else set()
    sets = {product_set.pk for product_set in channel.sets.all()} if channel.pk else set()
    for kind in KINDS:
        if not getattr(channel, SWITCHES[kind]):
            continue
        seeded = DiscordPost.objects.filter(key=head + SEEDED + kind).exists()
        if seeded and not due(channel, kind, now):
            continue
        posts = news(kind, now, store, channel, games, sets)
        known = set(DiscordPost.objects.filter(key__in=[head + p.key for p in posts]).values_list("key", flat=True))
        fresh = [p for p in posts if head + p.key not in known]
        if not seeded:
            # Set up just now: what is already there is old news.
            DiscordPost.objects.bulk_create(
                [DiscordPost(key=head + p.key, kind=NOTED, channel=saved, posted_at=now) for p in fresh]
                + [DiscordPost(key=head + SEEDED + kind, kind=NOTED, channel=saved, posted_at=now)],
                ignore_conflicts=True,
            )
            sent[kind] = 0
            continue
        batch = fresh[:channel.per_post]
        if not batch:
            sent[kind] = 0
            continue
        try:
            post(channel.webhook_url, message(store, channel, batch))
        except DiscordError as exc:
            logger.warning("Discord %s, %s: %s", channel.label, kind, exc)
            if stdout:
                stdout.write(f"{channel.label} {kind}: not posted, {exc}")
            if exc.status == 429:
                break
            continue
        DiscordPost.objects.bulk_create(
            [DiscordPost(key=head + p.key, kind=kind, channel=saved, posted_at=now) for p in batch], ignore_conflicts=True,
        )
        sent[kind] = len(batch)
    return sent


def run(now=None, post=None, stdout=None):
    """Post every channel's due updates. Returns {channel label: {kind: posts sent}}."""
    now = now or timezone.now()
    post = post or send
    store = copy.load_all()
    sent = {channel.label: post_channel(channel, now, store, post, stdout) for channel in channels()}
    DiscordPost.objects.filter(posted_at__lt=now - timedelta(days=KEEP_DAYS)).exclude(key__contains=":" + SEEDED).delete()
    return sent


def test(channels_to=None, post=None):
    """One hello to each channel set up (or to ``channels_to``). Returns how many were sent."""
    post = post or send
    store = copy.load_all()
    hello = copy.get("discord.test", store=store, site=settings.RIPRAPTOR_SITE_URL)
    targets = channels() if channels_to is None else channels_to
    for channel in targets:
        post(channel.webhook_url, message(store, channel, content=hello))
    return len(targets)
